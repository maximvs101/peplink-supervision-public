"""Le coffre : les identifiants retenus dans le trousseau du système.

Aucun test ne touche le VRAI trousseau : `conftest.py` impose le backend
`fail`, et ceux qui ont besoin d'un trousseau en reçoivent un en mémoire.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import keyring
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from keyring.backend import KeyringBackend
from keyring.errors import PasswordDeleteError

from app import coffre
from app.api import routes
from app.devices.registry import DeviceRegistry
from app.models import DeviceInfo
from app.pairing.service import Reconnexion

INFO = DeviceInfo(model="Pepwave MAX HD2", serial="0000-0000-0042",
                  firmware="8.5.4 build 5808", name="HD2-ESSAI")


class TrousseauMemoire(KeyringBackend):
    priority = 1  # type: ignore[assignment]

    def __init__(self) -> None:
        super().__init__()
        self.entrees: dict[tuple[str, str], str] = {}

    def set_password(self, service, username, password):
        self.entrees[(service, username)] = password

    def get_password(self, service, username):
        return self.entrees.get((service, username))

    def delete_password(self, service, username):
        if (service, username) not in self.entrees:
            raise PasswordDeleteError("absent")
        del self.entrees[(service, username)]


@pytest.fixture
def trousseau():
    precedent = keyring.get_keyring()
    memoire = TrousseauMemoire()
    keyring.set_keyring(memoire)
    yield memoire
    keyring.set_keyring(precedent)


# --- le module --------------------------------------------------------------------

def test_les_tests_ne_touchent_jamais_le_vrai_trousseau():
    assert coffre.nom_du_trousseau() is None
    assert coffre.retenir("0000-0000-0042", coffre.Identifiants("admin", "x")) is False
    assert coffre.lire("0000-0000-0042") is None


def test_retenir_lire_oublier(trousseau):
    ident = coffre.Identifiants(username="admin", password="secret")
    assert coffre.retenir("0000-0000-0042", ident) is True
    assert coffre.lire("0000-0000-0042") == ident
    # Une entrée par équipement, sous le service de l'outil, clé = numéro de série.
    assert list(trousseau.entrees) == [(coffre.SERVICE, "0000-0000-0042")]
    coffre.oublier("0000-0000-0042")
    assert coffre.lire("0000-0000-0042") is None
    coffre.oublier("0000-0000-0042")  # rien à effacer : pas une erreur


def test_un_secret_ne_sort_jamais_dans_une_trace():
    ident = coffre.Identifiants(username="admin", password="TRES-SECRET",
                                client_secret="AUSSI-SECRET")
    assert "TRES-SECRET" not in repr(ident) and "AUSSI-SECRET" not in repr(ident)


def test_une_entree_illisible_vaut_rien(trousseau, caplog):
    trousseau.entrees[(coffre.SERVICE, "0000-0000-0042")] = "pas du json"
    with caplog.at_level("WARNING"):
        assert coffre.lire("0000-0000-0042") is None
    # Le journal dit l'échec, jamais la série.
    assert "illisibles" in caplog.text and "0000-0000-0042" not in caplog.text


# --- les routes ------------------------------------------------------------------

class _Depot:
    def __init__(self, connus):
        self.connus = connus
        self.memorises: list[str] = []
        self.oublies: list[str] = []

    async def known_devices(self):
        return list(self.connus)

    async def remember_device(self, **kw):
        self.memorises.append(kw["serial"])

    async def forget_device(self, serial):
        self.oublies.append(serial)
        return True


def _web(depot, registry):
    app = FastAPI()
    app.state.repo = depot
    app.state.registry = registry
    app.state.poller = SimpleNamespace(forget=lambda *a, **k: None, fleet=[])
    app.include_router(routes.router)
    return TestClient(app, base_url="http://localhost:8000", client=("127.0.0.1", 5000))


CONNU = {"serial": "0000-0000-0042", "device_id": "0000-0000-0042", "name": "HD2-ESSAI",
         "host": "http://192.0.2.7", "model": "Pepwave MAX HD2", "firmware": "8.5.4",
         "last_seen": "2026-10-17T10:00:00+00:00"}


def _identify(result=None, error=None):
    mock = AsyncMock(side_effect=error) if error else AsyncMock(return_value=result)
    return patch("app.pairing.service.RouterApiPeplinkClient.identify", mock)


async def test_l_appairage_retient_seulement_si_on_le_demande(trousseau):
    registry = DeviceRegistry(default_mode="mock")
    web = _web(_Depot([]), registry)
    with _identify(INFO):
        r = web.post("/api/devices/pair", json={
            "host": "192.0.2.7", "username": "admin", "password": "secret", "remember": True})
    assert r.status_code == 200
    assert "secret" not in r.text
    assert coffre.lire("0000-0000-0042") == coffre.Identifiants("admin", "secret")

    # Réappairé sans cocher : l'entrée est effacée, pas laissée à son ancienne valeur.
    with _identify(INFO):
        web.post("/api/devices/pair", json={
            "host": "192.0.2.7", "username": "admin", "password": "secret"})
    assert coffre.lire("0000-0000-0042") is None
    await registry.close()


async def test_les_connus_disent_s_ils_sont_retenus_sans_rien_reveler(trousseau):
    coffre.retenir("0000-0000-0042", coffre.Identifiants("admin", "secret"))
    web = _web(_Depot([CONNU, {**CONNU, "serial": "0000-0000-0099"}]), DeviceRegistry())
    corps = web.get("/api/devices/known").json()
    assert [c["credentials_stored"] for c in corps] == [True, False]
    assert "secret" not in str(corps)


async def test_reconnecter_avec_les_identifiants_retenus(trousseau):
    coffre.retenir("0000-0000-0042", coffre.Identifiants("admin", "secret"))
    depot = _Depot([CONNU])
    registry = DeviceRegistry(default_mode="mock")
    web = _web(depot, registry)
    with _identify(INFO) as identify:
        r = web.post("/api/devices/known/0000-0000-0042/reconnect")
    assert r.status_code == 200, r.text
    assert identify.await_count == 1, "une seule tentative"
    assert registry.get("0000-0000-0042").config.host == "http://192.0.2.7"
    assert registry.get("0000-0000-0042").config.password == "secret"
    assert depot.memorises == ["0000-0000-0042"]
    await registry.close()


async def test_reconnecter_sans_identifiants_retenus_ne_tente_rien(trousseau):
    web = _web(_Depot([CONNU]), DeviceRegistry())
    with _identify(INFO) as identify:
        assert web.post("/api/devices/known/0000-0000-0042/reconnect").status_code == 409
        assert web.post("/api/devices/known/inconnu/reconnect").status_code == 404
    assert identify.await_count == 0


async def test_un_refus_le_dit_et_garde_l_entree(trousseau):
    """Un mot de passe changé sur le routeur : le dire, ne rien réessayer, et
    ne pas effacer — c'est l'exploitant qui corrige, en ressaisissant."""
    from app.peplink.router_api import AuthenticationError

    coffre.retenir("0000-0000-0042", coffre.Identifiants("admin", "ancien"))
    web = _web(_Depot([CONNU]), DeviceRegistry())
    with _identify(error=AuthenticationError("refusé")) as identify:
        r = web.post("/api/devices/known/0000-0000-0042/reconnect")
    assert r.status_code == 401 and identify.await_count == 1
    assert coffre.lire("0000-0000-0042") is not None


async def test_retirer_un_equipement_efface_son_entree(trousseau):
    coffre.retenir("0000-0000-0042", coffre.Identifiants("admin", "secret"))
    registry = DeviceRegistry(default_mode="mock")
    depot = _Depot([CONNU])
    web = _web(depot, registry)
    with _identify(INFO):
        web.post("/api/devices/known/0000-0000-0042/reconnect")
    assert web.delete("/api/devices/0000-0000-0042").status_code == 200
    assert coffre.lire("0000-0000-0042") is None
    await registry.close()


# --- la veille : au lancement, puis dès que l'équipement apparaît (A-1) -----------

def _veille(registry, depot):
    return Reconnexion(registry, depot, SimpleNamespace(forget=lambda *a, **k: None), cadence=3.0)


async def test_au_lancement_une_tentative_par_equipement_retenu(trousseau):
    coffre.retenir("0000-0000-0042", coffre.Identifiants("admin", "secret"))
    autre = {**CONNU, "serial": "0000-0000-0099", "host": "192.0.2.9"}   # non retenu
    registry = DeviceRegistry(default_mode="mock")
    with _identify(INFO) as identify:
        await _veille(registry, _Depot([CONNU, autre])).tour(maintenant=0.0)
    assert identify.await_count == 1
    assert registry.get("0000-0000-0042") is not None
    await registry.close()


async def test_un_equipement_qui_apparait_apres_le_lancement_est_reconnecte(trousseau):
    """Le véhicule arrive après l'ouverture de l'outil : le routeur s'allume, et
    il fallait cliquer « Reconnecter ». La veille le reprend, en reculant tant
    qu'il ne répond pas — 3, 6, 12, 24 s, puis toutes les 30 s."""
    coffre.retenir("0000-0000-0042", coffre.Identifiants("admin", "secret"))
    registry = DeviceRegistry(default_mode="mock")
    veille = _veille(registry, _Depot([CONNU]))
    with _identify(error=OSError("injoignable")) as identify:
        for t in range(0, 60):
            await veille.tour(maintenant=float(t))
    # Tentatives à 0, 3, 9, 21, 45 s : le recul, pas une par seconde.
    assert identify.await_count == 5
    with _identify(INFO) as identify:
        await veille.tour(maintenant=75.0)
    assert identify.await_count == 1
    assert registry.get("0000-0000-0042") is not None
    await registry.close()


