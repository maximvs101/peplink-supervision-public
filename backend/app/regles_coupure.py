"""Les règles qui classent un relevé du superviseur — en UN endroit.

Coupure, micro-coupure, passage en secours, désactivation, voisinage de deux
relevés : ces prédicats s'appliquent à deux endroits qui ne parlent pas la même
langue. Le collecteur et la fiche de session les évaluent en Python, relevé par
relevé ; le journal et le brassage de cellules les évaluent en SQL, sur des
dizaines de milliers de lignes d'un coup (les rapatrier en Python coûterait le
facteur que `LAG` fait gagner).

Ils vivaient en cinq exemplaires, recopiés à la main, et divergeaient déjà :

- « Standby » : le Python retirait les espaces avant de comparer, le SQL non.
  Un « Standby » mal bordé était un passage en secours pour la fiche et une
  micro-coupure pour le journal.
- Le trou de supervision : le journal et le brassage n'appariaient deux relevés
  qu'à moins d'une minute l'un de l'autre ; la fiche de session et le
  collecteur comptaient une coupure à travers n'importe quel trou — un lien vu
  debout le 30 août et coupé le 11 septembre y « coupait » au 11 septembre.
- La casse : `str.lower()` connaît l'Unicode, `LOWER()` de SQLite seulement
  l'ASCII. Sans conséquence aujourd'hui, mais rien ne le garantissait.

Ce module définit donc les états et les seuils UNE fois, et en dérive les deux
formes : le prédicat Python (`est_coupure`, `est_micro_coupure`, `en_secours`,
`sont_voisins`) et le fragment SQL (`sql_*`), qui ne contient que des noms de
colonnes du code et des paramètres liés `?` — aucune valeur n'est concaténée.
`tests/test_regles_coupure.py` juge une même table de cas par les deux, sur une
base SQLite en mémoire.

Choix tranchés, et pourquoi :

- **Espaces** : le collecteur écrivait déjà des messages nets (`_texte` du
  client routeur les retire), et le modèle `WanRadioStatus` les normalise
  désormais lui-même — un message n'entre plus en base bordé d'espaces. La
  lecture reste tolérante, À L'IDENTIQUE des deux côtés, pour les lignes
  écrites avant et pour les relevés du témoin, qui ne passent pas par ce
  modèle. Tolérer d'un seul côté était le défaut ; tolérer des deux ne coûte
  rien.
- **Casse** : comparaison ASCII seulement, celle de SQLite. Le firmware écrit
  « Standby » en ASCII ; un « ſtandby » n'est pas un mot du routeur.
- **Voisinage** : la règle du journal l'emporte. Au-delà d'un trou, on ne sait
  ni quand ni si le lien est tombé ; dater ou compter une coupure, c'est
  inventer un fait. L'écart est mesuré en millisecondes entières, chaque
  instant arrondi comme SQLite le fait (`julianday` ne garde que la
  milliseconde) : sans cela, un écart de 60,0004 s était voisin en SQL et
  lointain en Python.
- **Désactivation** : comparaison EXACTE au libellé que le collecteur écrit
  lui-même. Ce n'est pas un mot du routeur, aucune tolérance n'a lieu d'être.

Le témoin de lien Ethernet (`temoin/`) a ses propres états et ses propres
règles ; il n'emprunte ici que `en_secours`, le mot du firmware.
"""

from __future__ import annotations

import re
from datetime import datetime, timezone
from typing import NamedTuple

# Au-delà, deux relevés successifs d'un même lien ne sont plus VOISINS : un trou
# de supervision les sépare, et rien de ce qui s'y est passé n'est observé.
TROU_MAX_S = 60.0
TROU_MAX_MS = int(TROU_MAX_S * 1000)

# Le mot du firmware pour un WAN en priorité de secours. Passer un lien en
# secours remet son uptime à 0 SANS le couper.
PREFIXE_SECOURS = "standby"

# Les blancs retirés autour d'un message, des deux côtés. Explicites, parce que
# `str.strip()` sans argument retire aussi les blancs Unicode, et `TRIM()` de
# SQLite sans argument seulement l'espace.
ESPACES = " \t\n\r\f\v"

