"""Parsing de l'API locale, validé contre une capture réelle.

Matériel de référence : Peplink Dome Pro Duo, firmware 8.5.4 build 6264,
modem Quectel RM520N-GL, 2 WAN cellulaires Orange en 5G NSA.

La fixture `fixtures/dome_pro_duo_8.5.4.json` est la capture terrain avec les
identifiants anonymisés (IMEI, ICCID, IMSI, EID, MAC, n° de série, position GPS) ;
la structure et toutes les valeurs radio sont celles du routeur réel.
"""

import asyncio
import json
from pathlib import Path

import pytest

from app.peplink.router_api import _band_code, parse_location, parse_wan_connection

FIXTURE = Path(__file__).parent / "fixtures" / "dome_pro_duo_8.5.4.json"


@pytest.fixture(scope="module")
def capture() -> dict:
    with open(FIXTURE, encoding="utf-8") as f:
        return json.load(f)


@pytest.fixture(scope="module")
def all_wans(capture) -> list:
    return parse_wan_connection(capture["status.wan.connection"])


@pytest.fixture(scope="module")
def wans(all_wans) -> list:
    """Les seuls WAN cellulaires (WAN 2 et 3 sur ce routeur)."""
    return [w for w in all_wans if w.kind == "cellular"]


# --- sélection des WAN --------------------------------------------------------

def test_every_active_link_is_supervised(all_wans):
    """Cellulaire, Wi-Fi et filaire sont supervisés ; wovlan est ignoré.

    Le WAN Ethernet a longtemps été écarté faute de métrique radio. Mais il
    participe à la liaison agrégée et en porte souvent le débit principal : une
    supervision qui l'ignore annonce « tout va bien » alors que le lien principal
    est tombé.
    """
    assert [(w.wan_id, w.kind) for w in all_wans] == [
        ("1", "ethernet"),
        ("2", "cellular"),
        ("3", "cellular"),
        ("5", "wifi"),
    ]


def test_an_unpublished_sim_flag_is_not_an_empty_slot():
    """`bool(bloc.get("simCardDetected"))` faisait d'une clé absente un
    emplacement « vide », affirmé à l'écran — le défaut corrigé pour
    `carrierAggregation`, reproduit ici."""
    charge = {"response": {"2": {"type": "cellular", "statusLed": "green", "cellular": {
        "network": "LTE", "rat": [],
        "sim": {"1": {"simCardDetected": True, "active": True, "apn": "orange"},
                "2": {"apn": "sfr"}, "order": [1, 2]},
    }}}}
    slots = parse_wan_connection(charge)[0].sim_slots
    assert [(s.slot, s.card_detected, s.active) for s in slots] == [
        ("1", True, True), ("2", None, None),
    ]


def test_an_nsa_cell_without_an_lte_carrier_is_not_split(wans):
    """« NSA ⇒ `cellId` est l'ancre LTE » n'est établi que sur un modem.

    Le Dome mesure n78 + deux LTE : l'ancre est là, le découpage tient. Un
    modem qui dirait NSA sans mesurer la moindre porteuse LTE publierait un
    identifiant qui ne peut pas être l'ancre — un NCI court passerait la garde
    de taille et serait découpé en station et secteur inventés.
    """
    assert all(w.enb_id is not None for w in wans if w.connected and w.cell_id is not None)

    sans_ancre = {"response": {"2": {"type": "cellular", "statusLed": "green", "cellular": {
        "network": "5G", "dataTechnology": "5G NSA",
        "cellTower": {"cellId": 25735433},
        "rat": [{"name": "5G", "band": [
            {"name": "5G Band n78 (3500 MHz)", "signal": {"rsrp": -90}},
        ]}],
    }}}}
    wan = parse_wan_connection(sans_ancre)[0]
    assert wan.cell_id == 25735433
    assert (wan.enb_id, wan.sector_id) == (None, None)
    # En LTE pur, aucune ancre à exiger : le découpage est celui de l'ECI.
    assert _split_eci(25735433, "LTE-A", ancre_lte=False)[0] == 100529


@pytest.mark.parametrize(
    "led,message",
    [("gray", "Connecting..."), ("empty", "No SIM Card Detected"), ("", "Obtaining IP Address...")],
)
def test_an_active_cellular_wan_that_is_not_up_is_not_connected(led, message):
    """Aucune capture ne porte un WAN cellulaire actif mais non connecté —
    l'état terrain le plus fréquent : SIM absente, « Connecting… », LED
    grise. `connected = led in _LED_UP` mis à `led != "red"` laissait 515
    tests verts. Vert, ambre et jaune sont debout ; le reste ne l'est pas."""
    charge = {"response": {"2": {"type": "cellular", "statusLed": led, "message": message,
        "cellular": {"network": "LTE", "rat": [{"name": "LTE", "band": [
            {"name": "LTE Band 3 (1800 MHz)", "signal": {"rsrp": -110}},
        ]}]}}}}
    wan = parse_wan_connection(charge)[0]
    assert wan.connected is False
    assert wan.status_message == message
    # Et le lien reste SUPERVISÉ : il est actif, il a une mesure radio.
    assert wan.rsrp == -110.0


def test_three_measured_carriers_are_an_aggregation_whatever_the_flag_says(wans, capture):
    """Le Dome publie `carrierAggregation: false` en mesurant n78 + deux LTE.

    La fiche disait « Agrégation : inactive » sous trois porteuses mesurées.
    Ce qui est mesuré l'emporte sur le drapeau ; celui-ci ne garde la parole
    que sur une porteuse unique — et se tait quand il est absent.
    """
    brut = capture["status.wan.connection"]["response"]
    assert all(brut[w.wan_id]["cellular"]["carrierAggregation"] is False for w in wans)
    assert all(len(w.bands) == 3 and w.carrier_aggregation is True for w in wans)

    seule = {"response": {"2": {"type": "cellular", "statusLed": "green",
        "cellular": {"network": "LTE", "carrierAggregation": False, "rat": [{"name": "LTE", "band": [
            {"name": "LTE Band 3 (1800 MHz)", "signal": {"rsrp": -90}},
        ]}]}}}}
    assert parse_wan_connection(seule)[0].carrier_aggregation is False
    del seule["response"]["2"]["cellular"]["carrierAggregation"]
    assert parse_wan_connection(seule)[0].carrier_aggregation is None


def test_wired_wan_carries_no_radio_metrics(all_wans):
    """Un lien filaire n'a ni niveau, ni bande, ni cellule — donc aucune antenne."""
    wired = next(w for w in all_wans if w.kind == "ethernet")
    assert (wired.rsrp, wired.rsrq, wired.sinr, wired.rssi) == (None, None, None, None)
    assert wired.bands == [] and wired.cell_id is None and wired.enb_id is None
    assert wired.rat == "Ethernet"


def test_wired_wan_state_comes_from_the_status_led(all_wans):
    """Sur cette capture, le WAN 1 est câblé mais sans lien (voyant rouge)."""
    wired = next(w for w in all_wans if w.kind == "ethernet")
    assert wired.connected is False


def test_disabled_wan_is_skipped(capture):
    """Le WAN Wi-Fi 2,4 GHz est désactivé (`enable: false`) : rien à superviser."""
    response = capture["status.wan.connection"]["response"]
    assert response["4"]["type"] == "wifi" and response["4"]["enable"] is False
    assert "4" not in [w.wan_id for w in parse_wan_connection(capture["status.wan.connection"])]


def test_non_wan_keys_are_ignored(capture):
    """`order`, `timestamp`... cohabitent avec les WAN dans `response`."""
    response = capture["status.wan.connection"]["response"]
    assert {"order", "timestamp", "reportTimestamp"} <= set(response)
    assert len(parse_wan_connection(capture["status.wan.connection"])) == 4


# --- identité réseau ----------------------------------------------------------

def test_operator_and_plmn(wans):
    wan = wans[0]
    assert wan.operator == "Orange F"        # sous cellular.carrier.name
    assert wan.plmn == "208-01"              # la graphie du routeur, zéro compris


