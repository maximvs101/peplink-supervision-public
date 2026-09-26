"""C-49 : la référence et le README décrivent le même lancement.

REFERENCE faisait installer Python 3.11 et lancer uvicorn directement — donc
en mode simulateur (le défaut de config.py) et sans aucune sauvegarde de la
base —, quand le README et l'exécutable passent par lancer.py, en mode
routeur, avec Python 3.12. Deux comportements par défaut pour « lancer
l'outil ».
"""

from __future__ import annotations

import re
from pathlib import Path

RACINE = Path(__file__).resolve().parents[2]
REFERENCE = (RACINE / "docs" / "REFERENCE.md").read_text(encoding="utf-8")


def _section(titre: str) -> str:
    debut = REFERENCE.index(titre)
    suite = REFERENCE.find("\n### ", debut + len(titre))
    return REFERENCE[debut: suite if suite != -1 else None]


def test_le_deploiement_de_terrain_passe_par_le_lanceur():
    terrain = _section("### 3. Installer et lancer")
    assert "lancer.py" in terrain
    assert "uvicorn app.main:app" not in terrain


def test_une_seule_version_de_python():
    assert not re.search(r"Python 3\.11", REFERENCE)
    assert "Python 3.12" in REFERENCE


def test_le_lancement_direct_dit_qu_il_est_en_simulateur():
    dev = _section("### 1. Backend")
    if "uvicorn app.main:app" in dev:
        assert "simulateur" in dev and "PEPLINK_MODE" in dev
