"""Une seule règle de coupure, deux exécutants : Python et SQLite.

Le classement d'un relevé — coupure, micro-coupure, passage en secours,
désactivation, voisinage — vivait en cinq exemplaires (fiche de session,
collecteur, journal, brassage, modèle), qui divergeaient déjà : le Python
tolérait des espaces autour de « Standby », le SQL non ; la fiche comptait une
coupure à travers un trou de supervision, le journal non. Chaque cas de cette
table est jugé par le prédicat Python ET par le fragment SQL exécuté sur une
base en mémoire ; les deux verdicts doivent être identiques, et égaux à
l'attendu.
"""

from __future__ import annotations

import sqlite3

import pytest

from app import regles_coupure as R

_T0 = "2026-09-11T10:00:00+00:00"


def _apres(secondes: str) -> str:
    """Un instant `secondes` après `_T0`, écrit comme le collecteur l'écrit."""
    s = float(secondes)
    minutes, reste = divmod(s, 60)
    return f"2026-09-11T10:{int(minutes):02d}:{reste:09.6f}+00:00"


# (nom, avant, après, coupure attendue, micro-coupure attendue)
# avant / après : (connecté, uptime, message, instant)
_CAS = [
    ("chute franche", (1, 500, "Connected", _T0), (0, None, "Disconnected", _apres("5")), True, False),
    ("chute sans message", (1, 500, None, _T0), (0, None, None, _apres("5")), True, False),
    ("désactivation (C-20)", (1, 500, "Connected", _T0), (0, None, R.STATUT_DESACTIVE, _apres("5")),
     False, False),
    ("désactivation mal écrite : ce n'est plus notre libellé", (1, 500, "Connected", _T0),
     (0, None, " désactivé", _apres("5")), True, False),
    ("retour", (0, None, None, _T0), (1, 3, "Connected", _apres("5")), False, False),
    ("lien qui reste coupé", (0, None, None, _T0), (0, None, None, _apres("5")), False, False),
    ("micro-coupure", (1, 500, "Connected", _T0), (1, 2, "Connected", _apres("5")), False, True),
    ("uptime qui avance", (1, 500, "Connected", _T0), (1, 505, "Connected", _apres("5")), False, False),
    ("uptime égal", (1, 500, "Connected", _T0), (1, 500, "Connected", _apres("5")), False, False),
    ("uptime inconnu après", (1, 500, "Connected", _T0), (1, None, "Connected", _apres("5")), False, False),
    ("uptime inconnu avant", (1, None, "Connected", _T0), (1, 2, "Connected", _apres("5")), False, False),
    ("connexion inconnue après", (1, 500, "Connected", _T0), (None, 2, "Connected", _apres("5")),
     False, False),
    ("connexion inconnue avant", (None, 500, "Connected", _T0), (0, None, None, _apres("5")), False, False),
    ("entrée en secours", (1, 78, "Connected to SFR", _T0), (1, 0, "Standby", _apres("5")), False, False),
    ("sortie du secours", (1, 40, "Standby", _T0), (1, 0, "Connected to SFR", _apres("5")), False, False),
    ("secours en capitales", (1, 78, "Connected", _T0), (1, 0, "STANDBY", _apres("5")), False, False),
    ("secours en casse mêlée", (1, 78, "Connected", _T0), (1, 0, "sTaNdBy (priority 2)", _apres("5")),
     False, False),
    ("secours entouré d'espaces", (1, 78, "Connected", _T0), (1, 0, "  Standby  ", _apres("5")),
     False, False),
    ("secours après tabulation et saut de ligne", (1, 78, "Connected", _T0), (1, 0, "\t\nStandby", _apres("5")),
     False, False),
    ("secours avant, entouré d'espaces", (1, 40, " Standby ", _T0), (1, 0, "Connected", _apres("5")),
     False, False),
    ("« Standby » au milieu ne vaut pas secours", (1, 78, "Connected", _T0),
     (1, 0, "Not Standby", _apres("5")), False, True),
    ("préfixe tronqué", (1, 78, "Connected", _T0), (1, 0, "Standb", _apres("5")), False, True),
    ("message vide", (1, 78, "", _T0), (1, 0, "", _apres("5")), False, True),
    ("message fait d'espaces", (1, 78, "   ", _T0), (1, 0, "   ", _apres("5")), False, True),
    # « ſ » (s long) et « K » (signe kelvin) : la minuscule Unicode de Python
    # n'est pas celle, ASCII, de SQLite. Aucun ne doit passer pour « standby ».
    ("s long Unicode", (1, 78, "Connected", _T0), (1, 0, "ſtandby", _apres("5")), False, True),
    ("pleine chasse", (1, 78, "Connected", _T0), (1, 0, "Ｓtandby", _apres("5")), False, True),
    # Le voisinage : au-delà d'une minute sans relevé, on ne sait pas.
    ("micro-coupure pile au seuil", (1, 500, "Connected", _T0), (1, 2, "Connected", _apres("60")),
     False, True),
    ("micro-coupure une milliseconde au-delà", (1, 500, "Connected", _T0),
     (1, 2, "Connected", _apres("60.001")), False, False),
    ("coupure pile au seuil", (1, 500, "Connected", _T0), (0, None, None, _apres("60")), True, False),
    ("coupure à travers un trou", (1, 500, "Connected", _T0), (0, None, None, _apres("75")), False, False),
    ("sous la milliseconde, arrondi comme SQLite", (1, 500, "Connected", _T0),
     (1, 2, "Connected", _apres("60.000400")), False, True),
    ("demi-milliseconde, arrondie vers le haut", (1, 500, "Connected", _T0),
     (1, 2, "Connected", _apres("60.000500")), False, False),
    ("même instant", (1, 500, "Connected", _T0), (1, 2, "Connected", _T0), False, True),
    ("fuseau explicite", (1, 500, "Connected", _T0),
     (1, 2, "Connected", "2026-09-11T12:00:30+02:00"), False, True),
]