async def test_un_refus_arrete_la_veille_jusqu_a_de_nouveaux_identifiants(trousseau):
    """Jamais d'essai répété sur un refus : cinq échecs suspendent l'accès admin
    cinq minutes. La veille reprend seulement si le trousseau change."""
    from app.peplink.router_api import AuthenticationError

    coffre.retenir("0000-0000-0042", coffre.Identifiants("admin", "ancien"))
    registry = DeviceRegistry(default_mode="mock")
    veille = _veille(registry, _Depot([CONNU]))
    with _identify(error=AuthenticationError("refusé")) as identify:
        for t in range(0, 120, 3):
            await veille.tour(maintenant=float(t))
    assert identify.await_count == 1, "un refus ne se retente jamais seul"
    coffre.retenir("0000-0000-0042", coffre.Identifiants("admin", "nouveau"))
    with _identify(INFO) as identify:
        await veille.tour(maintenant=200.0)
    assert identify.await_count == 1
    await registry.close()


async def test_un_equipement_deja_supervise_n_est_pas_retente(trousseau):
    coffre.retenir("0000-0000-0042", coffre.Identifiants("admin", "secret"))
    registry = DeviceRegistry(default_mode="mock")
    veille = _veille(registry, _Depot([CONNU]))
    with _identify(INFO):
        await veille.tour(maintenant=0.0)
    with _identify(INFO) as identify:
        for t in range(3, 60, 3):
            await veille.tour(maintenant=float(t))
    assert identify.await_count == 0
    await registry.close()


