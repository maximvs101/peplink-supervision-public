"""Validation contre la capture réelle d'un **Pepwave MAX HD2, firmware 8.5.4 build 5808**.

Troisième matériel validé, et le premier rencontré HORS du réseau de
développement — sur un autre sous-réseau, en HTTP sans TLS, chez deux
opérateurs. La fixture est la capture terrain du 19 septembre 2026, anonymisée
motif par motif : IMEI, IMSI (MCC-MNC conservé), ICCID (longueur conservée),
séries — la sienne et celle du concentrateur —, adresses MAC, IPv4 renumérotées,
position ramenée au centre de Paris, numéro de ligne dans un nom de WAN.

Ce que ce châssis a appris, et que les deux premiers ne pouvaient pas dire :

- `status.wan.connection` porte les identifiants APN des SIM (`username`,
  `password`) sur ce firmware. La règle « `status.*` ne porte pas de secret »
  était donc FAUSSE ici. Les deux clés ont été retirées de la fixture — pas
  remplacées par une valeur inventée, retirées — et la route de capture le dit.
- Un seul banc de firmware, sept WAN déclarés dont deux Wi-Fi et un USB, une
  seule porteuse LTE par SIM : rien de tout cela n'existait sur le MBX.
- Le schéma de l'URL n'était pas une hypothèse à tester : il était figé.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.peplink.router_api import (
    parse_bond_routes,
    parse_capability,
    parse_lan_networks,
    parse_location,
    parse_peers,
    parse_ports,
    parse_system_info,
    parse_traffic,
    parse_wan_connection,
    parse_wan_latency,
)

FIXTURE = Path(__file__).parent / "fixtures" / "max_hd2_8.5.4.json"


@pytest.fixture(scope="module")
def capture() -> dict:
    with open(FIXTURE, encoding="utf-8") as f:
        return json.load(f)


@pytest.fixture(scope="module")
def wans(capture) -> list:
    return parse_wan_connection(capture["status.wan.connection"])


# --- ce qui est supervisé, et ce qui ne l'est pas -------------------------------


def test_trois_liens_sur_sept_declares(capture, wans):
    """Sept WAN dans la réponse, trois supervisés : un filaire et deux SIM.

    Les quatre autres sont un filaire désactivé, deux Wi-Fi non raccordés et
    l'emplacement USB sans modem. Les exclure n'est pas les cacher : la fiche
    de capacités les compte, la table des liens ne les liste pas.
    """
    reponse = capture["status.wan.connection"]["response"]
    # Sept WAN, et quatre clés de métadonnées à côté — `order`, `timestamp`,
    # `reportTimestamp`, `supportGatewayProxy`. Le parseur ne lit que les entrées
    # numérotées ; ce test avait d'abord compté onze.
    assert [k for k in reponse if k.isdigit()] == ["1", "2", "3", "4", "5", "6", "7"]
    # L'USB est déclaré sous un QUATRIÈME type, `modem`, que ni le MBX ni le
    # Dome n'employaient.
    assert reponse["6"]["type"] == "modem"
    assert [(w.wan_id, w.kind) for w in wans] == [
        ("1", "ethernet"), ("4", "cellular"), ("5", "cellular"),
    ]


def test_l_emplacement_usb_est_declare_mais_pas_supervise(capture, wans):
    usb = capture["status.wan.connection"]["response"]["6"]
    assert usb["name"] == "USB"
    assert "6" not in {w.wan_id for w in wans}


# --- deux opérateurs, une porteuse ---------------------------------------------


def test_deux_operateurs_et_leurs_plmn(wans):
    """Premier châssis vu chez DEUX opérateurs. Le PLMN doit suivre chacun."""
    cell = {w.wan_id: w for w in wans if w.kind == "cellular"}
    assert (cell["4"].operator, cell["4"].plmn) == ("Bouygues", "208-20")
    assert (cell["5"].operator, cell["5"].plmn) == ("Orange", "208-01")


def test_une_seule_porteuse_lte_par_sim(capture, wans):
    """Le MBX agrégeait quatre porteuses ; ici chaque SIM n'en a qu'une.

    `band` doit rendre cette porteuse-là, et non se taire faute d'agrégation.
    """
    for w in wans:
        if w.kind != "cellular":
            continue
        bandes = capture["status.wan.connection"]["response"][w.wan_id]["cellular"]["rat"][0]["band"]
        assert len(bandes) == 1
        assert w.band == "B3"
        assert w.rat == "LTE"


def test_les_mesures_radio_sont_lues_par_sim(wans):
    cell = {w.wan_id: w for w in wans if w.kind == "cellular"}
    assert cell["4"].rsrp == -76 and cell["4"].sinr is not None
    assert cell["5"].rsrp == -91 and cell["5"].sinr is not None
    for w in cell.values():
        assert w.cell_id is not None and w.enb_id is not None, "LTE : la station se déduit"


# --- le châssis ------------------------------------------------------------------


def test_identite_et_absence_de_telemetrie_d_alimentation(capture):
    """Pas d'alimentation, pas de température : `None`, jamais zéro.

    Le MAX HD2 ne publie ni consommation ni capteur thermique. Un « 0 W »
    se lirait comme un équipement éteint qui répond quand même.
    """
    info = parse_system_info(capture["status.system.info"])
    assert info is not None
    assert info.model == "Pepwave MAX HD2"
    assert info.firmware == "8.5.4 build 5808"
    assert info.hardware_revision == "5"
    assert info.power_watts is None
    assert info.temperature_c is None
    assert info.power_load_percent is None
    assert info.power_sources == []


def test_le_contrat_de_support_est_expire(capture):
    """Un fait publié par le châssis, que l'écran traduit en badge."""
    info = parse_system_info(capture["status.system.info"])
    assert info.care_plan == "EssentialCare"
    assert info.care_plan_active is False


