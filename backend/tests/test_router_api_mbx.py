"""Parsing validé contre une seconde capture réelle : Pepwave MAX HD4 MBX.

Matériel : MAX HD4 MBX (MAX-HD4-MBX-GLTE-G), firmware 8.5.4 build 6253,
modems Telit LM960, 4 WAN cellulaires Orange en LTE-A (agrégation 4 porteuses).

Ce boîtier diffère du Dome Pro Duo sur trois points qui cassaient le mapping :

  1. `cellular.rat[].name` est une **chaîne vide** — toutes les porteuses
     ressortaient avec le RAT "?" ;
  2. `cellular.network` vaut "LTE-A" alors que les porteuses s'annoncent
     "LTE Band 3 (1800 MHz)" : la comparaison brute ne trouvait jamais la
     porteuse primaire et retombait sur un choix par défaut ;
  3. `cellular.dataTechnology` vaut "FDD / CA Downlink" — un descripteur de
     duplex et d'agrégation, pas une technologie : il ne doit pas servir de
     libellé (contrairement au "5G NSA" du Dome Pro Duo).

La fixture est la capture terrain avec IMEI, IMSI, ICCID, numéros de ligne,
adresses opérateur, n° de série et position GPS remplacés.
"""

import json
import json as _json
from pathlib import Path

import pytest

from app.models import WanRadioStatus
from app.peplink.router_api import (
    _rat_family,
    _technology_label,
    parse_location,
    parse_wan_connection,
)

FIXTURE = Path(__file__).parent / "fixtures" / "max_hd4_mbx_8.5.4.json"


@pytest.fixture(scope="module")
def capture() -> dict:
    with open(FIXTURE, encoding="utf-8") as f:
        return json.load(f)


@pytest.fixture(scope="module")
def wans(capture) -> list:
    return parse_wan_connection(capture["status.wan.connection"])


# --- sélection des WAN --------------------------------------------------------

def test_the_four_sims_are_supervised(wans):
    """4 WAN cellulaires actifs ; ethernet, modems USB et Wi-Fi désactivés écartés."""
    assert [(w.wan_id, w.kind) for w in wans] == [
        ("4", "cellular"),
        ("5", "cellular"),
        ("6", "cellular"),
        ("7", "cellular"),
    ]


def test_usb_modem_wans_are_ignored(capture, wans):
    """Le HD4 expose deux WAN `modem` (USB) sans métrique radio à superviser."""
    response = capture["status.wan.connection"]["response"]
    assert [response[k]["type"] for k in ("8", "9")] == ["modem", "modem"]
    assert {"8", "9"}.isdisjoint(w.wan_id for w in wans)


# --- écart n°1 : `rat.name` vide ---------------------------------------------

def test_rat_name_is_empty_on_this_firmware(capture):
    """Prémisse du test suivant : le routeur ne nomme pas ses RAT."""
    rats = capture["status.wan.connection"]["response"]["4"]["cellular"]["rat"]
    assert [r["name"] for r in rats] == [""]


def test_rat_is_derived_from_the_band_label(wans):
    """Faute de `rat.name`, la famille se déduit du libellé de la porteuse."""
    assert {b.rat for w in wans for b in w.bands} == {"LTE"}


# --- écart n°2 : "LTE-A" côté WAN, "LTE ..." côté porteuse --------------------

@pytest.mark.parametrize(
    "label,expected",
    [
        ("LTE-A", "LTE"),
        ("LTE", "LTE"),
        ("5G NSA", "5G"),
        ("5G", "5G"),
        ("LTE Band 3 (1800 MHz)", "LTE"),
        ("FDD / CA Downlink", None),
        ("", None),
        (None, None),
    ],
)
def test_rat_family_normalisation(label, expected):
    assert _rat_family(label) == expected


def test_primary_carrier_is_the_first_listed(wans):
    """En LTE-A toutes les porteuses sont LTE : la première listée est la primaire.

    C'est aussi la seule dont le SINR soit renseigné — les porteuses secondaires
    remontent 0.0, ce qui confirme la lecture.
    """
    wan = wans[1]  # WAN 5
    assert [b.band for b in wan.bands] == ["B7", "B3", "B1", "B20"]
    assert wan.band == "B7"
    assert (wan.rsrp, wan.rsrq, wan.sinr) == (-93.0, -11.0, 14.2)
    # Le `0.0` des secondaires est une absence de mesure, pas un SINR de 0 dB.
    assert [b.sinr for b in wan.bands[1:]] == [None, None, None]


def test_an_unmeasured_secondary_carrier_publishes_no_signal(wans):
    """`sinr: 0.0` et `rsrq: -3.0` — la borne théorique — sur une secondaire
    dont la primaire est à 14 dB : le modem ne la mesure pas. Un test figeait
    `[0.0, 0.0, 0.0]` comme attendu, et l'écran notait ces porteuses « au bord
    du décrochage ».
    """
    wan6 = wans[2]
    assert wan6.bands[0].sinr == 11.0 and wan6.bands[0].rsrq == -12.0
    assert [b.sinr for b in wan6.bands[1:]] == [None, None, None]
    assert [b.rsrq for b in wan6.bands[1:]] == [None, None, -14.0], "seule la borne −3 s'efface"
    # Le WAN 4 mesure ses quatre porteuses : rien n'y est touché.
    assert [b.sinr for b in wans[0].bands] == [11.6, 14.0, 11.6, 14.2]
    # Une primaire à 0.0 reste une mesure : c'est la seule qu'on ait.
    payload = {"response": {"2": {"type": "cellular", "statusLed": "green",
        "cellular": {"network": "LTE", "rat": [{"name": "LTE", "band": [
            {"name": "LTE Band 3 (1800 MHz)", "signal": {"rsrp": -110, "sinr": 0.0, "rsrq": -3.0}},
        ]}]}}}}
    seule = parse_wan_connection(payload)[0]
    assert (seule.sinr, seule.rsrq) == (0.0, -3.0)


def test_primary_carrier_is_not_the_strongest_one(wans):
    """La porteuse primaire prime sur la mieux reçue : B3 est à -83 dBm, B7 reste primaire."""
    wan = wans[1]
    best = max(wan.bands, key=lambda b: b.rsrp)
    assert best.band == "B3" and best.rsrp == -83.0
    assert wan.band == "B7"


