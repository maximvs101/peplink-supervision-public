"""R-3.2 (lot A) : garder la série de latence ENTIÈRE que le routeur publie.

`status.wan.latency` porte, par WAN, l'heure écoulée à un point / 10 s. Elle est
téléchargée chaque minute ; on n'en gardait que le dernier point. Elle va dans
`router_history` — jamais dans `samples`, qui dit « un WAN à un instant » avec
sa connexion et son uptime, que cette série ne donne pas.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

from app.peplink.router_api import parse_latency_series
from app.storage.db import SampleRepository

HD2 = json.loads((Path(__file__).parent / "fixtures" / "max_hd2_8.5.4.json").read_text(encoding="utf-8"))


def _charge(debut: int, points: list, fin: int | None = None) -> dict:
    return {"response": {"4": {"latency": {
        "data": points, "pointStart": debut, "pointInterval": 10,
        "pointEnd": fin if fin is not None else debut + 10 * (len(points) - 1),
    }}}}


def test_every_point_is_dated_by_the_router_and_an_absence_stays_an_absence():
    debut = 1_790_000_000
    maintenant = datetime.fromtimestamp(debut + 25, tz=timezone.utc)
    serie = parse_latency_series(_charge(debut, [30, None, 31]), maintenant)
    assert serie == {"4": [
        (datetime.fromtimestamp(debut, tz=timezone.utc), 30.0),
        (datetime.fromtimestamp(debut + 10, tz=timezone.utc), None),
        (datetime.fromtimestamp(debut + 20, tz=timezone.utc), 31.0),
    ]}


def test_the_captured_hour_has_360_points_per_measured_wan():
    fin = HD2["status.wan.latency"]["response"]["4"]["latency"]["pointEnd"]
    serie = parse_latency_series(HD2["status.wan.latency"], datetime.fromtimestamp(fin, tz=timezone.utc))
    assert set(serie) == {"1", "4", "5"}
    assert all(len(points) == 360 for points in serie.values())
    # Calé sur la grille de 10 s : le début de série glisse d'une lecture à l'autre.
    assert serie["4"][-1][0] == datetime.fromtimestamp(round(fin / 10) * 10, tz=timezone.utc)


def test_a_series_whose_clock_disagrees_by_more_than_a_minute_is_set_aside():
    """On ne décale pas des points sur une horloge qu'on ne sait pas juger."""
    debut = 1_790_000_000
    fin = debut + 20
    a_l_heure = datetime.fromtimestamp(fin + 60, tz=timezone.utc)
    en_avance = datetime.fromtimestamp(fin + 61, tz=timezone.utc)
    assert "4" in parse_latency_series(_charge(debut, [1, 2, 3]), a_l_heure)
    assert parse_latency_series(_charge(debut, [1, 2, 3]), en_avance) == {}
    # Une série incohérente — plus de points que l'intervalle n'en contient — aussi.
    assert parse_latency_series(_charge(debut, [1, 2, 3], fin=debut + 10), a_l_heure) == {}


async def test_the_series_is_stored_once_however_often_the_hour_is_reread(tmp_path):
    repo = SampleRepository(str(tmp_path / "s.db"))
    await repo.connect()
    t0 = datetime(2026, 9, 25, 8, 0, tzinfo=timezone.utc)
    premiere = {"4": [(t0 + timedelta(seconds=10 * i), 30.0 + i) for i in range(6)]}
    seconde = {"4": [(t0 + timedelta(seconds=10 * i), 30.0 + i) for i in range(3, 9)]}
    await repo.insert_latency_series("r1", premiere)
    await repo.insert_latency_series("r1", seconde)
    points, disponibles = await repo.history("r1", "4", "path_latency_ms", t0, t0 + timedelta(minutes=5))
    assert disponibles == 9
    assert [p.value for p in points] == [30.0 + i for i in range(9)]
    await repo.close()


