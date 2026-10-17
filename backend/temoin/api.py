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
MOTIF_REFUS_EN_MESURE = "Mesure arrêtée : identifiants refusés par le routeur"
_FENETRES = {"tout": None, "1h": timedelta(hours=1), "24h": timedelta(hours=24)}
# Les fenêtres 1 h et 24 h ne relisent que leur fenêtre, plus cette marge avant
# elle : un redémarrage (sa suite d'injoignables, la renégociation du WAN) ou une
# coupure commencés juste avant la fenêtre s'y lisent comme sur l'observation
# entière, puis la bande est rognée au début de la fenêtre. Un état commencé
# plus tôt que la marge y paraît commencer au début de la marge.
MARGE_FENETRE = timedelta(minutes=15)
# Le verdict lit la fin de l'observation par reculs croissants depuis son
# dernier relevé ; il ne relit plus loin que si son « depuis » touche le début
# de ce qu'il a lu (le verdict ne dépend que des relevés qui se suivent depuis
# la fin : sur un suffixe, il est exact dès qu'il s'arrête avant son début).
_RECULS_VERDICT = (timedelta(hours=1), timedelta(hours=24), None)
_ETATS_PROLONGEABLES = {"stable", "coupe", "injoignable", "desactive"}


def _iso(d: datetime | None) -> str | None:
    return None if d is None else d.isoformat()


def _lien(wan_id: str) -> str:
    return f"WAN {wan_id}"


def _bande_json(segments: list[regles.Segment]) -> list[dict]:
    return [{"debut": _iso(s.debut), "fin": _iso(s.fin), "etat": s.etat} for s in segments]


def _coupures_json(coupures: list[regles.Coupure]) -> list[dict]:
    return [
        {"debut": _iso(c.debut), "fin": _iso(c.fin), "genre": c.genre,
         "cause": c.cause, "duree_s": c.duree_s, "fin_observee": c.fin_observee}
        for c in coupures
    ]


def _coupe(r: regles.Releve) -> bool:
    return r.joignable and not r.desactive and r.connecte is False


def _suffit(releves: list[regles.Releve], v: dict) -> bool:
    """Le verdict calculé sur cette fin de série vaut-il pour toute la série ?

    Oui s'il s'arrête avant son début — et si ce début n'est pas un relevé
    injoignable : une suite d'injoignables coupée par le début de la lecture
    paraîtrait plus courte, et un redémarrage pourrait ne pas s'y voir.
    """
    return v["depuis"] != releves[0].ts and releves[0].joignable


def _rogner(segments: list[regles.Segment], depuis: datetime) -> list[regles.Segment]:
    """La bande à partir de `depuis`."""
    return [
        regles.Segment(max(s.debut, depuis), s.fin, s.etat)
        for s in segments
        if s.fin > depuis
    ]


def _dans_la_fenetre(c: regles.Coupure, depuis: datetime) -> bool:
    if c.genre == "micro-coupure":
        return c.debut >= depuis
    return c.fin is None or c.fin > depuis


def _calculer(
    releves: list[regles.Releve],
    faits: list[regles.FaitRouteur],
    fin: datetime,
    depuis: datetime | None,
) -> tuple[list[regles.Segment], list[regles.Coupure], dict]:
    """Bande, coupures et chiffres (pur : tourne hors de la boucle d'événements)."""
    segments = regles.bande(releves, fin, faits)
    liste = regles.coupures(releves, faits)
    if depuis is not None:
        segments = _rogner(segments, depuis)
        liste = [c for c in liste if _dans_la_fenetre(c, depuis)]
    return segments, liste, regles.chiffres(segments, liste)


# Le calcul de la fenêtre « tout » de l'observation en cours se fait en deux
# morceaux : un début figé (plus vieux que cette marge), calculé une fois, et la
# fin, recalculée à chaque sondage. Le début n'avance que par pas de ce nombre
# de relevés (une heure à 3 s), pour ne pas être recalculé à chaque sondage.
MARGE_FIGEE = timedelta(hours=1)
PAS_FIGE = 1200


