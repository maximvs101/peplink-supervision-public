"""Collecte du témoin Ethernet, avec un faux client (jamais un vrai routeur)."""

from datetime import datetime, timedelta, timezone

import httpx
import pytest

from app.models import DeviceInfo, RouterEvent, WanRadioStatus
from app.peplink.router_api import (
    CredentialsRefusedError,
    EndpointRefusedError,
    RouterApiError,
)
from temoin.collecte import Collecte
from temoin.stockage import Depot

T0 = datetime(2026, 10, 17, 10, 0, 0, tzinfo=timezone.utc)
SERIE = "SERIE-TEST-1"


def wan(wan_id="1", nom="WAN-A", **kw):
    base = dict(
        kind="ethernet", wan_id=wan_id, wan_name=nom, connected=True,
        status_message="Connected", uptime_s=120, disabled=False,
    )
    base.update(kw)
    return WanRadioStatus(**base)


def evenement(s, kind, wan_id="1", detail=None):
    return RouterEvent(
        ts=T0 + timedelta(seconds=s), kind=kind, wan_id=wan_id, detail=detail,
        empreinte=f"{s}-{kind}-{wan_id}-{detail}",
    )


class FauxClient:
    def __init__(self):
        self.radio = [wan("1", "WAN-A"), wan("2", "WAN-B")]
        self.info = DeviceInfo(uptime_s=5000, clock_offset_s=0)
        self.evenements = []
        self.erreur_radio = None  # exception levée par get_radio_status
        self.erreur_info = None
        self.erreur_journal = None
        self.appels_radio = 0
        self.appels_info = 0
        self.appels_journal = 0
        self.noms_recus = None

    async def get_radio_status(self, echeance=None):
        self.appels_radio += 1
        if self.erreur_radio is not None:
            raise self.erreur_radio
        return list(self.radio)

    async def get_device_info(self):
        self.appels_info += 1
        if self.erreur_info is not None:
            raise self.erreur_info
        return self.info

    async def get_router_events(self, noms):
        self.appels_journal += 1
        self.noms_recus = dict(noms)
        if self.erreur_journal is not None:
            raise self.erreur_journal
        return list(self.evenements)


class Temps:
    """Horloge injectée : avance à la main."""

    def __init__(self):
        self.now = T0

    def __call__(self):
        return self.now

    def avancer(self, s):
        self.now += timedelta(seconds=s)


@pytest.fixture
async def depot(tmp_path):
    d = Depot(str(tmp_path / "temoin.db"))
    await d.connect()
    yield d
    await d.close()


@pytest.fixture
def client():
    return FauxClient()


@pytest.fixture
def temps():
    return Temps()


async def sommeil_nul(_s):
    return None


@pytest.fixture
async def collecte(client, depot, temps):
    obs = await depot.nouvelle_observation(SERIE, "1", T0)
    return Collecte(client, depot, obs, "1", sommeil=sommeil_nul, horloge=temps)


async def releves(depot, obs=1):
    return await depot.releves(obs)


async def test_un_tour_stocke_un_releve_du_wan_choisi(collecte, depot, client):
    client.radio = [wan("2", "WAN-B", connected=False), wan("1", "WAN-A", uptime_s=77)]
    r = await collecte.tour()
    assert r.joignable and r.connecte is True
    assert r.message == "Connected" and r.anciennete_s == 77 and not r.desactive
    stockes = await releves(depot)
    assert len(stockes) == 1
    assert stockes[0].connecte is True and stockes[0].anciennete_s == 77
    assert collecte.etat_lien == "ok"


async def test_une_lecture_en_echec_est_un_releve_non_joignable(collecte, depot, client):
    for erreur in (
        RouterApiError("boum"), EndpointRefusedError("non"),
        httpx.ConnectError("injoignable"), OSError("réseau"), TimeoutError(),
    ):
        client.erreur_radio = erreur
        r = await collecte.tour()
        assert r.joignable is False
        assert r.connecte is None and r.message is None
        assert r.anciennete_s is None and r.anciennete_routeur_s is None
        assert r.desactive is False
    assert len(await releves(depot)) == 5


async def test_un_refus_d_identifiants_remonte_sans_relevé(collecte, depot, client):
    client.erreur_radio = CredentialsRefusedError("refusé")
    with pytest.raises(CredentialsRefusedError):
        await collecte.tour()
    assert await releves(depot) == []


