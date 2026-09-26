"""Tunnel SpeedFusion : ce qui porte réellement le flux.

L'application jugeait lien par lien. Or un lien peut être parfaitement
« connecté » côté WAN et **écarté du bond** — il ne transporte alors rien. Et
les grandeurs qui font geler une image en bonding — latence, perte, désordre de
paquets — n'existent que dans cette vue.

Capture réelle : MAX HD4 MBX, tunnel vers un concentrateur, cinq liens actifs
(une fibre à 2 ms, quatre SIM entre 20 et 56 ms) et cinq liens hors du bond.
"""

import json
from pathlib import Path

import httpx
import pytest

from app.fleet import RouterConfig
from app.models import WanRadioStatus
from app.peplink.router_api import (
    _Counters,
    CredentialsRefusedError,
    RouterApiPeplinkClient,
    _LinkRates,
    parse_latency_cutoff,
    parse_peers,
    parse_tunnel,
    tunnel_counters,
)

# Aucun taux, aucun dénominateur : le cas où l'intervalle n'est pas mesurable.
# Comparé au type plutôt qu'à un n-uplet littéral, pour que l'ajout d'une
# grandeur ne se traduise pas par une ligne de `None` supplémentaire à recopier.
_RIEN_MESURÉ = _LinkRates()

FIXTURE = Path(__file__).parent / "fixtures" / "max_hd4_mbx_8.5.4.json"


@pytest.fixture(scope="module")
def capture() -> dict:
    with open(FIXTURE, encoding="utf-8") as f:
        return json.load(f)


# --- pairs --------------------------------------------------------------------

def test_peers_are_listed_with_their_state(capture):
    """L'emplacement SpeedFusion Connect suspendu — `status: ""`, sans nom ni
    compte — n'est pas un pair tombé : c'est un emplacement vide. Le compter
    mettait PEER_DOWN sur les quatre SIM d'un châssis sans profil PepVPN."""
    peers = parse_peers(capture["status.pepvpn.peer"])
    assert [(p[0], p[2]) for p in peers] == [("1-1", True)]


def test_an_empty_slot_alone_is_no_peer_at_all():
    vide = {"stat": "ok", "response": {"peer": [
        {"status": "", "name": "", "profileId": 60000, "route": [], "peerId": "60000-0"},
    ]}}
    assert parse_peers(vide) == []
    # Un pair déclaré et tombé, lui, reste un pair.
    tombe = {"stat": "ok", "response": {"peer": [
        {"status": "START", "name": "", "profileId": 4, "route": [], "peerId": "4-0"},
    ]}}
    assert parse_peers(tombe) == [("4-0", None, False)]


def test_no_peer_is_not_an_error():
    assert parse_peers({}) == []
    assert parse_peers({"response": {"peer": []}}) == []


# --- état des liens dans le bond ----------------------------------------------

@pytest.fixture(scope="module")
def tunnel(capture):
    return parse_tunnel(capture["status.pepvpn.tunnel"], "1-1", "CONCENTRATEUR-0")


def test_links_carry_their_state_in_the_bond(tunnel):
    """Cinq liens portent le flux, cinq sont là sans y participer."""
    actifs = {w for w, l in tunnel.links.items() if l.in_bond}
    assert actifs == {"1", "4", "5", "6", "7"}
    assert tunnel.links["8"].state == "WAN_DOWN"
    assert tunnel.links["2"].state == "WAN_DISABLED"


def test_latency_is_read_per_link(tunnel):
    # La fibre répond en 2 ms, les SIM entre 20 et 56 ms : c'est cet écart qui
    # décide de la composition du bond.
    assert tunnel.links["1"].rtt_ms == 2.0
    assert 15 <= tunnel.links["4"].rtt_ms <= 60
    # Un lien hors du bond n'a pas de latence : il n'en est pas dépourvu, il n'y
    # a simplement pas de mesure à faire.
    assert tunnel.links["8"].rtt_ms is None


def test_a_tunnel_with_no_active_link_is_not_connected():
    payload = {"response": {"tunnel": {"1-1": {"wan": {
        "4": {"id": 4, "state": "WAN_DOWN"},
    }}}}}
    assert parse_tunnel(payload, "1-1").connected is False


def test_unknown_peer_yields_an_empty_tunnel(capture):
    t = parse_tunnel(capture["status.pepvpn.tunnel"], "jamais-vu")
    assert t.links == {} and t.connected is False


# --- compteurs ----------------------------------------------------------------

def test_counters_are_kept_per_direction(capture):
    """Les deux sens sont relevés séparément, jamais additionnés."""
    counters = tunnel_counters(capture["status.pepvpn.tunnel"], "1-1")
    montant, descendant = counters["6"]
    assert montant is not None and descendant is not None
    assert montant[0] > 0 and descendant[0] > 0


def test_counters_accept_scalars_and_lists():
    """Le routeur renvoie des listes au niveau WAN, des scalaires sous `remote`."""
    payload = {"response": {"tunnel": {"p": {"wan": {
        "4": {"receive": {"packet": {"wan": [10, 20], "loss": [1, 2], "outOfOrder": [0, 0]}},
              "transmit": {"packet": {"wan": 5, "loss": 1, "outOfOrder": 3}}},
    }}}}}
    # (montant, descendant). Les deux sens restent séparés : une moyenne
    # laisserait le plus chargé masquer la dégradation de l'autre.
    montant, descendant = tunnel_counters(payload, "p")["4"]
    assert montant == _Counters(packets=5.0, loss=1.0, out_of_order=3.0)
    assert descendant == _Counters(packets=30.0, loss=3.0, out_of_order=0.0)


