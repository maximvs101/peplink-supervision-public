"""Rétention : regrouper les vieux relevés sans changer ce qu'une session affiche.

Une base de plusieurs mois, écrite à la main : une session close il y a quatre
mois — une coupure, une micro-coupure que seul un `uptime` qui recule trahit,
un lien absent du relevé pendant cinq minutes, un débit jamais mesuré, un pic
de latence, l'heure du routeur —, des relevés anciens hors de toute session, et
une session d'avant-hier qui ne doit pas bouger.
"""

from __future__ import annotations

import asyncio
import sqlite3
from datetime import datetime, timedelta, timezone

import httpx
import pytest
from fastapi import FastAPI

from app.api import routes
from app.fleet import RouterConfig, WanConfig
from app.retention import Retention
from app.storage import db as stockage
from app.storage.db import SampleRepository

UTC = timezone.utc
MAINTENANT = datetime(2026, 10, 24, 12, 0, 0, tzinfo=UTC)
VIEILLE = datetime(2026, 6, 20, 8, 0, 3, 250000, tzinfo=UTC)
HORS_SESSION = datetime(2026, 7, 10, 9, 0, 1, tzinfo=UTC)
RECENTE = MAINTENANT - timedelta(days=2)
N_VIEILLE = 1000

_COLONNES = (
    "router_id", "wan_id", "ts", "connected", "kind", "operator", "plmn", "rat",
    "cell_id", "enb_id", "earfcn", "rsrp", "sinr", "tx_kbps", "rx_kbps", "tunnel_state",
    "rtt_ms", "cutoff_ms", "loss_percent", "loss_percent_up", "loss_percent_down",
    "packets_up", "packets_down", "path_latency_ms", "uptime_s", "status_message",
    "site_lat", "site_lon",
)


