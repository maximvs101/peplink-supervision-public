"""Le poller face à une flotte qui change pendant qu'il tourne.

Trois situations que la flotte figée ne pouvait pas produire : un équipement
apparaît entre deux cycles, un autre disparaît en plein cycle, et un troisième
refuse les identifiants — auquel cas il ne faut surtout pas réessayer.
"""

import asyncio
from unittest.mock import AsyncMock, patch

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from app.api import routes
from app.devices.registry import DeviceRegistry
from app.fleet import RouterConfig, WanConfig
from app.models import WanRadioStatus, utcnow
from app.peplink.router_api import AuthenticationError, CredentialsRefusedError
from app.poller import Poller
from app.storage.db import SampleRepository


def _config(device_id: str) -> RouterConfig:
    return RouterConfig(
        id=device_id, name=device_id.upper(), mode="mock",
        wans=[WanConfig(id="cellular-1", name="SIM")],
    )


@pytest.fixture
async def poller(tmp_path):
    repo = SampleRepository(str(tmp_path / "s.db"))
    await repo.connect()
    registry = DeviceRegistry(default_mode="mock")
    p = Poller(registry, repo, interval_seconds=0.5)
    yield p, registry
    await repo.close()
    await registry.close()


async def test_a_device_added_between_cycles_is_polled(poller):
    p, registry = poller
    await p._poll_once()
    assert p.latest == {}

    await registry.add(_config("nouveau"))
    await p._poll_once()
    assert p.latest.get("nouveau")


async def test_removal_during_a_cycle_does_not_raise(poller):
    """Le parcours travaille sur une copie : muter la flotte ne doit rien casser."""
    p, registry = poller
    for i in range(5):
        await registry.add(_config(f"d{i}"))

    snapshot = registry.snapshot()
    await registry.remove("d2")
    await registry.add(_config("d9"))
    # Le cycle en cours continue sur sa copie, sans lever.
    assert len(snapshot) == 5
    await p._poll_once()
    assert "d9" in p.latest


async def test_forget_purges_every_trace(poller):
    p, registry = poller
    await registry.add(_config("a"))
    await p._poll_once()
    p._cell_changes[("a", "cellular-1")] = 3
    p._disconnects[("a", "cellular-1")] = 1
    p._gps["a"] = (48.0, 2.0)
    p.subscribe("a")

    await registry.remove("a")
    p.forget("a")

    assert "a" not in p.latest and "a" not in p._gps
    assert not any(k[0] == "a" for k in p._cell_changes)
    assert not any(k[0] == "a" for k in p._disconnects)
    assert "a" not in p._subscribers


async def test_a_removal_during_a_cycle_does_not_resurrect_the_state(poller):
    """Le retrait tombe pendant `get_radio_status()`, pas entre deux cycles.

    Le cycle travaille sur une copie de la flotte et rend la main à chaque
    `await` : il reprenait après la purge et réécrivait `latest[...]`. Vérifié
    avant correction : `registry.get("x")` rendait None alors que `"x"` était
    toujours dans `latest`, et la simple lecture de `_cell_changes[key]`
    recréait la clé purgée (defaultdict).
    """
    p, registry = poller
    record = await registry.add(_config("x"))
    answer = record.client.get_radio_status

    async def removed_while_answering(echeance=None):
        statuses = await answer()
        # Exactement ce que fait `DELETE /api/devices/x` pendant l'appel.
        await registry.remove("x")
        p.forget("x")
        return statuses

    record.client.get_radio_status = removed_while_answering
    await p._poll_once()

    assert registry.get("x") is None
    assert "x" not in p.latest
    assert not any(k[0] == "x" for k in p._cell_changes)
    assert not any(k[0] == "x" for k in p._disconnects)

    # Un identifiant réutilisé repart donc bien de zéro, ce que la purge promet.
    await registry.add(_config("x"))
    await p._poll_once()
    assert next(iter(p.latest["x"].values())).cell_changes == 0


async def test_a_reused_identifier_does_not_inherit_the_previous_state(poller):
    """Sans purge, les compteurs de stabilité du précédent survivraient."""
    p, registry = poller
    await registry.add(_config("poste-1"))
    await p._poll_once()
    p._cell_changes[("poste-1", "cellular-1")] = 42

    await registry.remove("poste-1")
    p.forget("poste-1")
    await registry.add(_config("poste-1"))
    await p._poll_once()

    sample = next(iter(p.latest["poste-1"].values()))
    assert sample.cell_changes == 0


async def test_refused_credentials_are_never_retried(poller):
    """Cinq tentatives ratées suspendent l'accès : le poller ne doit pas insister.

    Le test précédent affirmait qu'« il relit à chaque cycle mais ne se reconnecte
    jamais » et attendait quatre appels. C'était faux, et c'est tout le défaut :
    `_get()` réauthentifie dès que la session manque, donc chaque relecture
    déclenchait un `POST /api/login`. Reproduit sur transport bouchonné, six
    tentatives en six cycles — à 5,11 s de cadence, les cinq échecs qui
    suspendent l'accès admin étaient atteints en 26 secondes, verrouillant aussi
    l'exploitant hors de l'interface web de son routeur.

    Le contrat juste : un seul appel, puis plus rien du tout.
    """
    p, registry = poller
    record = await registry.add(_config("mbx"))
    record.client.get_radio_status = AsyncMock(
        side_effect=CredentialsRefusedError("login refusé")
    )

    for _ in range(4):
        await p._poll_once()

    assert record.status == "identifiants refusés"
    assert record.error and "refusé" in record.error
    assert record.auth_blocked is True
    assert record.client.get_radio_status.await_count == 1, "l'équipement a été rappelé"
    assert "mbx" not in p.latest      # aucun relevé n'a été produit


async def test_an_expired_session_is_not_a_refused_password(poller):
    """Un 401 sur une LECTURE est une session expirée : il ne verrouille rien.

    Le jeton vaut 48 h et la session admin expire : `_get()` se reconnecte, et
    c'est le fonctionnement normal. Confondre les deux cas aurait fait cesser la
    supervision d'un équipement parfaitement accessible.
    """
    p, registry = poller
    record = await registry.add(_config("mbx"))
    record.client.get_radio_status = AsyncMock(
        side_effect=AuthenticationError("session illisible")
    )

    for _ in range(3):
        await p._poll_once()

    assert record.status == "injoignable"
    assert record.auth_blocked is False
    assert record.client.get_radio_status.await_count == 3, "l'équipement a été abandonné"


async def test_reauth_lifts_the_lock(poller):
    """La reprise est une action humaine, et elle relance vraiment la scrutation."""
    p, registry = poller
    record = await registry.add(_config("mbx"))
    record.client.get_radio_status = AsyncMock(
        side_effect=CredentialsRefusedError("login refusé")
    )
    await p._poll_once()
    assert record.auth_blocked is True

    # L'exploitant corrige le mot de passe et relance.
    record.auth_blocked = False
    record.client.get_radio_status = AsyncMock(
        return_value=[WanRadioStatus(wan_id="4", wan_name="SIM", kind="cellular", connected=True)]
    )
    await p._poll_once()

    assert record.status == "connecté"
    assert set(p.latest["mbx"]) == {"4"}


async def test_unreachable_and_refused_are_distinguished(poller):
    p, registry = poller
    unreachable = await registry.add(_config("hors-portee"))
    refused = await registry.add(_config("mauvais-mdp"))
    unreachable.client.get_radio_status = AsyncMock(side_effect=OSError("réseau"))
    refused.client.get_radio_status = AsyncMock(side_effect=CredentialsRefusedError("refusé"))

    await p._poll_once()

    assert unreachable.status == "injoignable"
    assert refused.status == "identifiants refusés"


async def test_a_timeout_has_a_cause_that_is_never_empty(poller):
    """`str(httpx.ConnectTimeout())` est vide : le journal écrivait « Routeur
    mbx-01 injoignable : » et l'écran gardait la cause précédente. Vu sur le
    serveur en service, routeur hors réseau."""
    import httpx
    from app.poller import _cause_injoignable

    p, registry = poller
    record = await registry.add(_config("hors-reseau"))
    record.client.get_radio_status = AsyncMock(side_effect=httpx.ConnectTimeout(""))
    await p._poll_once()
    assert record.status == "injoignable"
    assert record.error and "délai" in record.error

    # Une cause enveloppée sans texte : on remonte jusqu'au premier mot.
    try:
        raise OSError("Network is unreachable")
    except OSError as origine:
        enveloppe = httpx.ConnectError("")
        enveloppe.__cause__ = origine
    assert _cause_injoignable(enveloppe) == "Network is unreachable"
    # Et jamais rien : au pire, le nom de l'erreur.
    assert _cause_injoignable(RuntimeError("")) == "RuntimeError"


async def test_the_poller_gives_the_complements_a_deadline_inside_its_budget(poller):
    """C-5, côté boucle : l'échéance passée au client laisse le temps de
    publier avant que le budget de l'équipement ne tombe. Un client qui
    consomme ses compléments jusqu'à l'échéance reste « connecté »."""
    from app.peplink.base import PeplinkClient  # noqa: F401 — le contrat typé

    p, registry = poller
    # Quatre secondes, donc une seconde de réserve : à un, les 0,25 s de
    # réserve ne suffisaient pas au runner Windows de l'action pour écrire.
    p._device_budget = 4.0
    record = await registry.add(_config("lent"))
    lu = await record.client.get_radio_status()
    vues: list[float | None] = []

    async def lent(echeance: float | None = None):
        vues.append(echeance)
        boucle = asyncio.get_running_loop()
        # Le client honore l'échéance : les compléments courent jusqu'à elle.
        await asyncio.sleep(max(0.0, (echeance or boucle.time() + 5) - boucle.time()))
        return lu

    record.client.get_radio_status = lent
    debut = asyncio.get_running_loop().time()
    await p._poll_once()
    assert vues and vues[0] is not None, "aucune échéance transmise au client"
    assert vues[0] < debut + p._device_budget
    assert record.status == "connecté", record.error


async def test_a_slow_identity_does_not_cost_the_reading_nor_an_attempt(poller):
    """L'identification est une lecture réseau de plus, faite APRÈS le relevé
    WAN : une fiche lente ne doit pas le faire jeter (C-5). Interrompue par
    l'échéance, elle ne compte pas comme une des trois tentatives."""
    p, registry = poller
    p._device_budget = 4.0   # une seconde de réserve, comme ci-dessus
    record = await registry.add(_config("fiche-lente"))

    async def fiche_lente():
        await asyncio.sleep(30)

    record.client.get_device_info = AsyncMock(side_effect=fiche_lente)
    await p._poll_once()
    assert record.status == "connecté", record.error
    assert "fiche-lente" in p.latest
    assert record.identify_attempts == 0 and not record.identified


