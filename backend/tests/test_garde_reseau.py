"""Le garde réseau de conftest.py (C-46) — éprouvé, pour qu'il ne soit pas vide."""

from __future__ import annotations

import os
import socket

import pytest


def test_une_connexion_tcp_hors_de_la_boucle_locale_echoue_tout_de_suite():
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        with pytest.raises(ConnectionRefusedError, match="réseau interdit"):
            s.connect(("203.0.113.7", 443))


def test_la_boucle_locale_reste_permise():
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as serveur:
        serveur.bind(("127.0.0.1", 0))
        serveur.listen(1)
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as client:
            client.connect(serveur.getsockname())


def test_aucun_mandataire_ne_contourne_le_garde():
    assert not any(os.environ.get(v) for v in ("HTTPS_PROXY", "HTTP_PROXY", "https_proxy", "http_proxy"))
