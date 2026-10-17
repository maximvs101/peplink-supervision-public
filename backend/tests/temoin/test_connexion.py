"""Découverte et connexion du témoin Ethernet : une tentative, jamais deux."""

import pytest

from app import coffre
from app.coffre import Identifiants
from app.models import DeviceInfo, DiscoveredDevice, WanRadioStatus
from app.peplink.router_api import AuthenticationError, RouterApiError
from temoin import connexion
from temoin.connexion import ClientTemoin, Injoignable, RefusIdentifiants
from temoin.stockage import Depot

HOTE = "192.0.2.10"
SERIE = "SERIE-TEST-1"
IDENT = Identifiants(username="admin", password="mdp-de-test")


def appareil(host=HOTE, joignable=True, firmware="8.9.9"):
    return DiscoveredDevice(host=host, firmware=firmware, api_reachable=joignable)


class FauxClient:
    """Remplace RouterApiPeplinkClient ; compte les appels."""

    instances: list = []
    resultat = DeviceInfo(model="Modèle-Test", serial=SERIE)
    liens: list = []

    def __init__(self, config):
        self.config = config
        self.identify_appels = 0
        self.ferme = False
        FauxClient.instances.append(self)

    async def identify(self):
        self.identify_appels += 1
        if isinstance(self.resultat, BaseException):
            raise self.resultat
        return self.resultat

    async def get_radio_status(self):
        return self.liens

    async def aclose(self):
        self.ferme = True


@pytest.fixture(autouse=True)
def faux(monkeypatch):
    FauxClient.instances = []
    FauxClient.resultat = DeviceInfo(model="Modèle-Test", serial=SERIE)
    FauxClient.liens = []
    monkeypatch.setattr(connexion, "ClientTemoin", FauxClient)
    connexion._RETENUS.clear()


@pytest.fixture
async def depot(tmp_path):
    d = Depot(str(tmp_path / "temoin.db"))
    await d.connect()
    yield d
    await d.close()


def decouverte(monkeypatch, appareils):
    async def faux_discover(extra_hosts=None):
        return appareils

    monkeypatch.setattr(connexion, "discover", faux_discover)


async def test_un_seul_peplink_est_preselectionne(monkeypatch):
    decouverte(monkeypatch, [appareil(), appareil("192.0.2.11", joignable=False)])
    assert await connexion.chercher() == [
        {"hote": HOTE, "firmware": "8.9.9", "retenu": False}
    ]


async def test_l_ordre_de_la_decouverte_est_conserve(monkeypatch):
    decouverte(monkeypatch, [appareil("192.0.2.2"), appareil("192.0.2.1")])
    assert [e["hote"] for e in await connexion.chercher()] == ["192.0.2.2", "192.0.2.1"]


async def test_le_trousseau_evite_la_saisie(monkeypatch, depot):
    await depot.memoriser_equipement(HOTE, SERIE, "Modèle-Test")
    decouverte(monkeypatch, [appareil()])
    monkeypatch.setattr(coffre, "lire", lambda serie: IDENT if serie == SERIE else None)
    assert (await connexion.chercher(depot=depot))[0]["retenu"] is True

    client, info = await connexion.connecter(HOTE, None, False, depot)
    assert info.serial == SERIE
    assert client.config.username == "admin" and client.config.password == "mdp-de-test"
    assert client.config.verify_ssl is False and client.ferme is False


async def test_retenu_exige_serie_et_identifiants(monkeypatch, depot):
    decouverte(monkeypatch, [appareil()])
    monkeypatch.setattr(coffre, "lire", lambda serie: IDENT)
    assert (await connexion.chercher(depot=depot))[0]["retenu"] is False  # série inconnue
    await depot.memoriser_equipement(HOTE, SERIE, None)
    monkeypatch.setattr(coffre, "lire", lambda serie: None)
    assert (await connexion.chercher(depot=depot))[0]["retenu"] is False  # trousseau vide


async def test_sans_identifiants_aucun_appel_reseau(depot):
    with pytest.raises(RefusIdentifiants, match="identifiants requis"):
        await connexion.connecter(HOTE, None, False, depot)
    assert FauxClient.instances == []


async def test_un_refus_ne_se_rejoue_pas(depot):
    FauxClient.resultat = AuthenticationError("non")
    with pytest.raises(RefusIdentifiants, match="cinq"):
        await connexion.connecter(HOTE, IDENT, True, depot)
    assert len(FauxClient.instances) == 1
    assert FauxClient.instances[0].identify_appels == 1
    assert FauxClient.instances[0].ferme is True
    assert await depot.serie_de(HOTE) is None


@pytest.mark.parametrize(
    "resultat", [RouterApiError("x"), OSError("réseau"), None]
)
async def test_une_panne_est_injoignable_et_ferme_le_client(depot, resultat):
    FauxClient.resultat = resultat
    with pytest.raises(Injoignable):
        await connexion.connecter(HOTE, IDENT, False, depot)
    assert FauxClient.instances[0].ferme is True


async def test_la_reussite_memorise_et_retient(monkeypatch, depot):
    retenus = []
    monkeypatch.setattr(coffre, "retenir", lambda s, i: retenus.append((s, i)) or True)
    await connexion.connecter(HOTE, IDENT, True, depot)
    assert await depot.serie_de(HOTE) == SERIE
    assert retenus == [(SERIE, IDENT)]


