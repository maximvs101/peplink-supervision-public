"""R-1.11 : la température de chaque modem, lue en POST une fois par minute.

Relevé sur le Dome Pro Duo 8.5.1 le 25 septembre 2026 : `{"temperature": 37}`
et `{"temperature": 35}` sur les deux SIM ; sur le WAN Ethernet, refus
« Unsupported request » (code 404). Un refus vaut donc pour UN WAN, pas pour
l'équipement. Pas d'historique : la dernière mesure, avec son heure, tenue
90 s au plus — pas de seuil, le routeur n'en publie aucun.
"""

from __future__ import annotations

import asyncio
from datetime import timedelta
from unittest.mock import AsyncMock, patch

import pytest

import app.peplink.router_api as module
from app.fleet import RouterConfig
from app.models import WanRadioStatus, utcnow
from app.peplink.router_api import (
    _TEMPERATURE_INTERVAL_S,
    CredentialsRefusedError,
    EndpointRefusedError,
    RouterApiError,
    RouterApiPeplinkClient,
    parse_modem_temperature,
)


def test_the_reading_is_an_integer_in_celsius():
    assert parse_modem_temperature({"stat": "ok", "response": {"temperature": 37}}) == 37.0
    assert parse_modem_temperature({"stat": "ok", "response": {"temperature": 0}}) == 0.0
    for vide in ({"response": {}}, {}, {"response": {"temperature": None}},
                 {"response": {"temperature": "NaN"}}):
        assert parse_modem_temperature(vide) is None


def _liens():
    return [
        WanRadioStatus(wan_id="1", wan_name="W1", kind="ethernet", connected=True),
        WanRadioStatus(wan_id="2", wan_name="W2", kind="cellular", connected=True),
        WanRadioStatus(wan_id="3", wan_name="W3", kind="cellular", connected=False),
    ]


def _routeur(par_wan: dict):
    async def lire(endpoint, params=None, corps=None):
        assert endpoint == "status.cellularModule.temperature"
        reponse = par_wan[corps["connId"]]
        if isinstance(reponse, Exception):
            raise reponse
        return {"stat": "ok", "response": {"temperature": reponse}}
    return AsyncMock(side_effect=lire)


def _demandes(faux: AsyncMock) -> list[int]:
    return [c.kwargs["corps"]["connId"] for c in faux.await_args_list]


def test_only_cellular_modems_are_asked_once_a_minute_and_carried_with_their_time(monkeypatch):
    t0 = utcnow()
    horloge = [t0]
    monkeypatch.setattr(module, "utcnow", lambda: horloge[0])
    client = RouterApiPeplinkClient(RouterConfig(id="d", name="D", host="h"))
    faux = _routeur({2: 37, 3: 35})
    with patch.object(client, "_get", faux):
        liens = _liens()
        asyncio.run(client._attach_modem_temperature(liens))
        # Le WAN Ethernet n'est pas demandé ; un modem déconnecté reste alimenté.
        assert _demandes(faux) == [2, 3]
        assert [(s.modem_temperature_c, s.modem_temperature_at) for s in liens] == [
            (None, None), (37.0, t0), (35.0, t0)]

        horloge[0] = t0 + timedelta(seconds=50)
        liens = _liens()
        asyncio.run(client._attach_modem_temperature(liens))
        assert _demandes(faux) == [2, 3], "une lecture par minute"
        assert liens[1].modem_temperature_c == 37.0 and liens[1].modem_temperature_at == t0

        client._temperature_at -= _TEMPERATURE_INTERVAL_S + 1
        horloge[0] = t0 + timedelta(seconds=70)
        asyncio.run(client._attach_modem_temperature(_liens()))
        assert _demandes(faux) == [2, 3, 2, 3]
    assert _TEMPERATURE_INTERVAL_S == 60.0