def test_four_carriers_are_aggregated_on_every_sim(wans):
    assert all(len(w.bands) == 4 for w in wans)
    assert all(b.rat == "LTE" for w in wans for b in w.bands)


# --- écart n°3 : dataTechnology non exploitable -------------------------------

def test_data_technology_is_a_duplex_descriptor_here(capture):
    """Prémisse : sur ce firmware `dataTechnology` ne nomme aucune génération."""
    cellular = capture["status.wan.connection"]["response"]["4"]["cellular"]
    assert cellular["dataTechnology"] == "FDD / CA Downlink"
    assert cellular["network"] == "LTE-A"


def test_technology_label_falls_back_to_network(wans):
    """Le badge doit afficher "LTE-A", pas "FDD / CA Downlink"."""
    assert {w.rat for w in wans} == {"LTE-A"}


@pytest.mark.parametrize(
    "cellular,expected",
    [
        ({"network": "LTE-A", "dataTechnology": "FDD / CA Downlink"}, "LTE-A"),
        ({"network": "5G", "dataTechnology": "5G NSA"}, "5G NSA"),  # Dome Pro Duo
        ({"network": "LTE", "dataTechnology": ""}, "LTE"),
        ({"network": None, "dataTechnology": "FDD"}, "FDD"),
        ({}, None),
    ],
)
def test_technology_label_selection(cellular, expected):
    assert _technology_label(cellular) == expected


# --- identité réseau et cellule ----------------------------------------------

def test_operator_and_plmn(wans):
    assert {w.operator for w in wans} == {"Orange"}
    # La GRAPHIE du routeur, zéro de tête compris : « 01 » n'est pas 1, et
    # « 001 » — un MNC nord-américain — ne doit pas se confondre avec lui.
    assert {w.plmn for w in wans} == {"208-01"}


def test_all_sims_share_the_same_tracking_area(wans):
    """Les 4 SIM sont sur le même TAC : quatre modems au même endroit."""
    assert {w.tac for w in wans} == {52641}


def test_two_sims_camp_on_the_same_cell(wans):
    """WAN 4 et WAN 6 partagent une cellule : même antenne probable attendue."""
    by_id = {w.wan_id: w.cell_id for w in wans}
    assert by_id["4"] == by_id["6"] == 25735433
    assert by_id["5"] == 25735426 and by_id["7"] == 25735436


def test_connection_state(wans):
    assert all(w.connected for w in wans)
    assert all(w.status_message == "Connected to Orange" for w in wans)
    assert all(w.signal_bars == 5 for w in wans)
    assert all(w.uptime_s and w.uptime_s > 0 for w in wans)


# --- champs absents sur ce modem ---------------------------------------------

def test_channel_width_is_absent(wans):
    """Le HD4 ne remonte pas la largeur de canal : dégradation propre, pas d'erreur."""
    assert all(b.channel_width is None for w in wans for b in w.bands)


def test_pci_zero_everywhere_is_read_as_not_reported(wans):
    """Le Telit LM960 renvoie `pci: 0` partout : c'est une absence, pas une valeur.

    Un PCI de 0 est légitime en soi, mais jamais sur les quatre porteuses agrégées
    à la fois. Le présenter comme une mesure ferait croire à une cellule identifiée.
    """
    raw = _json.load(open(FIXTURE, encoding="utf-8"))["status.wan.connection"]
    assert all(b["pci"] == 0 for w in ("4", "5", "6", "7")
               for r in raw["response"][w]["cellular"]["rat"] for b in r["band"])
    assert all(b.pci is None for w in wans for b in w.bands)
    assert all(w.pci is None for w in wans)


def test_a_genuine_pci_zero_is_preserved():
    """Une porteuse à PCI 0 parmi d'autres reste une mesure valide."""
    payload = {"response": {"2": {"type": "cellular", "statusLed": "green",
        "cellular": {"network": "LTE", "rat": [{"name": "LTE", "band": [
            {"name": "LTE Band 3 (1800 MHz)", "pci": 0, "signal": {"rsrp": -90}},
            {"name": "LTE Band 7 (2600 MHz)", "pci": 42, "signal": {"rsrp": -95}},
        ]}]}}}}
    bands = parse_wan_connection(payload)[0].bands
    assert [b.pci for b in bands] == [0, 42]


def test_no_throughput_in_this_endpoint(wans):
    """`status.wan.connection` ne porte aucun débit : la tuile restera vide."""
    assert all(w.rx_kbps is None and w.tx_kbps is None for w in wans)


# --- GPS ----------------------------------------------------------------------

def test_gps_fix_is_available(capture):
    """Le HD4 a un point GPS fixé : sa position prime sur celle du fichier de flotte."""
    position = parse_location(capture["info.location"])
    assert position is not None
    assert -90 <= position.lat <= 90 and -180 <= position.lon <= 180


def test_the_fix_carries_its_age_and_its_quality(capture):
    """Quatre champs de la même réponse, lus à chaque cycle et jetés.

    L'âge est le plus utile des quatre : un récepteur qui a perdu le ciel
    republie son dernier point, et rien dans la LECTURE ne le trahit — elle
    reste fraîche. Le poller avait dû se rabattre sur l'âge de sa propre lecture,
    qui ne mesure que la liaison entre lui et le routeur.

    Recoupement qui confirme l'unité : l'horodatage du point plus `timeElapsed`
    retombe à une seconde près sur l'horodatage de la réponse WAN du même
    instantané — donc des secondes.
    """
    position = parse_location(capture["info.location"])
    assert position is not None
    assert position.age_s == 7
    assert position.hdop == 0.74

    brut = capture["info.location"]["response"]["location"]
    assert brut["timestamp"] + brut["timeElapsed"] == pytest.approx(
        capture["status.wan.connection"]["response"]["timestamp"], abs=2
    ), "l'âge se compte bien en secondes"

    # La vitesse est lue et JAMAIS affichée : son unité n'est documentée nulle
    # part, et les deux matériels de référence étaient à l'arrêt — 0,0067 et
    # 0,0062, du bruit de mesure, qui ne tranche ni entre m/s, ni km/h, ni nœuds.
    assert position.speed == pytest.approx(0.006688)