async def test_sans_retenir_le_coffre_reste_intact(monkeypatch, depot):
    monkeypatch.setattr(coffre, "retenir", lambda s, i: pytest.fail("ne doit pas retenir"))
    await connexion.connecter(HOTE, IDENT, False, depot)
    assert await depot.serie_de(HOTE) == SERIE


async def test_seuls_les_wan_ethernet_sont_proposes():
    FauxClient.liens = [
        WanRadioStatus(kind="ethernet", wan_id="3", wan_name="Nom-Secret", connected=False),
        WanRadioStatus(kind="cellular", wan_id="2", wan_name="Cell", connected=True),
        WanRadioStatus(kind="ethernet", wan_id="1", wan_name="Autre", connected=True),
    ]
    liens = await connexion.liens_ethernet(FauxClient(None))
    assert liens == [
        {"wan_id": "1", "connecte": True},
        {"wan_id": "3", "connecte": False},
    ]


async def test_les_tests_ne_touchent_jamais_le_vrai_trousseau(depot):
    assert coffre.nom_du_trousseau() is None
    assert coffre.retenir(SERIE, IDENT) is False
    assert coffre.lire(SERIE) is None
    await depot.memoriser_equipement(HOTE, SERIE, None)
    client, _ = await connexion.connecter(HOTE, IDENT, True, depot)
    assert client.ferme is False


async def test_le_trousseau_n_est_lu_qu_une_fois_par_serie(monkeypatch, depot):
    await depot.memoriser_equipement(HOTE, SERIE, "Modèle-Test")
    decouverte(monkeypatch, [appareil()])
    lectures = []
    monkeypatch.setattr(coffre, "lire", lambda serie: lectures.append(serie) or IDENT)
    for _ in range(3):
        assert (await connexion.chercher(depot=depot))[0]["retenu"] is True
    assert lectures == [SERIE]
    # Une connexion (qui peut retenir) oublie la réponse gardée.
    monkeypatch.setattr(coffre, "retenir", lambda s, i: True)
    await connexion.connecter(HOTE, IDENT, True, depot)
    await connexion.chercher(depot=depot)
    assert lectures == [SERIE, SERIE]


async def test_le_client_du_temoin_ne_lit_que_le_lien_et_sa_latence(monkeypatch):
    import asyncio

    from app.fleet import RouterConfig

    lus = []
    reponses = {
        "status.wan.connection": {"stat": "ok", "response": {
            "1": {"type": "ethernet", "enable": True, "statusLed": "green",
                  "name": "LIEN-A", "uptime": 42, "message": "Connected"},
            "2": {"type": "ethernet", "enable": False},
            "order": [1, 2],
        }},
        "status.wan.latency": {"stat": "ok", "response": {
            "1": {"latency": {"data": [11, 12.5]}},
        }},
    }

    async def faux_get(self, chemin, *a, **kw):
        lus.append(chemin)
        return reponses[chemin]

    # Le vrai client du témoin (la fixture remplace celui que `connecter` crée).
    monkeypatch.setattr(ClientTemoin, "_get", faux_get)
    client = ClientTemoin(RouterConfig(id="t", name="t", host="192.0.2.10", mode="router"))
    echeance = asyncio.get_running_loop().time() + 2.0
    [lien] = await client.get_radio_status(echeance=echeance)
    assert (lien.wan_id, lien.connected, lien.uptime_s) == ("1", True, 42)
    assert lien.path_latency_ms == 12.5
    assert client.wans_desactives == {"2"}
    # La latence ne se relit qu'une fois par minute.
    [lien] = await client.get_radio_status(echeance=echeance)
    assert lien.path_latency_ms is None
    assert lus == ["status.wan.connection", "status.wan.latency", "status.wan.connection"]
    await client.aclose()


async def test_des_identifiants_retenus_refuses_sont_oublies(monkeypatch, depot):
    await depot.memoriser_equipement(HOTE, SERIE, "Modèle-Test")
    oublies = []
    monkeypatch.setattr(coffre, "lire", lambda serie: IDENT)
    monkeypatch.setattr(coffre, "oublier", oublies.append)
    FauxClient.resultat = AuthenticationError("refusé")
    with pytest.raises(RefusIdentifiants, match="oubliés"):
        await connexion.connecter(HOTE, None, True, depot)
    assert oublies == [SERIE]
    assert FauxClient.instances[0].ferme is True
    # Le clic suivant ne rejoue rien : le routeur n'est plus dit « retenu ».
    decouverte(monkeypatch, [appareil()])
    assert (await connexion.chercher(depot=depot))[0]["retenu"] is False


async def test_des_identifiants_saisis_refuses_ne_vident_pas_le_trousseau(monkeypatch, depot):
    oublies = []
    monkeypatch.setattr(coffre, "oublier", oublies.append)
    FauxClient.resultat = AuthenticationError("refusé")
    with pytest.raises(RefusIdentifiants):
        await connexion.connecter(HOTE, IDENT, True, depot)
    assert oublies == []


async def test_l_adresse_saisie_retrouve_l_equipement_decouvert(monkeypatch, depot):
    monkeypatch.setattr(coffre, "lire", lambda serie: IDENT if serie == SERIE else None)
    await connexion.connecter(f"http://{HOTE}/", IDENT, False, depot)
    _, info = await connexion.connecter(HOTE, None, False, depot)
    assert info.serial == SERIE
