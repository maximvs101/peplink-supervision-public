"""Confidentialité : l'identifiant d'équipement ne porte plus la série (revue du 24/10).

L'identifiant valait la série en minuscules ; il est désormais une empreinte
opaque, stable d'un lancement à l'autre, et les données existantes migrent une
fois. Les séries des tests sont fictives.
"""

from __future__ import annotations

import logging
import sqlite3
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, patch

import httpx
import pytest
from fastapi import FastAPI

from app.api import routes
from app.devices.registry import DeviceRegistry
from app.identite import ancien_identifiant, identifiant_opaque
from app.models import DeviceInfo, RadioSample, WanRadioStatus
from app.pairing.service import pair
from app.storage.db import SampleRepository

SERIE = "0000-0000-0051"
INFO = DeviceInfo(model="Pepwave MAX HD2", serial=SERIE, firmware="8.5.4", name=None)
SEL = b"s" * 32


def _morceaux(serie: str) -> list[str]:
    """Les formes sous lesquelles une série se trahirait : telle quelle, et
    normalisée comme l'ancien identifiant."""
    return [serie.lower(), ancien_identifiant(serie)]


# --- l'empreinte --------------------------------------------------------------------


def test_l_identifiant_est_stable_opaque_et_depend_du_sel():
    ident = identifiant_opaque(SERIE, SEL)
    assert ident == identifiant_opaque(SERIE.lower(), SEL), "deux graphies, deux identifiants"
    assert ident == identifiant_opaque(ancien_identifiant(SERIE), SEL)
    assert ident.startswith("eq-") and len(ident) == 15
    assert not any(m in ident for m in _morceaux(SERIE) + SERIE.lower().split("-")[2:])
    assert ident != identifiant_opaque(SERIE, b"t" * 32), "le sel n'entre pas dans l'empreinte"


async def test_le_sel_est_cree_une_fois_et_garde_dans_la_base(tmp_path):
    chemin = str(tmp_path / "s.db")
    async with SampleRepository(chemin) as r:
        sel = r.sel_identifiants
    assert isinstance(sel, bytes) and len(sel) == 32
    async with SampleRepository(chemin) as r:
        assert r.sel_identifiants == sel


# --- l'appairage --------------------------------------------------------------------


def _identify(result):
    return patch("app.pairing.service.RouterApiPeplinkClient.identify", AsyncMock(return_value=result))


async def test_l_appairage_donne_l_identifiant_opaque_et_ne_journalise_pas_la_serie(caplog):
    caplog.set_level(logging.DEBUG)
    registry = DeviceRegistry(default_mode="mock", sel_identifiants=SEL)
    with _identify(INFO), patch("app.pairing.service.RouterApiPeplinkClient.aclose", AsyncMock()):
        record = await pair(registry, "192.0.2.1", username="admin", password="x")
    assert record.id == identifiant_opaque(SERIE, SEL)
    assert record.config.serial == SERIE           # la clé d'identité reste, en mémoire
    assert not any(m in caplog.text.lower() for m in _morceaux(SERIE)), "la série est au journal"
    await registry.close()


async def test_deux_lancements_donnent_le_meme_identifiant(tmp_path):
    chemin = str(tmp_path / "s.db")
    ids = []
    for _ in range(2):
        async with SampleRepository(chemin) as repo:
            registry = DeviceRegistry(default_mode="mock", sel_identifiants=repo.sel_identifiants)
            with _identify(INFO), patch("app.pairing.service.RouterApiPeplinkClient.aclose", AsyncMock()):
                ids.append((await pair(registry, "192.0.2.1", username="a", password="x")).id)
            await registry.close()
    assert ids[0] == ids[1]