async def test_la_boucle_ne_relance_pas_un_refus_d_identifiants(client, depot, temps):
    client.erreur_radio = CredentialsRefusedError("refusé")
    obs = await depot.nouvelle_observation(SERIE, "1", T0)
    c = Collecte(client, depot, obs, "1", sommeil=sommeil_nul, horloge=temps)
    with pytest.raises(CredentialsRefusedError):
        await c.boucle()
    assert client.appels_radio == 1


async def test_le_journal_est_lu_chaque_minute_et_traduit(collecte, depot, client, temps):
    # HYPOTHÈSE — à confirmer à la séance de capture (spec §6.7)
    client.evenements = [
        evenement(5, "coupure", detail="Health check failed"),
        evenement(6, "coupure", detail="Câble débranché"),
        evenement(7, "routeur démarré", wan_id=None),
        evenement(8, "coupure", wan_id="2", detail="autre lien"),
        evenement(9, "retour"),
    ]
    await collecte.tour()
    assert client.appels_journal == 1
    assert client.noms_recus == {"WAN-A": "1", "WAN-B": "2"}
    faits = {(f.genre, f.detail) for f in await depot.faits(1)}
    assert faits == {
        ("test de santé", "Health check failed"),
        ("coupure", "Câble débranché"),
        ("redémarrage", None),
    }
    # Trois secondes plus tard : pas de relecture ; une minute plus tard : oui.
    temps.avancer(3)
    await collecte.tour()
    assert client.appels_journal == 1
    temps.avancer(60)
    await collecte.tour()
    assert client.appels_journal == 2
    assert len(await depot.faits(1)) == 3  # dédoublonné


async def test_hypothese_traduction_du_journal(collecte, depot, client):
    # HYPOTHÈSE — à confirmer à la séance de capture (spec §6.7)
    client.evenements = [
        evenement(1, "coupure", detail="DNS test failed"),
        evenement(2, "coupure", detail="Ping test failed"),
        evenement(3, "configuration modifiée"),
        evenement(4, "bascule de SIM"),
        evenement(5, "priorités modifiées"),
        evenement(6, "connexion admin"),
    ]
    await collecte.tour()
    faits = sorted((f.genre, f.detail) for f in await depot.faits(1))
    assert faits == [
        ("désactivation", None),
        ("test de santé", "DNS test failed"),
        ("test de santé", "Ping test failed"),
    ]


async def test_hypothese_journal_ignore_les_autres_liens(collecte, depot, client):
    # HYPOTHÈSE — à confirmer à la séance de capture (spec §6.7)
    client.evenements = [evenement(1, "coupure", wan_id="2", detail="x")]
    await collecte.tour()
    assert await depot.faits(1) == []


async def test_une_panne_du_journal_ne_rend_pas_le_releve_injoignable(
    collecte, depot, client
):
    client.erreur_journal = RouterApiError("journal illisible")
    client.erreur_info = httpx.ReadTimeout("lent")
    r = await collecte.tour()
    assert r.joignable is True
    assert r.anciennete_routeur_s is None


async def test_un_wan_disparu_se_dit_sans_planter(collecte, depot, client):
    client.radio = [wan("2", "WAN-B")]
    assert await collecte.tour() is None
    assert collecte.etat_lien == "introuvable"
    assert await releves(depot) == []
    # Un lien d'un autre type sous le même identifiant n'est pas le lien choisi.
    client.radio = [wan("1", "WAN-A", kind="cellular")]
    assert await collecte.tour() is None
    assert collecte.etat_lien == "introuvable"
    client.radio = [wan("1", "WAN-A")]
    assert (await collecte.tour()).joignable
    assert collecte.etat_lien == "ok"


async def test_injoignable_recule(client, depot, temps):
    client.erreur_radio = httpx.ConnectError("hors ligne")
    obs = await depot.nouvelle_observation(SERIE, "1", T0)
    pauses = []

    async def sommeil(s):
        pauses.append(s)
        if len(pauses) == 7:
            raise StopAsyncIteration

    c = Collecte(client, depot, obs, "1", cadence=3.0, sommeil=sommeil, horloge=temps)
    with pytest.raises(StopAsyncIteration):
        await c.boucle()
    assert pauses == [3, 6, 12, 24, 30, 30, 30]


