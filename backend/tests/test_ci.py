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
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest
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
        assert majeure and _VERSION_COMMENTEE.fullmatch(majeure), f"{action} : `# vN` manquant"


# Dependabot, quand il monte une action épinglée par empreinte, réécrit le
# commentaire avec l'étiquette complète qu'il a résolue : `# v7` devient
# `# v7.1.0`. Les deux formes sont admises ; la majeure est le premier nombre.
_VERSION_COMMENTEE = re.compile(r"v\d+(?:\.\d+){0,2}")


def _majeure(commentaire: str) -> int:
    return int(commentaire.removeprefix("v").split(".")[0])


def test_le_commentaire_de_dependabot_est_admis():
    for forme, majeure in (("v7", 7), ("v7.1", 7), ("v7.1.0", 7), ("v12.0.3", 12)):
        assert _VERSION_COMMENTEE.fullmatch(forme), forme
        assert _majeure(forme) == majeure
    for refusee in ("7", "v", "v7.x", "latest", "v7.1.0.2"):
        assert not _VERSION_COMMENTEE.fullmatch(refusee), refusee


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
        assert _majeure(majeure) >= _NODE24_DES[action], f"{action} {majeure}"
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


# --- Garde de version AVANT toute construction --------------------------------
# Elle ne tournait que dans `publier`, après les trois constructions : une
# étiquette sans section de CHANGELOG coûtait trois machines et une vingtaine
# de minutes pour échouer à la fin. Le job léger `garde` la lance d'abord, et
# les constructions en dépendent. Sur une étiquette `v*` (même condition que
# `publier`), la garde complète ; en lancement manuel sans étiquette, la
# cohérence interne seule : les trois numéros du dépôt concordent — un
# exécutable de test ne se dit pas 0.1.0 quand son interface dit 0.2.0 —, mais
# le CHANGELOG n'est pas consulté, l'« À venir » n'ayant pas encore de numéro.
# `publier` relance la garde complète pour produire notes.md (même commit,
# même verdict).

def _garder_coherence(racine: Path) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, str(GARDE), "--coherence", "--racine", str(racine)],
        capture_output=True, text=True, encoding="utf-8", check=False)


def test_la_coherence_passe_sans_section_de_changelog(tmp_path):
    racine = _arbre(tmp_path, tete="1.2.2", avenir="## À venir\n\n- Bientôt.\n\n")
    fait = _garder_coherence(racine)
    assert fait.returncode == 0, fait.stderr


def test_la_coherence_refuse_des_numeros_divergents(tmp_path):
    for champ in ("app", "temoin", "paquet"):
        fait = _garder_coherence(_arbre(tmp_path / champ, **{champ: "1.2.2"}))
        assert fait.returncode != 0, champ
        assert "1.2.2" in fait.stderr and "1.2.3" in fait.stderr, champ


def test_la_garde_exige_une_etiquette_ou_la_coherence_pas_les_deux(tmp_path):
    racine = _arbre(tmp_path)
    for arguments in ([], ["v1.2.3", "--coherence"]):
        fait = subprocess.run([sys.executable, str(GARDE), *arguments, "--racine", str(racine)],
                              capture_output=True, text=True, check=False)
        assert fait.returncode != 0, arguments


def test_la_coherence_passe_sur_le_depot_tel_qu_il_est():
    fait = _garder_coherence(RACINE)
    assert fait.returncode == 0, fait.stderr


def _etape_de_garde() -> dict:
    etapes = [e for e in _flux()["jobs"]["garde"]["steps"]
              if ".github/scripts/garde_version.py" in e.get("run", "")]
    assert len(etapes) == 1
    return etapes[0]