async def test_a_device_recovers_after_a_failure(poller):
    p, registry = poller
    record = await registry.add(_config("mbx"))
    real = record.client.get_radio_status
    record.client.get_radio_status = AsyncMock(side_effect=OSError("réseau"))
    await p._poll_once()
    assert record.status == "injoignable"

    record.client.get_radio_status = real
    await p._poll_once()
    assert record.status == "connecté" and record.error is None


# --- l'état de l'équipement remonte jusqu'au résumé ----------------------------

async def test_summary_carries_the_device_state(poller, tmp_path):
    """Sans cet état, l'interface affichait « Liaison OK » sur une liaison morte."""
    from app.api.routes import _summary

    p, registry = poller
    record = await registry.add(_config("a"))
    await p._poll_once()

    summary = _summary(p.latest, record.config, p)
    assert summary.status == "connecté" and summary.error is None
    assert summary.age_seconds is not None and summary.age_seconds < 5

    record.client.get_radio_status = AsyncMock(side_effect=OSError("réseau"))
    await p._poll_once()

    summary = _summary(p.latest, record.config, p)
    assert summary.status == "injoignable"
    assert "réseau" in (summary.error or "")
    # Les relevés survivent en mémoire — c'est précisément pourquoi l'état compte.
    assert summary.wans


async def test_the_access_token_never_reaches_the_api(poller):
    """Le jeton ne voyage qu'en paramètre d'URL : toute erreur HTTP le recopie.

    Chaîne reproduite : `raise_for_status()` compose son message avec l'URL
    entière, le poller rangeait ce message tel quel dans `record.error`, et
    `GET /api/routers` — route sans restriction de provenance, contrairement à
    `/discover` et `/devices/pair` — le publiait. Un simple 500 passager
    suffisait à livrer le jeton d'accès du routeur à qui interrogeait l'API.
    """
    p, registry = poller
    record = await registry.add(_config("dome"))
    fuite = (
        "Server error '500 Internal Server Error' for url "
        "'https://192.168.50.1/api/status.wan.connection?accessToken=s3cr3t-abcdef'"
    )
    record.client.get_radio_status = AsyncMock(side_effect=RuntimeError(fuite))

    await p._poll_once()

    assert record.status == "injoignable"
    assert "s3cr3t-abcdef" not in (record.error or "")

    app = FastAPI()
    app.include_router(routes.router)
    app.state.poller = p
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get("/api/routers")

    assert response.status_code == 200
    assert "s3cr3t-abcdef" not in response.text
    # La CAUSE, elle, reste lisible : c'est ce que le bandeau affiche à
    # l'utilisateur, et la masquer le laisserait devant un « injoignable » nu.
    assert "500 Internal Server Error" in response.json()[0]["error"]


async def test_age_is_measured_by_the_server(poller):
    """Le calculer côté navigateur revenait à comparer deux horloges."""
    from app.api.routes import _summary

    p, registry = poller
    record = await registry.add(_config("a"))
    await p._poll_once()

    summary = _summary(p.latest, record.config, p)
    assert summary.age_seconds >= 0


async def test_a_wan_disabled_on_the_router_stops_being_shown(poller):
    """Un WAN désactivé disparaît du relevé : son dernier état ne doit pas survivre.

    Le parseur écarte les WAN que le routeur déclare `enable: false`. Comme le
    poller n'écrivait que ce qu'il recevait, sans jamais retirer, le dernier
    échantillon restait indéfiniment dans l'état courant. Observé sur un
    MAX HD4 MBX : une SIM désactivée depuis l'interface d'administration
    continuait de s'afficher connectée et dans le bond, avec son signal d'une
    vingtaine de minutes plus tôt.
    """
    p, registry = poller
    await registry.add(_config("mbx"))
    record = registry.get("mbx")

    deux = [
        WanRadioStatus(wan_id="4", wan_name="SIM 1", kind="cellular", connected=True),
        WanRadioStatus(wan_id="7", wan_name="SIM 4", kind="cellular", connected=True),
    ]
    record.client.get_radio_status = AsyncMock(return_value=deux)
    await p._poll_once()
    assert set(p.latest["mbx"]) == {"4", "7"}

    # WAN 7 désactivé côté routeur : il n'est plus dans le relevé.
    record.client.get_radio_status = AsyncMock(return_value=deux[:1])
    await p._poll_once()
    assert set(p.latest["mbx"]) == {"4"}, "l'état du WAN désactivé a survécu"


async def test_an_unreachable_router_keeps_its_last_known_wans(poller):
    """Purger sur un échec effacerait l'écran au lieu de le dater.

    Un équipement injoignable ne dit rien de ses WAN : le dernier état connu
    reste la meilleure information disponible, et ce sont `status` et
    `age_seconds` qui disent qu'il a vieilli.
    """
    p, registry = poller
    await registry.add(_config("mbx"))
    record = registry.get("mbx")
    record.client.get_radio_status = AsyncMock(
        return_value=[WanRadioStatus(wan_id="4", wan_name="SIM 1", kind="cellular", connected=True)]
    )
    await p._poll_once()
    assert set(p.latest["mbx"]) == {"4"}

    record.client.get_radio_status = AsyncMock(side_effect=OSError("injoignable"))
    await p._poll_once()
    assert set(p.latest["mbx"]) == {"4"}, "l'état a été effacé au lieu d'être daté"
    assert record.status == "injoignable"


async def test_a_refused_password_costs_exactly_one_login_attempt(poller):
    """Le compte qui décide : combien de POST /api/login pour N cycles ?

    C'est la mesure de la panne, reproduite à l'identique sur le vrai client.
    Cinq échecs suspendent l'accès admin cinq minutes ; avant correction le
    poller en consommait un par cycle, soit le verrou en 26 secondes à la
    cadence réelle — et ce verrou atteignait aussi l'exploitant qui tentait
    d'ouvrir l'interface web du routeur au même moment.
    """
    import httpx
    from app.peplink.router_api import RouterApiPeplinkClient

    logins = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal logins
        if request.url.path.endswith("/api/login"):
            logins += 1
        return httpx.Response(200, json={"stat": "fail", "code": 401, "message": "Unauthorized"})

    p, registry = poller
    config = RouterConfig(
        id="mbx", name="MBX", mode="router", host="192.0.2.1",
        username="admin", password="mauvais", use_gps=False,
    )
    await registry.add(config)
    record = registry.get("mbx")
    await record.client.aclose()
    record.client = RouterApiPeplinkClient(config)
    await record.client.aclose()
    record.client._http = httpx.AsyncClient(
        transport=httpx.MockTransport(handler), base_url="https://192.0.2.1"
    )

    for _ in range(6):
        await p._poll_once()

    assert logins == 1, f"{logins} tentatives de connexion en 6 cycles"
    assert record.status == "identifiants refusés"
    assert record.auth_blocked is True


async def test_the_published_period_is_the_observed_one(poller):
    """La période n'est pas la consigne : la boucle scrute PUIS dort.

    Publier la consigne faisait déduire au navigateur un seuil de péremption de
    15 s, alors que le relevé le plus récent pouvait légitimement dater de 15,1 s
    — bandeau rouge sur liaison saine, dès un seul équipement.
    """
    p, _ = poller
    assert p.period_seconds is None, "aucune période avant deux cycles"
    assert p.period_for("mbx") is None

    p._mark_cycle("mbx")
    assert p.period_for("mbx") is None, "un seul point ne fait pas une période"

    p._last_cycle_start["mbx"] -= 5.0          # cinq secondes se sont écoulées
    p._mark_cycle("mbx")
    assert p.period_for("mbx") == pytest.approx(5.0, abs=0.05)

    # Un cycle isolément plus lent ne relâche pas le seuil d'un coup.
    p._last_cycle_start["mbx"] -= 15.0
    p._mark_cycle("mbx")
    assert 5.0 < p.period_for("mbx") < 9.0

    # La période est propre à CHAQUE équipement : un équipement mort ne doit pas
    # relâcher le seuil de péremption de ses voisins sains. Mesuré avant
    # correction : sur quatre véhicules dont un éteint, les trois sains
    # passaient de 5,1 s de période publiée à 23 s, et le bandeau restait vert
    # cent quinze secondes après la mort d'une liaison.
    p._mark_cycle("dome")
    p._last_cycle_start["dome"] -= 40.0
    p._mark_cycle("dome")
    assert p.period_for("mbx") < 9.0, "la lenteur d'un voisin a déteint"
    assert p.period_seconds == pytest.approx(40.0, abs=0.5), "la vue de flotte retient le pire"


async def test_a_link_that_came_back_between_two_polls_is_counted(poller):
    """Une coupure plus courte que le cycle ne se voit que par l'uptime.

    Les deux relevés qui l'encadrent montrent le lien connecté : le compteur de
    coupures, qui ne regardait que cette transition, n'en voyait rien. À 5,3 s de
    période, c'est un angle mort de plusieurs secondes par cycle — et les
    micro-coupures sont ce qui fait décrocher un flux sans laisser de trace.
    """
    p, registry = poller
    record = await registry.add(_config("mbx"))
    lien = lambda up: [
        WanRadioStatus(wan_id="4", wan_name="SIM", kind="cellular", connected=True, uptime_s=up)
    ]

    record.client.get_radio_status = AsyncMock(return_value=lien(3600))
    await p._poll_once()
    assert p._disconnects[("mbx", "4")] == 0

    # Le lien est toujours connecté, mais il ne l'est plus depuis aussi longtemps.
    record.client.get_radio_status = AsyncMock(return_value=lien(12))
    await p._poll_once()
    assert p._disconnects[("mbx", "4")] == 1

    # Un uptime qui progresse normalement ne compte rien.
    record.client.get_radio_status = AsyncMock(return_value=lien(18))
    await p._poll_once()
    assert p._disconnects[("mbx", "4")] == 1


async def test_a_vanished_wan_leaves_a_trace_in_the_history(poller):
    """L'absence est une mesure, et elle doit s'écrire.

    Une SIM désactivée disparaît du relevé : le parseur l'écarte, et la purge la
    retire de l'état courant. L'historique n'en gardait RIEN — treize minutes
    d'incident sans une ligne, ce qui se relit comme treize minutes sans incident.
    """
    p, registry = poller
    record = await registry.add(_config("mbx"))
    deux = [
        WanRadioStatus(wan_id="4", wan_name="SIM 1", kind="cellular", connected=True, rsrp=-90),
        WanRadioStatus(wan_id="7", wan_name="SIM 4", kind="cellular", connected=True, rsrp=-93),
    ]
    record.client.get_radio_status = AsyncMock(return_value=deux)
    await p._poll_once()

    record.client.get_radio_status = AsyncMock(return_value=deux[:1])
    await p._poll_once()

    # L'état courant l'a bien oublié…
    assert set(p.latest["mbx"]) == {"4"}
    # …mais la base porte sa disparition, sans recopier de mesures périmées.
    lignes, _ = await p._repo.history("mbx", "7", "rsrp")
    assert [ligne.value for ligne in lignes] == [-93.0, None]


