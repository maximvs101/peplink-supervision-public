"""Routes REST de supervision."""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Path, Response
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from ..discovery.probe import discover
from .. import coffre
from ..pairing.service import PairingError, pair, reconnecter
from ..poller import _JOURNAL_INTERVALLE_S
from ..peplink.router_api import (
    CredentialsRefusedError, EndpointRefusedError, hote_canonique, parse_peers, redact,
)
from ..fleet import RouterConfig
from ..models import (
    Episode,
    nom_du_reseau,
    EventLog,
    LinkChurn,
    Session,
    SessionSummary,
    Timeline,
    TimelineLane,
    TimelineSlice,
    DeviceStatus,
    DiscoveredDevice,
    HistorySeries,
    RouterSummary,
    utcnow,
)
from ..sessions import _identifiant_reseau, build_summary
from ..storage.db import METRICS
from .security import est_poste_local, require_loopback

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api", tags=["supervision"])


# Garde-fou ABSOLU du journal, en complément de la règle du routeur.
#
# Un épisode s'ouvre d'abord sur la règle que le routeur applique lui-même :
# dépasser de plus de `cutoff_ms` la latence du lien le plus rapide du cycle. Cet
# écart est aveugle à un cas, et c'est pourquoi ce nombre existe : un bond dont
# TOUS les liens sont mauvais se ressemble parfaitement, et aucun n'y dépasse
# personne. Une seconde d'aller-retour, et non les 500 ms du cutoff : ce
# dernier est DIFFÉRENTIEL, et le transposer en absolu dénoncerait un bond dont
# chaque lien siège légitimement à 550 ms. C'est la même valeur que le garde-fou
# du bandeau, et pour la même raison.
#
# Au-delà, un aller-retour est excessif quel que soit le trajet : plus aucun
# tampon de bonding ne tient un usage temps réel. Publié dans `RouterSummary` et
# dans `EventLog`, pour que l'interface n'ait pas à le redéclarer.
#
# Ce bloc a vécu deux cents lignes plus bas, au-dessus de `_EPISODES_MAX`, qu'il
# ne décrit pas : il y surplombait une constante sans rapport et laissait celle
# qu'il explique sans justification.
_SEUIL_EPISODE_MS = 1000.0


# Aucune borne avant cette date : bien avant tout relevé possible, et loin du
# bord du calendrier que les fenêtres calculées (fin − 24 h, début − 15 s)
# franchiraient. L'autre bord reste ouvert : l'an 9999 est une fin valide.
_BORNE_MIN = datetime(2000, 1, 1)


def _en_utc(instant: datetime | None) -> datetime | None:
    """Une borne de fenêtre, ramenée à un instant CONSCIENT de son fuseau.

    `?since=2026-09-11T20:00:00` — sans `Z` ni décalage — produisait un
    `datetime` naïf. Mesuré : `/events` et `/timeline` rendaient **500**
    (`TypeError: can't compare offset-naive and offset-aware datetimes`), et
    `/history` rendait 200 en passant la borne telle quelle au SQL, donc en
    comparant une chaîne sans décalage à des horodatages qui en portent un.
    Trois routes, trois comportements pour un même paramètre.

    Une borne sans fuseau est lue en UTC, et non dans le fuseau du serveur : ce
    projet ne date qu'en UTC, de la scrutation au stockage, et supposer ici
    l'heure locale de la machine ferait dépendre une relecture du réglage du
    poste.
    """
    if instant is None:
        return None
    # Une borne hors de toute date de relevé possible est une requête mal
    # formée : `0001-01-01` faisait déborder `fin − 24 h` et `début − 15 s`
    # (OverflowError, donc 500) sur /timeline et /events (C-36).
    if instant.replace(tzinfo=None) < _BORNE_MIN:
        raise HTTPException(
            status_code=400, detail=f"Borne hors du calendrier des relevés : {instant.isoformat()}"
        )
    if instant.tzinfo is None:
        return instant.replace(tzinfo=timezone.utc)
    # Une borne DÉJÀ datée doit être convertie, pas laissée telle quelle. Elle
    # part en SQL par `.isoformat()`, où les horodatages sont comparés comme des
    # CHAÎNES — tous écrits en `+00:00`. Mesuré : `?since=…T14:00:00+02:00`
    # et `?since=…T12:00:00Z` désignent le même instant et rendent 220 points
    # contre 653, le premier point passant de 12:00 à 14:00 UTC. La correction
    # précédente n'avait traité que le cas naïf, en affirmant avoir unifié
    # « trois routes, trois comportements ».
    try:
        return instant.astimezone(timezone.utc)
    except OverflowError:
        # `0001-01-01T00:00:00+14:00` : l'instant existe, pas sa version UTC.
        # Python le dit par une exception que rien n'attendait — un 500 pour
        # une borne que l'appelant a choisie, et qu'il peut corriger.
        raise HTTPException(
            status_code=400, detail=f"Borne hors du calendrier : {instant.isoformat()}"
        ) from None



# Fenêtre maximale relue par la bande et le journal. `minutes ≤ 1440` ne
# borne rien dès que `since` est posé — et l'écran pose `since = first_seen`
# pour la session en cours, relue toutes les trente secondes. Mesuré :
# 1 137 ms sur 9 h 40 (15,7 µs par ligne) ; une session de trois semaines —
# elles existent, ancrées sur l'uptime du châssis — ferait 45 à 60 s de SQL
# toutes les trente secondes sur l'unique connexion, et un relevé a déjà
# attendu 233 ms derrière une relecture. La fiche de session a son plafond
# (`_FENETRE_SESSION_S`) ; la bande et le journal reçoivent le même, et le
# DISENT (`capped`) : une fenêtre rognée en silence se lit comme entière.
_FENETRE_RELECTURE_S = 24 * 3600.0


def _fenetre(
    minutes: int, since: datetime | None, until: datetime | None
) -> tuple[datetime, datetime]:
    """Les deux bornes d'une fenêtre de relecture, et LA seule façon de les poser.

    Trois routes prennent le même trio `minutes`/`since`/`until`. `timeline` et
    `events` l'interprétaient correctement, en cinq lignes recopiées à
    l'identique ; `history` avait sa propre version, et elle était fausse :

        debut = since or (maintenant - minutes)     # et non (until - minutes)

    `?until=14:00` seul donnait donc `ts >= 15:53 AND ts < 14:00` — une fenêtre
    INVERSÉE, vide par construction quelle que soit la base. Mesuré sur le
    serveur : `200`, `points=0`, `available=0`, c'est-à-dire indiscernable d'une
    fenêtre où le routeur n'a rien mesuré. `since > until` faisait de même. Un
    même paramètre valait 400 sur deux routes et un mensonge silencieux sur la
    troisième.

    `minutes` glisse depuis la FIN de la fenêtre, pas depuis maintenant : c'est
    la seule lecture qui garde son sens quand `until` est posé, et elle se
    confond avec l'ancienne quand il ne l'est pas.
    """
    fin = until or utcnow()
    try:
        debut = since or (fin - timedelta(minutes=minutes))
    except OverflowError:
        # `?until=0001-01-01T00:00:00Z` : trente minutes avant l'an 1, c'est
        # hors du calendrier. Une borne absurde est une erreur de l'appelant.
        raise HTTPException(
            status_code=400, detail="Borne hors du calendrier : `until` trop ancien."
        ) from None
    if debut >= fin:
        raise HTTPException(status_code=400, detail="La fenêtre est vide : `since` ≥ `until`.")
    return debut, fin


