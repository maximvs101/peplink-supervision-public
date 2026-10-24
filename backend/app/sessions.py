"""Fiche d'une session d'alimentation, calculée depuis les relevés qu'elle borne.

L'équipement n'est alimenté que véhicule à l'arrêt : chaque session est un lieu
et une intervention. Sans fiche, une session n'est qu'un intervalle de temps ;
avec elle, « le même parking, trois semaines plus tôt » devient une phrase que
l'outil peut produire.

Tout est dérivé de la table des relevés — aucune requête à l'équipement, aucune
colonne de plus. Ce qui est calculé ici ne l'est pas à l'écriture, parce qu'une
session se relit rarement et se prolonge à chaque cycle : agréger en fin de
session coûte une requête, agréger à chaque cycle en coûterait des milliers.
"""

from __future__ import annotations

import bisect
from datetime import datetime
from math import atan2, cos, degrees, radians, sin, sqrt

from .models import (
    BondAvailability, CellLatency, PathDiversity, Session, SessionLink, SessionSummary,
    nom_du_reseau,
)
from .regles_coupure import TROU_MAX_S, est_coupure, est_micro_coupure, sont_voisins

# Rayon de la Terre en mètres, pour ramener des degrés à une distance.
_RAYON_TERRE_M = 6_371_000.0

# Au-delà de cet écart entre deux relevés consécutifs, on cesse d'intégrer : le
# superviseur s'est arrêté, ou l'équipement n'a pas répondu. Prolonger le dernier
# débit connu sur un trou de cinq minutes inventerait des mégaoctets qui n'ont
# jamais circulé. Le seuil est celui des règles de coupure : un seul trou.
_TROU_MAX_S = TROU_MAX_S


# Relevés qu'une cellule doit réunir avant qu'on publie ses percentiles.
#
# Environ dix-sept minutes à la cadence mesurée. En dessous, un p90 n'est pas un
# p90 : il désigne l'avant-dernière valeur d'une poignée de mesures. Éprouvé sur
# la base — le seuil écarte quatre cellules sur quatorze, qui ne portent à elles
# quatre que 127 relevés sur 39 480.
_RELEVES_PAR_CELLULE = 200

# Largeur d'un cycle de scrutation, pour regrouper les relevés simultanés.
# Reprise du découpage que le stockage emploie déjà.
_CYCLE_S = 3

# Latence sous laquelle un lien du bond est dit « propre ». C'est un CHOIX, et
# il est publié avec le chiffre qu'il produit : personne n'a jamais mis en
# regard un journal de remontée et ce qu'un opérateur a vu à l'antenne.
_LATENCE_PROPRE_MS = 150.0


def _cycle(ts: str) -> int:
    """Le cycle de scrutation auquel appartient un horodatage ISO."""
    return int(datetime.fromisoformat(ts).timestamp()) // _CYCLE_S


def _latence_par_cellule(lignes: list[dict]) -> list[CellLatency]:
    """Les percentiles de latence, CELLULE PAR CELLULE.

    La médiane d'un lien ne bouge presque pas quand sa queue se multiplie par
    quatre : c'est la queue qui fait geler une image, et elle ne se voit qu'à
    cellule fixe. Un percentile calculé sur une fenêtre qui chevauche deux
    cellules mélange deux populations et ne décrit ni l'une ni l'autre.
    """
    # Par (lien, réseau, cellule), et non par cellule seule : un percentile
    # est propre à un modem, et un ECI n'est unique que dans son PLMN.
    Cle = tuple[str, str | None, int]
    par_cellule: dict[Cle, list[float]] = {}
    stations: dict[Cle, int | None] = {}
    for ligne in lignes:
        if ligne["cell_id"] is None or ligne["rtt_ms"] is None:
            continue
        cle: Cle = (str(ligne["wan_id"]), ligne.get("plmn"), int(ligne["cell_id"]))
        par_cellule.setdefault(cle, []).append(float(ligne["rtt_ms"]))
        stations.setdefault(cle, ligne["enb_id"])

    return sorted(
        (
            CellLatency(
                cell_id=cellule,
                wan_id=wan_id,
                plmn=plmn,
                enb_id=stations.get((wan_id, plmn, cellule)),
                samples=len(rtts),
                rtt_p50_ms=_percentile(rtts, 0.50),
                rtt_p90_ms=_percentile(rtts, 0.90),
                rtt_p99_ms=_percentile(rtts, 0.99),
            )
            for (wan_id, plmn, cellule), rtts in par_cellule.items()
            if len(rtts) >= _RELEVES_PAR_CELLULE
        ),
        key=lambda c: (-(c.rtt_p99_ms or 0.0), c.wan_id, c.cell_id),
    )


