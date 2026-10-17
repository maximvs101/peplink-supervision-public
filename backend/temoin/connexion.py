"""Trouver le Peplink du réseau et s'y connecter une seule fois.

La découverte n'envoie aucun identifiant. La connexion est UNE tentative,
sur demande explicite : cinq refus verrouillent l'accès admin cinq minutes,
donc rien ici ne rejoue jamais après un refus. Ni la série, ni les
identifiants ne sont journalisés.
"""

from __future__ import annotations

import asyncio
import logging

from app import coffre
from app.coffre import Identifiants
from app.discovery.probe import discover
from app.fleet import RouterConfig
from app.models import DeviceInfo
from app.models import WanRadioStatus
from app.peplink.router_api import (
    AuthenticationError,
    RouterApiError,
    RouterApiPeplinkClient,
)
from temoin.stockage import Depot

logger = logging.getLogger(__name__)

# `retenu` par série, pour la vie de l'application : le sondage de l'écran ne
# réinterroge pas le trousseau toutes les 10 s. Seule une connexion (qui peut
# retenir) change la réponse : elle oublie l'entrée de sa série.
_RETENUS: dict[str, bool] = {}


class ClientTemoin(RouterApiPeplinkClient):
    """Le client du superviseur, réduit à ce que lit le témoin.

    Après `status.wan.connection`, le superviseur lit le débit, le tunnel, la
    température, les clients et la qualité : rien de cela ne sert au témoin, et
    chaque lecture pouvait attendre 15 s. Ici, seule la latence par WAN (que le
    client ne relit qu'une fois par minute), bornée par l'`echeance`. Le relevé
    lui-même, et les WAN désactivés, restent ceux du client du superviseur.
    """

    def _complements(self, statuses: list[WanRadioStatus]) -> tuple:
        return (lambda: self._attach_path_latency(statuses),)


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
            if serie is not None:
                retenu = _RETENUS.get(serie)
                if retenu is None:
                    # Le trousseau peut bloquer (déverrouillage) : hors de la boucle.
                    retenu = await asyncio.to_thread(coffre.lire, serie) is not None
                    _RETENUS[serie] = retenu
        resultat.append(
            {"hote": appareil.host, "firmware": appareil.firmware, "retenu": retenu}
        )
    return resultat


async def connecter(
    hote: str, ident: Identifiants | None, retenir: bool, depot: Depot
) -> tuple[RouterApiPeplinkClient, DeviceInfo]:
    """Une tentative. Le client rendu reste ouvert : la collecte le réutilise.

    Des identifiants du trousseau que le routeur refuse sont oubliés : sinon
    chaque clic suivant les rejouerait, et le cinquième verrouillerait l'accès
    admin pendant 5 minutes."""
    serie_du_trousseau = None
    if ident is None:
        serie = await depot.serie_de(hote)
        # Le trousseau peut bloquer (déverrouillage) : hors de la boucle.
        ident = await asyncio.to_thread(coffre.lire, serie) if serie else None
        if ident is None:
            raise RefusIdentifiants("identifiants requis")
        serie_du_trousseau = serie

    config = RouterConfig(
        id=f"temoin-{hote}", name=hote, host=hote,
        username=ident.username, password=ident.password,
        client_id=ident.client_id, client_secret=ident.client_secret,
        verify_ssl=False, mode="router",
    )
    client = ClientTemoin(config)
    try:
        try:
            info = await client.identify()
        except AuthenticationError as exc:
            if serie_du_trousseau is not None:
                await asyncio.to_thread(coffre.oublier, serie_du_trousseau)
                _RETENUS[serie_du_trousseau] = False
                raise RefusIdentifiants(
                    "Les identifiants retenus sur ce poste ont été refusés : ils sont "
                    "oubliés. Saisissez-les à nouveau (cinq tentatives ratées "
                    "suspendent l'accès à l'équipement pendant 5 minutes)."
                ) from exc
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
        _RETENUS.pop(info.serial, None)
        if retenir:
            await asyncio.to_thread(coffre.retenir, info.serial, ident)
    except BaseException:
        await client.aclose()
        raise
    return client, info


async def liens_ethernet(client: RouterApiPeplinkClient) -> list[dict]:
    """Les WAN filaires, par numéro. Jamais le nom du WAN."""
    wans = [w for w in await client.get_radio_status() if w.kind == "ethernet"]
    wans.sort(key=lambda w: (len(str(w.wan_id)), str(w.wan_id)))
    return [{"wan_id": w.wan_id, "connecte": w.connected} for w in wans]
