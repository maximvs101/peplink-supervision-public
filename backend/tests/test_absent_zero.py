"""« Une grandeur absente n'est jamais un zéro » : trois compteurs qui disaient 0
faute de donnée (revue du 24/10).

  - `SessionLink.maneuvers` : 0 quand le journal du routeur n'avait pas été lu ;
  - `SessionSummary.sites` : 0 quand aucune station n'était identifiable ;
  - `LinkChurn.station_changes` : 0 sans aucune station relevée.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import httpx
import pytest
from fastapi import FastAPI

from app.api import routes
from app.devices.registry import DeviceRegistry
from app.fleet import RouterConfig, WanConfig
from app.models import RadioSample, RouterEvent, Session, WanRadioStatus
from app.poller import Poller
from app.sessions import build_summary
from app.storage.db import SampleRepository

T0 = datetime(2026, 9, 11, 20, 0, 0, tzinfo=timezone.utc)


class _PollerFictif:
    def __init__(self, ids):
        self._ids = set(ids)

    def get_router(self, router_id):
        return object() if router_id in self._ids else None


@pytest.fixture
async def client(tmp_path):
    repo = SampleRepository(str(tmp_path / "s.db"))
    await repo.connect()
    for i in range(6):
        await repo.insert(RadioSample(
            router_id="r1", router_name="R1", ts=T0 + timedelta(seconds=6 * i),
            wan=WanRadioStatus(wan_id="4", wan_name="SIM", rsrp=-90.0, sinr=12.0),
        ))
    app = FastAPI()
    app.include_router(routes.router)
    app.state.repo = repo
    app.state.poller = _PollerFictif(["r1"])
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as c:
        c.repo = repo
        yield c
    await repo.close()


async def _fiche(client, sid):
    r = await client.get(f"/api/sessions/{sid}/summary")
    assert r.status_code == 200, r.text
    return r.json()


# --- 4. maneuvers ---------------------------------------------------------------


async def test_sans_lecture_du_journal_les_manoeuvres_sont_inconnues(client):
    """Journal jamais lu sur la fenêtre — refus, client sans journal, horloge
    décalée : « 0 manœuvre » affirmait une absence que rien n'avait vérifiée."""
    sid = await client.repo.open_session("r1", T0 - timedelta(minutes=1))
    await client.repo.touch_session(sid, T0 + timedelta(minutes=5), 6)
    lien = next(l for l in (await _fiche(client, sid))["links"] if l["wan_id"] == "4")
    assert lien["maneuvers"] is None


async def test_un_journal_lu_sans_manoeuvre_compte_zero(client):
    sid = await client.repo.open_session("r1", T0 - timedelta(minutes=1))
    await client.repo.touch_session(sid, T0 + timedelta(minutes=5), 6)
    await client.repo.note_journal_read("r1", T0 + timedelta(minutes=2))
    lien = next(l for l in (await _fiche(client, sid))["links"] if l["wan_id"] == "4")
    assert lien["maneuvers"] == 0


async def test_une_lecture_hors_de_la_fenetre_ne_vaut_pas_pour_elle(client):
    sid = await client.repo.open_session("r1", T0 - timedelta(minutes=1))
    await client.repo.touch_session(sid, T0 + timedelta(minutes=5), 6)
    await client.repo.note_journal_read("r1", T0 + timedelta(hours=3))
    lien = next(l for l in (await _fiche(client, sid))["links"] if l["wan_id"] == "4")
    assert lien["maneuvers"] is None


async def test_un_fait_du_journal_dans_la_fenetre_prouve_sa_lecture(client):
    """Les bases d'avant le suivi des lectures : un fait retenu dans la fenêtre
    dit à lui seul que le journal a été lu."""
    sid = await client.repo.open_session("r1", T0 - timedelta(minutes=1))
    await client.repo.touch_session(sid, T0 + timedelta(minutes=5), 6)
    await client.repo.insert_router_events("r1", [
        RouterEvent(ts=T0 + timedelta(seconds=10), kind="connexion admin réussie", wan_id=None,
                    empreinte="a" * 24),
    ])
    lien = next(l for l in (await _fiche(client, sid))["links"] if l["wan_id"] == "4")
    assert lien["maneuvers"] == 0


