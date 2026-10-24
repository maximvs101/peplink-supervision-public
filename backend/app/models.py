"""Modèles de données partagés (API + stockage + clients)."""

from __future__ import annotations

from datetime import datetime, timezone

from typing import Annotated

from pydantic import BaseModel, Field, PlainSerializer, computed_field


def nommer(valeur: int | None) -> str | None:
    """Publie un identifiant comme un NOM, jamais comme une quantité.

    Cell ID, station, secteur, TAC, PCI, canal : le backend les manipule en
    entiers parce que l'ECI se décompose par masque de bits et que la base les
    indexe ainsi. Mais sur le fil ce sont des noms. « 25 735 436 » avec ses
    séparateurs de milliers ne désigne plus la cellule 25735436 — et côté écran
    rien n'empêchait de passer un entier à `fmtInt`. Une chaîne, si : le type
    la refuse.
    """
    return None if valeur is None else str(valeur)


# Le type d'un identifiant : entier en mémoire, nom sur le fil. Poser le type
# suffit — aucun sérialiseur à déclarer champ par champ, donc aucun champ à
# oublier.
Nom = Annotated[int | None, PlainSerializer(nommer, return_type=str | None)]
NomRequis = Annotated[int, PlainSerializer(nommer, return_type=str)]


def _horloge() -> datetime:
    return datetime.now(timezone.utc)


# L'horloge de l'application, en UN endroit. Tout ce qui date un relevé, une
# session ou une fenêtre passe par `utcnow()` — et `utcnow()` passe par
# `_horloge`, que le rejeu d'une capture remplace par une horloge posée : deux
# exportations des mêmes captures sont alors identiques à l'octet près, sur
# n'importe quelle machine. Sans cela, les millisecondes entre deux cycles
# faisaient différer un Mac et la machine Windows de GitHub.
def en_secours(message: object) -> bool:
    """Le routeur dit-il ce lien EN SECOURS (« Standby ») ?

    Passer un WAN en priorité de secours remet son uptime à 0 sans le couper :
    l'uptime qui recule, seul témoin des micro-coupures, y voyait une panne. Une
    manœuvre de l'exploitant n'en est pas une. Même prédicat en SQL : voir
    les conditions `standby%` de `storage/db.py`.
    """
    return isinstance(message, str) and message.strip().lower().startswith("standby")


def utcnow() -> datetime:
    return _horloge()


class BandMeasurement(BaseModel):
    """Mesures d'une porteuse (bande) active.

    Les modems agrègent plusieurs porteuses simultanément (carrier aggregation) : en 5G NSA
    par exemple, une porteuse 5G n78 pour les données et une ou plusieurs ancres LTE. Chaque
    porteuse a ses propres niveaux, d'où cette liste plutôt qu'un jeu de valeurs unique.
    """

    rat: str  # "5G", "LTE"
    band: str  # code court : "n78", "B3"
    label: str | None = None  # libellé complet renvoyé par le routeur
    channel: Nom = None  # EARFCN / NR-ARFCN
    pci: Nom = None
    channel_width: str | None = None  # ex : "90 MHz"

    rsrp: float | None = None
    rsrq: float | None = None
    sinr: float | None = None
    rssi: float | None = None



class WifiLink(BaseModel):
    """Lien Wi-Fi WAN (point d'accès amont : Starlink, hotspot, Wi-Fi de site…).

    Un routeur peut agréger un lien Wi-Fi à ses modems cellulaires ; il fait partie de
    la liaison globale et mérite d'être supervisé au même titre.
    """

    ssid: str | None = None
    bssid: str | None = None
    channel: Nom = None
    band: str | None = None  # "2,4 GHz" / "5 GHz", déduit du canal
    signal_dbm: float | None = None  # force du signal reçu
    signal_level: int | None = None  # 0..5



class BandwidthAllowance(BaseModel):
    """Le plafond de volume que le routeur applique, et ce qu'il fera au-delà.

    Pour un véhicule de reportage, c'est le premier risque d'exploitation après
    la couverture — et le routeur publie tout dans une charge utile déjà
    téléchargée à chaque cycle, que le parseur jetait.

    `action` porte ce qui arrivera au dépassement. Vérifié sur le matériel :
    `["email", "restrict"]` — le routeur COUPERA la SIM, ce qui n'est pas un
    avertissement mais une panne programmée.

    Les trois champs de détail ne sont publiés QUE lorsque `enabled` est vrai :
    un plafond non configuré rend `None`, jamais `0`. Et le bloc entier peut
    manquer sur un WAN — il manque sur le port basculé en LAN du matériel de
    référence.
    """

    enabled: bool = False
    monthly_allowance_mb: float | None = None
    # Jour du mois où le compteur repart, 1 à 31.
    reset_day: int | None = None
    # Vu : "email", "restrict". La liste n'est pas exhaustive.
    actions: list[str] = []


class SimSlot(BaseModel):
    """Un emplacement SIM d'un modem, et ce qu'on en sait.

    Le châssis en déclare huit — deux par modem — et l'interface n'en montrait
    aucun : elle annonçait « 8 emplacements SIM » sans jamais dire s'ils étaient
    occupés. Or une seconde carte présente EST un secours, et son absence est
    l'inverse.

    L'emplacement ACTIF est publié, contrairement à ce que cette docstring a
    longtemps affirmé pour justifier de ne pas le lire. `cellular.sim.<n>.active`
    existe sur les DEUX matériels de référence et sur les deux emplacements ; sur
    le MAX HD4 MBX, le modem du WAN 5 travaille sur sa carte 2. L'exploitant ne
    savait pas laquelle de ses deux cartes servait, et le code s'en abstenait au
    nom d'une observation fausse.

    Aucun identifiant ne remonte ici. L'IMSI et l'ICCID sont publiés par
    l'équipement à côté de ces champs ; ils n'apprennent rien à la supervision et
    rendraient toute capture d'écran impartageable.
    """

    slot: str
    # `bool | None`, comme `carrier_aggregation` : une clé absente n'est pas
    # une carte absente. `bool(bloc.get(...))` faisait d'un emplacement dont le
    # firmware ne dit rien un emplacement « vide », affirmé à l'écran.
    card_detected: bool | None = None
    apn: str | None = None
    # L'emplacement que le modem emploie en ce moment.
    active: bool | None = None
    # Plafond de volume propre à CETTE carte. Le routeur en publie aussi un au
    # niveau du WAN : les deux coexistent et ne se confondent pas.
    allowance: BandwidthAllowance | None = None


# Les réseaux mobiles nationaux, par leur code MCC-MNC (R-2.6). Table FERMÉE :
# le modem du Dome publie « Mobile S Mobile » pour 208-10, qui est SFR ; un code
# absent d'ici garde le libellé du modem plutôt qu'un nom deviné.
_NOMS_PLMN: dict[str, str] = {
    "208-01": "Orange",
    "208-10": "SFR",
    "208-15": "Free",
    "208-20": "Bouygues",
}


def nom_du_reseau(libelle: str | None, plmn: str | None) -> str | None:
    """Le nom à AFFICHER : celui du code réseau quand il est connu, sinon le
    libellé du modem. Le libellé reste la donnée enregistrée — le rapprochement
    des relevés publiés sans code en dépend (R-3.4)."""
    return _NOMS_PLMN.get(plmn or "") or libelle


