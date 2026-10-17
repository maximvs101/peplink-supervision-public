"""Trouver le Peplink du réseau et s'y connecter une seule fois.

La découverte n'envoie aucun identifiant. La connexion est UNE tentative,
sur demande explicite : cinq refus verrouillent l'accès admin cinq minutes,
donc rien ici ne rejoue jamais après un refus. Ni la série, ni les
identifiants ne sont journalisés.
"""

from __future__ import annotations

import logging

from app import coffre
from app.coffre import Identifiants
from app.discovery.probe import discover
from app.fleet import RouterConfig
from app.models import DeviceInfo
from app.peplink.router_api import (
    AuthenticationError,
    RouterApiError,
    RouterApiPeplinkClient,
)
from temoin.stockage import Depot

logger = logging.getLogger(__name__)


class RefusIdentifiants(Exception):
    """Identifiants absents ou refusés par le routeur."""


class Injoignable(Exception):
    """Le routeur ne répond pas, ou pas comme un Peplink."""


async def chercher(*, depot: Depot | None = None) -> list[dict]:
    """Les Peplink joignables du réseau, dans l'ordre de la découverte."""
    resultat = []
    for appareil in await discover():
        if not appareil.api_reachable:
            continue
        retenu = False
        if depot is not None:
            serie = await depot.serie_de(appareil.host)
            retenu = serie is not None and coffre.lire(serie) is not None
        resultat.append(
            {"hote": appareil.host, "firmware": appareil.firmware, "retenu": retenu}
        )
    return resultat


async def connecter(
    hote: str, ident: Identifiants | None, retenir: bool, depot: Depot
) -> tuple[RouterApiPeplinkClient, DeviceInfo]:
    """Une tentative. Le client rendu reste ouvert : la collecte le réutilise."""
    if ident is None:
        serie = await depot.serie_de(hote)
        ident = coffre.lire(serie) if serie else None
        if ident is None:
            raise RefusIdentifiants("identifiants requis")

    config = RouterConfig(
        id=f"temoin-{hote}", name=hote, host=hote,
        username=ident.username, password=ident.password,
        client_id=ident.client_id, client_secret=ident.client_secret,
        verify_ssl=False, mode="router",
    )
    client = RouterApiPeplinkClient(config)
    try:
        try:
            info = await client.identify()
        except AuthenticationError as exc:
            raise RefusIdentifiants(
                "Identifiants refusés. Attention : cinq tentatives ratées "
                "suspendent l'accès à l'équipement pendant 5 minutes."
            ) from exc
        except RouterApiError as exc:
            raise Injoignable("L'équipement a refusé la lecture.") from exc
        except Exception as exc:
            raise Injoignable(
                f"Équipement injoignable ({type(exc).__name__})."
            ) from exc
        if info is None or not info.serial:
            raise Injoignable("L'équipement n'a pas publié son identité.")
        await depot.memoriser_equipement(hote, info.serial, info.model)
        if retenir:
            coffre.retenir(info.serial, ident)
    except BaseException:
        await client.aclose()
        raise
    return client, info


async def liens_ethernet(client: RouterApiPeplinkClient) -> list[dict]:
    """Les WAN filaires, par numéro. Jamais le nom du WAN."""
    wans = [w for w in await client.get_radio_status() if w.kind == "ethernet"]
    wans.sort(key=lambda w: (len(str(w.wan_id)), str(w.wan_id)))
    return [{"wan_id": w.wan_id, "connecte": w.connected} for w in wans]
