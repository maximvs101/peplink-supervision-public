"""L'exécutable unique : ce que `lancer.py` décide avant de charger l'application.

La construction elle-même (PyInstaller, 38 s) n'est pas un test : c'est
`tools/construire_executable.py`, à lancer sur chaque système. Ce qui se teste
ici est la logique qui rendait l'exécutable muet le 19 septembre — l'interface
était cherchée dans `../frontend/dist`, qui n'existe pas dans un exécutable —
et ce qui protège la base sur une clé USB.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import lancer


def test_unfrozen_it_uses_the_repository_layout():
    interface, donnees = lancer.dossiers()
    assert interface == Path(lancer.__file__).resolve().parent.parent / "frontend" / "dist"
    assert donnees == Path(lancer.__file__).resolve().parent / "data"


def test_frozen_the_interface_is_embedded_and_the_data_sits_next_to_the_executable(
    monkeypatch, tmp_path
):
    # Ce que PyInstaller pose : `sys.frozen`, le dossier de décompression dans
    # `sys._MEIPASS`, et `sys.executable` là où l'exploitant a mis le fichier.
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "_MEIPASS", str(tmp_path / "_MEI"), raising=False)
    monkeypatch.setattr(sys, "executable", str(tmp_path / "cle" / "superviseur"))
    interface, donnees = lancer.dossiers()
    assert interface == tmp_path / "_MEI" / "frontend" / "dist"
    assert donnees == tmp_path / "cle" / "data"


def test_preparing_sets_the_paths_the_application_reads(monkeypatch, tmp_path):
    for cle in ("FRONTEND_DIST", "DATABASE_PATH", "ROUTERS_CONFIG", "PEPLINK_MODE"):
        monkeypatch.delenv(cle, raising=False)
    monkeypatch.setattr(lancer, "dossiers", lambda: (tmp_path / "ui", tmp_path / "data"))
    decision = lancer.preparer()
    assert (tmp_path / "data").is_dir()
    assert decision["base"] == str(tmp_path / "data" / "samples.db")
    import os

    assert os.environ["FRONTEND_DIST"] == str(tmp_path / "ui")
    assert os.environ["DATABASE_PATH"] == str(tmp_path / "data" / "samples.db")
    assert os.environ["PEPLINK_MODE"] == "router"
    # Et l'application lit bien ces chemins-là.
    from app.config import Settings

    assert Settings().frontend_dir == tmp_path / "ui"
    assert Settings().database_file == str(tmp_path / "data" / "samples.db")


def test_a_backup_is_taken_when_the_base_changed_and_only_two_are_kept(tmp_path):
    """Deux copies, et seulement quand la base a bougé : cinq copies d'une
    base jamais élaguée faisaient plus d'un gigaoctet sur une clé au bout de
    quelques mois, recopiés à chaque lancement."""
    import os
    import sqlite3
    from contextlib import closing

    base = tmp_path / "samples.db"
    assert lancer.sauvegarder(base) is None, "rien à sauvegarder avant la première base"
    copies = []
    for i in range(4):
        # `closing` : le `with` de sqlite3 valide sans FERMER, et sous Windows
        # une base ouverte ne se renomme pas (WinError 32, vu dans l'action).
        with closing(sqlite3.connect(base)) as c, c:     # la base a changé
            c.execute("CREATE TABLE IF NOT EXISTS t (n INTEGER)")
            c.execute("INSERT INTO t VALUES (?)", (i,))
        os.utime(base, (1_800_000_000 + i, 1_800_000_000 + i))
        copie = lancer.sauvegarder(base)
        assert copie is not None
        with closing(sqlite3.connect(copie)) as c:
            assert c.execute("SELECT COUNT(*) FROM t").fetchone()[0] == i + 1
        # Deux copies dans la même seconde porteraient le même nom : on les distingue.
        copies.append(copie.rename(copie.with_name(f"samples.{i:02d}.db.bak")))
    restantes = sorted(p.name for p in tmp_path.glob("samples.*.db.bak"))
    assert restantes == ["samples.02.db.bak", "samples.03.db.bak"], restantes
    # Relancer sans que la base ait bougé ne copie rien de plus.
    assert lancer.sauvegarder(base) == tmp_path / "samples.03.db.bak"
    assert len(list(tmp_path.glob("samples.*.db.bak"))) == 2


def _adresses_locales() -> list[str]:
    import socket
    try:
        return sorted({i[4][0] for i in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET)})
    except OSError:
        return []


def test_the_port_is_checked_before_any_browser_opens():
    """Port pris : uvicorn s'arrêtait sur « address already in use » pendant que
    le navigateur s'ouvrait sur l'AUTRE serveur."""
    import socket

    with socket.socket() as occupe:
        occupe.bind(("127.0.0.1", 0))
        occupe.listen(1)
        port = occupe.getsockname()[1]
        assert lancer.port_disponible("127.0.0.1", port) is False
        # La garde testait un bind AVEC SO_REUSEADDR : relancer avec
        # HOST=0.0.0.0 passait, et deux instances vivaient sur le même port.
        assert lancer.port_disponible("0.0.0.0", port) is False
    assert lancer.port_disponible("127.0.0.1", port) is True
    with socket.socket() as occupe:
        occupe.bind(("0.0.0.0", 0))
        occupe.listen(1)
        port = occupe.getsockname()[1]
        assert lancer.port_disponible("127.0.0.1", port) is False

    # Un service sur une AUTRE interface ne prend pas 127.0.0.1 : la garde
    # sondait aussi 0.0.0.0 et refusait de démarrer sur un port libre.
    autre = next((a for a in _adresses_locales() if not a.startswith("127.")), None)
    if autre is not None:
        with socket.socket() as ailleurs:
            ailleurs.bind((autre, 0))
            ailleurs.listen(1)
            port = ailleurs.getsockname()[1]
            assert lancer.port_disponible("127.0.0.1", port) is True
            assert lancer.port_disponible("0.0.0.0", port) is False


def _port_en_time_wait() -> int:
    """Un port qui ne porte plus que des connexions en TIME_WAIT, sans écoute.

    C'est l'état du port juste après l'arrêt du superviseur : le côté qui ferme
    le premier — ici le serveur — garde la connexion en TIME_WAIT une trentaine
    de secondes (macOS). Relancer à ce moment-là échouait sur « déjà pris ».
    """
    import socket

    with socket.socket() as ecoute:
        ecoute.bind(("127.0.0.1", 0))
        ecoute.listen(1)
        port = ecoute.getsockname()[1]
        client = socket.create_connection(("127.0.0.1", port))
        accepte, _ = ecoute.accept()
        accepte.close()      # le serveur ferme le premier : TIME_WAIT de son côté
        client.close()
    return port


@pytest.mark.skipif(sys.platform == "win32", reason="Windows lie un port en TIME_WAIT sans SO_REUSEADDR")
def test_a_port_left_in_TIME_WAIT_by_a_stopped_server_is_available():
    """Relancer juste après un arrêt : le port n'a plus d'écoute, seulement des
    connexions en TIME_WAIT, qu'uvicorn ignore (il lie avec SO_REUSEADDR sous
    POSIX). La garde, stricte, répondait « déjà pris ». Vu deux fois le 25/09."""
    import socket

    port = _port_en_time_wait()
    # La situation est bien reproduite : un bind STRICT échoue sur ce port…
    with socket.socket() as strict:
        with pytest.raises(OSError):
            strict.bind(("127.0.0.1", port))
    # …et pourtant uvicorn démarrerait : la garde ne doit pas dire le contraire.
    assert lancer.port_disponible("127.0.0.1", port) is True


@pytest.mark.skipif(sys.platform == "win32", reason="SO_REUSEADDR n'a pas ce sens sous Windows")
def test_a_live_instance_bound_like_uvicorn_is_still_refused():
    """Une seconde instance VIVANTE, liée comme uvicorn la lie (SO_REUSEADDR),
    reste un refus — sur son adresse, et sur le joker qui la chevauche (H-6.1)."""
    import socket

    for adresse_de_l_autre in ("127.0.0.1", "0.0.0.0"):
        with socket.socket() as autre:
            autre.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            autre.bind((adresse_de_l_autre, 0))
            autre.listen(1)
            port = autre.getsockname()[1]
            assert lancer.port_disponible("127.0.0.1", port) is False, adresse_de_l_autre
            assert lancer.port_disponible("0.0.0.0", port) is False, adresse_de_l_autre


def _adresse_du_lan() -> str | None:
    """L'adresse de cette machine sur son réseau, SANS résolution de nom : un
    `connect` UDP choisit la route sans envoyer de paquet. `_adresses_locales`
    passe par le nom de la machine — 5 s ici, et une liste vide."""
    import socket

    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.connect(("192.0.2.1", 9))
            adresse = s.getsockname()[0]
    except OSError:
        return None
    return None if adresse.startswith("127.") or adresse == "0.0.0.0" else adresse


@pytest.mark.skipif(_adresse_du_lan() is None, reason="aucune interface hors boucle locale")
def test_the_wildcard_stays_strict_against_a_listener_on_another_interface():
    """Une écoute sur l'adresse du LAN prend le port pour 0.0.0.0 — et pas pour
    127.0.0.1. Le cas existait déjà, mais son adresse venait d'une résolution
    de nom qui échoue ici : il était sauté sans bruit."""
    import socket

    with socket.socket() as ailleurs:
        ailleurs.bind((_adresse_du_lan(), 0))
        ailleurs.listen(1)
        port = ailleurs.getsockname()[1]
        assert lancer.port_disponible("0.0.0.0", port) is False
        assert lancer.port_disponible("127.0.0.1", port) is True


def test_a_port_bound_but_not_yet_listening_is_refused():
    """Un autre programme a lié le port sans écouter encore : une connexion est
    refusée, mais le bind d'uvicorn échouerait. La garde suit le bind."""
    import socket

    with socket.socket() as lie:
        lie.bind(("127.0.0.1", 0))
        port = lie.getsockname()[1]
        assert lancer.port_disponible("127.0.0.1", port) is False


