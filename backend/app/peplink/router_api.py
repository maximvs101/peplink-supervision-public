"""Client pour l'API locale d'un routeur Peplink (firmware 8.x).

Validé contre une capture réelle d'un **Peplink Dome Pro Duo, firmware 8.5.4 build 6264**
(modem Quectel RM520N-GL, 2 WAN cellulaires Orange en 5G NSA). Voir
`tests/fixtures/dome_pro_duo_8.5.4.json` (identifiants anonymisés) et
`tests/test_router_api.py`.

Deux points structurants découverts sur le matériel réel :

1. **Les niveaux radio ne sont pas plats.** Ils vivent sous
   `cellular.rat[].band[].signal` : le modem agrège plusieurs porteuses simultanément
   (en 5G NSA : une porteuse 5G n78 pour les données + une ou plusieurs ancres LTE),
   chacune avec ses propres RSRP/RSRQ/SINR. On expose toutes les porteuses dans `bands`
   et on retient comme valeurs « principales » celles de la porteuse qui porte les
   données (RAT correspondant au champ `network`).

2. **L'identité de cellule est sous `cellular.cellTower`** (`cellId`, `tac`, `cellPlmn`),
   et l'opérateur sous `cellular.carrier.name`. `mcc`/`mnc` sont des chaînes ("208", "01") :
   c'est leur GRAPHIE qui est conservée, en un seul `plmn` — le zéro de tête du MNC distingue
   deux réseaux, et aucun calcul ne se fait jamais sur ces nombres.

Authentification : deux modes selon la configuration du routeur.
  - compte admin  -> POST /api/login (session par cookie) — mode utilisé pour la capture ;
  - client API    -> POST /api/auth.token.grant (clientId/secret créés dans l'admin web).
"""

from __future__ import annotations

import asyncio
import hashlib
import ipaddress
import logging
import math
import re
import socket
import time
from datetime import datetime, timedelta, timezone
from typing import NamedTuple
from urllib.parse import urlsplit

import httpx

from ..fleet import RouterConfig
from ..sessions import _TROU_MAX_S
from ..models import (
    RouterEvent,
    BandMeasurement,
    BondProfile,
    LanClients,
    BandwidthAllowance,
    GnssFix,
    DeviceCapabilities,
    DeviceInfo,
    LanNetwork,
    PowerSource,
    SimSlot,
    TunnelLink,
    TunnelStatus,
    WanRadioStatus,
    utcnow,
    WifiLink,
    PEER_DOWN,
)

logger = logging.getLogger(__name__)

# "5G Band n78 (3500 MHz)" -> n78 ; "LTE Band 1 (2100 MHz)" -> 1
_BAND_RE = re.compile(r"band\s+(n?\d+)", re.IGNORECASE)

# Famille de technologie radio, pour rapprocher un mode réseau d'un libellé de porteuse.
# Le MAX HD4 MBX annonce "LTE-A" côté WAN et "LTE Band 3 (1800 MHz)" côté porteuse ;
# le Dome Pro Duo annonce "5G" et "5G Band n78". Comparer les familles réconcilie les deux.
_RAT_FAMILY_RE = re.compile(r"\b(5G|LTE|4G|3G|UMTS|HSPA\+?|EDGE|GPRS)\b", re.IGNORECASE)

# statusLed considérés comme « connecté ».
_LED_UP = {"green", "amber", "yellow"}

# Types de WAN portant des métriques radio. "gobi" est le libellé des modems
# cellulaires sur les firmwares antérieurs à 8.0.1 — accepté par précaution,
# NON VALIDÉ sur matériel : personne n'a de Peplink assez ancien pour l'essayer.
_RADIO_WAN_TYPES = {"cellular", "gobi", "wifi"}

# Types supervisés en plus des précédents : un lien filaire n'a pas de métrique
# radio, mais il fait partie de la liaison agrégée et en porte souvent le débit
# principal. Les WAN `modem` (emplacements USB) restent exclus tant qu'aucun
# périphérique n'y est reconnu.
_WIRED_WAN_TYPES = {"ethernet"}

# Unités possibles de `status.traffic.bandwidth`, ramenées en kbit/s.
# Intervalle entre deux lectures de `status.wan.latency`.
#
# Une par MINUTE, et c'est la charge utile qui le permet : le routeur y rend une
# série de 360 points espacés de dix secondes, soit l'heure écoulée. On ne perd
# donc rien à espacer — au pire, la valeur retenue a dix secondes, ce qui est
# l'ordre de grandeur du cycle de scrutation lui-même.
#
# La piste était chiffrée à +20 % de cycle sur l'hypothèse d'une lecture à
# chaque tour. À ce rythme-ci, c'est un cycle sur onze : +1,7 % en moyenne, pour
# la seule latence que possèdent un lien filaire, un lien hors du bond, et un
# châssis sans SpeedFusion.
_WAN_LATENCY_INTERVAL_S = 60.0

_KBPS_FROM = {"bps": 0.001, "kbps": 1.0, "mbps": 1000.0, "gbps": 1_000_000.0}

# Codes d'erreur Peplink signalant une session absente ou expirée.
# 401 = pas de session ; 301 = requête d'authentification rejetée ; 403 = interdit.
_AUTH_FAIL_CODES = {301, 401, 403}

# Refus de DROITS : les identifiants sont bons, mais le client API n'a pas accès
# à CET endpoint. À distinguer d'un 401, qui condamne tout le relevé.
_FORBIDDEN_CODE = 403

# Ce qu'aucun refus ne retient hors du périmètre (C-1). Sans le relevé WAN ni
# la fiche système, il n'y a plus d'équipement supervisé : les retenir sur un
# refus passager — le serveur web du routeur qui redémarre juste après un
# relogin — rendait l'équipement « injoignable » pour la vie du processus, et
# le seul moyen d'en sortir n'apparaissait pas à l'écran. Un vrai refus de
# droits sur eux coûte un login par cycle : un login qui RÉUSSIT, qui ne
# compte donc pas dans les cinq échecs qui suspendent l'accès admin.
_JAMAIS_HORS_PERIMETRE = frozenset({"status.wan.connection", "status.system.info"})
# Un complément retenu hors du périmètre est redemandé passé ce délai : un
# login toutes les dix minutes sur un vrai refus de droits, plutôt qu'une
# lecture condamnée jusqu'au redémarrage du serveur sur un refus passager.
_HORS_PERIMETRE_REESSAI_S = 600.0


async def avant_echeance(echeance: float | None, coro):
    """Attend `coro` jusqu'à `echeance` (horloge de la boucle) au plus.

    Rend `(True, résultat)`, ou `(False, None)` si l'échéance tombe d'abord —
    auquel cas `coro` est annulée, ou jamais lancée si l'échéance est passée.
    """
    if echeance is None:
        return True, await coro
    if asyncio.get_running_loop().time() >= echeance:
        coro.close()
        return False, None
    try:
        async with asyncio.timeout_at(echeance):
            return True, await coro
    except TimeoutError:
        return False, None

# Nombre de cycles sautés entre deux sondes de `status.pepvpn` sur un équipement
# où aucun pair n'a jamais été vu. La période mesurée est de 5,3 s — 2,11 s de
# cycle, dont cinq requêtes HTTPS en série à ~420 ms, plus l'attente de 3 s —
# soit 11 requêtes par minute et plus de 16 000 par jour et par équipement, pour
# un endpoint qui répond `stat: ok` avec une liste vide. On espace sans jamais
# condamner, car un profil SpeedFusion peut être créé après le démarrage de la
# supervision.
_TUNNEL_IDLE_PROBE_CYCLES = 10

# Les profils SpeedFusion changent à l'échelle de la minute : les relire à
# chaque cycle coûterait une requête de plus toutes les cinq secondes.
_PROFIL_INTERVAL_S = 60.0
# Les clients du LAN, à la même cadence : un décompte, pas une mesure fine.
_CLIENTS_INTERVAL_S = 60.0
# L'heure de radio du routeur : lue à l'appairage, puis toutes les 10 minutes
# (41 Ko sur le Dome) — l'heure qu'elle couvre chevauche largement la
# précédente, un trou de moins de 50 min est comblé (R-3.1).
_QUALITE_INTERVAL_S = 600.0
# La température des modems, à la même cadence ; une mesure plus vieille que
# 90 s ne décrit plus l'instant (une relecture manquée tolérée, pas deux).
_TEMPERATURE_INTERVAL_S = 60.0
_TEMPERATURE_MAX_S = 90.0

# Paramètres de requête dont la valeur est un secret. Le jeton d'accès voyage en
# clair dans l'URL — le firmware refuse l'en-tête Bearer, voir `_request` — donc
# dans le message d'erreur que httpx construit à partir de l'URL complète.
_SECRET_QUERY_RE = re.compile(
    r"\b([\w.-]*(?:token|secret|password|passwd|pwd|apikey|api_key)[\w.-]*)=[^&\s'\"]*",
    re.IGNORECASE,
)


def _corps(payload: object) -> dict:
    """Le `response` d'une charge utile, et rien d'autre.

    Tous les analyseurs écrivaient la même incantation : le `response` d'une
    charge utile, replié sur un dictionnaire vide par un `or`. Ce repli couvre
    `None` et le vide — pas une CHAÎNE, ni une liste non vide, ni un nombre.

    Mesuré en passant huit charges dégénérées aux dix analyseurs : neuf
    levaient `AttributeError: 'str' object has no attribute 'get'`.

    Le chemin d'erreur rattrape bien l'exception, mais il l'étiquette
    « injoignable » — pour un équipement qui a répondu, et vite. C'est
    exactement la confusion de diagnostic que ce projet combat ailleurs : elle
    envoie vérifier un raccordement quand il faut lire une charge utile.
    """
    if not isinstance(payload, dict):
        return {}
    reponse = payload.get("response")
    return reponse if isinstance(reponse, dict) else {}


def _bloc(conteneur: object, cle: str) -> dict:
    """Le sous-bloc `cle` de `conteneur` s'il est un OBJET, sinon un objet vide.

    `_corps` ne protège que le premier niveau. En dessous, chaque analyseur
    écrivait `x.get("cellular") or {}` : le `or` couvre `None` et le vide, pas
    une chaîne. Un `carrier: "Orange"` à la place de `carrier: {"name": …}`
    levait `AttributeError` dans `parse_wan_connection` — le relevé ENTIER
    était perdu, et le poller étiquetait « injoignable » un équipement qui
    venait de répondre. Un bloc d'un autre type est un bloc absent : ce qu'il
    portait n'est pas lu, le reste l'est.
    """
    if not isinstance(conteneur, dict):
        return {}
    valeur = conteneur.get(cle)
    return valeur if isinstance(valeur, dict) else {}


def _liste(conteneur: object, cle: str) -> list:
    """Le pendant de `_bloc` pour une LISTE : `peer: 7` n'est pas itérable."""
    if not isinstance(conteneur, dict):
        return []
    valeur = conteneur.get(cle)
    return valeur if isinstance(valeur, list) else []


def redact(text: str) -> str:
    """Masque la valeur des paramètres de requête qui portent un secret.

    Mesuré : `resp.raise_for_status()` produit
    « Server error '500 …' for url 'https://…/api/status.wan.connection
    ?accessToken=<jeton>' ». Ce message finissait dans `RouterSummary.error`,
    donc sur une route HTTP qui n'est pas restreinte à la boucle locale : le
    jeton d'accès du routeur était lisible par quiconque atteignait l'interface.
    """
    return _SECRET_QUERY_RE.sub(r"\1=***", text)


class RouterApiError(RuntimeError):
    """Échec applicatif de l'API du routeur.

    `code` porte le code d'erreur du firmware quand la réponse en contient un
    (`{"stat":"fail","code":403,…}`). Les appelants s'en servent pour distinguer
    un refus qui condamne tout le relevé d'un refus qui ne concerne qu'un
    endpoint optionnel — la seule chose qui les sépare est ce nombre.
    """

    def __init__(self, message: str, code: int | None = None) -> None:
        super().__init__(message)
        self.code = code


class AuthenticationError(RouterApiError):
    """Les identifiants ont été refusés.

    Distincte des autres échecs pour une raison opérationnelle : cinq tentatives
    de connexion ratées suspendent l'accès admin depuis l'adresse pendant cinq
    minutes. Une erreur de ce type ne doit JAMAIS déclencher de nouvelle
    tentative automatique — c'est à l'utilisateur de corriger et de relancer.

    Attention : un `code` à 403 n'est PAS un refus d'identifiants. Le firmware
    l'emploie pour dire que le client API n'a pas les droits sur cet endpoint,
    alors que la session est parfaitement valide.
    """


class CredentialsRefusedError(AuthenticationError):
    """Le routeur a refusé la connexion elle-même. Ne plus jamais réessayer.

    Sous-classe d'`AuthenticationError` parce qu'un appelant qui traitait déjà
    celle-ci continue de fonctionner. La distinction est pourtant décisive, et
    elle ne l'était pas : une session expirée rend elle aussi un 401, mais sur
    une LECTURE, et elle doit provoquer une reconnexion — c'est le
    fonctionnement normal, le jeton vaut 48 h. Seul un refus du `POST /api/login`
    (ou de la demande de jeton) dit que les identifiants sont mauvais.

    Mesuré avant correction : le poller attrapait `AuthenticationError` sans
    distinguer les deux, et `_get()` réauthentifiait en tête de chaque lecture —
    soit une tentative de connexion PAR CYCLE, indéfiniment. Reproduit sur
    transport bouchonné : 6 tentatives en 6 cycles. À 5,11 s de cadence, les
    cinq échecs qui suspendent l'accès admin cinq minutes sont atteints en 26
    secondes, et la boucle recommence à chaque expiration. L'outil verrouillait
    donc l'exploitant hors de son propre routeur, au moment précis où il en
    avait besoin.
    """


def _base_url(host: str) -> str:
    """L'origine du routeur, schéma compris.

    `host` vient de `routers.yaml` ou du formulaire d'appairage. Il peut être
    nu (`192.168.63.1`) — HTTPS par défaut, comme depuis le début — ou porter
    son schéma (`http://10.10.1.1`) pour un équipement sans TLS. Un `/` final
    est retiré pour que `f"{base}/api/…"` ne double jamais la barre.
    """
    hote = host.strip().rstrip("/")
    # Le schéma est comparé sans casse : `HTTP://10.10.1.1` tapé à la main
    # devenait `https://HTTP://10.10.1.1`, que httpx résolvait sur l'hôte
    # littéral « http ».
    if hote.lower().startswith(("http://", "https://")):
        schema, reste = hote.split("://", 1)
        return f"{schema.lower()}://{reste}"
    return f"https://{hote}"


def hote_canonique(host: str | None) -> str | None:
    """L'IDENTITÉ d'un hôte, pour comparer deux graphies.

    Depuis que `host` peut porter son schéma, `10.10.1.1`, `http://10.10.1.1`
    et `http://10.10.1.1/` désignent le même équipement — et trois comparaisons
    de chaînes le manquaient : la découverte proposait d'appairer un équipement
    déjà suivi, et l'appairage en créait un second. Le schéma n'entre pas dans
    l'identité : c'est une manière de joindre l'équipement, pas l'équipement.
    """
    if not host:
        return None
    hote = host.strip().rstrip("/")
    if hote.lower().startswith(("http://", "https://")):
        hote = hote.split("://", 1)[1]
    return hote.lower() or None


class EndpointRefusedError(RouterApiError):
    """Le firmware a explicitement refusé l'endpoint (`stat: fail`).

    C'est la seule forme de refus qui autorise à condamner définitivement une
    fonctionnalité : l'équipement a répondu, et sa réponse dit non. Une erreur de
    transport ou une réponse illisible restent, elles, des pannes passagères.
    """


def _is_auth_failure(payload: dict) -> bool:
    """Le routeur signale-t-il une session absente ou expirée ?

    Vrai uniquement pour un refus d'authentification : les autres échecs
    (`code 999 unknown function`…) ne doivent pas déclencher de relance inutile.
    """
    if payload.get("stat") != "fail":
        return False
    if payload.get("code") in _AUTH_FAIL_CODES:
        return True
    return "unauthorized" in str(payload.get("message") or "").lower()


def _drapeau(bloc: dict, cle: str) -> bool | None:
    """Un booléen du firmware, ou None s'il ne l'a pas publié.

    `bool(bloc.get(cle))` faisait d'une clé absente un `False` affirmé — le
    même défaut, corrigé pour `carrierAggregation`, se reproduisait sur les
    emplacements SIM : un firmware qui ne dit rien de la carte la déclarait
    absente à l'écran.
    """
    return bool(bloc[cle]) if cle in bloc else None


def _wifi_band(channel: object) -> str | None:
    """Bande Wi-Fi déduite du numéro de canal.

    Prend `object` et non `int | None` : le canal vient du routeur, et
    `_wifi_band('6')` levait `TypeError`. La signature promettait un entier que
    rien ne garantissait.
    """
    channel = _to_int(channel)
    if channel is None:
        return None
    # Le numéro de canal ne suffit pas à nommer la bande : le Wi-Fi 6E numérote
    # 1 à 233 dans la bande 6 GHz, en collision avec 1-14 (2,4 GHz) et 36-177
    # (5 GHz), et le firmware ne publie que `channel`. La lecture 2,4/5 GHz est
    # de loin la plus probable sur un WAN Wi-Fi — c'est elle qu'on garde —, et
    # seuls les canaux au-delà du dernier canal 5 GHz (177, UNII-4) prouvent la
    # bande 6 GHz. La borne était 196, qui n'existe dans aucune des deux.
    if channel <= 14:
        return "2,4 GHz"
    if channel <= 177:
        return "5 GHz"
    return "6 GHz"


