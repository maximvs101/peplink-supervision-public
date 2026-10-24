"""Le fichier des mentions de licences tierces (`tools/licences_tierces.py`).

Les exécutables embarquent du code sous MIT, BSD, Apache, PSF, OFL, MPL, dont
les textes doivent les accompagner. Ce qui se vérifie ici : la fermeture des
dépendances d'EXÉCUTION (ni tests, ni construction), la liste blanche qui
arrête une GPL entrée par une montée de version, une sortie déterministe, et
qu'aucun texte de licence n'est inventé quand le paquet n'en distribue pas.
"""

from __future__ import annotations

import json
import random
import re
import sys
from pathlib import Path

import pytest

RACINE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(RACINE / "tools"))

import licences_tierces as lt

FRONTEND = RACINE.parent / "frontend"
_MIT = "MIT License\n\nCopyright (c) 2020 Auteur Fictif\n\nPermission is hereby granted, free of charge…\n"


def _paquet(site: Path, nom: str, version: str = "1.0", requires: tuple[str, ...] = (),
            expression: str | None = "MIT", licence: str | None = None,
            classificateurs: tuple[str, ...] = (), fichiers: dict[str, str] | None = None) -> None:
    """Un paquet installé factice : un dist-info avec METADATA et RECORD."""
    info = site / f"{nom.replace('-', '_')}-{version}.dist-info"
    info.mkdir(parents=True)
    lignes = ["Metadata-Version: 2.4", f"Name: {nom}", f"Version: {version}"]
    if expression:
        lignes.append(f"License-Expression: {expression}")
    if licence:
        lignes.append(f"License: {licence}")
    lignes += [f"Classifier: {c}" for c in classificateurs]
    lignes += [f"Requires-Dist: {r}" for r in requires]
    lignes.append(f"Project-URL: Source, https://exemple.invalid/{nom}")
    (info / "METADATA").write_text("\n".join(lignes) + "\n", encoding="utf-8")
    fichiers = {"licenses/LICENSE": _MIT} if fichiers is None else fichiers
    record = [f"{info.name}/METADATA,,", f"{info.name}/RECORD,,"]
    for chemin, texte in fichiers.items():
        (info / chemin).parent.mkdir(parents=True, exist_ok=True)
        (info / chemin).write_text(texte, encoding="utf-8")
        record.append(f"{info.name}/{chemin},,")
    (info / "RECORD").write_text("\n".join(record) + "\n", encoding="utf-8")


_ENV = {**lt._environnement(), "sys_platform": "darwin", "python_version": "3.12",
        "python_full_version": "3.12.7"}


# --- Marqueurs ----------------------------------------------------------------

@pytest.mark.parametrize(("marqueur", "attendu"), [
    ('sys_platform == "win32"', False),
    ("sys_platform == 'darwin'", True),
    ('python_version < "3.12"', False),
    ('python_version < "3.13"', True),
    ("python_version >= '3.9'", True),
    ('python_full_version ~= "3.12.0"', True),
    ('extra == "test"', False),
    ('sys_platform != "cygwin" and extra == "check"', False),
    ("(sys_platform != 'win32' and (sys_platform != 'cygwin' and platform_python_implementation != 'PyPy')) and extra == 'standard'", False),
    ('sys_platform == "linux" or python_version >= "3.10"', True),
    ('"arwi" in sys_platform', True),
    ('"arwi" not in sys_platform', False),
])
def test_marqueurs(marqueur, attendu):
    assert lt.evaluer_marqueur(marqueur, env=_ENV) is attendu


def test_un_marqueur_incompris_arrete_au_lieu_d_ecarter():
    with pytest.raises(ValueError):
        lt.evaluer_marqueur('variable_inconnue == "x"', env=_ENV)


# --- Fermeture ----------------------------------------------------------------

