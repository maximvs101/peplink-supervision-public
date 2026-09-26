"""Durcissement de l'API : CORS et restriction des routes locales.

Aucun test n'exerçait `main.py` ni les middlewares jusqu'ici. Ces vérifications
comptent d'autant plus que l'API servira bientôt à saisir les mots de passe des
équipements de la flotte.
"""

import pytest
from fastapi import Depends, FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.testclient import TestClient

from app.api.security import require_loopback
from app.config import Settings


def _app(origins: list[str], allow_credentials: bool) -> FastAPI:
    """Reproduit la configuration CORS de main.py, sans démarrer la supervision."""
    app = FastAPI()
    app.add_middleware(
        CORSMiddleware,
        allow_origins=origins,
        allow_credentials=allow_credentials,
        allow_methods=["GET", "OPTIONS"],
        allow_headers=["*"],
    )

    @app.get("/api/health")
    def health() -> dict:
        return {"status": "ok"}

    @app.get("/api/pair", dependencies=[Depends(require_loopback)])
    def pair() -> dict:
        return {"ok": True}

    return app


# --- CORS ---------------------------------------------------------------------

def test_wildcard_with_credentials_echoes_any_origin():
    """Démonstration du défaut corrigé, pour qu'il ne puisse pas revenir.

    La spécification CORS interdit `Allow-Origin: *` avec `Allow-Credentials`.
    Starlette contourne l'interdiction en renvoyant l'origine de la requête :
    toute page ouverte sur la machine pourrait interroger l'API avec ses cookies.
    """
    client = TestClient(_app(["*"], allow_credentials=True))
    r = client.get("/api/health", headers={"Origin": "https://exemple.invalid"})
    assert r.headers["access-control-allow-origin"] == "https://exemple.invalid"
    assert r.headers.get("access-control-allow-credentials") == "true"


def test_wildcard_without_credentials_stays_anonymous():
    """Configuration retenue quand l'origine est libre : plus de cookies."""
    client = TestClient(_app(["*"], allow_credentials=False))
    r = client.get("/api/health", headers={"Origin": "https://exemple.invalid"})
    assert r.headers["access-control-allow-origin"] == "*"
    assert "access-control-allow-credentials" not in r.headers


def test_listed_origin_is_allowed():
    client = TestClient(_app(["http://localhost:5173"], allow_credentials=True))
    r = client.get("/api/health", headers={"Origin": "http://localhost:5173"})
    assert r.headers["access-control-allow-origin"] == "http://localhost:5173"


def test_unlisted_origin_gets_no_permission():
    client = TestClient(_app(["http://localhost:5173"], allow_credentials=True))
    r = client.get("/api/health", headers={"Origin": "https://exemple.invalid"})
    assert "access-control-allow-origin" not in r.headers


def test_default_configuration_is_not_permissive():
    """Le défaut livré ne doit pas être « toute origine »."""
    settings = Settings(_env_file=None)
    assert settings.cors_allows_any_origin is False
    assert settings.cors_origin_list == ["http://localhost:5173", "http://127.0.0.1:5173"]


@pytest.mark.parametrize(
    "value,expected",
    [
        ("*", True),
        ("http://localhost:5173", False),
        ("http://a,*", True),
        ("", False),
    ],
)
def test_wildcard_detection(value, expected):
    assert Settings(_env_file=None, cors_origins=value).cors_allows_any_origin is expected


# --- restriction aux appels locaux --------------------------------------------

def test_loopback_request_is_accepted():
    client = TestClient(_app([], allow_credentials=False), client=("127.0.0.1", 5000))
    assert client.get("/api/pair").status_code == 200


def test_non_address_client_is_refused():
    """Échec fermé : tout ce qui n'est pas une adresse IP est refusé.

    `request.client.host` porte toujours l'adresse du pair au niveau socket. Une
    valeur qui n'en est pas une signale un contexte inattendu — le client de test
    de Starlette s'annonce « testclient » —, et le doute doit profiter au refus.
    """
    client = TestClient(_app([], allow_credentials=False))
    assert client.get("/api/pair").status_code == 403


@pytest.mark.parametrize("host", ["192.168.1.20", "10.0.0.5", "", "poste-distant"])
def test_remote_request_is_refused(host):
    """Serveur lancé avec --host 0.0.0.0 : les routes sensibles restent locales."""
    client = TestClient(_app([], allow_credentials=False), client=(host, 5000))
    response = client.get("/api/pair")
    assert response.status_code == 403
    assert "machine qui héberge" in response.json()["detail"]


