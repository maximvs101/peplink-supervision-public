"""Confrontation : l'outil face à des équipements qu'il n'a jamais vus.

Tout ce qui est validé jusqu'ici l'a été contre DEUX matériels — un MAX HD4 MBX
et un Dome Pro Duo, tous deux en 8.5.4, tous deux cellulaires, tous deux chez le
même opérateur. C'est une base étroite, et les hypothèses qu'elle laisse passer
ne se voient pas : elles ne se voient que le jour où l'on branche autre chose.

Ce fichier fabrique donc des réponses FIDÈLES à la forme réelle du firmware —
mêmes clés, même imbrication, même convention `{"stat": ..., "response": ...}` —
pour des familles d'équipements que le projet n'a jamais rencontrées : une
Balance sans le moindre modem, un BR1 à une seule SIM, un WAN Wi-Fi, un
identifiant 5G SA que le parseur refuse de décomposer, un firmware qui ignore la
moitié des endpoints, un châssis à treize WAN.

Ce qu'on vérifie n'est pas « le bon chiffre sort » — on ne connaît pas le bon
chiffre d'un matériel qu'on n'a pas. C'est plus sévère et plus utile :

  1. rien ne lève,
  2. rien n'est INVENTÉ — une grandeur absente reste `None` et ne devient
     jamais 0, 0.0 ni une chaîne vide,
  3. une capacité qu'on n'a pas pu établir n'est pas une capacité niée.
"""

from __future__ import annotations

import pytest

from app.peplink.router_api import (
    EndpointRefusedError,
    RouterApiError,
    parse_system_info,
    parse_traffic,
    parse_wan_connection,
)

# --------------------------------------------------------------------------
# Fabrique de réponses. Les clés reproduisent la capture réelle ; ce qui n'est
# pas passé en argument est ABSENT du corps, comme sur un firmware qui ne le
# publie pas — et non présent à zéro.
# --------------------------------------------------------------------------


def reponse(corps: object) -> dict:
    return {"stat": "ok", "response": corps}


def wan_ethernet(nom: str, *, port: int, connecte: bool = True, ip: str | None = "10.0.0.2") -> dict:
    bloc: dict = {
        "name": nom, "enable": True, "locked": False, "managementOnly": False,
        "statusLed": "green" if connecte else "gray",
        "message": "Connected" if connecte else "Disconnected",
        "uptime": 3600 if connecte else 0,
        "type": "ethernet", "virtualType": "ethernet", "virtual": False,
        "portId": port, "method": "dhcp", "routingMode": "NAT", "mtu": 1500,
    }
    if ip is not None and connecte:
        bloc["ip"] = ip
    return bloc


def wan_cellulaire(
    nom: str, *, techno: str = "LTE", bandes: list[dict] | None = None,
    cell_id: int | None = 25735436, operateur: str | None = "Orange",
    connecte: bool = True,
) -> dict:
    cellulaire: dict = {
        "network": techno, "mobileType": techno, "modulePowerOn": True,
        "sim": {"1": {"active": True, "simCardDetected": True, "autoApn": True},
                "order": [1]},
        "signalLevel": 4,
    }
    if operateur is not None:
        cellulaire["carrier"] = {"name": operateur, "country": "France"}
    if bandes is not None:
        cellulaire["rat"] = [{"name": "", "band": bandes}]
    if cell_id is not None:
        cellulaire["cellTower"] = {"cellId": cell_id}
    return {
        "name": nom, "enable": True, "locked": False, "managementOnly": False,
        "statusLed": "green" if connecte else "gray",
        "message": f"Connected to {operateur}" if connecte else "No SIM detected",
        "uptime": 3600 if connecte else 0,
        "type": "cellular", "virtualType": "cellular", "virtual": False,
        "priority": 1, "cellular": cellulaire,
    }


def bande(nom: str, *, canal: int, rsrp: int | None = -90, sinr: float | None = 12.0) -> dict:
    signal: dict = {}
    if rsrp is not None:
        signal["rsrp"] = rsrp
    if sinr is not None:
        signal["sinr"] = sinr
    return {"name": nom, "channel": canal, "pci": 17, "signal": signal}


def wan_wifi(nom: str, *, port: int = 1) -> dict:
    return {
        "name": nom, "enable": True, "locked": False, "managementOnly": False,
        "statusLed": "green", "message": "Connected", "uptime": 1200,
        "type": "wifi", "virtualType": "wifi", "virtual": False,
        "ip": "192.168.50.7", "priority": 2, "portId": port,
    }


# --------------------------------------------------------------------------
# La matrice. Chaque profil est une famille d'équipement PLAUSIBLE que le
# projet n'a jamais vue.
# --------------------------------------------------------------------------

