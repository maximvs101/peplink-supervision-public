"""Flux temps réel : cycle de vie d'un abonné.

Trois défauts ont motivé ces tests, tous produisant le même symptôme — un client
qui ne reçoit plus rien sans qu'aucune erreur ne soit visible.
"""

from unittest.mock import AsyncMock

import pytest

from app.devices.registry import DeviceRegistry
from app.fleet import RouterConfig, WanConfig
from app.models import (
    DeviceCapabilities,
    DeviceInfo,
    PowerSource,
    RadioSample,
    WanRadioStatus,
)
from app.poller import _CLOSED, _IDENTIFY_ATTEMPTS, _INFO_REFRESH_CYCLES, Poller
from app.storage.db import SampleRepository


@pytest.fixture
async def poller(tmp_path):
    repo = SampleRepository(str(tmp_path / "s.db"))
    await repo.connect()
    registry = DeviceRegistry(default_mode="mock")
    p = Poller(registry, repo, interval_seconds=0.5)
    yield p, registry
    await repo.close()
    await registry.close()


def _liens_stables(record) -> None:
    """Un lien qui vieillit d'un cycle à l'autre, à la place du simulateur.

    Le simulateur coupe un lien 0,3 % du temps et remet son uptime à zéro ; sur
    l'unique lien de ces équipements de test, c'est « tous les liens ont
    reculé », donc une relecture immédiate de la fiche qui consomme un
    `side_effect` — et un test de cadence qui tombe une fois sur trente.
    """
    uptime = iter(range(100, 1_000_000, 3))
    record.client.get_radio_status = AsyncMock(
        side_effect=lambda echeance=None: [WanRadioStatus(
            wan_id="cellular-1", wan_name="SIM", kind="cellular", connected=True,
            uptime_s=next(uptime),
        )]
    )


def _config(device_id: str) -> RouterConfig:
    return RouterConfig(id=device_id, name=device_id.upper(), mode="mock",
                        wans=[WanConfig(id="cellular-1", name="SIM")])


async def test_snapshot_is_safe_to_iterate_while_polling(poller):
    """L'envoi initial cède la main entre deux relevés.

    Itérer directement sur `latest[router_id]` levait `RuntimeError` — le poller
    réécrit ce dictionnaire — et l'exception, avalée en journal de mise au point,
    laissait le client attendre indéfiniment un état courant qui n'arrivait pas.
    """
    p, registry = poller
    await registry.add(_config("a"))
    await p._poll_once()

    snapshot = p.snapshot_latest("a")
    await p._poll_once()          # le poller réécrit pendant qu'on tient la copie
    assert len(snapshot) >= 1
    assert all(isinstance(s, RadioSample) for s in snapshot)


async def test_snapshot_of_an_unknown_device_is_empty(poller):
    p, _ = poller
    assert p.snapshot_latest("jamais-vu") == []


async def test_removing_a_device_wakes_its_subscribers(poller):
    """Sans sentinelle, la coroutine reste bloquée sur `queue.get()` à jamais."""
    p, registry = poller
    await registry.add(_config("a"))
    queue = p.subscribe("a")

    p.forget("a")

    assert not queue.empty()
    # Ce n'est pas un relevé : le lecteur doit refermer la connexion.
    assert not isinstance(queue.get_nowait(), RadioSample)


async def test_a_full_queue_still_receives_the_sentinel(poller):
    """Cas où le réveil échouait en silence : la file de l'abonné est PLEINE.

    `put_nowait` levait `QueueFull`, l'exception était avalée, et les abonnés
    ayant été retirés dans la même expression, plus rien ne pouvait reposer la
    sentinelle. Mesuré : `_CLOSED` absent, `queue.get()` bloqué à jamais, et la
    coroutine, la socket et la file perdues à chaque retrait.
    """
    p, registry = poller
    await registry.add(_config("a"))
    queue = p.subscribe("a")
    for _ in range(queue.maxsize):
        queue.put_nowait(object())
    assert queue.full()

    p.forget("a")

    # La sentinelle est passée : le lecteur finira par la lire et refermer.
    contents = [queue.get_nowait() for _ in range(queue.qsize())]
    assert contents[-1] is _CLOSED
    assert len(contents) == queue.maxsize   # un seul relevé sacrifié, pas la file


