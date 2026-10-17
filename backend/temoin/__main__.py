"""Le témoin de lien Ethernet en un fichier : double-clic, et l'écran s'ouvre.

    python -m temoin            (depuis backend/, pour l'essayer)

Point d'entrée de l'exécutable `temoin-ethernet` construit par
`tools/construire_executable.py --produit temoin`. Les règles du superviseur
(`lancer.py`), dont ce lanceur reprend les outils plutôt que de les recopier :

  - l'écran (`temoin/ecran`) est servi depuis le paquet — embarqué, une fois
    gelé, dans le dossier de décompression de PyInstaller ;
  - la base `data/temoin.db` vit À CÔTÉ de l'exécutable (sous `backend/data`
    depuis le dépôt), distincte de celle du superviseur, avec une copie de
    sauvegarde à chaque lancement ;
  - le serveur n'écoute que sur 127.0.0.1 — même si `HOST` est posé pour le
    superviseur : le témoin ne s'ouvre jamais au réseau ;
  - port 8010 par défaut (`PORT` le change), le superviseur gardant 8000 : les
    deux tournent ensemble. Pris, le témoin prend le suivant libre et le dit.
"""

from __future__ import annotations

import asyncio
import os
import sqlite3
import sys
from contextlib import asynccontextmanager
from pathlib import Path

import temoin
from lancer import dossiers, ouvrir_quand_pret, port_disponible, sauvegarder

HOTE = "127.0.0.1"
PORT_DEFAUT = 8010
# Combien de ports essayer après le premier, avant de renoncer.
_ESSAIS = 20


def chemins() -> tuple[Path, Path]:
    """(la base, le dossier de l'écran). Les données : la règle du superviseur."""
    _, donnees = dossiers()
    return donnees / "temoin.db", Path(temoin.__file__).resolve().parent / "ecran"


def choisir_port(depart: int) -> int | None:
    """Le premier port libre à partir de `depart` — vérifié AVANT d'ouvrir un
    navigateur, avec la garde du superviseur (TIME_WAIT, Windows)."""
    for port in range(depart, depart + _ESSAIS + 1):
        if port_disponible(HOTE, port):
            return port
    return None


async def servir(base: Path, ecran: Path, port: int) -> None:
    """Le dépôt appartient au lanceur : ouvert avant de servir, fermé après."""
    import uvicorn

    from temoin.api import creer_app
    from temoin.stockage import Depot

    depot = Depot(str(base))
    await depot.connect()
    try:
        app = creer_app(depot, interface=ecran)
        # Fermé à l'arrêt de l'APPLICATION, après le sien (collecte annulée) :
        # uvicorn, arrêté par un signal, le relève une fois arrêté, et le
        # processus meurt sans repasser par le `finally` ci-dessous : la base
        # n'était jamais fermée. Le `finally` reste pour un arrêt avant le
        # démarrage (et il est sans effet sur une base déjà fermée).
        cycle_de_l_api = app.router.lifespan_context

        @asynccontextmanager
        async def cycle(application):
            async with cycle_de_l_api(application) as etat:
                yield etat
            await depot.close()

        app.router.lifespan_context = cycle
        config = uvicorn.Config(app, host=HOTE, port=port, log_level="info")
        await uvicorn.Server(config).serve()
    finally:
        await depot.close()


def _base_illisible(base: Path, exc: Exception) -> None:
    copies = sorted(base.parent.glob(f"{base.stem}.*{base.suffix}.bak"))
    print(f"La base {base} est illisible ({exc}).", flush=True)
    if copies:
        print("Copies de sauvegarde disponibles, de la plus récente à la plus ancienne :", flush=True)
        for c in reversed(copies):
            print(f"  {c}", flush=True)
        print("Pour repartir de l'une d'elles : fermer cette fenêtre, mettre la base "
              f"illisible de côté, et renommer la copie choisie en {base.name}.", flush=True)
    else:
        print("Aucune copie de sauvegarde n'existe à côté d'elle.", flush=True)
    if getattr(sys, "frozen", False):
        input("Appuyer sur Entrée pour fermer.")


def main() -> int:
    # La console de Windows encode en cp1252 : un accent hors de cette page
    # ferait lever le premier `print`.
    for flux in (sys.stdout, sys.stderr):
        if hasattr(flux, "reconfigure"):
            flux.reconfigure(encoding="utf-8", errors="replace")
    base, ecran = chemins()
    brut = (os.environ.get("PORT") or "").strip()
    try:
        depart = int(brut) if brut else PORT_DEFAUT
    except ValueError:
        depart = 0
    if not 0 < depart < 65536:
        print(f"PORT={brut!r} n'est pas un numéro de port (1 à 65535) : "
              f"relancer sans PORT (port {PORT_DEFAUT}) ou avec PORT=8010 par exemple.", flush=True)
        return 2
    port = choisir_port(depart)
    if port is None:
        print(f"Aucun port libre sur {HOTE} de {depart} à {depart + _ESSAIS} : "
              "fermer un autre outil, ou relancer avec PORT=…", flush=True)
        return 2
    # Le port d'abord, la sauvegarde ensuite (comme le superviseur, C-24).
    base.parent.mkdir(parents=True, exist_ok=True)
    try:
        copie = sauvegarder(base)
    except sqlite3.DatabaseError as exc:
        _base_illisible(base, exc)
        return 3
    adresse = f"http://{HOTE}:{port}"
    if os.environ.get("OUVRIR_NAVIGATEUR", "1") == "1":
        ouvrir_quand_pret(adresse, sante="/api/sante")
    print(f"Témoin de lien Ethernet {temoin.__version__} — {adresse}", flush=True)
    if port != depart:
        print(f"  (le port {depart} était pris : {port} à la place)", flush=True)
    print(f"  base       {base}", flush=True)
    if copie:
        print(f"  sauvegarde {copie}", flush=True)
    asyncio.run(servir(base, ecran, port))
    return 0


if __name__ == "__main__":
    sys.exit(main())
