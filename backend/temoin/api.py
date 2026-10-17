"""L'API du témoin Ethernet : un exploitant, un poste, servie sur 127.0.0.1.

Elle relie la découverte, la connexion, la collecte, les règles et les exports,
et sert l'interface statique à la racine. Jamais un endpoint `config.*` du
routeur ; ni la série de l'équipement, ni le nom d'un WAN, ni un identifiant ne
sortent par une réponse ou par le journal : le lien se dit « WAN n »,
l'équipement par son modèle.

Le dépôt appartient à l'appelant : il l'ouvre avant `creer_app` et le ferme
après l'arrêt de l'application. L'arrêt, lui, annule la collecte et ferme le
client du routeur.
"""

from __future__ import annotations

import asyncio
import ipaddress
import logging
from contextlib import asynccontextmanager
from datetime import datetime, timedelta
from pathlib import Path
from typing import Literal
from urllib.parse import urlsplit

from fastapi import Depends, FastAPI, HTTPException, Request, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from app import coffre
from app.api.security import HoteDeConfianceMiddleware, hote_de_confiance
from app.coffre import Identifiants
from app.models import utcnow
from app.peplink.router_api import CredentialsRefusedError
from temoin import __version__, connexion, export, regles
from temoin.collecte import Collecte
from temoin.stockage import Depot

log = logging.getLogger(__name__)

# Le sondage de l'écran (3 s) ne relance la découverte du réseau qu'au-delà.
CACHE_RECHERCHE_S = 10.0
# Une collecte tombée sur une erreur imprévue repart après ce délai.
RELANCE_S = 30.0
_FENETRES = {"tout": None, "1h": timedelta(hours=1), "24h": timedelta(hours=24)}


def _iso(d: datetime | None) -> str | None:
    return None if d is None else d.isoformat()


def _lien(wan_id: str) -> str:
    return f"WAN {wan_id}"


def _bande_json(segments: list[regles.Segment]) -> list[dict]:
    return [{"debut": _iso(s.debut), "fin": _iso(s.fin), "etat": s.etat} for s in segments]


def _coupures_json(coupures: list[regles.Coupure]) -> list[dict]:
    return [
        {"debut": _iso(c.debut), "fin": _iso(c.fin), "genre": c.genre,
         "cause": c.cause, "duree_s": c.duree_s}
        for c in coupures
    ]


async def _meme_poste(request: Request) -> None:
    """Les routes qui écrivent : depuis ce poste, et pas pour une page tierce.

    Même règle que `require_loopback` du superviseur, sans ses réglages : une
    page d'une autre origine ne doit pas pouvoir déclencher une connexion (cinq
    refus verrouillent l'accès admin du routeur).
    """
    client = request.client.host if request.client else None
    try:
        local = client is not None and ipaddress.ip_address(client).is_loopback
    except ValueError:
        local = False
    if not local:
        raise HTTPException(403, "Opération réservée au poste qui héberge le témoin.")
    origine = request.headers.get("origin")
    hote = request.headers.get("host")
    intersite = request.headers.get("sec-fetch-site") == "cross-site" or (
        origine is not None
        and not (urlsplit(origine).netloc == hote and hote_de_confiance(hote, []))
    )
    if intersite:
        log.warning("Route refusée à une page tierce (%s)", request.url.path)
        raise HTTPException(403, "Cette opération ne peut pas être déclenchée depuis une autre page.")


class DemandeConnexion(BaseModel):
    hote: str = Field(min_length=1, max_length=253)
    username: str | None = None
    password: str | None = None
    client_id: str | None = None
    client_secret: str | None = None
    retenir: bool = True


class DemandeLien(BaseModel):
    wan_id: str = Field(min_length=1, max_length=16)


async def _fermer_client(client) -> None:
    if client is None:
        return
    try:
        await client.aclose()
    except Exception:
        log.exception("Fermeture du client du routeur en échec")