def _plafonnee(debut: datetime, fin: datetime) -> tuple[datetime, bool]:
    """Le début ramené à `_FENETRE_RELECTURE_S` de la fin, et si cela a rogné."""
    plancher = fin - timedelta(seconds=_FENETRE_RELECTURE_S)
    return (plancher, True) if debut < plancher else (debut, False)



def _wan_order(wan_id: str) -> tuple[int, int, str]:
    """Tri naturel des WAN.

    Les identifiants réels sont numériques et un châssis peut en exposer plus de
    neuf (11 sur un MAX HD4 MBX) : un tri lexicographique placerait "10" avant
    "2". Les identifiants du simulateur ("cellular-1") restent triés par texte.
    """
    return (0, int(wan_id), "") if wan_id.isdigit() else (1, 0, wan_id)


def _sans_serie(info, local: bool):
    """La fiche d'identité, sans le numéro de série hors du poste local.

    `GET /routers` et `GET /devices` sont ouvertes au réseau — l'écran se
    consulte depuis un autre poste — alors que `/devices/known`, qui liste les
    séries appairées, est réservée au poste local (C-37). Les deux premières
    publiaient pourtant la série de chaque équipement à tout le réseau.
    """
    if info is None or local or info.serial is None:
        return info
    return info.model_copy(update={"serial": None})


def _summary(
    latest: dict[str, dict], cfg: RouterConfig, poller=None, *, local: bool = True,
) -> RouterSummary:
    wans = latest.get(cfg.id, {})
    samples = [wans[k] for k in sorted(wans, key=_wan_order)]
    record = poller.registry.get(cfg.id) if poller else None
    age = (
        (utcnow() - max(s.ts for s in samples)).total_seconds() if samples else None
    )
    # La position des relevés fait foi : elle intègre le GPS du routeur s'il est actif.
    site_lat = samples[0].site_lat if samples else cfg.site_lat
    site_lon = samples[0].site_lon if samples else cfg.site_lon
    return RouterSummary(
        id=cfg.id,
        name=cfg.name,
        # Le mode de CET équipement, et non celui du fichier de configuration.
        mode=cfg.mode or (poller.registry.default_mode if poller else "mock"),
        site_lat=site_lat,
        site_lon=site_lon,
        wans=samples,
        device=_sans_serie(poller.devices.get(cfg.id) if poller else None, local),
        capabilities=poller.capabilities.get(cfg.id) if poller else None,
        status=record.status if record else "en attente",
        error=record.error if record else None,
        age_seconds=round(age, 1) if age is not None else None,
        period_seconds=poller.period_for(cfg.id) if poller else None,
        cycle_seconds=poller.cycle_for(cfg.id) if poller else None,
        bond_routes=list(record.bond_routes) if record else [],
        bond_profiles=list(record.bond_profiles) if record else [],
        lan_clients=record.lan_clients if record else None,
        tunnel_uptime_s=record.tunnel_uptime_s if record else None,
        tunnel_attendu=poller.tunnel_attendu(cfg.id) if poller else None,
        never_connected=poller.never_connected(cfg.id) if poller else [],
        alerts=poller.alerts(cfg.id) if poller else [],
        # Publié, et non recopié de l'autre côté de la couture.
        latency_threshold_ms=_SEUIL_EPISODE_MS,
    )


@router.get("/health")
async def health(request: Request) -> dict:
    state = request.app.state
    return {
        "status": "ok",
        "mode": state.settings.peplink_mode,
        # Le trousseau du système qui retient les identifiants, ou null : sans
        # lui, ils sont à ressaisir à chaque lancement (`app/coffre.py`).
        "coffre": coffre.nom_du_trousseau(),
        "routers": len(state.poller.fleet),
        # La CONSIGNE : la pause entre deux cycles, telle qu'elle est configurée.
        "poll_interval_seconds": state.settings.poll_interval_seconds,
        # La PÉRIODE réellement observée, dont le navigateur déduit son seuil de
        # péremption. Les deux diffèrent, et c'est la seconde qui compte : la
        # boucle scrute PUIS dort, donc la période vaut `cycle + consigne`, et le
        # cycle croît avec le nombre d'équipements. Mesuré sur un MAX HD4 MBX :
        # 2,11 s de cycle, donc 5,1 s de période pour une consigne de 3 s.
        # Publier la consigne faisait attendre 15 s au navigateur là où le relevé
        # le plus récent pouvait dater de 15,1 s — bandeau rouge sur liaison
        # saine, dès un seul équipement. Null tant que deux cycles n'ont pas eu
        # lieu : mieux vaut que l'appelant retombe sur son défaut prudent.
        "cycle_period_seconds": state.poller.period_seconds,
        # Ce que COÛTE une scrutation, hors pause. La période vaut
        # `cycle + consigne` : publier les deux rend leur écart explicable, là
        # où seule la période était mesurée et où une dérive ne pouvait se
        # constater qu'après coup, sans cause.
        "cycle_seconds": state.poller.cycle_seconds,
        # Pas de compte de relevés : `SELECT COUNT(*)` parcourt un index entier
        # — 1,6 ms sur 72 k lignes, et linéaire — toutes les quinze secondes,
        # pour un chiffre qu'aucun écran ne lisait. Un état de santé dit si
        # l'outil tourne, pas combien il a rangé.
    }


@router.get("/routers", response_model=list[RouterSummary])
async def list_routers(request: Request) -> list[RouterSummary]:
    poller = request.app.state.poller
    local = est_poste_local(request)
    return [_summary(poller.latest, cfg, poller, local=local) for cfg in poller.fleet]


@router.get("/routers/{router_id}", response_model=RouterSummary)
async def get_router(router_id: str, request: Request) -> RouterSummary:
    poller = request.app.state.poller
    cfg = poller.get_router(router_id)
    if cfg is None:
        raise HTTPException(status_code=404, detail=f"Routeur inconnu : {router_id}")
    return _summary(poller.latest, cfg, poller, local=est_poste_local(request))


