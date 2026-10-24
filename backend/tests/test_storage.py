"""Stockage des relevés : écriture, historique et migration de schéma."""

import sqlite3

import asyncio

import pytest

from app.models import RadioSample, TunnelLink, WanRadioStatus, WifiLink
from app.storage.db import SampleRepository

# Schéma de la première version : sans les colonnes Wi-Fi, et AVEC les sept
# colonnes `ant_*` de l'identification d'antenne, retirée depuis. Une base réelle
# les porte encore — on ne les supprime pas, elles contiennent de l'historique et
# un DROP COLUMN serait irréversible. Ce schéma sert donc aussi à vérifier que
# leur présence ne gêne en rien les écritures d'aujourd'hui.
_LEGACY_SCHEMA = """
CREATE TABLE samples (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    router_id TEXT NOT NULL, wan_id TEXT NOT NULL, ts TEXT NOT NULL,
    connected INTEGER NOT NULL DEFAULT 1,
    operator TEXT, mcc INTEGER, mnc INTEGER, rat TEXT, band TEXT, earfcn INTEGER,
    pci INTEGER, cell_id INTEGER, tac INTEGER,
    rsrp REAL, rsrq REAL, sinr REAL, rssi REAL, signal_bars INTEGER,
    tx_kbps REAL, rx_kbps REAL,
    ant_lat REAL, ant_lon REAL, ant_distance_m REAL, ant_bearing_deg REAL,
    ant_source TEXT, ant_confidence TEXT, ant_site_name TEXT
);
"""


def _cellular_sample() -> RadioSample:
    return RadioSample(
        router_id="r1",
        router_name="Routeur 1",
        wan=WanRadioStatus(
            wan_id="2", wan_name="SIM", operator="Orange",
            rsrp=-95.0, sinr=12.0, cell_id=123456,
        ),
    )


def _wifi_sample() -> RadioSample:
    return RadioSample(
        router_id="r1",
        router_name="Routeur 1",
        wan=WanRadioStatus(
            kind="wifi", wan_id="5", wan_name="Wi-Fi WAN", operator="STARLINK",
            rat="Wi-Fi", rssi=-62.0, signal_bars=4,
            wifi=WifiLink(ssid="STARLINK", bssid="aa:bb:cc:dd:ee:ff",
                          channel=112, band="5 GHz", signal_dbm=-62.0, signal_level=4),
        ),
    )


@pytest.fixture
async def repo(tmp_path):
    r = SampleRepository(str(tmp_path / "samples.db"))
    await r.connect()
    yield r
    await r.close()


async def test_insert_and_count(repo):
    await repo.insert(_cellular_sample())
    assert await repo.count() == 1


async def test_history_returns_stored_metric(repo):
    await repo.insert(_cellular_sample())
    points, _ = await repo.history("r1", "2", "sinr")
    assert [p.value for p in points] == [12.0]


async def test_history_rejects_unknown_metric(repo):
    with pytest.raises(ValueError):
        await repo.history("r1", "2", "'; DROP TABLE samples; --")


async def test_wifi_fields_are_persisted(repo, tmp_path):
    await repo.insert(_wifi_sample())
    points, _ = await repo.history("r1", "5", "rssi")
    assert [p.value for p in points] == [-62.0]

    # Vérification directe en base, indépendante du repository.
    conn = sqlite3.connect(str(tmp_path / "samples.db"))
    row = conn.execute("SELECT kind, ssid, bssid, wifi_channel FROM samples").fetchone()
    conn.close()
    assert row == ("wifi", "STARLINK", "aa:bb:cc:dd:ee:ff", 112)


async def test_legacy_database_is_migrated(tmp_path):
    """Une base créée par la version précédente doit recevoir les colonnes Wi-Fi."""
    path = tmp_path / "legacy.db"
    conn = sqlite3.connect(str(path))
    conn.executescript(_LEGACY_SCHEMA)
    conn.execute(
        "INSERT INTO samples (router_id, wan_id, ts, sinr) VALUES ('r1','2','2026-01-01T00:00:00+00:00', 9.5)"
    )
    conn.commit()
    conn.close()

    repo = SampleRepository(str(path))
    await repo.connect()
    try:
        # La donnée historique survit…
        assert await repo.count() == 1
        # …et les nouveaux relevés Wi-Fi passent.
        await repo.insert(_wifi_sample())
        assert await repo.count() == 2
        points, _ = await repo.history("r1", "5", "rssi")
        assert [p.value for p in points] == [-62.0]
    finally:
        await repo.close()

    conn = sqlite3.connect(str(path))
    columns = {row[1] for row in conn.execute("PRAGMA table_info(samples)")}
    conn.close()
    assert {"kind", "ssid", "bssid", "wifi_channel"} <= columns


# --- dénominateur de la perte ----------------------------------------------------
#
# Un taux sans son dénominateur ne se relit pas. Sur un tunnel au repos, seul le
# keepalive passe entre deux relevés : « 0,00 % de perte » y est calculé sur trois
# paquets de service. Écrit tel quel en base, ce point pèse dans l'historique
# autant qu'une mesure faite sur cent mille paquets d'image.

def _idle_tunnel_sample() -> RadioSample:
    """Relevé d'un tunnel au repos : trois paquets montants, aucun descendant."""
    return RadioSample(
        router_id="r1", router_name="Routeur 1",
        wan=WanRadioStatus(
            wan_id="4", wan_name="SIM",
            tunnel=TunnelLink(
                state="ACTIVE", rtt_ms=28.0,
                loss_percent=0.0, loss_percent_up=0.0,
                packets_up=3, packets_down=0,
            ),
        ),
    )


async def test_the_loss_denominator_is_persisted(repo, tmp_path):
    await repo.insert(_idle_tunnel_sample())
    conn = sqlite3.connect(str(tmp_path / "samples.db"))
    row = conn.execute(
        "SELECT loss_percent, packets_up, packets_down FROM samples"
    ).fetchone()
    conn.close()
    # Zéro pour cent, oui — mais sur trois paquets. Les deux dernières colonnes
    # sont ce qui permet, un mois plus tard, de savoir que cette ligne ne mesure
    # rien. Un dénominateur de zéro reste une mesure, distincte de NULL.
    assert row == (0.0, 3, 0)


async def test_the_denominator_is_available_in_history(repo):
    """La règle des 200 paquets vaut aussi sur un graphe.

    Sans dénominateur en historique, une courbe de perte à plat est invérifiable :
    rien n'y distingue un tunnel sain d'un tunnel au repos dont chaque point est
    calculé sur trois paquets de keepalive.
    """
    await repo.insert(_idle_tunnel_sample())
    assert [p.value for p in (await repo.history("r1", "4", "packets_up"))[0]] == [3.0]


async def test_legacy_database_gains_the_loss_denominator(tmp_path):
    """Migration additive : une base en service acquiert les deux colonnes.

    Ses lignes antérieures les gardent à NULL, ce qui est exact — leur
    dénominateur n'a pas été conservé et ne peut plus l'être.
    """
    path = tmp_path / "legacy.db"
    conn = sqlite3.connect(str(path))
    conn.executescript(_LEGACY_SCHEMA)
    conn.execute("INSERT INTO samples (router_id, wan_id, ts, sinr) "
                 "VALUES ('r1','2','2026-01-01T00:00:00+00:00', 9.5)")
    conn.commit()
    conn.close()

    repo = SampleRepository(str(path))
    await repo.connect()
    try:
        assert await repo.count() == 1      # la mesure antérieure survit
        await repo.insert(_idle_tunnel_sample())
    finally:
        await repo.close()

    conn = sqlite3.connect(str(path))
    columns = {row[1] for row in conn.execute("PRAGMA table_info(samples)")}
    rows = conn.execute("SELECT packets_up, packets_down FROM samples ORDER BY ts").fetchall()
    conn.close()
    assert {"packets_up", "packets_down"} <= columns
    assert rows[0] == (None, None)          # relevé d'avant : dénominateur inconnu
    assert rows[1] == (3, 0)                # relevé du jour : dénominateur mesuré


# --- tri naturel des WAN ------------------------------------------------------

from app.api.routes import _wan_order


def test_numeric_wan_ids_sort_numerically():
    """Un châssis MAX HD4 MBX expose 11 WAN : "10" ne doit pas passer avant "2"."""
    ids = ["10", "2", "1", "11", "9"]
    assert sorted(ids, key=_wan_order) == ["1", "2", "9", "10", "11"]


def test_named_wan_ids_keep_alphabetical_order():
    """Les identifiants du simulateur restent triés par texte."""
    ids = ["wifi-1", "cellular-2", "cellular-1"]
    assert sorted(ids, key=_wan_order) == ["cellular-1", "cellular-2", "wifi-1"]


def test_numeric_ids_come_before_named_ones():
    assert sorted(["wifi-1", "4"], key=_wan_order) == ["4", "wifi-1"]


# --- position du récepteur ------------------------------------------------------
#
# C'est le GPS de l'équipement lui-même, une mesure du routeur et non une donnée
# d'enrichissement : sans elle, un relevé dit « telle cellule, tel niveau » sans
# jamais dire d'où il a été fait. Le véhicule étant mobile en usage réel, chaque
# cycle non enregistré est perdu.

def _located_sample(lat: float | None, lon: float | None) -> RadioSample:
    return RadioSample(
        router_id="r1", router_name="Routeur 1", site_lat=lat, site_lon=lon,
        wan=WanRadioStatus(wan_id="4", wan_name="SIM", cell_id=25735433, rsrp=-88.0),
    )


async def test_receiver_position_is_persisted(repo, tmp_path):
    await repo.insert(_located_sample(48.856554, 2.352197))
    conn = sqlite3.connect(str(tmp_path / "samples.db"))
    row = conn.execute("SELECT site_lat, site_lon, cell_id, rsrp FROM samples").fetchone()
    conn.close()
    # Le triplet complet dont dépend toute estimation de position de cellule.
    assert row == (48.856554, 2.352197, 25735433, -88.0)


async def test_receiver_position_may_be_absent(repo, tmp_path):
    """Un équipement sans GPS ni coordonnées fixes reste enregistrable."""
    await repo.insert(_located_sample(None, None))
    conn = sqlite3.connect(str(tmp_path / "samples.db"))
    row = conn.execute("SELECT site_lat, site_lon FROM samples").fetchone()
    conn.close()
    assert row == (None, None)


async def test_a_new_database_has_no_antenna_columns(repo, tmp_path):
    """L'identification d'antenne a quitté le schéma : rien ne doit la rappeler.

    Elle ne pouvait pas fonctionner — aucun registre public ne lie une cellule à
    un site déclaré — et laisser ses colonnes vides donnerait à croire qu'un
    remplissage est encore attendu.
    """
    await repo.insert(_located_sample(48.8566, 2.3522))
    conn = sqlite3.connect(str(tmp_path / "samples.db"))
    columns = {row[1] for row in conn.execute("PRAGMA table_info(samples)")}
    conn.close()
    assert not [c for c in columns if c.startswith("ant_")]
    assert {"site_lat", "site_lon"} <= columns   # le GPS du routeur, lui, reste


async def test_legacy_database_gains_receiver_position(tmp_path):
    """Une base antérieure acquiert les colonnes sans perdre ses mesures."""
    path = tmp_path / "legacy.db"
    conn = sqlite3.connect(str(path))
    conn.executescript(_LEGACY_SCHEMA)
    conn.execute("INSERT INTO samples (router_id, wan_id, ts, sinr) "
                 "VALUES ('r1','2','2026-01-01T00:00:00+00:00', 9.5)")
    conn.commit()
    conn.close()

    repo = SampleRepository(str(path))
    await repo.connect()
    try:
        assert await repo.count() == 1  # la mesure antérieure survit
        await repo.insert(_located_sample(48.8566, 2.3522))
    finally:
        await repo.close()

    conn = sqlite3.connect(str(path))
    columns = {row[1] for row in conn.execute("PRAGMA table_info(samples)")}
    stored = conn.execute("SELECT site_lat FROM samples WHERE site_lat IS NOT NULL").fetchone()
    conn.close()
    assert {"site_lat", "site_lon"} <= columns
    assert stored[0] == 48.8566


# --- colonnes d'antenne d'une base antérieure ------------------------------------
#
# L'identification d'antenne est retirée, mais les bases déjà en service portent
# ses sept colonnes et trois semaines d'historique réel. Un DROP COLUMN serait
# irréversible : on les laisse en place, et l'INSERT — qui nomme ses colonnes —
# les ignore. Ce test tient cette promesse des deux côtés : rien n'est effacé,
# rien n'est cassé.

async def test_an_existing_database_keeps_its_antenna_columns_and_still_accepts_writes(tmp_path):
    path = tmp_path / "avec-antennes.db"
    conn = sqlite3.connect(str(path))
    conn.executescript(_LEGACY_SCHEMA)
    conn.execute(
        "INSERT INTO samples (router_id, wan_id, ts, rsrp, sinr, cell_id, ant_lat, "
        "ant_lon, ant_distance_m, ant_bearing_deg, ant_source, ant_confidence, "
        "ant_site_name) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
        ("r1", "2", "2026-01-01T00:00:00+00:00", -88.0, 14.0, 25735433,
         48.8588, 2.3541, 340.0, 51.0, "anfr", "medium", "ANFR 123456"),
    )
    conn.commit()
    conn.close()

    repo = SampleRepository(str(path))
    await repo.connect()
    try:
        # Une colonne surnuméraire n'empêche pas d'écrire : l'INSERT nomme les siennes.
        await repo.insert(_located_sample(48.8566, 2.3522))
        assert await repo.count() == 2
    finally:
        await repo.close()

    conn = sqlite3.connect(str(path))
    columns = {row[1] for row in conn.execute("PRAGMA table_info(samples)")}
    rows = conn.execute(
        "SELECT ant_source, ant_lat, ant_lon, ant_site_name, rsrp, sinr "
        "FROM samples ORDER BY ts"
    ).fetchall()
    conn.close()

    # Les colonnes sont intactes, et la ligne historique aussi : aucune donnée perdue.
    assert {"ant_lat", "ant_lon", "ant_source", "ant_site_name"} <= columns
    assert rows[0] == ("anfr", 48.8588, 2.3541, "ANFR 123456", -88.0, 14.0)
    # Le relevé du jour, lui, ne renseigne plus rien de tout cela.
    assert rows[1][:4] == (None, None, None, None)


# --- fenêtre d'historique ---------------------------------------------------

async def _remplir(repo, n, *, colonne="sinr", valeur=lambda i: float(i % 30),
                  debut="2026-09-01T08:00:00+00:00", pas=5):
    """Insère n relevés en UNE transaction.

    `repo.insert` commite à chaque ligne : cinq mille appels font cinq mille
    synchronisations disque et mettent le test à genoux. Ce raccourci n'existe
    que pour peupler une série ; l'écriture réelle est testée ailleurs.
    """
    from datetime import datetime, timedelta
    t0 = datetime.fromisoformat(debut)
    await repo._conn.executemany(
        f"INSERT INTO samples (router_id, wan_id, ts, connected, kind, {colonne}) "
        "VALUES ('r1', '4', ?, 1, 'cellular', ?)",
        [((t0 + timedelta(seconds=pas * i)).isoformat(), valeur(i)) for i in range(n)],
    )
    await repo._conn.commit()
    return t0


