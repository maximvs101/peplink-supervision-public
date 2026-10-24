"""C-48 : le jeton d'écriture ne sert qu'à publier.

`contents: write` était posé au niveau du workflow : tous les jobs de
construction le portaient, et checkout le persistait pendant `npm ci` et
`pip install` — des dépendances en `>=`. Une dépendance compromise pouvait
pousser sur le dépôt ou modifier une Release.

Suivent la garde de version à la publication, l'épinglage des actions par
empreinte de commit et celui des dépendances Python.
"""

from __future__ import annotations

import json
import re
import subprocess
import sys
from pathlib import Path

import yaml

RACINE = Path(__file__).resolve().parents[2]
WORKFLOW = RACINE / ".github" / "workflows" / "executable.yml"


def _flux() -> dict:
    return yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))


def test_le_workflow_ne_donne_que_la_lecture_par_defaut():
    assert _flux()["permissions"] == {"contents": "read"}


def test_seul_le_job_de_publication_ecrit():
    jobs = _flux()["jobs"]
    ecrivains = [nom for nom, job in jobs.items()
                 if (job.get("permissions") or {}).get("contents") == "write"]
    assert ecrivains == ["publier"]
    # Et il n'écrit que le contenu : ni paquets, ni actions, ni jeton OIDC.
    assert jobs["publier"]["permissions"] == {"contents": "write"}


def test_aucun_checkout_ne_garde_le_jeton():
    for nom, job in _flux()["jobs"].items():
        for etape in job.get("steps", []):
            if str(etape.get("uses", "")).startswith("actions/checkout"):
                assert (etape.get("with") or {}).get("persist-credentials") is False, nom


# --- Actions épinglées par empreinte ---------------------------------------
# Une étiquette `@v7` se déplace : qui la pousse change le code exécuté, y
# compris sous `contents: write`. Chaque `uses:` porte l'empreinte complète du
# commit, et la majeure qu'elle représente en commentaire (`# v7`) — c'est ce
# commentaire que lisent la relecture et le test de Node 24 ci-dessous.

_USES = re.compile(r"^\s*(?:-\s*)?uses:")
_EPINGLE = re.compile(r"^\s*(?:-\s*)?uses:\s*(?P<action>[\w.-]+/[\w.-]+)@(?P<ref>\S+)"
                      r"(?:\s+#\s*(?P<majeure>\S+))?\s*$")


def _usages() -> list[tuple[str, str, str | None]]:
    """(action, référence, commentaire) de chaque ligne `uses:`, lue en texte :
    l'analyse YAML jette les commentaires."""
    vus = []
    for ligne in WORKFLOW.read_text(encoding="utf-8").splitlines():
        if _USES.match(ligne):
            m = _EPINGLE.match(ligne)
            assert m, f"ligne `uses:` illisible : {ligne.strip()}"
            vus.append((m["action"], m["ref"], m["majeure"]))
    return vus


def test_chaque_action_est_epinglee_par_empreinte_complete():
    usages = _usages()
    assert len(usages) == sum(1 for job in _flux()["jobs"].values()
                              for e in job.get("steps", []) if "uses" in e)
    for action, ref, majeure in usages:
        assert re.fullmatch(r"[0-9a-f]{40}", ref), f"{action}@{ref} : pas une empreinte"
        assert majeure and re.fullmatch(r"v\d+", majeure), f"{action} : `# vN` manquant"


def test_une_action_porte_la_meme_empreinte_partout():
    vues: dict[str, set[tuple[str, str | None]]] = {}
    for action, ref, majeure in _usages():
        vues.setdefault(action, set()).add((ref, majeure))
    for action, refs in vues.items():
        assert len(refs) == 1, f"{action} épinglée de plusieurs façons : {refs}"


# La première version de chaque action qui s'exécute sous Node 24, lue dans
# son `action.yml` (`runs.using`). Node 20 est retiré des exécuteurs GitHub :
# le run de v0.1.1 l'annonçait pour checkout@v4, setup-node@v4 et
# setup-python@v5. Les majeures retenues sont les dernières au 26/09/2026.
_NODE24_DES = {
    "actions/checkout": 5,
    "actions/setup-node": 5,
    "actions/setup-python": 6,
    "actions/upload-artifact": 6,
    "actions/download-artifact": 7,
    "softprops/action-gh-release": 3,
}


def test_aucune_action_ne_tourne_encore_sous_node_20():
    vues = set()
    for action, ref, majeure in _usages():
        assert action in _NODE24_DES, f"action inconnue de ce test : {action}"
        assert majeure, f"{action}@{ref} : majeure absente du commentaire"
        assert int(majeure.removeprefix("v")) >= _NODE24_DES[action], f"{action} {majeure}"
        vues.add(action)
    assert vues == set(_NODE24_DES)


def test_l_action_construit_aussi_le_temoin():
    texte = WORKFLOW.read_text(encoding="utf-8")
    for livre in ("temoin-ethernet-windows-x64.exe", "temoin-ethernet-macos-x64.tar.gz",
                  "temoin-ethernet-macos-arm64.tar.gz"):
        assert livre in texte, livre
    etapes = [e.get("run", "") for e in _flux()["jobs"]["construire"]["steps"]]
    assert any("construire_executable.py --produit temoin" in r for r in etapes)
    assert any("verifier_executable.py" in r and "--sante /api/sante" in r for r in etapes)