async def test_le_recul_repart_de_la_cadence_au_retour(client, depot, temps):
    obs = await depot.nouvelle_observation(SERIE, "1", T0)
    pauses = []

    async def sommeil(s):
        pauses.append(s)
        if len(pauses) == 2:
            client.erreur_radio = None
        if len(pauses) == 4:
            raise StopAsyncIteration

    client.erreur_radio = OSError("hors ligne")
    c = Collecte(client, depot, obs, "1", cadence=2.0, sommeil=sommeil, horloge=temps)
    with pytest.raises(StopAsyncIteration):
        await c.boucle()
    assert pauses == [3, 6, 2.0, 2.0]


async def test_la_latence_du_lien_est_gardee(collecte, depot, client):
    client.radio = [wan("1", path_latency_recent_ms=6.0)]
    r = await collecte.tour()
    assert r.latence_ms == 6.0
    assert (await releves(depot))[0].latence_ms == 6.0


async def test_une_horloge_decalee_coupe_le_rapprochement_du_journal(
    collecte, depot, client
):
    client.info = DeviceInfo(uptime_s=5000, clock_offset_s=-754)
    client.evenements = [evenement(1, "coupure", detail="Health check failed")]
    await collecte.tour()
    assert collecte.horloge_decalee is True
    assert await depot.faits(1) == []


async def test_un_ecart_d_horloge_modere_ne_coupe_rien(collecte, depot, client):
    client.info = DeviceInfo(uptime_s=5000, clock_offset_s=-60)
    client.evenements = [evenement(1, "routeur démarré", wan_id=None)]
    await collecte.tour()
    assert collecte.horloge_decalee is False
    assert len(await depot.faits(1)) == 1


async def test_l_anciennete_du_routeur_avance_entre_deux_lectures(
    collecte, depot, client, temps
):
    r1 = await collecte.tour()
    assert r1.anciennete_routeur_s == 5000
    temps.avancer(3)
    r2 = await collecte.tour()
    assert client.appels_info == 1
    assert r2.anciennete_routeur_s == 5003
    temps.avancer(60)
    client.info = DeviceInfo(uptime_s=9000, clock_offset_s=0)
    r3 = await collecte.tour()
    assert client.appels_info == 2
    assert r3.anciennete_routeur_s == 9000


async def test_l_anciennete_est_relue_au_premier_tour_apres_une_coupure_de_lecture(
    collecte, depot, client, temps
):
    await collecte.tour()
    client.erreur_radio = OSError("réseau")
    temps.avancer(10)
    await collecte.tour()
    client.erreur_radio = None
    client.info = DeviceInfo(uptime_s=4, clock_offset_s=0)  # le routeur a redémarré
    temps.avancer(10)
    r = await collecte.tour()
    assert client.appels_info == 2
    assert r.anciennete_routeur_s == 4


async def test_ancienneté_illisible_ne_lit_pas_le_journal_et_retente_apres_30_s(
    collecte, depot, client, temps
):
    client.info = None  # le vrai client rend None quand la lecture échoue
    client.evenements = [evenement(1, "routeur démarré", wan_id=None)]
    r = await collecte.tour()
    assert r.joignable and r.anciennete_routeur_s is None
    assert client.appels_info == 1 and client.appels_journal == 0
    assert await depot.faits(1) == []
    temps.avancer(3)
    await collecte.tour()
    assert client.appels_info == 1  # pas à chaque tour
    temps.avancer(27)
    client.info = DeviceInfo(uptime_s=800, clock_offset_s=0)
    r = await collecte.tour()
    assert client.appels_info == 2
    assert r.anciennete_routeur_s == 800
    assert client.appels_journal == 1
    assert len(await depot.faits(1)) == 1


async def test_ancienneté_en_exception_se_retente_apres_30_s(
    collecte, client, temps
):
    client.erreur_info = RouterApiError("non")
    await collecte.tour()
    temps.avancer(10)
    await collecte.tour()
    assert client.appels_info == 1 and client.appels_journal == 0
    temps.avancer(20)
    await collecte.tour()
    assert client.appels_info == 2


async def test_un_refus_d_identifiants_du_journal_remonte(collecte, client):
    client.erreur_journal = CredentialsRefusedError("refusé")
    with pytest.raises(CredentialsRefusedError):
        await collecte.tour()


async def test_l_annulation_du_sommeil_remonte_de_la_boucle(client, depot, temps):
    import asyncio

    obs = await depot.nouvelle_observation(SERIE, "1", T0)

    async def sommeil(_s):
        raise asyncio.CancelledError

    c = Collecte(client, depot, obs, "1", sommeil=sommeil, horloge=temps)
    with pytest.raises(asyncio.CancelledError):
        await c.boucle()