def test_la_garde_tourne_avant_toute_construction():
    jobs = _flux()["jobs"]
    garde = jobs["garde"]
    # Léger : ni matrice, ni jeton d'écriture, rien à installer.
    assert "strategy" not in garde and "permissions" not in garde and "needs" not in garde
    assert garde["runs-on"] == "ubuntu-latest"
    for etape in garde["steps"]:
        assert "pip" not in etape.get("run", "") and "npm" not in etape.get("run", "")
        assert not str(etape.get("uses", "")).startswith(("actions/setup-", "actions/cache"))
    etape = _etape_de_garde()
    assert "--coherence" in etape["run"]
    # La garde complète exactement quand `publier` publiera : même condition.
    assert jobs["publier"]["if"] in etape["env"]["ETIQUETTE"]
    # Les constructions l'attendent, sans condition qui la contournerait.
    besoins = jobs["construire"]["needs"]
    assert "garde" in ([besoins] if isinstance(besoins, str) else besoins)
    assert "if" not in jobs["construire"]


@pytest.mark.skipif(sys.platform == "win32" or shutil.which("bash") is None,
                    reason="joue l'étape sous bash, avec un python3 de substitution")
def test_l_etape_de_garde_jouee_telle_quelle(tmp_path):
    """Étiquette → garde complète ; sans étiquette → cohérence seule."""
    etape = _etape_de_garde()
    racine = _arbre(tmp_path / "depot", avenir="## À venir\n\n- Bientôt.\n\n")
    (racine / ".github" / "scripts").mkdir(parents=True)
    shutil.copy(GARDE, racine / ".github" / "scripts" / "garde_version.py")
    binaires = tmp_path / "bin"
    binaires.mkdir()
    (binaires / "python3").symlink_to(sys.executable)

    def jouer(etiquette: str) -> int:
        env = {**os.environ, "PATH": f"{binaires}{os.pathsep}{os.environ.get('PATH', '')}",
               "ETIQUETTE": etiquette}
        return subprocess.run(["bash", "-eo", "pipefail", "-c", etape["run"]], cwd=racine,
                              env=env, capture_output=True, text=True, check=False).returncode

    assert jouer("v1.2.3") == 0
    assert jouer("v1.2.4") != 0, "une étiquette sans section doit arrêter la CI"
    assert jouer("") == 0, "un lancement manuel ne demande pas de section"
    (racine / "frontend" / "package.json").write_text('{"version": "1.2.2"}', encoding="utf-8")
    assert jouer("") != 0, "même sans étiquette, des numéros divergents arrêtent"


# --- Empreintes SHA-256 de la Release ------------------------------------------
# Les six fichiers partent avec `SHA256SUMS`, au format de `sha256sum`
# (`<hex>  <nom>`) : `shasum -a 256 -c SHA256SUMS` sur Mac, `Get-FileHash`
# sous Windows, vérifient un téléchargement.

def test_publier_joint_les_empreintes_sha256_des_six_fichiers():
    etapes = _flux()["jobs"]["publier"]["steps"]
    rang = dict(enumerate(etapes))
    telechargement = next(i for i, e in rang.items()
                          if str(e.get("uses", "")).startswith("actions/download-artifact"))
    envoi = next(i for i, e in rang.items()
                 if str(e.get("uses", "")).startswith("softprops/action-gh-release"))
    empreintes = [i for i, e in rang.items() if "sha256sum" in e.get("run", "")]
    assert len(empreintes) == 1
    assert telechargement < empreintes[0] < envoi
    run = etapes[empreintes[0]]["run"]
    assert "SHA256SUMS" in run and "-eq 6" in run, "six fichiers, ni plus ni moins"
    # Le fichier part avec les six autres.
    fichiers = etapes[envoi]["with"]["files"]
    assert "SHA256SUMS" in fichiers or fichiers.strip() == "fichiers/*"
    assert (etapes[empreintes[0]].get("working-directory") == "fichiers"
            or "fichiers" in run)


@pytest.mark.skipif(sys.platform == "win32" or shutil.which("bash") is None
                    or (shutil.which("sha256sum") is None and shutil.which("shasum") is None),
                    reason="joue l'étape sous bash, avec sha256sum")