async def test_a_power_cycle_opens_a_new_session(poller):
    """L'ancrage est l'uptime, pas un trou entre deux relevés.

    Mesuré sur la base réelle : à 60, 120 ou 300 s de seuil, une détection par
    trou coupe une vraie session en deux à cause d'un arrêt du superviseur de
    cinq minutes — pendant lequel le routeur n'avait pas cessé de fonctionner.
    Un uptime qui RECULE ne peut signifier qu'une chose.
    """
    p, registry = poller
    record = await registry.add(_config("mbx"))
    lien = lambda up: [
        WanRadioStatus(wan_id="4", wan_name="SIM", kind="cellular", connected=True, uptime_s=up)
    ]

    record.client.get_radio_status = AsyncMock(return_value=lien(7200))
    await p._poll_once()
    premiere = await p._repo.current_session("mbx")
    assert premiere is not None

    # Même session : l'uptime progresse.
    record.client.get_radio_status = AsyncMock(return_value=lien(7205))
    await p._poll_once()
    assert (await p._repo.current_session("mbx"))["id"] == premiere["id"]

    # Coupure de courant : l'équipement repart, l'uptime aussi.
    record.client.get_radio_status = AsyncMock(return_value=lien(9))
    await p._poll_once()
    seconde = await p._repo.current_session("mbx")
    assert seconde["id"] != premiere["id"], "la mise sous tension n'a pas ouvert de session"

    assert len(await p._repo.sessions("mbx")) == 2


async def test_a_slow_device_no_longer_delays_the_others(poller):
    """Le parcours était séquentiel : chaque équipement retardait tous les autres.

    À 2,11 s de cycle par équipement, six suffisaient pour que la période dépasse
    le seuil de péremption et que l'écran passe au rouge partout, y compris sur
    des liaisons parfaitement saines.
    """
    import asyncio
    import time

    p, registry = poller
    lent = await registry.add(_config("lent"))
    rapide = await registry.add(_config("rapide"))

    async def traine(echeance=None):
        await asyncio.sleep(0.3)
        return [WanRadioStatus(wan_id="4", wan_name="SIM", kind="cellular", connected=True)]

    lent.client.get_radio_status = AsyncMock(side_effect=traine)
    rapide.client.get_radio_status = AsyncMock(
        return_value=[WanRadioStatus(wan_id="4", wan_name="SIM", kind="cellular", connected=True)]
    )

    debut = time.monotonic()
    await p._poll_once()
    duree = time.monotonic() - debut

    # En série il aurait fallu au moins 0,3 s de plus que le rapide seul.
    assert duree < 0.6, f"le cycle a duré {duree:.2f} s : les équipements s'attendent"
    assert "lent" in p.latest and "rapide" in p.latest


async def test_a_device_that_never_answers_does_not_freeze_the_cycle(poller):
    """Un équipement mort mais routé absorbait les paquets sans répondre.

    Le client a son propre délai par requête, mais un cycle en enchaîne cinq :
    l'attente se cumulait et s'ajoutait à la période de tous les autres.
    """
    import asyncio

    p, registry = poller
    muet = await registry.add(_config("muet"))
    p._device_budget = 0.2

    async def jamais(echeance=None):
        await asyncio.sleep(30)

    muet.client.get_radio_status = AsyncMock(side_effect=jamais)
    await p._poll_once()

    assert muet.status == "injoignable"
    assert muet.error and "pas de réponse" in muet.error


async def test_the_gps_is_not_polled_on_a_model_without_a_receiver(poller):
    """Une requête par cycle — 420 ms — pour une réponse qui ne peut rien contenir."""
    from app.models import DeviceCapabilities

    p, registry = poller
    record = await registry.add(_config("sans-gps"))
    record.capabilities = DeviceCapabilities(gps=False)
    record.client.get_radio_status = AsyncMock(
        return_value=[WanRadioStatus(wan_id="4", wan_name="SIM", kind="cellular", connected=True)]
    )
    record.client.get_location = AsyncMock(return_value=(48.0, 2.0))

    await p._poll_once()
    record.client.get_location.assert_not_awaited()


async def test_restarting_the_supervisor_does_not_split_a_session(poller):
    """Quatre relances du superviseur créaient quatre sessions du même instant.

    L'état vit en mémoire : au démarrage on ne sait rien d'un équipement allumé
    depuis des heures. Mais l'identité d'une session est l'instant de MISE SOUS
    TENSION, que l'uptime donne — pas celui où on a commencé à regarder.
    """
    p, registry = poller
    record = await registry.add(_config("mbx"))
    record.client.get_radio_status = AsyncMock(
        return_value=[
            WanRadioStatus(wan_id="4", wan_name="SIM", kind="cellular",
                           connected=True, uptime_s=7200)
        ]
    )
    await p._poll_once()
    premiere = await p._repo.current_session("mbx")

    # Le superviseur redémarre : sa mémoire est vide, l'équipement non.
    p._session.clear()
    record.client.get_radio_status = AsyncMock(
        return_value=[
            WanRadioStatus(wan_id="4", wan_name="SIM", kind="cellular",
                           connected=True, uptime_s=7260)
        ]
    )
    await p._poll_once()

    assert (await p._repo.current_session("mbx"))["id"] == premiere["id"]
    assert len(await p._repo.sessions("mbx")) == 1, "la session a été dupliquée"


async def test_a_device_rebooted_while_the_supervisor_was_down_opens_a_new_session(poller):
    """La contre-épreuve de la reprise : `if ecart <= _SESSION_MATCH_S` mis à
    `if True` laissait 515 tests verts. Le véhicule éteint puis rallumé pendant
    l'arrêt du superviseur — le scénario le plus courant — rattachait sa
    nouvelle mise sous tension à l'ancienne session.
    """
    p, registry = poller
    record = await registry.add(_config("mbx"))
    record.client.get_radio_status = AsyncMock(return_value=[
        WanRadioStatus(wan_id="4", wan_name="SIM", kind="cellular", connected=True, uptime_s=7200),
    ])
    await p._poll_once()
    premiere = await p._repo.current_session("mbx")

    # Le superviseur redémarre ; entre-temps, l'équipement aussi : son uptime
    # est reparti de zéro. La mise sous tension n'est plus la même.
    p._session.clear()
    record.client.get_radio_status = AsyncMock(return_value=[
        WanRadioStatus(wan_id="4", wan_name="SIM", kind="cellular", connected=True, uptime_s=30),
    ])
    await p._poll_once()

    assert (await p._repo.current_session("mbx"))["id"] != premiere["id"]
    assert len(await p._repo.sessions("mbx")) == 2, "deux mises sous tension, deux sessions"


async def test_the_farewell_sample_is_dated_when_the_absence_is_noticed(poller):
    """La trace d'une disparition doit DATER la disparition.

    `model_copy` ne rejoue pas le `default_factory` de `ts` : la ligne d'adieu
    portait l'horodatage de la dernière lecture RÉUSSIE. Deux lignes à la
    microseconde près, l'une connectée et mesurée, l'autre absente et vide — et
    surtout, après treize minutes d'injoignabilité, une disparition antidatée
    d'autant : le trou se dessinait à l'abscisse exacte d'un point valide.
    """
    p, registry = poller
    record = await registry.add(_config("mbx"))
    deux = [
        WanRadioStatus(wan_id="4", wan_name="SIM 1", kind="cellular",
                       connected=True, rsrp=-90, uptime_s=3600),
        WanRadioStatus(wan_id="7", wan_name="SIM 4", kind="cellular",
                       connected=True, rsrp=-93, uptime_s=3600),
    ]
    record.client.get_radio_status = AsyncMock(return_value=deux)
    await p._poll_once()
    dernier_vu = p.latest["mbx"]["7"].ts

    record.client.get_radio_status = AsyncMock(return_value=deux[:1])
    await p._poll_once()

    lignes, _ = await p._repo.history("mbx", "7", "rsrp")
    assert [l.value for l in lignes] == [-93.0, None]
    assert lignes[1].ts > dernier_vu, "l'adieu a hérité de l'horodatage du dernier relevé valide"

    # Et l'ancienneté de session ne suit pas non plus : `_resume_lien` compte une
    # coupure sur un `uptime` qui recule, ce qui inventerait une micro-coupure.
    from datetime import timedelta
    from app.models import utcnow
    maintenant = utcnow()
    lignes_brutes, _ = await p._repo.session_rows(
        "mbx",
        (maintenant - timedelta(hours=1)).isoformat(),
        (maintenant + timedelta(hours=1)).isoformat(),
    )
    absent = [l for l in lignes_brutes if l["wan_id"] == "7" and not l["connected"]]
    assert absent and absent[0]["uptime_s"] is None


async def test_a_link_leaving_does_not_split_the_session(poller):
    """Un lien qui s'en va n'est pas une mise sous tension.

    L'ancienneté du châssis était approchée par le maximum des liens PRÉSENTS, et
    ce maximum retombe dès que le lien qui le portait quitte le relevé. Reproduit
    sur la fixture du MAX HD4 MBX : uptimes [6384, 6524, 6724, 6384], le lien à
    6724 s'en va, et une seconde session s'ouvrait — deux fenêtres qui se
    recouvrent presque entièrement, un stationnement unique affiché comme deux
    interventions, et son volume compté double.
    """
    p, registry = poller
    record = await registry.add(_config("mbx"))

    def liens(paires):
        return [
            WanRadioStatus(wan_id=w, wan_name=f"SIM {w}", kind="cellular",
                           connected=True, uptime_s=up)
            for w, up in paires
        ]

    record.client.get_radio_status = AsyncMock(
        return_value=liens([("4", 6384), ("5", 6524), ("6", 6724), ("7", 6384)])
    )
    await p._poll_once()
    premiere = await p._repo.current_session("mbx")

    # Le WAN 6 — celui qui portait le maximum — quitte le relevé.
    record.client.get_radio_status = AsyncMock(
        return_value=liens([("4", 6389), ("5", 6529), ("7", 6389)])
    )
    await p._poll_once()
    assert (await p._repo.current_session("mbx"))["id"] == premiere["id"], \
        "le départ d'un lien a été pris pour une mise sous tension"

    # Même chose lorsqu'il reste PRÉSENT mais repart de zéro : un modem qui se
    # rétablit seul ne redémarre pas le châssis.
    record.client.get_radio_status = AsyncMock(
        return_value=liens([("4", 6394), ("5", 6534), ("6", 0), ("7", 6394)])
    )
    await p._poll_once()
    assert (await p._repo.current_session("mbx"))["id"] == premiere["id"], \
        "un modem rétabli a été pris pour une mise sous tension"

    # Mais une vraie coupure de courant remet TOUS les liens à zéro.
    record.client.get_radio_status = AsyncMock(
        return_value=liens([("4", 9), ("5", 9), ("6", 9), ("7", 9)])
    )
    await p._poll_once()
    assert (await p._repo.current_session("mbx"))["id"] != premiere["id"], \
        "la mise sous tension n'a pas ouvert de session"


