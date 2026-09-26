"""Découverte d'équipements Peplink sur le réseau local.

Règle absolue de ce module : **aucun identifiant n'est jamais envoyé**. Cinq
tentatives de connexion ratées suspendent l'accès admin pendant 5 minutes, alors
que les refus provoqués par un simple sondage ne comptent pas — vérifié sur
matériel : douze refus consécutifs, puis connexion admin réussie.
"""

from unittest.mock import patch

import httpx
import pytest

from app.discovery import network
from app.discovery.probe import _der_string_at, discover, probe

# --- lecture de l'environnement réseau ----------------------------------------

@pytest.mark.parametrize(
    "mac,expected",
    [
        ("a8:c0:ea:00:00:12", True),
        ("A8:C0:EA:00:00:01", True),
        ("00:1a:dd:11:22:33", True),
        ("d4:13:f8:00:00:01", True),   # Dome Pro Duo 8.5.1, constaté
        ("aa:bb:cc:dd:ee:ff", False),
        ("", False),
        (None, False),
    ],
)
def test_peplink_oui_detection(mac, expected):
    assert network.is_peplink_mac(mac) is expected


ARP_MACOS = """? (192.168.63.1) at a8:c0:ea:00:00:12 on en3 ifscope [ethernet]
? (192.168.63.5) at a8:c0:ea:0:1:2 on en3 [ethernet]
? (224.0.0.251) at 1:0:5e:0:0:fb on en3 ifscope permanent [ethernet]"""

ARP_LINUX = """gateway (192.168.1.1) at 00:1A:DD:44:55:66 [ether] on eth0
? (192.168.1.42) at aa-bb-cc-dd-ee-ff [ether] on eth0"""


@pytest.mark.parametrize("output", [ARP_MACOS, ARP_LINUX])
def test_arp_table_parsing(output):
    with patch.object(network, "_run", return_value=output):
        table = network.arp_table()
    assert all(len(mac) == 17 for mac in table.values())   # octets normalisés
    assert all(mac == mac.lower() for mac in table.values())


def test_arp_table_pads_abbreviated_octets():
    """macOS abrège les octets nuls : « a8:c0:ea:0:1:2 » vaut « …:00:01:02 »."""
    with patch.object(network, "_run", return_value=ARP_MACOS):
        table = network.arp_table()
    assert table["192.168.63.5"] == "a8:c0:ea:00:01:02"
    assert network.is_peplink_mac(table["192.168.63.5"])


NETSTAT = "Destination        Gateway            Flags\ndefault            192.168.63.1       UGScg"
IP_ROUTE = "default via 10.0.0.1 dev eth0 proto dhcp metric 100"


@pytest.mark.parametrize("output,expected", [(NETSTAT, "192.168.63.1"), (IP_ROUTE, "10.0.0.1")])
def test_gateway_parsing_per_platform(output, expected):
    with patch.object(network, "_run", side_effect=lambda cmd: output):
        assert network.default_gateways()[0] == expected


def test_missing_route_command_is_not_fatal():
    """Aucune commande de routage disponible : la découverte doit rester possible."""
    with patch.object(network, "_run", return_value=""):
        assert network.default_gateways() == []


def test_candidates_start_with_the_gateway():
    """La passerelle d'abord : sur un réseau d'équipement, le Peplink EST la passerelle."""
    with patch.object(network, "default_gateways", return_value=["192.168.63.1"]), \
         patch.object(network, "local_address", return_value="192.168.63.148"):
        hosts = network.candidate_hosts()
    assert hosts[0] == "192.168.63.1"
    assert "192.168.50.1" in hosts        # adresse d'usine Pepwave MAX
    assert len(hosts) == len(set(hosts))  # aucun doublon


def test_extra_hosts_come_first():
    with patch.object(network, "default_gateways", return_value=["192.168.63.1"]), \
         patch.object(network, "local_address", return_value=None):
        assert network.candidate_hosts(["10.9.9.9"])[0] == "10.9.9.9"