def test_ipv6_loopback_is_accepted():
    client = TestClient(_app([], allow_credentials=False), client=("::1", 5000))
    assert client.get("/api/pair").status_code == 200


# --- route de découverte ------------------------------------------------------
#
# Elle déclenche une activité réseau et prépare l'appairage : deux choses qui
# n'ont pas à être pilotables depuis le réseau.

def test_discovery_route_is_restricted_to_the_local_machine():
    from unittest.mock import patch

    from app.api import routes

    app = FastAPI()

    class _Fleet:
        fleet = []

    app.state.poller = _Fleet()
    app.include_router(routes.router)

    with patch("app.api.routes.discover", return_value=[]):
        distant = TestClient(app, client=("192.168.1.20", 5000))
        assert distant.get("/api/discover").status_code == 403

        local = TestClient(app, client=("127.0.0.1", 5000))
        assert local.get("/api/discover").status_code == 200


def test_discovery_flags_already_supervised_devices():
    """Ne pas proposer d'appairer un équipement déjà dans la flotte."""
    from unittest.mock import patch

    from app.api import routes
    from app.fleet import RouterConfig
    from app.models import DiscoveredDevice

    app = FastAPI()

    class _Fleet:
        fleet = [RouterConfig(id="mbx", name="MBX", host="192.168.63.1")]

    app.state.poller = _Fleet()
    app.include_router(routes.router)

    found = [
        DiscoveredDevice(host="192.168.63.1", evidence=["API locale Peplink accessible"]),
        DiscoveredDevice(host="192.168.50.1", evidence=["certificat « peplink »"]),
    ]
    with patch("app.api.routes.discover", return_value=found):
        body = TestClient(app, client=("127.0.0.1", 5000)).get("/api/discover").json()

    assert [d["already_configured"] for d in body] == [True, False]


# --- appairage : routes d'écriture ---------------------------------------------

def _pairing_app():
    """Application minimale portant les routes d'équipements."""
    from unittest.mock import AsyncMock

    from app.api import routes
    from app.config import Settings
    from app.devices.registry import DeviceRegistry

    app = FastAPI()
    app.state.settings = Settings(_env_file=None)
    app.state.registry = DeviceRegistry(default_mode="mock")
    app.state.repo = AsyncMock()
    app.state.poller = AsyncMock()
    app.include_router(routes.router)
    return app


def test_pairing_is_restricted_to_the_local_machine():
    """Cette route reçoit des mots de passe d'équipement : elle ne sort pas du poste."""
    app = _pairing_app()
    distant = TestClient(app, client=("192.168.1.20", 5000))
    response = distant.post("/api/devices/pair", json={"host": "192.0.2.1", "username": "admin"})
    assert response.status_code == 403


def test_unpairing_is_restricted_to_the_local_machine():
    app = _pairing_app()
    distant = TestClient(app, client=("192.168.1.20", 5000))
    assert distant.delete("/api/devices/quelconque").status_code == 403


def test_refused_credentials_answer_401_not_502():
    """Le code de retour doit dire à l'interface quoi faire corriger."""
    from unittest.mock import patch

    from app.pairing.service import PairingError

    app = _pairing_app()
    local = TestClient(app, client=("127.0.0.1", 5000))
    with patch("app.api.routes.pair", side_effect=PairingError("refusé", credentials=True)):
        assert local.post("/api/devices/pair",
                          json={"host": "192.0.2.1", "username": "a", "password": "b"}
                          ).status_code == 401

    with patch("app.api.routes.pair", side_effect=PairingError("injoignable")):
        assert local.post("/api/devices/pair",
                          json={"host": "192.0.2.1", "username": "a", "password": "b"}
                          ).status_code == 502


def test_unpairing_an_unknown_device_is_a_404():
    app = _pairing_app()
    local = TestClient(app, client=("127.0.0.1", 5000))
    assert local.delete("/api/devices/jamais-vu").status_code == 404


