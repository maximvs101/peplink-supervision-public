"""Le superviseur en un fichier : double-clic, et le tableau de bord s'ouvre.

Point d'entrée de l'exécutable construit par `tools/construire_executable.py`
(PyInstaller). Tout ce qui, dans le dépôt, suppose une arborescence — l'interface
dans `../frontend/dist`, la base dans `./data` — est posé ici, une fois, avant
que l'application ne se charge :

  - l'interface est EMBARQUÉE dans l'exécutable, et servie depuis son dossier
    de décompression ;
  - la base vit dans `data/`, À CÔTÉ de l'exécutable — sur une clé USB, elle
    voyage avec la clé ; une copie de sauvegarde est faite à chaque lancement,
    parce qu'une clé arrachée pendant une écriture peut corrompre SQLite ;
  - le fichier de flotte `routers.yaml`, s'il est posé à côté, est lu ; sinon
    les équipements s'appairent depuis l'écran de découverte ;
  - le serveur n'écoute que sur 127.0.0.1 : rien à autoriser dans un pare-feu,
    aucun droit d'administrateur. `HOST=0.0.0.0` l'ouvre au réseau local, pour
    un téléphone — Windows demande alors une autorisation entrante.

Lancé depuis le dépôt (`python lancer.py`), il fait la même chose avec les
chemins du dépôt : c'est ainsi qu'il se teste.
"""

from __future__ import annotations

import os
import shutil
import socket
import sqlite3
import sys
import threading
import time
import webbrowser
from contextlib import closing
from datetime import datetime
from pathlib import Path


def dossiers() -> tuple[Path, Path]:
    """(dossier de l'interface, dossier des données)."""
    gele = getattr(sys, "frozen", False)
    if gele:
        # PyInstaller décompresse l'exécutable dans un dossier temporaire et le
        # nomme dans `_MEIPASS` ; l'exécutable lui-même est là où l'exploitant
        # l'a posé — et c'est là que vivent ses données.
        embarque = Path(getattr(sys, "_MEIPASS", Path(sys.executable).parent))
        return embarque / "frontend" / "dist", Path(sys.executable).resolve().parent / "data"
    racine = Path(__file__).resolve().parent
    return racine.parent / "frontend" / "dist", racine / "data"


# Copies de sauvegarde gardées. Deux : celle d'avant ce lancement, et celle
# d'avant le précédent — de quoi retrouver un état d'avant une clé arrachée.
# Cinq copies d'une base qui grossit de six cents kilooctets par jour et par
# équipement, jamais élaguée, faisaient plus d'un gigaoctet sur une clé au bout
# de quelques mois, recopiés à chaque lancement.
_COPIES = 2


def sauvegarder(base: Path) -> Path | None:
    """Une copie datée de la base, avant que le serveur n'y écrive.

    Rien n'est copié si la base n'a pas bougé depuis la dernière copie — même
    taille, même date : relancer trois fois de suite ne fait pas trois copies.
    """
    if not base.exists():
        return None
    # Un journal chaud à côté (`-journal`, `-wal`) : une écriture a été
    # interrompue — clé arrachée — et le fichier principal seul est un état
    # non annulé. La copie passe alors par SQLite, qui rejoue le journal avant
    # de copier ; et on ne saute jamais cette copie-là.
    journal_chaud = any(base.with_name(base.name + s).exists() for s in ("-journal", "-wal"))
    copies = sorted(base.parent.glob(f"{base.stem}.*{base.suffix}.bak"))
    if copies and not journal_chaud:
        derniere, actuelle = copies[-1].stat(), base.stat()
        if derniere.st_size == actuelle.st_size and int(derniere.st_mtime) == int(actuelle.st_mtime):
            return copies[-1]
    horodatage = datetime.now().strftime("%Y%m%d-%H%M%S")
    copie = base.with_name(f"{base.stem}.{horodatage}{base.suffix}.bak")
    # L'API de sauvegarde de SQLite, et non une copie du fichier : elle rend
    # une base COHÉRENTE même si le fichier porte un journal à rejouer.
    try:
        # `closing` et non le `with` de sqlite3, qui valide sans FERMER : sous
        # Windows, la copie resterait verrouillée et ne pourrait être effacée.
        with closing(sqlite3.connect(base)) as source, closing(sqlite3.connect(copie)) as cible:
            source.backup(cible)
    except sqlite3.DatabaseError:
        # Pas de copie vide à côté d'une base illisible : elle se ferait passer
        # pour la plus récente des sauvegardes (C-47).
        copie.unlink(missing_ok=True)
        raise
    shutil.copystat(base, copie)
    for vieille in sorted(base.parent.glob(f"{base.stem}.*{base.suffix}.bak"))[:-_COPIES]:
        vieille.unlink(missing_ok=True)
    return copie


