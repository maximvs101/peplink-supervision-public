"""R-1.5 : ce qui tient le tunnel à terre, dit avec les mots du routeur.

Trois faits que l'outil jetait : le MOT d'état du pair (`START` — le routeur
tente de le joindre — réduit à « pas connecté »), le profil SpeedFusion (nom,
état, pairs connectés) et l'échéance de la licence SpeedFusion Cloud. Validés
sur les captures anonymisées du MAX HD2 (profil FH en `START`, 0 pair) et du
MAX HD4 MBX (PROFIL-A connecté, profil Cloud `SFC Client` en `START`, licence
suspendue et échue le 19 janvier 2023).
"""

from __future__ import annotations

import asyncio
import json
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

from app.fleet import RouterConfig
from app.models import PEER_DOWN, BondProfile, WanRadioStatus
from app.peplink.router_api import (
    _PROFIL_INTERVAL_S,
    CredentialsRefusedError,
    EndpointRefusedError,
    RouterApiError,
    RouterApiPeplinkClient,
    parse_bond_profiles,
    parse_capability,
    peer_state,
)

FIXTURES = Path(__file__).parent / "fixtures"


def _charge(nom: str) -> dict:
    with open(FIXTURES / f"{nom}.json", encoding="utf-8") as f:
        return json.load(f)


HD2 = _charge("max_hd2_8.5.4")
MBX = _charge("max_hd4_mbx_8.5.4")


def test_the_profiles_are_read_with_their_state_and_their_kind():
    assert parse_bond_profiles(HD2["status.pepvpn.profile"]) == [
        BondProfile(id="4", name="PROFIL-C", status="START", peers_connected=0, cloud=False),
    ]
    # L'ordre du routeur, et le profil Cloud reconnu à son TYPE.
    assert parse_bond_profiles(MBX["status.pepvpn.profile"]) == [
        BondProfile(id="1", name="PROFIL-A", status="CONNECTED", peers_connected=1, cloud=False),
        BondProfile(id="60000", name="PROFIL-CLOUD", status="START", peers_connected=0, cloud=True),
    ]


def test_no_identity_leaves_the_profile_parser():
    """`siteId` et le `username` du pair ressemblent à des numéros de série."""
    for capture in (HD2, MBX):
        brut = capture["status.pepvpn.profile"]
        site = brut["response"]["profile"]["siteId"]
        sortie = json.dumps([p.model_dump() for p in parse_bond_profiles(brut)])
        assert site not in sortie
    assert set(BondProfile.model_fields) == {"id", "name", "status", "peers_connected", "cloud"}


def test_a_malformed_profile_payload_yields_nothing():
    assert parse_bond_profiles({"response": {"profile": {"order": [9]}}}) == []
    assert parse_bond_profiles({"response": {}}) == []
    assert parse_bond_profiles({}) == []


def test_the_peer_state_is_the_routers_word_only_when_unanimous():
    assert peer_state(HD2["status.pepvpn.peer"]) == "START"
    deux = {"response": {"peer": [
        {"peerId": "1-0", "status": "START"}, {"peerId": "2-0", "status": "STOPPED"},
    ]}}
    assert peer_state(deux) is None
    # Un emplacement au status vide n'est pas un pair (profil Connect suspendu).
    vide = {"response": {"peer": [{"peerId": "1-0", "status": "START"},
                                  {"peerId": "60000-0", "status": ""}]}}
    assert peer_state(vide) == "START"


def test_the_cloud_licence_carries_its_expiry_and_its_quota():
    mbx = parse_capability(MBX["info.capability"])
    assert mbx.sfc_expires_at == datetime(2023, 1, 19, tzinfo=timezone.utc)
    assert (mbx.sfc_quota_remaining_mb, mbx.sfc_quota_limit_mb) == (2621440.0, 2621440.0)
    # Pas de licence locale : ni échéance ni quota, plutôt que des zéros.
    hd2 = parse_capability(HD2["info.capability"])
    assert (hd2.sfc_expires_at, hd2.sfc_quota_remaining_mb, hd2.sfc_quota_limit_mb) == (None, None, None)


def test_a_quota_in_an_unknown_unit_is_not_published():
    brut = json.loads(json.dumps(MBX["info.capability"]))
    brut["response"]["featureSupplementary"]["pepvpn"]["speedfusionCloud"]["local"]["quota"]["unit"] = "GiB"
    cap = parse_capability(brut)
    assert (cap.sfc_quota_remaining_mb, cap.sfc_quota_limit_mb) == (None, None)


def _routeur(charge: dict):
    """Un `_get` qui répond par la capture, selon `infoType`."""
    async def get(endpoint, params=None):
        if endpoint == "status.pepvpn" and (params or {}).get("infoType") == "peer":
            return charge["status.pepvpn.peer"]
        if endpoint == "status.pepvpn" and (params or {}).get("infoType") == "profile":
            return charge["status.pepvpn.profile"]
        raise AssertionError(f"requête inattendue : {endpoint} {params}")
    return AsyncMock(side_effect=get)