# Le message du relevé d'adieu d'un WAN désactivé. La base n'a pas de colonne
# `disabled` : c'est ce libellé, écrit par le collecteur et reconnu par la bande
# (C-20), qui distingue une désactivation d'une panne. Une seule écriture.
STATUT_DESACTIVE = "désactivé"

_EPOQUE = datetime(1970, 1, 1, tzinfo=timezone.utc)


# --- le prédicat Python --------------------------------------------------------


def normaliser_message(message: object) -> object:
    """Le message tel qu'il doit s'écrire : sans blancs autour, `None` si vide.

    Ce qui n'est pas une chaîne passe tel quel — au validateur de le refuser.
    """
    if not isinstance(message, str):
        return message
    return message.strip(ESPACES) or None


def en_secours(message: object) -> bool:
    """Le routeur dit-il ce lien EN SECOURS (« Standby ») ?

    Passer un WAN en priorité de secours remet son uptime à 0 sans le couper :
    l'uptime qui recule, seul témoin des micro-coupures, y voyait une panne. Une
    manœuvre de l'exploitant n'en est pas une. Forme SQL : `sql_en_secours`.
    """
    if not isinstance(message, str):
        return False
    tete = message.strip(ESPACES)[: len(PREFIXE_SECOURS)]
    # `isascii` d'abord : la minuscule de SQLite est ASCII, celle de Python non.
    return tete.isascii() and tete.lower() == PREFIXE_SECOURS


def _instant_ms(instant: datetime | str) -> int:
    """L'instant en millisecondes depuis l'époque, arrondi comme SQLite.

    SQLite convertit les secondes en millisecondes par `(int)(s * 1000 + 0.5)` :
    l'arrondi au plus proche, demi vers le haut. Calculé ici en entiers, à partir
    des microsecondes. Un instant sans fuseau est en UTC, comme pour SQLite.
    """
    if isinstance(instant, str):
        instant = datetime.fromisoformat(instant)
    if instant.tzinfo is None:
        instant = instant.replace(tzinfo=timezone.utc)
    ecart = instant - _EPOQUE
    return (ecart.days * 86_400 + ecart.seconds) * 1000 + (ecart.microseconds + 500) // 1000


def ecart_ms(avant: datetime | str, apres: datetime | str) -> int:
    """Écart entre deux relevés, en millisecondes entières. Forme SQL : `sql_ecart_ms`."""
    return _instant_ms(apres) - _instant_ms(avant)


def sont_voisins(avant: datetime | str | None, apres: datetime | str | None) -> bool:
    """Deux relevés successifs sont-ils assez proches pour être comparés ?

    Le premier relevé d'un lien n'a pas de voisin. Forme SQL : `sql_voisins`.
    """
    if avant is None or apres is None:
        return False
    return ecart_ms(avant, apres) <= TROU_MAX_MS


def est_coupure(connecte_avant: object, connecte: object, message: object) -> bool:
    """Le lien est-il TOMBÉ entre deux relevés voisins ?

    Connecté puis plus connecté — sauf désactivation par l'exploitant (C-20).
    Une connexion inconnue (`None`) n'est ni l'un ni l'autre : `== 1` et `== 0`,
    comme `= 1` et `= 0` en SQL, et non la vérité Python. Forme SQL :
    `sql_coupure`.
    """
    return connecte_avant == 1 and connecte == 0 and message != STATUT_DESACTIVE


def est_micro_coupure(
    connecte_avant: object, connecte: object,
    uptime_avant: int | None, uptime: int | None,
    message_avant: object, message: object,
) -> bool:
    """Le lien s'est-il rétabli ENTRE deux relevés voisins ?

    Les deux montrent le lien debout ; seul l'uptime qui recule trahit la
    coupure. À 5,3 s de période, c'est l'angle mort où se cachent les
    micro-coupures. Un passage en secours, d'un côté ou de l'autre, remet aussi
    l'uptime à 0 sans couper : il n'en est pas une. Forme SQL :
    `sql_micro_coupure`.
    """
    return (
        connecte_avant == 1 and connecte == 1
        and uptime_avant is not None and uptime is not None
        and uptime < uptime_avant
        and not en_secours(message_avant) and not en_secours(message)
    )