def _verdict_python(avant, apres) -> tuple[bool, bool]:
    (c0, u0, m0, t0), (c1, u1, m1, t1) = avant, apres
    voisins = R.sont_voisins(t0, t1)
    return (
        voisins and R.est_coupure(c0, c1, m1),
        voisins and R.est_micro_coupure(c0, c1, u0, u1, m0, m1),
    )


def _verdict_sql(avant, apres) -> tuple[bool, bool]:
    base = sqlite3.connect(":memory:")
    base.execute(
        "CREATE TABLE samples (wan_id TEXT, ts TEXT, connected INTEGER, uptime_s INTEGER, "
        "status_message TEXT)"
    )
    for c, u, m, t in (avant, apres):
        base.execute("INSERT INTO samples VALUES ('1', ?, ?, ?, ?)", (t, c, u, m))
    coupure = R.sql_coupure("connecte_avant", "connected", "status_message")
    micro = R.sql_micro_coupure(
        "connecte_avant", "connected", "uptime_avant", "uptime_s", "message_avant", "status_message",
    )
    voisin = R.sql_voisins("ecart_ms")
    requete = f"""
        WITH f AS (
          SELECT ts, connected, uptime_s, status_message,
                 LAG(connected)      OVER w AS connecte_avant,
                 LAG(uptime_s)       OVER w AS uptime_avant,
                 LAG(status_message) OVER w AS message_avant,
                 {R.sql_ecart_ms("LAG(ts) OVER w", "ts")} AS ecart_ms
          FROM samples
          WINDOW w AS (PARTITION BY wan_id ORDER BY ts, rowid)
        )
        SELECT COALESCE({voisin.sql} AND {coupure.sql}, 0),
               COALESCE({voisin.sql} AND {micro.sql}, 0)
        FROM f WHERE ecart_ms IS NOT NULL
    """
    params = (*voisin.params, *coupure.params, *voisin.params, *micro.params)
    (ligne,) = base.execute(requete, params).fetchall()
    return bool(ligne[0]), bool(ligne[1])


@pytest.mark.parametrize(
    ("avant", "apres", "coupure", "micro"),
    [c[1:] for c in _CAS],
    ids=[c[0] for c in _CAS],
)
def test_python_et_sql_rendent_le_meme_verdict(avant, apres, coupure, micro):
    attendu = (coupure, micro)
    assert _verdict_python(avant, apres) == attendu
    assert _verdict_sql(avant, apres) == attendu


@pytest.mark.parametrize(
    "message",
    [None, "", "   ", "Standby", " Standby", "STANDBY\t", "Not Standby", "ſtandby", "Ｓtandby",
     "standby-ish", "Standb", "désactivé"],
)
def test_en_secours_python_et_sql(message):
    base = sqlite3.connect(":memory:")
    fragment = R.sql_en_secours("m")
    (sql,) = base.execute(
        f"SELECT {fragment.sql} FROM (SELECT ? AS m)", (*fragment.params, message)
    ).fetchone()
    assert bool(sql) is R.en_secours(message)


