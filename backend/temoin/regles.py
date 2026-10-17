"""Règles du témoin Ethernet : coupures, bande d'état, verdict.

Fonctions pures, sans entrée-sortie : elles reçoivent des relevés (une lecture du
routeur toutes les 3 s) et des faits du journal du routeur (une ligne par minute),
et rendent ce que l'écran et les exports racontent.

Principe commun : une absence de mesure n'est ni une coupure ni un zéro. Une
désactivation par l'exploitant, un passage en secours, un redémarrage du routeur
ne sont pas des coupures non plus.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from app.models import en_secours

# Écart au-delà duquel deux relevés ne se suivent pas (veille du PC, réseau perdu).
TROU_S = 15.0

# Après un relevé injoignable, la collecte recule (3, 6, 12, 24, puis 30 s) : deux
# relevés injoignables se suivent à 30 s au plus. Le trou ne commence qu'au-delà,
# avec une marge.
TROU_INJOIGNABLE_S = 45.0

# Tolérance de rapprochement entre un relevé et une ligne du journal (minute).
TOLERANCE_JOURNAL_S = 15.0

# HYPOTHÈSE — à confirmer à la séance de capture (spec §6.7)
# Au retour d'un redémarrage, le WAN filaire renégocie : ses relevés « tombés »
# qui suivent appartiennent encore au redémarrage, tant que le routeur a moins
# de cette ancienneté.
NEGOCIATION_APRES_REDEMARRAGE_S = 180


@dataclass(frozen=True)
class Releve:
    ts: datetime
    joignable: bool  # False quand la lecture a échoué (alors connecte est None)
    connecte: bool | None
    message: str | None
    anciennete_s: int | None  # uptime du lien
    desactive: bool
    anciennete_routeur_s: int | None  # uptime du routeur
    latence_ms: float | None = None  # latence propre du lien, publiée à la minute


@dataclass(frozen=True)
class FaitRouteur:
    ts: datetime
    genre: str  # « coupure », « test de santé », « désactivation », « redémarrage »
    detail: str | None


@dataclass(frozen=True)
class Coupure:
    debut: datetime
    fin: datetime | None
    genre: str  # « coupure » ou « micro-coupure »
    cause: str | None
    # Faux quand la fin n'a pas été vue : la coupure a été interrompue par un
    # trou de mesure ou par un routeur injoignable. La durée est alors un minimum.
    fin_observee: bool = True

    @property
    def duree_s(self) -> float | None:
        if self.fin is None or self.genre == "micro-coupure":
            return None
        return (self.fin - self.debut).total_seconds()


@dataclass(frozen=True)
class Segment:
    debut: datetime
    fin: datetime
    etat: str  # debout, coupe, desactive, secours, redemarrage, non_mesure


def _etat(rel: Releve) -> str:
    """État propre d'un relevé, sans regard sur ses voisins."""
    if not rel.joignable:
        return "injoignable"
    if rel.desactive:
        return "desactive"
    if not rel.connecte:
        return "coupe"
    if en_secours(rel.message):
        return "secours"
    return "debout"


def _s(a: datetime, b: datetime) -> float:
    return (b - a).total_seconds()


def _trou(avant: Releve, apres_ts: datetime) -> bool:
    """Y a-t-il un trou entre ce relevé et l'heure suivante ?"""
    limite = TROU_S if avant.joignable else TROU_INJOIGNABLE_S
    return _s(avant.ts, apres_ts) > limite


def _micro(avant: Releve, apres: Releve) -> bool:
    """L'ancienneté du lien est repartie en arrière entre deux relevés debout."""
    return (
        _etat(avant) == "debout"
        and _etat(apres) == "debout"
        and avant.anciennete_s is not None
        and apres.anciennete_s is not None
        and apres.anciennete_s < avant.anciennete_s
    )


def _cause(rel: Releve, faits: list[FaitRouteur]) -> str | None:
    # HYPOTHÈSE — à confirmer à la séance de capture (spec §6.7)
    if rel.message and "no cable" in rel.message.lower():
        return "câble"
    # HYPOTHÈSE — à confirmer à la séance de capture (spec §6.7)
    for fait in faits:
        if fait.genre == "test de santé" and abs(_s(rel.ts, fait.ts)) <= TOLERANCE_JOURNAL_S:
            return "test de santé"
    return rel.message