def test_cell_identity_comes_from_cell_tower(wans):
    assert wans[0].cell_id == 22628619
    assert wans[0].tac == 37793
    assert wans[1].cell_id == 22628613


def test_technology_prefers_data_technology(wans):
    # `network` vaut "5G" ; `dataTechnology` précise le mode : plus informatif.
    assert wans[0].rat == "5G NSA"


def test_connection_state_and_uptime(wans):
    assert all(w.connected for w in wans)    # statusLed "green"
    assert wans[0].status_message == "Connected to Orange F"
    assert wans[0].uptime_s == 8091


def test_signal_bars(wans):
    assert wans[0].signal_bars == 4          # cellular.signalLevel


# --- agrégation de porteuses --------------------------------------------------

def test_all_aggregated_bands_are_exposed(wans):
    """WAN 2 : une porteuse 5G n78 + deux ancres LTE (B1 et B3)."""
    bands = wans[0].bands
    assert [(b.rat, b.band) for b in bands] == [
        ("5G", "n78"),
        ("LTE", "B1"),
        ("LTE", "B3"),
    ]


def test_primary_band_is_the_data_carrier(wans):
    """`network` = "5G" -> les valeurs principales sont celles de la porteuse 5G."""
    wan = wans[0]
    assert wan.band == "n78"
    assert wan.earfcn == 650400
    assert wan.pci == 649
    assert wan.rsrp == -108.0
    assert wan.rsrq == -13.0
    assert wan.sinr == 14.0
    # La porteuse 5G ne remonte pas de RSSI sur ce modem.
    assert wan.rssi is None


def test_anchor_band_keeps_its_own_levels(wans):
    """Chaque porteuse garde ses propres niveaux — c'est tout l'intérêt."""
    anchor = next(b for b in wans[0].bands if b.band == "B1")
    assert anchor.rsrp == -97.0
    assert anchor.sinr == 16.0
    assert anchor.rssi == -65.0
    assert anchor.channel_width == "15 MHz"
    # L'ancre LTE est nettement meilleure que la 5G n78 sur ce relevé.
    assert anchor.rsrp > wans[0].rsrp


def test_second_wan_has_different_bands(wans):
    assert [b.band for b in wans[1].bands] == ["n78", "B3", "B7"]


@pytest.mark.parametrize(
    "label,expected",
    [
        ("5G Band n78 (3500 MHz)", "n78"),
        ("LTE Band 1 (2100 MHz)", "B1"),
        ("LTE Band 3 (1800 MHz)", "B3"),
        ("LTE Band 7 (2600 MHz)", "B7"),
        (None, "?"),
        ("libellé inattendu", "libellé inattendu"),
    ],
)
def test_band_code_extraction(label, expected):
    assert _band_code(label) == expected


# --- WAN Wi-Fi (lien amont, p. ex. Starlink) ----------------------------------

def test_wifi_wan_is_parsed(all_wans):
    wifi = next(w for w in all_wans if w.kind == "wifi")
    assert wifi.connected is True
    assert wifi.rat == "Wi-Fi"
    assert wifi.wifi is not None
    assert wifi.wifi.channel == 112
    assert wifi.wifi.band == "5 GHz"      # déduit du canal
    assert wifi.wifi.signal_dbm == -62.0
    assert wifi.wifi.signal_level == 4
    assert wifi.uptime_s == 2078


def test_wifi_signal_is_exposed_as_rssi(all_wans):
    """La force Wi-Fi est rangée dans `rssi`, grandeur homogène (dBm)."""
    wifi = next(w for w in all_wans if w.kind == "wifi")
    assert wifi.rssi == -62.0
    assert wifi.signal_bars == 4
    # RSRP/RSRQ/SINR n'ont pas de sens pour un lien Wi-Fi.
    assert (wifi.rsrp, wifi.rsrq, wifi.sinr) == (None, None, None)
    assert wifi.bands == []


def test_wifi_wan_has_no_cell_identity(all_wans):
    """Sans Cell ID, aucune antenne relais ne sera cherchée pour ce WAN."""
    wifi = next(w for w in all_wans if w.kind == "wifi")
    assert wifi.cell_id is None
    assert wifi.plmn is None


@pytest.mark.parametrize(
    "channel,expected",
    [(1, "2,4 GHz"), (11, "2,4 GHz"), (36, "5 GHz"), (112, "5 GHz"), (177, "5 GHz"),
     (181, "6 GHz"), (200, "6 GHz"), (None, None)],
)
def test_wifi_band_from_channel(channel, expected):
    """177 est le dernier canal 5 GHz (UNII-4) ; au-delà, seul le 6 GHz numérote.
    La borne était 196 — un canal qui n'existe dans aucune des deux bandes."""
    payload = {
        "response": {
            "5": {
                "name": "Wi-Fi WAN",
                "type": "wifi",
                "statusLed": "green",
                "wifi": {"channel": channel, "signal": {"strength": -60, "level": 4}},
            }
        }
    }
    assert parse_wan_connection(payload)[0].wifi.band == expected


# --- GPS ----------------------------------------------------------------------

def test_location_is_parsed(capture):
    position = parse_location(capture["info.location"])
    assert position is not None
    assert -90 <= position.lat <= 90 and -180 <= position.lon <= 180


def test_location_absent_without_gps_fix():
    assert parse_location({"response": {"gps": False}}) is None
    assert parse_location({"response": {"gps": True, "location": {}}}) is None
    assert parse_location({}) is None


# --- robustesse ---------------------------------------------------------------

def test_empty_payload_is_handled():
    assert parse_wan_connection({}) == []
    assert parse_wan_connection({"response": {}}) == []


def test_cellular_wan_without_radio_data():
    """Un modem sans porteuse active ne doit pas faire échouer le parsing."""
    payload = {
        "response": {
            "2": {
                "name": "SIM",
                "type": "cellular",
                "statusLed": "red",
                "message": "No SIM",
                "cellular": {},
            }
        }
    }
    wan = parse_wan_connection(payload)[0]
    assert wan.connected is False
    assert wan.bands == []
    assert wan.rsrp is None
    assert wan.band is None


# --- authentification et sessions expirées ------------------------------------
#
# Corps réellement observé sur un MBX 4 SIM interrogé sans session :
#   HTTP 200  {"stat":"fail","code":401,"message":"Unauthorized"}
# Le refus est dans le CORPS, pas dans le statut HTTP — d'où ces tests.

import httpx

from app.fleet import RouterConfig
from app.peplink.router_api import (
    CredentialsRefusedError,
    EndpointRefusedError,
    RouterApiError,
    RouterApiPeplinkClient,
    _is_auth_failure,
    _to_float,
    redact,
)

UNAUTHORIZED = {"stat": "fail", "code": 401, "message": "Unauthorized"}


@pytest.mark.parametrize(
    "payload,expected",
    [
        (UNAUTHORIZED, True),                                          # pas de session
        ({"stat": "fail", "code": 301, "message": "Unauthorized"}, True),   # login rejeté
        ({"stat": "fail", "code": 403, "message": "Forbidden"}, True),
        ({"stat": "fail", "code": 999, "message": "unknown function"}, False),  # autre échec
        ({"stat": "ok", "response": {}}, False),
        ({}, False),
    ],
)
def test_auth_failure_detection(payload, expected):
    assert _is_auth_failure(payload) is expected


def test_non_finite_numbers_are_absent_values():
    """`float("nan")` et `float("inf")` passent `float()` sans erreur.

    Un NaN en RSRP n'est jamais sous le seuil ni au-dessus, et JSON ne sait
    pas l'écrire : le relevé entier devenait illisible pour le navigateur.
    """
    assert _to_float("nan") is None
    assert _to_float("inf") is None
    assert _to_float("-Infinity") is None
    assert _to_float(float("nan")) is None
    assert _to_float("-97.5") == -97.5
    assert _to_float(0) == 0.0
    assert _to_float("abc") is None


