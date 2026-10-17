"""Le lancement du témoin (`python -m temoin`, et l'exécutable qui l'embarque).

Comme `test_lancer.py` pour le superviseur : ce qui se teste ici est ce que le
lanceur décide avant de servir — où vit la base, où est l'écran, sur quelle
adresse et quel port il écoute. La construction elle-même (PyInstaller) ne se
teste pas ici : `tools/construire_executable.py --produit temoin`, puis
`tools/verifier_executable.py dist/temoin-ethernet --sante /api/sante`.
"""

from __future__ import annotations

import socket
import sys
from pathlib import Path

import temoin
import temoin.__main__ as lanceur

BACKEND = Path(__file__).resolve().parents[2]


def test_la_base_vit_a_cote_de_l_executable(monkeypatch, tmp_path):
    # Depuis le dépôt : la base sous backend/data, comme celle du superviseur.
    base, ecran = lanceur.chemins()
    assert base == BACKEND / "data" / "temoin.db"
    assert ecran == Path(temoin.__file__).resolve().parent / "ecran"
    assert (ecran / "index.html").is_file()
    # Gelé : ce que PyInstaller pose — `sys.frozen`, le dossier de
    # décompression dans `sys._MEIPASS`, et `sys.executable` là où
    # l'exploitant a mis le fichier. La base va À CÔTÉ du fichier.
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "_MEIPASS", str(tmp_path / "_MEI"), raising=False)
    monkeypatch.setattr(sys, "executable", str(tmp_path / "cle" / "temoin-ethernet"))
    base, _ = lanceur.chemins()
    assert base == tmp_path / "cle" / "data" / "temoin.db"


def test_le_temoin_n_ecoute_que_sur_le_poste(monkeypatch, tmp_path):
    vu: dict = {}

    class FauxServeur:
        def __init__(self, config):
            vu["hote"], vu["port"], vu["app"] = config.host, config.port, config.app

        async def serve(self):
            vu["base_creee"] = (tmp_path / "data" / "temoin.db").exists()

    import uvicorn

    monkeypatch.setattr(uvicorn, "Server", FauxServeur)
    monkeypatch.setattr(lanceur, "chemins",
                        lambda: (tmp_path / "data" / "temoin.db", Path(temoin.__file__).parent / "ecran"))
    # Même un HOST hérité du superviseur (0.0.0.0) n'ouvre pas le témoin au réseau.
    monkeypatch.setenv("HOST", "0.0.0.0")
    monkeypatch.setenv("OUVRIR_NAVIGATEUR", "0")
    monkeypatch.delenv("PORT", raising=False)
    monkeypatch.setattr(lanceur, "port_disponible", lambda hote, port: True)

    assert lanceur.main() == 0
    assert vu["hote"] == "127.0.0.1"
    assert vu["port"] == lanceur.PORT_DEFAUT == 8010
    assert vu["base_creee"]
    assert vu["app"].title == "Témoin de lien Ethernet"


def test_un_port_pris_cede_la_place_au_suivant(monkeypatch):
    # Le superviseur garde 8000 ; si 8010 est pris (un autre outil, un second
    # témoin), le suivant libre — et l'adresse affichée le dit.
    pris = {8010, 8011}
    monkeypatch.setattr(lanceur, "port_disponible", lambda hote, port: port not in pris)
    assert lanceur.choisir_port(8010) == 8012


def test_un_port_reellement_occupe_est_saute():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        s.listen()
        occupe = s.getsockname()[1]
        assert lanceur.choisir_port(occupe) != occupe


async def test_la_base_est_fermee_a_l_arret_de_l_application(monkeypatch, tmp_path):
    # uvicorn, arrêté par un signal, s'arrête proprement puis RELÈVE le signal
    # (0.29 et après) : le processus meurt avant tout `finally` du lanceur. La
    # base doit donc être fermée à l'arrêt de l'APPLICATION, quand uvicorn n'a
    # pas encore rendu la main.
    from temoin.stockage import Depot

    fermetures: list[str] = []
    fermer = Depot.close

    async def close(self):
        fermetures.append("ferme" if self._conn is not None else "deja")
        await fermer(self)

    monkeypatch.setattr(Depot, "close", close)
    vu: dict = {}

    class FauxServeur:
        def __init__(self, config):
            self.app = config.app

        async def serve(self):
            async with self.app.router.lifespan_context(self.app):
                vu["pendant"] = list(fermetures)
            vu["apres"] = list(fermetures)

    import uvicorn

    monkeypatch.setattr(uvicorn, "Server", FauxServeur)
    await lanceur.servir(tmp_path / "data" / "temoin.db", Path(temoin.__file__).parent / "ecran", 8010)
    assert vu == {"pendant": [], "apres": ["ferme"]}


def test_un_port_non_numerique_est_dit_sans_trace(monkeypatch, capsys):
    import pytest

    for valeur in ("abc", "80 80", "0", "70000"):
        monkeypatch.setenv("PORT", valeur)
        monkeypatch.setattr(lanceur, "choisir_port",
                            lambda depart: pytest.fail("aucun port ne doit être essayé"))
        assert lanceur.main() == 2
        assert "n'est pas un numéro de port" in capsys.readouterr().out