class WanRadioStatus(BaseModel):
    """Instantané d'un WAN radio : modem cellulaire ou lien Wi-Fi amont."""

    # "cellular" (modem), "wifi" (point d'accès amont) ou "ethernet" (lien filaire :
    # fibre, xDSL, réseau du site). Un lien filaire n'a aucune métrique radio, mais
    # il participe à la liaison agrégée et en porte souvent l'essentiel du débit :
    # l'ignorer ferait dire à la supervision que tout va bien alors que le lien
    # principal est tombé.
    kind: str = "cellular"

    wan_id: str
    wan_name: str
    connected: bool = True

    operator: str | None = None
    # Le code réseau, TEL QUE LE ROUTEUR L'ÉCRIT : « 208-01 ».
    #
    # Il vivait en deux entiers, `mcc` et `mnc`, remplis par `_to_int`. Or le MNC
    # est un IDENTIFIANT à deux ou trois chiffres où le zéro de tête compte :
    # « 01 » devenait 1, et « 001 » — un MNC nord-américain à trois chiffres —
    # devenait le même 1. Deux réseaux distincts, un seul nombre. La docstring du
    # module le disait déjà (« `mcc`/`mnc` sont des chaînes ("208", "01") ») ;
    # le code convertissait quand même. On n'additionne jamais deux MNC : ce
    # n'est pas une quantité.
    plmn: str | None = None

    rat: str | None = None  # techno radio : "LTE", "LTE-A", "5G NSA", "5G SA"
    band: str | None = None  # ex : "B7", "B20", "n78"
    earfcn: Nom = None
    pci: Nom = None
    cell_id: Nom = None  # ECI (LTE) / NCI (5G)
    tac: Nom = None  # TAC (LTE) ou LAC
    # L'ECI n'est pas atomique : ses 20 bits de poids fort identifient la station
    # de base, les 8 de poids faible la cellule (secteur x porteuse) sur celle-ci.
    # Deux SIM partageant `enb_id` sont sur le MÊME site physique — donc sans
    # diversité de trajet, ce que la seule lecture du Cell ID ne montre pas.
    enb_id: Nom = None
    sector_id: Nom = None

    # Niveaux de la porteuse principale (celle qui porte les données).
    # Le détail de toutes les porteuses agrégées est dans `bands`.
    rsrp: float | None = None  # dBm
    rsrq: float | None = None  # dB
    sinr: float | None = None  # dB
    rssi: float | None = None  # dBm
    signal_bars: int | None = None  # 0..5

    bands: list[BandMeasurement] = []
    # Emplacements SIM de CE modem. Vide sur un lien qui n'en a pas — filaire,
    # Wi-Fi — et sur un modem dont le routeur ne publie rien.
    sim_slots: list[SimSlot] = []

    # Renseigné uniquement quand kind == "wifi".
    wifi: WifiLink | None = None

    tx_kbps: float | None = None
    rx_kbps: float | None = None

    # --- ce que le modem est, et dans quel réseau il travaille ---
    #
    # Quatre champs de la même charge utile, téléchargée à chaque cycle et
    # jusqu'ici jetée.
    #
    # `roaming_status` : le libellé du routeur ("home"), pas son code. Le code
    # vaut 1 sur les deux matériels de référence et aucune valeur d'itinérance
    # n'a été observée : traduire « code ≠ 1 » en « en itinérance » serait une
    # règle non calibrée. En reportage à l'étranger, l'itinérance change la
    # facture d'un ordre de grandeur.
    roaming_status: str | None = None
    # Fondeur, modèle et version du modem. Permet de DIRE pourquoi un champ est
    # vide : le projet sait que le Telit LM960 ne remonte pas le PCI, l'écran ne
    # le disait nulle part. Les formats de version sont incomparables entre
    # fondeurs — chaîne opaque, jamais de comparaison.
    modem_vendor: str | None = None
    modem_model: str | None = None
    modem_firmware: str | None = None
    # MTU de CE lien. Mesuré sur le matériel : 1428 sur les SIM, 1440 sur le
    # WAN 2, 1500 ailleurs. Une MTU hétérogène entre liens agrégés est une cause
    # classique de fragmentation.
    mtu: int | None = None
    # `bool | None` et non `bool = False` : sur un matériel qui ne le publie pas,
    # `False` affirmerait l'absence d'agrégation au lieu de l'ignorance.
    carrier_aggregation: bool | None = None
    # "Auto (Generic)" sur le MBX, absent du Dome Pro Duo. Révèle un verrouillage
    # de bande manuel.
    network_mode: str | None = None
    # Plafond de volume au niveau du WAN, distinct de celui de chaque carte.
    allowance: BandwidthAllowance | None = None
    # Volumes de la PÉRIODE EN COURS, en mégaoctets, tels que le routeur les
    # compte. Ils arrivent dans `status.traffic`, dont le parseur ne lisait que
    # le bloc de débit instantané.
    #
    # « Période en cours » et non « ce mois-ci » : la réponse ne porte AUCUN
    # marqueur de période — ni date de début, ni numéro de mois. Que le compteur
    # s'aligne sur le mois calendaire ou sur `allowance.reset_day` reste à
    # vérifier par un relevé à cheval sur un premier du mois. L'écran ne nommera
    # donc pas la fenêtre tant qu'elle n'est pas établie.
    period_rx_mb: float | None = None
    period_tx_mb: float | None = None

    # Latence du CHEMIN, en millisecondes — celle du lien lui-même, mesurée par
    # le routeur hors de tout tunnel.
    #
    # À ne pas confondre avec `tunnel.rtt_ms`, qui est l'aller-retour vers le
    # concentrateur : celui-ci n'existe que pour un lien DANS le bond, sur un
    # châssis qui porte un profil SpeedFusion. Un lien filaire, un lien écarté,
    # un équipement sans SpeedFusion n'avaient jusqu'ici aucune latence du tout.
    #
    # Les deux se ressemblent sans mesurer la même chose, et l'écart ne doit pas
    # être présenté comme un « coût du tunnel ». Mesuré sur quarante minutes, dix
    # relevés appariés par lien : le tunnel est plus lent de 2 à 5 ms sur quatre
    # liens, et PLUS RAPIDE de 4 ms sur le cinquième. Un surcoût d'agrégation
    # serait positif partout. Les deux sondes ne visent simplement pas la même
    # destination — l'une la cible du routeur, l'autre le concentrateur — et
    # c'est la comparaison de chaque grandeur À ELLE-MÊME dans le temps qui
    # apprend quelque chose, pas leur différence à un instant.
    #
    # `None` sur les cycles où l'endpoint n'a pas été lu : il l'est une fois par
    # minute, et une absence de mesure n'est pas une mesure. Le lecteur qui
    # trace une courbe y verra un point par minute, ce qui est exact.
    path_latency_ms: float | None = None
    # La DERNIÈRE mesure de latence du lien, avec l'heure du relevé qui l'a
    # portée — pour le tableau de bord seul, jamais écrite en base. Sans elle,
    # la colonne affichait « — » cinq cycles sur six. Ce n'est pas un chiffre
    # recopié : il voyage avec son heure et tombe au-delà de 90 s, ou dès que
    # le lien coupe.
    path_latency_recent_ms: float | None = None
    path_latency_at: datetime | None = None
    # Température du MODEM de ce WAN cellulaire, en °C, et l'heure de sa
    # lecture — lue une fois par minute en POST, tenue 90 s au plus. Pas en
    # base (pas d'historique), et pas de seuil : le routeur n'en publie aucun
    # (R-1.11). Distincte de `DeviceInfo.temperature_c`, celle du CHÂSSIS.
    modem_temperature_c: float | None = None
    modem_temperature_at: datetime | None = None

    @computed_field  # type: ignore[prop-decorator]
    @property
    def operator_name(self) -> str | None:
        """L'opérateur tel qu'on le nomme : par son code réseau (R-2.6)."""
        return nom_du_reseau(self.operator, self.plmn)

    ip: str | None = None  # adresse attribuée au WAN
    # État de ce lien dans le tunnel SpeedFusion, quand un tunnel est monté.
    # Un lien connecté mais absent du bond ne porte aucun flux.
    tunnel: TunnelLink | None = None
    # Priorité de la règle sortante : plusieurs WAN de même priorité se partagent
    # le trafic, une priorité plus élevée n'est qu'un secours. Absente sur un WAN
    # désactivé.
    priority: int | None = None
    uptime_s: int | None = None
    status_message: str | None = None  # ex : "Connected to Orange F"
    # Désactivé par l'exploitant pendant la session (`enable: false`). Un lien
    # désactivé n'est pas une panne : il reste nommé, sans mesure, et ne pèse
    # pas sur le verdict. Sans ce drapeau, il sortait de la table et le journal
    # écrivait « absent du relevé », comme pour une SIM évanouie.
    disabled: bool = False


# Le message du relevé d'adieu d'un WAN désactivé. La base n'a pas de colonne
# `disabled` : c'est ce libellé, écrit par le collecteur et reconnu par la bande
# (C-20), qui distingue une désactivation d'une panne. Une seule écriture.
STATUT_DESACTIVE = "désactivé"


