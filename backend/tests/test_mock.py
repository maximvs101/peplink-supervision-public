"""Le simulateur doit produire des métriques radio plausibles et évolutives."""

import random

from app.fleet import DEFAULT_FLEET
from app.peplink.mock import MockPeplinkClient


def _client(seed: int = 42) -> MockPeplinkClient:
    return MockPeplinkClient(DEFAULT_FLEET[0], rng=random.Random(seed))


async def _cellular(client: MockPeplinkClient) -> list:
    """Un pas de simulation, restreint aux WAN cellulaires."""
    return [s for s in await client.get_radio_status() if s.kind == "cellular"]


async def test_returns_one_status_per_wan():
    router = DEFAULT_FLEET[0]
    statuses = await _client().get_radio_status()
    assert [s.wan_id for s in statuses] == [w.id for w in router.wans]
    assert [s.kind for s in statuses] == [w.kind for w in router.wans]


async def test_metrics_stay_within_plausible_bounds():
    client = _client()
    for _ in range(300):
        for status in await _cellular(client):
            assert 0 <= (status.signal_bars or 0) <= 5
            if not status.connected:
                assert status.rsrp is None
                continue
            assert -125 <= status.rsrp <= -65
            assert -20 <= status.rsrq <= -5
            assert -10 <= status.sinr <= 32
            assert status.cell_id > 0
            assert 0 <= status.pci <= 503
            # Valeurs alignées sur celles du firmware réel (cf. test_router_api).
            assert status.rat in ("LTE", "LTE-A", "5G NSA", "5G SA")


async def test_handover_changes_cell_over_time():
    client = _client(seed=7)
    seen: set[int] = set()
    for _ in range(500):
        for status in await client.get_radio_status():
            if status.wan_id == "cellular-1" and status.cell_id:
                seen.add(status.cell_id)
    # Des handovers doivent survenir : plus d'une cellule visitée.
    assert len(seen) > 1


async def test_bands_mirror_real_hardware_structure():
    """Le simulateur doit produire la même forme que le matériel : porteuses agrégées."""
    client = _client(seed=3)
    seen_nsa = False
    for _ in range(200):
        for status in await _cellular(client):
            if not status.connected:
                continue
            assert status.bands, "toute porteuse active doit être listée"
            # Les valeurs « principales » sont celles d'une des porteuses listées.
            primary = status.bands[0]
            assert (status.band, status.pci) == (primary.band, primary.pci)
            if status.rat == "5G NSA":
                seen_nsa = True
                # En NSA, la porteuse 5G est agrégée à une ancre LTE.
                assert primary.rat == "5G"
                assert any(b.rat == "LTE" for b in status.bands)
    assert seen_nsa, "le jeu de bandes simulé doit inclure de la 5G NSA"


async def test_uptime_grows_then_resets_on_disconnect():
    """La coupure est forcée plutôt que tirée au sort : le test reste déterministe."""
    client = _client(seed=11)

    for _ in range(5):
        statuses = await client.get_radio_status()
    assert statuses[0].uptime_s > 0, "l'uptime doit croître pendant la connexion"

    # Force la coupure du premier WAN : `down` est décrémenté puis testé, il faut
    # donc 2 pour rester hors ligne un pas complet (1 se reconnecterait aussitôt).
    client._wans[0].down = 2
    client._wans[0].connected = False
    down = (await client.get_radio_status())[0]
    assert down.connected is False
    assert down.uptime_s == 0
    assert down.status_message == "Disconnected"

    # Après reconnexion, la session repart de zéro.
    back = (await client.get_radio_status())[0]
    assert back.connected is True
    assert 0 < back.uptime_s <= 6


