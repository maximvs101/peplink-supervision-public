"""Point d'entrée FastAPI de la supervision radio Peplink."""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.staticfiles import StaticFiles

from .api import routes, ws
from .api.security import HoteDeConfianceMiddleware
from .config import get_settings
from .devices.registry import DeviceRegistry
from .fleet import load_fleet
from .poller import Poller
from .storage.db import SampleRepository

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")

# httpx journalise l'URL complète de chaque requête en INFO — or le jeton d'accès
# des routeurs voyage en paramètre d'URL, le firmware refusant l'en-tête
# `Bearer` : ce niveau l'écrirait en clair dans les journaux. Les erreurs restent
# visibles ; le détail des requêtes est de toute façon redondant avec les
# messages du poller.
logging.getLogger("httpx").setLevel(logging.WARNING)

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = get_settings()
    fleet = load_fleet(settings.routers_file, settings.peplink_mode)
    logger.info(
        "Démarrage en mode '%s' — %d routeur(s), scrutation toutes les %.1fs",
        settings.peplink_mode,
        len(fleet),
        settings.poll_interval_seconds,
    )

    repo = SampleRepository(settings.database_file)
    await repo.connect()

    registry = DeviceRegistry(default_mode=settings.peplink_mode)

    # Un équipement mal configuré ne doit pas emporter tout le démarrage :
    # le registre l'enregistre en erreur et la supervision continue sans lui.
    for router in fleet:
        await registry.add(router)

    # Équipements appairés lors d'une session précédente. Leurs identifiants ne
    # sont PAS conservés — ils sont à ressaisir. Les retenir quand même permet de
    # les présenter comme connus plutôt que de les faire redécouvrir.
    for known in await repo.known_devices():
        if registry.find_by_serial(known.get("serial")) is None:
            logger.info(
                "Équipement appairé connu : %s (%s) — identifiants à ressaisir",
                known.get("name") or known.get("serial"), known.get("host"),
            )

    poller = Poller(registry, repo, settings.poll_interval_seconds)
    await poller.start()

    app.state.settings = settings
    app.state.repo = repo
    app.state.registry = registry
    app.state.poller = poller

    try:
        yield
    finally:
        await poller.stop()
        await registry.close()
        await repo.close()
        logger.info("Arrêt propre de la supervision")


app = FastAPI(
    title="Supervision radio 4G/5G — Peplink",
    description=(
        "Récupère les métriques radio des modems Peplink et expose la qualité de "
        "liaison en temps réel."
    ),
    version="0.1.0",
    lifespan=lifespan,
)

_settings = get_settings()

# Les cookies ne peuvent pas être autorisés avec une origine libre.
#
# La spécification CORS interdit la combinaison `Allow-Origin: *` et
# `Allow-Credentials: true`. Starlette, lui, contourne l'interdiction : quand
# `allow_credentials=True` il renvoie l'origine de la requête à la place de "*",
# ce qui autorise en pratique N'IMPORTE QUELLE page ouverte sur la machine à
# interroger cette API avec ses cookies. Mesuré avant correction : une requête
# portant `Origin: https://exemple.invalid` recevait cette origine en réponse,
# assortie de `Allow-Credentials: true`.
#
# L'interface étant servie par ce même backend en production — donc de même
# origine — les cookies inter-origines ne servent à rien ici.
_allow_credentials = not _settings.cors_allows_any_origin
if _settings.cors_allows_any_origin:
    logger.warning(
        "CORS_ORIGINS autorise toute origine : les cookies inter-origines sont "
        "désactivés. Lister les origines explicitement pour les rétablir."
    )

app.add_middleware(
    CORSMiddleware,
    allow_origins=_settings.cors_origin_list,
    allow_credentials=_allow_credentials,
    allow_methods=["GET", "OPTIONS"],
    allow_headers=["*"],
)

# Les réponses sont compressées au-delà d'un kilooctet. Mesuré sur le MAX HD2,
# un onglet ouvert sur le tableau de bord : la bande d'un stationnement de
# quatre heures pesait 75,7 Ko par lecture, deux lectures par minute — 152 Ko,
# soit les deux tiers de tout ce que l'onglet télécharge — et 3,2 Ko compressée.
# Le JSON d'une bande est répétitif par nature : deux cents tranches aux mêmes
# clés, la plupart à `null`. Les fichiers de l'interface, eux, sont mis en cache
# un an par leur empreinte et ne repassent qu'au premier chargement.
app.add_middleware(GZipMiddleware, minimum_size=1024)

# L'en-tête Host, en DERNIER ajouté donc en PREMIER traversé : une requête au
# nom rebasculé est refusée avant toute route, WebSocket comprise (C-3).
app.add_middleware(HoteDeConfianceMiddleware, noms=lambda: get_settings().allowed_host_list)

app.include_router(routes.router)
app.include_router(ws.router)

# Interface web servie directement par le backend si elle a été compilée
# (`npm run build` -> frontend/dist). Permet un déploiement sur un PC de terrain
# avec Python seul : ni Node, ni second serveur, ni CORS. Monté APRÈS les routes
# pour que /api et /ws gardent la priorité.
_FRONTEND_DIST = _settings.frontend_dir


class _FrontendFiles(StaticFiles):
    """Sert l'interface compilée, en interdisant de garder `index.html` en cache.

    Vite donne à chaque fichier compilé un nom porteur d'empreinte — `index-a1b2.js`
    — donc ces fichiers-là peuvent être gardés indéfiniment : leur nom change dès
    que leur contenu change. `index.html`, lui, garde toujours le même nom et
    c'est LUI qui désigne l'empreinte du jour.

    Constaté : après une mise à jour, le navigateur resservait l'`index.html`
    qu'il avait en cache, donc l'ANCIEN bundle — présent sur le disque à côté du
    nouveau. L'interface paraissait ne pas avoir bougé, sans le moindre message,
    et rien à l'écran ne permettait de s'en apercevoir. Sur un PC de terrain mis
    à jour entre deux interventions, c'est superviser avec une version dont on
    croit à tort qu'elle est à jour.
    """

    def is_not_modified(self, response_headers, request_headers) -> bool:  # type: ignore[override]
        if response_headers.get("content-type", "").startswith("text/html"):
            return False
        return super().is_not_modified(response_headers, request_headers)

    def file_response(self, *args, **kwargs):  # type: ignore[override]
        response = super().file_response(*args, **kwargs)
        if response.headers.get("content-type", "").startswith("text/html"):
            response.headers["cache-control"] = "no-cache, must-revalidate"
        else:
            # Nom porteur d'empreinte : le contenu ne changera jamais sous ce nom.
            response.headers["cache-control"] = "public, max-age=31536000, immutable"
        return response


if _FRONTEND_DIST.is_dir():
    app.mount("/", _FrontendFiles(directory=_FRONTEND_DIST, html=True), name="frontend")
    logger.info("Interface web servie depuis %s", _FRONTEND_DIST)
else:
    logger.info(
        "Aucune interface compilée (%s absent) : lancer le serveur de dev Vite, "
        "ou `npm run build` pour la servir ici",
        _FRONTEND_DIST,
    )