def test_the_summary_carries_the_mode_of_each_device():
    """Le mode est PAR ÉQUIPEMENT, pas celui du fichier de configuration.

    `/api/health` publie un mode global : un routeur réel appairé à chaud
    s'affichait donc sous un badge « Simulateur », alors que ses mesures étaient
    bien réelles. Et sans fichier de flotte, l'ouverture tombait sur la flotte de
    démonstration — des données vertes et plausibles, mais simulées.
    """
    from app.api.routes import _summary
    from app.fleet import RouterConfig

    simule = _summary({}, RouterConfig(id="demo", name="Démo", mode="mock"))
    reel = _summary({}, RouterConfig(id="mbx", name="MBX", mode="router", host="1.2.3.4"))
    assert simule.mode == "mock"
    assert reel.mode == "router"


# --- une mise à jour ne doit pas passer inaperçue ----------------------------

def test_the_compiled_index_is_never_kept_in_cache(tmp_path, monkeypatch):
    """Après une mise à jour, le navigateur resservait l'ancien bundle.

    Vite nomme chaque fichier compilé d'après son empreinte : ceux-là peuvent
    être gardés pour toujours. `index.html` garde le même nom et désigne
    l'empreinte du jour — le garder en cache fait tourner l'ANCIENNE interface,
    présente sur le disque à côté de la nouvelle, sans le moindre message. Sur un
    PC de terrain mis à jour entre deux interventions, c'est superviser avec une
    version dont on croit à tort qu'elle est à jour.
    """
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from app.main import _FrontendFiles

    (tmp_path / "index.html").write_text("<!doctype html><p>ok</p>", encoding="utf-8")
    (tmp_path / "assets").mkdir()
    (tmp_path / "assets" / "index-a1b2c3.js").write_text("console.log(1)", encoding="utf-8")

    app = FastAPI()
    app.mount("/", _FrontendFiles(directory=str(tmp_path), html=True), name="frontend")
    client = TestClient(app)

    page = client.get("/")
    assert page.status_code == 200
    assert "no-cache" in page.headers["cache-control"]

    # Et la contre-épreuve : un fichier au nom porteur d'empreinte, lui, se garde.
    bundle = client.get("/assets/index-a1b2c3.js")
    assert bundle.status_code == 200
    assert "immutable" in bundle.headers["cache-control"]


def test_a_cached_index_is_revalidated_rather_than_reused(tmp_path):
    """Un `If-Modified-Since` sur l'index ne doit pas rendre 304.

    C'est l'autre moitié du même défaut : sans cela le serveur confirme au
    navigateur qu'il peut resservir sa copie, et l'empreinte du jour n'arrive
    jamais jusqu'à lui.
    """
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from app.main import _FrontendFiles

    (tmp_path / "index.html").write_text("<!doctype html><p>ok</p>", encoding="utf-8")
    app = FastAPI()
    app.mount("/", _FrontendFiles(directory=str(tmp_path), html=True), name="frontend")
    client = TestClient(app)

    premier = client.get("/")
    rejoue = client.get("/", headers={"if-none-match": premier.headers.get("etag", "")})
    assert rejoue.status_code == 200, "le serveur a confirmé une copie périmée"


def test_the_websocket_refuses_a_foreign_origin():
    """Le middleware CORS ne voit jamais une poignée de main WebSocket.

    Vérifié avant correction : une connexion portant `Origin:
    https://exemple.invalid` était acceptée et livrait immédiatement la position
    GPS de l'équipement et les noms de WAN — qui portent en pratique le numéro de
    ligne et l'ICCID de chaque SIM. N'importe quelle page ouverte dans le
    navigateur de l'exploitant pouvait lire le flux temps réel en continu, sans
    que le serveur soit exposé au réseau.
    """
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from app.api import ws

    app = FastAPI()
    app.include_router(ws.router)

    class _Poller:
        def get_router(self, router_id: str):
            return object()

        def subscribe(self, router_id: str):
            raise AssertionError("le flux ne doit pas s'ouvrir sur une origine refusée")

    app.state.poller = _Poller()
    client = TestClient(app)

    with pytest.raises(Exception):  # noqa: B017 — la fermeture 4403 se manifeste ainsi
        with client.websocket_connect(
            "/ws/routers/mbx", headers={"Origin": "https://exemple.invalid"}
        ) as socket:
            socket.receive_text()


