"""Diffusion temps réel des relevés (mode terrain / affichage live fluide)."""

from __future__ import annotations

import asyncio
import logging
from urllib.parse import urlsplit

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from ..config import get_settings
from ..models import RadioSample
from .security import hote_de_confiance

logger = logging.getLogger(__name__)

router = APIRouter()


def origine_admise(
    origine: str | None, hote: str | None, origines: list[str], noms: list[str],
) -> bool:
    """L'origine d'une poignée de main est-elle admise ?

    La même origine l'est — mais seulement si son hôte est de confiance.
    « Origine égale à Host » suffisait, or l'attaquant d'un DNS rebinding
    choisit les deux (C-3). La garde de `main.py` refuse déjà ces hôtes ;
    celle-ci tient même si l'autre venait à manquer.
    """
    if origine is None:
        return True
    if origine in origines:
        return True
    netloc = urlsplit(origine).netloc
    return netloc == hote and hote_de_confiance(hote, noms)


@router.websocket("/ws/routers/{router_id}")
async def live_router(websocket: WebSocket, router_id: str) -> None:
    """Pousse chaque nouveau relevé du routeur au client connecté."""
    # L'ORIGINE est contrôlée ici, et pas seulement sur `/api`. Le middleware
    # CORS ne voit jamais une poignée de main WebSocket : vérifié, une connexion
    # portant `Origin: https://exemple.invalid` était acceptée et livrait
    # immédiatement la position GPS de l'équipement et les noms de WAN — qui
    # portent en pratique le numéro de ligne et l'ICCID de chaque SIM. N'importe
    # quelle page ouverte dans le navigateur de l'exploitant pouvait donc lire le
    # flux temps réel en continu, sans que le serveur soit exposé au réseau.
    #
    # Une requête SANS origine n'est pas un navigateur — c'est un outil en ligne
    # de commande, qui n'est pas soumis à la politique d'origine et n'a rien à
    # contourner. On ne refuse que ce qui se présente sous une origine non
    # autorisée.
    settings = get_settings()
    origine = websocket.headers.get("origin")
    # La MÊME ORIGINE est toujours admise, et c'est le cas de la production : le
    # backend sert lui-même l'interface, le navigateur envoie donc
    # `Origin: http://localhost:8000` — que `CORS_ORIGINS` ne contient pas,
    # puisqu'il ne liste que le serveur de développement. Mesuré sur le serveur
    # en service : 130 refus au journal, un toutes les deux secondes, pendant
    # qu'un client sans origine passait. Le flux temps réel ne fonctionnait donc
    # PAS en production, et le repli REST à quinze secondes le masquait.
    admise = settings.cors_allows_any_origin or origine_admise(
        origine, websocket.headers.get("host"),
        settings.cors_origin_list, settings.allowed_host_list,
    )
    if not admise:
        logger.warning("WebSocket refusé : origine %s non autorisée", origine)
        # ACCEPTER avant de refermer, comme pour le 4404 quinze lignes plus bas :
        # fermer une connexion jamais acceptée fait répondre à uvicorn un refus
        # de handshake HTTP 403, qui jette le code applicatif. Le navigateur ne
        # recevait qu'un 1006 indistinct — donc pris pour une coupure réseau —
        # et se reconnectait sans fin. Le correctif avait été appliqué au 4404 et
        # pas à celui-ci.
        await websocket.accept()
        await websocket.close(code=4403, reason="Origine non autorisée")
        return

    poller = websocket.app.state.poller
    if poller.get_router(router_id) is None:
        # Accepter AVANT de refermer, si étrange que cela paraisse : fermer une
        # connexion jamais acceptée fait répondre à uvicorn un refus de
        # handshake HTTP 403, qui jette le code et la raison applicatifs. Le
        # navigateur ne recevait qu'un 1006 indistinct — donc indiscernable
        # d'une coupure réseau — et se reconnectait sans fin à un routeur qui
        # n'existe pas. Accepté puis fermé, le 4404 lui parvient et il s'arrête.
        await websocket.accept()
        await websocket.close(code=4404, reason="Routeur inconnu")
        return

    await websocket.accept()
    queue = poller.subscribe(router_id)
    # Le retrait a pu tomber pendant `accept()` : l'abonnement viendrait de
    # recréer, par le defaultdict, l'entrée que `forget()` a purgée — un
    # abonné sur un équipement inexistant, que rien ne réveillerait jamais.
    if poller.get_router(router_id) is None:
        poller.unsubscribe(router_id, queue)
        await websocket.close(code=4404, reason="Routeur inconnu")
        return
    try:
        # État courant immédiat, pour ne pas attendre le prochain cycle de
        # scrutation. Sur une COPIE : le poller réécrit ce dictionnaire, et
        # l'envoi cède la main entre deux relevés.
        for sample in poller.snapshot_latest(router_id):
            await websocket.send_text(sample.model_dump_json())

        # Attendre le prochain relevé ET le canal `receive` en parallèle.
        # N'attendre que la file laissait la déconnexion du navigateur passer
        # inaperçue jusqu'au prochain `send_text` — donc jamais tant que le
        # poller est à l'arrêt ou le routeur muet. Mesuré sur uvicorn, poller
        # arrêté : ni une fermeture propre ni une coupure TCP brutale ne
        # réveillaient la coroutine, et le nombre d'abonnés montait à chaque
        # onglet fermé au lieu de retomber, chacun laissant derrière lui sa file
        # de cent places. Lire `receive` rend la fermeture immédiate, et le
        # `finally` ci-dessous atteignable.
        next_sample = asyncio.ensure_future(queue.get())
        client_event = asyncio.ensure_future(websocket.receive())
        try:
            while True:
                done, _ = await asyncio.wait(
                    (next_sample, client_event), return_when=asyncio.FIRST_COMPLETED
                )

                if client_event in done:
                    # Le client ne nous parle jamais : tout message est soit sa
                    # déconnexion, soit un bavardage sans objet qu'on ignore.
                    if client_event.result()["type"] == "websocket.disconnect":
                        return
                    client_event = asyncio.ensure_future(websocket.receive())

                if next_sample in done:
                    sample = next_sample.result()
                    if not isinstance(sample, RadioSample):
                        # Sentinelle : l'équipement a été retiré de la supervision.
                        await websocket.close(code=4404, reason="Équipement retiré")
                        return
                    next_sample = asyncio.ensure_future(queue.get())
                    await websocket.send_text(sample.model_dump_json())
        finally:
            # La perdante des deux attentes reste en vol : l'annuler évite une
            # tâche orpheline par connexion fermée.
            for task in (next_sample, client_event):
                task.cancel()
    except WebSocketDisconnect:
        pass
    except asyncio.CancelledError:
        raise
    except Exception as exc:
        logger.debug("WebSocket %s interrompu : %s", router_id, exc)
    finally:
        poller.unsubscribe(router_id, queue)