def test_le_message_est_normalise_a_l_ecriture():
    """Les espaces autour du message sont retirés AVANT la base : le relevé
    écrit est propre, la tolérance à la lecture ne sert qu'aux anciennes lignes
    et aux relevés du témoin."""
    from app.models import WanRadioStatus as WanStatus

    assert WanStatus(wan_id="1", wan_name="Cellulaire 1", connected=True, status_message="  Standby \n").status_message == "Standby"
    assert WanStatus(wan_id="1", wan_name="Cellulaire 1", connected=True, status_message="   ").status_message is None
    assert WanStatus(wan_id="1", wan_name="Cellulaire 1", connected=True, status_message=None).status_message is None


def test_un_nom_de_colonne_douteux_est_refuse():
    """Les fragments n'interpolent que des noms de colonnes du code : rien qui
    puisse fermer une chaîne ou enchaîner une instruction."""
    with pytest.raises(ValueError):
        R.sql_en_secours("m'); DROP TABLE samples; --")
    with pytest.raises(ValueError):
        R.sql_ecart_ms("ts; DELETE FROM samples", "ts")


# --- les autres lecteurs du voisinage : le journal et le collecteur ------------
#
# Deux relevés à EXACTEMENT soixante secondes sont voisins (`sont_voisins`). En
# secondes flottantes — `(julianday(b) - julianday(a)) * 86400.0 <= 60` —, ce
# couple-ci donne 60,0000367 : l'îlot de latence, le changement d'opérateur et la
# bascule de cellule y voyaient un trou de supervision que la fiche ne voyait pas.
_A = "2026-09-01T10:00:01.918000+00:00"
_B = "2026-09-01T10:01:01.918000+00:00"


def test_le_couple_d_essai_est_bien_voisin_et_piege_les_flottants():
    assert R.sont_voisins(_A, _B)
    base = sqlite3.connect(":memory:")
    assert base.execute("SELECT (julianday(?) - julianday(?)) * 86400.0 > 60",
                        (_B, _A)).fetchone()[0] == 1


async def _journal(tmp_path, lignes: list[tuple]):
    from datetime import datetime, timedelta

    from app.storage.db import SampleRepository

    async with SampleRepository(str(tmp_path / "s.db")) as repo:
        await repo._conn.executemany(
            "INSERT INTO samples (router_id, wan_id, ts, connected, kind, rtt_ms, cell_id, "
            "operator, plmn) VALUES ('r1', '1', ?, 1, 'cellular', ?, ?, ?, ?)", lignes)
        await repo._conn.commit()
        t = datetime.fromisoformat(_A)
        episodes, _ = await repo.events("r1", t - timedelta(minutes=1), t + timedelta(minutes=5), 500.0)
    return episodes


async def test_un_ilot_de_latence_suit_la_regle_de_voisinage(tmp_path):
    episodes = await _journal(tmp_path, [(_A, 900.0, None, None, None), (_B, 900.0, None, None, None)])
    ilots = [e for e in episodes if e["genre"] == "latence haute"]
    assert [e["releves"] for e in ilots] == [2], ilots


async def test_une_bascule_de_cellule_suit_la_regle_de_voisinage(tmp_path):
    episodes = await _journal(tmp_path, [(_A, 30.0, 11, None, None), (_B, 900.0, 12, None, None)])
    (ilot,) = [e for e in episodes if e["genre"] == "latence haute"]
    assert ilot["depuis_bascule"] == 0


async def test_un_changement_d_operateur_suit_la_regle_de_voisinage(tmp_path):
    episodes = await _journal(tmp_path, [(_A, 30.0, None, "SFR", "208-10"),
                                         (_B, 30.0, None, "Orange", "208-01")])
    assert [e["ts"] for e in episodes if e["genre"] == "changement d'opérateur"] == [_B]


def test_le_collecteur_ne_recalcule_pas_le_voisinage():
    """Le collecteur oubliait la dernière cellule d'un lien au-delà de
    `_TROU_MAX_S` secondes flottantes : un troisième exemplaire de la règle."""
    from pathlib import Path

    import app.poller as poller

    source = Path(poller.__file__).read_text(encoding="utf-8")
    assert "_TROU_MAX_S" not in source