def _disponibilite(lignes: list[dict]) -> BondAvailability:
    """Part du temps où la liaison avait au moins un lien propre.

    LE piège de ce calcul, et il n'est pas théorique : la sonde de tunnel est
    plus espacée que la scrutation. Un cycle où aucun lien ne porte d'état de
    tunnel n'est pas un cycle en panne, c'est un cycle NON OBSERVÉ. Les compter
    au dénominateur revient à prendre l'absence de regard pour une panne —
    mesuré sur la base, une halte passait ainsi de 99,8 % à 63,8 %, soit une
    heure et demie de panne entièrement fabriquée.

    `observed` et `total` voyagent donc avec le taux, comme un dénominateur
    accompagne un taux de perte partout ailleurs ici.
    """
    cycles: dict[int, tuple[int, int, int]] = {}
    for ligne in lignes:
        cle = _cycle(ligne["ts"])
        sondes, propres, dans_le_bond = cycles.get(cle, (0, 0, 0))
        if ligne["tunnel_state"] is not None:
            sondes += 1
            actif = ligne["connected"] and str(ligne["tunnel_state"]) == "ACTIVE"
            if actif:
                dans_le_bond += 1
            if (
                actif
                and ligne["rtt_ms"] is not None
                and float(ligne["rtt_ms"]) <= _LATENCE_PROPRE_MS
            ):
                propres += 1
        cycles[cle] = (sondes, propres, dans_le_bond)

    observes = [(s, p) for s, p, _ in cycles.values() if s > 0]
    # Les cycles où le bond a porté au moins un lien, propre ou non : c'est ce
    # qui distingue « jamais propre » de « jamais monté ».
    portes = sum(1 for s, _, d in cycles.values() if s > 0 and d > 0)
    # Un bond qui n'a porté AUCUN lien n'a pas de disponibilité « propre » :
    # elle est sans objet, pas nulle. L'écran le disait déjà ; l'API publiait
    # 0 % (revue, R-3.6) — « jamais disponible » pour qui la lirait seule.
    part = lambda n: (
        round(100.0 * sum(1 for _, p in observes if p >= n) / len(observes), 2)
        if observes and portes > 0
        else None
    )
    return BondAvailability(
        threshold_ms=_LATENCE_PROPRE_MS,
        total_cycles=len(cycles),
        observed_cycles=len(observes),
        at_least_one_pct=part(1),
        at_least_two_pct=part(2),
        cycles_in_bond=portes,
    )


def _identifiant_reseau(lignes: list[dict]):
    """Une fonction : relevé → identifiant de son réseau (PLMN), ou None.

    Le PLMN s'il est publié. Sinon le libellé, rangé sous le PLMN d'un libellé
    connu qu'il égale ou prolonge — « Orange » et « Orange F », même réseau.
    Mesuré en direct : à chaque reconnexion le routeur publie le libellé SANS
    PLMN, et une clé par libellé faisait alterner deux « Orange F ».
    """
    connus = {
        str(l["operator"]): str(l["plmn"])
        for l in lignes if l.get("operator") and l.get("plmn")
    }

    def reseau(ligne: dict) -> str | None:
        if ligne.get("plmn"):
            return str(ligne["plmn"])
        libelle = ligne.get("operator")
        if not libelle:
            return None
        libelle = str(libelle)
        if libelle in connus:
            return connus[libelle]
        proche = next((p for l, p in connus.items() if l.startswith(libelle) or libelle.startswith(l)), None)
        return proche or libelle

    return reseau