def test_fermeture_suit_les_exigences_les_extras_demandes_et_les_marqueurs(tmp_path):
    _paquet(tmp_path, "racine", requires=("dep-a>=1", "dep-b[rapide]; python_version >= '3.8'",
                                          "windows-seul; sys_platform == 'win32'",
                                          "outil-test; extra == 'test'"))
    _paquet(tmp_path, "dep-a")
    _paquet(tmp_path, "dep-b", requires=("acceleration; extra == 'rapide'", "lent; extra == 'autre'"))
    _paquet(tmp_path, "acceleration")
    for nom in ("windows-seul", "outil-test", "lent", "intrus"):
        _paquet(tmp_path, nom)
    fermeture = lt.fermeture_python(["racine"], [str(tmp_path)], _ENV)
    assert sorted(fermeture) == ["acceleration", "dep-a", "dep-b", "racine"]


def test_une_dependance_requise_mais_absente_arrete(tmp_path):
    _paquet(tmp_path, "racine", requires=("fantome",))
    with pytest.raises(lt.LicencesRefusees, match="fantome"):
        lt.fermeture_python(["racine"], [str(tmp_path)], _ENV)


def test_fermeture_reelle_egale_requirements_txt_sans_outils_de_test_ni_de_construction():
    """Toutes les dépendances indirectes sont épinglées dans requirements.txt :
    la fermeture calculée doit le retrouver EXACTEMENT, à la dépendance propre
    à Windows près (pywin32-ctypes, tirée par keyring, non épinglée)."""
    racines = lt.exigences(RACINE / "requirements.txt")
    fermeture = set(lt.fermeture_python(racines))
    attendues = {lt.normaliser(n) for n in racines}
    assert attendues <= fermeture
    assert fermeture - attendues <= ({"pywin32-ctypes"} if sys.platform == "win32" else set())
    for outil in ("pytest", "ruff", "httpx2", "pip-audit", "pyinstaller", "coverage", "packaging", "setuptools"):
        assert outil not in fermeture


# --- Liste blanche -----------------------------------------------------------

@pytest.mark.parametrize(("champs", "fautif"), [
    ({"expression": "GPL-3.0-only"}, "GPL-3.0-only"),
    ({"expression": "LGPL-2.1-or-later"}, "LGPL-2.1-or-later"),
    ({"expression": "MIT OR AGPL-3.0-only"}, "AGPL-3.0-only"),
    ({"expression": None, "classificateurs": ("License :: OSI Approved :: GNU General Public License v3 (GPLv3)",)}, "GPLv3"),
    ({"expression": None}, "licence inconnue"),
    ({"expression": None, "classificateurs": ("License :: OSI Approved :: BSD License",)}, "BSD-License"),
])
def test_une_licence_hors_liste_blanche_arrete_la_generation(tmp_path, champs, fautif):
    """Une montée de Dependabot qui tire une GPL, ou une licence que les
    métadonnées ne disent pas, n'entre pas en silence."""
    site = tmp_path / "site"
    _paquet(site, "racine", requires=("copyleft",))
    _paquet(site, "copyleft", **champs)
    exigences = tmp_path / "requirements.txt"
    exigences.write_text("racine==1.0\n", encoding="utf-8")
    with pytest.raises(lt.LicencesRefusees, match=re.escape(fautif)) as e:
        lt.generer(interface=False, requirements=exigences, chemins=[str(site)])
    assert "copyleft" in str(e.value)


def test_licences_reconnues_par_les_trois_voies(tmp_path):
    _paquet(tmp_path, "par-expression", expression="BSD-3-Clause")
    _paquet(tmp_path, "par-champ-libre", expression=None, licence="MIT License")
    _paquet(tmp_path, "par-classificateur", expression=None,
            classificateurs=("License :: OSI Approved :: Mozilla Public License 2.0 (MPL 2.0)",))
    index = lt._index([str(tmp_path)])
    assert lt.licence_python(index["par-expression"]) == "BSD-3-Clause"
    assert lt.licence_python(index["par-champ-libre"]) == "MIT"
    assert lt.licence_python(index["par-classificateur"]) == "MPL-2.0"


def test_le_chargeur_de_pyinstaller_est_mentionne_par_courtoisie_hors_liste_blanche():
    chargeur = lt.composant_pyinstaller()
    assert chargeur.courtoisie and "GPL" in chargeur.licence
    lt.verifier([chargeur])  # ne lève pas
    assert "courtoisie" in chargeur.note and "rien d'obligatoire" in chargeur.note