# --- le fragment SQL -----------------------------------------------------------


class Fragment(NamedTuple):
    """Une condition SQL et ses paramètres liés, dans l'ordre de ses `?`."""

    sql: str
    params: tuple


# Un nom de colonne, éventuellement préfixé d'un alias (`f.status_message`).
_COLONNE = re.compile(r"[A-Za-z_][A-Za-z0-9_]*(\.[A-Za-z_][A-Za-z0-9_]*)?")
# Une expression de fenêtre (`LAG(ts) OVER w`) : lettres, chiffres, points,
# parenthèses et espaces — ni guillemet, ni point-virgule, ni commentaire.
_EXPRESSION = re.compile(r"[A-Za-z0-9_.() ]+")


def _colonne(nom: str) -> str:
    if not _COLONNE.fullmatch(nom):
        raise ValueError(f"nom de colonne refusé : {nom!r}")
    return nom


def _expression(texte: str) -> str:
    if not _EXPRESSION.fullmatch(texte) or "--" in texte:
        raise ValueError(f"expression refusée : {texte!r}")
    return texte


def sql_en_secours(message: str) -> Fragment:
    """`en_secours` en SQL. `LOWER()` de SQLite est ASCII, comme le prédicat."""
    m = _colonne(message)
    return Fragment(
        f"(LOWER(SUBSTR(TRIM(COALESCE({m}, ''), ?), 1, ?)) = ?)",
        (ESPACES, len(PREFIXE_SECOURS), PREFIXE_SECOURS),
    )


def sql_ecart_ms(avant: str, apres: str) -> str:
    """`ecart_ms` en SQL, sans paramètre : une expression à nommer dans un CTE.

    `julianday` ne garde que la milliseconde ; son erreur d'arrondi en double
    (quelques centièmes de milliseconde) disparaît dans le `ROUND`.
    """
    return (
        f"CAST(ROUND((julianday({_expression(apres)}) - julianday({_expression(avant)}))"
        " * 86400000.0) AS INTEGER)"
    )


def sql_voisins(ecart: str) -> Fragment:
    """`sont_voisins` en SQL, sur une colonne calculée par `sql_ecart_ms`.

    Le premier relevé d'un lien a un écart NULL : la condition est NULL, donc
    fausse dans un `WHERE` comme dans un `SUM`.
    """
    return Fragment(f"({_colonne(ecart)} <= ?)", (TROU_MAX_MS,))


def sql_voisins_entre(avant: str, apres: str) -> Fragment:
    """`sont_voisins` en SQL, l'écart calculé sur place (voir `sql_ecart_ms`)."""
    return Fragment(f"({sql_ecart_ms(avant, apres)} <= ?)", (TROU_MAX_MS,))


def sql_coupure(connecte_avant: str, connecte: str, message: str) -> Fragment:
    """`est_coupure` en SQL."""
    return Fragment(
        f"({_colonne(connecte_avant)} = 1 AND {_colonne(connecte)} = 0"
        f" AND COALESCE({_colonne(message)}, '') <> ?)",
        (STATUT_DESACTIVE,),
    )


def sql_micro_coupure(
    connecte_avant: str, connecte: str,
    uptime_avant: str, uptime: str,
    message_avant: str, message: str,
) -> Fragment:
    """`est_micro_coupure` en SQL."""
    secours_avant = sql_en_secours(message_avant)
    secours = sql_en_secours(message)
    ua, u = _colonne(uptime_avant), _colonne(uptime)
    return Fragment(
        f"({_colonne(connecte_avant)} = 1 AND {_colonne(connecte)} = 1"
        f" AND {u} IS NOT NULL AND {ua} IS NOT NULL AND {u} < {ua}"
        f" AND NOT {secours.sql} AND NOT {secours_avant.sql})",
        (*secours.params, *secours_avant.params),
    )