def test_invalid_addresses_are_dropped():
    with patch.object(network, "default_gateways", return_value=[]), \
         patch.object(network, "local_address", return_value=None):
        hosts = network.candidate_hosts(["pas-une-adresse", "10.0.0.1"])
    assert "pas-une-adresse" not in hosts and "10.0.0.1" in hosts


def test_candidate_list_stays_short():
    """Jamais de balayage de sous-réseau : c'est lent, bruyant et inutile."""
    with patch.object(network, "default_gateways", return_value=["192.168.63.1"]), \
         patch.object(network, "local_address", return_value="192.168.63.148"):
        assert len(network.candidate_hosts()) < 12


# --- lecture du certificat ----------------------------------------------------
#
# Python renvoie un certificat décodé VIDE quand la vérification est désactivée,
# ce qui est le cas ici : les Peplink présentent un certificat auto-signé. On lit
# donc la forme binaire et on y repère le marqueur constructeur.

def _der_printable(text: bytes) -> bytes:
    return bytes([0x13, len(text)]) + text


def test_der_string_is_read_with_its_declared_length():
    """Sans lire la longueur, l'octet de structure suivant se retrouve dans le nom."""
    name = b"captive-portal.peplink.com"
    der = b"\x30\x82" + _der_printable(name) + b"\x30\x1f"   # suivi d'une structure
    position = der.find(b"peplink")
    assert _der_string_at(der, position) == "captive-portal.peplink.com"


def test_der_string_absent_returns_none():
    assert _der_string_at(b"\x00" * 50, 10) is None


# --- sondage ------------------------------------------------------------------

async def test_probe_without_any_evidence_returns_none():
    """Une adresse quelconque ne doit pas être présentée comme un Peplink."""
    with patch("app.discovery.probe._certificate_name", return_value=None):
        assert await probe("203.0.113.7", mac="aa:bb:cc:dd:ee:ff") is None


async def test_mac_alone_is_enough_to_report():
    """Un Peplink dont l'API est coupée reste signalable, sans preuve d'API."""
    with patch("app.discovery.probe._certificate_name", return_value=None):
        device = await probe("203.0.113.7", mac="a8:c0:ea:00:00:01")
    assert device is not None
    assert device.api_reachable is False and device.firmware is None
    assert "MAC Peplink" in device.evidence[0]


async def test_discovery_survives_a_failing_host():
    """Un hôte qui lève ne doit pas interrompre le sondage des autres."""
    async def flaky(host, mac=None):
        if host == "10.0.0.1":
            raise OSError("réseau injoignable")
        return None

    with patch("app.discovery.probe.candidate_hosts", return_value=["10.0.0.1", "10.0.0.2"]), \
         patch("app.discovery.probe.arp_table", return_value={}), \
         patch("app.discovery.probe.probe", side_effect=flaky):
        assert await discover() == []


async def test_discovery_never_sends_credentials():
    """Garde-fou : le sondage ne doit émettre aucune requête d'authentification."""
    calls: list[str] = []

    class _Recorder:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc):
            return False

        async def get(self, url, **kwargs):
            calls.append(url)
            # Un port FERMÉ : c'est le seul échec qui autorise l'autre schéma.
            raise httpx.ConnectError("connection refused")

        async def post(self, url, **kwargs):  # pragma: no cover - ne doit jamais servir
            raise AssertionError(f"le sondage a tenté un POST vers {url}")

    with patch("app.discovery.probe.httpx.AsyncClient", return_value=_Recorder()), \
         patch("app.discovery.probe._certificate_name", return_value=None):
        await probe("203.0.113.7")

    # La sonde essaie HTTPS puis HTTP — un Peplink sans TLS a été rencontré.
    # Ce que ce test protège n'est pas le NOMBRE d'appels mais leur nature :
    # rien d'autre que `info.firmware`, en GET, sans le moindre identifiant.
    assert calls == [
        "https://203.0.113.7/api/info.firmware",
        "http://203.0.113.7/api/info.firmware",
    ]
    assert all(url.endswith("/api/info.firmware") for url in calls)
    assert not any("login" in url or "auth" in url for url in calls)