def test_le_lancement_manuel_choisit_le_produit():
    flux = _flux()
    # PyYAML lit la clé `on` comme le booléen True.
    choix = flux[True]["workflow_dispatch"]["inputs"]["produit"]
    assert choix["type"] == "choice"
    assert choix["options"] == ["les deux", "superviseur", "témoin"]
    assert choix["default"] == "les deux"
    job = flux["jobs"]["construire"]
    # Sur une étiquette, l'entrée est vide : les deux produits, toujours.
    assert job["env"] == {"SUPERVISEUR": "${{ inputs.produit != 'témoin' }}",
                          "TEMOIN": "${{ inputs.produit != 'superviseur' }}"}
    for etape in job["steps"]:
        run, nom = etape.get("run", ""), etape.get("name", "")
        if "--produit temoin" in run or "--sante /api/sante" in run:
            assert etape["if"] == "env.TEMOIN == 'true'", nom
        elif "construire_executable.py" in run or "verifier_executable.py" in run:
            assert etape["if"] == "env.SUPERVISEUR == 'true'", nom
        elif str(etape.get("uses", "")).startswith("actions/upload-artifact"):
            produit = "TEMOIN" if "temoin" in etape["with"]["name"] else "SUPERVISEUR"
            assert etape["if"] == f"env.{produit} == 'true'"


def test_la_route_de_sante_n_est_pas_convertie_en_chemin_sous_windows():
    for etape in _flux()["jobs"]["construire"]["steps"]:
        if "--sante /api/sante" in etape.get("run", ""):
            assert (etape.get("env") or {}).get("MSYS_NO_PATHCONV") == "1"


# --- Garde de version à la publication -------------------------------------
# Une étiquette sans section de CHANGELOG publiait une Release au texte de
# secours (« Superviseur Peplink et témoin Ethernet vX. ») : `awk` ne trouvait
# rien, `test -s || echo` comblait le vide. Et rien ne vérifiait que les
# exécutables portaient le numéro de l'étiquette — v0.1.1 s'est dite 0.1.0.
# La garde est un script, testé ici, que `publier` lance avant tout envoi.

GARDE = RACINE / ".github" / "scripts" / "garde_version.py"

_CHANGELOG = """# Versions

Préambule.

{avenir}## v{tete} — La dernière

Ce que la dernière change.

## v0.0.9 — L'avant-dernière

Ce que l'avant-dernière changeait.
"""


def _arbre(racine: Path, *, tete="1.2.3", app="1.2.3", temoin="1.2.3",
           paquet="1.2.3", avenir="") -> Path:
    (racine / "backend" / "app").mkdir(parents=True)
    (racine / "backend" / "temoin").mkdir(parents=True)
    (racine / "frontend").mkdir()
    (racine / "CHANGELOG.md").write_text(
        _CHANGELOG.format(tete=tete, avenir=avenir), encoding="utf-8")
    (racine / "backend" / "app" / "__init__.py").write_text(
        f'"""Doc."""\n\n__version__ = "{app}"\n', encoding="utf-8")
    (racine / "backend" / "temoin" / "__init__.py").write_text(
        f'__version__ = "{temoin}"\n', encoding="utf-8")
    (racine / "frontend" / "package.json").write_text(
        json.dumps({"name": "x", "version": paquet}), encoding="utf-8")
    return racine


def _garder(racine: Path, etiquette: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, str(GARDE), etiquette, "--racine", str(racine)],
        capture_output=True, text=True, encoding="utf-8", check=False)


def test_la_garde_laisse_passer_une_version_concordante(tmp_path):
    fait = _garder(_arbre(tmp_path), "v1.2.3")
    assert fait.returncode == 0, fait.stderr
    assert "Ce que la dernière change." in fait.stdout
    assert "avant-dernière" not in fait.stdout


def test_la_garde_cherche_la_section_de_l_etiquette_pas_la_premiere(tmp_path):
    """« ## À venir » en tête ne doit ni bloquer ni être publié."""
    racine = _arbre(tmp_path, avenir="## À venir\n\n- Un correctif pas encore livré.\n\n")
    fait = _garder(racine, "v1.2.3")
    assert fait.returncode == 0, fait.stderr
    assert "Ce que la dernière change." in fait.stdout
    assert "pas encore livré" not in fait.stdout


def test_la_garde_refuse_une_etiquette_sans_section(tmp_path):
    fait = _garder(_arbre(tmp_path), "v1.2.4")
    assert fait.returncode != 0
    assert fait.stdout == ""
    assert "CHANGELOG" in fait.stderr


def test_la_garde_refuse_une_etiquette_qui_n_est_pas_la_derniere_section(tmp_path):
    """Réétiqueter une ancienne version publierait ses notes sur le code du jour."""
    fait = _garder(_arbre(tmp_path, app="0.0.9", temoin="0.0.9", paquet="0.0.9"), "v0.0.9")
    assert fait.returncode != 0
    assert "1.2.3" in fait.stderr