# Un ECI LTE tient sur 28 bits : 20 pour l'eNodeB, 8 pour la cellule.
_ECI_MAX = 1 << 28
_ECI_SECTOR_BITS = 8


def _split_eci(
    cell_id: object, rat: object, *, ancre_lte: bool = True
) -> tuple[int | None, int | None]:
    """Décompose un ECI en (eNodeB, cellule), quand la décomposition a un sens.

    En LTE — et en 5G NSA, où la cellule de contrôle référencée par `cellTower`
    est l'ancre LTE — l'identifiant est un ECI 28 bits dont le découpage est fixe.
    En 5G SA en revanche, c'est un NCI 36 bits dont la longueur du champ gNB est
    configurable par l'opérateur : la décomposition serait une invention, on s'en
    abstient. Le garde-fou sur la taille écarte aussi un NCI mal étiqueté.

    « NSA ⇒ `cellId` est l'ancre LTE » n'a été établi que sur un modem, le
    Quectel RM520N-GL du Dome. Un NCI court passerait la garde de taille et
    serait découpé en station et secteur inventés. `ancre_lte` est le témoin
    de cohérence que l'appelant tient : en NSA, une porteuse LTE doit figurer
    parmi celles que le modem mesure — c'est l'ancre. Sans elle, l'identifiant
    ne peut pas être celui de l'ancre, et on s'abstient.

    Sert à repérer les WAN campant sur la même station de base : quatre SIM sur un
    même eNodeB n'offrent aucune diversité de trajet, quels que soient les Cell ID.
    """
    cell_id = _to_int(cell_id)
    if cell_id is None or not 0 <= cell_id < _ECI_MAX:
        return None, None
    # `(rat or "").upper()` levait `AttributeError` sur un `rat` numérique.
    label = (_texte(rat) or "").upper()
    if "5G" in label and "NSA" not in label:
        return None, None  # 5G SA : découpage non déterminable
    if "NSA" in label and not ancre_lte:
        return None, None  # NSA sans porteuse LTE mesurée : ce n'est pas l'ancre
    return cell_id >> _ECI_SECTOR_BITS, cell_id & ((1 << _ECI_SECTOR_BITS) - 1)


def _identite_cellule(cell_tower: dict) -> int | None:
    """L'identifiant COMPLET de la cellule, tel que `cellTower` le porte.

    Sur les trois firmwares 8.5.4 connus, `cellId` et `cellUtranId` sont
    égaux. Sur un Dome Pro Duo en 8.5.1 build 5714, `cellId` vaut 8470 et
    `cellUtranId` 25698582 : 8470 en est exactement les SEIZE BITS BAS. Lu tel
    quel, `cellId` donnait une cellule et une station (eNB 33) qui n'existent
    pas — l'ECI réel est eNB 100385, secteur 22, le même secteur. On ne retient
    `cellUtranId` que dans ce cas précis — `cellId` égal à sa partie basse —
    pour ne rien inventer sur un firmware qui publierait autre chose.
    """
    cell_id = _to_int(cell_tower.get("cellId"))
    complet = _to_int(cell_tower.get("cellUtranId"))
    if cell_id is not None and complet is not None and complet != cell_id and complet & 0xFFFF == cell_id:
        return complet
    return cell_id


def _rat_family(label: object) -> str | None:
    """Famille de technologie d'un libellé : "LTE-A" -> "LTE", "5G NSA" -> "5G"."""
    label = _texte(label)
    if not label:
        return None
    match = _RAT_FAMILY_RE.search(label)
    return match.group(1).upper() if match else None


def _technology_label(cellular: dict) -> str | None:
    """Libellé de technologie le plus informatif pour l'affichage.

    `dataTechnology` vaut "5G NSA" sur Dome Pro Duo — plus précis que `network` ("5G").
    Mais il vaut "FDD / CA Downlink" sur MAX HD4 MBX : un descripteur de duplex et
    d'agrégation, pas une technologie. On ne le retient donc que s'il nomme bien une
    génération, sinon on garde `network` ("LTE-A").
    """
    data_tech = _texte((cellular or {}).get("dataTechnology"))
    if data_tech and _rat_family(data_tech):
        return data_tech
    return _texte((cellular or {}).get("network")) or data_tech


def _to_float(value) -> float | None:
    """Un nombre fini, ou None : `"nan"` et `"inf"` sont des chaînes que
    `float()` accepte. Un `NaN` passé en RSRP traverse toutes les comparaisons
    en silence — jamais sous le seuil, jamais au-dessus — et Python le sérialise
    en `NaN`, que JSON n'admet pas : le navigateur ne pouvait plus lire le
    relevé. Une valeur non finie est une valeur absente.
    """
    try:
        nombre = float(value)
    except (TypeError, ValueError):
        return None
    return nombre if math.isfinite(nombre) else None


def _to_int(value) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _plmn(cellular: dict) -> str | None:
    """Le code réseau « MCC-MNC », tel que le routeur l'écrit.

    Les deux champs sont des chaînes côté firmware — « 208 » et « 01 » — et
    partaient dans `_to_int`. Le zéro de tête du MNC compte : il distingue le
    MNC « 01 » du MNC « 001 », deux réseaux différents. Ce qu'on garde est donc
    la graphie publiée, assemblée en un seul identifiant.
    """
    mcc = _texte((cellular or {}).get("mcc"))
    mnc = _texte((cellular or {}).get("mnc"))
    if not mcc or not mnc:
        return None
    return f"{mcc}-{mnc}"


def _texte(value: object) -> str | None:
    """Un champ TEXTUEL du routeur, ramené à du texte — ou à rien.

    `_to_int` et `_to_float` existent depuis toujours pour les champs
    numériques : ils absorbent ce que le firmware met de travers et rendent
    `None`. Les champs textuels, eux, étaient consommés tels quels, et trois
    fonctions les passent directement à une expression régulière ou à
    `.upper()`. Vérifié à l'exécution sur un `network` numérique :

        _split_eci(1000, 4) -> AttributeError: 'int' object has no attribute 'upper'
        _rat_family(4)      -> TypeError: expected string or bytes-like object
        _band_code(3)       -> TypeError: expected string or bytes-like object

    La levée ne s'arrête pas à la porteuse : elle remonte au parseur, donc au
    relevé, donc c'est le cycle ENTIER de cet équipement qui tombe — et il
    retombe au cycle suivant, sur la même charge utile. Un seul champ mal typé
    suffit à rendre un routeur définitivement muet.

    Un nombre est converti : `band.name: 3` porte une information lisible. Un
    booléen, une liste, un objet ou un flottant non fini n'en portent pas — et
    en fabriquer un libellé serait une invention, ce que ce parseur refuse déjà
    de faire ailleurs pour la décomposition d'un identifiant 5G SA.
    """
    if isinstance(value, str):
        return value.strip() or None
    # `bool` est un `int` en Python : sans ce test, `True` deviendrait "True".
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return str(value) if math.isfinite(value) else None


def _band_code(label: object) -> str:
    """Code court d'une bande à partir du libellé du routeur."""
    label = _texte(label)
    if not label:
        return "?"
    match = _BAND_RE.search(label)
    if not match:
        return label
    token = match.group(1)
    return token if token.lower().startswith("n") else f"B{token}"


def _rat_entries(cellular: dict) -> list[dict]:
    """Entrées `rat[]`, ou repli sur un `band[]` à plat.

    Avant le firmware 8.1.2, les porteuses ne sont pas groupées par RAT : elles
    sont listées directement sous `cellular.band`. NON VALIDÉ sur matériel.
    """
    rats = cellular.get("rat")
    if isinstance(rats, list) and rats:
        return [r for r in rats if isinstance(r, dict)]
    flat = cellular.get("band")
    return [{"name": "", "band": flat}] if isinstance(flat, list) and flat else []


def _parse_bands(cellular: dict) -> list[BandMeasurement]:
    """Aplatit `cellular.rat[].band[]` en une liste de porteuses mesurées."""
    bands: list[BandMeasurement] = []
    for rat in _rat_entries(cellular):
        rat_name = _texte(rat.get("name")) or ""
        for band in _liste(rat, "band"):
            if not isinstance(band, dict):
                continue
            signal = _bloc(band, "signal")
            label = _texte(band.get("name"))
            if not label:
                # Sans libellé, une porteuse n'a pas d'identité : pendant une
                # renégociation le routeur en publie une vide, et l'écran
                # affichait « ? » comme bande — ni une mesure, ni une absence dite.
                continue
            # Le MAX HD4 MBX laisse `rat.name` vide : la famille se déduit alors du
            # libellé de la porteuse elle-même ("LTE Band 3 (1800 MHz)" -> "LTE").
            bands.append(
                BandMeasurement(
                    rat=rat_name or _rat_family(label) or "?",
                    band=_band_code(label),
                    label=label,
                    channel=_to_int(band.get("channel")),
                    pci=_to_int(band.get("pci")),
                    channel_width=_texte(band.get("channelWidth")),
                    rsrp=_to_float(signal.get("rsrp")),
                    rsrq=_to_float(signal.get("rsrq")),
                    sinr=_to_float(signal.get("sinr")),
                    rssi=_to_float(signal.get("rssi")),
                )
            )
    return bands


def _primary_band(bands: list[BandMeasurement], network: str | None) -> BandMeasurement | None:
    """Porteuse porteuse de données : celle de la famille courante, sinon la première.

    En 5G NSA, `network` vaut "5G" : on retient la porteuse 5G (celle qui porte le débit),
    même si l'ancre LTE reste la cellule de contrôle référencée par `cellTower`.
    En LTE-A pure (MAX HD4 MBX), toutes les porteuses sont LTE : la première listée est
    la porteuse primaire — c'est d'ailleurs la seule dont le SINR soit renseigné.
    """
    if not bands:
        return None
    wanted = _rat_family(network)
    if wanted:
        for band in bands:
            if _rat_family(band.rat) == wanted:
                return band
    return bands[0]


def _effacer_signal_non_mesure(bands: list[BandMeasurement], primary: BandMeasurement | None) -> None:
    """Les porteuses secondaires du Telit LM960 publient `sinr: 0.0` — et
    souvent `rsrq: -3.0`, la borne théorique du RSRQ — quand le modem ne les
    mesure pas. Un SINR de 0 dB est une porteuse au bord du décrochage ; un
    RSRQ de −3 dB, une porteuse parfaite : les deux à la fois, sur une porteuse
    dont la primaire est à 14 dB, c'est une absence de mesure, pas une mesure.

    Rien n'est touché sur la PRIMAIRE, ni sur un modem qui mesure ses
    secondaires (le WAN 4 du même châssis publie quatre SINR non nuls). Seule
    la signature complète — secondaire, SINR exactement 0.0 — efface le SINR,
    et le RSRQ avec lui s'il est à la borne.
    """
    for band in bands:
        if band is primary or band.sinr != 0.0:
            continue
        band.sinr = None
        if band.rsrq == -3.0:
            band.rsrq = None


class _Counters(NamedTuple):
    """Compteurs cumulés d'UN sens de transmission, tels que le routeur les tient.

    Tous sont cumulés depuis l'établissement du tunnel : seule leur différence
    entre deux relevés dit quelque chose d'un instant.

    `repair` recouvre deux grandeurs que le firmware sépare par sens : à
    l'émission, les paquets de correction d'erreur et les duplications que
    SpeedFusion ajoute (`fec` + `redundant`) ; à la réception, les paquets que
    cette correction a permis de reconstituer (`recover`). Les deux racontent le
    même travail, vu de chaque bout.
    """

    packets: float = 0.0
    loss: float = 0.0
    out_of_order: float = 0.0
    # Octets réellement portés PAR LE BOND sur ce lien — à ne pas confondre avec
    # le débit de `status.traffic`, qui compte toute l'interface WAN, tunnel et
    # hors tunnel mêlés. C'est la seule mesure de ce qui passe vraiment par
    # l'agrégation, et elle arrive dans une charge utile déjà téléchargée.
    byte: float = 0.0
    repair: float = 0.0


class _LinkRates(NamedTuple):
    """Mesures d'un lien sur l'intervalle écoulé, séparées par sens.

    « up » désigne le sens montant (`transmit`), « down » le sens descendant
    (`receive`). Les séparer n'est pas une question de priorité entre les deux —
    l'équipement fournit de la connectivité, et l'usage qui en est fait décide
    lequel compte : les additionner laissait simplement le sens le plus chargé
    masquer la dégradation de l'autre.
    """

    loss_up: float | None = None
    loss_down: float | None = None
    out_of_order_up: float | None = None
    out_of_order_down: float | None = None
    # Dénominateur des quatre taux ci-dessus : paquets comptés sur l'intervalle,
    # par sens. Il voyage avec eux parce qu'un « 0,00 % » sur trois paquets de
    # keepalive et un « 0,00 % » sur cent mille paquets utiles s'écrivent
    # exactement pareil.
    packets_up: int | None = None
    packets_down: int | None = None
    # Débit porté par le bond sur ce lien, par sens, en kbit/s.
    tx_kbps: float | None = None
    rx_kbps: float | None = None
    # Paquets de redondance émis, et paquets reconstitués à la réception.
    repair_sent: int | None = None
    repair_used: int | None = None


def _worst(*values: float | None) -> float | None:
    """Pire valeur parmi celles qui ont été mesurées, None si aucune ne l'a été."""
    measured = [v for v in values if v is not None]
    return max(measured) if measured else None


# Écart maximal entre deux lectures des compteurs de tunnel au-delà duquel la
# référence précédente est jetée. Même raisonnement et même valeur que le
# `_TROU_MAX_S` de la fiche de session : au-delà d'une minute, la différence ne
# borne plus un intervalle observé mais un intervalle pendant lequel on ne
# regardait pas.
_ECART_MAX_S = _TROU_MAX_S  # une seule déclaration, et non deux liées par un commentaire


def _direction_rates(
    current: _Counters | None, previous: _Counters | None, elapsed: float | None = None
) -> tuple[float | None, float | None, int | None, float | None, int | None]:
    """(perte, désordre, paquets comptés) sur l'intervalle, pour UN seul sens.

    Les deux premiers sont des pourcentages, le troisième est leur DÉNOMINATEUR.
    Il était calculé ici même pour la division, puis jeté : un taux sortait donc
    sans la seule grandeur qui dise s'il pèse quelque chose. Sur un tunnel au
    repos, trois paquets de service suffisent à produire « 0,00 % » — ou 33,33 %,
    la seule valeur de perte non nulle relevée sur 11 937 mesures du matériel.

    Sans les deux bornes, le seul taux calculable serait la moyenne depuis
    l'établissement du tunnel — plusieurs heures : on préfère ne rien dire. Un
    compteur qui recule signale un tunnel remonté, et vaut la même abstention
    plutôt qu'un taux négatif. Dans ces deux cas le dénominateur lui-même est
    inconnu : il vaut None, et non zéro.

    Un intervalle où rien n'est passé, lui, EST mesuré : il rend un dénominateur
    de zéro et aucun taux. C'est le cas courant du tunnel au repos, et il se
    distingue ainsi de l'absence de mesure.
    """
    if current is None or previous is None:
        return None, None, None, None, None
    # Un recul sur N'IMPORTE lequel des compteurs signale un tunnel remonté :
    # tous repartent de zéro ensemble, et aucune différence n'a plus de sens.
    if any(c < p for c, p in zip(current, previous)):
        return None, None, None, None, None

    delta = _Counters(*(c - p for c, p in zip(current, previous)))

    # Le débit ne dépend pas des paquets mais des octets, et il a besoin du temps
    # écoulé — que les taux, eux, n'exigeaient pas puisqu'ils sont des rapports.
    kbps = (
        round(delta.byte * 8 / 1000 / elapsed, 1)
        if elapsed and elapsed > 0
        else None
    )
    repair = round(delta.repair)

    total = delta.packets + delta.loss
    if total <= 0:
        return None, None, 0, kbps, repair
    return (
        round(100 * delta.loss / total, 2),
        round(100 * delta.out_of_order / total, 2),
        round(total),
        kbps,
        repair,
    )


