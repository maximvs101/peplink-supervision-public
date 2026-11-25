"""Construit l'exécutable unique — superviseur et témoin Ethernet réunis —, pour
LE système où il tourne.

    .venv/bin/python -m pip install -r requirements-build.txt
    .venv/bin/python tools/construire_executable.py

Produit `dist/peplink` (macOS, Linux) ou `dist\\peplink.exe` (Windows) : les
deux serveurs, leurs dépendances, l'interpréteur Python, l'interface compilée du
superviseur et l'écran statique du témoin dans un seul fichier. Son point
d'entrée est `lanceur_commun.py` : un double-clic démarre les deux serveurs.
PyInstaller ne compile pas en croisé — un fichier par système, construit sur
ce système. L'interface doit avoir été compilée avant
(`cd frontend && npm run build`).

Avant PyInstaller, `licences_tierces.py` écrit LICENCES-TIERCES.txt — les
mentions des composants tiers embarqués — et l'exécutable l'emporte deux fois,
servi à la racine de chaque interface : `http://127.0.0.1:8000/LICENCES-TIERCES.txt`
et `http://127.0.0.1:8010/LICENCES-TIERCES.txt`. Une copie est posée dans
`dist/`, que la Release publie. Une licence hors liste blanche arrête la
construction ici, avant PyInstaller.
"""

from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
from pathlib import Path

import licences_tierces

RACINE = Path(__file__).resolve().parents[1]
DIST = RACINE.parent / "frontend" / "dist"
SEPARATEUR = ";" if sys.platform == "win32" else ":"
NOM = "peplink"


# Ce qu'uvicorn charge par nom, invisible à l'analyse statique.
_UVICORN = [
    "--hidden-import", "uvicorn.logging",
    "--hidden-import", "uvicorn.loops.auto",
    "--hidden-import", "uvicorn.protocols.http.auto",
    "--hidden-import", "uvicorn.protocols.websockets.auto",
    "--hidden-import", "uvicorn.lifespan.on",
    "--hidden-import", "aiosqlite",
    "--hidden-import", "websockets",
]
# Le trousseau du système (`app/coffre.py`) : `keyring` trouve ses backends par
# points d'entrée, que l'analyse statique ne suit pas, et lit ces points
# d'entrée dans les MÉTADONNÉES du paquet. Sans les deux, l'exécutable se croit
# sans trousseau et redemande tout.
_TROUSSEAU = ["--collect-submodules", "keyring", "--copy-metadata", "keyring"]


def commande(licences: Path) -> list[str]:
    ecran = RACINE / "temoin" / "ecran"
    return [
        sys.executable, "-m", "PyInstaller",
        "--onefile", "--clean", "--noconfirm",
        "--name", NOM,
        "--distpath", str(RACINE / "dist"),
        "--workpath", str(RACINE / "build"),
        "--specpath", str(RACINE / "build"),
        # `lanceur_commun.py` importe `lancer` et `temoin` : depuis la racine
        # du serveur.
        "--paths", str(RACINE),
        # L'interface, embarquée sous le même chemin relatif qu'attend lancer.py.
        "--add-data", f"{DIST}{SEPARATEUR}frontend/dist",
        # L'écran du témoin, sous le chemin du paquet que lit `temoin/__main__.py`.
        "--add-data", f"{ecran}{SEPARATEUR}temoin/ecran",
        # Les licences tierces, à la racine de chaque interface : servies comme elle.
        "--add-data", f"{licences}{SEPARATEUR}frontend/dist",
        "--add-data", f"{licences}{SEPARATEUR}temoin/ecran",
        # Les deux applications entières : les routes sont importées par chaîne.
        "--collect-all", "app",
        "--collect-all", "temoin",
        *_UVICORN,
        *_TROUSSEAU,
        str(RACINE / "lanceur_commun.py"),
    ]


def main(argv: list[str] | None = None) -> int:
    argparse.ArgumentParser(
        description="Construit l'exécutable unique (superviseur et témoin Ethernet).").parse_args(argv)
    if not (DIST / "index.html").exists():
        print(f"L'interface n'est pas compilée : {DIST} — `cd frontend && npm run build` d'abord.")
        return 1
    # Hors du dossier de travail de PyInstaller (`build/<nom>`), que `--clean` vide.
    licences = RACINE / "build" / "licences" / licences_tierces.NOM
    try:
        contenu, sans_texte = licences_tierces.generer(interface=True)
    except licences_tierces.LicencesRefusees as e:
        print(f"LICENCES-TIERCES : {e}")
        return 1
    licences.parent.mkdir(parents=True, exist_ok=True)
    licences.write_text(contenu, encoding="utf-8", newline="\n")
    print(f"{licences} écrit" + "".join(f"\n  texte de licence non distribué : {n}" for n in sans_texte))
    (RACINE / "dist").mkdir(exist_ok=True)
    shutil.copyfile(licences, RACINE / "dist" / licences_tierces.NOM)
    appel = commande(licences)
    print(" ".join(appel))
    return subprocess.call(appel, cwd=RACINE)


if __name__ == "__main__":
    sys.exit(main())