def test_a_link_without_counters_is_absent_rather_than_zero(capture):
    """Sur la capture réelle, un lien hors du bond n'a AUCUN bloc de compteurs.

    Le mémoriser à (0, 0, 0) revenait à annoncer, au retour du lien, la perte
    cumulée depuis l'établissement du tunnel comme celle des trois dernières
    secondes.
    """
    wans = capture["status.pepvpn.tunnel"]["response"]["tunnel"]["1-1"]["wan"]
    assert set(wans["8"]) == {"id", "name", "remote", "state"}
    assert "8" not in tunnel_counters(capture["status.pepvpn.tunnel"], "1-1")


# --- taux, calculés par différence ---------------------------------------------
#
# Les compteurs du routeur sont cumulés depuis l'établissement du tunnel : ils
# donnent une moyenne sur plusieurs heures, sans rapport avec l'instant présent.

def _no_network(request: httpx.Request) -> httpx.Response:
    """Ces calculs sont purement arithmétiques : aucune requête n'est attendue."""
    raise AssertionError(f"requête inattendue vers {request.url}")


async def _client(handler=_no_network) -> RouterApiPeplinkClient:
    """Client branché sur un faux routeur (aucun accès réseau).

    Le client HTTP ouvert par le constructeur est fermé avant d'être remplacé :
    l'écraser sans le fermer abandonnait un pool de connexions par test.
    """
    client = RouterApiPeplinkClient(
        RouterConfig(id="r", name="R", host="192.0.2.1", username="a", password="b")
    )
    await client._http.aclose()
    client._http = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    return client


async def test_first_reading_produces_no_rate():
    """Sans référence, mieux vaut ne rien dire qu'annoncer une moyenne de six heures."""
    client = await _client()
    rates = client._tunnel_rates({"4": ((1000.0, 10.0, 2.0), (900.0, 5.0, 1.0))}, "1-1")
    assert rates["4"] == _RIEN_MESURÉ
    await client.aclose()


async def test_rate_is_computed_between_two_readings():
    client = await _client()
    client._tunnel_rates({"4": ((1000.0, 10.0, 0.0), (2000.0, 20.0, 0.0))}, "1-1")
    rates = client._tunnel_rates({"4": ((1100.0, 12.0, 1.0), (2200.0, 20.0, 0.0))}, "1-1")
    # Sens montant : 2 pertes sur 102 paquets transportés dans l'intervalle.
    assert rates["4"].loss_up == pytest.approx(1.96, abs=0.01)
    assert rates["4"].out_of_order_up == pytest.approx(0.98, abs=0.01)
    # Sens descendant : rien de perdu sur les 200 paquets de l'intervalle.
    assert rates["4"].loss_down == 0.0
    await client.aclose()


async def test_upstream_loss_is_not_diluted_by_downstream_volume():
    """C'est le sens montant qui porte l'image depuis le véhicule.

    Relevé de remontée vidéo : 5 000 paquets perdus sur 100 000 émis, contre
    900 000 reçus sans perte. La somme des deux sens publiait 0,50 % ; la perte
    réelle du sens utile est de 4,76 %, et c'est elle qui fait geler l'image.
    """
    client = await _client()
    client._tunnel_rates({"4": ((0.0, 0.0, 0.0), (0.0, 0.0, 0.0))}, "1-1")
    rates = client._tunnel_rates(
        {"4": ((100000.0, 5000.0, 0.0), (900000.0, 0.0, 0.0))}, "1-1"
    )
    assert rates["4"].loss_up == pytest.approx(4.76, abs=0.01)
    assert rates["4"].loss_down == 0.0
    await client.aclose()


async def test_a_counter_going_backwards_means_the_tunnel_was_rebuilt():
    """Repartir de zéro plutôt que de produire un taux négatif."""
    client = await _client()
    client._tunnel_rates({"4": ((5000.0, 50.0, 0.0), (5000.0, 50.0, 0.0))}, "1-1")
    rates = client._tunnel_rates({"4": ((100.0, 1.0, 0.0), (100.0, 1.0, 0.0))}, "1-1")
    assert rates["4"] == _RIEN_MESURÉ
    await client.aclose()