async def test_wifi_wan_is_simulated():
    """La flotte de démo inclut un lien Wi-Fi amont (type Starlink) comme en réel."""
    client = _client()
    for _ in range(50):
        statuses = await client.get_radio_status()

    wifi = [s for s in statuses if s.kind == "wifi"]
    assert len(wifi) == 1
    link = wifi[0]
    assert link.rat == "Wi-Fi"
    assert link.wifi is not None
    assert link.wifi.ssid == "STARLINK"
    assert link.wifi.band == "5 GHz"
    assert -90 <= link.rssi <= -40
    assert 1 <= link.signal_bars <= 5
    # Un lien Wi-Fi n'a ni cellule ni porteuses agrégées.
    assert link.cell_id is None
    assert link.bands == []


async def test_same_seed_gives_identical_run():
    a = [s.rsrp for s in await _client(seed=1).get_radio_status()]
    b = [s.rsrp for s in await _client(seed=1).get_radio_status()]
    assert a == b


def test_the_simulated_serial_survives_a_restart():
    """`hash()` d'une chaîne est SALÉ par processus depuis Python 3.3.

    Le même équipement simulé rendait `0000-0000-9272` puis `0000-0000-0943` au
    redémarrage du serveur. Or le numéro de série est LA clé d'identité du
    projet — celle qui survit à un changement d'adresse, et dont `_device_id`
    dérive l'identifiant. Un simulateur qui en change à chaque lancement
    fabrique un équipement neuf à chaque fois, et l'appairage en démonstration
    accumulait les doublons.

    Le test relance un interpréteur NEUF, avec un sel de hachage différent :
    c'est la seule façon de le prouver depuis un seul processus.
    """
    import os
    import subprocess
    import sys

    code = (
        "import asyncio,sys; sys.path.insert(0,'.');"
        "from app.fleet import RouterConfig;"
        "from app.peplink.mock import MockPeplinkClient;"
        "c=MockPeplinkClient(RouterConfig(id='mbx-01',name='M',host='h'));"
        "print(asyncio.run(c.get_device_info()).serial)"
    )
    series = set()
    for graine in ("1", "2", "3"):
        # L'environnement du parent, PLUS la graine : un environnement réduit à
        # PATH faisait tomber `import asyncio` sous Windows — winsock a besoin
        # de SYSTEMROOT (« WinError 10106 »), vu au premier run de l'action.
        out = subprocess.run(
            [sys.executable, "-c", code],
            capture_output=True, text=True, env={**os.environ, "PYTHONHASHSEED": graine},
        )
        assert out.returncode == 0, out.stderr
        series.add(out.stdout.strip())

    assert len(series) == 1, f"le série change d'un processus à l'autre : {series}"


def test_the_simulator_uses_the_real_channel_to_band_rule():
    """Le simulateur avait sa propre copie, à deux paliers au lieu de trois.

    Un canal 6 GHz y ressortait « 5 GHz » : le simulateur montrait un écran que
    le vrai routeur n'aurait jamais produit, ce qui lui retire sa seule raison
    d'être. Une règle, un exemplaire.
    """
    from app.peplink.mock import _wifi_band as regle_du_simulateur
    from app.peplink.router_api import _wifi_band

    assert regle_du_simulateur is _wifi_band
    assert (_wifi_band(6), _wifi_band(44), _wifi_band(197)) == ("2,4 GHz", "5 GHz", "6 GHz")


def test_the_simulator_names_the_station_of_every_cell():
    """Sans `enb_id`, la fiche de session ne pouvait jamais dire « même
    station » en démonstration : la diversité de trajet promise par les
    handovers simulés n'existait pas."""
    from app.peplink.mock import MockPeplinkClient
    from app.fleet import RouterConfig, WanConfig

    client = MockPeplinkClient(RouterConfig(
        id="demo", name="Démo", mode="mock",
        wans=[WanConfig(id="cellular-1", name="SIM 1"), WanConfig(id="cellular-2", name="SIM 2")],
    ))
    import asyncio
    statuses = asyncio.run(client.get_radio_status())
    cellulaires = [s for s in statuses if s.kind == "cellular" and s.cell_id is not None]
    assert cellulaires
    for s in cellulaires:
        assert s.enb_id == s.cell_id // 256 and s.sector_id == s.cell_id % 256
