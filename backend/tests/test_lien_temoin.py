"""Le lien du superviseur vers le témoin Ethernet (exécutable unique).

L'exécutable lance les deux serveurs et pose l'adresse du témoin sur
`app.state.lien_temoin` ; `/api/health` la publie au poste local, l'interface en
fait un lien. Le témoin n'écoute que sur 127.0.0.1 : un autre poste du réseau
ne l'atteindrait pas, et ne reçoit pas l'adresse.
"""

from __future__ import annotations

import httpx
from fastapi import FastAPI


async def _sante(tmp_path, monkeypatch, *, lien, client=("127.0.0.1", 123)) -> dict:
    from app import main
    from app.api import routes
    from app.config import Settings

    reglages = Settings(
        peplink_mode="mock", database_path=str(tmp_path / "s.db"),
        routers_config=str(tmp_path / "absent.yaml"), poll_interval_seconds=0.2,
    )
    monkeypatch.setattr(main, "get_settings", lambda: reglages)
    app = FastAPI()
    app.include_router(routes.router)
    if lien is not None:
        app.state.lien_temoin = lien
    async with main.lifespan(app):
        transport = httpx.ASGITransport(app=app, client=client)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
            return (await c.get("/api/health")).json()


async def test_lance_seul_le_superviseur_ne_publie_aucun_temoin(tmp_path, monkeypatch):
    assert (await _sante(tmp_path, monkeypatch, lien=None))["temoin"] is None


async def test_lance_avec_le_temoin_il_publie_son_adresse_au_poste_local(tmp_path, monkeypatch):
    sante = await _sante(tmp_path, monkeypatch, lien="http://127.0.0.1:8010")
    assert sante["temoin"] == "http://127.0.0.1:8010"


async def test_un_autre_poste_ne_recoit_pas_l_adresse_du_temoin(tmp_path, monkeypatch):
    sante = await _sante(tmp_path, monkeypatch, lien="http://127.0.0.1:8010",
                         client=("192.0.2.5", 123))
    assert sante["temoin"] is None
