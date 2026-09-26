"""C-3 : le DNS rebinding ne franchit plus la garde loopback.

`require_loopback` ne juge que l'adresse TCP, et un navigateur local se
présente toujours en 127.0.0.1. Une page dont le domaine est rebasculé vers
127.0.0.1 devenait de MÊME ORIGINE que l'API : elle lisait toutes les routes,
capture brute comprise (identifiants APN, IMEI, ICCID), et s'abonnait à la
WebSocket — qui tenait pour « même origine » toute origine égale à l'en-tête
Host, que l'attaquant choisit. Reproduit à la relecture du 25/09.

La parade : l'en-tête Host. Une attaque par rebinding porte forcément un NOM
de domaine — c'est lui qu'on fait pointer ailleurs. Une adresse IP littérale
ne se rebascule pas ; `localhost` non plus.
"""

from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.security import HoteDeConfianceMiddleware, hote_de_confiance


@pytest.mark.parametrize("hote", [
    "localhost", "localhost:8000", "LOCALHOST:8000",
    "127.0.0.1:8000", "[::1]:8000", "::1",
    # HOST=0.0.0.0 : un téléphone du LAN ouvre l'écran par l'adresse du poste.
    "192.168.1.20:8000", "10.0.0.5",
])
def test_les_hotes_de_confiance_passent(hote):
    assert hote_de_confiance(hote, []) is True


@pytest.mark.parametrize("hote", [
    "attaquant.example:8000", "attaquant.example",
    "localhost.attaquant.example:8000", "127.0.0.1.attaquant.example",
    "", None, "[::1", "local host",
])
def test_un_nom_de_domaine_est_refuse(hote):
    assert hote_de_confiance(hote, []) is False


def test_un_nom_declare_par_l_exploitant_passe():
    """`ALLOWED_HOSTS` : le nom du poste sur le réseau local, s'il sert."""
    assert hote_de_confiance("mon-mac.local:8000", ["mon-mac.local"]) is True
    assert hote_de_confiance("MON-MAC.LOCAL", ["mon-mac.local"]) is True
    assert hote_de_confiance("autre.local", ["mon-mac.local"]) is False


def _mini_app() -> FastAPI:
    app = FastAPI()
    app.add_middleware(HoteDeConfianceMiddleware, noms=lambda: [])

    @app.get("/api/devices")
    def devices() -> list:
        return [{"id": "dome"}]

    return app


def test_une_requete_au_nom_rebascule_est_refusee():
    client = TestClient(_mini_app(), base_url="http://attaquant.example:8000")
    r = client.get("/api/devices")
    assert r.status_code == 400
    assert "dome" not in r.text


def test_une_requete_locale_passe():
    for base in ("http://localhost:8000", "http://127.0.0.1:8000", "http://192.168.1.20:8000"):
        assert TestClient(_mini_app(), base_url=base).get("/api/devices").status_code == 200


def test_la_websocket_au_nom_rebascule_est_refusee():
    app = FastAPI()
    app.add_middleware(HoteDeConfianceMiddleware, noms=lambda: [])

    @app.websocket("/ws/routers/{router_id}")
    async def flux(websocket, router_id: str) -> None:  # pragma: no cover — ne doit pas s'ouvrir
        raise AssertionError("le flux ne doit pas s'ouvrir sur un hôte rebasculé")

    client = TestClient(app, base_url="http://attaquant.example:8000")
    with pytest.raises(Exception):  # noqa: B017 — refus de la poignée de main
        with client.websocket_connect(
            "/ws/routers/dome", headers={"Origin": "http://attaquant.example:8000"},
        ) as socket:
            socket.receive_text()


def test_l_application_porte_la_garde():
    """La garde n'a de valeur que montée sur l'application servie."""
    from app.main import app

    assert any(m.cls is HoteDeConfianceMiddleware for m in app.user_middleware)
    # Et elle agit sur les vraies routes : la capture brute, visée par l'attaque.
    client = TestClient(app, base_url="http://attaquant.example:8000",
                        client=("127.0.0.1", 5000))
    r = client.get("/api/devices/dome/capture/status.wan.connection")
    assert r.status_code == 400


def test_la_websocket_juge_aussi_l_origine_elle_meme():
    """Défense en profondeur : même si la garde de Host manquait, une origine
    égale à Host ne suffit plus — il faut que cet hôte soit de confiance."""
    from app.api.ws import origine_admise

    assert origine_admise("http://localhost:8000", "localhost:8000", [], []) is True
    assert origine_admise("http://attaquant.example:8000", "attaquant.example:8000", [], []) is False
    assert origine_admise("http://localhost:5173", "127.0.0.1:8000", ["http://localhost:5173"], []) is True
    assert origine_admise(None, "localhost:8000", [], []) is True
