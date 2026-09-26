"""Garde-fous d'accès à l'API de supervision.

L'API n'a aucune authentification : elle est conçue pour tourner sur le portable
de l'exploitant, à côté de l'interface qu'elle sert. Tant qu'elle n'expose que
des lectures, l'enjeu reste faible. Il change dès qu'elle sert à saisir les
identifiants des équipements de la flotte — c'est l'objet de cette garde, prête
pour les routes d'appairage.
"""

from __future__ import annotations

import ipaddress
import logging
from urllib.parse import urlsplit

from fastapi import HTTPException, Request

logger = logging.getLogger(__name__)


def _is_loopback(host: str | None) -> bool:
    if not host:
        return False
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        # Un nom d'hôte ne devrait pas arriver ici ; dans le doute, on refuse.
        return False


def _requete_intersite(request: Request) -> bool:
    """La requête vient-elle d'une page d'une autre origine ?

    Deux témoins, que tout navigateur récent pose : `Sec-Fetch-Site` et
    `Origin`. Une requête qui ne porte ni l'un ni l'autre n'est pas un
    navigateur — un outil en ligne de commande, qui n'a pas de page tierce à
    craindre. `Origin: null` (iframe isolée, fichier local) est tenue pour
    étrangère : elle ne dit pas d'où elle vient.
    """
    from ..config import get_settings

    if request.headers.get("sec-fetch-site") == "cross-site":
        return True
    origine = request.headers.get("origin")
    if origine is None:
        return False
    settings = get_settings()
    if origine in settings.cors_origin_list:
        return False
    netloc = urlsplit(origine).netloc
    hote = request.headers.get("host")
    return not (netloc and netloc == hote and hote_de_confiance(hote, settings.allowed_host_list))


async def require_loopback(request: Request) -> None:
    """Restreint une route à la machine locale.

    À appliquer à toute route qui écrit, et en particulier à celles qui
    manipulent des identifiants d'équipement : lancé avec `--host 0.0.0.0` pour
    être consulté depuis un autre poste du réseau, le serveur exposerait sinon
    ces routes à tout le réseau, sans mot de passe.
    """
    client = request.client.host if request.client else None
    # Un MANDATAIRE local trahit le garde. Le serveur de développement de Vite
    # écoute sur toutes les interfaces — c'est fait pour ouvrir l'écran depuis
    # un téléphone du LAN — et relaie `/api` vers 127.0.0.1 : de ce côté-ci,
    # chaque client du réseau se présente en loopback, et les routes d'écriture
    # comme la capture brute s'ouvraient à tout le LAN. Quand le voisin direct
    # est local ET qu'il transmet l'origine réelle, c'est elle qu'on juge.
    # Un `X-Forwarded-For` reçu d'un client NON local reste ignoré : n'importe
    # qui peut l'écrire, et il ne prouve rien.
    if _is_loopback(client):
        transmis = request.headers.get("x-forwarded-for")
        if transmis:
            # Le DERNIER élément, pas le premier. Un mandataire AJOUTE l'adresse
            # qu'il voit en fin de liste (`existant + ',' + adresse` — lu dans le
            # code de Vite) : ce qui précède a été écrit par le client, et un
            # poste du LAN qui envoyait « X-Forwarded-For: 127.0.0.1 » était jugé
            # local — reproduit lors de l'audit, 200 forgé contre 403 honnête.
            # Avec un seul mandataire de confiance, seul le dernier élément
            # vient de lui.
            client = transmis.rsplit(",", 1)[-1].strip()
    if not _is_loopback(client):
        logger.warning("Route locale refusée à %s (%s)", client, request.url.path)
        raise HTTPException(
            status_code=403,
            detail="Cette opération n'est possible que depuis la machine qui héberge la supervision.",
        )
    # Le poste local, oui — mais pas pour le compte d'une page tierce (C-13).
    # Un formulaire POST cross-site sans corps est une requête « simple »,
    # sans requête préalable CORS : il rejouait la reprise d'identifiants, et
    # cinq envois suspendent l'accès admin du routeur. Une balise <img>
    # déclenchait de même la capture ou la découverte.
    if _requete_intersite(request):
        logger.warning("Route locale refusée à une page tierce (%s, origine %s)",
                       request.url.path, request.headers.get("origin"))
        raise HTTPException(
            status_code=403,
            detail="Cette opération ne peut pas être déclenchée depuis une autre page.",
        )