# --- décomposition de l'ECI ---------------------------------------------------
#
# L'ECI LTE n'est pas atomique : 20 bits d'eNodeB + 8 bits de cellule. Sans cette
# lecture, quatre Cell ID différents laissent croire à quatre sites différents.

def test_all_four_sims_share_one_base_station(wans):
    """Constat majeur sur ce boîtier : les 4 SIM campent sur le même eNodeB."""
    assert {w.enb_id for w in wans} == {100529}
    assert [w.sector_id for w in wans] == [9, 2, 9, 12]


def test_sectors_distinguish_sims_on_the_same_station(wans):
    """Même station, mais chaque SIM sur sa cellule : WAN 4 et 6 partagent la leur."""
    by_wan = {w.wan_id: (w.enb_id, w.sector_id) for w in wans}
    assert by_wan["4"] == by_wan["6"]           # même cellule, d'où le même Cell ID
    assert len({by_wan["4"], by_wan["5"], by_wan["7"]}) == 3


def test_eci_split_is_consistent_with_cell_id(wans):
    for wan in wans:
        assert wan.enb_id * 256 + wan.sector_id == wan.cell_id


# --- identité de l'équipement -------------------------------------------------
#
# `status.system.info` n'est PAS documenté dans la référence officielle de l'API,
# mais il répond sur les deux matériels validés. Tout doit donc être optionnel.

from app.peplink.router_api import (
    apply_traffic,
    parse_capability,
    parse_ports,
    parse_system_info,
    parse_traffic,
)


@pytest.fixture(scope="module")
def device(capture):
    return parse_system_info(capture["status.system.info"])


def test_device_identity(device):
    assert device.model == "Pepwave MAX HD4 MBX"
    assert device.product_code == "MAX-HD4-MBX-GLTE-G"
    assert device.firmware == "8.5.4 build 6253"
    assert device.hardware_revision == "4"


def test_serial_is_the_stable_key(device):
    """L'IP dépend du réseau et le nom est modifiable : seul le numéro de série tient."""
    assert device.serial  # anonymisé dans la fixture, mais présent


def test_health_metrics(device):
    assert device.cpu_load_percent == pytest.approx(12.5, abs=0.1)
    assert device.power_watts == 19.0


def test_power_sources_show_redundancy(device):
    """Châssis à double alimentation : une seule branchée sur ce relevé."""
    assert [(s.name, s.connected) for s in device.power_sources] == [
        ("DC Source A", True),
        ("DC Source B", False),
        ("DC Term. Block", False),
    ]


def test_lan_mac_is_exposed(device):
    """Sert à reconnaître l'équipement dans la table ARP, avant authentification."""
    assert device.mac_lan and device.mac_lan.startswith("a8:c0:ea")  # OUI Peplink


@pytest.mark.parametrize(
    "payload",
    [{}, {"response": {}}, {"response": {"device": None}}, {"response": {"device": "x"}}],
)
def test_missing_system_info_degrades_to_none(payload):
    """Un équipement qui ne publie pas cet endpoint ne doit pas casser la supervision."""
    assert parse_system_info(payload) is None


def test_partial_system_info_is_accepted():
    """Le Dome Pro Duo ne remonte ni consommation ni état d'alimentation."""
    info = parse_system_info({"response": {"device": {"model": "Peplink Dome Pro Duo"}}})
    assert info.model == "Peplink Dome Pro Duo"
    assert info.power_watts is None and info.power_sources == []
    assert info.serial is None and info.cpu_load_percent is None


# --- capacités déclarées ------------------------------------------------------

@pytest.fixture(scope="module")
def caps(capture):
    return parse_capability(capture["info.capability"], parse_ports(capture["status.port"]))


def test_declared_features(caps):
    """Seules les capacités que l'application EXPLOITE sont modélisées.

    `wifi_ap`, `vlan` et `firewall` étaient lues, sérialisées et typées jusqu'au
    frontend sans qu'aucun écran ne les affiche jamais. Les modéliser donnait à
    croire qu'on en faisait quelque chose ; le firmware les déclare toujours, on
    ne les lit simplement plus.
    """
    assert (caps.gps, caps.pepvpn) == (True, True)


def test_wan_inventory_matches_what_the_operator_counts(caps):
    """8 WAN sur ce châssis : 4 cellulaires, 2 filaires, 2 Wi-Fi.

    L'inventaire brut en déclare 11. Deux corrections le ramènent à ce que voit
    l'exploitant : le port Ethernet 3 est basculé en LAN (`asLan`) et n'est donc
    plus un WAN, et les deux emplacements de modem USB sont des logements
    d'accessoire, comptés à part.
    """
    assert len(caps.wan_types) == 8
    kinds = sorted(caps.wan_types.values())
    assert kinds.count("cellular") == 4
    assert kinds.count("ethernet") == 2
    assert kinds.count("wifi") == 2
    assert caps.cellular_wans == 4


def test_port_switched_to_lan_is_not_a_wan(caps, capture):
    """Le port 3 sert de port LAN : il reste déclaré, mais n'est plus disponible."""
    ports = parse_ports(capture["status.port"])
    assert ports["3"] is True and ports["1"] is False
    assert caps.lan_repurposed == ["3"]
    assert "3" not in caps.wan_types


def test_usb_slots_are_counted_apart(caps):
    assert caps.usb_modem_slots == 2
    assert "modem" not in caps.wan_types.values()


def test_dual_sim_modems_give_eight_slots(caps):
    """Chaque modem du HD4 MBX est bi-SIM : 4 modems, 8 emplacements."""
    assert caps.sim_slots == 8


def test_inventory_survives_missing_port_status(capture):
    """Sans `status.port`, l'inventaire reste utilisable, seulement moins précis."""
    caps = parse_capability(capture["info.capability"])
    assert len(caps.wan_types) == 9  # le port basculé en LAN n'est pas identifiable
    assert caps.lan_repurposed == []
    assert caps.usb_modem_slots == 2


# --- priorités des règles sortantes -------------------------------------------

def test_priorities_are_exposed(wans):
    """Les quatre SIM sont à la même priorité : elles se partagent le trafic."""
    assert {w.priority for w in wans} == {1}