# --- relecture du 17/10 ------------------------------------------------------------

async def test_un_autre_equipement_a_l_adresse_retenue_n_est_pas_appaire(trousseau):
    """Un autre HD2, même mot de passe, occupe désormais l'adresse retenue : la
    veille l'appairait, puis recommençait à chaque tour — une connexion admin
    toutes les 3 s. La série identifiée doit être celle qu'on attend."""
    coffre.retenir("0000-0000-0042", coffre.Identifiants("admin", "secret"))
    autre = DeviceInfo(model="Pepwave MAX HD2", serial="0000-0000-0077", firmware="8.5.4", name="AUTRE")
    registry = DeviceRegistry(default_mode="mock")
    depot = _Depot([CONNU])
    veille = _veille(registry, depot)
    with _identify(autre) as identify:
        for t in range(0, 60, 3):
            await veille.tour(maintenant=float(t))
    assert registry.find_by_serial("0000-0000-0077") is None, "l'autre équipement a été appairé"
    assert depot.memorises == []
    assert identify.await_count < 10, "la veille ne recule pas sur une série inattendue"
    await registry.close()


async def test_deux_reconnexions_simultanees_n_en_font_qu_une(trousseau):
    import asyncio

    from app.pairing.service import reconnecter

    coffre.retenir("0000-0000-0042", coffre.Identifiants("admin", "secret"))
    registry = DeviceRegistry(default_mode="mock")
    depot = _Depot([CONNU])

    async def lente(*a, **k):
        await asyncio.sleep(0.05)
        return INFO

    with patch("app.pairing.service.RouterApiPeplinkClient.identify", AsyncMock(side_effect=lente)) as identify:
        a, b = await asyncio.gather(reconnecter(registry, depot, CONNU), reconnecter(registry, depot, CONNU))
    assert identify.await_count == 1, "deux connexions admin pour une reconnexion"
    assert a is b
    await registry.close()


