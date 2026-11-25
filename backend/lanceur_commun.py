"""Le superviseur et le témoin Ethernet en un seul fichier : double-clic, et les
deux serveurs démarrent.

Point d'entrée de l'exécutable `peplink` construit par
`tools/construire_executable.py`. Il réunit les deux lanceurs existants, sans
changer leurs règles :

  - le superviseur (`lancer.py`) sur `PORT_SUPERVISEUR` (8000 par défaut), sur
    l'hôte `HOST` (127.0.0.1 par défaut), sa base `data/samples.db` ;
  - le témoin (`temoin/__main__.py`) sur `PORT_TEMOIN` (8010 par défaut, le
    suivant libre s'il est pris), sur 127.0.0.1 seulement, sa base
    `data/temoin.db` ;
  - un seul processus, une seule boucle : Ctrl+C ou la fermeture de la console
    arrêtent les deux serveurs, bases fermées et verrou relâché ;
  - le navigateur s'ouvre sur le superviseur ; chaque interface porte un lien
    vers l'autre ;
  - lancé une seconde fois dans le même dossier, il rouvre l'instance qui
    tourne et s'arrête.

`--superviseur` ou `--temoin` n'en lance qu'un, avec les règles de son lanceur
— le port venant alors de `PORT_SUPERVISEUR` ou de `PORT_TEMOIN`. `PORT` n'est
pas lu ici : il garde son sens pour chaque outil lancé seul depuis les sources
(`python lancer.py`, `python -m temoin`).
"""

from __future__ import annotations

import argparse
import asyncio
import os
import signal
import sqlite3
import sys
import webbrowser
from contextlib import contextmanager
from pathlib import Path

import lancer
import temoin
from temoin import __main__ as temoin_lanceur

PORT_SUPERVISEUR = 8000
PORT_TEMOIN = temoin_lanceur.PORT_DEFAUT

# Ce qu'uvicorn capte lui-même : Ctrl+C, `kill`, et sous Windows Ctrl+Pause —
# et la fermeture de la console, que le runtime C de Windows livre en SIGBREAK.
SIGNAUX = tuple(getattr(signal, nom) for nom in ("SIGINT", "SIGTERM", "SIGBREAK") if hasattr(signal, nom))


def _serveur_sans_signaux():
    """Un `uvicorn.Server` qui laisse les signaux au lanceur commun.

    Chaque serveur d'uvicorn pose ses gestionnaires en démarrant et les rend en
    s'arrêtant, puis relève le signal reçu : à deux dans la même boucle, le
    second masquait le premier, et l'ordre des restitutions décidait lequel
    s'arrêtait. Le lanceur commun pose les siens une fois, et les transmet aux
    deux serveurs.
    """
    import uvicorn

    class Serveur(uvicorn.Server):
        @contextmanager
        def capture_signals(self):
            yield

    return Serveur


def lire_port(variable: str, defaut: int) -> int | None:
    """Le port de `variable`, ou None s'il n'en est pas un (le message est dit)."""
    brut = (os.environ.get(variable) or "").strip()
    try:
        port = int(brut) if brut else defaut
    except ValueError:
        port = 0
    if not 0 < port < 65536:
        print(f"{variable}={brut!r} n'est pas un numéro de port (1 à 65535) : "
              f"relancer sans {variable} (port {defaut}) ou avec {variable}={defaut} par exemple.",
              flush=True)
        return None
    return port


async def servir_les_deux(appli, hote: str, port_superviseur: int, base_temoin: Path, ecran: Path,
                          port_temoin: int, adresse_superviseur: str) -> None:
    """Les deux serveurs dans la même boucle, jusqu'au signal d'arrêt.

    Un serveur qui s'arrête de lui-même (démarrage manqué) arrête l'autre : pas
    de demi-outil qui tourne sans le dire."""
    import uvicorn

    classe = _serveur_sans_signaux()
    serveurs: list = []

    def fabrique(config):
        serveur = classe(config)
        serveurs.append(serveur)
        return serveur

    def arreter(sig, trame) -> None:
        # Le comportement d'uvicorn, pour les deux : un premier signal arrête
        # proprement, un second Ctrl+C force l'arrêt.
        for serveur in serveurs:
            serveur.handle_exit(sig, trame)

    superviseur = fabrique(uvicorn.Config(appli, host=hote, port=port_superviseur,
                                          **lancer.options_uvicorn()))
    anciens = {sig: signal.signal(sig, arreter) for sig in SIGNAUX}
    try:
        taches = [
            asyncio.create_task(superviseur.serve()),
            asyncio.create_task(temoin_lanceur.servir(base_temoin, ecran, port_temoin,
                                                      superviseur=adresse_superviseur,
                                                      fabrique=fabrique,
                                                      # Le journal d'accès d'uvicorn est
                                                      # un seul journal par processus : le
                                                      # témoin le rallumait pour le
                                                      # superviseur (vu à l'essai de
                                                      # l'exécutable).
                                                      journal_acces=False)),
        ]
        await asyncio.wait(taches, return_when=asyncio.FIRST_COMPLETED)
        for serveur in serveurs:
            serveur.should_exit = True
        resultats = await asyncio.gather(*taches, return_exceptions=True)
    finally:
        for sig, gestionnaire in anciens.items():
            signal.signal(sig, gestionnaire)
    for resultat in resultats:
        if isinstance(resultat, BaseException):
            raise resultat


def _ouvrir(adresse: str) -> None:
    if os.environ.get("OUVRIR_NAVIGATEUR", "1") == "1":
        webbrowser.open(adresse)