async def test_unsubscribe_does_not_resurrect_a_forgotten_device(poller):
    """`_subscribers` est un defaultdict : l'indexer recrée l'entrée purgée."""
    p, registry = poller
    await registry.add(_config("a"))
    queue = p.subscribe("a")
    p.forget("a")

    p.unsubscribe("a", queue)
    assert "a" not in p._subscribers


async def test_unsubscribing_the_last_listener_cleans_up(poller):
    p, registry = poller
    await registry.add(_config("a"))
    first, second = p.subscribe("a"), p.subscribe("a")

    p.unsubscribe("a", first)
    assert "a" in p._subscribers          # il reste un auditeur
    p.unsubscribe("a", second)
    assert "a" not in p._subscribers      # plus personne : on n'accumule pas


async def test_a_slow_subscriber_never_blocks_the_poller(poller):
    """Une file pleine fait sauter un relevé, jamais attendre la boucle."""
    p, registry = poller
    await registry.add(_config("a"))
    queue = p.subscribe("a")
    for _ in range(queue.maxsize):
        queue.put_nowait(object())

    await p._poll_once()                  # ne doit pas lever ni bloquer
    assert queue.full()


async def test_identity_requests_are_bounded_when_unavailable(poller):
    """Un équipement sans identité ne doit pas être réinterrogé sans fin."""
    p, registry = poller
    record = await registry.add(_config("a"))
    record.client.get_device_info = AsyncMock(return_value=None)
    record.client.get_capabilities = AsyncMock(return_value=None)

    for _ in range(_IDENTIFY_ATTEMPTS + 3):
        await p._poll_once()

    # Quelques essais, puis plus rien : le modèle ne la publie visiblement pas.
    assert record.client.get_device_info.await_count == _IDENTIFY_ATTEMPTS
    assert record.identified is True
    assert record.info is None            # toujours inconnue, et c'est admis


async def test_a_transient_identity_failure_is_retried(poller):
    """Un routeur qui finit de démarrer refuse cet endpoint quelques secondes.

    Le drapeau était posé AVANT la tentative, et `_identify` avalant toutes ses
    exceptions, ce refus passager rendait l'identité inconnue pour la vie du
    processus — rien ne remet jamais le drapeau à False.
    """
    p, registry = poller
    record = await registry.add(_config("a"))
    _liens_stables(record)
    real = record.client.get_device_info
    identity = await real()
    record.client.get_device_info = AsyncMock(side_effect=[OSError("réseau"), identity])

    await p._poll_once()
    assert record.info is None            # première tentative perdue
    assert record.identified is False     # mais la question reste ouverte

    await p._poll_once()
    assert record.info == identity
    assert record.identified is True

    await p._poll_once()                  # obtenue : on ne redemande plus
    assert record.client.get_device_info.await_count == 2


# --- la fiche d'équipement ne peut pas dater du démarrage -----------------------

def _fiche(charge: float, secours: bool) -> DeviceInfo:
    """Fiche plausible : ce qu'y lit l'écran, c'est la charge et l'alimentation."""
    return DeviceInfo(
        model="Pepwave MAX HD4 MBX", serial="1234-5678-9ABC", firmware="8.5.4 build 6253",
        cpu_load_percent=charge,
        power_sources=[
            PowerSource(name="DC Source A", connected=True),
            PowerSource(name="DC Source B", connected=secours),
        ],
    )


