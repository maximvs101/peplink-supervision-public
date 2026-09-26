"""Interface commune à tous les clients Peplink.

Pièce maîtresse de l'architecture : le simulateur et le client API réel implémentent
la même interface, donc brancher un vrai routeur = changer PEPLINK_MODE, pas réécrire l'app.
"""

from __future__ import annotations

from typing import Protocol

from ..models import DeviceCapabilities, DeviceInfo, GnssFix, WanRadioStatus


class PeplinkClient(Protocol):
    """Client capable de fournir l'état des WAN radio d'un routeur.

    Pas de `@runtime_checkable` : il n'y a aucun `isinstance(..., PeplinkClient)`
    dans le dépôt, et il n'en faut pas. Le décorateur ne vérifie de toute façon
    que la PRÉSENCE des noms, jamais leurs signatures ni le fait qu'ils soient
    asynchrones : un objet portant un `get_radio_status` synchrone le passerait.
    Il donnait donc l'illusion d'un contrôle que le typage statique fait
    réellement.
    """

    router_id: str

    async def get_radio_status(self, echeance: float | None = None) -> list[WanRadioStatus]:
        """État courant de chaque WAN radio (modems cellulaires et liens Wi-Fi).

        `echeance` (horloge de la boucle) : au-delà, les lectures complémentaires
        sont abandonnées et les états WAN déjà lus rendus tels quels.
        """
        ...

    async def get_location(self) -> GnssFix | None:
        """Position GPS du routeur, ou None si indisponible."""
        ...

    async def get_device_info(self) -> DeviceInfo | None:
        """Identité de l'équipement (modèle, série, firmware), ou None."""
        ...

    async def get_capabilities(self) -> DeviceCapabilities | None:
        """Capacités déclarées par l'équipement, ou None."""
        ...

    async def aclose(self) -> None:
        """Libère les ressources (connexions HTTP, etc.)."""
        ...