async def _client(handler) -> RouterApiPeplinkClient:
    """Client branché sur un faux routeur (aucun accès réseau).

    Le client HTTP ouvert par le constructeur est fermé avant d'être remplacé :
    l'écraser sans le fermer abandonnait un pool de connexions par test.
    """
    cfg = RouterConfig(id="mbx", name="MBX", host="192.0.2.1", username="admin", password="x")
    client = RouterApiPeplinkClient(cfg)
    await client._http.aclose()
    client._http = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    return client


async def test_expired_session_triggers_reauth_and_succeeds():
    """Une session expirée doit être renouvelée, pas ignorée."""
    calls = {"login": 0, "get": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/login":
            calls["login"] += 1
            return httpx.Response(200, json={"stat": "ok"})
        calls["get"] += 1
        # Le premier appel tombe sur une session expirée, le second réussit.
        if calls["get"] == 1:
            return httpx.Response(200, json=UNAUTHORIZED)
        return httpx.Response(200, json={"stat": "ok", "response": {"timestamp": 1}})

    client = await _client(handler)
    payload = await client._get("status.wan.connection")

    assert payload["stat"] == "ok"
    assert calls["login"] == 2   # login initial + renouvellement
    assert calls["get"] == 2     # requête rejouée après ré-authentification
    await client.aclose()


async def test_persistent_refusal_raises_instead_of_returning_empty():
    """Identifiants invalides : erreur explicite, jamais une liste vide silencieuse."""

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/login":
            return httpx.Response(200, json={"stat": "ok"})
        return httpx.Response(200, json=UNAUTHORIZED)

    client = await _client(handler)
    with pytest.raises(RouterApiError) as err:
        await client.get_radio_status()

    assert "401" in str(err.value) and "Unauthorized" in str(err.value)
    await client.aclose()


async def test_refused_login_is_reported():
    """Un mot de passe erroné doit remonter le message du routeur."""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"stat": "fail", "code": 301, "message": "Unauthorized"})

    client = await _client(handler)
    with pytest.raises(RouterApiError, match="login refusé"):
        await client.get_radio_status()
    await client.aclose()


async def test_non_auth_failure_is_not_retried():
    """Un échec non lié à la session ne doit pas relancer d'authentification."""
    calls = {"login": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/login":
            calls["login"] += 1
            return httpx.Response(200, json={"stat": "ok"})
        return httpx.Response(200, json={"stat": "fail", "code": 999, "message": "unknown function"})

    client = await _client(handler)
    with pytest.raises(RouterApiError, match="999"):
        await client._get("api.inexistante")
    assert calls["login"] == 1   # pas de ré-authentification inutile
    await client.aclose()


# --- décomposition de l'ECI ---------------------------------------------------

from app.peplink.router_api import _split_eci


def test_nsa_anchor_is_decomposed(wans):
    """En 5G NSA, `cellTower` désigne l'ancre LTE : l'ECI est décomposable."""
    assert [(w.enb_id, w.sector_id) for w in wans] == [(88393, 11), (88393, 5)]


@pytest.mark.parametrize(
    "cell_id,rat,expected",
    [
        (25735433, "LTE-A", (100529, 9)),
        (22628619, "5G NSA", (88393, 11)),      # ancre LTE d'une porteuse 5G
        (22628619, "5G SA", (None, None)),      # NCI : découpage non déterminable
        (1234, "LTE", (4, 210)),
        (None, "LTE", (None, None)),
        (1 << 28, "LTE", (None, None)),         # hors gabarit d'un ECI
        (-1, "LTE", (None, None)),
        (0, "LTE", (0, 0)),
    ],
)
def test_eci_split(cell_id, rat, expected):
    assert _split_eci(cell_id, rat) == expected


def test_wifi_wan_has_no_base_station(all_wans):
    wifi = next(w for w in all_wans if w.kind == "wifi")
    assert (wifi.enb_id, wifi.sector_id) == (None, None)


# --- WAN cellulaires des firmwares anciens ------------------------------------
#
# NON VALIDÉ SUR MATÉRIEL : personne dans l'équipe n'a de Peplink en 8.0.x.
# Ces replis sont écrits défensivement d'après la documentation, et testés
# uniquement contre des charges utiles synthétiques.

def test_legacy_gobi_wan_type_is_accepted():
    """Avant 8.0.1, un modem cellulaire s'annonce `type: "gobi"`."""
    payload = {"response": {"2": {
        "name": "SIM", "type": "gobi", "statusLed": "green",
        "cellular": {"network": "LTE", "carrier": {"name": "Orange"},
                     "rat": [{"name": "LTE", "band": [
                         {"name": "LTE Band 3 (1800 MHz)", "signal": {"rsrp": -90}}]}]},
    }}}
    wans = parse_wan_connection(payload)
    assert len(wans) == 1 and wans[0].kind == "cellular"
    assert wans[0].band == "B3" and wans[0].rsrp == -90.0


def test_legacy_flat_band_list_is_read():
    """Avant 8.1.2, les porteuses ne sont pas groupées par RAT mais listées à plat."""
    payload = {"response": {"2": {
        "name": "SIM", "type": "cellular", "statusLed": "green",
        "cellular": {"network": "LTE", "band": [
            {"name": "LTE Band 20 (800 MHz)", "signal": {"rsrp": -95, "sinr": 8}}]},
    }}}
    wan = parse_wan_connection(payload)[0]
    assert [b.band for b in wan.bands] == ["B20"]
    assert wan.bands[0].rat == "LTE"      # famille déduite du libellé
    assert wan.rsrp == -95.0 and wan.sinr == 8.0


def test_legacy_gobi_object_key():
    """Certains firmwares rangent les données sous `gobi` plutôt que `cellular`."""
    payload = {"response": {"2": {
        "name": "SIM", "type": "gobi", "statusLed": "green",
        "gobi": {"network": "LTE", "band": [
            {"name": "LTE Band 7 (2600 MHz)", "signal": {"rsrp": -88}}]},
    }}}
    wan = parse_wan_connection(payload)[0]
    assert wan.band == "B7" and wan.rsrp == -88.0


# --- mode client API ----------------------------------------------------------
#
# Mesuré sur MAX HD4 MBX 8.5.4, en créant un vrai client `api.read-only` :
#
#   form  clientId/clientSecret                -> stat: ok, jeton 48 h
#   form  client_id/client_secret/grant_type   -> 401 "Client not found"
#   JSON  clientId/clientSecret                -> 401 "Internal Error"
#   jeton en ?accessToken=                     -> ok
#   jeton en ?access_token= ou en Bearer       -> 401 Unauthorized
#
# Les noms de champs ne suivent donc PAS la convention OAuth2, malgré la forme
# de l'endpoint. Le code précédent utilisait la convention OAuth2 et n'aurait
# jamais fonctionné : ce mode n'avait jamais été exercé contre du matériel.

TOKEN = "b945f1af90acdb15f22ca49a5af31b67"


async def _api_client(handler) -> RouterApiPeplinkClient:
    cfg = RouterConfig(id="mbx", name="MBX", host="192.0.2.1",
                       client_id="ID-CLIENT", client_secret="SECRET-CLIENT")
    client = RouterApiPeplinkClient(cfg)
    await client._http.aclose()
    client._http = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    return client


async def test_token_grant_uses_camel_case_form_fields():
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/auth.token.grant":
            seen["content_type"] = request.headers.get("content-type", "")
            seen["body"] = request.content.decode()
            return httpx.Response(200, json={"stat": "ok", "response": {
                "accessToken": TOKEN, "scope": "api.read-only", "expiresIn": 172800}})
        seen["query"] = dict(request.url.params)
        return httpx.Response(200, json={"stat": "ok", "response": {}})

    client = await _api_client(handler)
    await client._get("status.wan.connection")

    assert "application/x-www-form-urlencoded" in seen["content_type"]
    assert "clientId=ID-CLIENT" in seen["body"]
    assert "clientSecret=SECRET-CLIENT" in seen["body"]
    assert "client_id" not in seen["body"] and "grant_type" not in seen["body"]
    await client.aclose()


async def test_token_is_passed_as_access_token_camel_case():
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/auth.token.grant":
            return httpx.Response(200, json={"stat": "ok", "response": {"accessToken": TOKEN}})
        assert request.url.params.get("accessToken") == TOKEN
        assert "access_token" not in request.url.params
        return httpx.Response(200, json={"stat": "ok", "response": {"ok": 1}})

    client = await _api_client(handler)
    assert (await client._get("status.wan.connection"))["stat"] == "ok"
    await client.aclose()


async def test_refused_client_credentials_are_reported():
    """« Client not found » doit remonter, pas se perdre en jeton absent."""
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"stat": "fail", "code": 401,
                                         "message": "Client not found"})

    client = await _api_client(handler)
    with pytest.raises(RouterApiError, match="Client not found"):
        await client.get_radio_status()
    await client.aclose()