async def test_the_device_sheet_is_refreshed_periodically(poller):
    """`status.system.info` ne porte pas que de l'identité : elle se périme.

    Charge CPU, consommation et état des alimentations vivent dans cette fiche,
    et l'interface les affiche sans horodatage à côté de données live. Lue une
    seule fois au démarrage — `_identify` n'est rappelée que tant que
    `identified` est faux — elle annonçait 3,8 % de charge sur un équipement
    saturé et une seconde alimentation raccordée alors qu'elle était perdue en
    mission. Même espèce que le WAN désactivé affiché connecté.
    """
    p, registry = poller
    record = await registry.add(_config("a"))
    record.client.get_device_info = AsyncMock(
        side_effect=[_fiche(3.8, True), _fiche(91.2, False)]
    )
    # Une fiche de capacités CONCLUE : c'est elle qu'il serait inutile de rejouer.
    record.client.get_capabilities = AsyncMock(
        return_value=DeviceCapabilities(gps=True, pepvpn=True)
    )
    # Un lien STABLE, qui vieillit d'un cycle à l'autre. Le simulateur coupe un
    # lien 0,3 % du temps et remet son uptime à zéro ; sur l'unique lien de cet
    # équipement, c'est « tous les liens ont reculé » — un redémarrage aux yeux
    # du collecteur, qui relit alors la fiche sur-le-champ, et ce test mesurait
    # cette relecture-là comme une échéance manquée. Vu sur la machine Windows
    # de GitHub, jamais ici : c'est le hasard du simulateur, pas le système.
    _liens_stables(record)

    await p._poll_once()
    assert record.info.cpu_load_percent == 3.8

    for _ in range(_INFO_REFRESH_CYCLES - 1):
        await p._poll_once()
    assert record.info.cpu_load_percent == 3.8, "la fiche a été rejouée avant l'échéance"

    await p._poll_once()
    assert record.info.cpu_load_percent == 91.2
    assert record.info.power_sources[1].connected is False, "l'alimentation perdue reste raccordée"

    # Les capacités CONCLUES sont du matériel : les rejouer coûterait deux
    # requêtes de plus par rafraîchissement — `info.capability` et `status.port` —
    # pour une réponse identique.
    assert record.client.get_capabilities.await_count == 1


async def test_a_capability_that_could_not_be_concluded_is_probed_again(poller):
    """Une capacité NIÉE par accident ne doit pas l'être pour la vie du processus.

    Un délai dépassé sur `info.location` pendant qu'un routeur finit de démarrer
    gravait `gps=False` : `get_location()` n'était plus jamais appelée, tous les
    relevés du véhicule portaient les coordonnées fixes du fichier de flotte, et
    le barycentre de session ne décrivait plus rien — sans que rien ne le signale.
    Une fiche qui n'a pas pu conclure se rejoue donc avec la fiche d'identité.
    """
    p, registry = poller
    record = await registry.add(_config("a"))
    record.client.get_device_info = AsyncMock(return_value=_fiche(3.8, True))
    record.client.get_capabilities = AsyncMock(
        side_effect=[
            DeviceCapabilities(gps=True, provisional=True),   # sonde sans conclusion
            DeviceCapabilities(gps=False, provisional=False),  # le firmware tranche
            DeviceCapabilities(gps=True, provisional=False),   # ne doit plus être lue
        ]
    )

    for _ in range(_INFO_REFRESH_CYCLES + 1):
        await p._poll_once()
    assert record.client.get_capabilities.await_count == 2, "la fiche à confirmer n'a pas été rejouée"
    assert record.capabilities.gps is False

    for _ in range(_INFO_REFRESH_CYCLES + 1):
        await p._poll_once()
    assert record.client.get_capabilities.await_count == 2, "une fiche conclue a été redemandée"


