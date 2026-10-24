"""Stockage des relevés en SQLite (aiosqlite).

Choix prototype : une table unique indexée sur (router_id, wan_id, ts) — zéro installation,
suffisant pour quelques routeurs scrutés à la seconde. L'accès passe par ce repository, donc
un passage ultérieur à TimescaleDB/InfluxDB ne touche que ce fichier.
"""

from __future__ import annotations

import asyncio
import logging
import math
import os
import secrets
import sqlite3
from contextlib import asynccontextmanager
from pathlib import Path
from datetime import datetime, timedelta

import aiosqlite

from ..identite import FORME_ANCIEN_IDENTIFIANT, ancien_identifiant, identifiant_opaque
from ..models import HistoryPoint, RadioSample, RouterEvent, STATUT_DESACTIVE, nom_du_reseau, utcnow
from ..regles_coupure import (
    sql_coupure, sql_ecart_ms, sql_micro_coupure, sql_voisins, sql_voisins_entre,
)
from ..sessions import _TROU_MAX_S

# Fenêtre pendant laquelle un pic de latence est rapporté au changement de
# cellule qui l'a précédé.
#
# Trente secondes, et c'est un CHOIX mesuré : à cette fenêtre, 15,8 % des pics
# au-dessus de 150 ms suivent un handover, contre 10,2 % à quinze secondes et
# 23,8 % à soixante. Au-delà, le lien de cause devient une coïncidence — le
# taux de pics sans handover est déjà de 21,7 %.
_APRES_BASCULE_S = 30.0

# Les îlots de latence d'`events()`. Au niveau du module, et non dans la
# méthode, pour qu'un test puisse demander son PLAN à SQLite : la
# matérialisation de `bascules` y est un garde de coût, pas une élégance.
_SQL_ILOTS = """
WITH b AS MATERIALIZED (
  -- MATERIALIZED : `b` est lue deux fois (le peloton, puis chaque relevé).
  -- Laissée en co-routine, SQLite la recalculait pour la seconde lecture en
  -- choisissant l'index `(router_id, wan_id, ts)` sur son seul préfixe
  -- `router_id` — donc en parcourant TOUT l'historique de l'équipement quelle
  -- que soit la fenêtre : 10,7 ms pour une minute, un plancher qui croît
  -- avec la base. Matérialisée, la fenêtre entre une fois dans l'index.
  SELECT wan_id, kind, ts, rtt_ms, cutoff_ms, tunnel_state,
         CAST(strftime('%s', ts) / 3 AS INTEGER) AS cycle
  FROM samples
  WHERE router_id = ? AND ts >= ? AND ts <= ? AND rtt_ms IS NOT NULL
), peloton AS (
  SELECT cycle, MIN(rtt_ms) AS bas, COUNT(*) AS n
  FROM b WHERE tunnel_state = 'ACTIVE' GROUP BY cycle
), j AS (
  SELECT b.*,
         (peloton.n >= 2 AND b.cutoff_ms IS NOT NULL
          AND b.rtt_ms - peloton.bas > b.cutoff_ms) AS ecarte,
         (peloton.n >= 2 AND b.cutoff_ms IS NOT NULL
          AND b.rtt_ms - peloton.bas > b.cutoff_ms)
         OR b.rtt_ms >= ? AS haut
  FROM b LEFT JOIN peloton ON peloton.cycle = b.cycle
), e AS (
  -- L'écart au relevé précédent du même lien — au-delà du trou maximal,
  -- personne ne regardait — et ce qu'il était : haut ou non.
  SELECT *,
         (julianday(ts) - julianday(LAG(ts) OVER w)) * 86400.0 AS ecart_s,
         LAG(haut) OVER w AS haut_avant
  FROM j
  WINDOW w AS (PARTITION BY wan_id ORDER BY ts)
), f AS (
  -- Un îlot s'OUVRE sur un relevé haut dont le précédent ne l'était pas, ou
  -- appartenait à une autre période observée : sans cette seconde condition,
  -- deux relevés hauts consécutifs en base formaient un même îlot quel que
  -- soit l'écart — un pic avant l'arrêt du superviseur et un autre à la
  -- reprise, trois heures plus tard, devenaient « trois heures de latence
  -- haute » (C-8). Le rang de l'îlot est le compte des ouvertures.
  --
  -- Il se calculait par deux `ROW_NUMBER` sur un segment d'observation, soit
  -- deux tris de toute la fenêtre de plus. Mesuré sur dix heures à cinq liens
  -- (60 000 relevés) : 700 → 480 ms, mêmes îlots (C-31).
  SELECT *,
         SUM(CASE WHEN haut AND (haut_avant IS NOT 1 OR ecart_s IS NULL OR ecart_s > ?)
                  THEN 1 ELSE 0 END)
           OVER (PARTITION BY wan_id ORDER BY ts ROWS UNBOUNDED PRECEDING) AS ilot
  FROM e
), bascules AS MATERIALIZED (
  -- Dernier changement de cellule sur chaque lien, pour EXPLIQUER un
  -- pic plutôt que le laisser sans cause.
  --
  -- MATERIALIZED, et ce mot pèse : cette table n'est lue que par la
  -- sous-requête corrélée du SELECT final, et SQLite, pour une table
  -- commune lue une seule fois, la RECALCULE à chaque évaluation —
  -- donc une fois par épisode, fenêtrage sur toute la session
  -- compris. Mesuré sur 29 525 relevés et 17 épisodes : 2 303 ms
  -- pour cette requête seule, 428 ms matérialisée, mêmes lignes.
  --
  -- L'écart au relevé précédent est plafonné : sans lui, `LAG`
  -- enjambe les trous de supervision et une cellule vue à une halte
  -- « devient » celle de la halte suivante douze jours plus tard. Le
  -- marquage afficherait alors des handovers fantômes en tête de
  -- liste, et ce sont précisément les plus gros pics.
  SELECT wan_id, ts
  FROM (
    SELECT wan_id, ts, cell_id,
           LAG(cell_id) OVER w AS cell_avant,
           (julianday(ts) - julianday(LAG(ts) OVER w)) * 86400.0 AS ecart_s
    FROM samples
    WHERE router_id = ? AND ts >= ? AND ts <= ?
    WINDOW w AS (PARTITION BY wan_id ORDER BY ts)
  )
  WHERE cell_id IS NOT NULL AND cell_avant IS NOT NULL
    AND cell_id <> cell_avant
    AND ecart_s > 0 AND ecart_s <= ?
)
SELECT f.wan_id, MAX(f.kind) AS kind, MIN(f.ts) AS debut, MAX(f.ts) AS fin,
       COUNT(*) AS releves, MAX(f.rtt_ms) AS pic,
       MAX(f.ecarte) AS ecarte,
       -- Secondes écoulées depuis le dernier changement de cellule au
       -- moment où l'épisode s'ouvre. NULL s'il n'y en a pas eu.
       (SELECT ROUND((julianday(MIN(f.ts)) - julianday(MAX(b.ts))) * 86400.0, 1)
        FROM bascules b
        WHERE b.wan_id = f.wan_id AND b.ts <= MIN(f.ts)) AS depuis_bascule
FROM f WHERE f.haut = 1
GROUP BY f.wan_id, f.ilot
ORDER BY pic DESC
"""

logger = logging.getLogger(__name__)

# La série de latence du routeur COUVRE-t-elle l'instant du relevé `s` ? Un
# point de la série à dix secondes près — sa résolution. Bornes en texte pour
# que la clé primaire (router_id, wan_id, ts) serve : les horodatages sont en
# ISO 8601 UTC, et `strftime` rend le même préfixe sans fuseau.
_SERIE_COUVRE = """EXISTS (
    SELECT 1 FROM router_history h
    WHERE h.router_id = s.router_id AND h.wan_id = s.wan_id
      AND (h.serie_latence = 1 OR h.path_latency_ms IS NOT NULL)
      AND h.ts >= strftime('%Y-%m-%dT%H:%M:%S', s.ts, '-10 seconds')
      AND h.ts <  strftime('%Y-%m-%dT%H:%M:%S', s.ts, '+11 seconds'))"""

# Grandeurs qui sont des TAUX : elles voyagent avec leur dénominateur, sans quoi
# « 0,00 % sur trois paquets de keepalive » et « 0,00 % sur cent mille paquets
# utiles » s'écrivent exactement pareil.
_TAUX = {"loss_percent"}

# Grandeurs RADIO : elles voyagent avec la technologie de leur relevé.
_RADIO = {"rsrp", "rsrq", "sinr", "rssi"}

# Colonnes numériques exposées à l'historique (liste blanche : le nom est interpolé en SQL).
METRICS: tuple[str, ...] = (
    "rsrp", "rsrq", "sinr", "rssi", "rx_kbps", "tx_kbps",
    # Grandeurs du tunnel : ce sont elles qui font geler une image en bonding.
    "rtt_ms", "loss_percent",
    # Dénominateur de la perte, par sens. La règle des 200 paquets vaut aussi sur
    # un graphe : sans lui, une courbe de perte à plat n'est pas relisible — on ne
    # peut pas distinguer un tunnel sain d'un tunnel au repos dont chaque point
    # est calculé sur trois paquets de keepalive. Exposer ces deux colonnes ne
    # coûte AUCUNE requête à l'équipement : elles sont déjà en base.
    "packets_up", "packets_down",
    # Débit porté par le BOND, distinct de celui de l'interface WAN.
    "tunnel_tx_kbps", "tunnel_rx_kbps",
    "path_latency_ms",
)

_SCHEMA = """
CREATE TABLE IF NOT EXISTS samples (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    router_id    TEXT NOT NULL,
    wan_id       TEXT NOT NULL,
    ts           TEXT NOT NULL,
    connected    INTEGER NOT NULL DEFAULT 1,
    operator     TEXT,
    plmn         TEXT,
    rat          TEXT,
    band         TEXT,
    earfcn       INTEGER,
    pci          INTEGER,
    cell_id      INTEGER,
    enb_id       INTEGER,
    tac          INTEGER,
    rsrp         REAL,
    rsrq         REAL,
    sinr         REAL,
    rssi         REAL,
    signal_bars  INTEGER,
    tx_kbps      REAL,
    rx_kbps      REAL,
    -- Les sept colonnes `ant_lat`, `ant_lon`, `ant_distance_m`, `ant_bearing_deg`,
    -- `ant_source`, `ant_confidence` et `ant_site_name` ont disparu d'ici avec
    -- l'identification d'antenne : aucun registre public ne lie une cellule à un
    -- site, les bases collaboratives donnent un barycentre de couverture et non
    -- une position d'antenne, et le modem Telit LM960 ne fournit ni Timing
    -- Advance ni cellules voisines — ce que ces colonnes ont porté n'était donc
    -- pas mesurable. Une base DÉJÀ CRÉÉE les conserve : elles portent trois
    -- semaines d'historique réel, et un DROP COLUMN serait irréversible. Comme
    -- l'INSERT ci-dessous nomme ses colonnes, des colonnes surnuméraires sont
    -- sans conséquence — elles restent simplement à NULL sur les nouveaux relevés.
    kind         TEXT NOT NULL DEFAULT 'cellular',
    ssid         TEXT,
    bssid        TEXT,
    wifi_channel INTEGER,
    -- Position du RÉCEPTEUR au moment de la mesure : GPS du routeur s'il en a un,
    -- sinon coordonnées fixes du fichier de flotte. C'est une mesure de
    -- l'équipement lui-même, sans rapport avec l'identification d'antenne : sans
    -- elle, un relevé dit « telle cellule, tel niveau » sans dire d'où il a été
    -- fait, et le véhicule étant mobile, chaque cycle non enregistré est perdu.
    site_lat     REAL,
    site_lon     REAL,
    -- Vue du lien depuis l'intérieur du tunnel : un WAN peut être connecté et
    -- pourtant écarté du bond, auquel cas il ne transporte rien.
    tunnel_state TEXT,
    rtt_ms       REAL,
    path_latency_ms REAL,
    loss_percent REAL,
    out_of_order_percent REAL,
    -- Les deux sens séparément : une perte montante et une perte descendante ne se
    -- corrigent pas au même endroit, et les additionner laissait le sens le plus
    -- chargé masquer la dégradation de l'autre — mesuré, 4,76 % de perte dans un
    -- sens ressortaient à 0,50 %. Les deux colonnes sans suffixe gardent le pire
    -- des deux sens, c'est-à-dire ce qui se voit à l'écran.
    loss_percent_up REAL,
    loss_percent_down REAL,
    out_of_order_percent_up REAL,
    out_of_order_percent_down REAL,
    -- Dénominateur des taux ci-dessus : paquets comptés sur l'intervalle, par
    -- sens. Sans lui, une perte nulle sur les trois paquets de service d'un
    -- tunnel au repos et une perte nulle sur cent mille paquets d'image
    -- s'écrivaient de la même façon ; relues un mois plus tard, ces deux lignes
    -- valaient autant l'une que l'autre, c'est-à-dire rien.
    packets_up   INTEGER,
    packets_down INTEGER,
    -- Débit porté par le bond sur ce lien, par sens. `tx_kbps` et
    -- `rx_kbps` plus haut mesurent autre chose : toute l'interface WAN,
    -- tunnel et hors tunnel confondus.
    tunnel_tx_kbps REAL,
    tunnel_rx_kbps REAL,
    -- Redondance de SpeedFusion sur l'intervalle : paquets de correction
    -- émis, et paquets reconstitués grâce à elle.
    repair_sent  INTEGER,
    repair_used  INTEGER,
    -- Le message du routeur : « Connected to Orange », « No Cable Detected »,
    -- « Disabled », « absent du relevé ». L'écran live l'affichait, l'historique
    -- le perdait — et un journal d'épisodes qui ne peut pas dire POURQUOI un
    -- lien est tombé n'apprend qu'à moitié.
    status_message TEXT,
    -- Seuil de latence du bond, tel que le PROFIL le configure. Brut, pas
    -- dérivé : le dépassement se recalcule à la lecture, contre le lien le plus
    -- rapide du même cycle. Sans lui, le journal ne peut pas appliquer à
    -- l'historique la règle que le routeur applique en direct — et il jugeait
    -- donc la latence sur un seuil ABSOLU que rien ne justifiait.
    cutoff_ms    REAL,
    -- Depuis combien de secondes CE lien est établi, tel que le routeur le
    -- compte. C'est la seule grandeur qui date une micro-coupure à la seconde
    -- et qui survive au redémarrage de la supervision : un `uptime` qui recule
    -- entre deux relevés prouve que le lien s'est rétabli sans qu'on l'ait vu
    -- tomber. Les compteurs en mémoire, eux, repartent de zéro à chaque
    -- démarrage du processus.
    uptime_s     INTEGER
);
CREATE INDEX IF NOT EXISTS idx_samples_router_wan_ts ON samples (router_id, wan_id, ts);
-- Un second index, SANS le lien, pour les requêtes qui n'en nomment pas.
--
-- `timeline()`, `session_rows()` et les six requêtes d'`events()` portent sur
-- TOUS les liens d'un routeur : du premier index, elles ne pouvaient employer
-- que le préfixe `router_id`, et balayaient donc l'intégralité des relevés de
-- l'équipement quelle que soit la fenêtre demandée. Mesuré sur cinquante mille
-- relevés : une fenêtre d'une minute coûtait autant qu'une fenêtre d'une heure,
-- 5,9 ms, parce que la borne temporelle n'était pas exploitable. Avec cet
-- index, 0,1 ms — et l'heure passe de 12,2 à 4,2 ms.
--
-- `history()` avait reçu un plafond de vingt mille lignes pour exactement cette
-- raison, « l'unique connexion SQLite monopolisée pendant deux cycles
-- entiers » ; `events()` et `timeline()` n'en ont aucun, et lancent six
-- balayages sur la même connexion.
CREATE INDEX IF NOT EXISTS idx_samples_router_ts ON samples (router_id, ts);
"""