@pytest.mark.parametrize("variable", ["HOST", "PORT"])
def test_host_and_port_come_from_the_environment(monkeypatch, tmp_path, variable):
    monkeypatch.setattr(lancer, "dossiers", lambda: (tmp_path / "ui", tmp_path / "data"))
    monkeypatch.setenv(variable, "0.0.0.0" if variable == "HOST" else "8123")
    decision = lancer.preparer()
    assert decision["hote" if variable == "HOST" else "port"] == ("0.0.0.0" if variable == "HOST" else "8123")


def test_the_env_file_next_to_the_tool_is_read_without_overriding_the_shell(monkeypatch, tmp_path):
    """Trois choses ne marchaient pas : à côté de l'exécutable, `.env` n'était
    jamais lu ; HOST/PORT/OUVRIR_NAVIGATEUR ne venaient que du shell ; et depuis
    le dépôt, le lanceur posait ses défauts par-dessus le `.env` local. L'ordre
    est : le shell, puis le `.env`, puis les défauts du lanceur."""
    for cle in ("FRONTEND_DIST", "DATABASE_PATH", "ROUTERS_CONFIG", "PEPLINK_MODE",
                "HOST", "PORT", "OUVRIR_NAVIGATEUR", "ENV_FILE", "POLL_INTERVAL_SECONDS"):
        monkeypatch.delenv(cle, raising=False)
    racine = tmp_path / "cle"
    racine.mkdir()
    (racine / ".env").write_text(
        "PEPLINK_MODE=mock\nPORT=8123\nDATABASE_PATH=ailleurs/base.db\nPOLL_INTERVAL_SECONDS=1\n"
    )
    monkeypatch.setenv("PORT", "9000")   # le shell gagne sur le .env
    monkeypatch.setattr(lancer, "dossiers", lambda: (racine / "ui", racine / "data"))
    decision = lancer.preparer()

    import os

    assert os.environ["PEPLINK_MODE"] == "mock", "le .env doit l'emporter sur le défaut du lanceur"
    assert decision["port"] == "9000", "le shell doit l'emporter sur le .env"
    # Relatif dans le .env, donc relatif au .env — et écrit en absolu (C-4).
    assert decision["base"] == str(racine / "ailleurs" / "base.db")
    # Et l'application lit le MÊME fichier pour ce que le lanceur ne pose pas.
    from app.config import Settings

    assert Settings().poll_interval_seconds == 1.0
    assert Settings().peplink_mode == "mock"


