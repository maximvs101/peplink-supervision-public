"""Exporte vers l'écran ce que les routes rendent sur chaque capture réelle.

    .venv/bin/python tools/exporter_rejeu.py

Écrit `frontend/src/test/reel/<equipement>.json` : la réponse de chaque route
que le tableau de bord lit, obtenue en rejouant la capture à travers le client,
le collecteur, la base et les routes RÉELS (voir `tests/rejeu.py`). L'écran
monte ensuite son tableau de bord dessus, et balaie ce qu'il rend.

C'est le seul chemin par lequel une charge utile d'équipement réel atteint les
tests de l'écran sans passer par une fabrique écrite à la main — c'est-à-dire
sans qu'une hypothèse de l'auteur se glisse entre le routeur et le rendu.
`test_rejeu_bout_en_bout.py` vérifie que ces fichiers sont à jour : un modèle
qui change sans que l'exportation soit rejouée fait tomber un test, avec la
commande à lancer.
"""

from __future__ import annotations

import asyncio
import json
import sys
import tempfile
from pathlib import Path

RACINE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(RACINE))
sys.path.insert(0, str(RACINE / "tests"))

from rejeu import CAPTURES, Rejeu

DESTINATION = RACINE.parent / "frontend" / "src" / "test" / "reel"

# Les horodatages sont POSÉS par le rejeu (voir `Rejeu.DEBUT` et `Rejeu.PAS`) :
# ils ne bougent pas d'une machine à l'autre. Ce qui reste à figer vient de
# `time.monotonic()` — la période et le cycle mesurés, l'âge du dernier relevé
# — et des volumes intégrés sur ce même temps machine. Le fichier exporté ne
# change alors qu'avec le CODE, et `git diff` dit quelque chose.


def _figer(valeur: object, cle: str | None = None) -> object:
    if isinstance(valeur, dict):
        return {k: _figer(v, k) for k, v in valeur.items()}
    if isinstance(valeur, list):
        return [_figer(v, cle) for v in valeur]
    if cle in {"age_seconds", "period_seconds", "cycle_seconds"} and isinstance(valeur, float):
        return {"age_seconds": 0.5, "period_seconds": 5.1, "cycle_seconds": 2.1}[cle]
    # Les VOLUMES s'intègrent sur le temps écoulé entre deux cycles — quelques
    # millisecondes de rejeu, qui font 0,0 Mo sur une machine et 0,1 sur une
    # autre (vu entre un Mac et la machine Windows de GitHub). On garde ce qui
    # compte pour l'écran, mesuré ou non : un volume mesuré vaut 0,0.
    if cle in {"mb_up", "mb_down"} and isinstance(valeur, (int, float)):
        return 0.0
    return valeur


async def exporter(nom: str, chemin: Path, dossier: Path) -> dict:
    async with Rejeu(nom, chemin, dossier) as r:
        await r.cycles(2)
        _, resume = await r.lire(f"/routers/{nom}")
        _, sessions = await r.lire(f"/routers/{nom}/sessions")
        sid = sessions[0]["id"]
        since = sessions[0]["first_seen"] or sessions[0]["started_at"]
        _, timeline = await r.lire(f"/routers/{nom}/timeline?since={since}")
        _, events = await r.lire(f"/routers/{nom}/events?since={since}")
        _, summary = await r.lire(f"/sessions/{sid}/summary")
        return {
            "capture": chemin.name,
            "router": resume,
            "sessions": sessions,
            "timeline": timeline,
            "events": events,
            "summary": summary,
        }


async def tout() -> dict[str, dict]:
    # Un dossier temporaire qui s'efface : mkdtemp en laissait un à chaque
    # passage — la suite l'appelle à chaque exécution (C-46).
    with tempfile.TemporaryDirectory() as brouillon:
        dossier = Path(brouillon)
        return {nom: _figer(await exporter(nom, chemin, dossier)) for nom, chemin in CAPTURES.items()}


def ecrire(exports: dict[str, dict]) -> list[Path]:
    DESTINATION.mkdir(parents=True, exist_ok=True)
    ecrits = []
    for nom, contenu in exports.items():
        cible = DESTINATION / f"{nom}.json"
        cible.write_text(json.dumps(contenu, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
        ecrits.append(cible)
    return ecrits


if __name__ == "__main__":
    for cible in ecrire(asyncio.run(tout())):
        print(cible.relative_to(RACINE.parent))