async def test_discovery_never_freezes_the_polling_loop():
    """Les lectures de l'environnement réseau lancent des sous-processus.

    `candidate_hosts` et `arp_table` exécutent `netstat` / `ip` / `arp` avec un
    délai de garde de cinq secondes CHACUN, et `discover` les appelait
    directement depuis sa coroutine. Au nominal c'est 12 ms — mesuré — mais un
    délai de garde de cinq secondes est la promesse qu'il peut durer cinq
    secondes, et pendant ce temps la boucle ne tourne plus du tout : la
    scrutation de toute la flotte s'arrête, les relevés vieillissent, et l'écran
    passe au gris sans qu'aucun lien n'ait bougé.

    On ne mesure pas une durée ici — ce serait un test qui dépend de la charge
    de la machine. On vérifie ce qui est vrai par construction : la boucle a
    continué à tourner PENDANT que la lecture bloquait.
    """
    import asyncio
    import time
    from unittest.mock import patch

    from app.discovery import probe as module

    battements = 0

    async def _horloge() -> None:
        nonlocal battements
        while True:
            await asyncio.sleep(0.005)
            battements += 1

    def _lente_candidate_hosts(_extra):
        time.sleep(0.2)          # le sous-processus qui pend
        return []

    def _lente_arp_table():
        time.sleep(0.2)
        return {}

    coeur = asyncio.create_task(_horloge())
    await asyncio.sleep(0.02)    # laisser l'horloge démarrer
    battements = 0
    try:
        with patch.object(module, "candidate_hosts", _lente_candidate_hosts), \
             patch.object(module, "arp_table", _lente_arp_table):
            await module.discover()
    finally:
        coeur.cancel()

    # Deux cents millisecondes de blocage, des battements toutes les cinq :
    # sur la boucle, `battements` vaudrait 0. Hors de la boucle, il monte.
    assert battements > 5, (
        f"la boucle n'a battu que {battements} fois : la découverte la gelait"
    )


async def test_un_peplink_sans_tls_est_reconnu_et_porte_son_schema():
    """Le premier équipement hors du réseau de développement ne parlait qu'HTTP.

    La sonde ne tentait qu'HTTPS : elle voyait le Peplink par son adresse MAC,
    mais rendait `api_reachable=False` et un hôte nu — que le client aurait
    ensuite préfixé en `https://`, vers un port fermé.
    """
    class _HttpSeul:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc):
            return False

        async def get(self, url, **kwargs):
            if url.startswith("https://"):
                raise httpx.ConnectError("port 443 fermé")

            class _R:
                @staticmethod
                def json():
                    return {"stat": "ok", "response": {
                        "1": {"version": "8.5.4 build 5808", "bootable": True, "inUse": True},
                        "order": [1],
                    }}
            return _R()

    with patch("app.discovery.probe.httpx.AsyncClient", return_value=_HttpSeul()), \
         patch("app.discovery.probe._certificate_name", return_value=None):
        trouve = await probe("10.10.1.1", mac="a8:c0:ea:00:00:11")

    assert trouve is not None
    assert trouve.api_reachable is True
    assert trouve.firmware == "8.5.4 build 5808"
    assert trouve.host == "http://10.10.1.1", "l'hôte doit porter son schéma"
    assert any("sans TLS" in e for e in trouve.evidence)