def _sans_chemins(monkeypatch):
    for cle in ("FRONTEND_DIST", "DATABASE_PATH", "ROUTERS_CONFIG", "PEPLINK_MODE",
                "HOST", "PORT", "OUVRIR_NAVIGATEUR", "ENV_FILE"):
        monkeypatch.delenv(cle, raising=False)


def test_relative_paths_from_the_env_file_are_anchored_next_to_it(monkeypatch, tmp_path):
    """C-4 : le .env du dépôt posé à côté de l'exécutable.

    Il porte `DATABASE_PATH=./data/samples.db` et `ROUTERS_CONFIG=./routers.yaml`.
    Lus en `setdefault`, ils l'emportaient sur les chemins absolus du lanceur ;
    l'application les résolvait depuis sa racine — `_MEIPASS` une fois gelée,
    que l'amorce efface à la sortie — et `sauvegarder()` depuis le répertoire
    courant, où il n'y avait rien : historique perdu à la relance, aucune
    copie, et le routers.yaml posé à côté jamais lu.
    """
    _sans_chemins(monkeypatch)
    cle = tmp_path / "cle"
    (cle / "data").mkdir(parents=True)
    (cle / ".env").write_text("DATABASE_PATH=./data/samples.db\nROUTERS_CONFIG=./routers.yaml\n")
    import sqlite3

    with sqlite3.connect(cle / "data" / "samples.db") as base:
        base.execute("CREATE TABLE t (x)")
    # Gelé : l'interface dans le dossier de décompression, le répertoire
    # courant ailleurs — le dossier personnel, sous macOS.
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "_MEIPASS", str(tmp_path / "_MEI"), raising=False)
    monkeypatch.setattr(sys, "executable", str(cle / "superviseur"))
    (tmp_path / "maison").mkdir()
    monkeypatch.chdir(tmp_path / "maison")

    decision = lancer.preparer()

    import os

    assert os.environ["DATABASE_PATH"] == str(cle / "data" / "samples.db")
    assert os.environ["ROUTERS_CONFIG"] == str(cle / "routers.yaml")
    assert decision["sauvegarde"], "la base existante doit être copiée"
    assert Path(decision["sauvegarde"]).parent == cle / "data"
    from app.config import Settings

    assert Settings().database_file == str(cle / "data" / "samples.db")
    assert Settings().routers_file == str(cle / "routers.yaml")