# --- Aucun texte inventé -----------------------------------------------------

def test_un_texte_absent_est_signale_jamais_reconstitue(tmp_path):
    site = tmp_path / "site"
    _paquet(site, "racine", requires=("sans-texte",))
    _paquet(site, "sans-texte", fichiers={})
    exigences = tmp_path / "requirements.txt"
    exigences.write_text("racine==1.0\n", encoding="utf-8")
    contenu, manquants = lt.generer(interface=False, requirements=exigences, chemins=[str(site)])
    assert "sans-texte 1.0" in manquants and "racine 1.0" not in manquants
    bloc = contenu.split("\nsans-texte 1.0\n", 1)[1].split("-" * 78, 1)[0]
    assert "non distribué avec le paquet, voir https://exemple.invalid/sans-texte" in bloc
    assert "Permission is hereby granted" not in bloc
    # Le texte du paquet qui en distribue un est repris tel quel.
    assert "Auteur Fictif" in contenu.split("\nracine 1.0\n", 1)[1]


def test_licence_et_notice_reprises_et_dedoublonnees(tmp_path):
    _paquet(tmp_path, "apache", expression="Apache-2.0",
            fichiers={"licenses/LICENSE": "Apache License\nVersion 2.0\n",
                      "LICENSE": "Apache License\nVersion 2.0\n",
                      "licenses/NOTICE": "Ce produit contient du code de X.\n",
                      "licenses/AUTHORS": "Des noms\n"})
    textes = lt.textes_python(lt._index([str(tmp_path)])["apache"])
    assert [t for t, _ in textes] == ["apache-1.0.dist-info/LICENSE", "apache-1.0.dist-info/licenses/NOTICE"]


def test_cpython_texte_repris_de_l_installation():
    cpython = lt.composant_cpython()
    assert cpython.licence == "Python-2.0"
    assert cpython.textes, "LICENSE.txt de l'installation introuvable"
    assert "PYTHON SOFTWARE FOUNDATION LICENSE VERSION 2" in cpython.textes[0][1]


# --- Interface : le verrou npm -----------------------------------------------

def _npm(frontend: Path, chemin: str, version: str, licence: str, entree: dict,
         fichiers: dict[str, str] | None = None) -> tuple[str, dict]:
    dossier = frontend / chemin
    dossier.mkdir(parents=True)
    nom = chemin.rsplit("node_modules/", 1)[-1]
    (dossier / "package.json").write_text(json.dumps({"name": nom, "version": version, "license": licence,
                                                      "repository": f"github:exemple/{nom}"}), encoding="utf-8")
    for f, t in ({"LICENSE": _MIT} if fichiers is None else fichiers).items():
        (dossier / f).write_text(t, encoding="utf-8")
    return chemin, {"version": version, "license": licence, **entree}


def _frontend(tmp_path: Path, en_plus: tuple = ()) -> Path:
    frontend = tmp_path / "frontend"
    paquets = dict([
        _npm(frontend, "node_modules/react", "19.0.0", "MIT", {}),
        _npm(frontend, "node_modules/@fontsource/une-police", "5.0.0", "OFL-1.1", {}, {"LICENSE": "SIL Open Font License 1.1\n"}),
        _npm(frontend, "node_modules/lib/node_modules/imbriquee", "2.0.0", "ISC", {}),
        _npm(frontend, "node_modules/sans-licence", "1.0.0", "MIT", {}, {}),
        _npm(frontend, "node_modules/outil-de-dev", "1.0.0", "GPL-3.0-only", {"dev": True}),
        _npm(frontend, "node_modules/rolldown", "1.0.0", "MIT", {"dev": True}),
        _npm(frontend, "node_modules/vite", "8.0.0", "MIT", {"dev": True},
             {"LICENSE.md": "# Vite core license\nMIT…\n\n# Licenses of bundled dependencies\nGPL-pas-pour-nous\n"}),
        *en_plus,
    ])
    paquets["node_modules/autre-systeme"] = {"version": "1.0.0", "license": "MIT", "optional": True}
    (frontend / "package-lock.json").write_text(
        json.dumps({"lockfileVersion": 3, "packages": {"": {"name": "app"}, **paquets}}), encoding="utf-8")
    return frontend