async def test_a_wide_window_no_longer_stops_hours_before_now(tmp_path):
    """Le piège armé : `LIMIT` rendait les points les plus ANCIENS de la fenêtre.

    Avec `ORDER BY ts ASC LIMIT 2000`, une fenêtre plus large que 2 000 points
    s'arrêtait silencieusement là, des heures avant sa fin — et l'absence de fin
    se lit comme « rien ne s'est passé ». À 5,3 s de période, le seuil tombe à
    moins de trois heures, soit moins qu'une seule session de stationnement.
    """
    from datetime import timedelta
    repo = SampleRepository(str(tmp_path / "s.db"))
    await repo.connect()
    t0 = await _remplir(repo, 5000)

    points, available = await repo.history(
        "r1", "4", "sinr", debut=t0 - timedelta(minutes=1), fin=t0 + timedelta(days=1)
    )
    assert available == 5000
    assert len(points) <= 2000, "la série n'a pas été allégée"
    # Ce qui compte : la courbe couvre TOUTE la fenêtre, jusqu'au dernier relevé.
    assert points[-1].ts == t0 + timedelta(seconds=5 * 4999)
    await repo.close()


async def test_the_envelope_keeps_the_spike_a_sampling_step_would_erase(tmp_path):
    """Un point sur N effacerait précisément ce qu'on cherche.

    Le relevé le plus instructif de la base est un pic isolé à 1 929 ms au milieu
    de liens à 30 ms. On garde donc le minimum ET le maximum de chaque tranche.
    """
    from datetime import timedelta
    repo = SampleRepository(str(tmp_path / "s.db"))
    await repo.connect()
    t0 = await _remplir(
        repo, 5000, colonne="rtt_ms", valeur=lambda i: 1929.0 if i == 2500 else 30.0
    )

    points, _ = await repo.history(
        "r1", "4", "rtt_ms", debut=t0 - timedelta(minutes=1), fin=t0 + timedelta(days=1)
    )
    valeurs = [p.value for p in points]
    assert 1929.0 in valeurs, "le pic a disparu de la courbe allégée"
    assert 30.0 in valeurs, "le niveau de fond a disparu"
    await repo.close()


async def test_an_absolute_window_reaches_a_past_session(tmp_path):
    """Sans `since`/`until`, un historique ne se relit que depuis maintenant.

    C'est ce qui rendait 83 % des relevés enregistrés inatteignables par la
    moindre route : la fenêtre ne savait que glisser depuis l'instant présent.
    """
    from datetime import timedelta
    repo = SampleRepository(str(tmp_path / "s.db"))
    await repo.connect()
    t0 = await _remplir(repo, 10, debut="2026-08-30T09:00:00+00:00", pas=60,
                        valeur=lambda i: float(i))

    # La fenêtre glissante ne voit rien : ces relevés datent de plusieurs jours.
    # Sans bornes, la fenêtre par défaut est la demi-heure écoulée.
    glissante, _ = await repo.history("r1", "4", "sinr")
    assert glissante == []

    absolue, available = await repo.history(
        "r1", "4", "sinr",
        debut=t0 - timedelta(minutes=1), fin=t0 + timedelta(minutes=5),
    )
    assert available == 5
    assert [p.value for p in absolue] == [0.0, 1.0, 2.0, 3.0, 4.0]
    await repo.close()


# --- sessions d'alimentation ------------------------------------------------

async def test_a_session_opens_and_is_prolonged(tmp_path):
    repo = SampleRepository(str(tmp_path / "s.db"))
    await repo.connect()
    from datetime import datetime, timedelta, timezone

    t0 = datetime(2026, 9, 1, 8, 0, tzinfo=timezone.utc)
    sid = await repo.open_session("r1", t0)
    await repo.touch_session(sid, t0 + timedelta(minutes=5), 5)
    await repo.touch_session(sid, t0 + timedelta(minutes=10), 5)

    courante = await repo.current_session("r1")
    assert courante["id"] == sid
    assert courante["samples"] == 10
    assert courante["last_seen"].startswith("2026-09-01T08:10")
    await repo.close()


async def test_a_session_says_when_the_supervisor_started_watching(tmp_path):
    """`first_seen` est le premier relevé, pas la mise sous tension.

    La session commence à l'allumage, calculé depuis l'`uptime` ; le
    superviseur, lui, a été lancé deux heures dix-huit plus tard sur le MAX HD2
    du 19 septembre. L'axe de la bande partait de l'allumage et donnait
    quarante pour cent de sa largeur à un vide qui n'est pas une mesure. La
    borne gauche de ce qui se dessine est le premier relevé rattaché — et
    `None` tant qu'il n'y en a pas, jamais l'allumage recopié.
    """
    from datetime import datetime, timedelta, timezone

    async with SampleRepository(str(tmp_path / "s.db")) as repo:
        allumage = datetime(2026, 9, 19, 8, 35, tzinfo=timezone.utc)
        sid = await repo.open_session("r1", allumage)
        assert (await repo.sessions("r1"))[0]["first_seen"] is None

        premier = allumage + timedelta(hours=2, minutes=18)
        await repo._conn.executemany(
            "INSERT INTO samples (router_id, wan_id, ts, connected, kind) "
            "VALUES ('r1', '4', ?, 1, 'cellular')",
            [((premier + timedelta(seconds=5 * i)).isoformat(),) for i in range(3)],
        )
        await repo._conn.commit()
        await repo.touch_session(sid, premier + timedelta(seconds=10), 3)

        assert (await repo.sessions("r1"))[0]["first_seen"] == premier.isoformat()
        assert (await repo.session_by_id(sid))["first_seen"] == premier.isoformat()


async def test_a_named_session_is_what_makes_two_stops_comparable(tmp_path):
    repo = SampleRepository(str(tmp_path / "s.db"))
    await repo.connect()
    from datetime import datetime, timezone

    sid = await repo.open_session("r1", datetime(2026, 9, 1, 8, 0, tzinfo=timezone.utc))
    assert await repo.name_session(sid, "Parking de la mairie") is True
    assert (await repo.current_session("r1"))["label"] == "Parking de la mairie"
    assert await repo.name_session(999999, "inexistante") is False
    await repo.close()


# --- ce qui remonte jusqu'à Python est borné --------------------------------