def _calculer_tout(
    releves: list[regles.Releve],
    faits: list[regles.FaitRouteur],
    fin: datetime,
    fige: tuple | None,
) -> tuple[list[regles.Segment], list[regles.Coupure], dict, tuple | None]:
    """Bande, coupures et chiffres de toute l'observation, en réutilisant le
    début `fige` = (indice de coupe, heure du relevé coupé, nombre de faits,
    segments, coupures) quand il vaut encore (voir `regles.point_stable`)."""
    if fige is not None:
        c, ts, nb_faits, _, _ = fige
        if nb_faits != len(faits) or c >= len(releves) or releves[c].ts != ts:
            fige = None
    if releves:
        coupe = regles.point_stable(releves, releves[-1].ts - MARGE_FIGEE)
        if coupe is not None and (fige is None or coupe - fige[0] >= PAS_FIGE):
            debut = releves[: coupe + 1]
            fige = (coupe, releves[coupe].ts, len(faits),
                    regles.bande(debut, releves[coupe].ts, faits),
                    regles.coupures(debut, faits))
    if fige is None:
        segments, liste, ch = _calculer(releves, faits, fin, None)
        return segments, liste, ch, None
    c, _, _, segments_fige, coupures_fige = fige
    reste = releves[c:]
    segments = regles.assembler(segments_fige + regles.bande(reste, fin, faits))
    liste = coupures_fige + regles.coupures(reste, faits)
    return segments, liste, regles.chiffres(segments, liste), fige


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
        # Pourquoi l'écran redemande les identifiants, s'il ne le sait pas.
        self.motif: str | None = None
        self.wan_id: str | None = None
        self.observation: int | None = None
        self.collecte = None
        # Les liens lus UNE fois à la connexion : le sondage de l'écran ne
        # réinterroge pas le routeur.
        self.liens_proposes: list[dict] = []
        self.wan_retenu: str | None = None  # le dernier lien choisi pour cet équipement
        self._tache: asyncio.Task | None = None
        self._recherche: tuple[datetime, list[dict]] | None = None
        # Par observation : (dernier relevé examiné, verdict), voir `_verdict`.
        self.verdicts: dict[int, tuple] = {}
        # Les relevés de la dernière observation lue en entier (fenêtre
        # « tout ») : (observation, dernier id lu, relevés triés). Une seule à
        # la fois : une autre observation la remplace.
        self.releves_lus: tuple[int, int, list] | None = None
        # Le début figé du calcul « tout » de l'observation en cours : (id, fige).
        self.fige: tuple[int, tuple | None] | None = None

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
        self.motif = None
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
                    self.motif = MOTIF_REFUS_EN_MESURE
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
            return {"phase": "identifiants", "hote": self.hote_a_identifier,
                    "motif": self.motif}
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
        # Un compte sans son secret serait une vraie tentative de connexion,
        # comptée par le routeur : refusée ici, avant lui.
        if (demande.username and not demande.password) or (
            demande.client_id and not demande.client_secret
        ):
            raise HTTPException(422, "Identifiants incomplets : il manque le mot de passe ou le secret.")
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

    def _retenir_verdict(ident: int, releves: list[regles.Releve], v: dict) -> None:
        # Seul un verdict tiré des relevés eux-mêmes se prolonge ; « aucune
        # mesure » dépend de l'heure du sondage.
        if releves and v["etat"] in _ETATS_PROLONGEABLES:
            session.verdicts[ident] = (releves[-1], v)

    async def _verdict(
        ident: int, dernier: datetime | None, maintenant: datetime,
        lus: list[regles.Releve], complet: bool,
    ) -> dict:
        """Le verdict sur toute l'observation, sans relire toute l'observation.

        Le verdict remonte depuis le dernier relevé, et chaque pas ne regarde
        que deux relevés voisins : calculé sur une fin de la série, il est exact
        dès qu'il s'arrête avant le début de ce qu'il a lu. D'où trois sources,
        de la moins chère à la plus chère :

          1. `lus`, les relevés déjà chargés (`complet` : toute l'observation) ;
          2. le verdict du sondage précédent, prolongé par les seuls relevés
             arrivés depuis : s'ils remontent sans rupture jusqu'à son dernier
             relevé et dans le même état, son « depuis » vaut toujours ;
          3. des reculs croissants depuis le dernier relevé (_RECULS_VERDICT),
             jusqu'à toute l'observation au pire — une fois, au premier sondage.
        """
        if lus:
            v = await asyncio.to_thread(regles.verdict, lus, maintenant)
            if complet or _suffit(lus, v):
                _retenir_verdict(ident, lus, v)
                return v
        elif complet or dernier is None:
            return regles.verdict([], maintenant)
        connu = session.verdicts.get(ident)
        if connu is not None:
            ancre, v_connu = connu
            # Un routeur injoignable peut s'avérer redémarré : la suite repart
            # du premier injoignable, pour que la détection voie toute la suite.
            repere = v_connu["depuis"] if v_connu["etat"] == "injoignable" else ancre.ts
            suite = await depot.releves(ident, depuis=repere)
            v = await asyncio.to_thread(regles.verdict, suite, maintenant)
            if suite and v["depuis"] == suite[0].ts and v["etat"] == v_connu["etat"]:
                v = {"etat": v["etat"], "depuis": v_connu["depuis"]}
            _retenir_verdict(ident, suite, v)
            return v
        for recul in _RECULS_VERDICT:
            depuis = None if recul is None else dernier - recul
            if depuis is not None and lus and depuis >= lus[0].ts:
                continue  # déjà lu
            releves = await depot.releves(ident, depuis=depuis)
            v = await asyncio.to_thread(regles.verdict, releves, maintenant)
            if depuis is None or not releves or _suffit(releves, v):
                _retenir_verdict(ident, releves, v)
                return v
        raise AssertionError("inatteignable : le dernier recul relit tout")

    async def _tous_les_releves(ident: int) -> list[regles.Releve]:
        """Tous les relevés de l'observation, en ne lisant que les nouveaux.

        Les relevés ne sont qu'ajoutés : seules les lignes d'id supérieur au
        dernier lu sont relues. Si l'une d'elles est plus ancienne que la fin de
        la copie (horloge du poste reculée), l'ordre changerait : tout est relu.
        """
        lus = session.releves_lus
        if lus is not None and lus[0] == ident:
            _, dernier_id, copie = lus
            nouveaux, dernier_id = await depot.releves_apres(ident, dernier_id)
            if not nouveaux:
                return copie
            if not copie or nouveaux[0].ts >= copie[-1].ts:
                releves = copie + nouveaux
                session.releves_lus = (ident, dernier_id, releves)
                return releves
        releves, dernier_id = await depot.releves_apres(ident)
        session.releves_lus = (ident, dernier_id, releves)
        return releves

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
        dernier = o["dernier"]
        # La fin de la bande : maintenant pour la mesure en cours, le dernier
        # relevé pour une observation passée (sinon tout l'après serait un trou).
        fin = maintenant if vivante or dernier is None else dernier
        largeur = _FENETRES[fenetre]
        faits = await depot.faits(id)
        fige = None
        if largeur is None:
            depuis = None
            if vivante:
                # La copie tenue à jour ne sert que l'observation en cours : une
                # observation passée ou un rapport ouverts à côté ne la chassent pas.
                releves = await _tous_les_releves(id)
                if session.fige is not None and session.fige[0] == id:
                    fige = session.fige[1]
            else:
                releves = await depot.releves(id)
        else:
            # Seulement la fenêtre et sa marge : une observation de plusieurs
            # jours n'est pas relue à chaque sondage de l'écran.
            depuis = fin - largeur
            charge = depuis - MARGE_FENETRE
            releves = await depot.releves(id, depuis=charge)
            if releves and _coupe(releves[0]) and releves[0].ts > o["debut"]:
                # Une coupure déjà en cours au début de la lecture : sans son
                # vrai début, elle paraîtrait commencer là. On relit depuis le
                # dernier relevé qui ne la montrait pas (marge comprise, pour
                # un redémarrage qui la précéderait).
                avant = await depot.dernier_hors_coupure(id, releves[0].ts)
                charge = None if avant is None else avant - MARGE_FENETRE
                releves = await depot.releves(id, depuis=charge)
            if charge is not None:
                faits = [f for f in faits if f.ts >= charge]
            # Le verdict porte sur toute l'observation : « stable depuis » ne se
            # tronque pas à la fenêtre affichée.
            verdict = await _verdict(id, dernier, maintenant, releves, complet=False)
        if largeur is None and vivante:
            segments, liste, chiffres, fige = await asyncio.to_thread(
                _calculer_tout, releves, faits, fin, fige
            )
            session.fige = (id, fige)
            # Le verdict n'a besoin que de la fin : après la coupe, il se
            # prolonge d'un sondage à l'autre (voir `_verdict`).
            lus = releves if fige is None else releves[fige[0]:]
            verdict = await _verdict(id, dernier, maintenant, lus, complet=fige is None)
        elif largeur is None:
            segments, liste, chiffres = await asyncio.to_thread(
                _calculer, releves, faits, fin, None
            )
            verdict = await _verdict(id, dernier, maintenant, releves, complet=True)
        else:
            segments, liste, chiffres = await asyncio.to_thread(
                _calculer, releves, faits, fin, depuis
            )
        collecte = session.collecte if vivante else None
        if collecte is not None and collecte.etat_lien == "introuvable":
            # Le WAN choisi a disparu de la réponse du routeur (reconfiguré,
            # renuméroté) : aucun relevé n'est rangé, et ce n'est pas le poste
            # qui dort.
            verdict = {"etat": "introuvable", "depuis": dernier}
        return {
            "id": id,
            "fenetre": fenetre,
            "lien": _lien(o["wan_id"]),
            "modele": o["modele"],
            "debut": _iso(o["debut"]),
            "dernier": _iso(dernier),
            "verdict": {**verdict, "depuis": _iso(verdict["depuis"])},
            "chiffres": chiffres,
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
            faits = await depot.faits(ident)
            texte = await asyncio.to_thread(
                lambda: export.coupures_csv(entete, regles.coupures(releves, faits))
            )
        else:
            texte = await asyncio.to_thread(export.releves_csv, entete, releves)
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