# Les séries appairées identifient la flotte : réservées au poste local, comme
# l'appairage lui-même (C-37).
@router.get("/devices/known", dependencies=[Depends(require_loopback)])
async def known_devices(request: Request) -> list[dict]:
    """Les équipements déjà appairés depuis cette machine, du plus récent au plus ancien.

    La table était écrite à chaque appairage et lue une seule fois, pour une ligne
    de journal au démarrage. L'exploitant qui rallume au matin ne voyait donc
    nulle part « GC-MBX-EXT8-F634, vu hier à 15 h 53 » : il devait relancer une
    découverte et tout retaper. Le mot de passe n'est jamais en base : il est
    au trousseau du système quand l'exploitant l'a voulu, à ressaisir sinon.
    """
    # Ce que le trousseau garde se dit par un booléen, jamais par son contenu.
    return [
        {**c, "credentials_stored": coffre.lire(c["serial"]) is not None}
        for c in await request.app.state.repo.known_devices()
    ]


@router.get("/routers/{router_id}/sessions", response_model=list[Session])
async def get_sessions(
    router_id: str,
    request: Request,
    limit: int = Query(50, ge=1, le=500, description="Sessions rendues au plus"),
) -> list[Session]:
    """Les mises sous tension de cet équipement, de la plus récente à la plus ancienne.

    Chacune est un lieu et une intervention — l'équipement n'est alimenté que
    véhicule à l'arrêt. Elles servent de fenêtre à l'historique : c'est ainsi
    qu'on relit ce qui s'est passé pendant un stationnement précis, plutôt que
    les trente dernières minutes.
    """
    poller = request.app.state.poller
    if poller.get_router(router_id) is None:
        raise HTTPException(status_code=404, detail=f"Routeur inconnu : {router_id}")
    return [
        Session(**row)
        for row in await request.app.state.repo.sessions(router_id, limit=limit)
    ]


# Le plus grand entier qu'une colonne SQLite accepte. Au-delà, aiosqlite lève
# `OverflowError: Python int too large to convert to SQLite INTEGER` DANS son
# thread de base, et la route rendait **500** — mesuré au curl sur le serveur.
# Un identifiant hors bornes est une demande malformée, pas une panne du
# serveur : il vaut 422, comme `abc` le valait déjà.
_ROWID_MAX = 2**63 - 1
_SessionId = Path(..., ge=1, le=_ROWID_MAX, description="Identifiant de session")


# --- emporter une session (R-3.7) -------------------------------------------
#
# « À partager » par défaut : ce qu'on remet à un tiers — client, opérateur —
# pour prouver une intervention. L'opérateur y est nommé par son code réseau,
# le Wi-Fi n'y laisse ni SSID ni BSSID, et la position n'y figure pas — arrondie
# à ~100 m, elle désignait encore le lieu d'intervention (A-12).
# `complet` ajoute le reste, pour un usage interne. Le nom des WAN (numéros de
# ligne, ICCID) n'est jamais en base : il ne peut pas fuir d'ici.
#
# Séparateur « ; », virgule décimale et marque d'encodage : Excel en français
# ouvre le fichier tel quel. Une absence reste une cellule VIDE, jamais un zéro.
_EXPORT_COLONNES = (
    ("instant", "ts"), ("wan", "wan_id"), ("connecte", "connected"), ("type", "kind"),
    ("operateur", None), ("plmn", "plmn"), ("techno", "rat"), ("bande", "band"),
    ("earfcn", "earfcn"), ("pci", "pci"), ("cellule", "cell_id"), ("station", "enb_id"),
    ("tac", "tac"), ("rsrp", "rsrp"), ("rsrq", "rsrq"), ("sinr", "sinr"), ("rssi", "rssi"),
    ("barres", "signal_bars"), ("montant_kbps", "tx_kbps"), ("descendant_kbps", "rx_kbps"),
    ("etat_tunnel", "tunnel_state"), ("latence_bond_ms", "rtt_ms"),
    ("perte_montante_pct", "loss_percent_up"), ("perte_descendante_pct", "loss_percent_down"),
    ("paquets_montants", "packets_up"), ("paquets_descendants", "packets_down"),
    ("latence_lien_ms", "path_latency_ms"), ("bond_montant_kbps", "tunnel_tx_kbps"),
    ("bond_descendant_kbps", "tunnel_rx_kbps"), ("message", None), ("uptime_s", "uptime_s"),
)
_EXPORT_COMPLET = (
    ("operateur_modem", "operator"), ("ssid", "ssid"), ("bssid", "bssid"),
    ("latitude", "site_lat"), ("longitude", "site_lon"),
)


def _cellule(valeur) -> str:
    """Une cellule de CSV : vide pour une absence, virgule décimale."""
    if valeur is None:
        return ""
    if isinstance(valeur, float):
        return f"{valeur:.6f}".rstrip("0").rstrip(".").replace(".", ",")
    return str(valeur)


def _csv(nom: str, entete: list[str], lignes: list[list[str]]) -> Response:
    import csv
    import io

    tampon = io.StringIO()
    ecrivain = csv.writer(tampon, delimiter=";", lineterminator="\n")
    ecrivain.writerow(entete)
    ecrivain.writerows(lignes)
    return Response(
        content=("\ufeff" + tampon.getvalue()).encode("utf-8"),
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{nom}"'},
    )


async def _session_ou_404(request: Request, session_id: int) -> dict:
    lignes = await request.app.state.repo.session_by_id(session_id)
    if lignes is None:
        raise HTTPException(status_code=404, detail=f"Session inconnue : {session_id}")
    return lignes


# Relevés par lot d'export : une requête courte et une mise en forme hors de
# la boucle d'événements par lot (C-23).
_LOT_EXPORT = 5000


def _mettre_en_forme(releves: list[dict], colonnes: list, reseau, complet: bool) -> str:
    """Un lot de relevés en lignes CSV. Pur calcul : tourne hors de la boucle."""
    import csv
    import io

    lignes: list[list[str]] = []
    for r in releves:
        wifi = r["kind"] == "wifi"
        ssid = r["ssid"] or (r["operator"] if wifi else None)
        message = r["status_message"]
        if not complet and wifi and ssid and message:
            message = message.replace(ssid, "…")
        operateur = nom_du_reseau(r["operator"], reseau(r))
        if wifi and not complet:
            operateur = None
        calcule = {"operateur": operateur, "message": message}
        lignes.append([_cellule(calcule[nom] if champ is None else r[champ]) for nom, champ in colonnes])
    tampon = io.StringIO()
    csv.writer(tampon, delimiter=";", lineterminator="\n").writerows(lignes)
    return tampon.getvalue()


