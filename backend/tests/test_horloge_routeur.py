"""R-3.9 : surveiller l'horloge du routeur, sans redater les relevés.

Mesuré sur le Dome le 25/09 : l'horloge du routeur a ~2 s d'avance sur le
poste (NTP) — redater les relevés à la source n'apporterait rien. Le risque est
un routeur à l'heure FAUSSE (démarré sans Internet) : son historique à 10 s est
écarté en silence (garde de 60 s), et les faits de son journal tomberaient au
mauvais endroit. L'écart se mesure à chaque lecture de `status.system.info`,
déjà faite ; au-delà de 60 s, une alerte le dit et le journal n'est plus lu.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from unittest.mock import AsyncMock, patch

import app.peplink.router_api as module
from app.fleet import RouterConfig, WanConfig
from app.peplink.router_api import RouterApiPeplinkClient

T0 = datetime(2026, 9, 25, 9, 0, tzinfo=timezone.utc)


def _info(decalage_s: float) -> dict:
    return {"stat": "ok", "response": {
        "device": {"serialNumber": "0000-0000-0042", "model": "Dome Pro Duo", "firmwareVersion": "8.5.1"},
        "systemTime": {"timestamp": int(T0.timestamp() + decalage_s)},
    }}


def test_the_offset_is_measured_at_each_identity_read():
    client = RouterApiPeplinkClient(RouterConfig(id="d", name="D", host="h"))
    for decalage in (2, -754):
        with patch.object(client, "_get", AsyncMock(return_value=_info(decalage))), \
                patch.object(module, "utcnow", lambda: T0):
            info = asyncio.run(client.identify())
        assert info.clock_offset_s == decalage


def test_no_published_clock_means_no_offset_rather_than_zero():
    brut = _info(0)
    del brut["response"]["systemTime"]
    client = RouterApiPeplinkClient(RouterConfig(id="d", name="D", host="h"))
    with patch.object(client, "_get", AsyncMock(return_value=brut)):
        assert asyncio.run(client.identify()).clock_offset_s is None


async def _poller_avec_decalage(tmp_path, decalage):
    from app.devices.registry import DeviceRegistry
    from app.models import DeviceInfo, WanRadioStatus
    from app.poller import Poller
    from app.storage.db import SampleRepository

    repo = SampleRepository(str(tmp_path / "s.db"))
    await repo.connect()
    registry = DeviceRegistry(default_mode="mock")
    p = Poller(registry, repo, interval_seconds=0.5)
    record = await registry.add(RouterConfig(id="d", name="D", mode="mock", wans=[WanConfig(id="2", name="SIM")]))
    record.client.get_radio_status = AsyncMock(return_value=[
        WanRadioStatus(wan_id="2", wan_name="SIM", kind="cellular", connected=True)])
    record.client.get_device_info = AsyncMock(return_value=DeviceInfo(
        model="Dome", serial="0000-0000-0042", clock_offset_s=decalage))
    record.client.get_router_events = AsyncMock(return_value=[])
    return repo, registry, p, record


async def test_a_clock_off_by_more_than_a_minute_raises_an_alert_and_stops_the_log(tmp_path):
    repo, registry, p, record = await _poller_avec_decalage(tmp_path, -754.0)
    await p._poll_once()
    alertes = [a for a in p.alerts("d") if a.kind == "horloge du routeur décalée"]
    assert len(alertes) == 1
    assert "12 min" in alertes[0].detail and "en retard" in alertes[0].detail
    record.client.get_router_events.assert_not_awaited()
    await registry.close()
    await repo.close()


async def test_a_synchronised_clock_raises_nothing_and_the_log_is_read(tmp_path):
    repo, registry, p, record = await _poller_avec_decalage(tmp_path, 2.0)
    await p._poll_once()
    assert not [a for a in p.alerts("d") if a.kind == "horloge du routeur décalée"]
    record.client.get_router_events.assert_awaited()
    await registry.close()
    await repo.close()


async def test_the_alert_goes_when_the_clock_is_fixed(tmp_path):
    from app.models import DeviceInfo

    repo, registry, p, record = await _poller_avec_decalage(tmp_path, 90.0)
    await p._poll_once()
    assert [a for a in p.alerts("d") if a.kind == "horloge du routeur décalée"]
    assert "en avance" in p.alerts("d")[-1].detail
    # La fiche relue porte une horloge remise à l'heure : l'alerte s'efface.
    record.info = DeviceInfo(model="Dome", clock_offset_s=1.0)
    p._verifier_horloge(record)
    assert not [a for a in p.alerts("d") if a.kind == "horloge du routeur décalée"]
    await registry.close()
    await repo.close()


async def test_a_clock_that_drifts_later_is_caught_by_the_periodic_refresh(tmp_path):
    """La fiche est relue tous les douze cycles : une horloge qui se décale après
    l'appairage — ou qui se remet à l'heure — se voit à cette relecture-là."""
    from app.models import DeviceInfo
    from app.poller import _INFO_REFRESH_CYCLES

    repo, registry, p, record = await _poller_avec_decalage(tmp_path, 2.0)
    await p._poll_once()
    assert not [a for a in p.alerts("d") if a.kind == "horloge du routeur décalée"]
    record.client.get_device_info = AsyncMock(return_value=DeviceInfo(
        model="Dome", serial="0000-0000-0042", clock_offset_s=-400.0))
    for _ in range(_INFO_REFRESH_CYCLES + 1):
        await p._poll_once()
    assert [a for a in p.alerts("d") if a.kind == "horloge du routeur décalée"]
    await registry.close()
    await repo.close()


async def test_the_same_offset_on_every_device_points_at_this_computer(tmp_path):
    """C-32 : l'écart se mesure contre l'horloge du POSTE, et le texte
    accusait le routeur sans confronter les équipements. Un PC sans NTP, trois
    minutes d'avance : tous les routeurs étaient déclarés décalés. Le même
    écart partout désigne le poste ; un seul équipement, on ne sait pas qui
    dérive, et le texte parle d'un écart entre les deux."""
    from unittest.mock import AsyncMock

    from app.fleet import RouterConfig, WanConfig
    from app.models import DeviceInfo

    repo, registry, p, record = await _poller_avec_decalage(tmp_path, -180.0)
    await p._poll_once()
    (seul,) = [a for a in p.alerts("d") if a.kind == "horloge du routeur décalée"]
    assert "ce poste" in seul.detail
    assert "horloge de ce poste" not in seul.detail, "un seul équipement ne désigne personne"

    autre = await registry.add(RouterConfig(id="e", name="E", mode="mock", wans=[WanConfig(id="2", name="SIM")]))
    autre.client.get_radio_status = record.client.get_radio_status
    autre.client.get_router_events = AsyncMock(return_value=[])
    autre.client.get_device_info = AsyncMock(return_value=DeviceInfo(
        model="MBX", serial="0000-0000-0043", clock_offset_s=-182.0))
    await p._poll_once()
    # Le second équipement, identifié à ce cycle, requalifie aussi l'alerte
    # déjà ouverte du premier.
    for rid in ("d", "e"):
        (alerte,) = [a for a in p.alerts(rid) if a.kind == "horloge du routeur décalée"]
        assert "horloge de ce poste" in alerte.detail, alerte.detail
    await registry.close()
    await repo.close()