async def test_un_equipement_absent_ne_retarde_pas_les_autres(trousseau):
    import asyncio
    import time

    autre = {**CONNU, "serial": "0000-0000-0099", "host": "192.0.2.9"}
    coffre.retenir("0000-0000-0042", coffre.Identifiants("admin", "secret"))
    coffre.retenir("0000-0000-0099", coffre.Identifiants("admin", "secret"))
    registry = DeviceRegistry(default_mode="mock")

    contacte: list[float] = []

    async def selon_hote(self):
        if "192.0.2.9" in self._base:
            await asyncio.sleep(0.5)
            raise OSError("injoignable")
        contacte.append(time.monotonic())
        return INFO

    with patch("app.pairing.service.RouterApiPeplinkClient.identify", selon_hote):
        debut = time.monotonic()
        await _veille(registry, _Depot([autre, CONNU])).tour(maintenant=0.0)
    assert registry.find_by_serial("0000-0000-0042") is not None
    assert contacte[0] - debut < 0.25, "l'équipement présent a attendu le délai de l'absent"
    await registry.close()


# --- revue du 24/10 ----------------------------------------------------------------

async def test_un_ecart_de_serie_fait_reculer_la_veille_jusqu_a_quelques_minutes(trousseau):
    """Un autre routeur occupe l'adresse retenue — ou la série lue est vide : la
    veille s'y reconnectait en admin toutes les 30 s, indéfiniment. Après un
    écart de série, le recul double jusqu'à quelques minutes."""
    coffre.retenir("0000-0000-0042", coffre.Identifiants("admin", "secret"))
    autre = DeviceInfo(model="Pepwave MAX HD2", serial="0000-0000-0077", firmware="8.5.4", name="AUTRE")
    sans_serie = DeviceInfo(model="Pepwave MAX HD2", serial=None, firmware="8.5.4", name="AUTRE")
    for info in (autre, sans_serie):
        registry = DeviceRegistry(default_mode="mock")
        veille = _veille(registry, _Depot([CONNU]))
        with _identify(info) as identify:
            for t in range(0, 1800, 3):
                await veille.tour(maintenant=float(t))
        # À 30 s de plafond : une soixantaine de connexions admin en 30 min.
        assert identify.await_count <= 15, identify.await_count
        assert veille._pause["0000-0000-0042"] >= 240.0
        assert registry.snapshot() == []
        await registry.close()


async def test_un_appairage_et_une_reconnexion_simultanes_sur_la_meme_adresse(trousseau):
    """Le verrou de reconnexion était PAR SÉRIE, et la route d'appairage ne le
    prenait pas : la veille et le formulaire ouvraient deux connexions admin
    sur le même routeur, et deux `registry.add` se remplaçaient. Le verrou est
    désormais par HÔTE canonique, partagé entre `pair()` et la veille."""
    import asyncio

    from app.pairing.service import pair, reconnecter

    coffre.retenir("0000-0000-0042", coffre.Identifiants("admin", "secret"))
    registry = DeviceRegistry(default_mode="mock")
    depot = _Depot([CONNU])
    en_cours = {"n": 0, "max": 0}

    async def lente(*a, **k):
        en_cours["n"] += 1
        en_cours["max"] = max(en_cours["max"], en_cours["n"])
        await asyncio.sleep(0.05)
        en_cours["n"] -= 1
        return INFO

    with patch("app.pairing.service.RouterApiPeplinkClient.identify", AsyncMock(side_effect=lente)), \
         patch("app.pairing.service.RouterApiPeplinkClient.aclose", AsyncMock()):
        await asyncio.gather(
            reconnecter(registry, depot, CONNU),
            # Autre graphie du même hôte : même équipement.
            pair(registry, "192.0.2.7/", username="admin", password="secret"),
        )
    assert en_cours["max"] == 1, "deux connexions admin simultanées sur le même routeur"
    assert len(registry.snapshot()) == 1
    await registry.close()