class RouterApiPeplinkClient:
    """Implémente l'interface PeplinkClient contre l'API locale du routeur."""

    def __init__(self, router: RouterConfig) -> None:
        if not router.host:
            raise RouterApiError(f"Routeur {router.id} : 'host' manquant pour le mode router")
        self.router_id = router.id
        self._cfg = router
        # Le schéma était figé en HTTPS : `f"https://{host}"`. Le premier
        # équipement rencontré hors du réseau de développement — un Peplink en
        # 8.5.4 build 5808, découvert par son préfixe MAC — ne parle qu'HTTP sur
        # le port 80, sans TLS. L'appairage tombait donc sur une erreur de
        # connexion avant d'avoir envoyé quoi que ce soit. Un hôte qui porte
        # déjà son schéma est respecté ; sans schéma, HTTPS reste le défaut.
        self._base = _base_url(router.host)
        self._token: str | None = None
        self._authenticated = False
        # Une seule authentification en vol. L'identification, la fiche et le
        # relevé partent ensemble au premier cycle et trouvaient tous trois la
        # session absente : trois POST /api/login pour une seule session utile,
        # et sur des identifiants faux, trois des cinq échecs qui suspendent
        # l'accès admin, consommés d'un coup. Le compteur de générations dit à
        # qui a vu sa session expirer si un autre l'a déjà renouvelée.
        self._auth_lock = asyncio.Lock()
        self._auth_generation = 0
        self._credentials_refused = False
        # Passe à False si l'équipement refuse `status.traffic` : inutile de le
        # redemander à chaque cycle sur un modèle qui ne le propose pas.
        self._supports_traffic = True
        self._supports_tunnel = True
        self._supports_wan_latency = True
        self._supports_log = True
        # Endpoints que CES identifiants ne couvrent pas — un refus d'accès qui
        # survit à une authentification réussie. Retenus pour ne pas les
        # redemander : chaque tentative coûterait une ré-authentification.
        # L'instant du refus, en horloge monotone : la mémoire s'efface passé
        # `_HORS_PERIMETRE_REESSAI_S`.
        self._hors_perimetre: dict[str, float] = {}
        # Instant de la dernière lecture de `status.wan.latency`, en horloge
        # MONOTONE : elle ne recule pas quand le système se synchronise, ce que
        # `utcnow()` fait — et une horloge qui recule d'une minute suspendrait
        # la lecture pendant deux.
        self._wan_latency_at: float | None = None
        # Compteurs du cycle précédent, par lien puis par sens : les valeurs du
        # routeur sont cumulées depuis l'établissement du tunnel et ne disent
        # rien d'un instant. Seule leur variation est exploitable.
        self._tunnel_counters: dict[str, tuple[_Counters | None, _Counters | None]] = {}
        # Instant de la lecture ci-dessus : le débit se calcule sur le temps
        # RÉELLEMENT écoulé, pas sur la période de consigne.
        self._tunnel_counters_at: float | None = None
        # Pair auquel ces compteurs se rapportent. Ils sont cumulés PAR TUNNEL :
        # les comparer d'un pair à l'autre soustrairait deux grandeurs sans
        # rapport, ce que le test de monotonie ne rattrape pas.
        self._tunnel_peer: str | None = None
        # Cycles restant à sauter avant la prochaine sonde de `status.pepvpn`,
        # quand aucun pair n'a encore jamais été vu.
        self._tunnel_skip = 0
        # L'heure de radio du routeur, en attente d'écriture (R-3.1).
        self.serie_qualite: dict[str, list[tuple[datetime, dict]]] | None = None
        self._qualite_at: float | None = None
        self._supports_qualite = True
        # La dernière série de latence lue, en attente d'écriture (R-3.2).
        self.serie_latence: dict[str, list[tuple[datetime, float | None]]] | None = None
        # Profils SpeedFusion, relus une fois par minute (R-1.5).
        self.bond_profiles: list[BondProfile] = []
        self._profils_at: float | None = None
        self._supports_profils = True
        # Clients actifs du LAN, relus une fois par minute (R-1.10).
        self.lan_clients: LanClients | None = None
        self._clients_at: float | None = None
        self._supports_clients = True
        # Température des modems (R-1.11) : dernière mesure par WAN, et les
        # WAN que le firmware refuse — un refus vaut pour UN WAN.
        self._temperatures: dict[str, tuple[float, datetime]] = {}
        self._temperature_at: float | None = None
        self._temperature_refusee: set[str] = set()
        self._supports_temperature = True
        # Seuil de latence du bond, tel que le PROFIL le configure. Arrive avec
        # la liste des pairs, dans une charge utile déjà téléchargée.
        self._latency_cutoff: float | None = None
        # Sous-réseaux acheminés par le tunnel, tels que le pair les déclare.
        self.bond_routes: list[str] = []
        # Depuis quand le bond TIENT, tel que le routeur le compte. Exposé comme
        # `bond_routes` : le poller le recopie sur le record, et l'API le publie.
        self.tunnel_uptime_s: int | None = None
        # L'équipement a répondu qu'il n'a AUCUN profil SpeedFusion : c'est une
        # observation, pas une absence de regard. Sans ce témoin, « aucun objet
        # tunnel » se lisait comme « sonde sautée », et une alerte de bond
        # ouverte avant le retrait du profil ne se résolvait plus de la session.
        self.tunnel_absent = False
        # Les WAN que le routeur déclare `enable: false`, au dernier relevé.
        self.wans_desactives: set[str] = set()
        # Les Peplink présentent un certificat auto-signé : verify désactivé par défaut.
        self._http = httpx.AsyncClient(verify=router.verify_ssl, timeout=15.0)

    # --- authentification ---
    def _raise_for_status(
        self, resp: httpx.Response, what: str, *, credentials: bool = False
    ) -> None:
        """Traduit un statut HTTP d'erreur en `RouterApiError` expurgée.

        Le jeton d'accès voyage en paramètre de requête, faute d'en-tête Bearer
        accepté par le firmware : le message que httpx construit contient donc
        l'URL complète, jeton compris. Comme ce message remonte jusqu'à une route
        HTTP qui n'est pas restreinte à la boucle locale, aucune
        `HTTPStatusError` brute ne doit s'échapper d'ici. Le chaînage est coupé
        (`from None`) pour la même raison : l'exception d'origine porte la même
        URL et réapparaîtrait dans n'importe quelle trace.

        `credentials=True` sur les deux endpoints d'authentification. Le refus
        d'identifiants n'arrive pas toujours en `{"stat":"fail"}` sur un 200 :
        mesuré sur MAX HD4 MBX 8.5.4, `client_id`/`client_secret` au lieu de
        `clientId`/`clientSecret` rend un **401 « Client not found »**. Sans ce
        drapeau, ce refus repartait en `RouterApiError` nue, que la boucle de
        scrutation range dans son `except Exception` générique — le verrou ne
        s'armait pas, et cinq cycles suffisaient à suspendre l'accès admin cinq
        minutes. Un 4xx sur un endpoint d'authentification est un refus ; un 5xx
        reste une panne de l'équipement, qu'on a le droit de retenter.
        """
        try:
            resp.raise_for_status()
        except httpx.HTTPStatusError as exc:
            message = redact(f"Routeur {self.router_id} : {what} — {exc}")
            if credentials and 400 <= resp.status_code < 500:
                self._credentials_refused = True
                raise CredentialsRefusedError(message, code=resp.status_code) from None
            raise RouterApiError(message) from None

    async def _ensure_authenticated(self, expiree: int | None) -> int:
        """Garantit une session, une seule authentification à la fois.

        `expiree` est la génération de session que l'appelant a vue refusée ;
        si un autre appel l'a déjà renouvelée entre-temps, la sienne est bonne
        et il n'y a rien à refaire. Rend la génération courante.
        """
        async with self._auth_lock:
            if expiree is not None and expiree == self._auth_generation:
                logger.info("Session expirée sur %s : ré-authentification", self.router_id)
                self._authenticated = False
                self._token = None
            if not self._authenticated:
                await self._authenticate()
            return self._auth_generation

    async def _authenticate(self) -> None:
        # Un refus déjà constaté ne se retente pas de lui-même : c'est le verrou
        # qui empêche la boucle de scrutation de consommer les cinq tentatives
        # qui suspendent l'accès admin. Seule une action explicite le lève.
        if self._credentials_refused:
            raise CredentialsRefusedError(
                f"Routeur {self.router_id} : identifiants déjà refusés, "
                "aucune nouvelle tentative sans action explicite"
            )
        if self._cfg.client_id and self._cfg.client_secret:
            await self._auth_client_credentials()
        elif self._cfg.username:
            await self._auth_admin_session()
        else:
            # Rien à retenter non plus : aucun identifiant ne se matérialisera.
            self._credentials_refused = True
            raise CredentialsRefusedError(
                f"Routeur {self.router_id} : aucun identifiant "
                "(client_id/client_secret ou username/password)"
            )

    def allow_new_credentials(self) -> None:
        """Lève le verrou : l'exploitant a corrigé les identifiants.

        ET RELÈVE CE QUE LE REFUS AVAIT CONDAMNÉ. Un refus d'identifiants arrivé
        sur `/api/login` porte un statut HTTP 403, que les gardes prenaient pour
        un refus de DROITS : `_supports_traffic` et `_supports_tunnel` passaient
        à faux pour la vie du processus. L'exploitant corrigeait ses identifiants,
        la supervision repartait — et le débit par WAN restait éteint jusqu'au
        redémarrage du serveur, sans rien qui le signale.

        Les deux causes sont corrigées, celle-ci en est le filet : après une
        reprise d'identifiants, on redonne sa chance à chaque sonde.
        """
        self._credentials_refused = False
        self._authenticated = False
        self._token = None
        self._supports_traffic = True
        self._supports_tunnel = True
        self._supports_wan_latency = True
        self._supports_log = True
        self._supports_profils = True
        self._supports_clients = True
        self._supports_qualite = True
        self._supports_temperature = True
        self._temperature_refusee = set()
        self._hors_perimetre = {}   # d'autres identifiants, un autre périmètre
        self._tunnel_skip = 0

    def adopter(self, router: RouterConfig) -> None:
        """Prend l'identité définitive d'un équipement que ce client a identifié.

        Seuls l'identifiant et le nom changent : l'appairage construit les deux
        configurations avec le même hôte, les mêmes identifiants et le même
        réglage TLS, et la session ouverte est gardée (R-1.13).
        """
        self.router_id = router.id
        self._cfg = router

    def _json_d_authentification(self, resp: httpx.Response, what: str) -> dict:
        """Le corps d'une réponse d'authentification, ou une panne passagère.

        Un routeur en fin de démarrage sert une page HTML : `resp.json()`
        levait un JSONDecodeError, ni RouterApiError ni httpx.HTTPError, qui
        traversait les `except` des compléments et faisait perdre le relevé
        (C-29). Ce n'est pas un refus d'identifiants : le verrou ne s'arme pas.
        """
        try:
            payload = resp.json()
        except ValueError:
            raise RouterApiError(
                f"Routeur {self.router_id} : réponse non-JSON sur {what}"
            ) from None
        if not isinstance(payload, dict):
            raise RouterApiError(f"Routeur {self.router_id} : réponse inattendue sur {what}")
        return payload

    async def _auth_client_credentials(self) -> None:
        """Jeton porteur via un client API — vérifié sur MAX HD4 MBX 8.5.4.

        Les noms de champs ne suivent PAS la convention OAuth2, contrairement à ce
        que la forme de l'endpoint suggère. Mesuré sur matériel :

          form-urlencoded  clientId / clientSecret        -> stat: ok
          form-urlencoded  client_id / client_secret      -> 401 "Client not found"
          JSON             clientId / clientSecret        -> 401 "Internal Error"

        Il n'y a pas non plus de `grant_type` à envoyer.
        """
        resp = await self._http.post(
            f"{self._base}/api/auth.token.grant",
            data={"clientId": self._cfg.client_id, "clientSecret": self._cfg.client_secret},
        )
        self._raise_for_status(resp, "/api/auth.token.grant", credentials=True)
        payload = self._json_d_authentification(resp, "/api/auth.token.grant")
        if payload.get("stat") != "ok":
            self._credentials_refused = True
            raise CredentialsRefusedError(
                f"Routeur {self.router_id} : jeton refusé "
                f"({payload.get('message') or 'sans message'})"
            )
        token = _corps(payload).get("accessToken")
        if not token:
            raise RouterApiError(f"Routeur {self.router_id} : jeton absent de la réponse d'auth")
        self._token = token
        self._authenticated = True
        self._auth_generation += 1

    async def _auth_admin_session(self) -> None:
        """Session par cookie — mode vérifié sur le Dome Pro Duo."""
        resp = await self._http.post(
            f"{self._base}/api/login",
            json={"username": self._cfg.username, "password": self._cfg.password},
        )
        self._raise_for_status(resp, "/api/login", credentials=True)
        payload = self._json_d_authentification(resp, "/api/login")
        if payload.get("stat") != "ok":
            self._credentials_refused = True
            raise CredentialsRefusedError(
                f"Routeur {self.router_id} : login refusé ({payload.get('message', 'inconnu')})"
            )
        self._authenticated = True  # le cookie de session est porté par le client httpx
        self._auth_generation += 1

    async def _request(
        self, endpoint: str, params: dict | None = None, corps: dict | None = None,
    ) -> dict:
        """GET brut — ou POST quand un `corps` est donné : retourne le corps JSON
        tel quel, sans juger de son contenu.

        Le jeton se transmet en paramètre `accessToken`. Mesuré sur matériel :
        `access_token` et l'en-tête `Authorization: Bearer` sont tous deux refusés.
        Le POST ne sert qu'aux lectures que l'API documente ainsi
        (`status.cellularModule.temperature`, avec `connId`) — jamais à écrire.
        """
        query = dict(params or {})
        if self._token:
            query["accessToken"] = self._token
        url = f"{self._base}/api/{endpoint}"
        if corps is None:
            resp = await self._http.get(url, params=query or None)
        else:
            resp = await self._http.post(url, params=query or None, json=corps)

        if resp.status_code in (401, 403):
            # Certains firmwares refusent au niveau HTTP plutôt que dans le corps :
            # on ramène les deux cas à la même forme pour n'avoir qu'un test ensuite.
            return {"stat": "fail", "code": resp.status_code, "message": "Unauthorized"}

        self._raise_for_status(resp, f"/api/{endpoint}")
        try:
            return resp.json()
        except ValueError as exc:
            raise RouterApiError(
                f"Routeur {self.router_id} : réponse non-JSON sur /api/{endpoint}"
            ) from exc

    async def _get(
        self, endpoint: str, params: dict | None = None, corps: dict | None = None,
    ) -> dict:
        """GET authentifié, avec une relance unique si la session a expiré.

        Le firmware répond **HTTP 200** même quand il refuse la requête : l'échec est
        dans le corps (`{"stat":"fail","code":401,"message":"Unauthorized"}`, observé
        sur MBX). Sans inspecter ce corps, une session expirée passait inaperçue et
        `parse_wan_connection` renvoyait une liste vide : les WAN disparaissaient de
        la supervision en silence, sans reconnexion ni trace dans les journaux.
        """
        # Indexé par l'endpoint ET son `infoType` (C-6) : `status.pepvpn` sert
        # trois lectures — peer, profile, tunnel —, et un refus de `profile`
        # condamnait les deux autres, donc toute la supervision du bond.
        cle = endpoint if not params or "infoType" not in params else f"{endpoint}?infoType={params['infoType']}"
        refuse_a = self._hors_perimetre.get(cle)
        if refuse_a is not None and time.monotonic() - refuse_a >= _HORS_PERIMETRE_REESSAI_S:
            del self._hors_perimetre[cle]
            refuse_a = None
        if refuse_a is not None:
            raise EndpointRefusedError(
                f"Routeur {self.router_id} : /api/{endpoint} hors du périmètre de ces identifiants",
                code=401,
            )
        generation = await self._ensure_authenticated(None)

        payload = await self._request(endpoint, params, corps)

        if _is_auth_failure(payload):
            await self._ensure_authenticated(generation)
            payload = await self._request(endpoint, params, corps)

        if payload.get("stat") == "fail":
            # Un refus qui survit à une ré-authentification n'est pas une session
            # expirée : ce sont les DROITS. Le firmware le dit de deux façons :
            # 403, la session est valide mais cet endpoint est hors du périmètre
            # du client API — mesuré sur MAX HD4 MBX avec un client
            # `api.read-only`, qui lit les WAN et se voit refuser
            # `info.capability` ; 401 malgré un login qui vient de réussir,
            # observé au rejeu sur `info.capability` avec un compte restreint.
            # Classer ce second cas en `AuthenticationError` ré-authentifiait à
            # chaque lecture et laissait la fiche « provisoire » pour toujours —
            # sept logins pour trois lectures, reproduit à l'audit, sur un
            # firmware qui suspend l'accès cinq minutes au bout de cinq échecs.
            # Le code voyage avec l'erreur, pour que l'appelant sache si le
            # refus condamne le relevé ou un seul détail.
            code = _to_int(payload.get("code"))
            message = (
                f"Routeur {self.router_id} : /api/{endpoint} refusé "
                f"(code {payload.get('code')} — {payload.get('message') or 'sans message'})"
            )
            if _is_auth_failure(payload) and endpoint not in _JAMAIS_HORS_PERIMETRE:
                # Retenu : le redemander coûterait un login par cycle. Les
                # identifiants mauvais, eux, ne passent pas le login et sont
                # dits par `CredentialsRefusedError` avant d'arriver ici.
                logger.info(
                    "Routeur %s : /api/%s hors du périmètre de ces identifiants (code %s)",
                    self.router_id, endpoint, payload.get("code"),
                )
                self._hors_perimetre[cle] = time.monotonic()
            raise EndpointRefusedError(message, code=code)
        return payload

    # --- lectures ---
    async def get_router_events(self, noms: dict[str, str]) -> list[RouterEvent]:
        """Les faits reconnus du journal du routeur, datés par son horloge.

        Complément, comme le débit et le tunnel : un échec ne touche à rien, et
        un refus explicite (droits, endpoint absent) ne se redemande pas.
        `status.log` n'est pas documenté — c'est l'endpoint de l'interface web.
        """
        if not self._supports_log:
            return []
        try:
            horloge = horloge_routeur(await self._get("status.system.info"))
            journal = await self._get("status.log")
        except AuthenticationError as exc:
            if isinstance(exc, CredentialsRefusedError) or exc.code != _FORBIDDEN_CODE:
                raise
            self._supports_log = False
            return []
        except EndpointRefusedError as exc:
            logger.info("Journal du routeur non proposé par %s (%s)", self.router_id, exc)
            self._supports_log = False
            return []
        except (RouterApiError, httpx.HTTPError) as exc:
            logger.debug("Journal du routeur illisible sur %s : %s", self.router_id, exc)
            return []
        try:
            return parse_router_log(journal, horloge, noms)
        except Exception:
            logger.warning("Journal du routeur inattendu sur %s", self.router_id, exc_info=True)
            return []

    async def get_radio_status(self, echeance: float | None = None) -> list[WanRadioStatus]:
        """Le relevé WAN, puis ses compléments tant que l'`echeance` le permet.

        `echeance` est un instant de l'horloge de la boucle (`loop.time()`).
        Les compléments « ne doivent jamais détruire les états WAN déjà lus » —
        or le budget de l'équipement les couvrait tous : `status.traffic` qui
        expire à 15 s puis une latence à 6 s faisaient perdre un relevé WAN lu
        en 0,4 s, et l'équipement était dit « pas de réponse en 20 s » (C-5).
        Passé l'échéance, les compléments restants sont abandonnés pour ce
        cycle et les états WAN rendus tels quels.
        """
        brut = await self._get("status.wan.connection")
        statuses = parse_wan_connection(brut)
        # Ce que le routeur déclare désactivé : le poller en a besoin pour dire
        # « désactivé » plutôt qu'« absent du relevé » d'un WAN qui s'en va.
        self.wans_desactives = parse_disabled_wans(brut)
        for complement in self._complements(statuses):
            fait, _ = await avant_echeance(echeance, complement())
            if not fait:
                logger.info(
                    "Routeur %s : compléments abandonnés pour ce cycle, faute de temps "
                    "— les états WAN sont publiés", self.router_id,
                )
                break
        return statuses

    def _complements(self, statuses: list[WanRadioStatus]) -> tuple:
        """Les lectures qui suivent le relevé WAN, dans l'ordre, chacune une
        fonction sans argument qui rend une coroutine. Point d'extension : un
        client qui n'en veut qu'une partie (le témoin Ethernet) la redéfinit."""
        return (
            lambda: self._attach_traffic(statuses),
            lambda: self._attach_path_latency(statuses),
            lambda: self._attach_tunnel(statuses),
            lambda: self._attach_modem_temperature(statuses),
            self._lire_clients,
            self._lire_qualite,
        )

    async def _attach_path_latency(self, statuses: list[WanRadioStatus]) -> None:
        """La latence du CHEMIN, une fois par minute.

        Espacée, parce que la charge utile porte l'heure écoulée : rien ne se
        perd à ne pas la relire à chaque tour. Et les cycles intermédiaires
        laissent le champ à `None` plutôt que de recopier la dernière valeur —
        une mesure vieille d'une minute n'est pas une mesure de maintenant, et
        ce projet préfère un trou à un chiffre qui se répète.

        Complément, comme le débit et le tunnel : rien de ce qui s'y passe ne
        doit détruire des états WAN déjà lus.
        """
        if not self._supports_wan_latency:
            return
        maintenant = time.monotonic()
        if (
            self._wan_latency_at is not None
            and maintenant - self._wan_latency_at < _WAN_LATENCY_INTERVAL_S
        ):
            return

        try:
            brut = await self._get("status.wan.latency")
        except AuthenticationError as exc:
            # Même règle qu'ailleurs : le TYPE, et non le numéro. Un refus
            # d'identifiants condamne le relevé entier et doit remonter ; un 403
            # ne dit que « cet endpoint est hors des droits de ce client API ».
            if isinstance(exc, CredentialsRefusedError) or exc.code != _FORBIDDEN_CODE:
                raise
            self._supports_wan_latency = False
            logger.info(
                "Latence par WAN hors des droits du client API sur %s (%s) : plus de tentative",
                self.router_id, exc,
            )
            return
        except EndpointRefusedError as exc:
            # Refus explicite du firmware : ce modèle ne propose pas l'endpoint.
            # C'est le seul cas qui autorise à le condamner.
            self._supports_wan_latency = False
            logger.info(
                "Latence par WAN non proposée par %s (%s) : plus de tentative",
                self.router_id, exc,
            )
            return
        except (RouterApiError, httpx.HTTPError) as exc:
            # Panne passagère : on retentera au prochain intervalle. Ne PAS
            # marquer l'instant, sinon une coupure de trois secondes coûterait
            # une minute de mesure.
            logger.debug("Latence par WAN illisible sur %s : %s", self.router_id, exc)
            return

        self._wan_latency_at = maintenant
        try:
            # L'heure entière, que la même charge utile porte : le poller
            # l'écrit dans `router_history` puis l'efface (R-3.2).
            self.serie_latence = parse_latency_series(brut, utcnow())
            apply_wan_latency(statuses, parse_wan_latency(brut))
        except Exception:
            logger.warning(
                "Latence par WAN illisible sur %s : relevé conservé sans elle",
                self.router_id, exc_info=True,
            )

    async def _attach_tunnel(self, statuses: list[WanRadioStatus]) -> None:
        """Complète les états avec la vue du tunnel SpeedFusion.

        C'est le tunnel qui porte le flux : un lien peut être connecté côté WAN
        et écarté du bond, et les grandeurs qui font geler une image — latence,
        perte, désordre — n'existent qu'ici.

        Cet endpoint est un COMPLÉMENT : rien de ce qui s'y passe ne doit
        détruire des états WAN déjà lus. Seul un refus d'identifiants remonte,
        parce qu'il condamne de toute façon le reste du relevé.
        """
        # L'ancienneté du bond ne vaut que pour CE cycle, et seule une lecture
        # de tunnel connecté la réécrit. Gardée d'un cycle à l'autre, elle
        # affichait « Monté depuis 2 h 03 » sous « Tunnel tombé » (C-7).
        self.tunnel_uptime_s = None
        if not self._supports_tunnel:
            return
        if self._tunnel_skip > 0:
            # Sonde espacée : le DERNIER cycle n'a déclaré aucun pair. Le
            # commentaire disait « n'a jamais déclaré le moindre pair », ce qui
            # décrivait une propriété permanente de l'équipement ; l'espacement
            # s'arme en fait à chaque cycle qui rend une liste vide, et se lève
            # au premier pair revu. C'est un tunnel au repos, pas un châssis
            # sans SpeedFusion.
            self._tunnel_skip -= 1
            return

        try:
            brut = await self._get("status.pepvpn", {"infoType": "peer"})
            peers = parse_peers(brut)
            # Le seuil que le ROUTEUR applique lui-même pour écarter un lien du
            # bond. Il arrive dans une charge utile déjà téléchargée.
            self._latency_cutoff = parse_latency_cutoff(brut)
            # Ce que le tunnel achemine : sans cette liste, un débit nul dans le
            # bond ne se distingue pas d'une panne.
            self.bond_routes = self._retenir_routes(brut)
        except AuthenticationError as exc:
            # Le TYPE, et non le numéro. `RouterApiError.code` porte deux
            # choses selon le chemin : le statut HTTP quand `_raise_for_status`
            # lève, le code du firmware quand `_get` lève. Un refus
            # d'identifiants sur `/api/login` arrive donc avec `code = 403`,
            # indistinguable d'un refus de DROITS sur un endpoint — et éteignait
            # le débit par WAN pour toute la durée du processus. C'est exactement
            # la régression que le commentaire ci-dessus dit empêcher.
            if isinstance(exc, CredentialsRefusedError) or exc.code != _FORBIDDEN_CODE:
                raise
            # Refus de DROITS, pas d'identifiants : la session est bonne, cet
            # endpoint est simplement hors du périmètre du client API. Le laisser
            # remonter jetait des états WAN valides, et `_get` réauthentifiait
            # avant chaque relevé — un POST /api/login PAR CYCLE, sur un firmware
            # qui suspend l'accès cinq minutes au bout de cinq échecs :
            # l'application pouvait se verrouiller elle-même hors de l'équipement.
            self._disable_tunnel("droits insuffisants", exc)
            return
        except EndpointRefusedError as exc:
            # Refus explicite du firmware au PREMIER appel : ce modèle ne propose
            # pas l'endpoint. C'est le seul cas qui autorise à le condamner.
            self._disable_tunnel("endpoint refusé", exc)
            return
        except (RouterApiError, httpx.HTTPError) as exc:
            # Panne de transport ou réponse illisible : passager par nature. La
            # condamnation était irréversible pour la vie du client, une coupure
            # de quelques secondes suffisait donc à éteindre le tunnel jusqu'au
            # redémarrage du processus.
            logger.debug("Lecture des pairs impossible sur %s : %s", self.router_id, exc)
            return

        if not peers:
            self.bond_profiles = []
            # Aucun profil SpeedFusion configuré. `tunnel` reste à None, que
            # l'interface lit comme INCONNU et non comme « hors du bond » : le
            # vert est ici légitime. L'endpoint répond `stat: ok`, on ne peut donc
            # pas le condamner — un profil peut être créé après le démarrage —,
            # seulement espacer la sonde.
            self._forget_tunnel_counters()
            self._tunnel_skip = _TUNNEL_IDLE_PROBE_CYCLES
            self.tunnel_absent = True
            return
        self.tunnel_absent = False
        await self._lire_profils()

        connected = [(pid, name) for pid, name, up in peers if up]
        if not connected:
            # Des profils existent, mais aucun pair n'est joignable : le bond est
            # TOMBÉ, ce qui est bien plus grave qu'un lien écarté. Sans objet
            # tunnel sur les statuts, l'interface n'avait rien à compter comme
            # hors du bond et affichait « Liaison OK » en vert, concentrateur
            # injoignable — alors qu'UN SEUL lien écarté la fait passer en
            # « dégradée ». La sentinelle rend le bond tombé visible.
            self._forget_tunnel_counters()
            mot = peer_state(brut)
            for status in statuses:
                status.tunnel = TunnelLink(state=PEER_DOWN, peer_state=mot)
            return

        # Un seul tunnel porte le flux dans le cas courant. S'il y en a
        # plusieurs, le premier connecté fait foi — et on le dit.
        peer_id, peer_name = connected[0]
        try:
            payload = await self._get(
                "status.pepvpn", {"infoType": "tunnel", "tunnelOption": peer_id}
            )
        except AuthenticationError as exc:
            # Le TYPE, et non le numéro. `RouterApiError.code` porte deux
            # choses selon le chemin : le statut HTTP quand `_raise_for_status`
            # lève, le code du firmware quand `_get` lève. Un refus
            # d'identifiants sur `/api/login` arrive donc avec `code = 403`,
            # indistinguable d'un refus de DROITS sur un endpoint — et éteignait
            # le débit par WAN pour toute la durée du processus. C'est exactement
            # la régression que le commentaire ci-dessus dit empêcher.
            if isinstance(exc, CredentialsRefusedError) or exc.code != _FORBIDDEN_CODE:
                raise
            logger.debug("Détail du tunnel refusé sur %s : %s", self.router_id, exc)
            return
        except (RouterApiError, httpx.HTTPError) as exc:
            # Ce second appel porte sur un pair qui peut disparaître entre les
            # deux requêtes : son échec ne dit rien de la présence de l'endpoint
            # et ne doit jamais condamner la sonde.
            logger.debug("Détail du tunnel illisible sur %s : %s", self.router_id, exc)
            return

        # L'ANALYSE AUSSI est sous filet. `_corps` ne protège que le premier
        # niveau : si `response.tunnel` n'est pas un objet, `parse_tunnel` lève,
        # l'exception traverse cette méthode — dont le contrat promet que « rien
        # de ce qui s'y passe ne doit détruire des états WAN déjà lus » — et le
        # poller étiquette « injoignable » un équipement qui vient de répondre.
        # C'est la confusion de diagnostic qui envoie vérifier un raccordement
        # quand il faut lire une charge utile.
        try:
            tunnel = parse_tunnel(payload, peer_id, peer_name)
            rates = self._tunnel_rates(tunnel_counters(payload, peer_id), peer_id)
        except Exception as exc:
            logger.warning(
                "Routeur %s : détail du tunnel illisible (%s) — les états WAN sont conservés",
                self.router_id, type(exc).__name__,
            )
            return

        self.tunnel_uptime_s = tunnel.uptime_s

        for status in statuses:
            link = tunnel.links.get(status.wan_id)
            if link is None:
                continue
            measured = rates.get(status.wan_id, _LinkRates())
            link.loss_percent_up = measured.loss_up
            link.loss_percent_down = measured.loss_down
            link.out_of_order_percent_up = measured.out_of_order_up
            link.out_of_order_percent_down = measured.out_of_order_down
            # Le dénominateur accompagne les taux jusqu'à l'écran. Sans lui,
            # l'interface n'a aucun moyen de refuser un « 0,00 % de perte » compté
            # sur les trois paquets de service d'un tunnel au repos.
            link.packets_up = measured.packets_up
            link.packets_down = measured.packets_down
            # Débit RÉELLEMENT porté par le bond sur ce lien, par sens. À ne pas
            # confondre avec `WanRadioStatus.tx_kbps`/`rx_kbps`, qui viennent de
            # `status.traffic` et comptent toute l'interface WAN — tunnel et hors
            # tunnel mêlés. Celui-ci ne compte que ce qui passe par l'agrégation.
            link.tx_kbps = measured.tx_kbps
            link.rx_kbps = measured.rx_kbps
            # Redondance : paquets de correction émis, paquets reconstitués reçus.
            link.repair_sent = measured.repair_sent
            link.repair_used = measured.repair_used
            # Les champs de synthèse retiennent le PIRE des deux sens. Non parce
            # qu'un sens compterait plus que l'autre — l'équipement fournit de la
            # connectivité, et l'usage décide lequel importe — mais parce qu'une
            # moyenne laisse le sens le plus chargé masquer la dégradation de
            # l'autre : mesuré, 5 000 paquets perdus sur 100 000 émis ressortaient
            # à 0,50 % une fois noyés dans 900 000 paquets reçus sans perte.
            link.loss_percent = _worst(measured.loss_up, measured.loss_down)
            link.out_of_order_percent = _worst(
                measured.out_of_order_up, measured.out_of_order_down
            )
            status.tunnel = link

        # Le seuil de latence du bond, appliqué. C'est le seul endroit où toutes
        # les latences d'un même instant coexistent : le comparer lien par lien
        # côté écran obligerait à recalculer un minimum que le serveur a déjà.
        self._apply_latency_cutoff(statuses)

    def _apply_latency_cutoff(self, statuses: list[WanRadioStatus]) -> None:
        """Marque les liens que le routeur a cessé d'employer, faute de latence.

        « Le trafic est arrêté pour les liens qui dépassent de cette valeur celui
        de latence la plus basse » — et rien dans l'état du tunnel ne le dit :
        sur les 113 relevés de la base qui franchissent le seuil, `state` vaut
        `ACTIVE` cent fois sur cent. L'écran annonçait donc qu'ils portaient le
        flux, alors que les intervalles sans un seul paquet montant y sont quatre
        fois et demie plus fréquents et que le débit porté n'y dépasse jamais
        zéro.

        On ne touche PAS à `in_bond` : c'est ce que le routeur déclare, et le
        remplacer par une déduction ferait dire à l'écran une chose que
        l'équipement ne dit pas. Le dépassement est un fait de plus, nommé.
        """
        seuil = self._latency_cutoff
        if seuil is None:
            return
        # Les liens DEBOUT et dans le bond, seuls : un lien déconnecté dont
        # l'état de tunnel traîne à ACTIVE fixait le plus bas temps de transit,
        # et écartait les autres d'un seuil mesuré sur un lien qui ne porte rien.
        latences = [
            s.tunnel.rtt_ms
            for s in statuses
            if s.connected and s.tunnel is not None and s.tunnel.in_bond
            and s.tunnel.rtt_ms is not None
        ]
        # Un seul lien dans le bond n'a personne à qui se comparer : le seuil est
        # un ÉCART, pas une valeur absolue.
        if len(latences) < 2:
            return
        plus_bas = min(latences)
        for s in statuses:
            if s.tunnel is None or s.tunnel.rtt_ms is None:
                continue
            s.tunnel.cutoff_ms = seuil
            s.tunnel.beyond_cutoff = s.tunnel.rtt_ms - plus_bas > seuil

    async def _attach_modem_temperature(self, statuses: list[WanRadioStatus]) -> None:
        """La température de chaque modem cellulaire, une fois par minute.

        `status.cellularModule.temperature` se lit en POST pour un `connId`. Le
        firmware REFUSE un WAN non cellulaire (« Unsupported request », 404,
        mesuré sur le Dome) : seuls les cellulaires sont demandés, et un refus
        ne retire que le WAN concerné. Entre deux lectures, la dernière mesure
        est reportée AVEC son heure, 90 s au plus. Complément : rien de ce qui
        s'y passe ne détruit les états WAN déjà lus.
        """
        if not self._supports_temperature:
            return
        maintenant = time.monotonic()
        if self._temperature_at is None or maintenant - self._temperature_at >= _TEMPERATURE_INTERVAL_S:
            lu = False
            for status in statuses:
                if status.kind != "cellular" or status.wan_id in self._temperature_refusee:
                    continue
                connid = _to_int(status.wan_id)
                if connid is None:
                    continue
                try:
                    brut = await self._get("status.cellularModule.temperature", corps={"connId": connid})
                except AuthenticationError as exc:
                    if isinstance(exc, CredentialsRefusedError) or exc.code != _FORBIDDEN_CODE:
                        raise
                    self._supports_temperature = False
                    logger.info("Température des modems hors des droits sur %s (%s) : plus de tentative",
                                self.router_id, exc)
                    return
                except EndpointRefusedError as exc:
                    self._temperature_refusee.add(status.wan_id)
                    logger.info("Température du modem du WAN %s refusée par %s (%s) : plus de tentative",
                                status.wan_id, self.router_id, exc)
                    continue
                except (RouterApiError, httpx.HTTPError) as exc:
                    logger.debug("Température du modem du WAN %s illisible sur %s : %s",
                                 status.wan_id, self.router_id, exc)
                    continue
                # Une réponse LUE compte pour la minute, qu'elle porte une
                # valeur ou non : sans cela, un firmware qui répond sans
                # température était redemandé à chaque cycle (C-30).
                lu = True
                valeur = parse_modem_temperature(brut)
                if valeur is not None:
                    self._temperatures[status.wan_id] = (valeur, utcnow())
            if lu:
                self._temperature_at = maintenant

        instant = utcnow()
        for status in statuses:
            memo = self._temperatures.get(status.wan_id)
            if memo is not None and (instant - memo[1]).total_seconds() <= _TEMPERATURE_MAX_S:
                status.modem_temperature_c, status.modem_temperature_at = memo

    async def _lire_qualite(self) -> None:
        """L'heure de radio du routeur, tout de suite puis toutes les 10 min.
        Complément : rien de ce qui s'y passe ne touche aux états WAN lus."""
        if not self._supports_qualite:
            return
        maintenant = time.monotonic()
        if self._qualite_at is not None and maintenant - self._qualite_at < _QUALITE_INTERVAL_S:
            return
        try:
            brut = await self._get("status.wan.quality")
        except AuthenticationError as exc:
            if isinstance(exc, CredentialsRefusedError) or exc.code != _FORBIDDEN_CODE:
                raise
            self._supports_qualite = False
            logger.info("Historique radio hors des droits sur %s (%s) : plus de tentative",
                        self.router_id, exc)
            return
        except EndpointRefusedError as exc:
            self._supports_qualite = False
            logger.info("Historique radio non proposé par %s (%s) : plus de tentative",
                        self.router_id, exc)
            return
        except (RouterApiError, httpx.HTTPError) as exc:
            logger.debug("Historique radio illisible sur %s : %s", self.router_id, exc)
            return
        self._qualite_at = maintenant
        try:
            self.serie_qualite = parse_quality_series(brut, utcnow())
        except Exception:
            logger.warning("Historique radio inattendu sur %s", self.router_id, exc_info=True)

    async def _lire_clients(self) -> None:
        """Les clients actifs du LAN, une fois par minute. Complément : rien de
        ce qui s'y passe ne doit détruire les états WAN déjà lus."""
        if not self._supports_clients:
            return
        maintenant = time.monotonic()
        # Deux intervalles sans lecture réussie : le dernier décompte ne dit
        # plus l'état du LAN, il s'efface plutôt que de rester figé (C-33).
        if self._clients_at is not None and maintenant - self._clients_at >= 2 * _CLIENTS_INTERVAL_S:
            self.lan_clients = None
        if self._clients_at is not None and maintenant - self._clients_at < _CLIENTS_INTERVAL_S:
            return
        try:
            brut = await self._get("status.client")
        except AuthenticationError as exc:
            if isinstance(exc, CredentialsRefusedError) or exc.code != _FORBIDDEN_CODE:
                raise
            self._supports_clients = False
            logger.info("Clients du LAN hors des droits sur %s (%s) : plus de tentative",
                        self.router_id, exc)
            return
        except EndpointRefusedError as exc:
            self._supports_clients = False
            logger.info("Clients du LAN non proposés par %s (%s) : plus de tentative",
                        self.router_id, exc)
            return
        except (RouterApiError, httpx.HTTPError) as exc:
            logger.debug("Clients du LAN illisibles sur %s : %s", self.router_id, exc)
            return
        self._clients_at = maintenant
        self.lan_clients = parse_lan_clients(brut, ip_locale_vers(self._base))
        if self.lan_clients is not None:
            self.lan_clients.read_at = utcnow()

    async def _lire_profils(self) -> None:
        """Les profils SpeedFusion, une fois par minute. Complément : rien de
        ce qui s'y passe ne doit détruire l'état du bond déjà lu."""
        if not self._supports_profils:
            return
        maintenant = time.monotonic()
        if self._profils_at is not None and maintenant - self._profils_at < _PROFIL_INTERVAL_S:
            return
        try:
            brut = await self._get("status.pepvpn", {"infoType": "profile"})
        except AuthenticationError as exc:
            if isinstance(exc, CredentialsRefusedError) or exc.code != _FORBIDDEN_CODE:
                raise
            self._supports_profils = False
            logger.info("Profils SpeedFusion hors des droits sur %s (%s) : plus de tentative",
                        self.router_id, exc)
            return
        except EndpointRefusedError as exc:
            self._supports_profils = False
            logger.info("Profils SpeedFusion non proposés par %s (%s) : plus de tentative",
                        self.router_id, exc)
            return
        except (RouterApiError, httpx.HTTPError) as exc:
            # Passager : on retentera au prochain cycle, sans marquer l'instant.
            logger.debug("Profils SpeedFusion illisibles sur %s : %s", self.router_id, exc)
            return
        self._profils_at = maintenant
        self.bond_profiles = parse_bond_profiles(brut)

    def _disable_tunnel(self, reason: str, exc: Exception) -> None:
        """Condamne la sonde `status.pepvpn` et le dit une fois."""
        logger.info(
            "Tunnel SpeedFusion indisponible sur %s (%s : %s) : plus de tentative",
            self.router_id, reason, exc,
        )
        self._supports_tunnel = False
        self._forget_tunnel_counters()

    def _forget_tunnel_counters(self) -> None:
        """Oublie la référence : la prochaine lecture sera une première lecture."""
        self._tunnel_counters = {}
        self._tunnel_counters_at = None
        self._tunnel_peer = None

    def _tunnel_rates(
        self,
        counters: dict[str, tuple[_Counters | None, _Counters | None]],
        peer_id: str,
    ) -> dict[str, _LinkRates]:
        """Taux par lien et par sens, depuis le relevé précédent du MÊME tunnel.

        Les compteurs sont cumulés par tunnel depuis son établissement : d'un
        pair à l'autre, la soustraction porterait sur deux grandeurs sans
        rapport. Avec deux profils montés, la bascule du « premier pair
        connecté » passait le test de monotonie et publiait un taux inventé, écrit
        tel quel en base. On repart donc de zéro dès que le pair change.

        Le premier relevé n'a pas de référence et ne produit aucun taux — mieux
        vaut ne rien dire que d'annoncer la moyenne depuis six heures.
        """
        if peer_id != self._tunnel_peer:
            self._forget_tunnel_counters()
            self._tunnel_peer = peer_id

        maintenant = time.monotonic()
        # Temps réellement écoulé depuis la lecture précédente, et non la période
        # de consigne : le cycle dure ce qu'il dure, et un débit calculé sur une
        # durée supposée serait faux de la dérive du cycle — mesurée à 2,1 s sur
        # une période de 5,3 s, soit 40 % d'erreur.
        elapsed = (
            maintenant - self._tunnel_counters_at
            if self._tunnel_counters_at is not None
            else None
        )

        # Au-delà du trou maximal, la référence ne borne plus un instant mais une
        # absence. `_attach_tunnel` n'est pas appelée quand l'équipement est
        # injoignable, alors que les compteurs et leur horodatage survivent : au
        # retour, la différence couvrait tout le trou. Mesuré sur treize minutes
        # d'absence — le plus long incident de la base : 0,99 % de perte ACCUMULÉE
        # republiée comme la perte de l'instant du retour, avec 787 800 paquets au
        # dénominateur, donc franchissant haut la main la règle des 200 paquets et
        # affichée. Trente fois la valeur réelle, et c'est chiffre pour chiffre le
        # défaut que le calcul par différence était censé supprimer. On repart
        # donc d'une première lecture : ne rien dire, plutôt qu'une moyenne sur
        # une durée pendant laquelle on ne regardait pas.
        if elapsed is not None and elapsed > _ECART_MAX_S:
            logger.info(
                "Routeur %s : %.0f s sans lecture du tunnel, compteurs repris à zéro",
                self.router_id, elapsed,
            )
            self._forget_tunnel_counters()
            self._tunnel_peer = peer_id
            elapsed = None

        rates: dict[str, _LinkRates] = {}
        for wan_id, (up, down) in counters.items():
            previous_up, previous_down = self._tunnel_counters.get(wan_id, (None, None))
            loss_up, ooo_up, packets_up, tx_kbps, repair_sent = _direction_rates(
                up, previous_up, elapsed
            )
            loss_down, ooo_down, packets_down, rx_kbps, repair_used = _direction_rates(
                down, previous_down, elapsed
            )
            rates[wan_id] = _LinkRates(
                loss_up, loss_down, ooo_up, ooo_down,
                packets_up, packets_down,
                tx_kbps, rx_kbps,
                repair_sent, repair_used,
            )

        # Remplacement complet, et non mise à jour : un lien absent des compteurs
        # de ce cycle disparaît de la référence, si bien que son retour est
        # traité comme une première lecture.
        self._tunnel_counters = counters
        self._tunnel_counters_at = maintenant
        return rates

    async def _attach_traffic(self, statuses: list[WanRadioStatus]) -> None:
        """Complète les états avec le débit par WAN, si l'équipement le fournit.

        `status.traffic` est un endpoint distinct de `status.wan.connection`. Un
        refus applicatif (stat=fail) signifie que ce modèle ne le propose pas :
        on cesse alors de le demander à chaque cycle. Une erreur réseau, elle,
        est passagère et ne doit pas condamner la fonctionnalité.
        """
        if not self._supports_traffic:
            return
        try:
            brut = await self._get("status.traffic")
            # L'ANALYSE sous son propre filet, comme pour le tunnel. Le
            # `try` ci-dessous n'attrape que `RouterApiError` et `httpx.HTTPError`
            # — les fautes du TRANSPORT. Une charge utile inattendue fait lever
            # `parse_traffic` d'un `TypeError` qui traverse tout, remonte au
            # poller, et l'équipement est étiqueté « injoignable » alors qu'il
            # vient de répondre. C'est la confusion de diagnostic que `_corps` et
            # le filet de `parse_tunnel` disent combattre, laissée ouverte ici.
            try:
                apply_traffic(statuses, parse_traffic(brut))
                # Le SECOND bloc de la même réponse : zéro requête de plus.
                apply_usage(statuses, parse_usage(brut))
            except Exception:
                logger.warning(
                    "Débit par WAN illisible sur %s : relevé conservé sans débit",
                    self.router_id, exc_info=True,
                )
        except AuthenticationError as exc:
            # `AuthenticationError` hérite de `RouterApiError` : sans ce cas
            # placé AVANT, un refus d'identifiants passager éteindrait le débit
            # par WAN pour toute la durée du processus. On la laisse remonter,
            # le poller sait la traiter.
            #
            # Un 403 est autre chose : les identifiants sont bons, seuls les
            # droits du client API ne couvrent pas cet endpoint. Le faire remonter
            # jetterait un relevé WAN valide et ferait réauthentifier à chaque
            # cycle, alors que la réponse ne changera jamais.
            # Le TYPE, et non le numéro. `RouterApiError.code` porte deux
            # choses selon le chemin : le statut HTTP quand `_raise_for_status`
            # lève, le code du firmware quand `_get` lève. Un refus
            # d'identifiants sur `/api/login` arrive donc avec `code = 403`,
            # indistinguable d'un refus de DROITS sur un endpoint — et éteignait
            # le débit par WAN pour toute la durée du processus. C'est exactement
            # la régression que le commentaire ci-dessus dit empêcher.
            if isinstance(exc, CredentialsRefusedError) or exc.code != _FORBIDDEN_CODE:
                raise
            logger.info(
                "Débit par WAN hors des droits du client API sur %s (%s) : plus de tentative",
                self.router_id, exc,
            )
            self._supports_traffic = False
        except EndpointRefusedError as exc:
            logger.info(
                "Débit par WAN indisponible sur %s (%s) : plus de tentative", self.router_id, exc
            )
            self._supports_traffic = False
        except (RouterApiError, httpx.HTTPError) as exc:
            # Transport en défaut ou réponse illisible : passager, on retentera.
            logger.debug("Lecture du débit impossible sur %s : %s", self.router_id, exc)

    async def identify(self) -> DeviceInfo | None:
        """Identité de l'équipement, en laissant remonter les erreurs.

        Utilisée par l'appairage, qui doit distinguer « identifiants refusés »
        de « injoignable » pour dire à l'utilisateur quoi corriger. La
        supervision, elle, passe par `get_device_info` : une identité manquante
        n'y est pas une raison d'arrêter de superviser.
        """
        avant = utcnow()
        brut = await self._get("status.system.info")
        apres = utcnow()
        info = parse_system_info(brut)
        if info is not None:
            # L'écart d'horloge, rapporté au milieu de l'aller-retour (R-3.9).
            horaire = _corps(brut).get("systemTime")
            instant = _to_float(horaire.get("timestamp")) if isinstance(horaire, dict) else None
            if instant is not None:
                milieu = avant.timestamp() + (apres - avant).total_seconds() / 2
                info.clock_offset_s = round(instant - milieu, 1)
        return info

    async def get_device_info(self) -> DeviceInfo | None:
        """Identité de l'équipement, ou None s'il ne la publie pas."""
        try:
            return await self.identify()
        except (httpx.HTTPError, RouterApiError) as exc:
            logger.info("Identité indisponible sur %s : %s", self.router_id, exc)
            return None

    async def get_capabilities(self) -> DeviceCapabilities | None:
        """Capacités de l'équipement, déclarées si possible, sondées sinon."""
        try:
            declared = await self._get("info.capability")
            # `status.port` dit quels ports WAN sont basculés en LAN ; son absence
            # ne remet pas en cause l'inventaire, elle le rend seulement moins précis.
            try:
                port_payload = await self._get("status.port")
                ports = parse_ports(port_payload)
                lan_networks = parse_lan_networks(port_payload)
            except Exception:
                # `Exception`, et non les seules fautes de TRANSPORT : l'analyse
                # d'une charge utile inattendue lève un `TypeError` qui traversait
                # ce `except` et faisait retomber tout `get_capabilities` sur le
                # sondage — donc une fiche dégradée pour un routeur qui avait
                # pourtant bien répondu à `info.capability`.
                ports = {}
                lan_networks = []
            caps = parse_capability(declared, ports, lan_networks)
        except EndpointRefusedError as exc:
            # Refus explicite : `info.capability` n'est pas couvert par le scope
            # `api.read-only`, et il ne le sera pas davantage au prochain tour.
            logger.info(
                "Capacités déclarées hors du périmètre sur %s (%s) : reconstitution par sondage",
                self.router_id, exc,
            )
            caps = await self._probe_capabilities()
        except (httpx.HTTPError, RouterApiError) as exc:
            # Panne passagère : le sondage donne une fiche utilisable tout de
            # suite, mais elle reste à confirmer — `info.capability` déclare tout
            # le châssis là où le sondage ne voit que les WAN actifs.
            logger.info(
                "Capacités déclarées illisibles sur %s (%s) : reconstitution par sondage",
                self.router_id, exc,
            )
            caps = await self._probe_capabilities()
            caps.provisional = True
        caps.per_wan_throughput = self._supports_traffic
        return caps

    def _retenir_routes(self, brut: dict) -> list[str]:
        """Les routes du bond, gardées en mémoire tant que le pair est déclaré.

        Un pair TOMBÉ publie `route: []` (fixture HD2, `status: START`) : le
        pair ne les déclare que connecté. Recopiée telle quelle, la liste vide
        faisait dire à l'écran, à l'instant précis où le concentrateur tombe,
        que le rôle du bond est inconnu — et « l'agrégation ne protège plus »
        d'un trafic qui, sur des routes dédiées, n'a jamais emprunté le tunnel.
        Ce que le pair achemine ne change pas parce qu'il est injoignable : le
        dernier rôle connu tient tant qu'un pair reste déclaré. Plus aucun pair
        — profil retiré — et la mémoire s'efface avec lui.
        """
        routes = parse_bond_routes(brut)
        if routes:
            return routes
        return list(self.bond_routes) if parse_peers(brut) else []

    async def _responds(self, endpoint: str) -> bool | None:
        """L'équipement sert-il cet endpoint ? Sert de test de présence matérielle.

        Trois réponses et non deux. `True` : il répond. `False` : le firmware a
        explicitement refusé, ce qui est la seule forme de refus qui autorise à
        condamner une fonctionnalité — l'équipement a répondu, et sa réponse dit
        non. `None` : on n'a pas pu conclure. Un délai dépassé, un transport en
        défaut, une session momentanément illisible ne disent RIEN du matériel, et
        les confondre avec un refus gravait `gps=False` pour la vie du processus
        sur un routeur qui finissait simplement de démarrer.
        """
        try:
            await self._get(endpoint)
            return True
        except EndpointRefusedError:
            return False
        except (httpx.HTTPError, RouterApiError) as exc:
            logger.debug(
                "Sonde de %s sans conclusion sur %s : %s", endpoint, self.router_id, exc
            )
            return None

    async def _probe_capabilities(self) -> DeviceCapabilities:
        """Profil reconstitué à partir des endpoints réellement accessibles.

        `info.capability` n'est PAS couvert par le scope `api.read-only` — mesuré
        sur MAX HD4 MBX : il répond `Unauthorized` avec un jeton de client API,
        alors que tout le reste passe. Un outil de terrain doit donc savoir s'en
        passer.

        Limite assumée : l'inventaire ne contient alors que les WAN **actifs**,
        là où `info.capability` déclare tout le châssis. C'est pourquoi le profil
        porte sa provenance dans `source`.
        """
        caps = DeviceCapabilities(source="probed")
        try:
            statuses = parse_wan_connection(await self._get("status.wan.connection"))
            caps.wan_types = {s.wan_id: s.kind for s in statuses}
        except (httpx.HTTPError, RouterApiError) as exc:
            logger.debug("Inventaire des WAN illisible sur %s : %s", self.router_id, exc)

        # `info.location` répond dès que le module GPS existe, même sans point fixé :
        # c'est un test de présence matérielle, pas de disponibilité de position.
        #
        # Sans conclusion, on reste OPTIMISTE : croire la capacité présente coûte
        # une requête par cycle qui rendra None, tandis que la nier éteint la
        # fonctionnalité en silence et pour toujours. Le drapeau `provisional`
        # fait rejouer la sonde jusqu'à ce qu'elle tranche.
        gps = await self._responds("info.location")
        pepvpn = await self._responds("status.pepvpn")
        caps.gps = gps is not False
        caps.pepvpn = pepvpn is not False
        caps.provisional = gps is None or pepvpn is None
        return caps

    async def get_location(self) -> GnssFix | None:
        """Position GPS du routeur, si le module GPS a un point fixé."""
        try:
            payload = await self._get("info.location")
        except (httpx.HTTPError, RouterApiError) as exc:
            logger.debug("GPS indisponible sur %s : %s", self.router_id, exc)
            return None
        return parse_location(payload)

    async def aclose(self) -> None:
        await self._http.aclose()