async def test_no_traffic_in_the_interval_yields_no_rate():
    """Zéro paquet transporté : un taux de perte n'aurait aucun sens.

    Le dénominateur, lui, vaut bien ZÉRO et non None : « rien n'est passé » est
    une mesure, et l'interface peut l'écrire. C'est l'absence de référence — un
    premier relevé, un tunnel remonté — qui laisse le dénominateur inconnu.
    """
    client = await _client()
    figes = _Counters(packets=1000.0, loss=10.0, out_of_order=0.0)
    counters = {"4": (figes, figes)}
    client._tunnel_rates(counters, "1-1")
    # Cinq secondes ÉCOULÉES entre les deux lectures, posées et non subies :
    # sur la machine Windows de GitHub, deux appels consécutifs tombent dans le
    # même tic d'horloge (≈ 15 ms de résolution), le temps écoulé vaut zéro,
    # et un débit sur zéro seconde est — à juste titre — inconnu, pas nul.
    client._tunnel_counters_at -= 5.0
    mesure = client._tunnel_rates(counters, "1-1")["4"]
    # Tout est mesuré à zéro, et rien n'est inconnu : ni paquet, ni octet, ni
    # paquet de redondance n'a bougé sur l'intervalle. Aucun taux en revanche,
    # faute de dénominateur — c'est la distinction que porte ce test.
    assert mesure == _LinkRates(
        packets_up=0, packets_down=0,
        tx_kbps=0.0, rx_kbps=0.0,
        repair_sent=0, repair_used=0,
    )
    assert mesure.loss_up is None and mesure.loss_down is None
    await client.aclose()


async def test_a_three_packet_interval_carries_the_denominator_that_disqualifies_it():
    """Un taux calculé sur trois paquets n'est pas une mesure — et le taux seul le cache.

    C'est l'état d'un tunnel au repos : entre deux relevés espacés de 5,3 s, seul
    le keepalive passe. L'écran en tirait « 0,00 % de perte », affirmation dont le
    dénominateur est de TROIS paquets de service. Le matériel le confirme : sur
    11 937 relevés, la seule valeur de perte non nulle jamais enregistrée vaut
    33,33 %, c'est-à-dire un paquet sur trois.

    Le seuil des 200 paquets n'est PAS appliqué ici : c'est une règle de RENDU.
    La graver dans le backend l'écrirait aussi dans l'historique en base, où elle
    survivrait à tout changement d'affichage. Le backend publie donc le taux brut
    ET son dénominateur ; c'est le dénominateur qui interdit d'afficher le taux.
    """
    client = await _client()
    client._tunnel_rates({"4": ((1000.0, 10.0, 0.0), (1000.0, 10.0, 0.0))}, "1-1")
    # Deux paquets arrivés, un perdu : le keepalive et rien d'autre.
    rates = client._tunnel_rates({"4": ((1002.0, 11.0, 0.0), (1000.0, 10.0, 0.0))}, "1-1")

    assert rates["4"].loss_up == pytest.approx(33.33, abs=0.01)
    assert rates["4"].packets_up == 3          # le taux ci-dessus ne pèse que ça
    # Le sens descendant n'a rien vu passer : zéro paquet, donc aucun taux.
    assert rates["4"].loss_down is None
    assert rates["4"].packets_down == 0
    await client.aclose()


async def test_a_link_back_in_the_bond_is_a_first_reading_again():
    """Un lien sans compteurs sort de la référence, il n'est pas remis à zéro.

    Sinon, un lien à 1 % de perte cumulée qui quitte le bond un cycle publiait
    0,99 % à son retour, pour 0,03 % réellement perdus sur l'intervalle — un
    facteur 30 à chaque micro-coupure.
    """
    client = await _client()
    client._tunnel_rates({"4": ((100000.0, 1000.0, 0.0), (0.0, 0.0, 0.0))}, "1-1")
    client._tunnel_rates({}, "1-1")          # le lien n'a plus de compteurs
    rates = client._tunnel_rates({"4": ((100300.0, 1010.0, 0.0), (0.0, 0.0, 0.0))}, "1-1")
    assert rates["4"] == _RIEN_MESURÉ
    await client.aclose()


async def test_counters_of_another_peer_are_never_subtracted():
    """Les compteurs sont cumulés PAR TUNNEL, pas par WAN.

    Avec deux profils montés, le « premier pair connecté » peut désigner un autre
    concentrateur d'un cycle à l'autre. La soustraction croisée passait le test de
    monotonie et publiait un taux fantaisiste, écrit tel quel en base.
    """
    client = await _client()
    client._tunnel_rates({"4": ((1000.0, 0.0, 0.0), (1000.0, 0.0, 0.0))}, "1-1")
    bascule = client._tunnel_rates({"4": ((9000.0, 900.0, 0.0), (9000.0, 900.0, 0.0))}, "2-1")
    assert bascule["4"] == _RIEN_MESURÉ
    # Le nouveau pair devient la référence, sans mélange avec l'ancien.
    suite = client._tunnel_rates({"4": ((9100.0, 900.0, 0.0), (9000.0, 900.0, 0.0))}, "2-1")
    assert suite["4"].loss_up == 0.0
    await client.aclose()


# --- intégration au relevé -----------------------------------------------------

async def test_tunnel_is_attached_to_the_matching_wan(capture):
    def handler(request: httpx.Request) -> httpx.Response:
        path, params = request.url.path, dict(request.url.params)
        if path == "/api/login":
            return httpx.Response(200, json={"stat": "ok"})
        if path == "/api/status.wan.connection":
            return httpx.Response(200, json=capture["status.wan.connection"])
        if path == "/api/status.traffic":
            return httpx.Response(200, json=capture["status.traffic"])
        if path == "/api/status.pepvpn":
            if params.get("infoType") == "peer":
                return httpx.Response(200, json=capture["status.pepvpn.peer"])
            return httpx.Response(200, json=capture["status.pepvpn.tunnel"])
        return httpx.Response(200, json={"stat": "ok", "response": {}})

    client = await _client(handler)
    statuses = await client.get_radio_status()

    par_wan = {s.wan_id: s for s in statuses}
    assert par_wan["4"].tunnel is not None
    assert par_wan["4"].tunnel.in_bond is True
    assert par_wan["4"].tunnel.rtt_ms is not None
    await client.aclose()