# --- la fiche de capacités --------------------------------------------------------


def test_sept_wan_dont_deux_wifi_et_un_usb(capture):
    """Un inventaire jamais vu : deux WAN Wi-Fi, un emplacement USB, deux SIM."""
    caps = parse_capability(capture["info.capability"], parse_ports(capture["status.port"]))
    assert caps.wan_types == {
        "1": "ethernet", "2": "ethernet", "3": "wifi", "4": "cellular", "5": "cellular", "7": "wifi",
    }
    assert caps.sim_slots == 2
    assert caps.usb_modem_slots == 1
    assert caps.gps is True and caps.pepvpn is True
    assert caps.source == "declared" and caps.provisional is False
    assert caps.lan_repurposed == []


# --- le tunnel, tombé -------------------------------------------------------------


def test_le_pair_est_declare_mais_injoignable(capture):
    """PEER_DOWN sur tous les liens : le concentrateur n'est pas joignable d'ici.

    Ce n'est pas un défaut de lecture, c'est l'état de l'équipement sur ce
    réseau — et l'écran l'a dit : « Liaison rompue ».
    """
    peers = parse_peers(capture["status.pepvpn.peer"])
    assert len(peers) == 1
    _peer_id, _nom, connecte = peers[0]
    assert connecte is False
    assert parse_bond_routes(capture["status.pepvpn.peer"]) == []
    # L'entrée `status.pepvpn.tunnel` de cette fixture NE DOCUMENTE PAS ce que
    # le collecteur lit : capturée sans `tunnelOption`, c'est la réponse à une
    # requête qu'il n'envoie jamais — et sur un pair tombé il ne lit pas le
    # tunnel du tout. Un `parse_tunnel` sur ce corps vide serait vrai pour de
    # mauvaises raisons ; le fait vérifiable est l'état du pair.
    assert "status.pepvpn.tunnel" in capture, "présente, mais non représentative — voir ci-dessus"


def test_l_identite_du_pair_est_son_numero_de_serie(capture):
    """Le `username` du pair est la SÉRIE du concentrateur — anonymisée comme telle.

    Il a failli être supprimé par la règle des secrets. C'est une identité,
    pas un identifiant de connexion : elle reste, sous une forme synthétique.
    """
    pair = capture["status.pepvpn.peer"]["response"]["peer"][0]
    assert pair["username"] == "0000-0000-0002"