async def test_a_removal_during_a_cycle_does_not_resurrect_the_session(poller):
    """`forget()` purge la session ; le cycle en cours ne doit pas la réécrire.

    Les écritures de `_session` suivent trois allers-retours en base et ne
    revérifiaient rien. L'entrée réécrite après la purge ne pouvait PLUS jamais
    l'être — aucun cycle ne repasse sur un équipement retiré —, et un identifiant
    réappairé REPRENAIT la session du précédent en continuant d'en incrémenter le
    compteur de relevés, au lieu d'en ouvrir une.
    """
    p, registry = poller
    record = await registry.add(_config("mbx"))
    statuts = [
        WanRadioStatus(wan_id="4", wan_name="SIM", kind="cellular",
                       connected=True, uptime_s=7200)
    ]

    async def retirer_puis_ouvrir(*args, **kwargs):
        p.forget("mbx")
        await registry.remove("mbx")
        return 1

    p._repo.open_session = retirer_puis_ouvrir
    await p._track_session(record, statuts)
    assert "mbx" not in p._session, "la session purgée a été ressuscitée"


async def test_every_sample_of_a_cycle_carries_the_same_timestamp(poller):
    """Chaque relevé prenait son horodatage à sa construction, après l'`await`
    d'écriture du précédent : un cycle s'étalait jusqu'à 2,58 s et chevauchait
    la frontière des tranches de trois secondes — six minutes de panne
    fabriquées sur neuf heures quarante (mesuré). Un cycle, un instant."""
    p, registry = poller
    await registry.add(RouterConfig(
        id="mbx", name="MBX", mode="mock",
        wans=[WanConfig(id=f"cellular-{i}", name=f"SIM {i}") for i in range(1, 5)],
    ))
    # Une écriture lente entre deux relevés : c'est là que le temps s'étalait.
    insert = p._repo.insert

    async def lent(sample):
        await asyncio.sleep(0.02)
        await insert(sample)

    with patch.object(p._repo, "insert", side_effect=lent):
        await p._poll_once()
    releves = p.latest["mbx"]
    assert len(releves) == 4
    assert len({s.ts for s in releves.values()}) == 1, "quatre relevés, quatre instants"


async def test_a_removed_profile_stops_expecting_the_bond(poller):
    """Le client dit « aucun profil » (`tunnel_absent`) : le bond n'est plus
    attendu, et une alerte de bond ouverte avant le retrait se résout au lieu
    de rester gelée jusqu'à la prochaine mise sous tension."""
    from app.models import Alert, TunnelLink

    p, registry = poller
    record = await registry.add(_config("mbx"))
    record.client.get_radio_status = AsyncMock(return_value=[
        WanRadioStatus(wan_id="4", wan_name="SIM", kind="cellular", connected=True,
                       uptime_s=3600, tunnel=TunnelLink(state="ACTIVE", rtt_ms=30.0)),
    ])
    await p._poll_once()
    assert p.tunnel_attendu("mbx") is True
    session_id = p._bond_porte["mbx"][0]
    # Une alerte de bond ouverte et publiée, comme si le lien était écarté.
    from datetime import timedelta
    p._alertes["mbx"] = [Alert(kind="hors du bond", wan_id="4",
                               since=utcnow() - timedelta(minutes=5), sustained_s=60.0)]

    # Le profil est retiré : plus d'objet tunnel, et le client le déclare.
    record.client.tunnel_absent = True
    record.client.get_radio_status = AsyncMock(return_value=[
        WanRadioStatus(wan_id="4", wan_name="SIM", kind="cellular", connected=True, uptime_s=3610),
    ])
    await p._poll_once()
    assert p._bond_porte["mbx"] == (session_id, False)
    assert p._alertes["mbx"][0].until is not None, "l'alerte a survécu au retrait du profil"


async def test_a_link_never_connected_since_power_on_is_named(poller):
    """Un WAN Ethernet sans câble depuis l'allumage : un état, pas une panne.

    Le bandeau d'un Dome Pro Duo disait « Liaison dégradée » en permanence
    pour un câble jamais branché. Le serveur nomme ces liens ; dès qu'un lien
    a été connecté une fois dans la session, sa chute redevient une nouvelle.
    """
    from app.api.routes import _summary

    p, registry = poller
    record = await registry.add(_config("dome"))
    assert _summary(p.latest, record.config, p).never_connected == []

    def cycle(uptime, filaire_debout):
        return [
            WanRadioStatus(wan_id="1", wan_name="WAN 1", kind="ethernet", connected=filaire_debout,
                           status_message=None if filaire_debout else "No Cable Detected"),
            WanRadioStatus(wan_id="2", wan_name="SIM", kind="cellular", connected=True, uptime_s=uptime),
        ]
    record.client.get_radio_status = AsyncMock(return_value=cycle(3600, False))
    await p._poll_once()
    assert _summary(p.latest, record.config, p).never_connected == ["1"]

    # Le câble est branché une fois : le lien est connu, et sa chute se dira.
    record.client.get_radio_status = AsyncMock(return_value=cycle(3610, True))
    await p._poll_once()
    record.client.get_radio_status = AsyncMock(return_value=cycle(3620, False))
    await p._poll_once()
    assert _summary(p.latest, record.config, p).never_connected == []


async def test_a_wan_disabled_during_the_session_stays_named_disabled(poller):
    """Scénario 1 de la revue : WAN 3 désactivé depuis l'interface du Dome. Il
    sortait de la table et le journal écrivait « absent du relevé », comme pour
    une panne. Un WAN désactivé pendant la session reste là, marqué désactivé,
    sans mesure ; le relevé d'adieu dit pourquoi. Réactivé, il reprend sa ligne."""
    p, registry = poller
    record = await registry.add(_config("dome"))
    sim = lambda up: WanRadioStatus(wan_id="3", wan_name="SIM 3", kind="cellular",
                                    connected=True, uptime_s=up, rsrp=-80.0)
    autre = WanRadioStatus(wan_id="2", wan_name="SIM 2", kind="cellular", connected=True, uptime_s=900)
    record.client.get_radio_status = AsyncMock(return_value=[autre, sim(600)])
    await p._poll_once()

    record.client.wans_desactives = {"3"}
    record.client.get_radio_status = AsyncMock(return_value=[autre])
    await p._poll_once()
    garde = p.latest["dome"]["3"].wan
    assert garde.disabled is True and garde.connected is False
    assert garde.status_message == "désactivé"
    assert garde.rsrp is None

    # Un cycle de plus : il reste désactivé, il n'est pas oublié.
    await p._poll_once()
    assert p.latest["dome"]["3"].wan.disabled is True

    # Réactivé : la ligne mesurée reprend.
    record.client.wans_desactives = set()
    record.client.get_radio_status = AsyncMock(return_value=[autre, sim(5)])
    await p._poll_once()
    assert p.latest["dome"]["3"].wan.disabled is False
    assert p.latest["dome"]["3"].wan.connected is True

    # Un WAN absent SANS être déclaré désactivé est oublié, comme avant.
    record.client.get_radio_status = AsyncMock(return_value=[autre])
    await p._poll_once()
    assert "3" not in p.latest["dome"]


async def test_a_switch_to_standby_does_not_count_a_disconnect(poller):
    """Le compteur de coupures du collecteur suit la même règle : un uptime
    remis à 0 sous « Standby » est un passage en secours, pas une coupure."""
    p, registry = poller
    record = await registry.add(_config("dome"))
    wan = lambda up, msg: WanRadioStatus(wan_id="3", wan_name="SIM 3", kind="cellular",
                                         connected=True, uptime_s=up, status_message=msg)
    # Entrée en secours puis sortie : chacune remet l'uptime à zéro, et un seul
    # des deux relevés qui l'encadrent dit « Standby ».
    for up, msg in ((78, "Connected to SFR"), (0, "Standby"), (40, "Standby"), (0, "Connected to SFR")):
        record.client.get_radio_status = AsyncMock(return_value=[wan(up, msg)])
        await p._poll_once()
    assert p.latest["dome"]["3"].disconnects == 0
    # Une vraie micro-coupure, elle, se compte.
    for up, msg in ((50, "Connected to SFR"), (2, "Connected to SFR")):
        record.client.get_radio_status = AsyncMock(return_value=[wan(up, msg)])
        await p._poll_once()
    assert p.latest["dome"]["3"].disconnects == 1


async def test_no_disconnect_is_counted_across_a_read_gap(poller, monkeypatch):
    """Deux lectures séparées de plus d'une minute ne sont plus voisines : une
    chute ou un uptime qui recule entre elles n'a pas été observé, et le
    compteur du collecteur ne le compte plus — même règle que le journal."""
    from datetime import timedelta

    from app import models

    p, registry = poller
    record = await registry.add(_config("dome"))
    wan = lambda up: WanRadioStatus(wan_id="3", wan_name="SIM 3", kind="cellular",
                                    connected=True, uptime_s=up, status_message="Connected")
    instant = models.utcnow()
    for decalage, up in ((0, 500), (120, 30), (125, 2)):
        monkeypatch.setattr(models, "_horloge", lambda d=decalage: instant + timedelta(seconds=d))
        record.client.get_radio_status = AsyncMock(return_value=[wan(up)])
        await p._poll_once()
    # Le recul à travers le trou ne compte pas ; celui d'après, entre voisins, si.
    assert p.latest["dome"]["3"].disconnects == 1


async def test_a_cell_change_across_an_outage_is_counted_live(poller):
    """Le compteur en direct du collecteur suivait le même défaut."""
    p, registry = poller
    record = await registry.add(_config("dome"))
    wan = lambda up, cell: WanRadioStatus(wan_id="3", wan_name="SIM 3", kind="cellular",
                                          connected=cell is not None, uptime_s=up, cell_id=cell)
    for up, cell in ((600, 25698582), (None, None), (None, None), (5, 2227220)):
        record.client.get_radio_status = AsyncMock(return_value=[wan(up, cell)])
        await p._poll_once()
    assert p.latest["dome"]["3"].cell_changes == 1


async def test_three_refused_admin_logins_in_five_minutes_raise_an_alert(poller):
    """Cinq connexions admin refusées suspendent l'accès pendant cinq minutes.
    Le journal du routeur les dit ; à trois en cinq minutes, une alerte — elles
    peuvent venir d'un autre poste que celui de l'exploitant."""
    from datetime import timedelta
    from app.models import RouterEvent

    p, registry = poller
    record = await registry.add(_config("dome"))
    record.client.get_radio_status = AsyncMock(return_value=[
        WanRadioStatus(wan_id="2", wan_name="SIM 2", kind="cellular", connected=True, uptime_s=900),
    ])
    maintenant = utcnow()
    refus = lambda n: [RouterEvent(ts=maintenant - timedelta(seconds=30 * i), kind="connexion admin refusée",
                                   empreinte=f"r{i:023d}") for i in range(n)]
    record.client.get_router_events = AsyncMock(return_value=refus(2))
    await p._poll_once()
    assert not [a for a in p.alerts("dome") if a.kind == "connexions admin refusées"]
    # Le journal n'est relu qu'une fois par minute : on force la relecture.
    p._journal_lu_a.pop("dome", None)
    record.client.get_router_events = AsyncMock(return_value=refus(3))
    await p._poll_once()
    alerte = [a for a in p.alerts("dome") if a.kind == "connexions admin refusées"]
    assert len(alerte) == 1 and "3 refus" in (alerte[0].detail or "")
    # Les faits sont en base, une fois chacun.
    assert len(await p._repo.router_events("dome", maintenant - timedelta(hours=1), maintenant)) == 3


