"""Garde de version, lancée par le job `publier` avant tout envoi.

    python3 .github/scripts/garde_version.py v0.2.1 > notes.md

Écrit sur la sortie standard la section de CHANGELOG.md qui porte l'étiquette
— le texte de la Release —, et échoue (code 1, rien sur la sortie) si l'un de
ces numéros diverge de l'étiquette sans son « v » :

  - la section du CHANGELOG qui la porte, et qui doit être la PREMIÈRE section
    numérotée : une section « ## À venir » au-dessus est permise (elle n'est
    pas publiée), une version plus récente au-dessus ne l'est pas ;
  - `backend/app/__init__.__version__` (le superviseur, et son API) ;
  - `backend/temoin/__init__.__version__` (le témoin Ethernet) ;
  - `frontend/package.json` (l'interface).

Avant, `awk` ne trouvait rien pour une étiquette sans section et un texte de
secours partait en Release ; et v0.1.1 a été publiée avec des exécutables qui
se disaient 0.1.0. Bibliothèque standard seule : le job de publication
n'installe rien.
"""

from __future__ import annotations

import argparse
import ast
import json
import re
import sys
from pathlib import Path

_ETIQUETTE = re.compile(r"v(\d+\.\d+\.\d+)")
# « ## v0.2.0 — Titre » ou « ## v0.2.0 » ; « ## À venir » n'est pas numérotée.
_SECTION_NUMEROTEE = re.compile(r"^## v(\d+\.\d+\.\d+)(?:\s|$)")


def _version_python(fichier: Path) -> str | None:
    """`__version__ = "x"` lu sans importer le paquet (ses dépendances ne sont
    pas installées ici)."""
    for noeud in ast.parse(fichier.read_text(encoding="utf-8")).body:
        if (isinstance(noeud, ast.Assign) and len(noeud.targets) == 1
                and isinstance(noeud.targets[0], ast.Name)
                and noeud.targets[0].id == "__version__"
                and isinstance(noeud.value, ast.Constant)):
            return str(noeud.value.value)
    return None


def _sections(changelog: str) -> list[tuple[str, list[str]]]:
    """(titre, lignes) de chaque section `## …`, dans l'ordre du fichier."""
    sections: list[tuple[str, list[str]]] = []
    for ligne in changelog.splitlines():
        if ligne.startswith("## "):
            sections.append((ligne, []))
        elif sections:
            sections[-1][1].append(ligne)
    return sections


def verifier(etiquette: str, racine: Path) -> tuple[list[str], str]:
    """(erreurs, notes). Les notes ne valent que si la liste d'erreurs est vide."""
    m = _ETIQUETTE.fullmatch(etiquette)
    if not m:
        return [f"étiquette « {etiquette} » : attendu vX.Y.Z"], ""
    version = m.group(1)
    erreurs: list[str] = []

    sections = _sections((racine / "CHANGELOG.md").read_text(encoding="utf-8"))
    numerotees = [(_SECTION_NUMEROTEE.match(titre).group(1), lignes)
                  for titre, lignes in sections if _SECTION_NUMEROTEE.match(titre)]
    notes = ""
    trouvee = [lignes for numero, lignes in numerotees if numero == version]
    if not trouvee:
        erreurs.append(f"CHANGELOG.md : aucune section « ## v{version} »")
    else:
        notes = "\n".join(trouvee[0]).strip()
        if not notes:
            erreurs.append(f"CHANGELOG.md : la section « ## v{version} » est vide")
        if numerotees[0][0] != version:
            erreurs.append(f"CHANGELOG.md : la section en tête est v{numerotees[0][0]}, "
                           f"pas v{version}")

    for nom, lu in (
        ("backend/app/__init__.py", _version_python(racine / "backend" / "app" / "__init__.py")),
        ("backend/temoin/__init__.py",
         _version_python(racine / "backend" / "temoin" / "__init__.py")),
        ("frontend/package.json",
         json.loads((racine / "frontend" / "package.json").read_text(encoding="utf-8"))
         .get("version")),
    ):
        if lu != version:
            erreurs.append(f"{nom} : version {lu}, l'étiquette dit {version}")
    return erreurs, notes


def main() -> int:
    analyse = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    analyse.add_argument("etiquette", help="l'étiquette publiée, ex. v0.2.1")
    analyse.add_argument("--racine", type=Path, default=Path(__file__).resolve().parents[2])
    args = analyse.parse_args()
    erreurs, notes = verifier(args.etiquette, args.racine)
    if erreurs:
        for erreur in erreurs:
            # Une annotation par divergence : lisible sans ouvrir les journaux.
            print(f"::error title=Garde de version::{erreur}", file=sys.stderr)
        return 1
    sys.stdout.reconfigure(encoding="utf-8")
    print(notes)
    return 0


if __name__ == "__main__":
    sys.exit(main())
