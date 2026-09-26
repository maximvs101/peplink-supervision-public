"""Rejouer une capture RÉELLE à travers toute la chaîne.

Les fixtures `tests/fixtures/*.json` sont ce que trois équipements ont
réellement répondu — un MAX HD4 MBX, un Dome Pro Duo, un MAX HD2 — anonymisés
motif par motif. Jusqu'ici chaque parseur était éprouvé seul sur ces charges
utiles. Ce module les fait passer par TOUT ce qui sépare le routeur de l'écran :
le client réel (avec sa gestion du `stat: fail`, de l'authentification, des
endpoints absents), le collecteur réel (sessions, alertes, GPS, écriture), la
base réelle (SQLite temporaire) et les routes réelles (FastAPI, en ASGI).

Un seul point est simulé : le transport HTTP. `_request` rend la charge captée,
ou le refus `999 unknown function` que le firmware rend pour un endpoint qu'il
n'a pas — la forme observée sur le MAX HD2 le 19 septembre.

Partagé entre les tests de bout en bout et l'exportation des réponses vers
l'écran (`tools/exporter_rejeu.py`), pour que les deux rejouent la MÊME chaîne.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from app import models
from app.api import routes
from app.devices.registry import DeviceRegistry
from app.fleet import RouterConfig
from app.peplink import router_api as router_api_module
from app.peplink.router_api import RouterApiPeplinkClient
from app.poller import Poller
from app.storage.db import SampleRepository

FIXTURES = Path(__file__).parent / "fixtures"

# Chaque capture, et l'équipement qu'elle décrit. La clé sert d'identifiant de
# routeur dans le rejeu et de nom de fichier à l'exportation.
CAPTURES = {
    "mbx": FIXTURES / "max_hd4_mbx_8.5.4.json",
    "dome": FIXTURES / "dome_pro_duo_8.5.4.json",
    "hd2": FIXTURES / "max_hd2_8.5.4.json",
}

_ABSENT = {"stat": "fail", "code": 999, "message": "unknown function"}


class ClientRejeu(RouterApiPeplinkClient):
    """Le client réel, dont seul le transport est remplacé par la capture."""

    def __init__(self, router: RouterConfig, capture: dict) -> None:
        super().__init__(router)
        self._capture = capture
        self.endpoints_demandes: list[str] = []

    async def _authenticate(self) -> None:
        self._authenticated = True

    async def _request(
        self, endpoint: str, params: dict | None = None, corps: dict | None = None,
    ) -> dict:
        # `status.pepvpn?infoType=peer` a été capté sous `status.pepvpn.peer`,
        # et une lecture en POST sous `<endpoint>.<connId>`.
        cle = endpoint
        if params and params.get("infoType"):
            cle = f"{endpoint}.{params['infoType']}"
        if corps and "connId" in corps:
            cle = f"{endpoint}.{corps['connId']}"
        self.endpoints_demandes.append(cle)
        return self._capture.get(cle, _ABSENT)


class Rejeu:
    """Un équipement rejoué de bout en bout, avec ses routes prêtes à lire."""

    # L'instant du premier cycle, et le pas entre deux : une période réaliste,
    # posée. Le temps du rejeu ne dépend d'aucune horloge de machine.
    DEBUT = datetime(2026, 9, 19, 10, 53, 25, tzinfo=timezone.utc)
    PAS = timedelta(seconds=5)

    def __init__(self, nom: str, chemin: Path, dossier: Path) -> None:
        self.nom = nom
        self.capture = json.loads(chemin.read_text(encoding="utf-8"))
        # Le rejeu démarre à l'heure de SA capture, telle que le routeur la
        # publiait : les trois captures ne datent pas du même jour, et une
        # horloge commune faisait lire aux rejeux un routeur décalé de
        # plusieurs jours — alerte d'horloge comprise (R-3.9).
        horaire = ((self.capture.get("status.system.info") or {}).get("response") or {}).get("systemTime") or {}
        instant = horaire.get("timestamp") if isinstance(horaire, dict) else None
        self.maintenant = (
            datetime.fromtimestamp(int(instant), tz=timezone.utc) if instant else self.DEBUT
        )
        self._horloge_reelle = models._horloge
        self.repo = SampleRepository(str(dossier / f"{nom}.db"))
        self.registry = DeviceRegistry(default_mode="router")
        self.poller = Poller(self.registry, self.repo, interval_seconds=0.5)
        self.client: ClientRejeu | None = None

    async def __aenter__(self) -> Rejeu:
        # L'horloge est posée EN DERNIER, et tout ce qui la précède est
        # rattrapé : si `connect()` levait après le remplacement, `__aexit__`
        # n'était jamais appelé et tous les tests suivants tournaient sur une
        # horloge gelée au 19 septembre 2026.
        try:
            await self.repo.connect()
            config = RouterConfig(id=self.nom, name=self.nom.upper(), mode="router", host="10.0.0.1")
            record = await self.registry.add(config)
            self.client = ClientRejeu(config, self.capture)
            record.client = self.client
            app = FastAPI()
            app.include_router(routes.router)
            app.state.repo = self.repo
            app.state.poller = self.poller
            app.state.registry = self.registry
            self._http = AsyncClient(transport=ASGITransport(app=app), base_url="http://rejeu/api")
        except BaseException:
            await self.registry.close()
            await self.repo.close()
            raise
        models._horloge = lambda: self.maintenant
        # L'adresse du poste vers le routeur se lisait par un vrai connect UDP,
        # et entrait dans l'export de l'écran : le rejeu dépendait de la table
        # de routage de la machine (C-46). Figée, comme l'horloge.
        self._ip_locale_reelle = router_api_module.ip_locale_vers
        router_api_module.ip_locale_vers = lambda base: "10.0.0.250"
        return self

    async def __aexit__(self, *_exc: object) -> None:
        models._horloge = self._horloge_reelle
        router_api_module.ip_locale_vers = self._ip_locale_reelle
        await self._http.aclose()
        await self.registry.close()
        await self.repo.close()

    async def cycles(self, n: int) -> None:
        for _ in range(n):
            await self.poller._poll_once()
            self.maintenant += self.PAS

    async def lire(self, chemin: str) -> tuple[int, object]:
        reponse = await self._http.get(chemin)
        return reponse.status_code, reponse.json()