def test_interface_dependances_de_production_et_code_inline(tmp_path):
    composants = {c.nom: c for c in lt.composants_interface(_frontend(tmp_path))}
    # Ni l'outil de dev (même GPL), ni l'optionnelle d'un autre système.
    assert sorted(composants) == ["@fontsource/une-police", "imbriquee", "react", "rolldown",
                                  "sans-licence", "vite"]
    assert composants["@fontsource/une-police"].licence == "OFL-1.1"
    assert composants["react"].source == "https://github.com/exemple/react"
    assert not composants["sans-licence"].textes
    # De Vite, sa seule licence : pas celles des dépendances de sa ligne de commande.
    (_titre, texte), = composants["vite"].textes
    assert "Vite core license" in texte and "GPL-pas-pour-nous" not in texte
    lt.verifier(list(composants.values()))


def test_interface_une_gpl_de_production_arrete(tmp_path):
    frontend = _frontend(tmp_path / "x", (
        ("node_modules/copyleft", {"version": "1.0.0", "license": "GPL-3.0-only"}),))
    _npm(frontend, "node_modules/copyleft", "1.0.0", "GPL-3.0-only", {})
    with pytest.raises(lt.LicencesRefusees, match="copyleft"):
        lt.verifier(lt.composants_interface(frontend))


def test_interface_sans_node_modules_arrete(tmp_path):
    frontend = tmp_path / "frontend"
    frontend.mkdir()
    (frontend / "package-lock.json").write_text('{"lockfileVersion": 3, "packages": {}}', encoding="utf-8")
    with pytest.raises(lt.LicencesRefusees, match="npm ci"):
        lt.composants_interface(frontend)


# --- Déterminisme ------------------------------------------------------------

def test_sortie_deterministe_sans_date(tmp_path, monkeypatch):
    """Deux générations donnent le même octet, quel que soit l'ordre dans lequel
    le système liste les paquets : un diff entre deux constructions a un sens."""
    interface = (FRONTEND / "node_modules").is_dir()
    premier, _ = lt.generer(interface=interface)
    vraies = lt.metadata.distributions

    def melangees(**kw):
        liste = list(vraies(**kw))
        random.Random(7).shuffle(liste)
        return iter(liste)

    monkeypatch.setattr(lt.metadata, "distributions", melangees)
    second, _ = lt.generer(interface=interface)
    assert premier == second
    from datetime import date
    entete = premier.split("Résumé", 1)[0]
    assert date.today().isoformat() not in entete and str(date.today().year) not in entete
    sortie = tmp_path / lt.NOM
    monkeypatch.setattr(lt.metadata, "distributions", vraies)
    lt.ecrire(sortie, interface=interface)
    assert b"\r\n" not in sortie.read_bytes()
    assert sortie.read_text(encoding="utf-8") == premier


def test_le_fichier_reel_couvre_requirements_et_le_package_json():
    if not (FRONTEND / "node_modules").is_dir():
        pytest.skip("frontend/node_modules absent (`npm ci`)")
    contenu, _ = lt.generer(interface=True)
    resume = contenu.split("Résumé", 1)[1].split("=" * 78, 1)[0]
    for nom in lt.exigences(RACINE / "requirements.txt"):
        assert re.search(rf"^  {re.escape(nom)} ", resume, re.M | re.I), nom
    paquet = json.loads((FRONTEND / "package.json").read_text(encoding="utf-8"))
    for nom in paquet["dependencies"]:
        assert re.search(rf"^  {re.escape(nom)} ", resume, re.M), nom
    # Rien de ce que le verrou ne tient QUE pour le développement (`dev`), hors
    # le code que la compilation inline. `@types/react` y est `devOptional` :
    # aussi tiré par la production, `npm ls --omit=dev` le liste — lui aussi.
    # Un même nom peut y être deux fois, à deux versions (react-is) : la paire compte.
    verrou = json.loads((FRONTEND / "package-lock.json").read_text(encoding="utf-8"))
    paires = [((c.rsplit("node_modules/", 1)[-1], e.get("version")), bool(e.get("dev")))
              for c, e in verrou["packages"].items() if c]
    production = {p for p, dev in paires if not dev}
    for (nom, version), dev in paires:
        if dev and (nom, version) not in production and nom not in lt._INLINES:
            assert f"\n  {nom} {version} —" not in resume, nom
    assert "PSF" in contenu and "CPython" in resume and "PyInstaller" in resume