def _succession_operateurs(lignes: list[dict], reseau) -> list[str]:
    """Les opérateurs d'UN lien, dans l'ordre où il les a eus.

    Un réseau = un PLMN (voir `_identifiant_reseau`) ; son nom est le DERNIER
    libellé vu sous ce réseau. Un relevé sans réseau — une coupure — n'interrompt
    rien : Orange, coupure, Orange reste « Orange ».
    """
    ordre: list[str] = []
    noms: dict[str, str] = {}
    for ligne in lignes:
        libelle = ligne.get("operator")
        cle = reseau(ligne)
        if not ligne.get("connected") or not libelle or cle is None:
            continue
        noms[cle] = str(libelle)
        if not ordre or ordre[-1] != cle:
            ordre.append(cle)
    # Nommés par leur code réseau quand la clé en est un (R-2.6).
    return [nom_du_reseau(noms[r], r) or noms[r] for r in ordre]


def _diversite(lignes: list[dict]) -> PathDiversity:
    """Ce que les modems partageaient, en part de temps.

    Ne comptent que les cycles où TOUS les liens cellulaires connectés ont
    publié leur cellule : un cycle où l'un d'eux se tait n'est pas un cycle de
    moindre diversité, c'est un cycle qu'on ne sait pas juger, et l'exclure vaut
    mieux que de conclure à sa place.

    Cette fiche est par SESSION, c'est-à-dire par halte. Un chiffre global
    mélangerait des régimes opposés : mesuré sur la base, les quatre modems sont
    sur la même station 0,2 % du temps à une halte et 99,7 % à une autre, et la
    moyenne des deux ne décrit ni l'une ni l'autre.
    """
    cycles: dict[int, list[dict]] = {}
    # Un opérateur, c'est un PLMN : « Orange » et « Orange F » (208-01) en
    # faisaient deux (revue, session 9). Voir `_identifiant_reseau`.
    reseau = _identifiant_reseau(lignes)
    operateurs: set[str] = set()
    for ligne in lignes:
        if str(ligne["kind"] or "cellular") != "cellular" or not ligne["connected"]:
            continue
        if (cle := reseau(ligne)) is not None:
            operateurs.add(cle)
        cycles.setdefault(_cycle(ligne["ts"]), []).append(ligne)

    complets = [
        groupe
        for groupe in cycles.values()
        if len(groupe) > 1 and all(l["cell_id"] is not None for l in groupe)
    ]
    def part(champ: str, cle=None) -> tuple[float | None, int]:
        """Part des cycles où TOUS les liens partagent ce champ.

        `cle` affine l'identité comparée — la cellule, c'est l'identifiant ET
        la porteuse — sans changer ce qui rend un cycle jugeable : `champ`.

        Le dénominateur est propre à chaque champ, et c'est le point : un cycle
        où l'un des liens ne publie pas sa station n'est pas un cycle « sans
        partage de station », c'est un cycle qu'on ne sait pas juger. Le compter
        au dénominateur le compterait comme une diversité qu'on n'a pas
        constatée.

        Le cas n'est pas théorique. La colonne `enb_id` est récente : toutes les
        lignes écrites avant elle la portent à NULL, et la première version de
        ce calcul rendait 3,9 % de partage de station sur une halte où la mesure
        indépendante en trouve 99,7 %. Un zéro pour une absence, exactement.
        """
        cle = cle or (lambda l: l[champ])
        jugeables = [g for g in complets if all(l[champ] is not None for l in g)]
        if not jugeables:
            return None, 0
        return round(100.0 * sum(1 for g in jugeables if len({cle(l) for l in g}) == 1)
                     / len(jugeables), 2), len(jugeables)

    # La CELLULE est identifiée par son identifiant ET sa porteuse, comme à
    # l'écran : deux secteurs peuvent porter le même identifiant sur des canaux
    # différents, et ce sont alors des ressources distinctes. La fiche jugeait
    # sur `cell_id` seul, et disait « 100 % même cellule » là où l'écran
    # comptait deux cellules — deux définitions d'un même mot, à deux clics
    # d'écart. Une porteuse inconnue reste inconnue (`None`), et deux liens
    # qui l'ignorent tous deux ne sont pas départagés par elle — même règle.
    # Une station et une cellule appartiennent à un OPÉRATEUR, et une porteuse
    # inconnue ne réunit personne : même règle qu'à l'écran (C-40).
    site, n_site = part("enb_id", cle=lambda l: (l.get("plmn"), l["enb_id"]))
    cellule, n_cellule = part("cell_id", cle=lambda l: (
        l.get("plmn"), l["cell_id"],
        l["earfcn"] if l["earfcn"] is not None else ("?", l["wan_id"]),
    ))
    frequence, n_frequence = part("earfcn")
    return PathDiversity(
        cycles=len(complets),
        same_site_pct=site, site_cycles=n_site,
        same_cell_pct=cellule, cell_cycles=n_cellule,
        same_earfcn_pct=frequence, earfcn_cycles=n_frequence,
        operators=len(operateurs),
    )