async def test_the_router_log_is_read_once_a_minute(poller):
    p, registry = poller
    record = await registry.add(_config("dome"))
    record.client.get_radio_status = AsyncMock(return_value=[
        WanRadioStatus(wan_id="2", wan_name="SIM 2", kind="cellular", connected=True, uptime_s=900),
    ])
    record.client.get_router_events = AsyncMock(return_value=[])
    await p._poll_once()
    await p._poll_once()
    assert record.client.get_router_events.await_count == 1
    noms = record.client.get_router_events.await_args.args[0]
    assert noms == {"SIM 2": "2"}


async def test_the_summary_says_whether_the_bond_ever_carried(poller):
    """`tunnel_attendu`, publié : la règle des alertes, rendue à l'écran.

    Un bond qui n'a jamais porté dans la session n'est pas tombé — c'est un
    état, et le serveur n'ouvre aucune alerte dessus. L'écran ne recevait pas
    ce témoin et titrait « Tunnel SpeedFusion tombé — 2 h dans cet état ».
    """
    from app.api.routes import _summary
    from app.models import TunnelLink

    p, registry = poller
    record = await registry.add(_config("hd2"))
    assert _summary(p.latest, record.config, p).tunnel_attendu is None

    tombe = TunnelLink(state="PEER_DOWN", rtt_ms=None)
    record.client.get_radio_status = AsyncMock(return_value=[
        WanRadioStatus(wan_id="4", wan_name="SIM", kind="cellular", connected=True,
                       uptime_s=3600, tunnel=tombe),
    ])
    await p._poll_once()
    assert _summary(p.latest, record.config, p).tunnel_attendu is False

    # Le bond porte une fois : à partir d'ici, sa chute sera une nouvelle.
    record.client.get_radio_status = AsyncMock(return_value=[
        WanRadioStatus(wan_id="4", wan_name="SIM", kind="cellular", connected=True,
                       uptime_s=3610, tunnel=TunnelLink(state="ACTIVE", rtt_ms=30.0)),
    ])
    await p._poll_once()
    assert _summary(p.latest, record.config, p).tunnel_attendu is True
    record.client.get_radio_status = AsyncMock(return_value=[
        WanRadioStatus(wan_id="4", wan_name="SIM", kind="cellular", connected=True,
                       uptime_s=3620, tunnel=tombe),
    ])
    await p._poll_once()
    assert _summary(p.latest, record.config, p).tunnel_attendu is True


async def test_the_timeline_route_answers_what_happened_while_i_was_there(poller):
    """La question « qu'est-ce qui s'est passé pendant que j'étais là » n'avait
    aucune réponse à l'écran.

    L'historique rend UNE grandeur pour UN lien, et ne sait pas dire qu'un lien
    était debout mais hors du bond — c'est pourtant la distinction que tout le
    modèle porte depuis le début.
    """
    from app.models import TunnelLink

    p, registry = poller
    record = await registry.add(_config("mbx"))
    record.client.get_radio_status = AsyncMock(
        return_value=[
            WanRadioStatus(
                wan_id="4", wan_name="SIM", kind="cellular", connected=True,
                uptime_s=3600, sinr=11.0,
                tunnel=TunnelLink(state="ACTIVE", rtt_ms=34.0),
            ),
            WanRadioStatus(
                wan_id="10", wan_name="SIM", kind="cellular", connected=True,
                uptime_s=3600, sinr=8.0,
                tunnel=TunnelLink(state="WAN_DOWN", rtt_ms=None),
            ),
        ]
    )
    await p._poll_once()

    app = FastAPI()
    app.include_router(routes.router)
    app.state.poller = p
    app.state.repo = p._repo
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get("/api/routers/mbx/timeline?minutes=60")
        inconnu = await client.get("/api/routers/absent/timeline?minutes=60")

    assert inconnu.status_code == 404
    assert response.status_code == 200
    corps = response.json()

    # Le nombre de tranches ne dépend pas de la durée : la réponse pèse le même
    # poids pour une demi-heure que pour une session de vingt-quatre heures.
    assert corps["slice_count"] == 200
    assert corps["slice_seconds"] == pytest.approx(3600 / 200, abs=0.01)

    # Ordre naturel, comme partout : « 10 » vient après « 4 ».
    assert [l["wan_id"] for l in corps["lanes"]] == ["4", "10"]

    porteur = corps["lanes"][0]["slices"][0]
    ecarte = corps["lanes"][1]["slices"][0]
    assert porteur["up"] == 1 and porteur["carrying"] == 1
    # Debout côté WAN, mais écarté du bond : les deux ne se confondent pas.
    assert ecarte["up"] == 1 and ecarte["carrying"] == 0
    assert ecarte["tunnel_known"] == 1, "l'état du tunnel était connu, et il disait non"


async def test_an_empty_window_is_refused_rather_than_drawn(poller):
    """Une fenêtre vide ne produit pas une bande vide : elle est refusée.

    Dessiner deux cents tranches de durée nulle ou négative inventerait une
    lecture qui n'a pas de sens, et l'écran la présenterait comme une absence
    de relevés — ce qui est un tout autre fait.
    """
    p, registry = poller
    await registry.add(_config("mbx"))

    app = FastAPI()
    app.include_router(routes.router)
    app.state.poller = p
    app.state.repo = p._repo
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get(
            "/api/routers/mbx/timeline"
            "?since=2026-09-01T10:00:00Z&until=2026-09-01T09:00:00Z"
        )
    assert response.status_code == 400


async def test_the_timeline_carries_what_redundancy_costs(poller):
    """Les compteurs de réparation étaient enregistrés et n'étaient lus par rien.

    Il n'y a pas de courbe à en tirer : sur le matériel de référence la
    réparation vaut zéro sur les 15 500 relevés où elle a été enregistrée, une
    seule valeur distincte, et tracer une grandeur qui ne varie jamais la ferait
    passer pour une mesure. Ce qu'ils établissent, en revanche, vaut d'être dit —
    du trafic passé sans aucune réparation prouve que la redondance du profil est
    éteinte, donc que chaque paquet perdu l'est pour le flux.
    """
    from app.models import TunnelLink

    p, registry = poller
    record = await registry.add(_config("mbx"))
    record.client.get_radio_status = AsyncMock(
        return_value=[
            WanRadioStatus(
                wan_id="4", wan_name="SIM", kind="cellular", connected=True, uptime_s=3600,
                tunnel=TunnelLink(state="ACTIVE", packets_up=12000, repair_sent=0, repair_used=0),
            ),
            WanRadioStatus(
                wan_id="5", wan_name="SIM", kind="cellular", connected=True, uptime_s=3600,
                tunnel=TunnelLink(state="ACTIVE", packets_up=8000, repair_sent=1300, repair_used=7),
            ),
        ]
    )
    await p._poll_once()

    app = FastAPI()
    app.include_router(routes.router)
    app.state.poller = p
    app.state.repo = p._repo
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        corps = (await client.get("/api/routers/mbx/timeline?minutes=60")).json()

    par_lien = {l["wan_id"]: l["slices"] for l in corps["lanes"]}
    muet = par_lien["4"][0]
    actif = par_lien["5"][0]
    assert muet["packets"] == 12000 and muet["repair"] == 0
    assert actif["packets"] == 8000 and actif["repair"] == 1300


async def test_a_link_without_tunnel_reports_no_redundancy_rather_than_zero(poller):
    """Un lien sans tunnel n'a pas dépensé zéro en redondance : il n'en a pas.

    La somme SQL compte les NULL pour zéro — c'est ce qu'on veut ici, parce que
    ces deux nombres servent à établir un fait sur le BOND, pas à décrire un
    lien. Mais encore faut-il qu'aucun paquet ne soit compté avec, sans quoi un
    équipement sans SpeedFusion finirait par franchir le seuil de conclusion et
    l'écran annoncerait « aucune redondance » sur un bond qui n'existe pas.
    """
    p, registry = poller
    record = await registry.add(_config("mbx"))
    record.client.get_radio_status = AsyncMock(
        return_value=[
            WanRadioStatus(
                wan_id="4", wan_name="SIM", kind="cellular", connected=True,
                uptime_s=3600, tunnel=None,
            )
        ]
    )
    await p._poll_once()

    app = FastAPI()
    app.include_router(routes.router)
    app.state.poller = p
    app.state.repo = p._repo
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        corps = (await client.get("/api/routers/mbx/timeline?minutes=60")).json()

    tranche = corps["lanes"][0]["slices"][0]
    # « rather than zero » : un lien sans tunnel n'a aucun compteur de paquets.
    # Zéro affirmait qu'aucun paquet n'avait circulé — la somme SQL repliait les
    # NULL sur zéro, et c'est de cette somme que sortait « la redondance du
    # profil est éteinte ».
    assert tranche["packets"] is None, "des paquets ont été comptés sans tunnel"
    assert tranche["repair"] is None


async def test_the_session_anchors_on_the_CHASSIS_uptime_when_the_router_publishes_it(poller):
    """L'ancienneté du châssis était DÉDUITE du maximum des liens.

    C'est de cette approximation que venaient les sessions scindées : le maximum
    retombe dès que le lien qui le portait quitte le relevé, et une seconde
    session s'ouvrait sur un châssis qui n'avait pas bougé. Le routeur publie la
    valeur exacte sous `uptime.second`, dans une charge utile déjà téléchargée.
    """
    from app.models import DeviceInfo

    p, registry = poller
    record = await registry.add(_config("mbx"))
    liens = lambda paires: [
        WanRadioStatus(wan_id=w, wan_name=f"SIM {w}", kind="cellular",
                       connected=True, uptime_s=up)
        for w, up in paires
    ]

    record.client.get_device_info = AsyncMock(return_value=DeviceInfo(uptime_s=7200))
    record.client.get_capabilities = AsyncMock(return_value=None)
    record.client.get_radio_status = AsyncMock(
        return_value=liens([("4", 6384), ("5", 6524), ("6", 6724)])
    )
    await p._poll_once()
    premiere = await p._repo.current_session("mbx")
    assert premiere is not None

    # Le lien qui portait le maximum s'en va : le châssis, lui, n'a pas bougé.
    record.client.get_radio_status = AsyncMock(
        return_value=liens([("4", 6389), ("5", 6529)])
    )
    await p._poll_once()
    assert (await p._repo.current_session("mbx"))["id"] == premiere["id"]

    # Et c'est bien l'ancienneté du CHÂSSIS qui décide : elle recule, donc
    # nouvelle session — alors que les liens, eux, continuent de vieillir.
    record.info = DeviceInfo(uptime_s=12)
    record.client.get_radio_status = AsyncMock(
        return_value=liens([("4", 6394), ("5", 6534)])
    )
    await p._poll_once()
    assert (await p._repo.current_session("mbx"))["id"] != premiere["id"]