def _adresses_du_poste() -> list[str]:
    """127.0.0.1 et l'adresse du poste sur son réseau, SANS résolution de nom :
    un `connect` UDP choisit la route sans envoyer de paquet (la résolution du
    nom de la machine prenait 5 s)."""
    adresses = ["127.0.0.1"]
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.connect(("192.0.2.1", 9))
            lan = s.getsockname()[0]
        if not lan.startswith("127.") and lan != "0.0.0.0":
            adresses.append(lan)
    except OSError:
        pass
    return adresses


def _ecoute(hote: str, port: int) -> bool:
    """Quelqu'un écoute-t-il là ? Un refus net, ou rien, veut dire non."""
    try:
        with socket.create_connection((hote, port), timeout=0.5):
            return True
    except OSError:
        return False


def port_disponible(hote: str, port: int) -> bool:
    """Le serveur pourra-t-il écouter ? Vérifié AVANT d'ouvrir un navigateur.

    Un second double-clic, ou un serveur de développement déjà sur le port :
    uvicorn s'arrêtait sur « address already in use » pendant que le navigateur
    s'ouvrait sur l'AUTRE serveur — et l'exploitant regardait une autre
    instance, une autre base, en croyant avoir lancé la sienne.
    """
    # SANS SO_REUSEADDR : ce n'est pas le bind qu'uvicorn fera, et avec lui le
    # test passait à côté — deux instances vivantes sur le même port en
    # relançant avec HOST=0.0.0.0 sur macOS (vérifié à lsof), et sous Windows
    # un bind qui passe toujours pendant qu'uvicorn tombe sur 10048. Et les
    # DEUX adresses : une instance sur 127.0.0.1 et une sur 0.0.0.0 se
    # disputent le même port.
    # L'adresse qu'uvicorn liera, et elle seule, SANS SO_REUSEADDR : c'est ce
    # bind-là qui échouera ou non. Sonder aussi 0.0.0.0 quand HOST vaut
    # 127.0.0.1 refusait de démarrer à cause d'un service sur une AUTRE
    # interface (192.168.x.x:8000) alors que 127.0.0.1:8000 était libre ; et
    # c'est inutile — une écoute sur le joker fait déjà échouer le bind d'une
    # adresse précise sous macOS et Linux. PAS sous Windows : voir plus bas.
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        try:
            s.bind((hote, port))
            lie = True
        except OSError:
            lie = False
    if lie:
        # Sous Windows, un bind strict passe à côté d'une écoute sur une AUTRE
        # adresse du même port, dans les deux sens : sur le joker quand le port
        # écoute sur 127.0.0.1 ou l'adresse du LAN, et sur 127.0.0.1 quand il
        # écoute sur le joker. Deux instances y coexisteraient — vu dans
        # l'action GitHub, où ces cas tombaient. Une connexion tranche : vers
        # l'adresse visée, ou vers celles du poste pour le joker.
        if sys.platform == "win32":
            cibles = _adresses_du_poste() if hote in ("0.0.0.0", "") else [hote]
            return not any(_ecoute(cible, port) for cible in cibles)
        return True
    # Le bind STRICT échoue aussi sur un port qui ne porte plus que des
    # connexions en TIME_WAIT — juste après l'arrêt du superviseur, une
    # trentaine de secondes sous macOS. uvicorn, lui, lie avec SO_REUSEADDR
    # sous POSIX et démarrerait : la garde refusait une relance légitime (vu
    # deux fois le 25/09). On distingue donc TIME_WAIT et écoute VIVANTE, mais
    # seulement là où c'est sûr :
    # - pas sous Windows, où SO_REUSEADDR permet de voler un port EN ÉCOUTE
    #   (et où TIME_WAIT ne bloque pas le bind strict) ;
    # - pas sur le joker : une écoute sur une AUTRE interface le prend, et la
    #   voir exigerait d'énumérer les adresses locales — par la résolution du
    #   nom de la machine, 5 s mesurées ici. Relancer sur 0.0.0.0 juste après
    #   un arrêt attend donc la fin du TIME_WAIT.
    if sys.platform == "win32" or hote in ("0.0.0.0", ""):
        return False
    # Pas de bind avec SO_REUSEADDR pour trancher : sous macOS il passe à côté
    # d'une écoute sur 0.0.0.0 (H-6.1). Une CONNEXION tranche : une écoute sur
    # cette adresse ou sur le joker l'accepte, un port en TIME_WAIT la refuse
    # net, et un port lié sans écoute la laisse expirer (mesuré sous macOS).
    # Seul un refus net vaut « libre ».
    try:
        with socket.create_connection((hote, port), timeout=0.5):
            return False
    except ConnectionRefusedError:
        return True
    except OSError:
        return False


