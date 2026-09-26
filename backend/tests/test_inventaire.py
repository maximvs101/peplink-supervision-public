"""L'outil d'inventaire ne montre jamais une valeur : des chemins et des types."""

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))

from inventaire import chemins, couches, refus, suspects

RACINE = Path(__file__).resolve().parents[1]


def test_paths_carry_types_never_values():
    charge = {"stat": "ok", "response": {"2": {"cellular": {"network": "5G", "tac": 52643}}}}
    vus = chemins(charge)
    assert vus["response.*.cellular.network"] == "str"
    assert vus["response.*.cellular.tac"] == "int"
    assert "52643" not in json.dumps(vus) and "5G" not in json.dumps(vus)


def test_lists_merge_the_keys_of_every_element():
    charge = {"band": [{"name": "n78"}, {"name": "B3", "pci": 499}]}
    vus = chemins(charge)
    assert vus["band[].pci"] == "int"
    assert vus["band[].name"] == "str"


def test_null_is_a_type_of_its_own():
    assert chemins({"a": None}) == {"a": "null"}


def test_secret_looking_keys_are_flagged_before_reading():
    charge = {"response": {"2": {"cellular": {"sim": {"1": {
        "apn": "x", "username": "u", "password": "p", "iccid": "8933", "imsi": "2080",
    }}}}}}
    drapeaux = suspects(chemins(charge))
    for cle in ("password", "username", "iccid", "imsi"):
        assert any(d.endswith("." + cle) for d in drapeaux), cle
    assert not any(d.endswith(".apn") for d in drapeaux)


def test_a_refusal_is_named_not_counted_as_empty():
    assert refus({"stat": "fail", "code": 401, "message": "Unauthorized"}) == "refusé (code 401)"
    assert refus({"stat": "ok", "response": {}}) is None


def test_layers_are_found_in_the_real_code():
    # `cellUtranId` est lu par le collecteur depuis le correctif 8.5.1, et
    # n'est ni stocké ni publié sous ce nom.
    vus = couches("cellUtranId", RACINE)
    assert vus["collecteur"] is True
    assert vus["base"] is False
    # `rsrp` traverse les quatre couches.
    assert all(couches("rsrp", RACINE).values())


def test_personal_fields_are_flagged_too():
    """`status.client` porte le nom, la MAC et l'IP de chaque appareil du LAN :
    l'examen ne signalait rien, et la charge aurait été jugée lisible en entier.
    Un nom de WAN porte, lui, des numéros de ligne."""
    charge = {"response": {"list": [{
        "name": "x", "mac": "x", "ip": "x", "clientType": "x", "active": True,
    }], "device": {"serialNumber": "x", "hostname": "x"}, "sim": {"pin": "x", "puk": "x"}}}
    drapeaux = suspects(chemins(charge))
    for cle in ("name", "mac", "ip", "serialNumber", "hostname", "pin", "puk"):
        assert any(d.endswith("." + cle) for d in drapeaux), cle
    assert not any(d.endswith((".clientType", ".active")) for d in drapeaux)
