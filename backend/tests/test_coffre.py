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
from app.pairing.service import reconnecter_retenus

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


def test_une_entree_illisible_vaut_rien(trousseau):
    trousseau.entrees[(coffre.SERVICE, "0000-0000-0042")] = "pas du json"
    assert coffre.lire("0000-0000-0042") is None


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


# --- le lancement ---------------------------------------------------------------

async def test_au_lancement_une_tentative_par_equipement_retenu(trousseau):
    coffre.retenir("0000-0000-0042", coffre.Identifiants("admin", "secret"))
    autre = {**CONNU, "serial": "0000-0000-0099", "host": "192.0.2.9"}   # non retenu
    depot = _Depot([CONNU, autre])
    registry = DeviceRegistry(default_mode="mock")
    poller = SimpleNamespace(forget=lambda *a, **k: None)
    with _identify(INFO) as identify:
        await reconnecter_retenus(registry, depot, poller)
    assert identify.await_count == 1
    assert registry.get("0000-0000-0042") is not None
    await registry.close()


async def test_au_lancement_un_equipement_absent_ne_bloque_rien(trousseau):
    coffre.retenir("0000-0000-0042", coffre.Identifiants("admin", "secret"))
    registry = DeviceRegistry(default_mode="mock")
    with _identify(error=OSError("injoignable")) as identify:
        await reconnecter_retenus(registry, _Depot([CONNU]), SimpleNamespace(forget=lambda *a, **k: None))
    assert identify.await_count == 1
    assert registry.get("0000-0000-0042") is None
    await registry.close()
