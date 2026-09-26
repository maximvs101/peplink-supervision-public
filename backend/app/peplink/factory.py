"""Fabrique de clients Peplink selon le mode configuré."""

from __future__ import annotations

from ..fleet import RouterConfig
from .base import PeplinkClient
from .mock import MockPeplinkClient
from .router_api import RouterApiPeplinkClient


def make_client(router: RouterConfig, default_mode: str) -> PeplinkClient:
    """Retourne le client adapté au mode ('mock' ou 'router').

    Le mode déclaré sur l'équipement prime sur le mode global : un équipement
    appairé à chaud est toujours réel, même si l'application tourne par ailleurs
    en simulateur pour une démonstration.
    """
    if (router.mode or default_mode) == "router":
        return RouterApiPeplinkClient(router)
    return MockPeplinkClient(router)