def test_priority_of_a_wired_wan():
    payload = {"response": {"1": {
        "name": "WAN 1", "type": "ethernet", "enable": True, "statusLed": "green",
        "priority": 1, "ip": "10.0.0.2",
    }}}
    assert parse_wan_connection(payload)[0].priority == 1


def test_absent_priority_is_none():
    payload = {"response": {"1": {
        "name": "WAN 1", "type": "ethernet", "enable": True, "statusLed": "green"}}}
    assert parse_wan_connection(payload)[0].priority is None


def test_missing_capability_endpoint_degrades():
    caps = parse_capability({})
    assert caps.wan_types == {} and caps.cellular_wans == 0
    assert caps.gps is False and caps.per_wan_throughput is False


# --- débit par WAN ------------------------------------------------------------
#
# `status.traffic` est un endpoint distinct de `status.wan.connection`, aux mêmes
# identifiants de WAN. C'est la seule source de débit par WAN de l'API.

def test_traffic_is_keyed_like_wan_connection(capture, wans):
    rates = parse_traffic(capture["status.traffic"])
    assert {w.wan_id for w in wans} <= set(rates)


def test_traffic_is_applied_to_statuses(capture, wans):
    statuses = parse_wan_connection(capture["status.wan.connection"])
    apply_traffic(statuses, parse_traffic(capture["status.traffic"]))
    cellular = [s for s in statuses if s.kind == "cellular"]
    assert all(s.rx_kbps is not None and s.tx_kbps is not None for s in cellular)
    assert any(s.rx_kbps > 0 for s in cellular)


@pytest.mark.parametrize(
    "unit,raw,expected",
    [
        ("kbps", 1500, 1500.0),
        ("Mbps", 2, 2000.0),
        ("bps", 2000, 2.0),
        (None, 42, 42.0),  # unité absente : kbit/s, l'unité des matériels validés
    ],
)
def test_traffic_unit_conversion(unit, raw, expected):
    bandwidth = {"unit": unit, "4": {"overall": {"download": raw, "upload": 0}}}
    assert parse_traffic({"response": {"bandwidth": bandwidth}})["4"][0] == expected


def test_an_unknown_unit_publishes_no_rate_at_all():
    """Le repli à 1.0 publiait un débit CENT FOIS trop bas, comme une mesure.

    Ce cas était couvert par un paramètre du test ci-dessus, dont le commentaire
    disait « unité inattendue : pas de conversion hasardeuse » et dont
    l'assertion vérifiait exactement l'inverse : `("inconnue", 42, 42.0)`. Dix
    mégabits déclarés en « Mibps » ressortaient à 10 kbit/s.

    Ne rien rendre laisse `apply_traffic` mettre les deux sens à `None`, et
    l'écran sait dire une absence — il ne sait pas se méfier d'un chiffre.
    """
    bandwidth = {"unit": "Mibps", "4": {"overall": {"download": 10, "upload": 3}}}
    assert parse_traffic({"response": {"bandwidth": bandwidth}}) == {}

    # Et le relevé n'hérite d'aucun débit d'un cycle précédent.
    statuses = [WanRadioStatus(wan_id="4", wan_name="SIM 4", rx_kbps=999.0, tx_kbps=888.0)]
    apply_traffic(statuses, parse_traffic({"response": {"bandwidth": bandwidth}}))
    assert statuses[0].rx_kbps is None and statuses[0].tx_kbps is None


def test_traffic_absent_leaves_rates_empty():
    assert parse_traffic({}) == {}
    assert parse_traffic({"response": {"bandwidth": {"unit": "kbps"}}}) == {}


def test_apply_traffic_clears_unknown_wans():
    """Un WAN absent de status.traffic n'hérite pas du débit d'un cycle précédent."""
    statuses = [WanRadioStatus(wan_id="9", wan_name="X", rx_kbps=999.0, tx_kbps=888.0)]
    apply_traffic(statuses, {"4": (10.0, 5.0)})
    assert statuses[0].rx_kbps is None and statuses[0].tx_kbps is None


# --- WAN filaire ---------------------------------------------------------------

def test_disabled_ethernet_wans_stay_out(capture, wans):
    """Les 3 ports Ethernet du châssis sont désactivés sur cette capture."""
    response = capture["status.wan.connection"]["response"]
    assert all(response[k]["type"] == "ethernet" and response[k]["enable"] is False
               for k in ("1", "2", "3"))
    assert all(w.kind == "cellular" for w in wans)


def test_active_ethernet_wan_is_supervised():
    """Un lien fibre actif doit apparaître, avec son adresse et sa session."""
    payload = {"response": {"1": {
        "name": "WAN 1", "type": "ethernet", "enable": True, "statusLed": "green",
        "message": "Connected", "ip": "10.231.114.2", "uptime": 176,
    }}}
    wan = parse_wan_connection(payload)[0]
    assert (wan.kind, wan.connected, wan.rat) == ("ethernet", True, "Ethernet")
    assert wan.ip == "10.231.114.2" and wan.uptime_s == 176
    assert wan.status_message == "Connected"


def test_usb_modem_slots_remain_excluded():
    """Un emplacement USB sans périphérique n'est pas un lien à superviser."""
    payload = {"response": {"8": {
        "name": "USB 1", "type": "modem", "enable": True, "statusLed": "empty",
    }}}
    assert parse_wan_connection(payload) == []


# --- trois champs que le routeur publiait et que personne ne lisait -----------

def test_the_bond_latency_cutoff_is_read_from_the_peer(capture):
    """`latencyDiffCutoff` est l'un des trois mécanismes qui retirent un lien du
    bond SANS que son état change, et il n'était lu par personne.

    Ce que le firmware en fait : « le trafic est arrêté pour les liens qui
    dépassent de cette valeur, en millisecondes, celui de latence la plus
    basse ». Vérifié dans la base de ce déploiement : sur les 113 relevés qui
    franchissent le seuil, l'état du tunnel vaut `ACTIVE` cent fois sur cent, et
    les intervalles sans un seul paquet montant y sont quatre fois et demie plus
    fréquents qu'en dessous. `state: ACTIVE` ne veut donc pas dire « ce lien
    porte le flux », et l'écran l'affirmait.
    """
    from app.peplink.router_api import parse_latency_cutoff
    assert parse_latency_cutoff(capture["status.pepvpn.peer"]) == 500.0