def coupures(releves: list[Releve], faits: list[FaitRouteur]) -> list[Coupure]:
    """Coupures et micro-coupures de la série de relevés (triée par heure)."""
    sortie: list[Coupure] = []
    en_cours: tuple[Releve, Releve] | None = None  # (premier relevé tombé, dernier tombé)
    precedent: Releve | None = None
    # Les relevés d'un redémarrage du routeur (injoignables, puis le WAN qui
    # renégocie) ne font pas de coupure : la bande les dit « redémarrage ».
    redemarrage = _indices_de_redemarrage(releves)

    for idx, rel in enumerate(releves):
        etat = "redemarrage" if idx in redemarrage else _etat(rel)
        suite = precedent is not None and not _trou(precedent, rel.ts)

        if en_cours is not None:
            if etat == "coupe" and suite:
                en_cours = (en_cours[0], rel)
                precedent = rel
                continue
            # La coupure s'arrête au relevé qui la contredit (y compris un relevé
            # injoignable : elle finit à son heure), ou, après un trou, au dernier
            # relevé où l'on a vu le lien tombé. Dans ces deux derniers cas, on
            # n'a pas vu le lien revenir : la fin n'est pas observée.
            fin = rel.ts if suite else en_cours[1].ts
            premier = en_cours[0]
            sortie.append(Coupure(
                premier.ts, fin, "coupure", _cause(premier, faits),
                fin_observee=suite and rel.joignable,
            ))
            en_cours = None

        if etat == "coupe":
            en_cours = (rel, rel)
        elif suite and precedent is not None and _micro(precedent, rel):
            sortie.append(Coupure(rel.ts, None, "micro-coupure", None))
        precedent = rel

    if en_cours is not None:
        premier = en_cours[0]
        sortie.append(Coupure(premier.ts, None, "coupure", _cause(premier, faits)))
    return sortie


def _redemarrages(releves: list[Releve]) -> dict[int, int]:
    """Suites de relevés injoignables suivies d'un routeur reparti de zéro.

    Rend {indice du premier injoignable: indice du premier relevé qui n'est plus
    du redémarrage} (éventuellement `len(releves)`). Le routeur a redémarré si
    son ancienneté, à la reprise, est inférieure à la durée de la suite. Les
    relevés « tombés » qui suivent directement la reprise en font partie (voir
    NEGOCIATION_APRES_REDEMARRAGE_S).
    """
    trouves: dict[int, int] = {}
    i = 0
    while i < len(releves):
        if releves[i].joignable:
            i += 1
            continue
        j = i
        while j < len(releves) and not releves[j].joignable:
            j += 1
        if j < len(releves):
            apres = releves[j]
            duree = _s(releves[i].ts, apres.ts)
            if apres.anciennete_routeur_s is not None and apres.anciennete_routeur_s < duree:
                # HYPOTHÈSE — à confirmer à la séance de capture (spec §6.7)
                # Le WAN filaire renégocie après le démarrage : ses relevés tombés
                # restent du redémarrage jusqu'au premier relevé debout, et tant
                # que le routeur est tout jeune.
                k = j
                while (
                    k < len(releves)
                    and _etat(releves[k]) == "coupe"
                    and releves[k].anciennete_routeur_s is not None
                    and releves[k].anciennete_routeur_s < NEGOCIATION_APRES_REDEMARRAGE_S
                    and not _trou(releves[k - 1], releves[k].ts)
                ):
                    k += 1
                trouves[i] = k
        i = j
    return trouves


def _indices_de_redemarrage(releves: list[Releve]) -> set[int]:
    indices: set[int] = set()
    for debut, fin in _redemarrages(releves).items():
        indices.update(range(debut, fin))
    return indices


def _fusionner(segments: list[Segment]) -> list[Segment]:
    """Colle les segments voisins de même état, écarte ceux de durée nulle."""
    sortie: list[Segment] = []
    for seg in segments:
        if seg.fin <= seg.debut:
            continue
        if sortie and sortie[-1].etat == seg.etat and sortie[-1].fin == seg.debut:
            sortie[-1] = Segment(sortie[-1].debut, seg.fin, seg.etat)
        else:
            sortie.append(seg)
    return sortie


