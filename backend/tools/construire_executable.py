"""Construit l'exécutable unique du superviseur — ou du témoin Ethernet —, pour
LE système où il tourne.

    .venv/bin/python -m pip install -r requirements-build.txt
    .venv/bin/python tools/construire_executable.py                    # superviseur
    .venv/bin/python tools/construire_executable.py --produit temoin   # témoin

Produit `dist/superviseur` (macOS, Linux) ou `dist\\superviseur.exe` (Windows) :
le serveur, ses dépendances, l'interpréteur Python et l'interface compilée dans
un seul fichier. PyInstaller ne compile pas en croisé — un fichier par système,
construit sur ce système. L'interface doit avoir été compilée avant
(`cd frontend && npm run build`).

Mesuré le 19 septembre 2026 sur macOS : 38 s de construction, 19,3 Mo, prêt à
répondre 1,5 s après le double-clic.

Le témoin produit `dist/temoin-ethernet` (ou `.exe`) : son écran est statique
(`temoin/ecran`), il ne demande aucune compilation de l'interface.

Avant PyInstaller, `licences_tierces.py` écrit LICENCES-TIERCES.txt — les
mentions des composants tiers embarqués — et chaque exécutable l'emporte, servi
à la racine de son interface : `http://127.0.0.1:8000/LICENCES-TIERCES.txt`. Le
superviseur en pose aussi une copie dans `dist/`, que la Release publie : la
sienne couvre le témoin, qui n'en a que le serveur. Une licence hors liste
blanche arrête la construction ici, avant PyInstaller.
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


def _commune(nom: str) -> list[str]:
    return [
        sys.executable, "-m", "PyInstaller",
        "--onefile", "--clean", "--noconfirm",
        "--name", nom,
        "--distpath", str(RACINE / "dist"),
        "--workpath", str(RACINE / "build"),
        "--specpath", str(RACINE / "build"),
    ]


def superviseur(licences: Path) -> list[str] | None:
    if not (DIST / "index.html").exists():
        print(f"L'interface n'est pas compilée : {DIST} — `cd frontend && npm run build` d'abord.")
        return None
    return [
        *_commune("superviseur"),
        # L'interface, embarquée sous le même chemin relatif qu'attend lancer.py.
        "--add-data", f"{DIST}{SEPARATEUR}frontend/dist",
        # Les licences tierces, à la racine de l'interface : servies comme elle.
        "--add-data", f"{licences}{SEPARATEUR}frontend/dist",
        # L'application entière : les routes sont importées par chaîne.
        "--collect-all", "app",
        *_UVICORN,
        *_TROUSSEAU,
        str(RACINE / "lancer.py"),
    ]


def temoin(licences: Path) -> list[str]:
    ecran = RACINE / "temoin" / "ecran"
    return [
        *_commune("temoin-ethernet"),
        # `temoin/__main__.py` importe `lancer` (ses outils de port, de
        # sauvegarde, de dossiers) et `app` : depuis la racine du serveur.
        "--paths", str(RACINE),
        # L'écran, sous le chemin du paquet que lit `temoin/__main__.py`.
        "--add-data", f"{ecran}{SEPARATEUR}temoin/ecran",
        "--add-data", f"{licences}{SEPARATEUR}temoin/ecran",
        "--collect-all", "temoin",
        *_UVICORN,
        *_TROUSSEAU,
        str(RACINE / "temoin" / "__main__.py"),
    ]


def main(argv: list[str] | None = None) -> int:
    lecteur = argparse.ArgumentParser(description="Construit l'exécutable unique.")
    lecteur.add_argument("--produit", choices=("superviseur", "temoin"), default="superviseur")
    args = lecteur.parse_args(argv)
    est_temoin = args.produit == "temoin"
    if not est_temoin and not (DIST / "index.html").exists():
        print(f"L'interface n'est pas compilée : {DIST} — `cd frontend && npm run build` d'abord.")
        return 1
    # Hors du dossier de travail de PyInstaller (`build/<nom>`), que `--clean`
    # vide. Le témoin n'embarque pas l'interface web : son fichier ne la cite pas.
    licences = RACINE / "build" / "licences" / args.produit / licences_tierces.NOM
    try:
        contenu, sans_texte = licences_tierces.generer(interface=not est_temoin)
    except licences_tierces.LicencesRefusees as e:
        print(f"LICENCES-TIERCES : {e}")
        return 1
    licences.parent.mkdir(parents=True, exist_ok=True)
    licences.write_text(contenu, encoding="utf-8", newline="\n")
    print(f"{licences} écrit" + "".join(f"\n  texte de licence non distribué : {n}" for n in sans_texte))
    if not est_temoin:
        (RACINE / "dist").mkdir(exist_ok=True)
        shutil.copyfile(licences, RACINE / "dist" / licences_tierces.NOM)
    commande = temoin(licences) if est_temoin else superviseur(licences)
    if commande is None:
        return 1
    print(" ".join(commande))
    return subprocess.call(commande, cwd=RACINE)


if __name__ == "__main__":
    sys.exit(main())