# Étapes de migration jouées UNE fois, numérotées dans `PRAGMA user_version`.
_VERSION_PURGE_HORS_GRILLE = 1
_VERSION_IDENTIFIANTS_OPAQUES = 2

# Les tables dont une colonne `router_id` porte l'identifiant d'équipement. La
# table `devices` le porte sous le nom `device_id`, et migre à part.
_TABLES_IDENTIFIANT: tuple[str, ...] = (
    "samples", "router_history", "router_events", "router_journal_reads", "sessions",
)

# Ce qui est propre à l'installation et ne se recalcule pas : le sel des
# identifiants opaques (`app/identite.py`). Dans la base, à côté des données
# qu'il protège : copié avec elles par la sauvegarde du lanceur.
_INSTALLATION_SCHEMA = """
CREATE TABLE IF NOT EXISTS installation (
    cle    TEXT PRIMARY KEY,
    valeur BLOB NOT NULL
);
"""

# Colonnes ajoutées après la première version : les bases déjà créées ne les ont pas,
# et CREATE TABLE IF NOT EXISTS ne les rajoute pas. On les ajoute à la volée.
_ADDED_COLUMNS: tuple[tuple[str, str], ...] = (
    ("kind", "TEXT NOT NULL DEFAULT 'cellular'"),
    # Le code réseau « 208-01 », en remplacement des colonnes `mcc`/`mnc`
    # INTEGER. Le MNC est un identifiant à zéro de tête significatif — « 01 » et
    # « 001 » sont deux réseaux — et une colonne d'affinité INTEGER convertit
    # « 01 » en 1 quoi qu'on y écrive. Purement additif : comme pour les colonnes
    # `ant_*`, les bases existantes gardent `mcc`/`mnc`, que plus rien n'écrit
    # ni ne lit. Leurs lignes anciennes ont `plmn` à NULL, ce qui est exact : la
    # graphie n'a pas été conservée et ne peut plus l'être.
    ("plmn", "TEXT"),
    # La STATION de base, décomposée par `_split_eci` — la seule fonction du
    # projet qui connaisse les deux garde-fous : un ECI tient sur 28 bits, et un
    # NCI 5G SA ne se décompose PAS (la longueur du champ gNB y est configurable
    # par l'opérateur, « le découpage serait une invention »).
    #
    # Elle n'était pas stockée, si bien que `sessions.py` et `events()` la
    # refabriquaient chacun à sa façon — `cell_id >> 8` et `cell_id / 256` — sans
    # aucun des deux garde-fous. Sur un équipement 5G SA, `enb_id` aurait rendu
    # `null` honnêtement pendant que la fiche de session et le journal auraient
    # publié une station INVENTÉE. Le projet avait écrit la règle qu'il enfreignait
    # deux fois sur trois. Elle est maintenant calculée une fois, à l'analyse, et
    # relue partout ailleurs.
    ("enb_id", "INTEGER"),
    # Latence du CHEMIN, distincte de celle du tunnel. Lue une fois par minute :
    # la colonne est donc NULLE sur la plupart des relevés, et c'est exact — une
    # absence de mesure n'est pas une mesure. Un lien filaire, un lien écarté du
    # bond et un châssis sans SpeedFusion n'avaient jusqu'ici aucune latence.
    ("path_latency_ms", "REAL"),
    ("ssid", "TEXT"),
    ("bssid", "TEXT"),
    ("wifi_channel", "INTEGER"),
    ("site_lat", "REAL"),
    ("site_lon", "REAL"),
    # Vue du lien depuis l'intérieur du tunnel SpeedFusion.
    ("tunnel_state", "TEXT"),
    ("rtt_ms", "REAL"),
    ("loss_percent", "REAL"),
    ("out_of_order_percent", "REAL"),
    # Taux par sens : le firmware les renvoie séparément, et les colonnes sans
    # suffixe n'en gardent que le maximum. Sans ces quatre-là, l'historique ne
    # permet plus de dire lequel des deux sens s'est dégradé.
    ("loss_percent_up", "REAL"),
    ("loss_percent_down", "REAL"),
    ("out_of_order_percent_up", "REAL"),
    ("out_of_order_percent_down", "REAL"),
    # Dénominateur des taux de perte, par sens. Ajout purement additif : les
    # lignes déjà écrites le gardent à NULL, ce qui est exact — leur dénominateur
    # n'a pas été conservé et ne peut plus l'être.
    ("packets_up", "INTEGER"),
    ("packets_down", "INTEGER"),
    ("tunnel_tx_kbps", "REAL"),
    ("tunnel_rx_kbps", "REAL"),
    ("repair_sent", "INTEGER"),
    ("repair_used", "INTEGER"),
    ("status_message", "TEXT"),
    ("cutoff_ms", "REAL"),
    ("uptime_s", "INTEGER"),
)


# Sessions d'alimentation. L'équipement n'est allumé que véhicule à l'arrêt :
# chaque mise sous tension est un lieu et une intervention, et c'est la structure
# naturelle de toutes ces mesures. L'outil l'ignorait, si bien que treize mille
# relevés formaient un ruban continu où rien ne séparait deux stationnements
# distants de trois semaines.
#
# L'ancrage est l'`uptime` des liens, PAS un seuil de trou entre deux relevés.
# Mesuré : à 60, 120 ou 300 s de seuil, la détection coupe une vraie session en
# deux à cause d'un arrêt du superviseur de cinq minutes — pendant lequel le
# routeur n'avait pas cessé de fonctionner, même cellule, même bande, niveau
# continu. L'`uptime` tranche sans ambiguïté et ne coûte aucune requête.
# Une session telle que l'écran la lit. `first_seen` n'est PAS une colonne :
# c'est le premier relevé rattaché, relu dans `samples`. La session commence à
# la mise sous tension, calculée depuis l'`uptime` — mais le superviseur, lui,
# a pu être lancé bien après : deux heures dix-huit sur le MAX HD2 du 19
# septembre, une heure quarante-six sur une session du MBX. Entre les deux
# instants il n'y a rien à dessiner, et un axe qui partait de l'allumage donnait
# quarante pour cent de sa largeur à ce vide. Un index seek par session :
# 0,03 ms pour les cinquante dernières, mesuré.
_SQL_SESSION = """
SELECT id, router_id, started_at, last_seen, samples, label,
       (SELECT MIN(ts) FROM samples
         WHERE router_id = s.router_id AND ts >= s.started_at AND ts <= s.last_seen)
       AS first_seen
FROM sessions s
"""

# Les faits du journal du ROUTEUR (`status.log`), sans texte brut : ni nom de
# WAN, ni IP. L'empreinte de la ligne source rend l'insertion idempotente — la
# même ligne revient à chaque lecture tant qu'elle reste parmi les cinquante.
# L'heure que le ROUTEUR garde, un point / 10 s par WAN (revue, R-3.1 et R-3.2).
# À part de `samples` : une ligne y dit « un WAN à un instant », avec sa
# connexion et son uptime, que ces séries ne donnent pas — des lignes partielles
# fausseraient coupures et brassage. Une ligne par (routeur, WAN, instant) :
# relire l'heure, qui chevauche la précédente, ne crée aucun doublon.
_ROUTER_HISTORY_SCHEMA = """
CREATE TABLE IF NOT EXISTS router_history (
    router_id        TEXT NOT NULL,
    wan_id           TEXT NOT NULL,
    ts               TEXT NOT NULL,
    path_latency_ms  REAL,
    rsrp             REAL,
    rsrq             REAL,
    sinr             REAL,
    rat              TEXT,
    -- 1 quand la série de LATENCE a écrit cette ligne. La radio arrive sur une
    -- grille décalée (…:50 contre …:51) : sans cette marque, la courbe de
    -- latence prenait ses lignes pour des latences non mesurées.
    serie_latence    INTEGER,
    PRIMARY KEY (router_id, wan_id, ts)
);
"""

# Colonnes ajoutées à `router_history` après sa création (lot A de R-3.2, sans
# la radio) : la base de l'exploitant les gagne à l'ouverture.
_ROUTER_HISTORY_ADDED = (
    ("rsrp", "REAL"), ("rsrq", "REAL"), ("sinr", "REAL"), ("rat", "TEXT"),
    ("serie_latence", "INTEGER"),
)

_ROUTER_EVENTS_SCHEMA = """
CREATE TABLE IF NOT EXISTS router_events (
    router_id TEXT NOT NULL,
    ts        TEXT NOT NULL,
    kind      TEXT NOT NULL,
    wan_id    TEXT,
    detail    TEXT,
    empreinte TEXT NOT NULL,
    PRIMARY KEY (router_id, empreinte)
);
CREATE INDEX IF NOT EXISTS idx_router_events_ts ON router_events (router_id, ts);
"""

# Les LECTURES abouties du journal du routeur, une ligne par lecture. Un fait
# absent de `router_events` ne dit rien tant qu'on ignore si le journal a été
# lu : refus, client sans journal, horloge décalée — la fiche affichait alors
# « 0 manœuvre » sur un journal que personne n'avait ouvert.
_JOURNAL_LECTURES_SCHEMA = """
CREATE TABLE IF NOT EXISTS router_journal_reads (
    router_id TEXT NOT NULL,
    ts        TEXT NOT NULL,
    PRIMARY KEY (router_id, ts)
);
"""

_SESSIONS_SCHEMA = """
CREATE TABLE IF NOT EXISTS sessions (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    router_id  TEXT NOT NULL,
    started_at TEXT NOT NULL,
    -- Dernier relevé rattaché. Une session en cours l'avance à chaque cycle :
    -- il n'y a pas d'instant « de fin » observable, l'équipement s'éteint sans
    -- prévenir.
    last_seen  TEXT NOT NULL,
    samples    INTEGER NOT NULL DEFAULT 0,
    -- Nom donné par l'exploitant. C'est ce qui rend deux stationnements
    -- comparables : « le parking de la mairie » se retrouve, pas un horodatage.
    label      TEXT
);
CREATE INDEX IF NOT EXISTS idx_sessions_router ON sessions (router_id, started_at DESC);
"""


# Équipements appairés depuis l'interface. Les IDENTIFIANTS N'Y SONT PAS : les
# conserver en clair serait pire que de les redemander. Quand l'exploitant le
# demande, ils vont au trousseau du système (`app/coffre.py`), jamais ici. On
# retient ce qui permet de reconnaître l'équipement et de le proposer à nouveau.
_DEVICES_SCHEMA = """
CREATE TABLE IF NOT EXISTS devices (
    serial     TEXT PRIMARY KEY,
    device_id  TEXT NOT NULL,
    name       TEXT,
    host       TEXT,
    model      TEXT,
    firmware   TEXT,
    first_seen TEXT NOT NULL,
    last_seen  TEXT NOT NULL
);
"""


# Au-delà de ce nombre de lignes dans la fenêtre, l'allègement est confié à
# SQLite au lieu d'être fait en Python. Le seuil n'est pas un réglage de confort :
# `since`/`until` posent une fenêtre ABSOLUE, que rien ne plafonne côté route —
# `?since=1970-01-01T00:00:00Z` demandait donc tout l'historique du lien. Mesuré
# sur une base de trente jours : 507 240 lignes, 11,6 s, 374 Mo de pic pour
# rendre 99 Ko, l'unique connexion SQLite monopolisée pendant deux cycles entiers
# et la boucle d'événements figée le temps de construire un demi-million de
# `HistoryPoint`. L'enveloppe rendue est la MÊME — minimum et maximum par tranche
# de rangs égaux — mais elle se calcule sur des lignes que Python ne voit jamais.
_LIGNES_MEMOIRE_MAX = 20_000