def lancer_les_deux(port_superviseur: int, port_temoin: int) -> int:
    # Le port et le verrou d'abord, les sauvegardes ensuite (C-24) : un
    # lancement refusé ne touche pas aux copies.
    decision = lancer.preparer(sauvegarde=False)
    hote = decision["hote"]
    adresse_superviseur = lancer.adresse_ouverte(hote, port_superviseur)
    base_temoin, ecran = temoin_lanceur.chemins()
    base_temoin.parent.mkdir(parents=True, exist_ok=True)
    verrou = temoin_lanceur.verrouiller(base_temoin)
    if verrou is None:
        # Le témoin de ce dossier tourne déjà : l'exécutable est lancé. On
        # ouvre le superviseur s'il répond, l'écran du témoin sinon.
        try:
            deja = int(temoin_lanceur._fichier_port(base_temoin).read_text().strip())
        except (OSError, ValueError):
            deja = None
        adresse_temoin = f"http://{temoin_lanceur.HOTE}:{deja}" if deja else None
        superviseur_vivant = not lancer.port_disponible(hote, port_superviseur)
        print("Déjà lancé dans ce dossier." if superviseur_vivant
              else "Un témoin tourne déjà avec cette base.", flush=True)
        if superviseur_vivant:
            print(f"  superviseur {adresse_superviseur}", flush=True)
        if adresse_temoin:
            print(f"  témoin      {adresse_temoin}", flush=True)
        ouvrir = adresse_superviseur if superviseur_vivant else adresse_temoin
        if ouvrir:
            _ouvrir(ouvrir)
        return 0
    try:
        if not lancer.port_disponible(hote, port_superviseur):
            lancer.dire_port_pris(hote, port_superviseur, adresse_superviseur, "PORT_SUPERVISEUR")
            return 2
        port_t = temoin_lanceur.choisir_port(port_temoin)
        if port_t is None:
            print(f"Aucun port libre sur {temoin_lanceur.HOTE} de {port_temoin} à "
                  f"{port_temoin + temoin_lanceur._ESSAIS} pour le témoin : fermer un autre outil, "
                  "ou relancer avec PORT_TEMOIN=…", flush=True)
            return 2
        temoin_lanceur._fichier_port(base_temoin).write_text(str(port_t))
        base_superviseur = Path(os.environ["DATABASE_PATH"])
        copies = {}
        for base in (base_superviseur, base_temoin):
            try:
                copies[base] = lancer.sauvegarder(base)
            except sqlite3.DatabaseError as exc:
                lancer.dire_base_illisible(base, exc)
                return 3
        adresse_temoin = f"http://{temoin_lanceur.HOTE}:{port_t}"
        if os.environ.get("OUVRIR_NAVIGATEUR", "1") == "1":
            lancer.ouvrir_quand_pret(adresse_superviseur)
        print(f"Superviseur Peplink — {adresse_superviseur}", flush=True)
        print(f"  base       {base_superviseur}", flush=True)
        if copies[base_superviseur]:
            print(f"  sauvegarde {copies[base_superviseur]}", flush=True)
        if decision["env"]:
            print(f"  .env       {decision['env']}", flush=True)
        print(f"Témoin de lien Ethernet {temoin.__version__} — {adresse_temoin}", flush=True)
        if port_t != port_temoin:
            print(f"  (le port {port_temoin} était pris : {port_t} à la place)", flush=True)
        print(f"  base       {base_temoin}", flush=True)
        if copies[base_temoin]:
            print(f"  sauvegarde {copies[base_temoin]}", flush=True)
        # Après `preparer()` : l'application lit l'environnement qu'il pose.
        from app.main import app as appli

        appli.state.lien_temoin = adresse_temoin
        asyncio.run(servir_les_deux(appli, hote, port_superviseur, base_temoin, ecran, port_t,
                                    adresse_superviseur))
        return 0
    finally:
        verrou.close()


def main(argv: list[str] | None = None) -> int:
    # La console de Windows encode en cp1252 : un accent hors de cette page
    # ferait lever le premier `print`.
    for flux in (sys.stdout, sys.stderr):
        if hasattr(flux, "reconfigure"):
            flux.reconfigure(encoding="utf-8", errors="replace")
    lecteur = argparse.ArgumentParser(
        prog="peplink", description="Superviseur Peplink et témoin de lien Ethernet.")
    seul = lecteur.add_mutually_exclusive_group()
    seul.add_argument("--superviseur", action="store_true", help="le superviseur seul")
    seul.add_argument("--temoin", action="store_true", help="le témoin Ethernet seul")
    args = lecteur.parse_args(argv)
    # Le `.env` posé à côté peut donner PORT_SUPERVISEUR et PORT_TEMOIN : lu
    # avant de les chercher (le shell l'emporte toujours).
    _, donnees = lancer.dossiers()
    lancer.lire_env(donnees.parent / ".env")
    port_superviseur = lire_port("PORT_SUPERVISEUR", PORT_SUPERVISEUR)
    port_temoin = lire_port("PORT_TEMOIN", PORT_TEMOIN)
    if port_superviseur is None or port_temoin is None:
        return 2
    if args.superviseur:
        os.environ["PORT"] = str(port_superviseur)
        lancer.main()
        return 0
    if args.temoin:
        os.environ["PORT"] = str(port_temoin)
        return temoin_lanceur.main()
    return lancer_les_deux(port_superviseur, port_temoin)


if __name__ == "__main__":
    sys.exit(main())
