"""R-1.11, étape d'observation : lire UN endpoint documenté en POST.

`status.cellularModule.temperature` (API 8.4.1+) ne se lit qu'en POST, avec un
`connId`. Le client ne savait faire que des GET, et la route de capture non
plus. Ce qui s'ouvre est aussi étroit que possible : une méthode qui POSTe un
corps JSON, et une entrée — une seule — dans une liste de capture fermée.
"""

from __future__ import annotations

import json
from unittest.mock import AsyncMock

import httpx
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from app.api import routes
from app.fleet import RouterConfig
from app.peplink.router_api import RouterApiPeplinkClient


async def test_a_body_turns_the_read_into_a_POST_with_the_same_authentication():
    vus: list[tuple[str, str, dict, bytes]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        vus.append((request.method, request.url.path, dict(request.url.params), request.content))
        if request.url.path == "/api/login":
            return httpx.Response(200, json={"stat": "ok"})
        return httpx.Response(200, json={"stat": "ok", "response": {"temperature": 41}})

    client = RouterApiPeplinkClient(
        RouterConfig(id="r", name="R", host="192.0.2.1", username="a", password="b"))
    await client._http.aclose()
    client._http = httpx.AsyncClient(transport=httpx.MockTransport(handler))

    await client._get("status.cellularModule.temperature", corps={"connId": 2})
    await client._get("status.wan.connection")
    await client.aclose()

    methode, chemin, _params, corps = vus[-2]
    assert (methode, chemin) == ("POST", "/api/status.cellularModule.temperature")
    assert json.loads(corps) == {"connId": 2}
    # Sans corps, rien ne change : un GET, comme toujours.
    assert vus[-1][:2] == ("GET", "/api/status.wan.connection")


def _app(lire: AsyncMock) -> FastAPI:
    app = FastAPI()
    app.include_router(routes.router)

    class _Record:
        client = type("C", (), {"_get": lire})()

    class _Registre:
        def get(self, device_id):
            return _Record() if device_id == "dome" else None

    app.state.registry = _Registre()
    return app


async def test_the_capture_route_posts_only_the_listed_endpoint_with_its_connId():
    lire = AsyncMock(return_value={"stat": "ok", "response": {"temperature": 41}})
    async with AsyncClient(transport=ASGITransport(app=_app(lire), client=("127.0.0.1", 1)),
                           base_url="http://t") as c:
        r = await c.get("/api/devices/dome/capture/status.cellularModule.temperature?conn_id=2")
        assert r.status_code == 200 and r.json()["response"]["temperature"] == 41
        assert lire.await_args.args[0] == "status.cellularModule.temperature"
        assert lire.await_args.kwargs["corps"] == {"connId": 2}

        # Sans `conn_id`, le firmware répondrait une erreur de paramètre : refusé ici.
        r = await c.get("/api/devices/dome/capture/status.cellularModule.temperature")
        assert r.status_code == 400
        # Un `conn_id` sur un endpoint GET n'ouvre rien de plus.
        lire.reset_mock()
        await c.get("/api/devices/dome/capture/status.client?conn_id=2")
        assert "corps" not in lire.await_args.kwargs


async def test_the_post_capture_stays_on_the_loopback():
    lire = AsyncMock(return_value={})
    async with AsyncClient(transport=ASGITransport(app=_app(lire), client=("10.0.0.5", 1)),
                           base_url="http://t") as c:
        r = await c.get("/api/devices/dome/capture/status.cellularModule.temperature?conn_id=2")
    assert r.status_code == 403
    lire.assert_not_awaited()


def test_the_post_capture_list_never_opens_the_config_family():
    for nom in routes._CAPTURABLES_POST:
        assert nom.startswith("status."), nom
    assert set(routes._CAPTURABLES_POST) == {"status.cellularModule.temperature"}