# --- Construction : le fichier voyage avec chaque exécutable ----------------

import construire_executable as ce


def _add_data(commande: list[str]) -> list[str]:
    return [commande[i + 1] for i, a in enumerate(commande) if a == "--add-data"]


def test_le_superviseur_embarque_le_fichier_a_la_racine_de_l_interface(tmp_path, monkeypatch):
    """Servi par l'exécutable sous /LICENCES-TIERCES.txt, à côté d'index.html."""
    dist = tmp_path / "dist"
    dist.mkdir()
    (dist / "index.html").write_text("<!doctype html>", encoding="utf-8")
    monkeypatch.setattr(ce, "DIST", dist)
    fichier = tmp_path / lt.NOM
    assert f"{fichier}{ce.SEPARATEUR}frontend/dist" in _add_data(ce.superviseur(fichier))


def test_le_temoin_embarque_le_fichier_avec_son_ecran(tmp_path):
    """Servi par le témoin sous /LICENCES-TIERCES.txt : son écran est monté à la racine."""
    fichier = tmp_path / lt.NOM
    assert f"{fichier}{ce.SEPARATEUR}temoin/ecran" in _add_data(ce.temoin(fichier))


def _sans_pyinstaller(monkeypatch, tmp_path) -> dict:
    """La construction jusqu'à PyInstaller, exclu : ce qui existe quand il serait lancé."""
    vu: dict = {}
    monkeypatch.setattr(ce, "RACINE", tmp_path)
    dist = tmp_path / "frontend-dist"
    dist.mkdir()
    (dist / "index.html").write_text("<!doctype html>", encoding="utf-8")
    monkeypatch.setattr(ce, "DIST", dist)

    def appel(commande, cwd=None):
        licences = [Path(d.rsplit(ce.SEPARATEUR, 1)[0]) for d in _add_data(commande) if lt.NOM in d]
        vu["contenu"] = licences[0].read_text(encoding="utf-8")
        return 0

    monkeypatch.setattr(ce.subprocess, "call", appel)
    return vu


def test_construire_genere_le_fichier_avant_pyinstaller_et_le_pose_dans_dist(tmp_path, monkeypatch):
    if not (FRONTEND / "node_modules").is_dir():
        pytest.skip("frontend/node_modules absent (`npm ci`)")
    vu = _sans_pyinstaller(monkeypatch, tmp_path)
    assert ce.main([]) == 0
    assert "Produit : superviseur" in vu["contenu"] and "\n  react " in vu["contenu"]
    # La Release le prend là, à côté de l'exécutable.
    assert (tmp_path / "dist" / lt.NOM).read_text(encoding="utf-8") == vu["contenu"]


def test_le_temoin_n_annonce_pas_l_interface_qu_il_n_embarque_pas(tmp_path, monkeypatch):
    vu = _sans_pyinstaller(monkeypatch, tmp_path)
    assert ce.main(["--produit", "temoin"]) == 0
    assert "Produit : témoin Ethernet" in vu["contenu"]
    assert "Interface web" not in vu["contenu"] and "\n  fastapi " in vu["contenu"]
    # Le fichier de dist/ reste celui du superviseur, qui couvre les deux.
    assert not (tmp_path / "dist" / lt.NOM).exists()


def test_une_licence_refusee_arrete_la_construction_avant_pyinstaller(tmp_path, monkeypatch):
    _sans_pyinstaller(monkeypatch, tmp_path)

    def refus(*a, **kw):
        raise lt.LicencesRefusees("copyleft 1.0 : GPL-3.0-only hors liste blanche")

    monkeypatch.setattr(lt, "generer", refus)
    monkeypatch.setattr(ce.subprocess, "call", lambda *a, **kw: pytest.fail("PyInstaller lancé"))
    assert ce.main(["--produit", "temoin"]) == 1