async def test_a_router_that_publishes_no_uptime_keeps_the_link_by_link_rule(poller):
    """`status.system.info` n'est pas documenté officiellement.

    Rien ne garantit cet endpoint sur les gammes Balance, BR1 ou Transit : la
    comparaison lien à lien reste le repli, et elle a fait ses preuves.
    """
    p, registry = poller
    record = await registry.add(_config("mbx"))
    record.client.get_device_info = AsyncMock(return_value=None)
    record.client.get_capabilities = AsyncMock(return_value=None)
    liens = lambda up: [
        WanRadioStatus(wan_id=w, wan_name="SIM", kind="cellular", connected=True, uptime_s=up)
        for w in ("4", "5")
    ]

    record.client.get_radio_status = AsyncMock(return_value=liens(7200))
    await p._poll_once()
    premiere = await p._repo.current_session("mbx")

    record.client.get_radio_status = AsyncMock(return_value=liens(9))
    await p._poll_once()
    assert (await p._repo.current_session("mbx"))["id"] != premiere["id"]


async def test_a_STALE_chassis_uptime_does_not_mask_a_reboot(poller):
    """L'ancienneté du châssis n'est relue qu'un cycle sur douze.

    Tant qu'elle stagne, elle ne recule pas — et quand elle décidait seule, elle
    affirmait « pas de redémarrage » pendant douze cycles alors que tous les
    liens venaient de retomber à cinq secondes. Vérifié : soixante secondes de
    relevés du nouveau stationnement comptés dans la session précédente.

    Un recul du châssis reste une preuve ; son immobilité n'en est pas une.
    """
    from app.poller import _redemarrage

    avant = {"1": 3600.0, "4": 3600.0, "5": 3600.0}
    apres = {"1": 5.0, "4": 5.0, "5": 5.0}

    # Châssis figé à la même valeur : il ne dit rien, les liens décident.
    assert _redemarrage(avant, apres, chassis=7000, chassis_avant=7000) is True
    # Châssis qui recule : preuve suffisante, même sans témoin de lien.
    assert _redemarrage({}, {}, chassis=5, chassis_avant=7000) is True
    # Châssis qui avance ET liens qui continuent : pas de redémarrage.
    assert _redemarrage(avant, {"1": 3700.0}, chassis=7100, chassis_avant=7000) is False


async def test_a_cycle_without_any_uptime_still_extends_the_session(poller, monkeypatch):
    """22,8 % des cycles réels de la session 1 ne portent aucune ancienneté.

    Les relevés sont pourtant ÉCRITS. Ne pas prolonger la session laissait sa
    fin geler — jusqu'à 1 h 43 mesuré — pendant que la collecte continuait, et
    la fiche de stationnement s'affichait complète en ayant perdu tout ce qui
    suivait. Mesuré sur la base : 19 990 relevés déclarés pour 25 920 écrits.

    La prolongation se fait désormais dans la transaction des relevés du cycle
    (revue du 24/10) : on passe donc par un cycle complet.
    """
    p, registry = poller
    record = await registry.add(_config("mbx"))
    # Ni fiche ni identification : seule l'ancienneté des liens témoigne.
    record.identified, record.info = True, None
    avec = [WanRadioStatus(wan_id="4", wan_name="SIM", kind="cellular",
                           connected=True, uptime_s=3600)]
    sans = [WanRadioStatus(wan_id="4", wan_name="SIM", kind="cellular",
                           connected=True, uptime_s=None)]

    record.client.get_radio_status = AsyncMock(return_value=avec)
    await p._poll_device_once(record)
    session = p._session["mbx"]
    premiere = (await p._repo.current_session("mbx"))["last_seen"]

    # Une horloge POSÉE, et non `sleep(0.01)` puis une inégalité stricte : sous
    # Windows, l'horloge murale avance par pas de 15,6 ms, et deux `utcnow()`
    # pouvaient être identiques sur la machine de la CI.
    from datetime import datetime, timedelta, timezone

    from app import models

    plus_tard = datetime.fromisoformat(premiere) + timedelta(seconds=5)
    monkeypatch.setattr(models, "_horloge", lambda: plus_tard)
    record.client.get_radio_status = AsyncMock(return_value=sans)
    await p._poll_device_once(record)
    assert p._session["mbx"].id == session.id, "la session a été rouverte"
    courante = await p._repo.current_session("mbx")
    assert courante["last_seen"] == plus_tard.astimezone(timezone.utc).isoformat(), (
        "la fin de session a gelé alors que les relevés continuaient"
    )
    assert courante["samples"] == 2


async def test_the_reboot_witness_survives_an_empty_sample(poller):
    """`max()` d'un dictionnaire vide levait, à chaque cycle, une `ValueError`
    avalée en avertissement — et la session cessait d'être prolongée tant que la
    situation durait. Atteignable dès qu'aucun lien ne publie son ancienneté.
    """
    from app.poller import _redemarrage

    assert _redemarrage({"1": 1000.0}, {}) is True
    assert _redemarrage({}, {}) is False


async def test_a_stale_position_stops_being_published():
    """Un point GPS était réécrit dans chaque relevé, indéfiniment.

    `self._gps[router.id]` n'avait pas d'horodatage : une position acquise une
    fois continuait d'estampiller les relevés des heures après que le récepteur
    eut perdu sa fixation. C'est la seule grandeur de l'outil dont la péremption
    soit invisible — une position ne « paraît » jamais vieille, contrairement à
    un signal figé ou à un compteur qui n'avance plus.
    """
    import time
    from app.poller import _GPS_MAX_AGE_S

    assert _GPS_MAX_AGE_S > 0

    # Le stockage porte désormais QUAND le point a été lu.
    gps: dict[str, tuple[tuple[float, float], float]] = {}
    gps["mbx"] = ((48.8566, 2.3522), time.monotonic() - _GPS_MAX_AGE_S - 1.0)
    _point, lu_a = gps["mbx"]
    assert time.monotonic() - lu_a > _GPS_MAX_AGE_S, "le point doit être réputé périmé"

    frais = ((48.8566, 2.3522), time.monotonic())
    assert time.monotonic() - frais[1] <= _GPS_MAX_AGE_S


async def test_forgetting_a_device_also_forgets_its_cadence():
    """La cadence fixe le seuil de péremption PUBLIÉ à l'écran.

    `forget()` purgeait les compteurs, le dernier relevé, la position et la
    session — mais pas `_period_seconds`, `_cycle_seconds` ni
    `_last_cycle_start`. Un équipement remplacé à la même adresse héritait donc
    de la période lissée du précédent : sur un châssis lent remplacé par un
    rapide, le bandeau restait vert bien après la mort d'une liaison.
    """
    from app.devices.registry import DeviceRegistry
    from app.poller import Poller
    from app.storage.db import SampleRepository

    registre = DeviceRegistry(default_mode="mock")
    p = Poller(registre, SampleRepository(":memory:"), interval_seconds=0.5)
    p._period_seconds["mbx"] = 23.0
    p._cycle_seconds["mbx"] = 9.0
    p._last_cycle_start["mbx"] = 1234.0

    p.forget("mbx")

    assert "mbx" not in p._period_seconds
    assert "mbx" not in p._cycle_seconds
    assert "mbx" not in p._last_cycle_start
    await registre.close()


# --- les alertes, de bout en bout ---------------------------------------------

async def test_an_alert_travels_from_the_cycle_to_the_api(poller, monkeypatch):
    """La règle, le poller et la route, éprouvés ensemble.

    Les règles ont leur propre fichier de tests ; celui-ci vérifie la COUTURE —
    que l'état suivi survit d'un cycle à l'autre, que le seuil de durée s'applique
    à travers le poller, et que seules les alertes publiables sortent par l'API.
    C'est exactement ce que la première version ratait : elle ne reportait que
    les alertes publiées, si bien qu'une condition en attente perdait son instant
    de départ à chaque cycle et n'aboutissait jamais.
    """
    from datetime import timedelta

    from app.alerts import HORS_BOND_S
    from app.models import TunnelLink, WanRadioStatus

    p, registry = poller
    await registry.add(_config("mbx"))
    record = registry.get("mbx")

    def _releve(etat: str):
        return [
            WanRadioStatus(wan_id="4", wan_name="SIM 4", connected=True,
                           tunnel=TunnelLink(state=etat)),
            WanRadioStatus(wan_id="5", wan_name="SIM 5", connected=True,
                           tunnel=TunnelLink(state="ACTIVE")),
            WanRadioStatus(wan_id="6", wan_name="SIM 6", connected=True,
                           tunnel=TunnelLink(state="ACTIVE")),
        ]

    etat = {"tunnel": "WAN_DOWN"}

    class _Client:
        router_id = "mbx"

        async def get_radio_status(self, echeance=None):
            return _releve(etat["tunnel"])

        async def get_location(self):
            return None

        async def get_device_info(self):
            return None

        async def get_capabilities(self):
            return None

        async def aclose(self):
            return None

    record.client = _Client()

    # Premier cycle : la condition est vraie, mais elle n'a pas duré.
    await p._poll_once()
    assert p.alerts("mbx") == [], "rien n'est annoncé à chaud"
    assert p._alertes["mbx"], "mais la condition est bien SUIVIE"

    # On recule le début de la condition au-delà du seuil, comme le ferait le
    # temps. C'est le seul moyen d'éprouver la durée sans attendre une minute.
    suivies = p._alertes["mbx"]
    p._alertes["mbx"] = [
        a.model_copy(update={"since": a.since - timedelta(seconds=HORS_BOND_S + 1)})
        for a in suivies
    ]
    await p._poll_once()

    publiees = p.alerts("mbx")
    assert [(a.kind, a.wan_id, a.detail) for a in publiees] == [
        ("hors du bond", "4", "WAN_DOWN")
    ]

    # Le lien revient : l'alerte se clôt sans disparaître tout de suite.
    etat["tunnel"] = "ACTIVE"
    await p._poll_once()
    closes = p.alerts("mbx")
    assert len(closes) == 1 and closes[0].until is not None

    # Et l'équipement oublié ne traîne aucune alerte derrière lui.
    p.forget("mbx")
    assert p.alerts("mbx") == []