@router.get("/sessions/{session_id}/export.csv")
async def export_session(
    request: Request, session_id: int = _SessionId,
    complet: bool = Query(False, description="Tout, y compris ce qu'un tiers n'a pas à voir"),
) -> StreamingResponse:
    """Les relevés d'une session, en CSV (R-3.7).

    Envoyé au fil de l'eau, par lots : l'export relisait toute la session puis
    bâtissait le fichier dans ce handler, sur la boucle d'événements — ~680 ms
    de Python synchrone pour dix heures, des dizaines de secondes pour trois
    semaines, pendant lesquelles la scrutation était figée et les budgets des
    équipements expiraient (C-23).
    """
    import csv
    import io

    # Le complet porte SSID, BSSID et position exacte : réservé au poste local
    # (C-37). L'export « à partager » reste ouvert, c'est son objet.
    if complet:
        await require_loopback(request)
    session = await _session_ou_404(request, session_id)
    repo = request.app.state.repo
    router_id, debut, fin = session["router_id"], session["started_at"], session["last_seen"]
    colonnes = list(_EXPORT_COLONNES) + (list(_EXPORT_COMPLET) if complet else [])
    # Un relevé publié SANS code réseau — juste après une reconnexion — se range
    # sous le réseau d'un libellé connu, comme la fiche le fait (R-3.4).
    reseau = _identifiant_reseau(await repo.export_reseaux(router_id, debut, fin))

    async def flux():
        entete = io.StringIO()
        csv.writer(entete, delimiter=";", lineterminator="\n").writerow([nom for nom, _ in colonnes])
        yield ("\ufeff" + entete.getvalue()).encode("utf-8")
        apres: tuple[str, str, int] | None = None
        while True:
            lot = await repo.export_rows(router_id, debut, fin, apres=apres, limite=_LOT_EXPORT)
            if not lot:
                return
            yield (await asyncio.to_thread(_mettre_en_forme, lot, colonnes, reseau, complet)).encode("utf-8")
            if len(lot) < _LOT_EXPORT:
                return
            apres = (lot[-1]["ts"], lot[-1]["wan_id"], lot[-1]["id"])

    return StreamingResponse(
        flux(), media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="session-{session_id}.csv"'},
    )


@router.get("/sessions/{session_id}/export-routeur.csv")
async def export_session_routeur(request: Request, session_id: int = _SessionId) -> Response:
    """L'heure gardée par le ROUTEUR sur la session, à 10 s (R-3.1, R-3.7) :
    un fichier à part — ce sont des mesures du routeur, sans état de connexion."""
    session = await _session_ou_404(request, session_id)
    points = await request.app.state.repo.export_router_history(
        session["router_id"], session["started_at"], session["last_seen"])
    lignes = [[_cellule(p[c]) for c in ("ts", "wan_id", "path_latency_ms", "rsrp", "rsrq", "sinr", "rat")]
              for p in points]
    return _csv(f"session-{session_id}-routeur.csv",
                ["instant", "wan", "latence_lien_ms", "rsrp", "rsrq", "sinr", "techno"], lignes)


@router.get("/sessions/{session_id}/summary", response_model=SessionSummary)
async def session_summary(
    request: Request, session_id: int = _SessionId
) -> SessionSummary:
    """La fiche d'une session : ce qui la rend comparable à une autre.

    Calculée à la demande depuis les relevés que la session borne — aucune
    colonne de plus, aucune requête à l'équipement. Agréger à l'écriture aurait
    coûté des milliers de calculs pour une fiche qu'on relit rarement.

    L'agrégation part dans un thread. Elle est purement calculatoire, et elle
    était SYNCHRONE dans le handler : sur une session de trois semaines, 3,3 s
    de percentiles et d'intégration figeaient la boucle d'événements — plus de
    scrutation, plus de WebSocket, plus aucune route, le temps que la fiche se
    calcule. `session_rows` borne désormais ce qu'elle lit, mais un calcul de
    plusieurs centaines de millisecondes n'a toujours rien à faire sur la boucle.
    """
    repo = request.app.state.repo
    lignes = await repo.session_by_id(session_id)
    if lignes is None:
        raise HTTPException(status_code=404, detail=f"Session inconnue : {session_id}")
    session = Session(**lignes)
    releves, fenetre = await repo.session_rows(
        session.router_id, lignes["started_at"], lignes["last_seen"]
    )
    # La latence du lien, selon la série du routeur, sur ce qui a été relu.
    lu_de = fenetre[0].isoformat() if fenetre else lignes["started_at"]
    lu_a = fenetre[1].isoformat() if fenetre else lignes["last_seen"]
    series = await repo.latency_series(session.router_id, lu_de, lu_a)
    # Le RSRP des trous de supervision, à l'heure du routeur (R-3.1).
    combles = await repo.filled_rsrp(session.router_id, lu_de, lu_a)
    fiche = await asyncio.to_thread(build_summary, session, releves, fenetre, series, combles)
    # Les manœuvres, du journal du routeur : une désactivation est une coupure
    # que le routeur attribue à l'exploitant (« Disabled »).
    # Sur la fenêtre RELUE, comme tout le reste de la fiche : une fiche
    # partielle (24 h d'une session de trois jours) comptait les manœuvres de
    # toute la session (C-35).
    faits = await repo.router_events(
        session.router_id, datetime.fromisoformat(lu_de), datetime.fromisoformat(lu_a),
    )
    # Encore faut-il que le journal ait été LU sur cette fenêtre : refusé,
    # absent du client, ou écarté pour une horloge décalée, il ne prouve
    # aucune absence — la fiche disait « 0 » sur un journal jamais ouvert.
    # Un fait retenu dans la fenêtre vaut preuve de lecture (bases d'avant le
    # suivi des lectures) ; sinon, une lecture notée entre le début relu et
    # la fin, à un intervalle de lecture près.
    journal_lu = bool(faits) or await repo.journal_read_between(
        session.router_id, datetime.fromisoformat(lu_de),
        datetime.fromisoformat(lu_a) + timedelta(seconds=_JOURNAL_INTERVALLE_S),
    )
    manoeuvres: dict[str, int] = {}
    for f in faits:
        if f.wan_id and (f.kind in ("bascule de SIM", "configuration modifiée")
                         or (f.kind == "coupure" and (f.detail or "").lower() == "disabled")):
            manoeuvres[f.wan_id] = manoeuvres.get(f.wan_id, 0) + 1
    for lien in fiche.links:
        lien.maneuvers = manoeuvres.get(lien.wan_id, 0) if journal_lu else None
    return fiche


class SessionLabel(BaseModel):
    label: str | None = Field(None, max_length=120)


@router.post("/sessions/{session_id}/label", dependencies=[Depends(require_loopback)])
async def name_session(
    body: SessionLabel, request: Request, session_id: int = _SessionId
) -> dict:
    """Nomme une session. C'est ce qui rend deux stationnements comparables :
    « le parking de la mairie » se retrouve, un horodatage non."""
    if not await request.app.state.repo.name_session(session_id, body.label):
        raise HTTPException(status_code=404, detail=f"Session inconnue : {session_id}")
    return {"session": session_id, "label": body.label}


