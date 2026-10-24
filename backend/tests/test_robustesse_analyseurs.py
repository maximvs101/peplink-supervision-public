"""Un sous-bloc d'un autre type ne doit jamais coûter le relevé.

Les analyseurs protégeaient leurs sous-blocs par un `or {}` : il couvre `None`
et le vide, pas une chaîne. `carrier: "Orange"` à la place de
`carrier: {"name": "Orange"}` levait `AttributeError` dans
`parse_wan_connection` — tout le relevé était perdu, et le poller étiquetait
« injoignable » un équipement qui venait de répondre.

La méthode : partir des trois captures réelles (MAX HD2, MAX HD4 MBX, Dome Pro
Duo), remplacer UN à UN chaque objet et chaque liste de la charge utile par une
valeur d'un autre type, et passer le résultat à l'analyseur de l'endpoint. Il
ne doit rien lever. Pour `status.wan.connection`, il doit en plus garder tous
les WAN : un détail mal typé ne retire pas le lien qui le porte.
"""

from __future__ import annotations

import copy
import json
from datetime import datetime, timezone
from pathlib import Path

import pytest

from app.peplink.router_api import (
    horloge_routeur,
    parse_bond_profiles,
    parse_bond_routes,
    parse_capability,
    parse_disabled_wans,
    parse_lan_clients,
    parse_lan_networks,
    parse_latency_cutoff,
    parse_latency_series,
    parse_location,
    parse_peers,
    parse_ports,
    parse_quality_series,
    parse_router_log,
    parse_system_info,
    parse_traffic,
    parse_tunnel,
    parse_usage,
    parse_wan_connection,
    parse_wan_latency,
    peer_state,
    tunnel_counters,
)

FIXTURES = Path(__file__).parent / "fixtures"
CAPTURES = ["max_hd2_8.5.4.json", "max_hd4_mbx_8.5.4.json", "dome_pro_duo_8.5.4.json"]
MAINTENANT = datetime(2026, 9, 25, 8, 0, tzinfo=timezone.utc)

# Ce qui remplace un objet ou une liste : une chaîne (le cas `carrier: "Orange"`),
# un nombre, une liste d'un autre contenu, un objet vide là où une liste était.
INTRUS = ("Orange", 7, ["x"], {"x": "y"})


def _analyseurs(endpoint: str, capture: dict):
    """Les analyseurs qu'un relevé de cet endpoint traverse en production."""
    if endpoint == "status.wan.connection":
        return [parse_wan_connection, parse_disabled_wans]
    if endpoint == "status.system.info":
        return [parse_system_info, horloge_routeur]
    if endpoint == "info.capability":
        return [parse_capability]
    if endpoint == "status.port":
        return [parse_ports, parse_lan_networks]
    if endpoint == "status.traffic":
        return [parse_traffic, parse_usage]
    if endpoint == "status.wan.latency":
        return [parse_wan_latency, lambda p: parse_latency_series(p, MAINTENANT)]
    if endpoint == "status.wan.quality":
        return [lambda p: parse_quality_series(p, MAINTENANT)]
    if endpoint == "status.log":
        horloge = horloge_routeur(capture.get("status.system.info") or {})
        return [lambda p: parse_router_log(p, horloge, {})]
    if endpoint == "status.client":
        return [lambda p: parse_lan_clients(p, "192.168.50.10")]
    if endpoint == "info.location":
        return [parse_location]
    if endpoint == "status.pepvpn.peer":
        return [parse_peers, parse_bond_routes, parse_latency_cutoff, peer_state]
    if endpoint == "status.pepvpn.profile":
        return [parse_bond_profiles]
    if endpoint == "status.pepvpn.tunnel":
        pairs = parse_peers(capture.get("status.pepvpn.peer") or {})
        pair = pairs[0][0] if pairs else "1"
        return [lambda p: parse_tunnel(p, pair), lambda p: tunnel_counters(p, pair)]
    raise AssertionError(f"endpoint sans analyseur déclaré : {endpoint}")