async def test_a_removal_during_the_log_read_does_not_resurrect_the_admin_alert(tmp_path):
    """C-34 : un forget() tombé pendant les awaits du journal ressuscitait
    _alerte_admin ; après un ré-appairage, le nouveau record héritait de
    l'alerte de l'ancien."""
    from datetime import timedelta

    from app.models import RouterEvent, utcnow

    repo, registry, p, record = await _poller_avec_decalage(tmp_path, 2.0)
    maintenant = utcnow()
    refus = [RouterEvent(ts=maintenant - timedelta(seconds=10 * i), kind="connexion admin refusée",
                         wan_id=None, detail="admin", empreinte=f"e{i}") for i in range(3)]
    origine = repo.router_events

    async def retrait_pendant_la_lecture(*args, **kwargs):
        await registry.remove("d")
        p.forget("d")
        return await origine(*args, **kwargs)

    record.client.get_router_events.return_value = refus
    repo.router_events = retrait_pendant_la_lecture
    await p._lire_journal_routeur(record, [])
    assert "d" not in p._alerte_admin, "l'alerte d'un équipement retiré a été réécrite"
    await registry.close()
    await repo.close()


async def test_a_removal_before_the_clock_check_leaves_no_clock_alert(tmp_path):
    repo, registry, p, record = await _poller_avec_decalage(tmp_path, -754.0)
    await p._poll_once()
    await registry.remove("d")
    p.forget("d")
    p._verifier_horloge(record)
    assert "d" not in p._alerte_horloge
    await registry.close()
    await repo.close()