async def test_the_latency_curve_reads_the_router_series_and_falls_back_before_it(tmp_path):
    """Une mesure par minute AVANT la première série gardée (les données d'avant
    cette version), la série du routeur ENSUITE — sans doublon avec les relevés,
    qui portent chacun une copie du dernier point."""
    repo = SampleRepository(str(tmp_path / "s.db"))
    await repo.connect()
    t0 = datetime(2026, 9, 25, 8, 0, tzinfo=timezone.utc)
    await repo._conn.executemany(
        "INSERT INTO samples (router_id, wan_id, ts, connected, kind, path_latency_ms) "
        "VALUES ('r1', '4', ?, 1, 'cellular', ?)",
        [((t0 + timedelta(minutes=m)).isoformat(), 50.0 + m) for m in range(0, 10)],
    )
    await repo._conn.commit()
    debut_serie = t0 + timedelta(minutes=5)
    # La série couvre les cinq dernières minutes, comme la relecture de l'heure
    # le fait en service : chaque relevé de cette période en a déjà la copie.
    await repo.insert_latency_series("r1", {"4": [
        (debut_serie + timedelta(seconds=10 * i), None if i == 2 else 30.0) for i in range(30)]})
    points, _ = await repo.history("r1", "4", "path_latency_ms", t0, t0 + timedelta(minutes=10))
    avant = [p for p in points if p.ts < debut_serie]
    apres = [p for p in points if p.ts >= debut_serie]
    assert [p.value for p in avant] == [50.0, 51.0, 52.0, 53.0, 54.0]
    assert len(apres) == 30 and apres[2].value is None and apres[3].value == 30.0
    # Une autre grandeur ne change pas de source.
    _rsrp, n = await repo.history("r1", "4", "rsrp", t0, t0 + timedelta(minutes=10))
    assert n == 10
    await repo.close()


async def test_the_link_latency_survives_a_day_without_router_series(tmp_path):
    """C-9 : une série existe le 2 septembre ; le 5, une session sans série
    compte 30 relevés porteurs de latence.

    Les relevés n'étaient gardés qu'AVANT le tout premier point de série de
    l'équipement, un minimum pris sur toute sa vie : l'historique rendait 0
    point pour le 5 et la fiche publiait 0 mesure — une absence affichée là où
    30 mesures sont en base. Un relevé n'est écarté que si la série couvre
    effectivement son instant.
    """
    repo = SampleRepository(str(tmp_path / "s.db"))
    await repo.connect()
    jour_a = datetime(2026, 9, 2, 8, 0, tzinfo=timezone.utc)
    await repo.insert_latency_series("r1", {"4": [
        (jour_a + timedelta(seconds=10 * i), 30.0) for i in range(6)]})
    jour_b = datetime(2026, 9, 5, 8, 0, tzinfo=timezone.utc)
    await repo._conn.executemany(
        "INSERT INTO samples (router_id, wan_id, ts, connected, kind, path_latency_ms) "
        "VALUES ('r1', '4', ?, 1, 'cellular', ?)",
        [((jour_b + timedelta(minutes=m)).isoformat(), 40.0 + m) for m in range(30)],
    )
    await repo._conn.commit()
    points, n = await repo.history("r1", "4", "path_latency_ms", jour_b, jour_b + timedelta(hours=1))
    assert n == 30, "les mesures du jour sans série ont disparu de l'historique"
    assert points[0].value == 40.0

    series = await repo.latency_series("r1", jour_b.isoformat(), (jour_b + timedelta(hours=1)).isoformat())
    from app.sessions import _resume_lien

    lignes = [
        {"wan_id": "4", "kind": "cellular", "ts": (jour_b + timedelta(minutes=m)).isoformat(),
         "connected": 1, "path_latency_ms": 40.0 + m, "rtt_ms": None, "rsrp": None,
         "cell_id": None, "enb_id": None, "earfcn": None, "tunnel_state": None,
         "tx_kbps": None, "rx_kbps": None, "uptime_s": 100 + 60 * m,
         "site_lat": None, "site_lon": None, "operator": "Orange", "plmn": "208-01",
         "status_message": None}
        for m in range(30)
    ]
    lien = _resume_lien(lignes, None, series.get("4"))
    assert lien.path_latency_samples == 30
    await repo.close()


