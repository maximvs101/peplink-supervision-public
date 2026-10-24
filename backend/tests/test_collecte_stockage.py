"""Collecte et stockage : ce que le cycle écrit, et ce qu'il n'écrit pas.

Les défauts couverts ici (revue du 24/10) : une latence recopiée sur un lien
absent du relevé, des transactions partagées entre équipements sans verrou ni
annulation, une alerte admin figée par l'alerte d'horloge, et une purge de
`router_history` rejouée à chaque démarrage.
"""

from __future__ import annotations

import asyncio
import sqlite3
from datetime import timedelta

import pytest

from app.devices.registry import DeviceRegistry
from app.fleet import RouterConfig, WanConfig
from app.models import Alert, RadioSample, WanRadioStatus, utcnow
from app.poller import Poller
from app.storage.db import SampleRepository


def _config(device_id: str) -> RouterConfig:
    return RouterConfig(
        id=device_id, name=device_id.upper(), mode="mock",
        wans=[WanConfig(id="cellular-1", name="SIM")],
    )


def _releve(router_id: str, wan_id: str = "2", **wan) -> RadioSample:
    return RadioSample(
        router_id=router_id, router_name=router_id.upper(),
        wan=WanRadioStatus(wan_id=wan_id, wan_name="SIM", connected=True, **wan),
    )


@pytest.fixture
async def repo(tmp_path):
    r = SampleRepository(str(tmp_path / "s.db"))
    await r.connect()
    yield r
    await r.close()


@pytest.fixture
async def poller(repo):
    registry = DeviceRegistry(default_mode="mock")
    p = Poller(registry, repo, interval_seconds=0.5)
    yield p, registry
    await registry.close()


async def _compte(repo: SampleRepository, router_id: str) -> int:
    async with repo._conn.execute(
        "SELECT COUNT(*) FROM samples WHERE router_id = ?", (router_id,)
    ) as cur:
        return (await cur.fetchone())[0]


# --- 7. un lien absent du relevé ne garde pas sa latence ------------------------


def test_un_lien_absent_du_releve_ne_recopie_pas_sa_latence(poller):
    """La branche « absent du relevé » remettait radio, débits et tunnel à vide
    mais recopiait `path_latency_ms` : la courbe traçait une latence mesurée sur
    un lien qui n'existait plus. La branche « désactivé » l'effaçait déjà."""
    p, _ = poller
    p.latest["r"] = {"2": _releve("r", path_latency_ms=42.0, path_latency_recent_ms=42.0,
                                  path_latency_at=utcnow())}
    adieux = p._prune_absent_wans("r", seen=set())
    assert len(adieux) == 1
    wan = adieux[0].wan
    assert wan.status_message == "absent du relevé"
    assert wan.path_latency_ms is None
    assert wan.path_latency_recent_ms is None and wan.path_latency_at is None


# --- 8. transactions : verrou, annulation, ordre de last_seen -------------------


async def test_l_ecriture_d_un_autre_equipement_ne_valide_pas_une_transaction_en_cours(repo):
    """Un `insert(commit=False)` d'un équipement était validé par le
    `touch_session` d'un autre, qui écrivait pendant que le premier attendait.
    Le verrou fait attendre le second ; l'échec du premier annule SES lignes."""
    t0 = utcnow()
    sid = await repo.open_session("b", t0)
    tache = None
    with pytest.raises(RuntimeError):
        async with repo.transaction():
            await repo.insert(_releve("a"), commit=False)
            tache = asyncio.create_task(repo.touch_session(sid, t0 + timedelta(seconds=5), 3))
            # L'autre écrivain a la main ; il doit attendre la fin de la transaction.
            await asyncio.sleep(0.05)
            assert not tache.done(), "l'autre écriture n'a pas attendu le verrou"
            raise RuntimeError("échec au milieu du cycle")
    await tache
    assert await _compte(repo, "a") == 0, "la transaction échouée a été validée par un tiers"
    session = await repo.session_by_id(sid)
    assert session["samples"] == 3