class GnssFix(BaseModel):
    """Une position, et de quoi savoir si elle vaut quelque chose.

    `get_location` ne rendait qu'un couple de coordonnées. Le routeur publie
    quatre champs de plus dans la même réponse, lus à chaque cycle et jetés — et
    ce sont eux qui disent si le point est encore VRAI.

    `age_s` est l'âge de la fixation TEL QUE LE RÉCEPTEUR LE COMPTE. Le poller
    avait dû se rabattre sur l'âge de sa propre lecture, ce qui ne mesure que la
    liaison entre lui et le routeur : un récepteur qui a perdu le ciel continue
    de republier son dernier point, et la lecture, elle, reste fraîche. C'est
    cette valeur-ci qui fait foi.

    `hdop` est la dilution de précision horizontale — sans dimension, plus petit
    vaut mieux. Sous auvent ou en parking couvert, le point dérive et fausse le
    regroupement par halte.

    `speed` est parsé mais NON AFFICHÉ : son unité n'est documentée nulle part,
    et les deux matériels de référence étaient à l'arrêt (0,0067 et 0,0062) —
    du bruit de mesure, qui ne permet de trancher ni entre m/s, ni km/h, ni
    nœuds. À calibrer une fois en roulant avant d'en faire quoi que ce soit.
    L'altitude reste dehors : combinée aux dilutions, elle affine la
    localisation d'un véhicule que ce projet ne situe qu'approximativement.
    """

    lat: float
    lon: float
    # Âge de la fixation, en secondes, compté par le récepteur.
    age_s: int | None = None
    hdop: float | None = None
    # Unité non documentée. Lu, jamais affiché. Voir la docstring.
    speed: float | None = None


class PowerSource(BaseModel):
    """Alimentation déclarée par l'équipement (double DC sur les châssis MBX)."""

    name: str
    connected: bool


class DeviceInfo(BaseModel):
    """Identité et santé de l'équipement supervisé.

    Lue sur `status.system.info`. Le **numéro de série** est la seule clé stable :
    l'adresse IP dépend du réseau où l'on se branche, et le nom est modifiable
    depuis l'administration web.

    Tous les champs sont optionnels : les modèles ne remontent pas les mêmes
    informations (le Dome Pro Duo n'a ni double alimentation ni relevé de
    consommation), et l'endpoint lui-même n'est pas garanti sur toutes les gammes.
    """

    model: str | None = None  # "Pepwave MAX HD4 MBX"
    product_code: str | None = None  # "MAX-HD4-MBX-GLTE-G"
    serial: str | None = None  # clé d'identité stable
    firmware: str | None = None  # "8.5.4 build 6253"
    hardware_revision: str | None = None
    name: str | None = None  # nom donné dans l'administration

    # Ancienneté du CHÂSSIS, telle que le routeur la publie. L'outil l'approchait
    # par le maximum des `uptime` de liens — et c'est de cette approximation que
    # venait le défaut des sessions scindées : le maximum retombe dès que le lien
    # qui le portait quitte le relevé. La valeur exacte arrivait dans une charge
    # utile déjà téléchargée, sous `uptime.second`.
    uptime_s: int | None = None
    cpu_load_percent: float | None = None
    power_watts: float | None = None
    power_sources: list[PowerSource] = []
    mac_lan: str | None = None  # identifie l'équipement dans la table ARP

    # --- ce que le châssis dit de lui-même, et qui était jeté ---
    #
    # Tout arrive dans `status.system.info`, lue un cycle sur douze.

    # Température du châssis, en degrés Celsius. Un véhicule au soleil en été,
    # un boîtier en soute : c'est le seul indicateur de risque thermique dont
    # l'outil dispose. ABSENT du Dome Pro Duo — le champ doit disparaître
    # proprement sur ce modèle, pas s'afficher à zéro.
    #
    # Le `threshold` publié à côté (20,0 pour une mesure à 43,4) n'est PAS un
    # seuil d'alarme haute : il est inférieur à la température normale de
    # fonctionnement. Sens inconnu, donc non lu.
    temperature_c: float | None = None

    # Charge de l'alimentation, en pourcentage de son budget. La tension et le
    # courant sont publiés à côté et restent dehors : leurs unités se déduisent
    # par cohérence interne — 54 × 360 = 19,4 W pour un `value` de 19 W, donc
    # des volts et des milliampères — mais cette déduction repose sur une seule
    # mesure d'un seul matériel, et une tension affichée à tort ferait
    # diagnostiquer une panne d'alimentation qui n'existe pas.
    power_load_percent: float | None = None

    # Contrat de support. Expiré sur le matériel de référence depuis 2023, ce
    # que rien n'annonçait.
    care_plan: str | None = None
    care_plan_active: bool | None = None

    # Version du protocole de tunnel, distincte du firmware : c'est ELLE qui
    # gouverne la compatibilité avec le concentrateur.
    pepvpn_version: str | None = None

    # Fuseau déclaré par le routeur. Sert à lire son horloge, jamais à dater un
    # relevé : ce projet ne date qu'en UTC, de la scrutation au stockage.
    timezone: str | None = None
    # Horloge du routeur MOINS celle du poste, en secondes, mesurée à la lecture
    # de cette fiche (R-3.9). ~2 s sur le Dome, synchronisé : on ne redate rien.
    # Au-delà de 60 s, l'historique à 10 s du routeur est écarté et son journal
    # n'est plus lu — et une alerte le dit. `None` : horloge non publiée.
    clock_offset_s: float | None = None

    # Une configuration est en cours d'application. Explique un transitoire
    # qu'on prendrait sinon pour une panne. `None` : le firmware ne le dit pas.
    config_activating: bool | None = None

    # Un tunnel d'assistance est ouvert vers l'éditeur. Fait de SÉCURITÉ : un
    # `true` inattendu mérite d'être vu.
    remote_assistance: bool | None = None


class LanNetwork(BaseModel):
    """Un réseau LAN du châssis, tel que `status.port` le nomme.

    Le nom et le numéro de VLAN, rien de plus : les adresses, les masques et
    le rattachement des ports ou des SSID vivent dans `config.lan`, famille que
    l'outil s'interdit. Ce que ça sert : savoir, depuis l'écran, que le HD2 du
    19 septembre portait un LAN natif et un VLAN « WIFI Range » — la question
    « c'est quoi cette adresse en 10.10.1.1 ? » s'est posée, et la réponse était
    dans une charge utile déjà téléchargée.
    """

    # Le numéro de VLAN est un nom, pas une quantité : 0 est le réseau natif.
    vlan_id: NomRequis
    name: str


class DeviceCapabilities(BaseModel):
    """Ce que l'équipement déclare savoir faire, plus ce qu'on a pu vérifier.

    L'essentiel vient de `info.capability`, où le routeur publie ses propres
    drapeaux de fonctionnalités et l'inventaire de ses WAN. `per_wan_throughput`
    n'en fait pas partie : il est constaté à l'usage, selon que `status.traffic`
    réponde ou non.

    Sert à n'afficher que ce que CET équipement sait produire — une donnée absente
    ne doit jamais être présentée comme une donnée mauvaise.
    """

    gps: bool = False
    pepvpn: bool = False  # SpeedFusion

    # --- la licence SpeedFusion Cloud, lue à l'appairage et jamais regardée ---
    #
    # `licensed` et `suspended` valent TOUS DEUX vrai sur le matériel de
    # référence : une licence existe, et le service est inutilisable. Un écran
    # qui n'afficherait que le premier annoncerait « sous licence » d'un service
    # suspendu — les deux booléens ne se lisent qu'ensemble.
    sfc_licensed: bool | None = None
    sfc_suspended: bool | None = None
    # Plafond de débit imposé par la licence, en Mbit/s. 200 sur le matériel de
    # référence, et cette limite n'apparaissait nulle part.
    sfc_throughput_limit_mbps: float | None = None
    # Échéance de la licence et quota restant sur quota total, en Mo (R-1.5).
    # Un quota voyage avec son total : « reste 3 To » ne dit rien seul.
    sfc_expires_at: datetime | None = None
    sfc_quota_remaining_mb: float | None = None
    sfc_quota_limit_mb: float | None = None
    # Profils SpeedFusion ACTIFS au plus. C'est cette limite-là qui contraint
    # l'exploitation — 3 sur le matériel de référence —, pas les 53 profils
    # configurables publiés à côté.
    pepvpn_max_active_profiles: int | None = None

    # Inventaire des WAN réellement disponibles : identifiant -> type
    # ("cellular", "ethernet", "wifi"). Renseigné même pour les WAN désactivés,
    # contrairement à `status.wan.connection`.
    #
    # Deux corrections par rapport à l'inventaire brut de l'équipement, sans
    # lesquelles le décompte ne correspond pas à ce que voit l'exploitant :
    #  - les ports WAN reconfigurés en LAN n'en font plus partie ;
    #  - les emplacements de modem USB sont comptés à part, ce sont des
    #    logements d'accessoire et non des liens du châssis.
    wan_types: dict[str, str] = {}
    # Ports WAN du châssis basculés en LAN (`asLan`), donc indisponibles.
    lan_repurposed: list[str] = []
    # Les réseaux LAN nommés par `status.port`, VLAN natif compris. Vide quand
    # l'endpoint n'a pas été lu — une fiche sondée — et ce vide n'affirme rien.
    lan_networks: list[LanNetwork] = []
    # Emplacements pour clé ou modem USB.
    usb_modem_slots: int = 0
    # Emplacements SIM : les modems Peplink sont souvent bi-SIM.
    sim_slots: int = 0

    # Constaté à l'exécution plutôt que déclaré.
    per_wan_throughput: bool = False

    # La fiche a-t-elle pu être conclue ? Faux dès qu'une sonde n'a pas su
    # trancher — un délai dépassé sur `info.location` pendant qu'un routeur finit
    # de démarrer ne dit pas qu'il n'a pas de GPS. Une capacité NIÉE ne se
    # redemandait jamais : `gps=False` éteignait `get_location()` pour la vie du
    # processus, tous les relevés portaient alors les coordonnées fixes du
    # fichier de flotte, et le barycentre de session ne décrivait plus rien —
    # sans que rien ne le signale. Tant que ce drapeau est vrai, la fiche est
    # rejouée avec le rafraîchissement d'identité.
    provisional: bool = False

    # "declared" : lu dans info.capability, complet et fiable.
    # "probed"   : reconstitué en sondant les endpoints accessibles, car
    #              info.capability n'est pas couvert par le scope api.read-only.
    #              L'inventaire des WAN se limite alors aux WAN actifs.
    source: str = "declared"

    @property
    def cellular_wans(self) -> int:
        return sum(1 for t in self.wan_types.values() if t == "cellular")


