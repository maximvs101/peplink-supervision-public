"""Construit l'exécutable unique du superviseur, pour LE système où il tourne.

    .venv/bin/python -m pip install -r requirements-build.txt
    .venv/bin/python tools/construire_executable.py

Produit `dist/superviseur` (macOS, Linux) ou `dist\\superviseur.exe` (Windows) :
le serveur, ses dépendances, l'interpréteur Python et l'interface compilée dans
un seul fichier. PyInstaller ne compile pas en croisé — un fichier par système,
construit sur ce système. L'interface doit avoir été compilée avant
(`cd frontend && npm run build`).

Mesuré le 19 septembre 2026 sur macOS : 38 s de construction, 19,3 Mo, prêt à
répondre 1,5 s après le double-clic.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

RACINE = Path(__file__).resolve().parents[1]
DIST = RACINE.parent / "frontend" / "dist"
SEPARATEUR = ";" if sys.platform == "win32" else ":"


def main() -> int:
    if not (DIST / "index.html").exists():
        print(f"L'interface n'est pas compilée : {DIST} — `cd frontend && npm run build` d'abord.")
        return 1
    commande = [
        sys.executable, "-m", "PyInstaller",
        "--onefile", "--clean", "--noconfirm",
        "--name", "superviseur",
        "--distpath", str(RACINE / "dist"),
        "--workpath", str(RACINE / "build"),
        "--specpath", str(RACINE / "build"),
        # L'interface, embarquée sous le même chemin relatif qu'attend lancer.py.
        "--add-data", f"{DIST}{SEPARATEUR}frontend/dist",
        # L'application entière : les routes sont importées par chaîne.
        "--collect-all", "app",
        # Ce qu'uvicorn charge par nom, invisible à l'analyse statique.
        "--hidden-import", "uvicorn.logging",
        "--hidden-import", "uvicorn.loops.auto",
        "--hidden-import", "uvicorn.protocols.http.auto",
        "--hidden-import", "uvicorn.protocols.websockets.auto",
        "--hidden-import", "uvicorn.lifespan.on",
        "--hidden-import", "aiosqlite",
        "--hidden-import", "websockets",
        str(RACINE / "lancer.py"),
    ]
    print(" ".join(commande))
    return subprocess.call(commande, cwd=RACINE)


if __name__ == "__main__":
    sys.exit(main())