def ouvrir_quand_pret(adresse: str, delai_s: float = 15.0, sante: str = "/api/health") -> None:
    """Ouvre le navigateur quand le serveur RÉPOND — pas après un délai fixe.

    `sante` : la route qui dit « prêt » — `/api/sante` pour le témoin Ethernet."""
    import urllib.request

    def attendre() -> None:
        debut = time.monotonic()
        while time.monotonic() - debut < delai_s:
            try:
                with urllib.request.urlopen(f"{adresse}{sante}", timeout=1) as r:
                    if r.status == 200:
                        webbrowser.open(adresse)
                        return
            except Exception:
                pass
            time.sleep(0.25)

    threading.Thread(target=attendre, daemon=True).start()


def lire_env(fichier: Path) -> dict[str, str]:
    """Le `.env` posé à côté — de l'exécutable, ou du dépôt — appliqué SANS
    écraser l'environnement du shell.

    Trois choses ne marchaient pas : à côté de l'exécutable, `.env` n'était
    jamais lu (l'application cherche le sien dans son dossier de décompression) ;
    HOST, PORT et OUVRIR_NAVIGATEUR ne pouvaient venir que du shell ; et depuis
    le dépôt, ce lanceur posait ses défauts par-dessus le `.env` local, qui
    définit précisément PEPLINK_MODE, DATABASE_PATH et ROUTERS_CONFIG. L'ordre
    est désormais : le shell, puis le `.env`, puis les défauts de ce lanceur.
    """
    if not fichier.is_file():
        return {}
    from dotenv import dotenv_values

    lues = {k: v for k, v in dotenv_values(fichier).items() if v is not None}
    for cle, valeur in lues.items():
        os.environ.setdefault(cle, valeur)
    return lues


# Les variables qui désignent un chemin : l'application et ce lanceur doivent
# les lire dans le même sens.
_CHEMINS = ("DATABASE_PATH", "ROUTERS_CONFIG", "FRONTEND_DIST")


def preparer(sauvegarde: bool = True) -> dict[str, str]:
    """Pose l'environnement de l'application et rend ce qui a été décidé.

    `sauvegarde=False` : sans copier la base — `main()` ne la copie qu'une fois
    le port acquis (C-24)."""
    interface, donnees = dossiers()
    du_shell = {cle for cle in _CHEMINS if os.environ.get(cle)}
    env = lire_env(donnees.parent / ".env")
    # Un chemin RELATIF n'avait pas le même sens pour tout le monde (C-4) :
    # l'application le résout depuis sa racine — `_MEIPASS` une fois gelée,
    # que l'amorce efface à la sortie —, `sauvegarder()` depuis le répertoire
    # courant. Le .env du dépôt, posé à côté de l'exécutable, envoyait ainsi
    # la base dans un dossier temporaire, sans aucune copie. Il est résolu ici
    # une fois pour toutes, et écrit en absolu : relatif au .env qui l'a
    # donné, ou au répertoire courant du shell qui l'a écrit.
    for cle in _CHEMINS:
        valeur = os.environ.get(cle)
        if valeur and not Path(valeur).is_absolute():
            ancre = Path.cwd() if cle in du_shell else donnees.parent
            # abspath et non resolve() : sous Windows, resolve() développe les
            # noms courts (RUNNER~1) et suit les liens — le chemin écrit ne
            # serait plus celui que l'exploitant reconnaît.
            os.environ[cle] = os.path.abspath(ancre / valeur)
    # L'application lit le même fichier, pour ce que ce lanceur ne pose pas.
    os.environ.setdefault("ENV_FILE", str(donnees.parent / ".env"))
    donnees.mkdir(parents=True, exist_ok=True)
    base = donnees / "samples.db"
    os.environ.setdefault("FRONTEND_DIST", str(interface))
    os.environ.setdefault("DATABASE_PATH", str(base))
    os.environ.setdefault("ROUTERS_CONFIG", str(donnees.parent / "routers.yaml"))
    os.environ.setdefault("PEPLINK_MODE", "router")
    copie = sauvegarder(Path(os.environ["DATABASE_PATH"])) if sauvegarde else None
    return {
        "interface": str(interface),
        "base": os.environ["DATABASE_PATH"],
        "sauvegarde": str(copie) if copie else "",
        "hote": os.environ.get("HOST", "127.0.0.1"),
        "port": os.environ.get("PORT", "8000"),
        "env": ", ".join(sorted(env)) if env else "",
    }