def test_the_MODE_is_validated_and_cannot_silently_fall_back_to_the_simulator():
    """Le repli de `make_client` est le SIMULATEUR.

    `peplink_mode` n'était pas contraint : toute valeur autre que le littéral
    exact — « Router », une faute de frappe dans `.env` — construisait un client
    simulé. L'écran n'affiche le badge « Simulateur » que sur `mode === 'mock'`
    exactement : des mesures INVENTÉES s'affichaient donc sans aucun badge,
    pendant que `/api/health` publiait le mode saisi.
    """
    from pydantic import ValidationError

    from app.config import Settings

    assert Settings(peplink_mode="router").peplink_mode == "router"
    assert Settings(peplink_mode="mock").peplink_mode == "mock"
    for faute in ("Router", "real", "routeur", ""):
        with pytest.raises(ValidationError):
            Settings(peplink_mode=faute)


def test_the_websocket_accepts_the_origin_that_SERVES_the_interface():
    """Le pendant POSITIF du test ci-dessus, et c'est par là que le défaut est passé.

    En production, le backend sert lui-même l'interface : le navigateur envoie
    `Origin: http://localhost:8000`, que `CORS_ORIGINS` ne contient pas —
    il ne liste que le serveur de développement. Le flux temps réel était donc
    refusé en silence, et le repli REST à quinze secondes le masquait. Mesuré sur
    le serveur en service : 130 refus au journal, un toutes les deux secondes,
    pendant qu'un client sans en-tête `Origin` passait.

    Toute assertion négative devrait avoir son pendant positif.
    """
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from app.api import ws

    app = FastAPI()
    app.include_router(ws.router)

    class _Poller:
        def get_router(self, router_id: str):
            return object()

        def subscribe(self, router_id: str):
            import asyncio

            return asyncio.Queue()

        def snapshot_latest(self, router_id: str):
            return []

        def unsubscribe(self, router_id: str, queue) -> None:
            pass

    app.state.poller = _Poller()
    client = TestClient(app)

    # `TestClient` pose `Host: testserver` : l'origine de même hôte doit passer,
    # sans figurer dans aucune liste.
    with client.websocket_connect(
        "/ws/routers/mbx", headers={"Origin": "http://testserver"}
    ) as socket:
        socket.close()


async def test_le_garde_juge_l_origine_transmise_par_un_mandataire_local():
    """Vite relaie `/api` vers 127.0.0.1 : sans cela, tout le LAN est « local ».

    Un voisin direct loopback qui transmet une origine réelle est jugé sur
    cette origine ; un client distant qui forge l'en-tête reste refusé.
    """
    from fastapi import FastAPI, Depends
    from httpx import ASGITransport, AsyncClient
    from app.api.security import require_loopback

    app = FastAPI()

    @app.get("/locale", dependencies=[Depends(require_loopback)])
    async def locale():
        return {"ok": True}

    # Mandataire local, origine LAN transmise : refusé.
    async with AsyncClient(transport=ASGITransport(app=app, client=("127.0.0.1", 5173)),
                           base_url="http://t") as c:
        r = await c.get("/locale", headers={"x-forwarded-for": "10.10.1.190"})
        assert r.status_code == 403
        # Mandataire local, origine locale transmise : accepté.
        r = await c.get("/locale", headers={"x-forwarded-for": "127.0.0.1"})
        assert r.status_code == 200
        # Un poste du LAN qui FORGE « 127.0.0.1 » en tête : le mandataire
        # AJOUTE l'adresse réelle en fin de liste, et c'est elle qui compte.
        # Le garde lisait le premier élément — 200 forgé, reproduit à l'audit.
        r = await c.get("/locale", headers={"x-forwarded-for": "127.0.0.1, 10.10.1.190"})
        assert r.status_code == 403
        r = await c.get("/locale", headers={"x-forwarded-for": "10.10.1.190, 127.0.0.1"})
        assert r.status_code == 200
        # Sans mandataire : accepté, comme avant.
        r = await c.get("/locale")
        assert r.status_code == 200
    # Client distant qui FORGE l'en-tête : l'en-tête ne compte pas, refusé.
    async with AsyncClient(transport=ASGITransport(app=app, client=("10.10.1.50", 1234)),
                           base_url="http://t") as c:
        r = await c.get("/locale", headers={"x-forwarded-for": "127.0.0.1"})
        assert r.status_code == 403


def test_the_capture_list_never_opens_the_config_family():
    """La liste de capture est FERMÉE, et `config.*` n'y entre jamais —
    ni par la clé, ni par l'endpoint réellement demandé."""
    from app.api.routes import _CAPTURABLES
    for nom, (cible, _params) in _CAPTURABLES.items():
        assert nom.startswith(("status.", "info.")), nom
        assert cible.startswith(("status.", "info.")), cible
