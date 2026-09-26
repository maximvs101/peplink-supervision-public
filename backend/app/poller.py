"""Boucle de scrutation : interroge les routeurs, persiste, diffuse.

Conserve le dernier relevé de chaque WAN en mémoire (vue « live ») et pousse chaque
nouveau relevé aux abonnés WebSocket. Une panne sur un routeur ne doit jamais arrêter
la boucle : les erreurs sont journalisées et la scrutation continue.
"""

from __future__ import annotations

import asyncio
import logging
import time
from datetime import datetime, timedelta
from collections import defaultdict
from typing import NamedTuple

from .devices.registry import DeviceRecord, DeviceRegistry
from .fleet import RouterConfig
from . import alerts as regles_alerte
from .models import STATUT_DESACTIVE, Alert, RadioSample, en_secours, utcnow
import httpx

from .peplink.router_api import (
    _HORLOGE_SERIE_S,
    AuthenticationError,
    CredentialsRefusedError,
    avant_echeance,
    redact,
)
from .sessions import _TROU_MAX_S
from .storage.db import SampleRepository

# Âge au-delà duquel la dernière latence du lien (lue une fois par minute) ne
# décrit plus l'instant : une lecture manquée tolérée, pas deux.
_LATENCE_LIEN_MAX_S = 90.0

logger = logging.getLogger(__name__)

_QUEUE_MAXSIZE = 100

# Sentinelle placée dans la file d'un abonné dont l'équipement disparaît.
_CLOSED = object()

# Nombre de cycles pendant lesquels on redemande l'identité d'un équipement qui
# répond mais ne la livre pas. Un échec PASSAGER — un routeur qui finit de
# démarrer refuse `status.system.info` pendant quelques secondes — ne doit pas
# rendre l'identité inconnue pour la vie du processus ; à l'inverse, un modèle
# qui ne la publie pas ne doit pas coûter deux requêtes par cycle, indéfiniment.
_IDENTIFY_ATTEMPTS = 3
# Ce que le budget d'un équipement garde, après ses lectures, pour écrire et
# publier le relevé : les lectures complémentaires s'arrêtent avant (C-5).
_RESERVE_PUBLICATION_S = 3.0
# Deux écarts d'horloge « identiques » à cette tolérance près : c'est alors le
# poste, commun à tous, qui dérive (C-32).
_HORLOGE_ACCORD_S = 30.0

# Tolérance de rapprochement entre l'instant de mise sous tension déduit de
# l'`uptime` et le début d'une session déjà enregistrée. Deux minutes : l'uptime
# est publié à la seconde, mais les liens ne se lèvent pas tous au même instant
# et le plus ancien peut avoir plusieurs dizaines de secondes d'avance.
_SESSION_MATCH_S = 120.0

# Marge, en secondes, entre l'ancienneté que le châssis DEVRAIT avoir et celle
# qu'il rend, avant de conclure qu'il n'a pas redémarré.
_CHASSIS_MARGE_S = 60.0

# Le journal du ROUTEUR : relu une fois par minute — cinquante lignes suffisent
# largement à cette cadence. Et la règle d'alerte des connexions admin
# refusées : cinq en cinq minutes suspendent l'accès, on prévient à trois.
_JOURNAL_INTERVALLE_S = 60.0
_ADMIN_FENETRE_S = 300.0
_ADMIN_SEUIL = 3

class _Session(NamedTuple):
    """Session d'alimentation en cours, telle que le poller la suit.

    `uptimes` retient l'ancienneté de CHAQUE lien au dernier relevé, et non leur
    seul maximum : c'est la comparaison lien à lien qui distingue un châssis remis
    sous tension d'un lien qui s'en est allé.
    """

    id: int
    uptimes: dict[str, float]
    # Ancienneté du châssis au dernier relevé, quand le routeur la publie.
    chassis: int | None = None
    # Quand cette ancienneté a été LUE (horloge monotone) : relue un cycle sur
    # douze, elle a vieilli d'autant depuis.
    chassis_lu_a: float | None = None


def _redemarrage(
    avant: dict[str, float],
    maintenant: dict[str, float],
    chassis: int | None = None,
    chassis_avant: int | None = None,
) -> bool:
    """Le châssis a-t-il été remis sous tension entre ces deux relevés ?

    Quand le routeur publie son ancienneté, la question n'en est plus une : elle
    recule, ou elle ne recule pas. Tout le raisonnement lien à lien ci-dessous
    n'existait que faute de cette mesure — et sa faiblesse est connue, puisque le
    maximum des liens retombe dès que celui qui le portait quitte le relevé.
    Cette valeur n'est rafraîchie qu'un cycle sur douze, donc elle STAGNE entre
    deux lectures — et c'est loin d'être sans importance. Tant qu'elle stagne,
    elle ne recule pas ; quand elle décidait seule, elle affirmait donc « pas de
    redémarrage » pendant douze cycles alors que les quatre liens venaient de
    retomber à cinq secondes. Vérifié : soixante secondes de relevés du nouveau
    stationnement comptés dans la session précédente, dont la fin était repoussée
    au-delà de l'extinction.
    Un recul du châssis reste une preuve ; son immobilité n'en est pas une, et
    les liens gardent alors la parole.

    Une mise sous tension remet TOUS les liens à zéro ensemble. On exige donc
    qu'aucun lien retrouvé d'un relevé à l'autre n'ait vieilli : il suffit qu'un
    seul continue de compter pour prouver que le châssis, lui, n'a pas redémarré.

    Seuls comptent les liens qui étaient DEBOUT au relevé précédent : un lien
    déjà à zéro n'était pas levé, et le voir grimper à neuf secondes ne prouve
    rien — c'est même ce que fait un lien qui se rétablit pendant que le châssis,
    lui, redémarre. Sans témoin — aucun lien commun, ou tous à zéro —, il ne
    reste que les sommets à comparer, et c'est le comportement d'origine.
    """
    if chassis is not None and chassis_avant is not None and chassis < chassis_avant:
        return True

    temoins = [wan_id for wan_id in maintenant if avant.get(wan_id, 0.0) > 0.0]
    if not temoins:
        # `default=` des deux côtés : `maintenant` pouvait être vide — aucun lien
        # ne publiant son ancienneté — et `max()` levait alors, à chaque cycle,
        # une `ValueError` avalée en avertissement. La session cessait d'être
        # prolongée tant que la situation durait.
        return max(maintenant.values(), default=0.0) < max(avant.values(), default=0.0)
    return all(maintenant[wan_id] < avant[wan_id] for wan_id in temoins)

# Équipements interrogés simultanément. Au-delà, on attend une place : c'est un
# PC de terrain qui héberge la supervision, pas un serveur.
_PARALLEL_DEVICES = 8

# Âge au-delà duquel une position n'est plus une position.
#
# Deux minutes, soit une vingtaine de cycles à la période mesurée (5,14 s). Le
# choix est éditorial et se publie ici : sur un véhicule à l'arrêt, deux minutes
# de retard ne déplacent rien ; en roulant, elles valent quelques kilomètres, et
# c'est précisément le cas où il vaut mieux ne rien dire. Le routeur publie aussi
# l'âge de SA fixation (`info.location.timeElapsed`), que l'outil ne lit pas
# encore : quand il le lira, c'est cette valeur-là qui fera foi, et celle-ci
# restera le garde-fou de la liaison entre nous et lui.
_GPS_MAX_AGE_S = 120.0

# Cadence à laquelle la boucle de surveillance aligne ses tâches sur la flotte.
# Un appairage ou un retrait est ainsi pris en compte en une seconde au plus,
# sans que la scrutation d'un équipement dépende de celle d'un autre.
_RECONCILIATION_S = 1.0

# Un cycle sur douze rejoue `status.system.info` sur un équipement DÉJÀ
# identifié. La fiche ne contient pas que de l'identité : la charge CPU, la
# consommation et l'état des alimentations y vivent aussi, et l'interface les
# affiche sans horodatage à côté de données live. Lue une seule fois au
# démarrage — `_identify` n'est rappelée que tant que `identified` est faux — la
# fiche disait 3,8 % de charge sur un équipement saturé et annonçait toujours
# raccordée une seconde alimentation perdue en mission. C'est la même espèce de
# mensonge que le WAN désactivé affiché connecté : une valeur d'hier présentée
# comme celle d'aujourd'hui.
#
# Le choix de douze est un choix de budget. L'endpoint coûte 420 ms mesurées,
# soit un cinquième d'un cycle (2,11 s pour cinq requêtes HTTPS en série) :
# amorti sur douze cycles il revient à 35 ms, c'est-à-dire 1,7 % du cycle et
# 0,66 % de la période observée de 5,3 s — sous les 3 % du budget dans les deux
# lectures. En contrepartie la fiche a au pire 64 s d'âge, ce qui suffit à voir
# une alimentation tomber ou un CPU saturer pendant une mission.
#
# Le surcoût ne trouble pas non plus le seuil de péremption du navigateur : le
# cycle concerné passe de 2,11 à 2,53 s, une fois sur douze, et `_mark_cycle`
# lisse la période à 30 % — la mesure publiée bouge de quelques dizaines de
# millisecondes, là où le seuil se compte en multiples de la période.
_INFO_REFRESH_CYCLES = 12


