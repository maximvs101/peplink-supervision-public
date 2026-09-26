"""R-3.1 (lot B, collecte) : l'heure de RADIO que le routeur garde.

`status.wan.quality` porte, par WAN cellulaire, 360 points à 10 s : RSRP, RSRQ,
SINR, porteuse. Capture du Dome Pro Duo 8.5.1 du 25 septembre 2026, noms de WAN
retirés. Lue à l'appairage (l'heure qui précède l'ouverture de l'outil), puis
toutes les 10 minutes — 41 Ko par lecture.
"""

from __future__ import annotations

import asyncio
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

import app.peplink.router_api as module
from app.fleet import RouterConfig
from app.peplink.router_api import (
    _QUALITE_INTERVAL_S,
    CredentialsRefusedError,
    EndpointRefusedError,
    RouterApiError,
    RouterApiPeplinkClient,
    parse_quality_series,
)
from app.storage.db import SampleRepository

QUALITE = json.loads((Path(__file__).parent / "fixtures" / "dome_pro_duo_8.5.4.json")
                     .read_text(encoding="utf-8"))["status.wan.quality"]
FIN = QUALITE["response"]["2"]["linkQuality"]["pointEnd"]
A_L_HEURE = datetime.fromtimestamp(FIN + 8, tz=timezone.utc)


def test_the_captured_hour_gives_360_dated_points_per_cellular_wan():
    serie = parse_quality_series(QUALITE, A_L_HEURE)
    assert set(serie) == {"2", "3"}, "Ethernet, Wi-Fi sans données et VLAN ne donnent rien"
    assert all(len(points) == 360 for points in serie.values())
    ts, mesure = serie["2"][-1]
    assert ts == datetime.fromtimestamp(round(FIN / 10) * 10, tz=timezone.utc)   # calé sur la grille
    assert set(mesure) == {"rsrp", "rsrq", "sinr", "rat"}
    assert mesure["rat"] == "5G" and isinstance(mesure["rsrp"], float)


def test_an_unmeasured_point_stays_an_absence():
    serie = parse_quality_series(QUALITE, A_L_HEURE)
    brut = QUALITE["response"]["3"]["linkQuality"]
    assert any(v is None for v in brut["rsrp"]), "la capture porte bien des trous"
    for (_, mesure), rsrp, porteuse in zip(serie["3"], brut["rsrp"], brut["bearer"], strict=True):
        assert (mesure["rsrp"] is None) == (rsrp is None)
        # Une porteuse absente ou vide n'est pas une techno.
        assert (mesure["rat"] is None) == (not porteuse)


def test_a_series_on_a_disagreeing_clock_or_of_the_wrong_length_is_set_aside():
    assert parse_quality_series(QUALITE, A_L_HEURE + timedelta(seconds=60)) == {}
    abime = json.loads(json.dumps(QUALITE))
    abime["response"]["2"]["linkQuality"]["rsrq"].pop()
    assert set(parse_quality_series(abime, A_L_HEURE)) == {"3"}


def test_quality_is_read_at_once_then_every_ten_minutes():
    client = RouterApiPeplinkClient(RouterConfig(id="d", name="D", host="h"))
    faux = AsyncMock(return_value=QUALITE)
    with patch.object(client, "_get", faux), patch.object(module, "utcnow", lambda: A_L_HEURE):
        asyncio.run(client._lire_qualite())
        assert faux.await_count == 1 and len(client.serie_qualite["2"]) == 360
        client.serie_qualite = None
        asyncio.run(client._lire_qualite())
        assert faux.await_count == 1 and client.serie_qualite is None
        client._qualite_at -= _QUALITE_INTERVAL_S + 1
        asyncio.run(client._lire_qualite())
        assert faux.await_count == 2
    assert _QUALITE_INTERVAL_S == 600.0


def test_the_quality_read_is_a_complement():
    client = RouterApiPeplinkClient(RouterConfig(id="d", name="D", host="h"))
    with patch.object(client, "_get", AsyncMock(side_effect=RouterApiError("r"))):
        asyncio.run(client._lire_qualite())
    assert client._qualite_at is None and client.serie_qualite is None
    faux = AsyncMock(side_effect=EndpointRefusedError("999"))
    with patch.object(client, "_get", faux):
        asyncio.run(client._lire_qualite())
        asyncio.run(client._lire_qualite())
    assert faux.await_count == 1
    autre = RouterApiPeplinkClient(RouterConfig(id="d", name="D", host="h"))
    with patch.object(autre, "_get", AsyncMock(side_effect=CredentialsRefusedError("401"))):
        with pytest.raises(CredentialsRefusedError):
            asyncio.run(autre._lire_qualite())


