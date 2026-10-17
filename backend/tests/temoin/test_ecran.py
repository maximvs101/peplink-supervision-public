"""L'écran du témoin : servi par `creer_app`, autonome, en français."""

import re
from pathlib import Path

import httpx
import pytest

from temoin.api import creer_app
from temoin.stockage import Depot

ECRAN = Path(__file__).resolve().parents[2] / "temoin" / "ecran"
FICHIERS = ["index.html", "temoin.js", "temoin.css", "rapport.html"]


async def _pas_de_routeur(*, depot=None):
    return []


async def _jamais(*args, **kw):
    raise AssertionError("aucune connexion attendue")


@pytest.fixture
async def ecran(tmp_path):
    depot = Depot(str(tmp_path / "temoin.db"))
    await depot.connect()
    app = creer_app(depot, interface=ECRAN, chercher=_pas_de_routeur, connecter=_jamais)
    async with app.router.lifespan_context(app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://127.0.0.1:8765") as c:
            yield c
    await depot.close()


def _texte(nom: str) -> str:
    return (ECRAN / nom).read_text("utf-8")


async def test_la_page_et_ses_fichiers_sont_servis(ecran):
    racine = await ecran.get("/")
    assert racine.status_code == 200 and "Témoin" in racine.text
    for nom in FICHIERS:
        r = await ecran.get(f"/{nom}")
        assert r.status_code == 200, nom
        assert r.text == _texte(nom)
    assert "javascript" in (await ecran.get("/temoin.js")).headers["content-type"]
    assert (await ecran.get("/temoin.css")).headers["content-type"].startswith("text/css")
    # Les deux pages chargent le même script et la même feuille.
    for page in ("index.html", "rapport.html"):
        assert 'src="temoin.js"' in _texte(page) and 'href="temoin.css"' in _texte(page)


def test_l_ecran_ne_charge_rien_de_l_exterieur():
    # L'outil sert quand la liaison est coupée : ni police, ni script, ni image
    # d'un tiers. Le SVG se construit sans espace de noms écrit en dur.
    interdit = re.compile(r"(?i)https?:|@import|(src|href)\s*=\s*[\"']//|url\(\s*[\"']?//")
    for nom in FICHIERS:
        trouve = interdit.findall(_texte(nom))
        assert not trouve, f"{nom} : {trouve}"
    # Le script ne parle qu'à l'API du poste.
    for appel in re.findall(r"fetch\(([^)]*)\)", _texte("temoin.js")):
        assert "http" not in appel


def test_les_libelles_sont_en_francais():
    tout = "\n".join(_texte(nom) for nom in FICHIERS)
    for libelle in (
        "Lien stable", "Exporter les coupures", "Exporter les relevés", "Rapport",
        "Nouvelle observation", "non mesuré", "en cours", "Retenir sur ce poste",
        "en clair", "Mesurer ce lien", "lecture seule", "Imprimer",
        "Horloges du routeur et du poste en désaccord",
        "Lien introuvable sur le routeur", "fin non observée", "au moins",
    ):
        assert libelle in tout, libelle
    for page in ("index.html", "rapport.html"):
        assert '<html lang="fr">' in _texte(page)


def test_les_nombres_et_les_themes_suivent_le_poste():
    js, css = _texte("temoin.js"), _texte("temoin.css")
    assert "toLocaleString('fr-FR'" in js
    assert "prefers-color-scheme" in css and "@media print" in css
    # La mesure n'interroge pas un onglet caché.
    assert "document.hidden" in js and "visibilitychange" in js
    # Jamais de boîte de dialogue du navigateur pour confirmer.
    assert "confirm(" not in js


def test_l_avertissement_en_clair_ne_vaut_que_pour_http():
    # Sans schéma, le client parle HTTPS : l'avertissement est caché par défaut
    # et le script ne le montre que pour un hôte « http://… ».
    html, js = _texte("index.html"), _texte("temoin.js")
    assert re.search(r'<p id="ident-clair"[^>]*\bhidden\b', html)
    assert "schema[0] === 'http'" in js and "$('ident-clair').hidden" in js