# --- en-tête Host : la parade au DNS rebinding (C-3) --------------------------
#
# `require_loopback` juge l'adresse TCP. Or un navigateur local se présente
# toujours en 127.0.0.1 : une page dont le domaine est rebasculé vers
# 127.0.0.1 devenait de même origine que l'API, lisait toutes les routes —
# capture brute comprise — et s'abonnait à la WebSocket. Reproduit à la
# relecture du 25/09.
#
# Une attaque par rebinding porte forcément un NOM de domaine : c'est lui
# qu'on fait pointer ailleurs. Une adresse IP littérale ne se rebascule pas,
# `localhost` non plus. On admet donc ces deux formes — ce qui couvre aussi
# HOST=0.0.0.0, où un téléphone du LAN ouvre l'écran par l'adresse du poste —
# et les seuls noms que l'exploitant déclare dans `ALLOWED_HOSTS`.

def _nom_d_hote(entete: str) -> str | None:
    """L'hôte sans son port ; `None` si l'en-tête est malformé."""
    entete = entete.strip()
    if entete.startswith("["):
        fin = entete.find("]")
        if fin < 0:
            return None
        reste = entete[fin + 1:]
        if reste and not (reste.startswith(":") and reste[1:].isdigit()):
            return None
        return entete[1:fin]
    if entete.count(":") > 1:
        return entete                       # IPv6 nue, sans port
    nom, _, port = entete.partition(":")
    if port and not port.isdigit():
        return None
    return nom


def hote_de_confiance(entete: str | None, noms: list[str]) -> bool:
    """L'en-tête Host désigne-t-il ce poste sans passer par un nom rebasculable ?"""
    if not entete:
        return False
    nom = _nom_d_hote(entete)
    if not nom or any(c.isspace() for c in nom):
        return False
    nom = nom.lower()
    if nom == "localhost" or nom in {n.lower() for n in noms}:
        return True
    try:
        ipaddress.ip_address(nom)
    except ValueError:
        return False
    return True


class HoteDeConfianceMiddleware:
    """Refuse toute requête HTTP ou WebSocket dont l'hôte n'est pas de confiance.

    Écrit plutôt que `TrustedHostMiddleware` : celui-ci ne sait pas dire
    « toute adresse IP littérale », qui est précisément la règle.
    """

    def __init__(self, app, noms) -> None:
        self.app = app
        self._noms = noms           # appelable : relu à chaque requête

    async def __call__(self, scope, receive, send) -> None:
        if scope["type"] not in ("http", "websocket"):
            await self.app(scope, receive, send)
            return
        entete = next(
            (v.decode("latin-1") for k, v in scope.get("headers", []) if k == b"host"), None,
        )
        if hote_de_confiance(entete, self._noms()):
            await self.app(scope, receive, send)
            return
        logger.warning("Requête refusée : hôte %r non reconnu (%s)", entete, scope.get("path"))
        if scope["type"] == "websocket":
            # Refus de la poignée de main, sans l'accepter : l'attaquant n'a
            # pas droit au code applicatif qui arrête les reconnexions.
            await send({"type": "websocket.close", "code": 1008})
            return
        corps = b"Hote non reconnu"
        await send({"type": "http.response.start", "status": 400,
                    "headers": [(b"content-type", b"text/plain; charset=utf-8"),
                                (b"content-length", str(len(corps)).encode())]})
        await send({"type": "http.response.body", "body": corps})