class DiscoveredDevice(BaseModel):
    """Équipement Peplink repéré sur le réseau, avant tout appairage.

    Rien ici ne suppose d'identifiants : ce sont les seules informations
    obtenables sans session. Le modèle, le numéro de série et les capacités
    exigent une authentification et n'arrivent qu'après appairage.
    """

    host: str
    mac: str | None = None
    firmware: str | None = None
    certificate_name: str | None = None
    # L'API locale répond-elle ? C'est la preuve, le reste n'est qu'indice.
    api_reachable: bool = False
    # Ce qui a permis de l'identifier, en clair, pour que l'utilisateur juge.
    evidence: list[str] = []
    # Renseigné par l'API : cet équipement est-il déjà dans la flotte ?
    already_configured: bool = False


class DeviceStatus(BaseModel):
    """État d'un équipement supervisé, tel que l'interface le présente."""

    id: str
    name: str
    host: str | None = None
    serial: str | None = None
    mode: str = "mock"
    source: str = "yaml"

    # "en attente" · "connecté" · "identifiants refusés" · "injoignable"
    # · "erreur de configuration". Distinguer ces états dit à l'utilisateur s'il
    # doit corriger un mot de passe ou vérifier un raccordement.
    status: str = "en attente"
    error: str | None = None

    device: DeviceInfo | None = None


# La sentinelle que le CLIENT pose sur chaque lien quand aucun pair ne répond.
# Ce n'est pas un état du routeur ; le nom est le nôtre, et il vit ici, à côté
# du champ qui le classe.
PEER_DOWN = "PEER_DOWN"


class LanClients(BaseModel):
    """Combien d'appareils dépendent de la liaison — des NOMBRES, rien d'autre.

    `status.client` publie nom, MAC, IP, bail, ESSID et BSSID de chaque client :
    rien de cela ne sort du parseur. `includes_this_host` : le poste qui
    supervise est-il parmi les actifs ? Comparé en mémoire, jamais publié ;
    `None` quand son adresse n'a pas pu être déterminée (R-1.10).
    """

    active: int
    wireless: int
    wired: int
    includes_this_host: bool | None = None
    # L'instant de la lecture : le décompte n'avait pas d'âge, et « 12
    # appareils actifs » lu le matin restait affiché le soir (C-33).
    read_at: datetime | None = None


class BondProfile(BaseModel):
    """Un profil SpeedFusion, tel que `status.pepvpn?infoType=profile` le publie.

    Le nom, l'état et le nombre de pairs connectés — rien d'autre. Le `siteId`
    de la réponse et le `username` des pairs ressemblent à des numéros de
    série : ils ne sortent pas du parseur. `cloud` : profil SpeedFusion Cloud
    (`type: "SFC Client"`), le seul que la licence Cloud conditionne.
    """

    id: str
    name: str | None = None
    status: str | None = None
    peers_connected: int | None = None
    cloud: bool = False


class TunnelLink(BaseModel):
    """État d'un lien vu de l'intérieur du tunnel SpeedFusion.

    C'est le tunnel qui porte le flux, pas les WAN pris isolément. Un lien peut
    être parfaitement « connecté » côté WAN et **écarté du bond** : son état ici
    n'est alors pas `ACTIVE`, et il ne transporte rien.

    Les grandeurs qui font geler une image en bonding — latence, perte, désordre
    de paquets — n'existent qu'ici. Aucune ne figure dans `status.wan.connection`.
    """

    # "ACTIVE" (le lien participe), "WAN_DOWN", "WAN_DISABLED", "COLD"…
    # Plus "PEER_DOWN", sentinelle que le routeur ne renvoie jamais : elle est
    # posée par le client quand des profils SpeedFusion existent mais qu'aucun
    # pair n'est joignable. Sans elle, un bond entièrement tombé ne produisait
    # aucun objet tunnel, et l'interface — qui ne compte hors du bond que les
    # liens qui en portent un — annonçait « Liaison OK » en vert alors qu'un
    # seul lien écarté suffit à la faire passer en « dégradée ».
    state: str | None = None
    # Le MOT d'état du pair, tel que le routeur le publie, sous `PEER_DOWN` :
    # `START` dit « le routeur tente de le joindre ». Posé seulement quand
    # tous les pairs déclarés portent le même ; sinon `None` (R-1.5).
    peer_state: str | None = None
    # Aller-retour vers le concentrateur, en millisecondes.
    rtt_ms: float | None = None

    # Taux mesurés entre deux relevés — les compteurs du routeur sont cumulés
    # depuis l'établissement du tunnel, donc inexploitables tels quels.
    #
    # Les deux sens sont publiés séparément parce que leur somme dilue le sens
    # dégradé : mesuré, 5 000 paquets perdus sur 100 000 émis — soit 4,76 %
    # dans ce sens-là — noyés dans
    # 900 000 paquets reçus sans perte donnaient 0,50 % affiché. Les deux champs
    # de synthèse valent donc le MAXIMUM des deux sens, c'est-à-dire le pire cas.
    loss_percent: float | None = None
    out_of_order_percent: float | None = None
    loss_percent_up: float | None = None
    loss_percent_down: float | None = None
    out_of_order_percent_up: float | None = None
    out_of_order_percent_down: float | None = None

    # DÉNOMINATEUR des taux ci-dessus : nombre de paquets réellement comptés sur
    # l'intervalle, par sens (transportés + perdus). Il était calculé pour la
    # division puis jeté, si bien qu'un taux arrivait à l'écran sans rien qui
    # permette de le relire. Sur un tunnel au repos, seul le keepalive passe entre
    # deux relevés : l'interface annonçait « 0,00 % de perte » sur un dénominateur
    # de TROIS paquets. Le matériel réel le confirme — sur 11 937 relevés, la seule
    # valeur de perte non nulle jamais enregistrée vaut 33,33 %, soit un paquet
    # sur trois.
    #
    # Le seuil des 200 paquets, en dessous duquel aucun taux n'est affiché, n'est
    # PAS appliqué ici : c'est une règle de rendu, elle peut changer sans que la
    # mesure change, et l'appliquer au backend la graverait aussi dans l'historique
    # en base, où elle survivrait à toute décision ultérieure. Le backend publie
    # donc le taux brut ET de quoi le refuser ; l'interface tranche.
    #
    # Zéro EST une mesure : « rien n'est passé pendant l'intervalle ». None est une
    # absence de mesure : premier relevé, tunnel remonté, ou lien sorti du bond.
    packets_up: int | None = None
    packets_down: int | None = None
    # Débit RÉELLEMENT porté par le bond sur ce lien, par sens, en kbit/s.
    # `WanRadioStatus.tx_kbps` mesure autre chose : tout ce qui passe sur
    # l'interface WAN, tunnel et hors tunnel confondus. Ces deux-ci ne comptent
    # que l'agrégation, et ils viennent d'une charge utile déjà téléchargée.
    tx_kbps: float | None = None
    rx_kbps: float | None = None
    # Redondance de SpeedFusion sur l'intervalle : paquets de correction émis
    # (`fec` + `redundant`), et paquets que cette correction a permis de
    # reconstituer à la réception (`recover`). De la capacité dépensée à
    # réparer plutôt qu'à transporter — invisible jusqu'ici.
    repair_sent: int | None = None
    repair_used: int | None = None

    # Seuil de latence du bond, tel que le profil SpeedFusion le configure, et
    # dépassement constaté. Le firmware arrête le trafic sur un lien qui dépasse
    # de cette valeur celui de latence la plus basse — et rien dans `state` ne le
    # dit : sur les 113 relevés de la base qui franchissent le seuil, il vaut
    # `ACTIVE` cent fois sur cent, alors que les intervalles sans un seul paquet
    # montant y sont quatre fois et demie plus fréquents.
    #
    # `in_bond` n'est PAS corrigé par ce constat : il porte ce que l'équipement
    # déclare, et le remplacer par une déduction ferait dire à l'écran une chose
    # que le routeur ne dit pas. Le dépassement est un fait de plus.
    cutoff_ms: float | None = None
    beyond_cutoff: bool = False

    @computed_field
    @property
    def cause_scope(self) -> str | None:
        """À QUI la cause s'adresse quand le lien est hors du bond.

        `PEER_DOWN` n'est pas un mot du routeur : c'est le client qui le pose
        sur chaque lien quand aucun pair ne répond — un fait de niveau BOND,
        éparpillé sur N liens. Trois consommateurs le recollaient chacun à sa
        manière : un préfixe `PEER_` dans les alertes, une égalité stricte dans
        la fiche de lien, l'unanimité des états dans le bandeau. La prochaine
        cause de niveau bond — concentrateur sans licence, profil désactivé —
        aurait dû être ajoutée aux trois, et un état du routeur qui commencerait
        par `PEER_` aurait été pris pour un fait de bond.

        Classée ici, une fois, à l'endroit où la sentinelle naît : « pair » pour
        elle, « lien » pour tout état publié par le routeur qui écarte le lien,
        rien quand le lien porte ou que son état est inconnu.
        """
        if self.state is None or self.in_bond:
            return None
        return "pair" if self.state == PEER_DOWN else "lien"

    @computed_field
    @property
    def in_bond(self) -> bool:
        """Ce lien porte-t-il réellement le flux ?

        Sérialisé, et non calculé côté interface : les valeurs d'état varient
        d'un firmware à l'autre, et le test appartient au seul endroit qui les
        connaît. Un `state` à None reste un état INCONNU, qui n'est ni dans le
        bond ni à compter comme en dehors.
        """
        return self.state == "ACTIVE"


