"""C-13 : une page tierce ne pilote plus les routes réservées au poste local.

`POST /devices/{id}/reauth` n'a pas de corps : un formulaire cross-site est
une requête « simple », sans requête préalable CORS, et `require_loopback`
la laissait passer puisque le navigateur est local. Chaque envoi rejouait
une connexion avec les identifiants en mémoire ; cinq, et le routeur
suspend l'accès admin cinq minutes. Une simple balise `<img>` déclenchait de
même `GET /capture` et `GET /discover`.
"""

from __future__ import annotations

import pytest
from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient

from app.api.security import require_loopback


def _app() -> TestClient:
    app = FastAPI()

    @app.post("/api/devices/{device_id}/reauth", dependencies=[Depends(require_loopback)])
    def reauth(device_id: str) -> dict:
        return {"ok": True}

    @app.get("/api/discover", dependencies=[Depends(require_loopback)])
    def discover() -> list:
        return []

    return TestClient(app, base_url="http://localhost:8000", client=("127.0.0.1", 5000))


@pytest.mark.parametrize("entetes", [
    {"Origin": "https://attaquant.example"},
    {"Origin": "null"},                                  # iframe isolée, fichier local
    {"Sec-Fetch-Site": "cross-site"},
    {"Origin": "https://attaquant.example", "Sec-Fetch-Site": "cross-site"},
])
def test_une_requete_intersite_est_refusee(entetes):
    client = _app()
    assert client.post("/api/devices/dome/reauth", headers=entetes).status_code == 403
    assert client.get("/api/discover", headers=entetes).status_code == 403


@pytest.mark.parametrize("entetes", [
    {},                                                   # curl, un script : pas un navigateur
    {"Origin": "http://localhost:8000", "Sec-Fetch-Site": "same-origin"},
    {"Sec-Fetch-Site": "same-origin"},
    {"Sec-Fetch-Site": "none"},                           # saisie dans la barre d'adresse
    {"Origin": "http://localhost:5173"},                  # le serveur de développement
])
def test_l_interface_et_les_outils_passent(entetes):
    client = _app()
    assert client.post("/api/devices/dome/reauth", headers=entetes).status_code == 200
    assert client.get("/api/discover", headers=entetes).status_code == 200