def _cause_injoignable(exc: BaseException) -> str:
    """Pourquoi l'équipement ne répond pas, en une phrase qui n'est jamais vide.

    `str(httpx.ConnectTimeout())` est une chaîne VIDE : le journal écrivait
    « Routeur mbx-01 injoignable : » et l'écran gardait la cause précédente —
    vu sur le serveur en service, routeur hors réseau. Les délais d'httpx
    n'ont pas de texte ; on leur en donne un, et pour le reste on remonte la
    chaîne des causes jusqu'au premier mot.
    """
    if isinstance(exc, httpx.ConnectTimeout):
        return "délai de connexion dépassé — l'adresse ne répond pas"
    if isinstance(exc, httpx.ReadTimeout | httpx.WriteTimeout | httpx.PoolTimeout):
        return "délai de réponse dépassé — l'équipement joint ne répond plus"
    courante: BaseException | None = exc
    vues: set[int] = set()
    while courante is not None and id(courante) not in vues:
        texte = redact(str(courante)).strip()
        if texte:
            return texte
        vues.add(id(courante))
        courante = courante.__cause__ or courante.__context__
    return type(exc).__name__


class Poller:
    """Scrute la flotte en continu et tient à jour l'état courant."""

    def __init__(
        self,
        registry: DeviceRegistry,
        repo: SampleRepository,
        interval_seconds: float,
    ) -> None:
        self._registry = registry
        self._repo = repo
        self._interval = max(0.5, interval_seconds)

        # router_id -> wan_id -> dernier relevé
        self.latest: dict[str, dict[str, RadioSample]] = {}
        # compteurs de stabilité : (router_id, wan_id) -> compteur
        self._cell_changes: dict[tuple[str, str], int] = defaultdict(int)
        # La DERNIÈRE cellule connue de chaque lien, et l'instant de son relevé :
        # un lien revenu d'une coupure sur une autre cellule comptait « 0 »,
        # puisque le relevé d'avant — la coupure — n'en portait pas (revue,
        # scénario 2). Au-delà d'un trou de supervision, elle ne vaut plus rien.
        self._derniere_cellule: dict[tuple[str, str], int] = {}
        self._disconnects: dict[tuple[str, str], int] = defaultdict(int)

        self._subscribers: dict[str, set[asyncio.Queue]] = defaultdict(set)
        self._task: asyncio.Task | None = None
        # Une tâche de scrutation par équipement : c'est ce qui rend la cadence
        # de chacun indépendante de la lenteur des autres.
        self._device_tasks: dict[str, asyncio.Task] = {}
        # Période RÉELLEMENT observée entre deux débuts de cycle, PAR ÉQUIPEMENT.
        # Elle n'est pas égale à `interval` : la boucle scrute PUIS dort, donc la
        # période vaut `durée du cycle + interval`. Mesuré : 2,11 s de cycle pour
        # un MAX HD4 MBX (cinq requêtes HTTPS en série), soit 5,1 s de période
        # pour un `interval` de 3 s. Le navigateur en déduit son seuil de
        # péremption : lui publier `interval` lui faisait attendre 15 s là où le
        # relevé le plus récent pouvait légitimement dater de 15,1 s — d'où un
        # bandeau rouge sur une liaison parfaitement saine, dès UN équipement.
        #
        # Par équipement et non pour la flotte : une mesure globale faisait payer
        # à tous la lenteur d'un seul, et relâchait le seuil de péremption des
        # liaisons saines de toute la durée du budget de l'équipement mort.
        self._period_seconds: dict[str, float] = {}
        self._last_cycle_start: dict[str, float] = {}
        # Durée du CYCLE lui-même, hors pause : le temps passé à interroger
        # l'équipement. La période vaut `cycle + consigne`, et seule la période
        # était mesurée — de sorte qu'un écart ne pouvait s'expliquer qu'en
        # devinant. Mesurer les deux rend la décomposition lisible : quand la
        # période bouge sans que la consigne change, c'est le cycle qui a bougé,
        # et on le voit.
        self._cycle_seconds: dict[str, float] = {}
        # Position GPS lue sur le routeur (prioritaire sur celle du fichier de flotte).
        # La dernière position CONNUE, et QUAND elle a été lue. Le second membre
        # manquait : un point acquis une fois était réécrit dans chaque relevé
        # indéfiniment, y compris des heures après que le récepteur eut perdu sa
        # fixation. Sur un véhicule, c'est la seule grandeur de l'outil dont la
        # péremption soit invisible — une position ne « paraît » jamais vieille.
        self._gps: dict[str, tuple[tuple[float, float], float]] = {}
        # Session d'alimentation en cours, par équipement. L'équipement n'est
        # allumé que véhicule à l'arrêt — chaque mise sous tension est un lieu et
        # une intervention.
        self._session: dict[str, _Session] = {}

        # Alertes SUIVIES par équipement — pas celles qui s'affichent. Une
        # condition vraie depuis deux secondes est ici et n'est pas annoncée :
        # c'est `alertes.publiables` qui décide. Les garder séparées est ce qui
        # donne leur durée aux conditions, et la durée est ce qui distingue un
        # fait d'un clignotement.
        self._alertes: dict[str, list[Alert]] = {}
        # Le bond a-t-il porté un lien depuis la mise sous tension ? Lu une
        # fois en base par SESSION, puis tenu à jour par les relevés eux-mêmes
        # — aucune requête par cycle. Tant que la réponse est « non », les
        # règles du bond n'ont pas de bond à surveiller. La clé porte la
        # session : une remise sous tension repart de « on ne sait pas ».
        self._bond_porte: dict[str, tuple[int, bool]] = {}
        # Les WAN connectés au moins une fois dans la session, même mécanique :
        # une lecture en base par session, puis les relevés. Un lien absent de
        # cet ensemble n'a jamais été raccordé depuis l'allumage — un état,
        # pas une panne ; l'écran le lit par `never_connected`.
        self._deja_connectes: dict[str, tuple[int, set[str]]] = {}
        # Dernière lecture du journal du routeur (horloge monotone), et l'alerte
        # des connexions admin refusées qu'il fonde.
        self._journal_lu_a: dict[str, float] = {}
        self._alerte_admin: dict[str, Alert] = {}
        # Horloge du routeur décalée de plus de 60 s (R-3.9).
        self._alerte_horloge: dict[str, Alert] = {}
        # Équipements dont on a déjà mesuré le trou de supervision au démarrage.
        # Le fait appartient au passé : on le constate une fois, pas à chaque
        # cycle.
        self._trou_mesure: set[str] = set()
        # Quand CE processus a démarré : un trou de supervision ne peut pas
        # s'étendre au-delà — ce que le superviseur n'a pas vu APRÈS son
        # démarrage est une injoignabilité, dite ailleurs, pas un arrêt de la
        # supervision.
        self._demarre_a = utcnow()

        # Scrutation parallèle, mais pas illimitée : vingt équipements interrogés
        # d'un coup ouvrent vingt sessions TLS depuis un PC de terrain. Huit tient
        # vingt équipements en une période raisonnable sans saturer la machine.
        self._slots = asyncio.Semaphore(_PARALLEL_DEVICES)
        # Délai accordé à UN équipement. Le client httpx a son propre délai de 15 s
        # par requête, mais un cycle en enchaîne cinq : un équipement mort dont
        # l'adresse est encore routée pouvait donc coûter plus d'une minute. Ce
        # budget-ci borne le tour complet, et il ne s'applique qu'à cet
        # équipement — c'est tout l'objet du parallélisme.
        self._device_budget = max(20.0, self._interval * 4)

    # --- flotte ---
    @property
    def registry(self) -> DeviceRegistry:
        return self._registry

    @property
    def fleet(self) -> list[RouterConfig]:
        return [r.config for r in self._registry.snapshot()]

    def get_router(self, router_id: str) -> RouterConfig | None:
        record = self._registry.get(router_id)
        return record.config if record else None

    @property
    def devices(self) -> dict[str, object]:
        """Identités connues, par équipement (vue de lecture pour les routes)."""
        return {r.id: r.info for r in self._registry.snapshot() if r.info}

    @property
    def capabilities(self) -> dict[str, object]:
        return {r.id: r.capabilities for r in self._registry.snapshot() if r.capabilities}

    async def _lire_journal_routeur(self, record: DeviceRecord, statuses: list) -> None:
        """Relit le journal du routeur une fois par minute, et en tire l'alerte
        des connexions admin refusées."""
        lire = getattr(record.client, "get_router_events", None)
        if lire is None:
            return
        # Une horloge de routeur décalée daterait ses faits au mauvais endroit
        # du journal, loin des coupures qu'ils expliquent : on ne les lit pas
        # tant qu'elle l'est (R-3.9). L'alerte le dit.
        if record.id in self._alerte_horloge:
            return
        maintenant_m = time.monotonic()
        dernier = self._journal_lu_a.get(record.id)
        if dernier is not None and maintenant_m - dernier < _JOURNAL_INTERVALLE_S:
            return
        self._journal_lu_a[record.id] = maintenant_m
        noms = {s.wan_name: s.wan_id for s in statuses if s.wan_name}
        faits = await lire(noms)
        if not self._still_supervised(record):
            return
        await self._repo.insert_router_events(record.id, faits)
        maintenant = utcnow()
        refus = await self._repo.router_events(
            record.id, maintenant - timedelta(seconds=_ADMIN_FENETRE_S), maintenant,
            kinds=("connexion admin refusée",),
        )
        # Un retrait pendant ces awaits ne doit pas ressusciter l'alerte : un
        # ré-appairage en hériterait (C-34).
        if not self._still_supervised(record):
            return
        if len(refus) >= _ADMIN_SEUIL:
            self._alerte_admin[record.id] = Alert(
                kind="connexions admin refusées", wan_id=None, since=refus[0].ts,
                detail=f"{len(refus)} refus en 5 min — cinq suspendent l'accès admin pendant 5 min",
                sustained_s=0.0,
            )
        else:
            self._alerte_admin.pop(record.id, None)

    def tunnel_attendu(self, router_id: str) -> bool | None:
        """Le bond a-t-il déjà porté un lien dans la session en cours ?

        C'est la règle qui décide, côté alertes, si un tunnel à terre est une
        panne ou un état : un bond qui n'a jamais tenu n'est pas tombé. L'écran
        ne la recevait pas et titrait « Tunnel tombé — 2 h dans cet état » en
        ambre sur un HD2 où SpeedFusion n'est pas censé fonctionner. None tant
        que la question n'a pas été posée — premier cycle, ou pas de tunnel.
        """
        memo = self._bond_porte.get(router_id)
        return None if memo is None else memo[1]

    def never_connected(self, router_id: str) -> list[str]:
        """Les WAN relevés qui n'ont pas été connectés une seule fois depuis la
        mise sous tension. Vide tant que la question n'a pas été posée : ne rien
        savoir ne fait pas d'un lien un lien « jamais raccordé »."""
        memo = self._deja_connectes.get(router_id)
        if memo is None:
            return []
        return sorted(w for w in self.latest.get(router_id, {}) if w not in memo[1])

    def alerts(self, router_id: str) -> list[Alert]:
        """Les alertes À AFFICHER pour cet équipement.

        Filtrées ici et non stockées filtrées : l'état suivi porte aussi les
        conditions qui n'ont pas encore franchi leur seuil, et c'est lui qui
        leur donne leur durée d'un cycle à l'autre.
        """
        admin = self._alerte_admin.get(router_id)
        horloge = self._alerte_horloge.get(router_id)
        return (
            regles_alerte.publiables(self._alertes.get(router_id, []))
            + ([admin] if admin else []) + ([horloge] if horloge else [])
        )

    def _verifier_horloge(self, record: DeviceRecord) -> None:
        """L'alerte d'horloge, à chaque fiche relue (R-3.9) : au-delà de 60 s
        d'écart, l'historique à 10 s du routeur est écarté et son journal n'est
        plus lu. En dessous, rien — pas d'avertissement permanent.

        Appelée après les awaits de la fiche : un équipement retiré entre-temps
        ne doit pas retrouver d'alerte (C-34)."""
        if not self._still_supervised(record):
            return
        ecart = record.info.clock_offset_s if record.info is not None else None
        if ecart is None or abs(ecart) <= _HORLOGE_SERIE_S:
            self._alerte_horloge.pop(record.id, None)
        elif record.id not in self._alerte_horloge:
            self._alerte_horloge[record.id] = Alert(
                kind="horloge du routeur décalée", wan_id=None, since=utcnow(),
                detail="", sustained_s=0.0,
            )
        self._requalifier_horloges()

    def _requalifier_horloges(self) -> None:
        """Le texte de chaque alerte d'horloge, au vu de TOUS les équipements.

        L'écart se mesure contre l'horloge de CE POSTE, et le texte accusait le
        routeur sans confronter les équipements : un PC sans NTP, trois minutes
        d'avance, et toute la flotte était « décalée » (C-32). Le même écart —
        à 30 s près — sur au moins deux équipements désigne le poste ; sinon,
        on dit un écart entre les deux, sans désigner personne.
        """
        ecarts = {
            r.id: r.info.clock_offset_s
            for r in self._registry.snapshot()
            if r.info is not None and r.info.clock_offset_s is not None
        }
        for rid, alerte in self._alerte_horloge.items():
            ecart = ecarts.get(rid)
            if ecart is None:
                continue
            semblables = [e for e in ecarts.values() if abs(e - ecart) <= _HORLOGE_ACCORD_S]
            sens = "en avance" if ecart > 0 else "en retard"
            valeur = f"{int(abs(ecart) // 60)} min" if abs(ecart) >= 90 else f"{round(abs(ecart))} s"
            if len(ecarts) >= 2 and len(semblables) == len(ecarts):
                cause = (f"le routeur est {valeur} {sens} sur ce poste, comme les "
                         f"{len(ecarts)} équipements supervisés : c'est sans doute l'horloge de "
                         "ce poste qui dérive — vérifier sa synchronisation")
            else:
                cause = f"écart de {valeur} entre le routeur et ce poste (le routeur {sens})"
            alerte.detail = (
                f"{cause} — l'historique à 10 s du routeur est écarté et son journal n'est "
                "plus lu tant que les deux horloges ne s'accordent pas"
            )

    def forget(self, router_id: str, *, conserver_abonnes: bool = False) -> None:
        """Efface tout état rattaché à un équipement retiré.

        Sans cette purge, réutiliser un identifiant ferait hériter le nouvel
        équipement des compteurs de stabilité et du dernier relevé du précédent.
        Les abonnés WebSocket sont libérés : leur file ne recevra plus rien —
        sauf `conserver_abonnes`, pour un RÉ-APPAIRAGE : l'identifiant reste
        supervisé, et fermer les flux en 4404 « retiré » faisait perdre le
        direct à tout onglet ouvert jusqu'au rechargement, sans retour possible.
        """
        self.latest.pop(router_id, None)
        self._gps.pop(router_id, None)
        self._alertes.pop(router_id, None)
        self._bond_porte.pop(router_id, None)
        self._deja_connectes.pop(router_id, None)
        self._journal_lu_a.pop(router_id, None)
        self._alerte_horloge.pop(router_id, None)
        self._alerte_admin.pop(router_id, None)
        self._trou_mesure.discard(router_id)
        self._session.pop(router_id, None)
        # La CADENCE aussi. Elle était oubliée, et c'est elle qui fixe le seuil
        # de péremption publié à l'écran : un équipement remplacé à la même
        # adresse héritait de la période lissée du précédent, donc d'un seuil
        # calculé sur une machine qui n'est plus là. Sur un châssis lent
        # remplacé par un rapide, le bandeau restait vert bien après la mort.
        self._period_seconds.pop(router_id, None)
        self._cycle_seconds.pop(router_id, None)
        self._last_cycle_start.pop(router_id, None)
        for counters in (self._cell_changes, self._disconnects, self._derniere_cellule):
            for key in [k for k in counters if k[0] == router_id]:
                del counters[key]

        # Réveiller les abonnés avant de les oublier : sans cette sentinelle, la
        # coroutine WebSocket reste bloquée sur `queue.get()` indéfiniment, et le
        # client garde à l'écran l'état d'un équipement qui n'existe plus.
        #
        # La sentinelle doit TOUJOURS passer, y compris sur une file pleine. Un
        # abonné lent accumule jusqu'à cent relevés ; `put_nowait` levait alors
        # `QueueFull`, l'exception était avalée, et comme les abonnés viennent
        # d'être retirés de `_subscribers` plus rien ne pouvait la reposer :
        # vérifié, `queue.get()` bloquait à jamais et la coroutine, la socket et
        # la file fuyaient à chaque retrait — la panne même que ce réveil corrige.
        # Sacrifier le relevé le plus ancien est sans conséquence : le lecteur
        # referme la connexion sans lire ce qui reste.
        if conserver_abonnes:
            return
        for queue in self._subscribers.pop(router_id, set()):
            while queue.full():
                try:
                    queue.get_nowait()
                except asyncio.QueueEmpty:
                    break
            queue.put_nowait(_CLOSED)

    # --- abonnements WebSocket ---
    def subscribe(self, router_id: str) -> asyncio.Queue:
        queue: asyncio.Queue = asyncio.Queue(maxsize=_QUEUE_MAXSIZE)
        self._subscribers[router_id].add(queue)
        return queue

    def unsubscribe(self, router_id: str, queue: asyncio.Queue) -> None:
        # `get` et non l'indexation : `_subscribers` est un defaultdict, et
        # l'indexer recréerait l'entrée que `forget()` vient de purger.
        subscribers = self._subscribers.get(router_id)
        if subscribers is not None:
            subscribers.discard(queue)
            if not subscribers:
                self._subscribers.pop(router_id, None)

    def snapshot_latest(self, router_id: str) -> list[RadioSample]:
        """Copie des derniers relevés d'un équipement.

        Le poller réécrit `latest[router_id]` à chaque cycle. Un appelant qui
        itérerait dessus en cédant la main — un envoi WebSocket, par exemple —
        lèverait `RuntimeError` en pleine boucle.
        """
        return list(self.latest.get(router_id, {}).values())

    def _publish(self, router_id: str, sample: RadioSample) -> None:
        for queue in list(self._subscribers.get(router_id, ())):
            try:
                queue.put_nowait(sample)
            except asyncio.QueueFull:
                # Abonné trop lent : on saute ce relevé plutôt que de bloquer la boucle.
                logger.debug("File pleine pour un abonné de %s, relevé ignoré", router_id)

    # --- cycle de vie ---
    async def start(self) -> None:
        if self._task is None:
            self._task = asyncio.create_task(self._run(), name="peplink-poller")

    async def stop(self) -> None:
        if self._task is not None:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None

    @property
    def period_seconds(self) -> float | None:
        """La plus longue période observée de la flotte, ou None.

        Vue d'ensemble pour `/api/health`. Un consommateur qui parle d'UN
        équipement doit lire `period_for` : ici, l'équipement le plus lent décide
        pour tout le monde, ce qui est prudent pour une jauge et faux pour un
        seuil de péremption.
        """
        return max(self._period_seconds.values()) if self._period_seconds else None

    def period_for(self, router_id: str) -> float | None:
        """Période observée de CET équipement, ou None avant deux cycles."""
        return self._period_seconds.get(router_id)

    def cycle_for(self, router_id: str) -> float | None:
        """Durée du cycle de CET équipement, hors pause.

        Publiée par équipement comme la période : les deux se lisent ensemble —
        « période = cycle + consigne » — et mêler un cycle de flotte à une
        période d'équipement faisait une soustraction qui ne tombait juste que
        sur une flotte d'un seul.
        """
        return self._cycle_seconds.get(router_id)

    @property
    def cycle_seconds(self) -> float | None:
        """Le plus long cycle observé, hors pause. Ce que coûte une scrutation.

        Publié à côté de la période pour que leur écart s'explique : la période
        vaut `cycle + consigne`, et si elle monte sans que la consigne bouge,
        c'est le cycle. Sans cette mesure, l'écart ne se constatait qu'après
        coup et sans cause.
        """
        return max(self._cycle_seconds.values()) if self._cycle_seconds else None

    def _mark_cycle_end(self, router_id: str, debut: float) -> None:
        """Retient ce qu'a coûté ce cycle, lissé comme la période."""
        duree = time.monotonic() - debut
        ancienne = self._cycle_seconds.get(router_id)
        self._cycle_seconds[router_id] = (
            duree if ancienne is None else ancienne * 0.7 + duree * 0.3
        )

    def _mark_cycle(self, router_id: str) -> None:
        """Mesure la période d'un équipement, lissée pour ne pas suivre un à-coup."""
        now = time.monotonic()
        precedent = self._last_cycle_start.get(router_id)
        if precedent is not None:
            mesure = now - precedent
            # Moyenne mobile : un cycle momentanément lent ne doit pas relâcher le
            # seuil de péremption d'un coup, ni son retour le serrer.
            ancienne = self._period_seconds.get(router_id)
            self._period_seconds[router_id] = (
                mesure if ancienne is None else ancienne * 0.7 + mesure * 0.3
            )
        self._last_cycle_start[router_id] = now

    async def _run(self) -> None:
        """Surveille la flotte : une tâche de scrutation par équipement.

        Le tour unique `asyncio.gather(...)` ne rendait la main qu'une fois TOUS
        les équipements traités : le budget de vingt secondes d'un seul
        équipement muet — éteint mais dont l'adresse reste routée — s'ajoutait à
        la période de tous les autres. C'est la panne que le passage au
        parallélisme devait supprimer, et il l'avait seulement déplacée : sur
        quatre véhicules dont un éteint, chaque cycle durait 20 s au lieu de
        2,1 s, les trois véhicules sains n'étaient rafraîchis que toutes les
        23 s, et comme le seuil de péremption vaut cinq fois la période, le
        bandeau restait VERT cent quinze secondes après la mort d'une liaison.

        Chaque équipement a donc sa boucle et sa cadence. Le sémaphore continue
        de borner le nombre d'interrogations SIMULTANÉES — c'est un PC de
        terrain qui héberge la supervision — mais il fait attendre son tour sans
        imposer de rendez-vous.
        """
        try:
            while True:
                # `_reconcilier` touche le registre, les tâches et les
                # compteurs. Une exception y passait par le `finally` et sortait
                # de `_run` : la supervision de TOUTE la flotte s'arrêtait, sans
                # qu'aucun écran ne puisse dire pourquoi — les relevés cessaient
                # simplement de vieillir. Une réconciliation qui échoue doit
                # coûter un tour, pas la boucle.
                try:
                    self._reconcilier()
                except asyncio.CancelledError:
                    raise
                except Exception:
                    logger.exception("Réconciliation de la flotte impossible : tour ignoré")
                await asyncio.sleep(_RECONCILIATION_S)
        finally:
            # ANNULER n'est pas ATTENDRE. Le `cancel()` ne fait que poser la
            # demande : sans l'attendre, l'arrêt du serveur rendait la main
            # pendant que des coroutines tenaient encore une connexion HTTP et
            # écrivaient en base — sur une base qu'`aclose` venait de fermer.
            taches = list(self._device_tasks.values())
            for task in taches:
                task.cancel()
            self._device_tasks.clear()
            if taches:
                await asyncio.gather(*taches, return_exceptions=True)

    def _reconcilier(self) -> None:
        """Aligne les tâches de scrutation sur la flotte réellement supervisée."""
        vivants = {record.id: record for record in self._registry.snapshot()}
        # Une tâche terminée l'est de sa propre initiative : `_run_device` sort
        # dès que son record n'est plus celui du registre, c'est-à-dire dès qu'un
        # identifiant a été réappairé sur un autre équipement. La relancer ici lui
        # donne le nouveau record, au lieu de la laisser tourner sur l'ancien.
        for router_id, task in list(self._device_tasks.items()):
            if task.done() or router_id not in vivants:
                # Une tâche terminée SEULE a pu l'être sur une exception échappée
                # du filet interne. La relancer sans lire son verdict laisse la
                # trace à l'éboueur d'asyncio, qui la crache bien plus tard sous
                # « Task exception was never retrieved » — hors contexte, et pas
                # dans notre journal.
                if task.done() and not task.cancelled():
                    faute = task.exception()
                    if faute is not None:
                        logger.error(
                            "Boucle de %s terminée sur une erreur — relance",
                            router_id, exc_info=faute,
                        )
                task.cancel()
                del self._device_tasks[router_id]
                self._last_cycle_start.pop(router_id, None)
                self._period_seconds.pop(router_id, None)
                self._cycle_seconds.pop(router_id, None)
        for router_id, record in vivants.items():
            if router_id not in self._device_tasks:
                self._device_tasks[router_id] = asyncio.create_task(
                    self._run_device(record), name=f"peplink-poller-{router_id}"
                )

    async def _run_device(self, record: DeviceRecord) -> None:
        """Boucle de scrutation d'UN équipement, à sa propre cadence."""
        while self._still_supervised(record):
            self._mark_cycle(record.id)
            debut = time.monotonic()
            # `_poll_device` avale déjà toutes ses exceptions : ce filet ne
            # couvre que l'imprévu, et la boucle ne doit jamais mourir.
            try:
                await self._poll_device(record)
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("Erreur inattendue dans la boucle de %s", record.id)
            self._mark_cycle_end(record.id, debut)
            await asyncio.sleep(self._interval)

    def _still_supervised(self, record: DeviceRecord) -> bool:
        """Cet équipement est-il toujours celui que le registre connaît ?

        `_poll_once` travaille sur une copie de la flotte et rend la main à
        chaque `await`. Un `DELETE /api/devices/x` qui tombe pendant
        `get_radio_status()` purge l'état, puis le cycle reprend et le réécrit :
        vérifié, `registry.get("x")` rendait None alors que `latest` contenait
        toujours ses relevés, et un identifiant réutilisé héritait du relevé du
        précédent — exactement ce que `forget()` promet d'empêcher.

        On compare l'OBJET et pas seulement l'identifiant : un retrait suivi
        d'un réappairage dans le même cycle rend un record différent, et publier
        le relevé de l'ancien équipement sous le nom du nouveau serait la même
        confusion d'identité, en moins visible.
        """
        return self._registry.get(record.id) is record

    async def _track_session(self, record: DeviceRecord, statuses: list) -> None:
        """Ouvre ou prolonge la session d'alimentation de cet équipement.

        L'ancrage est l'`uptime` des liens, pas un seuil de trou entre deux
        relevés. Mesuré : à 60, 120 ou 300 secondes de seuil, la détection coupe
        une vraie session en deux à cause d'un arrêt du superviseur de cinq
        minutes, pendant lequel le routeur n'avait pas cessé de fonctionner —
        même cellule, même bande, niveau continu. Un `uptime` qui RECULE, lui,
        ne peut signifier qu'une chose.

        Encore faut-il savoir de QUOI il recule. L'ancienneté du châssis était
        approchée par le plus élevé des liens PRÉSENTS, et ce maximum retombe dès
        que le lien qui le portait quitte le relevé — un WAN désactivé depuis
        l'administration, un modem qui passe à `uptime: 0`. Reproduit sur la
        fixture du MAX HD4 MBX : uptimes [6384, 6524, 6724, 6384], puis le lien à
        6724 s'en va, le maximum retombe à 6524 et une seconde session s'ouvrait
        trois minutes après la première. Les deux fenêtres se recouvraient
        presque entièrement : un stationnement unique s'affichait comme deux
        interventions et son volume était compté double.

        Ce qui sépare les deux cas n'est pas la hauteur du maximum mais
        l'ENSEMBLE des liens : une mise sous tension les remet TOUS à zéro,
        tandis qu'un lien qui s'en va laisse les autres continuer de vieillir.
        On compare donc lien à lien, et on ne conclut au redémarrage que si
        aucun de ceux qu'on retrouve n'a vieilli. La comparaison ne dépend
        d'aucune horloge : elle vaut donc aussi après un arrêt du superviseur,
        cas pour lequel l'ancrage sur l'`uptime` avait été choisi.
        """
        vus = {s.wan_id: float(s.uptime_s) for s in statuses if s.uptime_s is not None}
        # L'ancienneté du CHÂSSIS, quand le routeur la publie : c'est elle qui
        # fait la session, et tout ce qui précède n'était qu'une approche. Le
        # Dome Pro Duo la donne comme le MAX HD4 MBX, mais l'endpoint n'est pas
        # documenté officiellement — la comparaison lien à lien reste donc le
        # repli, et elle a fait ses preuves.
        chassis = record.info.uptime_s if record.info is not None else None
        lu_a = record.info_lu_a if record.info is not None else None
        courant_tot = self._session.get(record.id)
        if chassis is None and not vus:
            # Aucun témoin d'ancienneté sur ce cycle — 22,8 % des cycles réels de
            # la session 1. Les relevés sont pourtant ÉCRITS : ne pas prolonger
            # la session laissait sa fin geler jusqu'à 1 h 43 pendant que la
            # collecte continuait, et la fiche de stationnement s'affichait
            # complète en ayant perdu tout ce qui suivait.
            if courant_tot is not None:
                await self._repo.touch_session(courant_tot.id, utcnow(), 0)
            return
        maintenant = utcnow()
        router_id = record.id

        courant = self._session.get(router_id)
        if courant is not None and not _redemarrage(courant.uptimes, vus, chassis, courant.chassis):
            self._session[router_id] = _Session(courant.id, vus, chassis, lu_a)
            await self._repo.touch_session(courant.id, maintenant, 0)
            return

        # Les liens disent « redémarrage » et le châssis ne l'a pas encore
        # confirmé : on le RELIT, tout de suite, au lieu de conclure sur une
        # valeur relue un cycle sur douze. Le MAX HD2 du 19 septembre, 14:04:09 :
        # les trois liens retombent à sept secondes, un WAN disparaît du relevé.
        # Le châssis, lui, disait 19 800 s — et il avait RAISON : relu à la
        # remise en route du superviseur, il donnait la même mise sous tension
        # à la seconde près, 08:35:41. Les liens avaient été relancés ensemble
        # sans que le châssis redémarre. Le collecteur avait ouvert une session,
        # datée par ce châssis stagnant huit secondes après la précédente, dont
        # la fenêtre englobait toute la précédente.
        #
        # Une requête de plus, sur un événement rare, et c'est la mesure exacte
        # qui tranche : le châssis recule, c'est une mise sous tension et il la
        # date ; il ne recule pas, les liens ont été relancés et la session
        # continue. S'il est illisible, les liens gardent la parole — et datent.
        chassis_recule = (
            chassis is not None and courant is not None
            and courant.chassis is not None and chassis < courant.chassis
        )
        if courant is not None and not chassis_recule and record.info is not None:
            relue = await self._refresh_info(record)
            # Le retrait a pu tomber pendant la lecture : réécrire `_session`
            # ici ressusciterait l'entrée que `forget()` vient de purger — la
            # fuite décrite plus bas, et un identifiant réappairé qui reprendrait
            # la session du précédent.
            if not self._still_supervised(record):
                return
            if relue:
                # Une fiche fraîche vaut un rafraîchissement périodique ; une
                # lecture manquée n'en vaut pas un, et ne doit pas le repousser.
                record.cycles_since_info = 0
            relu = record.info.uptime_s if relue else None
            # Le châssis relu se compare à l'âge qu'il DEVRAIT avoir — la valeur
            # d'avant plus le temps écoulé depuis sa lecture — et non à cette
            # valeur d'avant telle quelle : lu à 5 s, une seconde mise sous
            # tension 40 s plus tard rend un châssis à 35 s, et 35 ≥ 5 concluait
            # « pas de redémarrage » ; la seconde session était fusionnée dans
            # la première. Une minute de marge : la lecture n'est pas datée
            # à la seconde, et le routeur arrondit.
            attendu = None
            if courant.chassis is not None and courant.chassis_lu_a is not None:
                attendu = courant.chassis + (time.monotonic() - courant.chassis_lu_a) - _CHASSIS_MARGE_S
            elif courant.chassis is not None:
                attendu = courant.chassis
            if relu is not None and attendu is not None and relu >= attendu:
                logger.info(
                    "Routeur %s : liens relancés sans mise sous tension (châssis à %d s)",
                    router_id, relu,
                )
                self._session[router_id] = _Session(courant.id, vus, relu, record.info_lu_a)
                await self._repo.touch_session(courant.id, maintenant, 0)
                return
            if relu is not None:
                # La valeur relue, ET l'instant de sa lecture : c'est ce couple
                # qui datera la comparaison suivante.
                chassis, lu_a = relu, record.info_lu_a
                chassis_recule = courant.chassis is None or relu < attendu if attendu is not None else True

        # Qui DATE la mise sous tension. Le châssis quand il vient d'être lu —
        # premier cycle, ou relu à l'instant — ou quand il a reculé ; sinon sa
        # valeur est celle d'avant par construction, et ce sont les liens.
        chassis_frais = chassis is not None and (courant is None or chassis_recule)
        haut = float(chassis) if chassis_frais else max(vus.values(), default=float(chassis or 0.0))
        debut = maintenant - timedelta(seconds=haut)

        # L'état de session vit en mémoire : au démarrage du superviseur, on ne
        # sait rien d'un équipement allumé depuis des heures. Ouvrir une session
        # à chaque redémarrage en créait autant que de relances — quatre en une
        # minute, toutes datées du même instant, alors que l'équipement n'avait
        # pas cessé de fonctionner. C'est précisément ce que l'ancrage sur
        # l'`uptime` devait empêcher, et il le permet : l'identité d'une session
        # est l'instant de MISE SOUS TENSION, pas celui où on a commencé à
        # regarder. On reprend donc celle dont le début coïncide.
        if courant is None:
            derniere = await self._repo.current_session(router_id)
            # Le retrait a pu tomber pendant la lecture ci-dessus. Les écritures
            # de `_session` suivent trois allers-retours en base et ne
            # revérifiaient rien : `forget()` purgeait l'entrée, le cycle la
            # réécrivait juste après, et plus aucun cycle ne repassant sur cet
            # équipement elle ne pouvait plus jamais être purgée — une fuite par
            # retrait, et un identifiant réappairé qui REPRENAIT la session du
            # précédent au lieu d'en ouvrir une.
            if not self._still_supervised(record):
                return
            if derniere is not None:
                ecart = abs(
                    (debut - datetime.fromisoformat(derniere["started_at"])).total_seconds()
                )
                if ecart <= _SESSION_MATCH_S:
                    session_id = int(derniere["id"])
                    self._session[router_id] = _Session(session_id, vus, chassis, lu_a)
                    await self._repo.touch_session(session_id, maintenant, 0)
                    logger.info(
                        "Routeur %s : session %s reprise (allumé depuis %.0f s)",
                        router_id, session_id, haut,
                    )
                    return

        if courant is not None:
            logger.info("Routeur %s : nouvelle session d'alimentation", router_id)
        session_id = await self._repo.open_session(router_id, debut)
        if not self._still_supervised(record):
            return
        self._session[router_id] = _Session(session_id, vus, chassis, lu_a)
        await self._repo.touch_session(session_id, maintenant, 0)

    def _prune_absent_wans(
        self, router_id: str, seen: set[str], desactives: set[str] | frozenset[str] = frozenset()
    ) -> list[RadioSample]:
        """Oublie les WAN qu'un relevé abouti n'a pas mentionnés, et rend leur adieu.

        À n'appeler QUE sur une lecture réussie : c'est la présence de la
        réponse qui autorise à conclure qu'un WAN absent n'existe plus. Sur un
        équipement injoignable, on ne sait rien de ses WAN et l'état conservé
        reste la meilleure information disponible.

        Les compteurs de stabilité ne sont pas purgés : ils sont bornés par le
        nombre de WAN qu'un châssis peut porter, et un lien remis en service a
        toutes les raisons de reprendre son historique là où il l'avait laissé.

        Le retour porte, pour chaque WAN qui s'en va, un dernier relevé marqué
        absent. Sans lui l'historique ne garde RIEN de la disparition : l'incident
        le plus grave de la base — une SIM évanouie treize minutes et cent
        cinquante-cinq cycles — n'y a pas laissé une ligne, et un débrief qui ne
        montre rien pendant treize minutes ressemble à un débrief où rien ne s'est
        passé. L'absence est une mesure ; elle doit s'écrire.
        """
        known = self.latest.get(router_id)
        if not known:
            return []
        adieux: list[RadioSample] = []
        for wan_id in [w for w in known if w not in seen]:
            dernier = known[wan_id]
            if wan_id in desactives:
                # Désactivé par l'exploitant : on ne l'oublie pas, on le DIT. Le
                # relevé d'adieu s'écrit une fois — au premier cycle —, puis la
                # ligne reste en mémoire, marquée, sans mesure.
                if dernier.wan.disabled:
                    continue
                marque = dernier.model_copy(update={
                    "ts": utcnow(),
                    "wan": dernier.wan.model_copy(update={
                        "connected": False, "disabled": True, "status_message": STATUT_DESACTIVE,
                        "rsrp": None, "rsrq": None, "sinr": None, "rssi": None,
                        "tx_kbps": None, "rx_kbps": None, "uptime_s": None,
                        "tunnel": None, "path_latency_ms": None,
                    }),
                })
                known[wan_id] = marque
                adieux.append(marque)
                logger.info("Routeur %s : le WAN %s est désactivé", router_id, wan_id)
                continue
            logger.info(
                "Routeur %s : le WAN %s a disparu du relevé, son état est oublié",
                router_id, wan_id,
            )
            adieux.append(
                dernier.model_copy(
                    update={
                        # `model_copy` ne rejoue pas le `default_factory` de `ts` :
                        # sans cette ligne, la trace de la disparition portait
                        # l'horodatage de la dernière lecture RÉUSSIE — deux lignes
                        # à la microseconde près, l'une connectée et mesurée,
                        # l'autre absente et vide. L'absence est une mesure, mais
                        # elle n'était pas DATÉE : après treize minutes
                        # d'injoignabilité, la disparition était antidatée d'autant
                        # et le trou se dessinait à l'abscisse d'un point valide.
                        "ts": utcnow(),
                        "wan": dernier.wan.model_copy(
                            update={
                                "connected": False,
                                "status_message": "absent du relevé",
                                # Les mesures du dernier cycle ne valent plus rien :
                                # les recopier ferait croire à un lien mesuré.
                                "rsrp": None, "rsrq": None, "sinr": None, "rssi": None,
                                "tx_kbps": None, "rx_kbps": None,
                                # L'ancienneté de session d'un lien qu'on déclare
                                # absent n'a pas de sens, et `_resume_lien` compte
                                # une coupure sur un `uptime` qui recule : la
                                # recopier inventerait des micro-coupures au retour.
                                "uptime_s": None,
                                "tunnel": None,
                            }
                        ),
                    }
                )
            )
            del known[wan_id]
        return adieux

    async def _identify(self, record: DeviceRecord) -> bool:
        """Lit l'identité et les capacités d'un équipement, sans jamais lever.

        Rend True quand l'identité a été OBTENUE, seul cas où il est inutile de
        redemander : cette fonction avalant toutes ses exceptions, l'appelant
        n'a aucun autre moyen de distinguer un échec d'une absence d'identité.
        """
        client = record.client
        assert client is not None
        try:
            info = await client.get_device_info()
        except Exception as exc:
            logger.debug("Identité illisible sur %s : %s", record.id, redact(str(exc)))
            info = None
        if info is not None:
            record.info = info
            record.info_lu_a = time.monotonic()
            self._verifier_horloge(record)
            # Le numéro de série est la clé d'identité stable : on la retient dès
            # qu'elle est connue, y compris pour un équipement venu du fichier.
            record.config.serial = record.config.serial or info.serial
            logger.info(
                "Équipement %s identifié : %s · firmware %s · série %s",
                record.id, info.model or "modèle inconnu", info.firmware or "?",
                info.serial or "?",
            )
        try:
            caps = await client.get_capabilities()
        except Exception as exc:
            logger.debug("Capacités illisibles sur %s : %s", record.id, redact(str(exc)))
            caps = None
        if caps is not None:
            record.capabilities = caps
        return info is not None

    async def _refresh_info(self, record: DeviceRecord) -> bool:
        """Rejoue la fiche d'un équipement déjà identifié, sans jamais lever.

        Rend vrai quand la fiche a été RELUE — l'appelant qui veut une mesure
        fraîche, et non la précédente, a besoin de le savoir.

        Ne touche ni `identified` ni `identify_attempts` : rafraîchir une
        identité OBTENUE et insister pour en obtenir une sont deux choses
        différentes, et le bornage des tentatives initiales répond à l'autre
        problème — un modèle qui ne publie pas `status.system.info` ne doit pas
        être interrogé indéfiniment.

        Ne redemande pas les capacités DÉJÀ CONCLUES : l'inventaire des WAN, les
        emplacements SIM et les logements USB sont du matériel, ils ne changent
        pas sans démontage, et les rejouer coûterait deux requêtes de plus par
        rafraîchissement — `info.capability` et `status.port` — pour une réponse
        identique.

        Une fiche `provisional`, elle, est rejouée. Une capacité NIÉE ne se
        redemandait jamais : un seul délai dépassé sur `info.location` pendant
        qu'un routeur finissait de démarrer gravait `gps=False`, `get_location()`
        n'était plus JAMAIS appelée, tous les relevés du véhicule portaient les
        coordonnées fixes du fichier de flotte, et le barycentre de session ne
        décrivait plus rien — sans que rien ne le signale. La sonde ne distingue
        plus qu'un refus du firmware d'une panne passagère, et seule la seconde
        laisse la fiche à confirmer.

        Un échec conserve la fiche précédente au lieu de l'effacer : cet appel
        n'a lieu qu'après une lecture radio ABOUTIE, donc un refus ici vise cet
        endpoint seul et se retentera au prochain tour ; et l'effacer emporterait
        aussi le modèle, le firmware et le numéro de série, qui sont stables et
        toujours vrais. Sur un équipement devenu injoignable, la boucle est
        sortie bien avant et ce sont `status` et `age_seconds` qui datent la
        carte entière.
        """
        client = record.client
        assert client is not None
        try:
            info = await client.get_device_info()
        except Exception as exc:
            logger.debug("Fiche illisible sur %s : %s", record.id, redact(str(exc)))
            return False
        if info is None:
            return False

        # Un rafraîchissement peut rendre l'identité d'un AUTRE équipement :
        # l'adresse est celle du réseau, pas celle du matériel, et un routeur
        # remplacé sur site en hérite. On ne réécrit pas le numéro de série —
        # la clé d'identité stable reste celle sous laquelle les relevés ont été
        # enregistrés — mais se taire referait le mensonge que tout ce module
        # combat : la fiche affichée ne serait plus celle du châssis supervisé.
        if info.serial and record.config.serial and info.serial != record.config.serial:
            logger.warning(
                "Équipement %s : la fiche annonce le numéro de série %s, "
                "supervisé sous %s — le matériel a-t-il été remplacé ?",
                record.id, info.serial, record.config.serial,
            )

        record.info = info
        record.info_lu_a = time.monotonic()
        self._verifier_horloge(record)
        # Une première identification peut avoir abouti sans numéro de série ;
        # on le retient dès qu'il apparaît, et jamais on ne le remplace.
        record.config.serial = record.config.serial or info.serial

        if record.capabilities is not None and not record.capabilities.provisional:
            return True
        try:
            caps = await client.get_capabilities()
        except Exception as exc:
            logger.debug("Capacités illisibles sur %s : %s", record.id, redact(str(exc)))
            return True  # la FICHE, elle, a été relue
        # Le retrait a pu tomber pendant les lectures ci-dessus.
        if caps is not None and self._still_supervised(record):
            record.capabilities = caps
        return True

    async def _poll_once(self) -> None:
        """Un tour COMPLET : tous les équipements, et on attend le dernier.

        La boucle de production ne passe plus par ici — `_run` donne à chaque
        équipement sa propre tâche et sa propre cadence, précisément parce que ce
        rendez-vous faisait payer à tous le budget de l'équipement le plus lent.
        Ce tour synchronisé reste le point d'entrée des tests et de toute
        vérification qui a besoin d'un état stable après un passage.

        Deux gardes valent pour les deux formes. Un sémaphore, parce que vingt
        équipements interrogés d'un coup ouvrent vingt sessions TLS et autant de
        connexions concurrentes sur un PC de terrain. Et un délai par équipement :
        un équipement mort mais dont l'adresse est encore routée absorbe les
        paquets sans répondre, et coûtait 15 s sur l'authentification puis 15 s
        sur la lecture.

        L'état est déjà indépendant par équipement — `latest`, les compteurs, le
        GPS, les sessions sont tous clés par `router_id` — et `_still_supervised`
        protège le rendez-vous avec `forget()`. Le seul partage est la connexion
        SQLite, qui sérialise ses écritures d'elle-même.
        """
        # Copie en tête de cycle : un appairage ou un retrait peut survenir
        # pendant la scrutation, et muter la flotte en la parcourant lèverait.
        records = self._registry.snapshot()
        if not records:
            return
        await asyncio.gather(
            *(self._poll_device(record) for record in records), return_exceptions=False
        )

    async def _poll_device(self, record: DeviceRecord) -> None:
        """Scrute UN équipement. Ne lève jamais : une panne ne doit pas en emporter d'autres."""
        # Le BUDGET enveloppe l'attente de place, et non l'inverse. Le sémaphore
        # était pris en premier : un équipement muet tenait alors sa place
        # pendant tout son budget — vingt secondes en production. Huit véhicules
        # éteints, c'est-à-dire l'état normal d'une flotte dont les équipements
        # ne sont allumés qu'à l'arrêt, saturaient les huit places et imposaient
        # vingt-trois secondes de période à chaque véhicule SAIN. C'est la panne
        # que le contrat de la boucle déclare avoir supprimée — « attendre son
        # tour sans imposer de rendez-vous » n'en est plus un dès que les muets
        # atteignent le nombre de places.
        #
        # Le budget couvre désormais l'attente elle-même : un équipement qui
        # n'obtient pas sa place à temps abandonne son tour au lieu de la retenir.
        try:
            async with asyncio.timeout(self._device_budget) as budget:
                async with self._slots:
                    # Les lectures complémentaires s'arrêtent AVANT le budget,
                    # avec de quoi écrire et publier le relevé WAN déjà lu : le
                    # budget les couvrait, et deux compléments lents faisaient
                    # jeter un relevé lu en 0,4 s (C-5).
                    reserve = min(_RESERVE_PUBLICATION_S, self._device_budget / 4)
                    await self._poll_device_once(record, echeance=budget.when() - reserve)
        except TimeoutError:
            message = f"pas de réponse en {self._device_budget:.0f} s"
            if record.status != "injoignable":
                logger.warning("Routeur %s : %s", record.id, message)
            record.status, record.error = "injoignable", message
        except Exception:
            logger.exception("Erreur inattendue sur %s", record.id)

    async def _poll_device_once(self, record: DeviceRecord, echeance: float | None = None) -> None:
        router, client = record.config, record.client
        if client is None:
            return

        # Identifiants refusés : on ne rappelle plus cet équipement du tout.
        # Le seul moyen de sortir de cet état est `POST /devices/{id}/reauth`,
        # c'est-à-dire une action humaine — exactement ce que promettait le
        # commentaire ci-dessous, et que le code ne faisait pas.
        if record.auth_blocked:
            return
        try:
            statuses = await client.get_radio_status(echeance=echeance)
        except CredentialsRefusedError as exc:
            # Identifiants refusés : ne JAMAIS réessayer tout seul. Cinq
            # tentatives ratées suspendent l'accès pendant cinq minutes.
            #
            # `redact` n'est pas une précaution de principe : le jeton voyage
            # en paramètre d'URL — le firmware refuse l'en-tête `Bearer` —,
            # `raise_for_status()` compose son message avec l'URL entière, et
            # ce message finit dans `RouterSummary.error`, que `GET /routers`
            # publie sans restriction de provenance. Un simple 500 passager
            # suffisait à livrer le jeton d'accès à qui interrogeait l'API.
            message = redact(str(exc))
            if record.status != "identifiants refusés":
                logger.warning("Routeur %s : identifiants refusés — %s", router.id, message)
            record.status, record.error = "identifiants refusés", message
            record.auth_blocked = True
            return
        except AuthenticationError as exc:
            # Un 401 sur une LECTURE n'est pas un refus d'identifiants : la
            # session a expiré, et `_get` s'est déjà reconnecté. Si l'erreur
            # remonte quand même, l'équipement est momentanément illisible —
            # on le marque injoignable et on réessaiera, sans consommer de
            # tentative de connexion supplémentaire.
            message = redact(str(exc))
            if record.status != "injoignable":
                logger.warning("Routeur %s illisible : %s", router.id, message)
            record.status, record.error = "injoignable", message
            return
        except Exception as exc:
            message = _cause_injoignable(exc)
            if record.status != "injoignable":
                logger.warning("Routeur %s injoignable : %s", router.id, message)
            record.status, record.error = "injoignable", message
            return

        # Le retrait a pu tomber pendant l'appel ci-dessus : ne rien réécrire
        # d'un équipement que `forget()` vient de purger.
        if not self._still_supervised(record):
            return

        record.status, record.error = "connecté", None
        # Les routes du tunnel arrivent avec la liste des pairs, à chaque cycle,
        # dans une charge utile déjà téléchargée.
        record.tunnel_uptime_s = getattr(client, "tunnel_uptime_s", None)
        routes = getattr(client, "bond_routes", None)
        if routes is not None:
            record.bond_routes = list(routes)
        profils = getattr(client, "bond_profiles", None)
        if isinstance(profils, list):
            record.bond_profiles = list(profils)
        record.lan_clients = getattr(client, "lan_clients", None)
        # La série de latence du routeur, un point / 10 s (R-3.2) : écrite une
        # fois, puis oubliée par le client. Un échec d'écriture ne coûte que
        # cette série — l'heure suivante la recouvrira.
        serie = getattr(client, "serie_latence", None)
        if serie:
            client.serie_latence = None
            try:
                await self._repo.insert_latency_series(router.id, serie)
            except Exception:
                logger.warning("Série de latence non écrite pour %s", router.id, exc_info=True)
        # L'heure de radio du routeur (R-3.1), même règle.
        radio = getattr(client, "serie_qualite", None)
        if radio:
            client.serie_qualite = None
            try:
                await self._repo.insert_quality_series(router.id, radio)
            except Exception:
                logger.warning("Série de radio non écrite pour %s", router.id, exc_info=True)

        # Un WAN absent d'un relevé RÉUSSI n'existe plus : le parseur écarte
        # ceux que le routeur déclare `enable: false`, et le dernier
        # échantillon resterait sinon dans l'état courant pour toujours.
        # Mesuré : un WAN désactivé depuis l'interface continuait de
        # s'afficher connecté et dans le bond, avec son signal d'une
        # vingtaine de minutes plus tôt, et `age_seconds` ne trahissait rien
        # puisqu'il porte sur le relevé le plus récent de l'ÉQUIPEMENT.
        #
        # La purge n'a lieu qu'ici, après une lecture aboutie. Sur un
        # équipement injoignable, la boucle est déjà sortie par `continue` :
        # le dernier état connu est conservé, et ce sont `status` et
        # `age_seconds` qui disent qu'il a vieilli. Effet de bord voulu :
        # un WAN réactivé revient sans antécédent, donc sa réapparition
        # n'est comptée ni comme un changement de cellule ni comme une
        # reconnexion.
        for adieu in self._prune_absent_wans(
            router.id, {s.wan_id for s in statuses},
            desactives=getattr(client, "wans_desactives", set()) or set(),
        ):
            # Écrit, mais PAS publié aux abonnés ni remis dans `latest` : le
            # WAN n'existe plus, l'écran ne doit pas le rouvrir. C'est une
            # trace d'historique, pas un état courant.
            try:
                await self._repo.insert(adieu)
            except Exception as exc:
                logger.warning(
                    "Trace d'absence non écrite (%s) : %s", router.id, redact(str(exc))
                )

        # L'équipement répond : on complète son identité tant qu'elle n'est
        # pas OBTENUE, et non dès la première tentative. Poser le drapeau
        # avant l'appel rendait définitif un échec passager — un routeur qui
        # finit de démarrer — puisque rien ne le remet jamais à False.
        #
        # Bornées par la même échéance que les compléments : ce sont elles
        # aussi des lectures réseau, et une fiche lente ne doit pas coûter le
        # relevé déjà lu. Une identification interrompue ne compte pas comme
        # une tentative — elle sera reprise au cycle suivant.
        if not record.identified:
            fait, identifie = await avant_echeance(echeance, self._identify(record))
            if fait:
                record.identify_attempts += 1
            if identifie:
                record.identified = True
            elif fait and record.identify_attempts >= _IDENTIFY_ATTEMPTS:
                # Une seule ligne, puis plus rien : le modèle ne publie
                # visiblement pas son identité, insister est sans espoir.
                record.identified = True
                logger.info(
                    "Équipement %s : identité illisible après %d tentatives, abandon",
                    router.id, record.identify_attempts,
                )
        elif record.info is not None:
            # Identité obtenue : la fiche se périme, on la rejoue un cycle
            # sur `_INFO_REFRESH_CYCLES`. La condition porte sur `info` et
            # non sur `identified` seul, parce que ce drapeau vaut AUSSI
            # True après l'abandon des trois tentatives initiales : s'y fier
            # rétablirait ici, à 420 ms la requête et pour toujours,
            # l'interrogation sans fin que ce bornage vient supprimer.
            record.cycles_since_info += 1
            if record.cycles_since_info >= _INFO_REFRESH_CYCLES:
                record.cycles_since_info = 0
                await avant_echeance(echeance, self._refresh_info(record))

        # Le suivi de session vient APRÈS l'identification, et c'est tout
        # l'objet : il s'exécutait avant, si bien qu'au premier cycle de chaque
        # équipement `record.info` valait None et que la session était ancrée sur
        # l'ancienneté des LIENS. L'ancrage erroné n'était jamais corrigé —
        # vérifié sur la session 5, datée à quatre secondes près de l'attache du
        # lien le plus ancien, et non de la mise sous tension du châssis.
        # --- l'angle mort, mesuré AVANT d'écrire quoi que ce soit -------------
        #
        # Quand le superviseur s'arrête, plus rien ne l'observe : ni lui-même,
        # ni le routeur, qui continue de fonctionner sans témoin. Le trou n'est
        # constatable qu'au redémarrage, en comparant le dernier relevé écrit à
        # l'heure qu'il est.
        #
        # Donc AVANT la boucle d'écriture, et non après. La première version
        # posait la question une fois le relevé du cycle enregistré : la base
        # répondait « il y a une seconde », et l'alerte ne s'est jamais
        # déclenchée. Le test le montrait ; le code ne le disait pas.
        #
        # Et une seule fois par équipement : le redire à chaque cycle ferait de
        # l'angle mort un papier peint.
        if router.id not in self._trou_mesure:
            self._trou_mesure.add(router.id)
            try:
                # Borné à l'instant où ce processus a démarré : mesuré sans
                # cette borne, un équipement éteint la nuit pendant que le
                # superviseur tournait donnait « supervision interrompue, 840
                # min » chaque matin — le papier peint que ce module combat.
                trou = regles_alerte.interruption(
                    await self._repo.last_sample_at(router.id), maintenant=self._demarre_a,
                )
            except Exception as exc:
                logger.debug("Trou de supervision non mesurable (%s) : %s", router.id, exc)
                trou = None
            if trou is not None:
                self._alertes.setdefault(router.id, []).append(trou)
                logger.warning(
                    "Supervision de %s interrompue : aucun relevé pendant %d min",
                    router.id,
                    round((trou.until - trou.since).total_seconds() / 60),
                )

        try:
            await self._track_session(record, statuses)
        except Exception as exc:
            logger.warning("Suivi de session impossible (%s) : %s",
                           router.id, redact(str(exc)))

        # Position du site. DEUX sources possibles, et elles ne se mélangent
        # pas : un équipement qui se situe lui-même n'emprunte jamais la
        # coordonnée déclarée dans le fichier de flotte.
        #
        # Elles remplissaient pourtant la même colonne, et `build_summary`
        # moyennait le tout : sur un véhicule doté d'un `site_lat` de fichier,
        # chaque cycle sans point GPS tirait le barycentre de la session vers
        # une position où le véhicule n'a jamais été — et le rayon avec lui.
        # Un barycentre entre Lyon et une coordonnée d'inventaire ne décrit
        # aucun lieu.
        site_lat = site_lon = None if router.use_gps else router.site_lat
        if not router.use_gps:
            site_lon = router.site_lon
        # `capabilities.gps` est lu depuis `info.capability` et n'était jamais
        # consulté : sur un modèle sans récepteur, on dépensait une requête par
        # cycle — 420 ms, soit un cinquième du budget — pour une réponse qui ne
        # pouvait rien contenir. Capacités inconnues : on essaie, c'est le seul
        # moyen de savoir.
        sait_se_situer = record.capabilities is None or record.capabilities.gps
        if router.use_gps and sait_se_situer:
            try:
                position = await client.get_location()
            except Exception as exc:
                logger.debug("GPS illisible sur %s : %s", router.id, redact(str(exc)))
                position = None
            if position is not None and self._still_supervised(record):
                # L'âge que le RÉCEPTEUR compte, quand il le publie. Il mesure la
                # bonne chose : un récepteur qui a perdu le ciel republie son
                # dernier point sans que notre lecture, elle, cesse d'être
                # fraîche. Notre propre horloge ne reste que le garde-fou de la
                # liaison entre le superviseur et le routeur.
                if position.age_s is not None and position.age_s > _GPS_MAX_AGE_S:
                    logger.info(
                        "Fixation GPS de %s vieille de %s s selon le récepteur : ignorée",
                        router.id, position.age_s,
                    )
                else:
                    self._gps[router.id] = ((position.lat, position.lon), time.monotonic())
            connu = self._gps.get(router.id)
            # Une position PÉRIME. Au-delà, on n'en a plus : le relevé porte une
            # absence, que l'écran sait dire, plutôt qu'un point que plus rien
            # ne soutient.
            if connu is not None:
                point, lu_a = connu
                if time.monotonic() - lu_a <= _GPS_MAX_AGE_S:
                    site_lat, site_lon = point
                else:
                    self._gps.pop(router.id, None)
                    logger.info(
                        "Position de %s périmée (plus de %.0f s sans fixation) : "
                        "les relevés n'en portent plus",
                        router.id, _GPS_MAX_AGE_S,
                    )

        ecrits = 0
        # UN horodatage pour tout le cycle. Chaque relevé prenait le sien à sa
        # construction, après l'`await` d'écriture du précédent : sur un poste
        # de terrain où un insert attend derrière une relecture, les relevés
        # d'un même cycle s'étalaient jusqu'à 2,58 s et chevauchaient la
        # frontière des tranches de trois secondes — 105 cycles sur 5 934
        # coupés en deux demi-cycles « observés », six minutes de panne
        # fabriquées sur neuf heures quarante (mesuré). Datés du même instant,
        # ils tombent dans la même tranche, quelle que soit la découpe.
        lu_a = utcnow()
        for status in statuses:
            # L'écriture du relevé précédent s'est achevée sur un `await` :
            # le retrait a pu tomber pendant, et toucher un compteur
            # recréerait à lui seul la clé purgée (`_cell_changes` est un
            # defaultdict) — la simple LECTURE de `_cell_changes[key]`
            # ci-dessous suffit. C'est le seul point de rendez-vous avec
            # `forget()` : de cette garde jusqu'à l'écriture en base, plus
            # rien ne rend la main.
            if not self._still_supervised(record):
                break

            key = (router.id, status.wan_id)
            previous = self.latest.get(router.id, {}).get(status.wan_id)

            # Indicateurs de stabilité
            if previous is not None and (lu_a - previous.ts).total_seconds() > _TROU_MAX_S:
                self._derniere_cellule.pop(key, None)
            if status.connected and status.cell_id is not None:
                connue = self._derniere_cellule.get(key)
                if connue is not None and connue != status.cell_id:
                    self._cell_changes[key] += 1
                self._derniere_cellule[key] = status.cell_id
            if previous is not None:
                if previous.wan.connected and not status.connected:
                    self._disconnects[key] += 1
                # Une coupure PLUS COURTE QUE LE CYCLE ne se voit jamais ainsi :
                # le lien est reparti avant qu'on le relise, et les deux
                # relevés l'encadrant le montrent connecté. Seul l'`uptime`
                # trahit le rétablissement — il recule. À 5,3 s de période,
                # c'est un angle mort de plusieurs secondes à chaque cycle, et
                # les micro-coupures sont précisément ce qui fait décrocher un
                # flux sans jamais apparaître dans un compteur.
                elif (
                    status.connected
                    and previous.wan.connected
                    and status.uptime_s is not None
                    and previous.wan.uptime_s is not None
                    and status.uptime_s < previous.wan.uptime_s
                    # Passage en secours : l'uptime repart de 0 sans coupure.
                    and not en_secours(status.status_message)
                    and not en_secours(previous.wan.status_message)
                ):
                    self._disconnects[key] += 1
                    logger.info(
                        "Routeur %s, WAN %s : rétabli entre deux relevés "
                        "(uptime %s s -> %s s)",
                        router.id, status.wan_id, previous.wan.uptime_s, status.uptime_s,
                    )

            # Dernière latence du lien, datée : tenue jusqu'à 90 s tant que le
            # lien reste connecté (R-1.3).
            # Un lien coupé n'en porte aucune, même si la série du routeur lui
            # prête encore un point d'avant la coupure.
            if status.connected and status.path_latency_ms is not None:
                status.path_latency_recent_ms = status.path_latency_ms
                status.path_latency_at = lu_a
            elif (
                status.connected
                and previous is not None
                and previous.wan.path_latency_at is not None
                and (lu_a - previous.wan.path_latency_at).total_seconds() <= _LATENCE_LIEN_MAX_S
            ):
                status.path_latency_recent_ms = previous.wan.path_latency_recent_ms
                status.path_latency_at = previous.wan.path_latency_at

            sample = RadioSample(
                router_id=router.id,
                router_name=router.name,
                ts=lu_a,
                site_lat=site_lat,
                site_lon=site_lon,
                wan=status,
                cell_changes=self._cell_changes[key],
                disconnects=self._disconnects[key],
            )

            self.latest.setdefault(router.id, {})[status.wan_id] = sample
            self._publish(router.id, sample)
            try:
                # Un seul commit par cycle, après la boucle : cinq relevés
                # faisaient cinq commits, deux fsync chacun sur une clé USB.
                await self._repo.insert(sample, commit=False)
            except Exception as exc:
                logger.warning(
                    "Écriture du relevé impossible (%s) : %s", router.id, redact(str(exc))
                )
            else:
                ecrits += 1
        try:
            await self._repo.commit()
        except Exception as exc:
            logger.warning(
                "Validation des relevés impossible (%s) : %s", router.id, redact(str(exc))
            )
            ecrits = 0

        # Le compte de la session est ce qui a été ÉCRIT, et il est arrêté ICI.
        #
        # `_track_session` l'avançait de `len(statuses)` AVANT cette boucle —
        # c'est-à-dire du nombre de relevés qu'on s'apprêtait à tenter. Or la
        # boucle peut sortir par `break` si l'équipement est retiré en cours, et
        # chaque écriture peut échouer sans interrompre les suivantes : le
        # chiffre annoncé était une intention, pas une observation. Mesuré sur la
        # base : 19 990 relevés déclarés par la session contre 25 920 réellement
        # présents, 30 % d'écart — et c'est ce dénominateur qui servirait à
        # rapporter quoi que ce soit « par heure ».
        # --- les alertes, après l'écriture et sur le relevé COMPLET ------------
        #
        # Après, parce qu'une alerte ne doit jamais devancer la trace : ce qui
        # est signalé doit être retrouvable au journal. Et sur le relevé
        # complet, parce que les deux règles du bond comptent des liens entre
        # eux — les juger lien par lien ferait dire « il n'en reste qu'un »
        # quatre fois de suite.
        try:
            session = self._session.get(router.id)
            session_id = session.id if session else -1
            memo = self._bond_porte.get(router.id)
            connus = self._deja_connectes.get(router.id)
            if memo is None or memo[0] != session_id or connus is None or connus[0] != session_id:
                # Nouvelle session — ou première évaluation : on demande à la
                # base ce que CETTE session a déjà porté.
                # La session TENUE EN MÉMOIRE, par son identifiant — pas « la
                # dernière en base », qui peut être une autre ligne (une
                # correction manuelle, une course avec l'ouverture). Sans son
                # début, la mémoire redeviendrait celle de tout temps.
                debut = None
                if session is not None:
                    ligne = await self._repo.session_by_id(session_id)
                    if ligne is not None:
                        debut = datetime.fromisoformat(ligne["started_at"])
                if memo is None or memo[0] != session_id:
                    porte = await self._repo.bond_carried_since(router.id, debut)
                    self._bond_porte[router.id] = (session_id, porte)
                if connus is None or connus[0] != session_id:
                    vus = await self._repo.wans_connected_since(router.id, debut)
                    self._deja_connectes[router.id] = (session_id, vus)
            self._deja_connectes[router.id][1].update(s.wan_id for s in statuses if s.connected)
            if not self._bond_porte[router.id][1] and any(
                # Le même prédicat qu'en base et dans la fiche de session :
                # debout ET dans le bond.
                s.connected and s.tunnel is not None and s.tunnel.in_bond for s in statuses
            ):
                # Première fois que le bond porte dans cette session : à partir
                # d'ici, sa chute sera une nouvelle.
                self._bond_porte[router.id] = (session_id, True)
            if getattr(client, "tunnel_absent", False):
                # L'équipement dit qu'il n'a PLUS de profil SpeedFusion : le
                # bond n'est plus attendu. Sans cela, « aucun objet tunnel »
                # se lisait comme une sonde sautée — le gel des alertes — et
                # une alerte de bond ouverte avant le retrait du profil restait
                # ouverte, durée croissante, jusqu'à la prochaine mise sous
                # tension.
                self._bond_porte[router.id] = (session_id, False)
            suivies = regles_alerte.evaluer(
                statuses, self._alertes.get(router.id, []),
                tunnel_attendu=self._bond_porte[router.id][1],
            )
            # Deux lectures en base viennent de céder la main : un retrait
            # pendant l'une d'elles a purgé `_alertes` et `_bond_porte`, et les
            # réécrire ici les ferait hériter par un identifiant réappairé.
            if not self._still_supervised(record):
                self._alertes.pop(router.id, None)
                self._bond_porte.pop(router.id, None)
                self._deja_connectes.pop(router.id, None)
                return
            self._alertes[router.id] = suivies
        except Exception as exc:
            # Une règle qui échoue ne doit pas coûter le relevé. Elle décrit ce
            # qui vient d'être écrit ; l'écriture, elle, a déjà eu lieu.
            logger.warning(
                "Évaluation des alertes impossible (%s) : %s", router.id, redact(str(exc))
            )

        # Le journal du routeur : un complément, avec son propre filet — son
        # échec ne coûte ni le relevé ni les alertes du bond.
        try:
            await self._lire_journal_routeur(record, statuses)
        except Exception as exc:
            logger.warning(
                "Lecture du journal du routeur impossible (%s) : %s", router.id, redact(str(exc))
            )

        session = self._session.get(router.id)
        if session is not None and ecrits:
            try:
                await self._repo.touch_session(session.id, utcnow(), ecrits)
            except Exception as exc:
                logger.warning(
                    "Compte de session non avancé (%s) : %s", router.id, redact(str(exc))
                )