async def test_le_poller_note_chaque_lecture_aboutie_du_journal(tmp_path):
    repo = SampleRepository(str(tmp_path / "s.db"))
    await repo.connect()
    registry = DeviceRegistry(default_mode="mock")
    p = Poller(registry, repo, interval_seconds=0.5)
    try:
        record = await registry.add(RouterConfig(
            id="a", name="A", mode="mock", wans=[WanConfig(id="cellular-1", name="SIM")]))

        async def journal(noms):
            return []

        record.client.get_router_events = journal
        debut = datetime.now(timezone.utc) - timedelta(minutes=1)
        await p._lire_journal_routeur(record, [])
        fin = datetime.now(timezone.utc) + timedelta(minutes=1)
        assert await repo.journal_read_between("a", debut, fin) is True

        # Un journal que le client n'a pas pu lire (`None`) n'est pas une lecture.
        async def illisible(noms):
            return None

        p._journal_lu_a.clear()
        record.client.get_router_events = illisible
        await p._lire_journal_routeur(record, [])
        assert await repo.journal_read_between(
            "a", fin + timedelta(hours=1), fin + timedelta(hours=2)) is False
    finally:
        await registry.close()
        await repo.close()


# --- 5. sites -------------------------------------------------------------------


def _session() -> Session:
    return Session(id=1, router_id="r1", started_at=T0, last_seen=T0 + timedelta(minutes=5), samples=2)


def _ligne(wan, i, **over):
    """Une ligne de `session_rows`, aux clés de la requête réelle (voir
    `tests/test_sessions.py`)."""
    base = dict(
        wan_id=wan, ts=(T0 + timedelta(seconds=5 * i)).isoformat(), connected=1,
        tx_kbps=0.0, rx_kbps=0.0, rtt_ms=None, rsrp=None, cell_id=None, enb_id=None,
        earfcn=None, tunnel_state=None,
        uptime_s=1000 + 5 * i, site_lat=None, site_lon=None, kind="cellular", operator="Orange",
        plmn="208-01", status_message=None, path_latency_ms=None,
    )
    base.update(over)
    return base


def test_sans_station_identifiable_les_sites_sont_inconnus():
    lignes = [
        _ligne("4", 0, cell_id=25735433, enb_id=None),   # 5G SA : station inconnue
        _ligne("5", 0, cell_id=25735426, enb_id=None),
    ]
    assert build_summary(_session(), lignes, None).sites is None


# --- 6. station_changes ---------------------------------------------------------


async def test_sans_station_relevee_les_changements_de_station_sont_inconnus(client):
    # WAN 5 : des cellules publiées, mais aucune station décomposable (5G SA).
    for i in range(4):
        await client.repo.insert(RadioSample(
            router_id="r1", router_name="R1", ts=T0 + timedelta(seconds=6 * i),
            wan=WanRadioStatus(wan_id="5", wan_name="SIM", rsrp=-90.0, cell_id=1000 + i,
                               enb_id=None, rat="5G SA"),
        ))
    # WAN 6 : stations connues, stables.
    for i in range(4):
        await client.repo.insert(RadioSample(
            router_id="r1", router_name="R1", ts=T0 + timedelta(seconds=6 * i),
            wan=WanRadioStatus(wan_id="6", wan_name="SIM", rsrp=-90.0, cell_id=2560, enb_id=10),
        ))
    r = await client.get("/api/routers/r1/events", params={
        "since": (T0 - timedelta(minutes=1)).isoformat(),
        "until": (T0 + timedelta(minutes=5)).isoformat(),
    })
    assert r.status_code == 200, r.text
    churn = {c["wan_id"]: c for c in r.json()["churn"]}
    assert churn["4"]["station_changes"] is None      # ni cellule ni station
    assert churn["5"]["has_cells"] is True
    assert churn["5"]["station_changes"] is None      # cellules, mais aucune station
    assert churn["6"]["station_changes"] == 0         # une station, jamais quittée