async def test_quality_and_latency_share_the_table_without_overwriting_each_other(tmp_path):
    repo = SampleRepository(str(tmp_path / "s.db"))
    await repo.connect()
    t0 = datetime(2026, 9, 25, 8, 0, tzinfo=timezone.utc)
    await repo.insert_latency_series("r1", {"2": [(t0, 30.0)]})
    await repo.insert_quality_series("r1", {"2": [
        (t0, {"rsrp": -100.0, "rsrq": -12.0, "sinr": 9.0, "rat": "5G"})]})
    await repo.insert_latency_series("r1", {"2": [(t0, 31.0)]})
    async with repo._conn.execute("SELECT * FROM router_history") as c:
        lignes = [dict(r) for r in await c.fetchall()]
    assert len(lignes) == 1
    assert {k: lignes[0][k] for k in ("path_latency_ms", "rsrp", "rsrq", "sinr", "rat")} == {
        "path_latency_ms": 31.0, "rsrp": -100.0, "rsrq": -12.0, "sinr": 9.0, "rat": "5G"}
    await repo.close()


async def test_an_existing_router_history_table_gains_the_radio_columns(tmp_path):
    """Le lot A a créé la table sans les colonnes radio, sur la base de
    l'exploitant : elles s'ajoutent à l'ouverture, sans perdre ce qu'elle tient."""
    import aiosqlite

    chemin = str(tmp_path / "s.db")
    async with aiosqlite.connect(chemin) as db:
        await db.execute("CREATE TABLE router_history (router_id TEXT NOT NULL, wan_id TEXT NOT NULL,"
                         " ts TEXT NOT NULL, path_latency_ms REAL, PRIMARY KEY (router_id, wan_id, ts))")
        await db.execute("INSERT INTO router_history VALUES ('r1', '2', '2026-09-25T08:00:00+00:00', 30.0)")
        await db.commit()
    repo = SampleRepository(chemin)
    await repo.connect()
    async with repo._conn.execute("SELECT path_latency_ms, rsrp, serie_latence FROM router_history") as c:
        assert [tuple(r) for r in await c.fetchall()] == [(30.0, None, None)]
    # La ligne d'avant la marque reste sur la courbe : elle porte une latence.
    t = datetime(2026, 9, 25, 8, 0, tzinfo=timezone.utc)
    points, _ = await repo.history("r1", "2", "path_latency_ms", t - timedelta(minutes=1), t + timedelta(minutes=1))
    assert [p.value for p in points] == [30.0]
    await repo.close()


async def test_the_poller_writes_the_radio_series_once_and_forgets_it(tmp_path):
    from app.devices.registry import DeviceRegistry
    from app.fleet import WanConfig
    from app.models import WanRadioStatus
    from app.poller import Poller

    repo = SampleRepository(str(tmp_path / "s.db"))
    await repo.connect()
    registry = DeviceRegistry(default_mode="mock")
    p = Poller(registry, repo, interval_seconds=0.5)
    record = await registry.add(RouterConfig(id="d", name="D", mode="mock",
                                             wans=[WanConfig(id="2", name="SIM")]))
    record.client.get_radio_status = AsyncMock(return_value=[
        WanRadioStatus(wan_id="2", wan_name="SIM", kind="cellular", connected=True)])
    t0 = datetime(2026, 9, 25, 8, 0, tzinfo=timezone.utc)
    record.client.serie_qualite = {"2": [
        (t0 + timedelta(seconds=10 * i), {"rsrp": -100.0, "rsrq": -12.0, "sinr": 9.0, "rat": "5G"})
        for i in range(4)]}
    await p._poll_once()
    assert record.client.serie_qualite is None
    async with repo._conn.execute("SELECT COUNT(*) FROM router_history WHERE rsrp IS NOT NULL") as c:
        assert (await c.fetchone())[0] == 4
    await registry.close()
    await repo.close()


async def _base_avec_trou(tmp_path):
    """Des relevés toutes les 5 s, un TROU de supervision de 3 min au milieu, et
    l'heure du routeur à 10 s par-dessus le tout."""
    repo = SampleRepository(str(tmp_path / "s.db"))
    await repo.connect()
    t0 = datetime(2026, 9, 25, 8, 0, tzinfo=timezone.utc)
    releves = [t0 + timedelta(seconds=5 * i) for i in range(12)]                     # 08:00:00 → 08:00:55
    releves += [t0 + timedelta(minutes=4, seconds=5 * i) for i in range(12)]         # 08:04:00 → 08:04:55
    # Un relevé isolé dans le trou : les points du routeur à moins de 15 s de
    # lui, AVANT comme APRÈS (08:02:10, :20, :30), n'entrent pas.
    releves += [t0 + timedelta(minutes=2, seconds=23)]
    await repo._conn.executemany(
        "INSERT INTO samples (router_id, wan_id, ts, connected, kind, rsrp, rat) "
        "VALUES ('r1', '2', ?, 1, 'cellular', -90.0, 'LTE')",
        [(t.isoformat(),) for t in releves],
    )
    await repo._conn.commit()
    await repo.insert_quality_series("r1", {"2": [
        (t0 + timedelta(seconds=10 * i),
         {"rsrp": None if i == 12 else -110.0, "rsrq": -15.0, "sinr": 3.0, "rat": "5G"})
        for i in range(30)]})                                                       # 08:00:00 → 08:04:50
    # Une ligne de LATENCE seule, dans le trou : pas une mesure de radio.
    await repo.insert_latency_series("r1", {"2": [(t0 + timedelta(minutes=2, seconds=5), 30.0)]})
    return repo, t0