@router.get("/routers/{router_id}/history", response_model=HistorySeries)
async def get_history(
    router_id: str,
    request: Request,
    wan: str = Query(..., description="Identifiant du WAN (ex : cellular-1)"),
    metric: str = Query("sinr", description=f"Une de : {', '.join(METRICS)}"),
    minutes: int = Query(30, ge=1, le=1440),
    since: datetime | None = Query(
        None, description="Début absolu de la fenêtre (ISO 8601). Prioritaire sur `minutes`."
    ),
    until: datetime | None = Query(None, description="Fin absolue de la fenêtre (ISO 8601)."),
    min_packets: int | None = Query(
        None, ge=1,
        description="Pour un taux : nombre de paquets en deçà duquel un sens n'est pas publié. "
        "La règle est celle de l'écran ; le serveur l'applique avant d'alléger la série.",
    ),
) -> HistorySeries:
    if metric not in METRICS:
        raise HTTPException(
            status_code=400, detail=f"Métrique inconnue '{metric}' (attendu : {', '.join(METRICS)})"
        )
    poller = request.app.state.poller
    cfg = poller.get_router(router_id)
    if cfg is None:
        raise HTTPException(status_code=404, detail=f"Routeur inconnu : {router_id}")
    # `since`/`until` ouvrent la relecture d'une session passée. Sans eux, la
    # fenêtre ne pouvait que glisser depuis maintenant, et un équipement allumé
    # six heures produisait un historique dont on ne voyait que la fin.
    debut, fin = _fenetre(minutes, _en_utc(since), _en_utc(until))
    repo = request.app.state.repo
    # Un WAN que ni la configuration, ni le dernier relevé, ni la base n'ont
    # jamais vu n'est pas un WAN silencieux : c'est un identifiant inconnu, et
    # le 200 vide qu'il recevait se lisait comme « rien mesuré sur ce lien ».
    connu = (
        any(w.id == wan for w in cfg.wans)
        or wan in poller.latest.get(router_id, {})
        or await repo.wan_connu(router_id, wan)
    )
    if not connu:
        raise HTTPException(status_code=404, detail=f"WAN inconnu sur {router_id} : {wan}")
    points, available = await repo.history(
        router_id, wan, metric, debut, fin, min_packets=min_packets
    )
    return HistorySeries(
        router_id=router_id, wan_id=wan, metric=metric, points=points, available=available
    )


@router.get("/routers/{router_id}/timeline", response_model=Timeline)
async def get_timeline(
    router_id: str,
    request: Request,
    minutes: int = Query(180, ge=1, le=1440),
    since: datetime | None = Query(None, description="Début absolu (ISO 8601)."),
    until: datetime | None = Query(None, description="Fin absolue (ISO 8601)."),
) -> Timeline:
    """Ce que chaque lien a fait pendant la fenêtre, en tranches de temps égales.

    C'est la réponse à « qu'est-ce qui s'est passé pendant que j'étais là »,
    qui n'en avait aucune à l'écran : l'historique rend UNE grandeur pour UN
    lien, et ne sait pas dire qu'un lien était debout mais hors du bond.

    L'agrégation est faite par SQLite et le nombre de tranches est fixe : la
    réponse pèse le même poids pour une demi-heure que pour une session de
    vingt-quatre heures.
    """
    poller = request.app.state.poller
    if poller.get_router(router_id) is None:
        raise HTTPException(status_code=404, detail=f"Routeur inconnu : {router_id}")

    debut, fin = _fenetre(minutes, _en_utc(since), _en_utc(until))
    debut, rognee = _plafonnee(debut, fin)

    # La cadence observée borne la finesse du découpage : une part plus fine que
    # l'intervalle entre deux relevés est vide par construction, et l'écran
    # l'attribuait alors au superviseur. Le stockage rend la largeur et le nombre
    # de parts RÉELLEMENT employés — les recalculer ici, comme avant, faisait
    # placer les tranches jusqu'à quarante-huit secondes du bon endroit.
    lignes, largeur, parts = await request.app.state.repo.timeline(
        router_id, debut, fin, periode=poller.period_for(router_id) if poller else None
    )

    par_lien: dict[str, list[TimelineSlice]] = {}
    for ligne in lignes:
        par_lien.setdefault(str(ligne["wan_id"]), []).append(
            TimelineSlice(
                t=int(ligne["tranche"]),
                samples=int(ligne["releves"]),
                up=int(ligne["debout"] or 0),
                carrying=int(ligne["portants"] or 0),
                tunnel_known=int(ligne["tunnel_connu"] or 0),
                sinr_worst=ligne["sinr_pire"],
                rsrp_worst=ligne["rsrp_pire"],
                # `or 0` refabriquait le zéro que le SQL vient de cesser
                # d'inventer : une colonne jamais renseignée par le firmware
                # n'est pas un intervalle sans réparation.
                repair=None if ligne["reparation"] is None else int(ligne["reparation"]),
                packets=None if ligne["paquets"] is None else int(ligne["paquets"]),
                disabled=int(ligne["desactives"] or 0),
            )
        )

    return Timeline(
        router_id=router_id,
        started_at=debut,
        ended_at=fin,
        capped=rognee,
        slice_count=parts,
        slice_seconds=largeur,
        # Ordre naturel, comme partout : « 10 » vient après « 2 ».
        lanes=[
            TimelineLane(wan_id=w, slices=par_lien[w])
            for w in sorted(par_lien, key=_wan_order)
        ],
    )



# Épisodes listés au plus. Au-delà, les plus marquants sont gardés et le compte
# des autres est DIT : un journal tronqué en silence se lit comme un journal
# complet.
_EPISODES_MAX = 120

# Ce que « le plus marquant » veut dire, énoncé — parce que la promesse ci-dessus
# n'était pas tenue. La troncature s'appliquait APRÈS un tri par date : elle
# gardait les 120 plus RÉCENTS, et une coupure franche du début de fenêtre
# tombait au profit d'un changement de message de statut de la dernière minute.
# Le SQL, lui, classait bien ses épisodes de latence par pic décroissant — un
# ordre que le retri par date effaçait aussitôt.
#
# Un genre pèse plus qu'une durée : une coupure de deux secondes compte plus
# qu'une latence haute de dix minutes, parce qu'elle interrompt le flux. Les
# rétablissements viennent en dernier : ils n'ont de sens que rapportés à leur
# début, qui est retenu avant eux.
_GRAVITE: dict[str, int] = {
    "coupure": 6,
    "hors du bond": 5,
    "micro-coupure": 4,
    "latence": 3,           # le ROUTEUR a écarté le lien
    "latence haute": 2,     # l'outil juge, le routeur n'a rien fait
    "signalement": 1,
    "désactivation": 1,   # une décision de l'exploitant, pas une panne (C-20)
    "changement d'opérateur": 1,  # un fait, pas une panne (R-2.3)
    "position perdue": 1,
    "retour": 0,
    "de retour dans le bond": 0,
    "position retrouvée": 0,
    # Tirés du journal du ROUTEUR.
    "connexion admin refusée": 5,
    "routeur démarré": 4,
    "bascule de SIM": 3,
    "priorités modifiées": 2,
    "configuration modifiée": 2,
}