async def test_le_poller_ne_journalise_pas_la_serie(caplog):
    from app.fleet import RouterConfig
    from app.poller import Poller

    caplog.set_level(logging.DEBUG)
    registry = DeviceRegistry(default_mode="mock")
    repo = SampleRepository(":memory:")
    await repo.connect()
    p = Poller(registry, repo, interval_seconds=0.5)
    record = await registry.add(RouterConfig(id="eq-000000000001", name="X", mode="mock", serial=SERIE))
    record.client.get_device_info = AsyncMock(return_value=INFO)
    await p._identify(record)
    autre = INFO.model_copy(update={"serial": "0000-0000-0052"})
    record.client.get_device_info = AsyncMock(return_value=autre)
    await p._refresh_info(record)
    texte = caplog.text.lower()
    assert not any(m in texte for m in _morceaux(SERIE) + _morceaux("0000-0000-0052"))
    assert "remplacé" in texte, "l'écart de série doit rester signalé"
    await registry.close()
    await repo.close()


async def test_la_veille_ne_journalise_pas_la_serie(caplog, monkeypatch):
    from types import SimpleNamespace

    from app import coffre
    from app.pairing.service import PairingError, Reconnexion

    caplog.set_level(logging.DEBUG)
    connu = {"serial": SERIE, "device_id": "eq-000000000001", "name": None,
             "host": "192.0.2.7", "model": "Pepwave MAX HD2"}

    class Depot:
        async def known_devices(self):
            return [connu]

    monkeypatch.setattr(coffre, "lire", lambda s: coffre.Identifiants("admin", "x"))
    for erreur in (PairingError("refus", credentials=True), PairingError("absent"), None):
        async def tenter(*a, erreur=erreur, **k):
            if erreur:
                raise erreur
            return SimpleNamespace()

        monkeypatch.setattr("app.pairing.service.reconnecter", tenter)
        veille = Reconnexion(DeviceRegistry(), Depot(), None, cadence=3.0)
        await veille.tour(0.0)
    assert not any(m in caplog.text.lower() for m in _morceaux(SERIE))


# --- l'API : la série seulement au poste local ----------------------------------------


class _Poller:
    def __init__(self, registry):
        self.registry = registry
        self.latest = {}

    @property
    def fleet(self):
        return [r.config for r in self.registry.snapshot()]

    @property
    def devices(self):
        return {r.id: r.info for r in self.registry.snapshot() if r.info}

    capabilities: dict = {}

    def get_router(self, router_id):
        r = self.registry.get(router_id)
        return r.config if r else None

    def period_for(self, _):
        return None

    cycle_for = tunnel_attendu = period_for

    def never_connected(self, _):
        return []

    alerts = never_connected


@pytest.fixture
async def web():
    from app.fleet import RouterConfig
    from app.config import get_settings

    registry = DeviceRegistry(default_mode="mock", sel_identifiants=SEL)
    record = await registry.add(RouterConfig(
        id=identifiant_opaque(SERIE, SEL), name="HD2", mode="mock", serial=SERIE))
    record.info = INFO
    app = FastAPI()
    app.include_router(routes.router)
    app.state.registry = registry
    app.state.poller = _Poller(registry)
    app.state.settings = get_settings()

    def client(adresse):
        transport = httpx.ASGITransport(app=app, client=(adresse, 5000))
        return httpx.AsyncClient(transport=transport, base_url="http://test")

    yield client
    await registry.close()


@pytest.mark.parametrize("route", ["/api/devices", "/api/routers"])
async def test_un_poste_distant_ne_recoit_pas_la_serie(web, route):
    async with web("192.0.2.50") as c:
        r = await c.get(route)
    assert r.status_code == 200, r.text
    assert not any(m in r.text.lower() for m in _morceaux(SERIE)), r.text
    corps = r.json()[0]
    assert corps.get("serial") is None
    assert corps["device"]["serial"] is None
    assert corps["device"]["model"] == "Pepwave MAX HD2"   # le reste de la fiche demeure


@pytest.mark.parametrize("route", ["/api/devices", "/api/routers"])
async def test_le_poste_local_recoit_la_serie(web, route):
    async with web("127.0.0.1") as c:
        r = await c.get(route)
    assert r.json()[0]["device"]["serial"] == SERIE