def main() -> None:
    # La console de Windows encode en cp1252 : un nom de WAN, un message du
    # routeur ou un tiret cadratin hors de cette page ferait lever le premier
    # `print`, avant que le serveur n'écoute. UTF-8, et un caractère
    # irreprésentable devient « ? » plutôt qu'une exception.
    for flux in (sys.stdout, sys.stderr):
        if hasattr(flux, "reconfigure"):
            flux.reconfigure(encoding="utf-8", errors="replace")
    # Le port d'abord, la sauvegarde ensuite. Préparer copiait la base et
    # élaguait les copies AVANT de savoir si ce lancement aurait lieu :
    # chaque double-clic refusé sur une instance vivante remplaçait une copie
    # d'avant l'incident par l'état courant (C-24).
    decision = preparer(sauvegarde=False)
    import uvicorn

    # L'adresse qu'uvicorn écoute, et non 127.0.0.1 d'office : avec HOST sur
    # une adresse précise, le navigateur s'ouvrait là où rien n'écoutait. Le
    # joker (0.0.0.0, ::) s'ouvre sur la boucle locale (C-47).
    hote = decision["hote"]
    if hote in ("", "0.0.0.0", "::"):
        hote = "127.0.0.1"
    elif ":" in hote:
        hote = f"[{hote}]"
    adresse = f"http://{hote}:{decision['port']}"
    if not port_disponible(decision["hote"], int(decision["port"])):
        # Pas de « relancer avec PORT=8001 » : deux instances dans le même
        # dossier écriraient la même base et superviseraient la même flotte
        # deux fois. Un second superviseur, c'est une seconde copie de l'outil
        # dans un autre dossier.
        print(
            f"Le port {decision['port']} est déjà pris sur {decision['hote']} : un superviseur "
            "tourne sans doute déjà dans ce dossier — c'est lui qu'il faut ouvrir, à l'adresse "
            "ci-dessous. Pour en lancer un second, copier l'outil dans un autre dossier et "
            "lui donner un autre PORT.",
            flush=True,
        )
        print(f"  {adresse}", flush=True)
        # Le second double-clic était muet : on ouvre l'instance qui tourne
        # (C-47).
        if os.environ.get("OUVRIR_NAVIGATEUR", "1") == "1":
            webbrowser.open(adresse)
        sys.exit(2)
    base = Path(os.environ["DATABASE_PATH"])
    try:
        copie = sauvegarder(base)
    except sqlite3.DatabaseError as exc:
        # Une base illisible — une clé arrachée pendant une écriture — plantait
        # sur une trace, et la console de Windows se refermait (C-47).
        copies = sorted(base.parent.glob(f"{base.stem}.*{base.suffix}.bak"))
        print(f"La base {base} est illisible ({exc}).", flush=True)
        if copies:
            print("Copies de sauvegarde disponibles, de la plus récente à la plus ancienne :", flush=True)
            for c in reversed(copies):
                print(f"  {c}", flush=True)
            print("Pour repartir de l'une d'elles : fermer cette fenêtre, mettre la base "
                  "illisible de côté, et renommer la copie choisie en "
                  f"{base.name}.", flush=True)
        else:
            print("Aucune copie de sauvegarde n'existe à côté d'elle.", flush=True)
        if getattr(sys, "frozen", False):
            input("Appuyer sur Entrée pour fermer.")
        sys.exit(3)
    decision["sauvegarde"] = str(copie) if copie else ""
    if os.environ.get("OUVRIR_NAVIGATEUR", "1") == "1":
        ouvrir_quand_pret(adresse)
    print(f"Superviseur Peplink — {adresse}", flush=True)
    print(f"  base       {decision['base']}", flush=True)
    if decision["sauvegarde"]:
        print(f"  sauvegarde {decision['sauvegarde']}", flush=True)
    if decision["env"]:
        print(f"  .env       {decision['env']}", flush=True)
    uvicorn.run(
        "app.main:app",
        host=decision["hote"],
        port=int(decision["port"]),
        log_level="info",
        # Le journal d'accès écrirait chaque adresse demandée, dont celles qui
        # portent une série (`/api/devices/known/<série>/reconnect`).
        access_log=False,
        ws_per_message_deflate=True,
    )


if __name__ == "__main__":
    main()