def test_a_relative_path_from_the_shell_is_anchored_on_the_current_directory(monkeypatch, tmp_path):
    """Le second cas de C-4 : lancé depuis la racine du dépôt, un chemin relatif
    était lu depuis `backend/` par l'application et depuis la racine par la
    sauvegarde. Un seul sens désormais — celui du shell qui l'a écrit."""
    _sans_chemins(monkeypatch)
    monkeypatch.setattr(lancer, "dossiers", lambda: (tmp_path / "ui", tmp_path / "vide" / "data"))
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("DATABASE_PATH", "rel/base.db")
    decision = lancer.preparer()
    assert decision["base"] == str(tmp_path / "rel" / "base.db")
    from app.config import Settings

    assert Settings().database_file == str(tmp_path / "rel" / "base.db")


def test_a_refused_launch_neither_backs_up_nor_prunes(monkeypatch, tmp_path):
    """C-24 : main() appelait preparer() — donc sauvegarder() et l'élagage à
    deux copies — AVANT de tester le port. Instance vivante en WAL : chaque
    double-clic refusé ajoutait une copie de l'état courant et supprimait la
    plus ancienne ; deux suffisaient à effacer les copies d'avant l'incident."""
    _sans_chemins(monkeypatch)
    monkeypatch.setattr(lancer, "dossiers", lambda: (tmp_path / "ui", tmp_path / "data"))
    appels: list[Path] = []
    monkeypatch.setattr(lancer, "sauvegarder", lambda base: appels.append(base))
    monkeypatch.setattr(lancer, "port_disponible", lambda hote, port: False)
    with pytest.raises(SystemExit) as sortie:
        lancer.main()
    assert sortie.value.code == 2
    assert appels == [], "un lancement refusé a touché aux sauvegardes"


def _main_sans_serveur(monkeypatch, tmp_path, *, port_libre=True, hote=None):
    """main() jusqu'au lancement d'uvicorn, remplacé ; rend (sortie, ouverts, lancé)."""
    import types

    _sans_chemins(monkeypatch)
    if hote is not None:
        monkeypatch.setenv("HOST", hote)
    monkeypatch.setattr(lancer, "dossiers", lambda: (tmp_path / "ui", tmp_path / "data"))
    monkeypatch.setattr(lancer, "port_disponible", lambda hote, port: port_libre)
    ouverts: list[str] = []
    monkeypatch.setattr(lancer.webbrowser, "open", lambda url: ouverts.append(url))
    monkeypatch.setattr(lancer, "ouvrir_quand_pret", lambda adresse: ouverts.append(adresse))
    lance: list[dict] = []
    monkeypatch.setitem(sys.modules, "uvicorn", types.SimpleNamespace(run=lambda *a, **k: lance.append(k)))
    code = None
    try:
        lancer.main()
    except SystemExit as sortie:
        code = sortie.code
    return code, ouverts, lance