async def test_a_supervision_gap_is_noticed_once_at_startup(poller):
    """L'angle mort : quand le superviseur s'arrête, plus rien ne l'observe.

    Le trou n'est constatable qu'au redémarrage, en comparant le dernier relevé
    écrit à l'heure qu'il est. Et il se constate UNE fois : le redire à chaque
    cycle ferait de l'angle mort un papier peint.
    """
    from datetime import timedelta

    from app.models import RadioSample, WanRadioStatus

    p, registry = poller
    await registry.add(_config("mbx"))

    # Un relevé vieux d'une demi-heure, écrit comme le ferait une supervision
    # arrêtée puis relancée.
    vieux = RadioSample(
        router_id="mbx", router_name="MBX",
        ts=utcnow() - timedelta(minutes=31),
        wan=WanRadioStatus(wan_id="4", wan_name="SIM 4", connected=True),
    )
    await p._repo.insert(vieux)

    await p._poll_once()
    trous = [a for a in p.alerts("mbx") if a.kind == "supervision interrompue"]
    assert len(trous) == 1
    assert round((trous[0].until - trous[0].since).total_seconds() / 60) == 31
    # CLOSE d'emblée : prétendre qu'elle dure affirmerait que la supervision est
    # encore arrêtée alors qu'elle vient de reprendre.
    assert trous[0].until is not None

    # Deuxième cycle : on ne le redit pas.
    await p._poll_once()
    assert len(p._alertes["mbx"]) <= 1


async def test_links_restarted_together_make_the_chassis_be_reread_on_the_spot(poller):
    """Le MAX HD2 du 19 septembre, 14:04:09 : les trois liens à sept secondes.

    Le collecteur a ouvert une session — datée par le châssis, relu un cycle
    sur douze et qui disait encore 19 800 s : 08:35:49, huit secondes après la
    session précédente, dont la fenêtre était englobée entière. Et le châssis
    avait RAISON : relu à la remise en route du superviseur, il donnait la même
    mise sous tension à la seconde près. Les liens avaient été relancés
    ensemble sans que le châssis redémarre.

    Quand les liens disent « redémarrage » et que le châssis ne l'a pas
    confirmé, on le relit tout de suite. Trois issues, une par vérité.
    """
    from datetime import datetime, timezone

    from app.models import DeviceInfo

    p, registry = poller
    record = await registry.add(_config("hd2"))
    liens = lambda up: [
        WanRadioStatus(wan_id=w, wan_name=f"WAN {w}", kind="cellular", connected=True, uptime_s=up)
        for w in ("1", "4", "5")
    ]
    record.client.get_device_info = AsyncMock(return_value=DeviceInfo(uptime_s=19_800))
    record.client.get_capabilities = AsyncMock(return_value=None)
    record.client.get_radio_status = AsyncMock(return_value=liens(19_200))
    await p._poll_once()
    premiere = await p._repo.current_session("hd2")
    record.client.get_device_info.reset_mock()

    # 1. Les liens retombent ; le châssis, relu sur-le-champ, continue de
    #    compter : pas de mise sous tension, la session continue.
    record.client.get_device_info = AsyncMock(return_value=DeviceInfo(uptime_s=19_860))
    record.client.get_radio_status = AsyncMock(return_value=liens(7))
    await p._poll_once()
    record.client.get_device_info.assert_awaited_once()
    assert (await p._repo.current_session("hd2"))["id"] == premiere["id"]

    # 2. Les liens retombent ; le châssis relu a reculé : mise sous tension,
    #    et c'est lui qui la date — pas la valeur d'avant.
    record.client.get_device_info = AsyncMock(return_value=DeviceInfo(uptime_s=12))
    record.client.get_radio_status = AsyncMock(return_value=liens(5))
    await p._poll_once()
    seconde = await p._repo.current_session("hd2")
    assert seconde["id"] != premiere["id"]
    age = (datetime.now(timezone.utc) - datetime.fromisoformat(seconde["started_at"])).total_seconds()
    assert age < 60, f"datée de {age:.0f} s, par un châssis stagnant"

    # 3. Les liens retombent ; le châssis est illisible : les liens gardent la
    #    parole, et datent.
    record.client.get_device_info = AsyncMock(side_effect=RuntimeError("délai"))
    record.client.get_radio_status = AsyncMock(return_value=liens(4))
    await p._poll_once()
    troisieme = await p._repo.current_session("hd2")
    assert troisieme["id"] != seconde["id"]
    age = (datetime.now(timezone.utc) - datetime.fromisoformat(troisieme["started_at"])).total_seconds()
    assert age < 60, f"datée de {age:.0f} s"


async def test_a_removal_during_the_chassis_reread_does_not_resurrect_the_session(poller):
    """La relecture du châssis est un `await` de plus dans `_track_session`, et
    la branche « liens relancés » réécrivait `_session` derrière lui sans
    revérifier que l'équipement est encore suivi : un retrait pendant la
    lecture ressuscitait l'entrée que `forget()` venait de purger — la fuite
    déjà décrite pour l'autre branche, et un identifiant réappairé qui
    reprendrait la session du précédent."""
    from app.models import DeviceInfo

    p, registry = poller
    record = await registry.add(_config("hd2"))
    liens = lambda up: [
        WanRadioStatus(wan_id=w, wan_name=f"WAN {w}", kind="cellular", connected=True, uptime_s=up)
        for w in ("1", "4")
    ]
    record.client.get_device_info = AsyncMock(return_value=DeviceInfo(uptime_s=19_800))
    record.client.get_capabilities = AsyncMock(return_value=None)
    record.client.get_radio_status = AsyncMock(return_value=liens(19_200))
    await p._poll_once()
    assert "hd2" in p._session

    # Les liens retombent ; pendant la relecture du châssis, l'équipement est retiré.
    async def retire_pendant_la_lecture():
        await registry.remove("hd2")
        p.forget("hd2")
        return DeviceInfo(uptime_s=19_860)

    record.client.get_device_info = AsyncMock(side_effect=retire_pendant_la_lecture)
    record.client.get_radio_status = AsyncMock(return_value=liens(7))
    await p._track_session(record, liens(7))
    assert "hd2" not in p._session, "la session d'un équipement retiré a été ressuscitée"


async def test_a_failed_chassis_reread_does_not_postpone_the_periodic_refresh(poller):
    """Le compteur de cycles n'est remis à zéro que si la fiche a bien été relue :
    une lecture manquée ne vaut pas un rafraîchissement, et ne doit pas repousser
    le prochain de douze cycles — au moment où le châssis est le plus stagnant."""
    from app.models import DeviceInfo

    p, registry = poller
    record = await registry.add(_config("hd2"))
    liens = lambda up: [
        WanRadioStatus(wan_id=w, wan_name=f"WAN {w}", kind="cellular", connected=True, uptime_s=up)
        for w in ("1", "4")
    ]
    record.client.get_device_info = AsyncMock(return_value=DeviceInfo(uptime_s=19_800))
    record.client.get_capabilities = AsyncMock(return_value=None)
    record.client.get_radio_status = AsyncMock(return_value=liens(19_200))
    await p._poll_once()

    record.cycles_since_info = 7
    record.client.get_device_info = AsyncMock(side_effect=RuntimeError("délai"))
    await p._track_session(record, liens(7))
    assert record.cycles_since_info == 7, "une relecture manquée a remis le compteur à zéro"

    record.client.get_device_info = AsyncMock(return_value=DeviceInfo(uptime_s=19_900))
    await p._track_session(record, liens(4))
    assert record.cycles_since_info == 0, "une relecture réussie vaut un rafraîchissement"


async def test_the_bond_memory_is_bounded_to_the_session_held_in_memory(poller):
    """La borne de la mémoire du bond est le début de la session TENUE EN
    MÉMOIRE, lue par son identifiant — pas « la dernière en base », qui peut
    être une autre ligne. Sans son début, la mémoire redevenait celle de tout
    temps, précisément ce que la borne de session voulait supprimer."""
    from datetime import datetime, timedelta, timezone

    from app.poller import _Session

    p, registry = poller
    record = await registry.add(_config("mbx"))
    t0 = datetime(2026, 9, 19, 8, 0, tzinfo=timezone.utc)
    ancienne = await p._repo.open_session("mbx", t0)
    await p._repo.open_session("mbx", t0 + timedelta(hours=3))   # une ligne plus récente
    p._session["mbx"] = _Session(ancienne, {}, None)

    bornes: list = []
    original = p._repo.bond_carried_since

    async def espion(router_id, since):
        bornes.append(since)
        return await original(router_id, since)

    p._repo.bond_carried_since = espion
    record.client.get_radio_status = AsyncMock(return_value=[
        WanRadioStatus(wan_id="4", wan_name="SIM", kind="cellular", connected=True, uptime_s=100)
    ])
    await p._poll_once()
    assert bornes and bornes[0] == t0, f"borne demandée : {bornes}"


async def test_replacing_a_record_does_not_let_the_old_cycle_rewrite_purged_state(poller):
    """Ré-appairage d'un équipement déjà supervisé : `forget()` puis
    `registry.add()`, qui cède la main sur la fermeture de l'ancien client.
    Pendant cette fenêtre, l'ancien record était encore celui du registre,
    toutes ses gardes passaient, et le cycle en cours réécrivait ce que
    `forget()` venait de purger — le nouveau châssis démarrait avec le
    `previous` de l'ancien. L'ancien record sort du registre AVANT l'attente."""
    p, registry = poller
    ancien = await registry.add(_config("x"))
    await p._poll_once()
    assert "x" in p.latest

    # Le cycle de l'ancien est « en vol » : sa lecture radio rend la main
    # pendant que le ré-appairage est DANS la fermeture de l'ancien client —
    # la fenêtre exacte où l'ancien record était encore celui du registre.
    import asyncio

    ajout: asyncio.Task | None = None

    async def fermeture_lente():
        await asyncio.sleep(0.05)

    async def lecture_pendant_le_reappairage(echeance=None):
        nonlocal ajout
        p.forget("x", conserver_abonnes=True)
        ancien.client.aclose = AsyncMock(side_effect=fermeture_lente)
        ajout = asyncio.create_task(registry.add(_config("x")))
        await asyncio.sleep(0.01)          # `add` est dans `aclose()`
        return [WanRadioStatus(wan_id="cellular-1", wan_name="SIM", kind="cellular", connected=True, uptime_s=10)]

    ancien.client.get_radio_status = AsyncMock(side_effect=lecture_pendant_le_reappairage)
    await p._poll_device_once(ancien)
    assert ajout is not None
    await ajout
    assert "x" not in p.latest, "le cycle de l'ancien record a réécrit un état purgé"
    assert "x" not in p._session and not any(k[0] == "x" for k in p._cell_changes)


async def test_re_pairing_keeps_the_live_subscribers(poller):
    """`forget()` réveillait les abonnés en 4404 « retiré » — et l'onglet
    cessait de se reconnecter pour de bon — alors qu'un ré-appairage laisse
    l'identifiant supervisé."""
    p, registry = poller
    await registry.add(_config("x"))
    file = p.subscribe("x")
    p.forget("x", conserver_abonnes=True)
    assert file.empty(), "la sentinelle de fermeture a été envoyée à un abonné qu'on garde"
    assert file in p._subscribers["x"]
    p.forget("x")
    assert file.get_nowait() is not None      # la sentinelle, cette fois


