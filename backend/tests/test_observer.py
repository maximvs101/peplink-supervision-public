import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))

from observer import ecarts, faits


def _resume(connecte=True, priorite=1, active="1"):
    return {
        "status": "connecté", "alerts": [], "tunnel_attendu": False, "never_connected": ["1"],
        "wans": [{"wan": {
            "wan_id": "2", "connected": connecte, "status_message": "Connected to Orange F",
            "rat": "5G NSA", "band": "n78", "cell_id": "25698582", "priority": priorite,
            "sim_slots": [{"slot": "1", "active": active == "1"}, {"slot": "2", "active": active == "2"}],
            "allowance": None,
        }}],
    }


def test_facts_are_flat_and_comparable():
    f = faits(_resume())
    assert f["w2.connecte"] == "oui"
    assert f["w2.sim_active"] == "1"
    assert f["jamais_connectes"] == "1"


def test_only_changes_are_reported():
    assert ecarts(faits(_resume()), faits(_resume())) == []
    assert ecarts(faits(_resume()), faits(_resume(connecte=False))) == ["w2.connecte: oui → non"]
    assert ecarts(faits(_resume()), faits(_resume(active="2"))) == ["w2.sim_active: 1 → 2"]


def test_a_vanished_wan_is_a_change_not_a_crash():
    sans = {**_resume(), "wans": []}
    assert "w2.connecte: oui → —" in ecarts(faits(_resume()), faits(sans))