def test_la_garde_refuse_un_numero_divergent(tmp_path):
    for champ in ("app", "temoin", "paquet"):
        racine = _arbre(tmp_path / champ, **{champ: "1.2.2"})
        fait = _garder(racine, "v1.2.3")
        assert fait.returncode != 0, champ
        assert "1.2.2" in fait.stderr, champ


def test_la_garde_refuse_une_etiquette_mal_formee(tmp_path):
    for etiquette in ("1.2.3", "v1.2", "v1.2.3-rc1", ""):
        assert _garder(_arbre(tmp_path / (etiquette or "vide")), etiquette).returncode != 0


def test_la_garde_passe_sur_le_depot_tel_qu_il_est():
    from app import __version__

    fait = _garder(RACINE, f"v{__version__}")
    assert fait.returncode == 0, fait.stderr
    assert fait.stdout.strip()


def test_publier_garde_la_version_avant_tout_envoi_et_sans_texte_de_secours():
    etapes = _flux()["jobs"]["publier"]["steps"]
    gardes = [i for i, e in enumerate(etapes)
              if ".github/scripts/garde_version.py" in e.get("run", "")]
    assert len(gardes) == 1, "une étape de garde dans publier"
    garde = gardes[0]
    assert "GITHUB_REF_NAME" in etapes[garde]["run"]
    assert "notes.md" in etapes[garde]["run"]
    envois = [i for i, e in enumerate(etapes)
              if str(e.get("uses", "")).startswith(("actions/download-artifact",
                                                    "softprops/action-gh-release"))]
    assert envois and garde < min(envois)
    # Plus de texte de secours : une section absente fait échouer, point.
    for etape in etapes:
        assert "echo" not in etape.get("run", ""), etape.get("name")
        assert "awk" not in etape.get("run", ""), etape.get("name")


# --- Dépendances Python ------------------------------------------------------
# Tout en `>=`, et pytest/ruff mêlés à l'exécution : chaque construction
# embarquait les versions du jour. L'exécution est épinglée à l'identique, les
# outils de test et de construction vivent à part.

BACKEND = RACINE / "backend"
_FICHIERS = ("requirements.txt", "requirements-dev.txt", "requirements-build.txt")


def _exigences(nom: str) -> list[str]:
    lignes = (BACKEND / nom).read_text(encoding="utf-8").splitlines()
    return [ligne.strip() for ligne in lignes
            if ligne.strip() and not ligne.lstrip().startswith("#")]


def _paquet(ligne: str) -> str:
    return re.split(r"[=<>!~;\s\[]", ligne, maxsplit=1)[0].lower().replace("_", "-")


def test_les_dependances_sont_epinglees_a_l_identique():
    for nom in _FICHIERS:
        for ligne in _exigences(nom):
            if ligne.startswith("-r "):
                continue
            assert re.match(r"^[A-Za-z0-9_.-]+==[0-9][\w.]*(\s*;.+)?$", ligne), f"{nom} : {ligne}"


def test_les_outils_de_test_ne_sont_pas_des_dependances_d_execution():
    execution = {_paquet(ligne) for ligne in _exigences("requirements.txt")}
    for outil in ("pytest", "pytest-asyncio", "ruff", "httpx2", "pyinstaller"):
        assert outil not in execution, outil
    for indispensable in ("fastapi", "uvicorn", "websockets", "pydantic", "pydantic-settings",
                          "httpx", "aiosqlite", "pyyaml", "keyring"):
        assert indispensable in execution, indispensable
    dev = _exigences("requirements-dev.txt")
    assert "-r requirements.txt" in dev
    assert {"pytest", "pytest-asyncio", "ruff", "httpx2"} <= {_paquet(ligne) for ligne in dev}
    construction = _exigences("requirements-build.txt")
    assert "-r requirements.txt" in construction
    assert "pyinstaller" in {_paquet(ligne) for ligne in construction}


def test_aucune_dependance_propre_a_un_systeme_sans_marqueur():
    """La CI installe sous Windows ET macOS : macholib n'existe pas sous
    Windows, pywin32-ctypes ni pefile sous macOS."""
    propres = {"macholib", "pyobjc", "appnope", "pywin32-ctypes", "pefile", "pywin32",
               "colorama", "tzdata"}
    for nom in _FICHIERS:
        for ligne in _exigences(nom):
            if _paquet(ligne) in propres:
                assert "sys_platform" in ligne, f"{nom} : {ligne}"


def test_la_ci_installe_les_outils_de_test():
    etapes = _flux()["jobs"]["construire"]["steps"]
    installs = [e["run"] for e in etapes if "pip install" in e.get("run", "")]
    assert installs and all("requirements-dev.txt" in r for r in installs)
    caches = [e["with"]["cache-dependency-path"] for e in etapes
              if str(e.get("uses", "")).startswith("actions/setup-python")]
    assert caches and all(f"backend/{nom}" in caches[0] for nom in _FICHIERS)