class TunnelStatus(BaseModel):
    """Vue d'ensemble d'un tunnel SpeedFusion."""

    peer_id: str
    peer_name: str | None = None
    connected: bool = False
    # Identifiant de WAN -> son état dans ce tunnel.
    links: dict[str, TunnelLink] = {}
    # Depuis quand ce tunnel TIENT, en secondes, tel que le routeur le compte.
    #
    # « Depuis quand le bond est-il monté ? » n'avait aucune réponse dans
    # l'outil, alors que la valeur arrive dans une charge utile déjà lue. C'est
    # elle qui distingue un tunnel stable d'un tunnel qui vient de se rétablir —
    # et c'est exactement ce qui aurait qualifié les cycles où l'état du tunnel
    # manque.
    #
    # Ce n'est PAS l'ancienneté de chaque lien DANS le bond : le routeur publie
    # aussi un `time.second` au niveau de chaque WAN, mais vérifié sur la
    # capture, il y recopie la même valeur sur les cinq liens actifs. Le lire
    # comme « ce lien est dans le bond depuis… » serait faux dès qu'un lien
    # rejoint le bond après coup.
    uptime_s: int | None = None


class Alert(BaseModel):
    """Un fait qui demande une décision, et qui n'est pas passé tout seul.

    Ni le bandeau ni le journal. Le bandeau dit l'état de l'instant, lissé ; le
    journal dit ce qui s'est passé, listé. Une alerte a une DURÉE — c'est elle
    qui distingue un fait d'un clignotement — et elle survit un moment à sa
    propre disparition, pour que quelqu'un qui s'est éloigné deux minutes
    l'apprenne.

    Trois règles seulement, et le choix se défend par la mesure : voir
    `app/alerts.py`, où les règles candidates écartées sont listées avec le
    nombre de fois qu'elles se seraient déclenchées sur 14 h 30 de relevés
    réels. La deuxième règle porte deux GENRES — un porteur, aucun — pour que
    l'écran titre juste ; ils sont de la même famille et se relaient sans
    rompre la durée.
    """

    # "hors du bond" · "liaison sur un seul lien" · "liaison sans aucun lien"
    # · "supervision interrompue"
    kind: str
    # Le lien concerné, quand la règle en désigne un.
    wan_id: str | None = None
    # Depuis quand la condition est vraie. Pas depuis quand on l'annonce : une
    # alerte publiée après soixante secondes de retenue doit dire les soixante.
    since: datetime
    # ABSORBÉE par une alerte de niveau bond : la condition reste vraie et
    # suivie — sa durée court — mais elle ne s'affiche pas tant qu'une cause
    # côté pair écarte tous les liens à la fois. Trois « lien N hors du bond
    # PEER_DOWN » sous « plus aucun lien dans le bond » disaient un fait quatre
    # fois ; les fermer, en revanche, les affichait RÉSOLUES au moment même où
    # le bond tombait, et repartait de zéro au retour du pair.
    absorbee: bool = False
    # Quand elle a cessé. `None` tant qu'elle dure.
    until: datetime | None = None
    # La cause, telle que le ROUTEUR la donne quand il la donne — `state` porte
    # son propre mot : WAN_DOWN, WAN_DISABLED, COLD… — ou la grandeur mesurée.
    detail: str | None = None
    # Le seuil de durée qui a fait publier cette alerte, en secondes. Il voyage
    # avec elle : l'exploitant doit pouvoir savoir sur quoi elle se fonde sans
    # lire le code.
    sustained_s: float = 0.0


class RouterEvent(BaseModel):
    """Un fait tiré du journal du routeur (`status.log`), SANS son texte brut.

    Les lignes du journal portent les noms de WAN (numéros de ligne) et l'IP
    du poste : seul le fait typé est gardé — le WAN par son identifiant, une
    cause courte. `empreinte` identifie la ligne source : la même ligne revient
    à chaque lecture, tant qu'elle reste parmi les cinquante dernières.
    """

    ts: datetime
    kind: str
    wan_id: str | None = None
    detail: str | None = None
    empreinte: str


class RadioSample(BaseModel):
    """Un relevé complet : état d'un WAN à un instant, et d'où il a été mesuré."""

    router_id: str
    router_name: str
    ts: datetime = Field(default_factory=utcnow)
    # Position du RÉCEPTEUR au moment de la mesure : GPS du routeur s'il en a un
    # (véhicule mobile), sinon coordonnées fixes déclarées dans la flotte. C'est
    # une mesure de l'équipement lui-même ; sans elle, un relevé dit « telle
    # cellule, tel niveau » sans jamais dire d'où il a été fait.
    site_lat: float | None = None
    site_lon: float | None = None
    wan: WanRadioStatus

    # Indicateurs de stabilité, cumulés depuis le démarrage de la supervision.
    cell_changes: int = 0
    disconnects: int = 0


