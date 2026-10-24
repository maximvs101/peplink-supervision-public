"""R-1.10 : combien d'appareils dépendent de la liaison — des NOMBRES, rien d'autre.

`status.client` publie pour chaque client son nom, sa MAC, son IP, son bail et,
en Wi-Fi, l'ESSID et le BSSID. Le parseur n'en garde que des décomptes : actifs,
Wi-Fi, filaires, et si le poste qui supervise en fait partie. L'IP de ce poste
n'est comparée qu'en mémoire, jamais publiée. Relevé sur le Dome Pro Duo le
25 septembre 2026 : trois clients actifs, deux Wi-Fi, un filaire — ce poste.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

from app.fleet import RouterConfig
from app.models import LanClients
from app.peplink.router_api import (
    _CLIENTS_INTERVAL_S,
    CredentialsRefusedError,
    EndpointRefusedError,
    RouterApiError,
    RouterApiPeplinkClient,
    ip_locale_vers,
    parse_lan_clients,
)

DOME = json.loads((Path(__file__).parent / "fixtures" / "dome_pro_duo_8.5.4.json").read_text(encoding="utf-8"))


def _client(ip: str, kind: str, actif: bool = True) -> dict:
    return {"ip": ip, "connectionType": kind, "active": actif, "name": "PC-DE-QUELQUUN",
            "mac": "02:00:00:00:00:01", "clientType": "11", "lease": {"type": "dhcp"},
            "essid": "RESEAU", "bssid": "02:00:00:00:00:02"}


def test_the_captured_dome_counts_three_active_clients():
    assert parse_lan_clients(DOME["status.client"], None) == LanClients(
        active=3, wireless=2, wired=1, includes_this_host=None)


def test_inactive_clients_are_not_counted_and_zero_is_a_measure():
    brut = {"response": {"list": [_client("10.0.0.2", "wireless", actif=False),
                                  _client("10.0.0.3", "ethernet")]}}
    assert parse_lan_clients(brut, None) == LanClients(active=1, wireless=0, wired=1, includes_this_host=None)
    vide = {"response": {"list": []}}
    assert parse_lan_clients(vide, None) == LanClients(active=0, wireless=0, wired=0, includes_this_host=None)
    # Une réponse sans liste n'est pas « aucun client » : c'est une absence.
    assert parse_lan_clients({"response": {}}, None) is None
    assert parse_lan_clients({}, None) is None


def test_a_client_of_an_unknown_kind_is_active_but_in_neither_column():
    brut = {"response": {"list": [_client("10.0.0.2", "vpn")]}}
    assert parse_lan_clients(brut, None) == LanClients(active=1, wireless=0, wired=0, includes_this_host=None)


def test_this_host_is_recognised_only_among_the_ACTIVE_clients():
    brut = {"response": {"list": [_client("10.0.0.2", "wireless"), _client("10.0.0.9", "ethernet")]}}
    assert parse_lan_clients(brut, "10.0.0.9").includes_this_host is True
    assert parse_lan_clients(brut, "10.0.0.7").includes_this_host is False
    inactif = {"response": {"list": [_client("10.0.0.9", "ethernet", actif=False)]}}
    assert parse_lan_clients(inactif, "10.0.0.9").includes_this_host is False


def test_no_identity_leaves_the_parser():
    brut = {"response": {"list": [_client("10.0.0.9", "wireless")]}}
    sortie = json.dumps(parse_lan_clients(brut, "10.0.0.9").model_dump())
    for trace in ("10.0.0.9", "PC-DE-QUELQUUN", "02:00:00:00:00", "RESEAU"):
        assert trace not in sortie
    # Des nombres, et l'instant de leur lecture (C-33) — aucune identité.
    assert set(LanClients.model_fields) == {"active", "wireless", "wired", "includes_this_host", "read_at"}


def test_the_captured_fixture_carries_no_identity():
    for c in DOME["status.client"]["response"]["list"]:
        assert set(c) == {"connectionType", "active"}


def test_the_local_address_is_only_sought_for_a_literal_ip():
    # Un nom d'hôte demanderait une résolution DNS, bloquante (5 s mesurées) :
    # aucune socket n'est même ouverte.
    with patch("app.peplink.router_api.socket.socket", side_effect=AssertionError("DNS")):
        assert ip_locale_vers("http://routeur.local") is None
        assert ip_locale_vers("h") is None
    assert ip_locale_vers("https://127.0.0.1:443") == "127.0.0.1"


def _routeur(charge):
    return AsyncMock(return_value=charge)


def test_the_clients_are_read_once_a_minute():
    client = RouterApiPeplinkClient(RouterConfig(id="d", name="D", host="h"))
    faux = _routeur(DOME["status.client"])
    with patch.object(client, "_get", faux):
        asyncio.run(client._lire_clients())
        asyncio.run(client._lire_clients())
        assert faux.await_count == 1
        assert client.lan_clients.active == 3
        client._clients_at -= _CLIENTS_INTERVAL_S + 1
        asyncio.run(client._lire_clients())
        assert faux.await_count == 2
    assert _CLIENTS_INTERVAL_S == 60.0


def test_the_client_read_is_a_complement():
    client = RouterApiPeplinkClient(RouterConfig(id="d", name="D", host="h"))
    with patch.object(client, "_get", AsyncMock(side_effect=RouterApiError("réseau"))):
        asyncio.run(client._lire_clients())
    assert client.lan_clients is None and client._clients_at is None

    # Un refus ne coûte pas l'intervalle : ce qu'on en retient est l'affaire
    # de la mémoire de `_get`, court-circuitée ici (voir test_refus_passagers).
    faux = AsyncMock(side_effect=EndpointRefusedError("999"))
    with patch.object(client, "_get", faux):
        asyncio.run(client._lire_clients())
        asyncio.run(client._lire_clients())
    assert faux.await_count == 2
    assert client.lan_clients is None and client._clients_at is None

    autre = RouterApiPeplinkClient(RouterConfig(id="d", name="D", host="h"))
    with patch.object(autre, "_get", AsyncMock(side_effect=CredentialsRefusedError("401"))):
        with pytest.raises(CredentialsRefusedError):
            asyncio.run(autre._lire_clients())


def test_the_count_is_dated_and_forgotten_after_two_missed_readings(monkeypatch):
    """C-33 : `lan_clients` n'était remplacé que sur une lecture réussie, et
    n'était pas daté. « 12 appareils actifs » lu le matin restait affiché le
    soir pendant que la lecture échouait. Daté, et effacé après deux
    intervalles sans lecture."""
    import app.peplink.router_api as module

    horloge = [1000.0]
    monkeypatch.setattr(module.time, "monotonic", lambda: horloge[0])
    client = RouterApiPeplinkClient(RouterConfig(id="d", name="D", host="h"))
    charge = {"stat": "ok", "response": {"list": [_client("192.168.50.10", "wireless")]}}
    with patch.object(client, "_get", AsyncMock(return_value=charge)):
        asyncio.run(client._lire_clients())
    assert client.lan_clients is not None and client.lan_clients.read_at is not None

    with patch.object(client, "_get", AsyncMock(side_effect=RouterApiError("réseau"))):
        horloge[0] += _CLIENTS_INTERVAL_S + 1
        asyncio.run(client._lire_clients())
        assert client.lan_clients is not None, "un échec isolé garde la dernière lecture, datée"
        horloge[0] += _CLIENTS_INTERVAL_S
        asyncio.run(client._lire_clients())
        assert client.lan_clients is None, "deux intervalles sans lecture : plus rien"