# --- le jeton ne doit jamais sortir dans un message d'erreur -------------------
#
# Le jeton voyage en paramètre de requête, faute d'en-tête Bearer accepté par le
# firmware. Le message que httpx construit reproduit donc l'URL COMPLÈTE :
#   Server error '500 …' for url 'https://…/api/status.wan.connection?accessToken=…'
# et ce message finit dans `RouterSummary.error`, servi par une route HTTP qui
# n'est pas restreinte à la boucle locale.

def test_redact_masks_secret_query_values():
    brut = ("Server error '500 Internal Server Error' for url "
            f"'https://192.0.2.1/api/status.wan.connection?accessToken={TOKEN}&infoType=peer'")
    masque = redact(brut)
    assert TOKEN not in masque
    assert "accessToken=***" in masque
    assert "infoType=peer" in masque          # rien d'autre n'est touché
    assert redact("clientSecret=SECRET-CLIENT") == "clientSecret=***"


async def test_a_http_error_never_carries_the_access_token():
    """Un 500 sur une lecture ne doit pas publier le jeton du routeur."""
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/auth.token.grant":
            return httpx.Response(200, json={"stat": "ok", "response": {"accessToken": TOKEN}})
        return httpx.Response(500, text="Internal Server Error")

    client = await _api_client(handler)
    with pytest.raises(RouterApiError) as err:
        await client.get_radio_status()

    assert TOKEN not in str(err.value)
    assert "accessToken=***" in str(err.value)
    # Aucune `HTTPStatusError` brute ne subsiste : elle porte la même URL et
    # réapparaîtrait dans la moindre trace.
    assert err.value.__cause__ is None
    await client.aclose()


async def test_a_http_error_on_the_token_grant_is_reported_without_the_secret():
    """Le secret du client API voyage dans le corps du POST : même précaution."""
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(503, text="Service Unavailable")

    client = await _api_client(handler)
    with pytest.raises(RouterApiError) as err:
        await client.get_radio_status()

    assert "SECRET-CLIENT" not in str(err.value)
    assert "auth.token.grant" in str(err.value)
    await client.aclose()


# --- profil de capacités reconstitué par sondage ------------------------------
#
# `info.capability` n'est PAS couvert par le scope api.read-only : il répond
# Unauthorized avec un jeton de client API, alors que tout le reste passe.

async def test_capabilities_fall_back_to_probing():
    """Sans info.capability, le profil se déduit des endpoints qui répondent."""
    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path == "/api/login":
            return httpx.Response(200, json={"stat": "ok"})
        if path == "/api/info.capability":
            return httpx.Response(200, json={"stat": "fail", "code": 401,
                                             "message": "Unauthorized"})
        if path == "/api/status.wan.connection":
            return httpx.Response(200, json={"stat": "ok", "response": {
                "4": {"type": "cellular", "statusLed": "green", "name": "SIM",
                      "cellular": {"network": "LTE"}},
                "10": {"type": "wifi", "statusLed": "green", "name": "WiFi",
                       "wifi": {"channel": 36, "signal": {"strength": -60}}},
            }})
        if path == "/api/info.location":
            return httpx.Response(200, json={"stat": "ok", "response": {"gps": False}})
        if path == "/api/status.pepvpn":
            return httpx.Response(200, json={"stat": "fail", "code": 999,
                                             "message": "unknown function"})
        return httpx.Response(200, json={"stat": "ok", "response": {}})

    client = await _client(handler)
    caps = await client.get_capabilities()

    assert caps.source == "probed"
    assert caps.wan_types == {"4": "cellular", "10": "wifi"}
    assert caps.cellular_wans == 1
    # `info.location` répond : le module GPS existe, même sans point fixé.
    assert caps.gps is True
    assert caps.pepvpn is False
    await client.aclose()


async def test_declared_capabilities_are_preferred():
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/login":
            return httpx.Response(200, json={"stat": "ok"})
        if request.url.path == "/api/info.capability":
            return httpx.Response(200, json={"stat": "ok", "response": {
                "feature": {"gps": True, "pepvpn": True},
                "featureSupplementary": {"wan": {"connection": {
                    "1": {"type": "ethernet"}, "4": {"type": "cellular"}}}},
            }})
        if request.url.path == "/api/status.port":
            return httpx.Response(200, json={"stat": "ok", "response": {"wan": {}}})
        raise AssertionError("aucun sondage de repli ne doit avoir lieu")

    client = await _client(handler)
    caps = await client.get_capabilities()
    assert caps.source == "declared"
    assert caps.wan_types == {"1": "ethernet", "4": "cellular"}
    await client.aclose()


# --- 401 qui survit au login : des droits, pas une session expirée ------------

async def test_a_401_that_survives_a_fresh_login_is_a_rights_refusal():
    """Login réussi, puis 401 sur l'endpoint : ce ne sont pas les identifiants.

    Ce cas était classé « session expirée » : ré-authentification, relance,
    401 de nouveau, `AuthenticationError`. À chaque lecture. Sept POST
    /api/login pour trois lectures de la fiche, reproduit à l'audit — sur un
    firmware qui suspend l'accès cinq minutes au bout de cinq échecs, et une
    fiche qui restait « provisoire » pour toujours. Un refus qui survit à un
    login qui vient de réussir est un refus de droits, comme un 403.
    """
    logins = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal logins
        if request.url.path == "/api/login":
            logins += 1
            return httpx.Response(200, json={"stat": "ok"})
        if request.url.path == "/api/status.wan.connection":
            return httpx.Response(200, json={"stat": "ok", "response": {}})
        return httpx.Response(200, json={"stat": "fail", "code": 401,
                                         "message": "Unauthorized"})

    client = await _client(handler)
    with pytest.raises(EndpointRefusedError) as refus:
        await client._get("status.wan.traffic")
    assert refus.value.code == 401
    # Un login à l'ouverture, un second pour lever le doute — et c'est tout :
    # l'endpoint est retenu hors du périmètre, plus jamais redemandé.
    assert logins == 2
    with pytest.raises(EndpointRefusedError):
        await client._get("status.wan.traffic")
    assert logins == 2
    await client.aclose()


async def test_capabilities_out_of_scope_are_probed_once_and_settled():
    """Le scénario de l'audit : trois lectures de la fiche, un seul login.

    `info.capability` hors du périmètre faisait retomber sur le sondage avec
    `provisional=True` — donc une nouvelle lecture au cycle suivant, avec sa
    ré-authentification. La fiche sondée sur un refus de droits est DÉFINITIVE :
    l'équipement ne dira jamais plus au prochain tour.
    """
    logins = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal logins
        if request.url.path == "/api/login":
            logins += 1
            return httpx.Response(200, json={"stat": "ok"})
        if request.url.path == "/api/info.capability":
            return httpx.Response(200, json={"stat": "fail", "code": 401,
                                             "message": "Unauthorized"})
        return httpx.Response(200, json={"stat": "ok", "response": {}})

    client = await _client(handler)
    for _ in range(3):
        caps = await client.get_capabilities()
        assert caps is not None
        assert caps.source == "probed"
        assert caps.provisional is False
    assert logins == 2   # ouverture + levée de doute, pas un par lecture
    await client.aclose()