# Les faits du journal du routeur qui deviennent des épisodes. Les coupures et
# les retours, l'outil les voit déjà : le routeur n'en apporte que la CAUSE.
# Une connexion admin réussie est le fonctionnement normal, pas un épisode.
_EPISODES_ROUTEUR = {
    "bascule de SIM", "priorités modifiées", "configuration modifiée",
    "routeur démarré", "connexion admin refusée",
}
# Écart maximal entre une coupure vue par l'outil et le fait du routeur qui
# l'explique : mesuré à 4 s (désactivation) et 7 s (contrôle de santé).
_CAUSE_ROUTEUR_S = 15.0


def _marquant(episode: "Episode") -> tuple[int, float, float]:
    """Ce qui fait qu'un épisode survit à la troncature : genre, puis ampleur.

    Un genre inconnu — le jour où le stockage en ajoutera un — se place JUSTE
    au-dessus des rétablissements plutôt qu'en dessous de tout : un épisode
    qu'on ne sait pas classer ne doit pas disparaître en silence.
    """
    duree = (episode.until - episode.at).total_seconds() if episode.until else 0.0
    return (_GRAVITE.get(episode.kind, 1), episode.peak_ms or 0.0, duree)


@router.get("/routers/{router_id}/events", response_model=EventLog)
async def get_events(
    router_id: str,
    request: Request,
    minutes: int = Query(180, ge=1, le=1440),
    since: datetime | None = Query(None, description="Début absolu (ISO 8601)."),
    until: datetime | None = Query(None, description="Fin absolue (ISO 8601)."),
) -> EventLog:
    """Ce qui s'est passé pendant la fenêtre, daté.

    Coupures, rétablissements que seul un `uptime` qui recule trahit, épisodes de
    latence : tout était détecté et compté depuis longtemps, et rien n'était
    jamais listé. Le brassage de cellules, lui, est trop fréquent pour faire des
    lignes — il est compté par lien.
    """
    poller = request.app.state.poller
    if poller.get_router(router_id) is None:
        raise HTTPException(status_code=404, detail=f"Routeur inconnu : {router_id}")

    debut, fin = _fenetre(minutes, _en_utc(since), _en_utc(until))
    debut, rognee = _plafonnee(debut, fin)

    bruts, brassage = await request.app.state.repo.events(
        router_id, debut, fin, _SEUIL_EPISODE_MS
    )

    episodes = [
        Episode(
            kind=str(e["genre"]),
            wan_id=str(e["wan_id"]),
            link_kind=str(e["kind"] or "cellular"),
            at=datetime.fromisoformat(e.get("ts") or e["debut"]),
            until=(
                datetime.fromisoformat(e["fin"])
                if e.get("fin") and e["fin"] != e.get("debut")
                else None
            ),
            samples=int(e.get("releves") or 1),
            after_handover_s=e.get("depuis_bascule"),
            peak_ms=e.get("pic"),
            detail=e.get("status_message"),
        )
        for e in bruts
    ]
    # Le journal du ROUTEUR : sa cause remplace celle que l'outil devinait, et
    # ses manœuvres deviennent des épisodes (revue, scénarios 1 à 4).
    faits = await request.app.state.repo.router_events(
        router_id, debut - timedelta(seconds=_CAUSE_ROUTEUR_S), fin
    )
    coupures_routeur = [f for f in faits if f.kind == "coupure" and f.detail]
    for ep in episodes:
        if ep.kind != "coupure":
            continue
        cause = next((
            f.detail for f in coupures_routeur
            if f.wan_id == ep.wan_id and abs((f.ts - ep.at).total_seconds()) <= _CAUSE_ROUTEUR_S
        ), None)
        if cause:
            ep.detail = cause
    liens = {e.wan_id: e.link_kind for e in episodes}
    episodes += [
        Episode(kind=f.kind, wan_id=f.wan_id, link_kind=liens.get(f.wan_id, "cellular"),
                at=f.ts, detail=f.detail)
        for f in faits if f.kind in _EPISODES_ROUTEUR and debut <= f.ts <= fin
    ]
    # On CHOISIT d'abord — par gravité —, on présente ensuite. L'ordre inverse
    # gardait les plus récents en affirmant garder les plus marquants.
    retenus = sorted(episodes, key=_marquant, reverse=True)[:_EPISODES_MAX]
    # Du plus récent au plus ancien : on relit une intervention en remontant.
    retenus.sort(key=lambda e: e.at, reverse=True)

    return EventLog(
        router_id=router_id,
        started_at=debut,
        ended_at=fin,
        capped=rognee,
        episodes=retenus,
        omitted=max(0, len(episodes) - _EPISODES_MAX),
        churn=[
            LinkChurn(
                wan_id=str(c["wan_id"]),
                link_kind=str(c["kind"] or "cellular"),
                has_cells=bool(c["avec_cellule"]),
                cell_changes=int(c["cellules"] or 0),
                station_changes=None if c["stations"] is None else int(c["stations"]),
                outages=int(c["coupures"] or 0) + int(c["micro"] or 0),
            )
            for c in sorted(brassage, key=lambda c: _wan_order(str(c["wan_id"])))
        ],
        latency_threshold_ms=_SEUIL_EPISODE_MS,
    )


@router.get(
    "/discover",
    response_model=list[DiscoveredDevice],
    dependencies=[Depends(require_loopback)],
)
async def discover_devices(request: Request) -> list[DiscoveredDevice]:
    """Cherche des équipements Peplink sur le réseau local.

    **N'envoie aucun identifiant.** Le sondage se limite aux deux endpoints
    lisibles sans session, au certificat TLS et au cache ARP. Les refus qu'il
    provoque n'alimentent pas le compteur anti-force-brute de l'équipement —
    contrairement aux tentatives de connexion, dont cinq échecs suspendent
    l'accès pendant 5 minutes.

    Restreinte au poste local : elle déclenche une activité réseau et prépare
    l'appairage, deux choses qui n'ont pas à être pilotables depuis le réseau.
    """
    found = await discover()
    # Signaler ce qui est déjà supervisé, pour ne pas proposer un doublon.
    # Sur l'identité de l'hôte, pas sa graphie : un équipement déclaré nu
    # dans le fichier de flotte et découvert avec son schéma est le même.
    known = {hote_canonique(r.host) for r in request.app.state.poller.fleet if r.host}
    for device in found:
        device.already_configured = hote_canonique(device.host) in known
    return found


