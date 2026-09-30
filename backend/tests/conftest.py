"""Ce que chaque test de ce dépôt reçoit sans le demander."""

from __future__ import annotations

import ipaddress
import os
import socket
import tempfile
import weakref

import aiosqlite
import pytest

# Les clients de test se présentent sous des noms d'hôte fictifs — `testserver`
# pour `TestClient`, les `base_url` des transports ASGI. La garde du DNS
# rebinding (C-3) les refuserait : ils sont déclarés ici, comme l'exploitant
# déclarerait le nom de son poste. Un nom rebasculé, lui, reste refusé.
os.environ.setdefault("ALLOWED_HOSTS", "testserver,test,t,rejeu")

# Jamais le `.env` du poste : sur une machine d'exploitation, il désigne la base
# RÉELLE (`DATABASE_PATH=./data/samples.db`), et tout test qui importe
# `app.main` la lisait comme configuration. Aucun n'y écrit aujourd'hui ; le
# premier qui démarrerait l'application le ferait sur les mesures de terrain.
# Les tests du lanceur posent leur propre fichier. Et la valeur PAR DÉFAUT du
# chemin de base est la même `data/samples.db` : écarter le `.env` n'y suffit pas.
os.environ.setdefault("ENV_FILE", os.devnull)
os.environ.setdefault(
    "DATABASE_PATH", os.path.join(tempfile.mkdtemp(prefix="peplink-tests-"), "samples.db")
)

# Chaque connexion s'inscrit à sa naissance ; les références sont faibles, donc
# une connexion fermée et oubliée disparaît d'elle-même. Un parcours de
# `gc.get_objects()` faisait le même travail sans rien patcher — mesuré, il
# doublait la durée de la suite (13,8 s → 24,8 s).
_ouvertes: weakref.WeakSet[aiosqlite.Connection] = weakref.WeakSet()
_init_origine = aiosqlite.Connection.__init__


def _init_inscrit(self: aiosqlite.Connection, *args: object, **kwargs: object) -> None:
    _init_origine(self, *args, **kwargs)
    _ouvertes.add(self)


aiosqlite.Connection.__init__ = _init_inscrit  # type: ignore[method-assign]


@pytest.fixture(autouse=True)
async def _aucune_connexion_orpheline():
    """Ferme les connexions `aiosqlite` qu'un test a laissées ouvertes.

    Le thread de travail d'`aiosqlite` n'est pas un daemon : tant qu'une
    connexion ouverte est référencée, l'interpréteur ne peut pas sortir. Sous
    pytest, la référence qui survit est la TRACE d'un test tombé — elle garde le
    cadre, le cadre garde le dépôt, le dépôt garde la connexion. C'est le
    déclencheur des « zombies pytest », resté inconnu depuis le 30 août et
    reproduit à la demande le 19 septembre : un test qui échoue entre un
    `connect()` nu et son `close()` laisse le processus vivant, muet, après
    le rapport. Trente-cinq tests de stockage ouvrent ainsi.

    Cette fixture ne change rien à un test vert. Elle fait qu'un test rouge
    reste un test rouge — pas un processus à tuer à la main.
    """
    yield
    for connexion in list(_ouvertes):
        if connexion._running:
            try:
                await connexion.close()
            except Exception:
                pass


# --- aucun réseau réel pendant les tests (C-46) ---------------------------------
#
# test_discovery sondait 203.0.113.7 sans moque : une vraie tentative TCP à
# chaque passage, dont l'issue dépendait du réseau de la machine — hors ligne,
# derrière un proxy, ou sur un réseau qui route cette plage. Toute connexion
# TCP hors de la boucle locale échoue désormais tout de suite, comme un hôte
# qui refuse : le résultat ne dépend plus de la machine. Un `connect` UDP, qui
# n'envoie aucun paquet et ne fait que choisir une route, reste permis.

_connect_origine = socket.socket.connect
_connect_ex_origine = socket.socket.connect_ex


def _hote_local(adresse) -> bool:
    """La boucle locale, le joker, ou une adresse de CETTE machine — que l'on
    reconnaît à ce qu'on peut s'y lier. Le lanceur sonde l'adresse du poste sur
    son réseau pour savoir si le port y écoute (sous Windows) : c'est la
    machine elle-même, pas le réseau."""
    try:
        hote = adresse[0]
    except (TypeError, IndexError):
        return False
    if hote == "localhost":
        return True
    try:
        ip = ipaddress.ip_address(hote)
    except ValueError:
        return False
    if ip.is_loopback or ip.is_unspecified:
        return True
    famille = socket.AF_INET6 if ip.version == 6 else socket.AF_INET
    try:
        with socket.socket(famille, socket.SOCK_DGRAM) as essai:
            essai.bind((hote, 0))
        return True
    except OSError:
        return False


def _garde(sock: socket.socket, adresse) -> None:
    if sock.family in (socket.AF_INET, socket.AF_INET6) and sock.type == socket.SOCK_STREAM \
            and not _hote_local(adresse):
        raise ConnectionRefusedError(f"réseau interdit pendant les tests : {adresse!r}")


def _connect(self, adresse):
    _garde(self, adresse)
    return _connect_origine(self, adresse)


def _connect_ex(self, adresse):
    _garde(self, adresse)
    return _connect_ex_origine(self, adresse)


@pytest.fixture(autouse=True)
def _aucun_reseau(monkeypatch):
    monkeypatch.setattr(socket.socket, "connect", _connect)
    monkeypatch.setattr(socket.socket, "connect_ex", _connect_ex)
    # Un mandataire sur la boucle locale ouvrirait la porte que ce garde ferme :
    # httpx lit ces variables et joindrait le proxy, qui joindrait l'hôte.
    for variable in ("HTTPS_PROXY", "HTTP_PROXY", "ALL_PROXY",
                     "https_proxy", "http_proxy", "all_proxy"):
        monkeypatch.delenv(variable, raising=False)