def test_an_unconnected_peer_does_not_impose_its_cutoff():
    """Le seuil retenu est celui des pairs CONNECTÉS.

    La capture porte un second pair au profil 60000, sans état ni nom : un
    emplacement vide. Lui laisser imposer un seuil ferait juger les liens sur une
    règle qui ne s'applique pas.
    """
    from app.peplink.router_api import parse_latency_cutoff
    payload = {"response": {"peer": [
        {"peerId": "1-1", "status": "CONNECTED", "latencyDiffCutoff": 500},
        {"peerId": "60000-0", "status": "", "latencyDiffCutoff": 50},
    ]}}
    assert parse_latency_cutoff(payload) == 500.0
    # Aucun pair connecté : aucun seuil, et surtout pas zéro.
    assert parse_latency_cutoff({"response": {"peer": [{"peerId": "x", "status": ""}]}}) is None


def test_the_chassis_uptime_comes_from_the_router(capture):
    """L'ancienneté du châssis était DÉDUITE du maximum des liens.

    C'est de cette approximation que venaient les sessions scindées : le maximum
    retombe dès que le lien qui le portait quitte le relevé. La valeur exacte
    arrivait dans une charge utile déjà téléchargée, sous `uptime.second`.
    """
    from app.peplink.router_api import parse_system_info
    info = parse_system_info(capture["status.system.info"])
    assert info is not None and info.uptime_s == 6830


def test_sim_slots_are_reported_without_a_single_identifier(capture):
    """Le châssis déclare huit emplacements ; l'écran n'en montrait aucun.

    Une seconde carte présente EST un secours, et son absence est l'inverse. Mais
    l'IMSI et l'ICCID que le routeur publie juste à côté n'apprennent rien à la
    supervision et rendraient toute capture d'écran impartageable : ils ne
    doivent PAS franchir le parseur.
    """
    from app.peplink.router_api import parse_wan_connection
    cellulaires = [s for s in parse_wan_connection(capture["status.wan.connection"])
                   if s.kind == "cellular"]
    assert cellulaires, "la capture porte des liens cellulaires"
    for s in cellulaires:
        assert len(s.sim_slots) == 2, "ce matériel est bi-SIM sur chaque modem"
        assert all(x.card_detected for x in s.sim_slots), "les huit emplacements sont peuplés"
        # Un seul des deux porte un APN — ce qui SUGGÈRE lequel sert, sans le prouver.
        assert sum(1 for x in s.sim_slots if x.apn) == 1

    # Aucun identifiant, sous aucune forme.
    serialise = " ".join(
        f"{x.slot}{x.card_detected}{x.apn}" for s in cellulaires for x in s.sim_slots
    )
    for interdit in ("imsi", "iccid", "imei", "89330", "20801000"):
        assert interdit not in serialise.lower()


def test_a_modem_without_sim_block_reports_no_slot():
    """Un modèle qui ne publie rien ne reçoit pas d'emplacement inventé."""
    from app.peplink.router_api import _parse_sim_slots
    assert _parse_sim_slots({}) == []
    assert _parse_sim_slots({"sim": None}) == []
    assert _parse_sim_slots({"sim": []}) == []


# --- ce qui arrive dans la charge utile et n'était pas lu ---------------------

def test_the_active_sim_slot_is_published_and_now_read(wans):
    """Le parseur s'en abstenait au nom d'une observation FAUSSE.

    Sa docstring affirmait « L'emplacement ACTIF n'est pas publié : on ne le
    déduit donc pas. » Le champ `cellular.sim.<n>.active` existe pourtant sur les
    deux matériels de référence et sur les deux emplacements. L'exploitant ne
    savait pas laquelle de ses deux cartes travaillait.
    """
    actifs = {w.wan_id: [s.slot for s in w.sim_slots if s.active] for w in wans}
    assert actifs == {"4": ["1"], "5": ["2"], "6": ["1"], "7": ["1"]}, (
        "le modem du WAN 5 travaille sur sa CARTE 2 : c'est le fait que l'écran taisait"
    )
    # Un seul emplacement actif par modem, et une carte est présente dans les deux.
    for wan in wans:
        assert sum(s.active for s in wan.sim_slots) == 1
        assert all(s.card_detected for s in wan.sim_slots)


def test_the_data_allowance_travels_with_what_the_router_will_do(wans):
    """Un plafond sans l'action qui l'accompagne n'apprend que la moitié.

    Le routeur ne se contente pas d'avertir : `action` vaut
    `["email", "restrict"]`, donc il COUPERA la carte au dépassement. Ce n'est
    pas un avertissement, c'est une panne programmée — et c'est configuré sur un
    seul emplacement du châssis.
    """
    avec = [
        (w.wan_id, s.slot, s.allowance)
        for w in wans for s in w.sim_slots
        if s.allowance is not None and s.allowance.enabled
    ]
    assert len(avec) == 1, "un seul emplacement porte un plafond sur ce châssis"
    wan_id, slot, plafond = avec[0]
    assert (wan_id, slot) == ("4", "2")
    assert plafond.monthly_allowance_mb == 5120.0
    assert plafond.reset_day == 1
    assert plafond.actions == ["email", "restrict"]

    # Un plafond DÉSACTIVÉ ne rend pas zéro : il ne rend rien. Le routeur ne
    # publie les trois champs de détail que lorsque `enable` est vrai, et un
    # zéro s'y lirait comme « plafond atteint ».
    eteints = [
        s.allowance for w in wans for s in w.sim_slots
        if s.allowance is not None and not s.allowance.enabled
    ]
    assert eteints, "le cas désactivé doit être représenté dans la capture"
    assert all(a.monthly_allowance_mb is None and a.reset_day is None for a in eteints)


def test_the_modem_identity_explains_what_is_missing(wans):
    """Le projet SAIT que le Telit LM960 ne publie pas le PCI ; l'écran ne le disait pas.

    Deux colonnes entièrement remplies de tirets se lisent comme des mesures
    perdues. Nommer le modem est ce qui les rend lisibles comme une limite du
    matériel.
    """
    assert {(w.modem_vendor, w.modem_model) for w in wans} == {("Telit", "LM960")}
    assert {w.modem_firmware for w in wans} == {"32.00.116"}
    assert {w.network_mode for w in wans} == {"Auto (Generic)"}
    assert {w.carrier_aggregation for w in wans} == {True}
    assert {w.roaming_status for w in wans} == {"home"}
    # Et c'est bien ce modem-là qui ne publie aucun PCI.
    assert all(b.pci is None for w in wans for b in w.bands)


