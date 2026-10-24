"""Une grandeur absente n'est jamais un `False` affirmé.

Trois lectures faisaient d'une clé manquante une négation :
`bool(e.get("connect"))` déclarait débranchée une alimentation dont le
firmware ne disait rien ; `config_activating` valait `False` sans bloc
`configActivation` ; `bool(bloc.get("enable"))` déclarait « aucun plafond »
un WAN dont le bloc de plafond ne disait pas s'il était actif.
"""

from __future__ import annotations

import json
from pathlib import Path

from app.peplink.router_api import parse_system_info, parse_wan_connection

MBX = Path(__file__).parent / "fixtures" / "max_hd4_mbx_8.5.4.json"


def _info(**response) -> dict:
    return {"stat": "ok", "response": {"device": {"model": "X"}, **response}}


def test_une_alimentation_sans_etat_n_est_pas_dite_debranchee():
    info = parse_system_info(_info(powerState=[
        {"name": "DC Source A", "connect": True},
        {"name": "DC Source B"},                      # état non publié
        {"name": "DC Term. Block", "connect": False},
    ]))
    etats = {s.name: s.connected for s in info.power_sources}
    assert etats == {"DC Source A": True, "DC Term. Block": False}, (
        "une alimentation sans état n'est ni branchée ni débranchée : elle "
        "n'est pas listée plutôt que comptée comme débranchée"
    )


def test_la_capture_mbx_garde_ses_trois_alimentations():
    capture = json.loads(MBX.read_text(encoding="utf-8"))
    info = parse_system_info(capture["status.system.info"])
    assert [(s.name, s.connected) for s in info.power_sources] == [
        ("DC Source A", True), ("DC Source B", False), ("DC Term. Block", False),
    ]


def test_une_activation_non_publiee_reste_inconnue():
    assert parse_system_info(_info()).config_activating is None
    assert parse_system_info(_info(configActivation={})).config_activating is None
    assert parse_system_info(_info(configActivation="?")).config_activating is None
    assert parse_system_info(_info(configActivation={"activating": False})).config_activating is False
    assert parse_system_info(_info(configActivation={"activating": True})).config_activating is True


def _wan(bloc) -> dict:
    return {"stat": "ok", "response": {"2": {
        "type": "cellular", "statusLed": "green", "name": "SIM",
        "cellular": {"network": "LTE"}, "bandwidthAllowanceMonitor": bloc,
    }}}


def test_un_plafond_sans_drapeau_enable_est_inconnu():
    sans_drapeau = {"hasSmtp": False, "monthlyAllowance": {"value": 20480, "unit": "MB"}}
    assert parse_wan_connection(_wan(sans_drapeau))[0].allowance is None
    inactif = parse_wan_connection(_wan({"enable": False, "hasSmtp": False}))[0].allowance
    assert inactif is not None and inactif.enabled is False
    actif = parse_wan_connection(_wan({
        "enable": True, "start": 11, "monthlyAllowance": {"value": 20, "unit": "GB"},
    }))[0].allowance
    assert actif.enabled is True and actif.monthly_allowance_mb == 20480.0