async def test_the_bond_age_is_forgotten_when_the_tunnel_falls(capture):
    """C-7 : « Monté depuis 2 h 03 » restait affiché sous « Tunnel tombé ».

    `tunnel_uptime_s` n'était écrit que sur une lecture de tunnel réussie, et
    jamais remis à None : ni la sentinelle PEER_DOWN, ni une erreur ne
    l'effaçaient, et le chiffre restait figé sous un bond à terre.
    """
    import copy

    tombe = False

    def handler(request: httpx.Request) -> httpx.Response:
        path, params = request.url.path, dict(request.url.params)
        if path == "/api/login":
            return httpx.Response(200, json={"stat": "ok"})
        if path == "/api/status.wan.connection":
            return httpx.Response(200, json=capture["status.wan.connection"])
        if path == "/api/status.pepvpn":
            if params.get("infoType") == "peer":
                pairs = copy.deepcopy(capture["status.pepvpn.peer"])
                if tombe:
                    for pair in pairs["response"]["peer"]:
                        pair["status"] = "START"
                return httpx.Response(200, json=pairs)
            if params.get("infoType") == "tunnel":
                return httpx.Response(200, json=capture["status.pepvpn.tunnel"])
        return httpx.Response(200, json={"stat": "ok", "response": {}})

    client = await _client(handler)
    await client.get_radio_status()
    assert client.tunnel_uptime_s is not None, "le bond debout doit dater son ancienneté"

    tombe = True
    statuses = await client.get_radio_status()
    assert any(s.tunnel is not None and s.tunnel.state == "PEER_DOWN" for s in statuses)
    assert client.tunnel_uptime_s is None
    await client.aclose()