# Fenêtre maximale relue pour la fiche d'une session, et plafond de lignes.
#
# Une session est ancrée sur l'`uptime` du châssis : superviser pour la première
# fois un équipement allumé depuis trois semaines ouvre une session de trois
# semaines, parfaitement légitime. La relire entière faisait 1 711 695 lignes,
# 12,3 s et 1,5 Go — sur un PC de terrain, un gel puis un risque d'épuisement
# mémoire. On relit donc les dernières vingt-quatre heures, ce que la fiche
# annonce (`partial`) plutôt que de le taire : une fiche muette sur ce qu'elle
# n'a pas lu se lit comme une fiche complète.
# Tranches de la bande de stationnement. Deux cents suffisent pour une bande de
# mille pixels de large — au-delà, on calcule des tranches plus fines qu'un
# pixel. En dessous, un incident d'un cycle disparaîtrait dans sa tranche.
_TRANCHES_BANDE = 200

_FENETRE_SESSION_S = 24 * 3600.0
_LIGNES_SESSION_MAX = 150_000


def _echantillonner(points: list[HistoryPoint], limit: int) -> list[HistoryPoint]:
    """Allège une série trop dense en gardant son ENVELOPPE, pas un point sur N.

    Prendre un point sur N est la manière évidente et la pire : c'est exactement
    l'extrême qu'on cherche qui disparaît. Le relevé le plus instructif de toute
    la base est un pic isolé à 1 929 ms au milieu de liens à 30 ms — un pas
    d'échantillonnage l'aurait effacé, et la courbe aurait montré un lien sain.

    On découpe donc la série en tranches de rangs égaux et on garde, dans
    chacune, le minimum ET le maximum, remis dans l'ordre du temps. La courbe
    obtenue est une bande qui contient la vraie : aucun extrême n'est perdu, et
    le nombre de points reste borné.
    """
    if len(points) <= limit:
        return points
    # Une limite sous quatre points ne permet aucune enveloppe — il faut au
    # moins deux tranches de deux. Elle était alors IGNORÉE et la série rendue
    # entière : demander « trois points au plus » en recevait deux mille, et
    # l'appelant ne l'apprenait nulle part. Une demande qu'on ne sait pas
    # satisfaire se refuse, elle ne se retourne pas en son contraire.
    if limit < 4:
        raise ValueError(
            f"Limite de {limit} point(s) : l'enveloppe en demande au moins 4 "
            "(deux tranches de deux)."
        )

    # Deux points par tranche, d'où la moitié des tranches.
    tranches = max(1, limit // 2)
    taille = (len(points) + tranches - 1) // tranches

    allege: list[HistoryPoint] = []
    for debut in range(0, len(points), taille):
        tranche = points[debut : debut + taille]
        mesures = [p for p in tranche if p.value is not None]
        if not mesures:
            # Une tranche sans aucune mesure est une information : le lien n'a
            # rien produit là. On garde son premier point pour que le trou reste
            # visible plutôt que d'être refermé par interpolation.
            allege.append(tranche[0])
            continue
        bas = min(mesures, key=lambda p: p.value)  # type: ignore[arg-type,return-value]
        haut = max(mesures, key=lambda p: p.value)  # type: ignore[arg-type,return-value]
        allege.extend(sorted({id(bas): bas, id(haut): haut}.values(), key=lambda p: p.ts))
    return allege


def _fusionner_tranche(garde: dict, autre: dict) -> None:
    """Réunit deux agrégats d'une même part, sans inventer de valeur.

    Les compteurs s'additionnent, les pires se comparent, et une grandeur que
    l'un des deux n'a pas mesurée ne tire pas l'autre vers le bas.
    """
    for cle in ("releves", "debout", "portants", "tunnel_connu", "reparation", "paquets", "desactives"):
        a, b = garde.get(cle), autre.get(cle)
        garde[cle] = b if a is None else a if b is None else a + b
    # `tx_max` et `rx_max` figuraient ici et dans le SQL. Ils n'étaient PUBLIÉS
    # nulle part : ni dans `TimelineSlice`, ni dans la route. Deux agrégats
    # calculés à chaque tranche de chaque requête pour être jetés — exactement ce
    # qui a valu son retrait à `rtt_worst_ms`, et par la même décision.
    for cle, pire in (("sinr_pire", min), ("rsrp_pire", min)):
        a, b = garde.get(cle), autre.get(cle)
        garde[cle] = b if a is None else a if b is None else pire(a, b)


def _libelle_varie(r) -> bool:
    """Le message a-t-il seulement changé de LIBELLÉ sur un lien resté debout ?

    Un Dome Pro Duo (8.5.1) alterne « Connected to Orange » et « Connected to
    Orange F » sans que rien ne change du lien : deux « Signalements » en cinq
    minutes, du bruit qui noie les vrais. La forme écartée est précise — lien
    connecté des deux côtés, et l'un des messages prolonge l'autre. Un
    changement de réseau (« Orange » → « SFR ») ne la satisfait pas et reste dit.
    """
    avant, apres = str(r["message_avant"]), str(r["status_message"])
    return bool(r["connected"]) and (avant.startswith(apres) or apres.startswith(avant))


def _reseau(operateur: str | None, plmn: str) -> str:
    """« SFR (208-10) » : nommé par son code réseau quand il est connu (R-2.6),
    sinon par le libellé du modem, ou le code seul quand il manque."""
    nom = nom_du_reseau(operateur, plmn)
    return f"{nom} ({plmn})" if nom else plmn


class SampleRepository:
    """Accès aux relevés persistés."""

    def __init__(self, path: str, *, identifiants_fixes: set[str] | frozenset[str] = frozenset()) -> None:
        self._path = path
        # Les identifiants déclarés tels quels dans `routers.yaml` : la
        # migration vers les identifiants opaques ne les renomme jamais, même
        # s'ils ont la forme d'une série.
        self._identifiants_fixes = frozenset(identifiants_fixes)
        self._sel: bytes | None = None
        self._conn: aiosqlite.Connection | None = None
        # Une connexion de LECTURE, pour le journal : ses cinq balayages
        # fenêtrés coûtaient 1,8 s sur une longue session, toutes les 30 s, sur
        # la connexion — donc le thread — de l'écrivain (C-31). En WAL, un
        # lecteur ne bloque pas l'écrivain.
        self._lecture: aiosqlite.Connection | None = None
        # UNE connexion d'écriture, partagée par jusqu'à huit tâches de
        # scrutation, les routes et la veille de reconnexion. Sans verrou, la
        # transaction ouverte par les `insert(commit=False)` d'un équipement
        # était validée par le `touch_session` d'un autre, qui écrivait pendant
        # que le premier attendait ; et un `commit()` raté laissait la
        # transaction ouverte, validée plus tard par n'importe qui. Toute unité
        # d'écriture passe donc par `transaction()`.
        self._ecriture = asyncio.Lock()

    @asynccontextmanager
    async def transaction(self):
        """Une unité d'écriture : sous verrou, validée en entier ou annulée.

        Les écritures faites dans le bloc — `insert(..., commit=False)`,
        `touch_session(..., commit=False)` — sont validées à la sortie. Une
        exception, ou un `commit()` qui échoue, les ANNULE : rien ne reste en
        suspens pour être validé par l'écriture suivante. Le verrou n'est pas
        réentrant : dans le bloc, n'appeler que les variantes `commit=False`.
        """
        async with self._ecriture:
            try:
                yield
                if self._conn is not None:
                    await self._conn.commit()
            except BaseException:
                await self._annuler()
                raise

    async def _annuler(self) -> None:
        if self._conn is None:
            return
        try:
            await self._conn.rollback()
        except Exception as exc:  # la cause d'origine compte davantage
            logger.warning("Annulation de transaction impossible : %s", exc)

    async def connect(self) -> None:
        directory = os.path.dirname(os.path.abspath(self._path))
        os.makedirs(directory, exist_ok=True)
        self._conn = await aiosqlite.connect(self._path)
        self._conn.row_factory = aiosqlite.Row
        # Journal en WAL : un LECTEUR ne bloque plus l'écrivain. En journal
        # DELETE, une transaction ouverte par un script de diagnostic — le cas
        # raconté plus bas, à propos du plan des îlots — rendait « database is
        # locked » après 5,1 s, le relevé était perdu et le cycle coûtait 25 s
        # tant que le lecteur restait. La sauvegarde de `lancer.py` passe par
        # l'API `backup()`, qui lit le WAL comme le reste.
        await self._conn.execute("PRAGMA journal_mode=WAL")
        await self._conn.executescript(_SCHEMA)
        await self._conn.executescript(_DEVICES_SCHEMA)
        await self._conn.executescript(_SESSIONS_SCHEMA)
        await self._conn.executescript(_ROUTER_EVENTS_SCHEMA)
        await self._conn.executescript(_JOURNAL_LECTURES_SCHEMA)
        await self._conn.executescript(_INSTALLATION_SCHEMA)
        await self._conn.executescript(_ROUTER_HISTORY_SCHEMA)
        await self._migrate()
        await self._conn.commit()
        if self._path != ":memory:":
            # Une URI de fichier en bonne forme — `file:///C:/…` sous Windows,
            # espaces encodés — et non `file:` suivi du chemin brut, que SQLite
            # ne lit pas sous Windows.
            uri = Path(os.path.abspath(self._path)).as_uri() + "?mode=ro"
            self._lecture = await aiosqlite.connect(uri, uri=True)
            self._lecture.row_factory = aiosqlite.Row

    async def _migrate(self) -> None:
        """Ajoute les colonnes manquantes sur une base créée par une version antérieure."""
        assert self._conn is not None
        async with self._conn.execute("PRAGMA table_info(samples)") as cursor:
            existing = {row["name"] for row in await cursor.fetchall()}
        for column, definition in _ADDED_COLUMNS:
            if column not in existing:
                await self._conn.execute(f"ALTER TABLE samples ADD COLUMN {column} {definition}")
        async with self._conn.execute("PRAGMA table_info(router_history)") as cursor:
            existing = {row["name"] for row in await cursor.fetchall()}
        if existing:
            for column, definition in _ROUTER_HISTORY_ADDED:
                if column not in existing:
                    await self._conn.execute(
                        f"ALTER TABLE router_history ADD COLUMN {column} {definition}")
        # Les étapes qui TRANSFORMENT des données sont numérotées et jouées
        # une seule fois : `PRAGMA user_version` retient la dernière. Les
        # colonnes ci-dessus se constatent, elles ; une suppression ne se
        # constate pas, et elle balayait toute la table à chaque démarrage.
        async with self._conn.execute("PRAGMA user_version") as cursor:
            version = (await cursor.fetchone())[0]
        if version < _VERSION_PURGE_HORS_GRILLE:
            # Les points écrits hors de la grille de 10 s AVANT le calage :
            # chaque relecture de l'heure les réécrivait décalés d'une seconde,
            # en double (360 lignes le 25/09). Plus rien ne s'écrit hors de la
            # grille depuis : l'étape ne retire que ces restes. La base est
            # sauvegardée par le lanceur avant chaque démarrage.
            await self._conn.execute(
                "DELETE FROM router_history WHERE CAST(substr(ts, 18, 2) AS INTEGER) % 10 != 0")
            # Dans la même transaction que la suppression : validées ensemble
            # par `connect()`, ou pas du tout.
            await self._conn.execute(f"PRAGMA user_version = {_VERSION_PURGE_HORS_GRILLE}")
            await self._conn.commit()
        self._sel = await self._sel_installation()
        if version < _VERSION_IDENTIFIANTS_OPAQUES:
            await self._migrer_identifiants(self._sel)

    @property
    def sel_identifiants(self) -> bytes:
        """Le sel des identifiants opaques de cette installation."""
        if self._sel is None:
            raise RuntimeError("Stockage non connecté : `connect()` n'a pas été appelé.")
        return self._sel

    async def _sel_installation(self) -> bytes:
        """Le sel de l'installation, tiré au sort à la première ouverture."""
        assert self._conn is not None
        async with self._conn.execute(
            "SELECT valeur FROM installation WHERE cle = 'sel_identifiants'"
        ) as cursor:
            ligne = await cursor.fetchone()
        if ligne is not None:
            return bytes(ligne[0])
        sel = secrets.token_bytes(32)
        await self._conn.execute(
            "INSERT INTO installation (cle, valeur) VALUES ('sel_identifiants', ?)", (sel,))
        await self._conn.commit()
        return sel

    async def _migrer_identifiants(self, sel: bytes) -> None:
        """Remplace les identifiants dérivés de la série par leur empreinte opaque.

        L'identifiant d'un équipement appairé valait sa série en minuscules :
        elle était dans chaque table, chaque URL, chaque journal. Sont renommés,
        dans UNE transaction — tout ou rien — marquée par `user_version` :

          - les équipements connus (`devices`) dont l'identifiant est
            exactement l'ancien identifiant dérivé de leur série ;
          - l'historique d'un équipement OUBLIÉ depuis, dont seule la forme
            de l'identifiant trahit la série (trois groupes de quatre) : son
            empreinte se calcule sur l'ancien identifiant, et un ré-appairage
            retombe dessus.

        Un identifiant écrit dans `routers.yaml` n'est jamais touché. Une copie
        de la base est faite juste avant, s'il y a quelque chose à renommer :
        le lanceur en fait une à chaque démarrage, mais pas un `uvicorn` lancé
        à la main.
        """
        assert self._conn is not None
        renommer: dict[str, str] = {}
        async with self._conn.execute("SELECT serial, device_id FROM devices") as cursor:
            connus = [(r["serial"], r["device_id"]) for r in await cursor.fetchall()]
        for serie, ident in connus:
            if serie and ident == ancien_identifiant(serie) and ident not in self._identifiants_fixes:
                renommer[ident] = identifiant_opaque(serie, sel)
        gardes = {ident for _, ident in connus} | self._identifiants_fixes
        for table in _TABLES_IDENTIFIANT:
            async with self._conn.execute(f"SELECT DISTINCT router_id FROM {table}") as cursor:
                for (ident,) in await cursor.fetchall():
                    if (ident not in renommer and ident not in gardes
                            and FORME_ANCIEN_IDENTIFIANT.match(ident or "")):
                        renommer[ident] = identifiant_opaque(ident, sel)
        if renommer:
            await self._copie_avant_migration()
        try:
            for ancien, nouveau in renommer.items():
                for table in _TABLES_IDENTIFIANT:
                    await self._conn.execute(
                        f"UPDATE {table} SET router_id = ? WHERE router_id = ?", (nouveau, ancien))
                await self._conn.execute(
                    "UPDATE devices SET device_id = ? WHERE device_id = ?", (nouveau, ancien))
            await self._conn.execute(f"PRAGMA user_version = {_VERSION_IDENTIFIANTS_OPAQUES}")
            await self._conn.commit()
        except BaseException:
            await self._annuler()
            raise
        if renommer:
            # Le nombre seulement : les anciens identifiants SONT des séries.
            logger.info("Identifiants d'équipement rendus opaques : %d renommé(s)", len(renommer))

    async def _copie_avant_migration(self) -> None:
        """Une copie de la base telle qu'avant la migration des identifiants.

        Hors du motif des copies du lanceur (`samples.*.db.bak`), pour qu'elle
        ne soit pas élaguée par les lancements suivants. Une seule : si elle
        existe déjà, la migration a déjà été tentée sur cet état.
        """
        if self._path == ":memory:" or self._conn is None:
            return
        cible = Path(os.path.abspath(self._path))
        cible = cible.with_name(cible.name + ".avant-identifiants-opaques.bak")
        if cible.exists():
            return
        copie = sqlite3.connect(cible)
        try:
            await self._conn.backup(copie)
        except BaseException:
            # Un fichier entamé (disque plein…) passerait au lancement suivant
            # pour la copie déjà faite, et la migration tournerait sans elle.
            copie.close()
            cible.unlink(missing_ok=True)
            raise
        copie.close()

    async def commit(self) -> None:
        """Valide ce que `insert(..., commit=False)` a écrit HORS de
        `transaction()` — sous verrou, et annulé s'il échoue. Le cycle de
        scrutation, lui, passe par `transaction()`."""
        async with self.transaction():
            pass

    async def insert(self, sample: RadioSample, *, commit: bool = True) -> None:
        """Écrit un relevé. `commit=False` laisse l'appelant valider une fois
        pour tout le cycle : cinq relevés faisaient cinq commits — et sur une
        clé USB, deux fsync chacun — là où un seul suffit. Avec `commit=False`,
        l'appelant tient `transaction()` ; sans, l'écriture prend la sienne."""
        if commit:
            async with self.transaction():
                await self.insert(sample, commit=False)
            return
        if self._conn is None:
            # Un relevé PERDU, et personne pour l'apprendre : le `return` nu
            # rendait l'écriture silencieusement sans effet, et la seule trace
            # était un historique qui cesse d'avancer pendant que l'écran vivant
            # continue. Ce n'est pas une erreur fatale — la supervision en direct
            # tient sans stockage — mais elle doit se DIRE.
            logger.warning(
                "Stockage non connecté : relevé %s/%s du %s perdu",
                sample.router_id, sample.wan.wan_id, sample.ts.isoformat(),
            )
            return
        wan = sample.wan
        await self._conn.execute(
            # 44 colonnes nommées, 44 paramètres. Les nommer explicitement est ce
            # qui rend inoffensives les colonnes `ant_*` que gardent les bases
            # créées avant le retrait de l'identification d'antenne.
            """
            INSERT INTO samples (
                router_id, wan_id, ts, connected, operator, plmn, rat, band, earfcn,
                pci, cell_id, enb_id, tac, rsrp, rsrq, sinr, rssi, signal_bars, tx_kbps, rx_kbps,
                path_latency_ms,
                kind, ssid, bssid, wifi_channel,
                site_lat, site_lon, tunnel_state, rtt_ms, loss_percent,
                out_of_order_percent, loss_percent_up, loss_percent_down,
                out_of_order_percent_up, out_of_order_percent_down,
                packets_up, packets_down,
                tunnel_tx_kbps, tunnel_rx_kbps, repair_sent, repair_used, status_message,
                cutoff_ms,
                uptime_s
            ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            """,
            (
                sample.router_id,
                wan.wan_id,
                sample.ts.isoformat(),
                1 if wan.connected else 0,
                wan.operator,
                wan.plmn,
                wan.rat,
                wan.band,
                wan.earfcn,
                wan.pci,
                wan.cell_id,
                wan.enb_id,
                wan.tac,
                wan.rsrp,
                wan.rsrq,
                wan.sinr,
                wan.rssi,
                wan.signal_bars,
                wan.tx_kbps,
                wan.rx_kbps,
                wan.path_latency_ms,
                wan.kind,
                wan.wifi.ssid if wan.wifi else None,
                wan.wifi.bssid if wan.wifi else None,
                wan.wifi.channel if wan.wifi else None,
                sample.site_lat,
                sample.site_lon,
                wan.tunnel.state if wan.tunnel else None,
                wan.tunnel.rtt_ms if wan.tunnel else None,
                wan.tunnel.loss_percent if wan.tunnel else None,
                wan.tunnel.out_of_order_percent if wan.tunnel else None,
                wan.tunnel.loss_percent_up if wan.tunnel else None,
                wan.tunnel.loss_percent_down if wan.tunnel else None,
                wan.tunnel.out_of_order_percent_up if wan.tunnel else None,
                wan.tunnel.out_of_order_percent_down if wan.tunnel else None,
                wan.tunnel.packets_up if wan.tunnel else None,
                wan.tunnel.packets_down if wan.tunnel else None,
                wan.tunnel.tx_kbps if wan.tunnel else None,
                wan.tunnel.rx_kbps if wan.tunnel else None,
                wan.tunnel.repair_sent if wan.tunnel else None,
                wan.tunnel.repair_used if wan.tunnel else None,
                wan.status_message,
                wan.tunnel.cutoff_ms if wan.tunnel else None,
                wan.uptime_s,
            ),
        )

    async def insert_latency_series(
        self, router_id: str, series: dict[str, list[tuple[datetime, float | None]]],
    ) -> None:
        """La série de latence du routeur, point par point (R-3.2). Relire
        l'heure écrase les points déjà connus par leur dernière valeur."""
        if self._conn is None or not series:
            return
        async with self.transaction():
            await self._conn.executemany(
                """
                INSERT INTO router_history (router_id, wan_id, ts, path_latency_ms, serie_latence)
                VALUES (?, ?, ?, ?, 1)
                ON CONFLICT(router_id, wan_id, ts) DO UPDATE SET
                    path_latency_ms = excluded.path_latency_ms, serie_latence = 1
                """,
                [
                    (router_id, wan_id, ts.isoformat(), valeur)
                    for wan_id, points in series.items()
                    for ts, valeur in points
                ],
            )

    async def insert_quality_series(
        self, router_id: str, series: dict[str, list[tuple[datetime, dict]]],
    ) -> None:
        """La série de radio du routeur (R-3.1). Même table que la latence, sans
        écraser ses colonnes : chacune ne met à jour que les siennes."""
        if self._conn is None or not series:
            return
        async with self.transaction():
            await self._conn.executemany(
                """
                INSERT INTO router_history (router_id, wan_id, ts, rsrp, rsrq, sinr, rat)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(router_id, wan_id, ts) DO UPDATE SET
                    rsrp = excluded.rsrp, rsrq = excluded.rsrq,
                    sinr = excluded.sinr, rat = excluded.rat
                """,
                [
                    (router_id, wan_id, ts.isoformat(), m["rsrp"], m["rsrq"], m["sinr"], m["rat"])
                    for wan_id, points in series.items()
                    for ts, m in points
                ],
            )

    async def filled_rsrp(self, router_id: str, debut: str, fin: str) -> dict[str, list[float]]:
        """Par WAN, le RSRP de l'heure du routeur dans les TROUS de supervision
        de la fenêtre — même règle que les courbes : aucun relevé à moins de
        15 s (R-3.1)."""
        if self._conn is None:
            return {}
        async with self._conn.execute(
            """
            SELECT h.wan_id, h.rsrp FROM router_history h
            WHERE h.router_id = ? AND h.ts >= ? AND h.ts <= ? AND h.rsrp IS NOT NULL
              AND NOT EXISTS (
                SELECT 1 FROM samples s
                WHERE s.router_id = h.router_id AND s.wan_id = h.wan_id
                  AND s.ts >= strftime('%Y-%m-%dT%H:%M:%S', substr(h.ts, 1, 19), '-14 seconds')
                  AND s.ts <  strftime('%Y-%m-%dT%H:%M:%S', substr(h.ts, 1, 19), '+15 seconds'))
            ORDER BY h.ts
            """,
            (router_id, debut, fin),
        ) as cursor:
            lignes = await cursor.fetchall()
        combles: dict[str, list[float]] = {}
        for r in lignes:
            combles.setdefault(str(r["wan_id"]), []).append(float(r["rsrp"]))
        return combles

    async def export_rows(
        self, router_id: str, debut: str, fin: str,
        apres: tuple[str, str, int] | None = None, limite: int | None = None,
    ) -> list[dict]:
        """Les relevés de la fenêtre, bruts, pour l'export (R-3.7).

        Par LOTS quand `limite` est donnée, repris après la clé `(ts, wan_id,
        id)` du lot précédent — l'`id` départage deux relevés d'un même instant : une requête courte par lot, et aucun curseur tenu
        ouvert sur l'unique connexion pendant qu'un client lent télécharge
        (C-23).
        """
        if self._conn is None:
            return []
        suite, params = "", [router_id, debut, fin]
        if apres is not None:
            suite = "AND (ts, wan_id, id) > (?, ?, ?)"
            params += list(apres)
        borne = ""
        if limite is not None:
            borne = "LIMIT ?"
            params.append(limite)
        async with self._conn.execute(
            f"""
            SELECT id, ts, wan_id, kind, connected, operator, plmn, rat, band, earfcn, pci,
                   cell_id, enb_id, tac, rsrp, rsrq, sinr, rssi, signal_bars,
                   tx_kbps, rx_kbps, tunnel_state, rtt_ms, loss_percent_up, loss_percent_down,
                   packets_up, packets_down, path_latency_ms, tunnel_tx_kbps, tunnel_rx_kbps,
                   status_message, uptime_s, site_lat, site_lon, ssid, bssid
            FROM samples WHERE router_id = ? AND ts >= ? AND ts <= ? {suite}
            ORDER BY ts, wan_id, id
            {borne}
            """,
            params,
        ) as cursor:
            return [dict(r) for r in await cursor.fetchall()]

    async def export_reseaux(self, router_id: str, debut: str, fin: str) -> list[dict]:
        """Les couples (libellé, PLMN) de la fenêtre : de quoi ranger sous son
        réseau un relevé publié sans code, sans relire toute la session."""
        if self._conn is None:
            return []
        async with self._conn.execute(
            "SELECT DISTINCT operator, plmn FROM samples "
            "WHERE router_id = ? AND ts >= ? AND ts <= ? "
            "AND operator IS NOT NULL AND plmn IS NOT NULL",
            (router_id, debut, fin),
        ) as cursor:
            return [dict(r) for r in await cursor.fetchall()]

    async def export_router_history(self, router_id: str, debut: str, fin: str) -> list[dict]:
        """L'heure gardée par le routeur sur la fenêtre, pour l'export (R-3.7)."""
        if self._conn is None:
            return []
        async with self._conn.execute(
            """
            SELECT ts, wan_id, path_latency_ms, rsrp, rsrq, sinr, rat FROM router_history
            WHERE router_id = ? AND ts >= ? AND ts <= ? ORDER BY ts, wan_id
            """,
            (router_id, debut, fin),
        ) as cursor:
            return [dict(r) for r in await cursor.fetchall()]

    async def latency_series(
        self, router_id: str, debut: str, fin: str,
    ) -> dict[str, tuple[list[str], list[float]]]:
        """Par WAN : les instants que la série de latence couvre dans la
        fenêtre, et ses mesures — de quoi résumer une session sans compter deux
        fois ce que les relevés recopient (R-3.2), ni perdre ceux qu'aucune
        série ne couvre (C-9)."""
        if self._conn is None:
            return {}
        async with self._conn.execute(
            """
            SELECT wan_id, ts, path_latency_ms AS v FROM router_history
            WHERE router_id = ? AND ts >= ? AND ts <= ?
              AND (serie_latence = 1 OR path_latency_ms IS NOT NULL)
            ORDER BY ts
            """,
            (router_id, debut, fin),
        ) as cursor:
            lignes = await cursor.fetchall()
        series: dict[str, tuple[list[str], list[float]]] = {}
        for r in lignes:
            instants, valeurs = series.setdefault(str(r["wan_id"]), ([], []))
            instants.append(r["ts"])
            if r["v"] is not None:
                valeurs.append(float(r["v"]))
        return series

    async def insert_router_events(self, router_id: str, faits: list[RouterEvent]) -> None:
        """Retient les faits du journal du routeur, une fois chacun."""
        if self._conn is None or not faits:
            return
        async with self.transaction():
            await self._conn.executemany(
                "INSERT OR IGNORE INTO router_events (router_id, ts, kind, wan_id, detail, empreinte) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                [(router_id, f.ts.isoformat(), f.kind, f.wan_id, f.detail, f.empreinte) for f in faits],
            )

    async def note_journal_read(self, router_id: str, lu_a: datetime) -> None:
        """Retient qu'une lecture du journal du routeur a ABOUTI à `lu_a`."""
        if self._conn is None:
            return
        async with self.transaction():
            await self._conn.execute(
                "INSERT OR IGNORE INTO router_journal_reads (router_id, ts) VALUES (?, ?)",
                (router_id, lu_a.isoformat()),
            )

    async def journal_read_between(self, router_id: str, debut: datetime, fin: datetime) -> bool:
        """Le journal du routeur a-t-il été lu au moins une fois entre `debut`
        et `fin` ? Sans cela, l'absence d'un fait n'en est pas la preuve."""
        if self._conn is None:
            return False
        async with self._conn.execute(
            "SELECT 1 FROM router_journal_reads WHERE router_id = ? AND ts >= ? AND ts <= ? LIMIT 1",
            (router_id, debut.isoformat(), fin.isoformat()),
        ) as cursor:
            return await cursor.fetchone() is not None

    async def router_events(
        self, router_id: str, debut: datetime, fin: datetime, kinds: tuple[str, ...] | None = None,
    ) -> list[RouterEvent]:
        """Les faits du journal du routeur dans la fenêtre, du plus ancien au plus récent."""
        if self._conn is None:
            return []
        requete = ("SELECT ts, kind, wan_id, detail, empreinte FROM router_events "
                   "WHERE router_id = ? AND ts >= ? AND ts <= ?")
        params: list[object] = [router_id, debut.isoformat(), fin.isoformat()]
        if kinds:
            requete += f" AND kind IN ({','.join('?' * len(kinds))})"
            params += list(kinds)
        async with self._conn.execute(requete + " ORDER BY ts", params) as cursor:
            return [
                RouterEvent(ts=datetime.fromisoformat(r["ts"]), kind=r["kind"], wan_id=r["wan_id"],
                            detail=r["detail"], empreinte=r["empreinte"])
                for r in await cursor.fetchall()
            ]

    async def wan_connu(self, router_id: str, wan_id: str) -> bool:
        """Cet équipement a-t-il jamais produit un relevé pour ce WAN ?

        Sert à distinguer un WAN inconnu — identifiant mal formé, vieux
        favori, autre équipement — d'un WAN réel qui n'a simplement rien
        mesuré dans la fenêtre demandée. Les deux rendaient un 200 vide.
        """
        if self._conn is None:
            return False
        async with self._conn.execute(
            "SELECT 1 FROM samples WHERE router_id = ? AND wan_id = ? LIMIT 1",
            (router_id, wan_id),
        ) as cur:
            return await cur.fetchone() is not None

    async def history(
        self,
        router_id: str,
        wan_id: str,
        metric: str,
        debut: datetime | None = None,
        fin: datetime | None = None,
        limit: int = 2000,
        min_packets: int | None = None,
    ) -> tuple[list[HistoryPoint], int]:
        """Série temporelle d'une métrique, et le nombre de points disponibles.

        `min_packets` est la règle de PUBLICATION d'un taux, telle que l'ÉCRAN
        la pose — elle n'est pas gravée ici, l'appelant la transmet. Quand elle
        est donnée, un taux est élu parmi les sens qui la satisfont, avec son
        dénominateur, et vaut NULL si aucun ne la satisfait. Sans elle, le taux
        publié est celui du pire sens, tel que le collecteur l'a écrit.

        Il faut qu'elle s'applique ICI, avant l'enveloppe : celle-ci élit le
        minimum et le maximum de chaque tranche sur la valeur seule, et sur une
        fenêtre dense le 33,33 % sur 3 paquets — que l'écran allait jeter —
        éclipsait le 4,7 % réel sur 300 paquets, disparu en amont sans mention.
        Et la table, elle, prenait déjà le pire des sens PUBLIABLES : graphe et
        table appliquaient deux règles au même relevé.

        La fenêtre arrive RÉSOLUE, comme pour `timeline` et `events`. Elle se
        calculait ici, à partir d'un trio `minutes`/`since`/`until` que la route
        transmettait tel quel — et le calcul était faux : sans `since`, le début
        glissait depuis MAINTENANT et non depuis `until`, si bien que
        `?until=14:00` produisait `ts >= 15:53 AND ts < 14:00`. Une fenêtre
        inversée ne lève pas : elle rend zéro point, ce que l'écran ne peut pas
        distinguer d'un routeur qui n'a rien mesuré. Les deux autres routes
        posaient déjà leurs bornes correctement, et dans l'API : c'est là que la
        règle vit maintenant, en un seul exemplaire.

        Le second membre du retour est le nombre de points RÉELLEMENT présents
        dans la fenêtre, avant tout échantillonnage. Sans lui, une courbe
        allégée se lit comme une courbe complète.
        """
        if self._conn is None:
            return [], 0
        if metric not in METRICS:
            raise ValueError(f"Métrique inconnue : {metric}")

        # Le repli ne connaît QUE la demi-heure écoulée, et il s'ancre sur la
        # fin. Il n'y a plus ici de `minutes` à mal ancrer : c'est très
        # exactement ce qui produisait la fenêtre inversée.
        fin = fin or utcnow()
        debut = debut or (fin - timedelta(minutes=30))

        # Les relevés où cette grandeur n'a PAS été mesurée restent dans la
        # série, avec une valeur nulle. Les filtrer paraissait plus propre — et
        # laisserait `withGaps` redériver les trous du TEMPS — mais l'enveloppe
        # allégée ne passe pas par `withGaps` : sur une fenêtre large, un trou
        # se refermerait alors par interpolation, et un lien qui n'a rien
        # produit se lirait comme un lien sain. C'est le mensonge que tout ce
        # module combat, et un test le tient.
        ou = "router_id = ? AND wan_id = ? AND ts >= ? AND ts < ?"
        params: list[object] = [router_id, wan_id, debut.isoformat(), fin.isoformat()]

        # La latence du lien se lit dans la série du ROUTEUR, un point / 10 s
        # (R-3.2). Les relevés n'en portent qu'une copie du dernier point, une
        # fois par minute : ils ne servent que là où la série ne COUVRE pas
        # leur instant, sans doublon ailleurs. La règle était « avant le tout
        # premier point de série de l'équipement » : un jour sans série — écartée
        # pour horloge décalée, écriture ratée — perdait alors ses mesures, et
        # l'écran publiait une absence sur trente relevés en base (C-9).
        source, origine = "samples", "NULL"
        if metric == "path_latency_ms":
            source = f"""(
                SELECT router_id, wan_id, ts, path_latency_ms FROM samples s
                WHERE router_id = ? AND wan_id = ? AND NOT {_SERIE_COUVRE}
                UNION ALL
                -- Les lignes que la série de LATENCE a écrites — et, d'avant la
                -- marque, celles qui portent une latence.
                SELECT router_id, wan_id, ts, path_latency_ms FROM router_history
                WHERE router_id = ? AND wan_id = ?
                  AND (serie_latence = 1 OR path_latency_ms IS NOT NULL)
            )"""
            params = [router_id, wan_id] * 2 + params
        elif metric in ("rsrp", "rsrq", "sinr"):
            # L'heure de RADIO du routeur comble les trous de supervision, et
            # rien qu'eux (R-3.1) : un de ses points n'entre que si aucun relevé
            # de cet outil n'est à moins de 15 s — fenêtre [t - 14 s, t + 15 s[
            # en comparaison de chaînes, les relevés portant leurs fractions de
            # seconde. Ses points non mesurés restent dehors : la courbe s'y
            # interrompt d'elle-même, par l'espacement.
            source = f"""(
                SELECT router_id, wan_id, ts, {metric}, rat, NULL AS source FROM samples
                WHERE router_id = ? AND wan_id = ?
                UNION ALL
                SELECT h.router_id, h.wan_id, h.ts, h.{metric}, h.rat, 'routeur' AS source
                FROM router_history h
                WHERE h.router_id = ? AND h.wan_id = ? AND h.{metric} IS NOT NULL
                  AND NOT EXISTS (
                    SELECT 1 FROM samples s
                    WHERE s.router_id = h.router_id AND s.wan_id = h.wan_id
                      AND s.ts >= strftime('%Y-%m-%dT%H:%M:%S', substr(h.ts, 1, 19), '-14 seconds')
                      AND s.ts <  strftime('%Y-%m-%dT%H:%M:%S', substr(h.ts, 1, 19), '+15 seconds'))
            )"""
            params = [router_id, wan_id] * 2 + params
            origine = "source"

        # Toujours pas de `LIMIT` sur la fenêtre : c'est lui qui piégeait, en
        # rendant les points les plus ANCIENS — avec un `ORDER BY ts ASC` —, si
        # bien qu'une fenêtre large s'arrêtait des heures avant maintenant et que
        # l'absence de fin se lisait comme « rien ne s'est passé ». Ce qui est
        # borné ici, c'est le nombre de lignes qui REMONTENT jusqu'à Python.
        async with self._conn.execute(
            f"SELECT COUNT(*) AS n FROM {source} WHERE {ou}", params
        ) as cursor:
            row = await cursor.fetchone()
        available = int(row["n"]) if row else 0
        if available == 0:
            return [], 0

        # Le dénominateur ne voyage qu'avec un taux : sur un niveau radio il
        # n'aurait aucun sens, et l'ajouter partout alourdirait chaque réponse.
        #
        # Celui DU SENS QUI PORTE LE TAUX, et non le plus grand des deux.
        # `loss_percent` est le pire des deux sens ; le dénominateur rendu était
        # `MAX(packets_up, packets_down)`, donc celui de l'AUTRE sens dès que le
        # sens dégradé était le moins chargé. Mesuré : douze relevés de la base
        # publient ainsi un taux établi sur moins de deux cents paquets — jusqu'à
        # 88 — avec un dénominateur qui franchit le seuil. Le 13 septembre à
        # 14 h 00, le WAN 6 trace 4,71 % calculés sur 191 paquets descendants,
        # sous un dénominateur de 249 paquets montants.
        #
        # Et `NULL` plutôt que zéro quand il est inconnu : un compteur absent
        # n'est pas un intervalle sans paquet. Huit mille relevés portent un taux
        # sans aucun compteur — les afficher « 0 paquet compté » serait une
        # affirmation de comptage que la base ne soutient pas.
        valeur = metric
        techno = "rat" if metric in _RADIO else "NULL"
        denom = (
            "CASE"
            " WHEN loss_percent_up IS NULL AND loss_percent_down IS NULL THEN NULL"
            " WHEN COALESCE(loss_percent_up, -1) >= COALESCE(loss_percent_down, -1)"
            " THEN packets_up ELSE packets_down END"
            if metric in _TAUX
            else "NULL"
        )
        if metric in _TAUX and min_packets is not None:
            # Le pire des sens PUBLIABLES, et son compte. Un sens ne l'est que
            # s'il porte un taux ET un compte qui atteint la règle ; à égalité,
            # le montant l'emporte — même départage que `reportableLoss`.
            haut_ok = f"(loss_percent_up IS NOT NULL AND packets_up >= {int(min_packets)})"
            bas_ok = f"(loss_percent_down IS NOT NULL AND packets_down >= {int(min_packets)})"
            montant = f"({haut_ok} AND (NOT {bas_ok} OR loss_percent_up >= loss_percent_down))"
            valeur = (
                f"CASE WHEN {montant} THEN loss_percent_up"
                f" WHEN {bas_ok} THEN loss_percent_down ELSE NULL END"
            )
            denom = (
                f"CASE WHEN {montant} THEN packets_up"
                f" WHEN {bas_ok} THEN packets_down ELSE NULL END"
            )

        if available <= _LIGNES_MEMOIRE_MAX:
            async with self._conn.execute(
                f"SELECT ts, {valeur} AS value, {denom} AS denom, {techno} AS rat, "
                f"{origine} AS source FROM {source} WHERE {ou} ORDER BY ts ASC",
                params,
            ) as cursor:
                rows = await cursor.fetchall()
            points = [
                HistoryPoint(
                    ts=datetime.fromisoformat(r["ts"]), value=r["value"], denominator=r["denom"],
                    rat=r["rat"], source=r["source"],
                )
                for r in rows
            ]
            return _echantillonner(points, limit), available

        # Fenêtre dense : même enveloppe, calculée par SQLite. `NTILE` découpe la
        # série en tranches de rangs égaux — le découpage exact de
        # `_echantillonner` — et les deux `ROW_NUMBER` désignent, dans chaque
        # tranche, la ligne du minimum et celle du maximum.
        #
        # `value IS NULL` en tête des deux tris pousse les absences de mesure en
        # dernier dans les DEUX sens : une tranche qui contient au moins une
        # mesure élit deux mesures, et une tranche entièrement vide élit sa
        # première ligne — le trou reste visible au lieu d'être refermé par
        # interpolation, exactement comme en Python.
        tranches = max(1, limit // 2)
        async with self._conn.execute(
            f"""
            WITH fenetre AS (
                SELECT ts, {valeur} AS value, {denom} AS denom, {techno} AS rat,
                       {origine} AS source,
                       NTILE(?) OVER (ORDER BY ts) AS tranche
                FROM {source} WHERE {ou}
            ), classee AS (
                SELECT ts, value, denom, rat, source,
                       ROW_NUMBER() OVER (
                           PARTITION BY tranche ORDER BY value IS NULL, value ASC, ts
                       ) AS bas,
                       ROW_NUMBER() OVER (
                           PARTITION BY tranche ORDER BY value IS NULL, value DESC, ts
                       ) AS haut
                FROM fenetre
            )
            SELECT ts, value, denom, rat, source FROM classee WHERE bas = 1 OR haut = 1 ORDER BY ts
            """,
            [tranches, *params],
        ) as cursor:
            rows = await cursor.fetchall()
        points = [
            HistoryPoint(
                ts=datetime.fromisoformat(r["ts"]), value=r["value"], denominator=r["denom"],
                rat=r["rat"], source=r["source"],
            )
            for r in rows
        ]
        return points, available

    async def remember_device(
        self, serial: str, device_id: str, name: str, host: str | None,
        model: str | None = None, firmware: str | None = None,
    ) -> None:
        """Retient un équipement appairé, sans aucun identifiant."""
        if self._conn is None or not serial:
            return
        now = utcnow().isoformat()
        async with self.transaction():
            await self._conn.execute(
                """
                INSERT INTO devices (serial, device_id, name, host, model, firmware,
                                     first_seen, last_seen)
                VALUES (?,?,?,?,?,?,?,?)
                ON CONFLICT(serial) DO UPDATE SET
                    device_id=excluded.device_id, name=excluded.name, host=excluded.host,
                    model=excluded.model, firmware=excluded.firmware, last_seen=excluded.last_seen
                """,
                (serial, device_id, name, host, model, firmware, now, now),
            )

    async def known_devices(self) -> list[dict]:
        """Équipements appairés lors de sessions précédentes."""
        if self._conn is None:
            return []
        async with self._conn.execute(
            "SELECT serial, device_id, name, host, model, firmware, last_seen "
            "FROM devices ORDER BY last_seen DESC"
        ) as cursor:
            return [dict(row) for row in await cursor.fetchall()]

    async def forget_device(self, serial: str) -> bool:
        if self._conn is None:
            return False
        async with self.transaction():
            cursor = await self._conn.execute("DELETE FROM devices WHERE serial = ?", (serial,))
        return cursor.rowcount > 0

    # --- sessions d'alimentation ---------------------------------------------

    async def current_session(self, router_id: str) -> dict | None:
        """La session la plus récente de cet équipement, ou None."""
        if self._conn is None:
            return None
        async with self._conn.execute(
            "SELECT id, router_id, started_at, last_seen, samples, label FROM sessions "
            "WHERE router_id = ? ORDER BY started_at DESC LIMIT 1",
            (router_id,),
        ) as cursor:
            row = await cursor.fetchone()
        return dict(row) if row else None

    async def open_session(self, router_id: str, started_at: datetime) -> int:
        """Ouvre une session. L'appelant a constaté une mise sous tension.

        La garde était un `assert`, que `python -O` retire : le dépôt se
        déploie « avec Python seul » sur un PC de terrain, où rien n'interdit
        cet indicateur. Sans la garde, `self._conn.execute` sur `None` lève un
        `AttributeError` opaque, à mille lieues de sa cause.
        """
        if self._conn is None:
            raise RuntimeError("Stockage non connecté : `connect()` n'a pas été appelé.")
        async with self.transaction():
            cursor = await self._conn.execute(
                "INSERT INTO sessions (router_id, started_at, last_seen, samples) VALUES (?,?,?,0)",
                (router_id, started_at.isoformat(), started_at.isoformat()),
            )
        return int(cursor.lastrowid or 0)

    async def touch_session(
        self, session_id: int, seen_at: datetime, samples: int, *, commit: bool = True,
    ) -> None:
        """Avance la fin d'une session en cours.

        Il n'y a pas d'instant de fin observable : l'équipement s'éteint sans
        prévenir, et c'est le dernier relevé reçu qui borne la session.
        `commit=False` : dans la `transaction()` de l'appelant — celle des
        relevés qu'elle compte, pour que le compte et la fin ne s'écrivent
        qu'avec eux.
        """
        if self._conn is None:
            return
        if commit:
            async with self.transaction():
                await self.touch_session(session_id, seen_at, samples, commit=False)
            return
        await self._conn.execute(
            "UPDATE sessions SET last_seen = ?, samples = samples + ? WHERE id = ?",
            (seen_at.isoformat(), samples, session_id),
        )

    async def wans_connected_since(self, router_id: str, since: datetime | None) -> set[str]:
        """Les WAN qui ont été connectés au moins une fois DEPUIS `since`.

        Même borne que `bond_carried_since` : la session d'alimentation. C'est
        ce qui sépare un lien qui TOMBE — une nouvelle — d'un lien jamais
        raccordé depuis l'allumage, un état : le WAN Ethernet sans câble d'un
        Dome Pro Duo rendait le bandeau « Liaison dégradée » en permanence.
        Une lecture par session ; la boucle tient ensuite le compte.
        """
        if self._conn is None:
            return set()
        requete = "SELECT DISTINCT wan_id FROM samples WHERE router_id = ? AND connected = 1"
        params: tuple = (router_id,)
        if since is not None:
            requete += " AND ts >= ?"
            params = (router_id, since.isoformat())
        async with self._conn.execute(requete, params) as cursor:
            return {str(r[0]) for r in await cursor.fetchall()}

    async def bond_carried_since(self, router_id: str, since: datetime | None) -> bool:
        """Le bond a-t-il porté un lien sur cet équipement DEPUIS `since` ?

        C'est ce qui distingue un événement d'un état. Un tunnel qui TOMBE est
        une nouvelle ; un tunnel qui n'a pas tenu depuis la mise sous tension
        est un état — et l'exploitant l'a dit du MAX HD2 : SpeedFusion n'y est
        pas censé fonctionner. Lire la base plutôt qu'une configuration.

        La borne est la SESSION D'ALIMENTATION, et non la vie entière de
        l'équipement. Une première version demandait « jamais » : la mémoire
        ne décroissait pas et ne connaissait pas le réseau — le HD2 emmené là
        où son pair répond, revenu ici, aurait alerté pour toujours ; une base
        vide aurait fait taire le MBX en pleine panne jusqu'au premier bond.
        Bornée à la session, la mémoire s'efface avec le contact, et c'est la
        même définition que `cycles_in_bond` dans la fiche de session.

        Une lecture par session — la boucle mémorise ensuite. La requête
        s'arrête au premier ACTIVE rencontré.
        """
        if self._conn is None:
            return False
        # `connected = 1` AUSSI : les deux lectures — état WAN, état tunnel —
        # sont à quatre cents millisecondes d'écart, et le cycle où un lien
        # tombe écrit une ligne `connected=0, tunnel_state='ACTIVE'`. Elle ne
        # prouve rien. C'est le même prédicat que `cycles_in_bond`.
        if since is None:
            requete = ("SELECT 1 FROM samples WHERE router_id = ? AND connected = 1"
                       " AND tunnel_state = 'ACTIVE' LIMIT 1")
            params: tuple = (router_id,)
        else:
            requete = ("SELECT 1 FROM samples WHERE router_id = ? AND ts >= ? AND connected = 1"
                       " AND tunnel_state = 'ACTIVE' LIMIT 1")
            params = (router_id, since.isoformat())
        async with self._conn.execute(requete, params) as cursor:
            return await cursor.fetchone() is not None

    async def last_sample_at(self, router_id: str) -> datetime | None:
        """Horodatage du dernier relevé écrit pour cet équipement.

        Sert à mesurer l'ANGLE MORT au redémarrage : quand le superviseur
        s'arrête, plus rien ne l'observe — ni lui-même, ni le routeur, qui
        continue de fonctionner sans témoin. Le trou n'est constatable qu'après,
        en comparant ce que la base porte à l'heure qu'il est.
        """
        if self._conn is None:
            return None
        async with self._conn.execute(
            "SELECT MAX(ts) AS dernier FROM samples WHERE router_id = ?", (router_id,)
        ) as cursor:
            row = await cursor.fetchone()
        if row is None or row["dernier"] is None:
            return None
        return datetime.fromisoformat(str(row["dernier"]))

    async def session_by_id(self, session_id: int) -> dict | None:
        if self._conn is None:
            return None
        async with self._conn.execute(
            _SQL_SESSION + "WHERE id = ?",
            (session_id,),
        ) as cursor:
            row = await cursor.fetchone()
        return dict(row) if row else None

    async def session_rows(
        self, router_id: str, debut: str, fin: str
    ) -> tuple[list[dict], tuple[datetime, datetime] | None]:
        """Relevés bruts d'une session, pour en calculer la fiche.

        L'agrégation se fait en Python et non en SQL : SQLite n'a pas de fonction
        de percentile, le volume se calcule par intégration sur des intervalles
        qui varient, et une session de six heures à 5,3 s de période sur cinq
        liens fait vingt mille lignes — qui tiennent largement en mémoire.

        Ce qui n'y tient pas, c'est une session de trois semaines : elles
        existent, parce qu'une session est ancrée sur l'`uptime` du châssis et
        que superviser pour la première fois un équipement allumé depuis trois
        semaines en ouvre une d'autant. Mesuré : 1 711 695 lignes, 12,3 s, 1,5 Go.
        La lecture est donc bornée aux dernières vingt-quatre heures de la
        session, et le second membre du retour porte la fenêtre RÉELLEMENT lue —
        `None` quand toute la session a été lue. L'appelant doit le dire :
        une fiche qui tait la fenêtre qu'elle a lue se lit comme une fiche
        complète, et c'est le mensonge que tout ce module combat.

        Le tri est chronologique et non par lien : le plafond de lignes est une
        soupape, et un `ORDER BY wan_id` la ferait tomber sur les derniers WAN,
        qui disparaîtraient en silence de la fiche. Par le temps, elle rogne la
        fin de la fenêtre pour tous les liens à la fois — ce que `partial` dit.
        Les listes par lien restent chronologiques, seule chose dont
        `_resume_lien` a besoin.
        """
        if self._conn is None:
            return [], None
        ouverture = datetime.fromisoformat(debut)
        cloture = datetime.fromisoformat(fin)
        depuis = max(ouverture, cloture - timedelta(seconds=_FENETRE_SESSION_S))
        async with self._conn.execute(
            """
            SELECT wan_id, ts, connected, tx_kbps, rx_kbps, rtt_ms, rsrp,
                   cell_id, enb_id, earfcn, tunnel_state, uptime_s,
                   site_lat, site_lon, kind, operator, plmn, status_message,
                   path_latency_ms
            FROM samples
            WHERE router_id = ? AND ts >= ? AND ts <= ?
            ORDER BY ts, wan_id
            LIMIT ?
            """,
            (router_id, depuis.isoformat(), fin, _LIGNES_SESSION_MAX),
        ) as cursor:
            lignes = [dict(row) for row in await cursor.fetchall()]
        # La soupape a-t-elle joué ? Alors la fenêtre s'arrête au dernier relevé
        # lu, et non à la clôture de la session.
        jusqu_a = cloture
        if len(lignes) >= _LIGNES_SESSION_MAX and lignes:
            jusqu_a = datetime.fromisoformat(lignes[-1]["ts"])
        if depuis <= ouverture and jusqu_a >= cloture:
            return lignes, None
        return lignes, (depuis, jusqu_a)

    async def sessions(self, router_id: str, limit: int = 50) -> list[dict]:
        if self._conn is None:
            return []
        async with self._conn.execute(
            _SQL_SESSION + "WHERE router_id = ? ORDER BY started_at DESC LIMIT ?",
            (router_id, limit),
        ) as cursor:
            return [dict(row) for row in await cursor.fetchall()]

    async def name_session(self, session_id: int, label: str | None) -> bool:
        """Nomme une session. C'est ce qui rend deux stationnements comparables."""
        if self._conn is None:
            return False
        async with self.transaction():
            cursor = await self._conn.execute(
                "UPDATE sessions SET label = ? WHERE id = ?", (label, session_id)
            )
        return cursor.rowcount > 0

    async def count(self) -> int:
        if self._conn is None:
            return 0
        async with self._conn.execute("SELECT COUNT(*) AS n FROM samples") as cursor:
            row = await cursor.fetchone()
        return int(row["n"]) if row else 0

    async def timeline(
        self,
        router_id: str,
        debut: datetime,
        fin: datetime,
        tranches: int = _TRANCHES_BANDE,
        periode: float | None = None,
    ) -> tuple[list[dict], float, int]:
        """L'état de chaque lien SUR LA DURÉE, en tranches de temps égales.

        C'est ce qui manquait pour dessiner un stationnement. L'historique
        existant rend UNE grandeur numérique pour UN lien : il ne sait pas dire
        « ce lien était debout mais hors du bond entre 15 h et 16 h », alors que
        c'est exactement ce qu'on vient relire après coup. La question « qu'est-ce
        qui s'est passé pendant que j'étais là » n'avait aucune réponse à l'écran.

        Une requête pour toute la fenêtre et tous les liens, agrégée par SQLite.
        Ramener les relevés bruts aurait été la même faute que l'historique
        corrigé plus haut : une session de six heures sur cinq liens fait vingt
        mille lignes, et personne n'a besoin de les voir une par une pour lire
        une bande de deux cents pixels.

        Chaque tranche porte le PIRE de ce qu'elle contient, jamais une moyenne :
        une bande qui lisse l'incident qu'on cherche ne sert à rien. Le relevé le
        plus instructif de la base est un pic isolé à 1 929 ms au milieu de liens
        à 30 ms — une moyenne sur cinq minutes l'aurait effacé.

        Les tranches sans aucun relevé sont ABSENTES du retour, et c'est une
        information : le superviseur ne regardait pas. Les inventer à zéro
        referait le mensonge que tout ce module combat.

        ENCORE FAUT-IL QU'UNE TRANCHE PUISSE ÊTRE REMPLIE. Le découpage valait
        toujours deux cents parts, quelle que soit la durée : sur une fenêtre de
        deux minutes, chaque part durait 0,6 s pour une cadence de scrutation de
        5,4 s. Quatre parts sur cinq étaient alors vides **par construction**, et
        l'écran annonçait « 89 % de la fenêtre n'a reçu aucun relevé — c'est la
        supervision qui ne tournait pas » sur une fenêtre intégralement couverte.
        Tout début de stationnement était touché, c'est-à-dire le cas d'usage
        central.

        La largeur ne descend donc pas sous deux fois la cadence observée — une
        moyenne, que les écarts réels dépassent souvent. Et elle est
        RENDUE, avec le nombre de parts réellement employé : la route les
        recalculait de son côté sans connaître le plancher, si bien que la bande
        plaçait les tranches jusqu'à quarante-huit secondes du bon endroit et
        divisait les durées par trois.
        """
        if self._conn is None:
            return [], 0.0, 0
        etendue = (fin - debut).total_seconds()
        # Le plancher d'une seconde reste, pour le cas où la cadence est inconnue
        # ou absurde : une tranche plus fine que la seconde n'a aucun sens sur
        # des horodatages qui s'écrivent à la milliseconde.
        #
        # DEUX périodes, pas une : la période est une moyenne lissée, et une part
        # n'est sûre de contenir un relevé que si elle dépasse le plus grand
        # écart. Sur un HD2 en HTTP, un écart sur cinq frôle le double de la
        # médiane ; à une période, 8 parts sur 68 restaient vides sur une
        # fenêtre couverte, lues comme autant de trous de supervision.
        plancher = max(1.0, 2 * (periode or 0.0))
        largeur = max(plancher, etendue / max(1, tranches))
        # Le nombre de parts suit la largeur retenue, sans quoi l'écran étalerait
        # la fenêtre sur deux cents pas dont la plupart ne peuvent rien porter.
        parts = max(1, min(tranches, math.ceil(etendue / largeur) if etendue > 0 else 1))
        async with self._conn.execute(
            """
            SELECT wan_id,
                   CAST((julianday(ts) - julianday(?)) * 86400.0 / ? AS INTEGER) AS tranche,
                   COUNT(*)                                            AS releves,
                   SUM(connected)                                      AS debout,
                   -- Porter, c'est être DEBOUT et dans le bond : un lien
                   -- `connected = 0` dont l'état de tunnel traîne à ACTIVE —
                   -- le relevé d'adieu, la lecture qui précède la chute — ne
                   -- porte rien. C'est le prédicat de la fiche de session et
                   -- de la mémoire du bond, ici en un troisième exemplaire
                   -- qui l'oubliait.
                   SUM(CASE WHEN connected AND tunnel_state = 'ACTIVE' THEN 1 ELSE 0 END) AS portants,
                   SUM(CASE WHEN tunnel_state IS NOT NULL THEN 1 ELSE 0 END) AS tunnel_connu,
                   -- Le relevé d'adieu d'un WAN désactivé est `connected = 0` :
                   -- compté à part, il ne se lit plus comme une panne (C-20).
                   SUM(CASE WHEN status_message = ? THEN 1 ELSE 0 END) AS desactives,
                   MIN(sinr)                                           AS sinr_pire,
                   MIN(rsrp)                                           AS rsrp_pire,
                   -- Redondance dépensée et paquets transportés, cumulés sur la
                   -- tranche. Ils ne servent pas à tracer une courbe mais à
                   -- ÉTABLIR un fait : si des paquets ont circulé sans qu'un
                   -- seul ne soit réparé, la redondance du profil SpeedFusion
                   -- est éteinte.
                   --
                   -- Sans `COALESCE`, et c'est tout l'objet : `SUM` ignore déjà
                   -- les NULL et rend NULL quand il n'y a rien à sommer, tandis
                   -- que le repli à zéro fabriquait « zéro paquet réparé » à
                   -- partir d'une colonne que le firmware n'avait jamais
                   -- renseignée. Mesuré : 720 relevés du 11 septembre portent un
                   -- `packets_up` mesuré et un `repair_sent` absent — et c'est
                   -- cette somme qui faisait écrire « la redondance du profil
                   -- est éteinte ». Quatre lignes plus haut, `MIN(sinr)` rend
                   -- honnêtement NULL quand rien n'est mesuré.
                   SUM(repair_sent)                                    AS reparation,
                   SUM(packets_up)                                     AS paquets
            FROM samples
            WHERE router_id = ? AND ts >= ? AND ts <= ?
            GROUP BY wan_id, tranche
            ORDER BY wan_id, tranche
            """,
            (debut.isoformat(), largeur, STATUT_DESACTIVE,
             router_id, debut.isoformat(), fin.isoformat()),
        ) as cursor:
            lignes = [dict(row) for row in await cursor.fetchall()]

        # Un relevé tombant EXACTEMENT sur la borne haute donne une part de rang
        # `parts`, hors du tableau. Ce n'est pas un arrondi : c'est déterministe,
        # et cela arrive à chaque relecture d'une session passée, puisque `until`
        # vaut alors son dernier relevé.
        #
        # Le rabotage écrasait l'indice sans FUSIONNER les agrégats : deux lignes
        # sortaient pour la même part et le même lien, et la bande comptait ce
        # lien deux fois. On les réunit.
        par_part: dict[tuple[str, int], dict] = {}
        for ligne in lignes:
            ligne["tranche"] = min(max(0, int(ligne["tranche"])), parts - 1)
            cle = (ligne["wan_id"], ligne["tranche"])
            deja = par_part.get(cle)
            if deja is None:
                par_part[cle] = ligne
                continue
            _fusionner_tranche(deja, ligne)
        return list(par_part.values()), largeur, parts

    async def events(
        self, router_id: str, debut: datetime, fin: datetime, seuil_rtt: float
    ) -> tuple[list[dict], list[dict]]:
        """Les ÉPISODES de la fenêtre, et ce qui est trop fréquent pour en être un.

        Tout est détecté depuis longtemps — coupures, rétablissements que seul un
        `uptime` qui recule trahit, changements de cellule — et rien n'était
        jamais listé avec son horodatage : l'écran donnait des compteurs, pas une
        chronologie. « Trois coupures » ne dit pas quand, ni sur quel lien, ni
        pourquoi.

        Deux retours, parce que les faits de cette base n'ont pas le même ordre
        de grandeur. Mesuré sur 32 641 relevés : **cinq** coupures, mais **985**
        changements de station. Lister les seconds noierait les premiers, et un
        journal qu'on ne lit plus ne vaut pas mieux qu'un compteur. Ce qui se
        liste se liste ; ce qui est trop fréquent se compte, par lien.

        Les épisodes de latence sont des ÎLOTS — des suites contiguës au-dessus
        du seuil —, pas un événement par relevé. Mesuré : à 500 ms, 59 îlots pour
        100 relevés ; sans le regroupement, le journal aurait porté cent lignes
        pour cinquante-neuf faits.

        Tout est agrégé par SQLite. Ramener les relevés bruts pour les parcourir
        en Python serait la faute déjà corrigée deux fois ici.
        """
        if self._conn is None:
            return [], []
        lecteur = self._lecture or self._conn
        bornes = (router_id, debut.isoformat(), fin.isoformat())

        # --- ce que le relevé PRÉCÉDENT du même lien permet de voir ---
        #
        # Les mots du routeur : le message d'état était déjà lu, déjà stocké, et
        # n'apparaissait qu'en PIÈCE JOINTE d'une transition de connexion. Or le
        # routeur narre aussi ce qui se passe entre deux transitions, et c'est là
        # qu'il est le plus utile. Câble WAN débranché puis rebranché le 11
        # septembre :
        #
        #     21:21:25  Connected         -> No Cable Detected   (coupure)
        #     21:33:15  No Cable Detected -> Connecting...       (invisible)
        #     21:33:20  Connecting...     -> Detecting MTU...    (invisible)
        #     21:33:30  Detecting MTU...  -> Connected           (retour)
        #
        # Le journal ne montrait que les deux extrémités, et concluait à douze
        # minutes de coupure. Le câble était en réalité rebranché à 21 h 33 min
        # 15 s : les quinze dernières secondes sont une négociation d'adresse et
        # de MTU. Aucun seuil n'est inventé ici : le routeur est l'autorité sur
        # son propre état, et les conditions qu'il sait nommer arriveront dans
        # le journal sans qu'une ligne de code soit écrite pour chacune.
        #
        # Le bond : un lien peut le quitter en restant parfaitement connecté —
        # le tunnel tombe, ou l'exploitant désactive le WAN. Mesuré : sept
        # bascules d'état de tunnel, dont trois SANS transition de connexion.
        # Pas de recoupement avec les épisodes de latence : un lien écarté par la
        # latence reste `ACTIVE`, mesuré sur la totalité des relevés au-delà du
        # seuil.
        #
        #
        # Trois familles d'épisodes se lisent en comparant chaque relevé à celui
        # d'avant sur le même lien : les transitions de connexion (coupure,
        # retour, micro-coupure trahie par un `uptime` qui recule), les mots du
        # routeur (`status_message` qui change sans transition) et les entrées
        # et sorties du bond (`tunnel_state` qui bascule sans transition). Elles
        # vivaient en trois requêtes, chacune rebalayant la fenêtre entière avec
        # sa propre fonction de fenêtrage. Mesuré sur une session de dix heures
        # à cinq liens (29 525 relevés) : 116 + 113 + 111 ms, pour 161 ms en une
        # passe qui porte les quatre `LAG` d'un coup — résultats identiques,
        # vérifiés ligne à ligne. Un même relevé peut appartenir à plusieurs
        # familles (une micro-coupure et un changement de message, par exemple) :
        # il rend alors un épisode par famille, comme avant.
        #
        # Le brassage de cellules, plus bas, est un AGRÉGAT par lien et non une
        # liste : il garde sa propre passe.
        # Coupure, micro-coupure, voisinage : les règles de `regles_coupure`,
        # celles de la fiche de session et du collecteur, en fragments à
        # paramètres liés — et non plus une copie qui en divergeait.
        micro = sql_micro_coupure(
            "connecte_avant", "connected", "uptime_avant", "uptime_s",
            "message_avant", "status_message",
        )
        voisin = sql_voisins("ecart_ms")
        async with lecteur.execute(
            f"""
            WITH f AS (
              SELECT wan_id, kind, ts, connected, uptime_s, status_message, tunnel_state,
                     LAG(connected)      OVER w AS connecte_avant,
                     LAG(uptime_s)       OVER w AS uptime_avant,
                     LAG(status_message) OVER w AS message_avant,
                     LAG(tunnel_state)   OVER w AS tunnel_avant,
                     -- Le relevé précédent n'est un VOISIN que s'il est proche :
                     -- au-delà d'un trou de supervision, `LAG` enjambait des
                     -- jours, et une fenêtre manuelle traversant le trou du
                     -- 30 août au 11 septembre datait une coupure, un retour ou
                     -- une bascule du premier relevé revu — un fait inventé.
                     -- Même garde que `bascules`.
                     {sql_ecart_ms("LAG(ts) OVER w", "ts")} AS ecart_ms
              FROM samples
              WHERE router_id = ? AND ts >= ? AND ts <= ?
              WINDOW w AS (PARTITION BY wan_id ORDER BY ts)
            )
            SELECT wan_id, kind, ts, connected, uptime_s, uptime_avant,
                   status_message, message_avant, tunnel_state,
                   (connecte_avant = 1 AND connected = 0) AS coupure,
                   (connecte_avant = 0 AND connected = 1) AS retour,
                   -- Passage en secours : uptime remis à 0 sans coupure.
                   {micro.sql} AS micro,
                   -- Seulement quand l'état de connexion NE CHANGE PAS : sinon la
                   -- coupure ou le retour porte déjà le message dans sa propre
                   -- colonne, et la ligne ferait doublon.
                   (message_avant IS NOT NULL AND status_message IS NOT NULL
                    AND message_avant <> status_message
                    AND connected = connecte_avant)       AS signalement,
                   (tunnel_avant IS NOT NULL AND tunnel_state IS NOT NULL
                    AND (tunnel_avant = 'ACTIVE') <> (tunnel_state = 'ACTIVE')
                    AND connected = connecte_avant)       AS bascule_bond
            FROM f
            WHERE {voisin.sql} AND (coupure OR retour OR micro OR signalement OR bascule_bond)
            ORDER BY ts
            """,
            (*bornes, *micro.params, *voisin.params),
        ) as cursor:
            voisins = [dict(r) for r in await cursor.fetchall()]

        transitions: list[dict] = []
        signalements: list[dict] = []
        bond: list[dict] = []
        for r in voisins:
            tete = {"wan_id": r["wan_id"], "kind": r["kind"], "ts": r["ts"]}
            if r["coupure"] or r["retour"] or r["micro"]:
                transitions.append({
                    **tete,
                    "status_message": r["status_message"],
                    "uptime_s": r["uptime_s"],
                    "uptime_avant": r["uptime_avant"],
                    # Le relevé d'adieu d'un WAN désactivé n'est pas une panne mais
                    # une DÉCISION de l'exploitant (C-20) : listée en « coupure »,
                    # elle prenait le premier rang de gravité et le rouge, vu en
                    # service le 17/10 sur la SIM 4 d'un HD2.
                    "genre": (
                        ("désactivation" if r["status_message"] == STATUT_DESACTIVE else "coupure")
                        if r["coupure"]
                        else "retour" if r["retour"] else "micro-coupure"
                    ),
                })
            if r["signalement"] and not _libelle_varie(r):
                signalements.append({
                    **tete,
                    "genre": "signalement",
                    "status_message": f"{r['message_avant']} → {r['status_message']}",
                })
            if r["bascule_bond"]:
                actif = r["tunnel_state"] == "ACTIVE"
                bond.append({
                    **tete,
                    "genre": "de retour dans le bond" if actif else "hors du bond",
                    # `WAN_DISABLED` n'est pas une panne mais une DÉCISION : la
                    # porter dans la colonne des mots du routeur évite de faire
                    # chercher un incident là où quelqu'un a simplement désactivé
                    # un WAN.
                    "status_message": None if actif else r["tunnel_state"],
                })

        # L'état d'OUVERTURE, que les bascules ne voient pas : un lien debout et
        # déjà hors du bond au premier relevé de la fenêtre n'a pas de relevé
        # précédent, donc pas de bascule. Vu le 30/09 sur un MAX HD2 : l'alerte
        # « Lien 4 hors du bond » levée, et un journal muet sur ce lien.
        async with lecteur.execute(
            """
            SELECT wan_id, kind, ts, tunnel_state
            FROM (
              SELECT wan_id, kind, ts, connected, tunnel_state,
                     ROW_NUMBER() OVER (PARTITION BY wan_id ORDER BY ts) AS rang
              FROM samples
              WHERE router_id = ? AND ts >= ? AND ts <= ?
            )
            WHERE rang = 1 AND connected = 1
              AND tunnel_state IS NOT NULL AND tunnel_state <> 'ACTIVE'
            """,
            bornes,
        ) as cursor:
            for r in await cursor.fetchall():
                bond.append({
                    "wan_id": r["wan_id"], "kind": r["kind"], "ts": r["ts"],
                    "genre": "hors du bond",
                    "status_message": f"{r['tunnel_state']} · dès le premier relevé",
                })

        # --- îlots de latence : la règle du ROUTEUR, pas un seuil inventé ---
        #
        # Le journal jugeait la latence sur un seuil ABSOLU de 500 ms, choisi sur
        # la distribution. Le routeur, lui, applique un écart : il arrête le
        # trafic sur un lien qui dépasse de plus de `cutoff_ms` celui de latence
        # la plus basse. Deux règles pour une grandeur, et la table marquait
        # « au-delà du seuil » des liens que le journal ne mentionnait pas.
        #
        # Les relevés d'un même cycle sont écrits à quelques millisecondes
        # d'écart : on les regroupe par tranche de trois secondes pour retrouver
        # le peloton contre lequel chacun se compare. Le garde-fou absolu
        # subsiste pour le cas que l'écart ne voit pas — un bond dont TOUS les
        # liens sont mauvais se ressemble parfaitement.
        #
        # Les deux ne disent PAS la même chose, et l'épisode le distingue
        # désormais. La règle du routeur est un FAIT : le firmware a cessé
        # d'employer ce lien. Le garde-fou absolu est une appréciation de cet
        # outil. L'écran étiquetait les deux « Écarté par la latence », donc
        # affirmait une action que le routeur n'avait pas faite : mesuré, les
        # dix-huit relevés de la base à mille millisecondes ou plus portent TOUS
        # un `cutoff_ms` nul, donc aucun n'a été écarté par qui que ce soit.
        async with lecteur.execute(
            _SQL_ILOTS,
            (*bornes, seuil_rtt, _TROU_MAX_S, *bornes, _TROU_MAX_S),
        ) as cursor:
            latences = [dict(r) for r in await cursor.fetchall()]

        for ligne in latences:
            # Deux genres, parce que ce sont deux faits différents : le routeur a
            # écarté le lien, ou l'outil juge la latence excessive sans que le
            # routeur ait rien fait.
            ligne["genre"] = "latence" if ligne["ecarte"] else "latence haute"
            # Une EXPLICATION quand elle existe, jamais une prédiction. Mesuré :
            # après un changement de cellule, un pic de plus de 150 ms survient
            # dans les trois relevés suivants 43,8 % du temps contre 21,7 % sans
            # — un risque doublé. Mais le pic dure une quinzaine de secondes,
            # donc l'information arrive EN MÊME TEMPS que l'effet : elle dit
            # pourquoi, elle n'avertit de rien.
            #
            # Et elle ne couvre qu'une minorité : 15,8 % des pics au-dessus de
            # 150 ms suivent un handover de moins de trente secondes. Les autres
            # doivent rester SANS explication plutôt qu'en recevoir une fausse.
            depuis = ligne.pop("depuis_bascule", None)
            ligne["depuis_bascule"] = (
                depuis if depuis is not None and 0 <= depuis <= _APRES_BASCULE_S else None
            )

        # --- la position : perdue, retrouvée ---
        #
        # Elle ne décrit aucun lien : c'est l'équipement qui a un point ou n'en a
        # pas. Mesuré sur la base, 11,9 % des relevés n'en portent aucun — et
        # l'écran continuait d'afficher la dernière position connue sans que rien
        # ne dise depuis quand elle datait. Le barycentre d'une session en dépend
        # entièrement.
        async with lecteur.execute(
            """
            WITH p AS (
              SELECT CAST(strftime('%s', ts) / 3 AS INTEGER) AS cycle,
                     MIN(ts) AS ts,
                     MAX(site_lat IS NOT NULL) AS fixe
              FROM samples WHERE router_id = ? AND ts >= ? AND ts <= ?
              GROUP BY cycle
            ), f AS (
              SELECT ts, fixe, LAG(fixe) OVER (ORDER BY cycle) AS avant FROM p
            )
            SELECT ts, fixe FROM f WHERE avant IS NOT NULL AND fixe <> avant ORDER BY ts
            """,
            bornes,
        ) as cursor:
            positions = [
                {
                    "genre": "position perdue" if not r["fixe"] else "position retrouvée",
                    "wan_id": "",
                    "kind": "",
                    "ts": r["ts"],
                }
                for r in await cursor.fetchall()
            ]


        # --- ce qui est trop fréquent pour être listé : le brassage de cellules ---
        voisin = sql_voisins_entre("LAG(ts) OVER w", "ts")
        coupure = sql_coupure("f.avant", "f.connected", "f.status_message")
        micro = sql_micro_coupure(
            "f.avant", "f.connected", "f.uptime_avant", "f.uptime_s",
            "f.message_avant", "f.status_message",
        )
        async with lecteur.execute(
            f"""
            WITH f0 AS (
              SELECT wan_id, kind, ts, connected, cell_id, enb_id, uptime_s, status_message,
                     -- Même garde de trou que `voisins` et `bascules` : au-delà,
                     -- chaque WAN comptait un changement de cellule fantôme.
                     {voisin.sql} AS voisin
              FROM samples
              WHERE router_id = ? AND ts >= ? AND ts <= ?
              WINDOW w AS (PARTITION BY wan_id ORDER BY ts)
            -- MATERIALIZED : `f` est lue deux fois ; sans cela SQLite la
            -- recalcule, fenêtres comprises (−6 % sur le journal, mesuré le 17/10).
            ), f AS MATERIALIZED (
              SELECT wan_id, kind, ts, connected, cell_id, uptime_s, status_message, voisin,
                     LAG(connected) OVER w AS avant,
                     LAG(uptime_s)  OVER w AS uptime_avant,
                     LAG(status_message) OVER w AS message_avant,
                     -- La colonne, et non `cell_id / 256` : la décomposition
                     -- a ses deux garde-fous, et ils vivent dans `_split_eci`.
                     enb_id AS station,
                     -- Segment d'OBSERVATION continue : un trou de supervision en
                     -- ouvre un nouveau. Une coupure du lien, elle, est observée et
                     -- ne coupe pas le segment.
                     SUM(CASE WHEN voisin = 0 THEN 1 ELSE 0 END)
                         OVER (PARTITION BY wan_id ORDER BY ts ROWS UNBOUNDED PRECEDING) AS segment
              FROM f0
              WINDOW w AS (PARTITION BY wan_id ORDER BY ts)
            ), rattachements AS (
              -- Les relevés où le lien PUBLIE sa cellule, comparés au précédent
              -- d'entre eux dans le même segment : la DERNIÈRE cellule connue, et
              -- non le relevé d'avant. Celui-ci est souvent une coupure, sans
              -- cellule — et un lien revenu sur un autre opérateur, une autre
              -- cellule, une autre station comptait « 0 / 0 » (revue, scénario 2).
              SELECT wan_id, cell_id, station,
                     LAG(cell_id) OVER c AS cell_avant,
                     LAG(station) OVER c AS station_avant
              FROM f
              WHERE connected = 1 AND cell_id IS NOT NULL
              WINDOW c AS (PARTITION BY wan_id, segment ORDER BY ts)
            ), changements AS (
              SELECT wan_id,
                     SUM(cell_avant IS NOT NULL AND cell_id <> cell_avant) AS cellules,
                     SUM(station IS NOT NULL AND station_avant IS NOT NULL
                         AND station <> station_avant)                     AS stations
              FROM rattachements
              GROUP BY wan_id
            )
            SELECT f.wan_id AS wan_id,
                   MAX(f.kind) AS kind,
                   -- Un lien qui n'a JAMAIS publié de cellule n'en a pas changé
                   -- zéro fois : il n'en a pas. Sans ce compte, l'écran écrivait
                   -- « 0 / 0 » pour un lien filaire, au milieu de modems dont le
                   -- zéro serait une vraie stabilité.
                   SUM(f.cell_id IS NOT NULL)                AS avec_cellule,
                   COALESCE(MAX(ch.cellules), 0)             AS cellules,
                   -- Même règle pour la STATION : sans aucune station
                   -- relevée (5G SA, lignes sans `enb_id`), le compte est
                   -- inconnu, pas nul.
                   CASE WHEN SUM(f.station IS NOT NULL) > 0
                        THEN COALESCE(MAX(ch.stations), 0) END AS stations,
                   -- Les coupures de CETTE fenêtre. Le compteur de l'écran les
                   -- cumulait depuis le démarrage du processus superviseur : une
                   -- base de temps qui n'apparaissait nulle part, et qui se
                   -- remettait à zéro à chaque relance — dans un panneau intitulé
                   -- « depuis la mise sous tension ».
                   -- Une désactivation par l'exploitant n'en est pas une (C-20).
                   -- Règles de `regles_coupure`, comme la fiche de session.
                   SUM(f.voisin AND {coupure.sql})           AS coupures,
                   SUM(f.voisin AND {micro.sql})             AS micro
            FROM f LEFT JOIN changements ch ON ch.wan_id = f.wan_id
            GROUP BY f.wan_id
            ORDER BY f.wan_id
            """,
            (*voisin.params, *bornes, *coupure.params, *micro.params),
        ) as cursor:
            brassage = [dict(r) for r in await cursor.fetchall()]

        # --- changements d'opérateur (R-2.3) ---
        #
        # Un lien revenu sur un AUTRE réseau — bascule de SIM, itinérance — ne
        # laissait qu'une coupure et un retour au journal (revue, scénario 2).
        # Même règle que le brassage : chaque relevé connecté qui publie son
        # code réseau est comparé au DERNIER code connu du lien, à travers une
        # coupure observée mais pas à travers un trou de supervision. Seul le
        # PLMN fait foi : « Orange F » et « Orange » sont le même réseau, et un
        # relevé sans code ne dit rien.
        async with lecteur.execute(
            """
            WITH f0 AS (
              SELECT wan_id, kind, ts, connected, operator, plmn,
                     ((julianday(ts) - julianday(LAG(ts) OVER w)) * 86400.0 <= ?) AS voisin
              FROM samples
              WHERE router_id = ? AND ts >= ? AND ts <= ?
              WINDOW w AS (PARTITION BY wan_id ORDER BY ts)
            -- MATERIALIZED : `f` est lue deux fois ; sans cela SQLite la
            -- recalcule, fenêtres comprises (−6 % sur le journal, mesuré le 17/10).
            ), f AS MATERIALIZED (
              SELECT *, SUM(CASE WHEN voisin = 0 THEN 1 ELSE 0 END)
                          OVER (PARTITION BY wan_id ORDER BY ts ROWS UNBOUNDED PRECEDING) AS segment
              FROM f0
            ), reseaux AS (
              SELECT wan_id, kind, ts, operator, plmn,
                     LAG(plmn)     OVER c AS plmn_avant,
                     LAG(operator) OVER c AS operateur_avant
              FROM f
              WHERE connected = 1 AND plmn IS NOT NULL
              WINDOW c AS (PARTITION BY wan_id, segment ORDER BY ts)
            )
            SELECT wan_id, kind, ts, operator, plmn, plmn_avant, operateur_avant
            FROM reseaux
            WHERE plmn_avant IS NOT NULL AND plmn <> plmn_avant
            ORDER BY ts
            """,
            (_TROU_MAX_S, *bornes),
        ) as cursor:
            operateurs = [
                {
                    "wan_id": r["wan_id"], "kind": r["kind"], "ts": r["ts"],
                    "genre": "changement d'opérateur",
                    "status_message": (
                        f"{_reseau(r['operateur_avant'], r['plmn_avant'])}"
                        f" → {_reseau(r['operator'], r['plmn'])}"
                    ),
                }
                for r in await cursor.fetchall()
            ]

        return transitions + latences + positions + signalements + bond + operateurs, brassage

    async def close(self) -> None:
        if self._lecture is not None:
            await self._lecture.close()
            self._lecture = None
        if self._conn is not None:
            await self._conn.close()
            self._conn = None

    # --- oublier de fermer coûte cher, et ça s'est vu deux fois -------------
    #
    # `aiosqlite` fait tourner un THREAD de travail par connexion, et ce thread
    # n'est PAS un daemon : il attend sur sa file l'ordre d'arrêt que `close()`
    # produit, et rien d'autre ne l'en sort. Tant qu'une référence à la
    # connexion survit quelque part, l'interpréteur ne peut pas sortir — le
    # processus reste vivant, muet, après avoir fait tout son travail.
    #
    # Constaté CINQ fois le même jour. Quatre exécutions de tests, et un
    # diagnostic d'une ligne lancé sur la base de PRODUCTION, qui l'a tenue
    # ouverte en écriture deux heures et vingt minutes à côté du serveur qui y
    # écrivait. La pile était la même à chaque fois :
    #
    #     main            wait_for_thread_shutdown
    #     Thread          _queue_SimpleQueue_get
    #
    # Ce qui n'est PAS établi : le déclencheur exact. Un script court qui
    # oublie de fermer sort quand même — le ramasse-miettes collecte la
    # connexion dès que la variable locale disparaît, et le thread s'arrête. Le
    # blocage demande qu'une référence survive, ce que font les cadres de pile
    # que pytest retient et probablement d'autres chemins. Je n'ai pas su le
    # reproduire à la demande, et ces deux méthodes ne « corrigent » donc aucun
    # défaut démontrable.
    #
    # Elles font autre chose, et c'est suffisant : elles rendent la fermeture
    # automatique pour tout appelant de passage — un script, un test, une sonde
    # — de sorte que la question ne se pose plus. Le serveur, lui, ne change
    # pas : son cycle de vie est géré par `lifespan`.
    async def __aenter__(self) -> "SampleRepository":
        await self.connect()
        return self

    async def __aexit__(self, *_exc: object) -> None:
        await self.close()