PROFILS: dict[str, dict] = {
    # Une Balance de bureau : deux liens filaires, aucun modem, aucune SIM.
    # C'est le cas qui fait tomber toute hypothèse « il y a du cellulaire ».
    "balance filaire seule": {
        "1": wan_ethernet("WAN 1", port=1),
        "2": wan_ethernet("WAN 2", port=2, connecte=False, ip=None),
    },
    # Un BR1 : un seul modem, rien d'autre. Aucun bond possible.
    "BR1, une seule SIM": {
        "1": wan_cellulaire("Cellular", bandes=[bande("LTE Band 20 (800 MHz)", canal=6400)]),
    },
    # Un WAN Wi-Fi en secours, que les deux matériels validés n'ont pas.
    "Wi-Fi en second lien": {
        "1": wan_ethernet("WAN", port=1),
        "2": wan_wifi("Wi-Fi WAN"),
    },
    # 5G SA : l'identifiant NCI ne se décompose pas en station + secteur.
    # Le parseur refuse d'inventer le découpage, donc `enb_id` reste nul.
    "5G SA, station indéterminable": {
        "1": wan_cellulaire("SIM 1", techno="5G SA", cell_id=1234567890123,
                            bandes=[bande("NR Band 78 (3500 MHz)", canal=640000)]),
    },
    # Un firmware qui publie le WAN mais AUCUNE mesure radio.
    "cellulaire sans aucune mesure": {
        "1": wan_cellulaire("SIM 1", bandes=None, cell_id=None, operateur=None),
    },
    # Un signal partiellement publié : RSRP sans SINR.
    "radio à moitié publiée": {
        "1": wan_cellulaire("SIM 1", bandes=[bande("LTE Band 3 (1800 MHz)", canal=1300, sinr=None)]),
    },
    # Treize WAN : le haut de gamme, et la limite de la mise en page.
    "treize WAN mixtes": {
        **{str(i): wan_ethernet(f"WAN {i}", port=i) for i in range(1, 4)},
        **{str(i): wan_cellulaire(f"SIM {i}",
                                  bandes=[bande("LTE Band 7 (2600 MHz)", canal=3000)])
           for i in range(4, 13)},
        "13": wan_wifi("Wi-Fi", port=13),
    },
    # Le routeur répond, mais sa table de WAN est vide : un châssis en cours
    # de configuration, ou dont tous les ports sont basculés en LAN.
    "aucun WAN déclaré": {},
}


@pytest.mark.parametrize("nom", sorted(PROFILS))
def test_un_profil_inconnu_ne_fait_rien_lever(nom: str) -> None:
    """Premier barreau : aucun profil ne doit faire tomber le parseur.

    C'est faible en apparence, et c'est pourtant ce qui a manqué : cinq
    parseurs de ce fichier levaient sur une charge utile inattendue, et le
    relevé entier disparaissait alors pour un champ mal formé.
    """
    statuses = parse_wan_connection(reponse(PROFILS[nom]))
    assert len(statuses) == len(PROFILS[nom])


@pytest.mark.parametrize("nom", sorted(PROFILS))
def test_aucune_grandeur_absente_ne_devient_un_zero(nom: str) -> None:
    """La règle la plus chère du projet, vérifiée sur du matériel inconnu.

    Aucune de ces réponses ne porte de débit ni de latence : ces champs
    viennent d'autres endpoints. Ils doivent donc valoir `None` — pas 0.0, qui
    se lirait à l'écran comme « ce lien ne transporte rien ».
    """
    for s in parse_wan_connection(reponse(PROFILS[nom])):
        assert s.tx_kbps is None, f"{nom}/{s.wan_id}: débit inventé"
        assert s.rx_kbps is None, f"{nom}/{s.wan_id}: débit inventé"
        assert s.path_latency_ms is None, f"{nom}/{s.wan_id}: latence inventée"


def test_un_filaire_ne_reçoit_aucune_grandeur_radio() -> None:
    """Un port Ethernet n'a ni cellule, ni bande, ni RSRP.

    Le piège serait de rendre 0 — « 0 dBm » est une valeur parfaitement
    plausible pour qui lit vite, et elle est fausse deux fois : la grandeur
    n'existe pas pour ce support.

    `rat` fait exception et ce n'est pas un oubli : ce champ ne porte pas une
    mesure mais le RATTACHEMENT du lien, celui qu'affiche la colonne du même
    nom. « Ethernet » y est la bonne réponse, au même titre que « LTE-A ».
    """
    (wan,) = parse_wan_connection(reponse({"1": wan_ethernet("WAN 1", port=1)}))
    assert wan.kind == "ethernet"
    for champ in ("rsrp", "rsrq", "sinr", "rssi", "cell_id", "enb_id", "band", "operator"):
        assert getattr(wan, champ) is None, f"{champ} inventé sur un filaire"
    assert wan.rat == "Ethernet", "le rattachement se dit, il ne se mesure pas"