async def test_un_mandataire_local_ne_fait_pas_passer_un_poste_distant_pour_local(web):
    async with web("127.0.0.1") as c:
        r = await c.get("/api/devices", headers={"x-forwarded-for": "192.0.2.50"})
    assert r.json()[0]["serial"] is None


async def test_le_detail_d_un_routeur_masque_aussi_la_serie(web):
    ident = identifiant_opaque(SERIE, SEL)
    async with web("192.0.2.50") as c:
        r = await c.get(f"/api/routers/{ident}")
    assert r.status_code == 200 and r.json()["device"]["serial"] is None


# --- la migration des données existantes ----------------------------------------------

T0 = datetime(2026, 9, 11, 20, 0, tzinfo=timezone.utc)
ANCIEN = ancien_identifiant(SERIE)
ORPHELIN = ancien_identifiant("0000-0000-0053")      # oublié depuis : plus de ligne `devices`
FIXE = ancien_identifiant("0000-0000-0054")          # déclaré tel quel dans routers.yaml


async def _base_d_avant(chemin):
    """Une base telle que la version précédente la laissait (user_version 1),
    avec des données sous les anciens identifiants."""
    async with SampleRepository(str(chemin)) as r:
        for rid in (ANCIEN, ORPHELIN, FIXE, "192-0-2-9", "dome-01"):
            await r.insert(RadioSample(router_id=rid, router_name="X", ts=T0,
                                       wan=WanRadioStatus(wan_id="2", wan_name="SIM")))
            sid = await r.open_session(rid, T0)
            await r.touch_session(sid, T0 + timedelta(minutes=1), 1)
            await r.note_journal_read(rid, T0)
        await r.insert_latency_series(ANCIEN, {"2": [(T0, 30.0)]})
        await r.remember_device(serial=SERIE, device_id=ANCIEN, name="HD2", host="192.0.2.7")
        await r.remember_device(serial="0000-0000-0054", device_id=FIXE, name="Y", host=None)
        await r.remember_device(serial="0000-0000-0055", device_id="dome-02", name="Z", host=None)
    with sqlite3.connect(chemin) as c:
        c.execute(
            "INSERT INTO router_events (router_id, ts, kind, wan_id, detail, empreinte) "
            "VALUES (?, ?, 'coupure', '2', NULL, 'e')", (ANCIEN, T0.isoformat()))
        c.execute("PRAGMA user_version = 1")


def _ids(chemin, table):
    with sqlite3.connect(chemin) as c:
        return sorted({r[0] for r in c.execute(f"SELECT router_id FROM {table}")})


_TABLES = ("samples", "router_history", "router_events", "sessions", "router_journal_reads")


async def test_la_migration_renomme_tout_en_une_fois(tmp_path):
    chemin = tmp_path / "samples.db"
    await _base_d_avant(chemin)
    async with SampleRepository(str(chemin), identifiants_fixes={FIXE}) as r:
        sel = r.sel_identifiants
        connus = {c["serial"]: c["device_id"] for c in await r.known_devices()}
    nouveau = identifiant_opaque(SERIE, sel)
    assert connus[SERIE] == nouveau
    assert connus["0000-0000-0054"] == FIXE, "un identifiant de routers.yaml a été renommé"
    assert connus["0000-0000-0055"] == "dome-02"
    for table in _TABLES:
        ids = _ids(chemin, table)
        assert ANCIEN not in ids and ORPHELIN not in ids, table
    assert nouveau in _ids(chemin, "samples")
    assert identifiant_opaque(ORPHELIN, sel) in _ids(chemin, "samples")
    assert {FIXE, "192-0-2-9", "dome-01"} <= set(_ids(chemin, "samples"))
    assert nouveau in _ids(chemin, "router_history") and nouveau in _ids(chemin, "router_events")
    with sqlite3.connect(chemin) as c:
        assert c.execute("PRAGMA user_version").fetchone()[0] == 2
    # La copie d'avant la migration, à côté de la base, sous l'ANCIEN état.
    copie = tmp_path / "samples.db.avant-identifiants-opaques.bak"
    assert copie.exists()
    assert ANCIEN in _ids(copie, "samples")


