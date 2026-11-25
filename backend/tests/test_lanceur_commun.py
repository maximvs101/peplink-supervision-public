"""L'exécutable unique : `lanceur_commun.py`, qui lance le superviseur et le
témoin Ethernet dans le même processus.

Comme `test_lancer.py` et `temoin/test_lancement.py` : ce qui se teste ici est
ce que le lanceur décide avant de servir — ports, bases, second lancement — et
l'arrêt des deux serveurs sur un signal. La construction (PyInstaller) se
vérifie par `tools/construire_executable.py` puis `tools/verifier_executable.py`.
"""

from __future__ import annotations

import asyncio
import os
import signal
import sys
from contextlib import asynccontextmanager
from pathlib import Path

import pytest
from fastapi import FastAPI

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import lanceur_commun as lc
import lancer
# Importée ici, avant que les tests ne changent l'environnement qu'elle lit.
from app.main import app as appli
from temoin import __main__ as temoin_lanceur

_VARIABLES = ("FRONTEND_DIST", "DATABASE_PATH", "ROUTERS_CONFIG", "PEPLINK_MODE", "HOST", "PORT",
              "PORT_SUPERVISEUR", "PORT_TEMOIN", "OUVRIR_NAVIGATEUR", "ENV_FILE")


@pytest.fixture
def dossier(monkeypatch, tmp_path):
    """Un dossier d'exécutable vide, un environnement propre, rendu après le test."""
    sauve = dict(os.environ)
    for cle in _VARIABLES:
        os.environ.pop(cle, None)
    os.environ["OUVRIR_NAVIGATEUR"] = "0"
    monkeypatch.setattr(lancer, "dossiers", lambda: (tmp_path / "ui", tmp_path / "data"))
    ecran = Path(temoin_lanceur.__file__).resolve().parent / "ecran"
    monkeypatch.setattr(temoin_lanceur, "chemins", lambda: (tmp_path / "data" / "temoin.db", ecran))
    monkeypatch.setattr(appli.state, "lien_temoin", None, raising=False)
    try:
        yield tmp_path
    finally:
        os.environ.clear()
        os.environ.update(sauve)


@pytest.fixture
def lance(monkeypatch):
    """`servir_les_deux` remplacé : ce qu'il aurait servi, et les ouvertures."""
    vu: dict = {"servi": [], "ouverts": []}

    async def servir(*args):
        vu["servi"].append(args)

    monkeypatch.setattr(lc, "servir_les_deux", servir)
    monkeypatch.setattr(lancer, "ouvrir_quand_pret", lambda adresse: vu["ouverts"].append(adresse))
    monkeypatch.setattr(lancer.webbrowser, "open", lambda adresse: vu["ouverts"].append(adresse))
    monkeypatch.setattr(lc.webbrowser, "open", lambda adresse: vu["ouverts"].append(adresse))
    return vu


def _ports_libres(monkeypatch, libre=lambda hote, port: True):
    monkeypatch.setattr(lancer, "port_disponible", libre)
    monkeypatch.setattr(temoin_lanceur, "port_disponible", libre)


def test_un_double_clic_lance_les_deux_serveurs(monkeypatch, dossier, lance):
    _ports_libres(monkeypatch)
    # PORT garde son sens pour les outils lancés seuls : l'exécutable ne le lit pas.
    os.environ["PORT"] = "9999"
    os.environ["OUVRIR_NAVIGATEUR"] = "1"
    assert lc.main([]) == 0
    [(app, hote, port_s, base_t, ecran, port_t, adresse_s)] = lance["servi"]
    assert app is appli
    assert (hote, port_s, port_t) == ("127.0.0.1", 8000, 8010)
    assert adresse_s == "http://127.0.0.1:8000"
    assert base_t == dossier / "data" / "temoin.db" and (ecran / "index.html").is_file()
    # Les deux bases, distinctes, dans data/ à côté.
    assert os.environ["DATABASE_PATH"] == str(dossier / "data" / "samples.db")
    # Le lien du superviseur vers le témoin, et le navigateur sur le superviseur.
    assert appli.state.lien_temoin == "http://127.0.0.1:8010"
    assert lance["ouverts"] == ["http://127.0.0.1:8000"]
    assert (dossier / "data" / "temoin.db.port").read_text() == "8010"
    # Le verrou du témoin est relâché à l'arrêt.
    verrou = temoin_lanceur.verrouiller(dossier / "data" / "temoin.db")
    assert verrou is not None
    verrou.close()


def test_les_ports_viennent_de_port_superviseur_et_port_temoin(monkeypatch, dossier, lance):
    _ports_libres(monkeypatch)
    os.environ.update(PORT_SUPERVISEUR="8100", PORT_TEMOIN="8110", HOST="0.0.0.0")
    assert lc.main([]) == 0
    [(_, hote, port_s, _, _, port_t, adresse_s)] = lance["servi"]
    # L'hôte du superviseur reste HOST ; le témoin, lui, reste sur 127.0.0.1.
    assert (hote, port_s, port_t) == ("0.0.0.0", 8100, 8110)
    assert adresse_s == "http://127.0.0.1:8100"
    assert appli.state.lien_temoin == "http://127.0.0.1:8110"