async def test_un_commit_rate_est_annule_et_pas_valide_plus_tard(poller, monkeypatch):
    """Si `commit()` échouait, la transaction restait ouverte et le commit
    suivant — celui de n'importe quelle autre écriture — l'écrivait quand même,
    alors que le compte de session ne l'avait pas comptée. `last_seen`, lui,
    avait déjà été avancé avant l'écriture."""
    p, registry = poller
    await registry.add(_config("a"))
    await p._poll_once()
    repo = p._repo
    avant = await _compte(repo, "a")
    assert avant > 0
    session_avant = await repo.current_session("a")

    vrai_commit = repo._conn.commit

    async def commit_rate():
        raise sqlite3.OperationalError("disk I/O error")

    monkeypatch.setattr(repo._conn, "commit", commit_rate)
    await p._poll_once()
    monkeypatch.setattr(repo._conn, "commit", vrai_commit)

    # N'importe quelle écriture ultérieure : elle ne doit rien emporter avec elle.
    await repo.remember_device(serial="x", device_id="x", name="x", host=None)
    assert await _compte(repo, "a") == avant, "les relevés d'un commit raté ont été écrits plus tard"
    session_apres = await repo.current_session("a")
    assert session_apres["samples"] == session_avant["samples"]
    assert session_apres["last_seen"] == session_avant["last_seen"], (
        "la fin de session a avancé sans aucun relevé écrit"
    )


async def test_le_compte_et_la_fin_de_session_suivent_les_releves_ecrits(poller):
    p, registry = poller
    await registry.add(_config("a"))
    await p._poll_once()
    await p._poll_once()
    session = await p._repo.current_session("a")
    assert session["samples"] == await _compte(p._repo, "a")
    async with p._repo._conn.execute("SELECT MAX(ts) FROM samples WHERE router_id = 'a'") as cur:
        dernier = (await cur.fetchone())[0]
    assert session["last_seen"] == dernier


# --- 9. l'alerte admin ne reste pas figée pendant une alerte d'horloge ----------


async def test_l_alerte_admin_ne_reste_pas_figee_pendant_une_alerte_d_horloge(poller):
    p, registry = poller
    record = await registry.add(_config("a"))

    async def journal(noms):
        return []

    record.client.get_router_events = journal
    p._alerte_admin["a"] = Alert(kind="connexions admin refusées", wan_id=None,
                                 since=utcnow(), detail="", sustained_s=0.0)
    p._alerte_horloge["a"] = Alert(kind="horloge du routeur décalée", wan_id=None,
                                   since=utcnow(), detail="", sustained_s=0.0)
    await p._lire_journal_routeur(record, [])
    kinds = {a.kind for a in p.alerts("a")}
    assert "connexions admin refusées" not in kinds, (
        "une alerte fondée sur un journal qu'on ne lit plus restait affichée"
    )
    assert "horloge du routeur décalée" in kinds


# --- 10. la purge hors grille n'est jouée qu'une fois ---------------------------


async def test_la_purge_hors_grille_n_est_jouee_qu_une_fois(tmp_path):
    chemin = str(tmp_path / "s.db")
    async with SampleRepository(chemin) as r:
        await r._conn.execute(
            "INSERT INTO router_history (router_id, wan_id, ts, path_latency_ms) "
            "VALUES ('a', '2', '2026-10-01T10:00:05+00:00', 30.0)")
        await r._conn.commit()
    # Second démarrage : la ligne écrite APRÈS la migration n'est pas balayée.
    async with SampleRepository(chemin) as r:
        async with r._conn.execute("SELECT COUNT(*) FROM router_history") as cur:
            assert (await cur.fetchone())[0] == 1, "la purge a été rejouée au démarrage"


async def test_une_base_d_avant_la_migration_est_purgee_une_fois(tmp_path):
    chemin = tmp_path / "s.db"
    async with SampleRepository(str(chemin)) as r:
        pass
    with sqlite3.connect(chemin) as c:
        c.execute("INSERT INTO router_history (router_id, wan_id, ts) "
                  "VALUES ('a', '2', '2026-10-01T10:00:05+00:00')")
        c.execute("INSERT INTO router_history (router_id, wan_id, ts) "
                  "VALUES ('a', '2', '2026-10-01T10:00:10+00:00')")
        c.execute("PRAGMA user_version = 0")
    async with SampleRepository(str(chemin)) as r:
        async with r._conn.execute("SELECT ts FROM router_history") as cur:
            assert [row[0] for row in await cur.fetchall()] == ["2026-10-01T10:00:10+00:00"]
        async with r._conn.execute("PRAGMA user_version") as cur:
            assert (await cur.fetchone())[0] >= 1