async def test_a_dense_window_never_materialises_in_memory(tmp_path):
    """`since`/`until` posent une fenêtre ABSOLUE, que rien ne plafonnait.

    `?since=1970-01-01T00:00:00Z` demandait donc tout l'historique du lien.
    Mesuré sur une base de trente jours : 507 240 lignes, 11,6 s et 374 Mo de
    pic pour rendre 99 Ko — l'unique connexion SQLite monopolisée pendant deux
    cycles entiers, et la boucle d'événements figée le temps de construire un
    demi-million de `HistoryPoint`. La route n'est pas restreinte à la boucle
    locale : elle est joignable depuis le LAN.
    """
    from datetime import datetime, timedelta
    from app.storage.db import _LIGNES_MEMOIRE_MAX

    repo = SampleRepository(str(tmp_path / "s.db"))
    await repo.connect()
    n = _LIGNES_MEMOIRE_MAX + 5_000
    t0 = await _remplir(repo, n, colonne="rtt_ms",
                        valeur=lambda i: 1929.0 if i == n // 2 else 30.0)

    points, available = await repo.history(
        "r1", "4", "rtt_ms",
        debut=datetime.fromisoformat("1970-01-01T00:00:00+00:00"),
        fin=t0 + timedelta(days=30),
    )

    assert available == n, "le nombre de points disponibles doit rester exact"
    assert len(points) <= 2000, "la série n'a pas été allégée"
    # Même enveloppe qu'en Python : le pic isolé survit, le fond aussi.
    valeurs = [p.value for p in points]
    assert 1929.0 in valeurs, "le pic a disparu de l'enveloppe calculée en SQL"
    assert 30.0 in valeurs, "le niveau de fond a disparu"
    # Et la courbe couvre toujours toute la fenêtre : à une tranche près — le
    # point retenu est celui de l'extrême, pas celui du bord —, elle va jusqu'au
    # dernier relevé. C'était tout l'objet du retrait du `LIMIT` : une fenêtre
    # qui s'arrête des heures avant sa fin se lit comme « rien ne s'est passé ».
    tranche = timedelta(seconds=5 * (n // 1000 + 1))
    assert points[0].ts - t0 <= tranche
    assert t0 + timedelta(seconds=5 * (n - 1)) - points[-1].ts <= tranche
    await repo.close()


async def test_a_dense_window_keeps_its_holes_visible(tmp_path):
    """Une tranche sans aucune mesure garde un point : le trou reste visible.

    C'est la règle de `_echantillonner`, et l'enveloppe calculée en SQL doit la
    tenir aussi — sans quoi un lien qui n'a rien produit se lirait comme un lien
    dont la courbe se referme par interpolation.
    """
    from datetime import timedelta
    from app.storage.db import _LIGNES_MEMOIRE_MAX

    repo = SampleRepository(str(tmp_path / "s.db"))
    await repo.connect()
    n = _LIGNES_MEMOIRE_MAX + 1_000
    # Le premier tiers n'a aucune mesure : le lien était absent du relevé.
    t0 = await _remplir(repo, n, colonne="sinr",
                        valeur=lambda i: None if i < n // 3 else 12.0)

    points, available = await repo.history(
        "r1", "4", "sinr", debut=t0 - timedelta(minutes=1), fin=t0 + timedelta(days=30)
    )
    assert available == n
    assert any(p.value is None for p in points), "le trou a été refermé"
    assert any(p.value == 12.0 for p in points)
    assert points == sorted(points, key=lambda p: p.ts), "l'enveloppe doit rester chronologique"
    await repo.close()


async def test_a_very_long_session_is_read_on_a_bounded_window(tmp_path):
    """Une session de trois semaines ne se relit pas en entier.

    Une session est ancrée sur l'`uptime` du châssis : superviser pour la
    première fois un équipement allumé depuis trois semaines en ouvre une de
    trois semaines, parfaitement légitime. La relire entière faisait 1 711 695
    lignes, 12,3 s et 1,5 Go — sur un PC de terrain, un gel puis un risque
    d'épuisement mémoire. On relit la fin, et on le DIT.
    """
    from datetime import datetime, timedelta
    from app.storage.db import _FENETRE_SESSION_S

    repo = SampleRepository(str(tmp_path / "s.db"))
    await repo.connect()
    # Trois jours de relevés à la minute : au-delà de la fenêtre de vingt-quatre heures.
    t0 = await _remplir(repo, 3 * 24 * 60, pas=60)
    fin = t0 + timedelta(seconds=60 * (3 * 24 * 60 - 1))

    lignes, fenetre = await repo.session_rows("r1", t0.isoformat(), fin.isoformat())
    assert fenetre is not None, "une lecture partielle doit se déclarer"
    debut_lu, fin_lue = fenetre
    assert debut_lu == fin - timedelta(seconds=_FENETRE_SESSION_S)
    assert fin_lue == fin
    assert lignes, "la fin de la session doit bien être lue"
    assert datetime.fromisoformat(lignes[0]["ts"]) >= debut_lu
    assert datetime.fromisoformat(lignes[-1]["ts"]) == fin
    await repo.close()


async def test_a_normal_session_is_read_whole_and_says_so(tmp_path):
    """Une session de stationnement tient dans la fenêtre : rien n'est tronqué."""
    from datetime import timedelta

    repo = SampleRepository(str(tmp_path / "s.db"))
    await repo.connect()
    t0 = await _remplir(repo, 4_000)          # ~5,5 h à 5 s de pas
    fin = t0 + timedelta(seconds=5 * 3_999)

    lignes, fenetre = await repo.session_rows("r1", t0.isoformat(), fin.isoformat())
    assert fenetre is None, "une session complète ne doit pas se déclarer partielle"
    assert len(lignes) == 4_000
    await repo.close()


# --- la bande de stationnement ----------------------------------------------

async def test_the_timeline_aggregates_in_sql_not_in_python(tmp_path):
    """Ramener les relevés bruts aurait été la faute déjà corrigée ailleurs.

    Une session de six heures sur cinq liens fait vingt mille lignes, et personne
    n'a besoin de les voir une par une pour lire une bande de deux cents pixels.
    """
    from datetime import datetime, timedelta
    from app.storage.db import _TRANCHES_BANDE

    repo = SampleRepository(str(tmp_path / "s.db"))
    await repo.connect()
    t0 = datetime.fromisoformat("2026-09-01T08:00:00+00:00")
    fin = t0 + timedelta(hours=6)
    # Deux liens, un relevé toutes les cinq secondes pendant six heures.
    await repo._conn.executemany(
        "INSERT INTO samples (router_id, wan_id, ts, connected, kind, rtt_ms, tunnel_state) "
        "VALUES ('r1', ?, ?, 1, 'cellular', ?, 'ACTIVE')",
        [
            (wan, (t0 + timedelta(seconds=5 * i)).isoformat(), 30.0)
            for wan in ("4", "5")
            for i in range(4320)
        ],
    )
    await repo._conn.commit()

    lignes, _, _ = await repo.timeline("r1", t0, fin)

    # Deux liens × au plus le nombre de tranches : la réponse ne grandit pas
    # avec la durée de la session.
    assert len(lignes) <= 2 * _TRANCHES_BANDE
    assert {l["wan_id"] for l in lignes} == {"4", "5"}
    assert all(0 <= l["tranche"] < _TRANCHES_BANDE for l in lignes)
    assert sum(l["releves"] for l in lignes) == 2 * 4320
    await repo.close()


async def test_a_timeline_slice_carries_the_WORST_of_what_it_contains(tmp_path):
    """Une bande qui lisse l'incident qu'on cherche ne sert à rien.

    Le relevé le plus instructif de la base est un pic isolé à 1 929 ms au milieu
    de liens à 30 ms : une moyenne sur cinq minutes l'effacerait.
    """
    from datetime import datetime, timedelta

    repo = SampleRepository(str(tmp_path / "s.db"))
    await repo.connect()
    t0 = datetime.fromisoformat("2026-09-01T08:00:00+00:00")
    fin = t0 + timedelta(minutes=10)
    await repo._conn.executemany(
        "INSERT INTO samples (router_id, wan_id, ts, connected, kind, rtt_ms, sinr, tunnel_state) "
        "VALUES ('r1', '4', ?, 1, 'cellular', ?, ?, 'ACTIVE')",
        [
            ((t0 + timedelta(seconds=5 * i)).isoformat(),
             1929.0 if i == 7 else 30.0,
             2.0 if i == 7 else 14.0)
            for i in range(12)
        ],
    )
    await repo._conn.commit()

    lignes, _, _ = await repo.timeline("r1", t0, fin)
    # `rtt_pire` a été retiré de la tranche : il était calculé, sérialisé à
    # chaque relecture, et lu par personne. La bande dit la qualité radio et le
    # fait de porter, pas le temps de transit.
    assert min(l["sinr_pire"] for l in lignes) == 2.0, "le creux de SINR a été lissé"
    await repo.close()


async def test_a_window_nobody_watched_leaves_its_slices_absent(tmp_path):
    """Une tranche sans relevé est une information : on ne regardait pas.

    La combler à zéro referait le mensonge que tout ce module combat — un lien
    au repos et un superviseur arrêté ne se ressemblent pas.
    """
    from datetime import datetime, timedelta
    from app.storage.db import _TRANCHES_BANDE

    repo = SampleRepository(str(tmp_path / "s.db"))
    await repo.connect()
    t0 = datetime.fromisoformat("2026-09-01T08:00:00+00:00")
    fin = t0 + timedelta(hours=2)
    # Des relevés sur la PREMIÈRE heure seulement.
    await repo._conn.executemany(
        "INSERT INTO samples (router_id, wan_id, ts, connected, kind, tunnel_state) "
        "VALUES ('r1', '4', ?, 1, 'cellular', 'ACTIVE')",
        [((t0 + timedelta(seconds=10 * i)).isoformat(),) for i in range(360)],
    )
    await repo._conn.commit()

    lignes, _, _ = await repo.timeline("r1", t0, fin)
    tranches = {l["tranche"] for l in lignes}
    assert max(tranches) < _TRANCHES_BANDE // 2 + 2, "la seconde heure a été inventée"
    assert len(tranches) < _TRANCHES_BANDE
    await repo.close()


async def test_the_timeline_separates_being_up_from_carrying(tmp_path):
    """Un lien peut être debout côté WAN et écarté du bond.

    C'est la distinction que le modèle porte depuis le début, et rien dans
    l'état du WAN ne la dit. `tunnel_connu` existe pour que « ne portait pas »
    ne se confonde pas avec « on ne savait pas ».
    """
    from datetime import datetime, timedelta

    repo = SampleRepository(str(tmp_path / "s.db"))
    await repo.connect()
    t0 = datetime.fromisoformat("2026-09-01T08:00:00+00:00")
    fin = t0 + timedelta(minutes=10)
    await repo._conn.executemany(
        "INSERT INTO samples (router_id, wan_id, ts, connected, kind, tunnel_state) "
        "VALUES ('r1', '4', ?, 1, 'cellular', ?)",
        [
            ((t0 + timedelta(seconds=5 * i)).isoformat(),
             "ACTIVE" if i < 4 else ("WAN_DOWN" if i < 8 else None))
            for i in range(12)
        ],
    )
    await repo._conn.commit()

    lignes, _, _ = await repo.timeline("r1", t0, fin)
    assert sum(l["debout"] for l in lignes) == 12, "les douze relevés étaient debout"
    assert sum(l["portants"] for l in lignes) == 4, "seuls quatre portaient"
    assert sum(l["tunnel_connu"] for l in lignes) == 8, "l'état du tunnel n'était connu que huit fois"

    # Le relevé d'adieu : `connected = 0`, état de tunnel encore ACTIVE. Il ne
    # porte rien — même prédicat que la fiche de session et la mémoire du bond,
    # que la bande oubliait.
    await repo._conn.execute(
        "INSERT INTO samples (router_id, wan_id, ts, connected, kind, tunnel_state) "
        "VALUES ('r1', '4', ?, 0, 'cellular', 'ACTIVE')",
        ((t0 + timedelta(seconds=5 * 12)).isoformat(),),
    )
    await repo._conn.commit()
    lignes, _, _ = await repo.timeline("r1", t0, fin)
    assert sum(l["debout"] for l in lignes) == 12
    assert sum(l["portants"] for l in lignes) == 4, "un lien tombé ne porte pas, ACTIVE ou non"
    await repo.close()


async def test_a_rate_travels_with_its_denominator(tmp_path):
    """Un taux sans son dénominateur est indéfendable.

    « 0,00 % sur trois paquets de keepalive » et « 0,00 % sur cent mille paquets
    utiles » s'écrivent exactement pareil. La tuile et la table refusaient déjà
    de publier un taux calculé sur moins de deux cents paquets ; l'historique,
    lui, ne transportait pas de quoi appliquer la règle, et la courbe traçait un
    pic à 33,33 % obtenu sur UN paquet manquant — sur un lien parfaitement sain.

    Le seuil n'est PAS appliqué ici : c'est une règle de rendu, et la graver côté
    serveur la ferait survivre à toute décision ultérieure.
    """
    from datetime import datetime, timedelta

    repo = SampleRepository(str(tmp_path / "s.db"))
    await repo.connect()
    t0 = datetime.fromisoformat("2026-09-01T08:00:00+00:00")
    await repo._conn.executemany(
        "INSERT INTO samples (router_id, wan_id, ts, connected, kind, "
        "loss_percent, loss_percent_up, loss_percent_down, packets_up, packets_down, sinr) "
        "VALUES ('r1', '4', ?, 1, 'cellular', ?, ?, ?, ?, ?, ?)",
        [
            ((t0 + timedelta(seconds=5 * i)).isoformat(),
             33.33 if i == 3 else 0.0,
             33.33 if i == 3 else 0.0,
             0.0,
             1 if i == 3 else 12000,
             1 if i == 3 else 12000,
             12.0)
            for i in range(10)
        ],
    )
    await repo._conn.commit()

    points, _ = await repo.history(
        "r1", "4", "loss_percent", debut=t0 - timedelta(minutes=1), fin=t0 + timedelta(hours=1)
    )
    # La valeur est rendue TELLE QUELLE, et le dénominateur DU SENS QUI LA PORTE
    # l'accompagne. Il valait `MAX(packets_up, packets_down)`, donc celui de
    # l'autre sens dès que le sens dégradé était le moins chargé : mesuré sur le
    # matériel, douze relevés publiaient ainsi un taux établi sur moins de deux
    # cents paquets sous un dénominateur qui franchissait le seuil.
    suspect = [p for p in points if p.value == 33.33]
    assert len(suspect) == 1
    assert suspect[0].denominator == 1, "l'écran ne peut plus juger le poids du taux"
    assert all(p.denominator == 12000 for p in points if p.value == 0.0)

    # Une grandeur qui n'est PAS un taux n'a pas de dénominateur : en inventer un
    # laisserait croire qu'un niveau radio se pondère.
    niveaux, _ = await repo.history(
        "r1", "4", "sinr", debut=t0 - timedelta(minutes=1), fin=t0 + timedelta(hours=1)
    )
    assert all(p.denominator is None for p in niveaux)
    await repo.close()


async def test_an_absent_counter_is_not_a_zero_denominator(tmp_path):
    """C-28 (B11) : un taux publié SANS compteur de paquets rend un
    dénominateur `None`, jamais 0 — « 0 paquet compté » serait une
    affirmation de comptage que la base ne soutient pas. La mutation qui
    remplaçait l'absence par zéro survivait."""
    from datetime import datetime, timedelta, timezone

    repo = SampleRepository(str(tmp_path / "s.db"))
    await repo.connect()
    t0 = datetime(2026, 9, 11, 20, 0, tzinfo=timezone.utc)
    await repo._conn.executemany(
        "INSERT INTO samples (router_id, wan_id, ts, connected, kind, loss_percent, "
        "loss_percent_up, loss_percent_down, packets_up, packets_down) "
        "VALUES ('r1', '4', ?, 1, 'cellular', ?, ?, ?, ?, ?)",
        [
            ((t0).isoformat(), 0.0, 0.0, 0.0, None, None),          # compteur absent
            ((t0 + timedelta(seconds=5)).isoformat(), 0.0, 0.0, None, 0, None),  # zéro compté
        ],
    )
    await repo._conn.commit()
    points, _ = await repo.history("r1", "4", "loss_percent", t0 - timedelta(seconds=1), t0 + timedelta(minutes=1))
    assert [p.denominator for p in points] == [None, 0]
    await repo.close()


async def test_the_denominator_survives_the_sql_envelope(tmp_path):
    """Sur une fenêtre dense, l'enveloppe est calculée par SQLite : le
    dénominateur doit franchir ce chemin-là aussi, sans quoi la règle des deux
    cents paquets s'appliquerait sur les fenêtres courtes et pas sur les longues.
    """
    from datetime import datetime, timedelta
    from app.storage.db import _LIGNES_MEMOIRE_MAX

    repo = SampleRepository(str(tmp_path / "s.db"))
    await repo.connect()
    n = _LIGNES_MEMOIRE_MAX + 500
    t0 = datetime.fromisoformat("2026-09-01T08:00:00+00:00")
    await repo._conn.executemany(
        "INSERT INTO samples (router_id, wan_id, ts, connected, kind, "
        "loss_percent, loss_percent_up, loss_percent_down, packets_up, packets_down) "
        "VALUES ('r1', '4', ?, 1, 'cellular', ?, ?, 0.0, ?, ?)",
        [
            ((t0 + timedelta(seconds=5 * i)).isoformat(),
             33.33 if i == n // 2 else 0.0,
             33.33 if i == n // 2 else 0.0,
             2 if i == n // 2 else 9000,
             2 if i == n // 2 else 9000)
            for i in range(n)
        ],
    )
    await repo._conn.commit()

    points, available = await repo.history(
        "r1", "4", "loss_percent",
        debut=t0 - timedelta(minutes=1), fin=t0 + timedelta(days=30),
    )
    assert available == n
    pic = [p for p in points if p.value == 33.33]
    assert pic, "le pic a disparu de l'enveloppe"
    assert pic[0].denominator == 2, "le dénominateur n'a pas franchi l'enveloppe SQL"
    await repo.close()


async def test_the_publication_rule_elects_the_peak_before_the_envelope(tmp_path):
    """Le 33,33 % sur 3 paquets éclipsait le 4,7 % réel sur 300.

    L'enveloppe élit min et max de chaque tranche sur la valeur seule ; l'écran
    jetait ensuite le pic non publiable — et le vrai pic avait disparu en
    amont, sans mention. La règle est celle de l'écran, transmise par
    `min_packets`, et elle s'applique AVANT l'allègement.
    """
    from datetime import datetime, timedelta
    from app.storage.db import _LIGNES_MEMOIRE_MAX

    repo = SampleRepository(str(tmp_path / "s.db"))
    await repo.connect()
    n = _LIGNES_MEMOIRE_MAX + 500
    t0 = datetime.fromisoformat("2026-09-01T08:00:00+00:00")
    # Dans la même tranche : un pic factice (33,33 % sur 3) et un pic réel
    # (4,7 % sur 300) ; partout ailleurs 0 % sur 9 000.
    milieu = n // 2
    await repo._conn.executemany(
        "INSERT INTO samples (router_id, wan_id, ts, connected, kind, "
        "loss_percent, loss_percent_up, loss_percent_down, packets_up, packets_down) "
        "VALUES ('r1', '4', ?, 1, 'cellular', ?, ?, 0.0, ?, 9000)",
        [
            ((t0 + timedelta(seconds=5 * i)).isoformat(),
             33.33 if i == milieu else 4.7 if i == milieu + 1 else 0.0,
             33.33 if i == milieu else 4.7 if i == milieu + 1 else 0.0,
             3 if i == milieu else 300 if i == milieu + 1 else 9000)
            for i in range(n)
        ],
    )
    await repo._conn.commit()

    fenetre = dict(debut=t0 - timedelta(minutes=1), fin=t0 + timedelta(days=30))
    sans, _ = await repo.history("r1", "4", "loss_percent", **fenetre)
    assert any(p.value == 33.33 for p in sans)
    assert not any(p.value == 4.7 for p in sans), "sans règle, le pic factice éclipse le réel"

    avec, _ = await repo.history("r1", "4", "loss_percent", min_packets=200, **fenetre)
    assert not any(p.value == 33.33 for p in avec), "le pic sur 3 paquets n'est pas publiable"
    reel = [p for p in avec if p.value == 4.7]
    assert reel and reel[0].denominator == 300, "le pic réel doit franchir l'enveloppe"
    await repo.close()


async def test_the_publication_rule_takes_the_worst_PUBLISHABLE_direction(tmp_path):
    """Même règle que la table : le pire des sens qui atteignent le compte.

    Montant à 50 % sur 4 paquets, descendant à 2 % sur 5 000 : le graphe
    rendait 50 % avec 4 pour dénominateur — un trou à l'écran — pendant que la
    table disait 2 %. Deux règles pour un même relevé.
    """
    from datetime import datetime, timedelta

    repo = SampleRepository(str(tmp_path / "s.db"))
    await repo.connect()
    t0 = datetime.fromisoformat("2026-09-01T08:00:00+00:00")
    await repo._conn.execute(
        "INSERT INTO samples (router_id, wan_id, ts, connected, kind, "
        "loss_percent, loss_percent_up, loss_percent_down, packets_up, packets_down) "
        "VALUES ('r1', '4', ?, 1, 'cellular', 50.0, 50.0, 2.0, 4, 5000)",
        (t0.isoformat(),),
    )
    # Aucun sens publiable : le taux est absent, pas nul.
    await repo._conn.execute(
        "INSERT INTO samples (router_id, wan_id, ts, connected, kind, "
        "loss_percent, loss_percent_up, loss_percent_down, packets_up, packets_down) "
        "VALUES ('r1', '4', ?, 1, 'cellular', 0.0, 0.0, 0.0, 3, 2)",
        ((t0 + timedelta(seconds=5)).isoformat(),),
    )
    await repo._conn.commit()
    fenetre = dict(debut=t0 - timedelta(minutes=1), fin=t0 + timedelta(hours=1))
    avec, _ = await repo.history("r1", "4", "loss_percent", min_packets=200, **fenetre)
    assert (avec[0].value, avec[0].denominator) == (2.0, 5000)
    assert (avec[1].value, avec[1].denominator) == (None, None)
    sans, _ = await repo.history("r1", "4", "loss_percent", **fenetre)
    assert (sans[0].value, sans[0].denominator) == (50.0, 4)
    await repo.close()


async def test_an_external_reader_does_not_block_the_collector(tmp_path):
    """En journal DELETE, une transaction de lecture ouverte par un script de
    diagnostic rendait « database is locked » après 5,1 s : relevé perdu, et
    25 s par cycle tant que le lecteur restait. En WAL, un lecteur ne bloque
    pas l'écrivain.
    """
    import sqlite3
    from app.models import RadioSample, WanRadioStatus

    repo = SampleRepository(str(tmp_path / "s.db"))
    await repo.connect()
    async with repo._conn.execute("PRAGMA journal_mode") as cur:
        assert (await cur.fetchone())[0] == "wal"

    # Un lecteur externe, transaction ouverte, résultat non consommé.
    lecteur = sqlite3.connect(str(tmp_path / "s.db"), timeout=0.1)
    lecteur.execute("BEGIN")
    lecteur.execute("SELECT COUNT(*) FROM samples").fetchone()
    try:
        await asyncio.wait_for(repo.insert(RadioSample(
            router_id="r1", router_name="R1",
            wan=WanRadioStatus(wan_id="4", wan_name="SIM"),
        )), timeout=2.0)
    finally:
        lecteur.rollback()
        lecteur.close()
    async with repo._conn.execute("SELECT COUNT(*) FROM samples") as cur:
        assert (await cur.fetchone())[0] == 1
    await repo.close()


async def test_a_cycle_is_committed_once(tmp_path):
    """`insert(..., commit=False)` laisse le cycle valider une fois : un autre
    lecteur ne voit rien avant `commit()`, et tout d'un coup après."""
    import sqlite3
    from app.models import RadioSample, WanRadioStatus

    repo = SampleRepository(str(tmp_path / "s.db"))
    await repo.connect()
    for wan in ("4", "5", "6"):
        await repo.insert(RadioSample(
            router_id="r1", router_name="R1", wan=WanRadioStatus(wan_id=wan, wan_name="SIM"),
        ), commit=False)
    autre = sqlite3.connect(str(tmp_path / "s.db"))
    assert autre.execute("SELECT COUNT(*) FROM samples").fetchone()[0] == 0
    await repo.commit()
    assert autre.execute("SELECT COUNT(*) FROM samples").fetchone()[0] == 3
    autre.close()
    await repo.close()


async def test_a_radio_series_carries_the_technology_of_each_point(tmp_path):
    """Scénario 5 de la revue : SIM forcée en LTE. La courbe du SINR enchaînait
    28 dB (5G n78) et 13 dB (LTE B3) sans rien qui le dise : un changement de
    technologie se lisait comme une chute de signal. Chaque point radio porte
    sa technologie, y compris dans l'enveloppe ; un débit n'en porte pas."""
    from datetime import datetime, timedelta
    from app.storage.db import _LIGNES_MEMOIRE_MAX

    repo = SampleRepository(str(tmp_path / "s.db"))
    await repo.connect()
    t0 = datetime.fromisoformat("2026-09-24T16:12:00+00:00")
    lignes = [((t0 + timedelta(seconds=5 * i)).isoformat(), "5G NSA" if i < 10 else "LTE-A",
               28.0 if i < 10 else 13.0) for i in range(20)]
    await repo._conn.executemany(
        "INSERT INTO samples (router_id, wan_id, ts, connected, kind, rat, sinr, rx_kbps) "
        "VALUES ('r1', '2', ?, 1, 'cellular', ?, ?, 100)", lignes)
    await repo._conn.commit()
    fenetre = dict(debut=t0 - timedelta(minutes=1), fin=t0 + timedelta(hours=1))
    pts, _ = await repo.history("r1", "2", "sinr", **fenetre)
    assert [p.rat for p in pts][:10] == ["5G NSA"] * 10 and pts[-1].rat == "LTE-A"
    debit, _ = await repo.history("r1", "2", "rx_kbps", **fenetre)
    assert all(p.rat is None for p in debit)

    # Et dans l'enveloppe SQL d'une fenêtre dense.
    n = _LIGNES_MEMOIRE_MAX + 100
    await repo._conn.executemany(
        "INSERT INTO samples (router_id, wan_id, ts, connected, kind, rat, sinr) "
        "VALUES ('r1', '4', ?, 1, 'cellular', ?, 20.0)",
        [((t0 + timedelta(seconds=i)).isoformat(), "LTE") for i in range(n)])
    await repo._conn.commit()
    dense, dispo = await repo.history("r1", "4", "sinr", debut=t0, fin=t0 + timedelta(days=1))
    assert dispo == n and dense and all(p.rat == "LTE" for p in dense)
    await repo.close()


async def test_router_events_are_stored_once_and_read_by_window(tmp_path):
    """Les faits du journal du routeur : la même ligne revient à chaque lecture
    tant qu'elle reste parmi les cinquante dernières — une seule fois en base."""
    from datetime import datetime, timedelta, timezone
    from app.models import RouterEvent

    repo = SampleRepository(str(tmp_path / "s.db"))
    await repo.connect()
    t0 = datetime(2026, 9, 24, 16, 1, 3, tzinfo=timezone.utc)
    faits = [
        RouterEvent(ts=t0, kind="coupure", wan_id="3", detail="Disabled", empreinte="a" * 24),
        RouterEvent(ts=t0 + timedelta(minutes=5), kind="bascule de SIM", wan_id="3",
                    detail="vers la SIM B", empreinte="b" * 24),
    ]
    await repo.insert_router_events("r1", faits)
    await repo.insert_router_events("r1", faits)            # relu une minute plus tard
    await repo.insert_router_events("autre", faits[:1])     # un autre équipement
    lus = await repo.router_events("r1", t0 - timedelta(seconds=1), t0 + timedelta(hours=1))
    assert [(f.kind, f.wan_id, f.detail) for f in lus] == [
        ("coupure", "3", "Disabled"), ("bascule de SIM", "3", "vers la SIM B"),
    ]
    assert len(await repo.router_events("r1", t0 + timedelta(minutes=1), t0 + timedelta(hours=1))) == 1
    await repo.close()


# --- le journal d'épisodes ---------------------------------------------------

async def _journal(repo, lignes, seuil=500.0):
    """Insère des relevés bruts puis rend le journal de la fenêtre.

    `enb_id` est posé comme le parseur le pose : cette insertion court-circuite
    `insert()`, donc elle doit écrire les mêmes colonnes, sans quoi elle teste une
    base que la production ne produit jamais. Le journal compte les changements de
    STATION depuis cette colonne — il refabriquait `cell_id / 256` sans le
    garde-fou qui refuse de décomposer un NCI 5G SA.
    """
    from datetime import datetime, timedelta
    t0 = datetime.fromisoformat("2026-09-01T08:00:00+00:00")
    await repo._conn.executemany(
        "INSERT INTO samples (router_id, wan_id, ts, connected, kind, "
        "rtt_ms, uptime_s, cell_id, enb_id, status_message) "
        "VALUES ('r1', ?, ?, ?, 'cellular', ?, ?, ?, ?, ?)",
        [
            (w, (t0 + timedelta(seconds=5 * i)).isoformat(), c, rtt, up, cell,
             None if cell is None else cell >> 8, msg)
            for i, (w, c, rtt, up, cell, msg) in enumerate(lignes)
        ],
    )
    await repo._conn.commit()
    return await repo.events("r1", t0 - timedelta(minutes=1), t0 + timedelta(hours=2), seuil)


async def test_the_log_dates_an_outage_and_says_what_the_router_said(tmp_path):
    """« Trois coupures » ne dit ni quand, ni sur quel lien, ni pourquoi.

    Le compteur existait depuis le début ; la chronologie, non. Et le message du
    routeur — la CAUSE — n'était même pas persisté.
    """
    repo = SampleRepository(str(tmp_path / "s.db"))
    await repo.connect()
    episodes, _ = await _journal(repo, [
        ("4", 1, 30.0, 100, 111, "Connected to Orange"),
        ("4", 0, None, 0, None, "No Cable Detected"),
        ("4", 1, 30.0, 5, 111, "Connected to Orange"),
    ])
    genres = {e["genre"] for e in episodes}
    assert genres == {"coupure", "retour"}
    coupure = next(e for e in episodes if e["genre"] == "coupure")
    assert coupure["status_message"] == "No Cable Detected", "la cause a été perdue"
    assert coupure["kind"] == "cellular", "le journal doit nommer le lien par son support"
    await repo.close()


async def test_the_log_catches_the_outage_only_the_uptime_betrays(tmp_path):
    """Une coupure plus courte que le cycle ne se voit jamais autrement.

    Le lien est reparti avant qu'on le relise, et les deux relevés l'encadrant le
    montrent debout : seul l'`uptime` qui recule la trahit. À 5,3 s de période,
    c'est l'angle mort où se cachent les micro-coupures.
    """
    repo = SampleRepository(str(tmp_path / "s.db"))
    await repo.connect()
    episodes, _ = await _journal(repo, [
        ("4", 1, 30.0, 7200, 111, "Connected to Orange"),
        ("4", 1, 30.0, 3, 111, "Connected to Orange"),
    ])
    assert [e["genre"] for e in episodes] == ["micro-coupure"]
    await repo.close()


async def test_latency_makes_ISLANDS_and_not_one_event_per_sample(tmp_path):
    """Un épisode est une suite CONTIGUË, pas un relevé.

    Mesuré sur ce déploiement : à 500 ms, cinquante-neuf îlots pour cent relevés.
    Sans le regroupement, le journal aurait porté cent lignes pour cinquante-neuf
    faits — et il aurait cessé d'être lu.
    """
    repo = SampleRepository(str(tmp_path / "s.db"))
    await repo.connect()
    # Trois relevés hauts d'affilée, un bas, puis deux hauts : DEUX îlots.
    episodes, _ = await _journal(repo, [
        ("4", 1, 520.0, 100, 111, None),
        ("4", 1, 610.0, 105, 111, None),
        ("4", 1, 540.0, 110, 111, None),
        ("4", 1, 30.0, 115, 111, None),
        ("4", 1, 900.0, 120, 111, None),
        ("4", 1, 700.0, 125, 111, None),
    ])
    # Un seul lien, aucun seuil de bond : le routeur ne peut avoir écarté
    # personne. C'est le garde-fou de l'outil qui parle, et l'épisode le dit —
    # l'étiqueter « écarté » affirmerait une action qui n'a pas eu lieu.
    ilots = [e for e in episodes if e["genre"] == "latence haute"]
    assert len(ilots) == 2, "les relevés contigus n'ont pas été regroupés"
    assert not [e for e in episodes if e["genre"] == "latence"]
    pics = sorted(e["pic"] for e in ilots)
    assert pics == [610.0, 900.0], "l'îlot doit porter son pic, pas sa moyenne"
    assert sorted(e["releves"] for e in ilots) == [2, 3]
    await repo.close()


async def test_a_high_latency_island_never_spans_a_supervision_gap(tmp_path):
    """C-8 : un pic juste avant l'arrêt du superviseur, un autre au premier
    relevé de la reprise, trois heures plus tard.

    L'îlot se calculait par différence de ROW_NUMBER sans segment de trou :
    deux relevés hauts CONSÉCUTIFS en base formaient un seul épisode, quel que
    soit l'écart entre eux. Le journal publiait « latence haute, 3 h, 2
    relevés » — classé en tête par sa durée — pour trois heures où personne ne
    regardait.
    """
    from datetime import datetime, timedelta

    repo = SampleRepository(str(tmp_path / "s.db"))
    await repo.connect()
    t0 = datetime.fromisoformat("2026-09-01T10:00:00+00:00")
    instants = [t0, t0 + timedelta(seconds=5), t0 + timedelta(seconds=10),
                t0 + timedelta(hours=3), t0 + timedelta(hours=3, seconds=5)]
    rtts = [30.0, 30.0, 1600.0, 1500.0, 30.0]
    await repo._conn.executemany(
        "INSERT INTO samples (router_id, wan_id, ts, connected, kind, rtt_ms, uptime_s) "
        "VALUES ('r1', '4', ?, 1, 'cellular', ?, ?)",
        [(t.isoformat(), r, 100 + i) for i, (t, r) in enumerate(zip(instants, rtts))],
    )
    await repo._conn.commit()
    episodes, _ = await repo.events("r1", t0 - timedelta(minutes=1), t0 + timedelta(hours=4), 500.0)
    ilots = [e for e in episodes if e["genre"] == "latence haute"]
    assert len(ilots) == 2, "l'épisode a enjambé le trou de supervision"
    assert all(e["releves"] == 1 for e in ilots)
    await repo.close()



async def test_a_reading_of_UNKNOWN_height_closes_a_latency_island(tmp_path):
    """Un relevé sous le seuil absolu, avec un écart de coupure publié mais
    sans peloton pour s'y comparer, n'est ni haut ni bas : `haut` y vaut NULL.
    Il n'est pas haut, donc il ferme l'îlot — deux pics qui l'encadrent sont
    deux épisodes. L'ancienne détection le faisait par ses `ROW_NUMBER` ; la
    nouvelle doit le faire aussi (C-31)."""
    from datetime import datetime, timedelta

    async with SampleRepository(str(tmp_path / "s.db")) as repo:
        t0 = datetime.fromisoformat("2026-09-01T10:00:00+00:00")
        await repo._conn.executemany(
            "INSERT INTO samples (router_id, wan_id, ts, connected, kind, rtt_ms, cutoff_ms) "
            "VALUES ('r1', '4', ?, 1, 'cellular', ?, 100.0)",
            [((t0 + timedelta(seconds=5 * i)).isoformat(), r) for i, r in enumerate((600.0, 30.0, 700.0))],
        )
        await repo._conn.commit()
        episodes, _ = await repo.events("r1", t0 - timedelta(minutes=1), t0 + timedelta(minutes=1), 500.0)
        ilots = [e for e in episodes if e["genre"] == "latence haute"]
        assert len(ilots) == 2, ilots


async def test_the_log_is_read_on_its_own_connection(tmp_path):
    """C-31 : events() enchaîne cinq balayages fenêtrés — 1,8 s sur la plus
    longue session de la base — sur l'UNIQUE connexion, relus toutes les 30 s
    par l'écran : les écritures du collecteur attendaient derrière. Le journal
    lit désormais sur une connexion à lui, en lecture seule ; la base est en
    WAL, un lecteur n'y bloque pas l'écrivain."""
    from datetime import datetime, timedelta, timezone

    repo = SampleRepository(str(tmp_path / "s.db"))
    await repo.connect()
    assert repo._lecture is not None and repo._lecture is not repo._conn
    t0 = datetime(2026, 9, 1, 8, 0, tzinfo=timezone.utc)
    await repo._conn.execute(
        "INSERT INTO samples (router_id, wan_id, ts, connected, kind, rtt_ms) "
        "VALUES ('r1', '4', ?, 1, 'cellular', 900.0)", (t0.isoformat(),))
    await repo._conn.commit()

    ecrivain = []
    origine = repo._conn.execute

    def espion(*args, **kwargs):
        ecrivain.append(args[0][:40])
        return origine(*args, **kwargs)

    repo._conn.execute = espion
    episodes, _ = await repo.events("r1", t0 - timedelta(minutes=1), t0 + timedelta(minutes=1), 500.0)
    repo._conn.execute = origine
    assert [e for e in episodes if e["genre"] == "latence haute"], "le lecteur doit voir le relevé validé"
    assert ecrivain == [], f"le journal a lu sur la connexion de l'écrivain : {ecrivain}"
    await repo.close()
    assert repo._lecture is None


async def test_what_is_too_frequent_is_counted_and_not_listed(tmp_path):
    """Cinq coupures contre neuf cent quatre-vingt-cinq changements de station.

    Lister les seconds noierait les premiers. Ce qui se liste se liste ; ce qui
    est trop fréquent se compte, par lien — et le compte reste parlant.
    """
    repo = SampleRepository(str(tmp_path / "s.db"))
    await repo.connect()
    # Le lien 4 saute de station à chaque relevé ; le lien 5 ne bouge pas.
    lignes = [("4", 1, 30.0, 100 + i, 25735426 + i * 256, None) for i in range(6)]
    lignes += [("5", 1, 30.0, 100 + i, 25735426, None) for i in range(6)]
    episodes, brassage = await _journal(repo, lignes)

    assert episodes == [], "le brassage ne doit produire AUCUNE ligne de journal"
    par_lien = {c["wan_id"]: c for c in brassage}
    assert par_lien["4"]["cellules"] == 5 and par_lien["4"]["stations"] == 5
    # Tous les liens sont comptés, y compris ceux qui ne brassent pas : la même
    # requête porte désormais les COUPURES de la fenêtre, qu'un lien stable doit
    # pouvoir déclarer à zéro. C'est l'écran qui choisit de ne citer que ceux qui
    # brassent.
    assert par_lien["5"]["cellules"] == 0 and par_lien["5"]["stations"] == 0
    await repo.close()


async def test_an_unmeasured_repair_counter_stays_absent_in_the_band(tmp_path):
    """`COALESCE(SUM(repair_sent), 0)` ne faisait tomber que le test « golden ».
    Une colonne que le firmware n'a jamais renseignée n'est pas un intervalle
    sans réparation — et c'est de cette somme que sortait « la redondance du
    profil est éteinte »."""
    from datetime import datetime, timedelta

    repo = SampleRepository(str(tmp_path / "s.db"))
    await repo.connect()
    t0 = datetime.fromisoformat("2026-09-01T08:00:00+00:00")
    await repo._conn.executemany(
        "INSERT INTO samples (router_id, wan_id, ts, connected, kind, tunnel_state, packets_up, repair_sent) "
        "VALUES ('r1', '4', ?, 1, 'cellular', 'ACTIVE', 1000, NULL)",
        [((t0 + timedelta(seconds=5 * i)).isoformat(),) for i in range(12)],
    )
    await repo._conn.commit()
    lignes, _, _ = await repo.timeline("r1", t0, t0 + timedelta(minutes=10))
    assert lignes and all(l["paquets"] is not None for l in lignes)
    assert all(l["reparation"] is None for l in lignes), "zéro fabriqué pour une absence"
    await repo.close()


async def test_a_link_without_any_cell_is_not_counted_as_having_one(tmp_path):
    """`avec_cellule` mis à 1 ne faisait tomber que le test « golden » : un
    filaire s'affichait « 0 / 0 » au milieu de modems dont le zéro serait une
    vraie stabilité."""
    from datetime import datetime, timedelta

    repo = SampleRepository(str(tmp_path / "s.db"))
    await repo.connect()
    t0 = datetime.fromisoformat("2026-09-01T08:00:00+00:00")
    await repo._conn.executemany(
        "INSERT INTO samples (router_id, wan_id, ts, connected, kind, cell_id) "
        "VALUES ('r1', ?, ?, 1, ?, ?)",
        [(w, (t0 + timedelta(seconds=5 * i)).isoformat(), k, c)
         for i in range(6) for w, k, c in (("1", "ethernet", None), ("4", "cellular", 25735426))],
    )
    await repo._conn.commit()
    _, brassage = await repo.events("r1", t0 - timedelta(minutes=1), t0 + timedelta(hours=1), 500.0)
    par_lien = {c["wan_id"]: c for c in brassage}
    assert par_lien["1"]["avec_cellule"] == 0
    assert par_lien["4"]["avec_cellule"] == 6
    await repo.close()


async def test_a_supervision_gap_is_not_a_neighbourhood(tmp_path):
    """`LAG` enjambait les trous : sur une fenêtre manuelle traversant le trou
    du 30 août au 11 septembre, chaque WAN comptait un changement de cellule
    fantôme, et le journal datait une coupure du premier relevé revu. Même
    garde que `bascules` : au-delà de `_TROU_MAX_S`, pas de voisin.
    """
    from datetime import datetime, timedelta

    repo = SampleRepository(str(tmp_path / "s.db"))
    await repo.connect()
    t0 = datetime.fromisoformat("2026-09-01T08:00:00+00:00")
    # Une halte, un trou de douze jours, une autre halte. Le lien 4 y est sur
    # une autre cellule, avec un uptime plus petit ; le lien 5 y est tombé.
    lignes = [
        ("4", t0, 1, 25735426, 100), ("4", t0 + timedelta(seconds=5), 1, 25735426, 105),
        ("4", t0 + timedelta(days=12), 1, 99999999, 3),
        ("4", t0 + timedelta(days=12, seconds=5), 1, 99999999, 8),
        ("5", t0, 1, 25735426, 100), ("5", t0 + timedelta(seconds=5), 1, 25735426, 105),
        ("5", t0 + timedelta(days=12), 0, None, None),
        ("5", t0 + timedelta(days=12, seconds=5), 0, None, None),
    ]
    await repo._conn.executemany(
        "INSERT INTO samples (router_id, wan_id, ts, connected, kind, cell_id, enb_id, uptime_s) "
        "VALUES ('r1', ?, ?, ?, 'cellular', ?, ?, ?)",
        [(w, ts.isoformat(), c, cell, None if cell is None else cell >> 8, up)
         for w, ts, c, cell, up in lignes],
    )
    await repo._conn.commit()
    episodes, brassage = await repo.events(
        "r1", t0 - timedelta(minutes=1), t0 + timedelta(days=13), 500.0
    )
    assert [e["genre"] for e in episodes] == [], "ni coupure ni micro-coupure datée du premier relevé revu"
    par_lien = {c["wan_id"]: c for c in brassage}
    assert par_lien["4"]["cellules"] == 0 and par_lien["4"]["stations"] == 0
    assert par_lien["4"]["micro"] == 0
    assert par_lien["5"]["coupures"] == 0
    await repo.close()


async def test_outages_are_counted_over_the_WINDOW_not_since_the_process_started(tmp_path):
    """Le compteur de l'écran avait une base de temps qui n'y figurait nulle part.

    Il cumulait depuis le démarrage du PROCESSUS superviseur — donc il se
    remettait à zéro à chaque relance — dans un panneau intitulé « depuis la mise
    sous tension ». Mesuré : zéro coupure dans la session affichée, cinq dans
    toute la base, et la colonne montrait ni l'une ni l'autre.
    """
    repo = SampleRepository(str(tmp_path / "s.db"))
    await repo.connect()
    _, brassage = await _journal(repo, [
        ("4", 1, 30.0, 100, 111, None),
        ("4", 0, None, 0, None, "No Cable Detected"),
        ("4", 1, 30.0, 5, 111, None),
        # Une micro-coupure : les deux relevés le montrent debout, seul
        # l'`uptime` qui recule la trahit.
        ("4", 1, 30.0, 2, 111, None),
        ("5", 1, 30.0, 100, 222, None),
        ("5", 1, 30.0, 105, 222, None),
    ])
    par_lien = {c["wan_id"]: c for c in brassage}
    assert par_lien["4"]["coupures"] == 1
    assert par_lien["4"]["micro"] == 1
    # Un lien stable déclare zéro, et il faut qu'il puisse le déclarer.
    assert par_lien["5"]["coupures"] == 0 and par_lien["5"]["micro"] == 0
    await repo.close()


async def test_a_latency_episode_follows_the_ROUTER_rule_not_an_invented_threshold(tmp_path):
    """Le journal jugeait la latence sur un seuil ABSOLU de 500 ms.

    Le routeur, lui, applique un ÉCART : il arrête le trafic sur un lien qui
    dépasse de plus de `cutoff_ms` celui de latence la plus basse. Deux règles
    pour une grandeur, et la table marquait « au-delà du seuil » des liens que le
    journal ne mentionnait pas. Une seule règle désormais — la sienne.
    """
    from datetime import datetime, timedelta

    repo = SampleRepository(str(tmp_path / "s.db"))
    await repo.connect()
    t0 = datetime.fromisoformat("2026-09-01T08:00:00+00:00")
    # Deux liens du même cycle : l'un à 6 ms, l'autre à 600 — soit 594 d'écart,
    # au-delà du seuil de 500. Aucun des deux n'atteint le garde-fou absolu.
    await repo._conn.executemany(
        "INSERT INTO samples (router_id, wan_id, ts, connected, kind, rtt_ms, cutoff_ms, "
        "tunnel_state) VALUES ('r1', ?, ?, 1, 'cellular', ?, 500, 'ACTIVE')",
        [
            (w, (t0 + timedelta(seconds=6 * i)).isoformat(), rtt)
            for i in range(4)
            for w, rtt in (("1", 6.0), ("4", 600.0))
        ],
    )
    await repo._conn.commit()

    episodes, _ = await repo.events(
        "r1", t0 - timedelta(minutes=1), t0 + timedelta(hours=1), 1000.0
    )
    latences = [e for e in episodes if e["genre"] == "latence"]
    assert len(latences) == 1, "un seul îlot, sur le lien détaché"
    assert latences[0]["wan_id"] == "4"
    assert latences[0]["pic"] == 600.0
    await repo.close()


async def test_a_bond_where_every_link_is_bad_still_opens_an_episode(tmp_path):
    """L'écart est aveugle à un cas, et c'est pourquoi le garde-fou existe.

    Un bond dont TOUS les liens sont mauvais se ressemble parfaitement : aucun
    n'y dépasse personne, et la règle du routeur ne dirait rien.
    """
    from datetime import datetime, timedelta

    repo = SampleRepository(str(tmp_path / "s.db"))
    await repo.connect()
    t0 = datetime.fromisoformat("2026-09-01T08:00:00+00:00")
    await repo._conn.executemany(
        "INSERT INTO samples (router_id, wan_id, ts, connected, kind, rtt_ms, cutoff_ms, "
        "tunnel_state) VALUES ('r1', ?, ?, 1, 'cellular', 1500, 500, 'ACTIVE')",
        [(w, (t0 + timedelta(seconds=6 * i)).isoformat())
         for i in range(3) for w in ("1", "4")],
    )
    await repo._conn.commit()

    episodes, _ = await repo.events(
        "r1", t0 - timedelta(minutes=1), t0 + timedelta(hours=1), 1000.0
    )
    # Le garde-fou, pas le routeur : quatre liens également mauvais ne se
    # dépassent pas, et aucun n'a été écarté.
    latences = [e for e in episodes if e["genre"] == "latence haute"]
    assert len(latences) == 2, "les deux liens sont mauvais, les deux sont dits"
    assert not [e for e in episodes if e["genre"] == "latence"]
    await repo.close()


async def test_a_lost_position_is_an_episode_of_the_DEVICE(tmp_path):
    """La position n'appartient à aucun lien : c'est l'équipement qui l'a ou non.

    Mesuré sur la base : 11,9 % des relevés n'en portent aucune, et l'écran
    continuait d'afficher la dernière position connue sans que rien ne dise
    depuis quand elle datait. Le barycentre d'une session en dépend entièrement.
    """
    from datetime import datetime, timedelta

    repo = SampleRepository(str(tmp_path / "s.db"))
    await repo.connect()
    t0 = datetime.fromisoformat("2026-09-01T08:00:00+00:00")
    lignes = []
    for i, lat in enumerate([48.1, 48.1, None, None, 48.1]):
        for w in ("1", "4"):
            lignes.append((w, (t0 + timedelta(seconds=6 * i)).isoformat(), lat))
    await repo._conn.executemany(
        "INSERT INTO samples (router_id, wan_id, ts, connected, kind, site_lat) "
        "VALUES ('r1', ?, ?, 1, 'cellular', ?)",
        lignes,
    )
    await repo._conn.commit()

    episodes, _ = await repo.events(
        "r1", t0 - timedelta(minutes=1), t0 + timedelta(hours=1), 1000.0
    )
    positions = [e for e in episodes if e["genre"].startswith("position")]
    assert [e["genre"] for e in positions] == ["position perdue", "position retrouvée"]
    # Aucun lien : l'attribuer à un modem serait faux.
    assert all(e["wan_id"] == "" for e in positions)
    await repo.close()


async def test_the_ROUTER_own_words_become_episodes_between_two_transitions(tmp_path):
    """Le message d'état n'apparaissait qu'en pièce jointe d'une coupure.

    Or le routeur narre aussi ce qui se passe ENTRE deux transitions, et c'est
    là qu'il est le plus utile. Câble WAN débranché puis rebranché le 11
    septembre, relevé sur le matériel :

        21:21:25  Connected         -> No Cable Detected   (coupure)
        21:33:15  No Cable Detected -> Connecting...       (invisible)
        21:33:20  Connecting...     -> Detecting MTU...    (invisible)
        21:33:30  Detecting MTU...  -> Connected           (retour)

    Le journal ne montrait que les deux extrémités et concluait à douze minutes
    de coupure. Le câble était rebranché à 21 h 33 min 15 s : les quinze
    dernières secondes sont une négociation d'adresse et de MTU.
    """
    from datetime import datetime, timedelta

    repo = SampleRepository(str(tmp_path / "s.db"))
    await repo.connect()
    t0 = datetime.fromisoformat("2026-09-11T21:21:20+00:00")
    # Les douze minutes de câble débranché sont RELEVÉES, toutes les cinq
    # secondes, comme sur le matériel : un trou de supervision n'est pas un
    # voisinage, et le journal ne compare plus deux relevés séparés d'un trou.
    releves = [
        (0, 1, "Connected"),
        *[(t, 0, "No Cable Detected") for t in range(5, 715, 5)],
        (715, 0, "Connecting..."),
        (720, 0, "Detecting MTU..."),
        (730, 1, "Connected"),
    ]
    await repo._conn.executemany(
        "INSERT INTO samples (router_id, wan_id, ts, connected, kind, status_message) "
        "VALUES ('r1', '1', ?, ?, 'ethernet', ?)",
        [((t0 + timedelta(seconds=s)).isoformat(), c, m) for s, c, m in releves],
    )
    await repo._conn.commit()

    episodes, _ = await repo.events(
        "r1", t0 - timedelta(minutes=1), t0 + timedelta(hours=1), 1000.0
    )
    signalements = sorted(
        (e for e in episodes if e["genre"] == "signalement"), key=lambda e: e["ts"]
    )
    assert [e["status_message"] for e in signalements] == [
        "No Cable Detected → Connecting...",
        "Connecting... → Detecting MTU...",
    ]
    # Les deux transitions portent DÉJÀ le message dans leur propre colonne : en
    # refaire des signalements doublerait chaque coupure et chaque retour.
    assert len(signalements) == 2, "ni la coupure ni le retour ne sont redoublés"
    await repo.close()


async def test_a_label_that_only_varies_on_an_up_link_is_not_a_signalement(tmp_path):
    """Dome Pro Duo 8.5.1 : « Connected to Orange » ↔ « Connected to Orange F »,
    lien debout, rien ne change — deux « Signalements » en cinq minutes. Un
    vrai changement de réseau, lui, reste dit ; et sur un lien À TERRE, chaque
    message dit pourquoi, donc tous restent."""
    from datetime import datetime, timedelta

    repo = SampleRepository(str(tmp_path / "s.db"))
    await repo.connect()
    t0 = datetime.fromisoformat("2026-09-24T14:30:00+00:00")
    releves = [
        ("2", 0, 1, "Connected to Orange F"), ("2", 5, 1, "Connected to Orange"),
        ("2", 10, 1, "Connected to Orange F"), ("2", 15, 1, "Connected to SFR"),
        ("1", 0, 0, "No Cable"), ("1", 5, 0, "No Cable Detected"),
    ]
    await repo._conn.executemany(
        "INSERT INTO samples (router_id, wan_id, ts, connected, kind, status_message) "
        "VALUES ('r1', ?, ?, ?, 'cellular', ?)",
        [(w, (t0 + timedelta(seconds=t)).isoformat(), c, m) for w, t, c, m in releves],
    )
    await repo._conn.commit()
    episodes, _ = await repo.events("r1", t0 - timedelta(minutes=1), t0 + timedelta(hours=1), 1000.0)
    dits = sorted((e["wan_id"], e["status_message"]) for e in episodes if e["genre"] == "signalement")
    assert dits == [
        ("1", "No Cable → No Cable Detected"),
        ("2", "Connected to Orange F → Connected to SFR"),
    ]
    await repo.close()


async def test_a_switch_to_standby_is_not_a_micro_outage(tmp_path):
    """Scénario 3 de la revue : WAN passé en secours. Le routeur publie
    « Standby » et remet l'uptime à 0, lien toujours connecté : le journal
    écrivait « micro-coupure », le brassage en comptait une. Une manœuvre de
    l'exploitant n'est pas une panne. Une vraie micro-coupure reste comptée."""
    from datetime import datetime, timedelta

    repo = SampleRepository(str(tmp_path / "s.db"))
    await repo.connect()
    t0 = datetime.fromisoformat("2026-09-24T16:08:30+00:00")
    releves = [
        ("3", 0, 78, "Connected to SFR"), ("3", 5, 0, "Standby"),     # entrée en secours
        ("3", 10, 40, "Standby"), ("3", 15, 0, "Connected to SFR"),   # sortie du secours
        ("2", 0, 500, "Connected to Orange"), ("2", 5, 505, "Connected to Orange"),
        ("2", 10, 2, "Connected to Orange"),                          # vraie micro-coupure
    ]
    await repo._conn.executemany(
        "INSERT INTO samples (router_id, wan_id, ts, connected, kind, uptime_s, status_message) "
        "VALUES ('r1', ?, ?, 1, 'cellular', ?, ?)",
        [(w, (t0 + timedelta(seconds=t)).isoformat(), u, m) for w, t, u, m in releves],
    )
    await repo._conn.commit()
    episodes, brassage = await repo.events("r1", t0 - timedelta(minutes=1), t0 + timedelta(hours=1), 1000.0)
    micro = sorted(e["wan_id"] for e in episodes if e["genre"] == "micro-coupure")
    assert micro == ["2"], "le passage en secours n'est pas une micro-coupure"
    par_lien = {b["wan_id"]: b for b in brassage}
    assert par_lien["3"]["micro"] == 0
    assert par_lien["2"]["micro"] == 1
    await repo.close()


async def test_journal_and_sheet_agree_on_a_padded_standby(tmp_path):
    """Une ancienne ligne écrite « ␣Standby␣ » : la fiche de session y voyait
    un passage en secours (elle retirait les blancs), le journal et le brassage
    une micro-coupure (ils ne les retiraient pas). Les deux suivent maintenant
    les mêmes règles, et la fiche de la même fenêtre rend le même compte."""
    from datetime import datetime, timedelta

    from app.models import Session
    from app.sessions import build_summary

    repo = SampleRepository(str(tmp_path / "s.db"))
    await repo.connect()
    t0 = datetime.fromisoformat("2026-09-24T16:08:30+00:00")
    releves = [("3", 0, 78, "Connected to SFR"), ("3", 5, 0, " Standby "),
               ("3", 10, 40, "\tSTANDBY"), ("3", 15, 0, "Connected to SFR")]
    await repo._conn.executemany(
        "INSERT INTO samples (router_id, wan_id, ts, connected, kind, uptime_s, status_message) "
        "VALUES ('r1', ?, ?, 1, 'cellular', ?, ?)",
        [(w, (t0 + timedelta(seconds=t)).isoformat(), u, m) for w, t, u, m in releves],
    )
    await repo._conn.commit()
    debut, fin = t0 - timedelta(minutes=1), t0 + timedelta(hours=1)
    episodes, brassage = await repo.events("r1", debut, fin, 1000.0)
    assert [e for e in episodes if e["genre"] == "micro-coupure"] == []
    assert brassage[0]["micro"] == 0
    async with repo._conn.execute(
        "SELECT * FROM samples WHERE router_id = 'r1' ORDER BY ts"
    ) as cursor:
        lignes = [dict(r) for r in await cursor.fetchall()]
    session = Session(id=1, router_id="r1", started_at=debut, last_seen=fin, samples=4)
    assert build_summary(session, lignes, None).links[0].outages == 0
    await repo.close()


async def test_a_cell_change_across_an_outage_is_counted(tmp_path):
    """Scénario 2 de la revue : bascule de SIM, 76 s de coupure, retour sur un
    autre opérateur, une autre cellule, une autre station — le brassage
    comptait « 0 / 0 » : la cellule d'avant était lue sur un relevé de coupure,
    sans cellule. On compare à la DERNIÈRE cellule connue, tant qu'aucun trou de
    supervision ne sépare les deux relevés."""
    from datetime import datetime, timedelta

    repo = SampleRepository(str(tmp_path / "s.db"))
    await repo.connect()
    t0 = datetime.fromisoformat("2026-09-24T16:05:00+00:00")
    releves = [("3", 0, 1, 25698582, 100385)]
    releves += [("3", 5 * i, 0, None, None) for i in range(1, 17)]           # 76 s de coupure, observée
    releves += [("3", 90, 1, 2227220, 8700), ("3", 95, 1, 2227220, 8700)]
    # Un autre lien : changement de cellule de part et d'autre d'un TROU de
    # supervision (aucun relevé pendant 5 min) — non compté, comme avant.
    releves += [("2", 0, 1, 111, 1), ("2", 300, 1, 222, 2)]
    await repo._conn.executemany(
        "INSERT INTO samples (router_id, wan_id, ts, connected, kind, cell_id, enb_id) "
        "VALUES ('r1', ?, ?, ?, 'cellular', ?, ?)",
        [(w, (t0 + timedelta(seconds=t)).isoformat(), c, cell, enb) for w, t, c, cell, enb in releves],
    )
    await repo._conn.commit()
    _, brassage = await repo.events("r1", t0 - timedelta(minutes=1), t0 + timedelta(hours=1), 1000.0)
    par_lien = {b["wan_id"]: b for b in brassage}
    assert (par_lien["3"]["cellules"], par_lien["3"]["stations"]) == (1, 1)
    assert (par_lien["2"]["cellules"], par_lien["2"]["stations"]) == (0, 0)
    await repo.close()


async def test_an_operator_change_is_an_episode_of_the_log(tmp_path):
    """R-2.3, scénario 2 de la revue : bascule de SIM, 76 s de coupure, retour
    sur un autre réseau (208-01 → 208-10). Le journal n'écrivait qu'une coupure
    et un retour. Seul le code réseau fait foi : un relevé sans code est
    ignoré, un libellé qui varie à code égal n'est pas un changement, et un
    trou de supervision remet la comparaison à zéro — comme le brassage."""
    from datetime import datetime, timedelta

    repo = SampleRepository(str(tmp_path / "s.db"))
    await repo.connect()
    t0 = datetime.fromisoformat("2026-09-24T16:05:00+00:00")
    releves = [("3", 0, 1, "Orange F", "208-01"),
               ("3", 3, 1, "Orange", None),                  # sans code : ignoré
               ("3", 5, 1, "Orange", "208-01")]              # libellé seul : rien
    releves += [("3", 5 + 5 * i, 0, None, None) for i in range(1, 17)]   # coupure observée
    # Le retour se publie d'abord SANS code (vu en direct, R-3.4), puis avec :
    # c'est le premier relevé qui porte le nouveau code qui date le changement.
    releves += [("3", 90, 1, "Mobile S Mobile", None), ("3", 95, 1, "Mobile S Mobile", "208-10")]
    # Un modem qui s'accroche un instant à un autre réseau PENDANT la coupure,
    # puis revient sur le sien : aucun changement pour le lien connecté.
    releves += [("4", 0, 1, "Orange F", "208-01"), ("4", 5, 0, "Free", "208-15"),
                ("4", 10, 1, "Orange F", "208-01")]
    # Un autre lien : changement de réseau de part et d'autre d'un TROU de
    # supervision (5 min sans relevé) — non daté, on ne regardait pas.
    releves += [("2", 0, 1, "SFR", "208-10"), ("2", 300, 1, "Orange F", "208-01")]
    await repo._conn.executemany(
        "INSERT INTO samples (router_id, wan_id, ts, connected, kind, operator, plmn) "
        "VALUES ('r1', ?, ?, ?, 'cellular', ?, ?)",
        [(w, (t0 + timedelta(seconds=t)).isoformat(), c, op, plmn) for w, t, c, op, plmn in releves],
    )
    await repo._conn.commit()
    episodes, _ = await repo.events("r1", t0 - timedelta(minutes=1), t0 + timedelta(hours=1), 1000.0)
    changements = [e for e in episodes if e["genre"] == "changement d'opérateur"]
    assert [(e["wan_id"], e["ts"], e["status_message"]) for e in changements] == [
        ("3", (t0 + timedelta(seconds=95)).isoformat(),
         "Orange (208-01) → SFR (208-10)"),
    ]
    await repo.close()


async def test_a_link_can_leave_the_bond_while_staying_connected(tmp_path):
    """Un lien peut quitter le bond sans quitter le réseau.

    Mesuré : sept bascules d'état de tunnel sur le matériel, dont trois SANS
    transition de connexion — les cinq secondes du 11 septembre où le lien était
    revenu mais pas encore le tunnel, et une désactivation en configuration le
    30 août. Rien ne le disait, alors que le module pose depuis toujours qu'un
    lien écarté du bond ne transporte rien.
    """
    from datetime import datetime, timedelta

    repo = SampleRepository(str(tmp_path / "s.db"))
    await repo.connect()
    t0 = datetime.fromisoformat("2026-08-30T15:09:48+00:00")
    etats = [(0, "ACTIVE"), (5, "WAN_DISABLED"), (25, "WAN_DOWN"), (30, "ACTIVE")]
    await repo._conn.executemany(
        "INSERT INTO samples (router_id, wan_id, ts, connected, kind, tunnel_state) "
        "VALUES ('r1', '7', ?, 1, 'cellular', ?)",
        [((t0 + timedelta(seconds=s)).isoformat(), e) for s, e in etats],
    )
    await repo._conn.commit()

    episodes, _ = await repo.events(
        "r1", t0 - timedelta(minutes=1), t0 + timedelta(hours=1), 1000.0
    )
    bond = sorted(
        (e for e in episodes if "bond" in e["genre"]), key=lambda e: e["ts"]
    )
    assert [e["genre"] for e in bond] == ["hors du bond", "de retour dans le bond"]
    # WAN_DISABLED n'est pas une panne mais une DÉCISION : sans ce mot, on
    # cherche un incident là où quelqu'un a désactivé un WAN.
    assert bond[0]["status_message"] == "WAN_DISABLED"
    # `WAN_DISABLED` -> `WAN_DOWN` reste HORS du bond : deux états écartés à la
    # suite ne font pas deux épisodes, sinon un tunnel qui cherche sa route en
    # produirait un par tentative.
    assert len(bond) == 2
    # Et le retour ne porte pas « ACTIVE » : la colonne des mots du routeur
    # deviendrait une colonne de remplissage.
    assert bond[1]["status_message"] is None
    await repo.close()


async def test_a_link_already_out_of_the_bond_at_the_first_reading_is_listed(tmp_path):
    """Vu le 30/09 sur un MAX HD2 : la SIM 4, debout mais hors du bond dès le
    premier relevé, faisait lever l'alerte « Lien 4 hors du bond » — et le
    journal n'en disait rien, faute de bascule observée. Relu plus tard, le
    stationnement ne montrait pas un lien sorti du bond avant le début de la
    supervision. L'état d'ouverture est un fait, daté du premier relevé.
    """
    from datetime import datetime, timedelta

    repo = SampleRepository(str(tmp_path / "s.db"))
    await repo.connect()
    t0 = datetime.fromisoformat("2026-09-30T14:49:00+00:00")
    lignes = []
    for i in range(4):
        ts = (t0 + timedelta(seconds=6 * i)).isoformat()
        lignes += [("4", ts, 1, "FAILURE"), ("5", ts, 1, "ACTIVE"),
                   # Un filaire sans câble ni tunnel : ni debout, ni concerné.
                   ("1", ts, 0, None)]
    await repo._conn.executemany(
        "INSERT INTO samples (router_id, wan_id, ts, connected, kind, tunnel_state) "
        "VALUES ('r1', ?, ?, ?, 'cellular', ?)", lignes,
    )
    await repo._conn.commit()

    episodes, _ = await repo.events("r1", t0, t0 + timedelta(minutes=5), 1000.0)
    bond = [e for e in episodes if "bond" in e["genre"]]
    assert [(e["genre"], e["wan_id"], e["ts"]) for e in bond] == [
        ("hors du bond", "4", t0.isoformat())
    ]
    # La cause reste le mot du routeur, et la ligne dit qu'elle n'est pas une bascule.
    assert bond[0]["status_message"] == "FAILURE · dès le premier relevé"
    await repo.close()


async def test_a_link_disabled_by_the_operator_is_a_decision_not_a_cut(tmp_path):
    """C-20, vu en service le 17/10 : la SIM 4 désactivée à 13:51 entrait au
    journal comme « Coupure — 1 min », en rouge et au premier rang de gravité,
    et le compteur de coupures de la table l'additionnait. Le relevé d'adieu
    (`STATUT_DESACTIVE`) en fait une désactivation ; une vraie chute reste une
    coupure."""
    from datetime import datetime, timedelta

    from app.models import STATUT_DESACTIVE

    repo = SampleRepository(str(tmp_path / "s.db"))
    await repo.connect()
    t0 = datetime.fromisoformat("2026-10-17T11:50:00+00:00")
    lignes = [
        ("4", 0, 1, "Connected to Bouygues"), ("4", 5, 0, STATUT_DESACTIVE),
        ("4", 10, 0, "Connecting..."), ("4", 15, 1, "Connected to Bouygues"),
        ("5", 0, 1, "Connected to Orange"), ("5", 5, 0, "No Signal"),
        ("5", 10, 1, "Connected to Orange"),
    ]
    await repo._conn.executemany(
        "INSERT INTO samples (router_id, wan_id, ts, connected, kind, status_message) "
        "VALUES ('r1', ?, ?, ?, 'cellular', ?)",
        [(w, (t0 + timedelta(seconds=s)).isoformat(), c, m) for w, s, c, m in lignes],
    )
    await repo._conn.commit()

    episodes, brassage = await repo.events("r1", t0, t0 + timedelta(minutes=5), 1000.0)
    genres = {(e["wan_id"], e["genre"]) for e in episodes}
    assert ("4", "désactivation") in genres
    assert ("4", "coupure") not in genres, "une décision de l'exploitant listée en panne"
    assert ("5", "coupure") in genres, "une vraie chute reste une coupure"
    coupures = {b["wan_id"]: b["coupures"] for b in brassage}
    assert coupures == {"4": 0, "5": 1}
    await repo.close()


async def test_a_bond_exit_that_comes_with_a_disconnection_is_not_listed_twice(tmp_path):
    """`bascule_bond` sans `connected = connecte_avant` ne faisait tomber aucun
    test : une coupure emportait aussi un « hors du bond », et le retour un
    « de retour dans le bond » — deux lignes pour un fait, deux fois."""
    from datetime import datetime, timedelta

    repo = SampleRepository(str(tmp_path / "s.db"))
    await repo.connect()
    t0 = datetime.fromisoformat("2026-08-30T15:09:48+00:00")
    etats = [(0, 1, "ACTIVE", 100), (5, 0, "WAN_DOWN", None), (10, 1, "ACTIVE", 3)]
    await repo._conn.executemany(
        "INSERT INTO samples (router_id, wan_id, ts, connected, kind, tunnel_state, uptime_s) "
        "VALUES ('r1', '7', ?, ?, 'cellular', ?, ?)",
        [((t0 + timedelta(seconds=s)).isoformat(), c, e, up) for s, c, e, up in etats],
    )
    await repo._conn.commit()
    episodes, _ = await repo.events(
        "r1", t0 - timedelta(minutes=1), t0 + timedelta(hours=1), 1000.0
    )
    genres = sorted(e["genre"] for e in episodes)
    assert genres == ["coupure", "retour"], f"le bond ne double pas la coupure : {genres}"
    await repo.close()


async def test_a_handover_older_than_thirty_seconds_does_not_explain_a_peak(tmp_path):
    """`depuis_bascule` non borné : un changement de cellule d'il y a dix
    minutes « expliquait » un pic. 15,8 % des pics suivent un handover de
    moins de trente secondes ; les autres restent sans explication plutôt
    qu'en recevoir une fausse."""
    from datetime import datetime, timedelta

    repo = SampleRepository(str(tmp_path / "s.db"))
    await repo.connect()
    t0 = datetime.fromisoformat("2026-08-30T15:00:00+00:00")
    # Changement de cellule à t=5 ; pic à t=20 (15 s après) puis à t=600.
    releves = [(0, 100, 30.0), (5, 200, 30.0), (10, 200, 30.0), (15, 200, 30.0),
               (20, 200, 1500.0), (25, 200, 30.0)]
    releves += [(t, 200, 30.0) for t in range(30, 600, 5)]
    releves += [(600, 200, 1500.0), (605, 200, 30.0)]
    await repo._conn.executemany(
        "INSERT INTO samples (router_id, wan_id, ts, connected, kind, tunnel_state, cell_id, rtt_ms) "
        "VALUES ('r1', '4', ?, 1, 'cellular', 'ACTIVE', ?, ?)",
        [((t0 + timedelta(seconds=s)).isoformat(), c, r) for s, c, r in releves],
    )
    await repo._conn.commit()
    episodes, _ = await repo.events(
        "r1", t0 - timedelta(minutes=1), t0 + timedelta(hours=1), 1000.0
    )
    pics = sorted((e for e in episodes if e["genre"] == "latence haute"), key=lambda e: e["debut"])
    assert len(pics) == 2
    assert pics[0]["depuis_bascule"] == 15.0
    assert pics[1]["depuis_bascule"] is None, "un handover de dix minutes n'explique rien"
    await repo.close()


async def test_two_aggregates_of_the_same_slice_merge_without_inventing(tmp_path):
    """`_fusionner_tranche` n'était appelée par aucun test : les compteurs
    s'additionnent, les pires se comparent, et une grandeur que l'un des deux
    n'a pas mesurée ne tire pas l'autre vers le bas."""
    from app.storage.db import _fusionner_tranche

    garde = {"releves": 3, "debout": 3, "portants": 2, "tunnel_connu": 3, "reparation": None,
             "paquets": 100, "sinr_pire": 8.0, "rsrp_pire": None}
    autre = {"releves": 2, "debout": 1, "portants": 0, "tunnel_connu": 1, "reparation": 4,
             "paquets": None, "sinr_pire": 12.0, "rsrp_pire": -101.0}
    _fusionner_tranche(garde, autre)
    assert (garde["releves"], garde["debout"], garde["portants"], garde["tunnel_connu"]) == (5, 4, 2, 4)
    assert garde["reparation"] == 4 and garde["paquets"] == 100, "une absence ne fait pas zéro"
    assert garde["sinr_pire"] == 8.0 and garde["rsrp_pire"] == -101.0
    # Deux absences restent une absence — pas un zéro.
    aucune = {"releves": 1, "reparation": None, "paquets": None, "sinr_pire": None, "rsrp_pire": None}
    _fusionner_tranche(aucune, {"releves": 1, "reparation": None, "paquets": None,
                                "sinr_pire": None, "rsrp_pire": None})
    assert aucune["reparation"] is None and aucune["paquets"] is None
    assert aucune["sinr_pire"] is None and aucune["releves"] == 2


async def test_a_latency_exclusion_is_not_counted_twice_as_a_bond_exit(tmp_path):
    """Un lien écarté par la latence reste `ACTIVE` — mesuré sur la totalité des
    relevés au-delà du seuil. Les deux détections ne peuvent donc pas se
    recouvrir, et ce test le fige : le jour où le routeur changerait d'état en
    même temps qu'il écarte, le journal doublerait chaque épisode de latence.
    """
    from datetime import datetime, timedelta

    repo = SampleRepository(str(tmp_path / "s.db"))
    await repo.connect()
    t0 = datetime.fromisoformat("2026-09-01T08:00:00+00:00")
    await repo._conn.executemany(
        "INSERT INTO samples (router_id, wan_id, ts, connected, kind, rtt_ms, cutoff_ms, "
        "tunnel_state) VALUES ('r1', ?, ?, 1, 'cellular', ?, 500, 'ACTIVE')",
        [
            (w, (t0 + timedelta(seconds=6 * i)).isoformat(), rtt)
            for i in range(4)
            for w, rtt in (("1", 6.0), ("4", 600.0))
        ],
    )
    await repo._conn.commit()

    episodes, _ = await repo.events(
        "r1", t0 - timedelta(minutes=1), t0 + timedelta(hours=1), 1000.0
    )
    assert [e for e in episodes if e["genre"] == "latence"]
    assert not [e for e in episodes if "bond" in e["genre"]]
    await repo.close()


async def test_a_rate_whose_DIRECTION_is_unknown_has_no_denominator(tmp_path):
    """`loss_percent` est le pire des deux sens. Sans savoir lequel, on ignore
    sur combien de paquets il a été établi — et un taux sans dénominateur ne se
    publie pas.

    Le cas existe : les lignes écrites avant l'ajout des colonnes par sens
    portent un taux et deux compteurs, sans rien qui dise lequel a décroché. Le
    dénominateur rendu était alors `MAX` des deux, c'est-à-dire une réponse
    inventée à une question sans réponse.
    """
    from datetime import datetime, timedelta

    repo = SampleRepository(str(tmp_path / "s.db"))
    await repo.connect()
    t0 = datetime.fromisoformat("2026-09-01T08:00:00+00:00")
    await repo._conn.execute(
        "INSERT INTO samples (router_id, wan_id, ts, connected, kind, "
        "loss_percent, packets_up, packets_down) "
        "VALUES ('r1', '4', ?, 1, 'cellular', 4.71, 249, 191)",
        (t0.isoformat(),),
    )
    await repo._conn.commit()

    points, _ = await repo.history(
        "r1", "4", "loss_percent", debut=t0 - timedelta(minutes=1), fin=t0 + timedelta(hours=1)
    )
    assert len(points) == 1
    assert points[0].value == 4.71
    assert points[0].denominator is None, "aucun des deux compteurs n'est LE dénominateur"
    await repo.close()


async def test_the_ROUTER_exclusion_and_our_GUARD_are_two_different_episodes(tmp_path):
    """« Écarté par la latence » est une action du ROUTEUR, constatée.

    Le garde-fou absolu de l'outil est autre chose : il se déclenche sans que le
    firmware ait rien fait. Les deux portaient la même étiquette, et l'écran
    affirmait donc une exclusion qui n'avait pas eu lieu — mesuré, les dix-huit
    relevés de la base au-delà de mille millisecondes ont TOUS un `cutoff_ms`
    nul, donc aucun n'a jamais été écarté par personne.
    """
    from datetime import datetime, timedelta

    repo = SampleRepository(str(tmp_path / "s.db"))
    await repo.connect()
    t0 = datetime.fromisoformat("2026-09-01T08:00:00+00:00")

    # Deux liens du même cycle, un seuil de bond connu, un écart qui le franchit :
    # le routeur écarte. Le lien lent reste sous le garde-fou absolu.
    await repo._conn.executemany(
        "INSERT INTO samples (router_id, wan_id, ts, connected, kind, rtt_ms, cutoff_ms, "
        "tunnel_state) VALUES ('r1', ?, ?, 1, 'cellular', ?, 500, 'ACTIVE')",
        [
            (w, (t0 + timedelta(seconds=6 * i)).isoformat(), rtt)
            for i in range(4)
            for w, rtt in (("1", 6.0), ("4", 700.0))
        ],
    )
    # Un lien seul, sans seuil, au-delà du garde-fou : personne ne l'a écarté.
    await repo._conn.executemany(
        "INSERT INTO samples (router_id, wan_id, ts, connected, kind, rtt_ms, tunnel_state) "
        "VALUES ('r1', '9', ?, 1, 'cellular', 1500.0, 'ACTIVE')",
        [((t0 + timedelta(hours=1, seconds=6 * i)).isoformat(),) for i in range(3)],
    )
    await repo._conn.commit()

    episodes, _ = await repo.events(
        "r1", t0 - timedelta(minutes=1), t0 + timedelta(hours=2), 1000.0
    )
    ecartes = [e for e in episodes if e["genre"] == "latence"]
    hauts = [e for e in episodes if e["genre"] == "latence haute"]

    assert [e["wan_id"] for e in ecartes] == ["4"], "seul le lien écarté par le routeur"
    assert [e["wan_id"] for e in hauts] == ["9"], "seul le lien retenu par le garde-fou"
    await repo.close()


async def test_a_slice_is_never_finer_than_the_polling_cadence(tmp_path):
    """Une part plus fine que l'intervalle entre deux relevés est vide PAR
    CONSTRUCTION — et l'écran l'attribuait au superviseur.

    Le découpage valait toujours deux cents parts, quelle que soit la durée. Sur
    une fenêtre de deux minutes et une cadence de 5,4 s, chaque part durait 0,6 s :
    mesuré sur le matériel, 178 parts vides sur 200, et l'écran annonçait « 89 %
    de la fenêtre n'a reçu aucun relevé — c'est la supervision qui ne tournait
    pas » sur une fenêtre intégralement couverte. Tout début de stationnement
    était touché.
    """
    from datetime import datetime, timedelta

    repo = SampleRepository(str(tmp_path / "s.db"))
    await repo.connect()
    t0 = datetime.fromisoformat("2026-09-01T08:00:00+00:00")
    # Deux minutes de relevés, un toutes les 5 s : couverture intégrale.
    await repo._conn.executemany(
        "INSERT INTO samples (router_id, wan_id, ts, connected, kind) "
        "VALUES ('r1', '4', ?, 1, 'cellular')",
        [((t0 + timedelta(seconds=5 * i)).isoformat(),) for i in range(24)],
    )
    await repo._conn.commit()
    fin = t0 + timedelta(seconds=120)

    lignes, largeur, parts = await repo.timeline("r1", t0, fin, periode=5.4)
    assert largeur >= 5.4, "une part ne peut pas être plus fine que la cadence"
    assert parts <= 23, "le nombre de parts suit la largeur retenue"
    # Aucun TROU dans les parts couvertes : la fenêtre est renseignée de bout en
    # bout, et rien n'est à imputer au superviseur. (La dernière part déborde le
    # dernier relevé de cinq secondes — c'est la fenêtre demandée qui est plus
    # longue que la collecte, pas la collecte qui a manqué.)
    vues = sorted({l["tranche"] for l in lignes})
    assert vues == list(range(len(vues))), "une part vide au milieu d'une fenêtre couverte"
    assert len(vues) >= parts - 1

    # Sans cadence connue, le plancher d'une seconde subsiste : une part plus
    # fine n'a aucun sens sur des horodatages écrits à la milliseconde.
    _, sans, _ = await repo.timeline("r1", t0, fin)
    assert sans >= 1.0
    await repo.close()


async def test_an_irregular_cadence_leaves_no_empty_slice(tmp_path):
    """La période publiée est une MOYENNE ; une part n'est sûre de contenir un
    relevé que si elle est plus large que le plus grand ÉCART entre deux.

    Mesuré sur un MAX HD2 en HTTP (30/09) : écart médian 5,4 s, un sur cinq
    près de 10 s, et une période lissée qui retombe à 5,8 s après une suite de
    cycles courts. Des parts larges d'une période restaient vides — 8 sur 68 —
    sur une fenêtre intégralement couverte : l'écran annonçait « 18 % de la
    fenêtre sans relevé », et chaque part vide coupait en deux un bond resté à
    un lien sur trois — « redondance réduite 9 fois » en huit minutes d'un état
    qui n'avait pas bougé.
    """
    from datetime import datetime, timedelta

    repo = SampleRepository(str(tmp_path / "s.db"))
    await repo.connect()
    t0 = datetime.fromisoformat("2026-09-01T08:00:00+00:00")
    # Les écarts relevés ce jour-là, dans l'ordre.
    ecarts = [
        9.9, 5.3, 5.3, 5.4, 5.5, 5.2, 5.5, 5.3, 8.5, 5.4, 9.9, 9.6, 5.5, 5.4, 4.4,
        5.3, 5.3, 5.4, 7.6, 5.3, 9.9, 5.4, 5.3, 9.8, 5.3, 5.5, 5.5, 5.4, 8.0, 10.0,
        5.4, 5.3, 5.3, 5.3, 5.3, 9.9, 5.5, 5.3, 7.9, 10.2, 5.3, 5.3, 5.5, 5.3, 5.4,
        5.6, 5.4, 9.9, 7.6, 10.0, 5.5, 5.5, 5.4, 5.6, 5.4, 5.4, 5.4, 5.5, 7.6, 14.6,
    ]
    instants, t = [], t0
    for e in ecarts:
        instants.append(t)
        t += timedelta(seconds=e)
    await repo._conn.executemany(
        "INSERT INTO samples (router_id, wan_id, ts, connected, kind) "
        "VALUES ('r1', '4', ?, 1, 'cellular')",
        [(i.isoformat(),) for i in instants],
    )
    await repo._conn.commit()
    fin = instants[-1]

    lignes, _, parts = await repo.timeline("r1", t0, fin, periode=5.8)
    vues = {l["tranche"] for l in lignes}
    assert sorted(set(range(parts)) - vues) == [], "une part vide dans une fenêtre couverte"
    await repo.close()


async def test_a_sample_on_the_upper_bound_does_not_duplicate_its_slice(tmp_path):
    """Un relevé tombant EXACTEMENT sur la borne haute donne une part hors du
    tableau. Ce n'est pas un arrondi : c'est déterministe, et cela arrive à
    chaque relecture d'une session passée, dont `until` vaut le dernier relevé.

    Le rabotage écrasait l'indice sans FUSIONNER : deux lignes sortaient pour la
    même part et le même lien, et la bande comptait ce lien deux fois — de quoi
    faire croire qu'une tranche était complète alors qu'un autre lien y manquait.
    """
    from datetime import datetime, timedelta

    repo = SampleRepository(str(tmp_path / "s.db"))
    await repo.connect()
    t0 = datetime.fromisoformat("2026-09-01T08:00:00+00:00")
    await repo._conn.executemany(
        "INSERT INTO samples (router_id, wan_id, ts, connected, kind, sinr) "
        "VALUES ('r1', '4', ?, 1, 'cellular', ?)",
        [((t0 + timedelta(seconds=10 * i)).isoformat(), 20.0 - i) for i in range(7)],
    )
    await repo._conn.commit()
    fin = t0 + timedelta(seconds=60)  # exactement le dernier relevé

    lignes, _, parts = await repo.timeline("r1", t0, fin, periode=10.0)
    cles = [(l["wan_id"], l["tranche"]) for l in lignes]
    assert len(cles) == len(set(cles)), "deux lignes pour une même part et un même lien"
    assert all(0 <= l["tranche"] < parts for l in lignes)
    # Et la fusion n'a rien perdu : les sept relevés sont comptés.
    assert sum(l["releves"] for l in lignes) == 7
    # Le pire de la part fusionnée est bien le pire des deux.
    derniere = max(lignes, key=lambda l: l["tranche"])
    assert derniere["sinr_pire"] == 14.0
    await repo.close()


async def test_the_time_bound_is_usable_by_an_index(tmp_path):
    """Les requêtes qui ne nomment AUCUN lien doivent pouvoir chercher par date.

    `timeline()`, `session_rows()` et les quatre requêtes d'`events()` portent sur
    tous les liens d'un routeur : du seul index `(router_id, wan_id, ts)`, elles
    ne pouvaient employer que le préfixe `router_id`, et balayaient donc tous les
    relevés de l'équipement quelle que soit la fenêtre demandée. Mesuré sur
    cinquante mille relevés, une fenêtre d'une minute coûtait autant qu'une
    fenêtre d'une heure — 5,9 ms — parce que la borne temporelle n'entrait pas
    dans la recherche. Avec l'index, 0,1 ms ; et `events()` sur une fenêtre
    étroite passe de 61,6 ms à 12,8 ms.
    """
    from datetime import datetime, timedelta

    repo = SampleRepository(str(tmp_path / "s.db"))
    await repo.connect()
    t0 = datetime.fromisoformat("2026-09-01T08:00:00+00:00")
    await repo._conn.executemany(
        "INSERT INTO samples (router_id, wan_id, ts, connected, kind) "
        "VALUES ('r1', '4', ?, 1, 'cellular')",
        [((t0 + timedelta(seconds=5 * i)).isoformat(),) for i in range(200)],
    )
    await repo._conn.commit()

    async with repo._conn.execute(
        "EXPLAIN QUERY PLAN SELECT wan_id, ts FROM samples "
        "WHERE router_id = ? AND ts >= ? AND ts <= ?",
        ("r1", t0.isoformat(), (t0 + timedelta(minutes=1)).isoformat()),
    ) as cursor:
        plan = " ".join(str(row[-1]) for row in await cursor.fetchall())

    assert "idx_samples_router_ts" in plan, f"la borne temporelle n'est pas cherchée : {plan}"
    assert "ts>" in plan and "ts<" in plan, f"les deux bornes doivent entrer dans l'index : {plan}"
    await repo.close()


async def test_the_latency_islands_never_scan_the_whole_history(tmp_path):
    """La CTE des îlots est lue deux fois ; pour la seconde, SQLite choisissait
    l'index `(router_id, wan_id, ts)` sur son seul préfixe et parcourait tout
    l'historique de l'équipement — 10,7 ms pour une minute, un plancher qui
    croît avec la base. Chaque accès à `samples` doit entrer par la fenêtre."""
    from datetime import datetime, timedelta

    from app.regles_coupure import TROU_MAX_MS
    from app.storage.db import _SQL_ILOTS

    repo = SampleRepository(str(tmp_path / "s.db"))
    await repo.connect()
    t0 = datetime.fromisoformat("2026-09-01T08:00:00+00:00")
    await repo._conn.executemany(
        "INSERT INTO samples (router_id, wan_id, ts, connected, kind, rtt_ms) "
        "VALUES ('r1', '4', ?, 1, 'cellular', 30.0)",
        [((t0 + timedelta(seconds=5 * i)).isoformat(),) for i in range(200)],
    )
    await repo._conn.commit()
    bornes = ("r1", t0.isoformat(), (t0 + timedelta(minutes=1)).isoformat())
    async with repo._conn.execute(
        "EXPLAIN QUERY PLAN " + _SQL_ILOTS, (*bornes, 500.0, TROU_MAX_MS, *bornes, TROU_MAX_MS)
    ) as cursor:
        acces = [str(r[-1]) for r in await cursor.fetchall() if "samples" in str(r[-1])]
    assert acces, "le plan doit lire samples"
    for ligne in acces:
        assert "ts>" in ligne and "ts<" in ligne, f"accès sans borne temporelle : {ligne}"
    await repo.close()


async def test_the_handover_table_of_the_latency_islands_is_computed_once(tmp_path):
    """`bascules` doit être MATÉRIALISÉE, et ce n'est pas une élégance.

    Elle n'est lue que par la sous-requête corrélée du SELECT final, et pour
    une table commune lue une seule fois SQLite la recalcule à CHAQUE
    évaluation — donc une fois par épisode, fenêtrage sur toute la session
    compris. Mesuré sur une session de dix heures à cinq liens (29 525 relevés,
    17 épisodes) : 2 303 ms pour cette requête, 428 ms matérialisée, mêmes
    lignes ; et `events()` entier sur la plus longue session de la base,
    10 946 ms → 836 ms. Relue toutes les trente secondes par l'écran, sur la
    même connexion que le collecteur.

    Le plan est demandé à SQLite : sans le mot, il n'y a pas de ligne
    « MATERIALIZE bascules », et le coût revient sans qu'un seul test tombe.
    """
    from datetime import datetime

    from app.regles_coupure import TROU_MAX_MS
    from app.storage.db import _SQL_ILOTS

    # `async with`, et non connect/close : ce test a été vu tomber par mutation,
    # et un test qui tombe entre les deux laisse le thread vivant — voir ci-dessous.
    async with SampleRepository(str(tmp_path / "s.db")) as repo:
        t0 = datetime.fromisoformat("2026-09-01T08:00:00+00:00")
        bornes = ("r1", t0.isoformat(), t0.isoformat())
        async with repo._conn.execute(
            "EXPLAIN QUERY PLAN " + _SQL_ILOTS, (*bornes, 500.0, TROU_MAX_MS, *bornes, TROU_MAX_MS)
        ) as cursor:
            plan = [str(row[-1]) for row in await cursor.fetchall()]
        assert "MATERIALIZE bascules" in plan, plan


async def test_a_repository_closes_itself_when_used_as_a_context(tmp_path):
    """Oublier de fermer ne coûte pas rien : `aiosqlite` fait tourner un THREAD
    de travail par connexion, et ce thread n'est pas un daemon.

    Il attend sur sa file l'ordre d'arrêt que `close()` produit, et rien d'autre
    ne l'en sort. Tant qu'une référence à la connexion survit, l'interpréteur ne
    peut pas SORTIR : le processus reste vivant, muet, après avoir fait tout son
    travail. Constaté cinq fois le même jour, dont un diagnostic d'une ligne qui
    a tenu la base de production ouverte en écriture deux heures et vingt
    minutes, à côté du serveur qui y écrivait.

    Le déclencheur, reproduit trois fois le 19 septembre : une EXCEPTION entre
    `connect()` et `close()`. Un script court sans exception sort ; le même
    script qui lève garde sa trace, la trace garde le cadre, le cadre garde le
    dépôt, le dépôt garde la connexion, et la connexion garde son thread. Sous
    pytest, c'est un test qui TOMBE après un `connect()` nu — et ses échecs
    sont précisément ceux qu'on relit. Les tests qui touchent la base passent
    donc par `async with`.

    Ce test n'éprouve pas le blocage lui-même. Il éprouve ce que ces deux
    méthodes garantissent : la fermeture a lieu, y compris quand le bloc se
    termine par une exception, qui est le cas où l'on oublie de nettoyer.
    """
    chemin = str(tmp_path / "s.db")

    async with SampleRepository(chemin) as repo:
        assert repo._conn is not None
        await _remplir(repo, 3)
        assert repo._conn is not None

    assert repo._conn is None, "la sortie du bloc ferme, même sans y penser"

    # Et l'exception ne fait pas fuir la connexion : c'est le cas qui compte,
    # puisque c'est en échouant qu'un script oublie de nettoyer.
    depot = SampleRepository(chemin)
    with pytest.raises(ValueError):
        async with depot:
            raise ValueError("échec en cours de route")
    assert depot._conn is None


async def test_bond_carried_since_distingue_un_tunnel_jamais_monte(repo):
    """Un événement contre un état : le bond a-t-il jamais porté sur cet
    équipement ? Le MBX oui, des semaines durant ; le MAX HD2 jamais.

    C'est ce que le poller lit une fois pour décider si les règles du bond ont
    un bond à surveiller. Une seule ligne ACTIVE suffit, et ce fait ne fuit pas
    d'un équipement à l'autre.
    """
    def tombe(router_id: str) -> RadioSample:
        return RadioSample(
            router_id=router_id, router_name=router_id,
            wan=WanRadioStatus(wan_id="4", wan_name="SIM",
                               tunnel=TunnelLink(state="PEER_DOWN")),
        )
    # Rien en base : pas de bond porté.
    assert await repo.bond_carried_since("hd2", None) is False
    # Un tunnel toujours tombé : toujours non.
    for _ in range(5):
        await repo.insert(tombe("hd2"))
    assert await repo.bond_carried_since("hd2", None) is False
    # Le voisin a porté une fois — et cela ne déteint pas sur le premier.
    porte = _idle_tunnel_sample()
    porte.router_id = "mbx"
    await repo.insert(porte)
    assert await repo.bond_carried_since("mbx", None) is True
    assert await repo.bond_carried_since("hd2", None) is False


async def test_bond_carried_since_est_borne_a_la_session(repo):
    """La mémoire du bond est celle de la SESSION, pas de la vie entière.

    Un HD2 qui a bondé une fois sur un autre réseau, revenu ici, aurait
    alerté pour toujours avec une mémoire « jamais » ; borné à la mise sous
    tension, le fait s'efface avec le contact.
    """
    from datetime import datetime, timedelta, timezone
    t0 = datetime(2026, 9, 19, 8, 0, tzinfo=timezone.utc)
    ancien = _idle_tunnel_sample(); ancien.router_id = "hd2"; ancien.ts = t0 - timedelta(days=3)
    await repo.insert(ancien)                       # a porté… il y a trois jours
    assert await repo.bond_carried_since("hd2", None) is True, "sur toute la vie : oui"
    assert await repo.bond_carried_since("hd2", t0) is False, "depuis cette session : non"
    recent = _idle_tunnel_sample(); recent.router_id = "hd2"; recent.ts = t0 + timedelta(minutes=5)
    await repo.insert(recent)
    assert await repo.bond_carried_since("hd2", t0) is True, "et dès qu'elle porte, oui"


async def test_a_timeline_slice_counts_the_DISABLED_readings_apart(tmp_path):
    """C-20 : un WAN désactivé par l'exploitant laisse un relevé d'adieu,
    `connected = 0` et « désactivé ». La tranche le comptait comme un relevé
    tombé, et la bande le peignait en panne. Elle dit maintenant combien de ses
    relevés sont des désactivations — un choix, pas une défaillance."""
    from datetime import datetime, timedelta

    repo = SampleRepository(str(tmp_path / "s.db"))
    await repo.connect()
    t0 = datetime.fromisoformat("2026-09-01T08:00:00+00:00")
    await repo._conn.executemany(
        "INSERT INTO samples (router_id, wan_id, ts, connected, kind, status_message) "
        "VALUES ('r1', ?, ?, ?, 'cellular', ?)",
        [
            ("4", (t0 + timedelta(seconds=1)).isoformat(), 1, "Connected"),
            ("4", (t0 + timedelta(seconds=2)).isoformat(), 0, "désactivé"),
            ("5", (t0 + timedelta(seconds=1)).isoformat(), 0, "No signal"),
        ],
    )
    await repo._conn.commit()

    lignes, _, _ = await repo.timeline("r1", t0, t0 + timedelta(minutes=10))
    par_lien = {l["wan_id"]: l for l in lignes}
    assert par_lien["4"]["desactives"] == 1
    assert par_lien["5"]["desactives"] == 0, "une vraie panne n'est pas une désactivation"
    await repo.close()


def test_merged_slices_add_their_disabled_counts():
    from app.storage.db import _fusionner_tranche

    garde = {"releves": 1, "desactives": 1}
    _fusionner_tranche(garde, {"releves": 2, "desactives": 1})
    assert garde["desactives"] == 2


def _tris(plan: list[str]) -> int:
    return sum("USE TEMP B-TREE FOR ORDER BY" in ligne for ligne in plan)


async def test_the_latency_islands_are_found_in_ONE_ordered_pass(tmp_path):
    """C-31 : les îlots se trouvaient par deux `ROW_NUMBER` — deux tris de
    toute la fenêtre de plus. Un îlot s'ouvre sur un relevé haut dont le
    précédent ne l'est pas, ou appartient à une autre période observée : un
    `LAG` et une somme cumulée, dans l'ordre du lien. Mesuré sur dix heures à
    cinq liens (60 000 relevés) : 700 → 480 ms, mêmes îlots."""
    from datetime import datetime

    from app.regles_coupure import TROU_MAX_MS
    from app.storage.db import _SQL_ILOTS

    async with SampleRepository(str(tmp_path / "s.db")) as repo:
        t0 = datetime.fromisoformat("2026-09-01T08:00:00+00:00").isoformat()
        bornes = ("r1", t0, t0)
        async with repo._conn.execute(
            "EXPLAIN QUERY PLAN " + _SQL_ILOTS, (*bornes, 500.0, TROU_MAX_MS, *bornes, TROU_MAX_MS)
        ) as cursor:
            plan = [str(row[-1]) for row in await cursor.fetchall()]
        assert _tris(plan) <= 4, plan