def _profils_demandes(faux: AsyncMock) -> int:
    return sum(1 for c in faux.await_args_list if (c.args[1:] or [{}])[0].get("infoType") == "profile")


def test_a_fallen_bond_carries_the_peer_word_and_the_profiles_once_a_minute():
    client = RouterApiPeplinkClient(RouterConfig(id="hd2", name="HD2", host="h"))
    liens = lambda: [WanRadioStatus(wan_id=w, wan_name=f"W {w}", connected=True) for w in ("1", "4")]
    faux = _routeur(HD2)
    with patch.object(client, "_get", faux):
        premiers = liens()
        asyncio.run(client._attach_tunnel(premiers))
        assert [(s.tunnel.state, s.tunnel.peer_state) for s in premiers] == [(PEER_DOWN, "START")] * 2
        assert [p.name for p in client.bond_profiles] == ["PROFIL-C"]
        assert _profils_demandes(faux) == 1

        asyncio.run(client._attach_tunnel(liens()))
        assert _profils_demandes(faux) == 1, "le profil n'est relu qu'une fois par minute"
        assert [p.name for p in client.bond_profiles] == ["PROFIL-C"]

        client._profils_at -= _PROFIL_INTERVAL_S + 1
        asyncio.run(client._attach_tunnel(liens()))
        assert _profils_demandes(faux) == 2
    assert _PROFIL_INTERVAL_S == 60.0


def test_the_profile_read_is_a_complement_that_never_breaks_the_bond_state():
    client = RouterApiPeplinkClient(RouterConfig(id="hd2", name="HD2", host="h"))
    base = _routeur(HD2)

    async def panne(endpoint, params=None):
        if (params or {}).get("infoType") == "profile":
            raise RouterApiError("réseau")
        return await base(endpoint, params)

    with patch.object(client, "_get", AsyncMock(side_effect=panne)):
        liens = [WanRadioStatus(wan_id="4", wan_name="W", connected=True)]
        asyncio.run(client._attach_tunnel(liens))
    assert liens[0].tunnel.state == PEER_DOWN
    assert client.bond_profiles == []
    assert client._profils_at is None, "une panne passagère ne coûte pas une minute"

    async def refus(endpoint, params=None):
        if (params or {}).get("infoType") == "profile":
            raise EndpointRefusedError("999")
        return await base(endpoint, params)

    with patch.object(client, "_get", AsyncMock(side_effect=refus)):
        asyncio.run(client._attach_tunnel([WanRadioStatus(wan_id="4", wan_name="W", connected=True)]))
    assert client._supports_profils is False

    async def mot_de_passe(endpoint, params=None):
        if (params or {}).get("infoType") == "profile":
            raise CredentialsRefusedError("401")
        return await base(endpoint, params)

    autre = RouterApiPeplinkClient(RouterConfig(id="hd2", name="HD2", host="h"))
    with patch.object(autre, "_get", AsyncMock(side_effect=mot_de_passe)):
        with pytest.raises(CredentialsRefusedError):
            asyncio.run(autre._attach_tunnel([WanRadioStatus(wan_id="4", wan_name="W", connected=True)]))


def test_the_cloud_profile_is_known_by_its_type_not_by_its_number():
    brut = {"response": {"profile": {"order": [7, 60000], "7": {"type": "SFC Client", "status": "START"},
                                     "60000": {"type": "l3", "status": "START"}}}}
    assert [(p.id, p.cloud) for p in parse_bond_profiles(brut)] == [("7", True), ("60000", False)]


def test_profiles_are_forgotten_when_no_peer_is_declared_any_more():
    client = RouterApiPeplinkClient(RouterConfig(id="hd2", name="HD2", host="h"))
    with patch.object(client, "_get", _routeur(HD2)):
        asyncio.run(client._attach_tunnel([WanRadioStatus(wan_id="4", wan_name="W", connected=True)]))
    assert client.bond_profiles
    sans_pair = {"status.pepvpn.peer": {"response": {"peer": []}}}
    with patch.object(client, "_get", _routeur(sans_pair)):
        asyncio.run(client._attach_tunnel([WanRadioStatus(wan_id="4", wan_name="W", connected=True)]))
    assert client.bond_profiles == []


def test_a_refused_profile_endpoint_is_never_asked_again():
    client = RouterApiPeplinkClient(RouterConfig(id="hd2", name="HD2", host="h"))
    base = _routeur(HD2)

    async def refus(endpoint, params=None):
        if (params or {}).get("infoType") == "profile":
            raise EndpointRefusedError("999")
        return await base(endpoint, params)

    faux = AsyncMock(side_effect=refus)
    with patch.object(client, "_get", faux):
        for _ in range(2):
            asyncio.run(client._attach_tunnel([WanRadioStatus(wan_id="4", wan_name="W", connected=True)]))
    assert _profils_demandes(faux) == 1