def parse_wan_connection(payload: dict) -> list[WanRadioStatus]:
    """Traduit une réponse `status.wan.connection` en états WAN radio.

    Retient les WAN cellulaires, Wi-Fi **et** filaires. Un lien Wi-Fi amont
    (Starlink, hotspot) comme un lien filaire (fibre, xDSL, réseau du site)
    participe à la liaison agrégée, et le filaire en porte souvent l'essentiel du
    débit : l'ignorer ferait dire à la supervision que tout va bien alors que le
    lien principal est tombé.

    Sont écartés : les WAN désactivés, et les types sans rapport — modem USB,
    wovlan. La docstring affirmait pourtant que « les WAN ethernet [...] sont
    ignorés », alors que `_WIRED_WAN_TYPES` les retient vingt lignes plus bas,
    et que c'est par un WAN filaire que passe le lien 1 du matériel de référence.
    """
    response = _corps(payload)
    statuses: list[WanRadioStatus] = []

    for wan_id, wan in response.items():
        # `_NON_WAN_KEYS` — un jeu de clés scalaires de la réponse — vivait ici
        # en tête de condition. Il ne pouvait rien écarter : toutes ces clés
        # portent un scalaire ou une liste, donc le `not isinstance(wan, dict)`
        # de la même ligne les écartait déjà, et le jeu devait être tenu à jour
        # à chaque champ que le firmware ajoute. Une garde structurelle vaut
        # mieux qu'un inventaire.
        if not isinstance(wan, dict):
            continue
        wan_type = wan.get("type")
        if wan_type not in _RADIO_WAN_TYPES | _WIRED_WAN_TYPES:
            continue
        if wan.get("enable") is False:
            continue

        if wan_type == "wifi":
            statuses.append(_parse_wifi_wan(str(wan_id), wan))
            continue

        if wan_type in _WIRED_WAN_TYPES:
            statuses.append(_parse_wired_wan(str(wan_id), wan))
            continue

        cellular = _bloc(wan, "cellular") or _bloc(wan, "gobi")
        emplacements = _parse_sim_slots(cellular)
        bands = _parse_bands(cellular)
        # Le Telit LM960 du MAX HD4 MBX remonte `pci: 0` sur TOUTES les porteuses :
        # c'est une absence de mesure, pas une cellule de PCI 0. Un PCI 0 réel
        # existe, mais jamais simultanément sur toutes les porteuses agrégées.
        #
        # Et pas davantage sur une porteuse UNIQUE. Une version exigeait
        # plusieurs porteuses pour conclure, au nom du modem sans agrégation
        # qui camperait réellement sur la cellule de PCI 0 : mesuré sur le MAX
        # HD2 (Quectel EC25), les deux WAN — Bouygues et Orange, deux cellules
        # — publient une porteuse à `pci: 0`. Deux cellules d'opérateurs
        # différents au même PCI, c'est une chance sur 504² ; un modem qui ne
        # le publie pas, c'est ce qu'on a sous les yeux. Le PCI 0 réel tient
        # dès qu'une AUTRE porteuse du même modem en publie un différent.
        if bands and all(b.pci == 0 for b in bands):
            for band in bands:
                band.pci = None
        network = _texte(cellular.get("network"))
        primary = _primary_band(bands, network)
        _effacer_signal_non_mesure(bands, primary)
        cell_tower = _bloc(cellular, "cellTower")
        carrier = _bloc(cellular, "carrier")
        led = str(wan.get("statusLed") or "").lower()
        technology = _technology_label(cellular)
        cell_id = _identite_cellule(cell_tower)
        enb_id, sector_id = _split_eci(
            cell_id, technology,
            ancre_lte=any(_rat_family(b.rat) == "LTE" for b in bands),
        )

        statuses.append(
            WanRadioStatus(
                wan_id=str(wan_id),
                wan_name=_texte(wan.get("name")) or f"WAN {wan_id}",
                connected=led in _LED_UP,
                operator=_texte(carrier.get("name")),
                plmn=_plmn(cellular),
                # Quatre champs de la même charge utile, jusqu'ici jetés.
                roaming_status=_texte(_bloc(cellular, "roamingStatus").get("message")),
                modem_vendor=_texte(cellular.get("manufacturer")),
                modem_model=_texte(cellular.get("model")),
                modem_firmware=_texte(cellular.get("firmware")),
                mtu=_to_int(wan.get("mtu")),
                # Le drapeau du firmware ne dit pas ce que le modèle lui
                # prêtait : le Dome Pro Duo publie `carrierAggregation: false`
                # en mesurant TROIS porteuses (n78 + deux LTE, en NSA), et la
                # fiche disait « Agrégation : inactive » sous trois porteuses
                # mesurées. Plusieurs porteuses mesurées, c'est une agrégation
                # constatée, quoi qu'en dise le drapeau — qui ne garde la
                # parole que sur une porteuse unique.
                carrier_aggregation=(
                    True if len(bands) > 1
                    else bool(cellular["carrierAggregation"])
                    if "carrierAggregation" in cellular
                    else None
                ),
                network_mode=_texte(cellular.get("networkMode")),
                allowance=_parse_allowance(wan.get("bandwidthAllowanceMonitor")),
                rat=technology,
                band=primary.band if primary else None,
                earfcn=primary.channel if primary else None,
                pci=primary.pci if primary else None,
                cell_id=cell_id,
                tac=_to_int(cell_tower.get("tac")),
                enb_id=enb_id,
                sector_id=sector_id,
                rsrp=primary.rsrp if primary else None,
                rsrq=primary.rsrq if primary else None,
                sinr=primary.sinr if primary else None,
                rssi=primary.rssi if primary else None,
                signal_bars=_to_int(cellular.get("signalLevel")),
                bands=bands,
                sim_slots=emplacements,
                ip=_texte(wan.get("ip")),
                priority=_to_int(wan.get("priority")),
                uptime_s=_to_int(wan.get("uptime")),
                status_message=_texte(wan.get("message")),
            )
        )

    return statuses