def test_l_etape_d_empreintes_jouee_telle_quelle(tmp_path):
    etape = next(e for e in _flux()["jobs"]["publier"]["steps"]
                 if "sha256sum" in e.get("run", ""))
    dossier = tmp_path / "fichiers"
    dossier.mkdir()
    import hashlib
    noms = ["superviseur-windows-x64.exe", "superviseur-macos-x64.tar.gz",
            "superviseur-macos-arm64.tar.gz", "temoin-ethernet-windows-x64.exe",
            "temoin-ethernet-macos-x64.tar.gz", "temoin-ethernet-macos-arm64.tar.gz"]
    for n, nom in enumerate(noms):
        (dossier / nom).write_bytes(bytes([n]) * (n + 1) * 1000)
    env = dict(os.environ)
    if shutil.which("sha256sum") is None:
        # macOS : `shasum -a 256` écrit le même format.
        binaires = tmp_path / "bin"
        binaires.mkdir()
        (binaires / "sha256sum").write_text('#!/bin/sh\nexec shasum -a 256 "$@"\n')
        (binaires / "sha256sum").chmod(0o755)
        env["PATH"] = f"{binaires}{os.pathsep}{env.get('PATH', '')}"
    cwd = dossier if etape.get("working-directory") == "fichiers" else tmp_path
    fait = subprocess.run(["bash", "-eo", "pipefail", "-c", etape["run"]], cwd=cwd, env=env,
                          capture_output=True, text=True, check=False)
    assert fait.returncode == 0, fait.stderr
    lignes = (dossier / "SHA256SUMS").read_text(encoding="utf-8").splitlines()
    attendues = sorted(f"{hashlib.sha256((dossier / nom).read_bytes()).hexdigest()}  {nom}"
                       for nom in noms)
    assert sorted(lignes) == attendues
    # Un septième fichier (ou un manquant) arrête la publication.
    (dossier / "SHA256SUMS").unlink()
    (dossier / "intrus.txt").write_text("x")
    fait = subprocess.run(["bash", "-eo", "pipefail", "-c", etape["run"]], cwd=cwd, env=env,
                          capture_output=True, text=True, check=False)
    assert fait.returncode != 0


# --- Audits de vulnérabilités et couverture ----------------------------------
# Une faille connue dans une dépendance d'EXÉCUTION (ce que l'exécutable
# embarque, ce que le navigateur charge) fait échouer la CI, donc bloque la
# Release ; les outils de développement ne sont pas audités. La couverture est
# affichée, sans seuil, sur une seule machine de la matrice.

def test_un_job_audite_les_dependances_d_execution():
    jobs = _flux()["jobs"]
    audits = jobs["audits"]
    assert audits["runs-on"] == "ubuntu-latest" and "permissions" not in audits
    runs = "\n".join(e.get("run", "") for e in audits["steps"])
    assert re.search(r"pip_audit .*-r backend/requirements\.txt", runs)
    assert "npm audit --omit=dev --audit-level=high" in runs
    # `|| true` ou `continue-on-error` rendraient l'audit décoratif.
    assert "|| true" not in runs
    assert not any(e.get("continue-on-error") for e in audits["steps"])
    assert not audits.get("continue-on-error")
    besoins = jobs["publier"]["needs"]
    assert "audits" in besoins and "construire" in besoins


def test_pip_audit_et_la_couverture_sont_epingles_a_part():
    """La couverture sert aux tests de la construction ; pip-audit au seul
    job d'audit. Les trois constructions installaient pip-audit et ses vingt
    dépendances pour rien."""
    test = {_paquet(ligne) for ligne in _exigences("requirements-test.txt")}
    audit = {_paquet(ligne) for ligne in _exigences("requirements-audit.txt")}
    assert {"pytest-cov", "coverage"} <= test and "pip-audit" not in test
    assert "pip-audit" in audit
    execution = {_paquet(ligne) for ligne in _exigences("requirements.txt")}
    assert not {"pip-audit", "pytest-cov", "coverage"} & execution
    # Le poste de développement installe tout d'un coup, comme avant.
    dev = _exigences("requirements-dev.txt")
    assert "-r requirements-test.txt" in dev and "-r requirements-audit.txt" in dev


