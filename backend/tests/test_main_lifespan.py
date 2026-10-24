"""Arrêt du serveur : la veille de reconnexion est ATTENDUE avant que le
registre ne ferme ses clients (revue du 24/10)."""

from __future__ import annotations

import asyncio

from fastapi import FastAPI


async def test_la_veille_est_attendue_avant_la_fermeture_du_registre(tmp_path, monkeypatch):
    from app import main
    from app.config import Settings
    from app.devices.registry import DeviceRegistry

    reglages = Settings(
        peplink_mode="mock", database_path=str(tmp_path / "s.db"),
        routers_config=str(tmp_path / "absent.yaml"), poll_interval_seconds=0.2,
    )
    monkeypatch.setattr(main, "get_settings", lambda: reglages)
    etat = {"veille_finie": False, "vue_a_la_fermeture": None}

    class VeilleLente:
        def __init__(self, *a, **k):
            pass

        async def boucle(self):
            try:
                await asyncio.Event().wait()
            finally:
                # Ce qu'une tentative en vol ferait : un dernier `await` —
                # pendant lequel le registre était déjà en train de fermer.
                await asyncio.sleep(0.05)
                etat["veille_finie"] = True

    fermer = DeviceRegistry.close

    async def fermer_en_notant(self):
        etat["vue_a_la_fermeture"] = etat["veille_finie"]
        await fermer(self)

    monkeypatch.setattr(main, "Reconnexion", VeilleLente)
    monkeypatch.setattr(DeviceRegistry, "close", fermer_en_notant)
    async with main.lifespan(FastAPI()):
        await asyncio.sleep(0.05)
    assert etat["vue_a_la_fermeture"] is True, "le registre a fermé pendant que la veille tournait"