async def test_a_login_cut_by_the_deadline_still_records_its_refusal():
    """L'échéance des compléments annule leur coroutine — y compris une
    reconnexion en vol. Le `POST /api/login` partait, le routeur comptait
    l'échec, mais l'annulation tombait avant la lecture de la réponse : le
    refus n'était pas mémorisé, et le cycle suivant retentait un login. Cinq
    cycles coupés ainsi suffisaient à suspendre l'accès admin cinq minutes.

    La reconnexion va désormais à son terme quoi qu'il arrive à l'appelant, et
    un appel concurrent attend LA MÊME tentative au lieu d'en ouvrir une autre.
    """
    from app.peplink.router_api import avant_echeance

    logins = 0

    async def handler(request: httpx.Request) -> httpx.Response:
        nonlocal logins
        if request.url.path == "/api/login":
            logins += 1
            await asyncio.sleep(0.2)
            return httpx.Response(200, json={"stat": "fail", "message": "mauvais mot de passe"})
        return httpx.Response(200, json={"stat": "ok", "response": {}})

    client = await _client(handler)
    boucle = asyncio.get_running_loop()
    fait, _ = await avant_echeance(boucle.time() + 0.05, client._get("status.traffic"))
    assert fait is False, "l'échéance a bien coupé l'appelant"

    # Pendant que la tentative est encore en vol, un autre appel la rejoint.
    with pytest.raises(CredentialsRefusedError):
        await client._get("status.wan.connection")
    assert logins == 1
    assert client._credentials_refused is True

    # Et le refus tient : plus aucun login.
    with pytest.raises(CredentialsRefusedError):
        await client._get("status.wan.connection")
    assert logins == 1
    await client.aclose()


async def test_a_cancelled_caller_alone_does_not_lose_the_refusal():
    """Même scénario sans appel concurrent : le refus est mémorisé par la
    tentative elle-même, une fois la réponse arrivée."""
    from app.peplink.router_api import avant_echeance

    logins = 0

    async def handler(request: httpx.Request) -> httpx.Response:
        nonlocal logins
        if request.url.path == "/api/login":
            logins += 1
            await asyncio.sleep(0.1)
            return httpx.Response(200, json={"stat": "fail", "message": "refusé"})
        return httpx.Response(200, json={"stat": "ok", "response": {}})

    client = await _client(handler)
    boucle = asyncio.get_running_loop()
    fait, _ = await avant_echeance(boucle.time() + 0.02, client._get("status.traffic"))
    assert fait is False
    await asyncio.sleep(0.2)
    assert client._credentials_refused is True
    with pytest.raises(CredentialsRefusedError):
        await client._get("status.wan.connection")
    assert logins == 1
    await client.aclose()


async def test_concurrent_first_reads_share_a_single_login():
    """Identité, fiche et relevé partent ensemble au premier cycle.

    Chacun trouvait la session absente et lançait son propre POST /api/login :
    trois logins pour une session — et sur des identifiants faux, trois des
    cinq échecs qui suspendent l'accès admin, consommés en un seul cycle.
    """
    import asyncio
    logins = 0

    async def handler(request: httpx.Request) -> httpx.Response:
        nonlocal logins
        if request.url.path == "/api/login":
            logins += 1
            await asyncio.sleep(0.01)      # le temps que les autres arrivent
            return httpx.Response(200, json={"stat": "ok"})
        return httpx.Response(200, json={"stat": "ok", "response": {}})

    client = await _client(handler)
    await asyncio.gather(
        client._get("status.system.info"),
        client._get("info.capability"),
        client._get("status.wan.connection"),
    )
    assert logins == 1
    await client.aclose()


async def test_a_session_renewed_by_another_call_is_not_renewed_twice():
    """Deux lectures voient la même session expirer : un seul renouvellement.

    La seconde attend derrière le verrou, constate que la génération a changé,
    et relit avec la session neuve au lieu d'en ouvrir une troisième.
    """
    import asyncio
    logins = 0
    expiree = False

    async def handler(request: httpx.Request) -> httpx.Response:
        nonlocal logins, expiree
        if request.url.path == "/api/login":
            logins += 1
            expiree = False             # la session neuve est valide
            return httpx.Response(200, json={"stat": "ok"})
        if expiree:
            await asyncio.sleep(0.01)   # les deux lectures voient le refus
            return httpx.Response(200, json={"stat": "fail", "code": 401,
                                             "message": "Unauthorized"})
        return httpx.Response(200, json={"stat": "ok", "response": {}})

    client = await _client(handler)
    await client._get("status.wan.connection")
    assert logins == 1

    expiree = True
    await asyncio.gather(client._get("status.system.info"), client._get("info.capability"))
    assert logins == 2
    await client.aclose()


async def test_new_credentials_reopen_the_perimeter():
    """Un autre compte, un autre périmètre : la mémoire des refus est effacée."""
    refuse = True

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/login":
            return httpx.Response(200, json={"stat": "ok"})
        if refuse:
            return httpx.Response(200, json={"stat": "fail", "code": 401,
                                             "message": "Unauthorized"})
        return httpx.Response(200, json={"stat": "ok", "response": {"a": 1}})

    client = await _client(handler)
    with pytest.raises(EndpointRefusedError):
        await client._get("info.capability")
    refuse = False
    with pytest.raises(EndpointRefusedError):       # retenu, pas redemandé
        await client._get("info.capability")
    client.allow_new_credentials()
    assert (await client._get("info.capability"))["response"] == {"a": 1}
    await client.aclose()


@pytest.mark.parametrize("endpoint", ["status.wan.connection", "status.system.info"])
async def test_the_main_endpoints_are_never_retained_out_of_scope(endpoint):
    """C-1 : deux 401 autour d'un login réussi ne condamnent pas le relevé.

    La session de 48 h expire, le relogin réussit, et la relance prend encore
    un 401 — le serveur web du routeur redémarre, par exemple. L'endpoint
    principal était alors retenu hors du périmètre : plus aucune requête, un
    équipement « injoignable » pour la vie du processus, et le seul moyen d'en
    sortir — « Réessayer la connexion » — n'apparaissait pas à l'écran.
    """
    refuse = False
    envoyes = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal envoyes
        if request.url.path == "/api/login":
            return httpx.Response(200, json={"stat": "ok"})
        envoyes += 1
        if refuse:
            return httpx.Response(200, json={"stat": "fail", "code": 401,
                                             "message": "Unauthorized"})
        return httpx.Response(200, json={"stat": "ok", "response": {"a": 1}})

    client = await _client(handler)
    await client._get(endpoint)

    refuse = True
    with pytest.raises(EndpointRefusedError):
        await client._get(endpoint)

    refuse = False
    avant = envoyes
    assert (await client._get(endpoint))["response"] == {"a": 1}
    assert envoyes == avant + 1, "le cycle suivant doit réinterroger l'équipement"
    await client.aclose()


async def test_an_endpoint_out_of_scope_is_asked_again_later(monkeypatch):
    """Un complément retenu hors du périmètre n'y reste pas pour toujours.

    Le même 401 passager condamnait `status.traffic` jusqu'au redémarrage du
    serveur. Retenu, il n'est plus redemandé à chaque cycle — c'est la raison
    d'être du mécanisme — mais il l'est de nouveau passé un délai.
    """
    from app.peplink import router_api

    horloge = [1000.0]
    monkeypatch.setattr(router_api.time, "monotonic", lambda: horloge[0])
    refuse = True
    envoyes = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal envoyes
        if request.url.path == "/api/login":
            return httpx.Response(200, json={"stat": "ok"})
        envoyes += 1
        if refuse:
            return httpx.Response(200, json={"stat": "fail", "code": 401,
                                             "message": "Unauthorized"})
        return httpx.Response(200, json={"stat": "ok", "response": {"a": 1}})

    client = await _client(handler)
    with pytest.raises(EndpointRefusedError):
        await client._get("status.traffic")
    assert envoyes == 2

    refuse = False
    horloge[0] += router_api._HORS_PERIMETRE_REESSAI_S - 1
    with pytest.raises(EndpointRefusedError):     # encore retenu
        await client._get("status.traffic")
    assert envoyes == 2

    horloge[0] += 2
    assert (await client._get("status.traffic"))["response"] == {"a": 1}
    assert envoyes == 3
    await client.aclose()