def test_the_mtu_is_read_per_link(capture, wans):
    """Une MTU différente d'un lien à l'autre est une cause classique de fragmentation.

    Sur CE châssis, les quatre liens supervisés partagent 1428 : il n'y a rien à
    signaler, et c'est le bon résultat. L'hétérogénéité existe bien dans la
    charge utile — 1440 sur le WAN 2, 1500 sur les deux Wi-Fi — mais ces
    WAN-là sont DÉSACTIVÉS, donc écartés du relevé. Le champ est lu pour que
    l'écart se voie le jour où deux liens actifs divergent, pas pour en inventer
    un aujourd'hui.
    """
    assert {w.mtu for w in wans} == {1428}, "les quatre SIM actives partagent leur MTU"

    brut = capture["status.wan.connection"]["response"]
    valeurs = {v.get("mtu") for v in brut.values() if isinstance(v, dict) and v.get("mtu")}
    assert valeurs == {1428, 1440, 1500}, "la charge utile porte bien trois MTU"
    actifs = {
        v.get("mtu") for v in brut.values()
        if isinstance(v, dict) and v.get("enable") and v.get("mtu")
    }
    assert actifs == {1428}, "et une seule parmi les WAN activés"


def test_period_volumes_come_from_a_payload_already_downloaded():
    """`status.traffic` porte DEUX blocs ; le parseur n'en lisait qu'un.

    `bandwidth` donne le débit instantané, `traffic` les volumes cumulés — et
    c'est leur rapprochement avec le plafond qui dit « 2 532 sur 5 120 Mo ».
    Zéro requête de plus : c'est la même réponse.
    """
    from app.peplink.router_api import apply_usage, parse_usage

    capture = _json.load(open(FIXTURE, encoding="utf-8"))
    volumes = parse_usage(capture["status.traffic"])
    assert volumes["4"] == (2532.0, 93.0)
    assert set(volumes) == {"4", "5", "6", "7"}

    statuses = parse_wan_connection(capture["status.wan.connection"])
    apply_usage(statuses, volumes)
    wan4 = next(w for w in statuses if w.wan_id == "4")
    assert (wan4.period_rx_mb, wan4.period_tx_mb) == (2532.0, 93.0)

    # Une unité inconnue ne vaut pas des mégaoctets : on ne publie rien.
    assert parse_usage({"response": {"traffic": {"unit": "Mio", "4": {"overall": {"download": 10}}}}}) == {}
    # Et l'endpoint absent — le cas du Dome Pro Duo — dégrade proprement.
    assert parse_usage({}) == {}


def test_the_chassis_publishes_its_health_and_its_contracts(capture):
    """Cinq champs de `status.system.info`, lue un cycle sur douze et jetée."""
    from app.peplink.router_api import parse_system_info

    info = parse_system_info(capture["status.system.info"])
    assert info.temperature_c == pytest.approx(43.4, abs=0.1)
    assert info.power_load_percent == pytest.approx(10.73)
    # Contrat EXPIRÉ depuis 2023 sur ce châssis, ce que rien n'annonçait.
    assert (info.care_plan, info.care_plan_active) == ("EssentialCare", False)
    # La version du TUNNEL, distincte du firmware : c'est elle qui gouverne la
    # compatibilité avec le concentrateur.
    assert info.pepvpn_version == "9.2.4" and info.firmware != "9.2.4"
    assert info.timezone == "Europe/Paris"
    assert info.remote_assistance is False and info.config_activating is False


def test_the_speedfusion_licence_is_read_with_both_of_its_booleans(capture):
    """`licensed` et `suspended` valent TOUS DEUX vrai, et c'est le fait à dire.

    Une licence existe, et le service est inutilisable. Un écran qui n'afficherait
    que le premier annoncerait « sous licence » d'un service suspendu : les deux
    booléens ne se lisent qu'ensemble.
    """
    from app.peplink.router_api import parse_capability

    caps = parse_capability(capture["info.capability"])
    assert caps.sfc_licensed is True
    assert caps.sfc_suspended is True
    # Le plafond de débit de la licence — invisible nulle part jusqu'ici.
    assert caps.sfc_throughput_limit_mbps == 200.0
    # Et la limite qui contraint RÉELLEMENT l'exploitation : les profils ACTIFS,
    # pas les 53 profils configurables publiés à côté.
    assert caps.pepvpn_max_active_profiles == 3


def test_the_bond_carries_its_own_age(capture):
    """« Depuis quand le tunnel est-il monté ? » n'avait aucune réponse.

    Ce n'est PAS l'ancienneté de chaque lien dans le bond : le routeur publie
    aussi un `time.second` par WAN, mais il y recopie la même valeur sur les cinq
    liens actifs. Le lire comme « ce lien est dans le bond depuis… » serait faux
    dès qu'un lien rejoint le bond après coup — et ce test le vérifie.
    """
    from app.peplink.router_api import parse_tunnel

    tunnel = parse_tunnel(capture["status.pepvpn.tunnel"], "1-1")
    assert tunnel.uptime_s == 22435  # 6 h 14

    brut = capture["status.pepvpn.tunnel"]["response"]["tunnel"]["1-1"]
    par_lien = {
        w: (v.get("time") or {}).get("second")
        for w, v in brut["wan"].items()
        if isinstance(v, dict) and v.get("state") == "ACTIVE"
    }
    assert len(par_lien) == 5
    assert set(par_lien.values()) == {22435}, (
        "le routeur recopie l'âge du TUNNEL sur chaque lien : ce n'est pas "
        "l'ancienneté du lien dans le bond, et on ne le lit donc pas comme telle"
    )


# --- les quatre endpoints qui existaient sans jamais être interrogés ----------
#
# Leur existence était établie par sondage — le firmware répond `401` quand un
# endpoint existe et exige une session, `999 unknown function` sinon — mais leur
# CONTENU ne l'était pas. Ces quatre captures le figent, et corrigent au passage
# trois attentes que l'exploration avait formulées.