async def test_an_abandoned_identity_is_never_resurrected_by_the_refresh(poller):
    """Rafraîchir une identité OBTENUE, insister pour en obtenir une : deux choses.

    Le rafraîchissement s'appuierait volontiers sur `identified`, mais ce
    drapeau vaut aussi True après l'abandon des trois tentatives initiales. S'y
    fier rétablirait, à 420 ms la requête et jusqu'à l'arrêt du processus,
    l'interrogation sans fin des modèles qui ne publient pas cet endpoint —
    exactement ce que ce bornage supprime.
    """
    p, registry = poller
    record = await registry.add(_config("a"))
    record.client.get_device_info = AsyncMock(return_value=None)
    record.client.get_capabilities = AsyncMock(return_value=None)

    for _ in range(_IDENTIFY_ATTEMPTS + _INFO_REFRESH_CYCLES + 2):
        await p._poll_once()

    assert record.identified is True and record.info is None
    assert record.client.get_device_info.await_count == _IDENTIFY_ATTEMPTS


async def test_a_failed_refresh_keeps_the_last_known_sheet(poller):
    """Un refus ponctuel ne doit ni effacer la fiche, ni condamner le rafraîchissement.

    Le rafraîchissement n'a lieu qu'après une lecture radio ABOUTIE : un refus
    ici vise ce seul endpoint. Effacer la fiche emporterait le modèle, le
    firmware et le numéro de série, qui sont stables et toujours vrais — on
    garde donc la précédente, et on retente à l'échéance suivante.
    """
    p, registry = poller
    record = await registry.add(_config("a"))
    _liens_stables(record)
    record.client.get_device_info = AsyncMock(
        side_effect=[_fiche(3.8, True), OSError("réseau"), _fiche(91.2, False)]
    )
    record.client.get_capabilities = AsyncMock(return_value=None)

    await p._poll_once()
    for _ in range(_INFO_REFRESH_CYCLES):     # première échéance : elle échoue
        await p._poll_once()
    assert record.info.cpu_load_percent == 3.8, "la fiche a été effacée par un échec"

    for _ in range(_INFO_REFRESH_CYCLES):     # seconde échéance : elle aboutit
        await p._poll_once()
    assert record.info.cpu_load_percent == 91.2, "le rafraîchissement a été condamné"


async def test_a_removal_during_the_handshake_does_not_create_an_orphan_subscriber():
    """`get_router` puis `accept()` puis `subscribe` : un retrait tombé pendant
    l'`accept` recréait, par le defaultdict, l'entrée que `forget()` venait de
    purger — un abonné sur un équipement inexistant, que rien ne réveillerait
    jamais, et une socket ouverte à jamais. L'abonnement est revérifié après
    l'`accept`, et la socket fermée en 4404.

    Le gestionnaire est appelé DIRECTEMENT, avec une socket factice : par le
    client de test, le défaut se manifeste par une réception qui ne revient
    jamais — un test qui pend au lieu de tomber."""
    import asyncio
    from unittest.mock import AsyncMock, MagicMock

    from app.api.ws import live_router

    class _Poller:
        def __init__(self) -> None:
            self.abonnes: dict[str, set] = {}
            self.reponses = iter([object(), None])   # connu à l'entrée, retiré pendant l'accept

        def get_router(self, router_id: str):
            return next(self.reponses, None)

        def subscribe(self, router_id: str):
            file = asyncio.Queue()
            self.abonnes.setdefault(router_id, set()).add(file)
            return file

        def unsubscribe(self, router_id: str, file) -> None:
            self.abonnes.get(router_id, set()).discard(file)

        def snapshot_latest(self, router_id: str):
            return []

    poller = _Poller()
    socket = MagicMock()
    socket.headers = {}
    socket.app.state.poller = poller
    socket.accept = AsyncMock()
    socket.close = AsyncMock()
    socket.send_text = AsyncMock()

    await asyncio.wait_for(live_router(socket, "mbx"), timeout=2)
    socket.close.assert_awaited_once()
    assert socket.close.await_args.kwargs.get("code") == 4404
    assert poller.abonnes.get("mbx", set()) == set(), "un abonné orphelin est resté"