def _parse_wired_wan(wan_id: str, wan: dict) -> WanRadioStatus:
    """État d'un WAN filaire (fibre, xDSL, réseau du site).

    Aucune grandeur radio n'a de sens ici : ni RSRP, ni bande, ni cellule, donc
    aucune recherche d'antenne. Ne restent que l'état du lien, son adresse, sa
    durée de session — et le débit, ajouté séparément depuis `status.traffic`.
    """
    led = str(wan.get("statusLed") or "").lower()
    return WanRadioStatus(
        kind="ethernet",
        wan_id=wan_id,
        wan_name=_texte(wan.get("name")) or f"WAN {wan_id}",
        connected=led in _LED_UP,
        rat="Ethernet",
        ip=_texte(wan.get("ip")),
        priority=_to_int(wan.get("priority")),
        uptime_s=_to_int(wan.get("uptime")),
        status_message=_texte(wan.get("message")),
    )


def _parse_wifi_wan(wan_id: str, wan: dict) -> WanRadioStatus:
    """Construit l'état d'un WAN Wi-Fi (point d'accès amont).

    Le routeur ne fournit qu'une force de signal et un niveau : pas de RSRP/RSRQ/SINR,
    qui n'ont pas de sens ici. La force est rangée dans `rssi`, grandeur homogène.
    """
    wifi = _bloc(wan, "wifi")
    signal = _bloc(wifi, "signal")
    channel = _to_int(wifi.get("channel"))
    led = str(wan.get("statusLed") or "").lower()
    strength = _to_float(signal.get("strength"))

    return WanRadioStatus(
        kind="wifi",
        wan_id=wan_id,
        wan_name=_texte(wan.get("name")) or f"WAN {wan_id}",
        connected=led in _LED_UP,
        operator=_texte(wifi.get("ssid")),  # le SSID tient lieu de « réseau »
        rat="Wi-Fi",
        # Un lien Wi-Fi amont est un WAN comme un autre : il a une adresse et une
        # place dans la règle sortante. Les deux champs étaient simplement omis
        # ici, si bien que la carte d'un lien Wi-Fi montrait deux cases vides là
        # où celle d'un modem les renseigne — une absence de code lue comme une
        # absence de donnée.
        ip=_texte(wan.get("ip")),
        priority=_to_int(wan.get("priority")),
        band=_wifi_band(channel),
        rssi=strength,
        signal_bars=_to_int(signal.get("level")),
        wifi=WifiLink(
            ssid=_texte(wifi.get("ssid")),
            bssid=_texte(wifi.get("bssid")),
            channel=channel,
            band=_wifi_band(channel),
            signal_dbm=strength,
            signal_level=_to_int(signal.get("level")),
        ),
        uptime_s=_to_int(wan.get("uptime")),
        status_message=_texte(wan.get("message")),
    )