def test_les_ports_viennent_aussi_du_env_pose_a_cote(monkeypatch, dossier, lance):
    _ports_libres(monkeypatch)
    (dossier / ".env").write_text("PORT_SUPERVISEUR=8200\nPORT_TEMOIN=8210\n", encoding="utf-8")
    assert lc.main([]) == 0
    [(_, _, port_s, _, _, port_t, _)] = lance["servi"]
    assert (port_s, port_t) == (8200, 8210)


def test_le_temoin_prend_le_port_suivant_si_le_sien_est_pris(monkeypatch, dossier, lance):
    _ports_libres(monkeypatch, lambda hote, port: port != 8010)
    assert lc.main([]) == 0
    [(_, _, port_s, _, _, port_t, _)] = lance["servi"]
    assert (port_s, port_t) == (8000, 8011)
    assert appli.state.lien_temoin == "http://127.0.0.1:8011"


@pytest.mark.parametrize("variable", ["PORT_SUPERVISEUR", "PORT_TEMOIN"])
def test_un_port_non_numerique_est_dit_sans_trace(monkeypatch, dossier, lance, capsys, variable):
    for valeur in ("abc", "0", "70000"):
        os.environ[variable] = valeur
        assert lc.main([]) == 2
        assert f"{variable}={valeur!r} n'est pas un numéro de port" in capsys.readouterr().out
    assert lance["servi"] == []


def test_superviseur_seul(monkeypatch, dossier):
    vu = []
    monkeypatch.setattr(lancer, "main", lambda: vu.append(("superviseur", os.environ["PORT"])))
    monkeypatch.setattr(temoin_lanceur, "main", lambda: pytest.fail("le témoin a été lancé"))
    assert lc.main(["--superviseur"]) == 0
    os.environ["PORT_SUPERVISEUR"] = "8100"
    assert lc.main(["--superviseur"]) == 0
    assert vu == [("superviseur", "8000"), ("superviseur", "8100")]


def test_temoin_seul(monkeypatch, dossier):
    vu = []

    def temoin():
        vu.append(os.environ["PORT"])
        return 0

    monkeypatch.setattr(temoin_lanceur, "main", temoin)
    monkeypatch.setattr(lancer, "main", lambda: pytest.fail("le superviseur a été lancé"))
    assert lc.main(["--temoin"]) == 0
    os.environ["PORT_TEMOIN"] = "8110"
    assert lc.main(["--temoin"]) == 0
    assert vu == ["8010", "8110"]


def test_les_deux_options_ensemble_sont_refusees(dossier):
    with pytest.raises(SystemExit) as sortie:
        lc.main(["--superviseur", "--temoin"])
    assert sortie.value.code == 2


def test_un_second_lancement_rouvre_l_instance_qui_tourne(monkeypatch, dossier, lance, capsys):
    # La première instance tient le verrou du témoin et le port du superviseur.
    base = dossier / "data" / "temoin.db"
    base.parent.mkdir(parents=True)
    premier = temoin_lanceur.verrouiller(base)
    temoin_lanceur._fichier_port(base).write_text("8010")
    _ports_libres(monkeypatch, lambda hote, port: False)
    sauvegardes = []
    monkeypatch.setattr(lancer, "sauvegarder", lambda b: sauvegardes.append(b))
    os.environ["OUVRIR_NAVIGATEUR"] = "1"
    try:
        assert lc.main([]) == 0
    finally:
        premier.close()
    sortie = capsys.readouterr().out
    assert "Déjà lancé dans ce dossier" in sortie and "http://127.0.0.1:8010" in sortie
    assert lance["servi"] == [] and sauvegardes == []
    assert lance["ouverts"] == ["http://127.0.0.1:8000"]


def test_un_temoin_lance_seul_dans_ce_dossier_est_rouvert(monkeypatch, dossier, lance):
    base = dossier / "data" / "temoin.db"
    base.parent.mkdir(parents=True)
    premier = temoin_lanceur.verrouiller(base)
    temoin_lanceur._fichier_port(base).write_text("8012")
    _ports_libres(monkeypatch)
    os.environ["OUVRIR_NAVIGATEUR"] = "1"
    try:
        assert lc.main([]) == 0
    finally:
        premier.close()
    assert lance["servi"] == [] and lance["ouverts"] == ["http://127.0.0.1:8012"]


def test_le_port_du_superviseur_pris_arrete_tout_sans_sauvegarde(monkeypatch, dossier, lance, capsys):
    _ports_libres(monkeypatch, lambda hote, port: port != 8000)
    sauvegardes = []
    monkeypatch.setattr(lancer, "sauvegarder", lambda b: sauvegardes.append(b))
    os.environ["OUVRIR_NAVIGATEUR"] = "1"
    assert lc.main([]) == 2
    assert "PORT_SUPERVISEUR" in capsys.readouterr().out
    assert lance["servi"] == [] and sauvegardes == []
    assert lance["ouverts"] == ["http://127.0.0.1:8000"]
    # Le verrou du témoin n'est pas gardé.
    verrou = temoin_lanceur.verrouiller(dossier / "data" / "temoin.db")
    assert verrou is not None
    verrou.close()


