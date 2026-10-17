"""C-48 : le jeton d'écriture ne sert qu'à publier.

`contents: write` était posé au niveau du workflow : tous les jobs de
construction le portaient, et checkout le persistait pendant `npm ci` et
`pip install` — des dépendances en `>=`. Une dépendance compromise pouvait
pousser sur le dépôt ou modifier une Release.
"""

from __future__ import annotations

from pathlib import Path

import yaml

WORKFLOW = Path(__file__).resolve().parents[2] / ".github" / "workflows" / "executable.yml"


def _flux() -> dict:
    return yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))


def test_le_workflow_ne_donne_que_la_lecture_par_defaut():
    assert _flux()["permissions"] == {"contents": "read"}


def test_seul_le_job_de_publication_ecrit():
    jobs = _flux()["jobs"]
    ecrivains = [nom for nom, job in jobs.items()
                 if (job.get("permissions") or {}).get("contents") == "write"]
    assert ecrivains == ["publier"]


def test_aucun_checkout_ne_garde_le_jeton():
    for nom, job in _flux()["jobs"].items():
        for etape in job.get("steps", []):
            if str(etape.get("uses", "")).startswith("actions/checkout"):
                assert (etape.get("with") or {}).get("persist-credentials") is False, nom


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
    for nom, job in _flux()["jobs"].items():
        for etape in job.get("steps", []):
            action, _, version = str(etape.get("uses", "")).partition("@")
            if not action:
                continue
            assert action in _NODE24_DES, f"action inconnue de ce test : {action}"
            assert int(version.removeprefix("v")) >= _NODE24_DES[action], f"{nom} : {action}@{version}"
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