def parse_system_info(payload: dict) -> DeviceInfo | None:
    """Traduit `status.system.info` en identité d'équipement.

    Endpoint **non documenté** dans la référence officielle de l'API, mais présent
    sur les deux matériels validés. Tout est optionnel : le Dome Pro Duo ne remonte
    ni consommation ni état d'alimentation, et rien ne garantit cet endpoint sur
    les gammes Balance / BR1 / Transit.
    """
    device = _corps(payload).get("device")
    if not isinstance(device, dict):
        return None

    corps = _corps(payload)
    cpu = _bloc(corps, "cpuLoad")
    load = _to_float(cpu.get("percentage"))

    # L'ancienneté du châssis, à la source. Elle décide des sessions
    # d'alimentation, et elle était jusqu'ici déduite du maximum des liens.
    depuis = _to_int(_bloc(corps, "uptime").get("second"))

    # `powerUsage` est une liste d'entrées d'alimentation ; on somme les actives.
    watts = None
    for entry in _liste(corps, "powerUsage"):
        if isinstance(entry, dict) and entry.get("active"):
            value = _to_float(entry.get("value"))
            if value is not None:
                watts = (watts or 0.0) + value

    # Une entrée sans `connect` n'est PAS listée : `bool(e.get("connect"))` la
    # déclarait débranchée, et l'écran signalait une alimentation perdue dont
    # le firmware ne disait rien. Le modèle ne connaît que branchée/débranchée ;
    # une source d'état inconnu n'y a pas sa place — la taire vaut mieux que
    # l'affirmer coupée.
    sources = [
        PowerSource(name=_texte(e.get("name")) or "?", connected=bool(e["connect"]))
        for e in _liste(corps, "powerState")
        if isinstance(e, dict) and "connect" in e
    ]

    # Le premier capteur thermique. Absent du Dome Pro Duo : `None` y vaut
    # « ce modèle n'en publie pas », et non « zéro degré ».
    temperature = next(
        (_to_float(c.get("temperature")) for c in _liste(corps, "thermalSensor")
         if isinstance(c, dict)),
        None,
    )

    # La CHARGE de l'alimentation, en pourcentage de son budget. La tension et le
    # courant restent dehors — voir `DeviceInfo.power_load_percent`.
    charge = next(
        (_to_float(e.get("percentage"))
         for e in _liste(corps, "powerUsage")
         if isinstance(e, dict) and e.get("active")),
        None,
    )

    plan = _bloc(_bloc(corps, "subscription"), "carePlan")
    horaire = _bloc(corps, "systemTime")
    activation = _bloc(corps, "configActivation")
    assistance = _bloc(corps, "remoteAssistance")

    mac_lan = next(
        (_texte(e.get("mac")) for e in _liste(corps, "macInfo")
         if isinstance(e, dict) and e.get("interfaceType") == "lan"),
        None,
    )

    return DeviceInfo(
        model=_texte(device.get("model")),
        product_code=_texte(device.get("productCode")),
        serial=_texte(device.get("serialNumber")),
        firmware=_texte(device.get("firmwareVersion")),
        hardware_revision=_texte(device.get("hardwareRevision")),
        temperature_c=temperature,
        power_load_percent=charge,
        care_plan=_texte(plan.get("name")),
        care_plan_active=_drapeau(plan, "active"),
        pepvpn_version=_texte(device.get("pepvpnVersion")),
        timezone=_texte(horaire.get("timezone")),
        # Bloc ou clé absents : on ne sait pas. `False` affirmait qu'aucune
        # configuration ne s'appliquait, sur un firmware qui n'en dit rien.
        config_activating=_drapeau(activation, "activating"),
        remote_assistance=_drapeau(assistance, "enable"),
        name=_texte(device.get("name")),
        uptime_s=depuis,
        cpu_load_percent=round(load, 1) if load is not None else None,
        power_watts=watts,
        power_sources=sources,
        mac_lan=mac_lan,
    )


# Unités de volume déclarées par le routeur, en mégaoctets. Même posture que
# `_KBPS_FROM` : une unité inconnue ne vaut pas « des mégaoctets », elle vaut
# rien du tout — publier un plafond faux est pire que ne pas en publier.
_MB_FROM = {"kb": 1 / 1024, "mb": 1.0, "gb": 1024.0, "tb": 1024.0 * 1024.0}


def _parse_allowance(bloc: object) -> BandwidthAllowance | None:
    """Le plafond de volume d'un WAN ou d'une carte, s'il en porte un.

    Les trois champs de détail ne sont publiés QUE lorsque `enable` est vrai :
    un plafond non configuré rend `None` partout, jamais `0`. Et le bloc entier
    peut manquer — il manque sur le port basculé en LAN du matériel de référence.
    """
    if not isinstance(bloc, dict) or "enable" not in bloc:
        # Sans `enable`, le bloc ne dit pas si un plafond s'applique : c'est
        # un plafond INCONNU, pas un plafond désactivé. `bool(bloc.get(…))`
        # rendait `enabled=False`, et l'écran taisait un plafond peut-être actif.
        return None
    actif = bool(bloc["enable"])
    plafond = bloc.get("monthlyAllowance")
    valeur = None
    if actif and isinstance(plafond, dict):
        unite = str(plafond.get("unit") or "mb").lower()
        brut = _to_float(plafond.get("value"))
        if brut is not None and unite in _MB_FROM:
            valeur = round(brut * _MB_FROM[unite], 1)
        elif brut is not None:
            logger.warning("Unité de plafond inconnue « %s » : plafond non publié", unite)
    actions = bloc.get("action")
    return BandwidthAllowance(
        enabled=actif,
        monthly_allowance_mb=valeur,
        reset_day=_to_int(bloc.get("start")) if actif else None,
        actions=[t for t in (_texte(a) for a in actions) if t] if isinstance(actions, list) else [],
    )


def _parse_sim_slots(cellular: dict) -> list[SimSlot]:
    """Les emplacements SIM d'un modem : présence d'une carte, et APN.

    Le châssis en déclare huit — deux par modem sur ce matériel — et rien ne
    disait s'ils étaient occupés. Vérifié en direct : les huit le sont, donc
    chaque modem a une carte de secours, ce que l'écran n'apprenait nulle part.

    Ni IMSI ni ICCID : le routeur les publie à côté, ils n'apprennent rien à la
    supervision, et ils rendraient toute capture d'écran impartageable. C'est la
    même raison qui fait anonymiser les fixtures.

    L'emplacement ACTIF est publié — `sim.<n>.active`, sur les deux matériels de
    référence et sur les deux emplacements. Cette docstring a longtemps affirmé
    le contraire pour justifier de ne pas le lire : l'exploitant ne savait pas
    laquelle de ses deux cartes travaillait, au nom d'une observation fausse.

    L'ordre d'affichage suit `sim.order`, que le routeur publie. Le tri
    lexicographique qui le remplaçait placerait « 10 » avant « 2 » sur un
    châssis à plus de neuf emplacements.
    """
    slots = cellular.get("sim")
    if not isinstance(slots, dict):
        return []
    ordre = slots.get("order")
    rang = (
        {str(n): i for i, n in enumerate(ordre)}
        if isinstance(ordre, list)
        else {}
    )
    out: list[SimSlot] = []
    for nom, bloc in sorted(
        slots.items(), key=lambda kv: (rang.get(str(kv[0]), len(rang)), str(kv[0]))
    ):
        if not isinstance(bloc, dict):
            continue
        out.append(
            SimSlot(
                slot=str(nom),
                card_detected=_drapeau(bloc, "simCardDetected"),
                apn=_texte(bloc.get("apn")),
                active=_drapeau(bloc, "active"),
                allowance=_parse_allowance(bloc.get("bandwidthAllowanceMonitor")),
            )
        )
    return out


def parse_ports(payload: dict) -> dict[str, bool]:
    """`status.port` : identifiant de port physique -> le port est-il basculé en LAN ?

    Un châssis expose des ports Ethernet commutables. Celui qui porte `asLan: true`
    sert de port LAN et n'est plus un WAN, alors qu'il reste listé comme tel dans
    l'inventaire déclaré. Sans cette lecture, le décompte annoncé dépasse celui
    que voit l'exploitant.
    """
    wan_ports = _bloc(_corps(payload), "wan")
    return {
        str(port_id): bool(entry.get("asLan"))
        for port_id, entry in wan_ports.items()
        if isinstance(entry, dict)
    }