class RouterSummary(BaseModel):
    """Résumé d'un routeur pour la liste (dernier état par WAN)."""

    id: str
    name: str
    # « mock » ou « router », PAR ÉQUIPEMENT. `/api/health` publie un mode global,
    # qui est celui du fichier de configuration : un routeur réel appairé à chaud
    # s'affichait donc sous un badge « Simulateur », alors que ses mesures étaient
    # bien réelles. La vérité est ici, et elle l'était déjà dans `RouterConfig`.
    mode: str = "mock"
    site_lat: float | None = None
    site_lon: float | None = None
    wans: list[RadioSample] = []
    # Renseignés dès que l'équipement a répondu ; None tant qu'il est injoignable
    # ou s'il ne fournit pas ces endpoints.
    device: DeviceInfo | None = None
    capabilities: DeviceCapabilities | None = None

    # État de la liaison avec l'équipement lui-même, tel que le poller le
    # constate. Sans lui, l'interface continuait d'afficher le dernier relevé
    # connu — donc « Liaison OK » — alors que le routeur ne répondait plus.
    status: str = "en attente"
    error: str | None = None

    # Âge du relevé le plus récent, en secondes, MESURÉ PAR LE SERVEUR.
    # Le calculer dans le navigateur revenait à comparer deux horloges : sur un
    # portable dont l'horloge avance, l'écart devient négatif, se retrouve borné
    # à zéro, et affiche « à l'instant » indéfiniment sur des données mortes.
    age_seconds: float | None = None

    # Période RÉELLEMENT observée entre deux relevés de CET équipement, dont le
    # navigateur déduit son seuil de péremption. Elle est propre à l'équipement
    # depuis que chacun a sa boucle : une mesure de flotte faisait payer aux
    # liaisons saines la lenteur d'un équipement mort, et relâchait leur seuil de
    # toute la durée de son budget. None tant que deux cycles n'ont pas eu lieu —
    # mieux vaut que l'appelant retombe sur son défaut prudent.
    period_seconds: float | None = None
    # Ce que COÛTE une scrutation de cet équipement, hors pause. Publié à côté
    # de la période pour que leur écart s'explique : mêler un cycle de flotte à
    # une période d'équipement faisait une soustraction qui ne tombait juste que
    # sur une flotte d'un seul.
    cycle_seconds: float | None = None
    # Sous-réseaux acheminés PAR le tunnel, tels que le pair les déclare. Sans
    # eux, un débit nul dans le bond se lit comme une panne — alors qu'un tunnel
    # dont aucun profil n'est sélectionné dans « Send All Traffic To » ne
    # transporte légitimement que ce qui lui est explicitement routé.
    bond_routes: list[str] = []
    # Les profils SpeedFusion, relus une fois par minute : ce qui nomme la
    # cause d'un tunnel à terre (R-1.5).
    bond_profiles: list[BondProfile] = []
    # Clients actifs du LAN, relus une fois par minute (R-1.10). `None` tant
    # que l'endpoint n'a pas été lu, ou sur un modèle qui ne le propose pas.
    lan_clients: LanClients | None = None
    # Depuis quand le bond TIENT, en secondes, selon le routeur. Distingue un
    # tunnel stable d'un tunnel qui vient de se rétablir — et c'est exactement ce
    # qui aurait qualifié les cycles où l'état du tunnel manque.
    tunnel_uptime_s: int | None = None
    # Le bond a-t-il déjà porté un lien dans CETTE session ? C'est ce qui
    # sépare, côté alertes, un tunnel tombé d'un tunnel qui n'a jamais tenu —
    # une panne d'un état. Publié pour que l'écran applique la même règle au
    # lieu de titrer « tombé » sur un bond qui n'a jamais porté. None tant que
    # la question n'a pas été posée.
    tunnel_attendu: bool | None = None
    # Les WAN qui n'ont pas été connectés UNE SEULE FOIS depuis la mise sous
    # tension — un Ethernet sans câble. Même règle que `tunnel_attendu` : un
    # lien jamais raccordé est un état, pas une panne, et le bandeau ne s'en
    # dégrade pas. Vide tant que la question n'a pas été posée.
    never_connected: list[str] = []
    # Ce qui demande une décision, et qui n'est pas passé tout seul. Trois
    # genres seulement, choisis par la mesure — voir `app/alerts.py`.
    alerts: list[Alert] = []
    # Le garde-fou absolu de latence, PUBLIÉ plutôt que recopié. Il vivait en
    # double — `_SEUIL_EPISODE_MS` côté serveur, `LATENCY_ABSOLUTE_MS` côté
    # interface — pour une seule et même règle éditoriale. Deux déclarations
    # d'un même seuil de part et d'autre d'une couture finissent toujours par
    # diverger, et ce projet en a déjà fait les frais avec la latence.
    latency_threshold_ms: float | None = None


class Session(BaseModel):
    """Une mise sous tension de l'équipement, d'un bout à l'autre.

    Le Peplink n'est alimenté que véhicule à l'arrêt : chaque session est un lieu
    et une intervention. C'est la structure naturelle de toutes les mesures, et
    elle rend deux stationnements comparables — ce qu'un ruban continu de relevés
    ne permet pas.
    """

    id: int
    router_id: str
    # La mise sous tension, déduite de l'`uptime` des liens. C'est le début du
    # STATIONNEMENT — pas celui de la supervision.
    started_at: datetime
    # Premier relevé rattaché : l'instant où le superviseur a commencé à
    # regarder. Il vient souvent bien après l'allumage — le routeur démarre avec
    # le véhicule, l'outil quand l'exploitant l'ouvre. C'est la borne gauche de
    # tout ce qui se DESSINE : avant elle il n'y a rien, et un axe qui partait
    # de l'allumage donnait à ce vide quarante pour cent de sa largeur. `None`
    # tant qu'aucun relevé n'est rattaché.
    first_seen: datetime | None = None
    # Dernier relevé rattaché. Il n'y a pas d'instant de fin observable :
    # l'équipement s'éteint sans prévenir.
    last_seen: datetime
    samples: int = 0
    label: str | None = None


class SessionLink(BaseModel):
    """Ce qu'un lien a fait pendant une session, résumé en une ligne."""

    wan_id: str
    # Pas de `wan_name` : il valait `None` EN DUR, parce que la table des
    # relevés ne conserve pas le nom configuré du WAN — la fiche ne peut pas le
    # connaître. Un champ toujours vide est une promesse qui ne se tient jamais,
    # et l'interface nomme de toute façon les liens « SIM 4 » par leur support
    # et leur identifiant. Le nom configuré porte couramment un ICCID complet,
    # que le projet tient déjà hors de ses écrans.
    kind: str = "cellular"
    operator: str | None = None
    # La SUCCESSION des opérateurs du lien, dans l'ordre, un libellé par réseau
    # (PLMN). `operator` n'en dit que le premier : le WAN 3 de la revue, passé
    # chez SFR après une bascule de SIM, restait « Orange F » sur sa fiche.
    operators: list[str] = []
    # Les MANŒUVRES de l'exploitant sur ce lien, lues dans le journal du
    # routeur : bascule de SIM, configuration modifiée, désactivation. Comptées
    # à part des coupures, qu'elles ne sont pas.
    maneuvers: int = 0
    samples: int = 0
    # Volumes INTÉGRÉS depuis les débits relevés, pas lus d'un compteur : le
    # routeur ne publie qu'un cumul depuis son installation — février 2022 sur cet
    # équipement —, inexploitable pour borner une session. L'intégration vaut ce
    # que vaut l'échantillonnage : à 5,3 s de période, un pic plus court passe
    # entre les mailles. C'est un ordre de grandeur, et il doit se lire comme tel.
    #
    # `None` quand l'équipement ne publie pas de débit par WAN — le Dome Pro Duo
    # ne le fait pas. Zéro voudrait dire « rien n'a circulé », et la fiche
    # l'écrivait en gras : « 0.0 Mo montants » sur sept cent vingt relevés où
    # aucun débit n'avait jamais été mesuré.
    mb_up: float | None = None
    mb_down: float | None = None
    # Le nombre de mesures du BOND qui fondent ses percentiles : un p90 sur
    # trois points ne se lit pas comme un p90 sur trois heures (C-12).
    rtt_samples: int = 0
    rtt_p50_ms: float | None = None
    rtt_p90_ms: float | None = None
    rtt_max_ms: float | None = None
    # La latence du LIEN, hors tunnel, et le nombre de mesures qui la fondent
    # — une par minute (R-3.3). Sans elles, « — » sur tout équipement dont le
    # tunnel ne tient pas, alors que le routeur mesure chaque SIM.
    path_latency_samples: int = 0
    path_latency_p50_ms: float | None = None
    path_latency_p90_ms: float | None = None
    path_latency_max_ms: float | None = None
    rsrp_median: float | None = None
    # Points de l'heure du ROUTEUR comptés dans la médiane, pris dans les trous
    # de supervision seulement (R-3.1) : la fiche dit ce qu'elle a comblé.
    rsrp_filled_samples: int = 0
    # Transitions connecté -> déconnecté observées, plus les rétablissements que
    # seul un `uptime` qui recule trahit — une coupure plus courte que le cycle
    # ne se voit pas autrement.
    outages: int = 0
    cells: int = 0
    # Changements de cellule observés, et leur taux horaire.
    #
    # Le compte brut ne se compare pas d'une session à l'autre ; le taux, si —
    # et c'est ainsi qu'on voit un modem changer de cellule six fois plus souvent
    # que son voisin au même endroit et au même instant. Mesuré sur la base à une
    # halte : 33,0 changements par heure sur un lien contre 5,1 sur un autre, et
    # c'est le premier dont la perte dépasse 1 %.
    #
    # Le taux n'est PAS qualifié « à l'arrêt », bien que ce soit l'état nominal :
    # éprouvé sur la base, le filtre d'immobilité est vide de sens — l'étendue
    # GPS maximale d'une halte entière est de 79 m, soit de la gigue de
    # récepteur, et un seuil de 50 m ne retire que 7 changements sur 785.
    # L'équipement n'est alimenté que véhicule à l'arrêt ; le préciser
    # laisserait croire à une restriction validée qui n'en est pas une.
    cell_changes: int = 0
    # `None` sous un quart d'heure observé : une seule bascule sur cinq minutes
    # vues vaut « 12 par heure », un chiffre qui ne décrit rien.
    cell_changes_per_hour: float | None = None


