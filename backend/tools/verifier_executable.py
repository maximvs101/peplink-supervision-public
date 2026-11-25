"""Lance l'exécutable construit et vérifie qu'il répond — les deux serveurs,
leurs interfaces, leurs bases, leurs liens croisés.

    .venv/bin/python tools/verifier_executable.py dist/peplink

Ce que l'essai du 19 septembre a appris : un exécutable peut répondre sur
`/api/health` et servir un 404 à la place de l'interface. Tout est vérifié,
depuis un dossier vide et temporaire (TMPDIR le choisit), sur deux ports
libres, sans ouvrir de navigateur. Sert en local comme dans l'action GitHub,
sur les trois systèmes :

  - le superviseur répond sur `/api/health`, sert sa page et
    LICENCES-TIERCES.txt, crée `data/samples.db` à côté de l'exécutable ;
  - le témoin répond sur `/api/sante`, sert son écran et LICENCES-TIERCES.txt,
    crée `data/temoin.db` ;
  - chacun publie l'adresse de l'autre, et son interface porte le lien ;
  - un second lancement dans le même dossier rouvre l'instance et s'arrête ;
  - hors Windows, l'arrêt par signal ferme proprement les deux serveurs.
"""

from __future__ import annotations

import json
import os
import re
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


def attendre(processus, url: str, fautes: list[str], delai_s: float = 60) -> tuple[float | None, dict]:
    depart = time.monotonic()
    while time.monotonic() - depart < delai_s:
        if processus.poll() is not None:
            fautes.append(f"le processus s'est arrêté (code {processus.returncode})")
            return None, {}
        code, corps = lire(url)
        if code == 200:
            return time.monotonic() - depart, json.loads(corps)
        time.sleep(0.25)
    fautes.append(f"pas de réponse sur {url} en {delai_s:.0f} s")
    return None, {}