def parse_lan_networks(payload: dict) -> list[LanNetwork]:
    """`status.port` : les réseaux LAN que le châssis nomme, VLAN natif compris.

    Sous `lan.reference.vlanIdInfo`, indexés par un rang interne qui n'est PAS
    le numéro de VLAN — celui-ci est `id`, et 0 est le réseau natif. Le MAX HD2
    du 19 septembre en publie deux : « RESEAU-DEMO_EXT1 » (0) et « WIFI Range »
    (1) ; le MBX un seul, « local ». Rien d'autre n'est lu ici : les adresses
    sont dans `config.lan`.
    """
    reference = _bloc(_bloc(_corps(payload), "lan"), "reference")
    reseaux: list[LanNetwork] = []
    for entry in _bloc(reference, "vlanIdInfo").values():
        if not isinstance(entry, dict):
            continue
        vlan_id = _to_int(entry.get("id"))
        nom = entry.get("name")
        if vlan_id is None or not isinstance(nom, str) or not nom:
            continue
        reseaux.append(LanNetwork(vlan_id=vlan_id, name=nom))
    return sorted(reseaux, key=lambda r: r.vlan_id)


def parse_capability(
    payload: dict,
    ports: dict[str, bool] | None = None,
    lan_networks: list[LanNetwork] | None = None,
) -> DeviceCapabilities:
    """Traduit `info.capability` : ce que l'équipement déclare savoir faire.

    `featureSupplementary.wan.connection` énumère TOUS les WAN du châssis avec
    leur type, y compris ceux qui sont désactivés — ce que `status.wan.connection`
    ne montre pas. C'est l'inventaire matériel, indépendant de la configuration.
    """
    response = _corps(payload)
    feature = _bloc(response, "feature")
    supplementary = _bloc(response, "featureSupplementary")

    as_lan = ports or {}
    wan_types: dict[str, str] = {}
    lan_repurposed: list[str] = []
    usb_slots = sim_slots = 0

    # La licence SpeedFusion Cloud. Trois variantes coexistent sous le même
    # bloc — `local`, `subscription`, `fup` — et c'est `local` qui porte la
    # licence de cet équipement. Le niveau et les plafonds de profils sont un
    # cran AU-DESSUS, sous `pepvpn` : ils décrivent le PepVPN en général, pas la
    # licence Cloud.
    pepvpn_bloc = _bloc(supplementary, "pepvpn")
    nuage = _bloc(_bloc(pepvpn_bloc, "speedfusionCloud"), "local")
    plafond_debit = _bloc(nuage, "throughputLimit")
    debit = _to_float(plafond_debit.get("value"))
    if debit is not None and str(plafond_debit.get("unit") or "mbps").lower() != "mbps":
        logger.warning(
            "Unité de plafond SpeedFusion inconnue « %s » : plafond non publié",
            plafond_debit.get("unit"),
        )
        debit = None

    # Échéance (secondes UTC) et quota en Mo. Une autre unité n'est pas
    # convertie à l'aveugle : le quota n'est alors pas publié.
    horodatage = _to_float(nuage.get("expiryTimestamp"))
    echeance = (
        datetime.fromtimestamp(horodatage, tz=timezone.utc) if horodatage and horodatage > 0 else None
    )
    quota = nuage.get("quota") if isinstance(nuage.get("quota"), dict) else {}
    reste = total = None
    if quota and str(quota.get("unit") or "").upper() == "MB":
        reste, total = _to_float(quota.get("remaining")), _to_float(quota.get("limit"))
    elif quota:
        logger.warning("Unité de quota SpeedFusion inconnue « %s » : quota non publié",
                       quota.get("unit"))

    connections = _bloc(_bloc(supplementary, "wan"), "connection")
    for wan_id, entry in connections.items():
        if not isinstance(entry, dict) or not entry.get("type"):
            continue
        wan_id = str(wan_id)
        kind = str(entry["type"])

        if kind == "modem":
            usb_slots += 1  # logement d'accessoire, pas un lien du châssis
            continue
        if kind == "ethernet" and as_lan.get(str(entry.get("portId", wan_id))):
            lan_repurposed.append(wan_id)
            continue
        if kind == "cellular":
            sim_slots += 2 if entry.get("dualSim") else 1

        wan_types[wan_id] = kind

    return DeviceCapabilities(
        gps=bool(feature.get("gps")),
        pepvpn=bool(feature.get("pepvpn")),
        wan_types=wan_types,
        lan_repurposed=sorted(lan_repurposed, key=lambda w: (len(w), w)),
        lan_networks=list(lan_networks or []),
        usb_modem_slots=usb_slots,
        sim_slots=sim_slots,
        sfc_licensed=bool(nuage["licensed"]) if "licensed" in nuage else None,
        sfc_suspended=bool(nuage["suspended"]) if "suspended" in nuage else None,
        sfc_throughput_limit_mbps=debit,
        pepvpn_max_active_profiles=_to_int(pepvpn_bloc.get("maxActiveProfile")),
        sfc_expires_at=echeance,
        sfc_quota_remaining_mb=reste,
        sfc_quota_limit_mb=total,
    )


def parse_traffic(payload: dict) -> dict[str, tuple[float | None, float | None]]:
    """Débit instantané par WAN, en kbit/s : identifiant -> (descendant, montant).

    `status.traffic.bandwidth` porte une entrée par WAN, aux mêmes identifiants
    que `status.wan.connection` — la jointure est directe. L'unité est déclarée
    par le routeur (kbps sur les matériels validés) et convertie si besoin.
    """
    bandwidth = _bloc(_corps(payload), "bandwidth")
    # Une unité INCONNUE ne vaut pas kbit/s. Le repli à 1.0 publiait « 10 » pour
    # dix mégabits déclarés en « Mibps » — un débit faux, cent fois trop bas, et
    # présenté comme une mesure. Ne rien rendre laisse `apply_traffic` mettre les
    # deux sens à `None`, et l'écran sait dire une absence.
    unite = str(bandwidth.get("unit") or "kbps").lower()
    if unite not in _KBPS_FROM:
        logger.warning(
            "Unité de débit inconnue « %s » : débit par WAN non publié pour ce cycle", unite
        )
        return {}
    scale = _KBPS_FROM[unite]

    rates: dict[str, tuple[float | None, float | None]] = {}
    for wan_id, entry in bandwidth.items():
        if not isinstance(entry, dict):
            continue
        overall = entry.get("overall")
        if not isinstance(overall, dict):
            continue
        down = _to_float(overall.get("download"))
        up = _to_float(overall.get("upload"))
        rates[str(wan_id)] = (
            round(down * scale, 1) if down is not None else None,
            round(up * scale, 1) if up is not None else None,
        )
    return rates


def parse_usage(payload: dict) -> dict[str, tuple[float | None, float | None]]:
    """Volumes de la période en cours, par WAN, en mégaoctets.

    `status.traffic` porte DEUX blocs et le parseur n'en lisait qu'un.
    `bandwidth` donne le débit instantané ; `traffic` donne les volumes cumulés,
    aux mêmes identifiants — et c'est lui qui, rapporté au plafond de
    `bandwidthAllowanceMonitor`, dit « 2 532 sur 5 120 Mo ». Pour un véhicule de
    reportage, c'est le premier risque d'exploitation après la couverture.

    « Période en cours » et non « ce mois-ci » : la réponse ne porte AUCUN
    marqueur de période. Ni date de début, ni numéro de mois — son seul
    horodatage est celui du relevé. Que le compteur s'aligne sur le mois
    calendaire ou sur le jour de remise à zéro du plafond reste à établir par un
    relevé à cheval sur un premier du mois, et l'écran ne nommera pas la fenêtre
    avant. Nommer une période qu'on n'a pas vérifiée serait exactement le genre
    d'affirmation que ce projet refuse.

    Le cumul `lifetime` n'est pas lu : il est agrégé sous une unique clé `all`,
    donc châssis et non WAN, et il court depuis février 2022 — 828 To sur ce
    matériel. Il ne borne aucune exploitation.
    """
    traffic = _bloc(_corps(payload), "traffic")
    unite = str(traffic.get("unit") or "mb").lower()
    if unite not in _MB_FROM:
        logger.warning(
            "Unité de volume inconnue « %s » : volumes par WAN non publiés", unite
        )
        return {}
    echelle = _MB_FROM[unite]

    volumes: dict[str, tuple[float | None, float | None]] = {}
    for wan_id, entree in traffic.items():
        if not isinstance(entree, dict):
            continue
        overall = entree.get("overall")
        if not isinstance(overall, dict):
            continue
        bas = _to_float(overall.get("download"))
        haut = _to_float(overall.get("upload"))
        volumes[str(wan_id)] = (
            round(bas * echelle, 1) if bas is not None else None,
            round(haut * echelle, 1) if haut is not None else None,
        )
    return volumes


# Les points de la série de latence qui datent de la dernière minute — le
# temps qui sépare deux lectures : 6 points à 10 s.
_POINTS_LATENCE_RECENTS = 6


def parse_wan_latency(payload: dict) -> dict[str, float]:
    """Latence du CHEMIN, par WAN, en millisecondes — la plus récente.

    C'est la grandeur qui manquait le plus. Jusqu'ici la latence n'existait que
    dans le tunnel : un lien hors du bond n'en avait aucune, un châssis sans
    profil SpeedFusion non plus, et le lien FILAIRE — qui porte souvent
    l'essentiel du débit — n'en avait jamais eu.

    Le routeur ne rend pas un instant mais une SÉRIE : 360 points espacés de dix
    secondes, soit l'heure écoulée, pour 8,5 Ko. On n'en retient que le dernier.
    Garder toute la série demanderait des lignes de relevé partielles — une par
    point, avec tout le reste à `None` —, et la table dit « un WAN à un instant »,
    pas « une grandeur à un instant ». La série reste disponible pour qui voudra
    rattraper un trou de supervision ; c'est un autre chantier.

    Un WAN sans mesure porte un bloc `latency` SANS clé `data` : c'est ainsi que
    le firmware dit « rien ici », et c'est ce qui distingue un port désactivé
    d'un port à zéro milliseconde.
    """
    mesures: dict[str, float] = {}
    for wan_id, bloc in _corps(payload).items():
        if not isinstance(bloc, dict):
            continue
        serie = bloc.get("latency")
        points = serie.get("data") if isinstance(serie, dict) else None
        if not isinstance(points, list):
            continue
        # Le dernier point RENSEIGNÉ, parmi ceux de la dernière minute. Le
        # commentaire promettait déjà qu'un trou en fin de série — le lien vient
        # de se rétablir — ne ferait pas remonter une mesure vieille d'une
        # heure ; la boucle, elle, remontait jusqu'au premier point renseigné,
        # si loin soit-il, et le poller le datait de l'instant de lecture (C-10).
        for brut in reversed(points[-_POINTS_LATENCE_RECENTS:]):
            valeur = _to_float(brut)
            if valeur is not None:
                mesures[str(wan_id)] = valeur
                break
    return mesures


# Écart toléré entre la fin d'une série et l'heure du poste à la lecture. Le
# dernier point date de moins de 10 s sur le Dome ; au-delà d'une minute,
# l'horloge du routeur ne se juge pas d'ici, et la série est écartée (R-3.2).
_HORLOGE_SERIE_S = 60.0


def parse_latency_series(
    payload: dict, maintenant: datetime,
) -> dict[str, list[tuple[datetime, float | None]]]:
    """La série ENTIÈRE de `status.wan.latency`, datée point par point.

    Un point / 10 s sur l'heure écoulée, daté par l'horloge du routeur
    (`pointStart` + rang × `pointInterval`). Un point non mesuré reste une
    ABSENCE explicite : il marque un trou dans la courbe au lieu de la laisser
    se refermer. Une série écartée plutôt que décalée : si sa fin s'éloigne de
    plus d'une minute de l'heure du poste, ou si son nombre de points ne
    correspond pas à son intervalle.
    """
    series: dict[str, list[tuple[datetime, float | None]]] = {}
    for wan_id, bloc in _corps(payload).items():
        serie = bloc.get("latency") if isinstance(bloc, dict) else None
        points = serie.get("data") if isinstance(serie, dict) else None
        if not isinstance(points, list) or not points:
            continue
        debut, pas, fin = (_to_float(serie.get(k)) for k in ("pointStart", "pointInterval", "pointEnd"))
        if debut is None or fin is None or not pas or pas <= 0:
            continue
        if abs(debut + pas * (len(points) - 1) - fin) > pas / 2:
            continue
        if abs(maintenant.timestamp() - fin) > _HORLOGE_SERIE_S:
            logger.info("Série de latence du WAN %s écartée : horloge du routeur à %.0f s du poste",
                        wan_id, maintenant.timestamp() - fin)
            continue
        series[str(wan_id)] = [
            (_sur_la_grille(debut + pas * i, pas), _to_float(v))
            for i, v in enumerate(points)
        ]
    return series


def _sur_la_grille(instant: float, pas: float) -> datetime:
    """Un point de série calé sur la grille de son intervalle.

    Le début de la série glisse d'une seconde d'une lecture à l'autre (vu sur le
    Dome : …:00, puis …:01) : sans ce calage, relire l'heure l'écrivait sous des
    horodatages neufs, et la base doublait ses points. Calés, une relecture
    retombe sur les mêmes instants — et la radio sur ceux de la latence.

    `floor(x + 0,5)` et non `round()`, qui arrondit les demies au PAIR : une
    série commençant à …5 s voyait deux points sur trois instants fondus en un,
    la moitié de l'heure perdue par l'upsert (C-11).
    """
    return datetime.fromtimestamp(math.floor(instant / pas + 0.5) * pas, tz=timezone.utc)


def parse_quality_series(payload: dict, maintenant: datetime) -> dict[str, list[tuple[datetime, dict]]]:
    """L'heure de RADIO de `status.wan.quality`, datée point par point (R-3.1).

    Même forme que la latence : un point / 10 s, daté par l'horloge du routeur,
    et les mêmes raisons d'écarter une série — horloge à plus d'une minute du
    poste, ou une grandeur dont le nombre de points ne colle pas à l'intervalle.
    Un point non mesuré reste une absence ; une porteuse vide n'est pas une
    techno. Seuls RSRP, RSRQ, SINR et porteuse sont retenus.
    """
    series: dict[str, list[tuple[datetime, dict]]] = {}
    for wan_id, bloc in _corps(payload).items():
        lq = bloc.get("linkQuality") if isinstance(bloc, dict) else None
        if not isinstance(lq, dict) or not isinstance(lq.get("rsrp"), list) or not lq["rsrp"]:
            continue
        debut, pas, fin = (_to_float(lq.get(k)) for k in ("pointStart", "pointInterval", "pointEnd"))
        if debut is None or fin is None or not pas or pas <= 0:
            continue
        colonnes = {k: lq.get(k) for k in ("rsrp", "rsrq", "sinr", "bearer")}
        n = len(colonnes["rsrp"])
        if any(not isinstance(v, list) or len(v) != n for v in colonnes.values()):
            continue
        if abs(debut + pas * (n - 1) - fin) > pas / 2:
            continue
        if abs(maintenant.timestamp() - fin) > _HORLOGE_SERIE_S:
            logger.info("Série de radio du WAN %s écartée : horloge du routeur à %.0f s du poste",
                        wan_id, maintenant.timestamp() - fin)
            continue
        series[str(wan_id)] = [
            (
                _sur_la_grille(debut + pas * i, pas),
                {
                    "rsrp": _to_float(colonnes["rsrp"][i]),
                    "rsrq": _to_float(colonnes["rsrq"][i]),
                    "sinr": _to_float(colonnes["sinr"][i]),
                    "rat": _texte(colonnes["bearer"][i]),
                },
            )
            for i in range(n)
        ]
    return series


def apply_wan_latency(statuses: list[WanRadioStatus], mesures: dict[str, float]) -> None:
    """Reporte la latence de chemin sur les états WAN, en place."""
    for status in statuses:
        status.path_latency_ms = mesures.get(status.wan_id)


def apply_usage(
    statuses: list[WanRadioStatus], volumes: dict[str, tuple[float | None, float | None]]
) -> None:
    """Reporte les volumes de période sur les états WAN, en place."""
    for status in statuses:
        bas, haut = volumes.get(status.wan_id, (None, None))
        status.period_rx_mb = bas
        status.period_tx_mb = haut


def apply_traffic(
    statuses: list[WanRadioStatus], rates: dict[str, tuple[float | None, float | None]]
) -> None:
    """Reporte les débits sur les états WAN correspondants, en place."""
    for status in statuses:
        down, up = rates.get(status.wan_id, (None, None))
        status.rx_kbps = down
        status.tx_kbps = up


_MOIS = {m: i for i, m in enumerate(
    ("Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"), start=1)}
_HORLOGE_RE = re.compile(r"^\w{3} (?P<mois>\w{3}) +(?P<jour>\d{1,2}) (?P<heure>\d{2}):(?P<min>\d{2}):(?P<sec>\d{2}) \S+ (?P<annee>\d{4})$")
_LIGNE_RE = re.compile(r"^(?P<mois>\w{3}) +(?P<jour>\d{1,2}) (?P<heure>\d{2}):(?P<min>\d{2}):(?P<sec>\d{2}) (?P<cat>[^:]+): (?P<texte>.*)$")
_WAN_COUPE = re.compile(r"^(?P<nom>[^()]+?) \(Priority[^)]*\) disconnected(?: \((?P<cause>[^)]*)\))?(?: in SIM slot (?P<slot>\w+))?$")
_WAN_RETOUR = re.compile(r"^(?P<nom>[^()]+?) \(Priority[^)]*\) connected to (?P<op>[^()]+?) \([^)]*\)(?: in SIM slot (?P<slot>\w+))?$")
_WAN_BASCULE = re.compile(r"^(?P<nom>[^()]+?) detected SIM available, switching to SIM (?P<slot>\w+)")
_WAN_CONFIG = re.compile(r"^(?P<nom>[^()]+?) changes applied$")
_ADMIN = re.compile(r"^\S+ \([^)]*\) login (?P<res>successful|failed)$")