async def test_un_equipement_tls_n_est_jamais_retrograde_en_http():
    """Un délai dépassé n'est pas un port fermé, et un certificat vu vaut preuve.

    Une première version engageait le repli HTTP sur n'importe quel échec
    HTTPS : un routeur chargé qui dépasse le délai était rendu en `http://`,
    le formulaire d'appairage en héritait, et le mot de passe partait en clair
    à chaque cycle. Ici le certificat a été lu, l'HTTPS expire : on ne tente
    PAS l'HTTP, et l'hôte reste nu.
    """
    calls: list[str] = []

    class _Lent:
        async def __aenter__(self): return self
        async def __aexit__(self, *exc): return False
        async def get(self, url, **kwargs):
            calls.append(url)
            if url.startswith("https://"):
                raise httpx.ReadTimeout("trop lent")
            class _R:
                @staticmethod
                def json(): return {"stat": "ok", "response": {"1": {"version": "8.5.4", "inUse": True}}}
            return _R()

    with patch("app.discovery.probe.httpx.AsyncClient", return_value=_Lent()), \
         patch("app.discovery.probe._certificate_name", return_value="Peplink"):
        trouve = await probe("10.0.0.1", mac="a8:c0:ea:00:00:11")

    assert calls == ["https://10.0.0.1/api/info.firmware"], "aucune tentative HTTP"
    assert trouve is not None and trouve.host == "10.0.0.1", "l'hôte reste nu"
    assert trouve.api_reachable is False
    assert not any("sans TLS" in e for e in trouve.evidence)


async def test_un_echec_de_poignee_de_main_tls_n_ouvre_pas_le_repli_http():
    """Port 443 ouvert mais TLS refusé : ce n'est pas un port fermé.

    httpx range l'échec de poignée de main dans `ConnectError`, comme le refus
    de connexion. Le certificat n'a pas pu être lu — pour la même raison — et
    le repli tentait l'HTTP sur un équipement qui parle TLS : le formulaire
    d'appairage en héritait, le mot de passe partait en clair.
    """
    import ssl
    calls: list[str] = []

    class _TlsRefuse:
        async def __aenter__(self): return self
        async def __aexit__(self, *exc): return False
        async def get(self, url, **kwargs):
            calls.append(url)
            if url.startswith("https://"):
                try:
                    raise ssl.SSLError(1, "[SSL: TLSV1_ALERT_PROTOCOL_VERSION]")
                except ssl.SSLError as cause:
                    raise httpx.ConnectError("handshake") from cause
            class _R:
                @staticmethod
                def json(): return {"stat": "ok", "response": {"1": {"version": "8.5.4", "inUse": True}}}
            return _R()

    with patch("app.discovery.probe.httpx.AsyncClient", return_value=_TlsRefuse()), \
         patch("app.discovery.probe._certificate_name", return_value=None):
        trouve = await probe("10.0.0.1", mac="a8:c0:ea:00:00:11")

    assert calls == ["https://10.0.0.1/api/info.firmware"], "aucune tentative HTTP"
    assert trouve is not None and trouve.host == "10.0.0.1"
    assert trouve.api_reachable is False


async def test_une_reponse_https_fail_n_ouvre_pas_le_repli_http():
    """`stat: fail` en HTTPS est une API vivante qui refuse — pas un port fermé."""
    calls: list[str] = []

    class _Refuse:
        async def __aenter__(self): return self
        async def __aexit__(self, *exc): return False
        async def get(self, url, **kwargs):
            calls.append(url)
            class _R:
                @staticmethod
                def json(): return {"stat": "fail", "code": 401, "message": "Unauthorized"}
            return _R()

    with patch("app.discovery.probe.httpx.AsyncClient", return_value=_Refuse()), \
         patch("app.discovery.probe._certificate_name", return_value=None):
        await probe("10.0.0.1", mac="a8:c0:ea:00:00:11")
    assert calls == ["https://10.0.0.1/api/info.firmware"]


async def test_api_joignable_des_que_l_api_repond_ok():
    """« Joignable » : l'API a répondu `ok`, banc de firmware lisible ou non."""
    class _SansBanc:
        async def __aenter__(self): return self
        async def __aexit__(self, *exc): return False
        async def get(self, url, **kwargs):
            class _R:
                @staticmethod
                def json(): return {"stat": "ok", "response": {}}
            return _R()

    with patch("app.discovery.probe.httpx.AsyncClient", return_value=_SansBanc()), \
         patch("app.discovery.probe._certificate_name", return_value=None):
        trouve = await probe("10.0.0.1", mac="a8:c0:ea:00:00:11")
    assert trouve is not None
    assert trouve.api_reachable is True
    assert trouve.firmware is None