def _chemins(noeud, chemin=()):
    """Chaque objet ou liste SOUS la racine `response`, avec son chemin."""
    if isinstance(noeud, dict):
        enfants = noeud.items()
    elif isinstance(noeud, list):
        enfants = enumerate(noeud)
    else:
        return
    for cle, enfant in enfants:
        if isinstance(enfant, (dict, list)):
            yield (*chemin, cle)
            yield from _chemins(enfant, (*chemin, cle))


def _remplacer(payload: dict, chemin: tuple, valeur) -> dict:
    copie = copy.deepcopy(payload)
    noeud = copie["response"]
    for cle in chemin[:-1]:
        noeud = noeud[cle]
    noeud[chemin[-1]] = valeur
    return copie


def _cas():
    for nom in CAPTURES:
        capture = json.loads((FIXTURES / nom).read_text(encoding="utf-8"))
        for endpoint, payload in capture.items():
            if isinstance(payload, dict) and isinstance(payload.get("response"), (dict, list)):
                yield pytest.param(capture, endpoint, payload, id=f"{nom}:{endpoint}")


@pytest.mark.parametrize(("capture", "endpoint", "payload"), list(_cas()))
def test_un_sous_bloc_d_un_autre_type_ne_fait_rien_lever(capture, endpoint, payload):
    analyseurs = _analyseurs(endpoint, capture)
    chemins = list(_chemins(payload["response"]))
    # Les séries de l'heure écoulée comptent des centaines de points identiques
    # de forme : les premiers suffisent à couvrir chaque structure.
    if len(chemins) > 200:
        chemins = chemins[:200]
    attendus = (
        len(parse_wan_connection(payload)) if endpoint == "status.wan.connection" else None
    )
    fautes = []
    for chemin in chemins:
        for intrus in INTRUS:
            altere = _remplacer(payload, chemin, intrus)
            for analyseur in analyseurs:
                try:
                    resultat = analyseur(altere)
                except Exception as exc:
                    fautes.append(f"{'/'.join(map(str, chemin))} = {intrus!r} : "
                                  f"{type(exc).__name__}: {exc}")
                    continue
                # Un détail mal typé SOUS un WAN ne retire pas ce WAN.
                sous_un_wan = analyseur is parse_wan_connection and len(chemin) >= 2
                if sous_un_wan and len(resultat) != attendus:
                    fautes.append(f"{'/'.join(map(str, chemin))} = {intrus!r} : "
                                  f"{len(resultat)} WAN au lieu de {attendus}")
    assert not fautes, f"{len(fautes)} fautes, dont :\n" + "\n".join(fautes[:15])


@pytest.mark.parametrize("champ", ["name", "ip", "message"])
@pytest.mark.parametrize("intrus", [{"x": 1}, ["x"], 7])
def test_un_champ_texte_mal_type_ne_coute_pas_le_wan(champ, intrus):
    """`wan.get("name")`, `ip`, `message` partaient tels quels dans le modèle :
    un objet à la place d'une chaîne y levait une erreur de validation."""
    for nom in CAPTURES:
        capture = json.loads((FIXTURES / nom).read_text(encoding="utf-8"))
        payload = copy.deepcopy(capture["status.wan.connection"])
        attendus = len(parse_wan_connection(payload))
        for wan in payload["response"].values():
            if isinstance(wan, dict):
                wan[champ] = intrus
                if isinstance(wan.get("cellular"), dict):
                    carrier = wan["cellular"].get("carrier")
                    if isinstance(carrier, dict):
                        carrier["name"] = intrus
        statuses = parse_wan_connection(payload)
        assert len(statuses) == attendus, nom
        for s in statuses:
            assert isinstance(s.wan_name, str) and s.wan_name
            assert s.ip is None or isinstance(s.ip, str)
            assert s.operator is None or isinstance(s.operator, str)
            assert s.status_message is None or isinstance(s.status_message, str)


def test_carrier_en_chaine_ne_perd_pas_le_releve():
    """Le cas de la revue, tel quel."""
    charge = {"stat": "ok", "response": {"2": {
        "type": "cellular", "statusLed": "green", "name": "SIM",
        "cellular": {"network": "LTE", "carrier": "Orange", "cellTower": "x",
                     "roamingStatus": ["?"]},
    }}}
    statuses = parse_wan_connection(charge)
    assert len(statuses) == 1 and statuses[0].connected is True
    assert statuses[0].operator is None and statuses[0].cell_id is None
