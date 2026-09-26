"""C-25 : les scripts de capture Windows parlent au firmware comme le client.

Le client consigne, mesures à l'appui (router_api.py, _auth_client_credentials
et _request), que le firmware n'accepte que `clientId` / `clientSecret`, sans
`grant_type`, et le jeton en paramètre `accessToken`. Les deux scripts
envoyaient `client_id`, `client_secret`, `grant_type` puis `?access_token=` :
un technicien qui suivait la documentation archivait des « Client not found »
à la place des captures. Et le .cmd prenait le mot de passe sur la ligne de
commande, visible dans l'historique et la liste des processus.

Rien de tout ceci n'est testé contre un routeur — c'est interdit ici : on
vérifie le texte des scripts contre le contrat mesuré du client.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

OUTILS = Path(__file__).resolve().parents[1] / "tools"
SCRIPTS = [OUTILS / "capture_router_api.cmd", OUTILS / "capture_router_api.ps1"]


@pytest.fixture(params=SCRIPTS, ids=lambda p: p.suffix)
def script(request) -> str:
    return request.param.read_text(encoding="utf-8")


def test_les_noms_de_champs_sont_ceux_que_le_firmware_accepte(script):
    assert "clientId" in script and "clientSecret" in script
    assert "grant_type" not in script
    assert not re.search(r"client_id\s*=|client_secret\s*=", script)


def test_le_jeton_voyage_en_accessToken(script):
    assert "accessToken=" in script
    assert "?access_token=" not in script


def test_aucun_secret_sur_la_ligne_de_commande_du_cmd():
    cmd = SCRIPTS[0].read_text(encoding="utf-8")
    # Le mot de passe et le secret du client ne viennent plus des arguments.
    assert "%~3\\\"}" not in cmd and '"password\\":\\"%~3' not in cmd
    assert "%~4" not in cmd
    # Ils sont demandés, sans écho.
    assert "AsSecureString" in cmd


def test_aucun_secret_en_parametre_texte_du_ps1():
    ps1 = SCRIPTS[1].read_text(encoding="utf-8")
    assert "[string]$Password" not in ps1 and "[string]$ClientSecret" not in ps1
    assert "Read-Host" in ps1 and "AsSecureString" in ps1