async def test_the_router_hour_fills_only_the_supervision_gap_and_says_so(tmp_path):
    repo, t0 = await _base_avec_trou(tmp_path)
    points, _ = await repo.history("r1", "2", "rsrp", t0, t0 + timedelta(minutes=5))
    comble = [p for p in points if p.source == "routeur"]
    nous = [p for p in points if p.source is None]
    assert len(nous) == 25 and all(p.value == -90.0 for p in nous)
    # Le trou, pour le routeur, va de 08:01:10 à 08:03:40 : ses points à 15 s ou
    # plus de tout relevé. Seize points, dont un non mesuré — gardé hors de la
    # courbe, qui s'y interrompt.
    assert [p.ts for p in comble] == [
        t0 + timedelta(seconds=10 * i) for i in range(7, 23) if i not in (12, 13, 14, 15)]
    assert all(p.value == -110.0 and p.rat == "5G" for p in comble)
    await repo.close()


async def test_the_filled_points_take_the_dense_path_too(tmp_path, monkeypatch):
    import app.storage.db as db

    monkeypatch.setattr(db, "_LIGNES_MEMOIRE_MAX", 5)
    repo, t0 = await _base_avec_trou(tmp_path)
    points, disponibles = await repo.history("r1", "2", "rsrp", t0, t0 + timedelta(minutes=5), limit=8)
    assert disponibles == 25 + 12
    assert any(p.source == "routeur" for p in points) and any(p.source is None for p in points)
    await repo.close()


async def test_a_metric_the_router_does_not_keep_is_untouched(tmp_path):
    repo, t0 = await _base_avec_trou(tmp_path)
    points, n = await repo.history("r1", "2", "rx_kbps", t0, t0 + timedelta(minutes=5))
    assert n == 25 and all(p.source is None for p in points)
    await repo.close()


async def test_the_latency_curve_ignores_the_radio_rows_of_the_shared_table(tmp_path):
    """Vu en direct le 25/09 : la radio arrive sur une grille décalée d'une
    seconde (…:50 contre …:51). La courbe de latence ramassait ses lignes comme
    autant de latences NON MESURÉES — 107 points sur 10 min, dont 53 mesurés :
    un trou tous les deux points."""
    repo = SampleRepository(str(tmp_path / "s.db"))
    await repo.connect()
    t0 = datetime(2026, 9, 25, 8, 0, 1, tzinfo=timezone.utc)
    await repo.insert_latency_series("r1", {"3": [
        (t0 + timedelta(seconds=10 * i), None if i == 4 else 30.0) for i in range(6)]})
    await repo.insert_quality_series("r1", {"3": [
        (t0 + timedelta(seconds=10 * i - 1), {"rsrp": -100.0, "rsrq": None, "sinr": None, "rat": "5G"})
        for i in range(6)]})
    points, disponibles = await repo.history("r1", "3", "path_latency_ms", t0 - timedelta(minutes=1),
                                             t0 + timedelta(minutes=2))
    assert disponibles == 6
    # Le point de latence non mesuré reste une absence, comme avant.
    assert [p.value for p in points] == [30.0, 30.0, 30.0, 30.0, None, 30.0]
    await repo.close()


async def test_the_fallback_to_samples_ends_at_the_first_LATENCY_point_not_the_first_radio_one(tmp_path):
    """La radio gardée plus tôt que la latence ne doit pas couper la reprise des
    relevés : ceux d'avant la première latence gardée restent sur la courbe."""
    repo = SampleRepository(str(tmp_path / "s.db"))
    await repo.connect()
    t0 = datetime(2026, 9, 25, 8, 0, tzinfo=timezone.utc)
    await repo._conn.executemany(
        "INSERT INTO samples (router_id, wan_id, ts, connected, kind, path_latency_ms) "
        "VALUES ('r1', '3', ?, 1, 'cellular', 50.0)",
        [((t0 + timedelta(minutes=m)).isoformat(),) for m in range(5)],
    )
    await repo._conn.commit()
    await repo.insert_quality_series("r1", {"3": [
        (t0 - timedelta(minutes=1), {"rsrp": -100.0, "rsrq": None, "sinr": None, "rat": "5G"})]})
    await repo.insert_latency_series("r1", {"3": [(t0 + timedelta(minutes=5), 30.0)]})
    points, _ = await repo.history("r1", "3", "path_latency_ms", t0 - timedelta(minutes=2), t0 + timedelta(minutes=6))
    assert [p.value for p in points] == [50.0] * 5 + [30.0]
    series = await repo.latency_series("r1", (t0 - timedelta(minutes=2)).isoformat(),
                                       (t0 + timedelta(minutes=6)).isoformat())
    assert series["3"][0] == [(t0 + timedelta(minutes=5)).isoformat()]
    await repo.close()
