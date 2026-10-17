"""Lance l'exécutable construit et vérifie qu'il répond — serveur ET interface.

    .venv/bin/python tools/verifier_executable.py dist/superviseur

Ce que l'essai du 19 septembre a appris : un exécutable peut répondre sur
`/api/health` et servir un 404 à la place de l'interface. Les deux sont
vérifiés, depuis un dossier vide et temporaire, sur un port libre, sans ouvrir
de navigateur. Sert en local comme dans l'action GitHub, sur les trois systèmes.
"""

from __future__ import annotations

import json
import os
import shutil
import socket
import subprocess
import sys
import tempfile
import time
import urllib.request
from pathlib import Path


def port_libre() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])


def lire(url: str) -> tuple[int, bytes]:
    """Le code HTTP et le corps ; 0 quand le serveur ne répond plus du tout.

    Un processus qui meurt entre deux lectures rendait une `URLError` brute, qui
    court-circuitait le compte rendu — et le journal de l'exécutable avec.
    """
    try:
        with urllib.request.urlopen(url, timeout=2) as r:
            return r.status, r.read()
    except urllib.error.HTTPError as e:
        return e.code, b""
    except (urllib.error.URLError, OSError):
        return 0, b""


def main(chemin: str) -> int:
    # Ce script écrit des accents ; la console qui le lit n'est pas forcément
    # en UTF-8 (cp1252 sous Windows, ASCII sous un `LC_ALL=C`) — vu en le
    # rejouant sous une locale ASCII : il tombait sur son propre compte rendu.
    for flux in (sys.stdout, sys.stderr):
        if hasattr(flux, "reconfigure"):
            flux.reconfigure(encoding="utf-8", errors="replace")
    executable = Path(chemin).resolve()
    if not executable.exists():
        print(f"introuvable : {executable}")
        return 2
    port = port_libre()
    # Une copie dans un dossier vide, comme sur une clé : les données doivent
    # naître À CÔTÉ de l'exécutable, pas dans le dossier courant ni dans dist/.
    dossier = Path(tempfile.mkdtemp(prefix="superviseur-"))
    copie = dossier / executable.name
    shutil.copy2(executable, copie)
    env = {**os.environ, "PORT": str(port), "OUVRIR_NAVIGATEUR": "0", "HOST": "127.0.0.1"}
    journal = open(dossier / "lancement.log", "w", encoding="utf-8")
    processus = subprocess.Popen([str(copie)], cwd=dossier, env=env, stdout=journal, stderr=subprocess.STDOUT)
    fautes: list[str] = []
    try:
        depart = time.monotonic()
        pret = None
        while time.monotonic() - depart < 60:
            if processus.poll() is not None:
                fautes.append(f"le processus s'est arrêté (code {processus.returncode})")
                break
            code, corps = lire(f"http://127.0.0.1:{port}/api/health")
            if code == 200:
                pret = time.monotonic() - depart
                break
            time.sleep(0.25)
        if pret is None and not fautes:
            fautes.append("pas de réponse sur /api/health en 60 s")
        if pret is not None:
            sante = json.loads(corps)
            if sante.get("status") != "ok":
                fautes.append(f"/api/health : {sante}")
            # Le NOM du trousseau, jamais son contenu : un exécutable qui n'en
            # voit aucun fait tout ressaisir à chaque lancement, en silence.
            if sys.platform in ("darwin", "win32") and not sante.get("coffre"):
                fautes.append("aucun trousseau du système vu par l'exécutable (keyring non embarqué ?)")
            code, page = lire(f"http://127.0.0.1:{port}/")
            if code != 200 or b"<script" not in page:
                fautes.append(f"l'interface n'est pas servie : HTTP {code}, {len(page)} o")
            code, _ = lire(f"http://127.0.0.1:{port}/api/devices")
            if code != 200:
                fautes.append(f"/api/devices : HTTP {code}")
            if not (dossier / "data" / "samples.db").exists():
                fautes.append("data/samples.db n'a pas été créée à côté de l'exécutable")
            print(f"prêt en {pret:.1f} s · {executable.stat().st_size / 1048576:.1f} Mo · interface servie · base créée"
                  f" · trousseau : {sante.get('coffre') or 'aucun'}")
    finally:
        # Un exécutable « un fichier » est une amorce PLUS un processus enfant :
        # sous Windows, TerminateProcess ne se propage pas et le serveur
        # restait orphelin. `taskkill /T` abat l'arbre ; ailleurs, l'amorce
        # transmet le signal.
        if sys.platform == "win32":
            subprocess.run(["taskkill", "/PID", str(processus.pid), "/T", "/F"], capture_output=True)
        else:
            processus.terminate()
        try:
            processus.wait(timeout=10)
        except subprocess.TimeoutExpired:
            processus.kill()
        journal.close()
    if fautes:
        print("\n".join(f"FAUTE : {f}" for f in fautes))
        print((dossier / "lancement.log").read_text(encoding="utf-8", errors="replace")[-2000:])
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1] if len(sys.argv) > 1 else "dist/superviseur"))
