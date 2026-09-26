"""Registre des équipements : ajout et retrait à chaud, sans redémarrage.

Ce module remplace la flotte figée au démarrage. Trois propriétés comptent :
un équipement mal configuré ne doit pas emporter les autres, la flotte doit
pouvoir muter pendant un cycle de scrutation, et un retrait doit effacer tout
état rattaché.
"""

import asyncio


from app.devices.registry import DeviceRegistry
from app.fleet import RouterConfig


def _simulated(device_id: str) -> RouterConfig:
    return RouterConfig(id=device_id, name=device_id.upper(), mode="mock")


def _real(device_id: str, host: str = "192.0.2.1", serial: str | None = None) -> RouterConfig:
    return RouterConfig(id=device_id, name=device_id.upper(), host=host,
                        username="admin", password="x", mode="router", serial=serial)


async def test_add_creates_a_client():
    registry = DeviceRegistry(default_mode="mock")
    record = await registry.add(_simulated("a"))
    assert record.client is not None and record.status == "en attente"
    await registry.close()


async def test_device_mode_overrides_the_global_one():
    """Un équipement appairé est réel même si l'application tourne en simulateur.

    C'est ce qui permet à une flotte de démonstration et à un équipement de
    terrain de coexister, ce qu'un mode global rendait impossible.
    """
    registry = DeviceRegistry(default_mode="mock")
    real = await registry.add(_real("mbx"))
    simulated = await registry.add(_simulated("demo"))
    assert type(real.client).__name__ == "RouterApiPeplinkClient"
    assert type(simulated.client).__name__ == "MockPeplinkClient"
    await registry.close()


async def test_a_broken_device_does_not_break_the_others():
    """Mode router sans adresse : enregistré en erreur, la flotte continue."""
    registry = DeviceRegistry(default_mode="router")
    broken = await registry.add(RouterConfig(id="cassé", name="Cassé", mode="router"))
    healthy = await registry.add(_simulated("sain"))

    assert broken.client is None
    assert broken.status == "erreur de configuration" and broken.error
    assert healthy.client is not None
    assert {r.id for r in registry.snapshot()} == {"cassé", "sain"}
    await registry.close()


async def test_re_adding_replaces_and_closes_the_previous_client():
    registry = DeviceRegistry(default_mode="mock")
    first = await registry.add(_simulated("a"))
    second = await registry.add(_simulated("a"))
    assert first is not second
    assert len(registry.snapshot()) == 1
    await registry.close()


async def test_lookup_by_serial_and_host():
    """Deux recherches, et la seconde n'est pas morte : c'est un rattrapage.

    `pair()` ne reconnaît un équipement déjà supervisé que par son numéro de
    série. Or celui d'un équipement déclaré dans le fichier de flotte n'est
    connu qu'APRÈS sa première identification réussie : avant, aucune
    correspondance n'est possible et appairer la même adresse crée un second
    identifiant. La base porte la trace de cet accident — le même routeur
    physique supervisé deux fois, donc scruté deux fois et affiché deux fois.
    L'adresse est l'indice le plus faible des deux, d'où « second recours ».
    """
    registry = DeviceRegistry(default_mode="mock")
    await registry.add(_real("mbx", host="192.0.2.7", serial="ABCD-1234-EFGH"))
    assert registry.find_by_serial("ABCD-1234-EFGH").id == "mbx"
    assert registry.find_by_host("192.0.2.7").id == "mbx"
    assert registry.find_by_serial(None) is None
    assert registry.find_by_host("203.0.113.1") is None
    await registry.close()


async def test_remove_returns_false_for_an_unknown_device():
    registry = DeviceRegistry(default_mode="mock")
    assert await registry.remove("jamais-vu") is False
    await registry.close()


async def test_snapshot_is_a_copy():
    """Le poller itère dessus pendant qu'un appairage peut survenir.

    Sans copie, muter la flotte en la parcourant lèverait au beau milieu d'un
    cycle — une panne intermittente, impossible à reproduire sur le terrain.
    """
    registry = DeviceRegistry(default_mode="mock")
    await registry.add(_simulated("a"))
    snapshot = registry.snapshot()

    await registry.add(_simulated("b"))
    await registry.remove("a")

    assert [r.id for r in snapshot] == ["a"]        # la copie est figée
    assert {r.id for r in registry.snapshot()} == {"b"}
    await registry.close()


async def test_concurrent_mutations_are_serialised():
    """Ajouts et retraits simultanés ne doivent pas corrompre le registre."""
    registry = DeviceRegistry(default_mode="mock")
    await asyncio.gather(*(registry.add(_simulated(f"d{i}")) for i in range(20)))
    assert len(registry.snapshot()) == 20

    await asyncio.gather(*(registry.remove(f"d{i}") for i in range(0, 20, 2)))
    assert len(registry.snapshot()) == 10
    await registry.close()


def test_without_a_fleet_file_router_mode_starts_empty_and_mock_mode_starts_with_the_demo(tmp_path):
    """L'exécutable emporté n'a pas de `routers.yaml` à côté : sa flotte est
    vide, et les équipements s'appairent depuis l'écran. Le repli sur la démo
    y faisait apparaître deux MAX HD4 « simulés » — vu à la première
    vérification de l'exécutable, le 19 septembre. La démo reste celle du
    simulateur."""
    from app.fleet import load_fleet

    absent = str(tmp_path / "routers.yaml")
    assert load_fleet(absent, "router") == []
    assert [r.id for r in load_fleet(absent, "mock")] == ["sng-01", "sng-02"]
    assert [r.id for r in load_fleet(absent)] == ["sng-01", "sng-02"]
    vide = tmp_path / "vide.yaml"
    vide.write_text("routers: []\n")
    assert load_fleet(str(vide), "router") == []