class CellLatency(BaseModel):
    """Ce qu'une CELLULE a coûté en latence pendant la session.

    C'est la coupe qui manquait. La médiane d'un lien ne bouge presque pas quand
    sa queue se multiplie par quatre : mesuré sur une cellule tenue constante de
    13 h à 21 h, le RSRP varie de 1,4 dB, la médiane passe de 31 à 40 ms — et le
    p90 de 119 à 435 ms. C'est la queue qui fait geler une image, et elle ne se
    voit qu'à cellule fixe : un percentile calculé sur une fenêtre qui chevauche
    deux cellules mélange deux populations.

    Ne sont publiées que les cellules ayant réuni assez de relevés. Un p90 sur
    vingt mesures n'est pas un p90.
    """

    cell_id: NomRequis
    # Le LIEN qui a mesuré, et son réseau : un percentile est propre à un modem
    # — chaque SIM a son chemin —, et un ECI n'est unique que dans son PLMN.
    # Mesuré : une même cellule vue par quatre WAN, p90 de 223 à 397 ms selon
    # le WAN, publiée « 337 ms sur 6 238 relevés » — un chiffre qui ne décrivait
    # aucun modem.
    # `str`, comme tout `wan_id` du contrat — pas `NomRequis` : ce type
    # convertit en ENTIER, et `cellular-1` (simulateur, ou tout châssis qui
    # nomme autrement) faisait lever la fiche de session entière.
    wan_id: str
    plmn: str | None = None
    # La station, telle que le parseur l'a décomposée — `None` quand il s'en est
    # abstenu, ce qu'il fait sur un identifiant 5G SA.
    enb_id: Nom = None
    samples: int = 0
    rtt_p50_ms: float | None = None
    rtt_p90_ms: float | None = None
    rtt_p99_ms: float | None = None



class BondAvailability(BaseModel):
    """Part du temps où la liaison avait au moins un lien PROPRE.

    Le chiffre de recette qui manquait, et il ne vaut que si son dénominateur
    est honnête : les cycles où la sonde de tunnel n'a pas tourné ne sont pas
    des pannes, ce sont des cycles NON OBSERVÉS. Mesuré sur la base, l'écart
    n'est pas théorique — une halte est passée de 63,8 % à 99,8 % en excluant
    1 383 cycles sans regard, soit « une heure et demie de panne » entièrement
    fabriquée par un défaut d'observation.

    `observed` et `total` voyagent donc avec le taux, comme un dénominateur
    accompagne un taux de perte partout ailleurs dans ce projet.
    """

    # Seuil de latence sous lequel un lien est dit « propre », en ms.
    threshold_ms: float
    total_cycles: int = 0
    observed_cycles: int = 0
    at_least_one_pct: float | None = None
    at_least_two_pct: float | None = None
    # Cycles observés où le bond a porté AU MOINS un lien, latence mise à part.
    # Zéro sur une session entière veut dire « tunnel jamais monté » — et alors
    # « 0 % de lien propre » ne juge pas la liaison, il constate l'absence du
    # tunnel. Vu sur le MAX HD2, concentrateur injoignable, 200 Mo sortis en
    # direct pendant que la fiche affichait « au moins un lien propre 0 % ».
    cycles_in_bond: int = 0


class PathDiversity(BaseModel):
    """Ce que les modems partageaient, en PART DE TEMPS.

    L'outil détectait déjà le partage au relevé courant. Ce qui manquait est la
    prévalence, qui transforme un avertissement ponctuel en fait de recette.

    Attention à la lecture : un chiffre global mélange des régimes opposés.
    Mesuré sur la base, les quatre modems sont sur la même station 0,2 % du
    temps à une halte et 99,7 % à une autre — la moyenne des deux, 53 %, ne
    décrit aucune des deux et ne dit que la durée relative des haltes. Cette
    fiche est donc par SESSION, c'est-à-dire par halte.
    """

    # Cycles où tous les liens cellulaires connectés ont publié leur cellule.
    cycles: int = 0
    same_site_pct: float | None = None
    same_cell_pct: float | None = None
    same_earfcn_pct: float | None = None
    # Le dénominateur de CHAQUE part : les cycles où tous les liens ont publié
    # ce champ-là. Un seul `cycles` était publié, et « même station 100 % » se
    # lisait sur 5 912 cycles quand il n'en jugeait que 1 999 (C-12).
    site_cycles: int = 0
    cell_cycles: int = 0
    earfcn_cycles: int = 0
    # Nombre d'opérateurs distincts vus pendant la session. Un seul signifie
    # qu'une panne de cœur de réseau emporterait tous les liens à la fois.
    operators: int = 0


class SessionSummary(BaseModel):
    """La fiche d'une session : ce qui la rend comparable à une autre.

    Sans elle, une session n'est qu'un intervalle de temps. Avec elle, « le même
    parking, trois semaines plus tôt » devient une phrase que l'outil peut
    produire — et c'est tout l'intérêt d'un équipement qui n'est allumé qu'à
    l'arrêt.
    """

    session: Session
    links: list[SessionLink] = []
    mb_up: float | None = None
    mb_down: float | None = None
    # La fiche a-t-elle été calculée sur toute la session, ou sur une fenêtre ?
    # Une session de trois semaines — un châssis allumé depuis trois semaines et
    # supervisé pour la première fois — ne se relit pas en entier sans faire
    # tomber le superviseur. Quand `partial` est vrai, `window_start` et
    # `window_end` bornent ce qui a RÉELLEMENT été lu, et l'interface doit le
    # dire : une fiche muette sur sa fenêtre se lit comme une fiche complète.
    partial: bool = False
    window_start: datetime | None = None
    window_end: datetime | None = None
    # Barycentre des positions relevées, et rayon contenant 90 % d'entre elles.
    # Mesuré sur véhicule immobile : 10,9 m d'écart médian au barycentre, 31 m au
    # maximum. Le rayon dit donc surtout la dérive du récepteur — mais il sépare
    # sans risque deux stationnements distants de plus de cent mètres.
    lat: float | None = None
    lon: float | None = None
    radius_m: float | None = None
    # Les trois analyses qui ne coûtent aucune requête : elles se calculent sur
    # les relevés que la fiche lit déjà.
    cells: list[CellLatency] = []
    availability: BondAvailability | None = None
    diversity: PathDiversity | None = None
    # Stations de base distinctes vues pendant la session, tous liens confondus.
    sites: int = 0