async def test_la_migration_est_idempotente(tmp_path):
    chemin = tmp_path / "samples.db"
    await _base_d_avant(chemin)
    async with SampleRepository(str(chemin), identifiants_fixes={FIXE}):
        pass
    avant = {t: _ids(chemin, t) for t in _TABLES}
    with sqlite3.connect(chemin) as c:
        c.execute("PRAGMA user_version = 1")   # même rejouée de force
    async with SampleRepository(str(chemin), identifiants_fixes={FIXE}):
        pass
    assert {t: _ids(chemin, t) for t in _TABLES} == avant


async def test_une_migration_qui_echoue_ne_laisse_rien_a_moitie(tmp_path):
    chemin = tmp_path / "samples.db"
    await _base_d_avant(chemin)
    # L'échec tombe APRÈS que les tables de relevés ont été renommées : sur la
    # ligne `devices`, la dernière écrite pour chaque équipement.
    with sqlite3.connect(chemin) as c:
        c.execute("CREATE TRIGGER refus BEFORE UPDATE ON devices "
                  "BEGIN SELECT RAISE(ABORT, 'refus'); END")
    with pytest.raises(sqlite3.DatabaseError):
        async with SampleRepository(str(chemin)):
            pass
    for table in _TABLES:
        assert ANCIEN in _ids(chemin, table), table
    with sqlite3.connect(chemin) as c:
        assert c.execute("PRAGMA user_version").fetchone()[0] == 1
        assert c.execute("SELECT device_id FROM devices WHERE serial = ?", (SERIE,)).fetchone()[0] == ANCIEN


async def test_une_copie_ratee_ne_passe_pas_pour_bonne(tmp_path):
    """Disque plein pendant la copie : le démarrage s'arrête, et le fichier
    entamé est effacé. Sinon, au lancement suivant, il passait pour la copie
    déjà faite et la migration tournait sans copie valide."""
    chemin = tmp_path / "samples.db"
    await _base_d_avant(chemin)
    copie = tmp_path / "samples.db.avant-identifiants-opaques.bak"
    with patch("aiosqlite.Connection.backup", AsyncMock(side_effect=sqlite3.OperationalError("disque plein"))):
        with pytest.raises(sqlite3.OperationalError):
            async with SampleRepository(str(chemin)):
                pass
    assert not copie.exists()
    for table in _TABLES:
        assert ANCIEN in _ids(chemin, table), table


async def test_la_copie_n_est_jamais_ecrite_sous_son_nom_final(tmp_path, monkeypatch):
    """Un processus tué PENDANT la copie (Ctrl+C, fenêtre fermée) ne passe par
    aucun `except` : le fichier entamé restait sous son nom final, tenu pour la
    copie déjà faite au lancement suivant. Elle s'écrit sous un nom temporaire,
    renommé d'un coup une fois complète ; un temporaire orphelin est effacé."""
    from app.storage import db as stockage

    chemin = tmp_path / "samples.db"
    await _base_d_avant(chemin)
    copie = tmp_path / "samples.db.avant-identifiants-opaques.bak"
    orphelin = tmp_path / "samples.db.avant-identifiants-opaques.bak.partiel"
    orphelin.write_bytes(b"entame")
    ouverts: list[str] = []
    connecter = sqlite3.connect

    def espion(cible, *a, **k):
        ouverts.append(str(cible))
        return connecter(cible, *a, **k)

    monkeypatch.setattr(stockage.sqlite3, "connect", espion)
    async with SampleRepository(str(chemin)):
        pass
    assert str(copie) not in ouverts
    assert copie.exists() and not orphelin.exists()
    assert ANCIEN in _ids(copie, "samples")