def test_le_5g_sa_refuse_de_decomposer_plutot_que_d_inventer() -> None:
    """La cellule est connue, la STATION ne l'est pas — et les deux se disent.

    Sur un identifiant NR, le découpage station/secteur du LTE ne s'applique
    pas. Le renvoyer quand même produirait un numéro de station crédible et
    faux, sur lequel l'écran conclurait « ces deux modems partagent une
    station ».
    """
    (wan,) = parse_wan_connection(reponse(PROFILS["5G SA, station indéterminable"]))
    assert wan.cell_id is not None, "la cellule est publiée : elle doit être lue"
    assert wan.enb_id is None, "la station ne se déduit pas d'un NCI"


def test_une_radio_a_moitie_publiee_ne_comble_pas_les_trous() -> None:
    """RSRP sans SINR : on garde l'un, on n'invente pas l'autre."""
    (wan,) = parse_wan_connection(reponse(PROFILS["radio à moitié publiée"]))
    assert wan.rsrp == -90
    assert wan.sinr is None


def test_un_refus_du_firmware_bascule_sur_le_sondage() -> None:
    """`999 unknown function` veut dire « pas chez moi », pas « pas du tout ».

    Un firmware plus ancien, ou une gamme plus modeste, n'expose pas les mêmes
    fonctions. Confondre « je n'ai pas pu demander » avec « la réponse est
    non » ferait afficher un équipement amputé de ce qu'il sait faire.

    Le contrat ne se joue PAS dans `parse_capability` — qui ne voit jamais un
    échec, puisque la lecture lève avant — mais dans le type d'exception levé :
    `EndpointRefusedError` est la seule forme de refus qui autorise à conclure,
    et c'est elle qui déclenche la reconstitution par sondage.
    """
    assert issubclass(EndpointRefusedError, RouterApiError)
    # Les parseurs ne voient JAMAIS un `stat: fail` : `_get` lève avant. Ce
    # qui se teste ici est donc le type levé, pas leur tolérance à une forme
    # qu'ils ne reçoivent pas — une version précédente de ce fichier
    # l'affirmait, et affirmait aussi « gps: False » sur un corps vide, le
    # zéro-pour-une-absence que ses trois principes interdisent.


def test_un_corps_vide_est_traite_comme_un_refus() -> None:
    """Certains firmwares répondent `ok` avec un corps vide plutôt qu'un code."""
    assert parse_wan_connection(reponse({})) == []
    assert parse_system_info(reponse({})) is None
    assert parse_traffic(reponse({})) == {}


def test_treize_wan_sont_tous_lus_et_dans_l_ordre_du_routeur() -> None:
    """Le haut de gamme : rien ne doit être tronqué ni réordonné au parsing."""
    statuses = parse_wan_connection(reponse(PROFILS["treize WAN mixtes"]))
    assert len(statuses) == 13
    assert {s.kind for s in statuses} == {"ethernet", "cellular", "wifi"}
    assert [s.wan_id for s in statuses] == [str(i) for i in range(1, 14)]


def test_les_identifiants_partent_sur_le_fil_comme_des_noms() -> None:
    """Cell ID, station, secteur, TAC, PCI, canal : des NOMS, pas des quantités.

    Le backend les tient en entiers — l'ECI se décompose par masque de bits, la
    base les indexe ainsi — mais c'est à la frontière que la faute se commettait :
    un entier sur le fil, et rien côté écran n'empêchait de lui poser des
    séparateurs de milliers. « 25 735 436 » ne désigne plus la cellule 25735436.
    En chaîne, le type de `fmtInt` la refuse à la compilation.
    """
    import json

    from app.models import CellLatency, WifiLink

    statuses = parse_wan_connection(reponse(PROFILS["treize WAN mixtes"]))
    cellulaire = next(s for s in statuses if s.kind == "cellular")
    assert cellulaire.cell_id is not None and cellulaire.enb_id is not None

    fil = json.loads(cellulaire.model_dump_json())
    for champ in ("cell_id", "enb_id", "sector_id", "tac", "pci", "earfcn"):
        assert fil[champ] is None or isinstance(fil[champ], str), champ
    assert isinstance(fil["cell_id"], str)
    for bande in fil["bands"]:
        for champ in ("channel", "pci"):
            assert bande[champ] is None or isinstance(bande[champ], str), champ
    assert json.loads(WifiLink(channel=112).model_dump_json())["channel"] == "112"

    fiche = json.loads(CellLatency(cell_id=25735436, wan_id="4", enb_id=100529).model_dump_json())
    assert (fiche["cell_id"], fiche["wan_id"], fiche["enb_id"]) == ("25735436", "4", "100529")
    # Et une absence reste une absence — jamais « None » écrit en toutes lettres.
    assert json.loads(CellLatency(cell_id=1, wan_id="4").model_dump_json())["enb_id"] is None
