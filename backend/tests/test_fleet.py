"""C-15 : un routers.yaml maladroit ne livre pas le mot de passe, et ne
coupe pas le démarrage.

YAML lit un mot de passe fait de chiffres, sans guillemets, comme un ENTIER.
Pydantic le refusait, et son message portait `input_value` — le mot de passe
en clair dans la console —, puis l'exception remontait du lifespan et le
serveur ne démarrait plus.
"""

from __future__ import annotations

import logging

from app.fleet import load_fleet

# Valeurs fictives.
_YAML = """
routers:
  - id: dome
    name: Dome
    host: 192.0.2.10
    username: admin
    password: 12345678
  - id: hd2
    name: HD2
    host: 192.0.2.20
    client_id: 4242
    client_secret: 98765432
"""


def test_un_identifiant_numerique_est_lu_comme_une_chaine(tmp_path):
    chemin = tmp_path / "routers.yaml"
    chemin.write_text(_YAML, encoding="utf-8")
    flotte = load_fleet(str(chemin), "router")
    assert [r.id for r in flotte] == ["dome", "hd2"]
    assert flotte[0].password == "12345678"
    assert (flotte[1].client_id, flotte[1].client_secret) == ("4242", "98765432")


def test_une_entree_invalide_est_ecartee_sans_rien_divulguer(tmp_path, caplog):
    chemin = tmp_path / "routers.yaml"
    chemin.write_text(
        "routers:\n"
        "  - name: sans identifiant\n    password: s3cret-fictif\n"
        "  - id: dome\n    name: Dome\n    password: autre-fictif\n",
        encoding="utf-8",
    )
    with caplog.at_level(logging.WARNING):
        flotte = load_fleet(str(chemin), "router")
    assert [r.id for r in flotte] == ["dome"], "l'entrée valide doit survivre à sa voisine"
    assert "s3cret-fictif" not in caplog.text, "le message d'erreur a livré le mot de passe"
    assert "routers.yaml" in caplog.text or "entrée" in caplog.text