def test_wan_latency_carries_an_hour_of_history_for_EVERY_link(capture):
    """Le plus gros trou fonctionnel de l'outil, comblé par une charge utile.

    Aujourd'hui la latence n'existe QUE dans le tunnel : un lien hors du bond
    n'en a aucune, un châssis sans profil SpeedFusion non plus, et le lien
    FILAIRE — qui porte souvent l'essentiel du débit — n'en a jamais eu.

    Cet endpoint en donne une par WAN, et il ne rend pas un instant : il rend
    **360 points espacés de 10 s**, soit l'heure écoulée. C'est ce qui change le
    calcul de son coût — le lire une fois par minute ne perd rien.
    """
    r = capture["status.wan.latency"]["response"]
    mesures = {
        w: b["latency"] for w, b in r.items()
        if isinstance(b, dict) and (b.get("latency") or {}).get("data")
    }
    # Le filaire en fait partie, et c'est tout l'intérêt.
    assert set(mesures) == {"1", "4", "5", "6", "7"}
    assert r["1"]["portType"] == "ethernet"

    for serie in mesures.values():
        assert len(serie["data"]) == 360
        assert serie["pointInterval"] == 10
        assert serie["pointEnd"] - serie["pointStart"] == 3590  # une heure

    # Le lien filaire est un ordre de grandeur sous les modems : 8 ms contre 31.
    filaire = [v for v in mesures["1"]["data"] if isinstance(v, (int, float))]
    modem = [v for v in mesures["4"]["data"] if isinstance(v, (int, float))]
    assert max(filaire) < min(modem)


def test_wan_quality_measures_nothing_new_and_that_is_the_point(capture):
    """95 Ko pour ce que l'outil relève déjà, deux fois plus vite.

    Même structure que la latence — 360 points à 10 s — mais les grandeurs sont
    celles que la scrutation collecte toutes les 5,3 s et stocke depuis des
    semaines. Sa valeur n'est donc PAS de mesurer du neuf : c'est de rattraper
    une heure manquée après une interruption de supervision.

    `ecio` est nul partout : c'est une grandeur 3G, sans objet sur un lien LTE.
    """
    q = capture["status.wan.quality"]["response"]["4"]["linkQuality"]
    assert set(q) >= {"rssi", "rsrp", "sinr", "rsrq", "signalLevel", "bearer", "bandName"}
    assert len(q["rsrp"]) == 360 and q["pointInterval"] == 10
    assert set(q["bearer"]) <= {"LTE", "LTE-A"}
    assert all(b.startswith("LTE Band ") for b in set(q["bandName"]))
    assert set(q["ecio"]) == {None}, "grandeur 3G : sans objet ici"


def test_the_router_log_says_what_the_supervisor_cannot_see(capture):
    """Ce que l'outil ne peut PAS savoir, et que le routeur date lui-même.

    Trois faits que la scrutation ne produira jamais, parce qu'ils décrivent des
    moments où elle ne regardait pas ou des causes qu'elle ne voit pas :

      - l'instant de MISE HORS TENSION, que l'outil ne fait qu'inférer ;
      - une bascule de carte SIM, avec sa cause ;
      - la cause d'une coupure filaire, distincte d'une coupure à l'autre.
    """
    log = capture["status.log"]["response"]["log"]

    # Le routeur DATE son extinction. L'outil, lui, en est réduit à déduire une
    # session d'alimentation d'un `uptime` qui repart.
    assert any("Powered off around" in l for l in log)

    # Une bascule de SIM, avec sa cause — c'est l'explication du fait que le
    # WAN 5 travaille sur sa carte 2, que `sim.active` montre sans l'expliquer.
    assert any("failed to read SIM A, switching to SIM B" in l for l in log)
    assert any("reaches failback timeout" in l for l in log)

    # Deux causes DIFFÉRENTES pour un même état « déconnecté », et deux remèdes
    # opposés : un câble à rebrancher, un résolveur à corriger.
    assert any("No cable detected" in l for l in log)
    assert any("WAN failed DNS test" in l for l in log)

    # Le tampon est court : cinquante lignes. Ce n'est pas un historique, c'est
    # une fenêtre — et nos propres connexions d'administration la consomment.
    assert len(log) == 50
    connexions = [l for l in log if "login successful" in l]
    assert len(connexions) > len(log) // 3, (
        "plus d'un tiers du journal est occupé par les connexions du superviseur"
    )


def test_the_pepvpn_profile_endpoint_carries_no_secret(capture):
    """La distinction d'avec `config.pepvpn.profile`, VÉRIFIÉE.

    Le projet s'interdit `config.pepvpn.profile`, qui expose la clé
    pré-partagée SpeedFusion en clair. `status.pepvpn?infoType=profile` est un
    endpoint différent, et la capture le confirme : aucune clé, aucun secret,
    seulement l'état des profils.

    Ce qu'il n'apporte PAS, en revanche : l'exploration espérait y trouver le
    troisième mécanisme qui retire un lien du bond — l'exclusion par le profil.
    La réponse décrit les profils, pas les WAN que chacun emploie. L'espoir
    n'est pas confirmé.
    """
    r = capture["status.pepvpn.profile"]["response"]["profile"]
    profils = {k: v for k, v in r.items() if isinstance(v, dict)}
    assert len(profils) == 2

    brut = _json.dumps(r).lower()
    for interdit in ("key", "secret", "password", "passphrase", "psk", "preshared"):
        assert interdit not in brut, f"« {interdit} » n'a rien à faire dans une réponse status.*"

    # Aucun WAN n'est nommé : le mécanisme d'exclusion par profil n'est pas là.
    assert not any(cle.lower().startswith("wan") for p in profils.values() for cle in p)

    # Le profil SpeedFusion Connect est déclaré SUSPENDU, ce qui recoupe la
    # licence lue dans `info.capability` — deux sources, un même fait.
    sfc = [p for p in profils.values() if p.get("speedfusionConnectProtect")]
    assert len(sfc) == 1 and sfc[0]["speedfusionConnectProtectSuspend"] is True