async def test_a_dense_window_takes_the_router_series_too(tmp_path, monkeypatch):
    """Au-delà du seuil de lignes en mémoire, l'enveloppe se calcule en SQL :
    même source, et le pic garde sa place."""
    import app.storage.db as module

    monkeypatch.setattr(module, "_LIGNES_MEMOIRE_MAX", 10)
    repo = SampleRepository(str(tmp_path / "s.db"))
    await repo.connect()
    t0 = datetime(2026, 9, 25, 8, 0, tzinfo=timezone.utc)
    await repo.insert_latency_series("r1", {"4": [
        (t0 + timedelta(seconds=10 * i), 900.0 if i == 17 else 30.0) for i in range(60)]})
    points, disponibles = await repo.history("r1", "4", "path_latency_ms", t0, t0 + timedelta(hours=1), limit=10)
    assert disponibles == 60
    assert 900.0 in [p.value for p in points]
    await repo.close()


def test_the_client_keeps_the_whole_series_it_already_downloads():
    """Zéro requête de plus : la charge utile lue chaque minute porte l'heure."""
    import asyncio
    from unittest.mock import AsyncMock, patch

    import app.peplink.router_api as module
    from app.fleet import RouterConfig
    from app.models import WanRadioStatus
    from app.peplink.router_api import RouterApiPeplinkClient

    charge = HD2["status.wan.latency"]
    fin = charge["response"]["4"]["latency"]["pointEnd"]
    client = RouterApiPeplinkClient(RouterConfig(id="r", name="R", host="h"))
    faux = AsyncMock(return_value=charge)
    with patch.object(client, "_get", faux), \
            patch.object(module, "utcnow", lambda: datetime.fromtimestamp(fin + 5, tz=timezone.utc)):
        asyncio.run(client._attach_path_latency([WanRadioStatus(wan_id="4", wan_name="W")]))
    assert faux.await_count == 1
    assert len(client.serie_latence["4"]) == 360


async def test_the_poller_writes_the_series_once_and_forgets_it(tmp_path):
    from unittest.mock import AsyncMock

    from app.devices.registry import DeviceRegistry
    from app.fleet import RouterConfig, WanConfig
    from app.models import WanRadioStatus
    from app.poller import Poller

    repo = SampleRepository(str(tmp_path / "s.db"))
    await repo.connect()
    registry = DeviceRegistry(default_mode="mock")
    p = Poller(registry, repo, interval_seconds=0.5)
    record = await registry.add(RouterConfig(id="d", name="D", mode="mock",
                                             wans=[WanConfig(id="4", name="SIM")]))
    record.client.get_radio_status = AsyncMock(return_value=[
        WanRadioStatus(wan_id="4", wan_name="SIM", kind="cellular", connected=True)])
    t0 = datetime.now(timezone.utc).replace(microsecond=0) - timedelta(minutes=1)
    record.client.serie_latence = {"4": [(t0 + timedelta(seconds=10 * i), 30.0) for i in range(6)]}
    await p._poll_once()
    assert record.client.serie_latence is None
    _points, n = await repo.history("d", "4", "path_latency_ms", t0, t0 + timedelta(minutes=2))
    assert n == 6
    await registry.close()
    await repo.close()