def _barycentre(
    positions: list[tuple[float, float]],
) -> tuple[float | None, float | None, float | None]:
    """Le centre d'un stationnement, et le rayon qui le contient à 90 %.

    Deux défauts vivaient dans les quatre lignes que ceci remplace.

    **Le rayon était pris par index tronqué** : `ecarts[int(0.90 * (n - 1))]`.
    Sur UN point, `int(0.90 * 0)` vaut 0 et le rayon rendu était `0.0` — un
    zéro pour une absence, alors qu'un rayon demande au moins deux points pour
    exister. Sur deux points il rendait le PLUS PETIT des deux écarts. Et
    `_percentile`, qui interpole correctement, est déclaré juste en dessous et
    n'était pas appelé.

    **La longitude était moyennée arithmétiquement.** À cheval sur
    l'antiméridien, −179,9° et +179,9° — deux points distants de 22 km — donnent
    une moyenne de 0°, soit le golfe de Guinée. La moyenne d'angles passe par
    les composantes cartésiennes. Le cas ne se présente pas en Bretagne ; il se
    présenterait au premier déploiement dans le Pacifique, et il serait alors
    indétectable à l'écran — une position plausible, simplement fausse.
    """
    if not positions:
        return None, None, None

    lat = sum(p[0] for p in positions) / len(positions)
    # Moyenne CIRCULAIRE des longitudes : la moyenne arithmétique de −179,9 et
    # +179,9 vaut 0, à 20 000 km des deux points qu'elle prétend résumer.
    x = sum(cos(radians(p[1])) for p in positions)
    y = sum(sin(radians(p[1])) for p in positions)
    lon = degrees(atan2(y, x)) if (x, y) != (0.0, 0.0) else positions[0][1]

    # Un rayon demande au moins deux points. Sur un seul, il n'y a pas de
    # dispersion à mesurer — et zéro affirmerait qu'il n'y en a aucune.
    rayon = (
        _percentile([_distance_m(lat, lon, p[0], p[1]) for p in positions], 0.90)
        if len(positions) > 1
        else None
    )
    return round(lat, 6), round(lon, 6), rayon


def _percentile(valeurs: list[float], part: float) -> float | None:
    """Percentile par interpolation linéaire, ou None sans mesure.

    SQLite n'en a pas, et les percentiles sont justement ce qui sépare un lien
    régulier d'un lien qui décroche : le RTT médian de deux SIM peut être
    identique quand l'une monte à 1 929 ms et l'autre jamais au-dessus de 80.
    """
    if not valeurs:
        return None
    ordonnees = sorted(valeurs)
    if len(ordonnees) == 1:
        return round(ordonnees[0], 1)
    position = part * (len(ordonnees) - 1)
    bas = int(position)
    haut = min(bas + 1, len(ordonnees) - 1)
    poids = position - bas
    return round(ordonnees[bas] * (1 - poids) + ordonnees[haut] * poids, 1)