def test_path_latency_is_read_once_a_minute_and_never_repeated():
    """Une lecture par minute, et rien entre — pas même la dernière valeur.

    La charge utile porte l'heure écoulée : rien ne se perd à espacer. Mais les
    cycles intermédiaires laissent le champ à `None` plutôt que de recopier,
    parce qu'une mesure vieille de cinquante secondes n'est pas une mesure de
    maintenant. Ce projet préfère un trou à un chiffre qui se répète — c'est la
    même règle qui fait refuser un zéro pour une absence.
    """
    import asyncio
    from unittest.mock import AsyncMock, patch

    from app.fleet import RouterConfig
    from app.models import WanRadioStatus
    from app.peplink.router_api import _WAN_LATENCY_INTERVAL_S, RouterApiPeplinkClient

    charge = _json.load(open(FIXTURE, encoding="utf-8"))["status.wan.latency"]
    client = RouterApiPeplinkClient(RouterConfig(id="r", name="R", host="h"))
    liens = lambda: [WanRadioStatus(wan_id=w, wan_name=f"W {w}") for w in ("1", "4")]

    faux = AsyncMock(return_value=charge)
    with patch.object(client, "_get", faux):
        # Premier passage : on lit, et les deux liens reçoivent leur latence —
        # y compris le FILAIRE, qui n'en avait jamais eu.
        premiers = liens()
        asyncio.run(client._attach_path_latency(premiers))
        assert [s.path_latency_ms for s in premiers] == [8.0, 34.0]
        assert faux.await_count == 1

        # Cycle suivant, quelques secondes plus tard : aucune requête, et aucune
        # valeur recopiée.
        suivants = liens()
        asyncio.run(client._attach_path_latency(suivants))
        assert [s.path_latency_ms for s in suivants] == [None, None]
        assert faux.await_count == 1, "une seule lecture par minute"

        # Une minute écoulée : on relit.
        client._wan_latency_at -= _WAN_LATENCY_INTERVAL_S + 1
        apres = liens()
        asyncio.run(client._attach_path_latency(apres))
        assert [s.path_latency_ms for s in apres] == [8.0, 34.0]
        assert faux.await_count == 2

    assert _WAN_LATENCY_INTERVAL_S == 60.0


def test_a_model_without_the_endpoint_is_asked_only_once():
    """Un refus explicite condamne l'endpoint ; une panne passagère ne coûte rien.

    Et un échec de transport ne doit PAS marquer l'instant de lecture : sinon
    une coupure de trois secondes coûterait une minute de mesure.
    """
    import asyncio
    from unittest.mock import AsyncMock, patch

    from app.fleet import RouterConfig
    from app.models import WanRadioStatus
    from app.peplink.router_api import (
        RouterApiError,
        RouterApiPeplinkClient,
    )

    client = RouterApiPeplinkClient(RouterConfig(id="r", name="R", host="h"))

    # Refus du firmware : ce modèle ne le propose pas. On cesse de demander —
    # jusqu'à l'échéance de la mémoire des refus de `_get`, ici sous elle.
    inconnu = {"stat": "fail", "code": 999, "message": "unknown function"}
    transport = AsyncMock(return_value=inconnu)
    with patch.object(client, "_request", transport), \
         patch.object(client, "_ensure_authenticated", AsyncMock(return_value=1)):
        for _ in range(2):
            asyncio.run(client._attach_path_latency([WanRadioStatus(wan_id="4", wan_name="W")]))
    assert transport.await_count == 1
    assert client._retenu("status.wan.latency") is not None
    assert client._wan_latency_at is None

    # Panne de transport : la sonde reste vivante, et l'instant n'est pas marqué.
    autre = RouterApiPeplinkClient(RouterConfig(id="r", name="R", host="h"))
    with patch.object(autre, "_get", AsyncMock(side_effect=RouterApiError("réseau"))):
        asyncio.run(autre._attach_path_latency([WanRadioStatus(wan_id="4", wan_name="W")]))
    assert autre._retenu("status.wan.latency") is None
    assert autre._wan_latency_at is None, (
        "un échec ne consomme pas l'intervalle : sinon trois secondes de coupure "
        "coûteraient une minute de mesure"
    )


def test_a_link_that_stopped_reporting_does_not_yield_an_hour_old_value():
    """Le dernier point RENSEIGNÉ, pas le dernier point.

    Un trou en fin de série — le lien vient de se rétablir — ferait remonter une
    mesure vieille d'une heure comme si elle datait de dix secondes.
    """
    from app.peplink.router_api import parse_wan_latency

    charge = {"response": {"4": {"latency": {
        "data": [30, 31, None, None], "pointEnd": 1789325235,
        "pointStart": 1789325205, "pointInterval": 10,
    }}}}
    assert parse_wan_latency(charge) == {"4": 31.0}

    # Un WAN sans aucune mesure porte un bloc SANS clé `data` : c'est ainsi que
    # le firmware dit « rien ici », et cela distingue un port désactivé d'un
    # port à zéro milliseconde.
    vide = {"response": {"2": {"latency": {"pointEnd": 1, "pointStart": 0, "pointInterval": 10}}}}
    assert parse_wan_latency(vide) == {}

    # C-10 : le dernier point renseigné, mais pas au-delà de la minute. Un lien
    # rétabli avec 359 points non mesurés en fin de série faisait remonter une
    # mesure d'AVANT la coupure, que l'écran datait « il y a 3 s ».
    ancienne = {"response": {"4": {"latency": {
        "data": [42] + [None] * 359, "pointEnd": 1789325235,
        "pointStart": 1789321645, "pointInterval": 10,
    }}}}
    assert parse_wan_latency(ancienne) == {}
    # Six points = la minute qui sépare deux lectures : tenue.
    recente = {"response": {"4": {"latency": {
        "data": [None] * 10 + [37] + [None] * 5, "pointEnd": 1789325235,
        "pointStart": 1789325085, "pointInterval": 10,
    }}}}
    assert parse_wan_latency(recente) == {"4": 37.0}
    hors = {"response": {"4": {"latency": {
        "data": [None] * 10 + [37] + [None] * 6, "pointEnd": 1789325235,
        "pointStart": 1789325075, "pointInterval": 10,
    }}}}
    assert parse_wan_latency(hors) == {}