AUDITER = RACINE / ".github" / "scripts" / "auditer.py"


def _auditer(tmp_path: Path, sorties: list[tuple[int, str]]) -> subprocess.CompletedProcess:
    """Lance l'enveloppe d'audit sur un faux outil qui rend `sorties`, une par appel."""
    faux = tmp_path / "outil.py"
    compteur = tmp_path / "appels"
    faux.write_text(
        "import json, sys, pathlib\n"
        f"c = pathlib.Path({str(compteur)!r})\n"
        "n = int(c.read_text()) if c.exists() else 0\n"
        "c.write_text(str(n + 1))\n"
        f"code, texte = json.loads({json.dumps(json.dumps(sorties))})[min(n, {len(sorties) - 1})]\n"
        "print(texte)\n"
        "sys.exit(code)\n",
        encoding="utf-8",
    )
    fait = subprocess.run(
        [sys.executable, str(AUDITER), "--", sys.executable, str(faux)],
        capture_output=True, text=True, check=False, env={**os.environ, "AUDIT_PAUSE_S": "0"},
    )
    fait.appels = int(compteur.read_text())  # type: ignore[attr-defined]
    return fait


_RESEAU_PIP = "requests.exceptions.ConnectionError: Max retries exceeded with url: /pypi/fastapi/json"
_RESEAU_NPM = "npm error code EAI_AGAIN\nnpm error audit endpoint returned an error"


def test_l_audit_propre_passe(tmp_path):
    fait = _auditer(tmp_path, [(0, "No known vulnerabilities found")])
    assert fait.returncode == 0 and fait.appels == 1


@pytest.mark.parametrize("sortie", [
    "Found 2 known vulnerabilities in 1 package",
    "# npm audit report\n\n3 vulnerabilities (1 moderate, 2 high)",
])
def test_des_failles_connues_echouent_sans_retenter(tmp_path, sortie):
    fait = _auditer(tmp_path, [(1, sortie)])
    assert fait.returncode == 1 and fait.appels == 1
    assert "failles connues" in fait.stdout


@pytest.mark.parametrize("sortie", [_RESEAU_PIP, _RESEAU_NPM])
def test_une_erreur_reseau_passagere_est_retentee(tmp_path, sortie):
    fait = _auditer(tmp_path, [(1, sortie), (0, "found 0 vulnerabilities")])
    assert fait.returncode == 0 and fait.appels == 2


@pytest.mark.parametrize("sortie", [_RESEAU_PIP, _RESEAU_NPM])
def test_une_erreur_reseau_qui_persiste_se_dit_erreur_reseau(tmp_path, sortie):
    fait = _auditer(tmp_path, [(1, sortie)])
    assert fait.returncode == 2 and fait.appels == 2
    assert "erreur réseau" in fait.stdout and "failles connues" not in fait.stdout


def test_les_deux_audits_passent_par_l_enveloppe():
    etapes = _flux()["jobs"]["audits"]["steps"]
    for outil in ("pip_audit", "npm audit"):
        (run,) = [e["run"] for e in etapes if outil in e.get("run", "")]
        ligne = next(ligne for ligne in run.splitlines() if outil in ligne)
        assert "auditer.py --" in ligne, ligne
    installs = [e["run"] for e in etapes if "pip install" in e.get("run", "")]
    assert installs and all("requirements-audit.txt" in r for r in installs)


def test_la_couverture_est_mesuree_sur_une_seule_machine():
    job = _flux()["jobs"]["construire"]
    avec = [e for e in job["strategy"]["matrix"]["include"] if e.get("couverture")]
    assert len(avec) == 1
    runs = "\n".join(e.get("run", "") for e in job["steps"])
    assert "--cov=app" in runs and "--cov=temoin" in runs
    assert "--coverage.enabled" in runs
    assert "GITHUB_STEP_SUMMARY" in runs
    # Sans seuil : la couverture informe, elle n'arrête rien.
    assert "fail-under" not in runs and "thresholds" not in runs