def _distance_m(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Distance plane suffisante à cette échelle.

    Les positions d'une session tiennent dans quelques dizaines de mètres — un
    véhicule à l'arrêt, mesuré : 31 m d'écart maximal au barycentre. À cette
    échelle, l'approximation plane est exacte au centimètre, et haversine serait
    une complication sans objet.

    L'écart de longitude est ramené dans [−180, 180]. Sans cela, −179,9° et
    +180° — onze kilomètres — donnaient 359,9° d'écart, soit le tour du globe
    par l'autre côté : mesuré à l'exécution, un rayon de 34 573 km sur une
    session à cheval sur l'antiméridien. Le barycentre, lui, avait déjà été
    corrigé ; c'est la distance AU barycentre qui restait à faire.
    """
    dlon = (lon2 - lon1 + 180.0) % 360.0 - 180.0
    dx = dlon * cos(radians((lat1 + lat2) / 2)) * 111_320.0
    dy = (lat2 - lat1) * 111_320.0
    return sqrt(dx * dx + dy * dy)


# La résolution de la série du routeur : un de ses points à moins de dix
# secondes d'un relevé en couvre l'instant.
_COUVERTURE_SERIE_S = 10.0


def _couvert(instant: datetime, couverts: list[datetime]) -> bool:
    """Un point de `couverts` (trié) tombe-t-il à moins de dix secondes ?"""
    i = bisect.bisect_left(couverts, instant)
    return any(
        abs((couverts[j] - instant).total_seconds()) <= _COUVERTURE_SERIE_S
        for j in (i - 1, i) if 0 <= j < len(couverts)
    )


def _resume_lien(
    lignes: list[dict], reseau=None, serie: tuple[list[str], list[float]] | None = None,
    comble: list[float] | None = None,
) -> SessionLink:
    """`serie` : la latence du lien selon la série du ROUTEUR (R-3.2) — les
    instants qu'elle couvre dans la fenêtre, et ses mesures. Un relevé ne compte
    que si aucun point de la série ne couvre son instant : sinon il n'en porte
    qu'une copie. La règle était « avant le tout premier point », et un jour
    sans série perdait ses mesures (C-9)."""
    couverts = sorted(datetime.fromisoformat(t) for t in serie[0]) if serie else []
    premiere = lignes[0]
    mb_up = mb_down = 0.0
    # Un débit jamais mesuré n'est pas un débit nul. Le Dome Pro Duo ne publie
    # aucun débit par WAN : sept cent vingt relevés à `None` donnaient « 0.0 Mo
    # montants · 0.0 Mo descendants », en gras, sur une fiche qui n'avait rien
    # mesuré. C'est la règle « jamais zéro à la place d'une absence », et la
    # fiche de session était le seul écran du dépôt à l'enfreindre.
    mesure_up = mesure_down = False
    rtts: list[float] = []
    # La latence du LIEN, hors tunnel (R-3.3) : une mesure par minute, d'un lien
    # connecté seulement — un point prêté par la série du routeur à un lien
    # tombé ne décrit rien.
    chemins: list[float] = []
    rsrps: list[float] = []
    cellules: set[int] = set()
    coupures = 0
    # Changements de cellule, et temps RÉELLEMENT observé sur ce lien. Les deux
    # font le taux de brassage : un compte brut ne se compare pas d'une session à
    # l'autre, alors qu'un taux horaire le fait — et c'est ainsi qu'on voit un
    # modem changer de cellule six fois plus souvent que son voisin au même
    # endroit et au même instant.
    #
    # Le dénominateur est le temps OBSERVÉ, plafonné comme l'intégration des
    # volumes : un trou de supervision ne peut porter aucun changement constaté,
    # et l'y compter ferait baisser le taux sans qu'aucun modem se soit calmé.
    bascules = 0
    secondes_vues = 0.0
    # La DERNIÈRE cellule connue du lien, et non celle du relevé d'avant : une
    # coupure s'intercale sans cellule, et un lien revenu sur une autre cellule
    # comptait « 0 changement » (revue, scénario 2). Un trou de supervision
    # l'efface : au-delà, on ne sait pas ce qui s'est passé.
    derniere_cellule: int | None = None

    precedent: dict | None = None
    for ligne in lignes:
        if ligne["rtt_ms"] is not None:
            rtts.append(float(ligne["rtt_ms"]))
        if (
            ligne["connected"] and ligne.get("path_latency_ms") is not None
            and not _couvert(datetime.fromisoformat(ligne["ts"]), couverts)
        ):
            chemins.append(float(ligne["path_latency_ms"]))
        if ligne["rsrp"] is not None:
            rsrps.append(float(ligne["rsrp"]))
        if ligne["cell_id"] is not None:
            cellules.add(int(ligne["cell_id"]))

        if precedent is not None:
            # Intégration du débit sur l'intervalle réellement écoulé. Le routeur
            # ne publie qu'un cumul depuis son installation — février 2022 ici —,
            # qui ne sait borner aucune session.
            ecart = (
                datetime.fromisoformat(ligne["ts"]) - datetime.fromisoformat(precedent["ts"])
            ).total_seconds()
            if ecart > _TROU_MAX_S:
                derniere_cellule = None
            if 0 < ecart <= _TROU_MAX_S:
                secondes_vues += ecart
                # kbit/s x s = kbit ; / 8 = ko ; / 1000 = Mo.
                if precedent["tx_kbps"] is not None:
                    mb_up += float(precedent["tx_kbps"]) * ecart / 8_000.0
                    mesure_up = True
                if precedent["rx_kbps"] is not None:
                    mb_down += float(precedent["rx_kbps"]) * ecart / 8_000.0
                    mesure_down = True

            # Les règles de `regles_coupure`, celles du journal et du brassage :
            # une désactivation n'est pas une coupure (C-20), un passage en
            # secours n'est pas une micro-coupure, et rien ne se compte À
            # TRAVERS un trou de supervision — la fiche comptait une coupure
            # entre un lien vu debout avant le trou et coupé après, que le
            # journal de la même session ne listait pas.
            if sont_voisins(precedent["ts"], ligne["ts"]) and (
                est_coupure(precedent["connected"], ligne["connected"], ligne.get("status_message"))
                # Rétablissement entre deux relevés : les deux montrent le lien
                # debout, seul l'uptime qui recule trahit la coupure. À 5,3 s de
                # période, c'est l'angle mort où se cachent les micro-coupures.
                or est_micro_coupure(
                    precedent["connected"], ligne["connected"],
                    precedent["uptime_s"], ligne["uptime_s"],
                    precedent.get("status_message"), ligne.get("status_message"),
                )
            ):
                coupures += 1
        if ligne["connected"] and ligne["cell_id"] is not None:
            cellule = int(ligne["cell_id"])
            if derniere_cellule is not None and cellule != derniere_cellule:
                bascules += 1
            derniere_cellule = cellule
        precedent = ligne

    if serie is not None:
        chemins.extend(serie[1])
    # Le RSRP des trous de supervision, pris à l'heure du routeur (R-3.1).
    rsrps.extend(comble or [])

    return SessionLink(
        wan_id=str(premiere["wan_id"]),
        kind=str(premiere["kind"] or "cellular"),
        operator=premiere["operator"],
        operators=_succession_operateurs(lignes, reseau or _identifiant_reseau(lignes)),
        samples=len(lignes),
        mb_up=round(mb_up, 1) if mesure_up else None,
        mb_down=round(mb_down, 1) if mesure_down else None,
        rtt_p50_ms=_percentile(rtts, 0.50),
        rtt_p90_ms=_percentile(rtts, 0.90),
        rtt_max_ms=round(max(rtts), 1) if rtts else None,
        rtt_samples=len(rtts),
        path_latency_samples=len(chemins),
        path_latency_p50_ms=_percentile(chemins, 0.50),
        path_latency_p90_ms=_percentile(chemins, 0.90),
        path_latency_max_ms=round(max(chemins), 1) if chemins else None,
        rsrp_median=_percentile(rsrps, 0.50),
        rsrp_filled_samples=len(comble or []),
        outages=coupures,
        cells=len(cellules),
        cell_changes=bascules,
        # Le taux n'est publié qu'au-delà d'un quart d'heure observé. En dessous,
        # une seule bascule produit un chiffre à deux chiffres qui ne décrit
        # rien : sur cinq minutes vues, un changement vaut « 12 par heure ».
        # `None`, et surtout pas zéro, sur un lien qui n'a JAMAIS publié de
        # cellule : un lien filaire ne peut pas en changer, et « 0,0 par heure »
        # s'y lirait comme une stabilité exemplaire alors que c'est une absence
        # de grandeur. C'est le zéro pour une absence, dans le calcul même qui
        # sert à comparer des modems entre eux.
        cell_changes_per_hour=(
            round(3600.0 * bascules / secondes_vues, 1)
            if cellules and secondes_vues >= 900.0
            else None
        ),
    )


def _rang(wan_id: str) -> tuple[int, int, str]:
    """Tri naturel des WAN, identique à celui des routes."""
    return (0, int(wan_id), "") if wan_id.isdigit() else (1, 0, wan_id)


def _total(parts: list[float | None]) -> float | None:
    """Somme qui reste `None` tant qu'aucun membre n'a été mesuré."""
    mesures = [p for p in parts if p is not None]
    return round(sum(mesures), 1) if mesures else None


def build_summary(
    session: Session,
    lignes: list[dict],
    fenetre: tuple[datetime, datetime] | None = None,
    series: dict[str, tuple[list[str], list[float]]] | None = None,
    combles: dict[str, list[float]] | None = None,
) -> SessionSummary:
    """Assemble la fiche d'une session à partir de ses relevés bruts.

    `fenetre` borne ce qui a RÉELLEMENT été lu, quand la session est trop longue
    pour être relue en entier. La fiche le porte alors jusqu'à l'écran : taire la
    fenêtre ferait lire un résumé partiel comme un résumé complet, et ce serait
    l'erreur la plus coûteuse de tout ce module — les volumes, les coupures et le
    barycentre y sont tous des totaux.
    """
    par_lien: dict[str, list[dict]] = {}
    positions: list[tuple[float, float]] = []
    stations: set[int] = set()

    for ligne in lignes:
        par_lien.setdefault(str(ligne["wan_id"]), []).append(ligne)
        if ligne["site_lat"] is not None and ligne["site_lon"] is not None:
            positions.append((float(ligne["site_lat"]), float(ligne["site_lon"])))
        # La STATION telle que `_split_eci` l'a décomposée à l'analyse, relue dans
        # sa colonne. Elle se refabriquait ici par `cell_id >> 8`, sans aucun des
        # deux garde-fous de cette fonction : un NCI 5G SA ne se décompose pas —
        # la longueur du champ gNB y est configurable par l'opérateur — et le
        # parseur s'en abstient explicitement. La fiche, elle, publiait une
        # station inventée. La règle est écrite une fois ; on la LIT ici.
        #
        # Comparaison arithmétique entre identifiants rapportés par les modems :
        # elle ne nomme ni ne localise aucune antenne.
        if ligne["enb_id"] is not None:
            stations.add(int(ligne["enb_id"]))

    # Ordre naturel et non lexicographique : un châssis expose plus de neuf WAN
    # (onze sur un MAX HD4 MBX), et trier les chaînes listait 1, 10, 11, 2, 3…
    reseau = _identifiant_reseau(lignes)
    liens = [
        _resume_lien(l, reseau, (series or {}).get(w), (combles or {}).get(w))
        for w, l in sorted(par_lien.items(), key=lambda kv: _rang(kv[0]))
    ]

    lat, lon, rayon = _barycentre(positions)

    return SessionSummary(
        session=session,
        links=liens,
        mb_up=_total([l.mb_up for l in liens]),
        mb_down=_total([l.mb_down for l in liens]),
        lat=lat,
        lon=lon,
        radius_m=rayon,
        cells=_latence_par_cellule(lignes),
        availability=_disponibilite(lignes),
        diversity=_diversite(lignes),
        # Aucune station identifiable (5G SA, lignes sans `enb_id`) : le nombre
        # de sites est inconnu, pas nul.
        sites=len(stations) or None,
        partial=fenetre is not None,
        window_start=fenetre[0] if fenetre else None,
        window_end=fenetre[1] if fenetre else None,
    )