# --- les grandeurs qui viennent d'ailleurs --------------------------------------


def test_le_debit_par_wan_est_lu(capture):
    assert set(parse_traffic(capture["status.traffic"])) == {"1", "4", "5"}


def test_la_latence_du_filaire_est_une_vraie_mesure_sous_la_milliseconde(capture):
    """0 ms sur l'Ethernet n'est PAS un zéro pour une absence.

    La série brute porte 360 points allant de 0 à 6 ms : une cible de sonde
    très proche, côté LAN, arrondie à l'entier. C'est une lecture, et le
    dernier point vaut ce qu'il vaut. La vérifier a évité de « corriger » un
    parseur qui avait raison.
    """
    serie = capture["status.wan.latency"]["response"]["1"]["latency"]["data"]
    assert len(serie) == 360
    assert set(serie) <= {0, 1, 2, 3, 4, 5, 6}
    assert len(set(serie)) > 1, "si tout valait 0, ce serait une absence déguisée"
    lat = parse_wan_latency(capture["status.wan.latency"])
    assert set(lat) == {"1", "4", "5"}
    # Deux SIM sur une cible de sonde à quelques dizaines de millisecondes.
    assert 10 <= lat["4"] <= 60 and 10 <= lat["5"] <= 60


def test_les_wan_sans_serie_de_latence_n_en_recoivent_pas(capture):
    """Cinq WAN n'ont aucun point : ils n'apparaissent pas, plutôt qu'à zéro."""
    lat = parse_wan_latency(capture["status.wan.latency"])
    for absent in ("2", "3", "6", "7"):
        assert absent not in lat


def test_la_position_gps_est_fixee(capture):
    fix = parse_location(capture["info.location"])
    assert fix is not None
    assert fix.hdop is not None and fix.hdop < 2.0


def test_les_reseaux_lan_sont_nommes_depuis_status_port(capture):
    """« C'est quoi cette adresse en 10.10.1.1 ? je pensais que le routeur
    était sur 192.168.56.1. » Les deux : un LAN natif et un VLAN pour le Wi-Fi.
    La réponse était dans `status.port`, déjà téléchargé pour l'inventaire des
    ports — sans ouvrir `config.lan`, qui reste interdit. Le numéro de VLAN est
    un nom sur le fil ; 0 est le réseau natif.
    """
    import json

    reseaux = parse_lan_networks(capture["status.port"])
    assert [(r.vlan_id, r.name) for r in reseaux] == [(0, "RESEAU-DEMO_EXT1"), (1, "WIFI Range")]

    caps = parse_capability(capture["info.capability"], parse_ports(capture["status.port"]), reseaux)
    fil = json.loads(caps.model_dump_json())["lan_networks"]
    assert fil == [{"vlan_id": "0", "name": "RESEAU-DEMO_EXT1"}, {"vlan_id": "1", "name": "WIFI Range"}]

    # Une fiche sans `status.port` n'affirme rien : liste vide, pas de VLAN 0 inventé.
    assert parse_capability(capture["info.capability"]).lan_networks == []
    assert parse_lan_networks({"stat": "ok", "response": {}}) == []


def test_a_lone_carrier_at_pci_zero_is_not_a_measure(capture):
    """Les deux WAN — Bouygues et Orange, deux cellules — publient une porteuse
    unique à `pci: 0`. Deux cellules d'opérateurs différents au même PCI, c'est
    une chance sur 504² ; un modem qui ne le publie pas, c'est ce qu'on a sous
    les yeux. La garde exigeait plusieurs porteuses et laissait passer ce zéro.
    """
    wans = [w for w in parse_wan_connection(capture["status.wan.connection"]) if w.kind == "cellular"]
    connectes = [w for w in wans if w.connected and w.bands]
    assert len(connectes) >= 2
    assert all(len(w.bands) == 1 for w in connectes), "une porteuse par modem sur le HD2"
    assert all(w.pci is None for w in connectes)
    assert all(b.pci is None for w in connectes for b in w.bands)