async def test_slow_complements_never_cost_the_wan_reading(capture):
    """C-5 : le relevé WAN est lu en 0,4 s, puis `status.traffic` expire à
    15 s et `status.wan.latency` met 6 s. Le budget de 20 s couvrait le tout :
    le cycle était perdu, l'équipement déclaré « pas de réponse en 20 s »,
    cycle après cycle — alors que les compléments « ne doivent jamais
    détruire les états WAN déjà lus ». Passé l'échéance, ils sont abandonnés
    et les états WAN publiés.
    """
    lents: list[str] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/login":
            return httpx.Response(200, json={"stat": "ok"})
        if request.url.path == "/api/status.wan.connection":
            return httpx.Response(200, json=capture["status.wan.connection"])
        lents.append(request.url.path)
        await asyncio.sleep(5)
        return httpx.Response(200, json={"stat": "ok", "response": {}})

    client = await _client(handler)
    boucle = asyncio.get_running_loop()
    debut = boucle.time()
    statuses = await client.get_radio_status(echeance=debut + 0.3)
    assert boucle.time() - debut < 1.5, "l'échéance des compléments n'a pas été tenue"
    assert len(statuses) == len(parse_wan_connection(capture["status.wan.connection"]))
    assert lents, "un complément au moins a été tenté"
    await client.aclose()


async def test_without_a_deadline_the_complements_run_as_before(capture):
    """Le pendant positif : sans échéance, rien n'est abandonné."""
    demandes: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/login":
            return httpx.Response(200, json={"stat": "ok"})
        demandes.append(request.url.path)
        if request.url.path == "/api/status.wan.connection":
            return httpx.Response(200, json=capture["status.wan.connection"])
        return httpx.Response(200, json={"stat": "ok", "response": {}})

    client = await _client(handler)
    await client.get_radio_status()
    assert "/api/status.traffic" in demandes
    await client.aclose()


async def test_a_refused_info_type_does_not_condemn_its_siblings():
    """C-6 : `status.pepvpn` sert trois lectures — peer, profile, tunnel.

    La mémoire des refus était indexée par le nom seul : un refus de
    `infoType=profile` (ou une simple capture de profil) condamnait aussi
    `peer` et `tunnel`, la supervision du bond s'éteignait pour la vie du
    processus, et la chute du concentrateur passait inaperçue.
    """
    demandes: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/login":
            return httpx.Response(200, json={"stat": "ok"})
        info = request.url.params.get("infoType")
        demandes.append(info)
        if info == "profile":
            return httpx.Response(200, json={"stat": "fail", "code": 401,
                                             "message": "Unauthorized"})
        return httpx.Response(200, json={"stat": "ok", "response": {"a": 1}})

    client = await _client(handler)
    with pytest.raises(EndpointRefusedError):
        await client._get("status.pepvpn", {"infoType": "profile"})
    with pytest.raises(EndpointRefusedError):     # profile, lui, reste retenu
        await client._get("status.pepvpn", {"infoType": "profile"})
    assert demandes.count("profile") == 2
    assert (await client._get("status.pepvpn", {"infoType": "peer"}))["response"] == {"a": 1}
    assert (await client._get(
        "status.pepvpn", {"infoType": "tunnel", "tunnelOption": "1"}))["response"] == {"a": 1}
    await client.aclose()