class PairRequest(BaseModel):
    """Demande d'appairage. Les identifiants ne sont pas conservés en base."""

    # Bornes de longueur : le corps arrive d'un formulaire, et une adresse ou
    # un nom sans limite se retrouvent en base (`known_devices`) puis à l'écran.
    # 253 est la longueur maximale d'un nom d'hôte pleinement qualifié ; le nom
    # d'affichage suit la même borne que le libellé de session (120).
    host: str = Field(..., min_length=1, max_length=253,
                      description="Adresse de l'équipement")
    name: str | None = Field(None, max_length=120,
                             description="Nom d'affichage (défaut : celui du routeur)")
    username: str | None = None
    password: str | None = None
    client_id: str | None = None
    client_secret: str | None = None
    verify_ssl: bool = False
    # Retenir les identifiants dans le trousseau du système (`app/coffre.py`).
    # Faux par défaut : seul un choix explicite écrit quelque part.
    remember: bool = False


# Ce qu'une capture a le droit de lire, et rien d'autre. La famille `config.*`
# restitue la configuration — clés pré-partagées, mots de passe — et n'entre
# JAMAIS ici : la liste est fermée, pas un préfixe. Mais `status.*` n'est pas
# sans secret pour autant : sur le MAX HD2 (8.5.4 build 5808),
# `status.wan.connection` porte `username` et `password` APN sous chaque SIM.
# Ce qui sort d'ici est BRUT ; l'anonymisation, motif par motif et jusque dans
# le texte libre, est l'affaire de qui archive.
#
# La table dit aussi la REQUÊTE réelle : `status.pepvpn.*` se lit avec un
# `infoType`, et le tunnel avec le `tunnelOption` du pair — la même requête que
# le collecteur, sinon la fixture archive une réponse qu'il ne recevra jamais.
_CAPTURABLES: dict[str, tuple[str, dict[str, str] | None]] = {
    "status.wan.connection": ("status.wan.connection", None),
    "status.system.info": ("status.system.info", None),
    "info.capability": ("info.capability", None),
    "status.traffic": ("status.traffic", None),
    "status.port": ("status.port", None),
    "info.location": ("info.location", None),
    "status.wan.latency": ("status.wan.latency", None),
    "status.wan.quality": ("status.wan.quality", None),
    "status.log": ("status.log", None),
    "status.pepvpn.peer": ("status.pepvpn", {"infoType": "peer"}),
    "status.pepvpn.tunnel": ("status.pepvpn", {"infoType": "tunnel"}),
    "status.pepvpn.profile": ("status.pepvpn", {"infoType": "profile"}),
    # Documentés (API 8.5.0) et jamais lus par le collecteur, examinés sur un
    # Dome Pro Duo 8.5.1 par leurs NOMS DE CLÉS seuls : aucun secret. Mais
    # `status.client` porte le nom, la MAC et l'IP de chaque appareil du LAN —
    # des données personnelles : une capture qui en contient ne devient pas une
    # fixture sans anonymisation.
    "status.wan.connection.allowance": ("status.wan.connection.allowance", None),
    "status.client": ("status.client", None),
    "status.lan.profile": ("status.lan.profile", None),
    "info.time": ("info.time", None),
    "info.firmware": ("info.firmware", None),
}


# Les lectures que l'API documente en POST — une seule, et elle LIT : la
# température du module cellulaire (8.4.1+), pour un `connId` (R-1.11).
_CAPTURABLES_POST: dict[str, str] = {
    "status.cellularModule.temperature": "status.cellularModule.temperature",
}


@router.get("/devices/{device_id}/capture/{endpoint}", dependencies=[Depends(require_loopback)])
async def capture_endpoint(
    device_id: str, endpoint: str, request: Request,
    conn_id: int | None = Query(None, ge=1, description="WAN visé, pour une lecture en POST"),
) -> dict:
    """La réponse BRUTE d'un endpoint, pour archiver une fixture.

    Un matériel nouveau est une occasion rare, et ses charges utiles valent plus
    que n'importe quelle hypothèse : c'est ainsi que les deux fixtures existantes
    ont été établies. La lecture passe par le client que l'appairage a construit
    — les identifiants ne transitent par personne — et reste bornée au loopback,
    comme la découverte et l'appairage.

    Ce qui sort d'ici est brut : IMEI, IMSI, ICCID, série, adresses MAC, position,
    numéros de ligne dans les noms de WAN et les lignes de journal, identifiants
    APN. L'anonymisation est l'affaire de qui archive, motif par motif, AVANT
    d'écrire dans le dépôt.
    """
    if endpoint not in _CAPTURABLES and endpoint not in _CAPTURABLES_POST:
        raise HTTPException(404, f"Endpoint hors de la liste de capture : {endpoint}")
    if endpoint in _CAPTURABLES_POST and conn_id is None:
        raise HTTPException(400, f"{endpoint} se lit pour un WAN : conn_id requis")
    record = request.app.state.registry.get(device_id)
    if record is None or record.client is None:
        raise HTTPException(404, "Équipement inconnu ou sans client")
    lire = getattr(record.client, "_get", None)
    if lire is None:
        # Le client simulé n'a pas de firmware à lire : le dire, plutôt que de
        # rendre une erreur d'attribut habillée en réponse.
        raise HTTPException(409, "Pas de capture possible sur un équipement simulé")
    corps: dict | None = None
    if endpoint in _CAPTURABLES_POST:
        cible, params, corps = _CAPTURABLES_POST[endpoint], None, {"connId": conn_id}
    else:
        cible, params = _CAPTURABLES[endpoint]
    if endpoint == "status.pepvpn.tunnel":
        # Le collecteur lit le tunnel POUR un pair : sans `tunnelOption`, le
        # firmware rend un corps vide qui ne documente rien. On demande d'abord
        # les pairs, comme lui.
        pairs = parse_peers(await lire("status.pepvpn", {"infoType": "peer"}))
        if not pairs:
            raise HTTPException(409, "Aucun pair SpeedFusion déclaré : pas de tunnel à capturer")
        params = {**(params or {}), "tunnelOption": pairs[0][0]}
    try:
        return await (lire(cible, params) if corps is None else lire(cible, params, corps=corps))
    except EndpointRefusedError as exc:
        # Le refus du FIRMWARE est une donnée de capture : il dit ce que ce
        # matériel n'a pas. Il est rendu tel quel, et nommé comme tel.
        return {"stat": "fail", "captured_error": "EndpointRefusedError",
                "message": redact(str(exc))}
    except CredentialsRefusedError as exc:
        # Un refus d'IDENTIFIANTS n'est pas une absence de fonction : archiver
        # douze fois « ce que ce matériel n'a pas » sur un mot de passe qui a
        # changé serait faux douze fois. Erreur franche, et aucune relance —
        # le client a déjà cessé d'essayer.
        raise HTTPException(401, redact(str(exc))) from exc
    except Exception as exc:
        raise HTTPException(502, redact(str(exc))) from exc


