"""C-26 : la règle « un refus d'identifiants n'est jamais retenté seul », gardée.

Cinq échecs de connexion suspendent l'accès admin du routeur cinq minutes.
La relecture du 25/09 a muté cette règle de quatre façons, et les quatre
mutants survivaient : aucun test n'appelait POST /reauth (B15 : la route
n'appelle plus allow_new_credentials ; B16 : elle ne remet plus auth_blocked
à faux), et aucun ne comptait les POST d'authentification après un
« stat: fail » (B3, B4 : le verrou ne s'arme plus, en jeton comme en
session). Le mutant F9, côté écran, est tenu par RepriseIdentifiants.test.tsx.
"""

from __future__ import annotations

from types import SimpleNamespace

import httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api import routes
from app.fleet import RouterConfig
from app.peplink.router_api import CredentialsRefusedError, RouterApiPeplinkClient


# --- la route --------------------------------------------------------------------

def test_la_route_de_reprise_leve_le_verrou_et_rouvre_les_identifiants():
    reouverts: list[bool] = []
    client = SimpleNamespace(allow_new_credentials=lambda: reouverts.append(True))
    record = SimpleNamespace(auth_blocked=True, status="identifiants refusés",
                             error="login refusé", client=client)

    class _Registre:
        def get(self, device_id):
            return record if device_id == "dome" else None

    app = FastAPI()
    app.state.registry = _Registre()
    app.include_router(routes.router)
    web = TestClient(app, base_url="http://localhost:8000", client=("127.0.0.1", 5000))

    r = web.post("/api/devices/dome/reauth")
    assert r.status_code == 200
    assert record.auth_blocked is False, "B16 : le verrou n'a pas été levé"
    assert reouverts == [True], "B15 : les identifiants n'ont pas été rouverts"
    assert (record.status, record.error) == ("en attente", None)
    assert web.post("/api/devices/inconnu/reauth").status_code == 404


# --- le client : un seul POST d'authentification par refus --------------------------

def _compteur(chemin_auth: str):
    posts: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == chemin_auth:
            posts.append(request.url.path)
            return httpx.Response(200, json={"stat": "fail", "code": 401, "message": "refusé"})
        raise AssertionError(f"aucune lecture ne doit partir : {request.url.path}")

    return posts, handler


@pytest.mark.parametrize("mode, chemin", [
    ({"client_id": "ID", "client_secret": "SECRET"}, "/api/auth.token.grant"),   # B4
    ({"username": "admin", "password": "x"}, "/api/login"),                        # B3
])
async def test_un_refus_ne_coute_qu_un_seul_post_d_authentification(mode, chemin):
    posts, handler = _compteur(chemin)
    client = RouterApiPeplinkClient(RouterConfig(id="r", name="R", host="192.0.2.1", **mode))
    await client._http.aclose()
    client._http = httpx.AsyncClient(transport=httpx.MockTransport(handler))

    for _ in range(4):
        with pytest.raises(CredentialsRefusedError):
            await client.get_radio_status()
    assert posts == [chemin], f"{len(posts)} tentatives pour un seul refus"

    # Seule l'action explicite rouvre la porte — une tentative, pas plus.
    client.allow_new_credentials()
    with pytest.raises(CredentialsRefusedError):
        await client.get_radio_status()
    assert len(posts) == 2
    await client.aclose()


# --- C-29 : une page non JSON au login n'est ni un refus, ni une perte ----------

@pytest.mark.parametrize("mode, chemin", [
    ({"client_id": "ID", "client_secret": "SECRET"}, "/api/auth.token.grant"),
    ({"username": "admin", "password": "x"}, "/api/login"),
])
async def test_une_page_html_au_login_est_une_panne_passagere(mode, chemin):
    """Le routeur en fin de démarrage sert une page HTML pile quand la session
    expire : `resp.json()` levait un JSONDecodeError, qui n'est ni
    RouterApiError ni httpx.HTTPError — il traversait les `except` des
    compléments, et le relevé était perdu avec un message de parseur. Ce n'est
    pas non plus un refus d'identifiants : le verrou ne doit pas s'armer."""
    from app.peplink.router_api import RouterApiError

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == chemin:
            return httpx.Response(200, text="<html>démarrage…</html>")
        raise AssertionError(request.url.path)

    client = RouterApiPeplinkClient(RouterConfig(id="r", name="R", host="192.0.2.1", **mode))
    await client._http.aclose()
    client._http = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    with pytest.raises(RouterApiError) as erreur:
        await client._authenticate()
    assert not isinstance(erreur.value, CredentialsRefusedError)
    assert client._credentials_refused is False
    await client.aclose()
