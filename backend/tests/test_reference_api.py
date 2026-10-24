"""La table « API » de docs/REFERENCE.md suit les routes réelles.

Elle listait 9 routes quand le superviseur en sert une vingtaine : les
sessions, leurs exports, la frise, les événements, le réappairage, la capture
brute n'y figuraient pas. Comparée ici aux routes de `app.main` — et celle du
témoin Ethernet aux routes de `temoin.api` —, elle ne peut plus redevenir
fausse sans qu'un test tombe.

Les noms des paramètres de chemin ne sont pas comparés (`{id}` dans la doc,
`{router_id}` dans le code) : seule la forme de la route compte.
"""

from __future__ import annotations

import re
from pathlib import Path

from fastapi.routing import APIRoute, APIWebSocketRoute

REFERENCE = (Path(__file__).resolve().parents[2] / "docs" / "REFERENCE.md").read_text(
    encoding="utf-8")


def _forme(chemin: str) -> str:
    return re.sub(r"\{[^}]*\}", "{}", chemin)


def _section(titre: str) -> str:
    debut = REFERENCE.index(f"\n{titre}\n")
    niveau = titre.split(" ", 1)[0]
    suite = re.search(rf"\n#{{1,{len(niveau)}}} ", REFERENCE[debut + len(titre) + 1:])
    return REFERENCE[debut: debut + len(titre) + 1 + suite.start() if suite else None]


def _table(texte: str) -> set[tuple[str, str]]:
    """(méthode, forme) de chaque ligne `| `GET` | `/api/…` | … |`."""
    lignes = re.findall(r"^\|\s*`(GET|POST|DELETE|PUT|PATCH|WS)`\s*\|\s*`([^`]+)`\s*\|",
                        texte, re.M)
    vues = [(m, _forme(c)) for m, c in lignes]
    assert len(vues) == len(set(vues)), "une route listée deux fois"
    return set(vues)


def _routes(app) -> set[tuple[str, str]]:
    """Toutes les routes servies, routeurs inclus compris — sans la
    documentation interactive (/docs, /openapi.json) ni l'interface montée."""
    vues: set[tuple[str, str]] = set()

    def parcourir(routes, prefixe: str = "") -> None:
        for r in routes:
            if isinstance(r, APIWebSocketRoute):
                vues.add(("WS", _forme(prefixe + r.path)))
            elif isinstance(r, APIRoute):
                for methode in r.methods - {"HEAD"}:
                    vues.add((methode, _forme(prefixe + r.path)))
            elif hasattr(r, "original_router"):
                # FastAPI récent : un `include_router` reste un routeur inclus.
                parcourir(r.original_router.routes, prefixe)

    parcourir(app.routes)
    # Contre-vérification : l'OpenAPI (préfixes résolus) voit les mêmes routes HTTP.
    openapi = {(m.upper(), _forme(c)) for c, ops in app.openapi()["paths"].items() for m in ops}
    assert openapi <= vues, openapi - vues
    return vues


def test_la_table_api_liste_toutes_les_routes_du_superviseur():
    from app.main import app

    servies = _routes(app)
    documentees = _table(_section("## API"))
    assert not servies - documentees, f"routes absentes de REFERENCE : {sorted(servies - documentees)}"
    assert not documentees - servies, f"routes qui n'existent plus : {sorted(documentees - servies)}"


def test_la_table_du_temoin_liste_toutes_ses_routes(tmp_path):
    from temoin.api import creer_app

    # La construction de l'application ne touche pas au dépôt : un objet vide
    # suffit pour en lire les routes.
    app = creer_app(object(), interface=tmp_path / "absent")
    servies = _routes(app)
    documentees = _table(_section("## Témoin de lien Ethernet"))
    assert servies == documentees, (sorted(servies - documentees), sorted(documentees - servies))


def test_la_section_du_temoin_dit_l_essentiel():
    texte = _section("## Témoin de lien Ethernet")
    for attendu in ("8010", "127.0.0.1", "data/temoin.db", "temoin.db.verrou", "PORT",
                    "coupures.csv", "releves.csv"):
        assert attendu in texte, attendu