def verifier_serveur(nom: str, adresse: str, sante: dict, lecture: str, page_attendue: bytes,
                     fautes: list[str]) -> None:
    if sante.get("status") != "ok":
        fautes.append(f"{nom} : santé {sante}")
    # Le NOM du trousseau, jamais son contenu : un exécutable qui n'en voit
    # aucun fait tout ressaisir à chaque lancement, en silence.
    if sys.platform in ("darwin", "win32") and not sante.get("coffre"):
        fautes.append(f"{nom} : aucun trousseau du système vu (keyring non embarqué ?)")
    code, page = lire(f"{adresse}/")
    if code != 200 or page_attendue not in page:
        fautes.append(f"{nom} : interface non servie : HTTP {code}, {len(page)} o")
    # Les mentions des composants tiers, embarquées par la construction.
    code, licences = lire(f"{adresse}/LICENCES-TIERCES.txt")
    if code != 200 or not licences.startswith(b"Composants tiers"):
        fautes.append(f"{nom} : /LICENCES-TIERCES.txt non servi : HTTP {code}, {len(licences)} o")
    code, _ = lire(f"{adresse}{lecture}")
    if code != 200:
        fautes.append(f"{nom} : {lecture} : HTTP {code}")


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
    port_s, port_t = port_libre(), port_libre()
    while port_t == port_s:
        port_t = port_libre()
    superviseur, temoin = f"http://127.0.0.1:{port_s}", f"http://127.0.0.1:{port_t}"
    # Une copie dans un dossier vide, comme sur une clé : les données doivent
    # naître À CÔTÉ de l'exécutable, pas dans le dossier courant ni dans dist/.
    dossier = Path(tempfile.mkdtemp(prefix=f"{executable.stem}-"))
    copie = dossier / executable.name
    shutil.copy2(executable, copie)
    env = {**os.environ, "PORT_SUPERVISEUR": str(port_s), "PORT_TEMOIN": str(port_t),
           "OUVRIR_NAVIGATEUR": "0", "HOST": "127.0.0.1"}
    env.pop("PORT", None)
    journal = open(dossier / "lancement.log", "w", encoding="utf-8")
    processus = subprocess.Popen([str(copie)], cwd=dossier, env=env, stdout=journal, stderr=subprocess.STDOUT)
    fautes: list[str] = []
    arret_propre = None
    try:
        pret, sante_s = attendre(processus, f"{superviseur}/api/health", fautes)
        _, sante_t = attendre(processus, f"{temoin}/api/sante", fautes) if pret is not None else (None, {})
        if pret is not None and sante_t:
            verifier_serveur("superviseur", superviseur, sante_s, "/api/devices", b"<script", fautes)
            verifier_serveur("témoin", temoin, sante_t, "/api/observations", b'id="lien-superviseur"', fautes)
            # Les liens croisés : chacun publie l'adresse de l'autre…
            if sante_s.get("temoin") != temoin:
                fautes.append(f"/api/health ne donne pas l'adresse du témoin : {sante_s.get('temoin')!r}")
            if sante_t.get("superviseur") != superviseur:
                fautes.append(f"/api/sante ne donne pas l'adresse du superviseur : {sante_t.get('superviseur')!r}")
            # … et l'interface du superviseur porte le lien.
            _, page = lire(f"{superviseur}/")
            scripts = re.findall(rb'<script[^>]*src="([^"]+)"', page)
            if not any("Témoin Ethernet".encode() in lire(f"{superviseur}{s.decode()}")[1] for s in scripts):
                fautes.append("le lien « Témoin Ethernet » est absent de l'interface du superviseur")
            for base in ("samples.db", "temoin.db"):
                if not (dossier / "data" / base).exists():
                    fautes.append(f"data/{base} n'a pas été créée à côté de l'exécutable")
            # Un second lancement dans le même dossier rouvre l'instance et s'arrête.
            second = subprocess.run([str(copie)], cwd=dossier, env=env, capture_output=True,
                                    text=True, encoding="utf-8", errors="replace", timeout=60)
            if second.returncode != 0 or "Déjà lancé dans ce dossier" not in second.stdout:
                fautes.append(f"second lancement : code {second.returncode}, {second.stdout[-300:]!r}")
            if processus.poll() is not None:
                fautes.append("la première instance s'est arrêtée pendant le second lancement")
            print(f"prêt en {pret:.1f} s · {executable.stat().st_size / 1048576:.1f} Mo · deux serveurs"
                  f" ({port_s}, {port_t}) · interfaces servies · liens croisés · bases créées"
                  f" · licences tierces servies · second lancement rouvert"
                  f" · trousseau : {sante_s.get('coffre') or 'aucun'}")
    finally:
        # Un exécutable « un fichier » est une amorce PLUS un processus enfant :
        # sous Windows, TerminateProcess ne se propage pas et le serveur
        # restait orphelin. `taskkill /T` abat l'arbre ; ailleurs, l'amorce
        # transmet le signal — et l'arrêt doit être propre.
        if sys.platform == "win32":
            subprocess.run(["taskkill", "/PID", str(processus.pid), "/T", "/F"], capture_output=True)
        else:
            processus.terminate()
        try:
            processus.wait(timeout=15)
            arret_propre = sys.platform != "win32"
        except subprocess.TimeoutExpired:
            processus.kill()
            processus.wait()
            if sys.platform != "win32":
                fautes.append("l'exécutable ne s'est pas arrêté en 15 s après le signal")
        journal.close()
    texte = (dossier / "lancement.log").read_text(encoding="utf-8", errors="replace")
    # Le journal d'accès d'uvicorn est un seul journal pour le processus : la
    # configuration du témoin le rallumait pour le superviseur, dont les
    # adresses ne doivent pas passer à la console.
    if '"GET /' in texte:
        fautes.append("le journal d'accès écrit les adresses demandées")
    if arret_propre and not fautes:
        if "Arrêt propre de la supervision" not in texte:
            fautes.append("le superviseur ne s'est pas arrêté proprement")
        if texte.count("Application shutdown complete") < 2:
            fautes.append("les deux serveurs ne se sont pas arrêtés proprement")
        verrou = dossier / "data" / "temoin.db.verrou"
        if verrou.exists():
            import fcntl

            with open(verrou, "a+b") as f:
                try:
                    fcntl.flock(f.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                except OSError:
                    fautes.append("le verrou du témoin est resté pris après l'arrêt")
    if fautes:
        print("\n".join(f"FAUTE : {f}" for f in fautes))
        print(texte[-3000:])
        return 1
    if arret_propre:
        print("arrêt propre des deux serveurs · verrou relâché")
    return 0


if __name__ == "__main__":
    import argparse

    lecteur = argparse.ArgumentParser(description="Lance l'exécutable construit et vérifie qu'il répond.")
    lecteur.add_argument("chemin", nargs="?", default="dist/peplink")
    args = lecteur.parse_args()
    sys.exit(main(args.chemin))