def test_vitest_et_sa_couverture_vont_de_pair():
    paquet = json.loads((RACINE / "frontend" / "package.json").read_text(encoding="utf-8"))
    verrou = json.loads((RACINE / "frontend" / "package-lock.json").read_text(encoding="utf-8"))
    assert "@vitest/coverage-v8" in paquet["devDependencies"]
    assert "@vitest/coverage-v8" not in paquet.get("dependencies", {})
    versions = {nom: verrou["packages"][f"node_modules/{nom}"]["version"]
                for nom in ("vitest", "@vitest/coverage-v8")}
    assert versions["vitest"] == versions["@vitest/coverage-v8"], versions


# --- Dependabot -----------------------------------------------------------------

def test_dependabot_surveille_les_trois_ecosystemes_au_mois_regroupes():
    config = yaml.safe_load((RACINE / ".github" / "dependabot.yml").read_text(encoding="utf-8"))
    assert config["version"] == 2
    vus = {(u["package-ecosystem"], u["directory"]) for u in config["updates"]}
    assert vus == {("github-actions", "/"), ("pip", "/backend"), ("npm", "/frontend")}
    for maj in config["updates"]:
        assert maj["schedule"]["interval"] == "monthly", maj["package-ecosystem"]
        assert 1 <= maj["open-pull-requests-limit"] <= 3
        assert maj.get("groups"), f"{maj['package-ecosystem']} : non regroupé"


# --- Licence -------------------------------------------------------------------

def test_licence_mit_au_nom_du_compte():
    licence = (RACINE / "LICENSE").read_text(encoding="utf-8")
    assert licence.startswith("MIT License")
    assert "Copyright (c) 2026 maximvs101" in licence
    assert "LICENSE" in (RACINE / "README.md").read_text(encoding="utf-8")


# --- Dépendances Python ------------------------------------------------------
# Tout en `>=`, et pytest/ruff mêlés à l'exécution : chaque construction
# embarquait les versions du jour. L'exécution est épinglée à l'identique, les
# outils de test et de construction vivent à part.

BACKEND = RACINE / "backend"
_FICHIERS = ("requirements.txt", "requirements-test.txt", "requirements-audit.txt",
             "requirements-build.txt")


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
    test = _exigences("requirements-test.txt")
    assert "-r requirements.txt" in test
    assert {"pytest", "pytest-asyncio", "ruff", "httpx2"} <= {_paquet(ligne) for ligne in test}
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


def test_un_seul_vite_dans_le_verrou():
    """Vite 5 compilait l'interface, vite 8 servait vitest : deux outils, deux
    comportements, et vite 5 en fin de vie. Un seul, celui de la compilation."""
    verrou = json.loads((RACINE / "frontend" / "package-lock.json").read_text(encoding="utf-8"))
    vites = [chemin for chemin in verrou["packages"]
             if chemin == "node_modules/vite" or chemin.endswith("/node_modules/vite")]
    assert vites == ["node_modules/vite"]
    assert int(verrou["packages"]["node_modules/vite"]["version"].split(".")[0]) >= 8


def test_la_ci_installe_les_outils_de_test_et_rien_de_plus():
    etapes = _flux()["jobs"]["construire"]["steps"]
    installs = [e["run"] for e in etapes if "pip install" in e.get("run", "")]
    assert installs and all("requirements-test.txt" in r and "requirements-build.txt" in r
                            for r in installs)
    assert not any("requirements-dev.txt" in r or "requirements-audit.txt" in r for r in installs)
    caches = [e["with"]["cache-dependency-path"] for e in etapes
              if str(e.get("uses", "")).startswith("actions/setup-python")]
    assert caches and all(f"backend/{nom}" in caches[0]
                          for nom in ("requirements.txt", "requirements-test.txt",
                                      "requirements-build.txt"))