async def test_a_series_whose_start_jitters_by_a_second_lands_on_the_same_instants(tmp_path):
    """Vu en direct le 25/09 : le début de la série glisse d'une seconde d'une
    lecture à l'autre (…:00, puis …:01). Relire l'heure l'écrivait sous des
    horodatages NEUFS — 88 points de latence en 10 min au lieu de 60. Les points
    se calent sur la grille de 10 s : une relecture retombe sur les mêmes."""
    from app.peplink.router_api import parse_quality_series

    debut = 1_790_000_000
    maintenant = datetime.fromtimestamp(debut + 60, tz=timezone.utc)
    a = parse_latency_series(_charge(debut, [30] * 6), maintenant)
    b = parse_latency_series(_charge(debut + 1, [31] * 6), maintenant)
    assert [t for t, _ in a["4"]] == [t for t, _ in b["4"]]
    repo = SampleRepository(str(tmp_path / "s.db"))
    await repo.connect()
    await repo.insert_latency_series("r1", a)
    await repo.insert_latency_series("r1", b)
    _, disponibles = await repo.history("r1", "4", "path_latency_ms", maintenant - timedelta(minutes=5), maintenant)
    assert disponibles == 6
    await repo.close()
    # La radio se cale sur la même grille.
    q = {"response": {"2": {"linkQuality": {
        "pointStart": debut - 1, "pointInterval": 10, "pointEnd": debut + 49,
        "rsrp": [-100] * 6, "rsrq": [-10] * 6, "sinr": [5] * 6, "bearer": ["5G"] * 6}}}}
    assert [t for t, _ in parse_quality_series(q, maintenant)["2"]] == [t for t, _ in a["4"]]


def test_a_series_starting_on_a_half_step_keeps_every_point():
    """C-11 : `round()` arrondit les demies au PAIR. Une série qui commence à
    …5 s voyait 15 et 25 tomber sur 20, 35 et 45 sur 40 : 360 points pour 180
    instants, l'upsert n'en gardant qu'un — la moitié de l'heure perdue, le
    reste décalé de 5 s."""
    debut = 1_790_000_005
    maintenant = datetime.fromtimestamp(debut + 10 * 359 + 5, tz=timezone.utc)
    serie = parse_latency_series(_charge(debut, [30] * 360), maintenant)
    instants = [t for t, _ in serie["4"]]
    assert len(set(instants)) == 360, "des points se sont fondus sur le même instant"
    # Et le calage reste stable d'une lecture à l'autre, décalée d'une seconde.
    b = parse_latency_series(_charge(debut + 1, [30] * 360), maintenant)
    assert [t for t, _ in b["4"]] == instants


async def test_the_rows_written_off_the_grid_before_the_fix_are_removed_at_opening(tmp_path):
    """Avant le calage sur la grille de 10 s, chaque relecture de l'heure
    écrivait ses points sous des horodatages décalés d'une seconde : une heure
    de latence en double dans la base de l'exploitant (360 lignes le 25/09).
    L'ouverture de la base les retire ; les points calés ne sont pas touchés, et
    plus rien ne s'écrit hors de la grille depuis le correctif."""
    import aiosqlite

    chemin = str(tmp_path / "s.db")
    repo = SampleRepository(chemin)
    await repo.connect()
    t0 = datetime(2026, 9, 25, 8, 0, tzinfo=timezone.utc)
    await repo.insert_latency_series("r1", {"3": [(t0 + timedelta(seconds=10 * i), 30.0) for i in range(6)]})
    await repo.close()
    async with aiosqlite.connect(chemin) as db:   # des restes d'avant le correctif
        await db.executemany(
            "INSERT INTO router_history (router_id, wan_id, ts, path_latency_ms, serie_latence) VALUES ('r1', '3', ?, 31.0, 1)",
            [((t0 + timedelta(seconds=10 * i + 1)).isoformat(),) for i in range(6)])
        await db.commit()
    repo = SampleRepository(chemin)
    await repo.connect()
    _, disponibles = await repo.history("r1", "3", "path_latency_ms", t0 - timedelta(minutes=1), t0 + timedelta(minutes=2))
    assert disponibles == 6
    async with repo._conn.execute("SELECT COUNT(*) FROM router_history WHERE path_latency_ms = 30.0") as c:
        assert (await c.fetchone())[0] == 6
    await repo.close()