async def test_unsupported_traffic_stops_being_requested():
    """Un modèle qui ne propose pas l'endpoint ne doit pas être resollicité."""
    calls = {"traffic": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/login":
            return httpx.Response(200, json={"stat": "ok"})
        if request.url.path == "/api/status.traffic":
            calls["traffic"] += 1
            return httpx.Response(200, json={"stat": "fail", "code": 999,
                                             "message": "unknown function"})
        return httpx.Response(200, json={"stat": "ok", "response": {}})

    client = await _client(handler)
    for _ in range(3):
        await client.get_radio_status()
    assert calls["traffic"] == 1
    assert client._retenu("status.traffic") is not None
    await client.aclose()


# --- un refus d'identifiants arrive AUSSI par le statut HTTP -------------------

async def test_a_http_refusal_on_the_token_endpoint_arms_the_lock():
    """Un 401 sur `auth.token.grant` est un refus d'identifiants, pas une panne.

    Mesuré sur MAX HD4 MBX 8.5.4 et documenté dans `_auth_client_credentials` :
    envoyer `client_id`/`client_secret` au lieu de `clientId`/`clientSecret` rend
    un **401 « Client not found »** — un statut HTTP, pas un `{"stat":"fail"}`
    sur 200. Ce chemin repartait en `RouterApiError` nue, que la boucle de
    scrutation range dans son `except Exception` générique : le verrou ne
    s'armait pas, l'équipement était dit « injoignable » au lieu de
    « identifiants refusés », et le cycle suivant retentait. À 5,3 s de cadence,
    les cinq échecs qui suspendent l'accès admin cinq minutes sont atteints en
    vingt-six secondes — l'outil verrouillait l'exploitant hors de son routeur.
    """
    tentatives = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        tentatives["n"] += 1
        return httpx.Response(401, json={"message": "Client not found"})

    client = await _api_client(handler)
    with pytest.raises(CredentialsRefusedError):
        await client.get_radio_status()
    assert client._credentials_refused is True

    # Et surtout : plus AUCUNE tentative tant que l'exploitant n'a rien corrigé.
    for _ in range(5):
        with pytest.raises(CredentialsRefusedError):
            await client.get_radio_status()
    assert tentatives["n"] == 1, "le verrou n'a pas retenu la boucle"

    client.allow_new_credentials()
    with pytest.raises(CredentialsRefusedError):
        await client.get_radio_status()
    assert tentatives["n"] == 2, "une action explicite doit rouvrir une tentative"
    await client.aclose()


async def test_a_http_refusal_on_the_login_endpoint_arms_the_lock():
    """Même règle pour la session admin : cinq échecs suspendent l'accès."""
    tentatives = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        tentatives["n"] += 1
        return httpx.Response(403, text="Forbidden")

    client = await _client(handler)
    for _ in range(4):
        with pytest.raises(CredentialsRefusedError):
            await client.get_radio_status()
    assert tentatives["n"] == 1
    await client.aclose()


async def test_a_server_error_on_the_login_endpoint_stays_retryable():
    """Un 500 est une panne de l'équipement, pas un mauvais mot de passe.

    Condamner les identifiants sur un 5xx interdirait toute reprise automatique
    après un redémarrage du firmware, alors que rien ne dit qu'ils sont mauvais.
    """
    tentatives = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        tentatives["n"] += 1
        return httpx.Response(500, text="Internal Server Error")

    client = await _client(handler)
    for _ in range(3):
        with pytest.raises(RouterApiError) as err:
            await client.get_radio_status()
        assert not isinstance(err.value, CredentialsRefusedError)
    assert tentatives["n"] == 3, "une panne passagère doit rester retentable"
    assert client._credentials_refused is False
    await client.aclose()


async def test_a_http_refusal_never_carries_the_access_token():
    """Le message d'un refus 4xx passe par `redact` comme les autres."""
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(401, text="nope")

    client = await _api_client(handler)
    with pytest.raises(CredentialsRefusedError) as err:
        await client.get_radio_status()
    assert TOKEN not in str(err.value)
    await client.aclose()


def test_a_payload_of_an_unexpected_SHAPE_does_not_raise():
    """Tous les analyseurs repliaient `response` sur `{}` par un `or`.

    Ce repli couvre `None` et le vide — pas une CHAÎNE, ni une liste non vide,
    ni un nombre. Mesuré en passant douze charges dégénérées : neuf analyseurs
    levaient `AttributeError: 'str' object has no attribute 'get'`.

    Le chemin d'erreur rattrapait bien, mais il étiquetait « injoignable » un
    équipement qui avait répondu — la confusion de diagnostic que ce projet
    combat ailleurs, celle qui envoie vérifier un raccordement quand il faut
    lire une charge utile.
    """
    import inspect

    from app.peplink import router_api as ra

    analyseurs = [
        f
        for n, f in vars(ra).items()
        if n.startswith("parse_")
        and inspect.isfunction(f)
        and len(inspect.signature(f).parameters) == 1
    ]
    assert analyseurs, "aucun analyseur trouvé — le test ne prouverait rien"
    charges = [
        {},
        {"stat": "ok"},
        {"response": None},
        {"response": []},
        {"response": "boom"},
        {"response": 42},
        {"response": [{"a": 1}]},
        {"response": {"order": "pas une liste", "1": 42}},
        "pas un objet",
        None,
        7,
    ]
    for f in analyseurs:
        for charge in charges:
            f(charge)  # ne doit rien lever


def test_a_CREDENTIALS_refusal_does_not_condemn_the_per_wan_throughput():
    """`RouterApiError.code` porte deux choses selon le chemin.

    Le statut HTTP quand `_raise_for_status` lève, le code du firmware quand
    `_get` lève. Un refus d'identifiants sur `/api/login` arrive donc avec
    `code = 403`, indistinguable d'un refus de DROITS sur un endpoint — et les
    trois gardes `exc.code != _FORBIDDEN_CODE` éteignaient la sonde pour toute la
    durée du processus. C'est exactement la régression que leur commentaire dit
    empêcher.
    """
    import asyncio

    from app.fleet import RouterConfig
    from app.peplink.router_api import CredentialsRefusedError, RouterApiPeplinkClient

    client = RouterApiPeplinkClient(RouterConfig(id="r", name="R", host="h"))

    async def refus(*_a, **_k):
        raise CredentialsRefusedError("identifiants refusés", code=403)

    client._get = refus  # type: ignore[method-assign]
    assert client._retenu("status.traffic") is None

    async def scenario() -> None:
        try:
            await client._attach_traffic([])
        except CredentialsRefusedError:
            pass
        else:  # pragma: no cover - le refus DOIT remonter
            raise AssertionError("le refus d'identifiants a été avalé")

    asyncio.run(scenario())
    assert client._retenu("status.traffic") is None, "un refus d'identifiants a condamné la sonde"


def test_new_credentials_revive_what_the_refusal_had_condemned():
    """Après une reprise d'identifiants, chaque sonde redonne sa chance.

    `allow_new_credentials` ne relevait que le verrou, le jeton et
    l'authentification : le débit par WAN restait éteint jusqu'au redémarrage du
    serveur, sans rien qui le signale.
    """
    from app.fleet import RouterConfig
    from app.peplink.router_api import RouterApiPeplinkClient

    import time

    client = RouterApiPeplinkClient(RouterConfig(id="r", name="R", host="h"))
    loin = time.monotonic() + 3600
    client._retenus["status.traffic"] = (loin, "droits", 401)
    client._retenus["status.pepvpn?infoType=peer"] = (loin, "non proposé", 999)
    client._tunnel_skip = 10
    client._credentials_refused = True

    client.allow_new_credentials()

    assert client._credentials_refused is False
    assert client._retenu("status.traffic") is None
    assert client._retenu("status.pepvpn", {"infoType": "peer"}) is None
    assert client._tunnel_skip == 0


# --------------------------------------------------------------------------
# Un champ mal typé ne doit pas rendre un routeur muet
# --------------------------------------------------------------------------

def test_a_numeric_text_field_does_not_kill_the_whole_poll():
    """Trois fonctions passaient un champ du routeur à une regex ou à `.upper()`.

    Mesuré à l'exécution, avant correction :

        _split_eci(1000, 4) -> AttributeError: 'int' object has no attribute 'upper'
        _rat_family(4)      -> TypeError: expected string or bytes-like object
        _band_code(3)       -> TypeError: expected string or bytes-like object

    La levée remonte du parseur au relevé, donc à la boucle : c'est le cycle
    ENTIER de cet équipement qui tombe, et il retombe au cycle suivant sur la
    même charge utile. Un seul champ mal typé suffisait à rendre un routeur
    définitivement muet — sans qu'aucun écran ne puisse dire pourquoi.
    """
    from app.peplink.router_api import _texte

    charge = {
        "response": {
            "4": {
                "name": "SIM 4",
                "type": "cellular",
                "statusLed": "green",
                "cellular": {
                    "network": 4,                       # au lieu de "LTE"
                    "dataTechnology": 5,                # au lieu de "5G NSA"
                    "cellTower": {"cellId": 15941386},
                    "rat": [{
                        "name": 7,                      # au lieu de "LTE"
                        "band": [{
                            "name": 3,                  # au lieu de "LTE Band 3 (1800 MHz)"
                            "channelWidth": 20,         # au lieu de "20 MHz"
                            "signal": {"rsrp": -88, "sinr": 12},
                        }],
                    }],
                },
            }
        }
    }

    statuses = parse_wan_connection(charge)

    # Le relevé existe, et ce qui était mesurable l'est resté.
    assert len(statuses) == 1
    wan = statuses[0]
    assert wan.wan_id == "4" and wan.connected is True
    assert wan.rsrp == -88.0 and wan.sinr == 12.0
    # Et la station se décompose quand même : seul le libellé était mal typé.
    assert wan.enb_id == 62271

    # `_texte` est la frontière. Un nombre porte une information lisible et se
    # convertit ; une structure n'en porte pas, et en fabriquer un libellé
    # serait une invention — ce que ce parseur refuse déjà pour un ECI 5G SA.
    assert _texte("  LTE-A  ") == "LTE-A"
    assert _texte(3) == "3"
    assert _texte("") is None and _texte("   ") is None
    # `bool` est un `int` en Python : sans garde, `True` deviendrait "True".
    assert _texte(True) is None
    assert _texte(["B3"]) is None and _texte({"a": 1}) is None
    assert _texte(float("nan")) is None and _texte(float("inf")) is None


def test_an_unreadable_payload_never_costs_the_whole_reading():
    """`parse_tunnel` avait reçu un filet ; `parse_traffic` et `parse_ports` non.

    Leurs deux `try` n'attrapaient que `RouterApiError` et `httpx.HTTPError` —
    les fautes du TRANSPORT. Une charge utile inattendue fait lever l'ANALYSE
    d'un `TypeError` qui les traverse : pour `parse_traffic` il remontait
    jusqu'au poller, et l'équipement était étiqueté « injoignable » alors qu'il
    venait de répondre ; pour `parse_ports` il faisait retomber tout
    `get_capabilities` sur le sondage. Un routeur muet et un routeur bavard mais
    mal compris demandent deux remèdes opposés.
    """
    import asyncio
    from unittest.mock import AsyncMock, patch

    from app.fleet import RouterConfig
    from app.models import WanRadioStatus
    from app.peplink.router_api import RouterApiPeplinkClient

    client = RouterApiPeplinkClient(RouterConfig(id="r", name="R", host="h"))
    statuses = [WanRadioStatus(wan_id="4", wan_name="SIM 4", rx_kbps=None, tx_kbps=None)]

    # Une charge utile que l'analyseur ne sait pas lire : `bandwidth` est une liste.
    charge = {"response": {"bandwidth": ["pas", "un", "objet"]}}
    with patch.object(client, "_get", AsyncMock(return_value=charge)):
        asyncio.run(client._attach_traffic(statuses))

    # Le relevé WAN a survécu, simplement sans débit.
    assert len(statuses) == 1 and statuses[0].wan_id == "4"
    assert statuses[0].rx_kbps is None and statuses[0].tx_kbps is None
    # Et la sonde n'est pas condamnée : c'est une charge illisible, pas un refus.
    assert client._retenu("status.traffic") is None


def test_a_lone_carrier_at_pci_zero_is_read_as_not_reported():
    """Décision INVERSÉE par la mesure.

    Une version gardait le `pci: 0` d'une porteuse unique comme une mesure, au
    nom du modem sans agrégation qui camperait réellement sur la cellule de
    PCI 0. Le MAX HD2 (Quectel EC25) a tranché : ses deux WAN — deux
    opérateurs, deux cellules — publient chacun une porteuse à `pci: 0`. Une
    chance sur 504² contre un modem qui ne le publie pas. Le PCI 0 réel ne
    tient que si une autre porteuse du même modem en publie un différent.
    """
    charge = {"response": {"2": {"type": "cellular", "statusLed": "green", "cellular": {
        "network": "LTE",
        "rat": [{"name": "LTE", "band": [
            {"name": "LTE Band 3 (1800 MHz)", "pci": 0, "signal": {"rsrp": -88}},
        ]}],
    }}}}
    wan = parse_wan_connection(charge)[0]
    assert [b.pci for b in wan.bands] == [None]
    assert wan.pci is None


def test_a_wifi_wan_carries_its_address_and_its_priority():
    """Deux champs simplement omis de `_parse_wifi_wan`.

    La carte d'un lien Wi-Fi montrait deux cases vides là où celle d'un modem
    les renseigne : une absence de CODE lue comme une absence de DONNÉE.
    """
    charge = {"response": {"3": {
        "type": "wifi", "statusLed": "green", "name": "Hotspot",
        "ip": "192.0.2.55", "priority": 2,
        "wifi": {"ssid": "Relais", "bssid": "00:11:22:33:44:55", "channel": 149,
                 "signal": {"strength": -58, "level": 4}},
    }}}
    wan = parse_wan_connection(charge)[0]
    assert wan.kind == "wifi"
    assert wan.ip == "192.0.2.55"
    assert wan.priority == 2
    assert wan.wifi is not None and wan.wifi.band == "5 GHz"


def test_le_schema_de_l_hote_est_respecte_et_https_reste_le_defaut():
    """`host` nu -> HTTPS, comme depuis le début ; `host` avec schéma -> tel quel."""
    from app.peplink.router_api import _base_url

    assert _base_url("192.168.63.1") == "https://192.168.63.1"
    assert _base_url("http://10.10.1.1") == "http://10.10.1.1"
    assert _base_url("https://routeur.local") == "https://routeur.local"
    # Une barre finale ne doit jamais doubler celle de `/api/…`.
    assert _base_url("http://10.10.1.1/") == "http://10.10.1.1"
    assert _base_url("  10.10.1.1  ") == "https://10.10.1.1"


def test_hote_canonique_ignore_schema_barre_et_casse():
    """Trois graphies, un équipement — et le schéma n'est pas l'identité."""
    from app.peplink.router_api import hote_canonique, _base_url
    assert hote_canonique("10.10.1.1") == hote_canonique("http://10.10.1.1")
    assert hote_canonique("http://10.10.1.1/") == hote_canonique("HTTPS://10.10.1.1")
    assert hote_canonique(" routeur.local ") == "routeur.local"
    assert hote_canonique(None) is None and hote_canonique("") is None
    # Et l'URL de connexion garde le schéma, en minuscules.
    assert _base_url("HTTP://10.10.1.1") == "http://10.10.1.1"


def test_a_truncated_cell_id_is_read_from_its_full_form():
    """Dome Pro Duo, firmware 8.5.1 build 5714 : `cellId` n'est que les seize
    bits bas de `cellUtranId`. Lu tel quel, il donnait une cellule et une
    station (eNB 33) inexistantes. Sur 8.5.4, les deux sont égaux."""
    def charge(cell_id, utran):
        return {"response": {"2": {"type": "cellular", "statusLed": "green", "cellular": {
            "network": "5G", "dataTechnology": "5G NSA",
            "cellTower": {"cellId": cell_id, "cellUtranId": utran, "cellPlmn": 20801},
            "rat": [{"name": "5G", "band": [{"name": "5G Band n78 (3500 MHz)", "signal": {"rsrp": -80}}]},
                    {"name": "LTE", "band": [{"name": "LTE Band 3 (1800 MHz)", "signal": {"rsrp": -85}}]}],
        }}}}
    tronque = parse_wan_connection(charge(8470, 25698582))[0]
    assert tronque.cell_id == 25698582
    assert (tronque.enb_id, tronque.sector_id) == (100385, 22)
    # Égaux (8.5.4) : rien ne change.
    assert parse_wan_connection(charge(22628619, 22628619))[0].cell_id == 22628619
    # Différents SANS la relation des seize bits : on ne devine pas.
    assert parse_wan_connection(charge(1234, 25698582))[0].cell_id == 1234
    # `cellUtranId` absent : `cellId`.
    assert parse_wan_connection(charge(8470, None))[0].cell_id == 8470


def test_the_wans_the_router_declares_disabled_are_named():
    """`enable: false` : le parseur écarte ces WAN des relevés — à raison, ils ne
    mesurent rien — mais leur identifiant doit rester connu, sans quoi un WAN
    que l'exploitant désactive se lit comme un lien « absent du relevé »."""
    from app.peplink.router_api import parse_disabled_wans
    charge = {"response": {
        "2": {"type": "cellular", "enable": True, "statusLed": "green", "cellular": {"rat": []}},
        "3": {"type": "cellular", "enable": False},
        "4": {"type": "wifi", "enable": False},
        "order": [2, 3, 4],
    }}
    assert parse_disabled_wans(charge) == {"3", "4"}
    assert [s.wan_id for s in parse_wan_connection(charge)] == ["2"]
    assert parse_disabled_wans({"stat": "fail"}) == set()


def test_a_carrier_without_a_label_is_not_published():
    """Pendant une renégociation, le routeur publie une porteuse sans libellé :
    la bande sortait « ? » à l'écran (scénarios 1 à 3 de la revue), un mot
    qui n'est ni une mesure ni une absence dite. Sans libellé, une porteuse n'a
    pas d'identité : elle ne se nomme ni ne se compare, et n'est pas publiée."""
    charge = {"response": {"3": {"type": "cellular", "statusLed": "yellow", "cellular": {
        "network": "", "rat": [{"name": "", "band": [{"name": "", "signal": {}}]}],
    }}}}
    wan = parse_wan_connection(charge)[0]
    assert wan.band is None
    assert wan.bands == []
    # Une porteuse nommée à côté reste, elle.
    charge["response"]["3"]["cellular"]["rat"][0]["band"].append(
        {"name": "LTE Band 3 (1800 MHz)", "signal": {"rsrp": -80}})
    assert [b.band for b in parse_wan_connection(charge)[0].bands] == ["B3"]


async def test_an_unread_router_log_is_none_not_an_empty_log():
    """Un journal qui n'a pas pu être lu rend None, jamais `[]`.

    `[]` est un constat : « aucun fait dans le journal », que le poller note
    comme une lecture aboutie et dont la fiche tire « 0 manœuvre ». Une panne
    passagère, une échéance ou un refus retenu de `status.log` ne sont pas ce
    constat.
    """
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/login":
            return httpx.Response(200, json={"stat": "ok"})
        if request.url.params.get("func") == "status.log" or "status.log" in str(request.url):
            return httpx.Response(200, json={"stat": "fail", "code": 999, "message": "Unknown function"})
        return httpx.Response(200, json={"stat": "ok", "response": {"timestamp": 1}})

    client = await _client(handler)
    try:
        assert await client.get_router_events({}) is None
        # Le refus est désormais retenu : la question n'est même plus posée,
        # et la réponse reste « non lu ».
        assert await client.get_router_events({}) is None
    finally:
        await client.aclose()
