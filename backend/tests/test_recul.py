"""Le recul sur équipement injoignable.

Un équipement éteint était réinterrogé à chaque cadence, pour toujours. Or les
équipements ne sont allumés qu'à l'arrêt du véhicule : une flotte en majorité
éteinte est l'état NOMINAL, et chaque muet consommait un délai de connexion à
chaque tour. La pause d'un injoignable double à chaque échec consécutif,
plafonnée à `_RECUL_MAX_S` ; un relevé abouti la ramène à la cadence.
"""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock

import pytest

from app.devices.registry import DeviceRegistry
from app.fleet import RouterConfig, WanConfig
from app.models import WanRadioStatus
from app.poller import _RECUL_MAX_S, Poller
from app.storage.db import SampleRepository


async def _monter(tmp_path):
    repo = SampleRepository(str(tmp_path / "s.db"))
    await repo.connect()
    registry = DeviceRegistry(default_mode="mock")
    p = Poller(registry, repo, interval_seconds=0.5)
    record = await registry.add(RouterConfig(
        id="d", name="D", mode="mock", wans=[WanConfig(id="2", name="SIM")]))
    record.client.get_radio_status = AsyncMock(side_effect=OSError("injoignable"))
    record.client.get_router_events = AsyncMock(return_value=[])
    return repo, registry, p, record


class _Arret(Exception):
    pass


def _faux_sommeil(journal: list[float], *, apres: int, action=None):
    """Note chaque attente demandée, sans attendre ; s'arrête après `apres`."""
    async def dormir(duree):
        journal.append(duree)
        await asyncio.sleep(0)   # rend la main, comme une vraie attente
        if action is not None:
            action(len(journal))
        if len(journal) >= apres:
            raise _Arret
    return dormir


async def test_la_pause_double_jusqu_au_plafond_puis_revient_a_la_cadence(tmp_path):
    repo, registry, p, record = await _monter(tmp_path)
    record.status = "injoignable"
    pauses = [p._pause(record) for _ in range(9)]
    assert pauses == [0.5, 1.0, 2.0, 4.0, 8.0, 16.0, _RECUL_MAX_S, _RECUL_MAX_S, _RECUL_MAX_S]
    # Un relevé abouti : retour immédiat à la cadence, et le compte repart de zéro.
    record.status = "connecté"
    assert p._pause(record) == 0.5
    record.status = "injoignable"
    assert p._pause(record) == 0.5
    await registry.close()
    await repo.close()


async def test_un_refus_d_identifiants_n_est_pas_un_recul(tmp_path):
    """Le refus a déjà son arrêt (auth_blocked) : le recul ne s'y mêle pas."""
    repo, registry, p, record = await _monter(tmp_path)
    record.status = "identifiants refusés"
    assert [p._pause(record) for _ in range(3)] == [0.5, 0.5, 0.5]
    await registry.close()
    await repo.close()


async def test_la_boucle_espace_ses_tentatives(tmp_path):
    repo, registry, p, record = await _monter(tmp_path)
    attentes: list[float] = []
    p._sommeil = _faux_sommeil(attentes, apres=16)
    with pytest.raises(_Arret):
        await p._run_device(record)
    tentatives = record.client.get_radio_status.await_count
    # Seize tranches d'au plus une cadence : à cadence fixe, seize tentatives.
    # Avec le recul (0,5 + 1 + 2 + 4 = 7,5 s en tranches de 0,5), cinq.
    assert all(a <= 0.5 for a in attentes), "la pause se découpe en tranches d'une cadence"
    assert tentatives == 5, f"{tentatives} tentatives pour 16 tranches"
    assert record.status == "injoignable"
    await registry.close()
    await repo.close()


async def test_un_reappairage_pendant_le_recul_est_repris_sans_attendre_le_plafond(tmp_path):
    """La pause vérifie entre ses tranches que l'équipement est toujours celui
    du registre : sans cela, un réappairage en plein recul attendait jusqu'à
    trente secondes son premier relevé, contre une cadence aujourd'hui."""
    repo, registry, p, record = await _monter(tmp_path)
    for _ in range(6):
        record.status = "injoignable"
        p._pause(record)          # le recul est au plafond

    attentes: list[float] = []

    def remplacer(n):
        if n == 2:
            # Le même identifiant, réappairé : un AUTRE record.
            asyncio.get_running_loop().create_task(registry.add(RouterConfig(
                id="d", name="D", mode="mock", wans=[WanConfig(id="2", name="SIM")])))

    p._sommeil = _faux_sommeil(attentes, apres=200, action=remplacer)
    await asyncio.wait_for(p._run_device(record), timeout=5)
    # La boucle de l'ancien record s'est arrêtée au bout de quelques tranches,
    # pas après les soixante d'un plafond de trente secondes.
    assert len(attentes) <= 4
    await registry.close()
    await repo.close()


async def test_oublier_un_equipement_efface_son_recul(tmp_path):
    repo, registry, p, record = await _monter(tmp_path)
    record.status = "injoignable"
    for _ in range(5):
        p._pause(record)
    p.forget("d")
    assert p._pause(record) == 0.5
    await registry.close()
    await repo.close()


async def test_un_equipement_qui_repond_garde_sa_cadence(tmp_path):
    repo, registry, p, record = await _monter(tmp_path)
    record.client.get_radio_status = AsyncMock(return_value=[
        WanRadioStatus(wan_id="2", wan_name="SIM", kind="cellular", connected=True)])
    attentes: list[float] = []
    p._sommeil = _faux_sommeil(attentes, apres=6)
    with pytest.raises(_Arret):
        await p._run_device(record)
    assert record.client.get_radio_status.await_count == 6
    await registry.close()
    await repo.close()