def test_une_base_illisible_est_dite_sans_trace(monkeypatch, dossier, lance, capsys):
    _ports_libres(monkeypatch)
    (dossier / "data").mkdir()
    (dossier / "data" / "temoin.db").write_bytes(b"ceci n'est pas une base" * 50)
    assert lc.main([]) == 3
    sortie = capsys.readouterr().out
    assert "illisible" in sortie and "Traceback" not in sortie
    assert lance["servi"] == []


# --- Arrêt -----------------------------------------------------------------

def test_le_serveur_commun_laisse_les_signaux_au_lanceur():
    import uvicorn

    avant = signal.getsignal(signal.SIGINT)
    serveur = lc._serveur_sans_signaux()(uvicorn.Config(FastAPI()))
    with serveur.capture_signals():
        assert signal.getsignal(signal.SIGINT) is avant


class _FauxServeur:
    """Ce que fait uvicorn de l'application : son cycle de vie, jusqu'à l'arrêt."""

    vus: list = []

    def __init__(self, config):
        self.config, self.should_exit, self.force_exit = config, False, False
        _FauxServeur.vus.append(self)

    def handle_exit(self, sig, trame):
        self.should_exit = True

    async def serve(self):
        app = self.config.app
        async with app.router.lifespan_context(app):
            while not self.should_exit:
                await asyncio.sleep(0.01)


def _appli_notee(journal: list) -> FastAPI:
    @asynccontextmanager
    async def cycle(_app):
        journal.append("superviseur démarré")
        yield
        journal.append("superviseur arrêté")

    return FastAPI(lifespan=cycle)


async def test_ctrl_c_arrete_les_deux_serveurs_et_ferme_les_bases(monkeypatch, tmp_path):
    from temoin.stockage import Depot

    journal: list = []
    fermer = Depot.close

    async def close(self):
        if self._conn is not None:
            journal.append("base du témoin fermée")
        await fermer(self)

    monkeypatch.setattr(Depot, "close", close)
    _FauxServeur.vus = []
    monkeypatch.setattr(lc, "_serveur_sans_signaux", lambda: _FauxServeur)
    avant = signal.getsignal(signal.SIGINT)
    ecran = Path(temoin_lanceur.__file__).resolve().parent / "ecran"

    async def ctrl_c():
        while len(_FauxServeur.vus) < 2:
            await asyncio.sleep(0.01)
        await asyncio.sleep(0.05)
        signal.raise_signal(signal.SIGINT)

    appui = asyncio.create_task(ctrl_c())
    await asyncio.wait_for(lc.servir_les_deux(
        _appli_notee(journal), "127.0.0.1", 8000, tmp_path / "temoin.db", ecran, 8010,
        "http://127.0.0.1:8000"), timeout=10)
    await appui
    assert [s.config.port for s in _FauxServeur.vus] == [8000, 8010]
    assert all(s.should_exit for s in _FauxServeur.vus)
    # Le journal d'accès d'uvicorn est UN journal pour le processus : la
    # configuration du témoin le rallumait pour le superviseur, dont les
    # adresses ne doivent pas passer à la console. Coupé pour les deux.
    assert [s.config.access_log for s in _FauxServeur.vus] == [False, False]
    # Les deux arrêts ont lieu ; leur ordre relatif dépend de la boucle.
    assert sorted(journal) == sorted(["superviseur démarré", "superviseur arrêté",
                                      "base du témoin fermée"])
    # L'adresse du superviseur est donnée au témoin, pour son lien.
    import httpx

    temoin = _FauxServeur.vus[1].config.app
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=temoin),
                                 base_url="http://127.0.0.1:8010") as c:
        assert (await c.get("/api/sante")).json()["superviseur"] == "http://127.0.0.1:8000"
    # Les gestionnaires d'avant sont rendus.
    assert signal.getsignal(signal.SIGINT) is avant


async def test_un_serveur_qui_s_arrete_arrete_l_autre(monkeypatch, tmp_path):
    class TemoinQuiTombe(_FauxServeur):
        async def serve(self):
            if self.config.port == 8010:
                return  # démarrage manqué
            await super().serve()

    _FauxServeur.vus = []
    monkeypatch.setattr(lc, "_serveur_sans_signaux", lambda: TemoinQuiTombe)
    journal: list = []
    ecran = Path(temoin_lanceur.__file__).resolve().parent / "ecran"
    await asyncio.wait_for(lc.servir_les_deux(
        _appli_notee(journal), "127.0.0.1", 8000, tmp_path / "temoin.db", ecran, 8010,
        "http://127.0.0.1:8000"), timeout=10)
    assert "superviseur arrêté" in journal