async def test_the_send_loop_delivers_the_snapshot_then_each_new_sample():
    """Le transport vers le client — le symptôme que le module dit combattre —
    n'était vérifié nulle part : supprimer `send_text` laissait 515 tests
    verts. Ici, l'état courant part à l'ouverture, puis chaque relevé publié,
    puis la sentinelle ferme en 4404 ; et la déconnexion du client termine
    la boucle sans rien laisser en vol."""
    import asyncio
    import json
    from unittest.mock import AsyncMock, MagicMock

    from app.api.ws import live_router
    from app.models import RadioSample, WanRadioStatus

    def releve(wan_id: str, rsrp: float) -> RadioSample:
        return RadioSample(router_id="mbx", router_name="MBX",
                           wan=WanRadioStatus(wan_id=wan_id, wan_name="SIM", rsrp=rsrp))

    class _Poller:
        def __init__(self) -> None:
            self.file: asyncio.Queue = asyncio.Queue()

        def get_router(self, router_id: str):
            return object()

        def subscribe(self, router_id: str):
            return self.file

        def unsubscribe(self, router_id: str, file) -> None:
            self.file = None

        def snapshot_latest(self, router_id: str):
            return [releve("4", -90.0), releve("5", -95.0)]

    poller = _Poller()
    deconnexion: asyncio.Future = asyncio.get_running_loop().create_future()
    socket = MagicMock()
    socket.headers = {}
    socket.app.state.poller = poller
    socket.accept = AsyncMock()
    socket.close = AsyncMock()
    socket.send_text = AsyncMock()
    async def recevoir():
        return await deconnexion
    socket.receive = recevoir

    tache = asyncio.ensure_future(live_router(socket, "mbx"))
    await asyncio.sleep(0.05)
    # L'état courant, tout de suite, avant tout nouveau relevé.
    envoyes = [json.loads(c.args[0]) for c in socket.send_text.await_args_list]
    assert [(e["wan"]["wan_id"], e["wan"]["rsrp"]) for e in envoyes] == [("4", -90.0), ("5", -95.0)]

    # Un relevé publié par le poller arrive au client.
    await poller.file.put(releve("4", -88.0))
    await asyncio.sleep(0.05)
    envoyes = [json.loads(c.args[0]) for c in socket.send_text.await_args_list]
    assert (envoyes[-1]["wan"]["wan_id"], envoyes[-1]["wan"]["rsrp"]) == ("4", -88.0)

    # Le client raccroche : la boucle se termine, l'abonnement est rendu.
    deconnexion.set_result({"type": "websocket.disconnect"})
    await asyncio.wait_for(tache, timeout=2)
    assert poller.file is None, "l'abonnement n'a pas été rendu"
    socket.close.assert_not_awaited()


async def test_the_sentinel_closes_the_socket_in_4404():
    """L'équipement retiré : la sentinelle (un non-relevé dans la file) ferme
    la socket avec le code applicatif, et le navigateur cesse de se
    reconnecter."""
    import asyncio
    from unittest.mock import AsyncMock, MagicMock

    from app.api.ws import live_router

    class _Poller:
        def __init__(self) -> None:
            self.file: asyncio.Queue = asyncio.Queue()

        def get_router(self, router_id: str):
            return object()

        def subscribe(self, router_id: str):
            return self.file

        def unsubscribe(self, router_id: str, file) -> None:
            pass

        def snapshot_latest(self, router_id: str):
            return []

    poller = _Poller()
    jamais: asyncio.Future = asyncio.get_running_loop().create_future()
    socket = MagicMock()
    socket.headers = {}
    socket.app.state.poller = poller
    socket.accept = AsyncMock()
    socket.close = AsyncMock()
    socket.send_text = AsyncMock()
    async def recevoir():
        return await jamais
    socket.receive = recevoir

    tache = asyncio.ensure_future(live_router(socket, "mbx"))
    await asyncio.sleep(0.02)
    await poller.file.put(None)
    await asyncio.wait_for(tache, timeout=2)
    socket.close.assert_awaited_once()
    assert socket.close.await_args.kwargs.get("code") == 4404