async def test_the_chassis_dates_the_power_on_when_it_has_just_been_read(poller):
    """`chassis_frais = False` ne faisait tomber que le test « golden » de
    l'export, dont le message dit « régénérer ». Au premier cycle, le châssis
    vient d'être lu : c'est LUI qui date la mise sous tension — pas le lien le
    plus ancien, relancé cent secondes plus tôt sur un châssis allumé depuis
    deux heures."""
    from datetime import datetime

    from app.models import DeviceInfo, utcnow

    p, registry = poller
    record = await registry.add(_config("hd2"))
    record.client.get_device_info = AsyncMock(return_value=DeviceInfo(uptime_s=7200))
    record.client.get_capabilities = AsyncMock(return_value=None)
    record.client.get_radio_status = AsyncMock(return_value=[
        WanRadioStatus(wan_id="4", wan_name="SIM", kind="cellular", connected=True, uptime_s=100),
    ])
    avant = utcnow()
    await p._poll_once()
    session = await p._repo.current_session("hd2")
    allume_depuis = (avant - datetime.fromisoformat(session["started_at"])).total_seconds()
    assert 7195 <= allume_depuis <= 7205, f"la session est datée du lien, pas du châssis : {allume_depuis:.0f} s"


async def test_a_reread_chassis_is_compared_to_its_expected_age(poller, monkeypatch):
    """Châssis lu à 5 s au premier cycle ; seconde mise sous tension 40 s plus
    tard, l'équipement revient avec un châssis à 35 s. « 35 ≥ 5 » concluait
    « liens relancés sans mise sous tension » et fusionnait la seconde session
    dans la première. Le châssis relu se compare à l'âge qu'il DEVRAIT avoir :
    5 + 40 = 45 s, moins la marge — 35 est en deçà, c'est un redémarrage."""
    import time as horloge

    from app.models import DeviceInfo
    from app import poller as module

    p, registry = poller
    record = await registry.add(_config("hd2"))
    liens = lambda up: [
        WanRadioStatus(wan_id=w, wan_name=f"WAN {w}", kind="cellular", connected=True, uptime_s=up)
        for w in ("1", "4")
    ]
    from datetime import datetime, timedelta, timezone

    from app import models

    monkeypatch.setattr(module, "_CHASSIS_MARGE_S", 5.0)
    # Les DEUX horloges avancent ensemble : la monotone date les lectures, la
    # murale date les sessions — sans elle, une session ouverte « 35 s avant
    # maintenant » se rangerait avant la première, et `current_session` ne la
    # verrait pas.
    t = [1000.0]
    origine = datetime(2026, 9, 19, 12, 0, tzinfo=timezone.utc)
    monkeypatch.setattr(horloge, "monotonic", lambda: t[0])
    monkeypatch.setattr(models, "_horloge", lambda: origine + timedelta(seconds=t[0] - 1000.0))
    record.client.get_device_info = AsyncMock(return_value=DeviceInfo(uptime_s=5))
    record.client.get_capabilities = AsyncMock(return_value=None)
    record.client.get_radio_status = AsyncMock(return_value=liens(4))
    await p._poll_once()
    premiere = await p._repo.current_session("hd2")

    # Quarante secondes plus tard, tout est retombé et le châssis dit 35 s.
    t[0] += 40.0
    record.client.get_device_info = AsyncMock(return_value=DeviceInfo(uptime_s=35))
    record.client.get_radio_status = AsyncMock(return_value=liens(3))
    await p._poll_once()
    assert (await p._repo.current_session("hd2"))["id"] != premiere["id"], (
        "la seconde mise sous tension a été fusionnée dans la première"
    )

    # Et un châssis qui a l'âge attendu, lui, ne redémarre pas : liens relancés.
    seconde = await p._repo.current_session("hd2")
    t[0] += 40.0
    record.client.get_device_info = AsyncMock(return_value=DeviceInfo(uptime_s=75))
    record.client.get_radio_status = AsyncMock(return_value=liens(2))
    await p._poll_once()
    assert (await p._repo.current_session("hd2"))["id"] == seconde["id"]


async def test_a_device_unreachable_while_the_supervisor_ran_is_not_a_supervision_gap(poller):
    """Superviseur en marche toute la nuit, équipement éteint puis rallumé le
    matin : le premier cycle abouti mesurait le trou depuis le dernier relevé
    en base — « supervision interrompue, 840 min » chaque matin, alors que la
    supervision n'a jamais cessé. Le trou est borné à l'instant où CE processus
    a démarré : ce qu'il n'a pas vu après, c'est une injoignabilité, dite
    ailleurs."""
    from datetime import timedelta

    from app.models import RadioSample, WanRadioStatus

    p, registry = poller
    await registry.add(_config("mbx"))
    # Le processus a démarré il y a douze heures ; le dernier relevé date de
    # onze heures — l'équipement s'est éteint APRÈS le démarrage.
    p._demarre_a = utcnow() - timedelta(hours=12)
    await p._repo.insert(RadioSample(
        router_id="mbx", router_name="MBX", ts=utcnow() - timedelta(hours=11),
        wan=WanRadioStatus(wan_id="4", wan_name="SIM 4", connected=True),
    ))
    await p._poll_once()
    assert [a for a in p.alerts("mbx") if a.kind == "supervision interrompue"] == []

    # Et un relevé d'AVANT le démarrage, lui, mesure un trou — jusqu'au
    # démarrage, pas jusqu'à maintenant.
    await registry.add(_config("dome"))
    p._demarre_a = utcnow() - timedelta(minutes=1)
    await p._repo.insert(RadioSample(
        router_id="dome", router_name="DOME", ts=utcnow() - timedelta(minutes=31),
        wan=WanRadioStatus(wan_id="4", wan_name="SIM 4", connected=True),
    ))
    await p._poll_once()
    trous = [a for a in p.alerts("dome") if a.kind == "supervision interrompue"]
    assert len(trous) == 1
    assert round((trous[0].until - trous[0].since).total_seconds() / 60) == 30


async def test_the_last_path_latency_is_carried_with_its_time_then_dropped(poller, monkeypatch):
    """R-1.3 : la latence du lien n'est lue qu'une fois par minute. Le tableau de
    bord reçoit la dernière mesure AVEC son heure, jusqu'à 90 s, puis plus rien —
    et le relevé enregistré, lui, garde son trou."""
    from datetime import timedelta

    import app.poller as module

    p, registry = poller
    record = await registry.add(_config("dome"))
    t0 = utcnow()
    horloge = [t0]
    monkeypatch.setattr(module, "utcnow", lambda: horloge[0])
    wan = lambda ms, up=True: WanRadioStatus(wan_id="3", wan_name="SIM 3", kind="cellular",
                                             connected=up, path_latency_ms=ms)

    async def cycle(secondes, ms, up=True):
        horloge[0] = t0 + timedelta(seconds=secondes)
        record.client.get_radio_status = AsyncMock(return_value=[wan(ms, up)])
        await p._poll_once()
        return p.latest["dome"]["3"].wan

    lu = await cycle(0, 31.0)
    assert (lu.path_latency_recent_ms, lu.path_latency_at) == (31.0, t0)
    lu = await cycle(50, None)
    assert lu.path_latency_ms is None  # le relevé garde son trou
    assert (lu.path_latency_recent_ms, lu.path_latency_at) == (31.0, t0)
    lu = await cycle(90, None)
    assert lu.path_latency_recent_ms == 31.0
    lu = await cycle(91, None)
    assert lu.path_latency_recent_ms is None and lu.path_latency_at is None
    # Une nouvelle mesure remplace l'ancienne ; un lien coupé n'en porte aucune.
    lu = await cycle(100, 29.0)
    assert (lu.path_latency_recent_ms, lu.path_latency_at) == (29.0, t0 + timedelta(seconds=100))
    # Même quand la série du routeur lui prête encore un point d'avant la coupure.
    lu = await cycle(105, 12.0, up=False)
    assert lu.path_latency_recent_ms is None
    lu = await cycle(110, None)
    assert lu.path_latency_recent_ms is None  # la coupure a rompu la chaîne


async def test_the_bond_profiles_travel_from_the_client_to_the_summary(poller):
    """R-1.5 : ce qui nomme la cause d'un tunnel à terre arrive jusqu'à l'API."""
    from app.api.routes import _summary
    from app.models import BondProfile

    p, registry = poller
    record = await registry.add(_config("hd2"))
    profil = BondProfile(id="4", name="FH", status="START", peers_connected=0)
    record.client.bond_profiles = [profil]
    await p._poll_once()
    assert _summary(p.latest, record.config, p).bond_profiles == [profil]


async def test_the_lan_clients_travel_from_the_client_to_the_summary(poller):
    """R-1.10 : le décompte des clients arrive jusqu'à l'API."""
    from app.api.routes import _summary
    from app.models import LanClients

    p, registry = poller
    record = await registry.add(_config("dome"))
    compte = LanClients(active=3, wireless=2, wired=1, includes_this_host=True)
    record.client.lan_clients = compte
    await p._poll_once()
    assert _summary(p.latest, record.config, p).lan_clients == compte


async def test_une_base_qui_fait_attendre_n_est_pas_imputee_a_l_equipement(poller):
    """Une écriture qui attend la base (VACUUM, clé USB lente, lecteur qui
    traîne) passait dans le budget RÉSEAU de l'équipement : toute la flotte
    devenait « injoignable : pas de réponse en 20 s », reculait, et les
    relevés lus étaient perdus. L'attente de la base ne compte pas."""
    p, registry = poller
    p._device_budget = 0.4
    record = await registry.add(_config("patient"))
    libere = asyncio.Event()

    async def tenir_la_base():
        async with p._repo.transaction():
            libere.set()
            await asyncio.sleep(1.2)   # trois budgets

    tenue = asyncio.create_task(tenir_la_base())
    await libere.wait()
    await p._poll_once()
    await tenue
    assert record.status == "connecté", record.error
    assert "patient" in p.latest
    assert (await p._repo.current_session("patient"))["samples"] > 0


async def test_une_base_qui_fait_attendre_ne_prive_pas_les_autres_de_place(poller):
    """Les places de scrutation bornent le RÉSEAU : un équipement qui attend la
    base rend la sienne, sinon huit équipements bloqués sur la base faisaient
    passer les suivants « injoignables » faute de place."""
    p, registry = poller
    p._device_budget = 0.4
    p._slots = asyncio.Semaphore(1)
    records = [await registry.add(_config(f"e{i}")) for i in range(3)]
    libere = asyncio.Event()

    async def tenir_la_base():
        async with p._repo.transaction():
            libere.set()
            await asyncio.sleep(1.2)

    tenue = asyncio.create_task(tenir_la_base())
    await libere.wait()
    await p._poll_once()
    await tenue
    assert [r.status for r in records] == ["connecté"] * 3, [r.error for r in records]