async def test_the_worst_direction_is_the_one_published():
    """`loss_percent` doit valoir le pire des deux sens, pas leur mélange."""
    calls = {"tunnel": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        path, params = request.url.path, dict(request.url.params)
        if path == "/api/login":
            return httpx.Response(200, json={"stat": "ok"})
        if path == "/api/status.wan.connection":
            return httpx.Response(200, json={"stat": "ok", "response": {
                "4": {"type": "cellular", "statusLed": "green", "name": "SIM",
                      "cellular": {"network": "LTE"}}}})
        if path == "/api/status.pepvpn":
            if params.get("infoType") == "peer":
                return httpx.Response(200, json={"stat": "ok", "response": {"peer": [
                    {"peerId": "1-1", "name": "CONCENTRATEUR", "status": "CONNECTED"},
                ]}})
            if params.get("infoType") == "profile":
                return httpx.Response(200, json={"stat": "ok", "response": {}})
            # Deux relevés successifs : le second ajoute 5 000 pertes montantes
            # sur 100 000 paquets émis, et 900 000 paquets reçus sans perte.
            emis = 100000 if calls["tunnel"] else 0
            recus = 900000 if calls["tunnel"] else 0
            perdus = 5000 if calls["tunnel"] else 0
            calls["tunnel"] += 1
            return httpx.Response(200, json={"response": {"tunnel": {"1-1": {"wan": {
                "4": {"id": 4, "state": "ACTIVE", "rtt": 30,
                      "transmit": {"packet": {"wan": [emis], "loss": [perdus],
                                              "outOfOrder": [0]}},
                      "receive": {"packet": {"wan": [recus], "loss": [0],
                                             "outOfOrder": [0]}}},
            }}}}})
        return httpx.Response(200, json={"stat": "ok", "response": {}})

    client = await _client(handler)
    await client.get_radio_status()
    tunnel = (await client.get_radio_status())[0].tunnel

    assert tunnel.loss_percent_up == pytest.approx(4.76, abs=0.01)
    assert tunnel.loss_percent_down == 0.0
    assert tunnel.loss_percent == tunnel.loss_percent_up
    # Le dénominateur arrive jusqu'au lien publié : ces 4,76 % pèsent 105 000
    # paquets et non trois, et l'interface peut le vérifier sans rien supposer.
    assert tunnel.packets_up == 105000
    assert tunnel.packets_down == 900000
    await client.aclose()


async def test_an_unsupported_tunnel_is_asked_only_once():
    calls = {"pepvpn": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/login":
            return httpx.Response(200, json={"stat": "ok"})
        if request.url.path == "/api/status.pepvpn":
            calls["pepvpn"] += 1
            return httpx.Response(200, json={"stat": "fail", "code": 999,
                                             "message": "unknown function"})
        return httpx.Response(200, json={"stat": "ok", "response": {}})

    client = await _client(handler)
    for _ in range(3):
        await client.get_radio_status()
    assert calls["pepvpn"] == 1
    assert client._supports_tunnel is False
    await client.aclose()


async def test_a_bond_entirely_down_is_visible_on_every_link():
    """Des pairs existent, aucun n'est joignable : le bond est TOMBÉ.

    Sans objet tunnel sur les statuts, l'interface n'avait rien à compter comme
    hors du bond et affichait « Liaison OK » en vert, concentrateur injoignable —
    alors qu'UN SEUL lien écarté la fait passer en « Liaison dégradée ».
    """
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/login":
            return httpx.Response(200, json={"stat": "ok"})
        if request.url.path == "/api/status.pepvpn":
            return httpx.Response(200, json={"stat": "ok", "response": {"peer": [
                {"peerId": "1-1", "status": "DISCONNECTED"},
            ]}})
        if request.url.path == "/api/status.wan.connection":
            return httpx.Response(200, json={"stat": "ok", "response": {
                "4": {"type": "cellular", "statusLed": "green", "name": "SIM",
                      "cellular": {"network": "LTE"}}}})
        return httpx.Response(200, json={"stat": "ok", "response": {}})

    client = await _client(handler)
    statuses = await client.get_radio_status()
    assert statuses[0].tunnel is not None
    assert statuses[0].tunnel.state == "PEER_DOWN"
    assert statuses[0].tunnel.in_bond is False
    assert client._supports_tunnel is True     # l'endpoint répond, il n'est pas condamné
    await client.aclose()


async def test_without_any_profile_the_tunnel_stays_unknown():
    """Aucun profil SpeedFusion : rien à joindre, donc rien à signaler.

    Même chemin que le bond tombé, mais conclusion opposée : `tunnel` reste à
    None, que l'interface lit comme INCONNU. Le vert est ici légitime.
    """
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/login":
            return httpx.Response(200, json={"stat": "ok"})
        if request.url.path == "/api/status.pepvpn":
            return httpx.Response(200, json={"stat": "ok", "response": {"peer": []}})
        if request.url.path == "/api/status.wan.connection":
            return httpx.Response(200, json={"stat": "ok", "response": {
                "4": {"type": "cellular", "statusLed": "green", "name": "SIM",
                      "cellular": {"network": "LTE"}}}})
        return httpx.Response(200, json={"stat": "ok", "response": {}})

    client = await _client(handler)
    statuses = await client.get_radio_status()
    assert statuses[0].tunnel is None
    await client.aclose()


async def test_a_device_without_any_peer_is_probed_sparingly():
    """Sonde espacée là où aucun pair n'a jamais été vu, mais jamais condamnée.

    Une liste de pairs vide répond `stat: ok` : l'ancien code redemandait donc
    l'endpoint à chaque cycle, soit — à la période mesurée de 5,3 s — 11 requêtes
    par minute et plus de 16 000 par jour et par équipement, pour rien. La
    condamner serait pire : un profil peut être configuré après le démarrage de
    la supervision.
    """
    calls = {"pepvpn": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/login":
            return httpx.Response(200, json={"stat": "ok"})
        if request.url.path == "/api/status.pepvpn":
            calls["pepvpn"] += 1
            return httpx.Response(200, json={"stat": "ok", "response": {"peer": []}})
        return httpx.Response(200, json={"stat": "ok", "response": {}})

    client = await _client(handler)
    for _ in range(5):
        await client.get_radio_status()
    assert calls["pepvpn"] == 1
    assert client._supports_tunnel is True
    await client.aclose()


async def test_a_rights_refusal_keeps_the_wan_reading():
    """403 sur status.pepvpn : ce sont les DROITS, pas les identifiants.

    L'endpoint est optionnel et hors du périmètre du client API. Le laisser
    remonter jetait des états WAN déjà lus, et `_get` réauthentifiait avant
    chaque relevé — un POST /api/login par cycle, sur un firmware qui suspend
    l'accès cinq minutes au bout de cinq échecs.
    """
    calls = {"login": 0, "pepvpn": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/login":
            calls["login"] += 1
            return httpx.Response(200, json={"stat": "ok"})
        if request.url.path == "/api/status.pepvpn":
            calls["pepvpn"] += 1
            return httpx.Response(200, json={"stat": "fail", "code": 403,
                                             "message": "Forbidden"})
        if request.url.path == "/api/status.wan.connection":
            return httpx.Response(200, json={"stat": "ok", "response": {
                "4": {"type": "cellular", "statusLed": "green", "name": "SIM",
                      "cellular": {"network": "LTE"}}}})
        return httpx.Response(200, json={"stat": "ok", "response": {}})

    client = await _client(handler)
    for _ in range(3):
        statuses = await client.get_radio_status()

    assert [s.wan_id for s in statuses] == ["4"]     # le relevé WAN survit
    assert statuses[0].tunnel is None
    assert client._supports_tunnel is False
    # Une seule tentative, rejouée une fois après ré-authentification, puis plus rien.
    assert calls["pepvpn"] == 2
    assert calls["login"] == 2
    await client.aclose()


async def test_refused_credentials_still_stop_everything():
    """Identifiants refusés au login : plus rien n'est lisible — il faut le dire.

    Le refus vient du LOGIN, pas de l'endpoint : un 401 qui survivrait à un
    login réussi serait un refus de droits, et condamnerait le seul tunnel.
    """
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/login":
            return httpx.Response(200, json={"stat": "fail", "code": 401,
                                             "message": "Unauthorized"})
        return httpx.Response(200, json={"stat": "ok", "response": {}})

    client = await _client(handler)
    with pytest.raises(CredentialsRefusedError):
        await client.get_radio_status()
    assert client._supports_tunnel is True
    await client.aclose()


async def test_a_tunnel_out_of_scope_is_condemned_like_a_403():
    """401 sur `status.pepvpn` après un login réussi : des droits, pas la session.

    Ce cas ré-authentifiait à chaque cycle — un POST /api/login par relevé, sur
    un firmware qui suspend l'accès au bout de cinq échecs. Il est traité comme
    le 403 : le tunnel est hors du périmètre, les WAN restent lus.
    """
    logins = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal logins
        if request.url.path == "/api/login":
            logins += 1
            return httpx.Response(200, json={"stat": "ok"})
        if request.url.path == "/api/status.pepvpn":
            return httpx.Response(200, json={"stat": "fail", "code": 401,
                                             "message": "Unauthorized"})
        return httpx.Response(200, json={"stat": "ok", "response": {}})

    client = await _client(handler)
    await client.get_radio_status()
    await client.get_radio_status()
    assert client._supports_tunnel is False
    assert logins == 2
    await client.aclose()


async def test_a_transport_failure_never_condemns_the_tunnel():
    """Le drapeau n'est jamais réarmé : une panne passagère ne doit pas le poser.

    Une réponse illisible suffisait à priver la supervision de latence, de perte
    et de désordre jusqu'au redémarrage du processus.
    """
    calls = {"pepvpn": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/login":
            return httpx.Response(200, json={"stat": "ok"})
        if request.url.path == "/api/status.pepvpn":
            calls["pepvpn"] += 1
            return httpx.Response(200, content=b"<html>portail captif</html>")
        return httpx.Response(200, json={"stat": "ok", "response": {}})

    client = await _client(handler)
    for _ in range(2):
        await client.get_radio_status()
    assert calls["pepvpn"] == 2
    assert client._supports_tunnel is True
    await client.aclose()


async def test_a_failure_on_the_second_call_never_condemns_the_endpoint():
    """Le détail du tunnel dépend d'un pair qui peut disparaître entre deux requêtes.

    L'échec de ce second appel ne dit rien de la présence de l'endpoint.
    """
    calls = {"detail": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/login":
            return httpx.Response(200, json={"stat": "ok"})
        if request.url.path == "/api/status.pepvpn":
            if dict(request.url.params).get("infoType") == "peer":
                return httpx.Response(200, json={"stat": "ok", "response": {"peer": [
                    {"peerId": "1-1", "status": "CONNECTED"},
                ]}})
            if dict(request.url.params).get("infoType") == "profile":
                return httpx.Response(200, json={"stat": "ok", "response": {}})
            calls["detail"] += 1
            return httpx.Response(200, json={"stat": "fail", "code": 999,
                                             "message": "unknown function"})
        return httpx.Response(200, json={"stat": "ok", "response": {}})

    client = await _client(handler)
    for _ in range(2):
        await client.get_radio_status()
    assert calls["detail"] == 2
    assert client._supports_tunnel is True
    await client.aclose()


async def test_the_bond_throughput_comes_from_bytes_already_downloaded():
    """Le débit PORTÉ PAR LE BOND, distinct de celui de l'interface WAN.

    `status.traffic` compte tout ce qui passe sur l'interface — tunnel et hors
    tunnel confondus. Les octets du tunnel, eux, ne comptent que l'agrégation, et
    ils arrivent dans une charge utile déjà téléchargée à chaque cycle : le
    parseur les jetait.
    """
    client = await _client()
    debut = _Counters(packets=100.0, loss=0.0, out_of_order=0.0, byte=0.0, repair=0.0)
    # 1 000 000 octets de plus, soit 8 Mbit, sur l'intervalle mesuré.
    fin = _Counters(packets=900.0, loss=0.0, out_of_order=0.0, byte=1_000_000.0, repair=12.0)

    client._tunnel_rates({"4": (debut, debut)}, "1-1")
    client._tunnel_counters_at -= 4.0          # quatre secondes se sont écoulées
    mesure = client._tunnel_rates({"4": (fin, fin)}, "1-1")["4"]

    # 1 000 000 o x 8 / 1000 / 4 s = 2 000 kbit/s
    assert mesure.tx_kbps == pytest.approx(2000.0, abs=1.0)
    assert mesure.rx_kbps == pytest.approx(2000.0, abs=1.0)
    assert mesure.repair_sent == 12 and mesure.repair_used == 12
    await client.aclose()


async def test_no_throughput_without_an_interval_to_measure_it_on():
    """Première lecture : aucune référence, donc aucun débit — pas un zéro."""
    client = await _client()
    c = _Counters(packets=10.0, byte=5000.0)
    mesure = client._tunnel_rates({"4": (c, c)}, "1-1")["4"]
    assert mesure.tx_kbps is None and mesure.rx_kbps is None
    await client.aclose()


# --- une absence n'est pas un intervalle --------------------------------------

async def test_counters_are_dropped_after_a_long_blind_spot():
    """Treize minutes sans lire les compteurs ne font pas une mesure instantanée.

    `_attach_tunnel` n'est pas appelée quand l'équipement est injoignable, mais
    `_tunnel_counters` et son horodatage survivent : au retour, la différence
    couvrait tout le trou. Sur le plus long incident de la base — treize minutes
    d'absence — 0,99 % de perte ACCUMULÉE ressortait comme la perte de l'instant
    du retour, avec 787 800 paquets au dénominateur : la règle des 200 paquets
    était franchie haut la main, le taux était affiché, et il valait trente fois
    la valeur réelle. C'est chiffre pour chiffre le défaut que le calcul par
    différence devait supprimer.
    """
    client = await _client()
    client._tunnel_rates({"4": ((100_000.0, 0.0, 0.0), (0.0, 0.0, 0.0))}, "1-1")

    # Le superviseur n'a rien lu pendant treize minutes.
    client._tunnel_counters_at -= 780.0
    rates = client._tunnel_rates({"4": ((887_800.0, 7_800.0, 0.0), (0.0, 0.0, 0.0))}, "1-1")["4"]

    assert rates.loss_up is None, "une perte accumulée a été republiée comme instantanée"
    assert rates.packets_up is None, "le dénominateur d'une absence n'est pas un dénominateur"
    assert rates.tx_kbps is None

    # Et le cycle suivant repart normalement : la référence a bien été reprise.
    suivant = client._tunnel_rates({"4": ((888_000.0, 7_800.0, 0.0), (0.0, 0.0, 0.0))}, "1-1")["4"]
    assert suivant.packets_up == 200
    assert suivant.loss_up == 0.0
    await client.aclose()


async def test_a_normal_gap_still_produces_a_rate():
    """Un cycle un peu long reste une mesure : seule une ABSENCE est écartée."""
    client = await _client()
    client._tunnel_rates({"4": ((1_000.0, 0.0, 0.0), (0.0, 0.0, 0.0))}, "1-1")
    client._tunnel_counters_at -= 20.0          # un budget d'équipement entier
    rates = client._tunnel_rates({"4": ((1_400.0, 0.0, 0.0), (0.0, 0.0, 0.0))}, "1-1")["4"]
    assert rates.packets_up == 400
    await client.aclose()


# --- le seuil de latence du bond, appliqué -----------------------------------

def _lien(wan_id: str, rtt: float | None, actif: bool = True) -> WanRadioStatus:
    from app.models import TunnelLink
    return WanRadioStatus(
        wan_id=wan_id, wan_name=f"SIM {wan_id}", kind="cellular", connected=True,
        tunnel=TunnelLink(state="ACTIVE" if actif else "WAN_DOWN", rtt_ms=rtt),
    )


async def test_a_link_beyond_the_cutoff_is_named_without_touching_in_bond():
    """Le routeur arrête le trafic sur un lien trop lent, et le dit ACTIVE.

    Vérifié sur la base de ce déploiement : sur les 113 relevés qui franchissent
    « le plus bas du cycle + 500 ms », l'état vaut `ACTIVE` cent fois sur cent —
    et les intervalles sans un seul paquet montant y sont quatre fois et demie
    plus fréquents, le débit porté n'y dépassant jamais zéro.

    `in_bond` n'est PAS corrigé pour autant : il porte ce que l'équipement
    déclare, et le remplacer par une déduction ferait dire à l'écran une chose
    que le routeur ne dit pas.
    """
    client = await _client()
    client._latency_cutoff = 500.0
    liens = [_lien("1", 6.0), _lien("4", 30.0), _lien("6", 2249.0)]
    client._apply_latency_cutoff(liens)

    lent = liens[2].tunnel
    assert lent is not None
    assert lent.beyond_cutoff is True
    assert lent.cutoff_ms == 500.0
    assert lent.in_bond is True, "l'état déclaré par le routeur ne doit pas être réécrit"
    assert all(not l.tunnel.beyond_cutoff for l in liens[:2])
    await client.aclose()


async def test_a_single_link_in_the_bond_has_nobody_to_exceed():
    """Le seuil est un ÉCART, pas une valeur absolue.

    Un lien seul dans le bond est par construction le plus rapide : le déclarer
    au-delà du seuil n'aurait aucun sens, quelle que soit sa latence.
    """
    client = await _client()
    client._latency_cutoff = 500.0
    liens = [_lien("4", 4000.0)]
    client._apply_latency_cutoff(liens)
    assert liens[0].tunnel.beyond_cutoff is False
    await client.aclose()


async def test_without_a_published_cutoff_nothing_is_claimed():
    """Sans seuil publié, on n'en invente pas un.

    Tous les profils n'en configurent pas, et supposer une valeur ferait accuser
    des liens sur une règle que le routeur n'applique pas.
    """
    client = await _client()
    client._latency_cutoff = None
    liens = [_lien("1", 6.0), _lien("6", 5000.0)]
    client._apply_latency_cutoff(liens)
    assert all(l.tunnel.cutoff_ms is None and not l.tunnel.beyond_cutoff for l in liens)
    await client.aclose()


async def test_a_link_out_of_the_bond_does_not_lower_the_reference():
    """La référence est le plus rapide DANS LE BOND.

    Un lien écarté du bond ne transporte rien : lui laisser fixer le minimum
    ferait juger les autres contre une latence qui ne porte aucun trafic.
    """
    client = await _client()
    client._latency_cutoff = 500.0
    liens = [_lien("1", 2.0, actif=False), _lien("4", 600.0), _lien("6", 700.0)]
    client._apply_latency_cutoff(liens)
    # Le plus bas DANS le bond vaut 600 : 700 n'en est qu'à 100 ms.
    assert all(not l.tunnel.beyond_cutoff for l in liens)
    await client.aclose()


def test_the_cutoff_kept_is_the_smallest_of_the_connected_peers():
    """`min → max` et `> → >=` laissaient 514 tests verts : les règles
    « mesurées » n'avaient pas de test. Le seuil retenu est le plus PETIT des
    pairs connectés — celui qui s'applique en premier — et un pair tombé ne
    compte pas."""
    charge = {"stat": "ok", "response": {"peer": [
        {"peerId": "1-1", "status": "CONNECTED", "latencyDiffCutoff": 500},
        {"peerId": "2-1", "status": "CONNECTED", "latencyDiffCutoff": 300},
        {"peerId": "3-1", "status": "START", "latencyDiffCutoff": 100},
    ]}}
    assert parse_latency_cutoff(charge) == 300.0
    assert parse_latency_cutoff({"stat": "ok", "response": {"peer": []}}) is None


async def test_the_cutoff_is_an_excess_strictly_beyond_the_threshold():
    """Le firmware arrête le trafic pour les liens qui DÉPASSENT le seuil :
    un écart exactement égal ne l'atteint pas."""
    client = await _client()
    client._latency_cutoff = 500.0
    liens = [_lien("4", 20.0), _lien("5", 520.0), _lien("6", 520.1)]
    client._apply_latency_cutoff(liens)
    assert [l.tunnel.beyond_cutoff for l in liens] == [False, False, True]
    await client.aclose()


async def test_a_disconnected_link_does_not_lower_the_reference_either():
    """`in_bond` sans `connected` : le relevé d'adieu d'un lien tombé garde un
    état de tunnel ACTIVE et une latence — et fixait le plus bas temps de
    transit, écartant les autres d'un seuil mesuré sur un lien qui ne porte
    rien. Porter, c'est être debout ET dans le bond, comme partout ailleurs.
    """
    client = await _client()
    client._latency_cutoff = 500.0
    tombe = _lien("1", 2.0)
    tombe.connected = False
    liens = [tombe, _lien("4", 600.0), _lien("6", 700.0)]
    client._apply_latency_cutoff(liens)
    assert all(not l.tunnel.beyond_cutoff for l in liens)
    await client.aclose()


# --- les routes du bond survivent à la chute du pair --------------------------

def _pair(status: str, routes: list[str]) -> dict:
    return {"stat": "ok", "response": {"peer": [
        {"status": status, "peerId": "4-0", "name": "HUB", "route": routes},
    ]}}


async def test_a_fallen_peer_keeps_the_last_declared_routes():
    """Un pair TOMBÉ publie `route: []` (fixture HD2, `status: START`).

    Recopiée telle quelle, la liste vide faisait dire à l'écran — à l'instant
    précis où le concentrateur tombe — que le rôle du bond est inconnu et que
    « l'agrégation ne protège plus » un trafic qui, sur des routes dédiées,
    n'a jamais emprunté le tunnel. Ce que le pair achemine ne change pas parce
    qu'il est injoignable.
    """
    client = await _client()
    assert client._retenir_routes(_pair("CONNECTED", ["10.0.0.0/8"])) == ["10.0.0.0/8"]
    client.bond_routes = ["10.0.0.0/8"]
    # Le pair tombe : il est encore déclaré, ses routes tiennent.
    assert client._retenir_routes(_pair("START", [])) == ["10.0.0.0/8"]
    # Il revient avec d'autres routes : ce sont elles qui comptent.
    assert client._retenir_routes(_pair("CONNECTED", ["0.0.0.0/0"])) == ["0.0.0.0/0"]
    client.bond_routes = ["0.0.0.0/0"]
    # Plus aucun pair — profil retiré : la mémoire s'efface avec lui.
    assert client._retenir_routes({"stat": "ok", "response": {"peer": []}}) == []
    await client.aclose()


def test_la_portee_de_la_cause_est_classee_par_le_modele():
    """« pair » pour la sentinelle du client, « lien » pour un mot du routeur,
    rien quand le lien porte ou que son état est inconnu.

    Trois consommateurs recollaient un fait de niveau bond à partir de N
    états de lien, chacun à sa manière. Le modèle le classe une fois.
    """
    from app.models import PEER_DOWN, TunnelLink
    assert TunnelLink(state=PEER_DOWN).cause_scope == "pair"
    assert TunnelLink(state="WAN_DOWN").cause_scope == "lien"
    assert TunnelLink(state="COLD").cause_scope == "lien"
    assert TunnelLink(state="ACTIVE").cause_scope is None
    assert TunnelLink(state=None).cause_scope is None
    # Un état du routeur qui commencerait par PEER_ n'est PAS un fait de bond.
    assert TunnelLink(state="PEER_LIMIT").cause_scope == "lien"