class _Session:
    """L'état en mémoire : un routeur, un lien, une collecte au plus."""

    def __init__(self, depot: Depot, *, chercher, connecter, liens, horloge,
                 fabrique_collecte, sommeil) -> None:
        self.depot = depot
        self._chercher = chercher
        self._connecter = connecter
        self.liens = liens
        self.horloge = horloge
        self._fabrique = fabrique_collecte
        self._sommeil = sommeil
        self.verrou = asyncio.Lock()
        self.client = None
        self.info = None  # DeviceInfo : sa série ne quitte jamais cet objet
        self.hote: str | None = None
        self.hote_a_identifier: str | None = None
        self.wan_id: str | None = None
        self.observation: int | None = None
        self.collecte = None
        # Les liens lus UNE fois à la connexion : le sondage de l'écran ne
        # réinterroge pas le routeur.
        self.liens_proposes: list[dict] = []
        self.wan_retenu: str | None = None  # le dernier lien choisi pour cet équipement
        self._tache: asyncio.Task | None = None
        self._recherche: tuple[datetime, list[dict]] | None = None

    async def equipements(self) -> list[dict]:
        maintenant = self.horloge()
        if self._recherche is not None:
            vue_a, trouves = self._recherche
            if (maintenant - vue_a).total_seconds() < CACHE_RECHERCHE_S:
                return trouves
        trouves = await self._chercher(depot=self.depot)
        self._recherche = (maintenant, trouves)
        return trouves

    async def connecter(self, hote: str, ident: Identifiants | None, retenir: bool) -> None:
        await self.fermer()
        self.hote_a_identifier = None
        try:
            client, info = await self._connecter(hote, ident, retenir, self.depot)
        except connexion.RefusIdentifiants:
            self.hote_a_identifier = hote
            raise
        try:
            proposes = await self.liens(client)
        except CredentialsRefusedError:
            await _fermer_client(client)
            self.hote_a_identifier = hote
            raise connexion.RefusIdentifiants(
                "Identifiants refusés à la lecture des liens."
            ) from None
        except Exception as exc:
            await _fermer_client(client)
            raise connexion.Injoignable(
                f"Lecture des liens impossible ({type(exc).__name__})."
            ) from None
        self.client, self.info, self.hote = client, info, hote
        self.liens_proposes = proposes
        self.wan_retenu = await self.depot.lien_de(info.serial)

    def lien_retenu(self) -> str | None:
        """Le lien à présélectionner : le dernier choisi s'il est encore offert,
        sinon le seul offert. L'écran décide ; rien ne démarre ici."""
        offerts = [str(lien["wan_id"]) for lien in self.liens_proposes]
        if self.wan_retenu in offerts:
            return self.wan_retenu
        return offerts[0] if len(offerts) == 1 else None

    async def mesurer(self, wan_id: str, observation: int) -> None:
        await self._arreter_collecte()
        self.wan_id, self.observation = wan_id, observation
        self._tache = asyncio.create_task(self._veiller(), name="temoin-collecte")

    async def _veiller(self) -> None:
        """Fait tourner la collecte ; ne meurt jamais en silence."""
        moi = asyncio.current_task()
        client, observation, wan_id = self.client, self.observation, self.wan_id
        while True:
            try:
                self.collecte = self._fabrique(client, self.depot, observation, wan_id)
                await self.collecte.boucle()
                return
            except CredentialsRefusedError:
                # Jamais de relance : on redemande les identifiants.
                log.warning("Identifiants refusés pendant la collecte ; arrêt de la mesure.")
                async with self.verrou:
                    # Une connexion ou une mesure plus récente a pu prendre la
                    # place : on ne touche alors à rien.
                    if self._tache is not moi or self.client is not client:
                        return
                    self._tache = None
                    hote = self.hote
                    await self.fermer()
                    self.hote_a_identifier = hote
                return
            except Exception:
                log.exception("Collecte interrompue ; reprise dans %.0f s", RELANCE_S)
                await self._sommeil(RELANCE_S)

    async def _arreter_collecte(self) -> None:
        tache, self._tache = self._tache, None
        if tache is not None and not tache.done():
            tache.cancel()
            try:
                await tache
            except asyncio.CancelledError:
                pass
            except Exception:
                log.exception("Collecte arrêtée sur une erreur")
        self.collecte = None
        self.wan_id = self.observation = None

    async def fermer(self) -> None:
        await self._arreter_collecte()
        client, self.client = self.client, None
        self.info = self.hote = self.wan_retenu = None
        self.liens_proposes = []
        await _fermer_client(client)

    def modele(self) -> str | None:
        return self.info.model if self.info is not None else None

    async def etat(self) -> dict:
        if self.client is not None and self.observation is not None:
            return {"phase": "mesure", "observation": self.observation,
                    "lien": _lien(self.wan_id), "modele": self.modele()}
        if self.client is not None:
            return {"phase": "choix_lien", "liens": self.liens_proposes,
                    "modele": self.modele(), "lien_retenu": self.lien_retenu()}
        if self.hote_a_identifier is not None:
            return {"phase": "identifiants", "hote": self.hote_a_identifier}
        return {"phase": "recherche", "equipements": await self.equipements()}