@router.get("/devices", response_model=list[DeviceStatus])
async def list_devices(request: Request) -> list[DeviceStatus]:
    """État de chaque équipement supervisé.

    Distingue « en attente », « connecté », « identifiants refusés »,
    « injoignable » et « erreur de configuration » : c'est ce qui dit à
    l'utilisateur s'il doit corriger un mot de passe, vérifier un raccordement
    ou revoir la déclaration de l'équipement.

    La série, au poste local seulement (voir `_sans_serie`) : `null` ailleurs.
    """
    local = est_poste_local(request)
    return [
        DeviceStatus(
            id=record.id,
            name=record.config.name,
            host=record.config.host,
            serial=record.config.serial if local else None,
            mode=record.config.mode or request.app.state.settings.peplink_mode,
            source=record.config.source,
            status=record.status,
            error=record.error,
            device=_sans_serie(record.info, local),
        )
        for record in request.app.state.registry.snapshot()
    ]


@router.post(
    "/devices/pair",
    response_model=DeviceStatus,
    dependencies=[Depends(require_loopback)],
)
async def pair_device(body: PairRequest, request: Request) -> DeviceStatus:
    """Met un équipement en supervision, sans redémarrage.

    **Une seule tentative de connexion**, celle-ci. En cas de refus, l'erreur
    remonte sans nouvelle tentative : cinq échecs suspendent l'accès à
    l'équipement pendant cinq minutes, y compris avec les bons identifiants.

    Les identifiants restent en mémoire pour la durée de la session. Ils ne sont
    jamais écrits en clair : `remember` les confie au trousseau du système
    (`app/coffre.py`), et son absence efface ce qu'il gardait.
    """
    state = request.app.state
    try:
        record = await pair(
            state.registry, body.host,
            username=body.username, password=body.password,
            client_id=body.client_id, client_secret=body.client_secret,
            name=body.name, verify_ssl=body.verify_ssl,
            # Pour que le ré-appairage purge ce que l'identifiant portait avant.
            poller=state.poller,
        )
    except PairingError as exc:
        # 401 pour un refus d'identifiants, 502 pour un équipement injoignable :
        # l'interface peut ainsi orienter l'utilisateur sans lire le message.
        raise HTTPException(status_code=401 if exc.credentials else 502, detail=str(exc)) from exc

    if record.info and record.info.serial:
        await state.repo.remember_device(
            serial=record.info.serial, device_id=record.id, name=record.config.name,
            host=record.config.host, model=record.info.model, firmware=record.info.firmware,
        )
        # Décoché, c'est aussi un choix : l'entrée d'un appairage précédent est
        # effacée plutôt que laissée à une valeur que l'exploitant croit oubliée.
        if body.remember:
            coffre.retenir(record.info.serial, coffre.Identifiants(
                username=body.username, password=body.password,
                client_id=body.client_id, client_secret=body.client_secret,
            ))
        else:
            coffre.oublier(record.info.serial)
    return _statut(record)


def _statut(record) -> DeviceStatus:
    return DeviceStatus(
        id=record.id, name=record.config.name, host=record.config.host,
        serial=record.config.serial, mode="router", source=record.config.source,
        status=record.status, error=record.error, device=record.info,
    )


@router.post(
    "/devices/known/{serial}/reconnect",
    response_model=DeviceStatus,
    dependencies=[Depends(require_loopback)],
)
async def reconnect_known_device(serial: str, request: Request) -> DeviceStatus:
    """Réappaire un équipement déjà vu avec les identifiants du trousseau.

    Une tentative, comme l'appairage : 401 pour un refus, 502 pour un
    équipement injoignable, 409 quand rien n'est retenu — l'interface ouvre
    alors le formulaire.
    """
    state = request.app.state
    connu = next((c for c in await state.repo.known_devices() if c["serial"] == serial), None)
    if connu is None or not connu.get("host"):
        raise HTTPException(status_code=404, detail="Équipement inconnu de ce poste.")
    if coffre.lire(serial) is None:
        raise HTTPException(status_code=409, detail="Aucun identifiant retenu pour cet équipement.")
    try:
        record = await reconnecter(state.registry, state.repo, connu, poller=state.poller)
    except PairingError as exc:
        raise HTTPException(status_code=401 if exc.credentials else 502, detail=str(exc)) from exc
    return _statut(record)


@router.post("/devices/{device_id}/reauth", dependencies=[Depends(require_loopback)])
async def reauth_device(device_id: str, request: Request) -> dict:
    """Relance la supervision d'un équipement dont les identifiants ont été refusés.

    C'est l'action humaine explicite sans laquelle rien ne retente une connexion.
    Le poller écarte entièrement un équipement verrouillé : cinq tentatives
    ratées suspendent l'accès admin cinq minutes, et il en consommait une par
    cycle — soit un verrouillage en 26 secondes, qui atteignait aussi
    l'exploitant essayant d'ouvrir l'interface web du routeur.

    Rejouer cette route sans avoir corrigé le mot de passe reverrouillera
    l'équipement au cycle suivant, en une seule tentative : c'est voulu.
    """
    state = request.app.state
    record = state.registry.get(device_id)
    if record is None:
        raise HTTPException(status_code=404, detail=f"Équipement inconnu : {device_id}")

    record.auth_blocked = False
    record.status, record.error = "en attente", None
    if record.client is not None and hasattr(record.client, "allow_new_credentials"):
        record.client.allow_new_credentials()
    logger.info("Équipement %s : nouvelle tentative de connexion autorisée", device_id)
    return {"device": device_id, "status": record.status}


@router.delete("/devices/{device_id}", dependencies=[Depends(require_loopback)])
async def unpair_device(device_id: str, request: Request) -> dict:
    """Retire un équipement de la supervision et efface son état courant.

    Les mesures déjà enregistrées sont conservées : c'est un historique, pas une
    donnée de session.
    """
    state = request.app.state
    record = state.registry.get(device_id)
    if record is None:
        raise HTTPException(status_code=404, detail=f"Équipement inconnu : {device_id}")

    serial = record.config.serial
    await state.registry.remove(device_id)
    # Purge des compteurs, du dernier relevé et des abonnés : sans cela, un
    # identifiant réutilisé hériterait de l'état du précédent.
    state.poller.forget(device_id)
    if serial:
        await state.repo.forget_device(serial)
        coffre.oublier(serial)
    return {"removed": device_id}