def _releve(wan: str, t: datetime, i: int) -> tuple:
    """Un relevé fictif. Le lien 1 tombe trente secondes à i=300, se rétablit
    sans être vu tomber à i=500 (micro-coupure) et culmine à 1 500 ms à i=700.
    Le lien 2 ne publie aucun débit montant."""
    if wan == "1":
        coupe = 300 <= i < 306
        uptime = 1000 + 5 * i if i < 306 else (5 * (i - 306) if i < 500 else 2 + 5 * (i - 500))
        rtt = 1500.0 if i == 700 else 40.0 + i % 13
        return (
            "r1", "1", t.isoformat(), 0 if coupe else 1, "cellular", "Orange", "208-01", "LTE",
            1000 + (i // 200), 3, 1300, -90.0 - i % 5, 12.0 + i % 3,
            None if coupe else 1000.0 + (i % 7) * 10, None if coupe else 5000.0,
            "ACTIVE", None if coupe else rtt, 200.0, 0.5, 0.5, 0.2, 500, 900,
            25.0 + i % 3 if i % 12 == 0 else None, None if coupe else uptime,
            "No Cable Detected" if coupe else "Connected to Orange", 10.0, 20.0,
        )
    return (
        "r1", "2", t.isoformat(), 1, "cellular", "SFR", "208-10", "LTE",
        2000, 7, 6300, -100.0 - i % 3, 5.0, None, 800.0,
        "ACTIVE", 60.0 + i % 5, 200.0, None, None, None, None, None,
        None, 3000 + 5 * i, "Connected to SFR", 10.0, 20.0,
    )


async def _ecrire(repo: SampleRepository, lignes: list[tuple]) -> None:
    await repo._conn.executemany(
        f"INSERT INTO samples ({', '.join(_COLONNES)}) VALUES ({', '.join('?' * len(_COLONNES))})",
        lignes,
    )
    await repo._conn.commit()


async def _peupler(repo: SampleRepository) -> dict[str, int]:
    """La base de plusieurs mois. Rend les identifiants des deux sessions."""
    # La vieille session : allumée cinq minutes avant le premier relevé.
    vieille = await repo.open_session("r1", VIEILLE - timedelta(minutes=5))
    lignes = []
    for i in range(N_VIEILLE):
        t = VIEILLE + timedelta(seconds=5 * i)
        lignes.append(_releve("1", t, i))
        # Le lien 2 est ABSENT du relevé pendant cinq minutes.
        if not 100 <= i < 160:
            lignes.append(_releve("2", t + timedelta(milliseconds=40), i))
    await _ecrire(repo, lignes)
    fin_vieille = VIEILLE + timedelta(seconds=5 * (N_VIEILLE - 1), milliseconds=40)
    await repo.touch_session(vieille, fin_vieille, len(lignes))
    # L'heure du routeur : la latence du lien 1 toutes les 10 s sur la
    # première demi-heure, et la radio du lien 2 dans son trou.
    serie = {"1": [(VIEILLE.replace(microsecond=0) + timedelta(seconds=10 * k), 30.0 + k % 4)
                   for k in range(180)]}
    await repo.insert_latency_series("r1", serie)
    trou = VIEILLE.replace(microsecond=0) + timedelta(seconds=5 * 110)
    await repo.insert_quality_series("r1", {"2": [
        (trou + timedelta(seconds=10 * k), {"rsrp": -111.0, "rsrq": -12.0, "sinr": 1.0, "rat": "LTE"})
        for k in range(10)]})
    # Des relevés anciens hors de toute session (d'avant le suivi des sessions).
    await _ecrire(repo, [_releve("1", HORS_SESSION + timedelta(seconds=5 * i), i) for i in range(200)])
    # La session d'avant-hier.
    recente = await repo.open_session("r1", RECENTE - timedelta(minutes=1))
    await _ecrire(repo, [_releve("1", RECENTE + timedelta(seconds=5 * i), i) for i in range(100)])
    await repo.touch_session(recente, RECENTE + timedelta(seconds=495), 100)
    return {"vieille": vieille, "recente": recente}


class _Poller:
    def get_router(self, router_id: str):
        if router_id != "r1":
            return None
        return RouterConfig(id="r1", name="R1", mode="mock",
                            wans=[WanConfig(id="1", name="A"), WanConfig(id="2", name="B")])

    def period_for(self, router_id: str):
        return 5.0

    latest: dict = {}


@pytest.fixture
async def base(tmp_path):
    repo = SampleRepository(str(tmp_path / "samples.db"))
    await repo.connect()
    ids = await _peupler(repo)
    app = FastAPI()
    app.include_router(routes.router)
    app.state.repo = repo
    app.state.poller = _Poller()
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        client.repo = repo
        client.ids = ids
        client.chemin = tmp_path / "samples.db"
        yield client
    await repo.close()


def _retention(repo: SampleRepository, jours: int = 30) -> Retention:
    return Retention(repo, jours, routes._SEUIL_EPISODE_MS, pause_s=0)


async def _compte(repo: SampleRepository, sql: str, *params) -> int:
    async with repo._conn.execute(sql, params) as cursor:
        return int((await cursor.fetchone())[0] or 0)


async def _ecran(client, sid: int) -> dict:
    """Ce que l'écran et le rapport relisent d'une session passée."""
    fiche = (await client.get(f"/api/sessions/{sid}/summary")).json()
    s = fiche["session"]
    journal = (await client.get(
        "/api/routers/r1/events", params={"since": s["first_seen"], "until": s["last_seen"]})).json()
    rapport = (await client.get(
        "/api/routers/r1/events", params={"since": s["started_at"], "until": s["last_seen"]})).json()
    return {"fiche": fiche, "journal": journal, "rapport": rapport}


async def test_une_session_close_garde_exactement_sa_fiche_et_son_journal(base):
    sid = base.ids["vieille"]
    avant = await _ecran(base, sid)
    lien1 = next(lien for lien in avant["fiche"]["links"] if lien["wan_id"] == "1")
    # La base porte bien ce qu'un regroupement naïf détruirait.
    assert lien1["outages"] == 2  # une coupure, une micro-coupure
    assert lien1["mb_up"] is not None
    genres = {e["kind"] for e in avant["journal"]["episodes"]}
    assert {"coupure", "retour", "micro-coupure"} <= genres

    bilan = await _retention(base.repo).passe(MAINTENANT)
    assert bilan["sessions_figees"] == 1 and bilan["lots"] > 0
    # Les relevés bruts de la session ont disparu…
    s = avant["fiche"]["session"]
    assert await _compte(base.repo, "SELECT COUNT(*) FROM samples WHERE ts <= ?", s["last_seen"]) == 0
    # … et la session se relit à l'identique, fiche, journal et rapport.
    apres = await _ecran(base, sid)
    assert apres == avant
    assert apres["journal"]["grouped_before"] is None


async def test_une_session_renommee_apres_coup_garde_sa_fiche_sous_son_nouveau_nom(base):
    sid = base.ids["vieille"]
    await _retention(base.repo).passe(MAINTENANT)
    await base.post(f"/api/sessions/{sid}/label", json={"label": "parking"})
    fiche = (await base.get(f"/api/sessions/{sid}/summary")).json()
    assert fiche["session"]["label"] == "parking"
    assert fiche["links"]


async def test_une_courbe_ancienne_reste_lisible_a_la_minute(base):
    s = (await base.get(f"/api/sessions/{base.ids['vieille']}/summary")).json()["session"]
    fenetre = {"since": s["first_seen"], "until": s["last_seen"], "wan": "1"}

    async def courbe(metric: str, wan: str = "1") -> dict:
        return (await base.get("/api/routers/r1/history",
                               params={**fenetre, "metric": metric, "wan": wan})).json()

    rtt_avant = await courbe("rtt_ms")
    await _retention(base.repo).passe(MAINTENANT)
    rtt = await courbe("rtt_ms")
    assert rtt["grouped_before"] is not None
    assert {p["source"] for p in rtt["points"]} == {"minute"}
    valeurs = [p["value"] for p in rtt["points"] if p["value"] is not None]
    avant = [p["value"] for p in rtt_avant["points"] if p["value"] is not None]
    # Le pic isolé et le plancher survivent : la minute garde ses extrêmes.
    assert max(valeurs) == max(avant) == 1500.0
    assert min(valeurs) == min(avant)
    # La coupure de trente secondes reste un trou, pas un zéro.
    assert 0.0 not in valeurs

    # Un débit jamais mesuré reste sans mesure : aucun zéro fabriqué.
    tx2 = await courbe("tx_kbps", wan="2")
    assert tx2["points"] and all(p["value"] is None for p in tx2["points"])
    # Le lien 2 absent cinq minutes : aucune valeur au cœur du trou. Une
    # minute n'y existe que par l'heure du routeur, et sans débit.
    trou_de = datetime.fromisoformat(s["first_seen"]) + timedelta(seconds=5 * 101)
    trou_a = trou_de + timedelta(seconds=5 * 57)
    rx2 = await courbe("rx_kbps", wan="2")
    dans_le_trou = [p for p in rx2["points"]
                    if trou_de + timedelta(minutes=1) <= datetime.fromisoformat(p["ts"]) < trou_a
                    - timedelta(minutes=1)]
    assert all(p["value"] is None for p in dans_le_trou)
    # La radio du routeur dans ce trou, elle, est restée.
    rsrp2 = await courbe("rsrp", wan="2")
    assert -111.0 in [p["value"] for p in rsrp2["points"]]
    # La latence du lien vient toujours de la série du routeur.
    chemin = await courbe("path_latency_ms")
    assert {30.0, 33.0} <= {p["value"] for p in chemin["points"]}


async def test_la_bande_ancienne_garde_ses_comptes(base):
    s = (await base.get(f"/api/sessions/{base.ids['vieille']}/summary")).json()["session"]
    fenetre = {"since": s["first_seen"], "until": s["last_seen"]}

    def totaux(bande: dict) -> dict:
        return {lane["wan_id"]: (sum(t["samples"] for t in lane["slices"]),
                                 sum(t["up"] for t in lane["slices"]),
                                 sum(t["carrying"] for t in lane["slices"]))
                for lane in bande["lanes"]}

    avant = (await base.get("/api/routers/r1/timeline", params=fenetre)).json()
    await _retention(base.repo).passe(MAINTENANT)
    apres = (await base.get("/api/routers/r1/timeline", params=fenetre)).json()
    assert totaux(apres) == totaux(avant)
    assert apres["grouped_before"] is not None
    assert apres["slice_seconds"] >= 120.0


async def test_rien_de_recent_n_est_touche(base):
    sid = base.ids["recente"]
    avant = await _ecran(base, sid)
    bruts = await _compte(base.repo, "SELECT COUNT(*) FROM samples WHERE ts >= ?",
                          (RECENTE - timedelta(minutes=1)).isoformat())
    await _retention(base.repo).passe(MAINTENANT)
    assert await _ecran(base, sid) == avant
    assert await _compte(base.repo, "SELECT COUNT(*) FROM samples WHERE ts >= ?",
                         (RECENTE - timedelta(minutes=1)).isoformat()) == bruts
    # Rien n'est regroupé à moins de trente jours, ni même trente et un.
    limite = (MAINTENANT - timedelta(days=31)).isoformat()
    assert await _compte(base.repo, "SELECT COUNT(*) FROM samples_minute WHERE ts >= ?", limite) == 0


async def test_une_fenetre_hors_session_dit_qu_elle_est_regroupee(base):
    await _retention(base.repo).passe(MAINTENANT)
    params = {"since": HORS_SESSION.isoformat(),
              "until": (HORS_SESSION + timedelta(minutes=20)).isoformat()}
    journal = (await base.get("/api/routers/r1/events", params=params)).json()
    # Les relevés ne sont plus là pour dater une coupure : c'est DIT.
    assert journal["grouped_before"] is not None
    courbe = (await base.get("/api/routers/r1/history",
                             params={**params, "wan": "1", "metric": "rx_kbps"})).json()
    assert courbe["points"] and courbe["available"] > 0


async def test_l_export_d_une_session_regroupee_dit_ce_qu_il_porte(base):
    sid = base.ids["vieille"]
    await _retention(base.repo).passe(MAINTENANT)
    texte = (await base.get(f"/api/sessions/{sid}/export.csv")).text
    lignes = texte.strip().splitlines()
    entete = lignes[0].lstrip("﻿").split(";")
    corps = [dict(zip(entete, ligne.split(";"))) for ligne in lignes[1:]]
    assert corps and all("regroupé à la minute" in c["message"] for c in corps)
    # Le débit montant du lien 2, jamais mesuré, reste une cellule vide.
    assert {c["montant_kbps"] for c in corps if c["wan"] == "2"} == {""}


async def _etat(repo: SampleRepository) -> tuple:
    async with repo._conn.execute(
        "SELECT * FROM samples_minute ORDER BY router_id, wan_id, ts") as cursor:
        minutes = [tuple(r) for r in await cursor.fetchall()]
    async with repo._conn.execute("SELECT id FROM samples ORDER BY id") as cursor:
        bruts = [r[0] for r in await cursor.fetchall()]
    return minutes, bruts, await repo.regroupe_jusqu_a("r1")


async def test_un_arret_au_milieu_ne_laisse_rien_d_incoherent(base, tmp_path, monkeypatch):
    # La même base, regroupée d'une traite, sert de témoin.
    temoin = SampleRepository(str(tmp_path / "temoin.db"))
    await temoin.connect()
    await _peupler(temoin)
    await _retention(temoin).passe(MAINTENANT)
    attendu = await _etat(temoin)
    await temoin.close()

    total = await _compte(base.repo, "SELECT COUNT(*) FROM samples WHERE ts < ?",
                          (MAINTENANT - timedelta(days=31)).isoformat())
    d_origine = SampleRepository._minutes_du_lot
    appels = {"n": 0}

    async def interrompu(self, *args):
        appels["n"] += 1
        lignes = await d_origine(self, *args)
        if appels["n"] == 3:
            # Les minutes sont calculées, rien n'est encore validé.
            raise RuntimeError("arrêt brutal")
        return lignes

    monkeypatch.setattr(SampleRepository, "_minutes_du_lot", interrompu)
    with pytest.raises(RuntimeError):
        await _retention(base.repo).passe(MAINTENANT)
    # Deux lots validés, le troisième annulé en entier : chaque relevé ancien
    # est soit regroupé (avant la borne), soit encore brut — jamais les deux,
    # jamais aucun.
    borne = await base.repo.regroupe_jusqu_a("r1")
    regroupes = await _compte(base.repo, "SELECT SUM(releves) FROM samples_minute")
    restants = await _compte(base.repo, "SELECT COUNT(*) FROM samples WHERE ts >= ? AND ts < ?",
                             borne, (MAINTENANT - timedelta(days=31)).isoformat())
    assert regroupes + restants == total
    monkeypatch.setattr(SampleRepository, "_minutes_du_lot", d_origine)
    await _retention(base.repo).passe(MAINTENANT)
    assert await _etat(base.repo) == attendu


async def test_l_arret_du_serveur_au_milieu_d_un_lot_l_annule_en_entier(base, monkeypatch):
    """L'arrêt annule la tâche de fond : le lot en vol ne laisse rien."""
    repo = base.repo
    limite = MAINTENANT - timedelta(days=30)
    retention = _retention(repo)
    for s in await repo.sessions_a_figer(limite):
        await retention.figer(s)
    avant = await _etat(repo)
    d_origine = SampleRepository._minutes_du_lot
    en_vol = asyncio.Event()

    async def suspendu(self, *args):
        lignes = await d_origine(self, *args)
        # Le premier relevé des sessions est déjà retenu, les minutes calculées.
        en_vol.set()
        await asyncio.Event().wait()
        return lignes

    monkeypatch.setattr(SampleRepository, "_minutes_du_lot", suspendu)
    lot = asyncio.create_task(repo.regrouper_lot("r1", limite))
    await en_vol.wait()
    lot.cancel()
    with pytest.raises(asyncio.CancelledError):
        await lot
    assert await _etat(repo) == avant
    async with repo._conn.execute("SELECT COUNT(*) FROM sessions WHERE first_seen IS NOT NULL") as c:
        figes = (await c.fetchone())[0]
    # Seule la session figée a retenu son premier relevé, par `figer_session`.
    assert figes == 1


async def test_une_seconde_passe_n_a_plus_rien_a_faire(base):
    await _retention(base.repo).passe(MAINTENANT)
    bilan = await _retention(base.repo).passe(MAINTENANT)
    assert bilan["sessions_figees"] == 0 and bilan["lots"] == 0


async def test_zero_jour_desactive_la_retention(base):
    avant = await _etat(base.repo)
    assert await _retention(base.repo, jours=0).passe(MAINTENANT) == {}
    assert await _etat(base.repo) == avant


def test_la_duree_de_conservation_se_regle_par_l_environnement(monkeypatch):
    from pydantic import ValidationError

    from app.config import Settings

    monkeypatch.delenv("RETENTION_JOURS", raising=False)
    assert Settings().retention_jours == 30
    monkeypatch.setenv("RETENTION_JOURS", "90")
    assert Settings().retention_jours == 90
    monkeypatch.setenv("RETENTION_JOURS", "-1")
    with pytest.raises(ValidationError):
        Settings()


async def test_la_premiere_purge_est_precedee_d_une_copie(base):
    await _retention(base.repo).passe(MAINTENANT)
    copie = base.chemin.with_name("samples.db.avant-retention.bak")
    assert copie.exists()
    lu = sqlite3.connect(copie)
    try:
        # La copie a TOUT : les relevés que la purge a retirés depuis.
        assert lu.execute("SELECT COUNT(*) FROM samples WHERE ts < ?",
                          ((MAINTENANT - timedelta(days=31)).isoformat(),)).fetchone()[0] > 0
    finally:
        lu.close()


async def test_une_copie_ratee_empeche_la_purge_et_ne_reste_pas(base, monkeypatch):
    copie = base.chemin.with_name("samples.db.avant-retention.bak")

    def disque_plein(source, cible):
        raise OSError("disque plein")

    monkeypatch.setattr(stockage, "_copier_base", disque_plein)
    avant = await _compte(base.repo, "SELECT COUNT(*) FROM samples")
    with pytest.raises(OSError):
        await _retention(base.repo).passe(MAINTENANT)
    assert not copie.exists()
    assert await _compte(base.repo, "SELECT COUNT(*) FROM samples") == avant
    assert not await base.repo.retention_commencee()


def test_la_copie_entamee_est_effacee(tmp_path, monkeypatch):
    source = tmp_path / "s.db"
    sqlite3.connect(source).close()
    cible = tmp_path / "s.db.avant-retention.bak"

    class _Origine:
        def backup(self, copie):
            raise OSError("disque plein")

        def close(self):
            pass

    connecter = sqlite3.connect
    monkeypatch.setattr(stockage.sqlite3, "connect",
                        lambda chemin, *a, **k: _Origine() if chemin == source else connecter(chemin))
    with pytest.raises(OSError):
        stockage._copier_base(source, cible)
    assert not cible.exists()


async def _base_ancienne(chemin, *, existante: bool) -> SampleRepository:
    if existante:
        # Une base créée AVANT la rétention : sans vidage incrémental.
        sqlite3.connect(chemin).execute("CREATE TABLE ancienne (x)").connection.close()
    repo = SampleRepository(str(chemin))
    await repo.connect()
    debut = MAINTENANT - timedelta(days=120)
    lignes = [_releve("1", debut + timedelta(seconds=5 * i), i) for i in range(20_000)]
    await _ecrire(repo, lignes)
    async with repo._conn.execute("PRAGMA wal_checkpoint(TRUNCATE)") as c:
        await c.fetchall()
    return repo


async def test_le_fichier_neuf_retrecit_apres_compactage(tmp_path):
    chemin = tmp_path / "samples.db"
    repo = await _base_ancienne(chemin, existante=False)
    try:
        taille = chemin.stat().st_size
        bilan = await _retention(repo).passe(MAINTENANT)
        assert bilan["pages_rendues"] > 0
        assert chemin.stat().st_size < taille / 2
        async with repo._conn.execute("PRAGMA auto_vacuum") as c:
            assert (await c.fetchone())[0] == 2
        # La courbe se relit après le compactage.
        points, _ = await repo.history("r1", "1", "rtt_ms", MAINTENANT - timedelta(days=121), MAINTENANT)
        assert points
    finally:
        await repo.close()


async def _auto_vacuum(repo: SampleRepository) -> int:
    async with repo._conn.execute("PRAGMA auto_vacuum") as c:
        return (await c.fetchone())[0]


async def test_une_base_existante_n_est_jamais_convertie_en_marche(tmp_path):
    """Le VACUUM complet de conversion réécrit toute la base sous le verrou
    d'écriture : en marche, il gelait la scrutation — toute la flotte passait
    « injoignable ». En marche, la passe ne fait QUE du vidage incrémental."""
    chemin = tmp_path / "samples.db"
    repo = await _base_ancienne(chemin, existante=True)
    try:
        bilan = await _retention(repo).passe(MAINTENANT)
        assert bilan["lots"] > 0
        assert bilan["pages_rendues"] == 0
        assert await _auto_vacuum(repo) == 0
    finally:
        await repo.close()


async def test_la_conversion_se_fait_au_demarrage_suivant(tmp_path, caplog):
    chemin = tmp_path / "samples.db"
    repo = await _base_ancienne(chemin, existante=True)
    await _retention(repo).passe(MAINTENANT)
    await repo.close()
    taille = chemin.stat().st_size
    repo = SampleRepository(str(chemin))
    await repo.connect()
    try:
        with caplog.at_level("WARNING"):
            assert await repo.convertir_au_demarrage() is True
        assert "conversion unique" in caplog.text.lower()
        assert await _auto_vacuum(repo) == 2
        assert chemin.stat().st_size < taille / 2
        # Une fois : le lancement suivant n'a plus rien à convertir.
        assert await repo.convertir_au_demarrage() is False
        points, _ = await repo.history("r1", "1", "rtt_ms", MAINTENANT - timedelta(days=121), MAINTENANT)
        assert points
    finally:
        await repo.close()


async def test_sans_place_sur_le_disque_la_conversion_est_differee(tmp_path, monkeypatch, caplog):
    """VACUUM en WAL : le journal grossit d'environ la taille de la base, la
    copie temporaire autant. Sans 2,2 fois la base de libre, la conversion
    échouait sur disque plein ; elle est différée au lancement suivant."""
    import collections

    chemin = tmp_path / "samples.db"
    repo = await _base_ancienne(chemin, existante=True)
    await _retention(repo).passe(MAINTENANT)
    await repo.close()
    taille = chemin.stat().st_size
    Usage = collections.namedtuple("Usage", "total used free")
    monkeypatch.setattr(stockage.shutil, "disk_usage",
                        lambda _chemin: Usage(10 * taille, 9 * taille, 2 * taille))
    repo = SampleRepository(str(chemin))
    await repo.connect()
    try:
        with caplog.at_level("WARNING"):
            assert await repo.convertir_au_demarrage() is False
        assert "différée" in caplog.text
        assert await _auto_vacuum(repo) == 0
        assert chemin.stat().st_size == taille
    finally:
        await repo.close()


async def test_une_base_neuve_ou_peu_liberee_n_est_pas_convertie(tmp_path):
    chemin = tmp_path / "samples.db"
    repo = await _base_ancienne(chemin, existante=True)
    try:
        # Rien de purgé : réécrire toute la base ne rendrait rien.
        assert await repo.convertir_au_demarrage() is False
        assert await _auto_vacuum(repo) == 0
    finally:
        await repo.close()


async def test_une_fiche_qui_echoue_n_arrete_pas_la_passe(base, monkeypatch, caplog):
    """Une session dont la fiche ne se calcule pas arrêtait TOUTE la passe,
    avant le regroupement, et cela chaque heure. Les autres continuent ; elle
    n'est jamais purgée tant qu'elle n'est pas figée, et son échec se voit."""
    from app import retention as module

    repo = base.repo
    # Un second équipement, une vieille session close, qui se fige sans peine.
    autre = await repo.open_session("r2", VIEILLE - timedelta(minutes=1))
    await _ecrire(repo, [("r2", *_releve("1", VIEILLE + timedelta(seconds=5 * i), i)[1:])
                         for i in range(50)])
    await repo.touch_session(autre, VIEILLE + timedelta(seconds=245), 50)
    vieille = base.ids["vieille"]
    bruts_r1 = await _compte(repo, "SELECT COUNT(*) FROM samples WHERE router_id = 'r1'")
    calculer = module.calculer_fiche

    async def capricieuse(depot, lignes):
        if lignes["id"] == vieille:
            raise ValueError("fiche incalculable")
        return await calculer(depot, lignes)

    monkeypatch.setattr(module, "calculer_fiche", capricieuse)
    retention = _retention(repo)
    with caplog.at_level("WARNING"):
        bilan = await retention.passe(MAINTENANT)
        await retention.passe(MAINTENANT)
    assert bilan["sessions_figees"] == 1 and bilan["lots"] > 0
    assert await _compte(repo, "SELECT COUNT(*) FROM samples WHERE router_id = 'r2'") == 0
    # La session en échec n'a rien perdu de ses relevés bruts.
    assert await _compte(repo, "SELECT COUNT(*) FROM samples WHERE router_id = 'r1'") == bruts_r1
    assert retention.echecs == {vieille: "ValueError: fiche incalculable"}
    # Dit une fois, pas à chaque passe.
    assert caplog.text.count("fiche incalculable") == 1
    # Réparée, elle se fige et sort des échecs.
    monkeypatch.setattr(module, "calculer_fiche", calculer)
    await retention.passe(MAINTENANT)
    assert retention.echecs == {}