def test_a_measure_older_than_90_s_is_dropped(monkeypatch):
    t0 = utcnow()
    horloge = [t0]
    monkeypatch.setattr(module, "utcnow", lambda: horloge[0])
    client = RouterApiPeplinkClient(RouterConfig(id="d", name="D", host="h"))
    with patch.object(client, "_get", _routeur({2: 37, 3: 35})):
        asyncio.run(client._attach_modem_temperature(_liens()))
    # La relecture échoue : la dernière mesure tient jusqu'à 90 s, puis tombe.
    client._temperature_at -= _TEMPERATURE_INTERVAL_S + 1
    panne = _routeur({2: RouterApiError("réseau"), 3: RouterApiError("réseau")})
    with patch.object(client, "_get", panne):
        horloge[0] = t0 + timedelta(seconds=90)
        liens = _liens()
        asyncio.run(client._attach_modem_temperature(liens))
        assert liens[1].modem_temperature_c == 37.0
        horloge[0] = t0 + timedelta(seconds=91)
        liens = _liens()
        asyncio.run(client._attach_modem_temperature(liens))
        assert liens[1].modem_temperature_c is None and liens[1].modem_temperature_at is None


def test_a_refusal_removes_one_modem_not_the_device():
    client = RouterApiPeplinkClient(RouterConfig(id="d", name="D", host="h"))
    faux = _routeur({2: EndpointRefusedError("404 Unsupported request", code=404), 3: 35})
    with patch.object(client, "_get", faux):
        liens = _liens()
        asyncio.run(client._attach_modem_temperature(liens))
        assert liens[2].modem_temperature_c == 35.0
        client._temperature_at -= _TEMPERATURE_INTERVAL_S + 1
        asyncio.run(client._attach_modem_temperature(_liens()))
    assert _demandes(faux) == [2, 3, 3], "le WAN refusé n'est plus demandé, l'autre si"


def test_a_transient_failure_does_not_cost_a_minute():
    client = RouterApiPeplinkClient(RouterConfig(id="d", name="D", host="h"))
    with patch.object(client, "_get", _routeur({2: RouterApiError("r"), 3: RouterApiError("r")})):
        asyncio.run(client._attach_modem_temperature(_liens()))
    assert client._temperature_at is None
    faux = _routeur({2: 37, 3: 35})
    with patch.object(client, "_get", faux):
        asyncio.run(client._attach_modem_temperature(_liens()))
    assert _demandes(faux) == [2, 3]


def test_refused_credentials_surface():
    client = RouterApiPeplinkClient(RouterConfig(id="d", name="D", host="h"))
    with patch.object(client, "_get", _routeur({2: CredentialsRefusedError("401"), 3: 35})):
        with pytest.raises(CredentialsRefusedError):
            asyncio.run(client._attach_modem_temperature(_liens()))


def test_the_temperature_is_never_written_to_the_samples_table():
    from app.storage import db
    assert not any("temperature" in c for c in db.METRICS)
    assert "modem_temperature" not in db._SCHEMA


def test_a_rights_refusal_stops_asking_every_modem():
    from app.peplink.router_api import AuthenticationError
    client = RouterApiPeplinkClient(RouterConfig(id="d", name="D", host="h"))
    faux = _routeur({2: AuthenticationError("403", code=403), 3: 35})
    with patch.object(client, "_get", faux):
        asyncio.run(client._attach_modem_temperature(_liens()))
        client._temperature_at = None
        asyncio.run(client._attach_modem_temperature(_liens()))
    assert _demandes(faux) == [2]


def test_a_reply_without_temperature_still_counts_as_the_minute_s_reading():
    """C-30 : l'instant n'avançait que si une VALEUR avait été lue. Un
    firmware qui répond `stat: ok` sans température ajoutait donc un POST par
    modem à chaque cycle, indéfiniment — de quoi rapprocher du budget de
    l'équipement. Une réponse lue compte ; seule une panne ne compte pas."""
    client = RouterApiPeplinkClient(RouterConfig(id="d", name="D", host="h"))
    faux = _routeur({2: None, 3: None})
    with patch.object(client, "_get", faux):
        for _ in range(5):
            asyncio.run(client._attach_modem_temperature(_liens()))
    assert _demandes(faux) == [2, 3], "une réponse sans valeur a été redemandée à chaque cycle"