def creer_app(
    depot: Depot,
    *,
    interface: Path,
    chercher=connexion.chercher,
    connecter=connexion.connecter,
    liens=connexion.liens_ethernet,
    horloge=utcnow,
    fabrique_collecte=None,
    sommeil=asyncio.sleep,
) -> FastAPI:
    """L'application. `depot` est déjà connecté ; l'appelant le fermera."""
    if fabrique_collecte is None:
        def fabrique_collecte(client, depot_, observation, wan_id):
            return Collecte(client, depot_, observation, wan_id, horloge=horloge)

    session = _Session(depot, chercher=chercher, connecter=connecter, liens=liens,
                       horloge=horloge, fabrique_collecte=fabrique_collecte,
                       sommeil=sommeil)

    @asynccontextmanager
    async def cycle(_app: FastAPI):
        yield
        # Le dépôt n'est PAS fermé ici : il appartient à l'appelant.
        await session.fermer()

    app = FastAPI(title="Témoin de lien Ethernet", version=__version__, lifespan=cycle)
    # Parade au DNS rebinding : seuls localhost et les adresses IP littérales.
    app.add_middleware(HoteDeConfianceMiddleware, noms=lambda: [])
    app.state.session = session

    async def _observation(ident: int) -> dict:
        for o in await depot.observations():
            if o["id"] == ident:
                return o
        raise HTTPException(404, "Observation inconnue.")

    @app.get("/api/sante")
    async def sante() -> dict:
        return {"status": "ok", "version": __version__, "coffre": coffre.nom_du_trousseau()}

    @app.get("/api/etat")
    async def etat() -> dict:
        return await session.etat()

    @app.post("/api/connexion", dependencies=[Depends(_meme_poste)])
    async def poster_connexion(demande: DemandeConnexion) -> dict:
        ident = None
        if demande.username or demande.client_id:
            ident = Identifiants(
                username=demande.username, password=demande.password,
                client_id=demande.client_id, client_secret=demande.client_secret,
            )
        async with session.verrou:
            try:
                await session.connecter(demande.hote.strip(), ident, demande.retenir)
            except connexion.RefusIdentifiants as exc:
                raise HTTPException(401, str(exc)) from None
            except connexion.Injoignable as exc:
                raise HTTPException(502, str(exc)) from None
            return await session.etat()

    @app.post("/api/lien", dependencies=[Depends(_meme_poste)])
    async def poster_lien(demande: DemandeLien) -> dict:
        async with session.verrou:
            client, info = session.client, session.info
            if client is None or info is None:
                raise HTTPException(409, "Aucun équipement connecté.")
            proposes = {str(lien["wan_id"]) for lien in session.liens_proposes}
            if demande.wan_id not in proposes:
                raise HTTPException(422, "Ce lien n'est pas un WAN Ethernet de l'équipement.")
            obs = await depot.observation_courante(info.serial, demande.wan_id, horloge())
            await depot.memoriser_lien(info.serial, demande.wan_id)
            if session.client is not client:
                raise HTTPException(409, "La connexion à l'équipement a changé.")
            session.wan_retenu = demande.wan_id
            await session.mesurer(demande.wan_id, obs)
            return {"observation": obs, "lien": _lien(demande.wan_id)}

    @app.post("/api/observations/nouvelle", dependencies=[Depends(_meme_poste)])
    async def nouvelle() -> dict:
        async with session.verrou:
            client, info, wan_id = session.client, session.info, session.wan_id
            if client is None or info is None or session.observation is None:
                raise HTTPException(409, "Aucune mesure en cours.")
            obs = await depot.nouvelle_observation(info.serial, wan_id, horloge())
            if session.client is not client:
                raise HTTPException(409, "La connexion à l'équipement a changé.")
            await session.mesurer(wan_id, obs)
            return {"observation": obs, "lien": _lien(wan_id)}

    @app.get("/api/observations")
    async def observations() -> list[dict]:
        return [
            {"id": o["id"], "modele": o["modele"], "lien": _lien(o["wan_id"]),
             "debut": _iso(o["debut"]), "dernier": _iso(o["dernier"]),
             "en_cours": o["id"] == session.observation}
            for o in await depot.observations()
        ]

    @app.get("/api/observation")
    async def observation(
        id: int | None = None, fenetre: Literal["tout", "1h", "24h"] = "tout"
    ) -> dict:
        if id is None:
            if session.observation is None:
                raise HTTPException(404, "Aucune observation en cours.")
            id = session.observation
        o = await _observation(id)
        vivante = id == session.observation
        maintenant = horloge()
        tous = await depot.releves(id)
        # La fin de la bande : maintenant pour la mesure en cours, le dernier
        # relevé pour une observation passée (sinon tout l'après serait un trou).
        fin = maintenant if vivante or not tous else tous[-1].ts
        faits = await depot.faits(id)
        releves = tous
        largeur = _FENETRES[fenetre]
        if largeur is not None:
            depuis = fin - largeur
            releves = [r for r in tous if r.ts >= depuis]
            faits = [f for f in faits if f.ts >= depuis]
        segments = regles.bande(releves, fin, faits)
        liste = regles.coupures(releves, faits)
        # Le verdict porte sur toute l'observation : « stable depuis » ne se
        # tronque pas à la fenêtre affichée.
        verdict = regles.verdict(tous, maintenant)
        collecte = session.collecte if vivante else None
        return {
            "id": id,
            "fenetre": fenetre,
            "lien": _lien(o["wan_id"]),
            "modele": o["modele"],
            "debut": _iso(o["debut"]),
            "dernier": _iso(o["dernier"]),
            "verdict": {**verdict, "depuis": _iso(verdict["depuis"])},
            "chiffres": regles.chiffres(segments, liste),
            "bande": _bande_json(segments),
            "coupures": _coupures_json(liste),
            "etat_lien": collecte.etat_lien if collecte is not None else None,
            "horloge_decalee": bool(collecte.horloge_decalee) if collecte is not None else False,
        }

    async def _csv(ident: int, genre: str) -> Response:
        o = await _observation(ident)
        vivante = ident == session.observation and session.info is not None
        entete = {
            "modele": o["modele"],
            "firmware": session.info.firmware if vivante else None,
            "wan": o["wan_id"],
            "debut": o["debut"],
            "fin": o["dernier"],
        }
        releves = await depot.releves(ident)
        if genre == "coupures":
            texte = export.coupures_csv(entete, regles.coupures(releves, await depot.faits(ident)))
        else:
            texte = export.releves_csv(entete, releves)
        return Response(
            content=texte.encode("utf-8"),
            media_type="text/csv; charset=utf-8",
            headers={"Content-Disposition": f'attachment; filename="temoin-{genre}-{ident}.csv"'},
        )

    @app.get("/api/observations/{ident}/coupures.csv")
    async def coupures_csv(ident: int) -> Response:
        return await _csv(ident, "coupures")

    @app.get("/api/observations/{ident}/releves.csv")
    async def releves_csv(ident: int) -> Response:
        return await _csv(ident, "releves")

    # Monté APRÈS les routes : /api garde la priorité.
    if Path(interface).is_dir():
        app.mount("/", StaticFiles(directory=interface, html=True), name="interface")
    return app