def _aligner(segments: list[Segment], faits: list[FaitRouteur]) -> list[Segment]:
    """Cale le début d'un segment « desactive » ou « redemarrage » sur le journal."""
    # HYPOTHÈSE — à confirmer à la séance de capture (spec §6.7)
    genres = {"desactive": "désactivation", "redemarrage": "redémarrage"}
    sortie = list(segments)
    for i, seg in enumerate(sortie):
        genre = genres.get(seg.etat)
        if genre is None or i == 0:
            continue
        proches = [
            f
            for f in faits
            if f.genre == genre and abs(_s(seg.debut, f.ts)) <= TOLERANCE_JOURNAL_S
        ]
        if not proches:
            continue
        fait = min(proches, key=lambda f: abs(_s(seg.debut, f.ts)))
        avant = sortie[i - 1]
        # Le recalage ne doit ni dépasser le segment ni avaler celui d'avant.
        if avant.fin == seg.debut and avant.debut < fait.ts < seg.fin:
            sortie[i - 1] = Segment(avant.debut, fait.ts, avant.etat)
            sortie[i] = Segment(fait.ts, seg.fin, seg.etat)
    return sortie


def bande(
    releves: list[Releve], fin: datetime, faits: list[FaitRouteur] | None = None
) -> list[Segment]:
    """La bande d'état de l'observation, du premier relevé jusqu'à `fin`."""
    if not releves:
        return []
    redemarrages = _redemarrages(releves)
    segments: list[Segment] = []
    i = 0
    while i < len(releves):
        rel = releves[i]
        if i in redemarrages:
            # Un écart au-delà du trou (voir _trou) entre deux relevés de la suite reste
            # un trou : il n'est pas du redémarrage, il est non mesuré.
            for k in range(i, redemarrages[i]):
                a = releves[k].ts
                b = releves[k + 1].ts if k + 1 < len(releves) else max(fin, a)
                segments.append(Segment(a, b, "non_mesure" if _trou(releves[k], b) else "redemarrage"))
            i = redemarrages[i]
            continue
        etat = _etat(rel)
        suivant_ts = releves[i + 1].ts if i + 1 < len(releves) else fin
        if etat == "injoignable":
            # Injoignable sans redémarrage : rien n'a été mesuré. Le recul de la
            # cadence (jusqu'à 30 s) est connu, il ne fait pas un trou en soi.
            segments.append(Segment(rel.ts, max(suivant_ts, rel.ts), "non_mesure"))
        elif _trou(rel, suivant_ts):
            segments.append(Segment(rel.ts, suivant_ts, "non_mesure"))
        else:
            segments.append(Segment(rel.ts, suivant_ts, etat))
        i += 1
    return _aligner(_fusionner(segments), faits or [])


def verdict(releves: list[Releve], maintenant: datetime) -> dict:
    """Où en est le lien, et depuis quand (état du dernier relevé)."""
    if not releves:
        return {"etat": "aucune_mesure", "depuis": None}
    if _trou(releves[-1], maintenant):
        # PC en veille ou collecte arrêtée : on ne sait pas, et l'absence n'est pas un zéro.
        return {"etat": "aucune_mesure", "depuis": releves[-1].ts}
    groupes = {
        "injoignable": "injoignable",
        "desactive": "desactive",
        "coupe": "coupe",
        "debout": "stable",
        "secours": "stable",
    }
    dernier = _etat(releves[-1])
    etat = groupes[dernier]
    depuis = releves[-1].ts
    for i in range(len(releves) - 2, -1, -1):
        rel, suivant = releves[i], releves[i + 1]
        if groupes[_etat(rel)] != etat:
            break
        if _trou(rel, suivant.ts):
            break
        if etat == "stable" and _micro(rel, suivant):
            # L'ancienneté du lien est repartie de zéro : il n'est stable que
            # depuis cette micro-coupure.
            break
        depuis = rel.ts
    return {"etat": etat, "depuis": depuis}


_MESURES = {"debout", "secours", "coupe"}


def chiffres(segments: list[Segment], coupures: list[Coupure]) -> dict:
    """Totaux de l'observation.

    Le temps mesuré compte debout, secours et coupe ; ni les trous, ni les
    désactivations, ni les redémarrages du routeur n'y entrent.
    """
    duree = dict.fromkeys(_MESURES, 0.0)
    for seg in segments:
        if seg.etat in _MESURES:
            duree[seg.etat] += _s(seg.debut, seg.fin)
    mesure = duree["debout"] + duree["secours"] + duree["coupe"]
    return {
        "coupures": len(coupures),
        "coupe_s": duree["coupe"],
        "mesure_s": mesure,
        "disponibilite": (duree["debout"] + duree["secours"]) / mesure if mesure else None,
    }