class TimelineSlice(BaseModel):
    """Une tranche de temps, pour UN lien, résumée par son PIRE cas.

    Jamais une moyenne : une bande qui lisse l'incident qu'on cherche ne sert à
    rien. Le relevé le plus instructif de la base est un pic isolé à 1 929 ms au
    milieu de liens à 30 ms, et une moyenne sur cinq minutes l'effacerait.
    """

    t: int
    samples: int
    # Relevés où le lien était debout, et où il PORTAIT vraiment — les deux ne
    # se confondent pas : un lien peut être connecté côté WAN et écarté du bond.
    up: int
    carrying: int
    # Relevés où l'état du tunnel était connu. Sans lui, `carrying = 0` ne
    # distinguerait pas « écarté du bond » de « on ne sait pas ».
    tunnel_known: int
    # `rtt_worst_ms` vivait ici : la pire latence de la tranche, calculée par
    # SQL, sérialisée à chaque relecture, et lue par personne. Retiré plutôt que
    # gardé « au cas où » — la bande dit la qualité radio et le fait de porter,
    # pas le temps de transit. Le jour où une latence dans le temps aura un
    # usage, elle revient en une ligne : `MAX(rtt_ms)` par tranche.
    sinr_worst: float | None = None
    rsrp_worst: float | None = None
    # Redondance dépensée et paquets transportés, CUMULÉS sur la tranche.
    #
    # Ils ne sont pas là pour être tracés : sur le matériel de référence la
    # réparation vaut zéro sur les 15 500 relevés où elle a été enregistrée, et
    # tracer une grandeur qui ne varie jamais la ferait passer pour une mesure.
    # Ils servent à établir un FAIT que rien n'exposait : si des paquets ont
    # circulé sans qu'un seul ne soit réparé, la redondance du profil SpeedFusion
    # est éteinte — donc chaque paquet perdu sur un lien est perdu pour le flux.
    repair: int | None = None
    packets: int | None = None
    # Relevés d'adieu d'un WAN désactivé (C-20). Ils sont `connected = 0` et
    # comptés dans `samples`, pas dans `up` : sans ce compte, la bande peignait
    # en panne le choix de l'exploitant.
    disabled: int = 0


class TimelineLane(BaseModel):
    """Ce qu'un lien a fait pendant toute la fenêtre."""

    wan_id: str
    slices: list[TimelineSlice] = []


class Timeline(BaseModel):
    """L'état de tous les liens sur la durée d'un stationnement.

    Les tranches ABSENTES sont une information : le superviseur ne regardait
    pas. Les combler à zéro referait le mensonge que tout ce projet combat.
    """

    router_id: str
    started_at: datetime
    ended_at: datetime
    # La fenêtre demandée a été ROGNÉE à vingt-quatre heures : `started_at`
    # est le début réellement relu, pas celui demandé. Jamais tu.
    capped: bool = False
    slice_count: int
    slice_seconds: float
    lanes: list[TimelineLane] = []


class Episode(BaseModel):
    """Un fait daté de la fenêtre, sur un lien.

    Tout ceci était détecté depuis longtemps et n'était jamais LISTÉ : l'écran
    donnait des compteurs, pas une chronologie. « Trois coupures » ne dit ni
    quand, ni sur quel lien, ni pourquoi.
    """

    # La liste COMPLÈTE des genres, et elle est contractuelle : `_GRAVITE` dans
    # les routes classe la troncature du journal sur ces valeurs, et un genre
    # qu'elle ne connaît pas se retrouverait juste au-dessus des rétablissements.
    #   coupure · retour · micro-coupure · désactivation (C-20 : un WAN désactivé
    #   par l'exploitant, reconnu à son relevé d'adieu — une décision, pas une panne)
    #   latence (le routeur a écarté) · latence haute (l'outil juge)
    #   signalement · hors du bond · de retour dans le bond
    #   position perdue · position retrouvée
    kind: str
    # Secondes écoulées depuis le dernier changement de cellule de ce lien, au
    # moment où l'épisode s'ouvre. `None` quand il n'y en a pas eu d'assez
    # récent — et c'est le cas le plus fréquent.
    #
    # Une EXPLICATION, jamais une prédiction. Mesuré : après un changement de
    # cellule, un pic de plus de 150 ms survient dans les trois relevés suivants
    # 43,8 % du temps contre 21,7 % sans, soit un risque doublé. Mais le pic dure
    # une quinzaine de secondes : l'information arrive EN MÊME TEMPS que l'effet.
    # Et elle ne couvre qu'une minorité — 15,8 % des pics à trente secondes de
    # fenêtre. Les autres restent sans explication plutôt que d'en recevoir une
    # fausse.
    after_handover_s: float | None = None
    # Vide pour un épisode qui ne décrit AUCUN lien : la position est celle de
    # l'équipement, pas d'un modem.
    # `None` pour un fait du ROUTEUR entier (démarrage, priorités, connexion
    # admin), tiré de son journal.
    wan_id: str | None
    # Support du lien, pour que le journal le nomme comme le reste de
    # l'interface : « SIM 6 » et non « 6 ». Le libellé suit le SUPPORT — coller
    # « SIM » à un Wi-Fi amont est un défaut déjà corrigé ailleurs.
    link_kind: str = "cellular"
    at: datetime
    # Fin de l'épisode, pour ceux qui durent. Les transitions sont ponctuelles.
    until: datetime | None = None
    samples: int = 1
    # Pic de latence de l'épisode, en ms.
    peak_ms: float | None = None
    # Ce que le routeur disait du lien à cet instant : « No Cable Detected »,
    # « Disabled », « absent du relevé ». C'est la CAUSE, et l'historique ne la
    # gardait pas.
    detail: str | None = None


class LinkChurn(BaseModel):
    """Ce qui est trop fréquent pour être listé, et se compte donc.

    Mesuré sur 32 641 relevés : cinq coupures, mais neuf cent quatre-vingt-cinq
    changements de station. Lister les seconds noierait les premiers, et un
    journal qu'on ne lit plus ne vaut pas mieux qu'un compteur.
    """

    wan_id: str
    # Support du lien, pour que l'écran le nomme comme le reste de l'interface.
    # Sans lui, un WAN filaire s'affichait « SIM 1 ».
    link_kind: str = "cellular"
    # Ce lien a-t-il jamais publié une cellule ? Un filaire n'en a pas, et son
    # « 0 changement » n'est pas une stabilité : c'est une absence de grandeur.
    has_cells: bool = False
    cell_changes: int = 0
    station_changes: int = 0
    # Coupures et rétablissements que seul un `uptime` qui recule trahit, sur la
    # FENÊTRE. L'écran les cumulait depuis le démarrage du processus superviseur
    # — une base de temps qui n'apparaissait nulle part et qui se remettait à
    # zéro à chaque relance, dans un panneau intitulé « depuis la mise sous
    # tension ».
    outages: int = 0


class EventLog(BaseModel):
    """Le journal d'une fenêtre : ce qui s'y est passé, daté."""

    router_id: str
    started_at: datetime
    ended_at: datetime
    # Fenêtre rognée à vingt-quatre heures : voir `Timeline.capped`.
    capped: bool = False
    episodes: list[Episode] = []
    churn: list[LinkChurn] = []
    # Épisodes retenus par le plafond, et donc NON listés. Jamais tu : un
    # journal tronqué en silence se lit comme un journal complet.
    omitted: int = 0
    # Garde-fou absolu au-delà duquel une latence ouvre un épisode quoi qu'il
    # arrive, publié pour que l'exploitant sache sur quoi le journal se fonde.
    # La règle principale, elle, est celle du routeur : dépasser de plus de son
    # `latencyDiffCutoff` le lien le plus rapide du cycle.
    latency_threshold_ms: float = 0


class HistoryPoint(BaseModel):
    ts: datetime
    value: float | None = None
    # « routeur » : un point de l'heure que le ROUTEUR garde, placé dans un trou
    # de supervision (R-3.1) — une mesure passée, sans état de connexion, que
    # l'écran dessine à part. `None` : un relevé de cet outil.
    source: str | None = None
    # Paquets réellement comptés sous ce point, quand la grandeur est un TAUX.
    #
    # Un taux sans son dénominateur est indéfendable, et la courbe de perte était
    # le dernier endroit qui le perdait : la tuile et la table refusent de
    # publier un taux calculé sur moins de deux cents paquets, le graphe traçait
    # un pic à 33,33 % obtenu sur UN paquet manquant — sur un lien parfaitement
    # sain, et sans rien qui le dise.
    #
    # Le seuil n'est PAS appliqué ici : c'est une règle de rendu, et la graver
    # côté serveur la ferait survivre à toute décision ultérieure. Le serveur
    # publie la mesure et de quoi la juger ; l'écran juge.
    denominator: float | None = None
    # La technologie du relevé, pour une grandeur RADIO seulement : un SINR 5G
    # et un SINR LTE ne se comparent pas, et une courbe qui les enchaîne sans
    # le dire fait lire un changement de technologie comme une chute de signal
    # (revue, scénario 5). L'écran en tire les repères.
    rat: str | None = None


class HistorySeries(BaseModel):
    router_id: str
    wan_id: str
    metric: str
    points: list[HistoryPoint] = []
    # Points RÉELLEMENT présents dans la fenêtre, avant allègement. Quand il
    # dépasse le nombre de points rendus, la courbe est une enveloppe — minimum
    # et maximum par tranche — et non la série complète. Le dire est nécessaire :
    # une courbe allégée se lit exactement comme une courbe complète.
    available: int = 0