def horloge_routeur(system_info: dict) -> tuple[datetime, datetime] | None:
    """L'heure LOCALE affichée par le routeur et l'instant UTC, lus ensemble.

    `status.system.info` publie les deux (« Thu Sep 24 18:10:00 CEST 2026 » et
    un horodatage) : leur écart convertit l'heure locale du journal en UTC sans
    base de fuseaux horaires, et reste juste quel que soit le fuseau réglé.
    """
    horaire = _bloc(_corps(system_info), "systemTime")
    if not isinstance(horaire, dict):
        return None
    m = _HORLOGE_RE.match(str(horaire.get("string") or "").strip())
    horodatage = _to_int(horaire.get("timestamp"))
    if not m or horodatage is None or m["mois"] not in _MOIS:
        return None
    local = datetime(int(m["annee"]), _MOIS[m["mois"]], int(m["jour"]),
                     int(m["heure"]), int(m["min"]), int(m["sec"]))
    return local, datetime.fromtimestamp(horodatage, tz=timezone.utc)


def parse_router_log(
    payload: dict, horloge: tuple[datetime, datetime] | None, noms: dict[str, str],
) -> list[RouterEvent]:
    """Les faits RECONNUS du journal du routeur — liste fermée, rien de brut.

    `status.log` n'est pas documenté : c'est l'endpoint de l'interface web du
    routeur, et son format peut changer d'un firmware à l'autre. Toute ligne
    qui ne correspond pas exactement à une forme connue est ignorée ; un nom de
    WAN qui ne correspond à aucun WAN publié aussi — l'outil ne devine pas.
    `noms` : nom de WAN tel que `status.wan.connection` le publie → identifiant.
    """
    if horloge is None:
        return []
    corps = _corps(payload)
    lignes = corps.get("log") if isinstance(corps, dict) else None
    if not isinstance(lignes, list):
        return []
    local_ref, utc_ref = horloge
    decalage = local_ref - utc_ref.replace(tzinfo=None)
    faits: list[RouterEvent] = []
    for ligne in lignes:
        if not isinstance(ligne, str):
            continue
        m = _LIGNE_RE.match(ligne.strip())
        if not m or m["mois"] not in _MOIS:
            continue
        # Pas d'année dans le journal : celle de l'horloge, sauf pour une ligne
        # qui serait alors dans le futur — elle date de l'an passé.
        local = datetime(local_ref.year, _MOIS[m["mois"]], int(m["jour"]),
                         int(m["heure"]), int(m["min"]), int(m["sec"]))
        if local > local_ref + timedelta(days=1):
            local = local.replace(year=local.year - 1)
        ts = (local - decalage).replace(tzinfo=timezone.utc)
        empreinte = hashlib.sha256(ligne.strip().encode("utf-8")).hexdigest()[:24]
        cat, texte = m["cat"].strip(), m["texte"].strip()
        fait: tuple[str, str | None, str | None] | None = None
        if cat == "WAN":
            if texte.startswith("Priority changed"):
                fait = ("priorités modifiées", None, None)
            elif (x := _WAN_COUPE.match(texte)) and x["nom"] in noms:
                fait = ("coupure", noms[x["nom"]], x["cause"])
            elif (x := _WAN_RETOUR.match(texte)) and x["nom"] in noms:
                detail = x["op"].strip() + (f" · SIM {x['slot']}" if x["slot"] else "")
                fait = ("retour", noms[x["nom"]], detail)
            elif (x := _WAN_BASCULE.match(texte)) and x["nom"] in noms:
                fait = ("bascule de SIM", noms[x["nom"]], f"vers la SIM {x['slot']}")
            elif (x := _WAN_CONFIG.match(texte)) and x["nom"] in noms:
                fait = ("configuration modifiée", noms[x["nom"]], None)
        elif cat == "System" and texte.startswith("Started up"):
            fait = ("routeur démarré", None, None)
        elif cat == "Admin" and (x := _ADMIN.match(texte)):
            fait = ("connexion admin " + ("refusée" if x["res"] == "failed" else "réussie"), None, None)
        if fait is not None:
            faits.append(RouterEvent(ts=ts, kind=fait[0], wan_id=fait[1], detail=fait[2],
                                     empreinte=empreinte))
    return faits


def parse_disabled_wans(payload: dict) -> set[str]:
    """Les WAN que le routeur déclare désactivés (`enable: false`).

    `parse_wan_connection` les écarte — ils ne mesurent rien — mais leur
    identifiant reste une information : c'est lui qui distingue un lien que
    l'exploitant a désactivé d'un lien qui a disparu.
    """
    corps = _corps(payload)
    return {
        str(wan_id) for wan_id, wan in corps.items()
        if isinstance(wan, dict) and wan.get("enable") is False
    }


def parse_peers(payload: dict) -> list[tuple[str, str | None, bool]]:
    """`status.pepvpn?infoType=peer` : (identifiant, nom, connecté) par pair.

    Un emplacement au `status` VIDE n'est pas un pair : c'est le profil
    SpeedFusion Connect suspendu — `{"peerId": "60000-0", "status": ""}` sur
    le MAX HD4 MBX, sans nom ni identifiant de compte. Le compter comme un
    pair TOMBÉ mettait `PEER_DOWN` sur les quatre SIM d'un châssis sans aucun
    profil PepVPN, et empêchait d'espacer la sonde. Un pair déclaré porte un
    état ; un emplacement vide n'en porte pas.
    """
    peers = _liste(_corps(payload), "peer")
    return [
        (str(p.get("peerId")), _texte(p.get("name")), p.get("status") == "CONNECTED")
        for p in peers
        if isinstance(p, dict) and p.get("peerId") and _texte(p.get("status"))
    ]


def parse_modem_temperature(payload: dict) -> float | None:
    """`{"response": {"temperature": 37}}` → 37.0 °C. Zéro est une mesure ;
    une clé absente ou un nombre malformé n'en est pas une."""
    return _to_float(_corps(payload).get("temperature"))


def ip_locale_vers(base: str) -> str | None:
    """L'adresse locale que le système choisirait pour joindre le routeur.

    Un `connect` UDP n'envoie aucun paquet : il ne fait que choisir la route.
    Seulement vers une IP LITTÉRALE — un nom d'hôte demanderait une résolution
    DNS, bloquante. `None` si rien ne se détermine.
    """
    hote = urlsplit(base if "//" in base else f"//{base}").hostname
    try:
        ipaddress.ip_address(hote or "")
    except ValueError:
        return None
    try:
        with socket.socket(socket.AF_INET6 if ":" in hote else socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.connect((hote, 9))
            return s.getsockname()[0]
    except OSError:
        return None


def parse_lan_clients(payload: dict, ip_du_poste: str | None) -> LanClients | None:
    """`status.client` réduit à des décomptes : actifs, Wi-Fi, filaires.

    Un client inactif (bail encore valide, appareil parti) n'est pas compté.
    Une réponse SANS liste n'est pas « aucun client » : `None`. L'IP de chaque
    client n'est lue que pour la comparer à celle du poste, puis oubliée.
    """
    liste = _corps(payload).get("list")
    if not isinstance(liste, list):
        return None
    actifs = [c for c in liste if isinstance(c, dict) and c.get("active") is True]
    return LanClients(
        active=len(actifs),
        wireless=sum(1 for c in actifs if c.get("connectionType") == "wireless"),
        wired=sum(1 for c in actifs if c.get("connectionType") == "ethernet"),
        includes_this_host=(
            None if ip_du_poste is None else any(c.get("ip") == ip_du_poste for c in actifs)
        ),
    )


def peer_state(payload: dict) -> str | None:
    """Le mot d'état des pairs déclarés, s'ils portent TOUS le même.

    `START` sur le MAX HD2 dont le concentrateur ne répond pas : le routeur
    tente de le joindre. Deux mots différents ne font pas une cause : `None`.
    Un emplacement au status vide n'est pas un pair (voir `parse_peers`).
    """
    mots = {
        str(p["status"]).strip()
        for p in _liste(_corps(payload), "peer")
        if isinstance(p, dict) and p.get("peerId") and str(p.get("status") or "").strip()
    }
    return mots.pop() if len(mots) == 1 else None


def parse_bond_profiles(payload: dict) -> list[BondProfile]:
    """`status.pepvpn?infoType=profile` : les profils, dans l'ordre du routeur.

    Seuls le nom, l'état, le nombre de pairs connectés et le type sont lus —
    jamais `siteId`. Un identifiant listé dans `order` sans bloc est ignoré.
    """
    corps = _corps(payload).get("profile")
    if not isinstance(corps, dict):
        return []
    profils: list[BondProfile] = []
    for pid in _liste(corps, "order"):
        bloc = corps.get(str(pid))
        if not isinstance(bloc, dict):
            continue
        profils.append(BondProfile(
            id=str(pid),
            name=str(bloc["name"]) if bloc.get("name") else None,
            status=(str(bloc.get("status") or "").strip() or None),
            peers_connected=_to_int(bloc.get("peerCount")),
            cloud=str(bloc.get("type") or "") == "SFC Client",
        ))
    return profils


def parse_bond_routes(payload: dict) -> list[str]:
    """Les sous-réseaux acheminés PAR le tunnel, publiés par le pair.

    Lus par personne jusqu'ici, et ils expliquent tout. Mesuré sur ce
    déploiement : le tunnel est monté, ses cinq liens y sont `ACTIVE`, et il n'a
    jamais transporté plus de 2,6 kbit/s — un keepalive — quand les interfaces
    montaient à 262 Mbit/s. Ce n'est pas une panne : aucun profil n'est
    sélectionné dans « Send All Traffic To », et le tunnel ne sert donc qu'aux
    destinations explicitement routées vers le pair.

    Sans cette liste, l'écran ne pouvait que constater un débit nul et le
    présenter comme une anomalie. Avec elle, il dit ce que le bond transporte.
    """
    peers = _liste(_corps(payload), "peer")
    routes: list[str] = []
    for p in peers:
        if not isinstance(p, dict) or p.get("status") != "CONNECTED":
            continue
        for r in _liste(p, "route"):
            if isinstance(r, str) and r and r not in routes:
                routes.append(r)
    return routes


def parse_latency_cutoff(payload: dict) -> float | None:
    """Le seuil de latence au-delà duquel le routeur cesse d'employer un lien.

    Publié par `status.pepvpn?infoType=peer` sous `latencyDiffCutoff`, et lu par
    personne jusqu'ici. C'est pourtant l'un des trois mécanismes — avec la ligne
    de priorité et l'exclusion du profil — qui retirent un lien du bond SANS que
    son état change : mesuré sur ce déploiement, 500 ms.

    Ce que le firmware en fait : « le trafic est arrêté pour les liens qui
    dépassent de cette valeur, en millisecondes, celui de latence la plus
    basse ». Vérifié dans la base : sur les 113 relevés qui franchissent le
    seuil, l'état du tunnel vaut `ACTIVE` **cent fois sur cent** — et sur ces
    mêmes relevés, les intervalles sans un seul paquet montant sont quatre fois
    et demie plus fréquents qu'en dessous (38,7 % contre 8,8 %), le débit porté
    par le bond n'y dépassant jamais zéro.

    Autrement dit : `state: ACTIVE` ne veut pas dire « ce lien porte le flux »,
    et l'écran l'affirmait. Le seuil retenu est le plus PETIT des pairs
    connectés : c'est celui qui s'applique en premier.
    """
    peers = _liste(_corps(payload), "peer")
    seuils = [
        _to_float(p.get("latencyDiffCutoff"))
        for p in peers
        if isinstance(p, dict) and p.get("status") == "CONNECTED"
    ]
    mesures = [s for s in seuils if s is not None and s > 0]
    return min(mesures) if mesures else None


def _counters(block: dict | None) -> _Counters | None:
    """(paquets transportés, perdus, désordonnés) d'un sens, None sans mesure.

    Les compteurs arrivent en listes — une entrée par lien distant du bond —
    quand on lit le niveau WAN, et en scalaires sous `remote`. On somme : avec
    un seul concentrateur, le cas courant, la somme vaut la valeur unique.

    Une absence de bloc ne vaut PAS zéro. Sur la capture réelle, un lien qui
    n'est pas ACTIVE n'a ni `receive` ni `transmit` : il ne porte que
    ['id','name','remote','state']. Mémoriser (0, 0, 0) comme référence faisait
    republier, au retour du lien dans le bond, toute la perte cumulée depuis
    l'établissement du tunnel comme si elle datait des trois dernières secondes
    — mesuré : 0,99 % annoncé pour 0,03 % réel, à chaque micro-coupure.
    """
    packets = block.get("packet") if isinstance(block, dict) else None
    if not isinstance(packets, dict):
        return None

    def somme(source: dict, key: str) -> float:
        value = source.get(key)
        if isinstance(value, list):
            return float(sum(v for v in value if isinstance(v, (int, float))))
        return float(value) if isinstance(value, (int, float)) else 0.0

    # `fec` et `redundant` n'existent qu'à l'émission, `recover` qu'à la
    # réception : le firmware décrit le même mécanisme de chaque bout. Les clés
    # absentes valent zéro, et c'est exact — un sens n'a pas de compteur pour ce
    # que l'autre fait.
    repair = somme(packets, "fec") + somme(packets, "redundant") + somme(packets, "recover")

    return _Counters(
        packets=somme(packets, "wan"),
        loss=somme(packets, "loss"),
        out_of_order=somme(packets, "outOfOrder"),
        byte=somme(block, "byte"),
        repair=repair,
    )


def parse_tunnel(payload: dict, peer_id: str, peer_name: str | None = None) -> TunnelStatus:
    """`status.pepvpn?infoType=tunnel&tunnelOption=<peerId>` : état par lien.

    Les compteurs du routeur sont **cumulés depuis l'établissement du tunnel** :
    ils ne disent rien d'un instant. Le calcul des taux se fait ailleurs, par
    différence entre deux relevés (voir `RouterApiPeplinkClient._tunnel_rates`).
    """
    entry = _bloc(_bloc(_corps(payload), "tunnel"), peer_id)

    links: dict[str, TunnelLink] = {}
    for wan_id, wan in _bloc(entry, "wan").items():
        if not isinstance(wan, dict):
            continue
        links[str(wan_id)] = TunnelLink(
            state=wan.get("state"),
            rtt_ms=_to_float(wan.get("rtt")),
        )

    # L'ancienneté du BOND, au niveau du tunnel et non du lien. Le routeur
    # publie aussi un `time.second` par WAN, mais vérifié sur la capture il y
    # recopie la même valeur sur les cinq liens actifs : le lire comme
    # « ce lien est dans le bond depuis… » serait faux dès qu'un lien rejoint le
    # bond après coup.
    duree = _bloc(_bloc(entry, "overall"), "time").get("second")

    return TunnelStatus(
        peer_id=peer_id,
        peer_name=peer_name,
        connected=any(link.in_bond for link in links.values()),
        links=links,
        uptime_s=_to_int(duree),
    )


def tunnel_counters(
    payload: dict, peer_id: str
) -> dict[str, tuple[_Counters | None, _Counters | None]]:
    """Compteurs cumulés par lien, sens MONTANT puis DESCENDANT.

    Les deux sens restent séparés. Les additionner diluait le seul sens utile
    dégradé : mesuré sur un relevé réel, 5 000 paquets perdus sur
    100 000 émis — 4,76 % dans ce sens-là — noyés dans 900 000
    paquets reçus sans perte donnaient 0,50 % publié.

    Un lien sans aucun compteur est absent du résultat plutôt que ramené à zéro :
    voir `_counters`.
    """
    entry = _bloc(_bloc(_corps(payload), "tunnel"), peer_id)

    counters: dict[str, tuple[_Counters | None, _Counters | None]] = {}
    for wan_id, wan in _bloc(entry, "wan").items():
        if not isinstance(wan, dict):
            continue
        up = _counters(wan.get("transmit"))
        down = _counters(wan.get("receive"))
        if up is None and down is None:
            continue
        counters[str(wan_id)] = (up, down)
    return counters


def parse_location(payload: dict) -> GnssFix | None:
    """Une position `info.location`, et de quoi savoir si elle vaut quelque chose.

    Rendait un simple couple de coordonnées. Le routeur publie quatre champs de
    plus dans la MÊME réponse, déjà lue à chaque cycle : l'âge de la fixation,
    les trois dilutions de précision, l'altitude et la vitesse.

    L'âge est le plus utile : un récepteur qui a perdu le ciel continue de
    republier son dernier point, et rien dans la lecture ne le trahit. Le poller
    avait dû se rabattre sur l'âge de SA propre lecture, ce qui ne mesure que la
    liaison entre lui et le routeur.
    """
    response = _corps(payload)
    if not response.get("gps"):
        return None
    location = _bloc(response, "location")
    lat = _to_float(location.get("latitude"))
    lon = _to_float(location.get("longitude"))
    if lat is None or lon is None:
        return None
    return GnssFix(
        lat=lat,
        lon=lon,
        age_s=_to_int(location.get("timeElapsed")),
        hdop=_to_float(location.get("hdop")),
        speed=_to_float(location.get("speed")),
    )
