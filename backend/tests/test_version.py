"""Un seul numéro de version, celui de la dernière section du CHANGELOG.

La Release v0.1.1 a été publiée avec des exécutables qui se disaient 0.1.0 :
le numéro vivait en quatre endroits écrits à la main — le paquet Python,
l'application FastAPI, `package.json` et son verrou — et aucun ne suivait
l'étiquette.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

RACINE = Path(__file__).resolve().parents[2]


def _derniere_version_publiee() -> str:
    texte = (RACINE / "CHANGELOG.md").read_text(encoding="utf-8")
    return re.search(r"^## v(\d+\.\d+\.\d+)\b", texte, re.M).group(1)


def test_le_serveur_porte_la_version_du_changelog():
    from app import __version__
    from app.main import app

    attendue = _derniere_version_publiee()
    assert __version__ == attendue
    assert app.version == __version__, "l'API doit lire __version__, pas une copie"


def test_l_interface_porte_la_meme_version():
    attendue = _derniere_version_publiee()
    paquet = json.loads((RACINE / "frontend" / "package.json").read_text(encoding="utf-8"))
    verrou = json.loads((RACINE / "frontend" / "package-lock.json").read_text(encoding="utf-8"))
    assert paquet["version"] == attendue
    assert verrou["version"] == attendue
    assert verrou["packages"][""]["version"] == attendue