def test_an_unreadable_base_is_said_plainly_not_as_a_trace(monkeypatch, tmp_path, capsys):
    """C-47 : sauvegarder() n'interceptait pas sqlite3.DatabaseError. Sous
    Windows, après un arrachage de clé : « file is not a database » dans une
    trace, puis la console se refermait."""
    (tmp_path / "data").mkdir()
    (tmp_path / "data" / "samples.db").write_bytes(b"ceci n'est pas une base" * 50)
    (tmp_path / "data" / "samples.20260920-101010.db.bak").write_bytes(b"x")
    code, _, lance = _main_sans_serveur(monkeypatch, tmp_path)
    sortie = capsys.readouterr().out
    assert code == 3 and lance == []
    assert "illisible" in sortie and "samples.20260920-101010.db.bak" in sortie
    assert "Traceback" not in sortie
    assert sorted(p.name for p in (tmp_path / "data").glob("*.bak")) == ["samples.20260920-101010.db.bak"], \
        "une copie vide de la base illisible a été laissée"


def test_a_second_double_click_opens_the_running_instance(monkeypatch, tmp_path):
    """C-47 : port pris, le lanceur sortait sans rien ouvrir — l'exploitant
    double-cliquait, et rien ne se passait."""
    code, ouverts, _ = _main_sans_serveur(monkeypatch, tmp_path, port_libre=False)
    assert code == 2
    assert ouverts == ["http://127.0.0.1:8000"]


def test_the_configured_host_is_the_one_opened(monkeypatch, tmp_path):
    """C-47 : uvicorn écoutait sur HOST, le navigateur s'ouvrait toujours sur
    127.0.0.1 — ailleurs que le serveur quand HOST désigne une adresse précise."""
    code, ouverts, lance = _main_sans_serveur(monkeypatch, tmp_path, hote="192.168.1.20")
    assert code is None and lance and lance[0]["host"] == "192.168.1.20"
    assert ouverts == ["http://192.168.1.20:8000"]
    _, ouverts, _ = _main_sans_serveur(monkeypatch, tmp_path, hote="0.0.0.0")
    assert ouverts == ["http://127.0.0.1:8000"], "le joker s'ouvre sur la boucle locale"


def test_a_hot_journal_is_rolled_back_before_the_backup(tmp_path):
    """Une clé arrachée en pleine écriture laisse un `-journal` : le fichier
    principal seul est un état non annulé, et c'est lui que `copy2` prenait —
    la copie censée protéger de l'arrachage était celle qui l'avait subi. La
    copie passe par SQLite, qui rejoue le journal, et n'est jamais sautée."""
    import shutil
    import sqlite3

    base = tmp_path / "samples.db"
    with sqlite3.connect(base) as c:
        c.execute("CREATE TABLE t (n INTEGER)")
        c.execute("INSERT INTO t VALUES (1)")
    # Fabriquer un journal chaud : commencer une écriture et copier les fichiers
    # au milieu, comme une clé arrachée les laisserait.
    c = sqlite3.connect(base, isolation_level=None)
    c.execute("PRAGMA cache_size = 1")               # les pages partent sur le disque tout de suite
    c.execute("BEGIN")
    c.executemany("INSERT INTO t VALUES (?)", [(n,) for n in range(2, 20_002)])
    journal = base.with_name("samples.db-journal")
    assert journal.exists(), "le journal DELETE doit exister pendant l'écriture"
    arrache = tmp_path / "arrache"
    arrache.mkdir()
    shutil.copy2(base, arrache / "samples.db")
    shutil.copy2(journal, arrache / "samples.db-journal")
    c.execute("ROLLBACK")
    c.close()

    copie = lancer.sauvegarder(arrache / "samples.db")
    assert copie is not None
    with sqlite3.connect(copie) as v:
        assert v.execute("SELECT COUNT(*) FROM t").fetchone()[0] == 1, "l'écriture interrompue a été annulée"
    assert not copie.with_name(copie.name + "-journal").exists()


def test_le_journal_d_acces_n_ecrit_pas_les_adresses_demandees(monkeypatch, tmp_path):
    """Le journal d'accès d'uvicorn écrivait chaque adresse demandée, dont
    `/api/devices/known/<série>/reconnect` : la série passait à la console."""
    import uvicorn

    appel = {}
    monkeypatch.setattr(lancer, "preparer", lambda sauvegarde=True: {
        "hote": "127.0.0.1", "port": "8000", "base": str(tmp_path / "samples.db"),
        "env": "", "sauvegarde": "",
    })
    monkeypatch.setattr(lancer, "port_disponible", lambda hote, port: True)
    monkeypatch.setattr(lancer, "sauvegarder", lambda base: None)
    monkeypatch.setenv("DATABASE_PATH", str(tmp_path / "samples.db"))
    monkeypatch.setenv("OUVRIR_NAVIGATEUR", "0")
    monkeypatch.setattr(uvicorn, "run", lambda *a, **kw: appel.update(kw))
    lancer.main()
    assert appel["access_log"] is False
