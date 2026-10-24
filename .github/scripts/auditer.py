"""Lance un audit de failles (pip-audit, npm audit) et dit POURQUOI il échoue.

Les deux outils rendent le même code de sortie pour « failles connues » et
pour « l'index ne répond pas » : une panne réseau du runner se lisait comme
une faille et bloquait la Release, sans que rien ne dise laquelle des deux.
Ici :

  - code 0 : rien de connu ;
  - failles connues (le rapport de l'outil le dit) : échec, code 1, sans
    retenter — une faille ne disparaît pas en réessayant ;
  - tout autre échec, typiquement le réseau : retenté UNE fois après une
    pause ; s'il persiste, échec en « erreur réseau », code 2.

Usage : python auditer.py -- <commande de l'outil…>
La pause (secondes) se règle par AUDIT_PAUSE_S (20 par défaut).
"""

from __future__ import annotations

import os
import re
import subprocess
import sys
import time

# Le rapport de failles de chaque outil — et lui seul.
_FAILLES = re.compile(
    r"Found \d+ known vulnerabilit"          # pip-audit
    r"|^\d+ (?:\w+ severity )?vulnerabilit"  # npm audit : « 3 vulnerabilities (…) »
    , re.IGNORECASE | re.MULTILINE,
)


def _lancer(commande: list[str]) -> tuple[int, str]:
    fait = subprocess.run(commande, capture_output=True, text=True, check=False)
    sortie = (fait.stdout or "") + (fait.stderr or "")
    print(sortie, end="" if sortie.endswith("\n") else "\n", flush=True)
    return fait.returncode, sortie


def main(arguments: list[str]) -> int:
    if "--" not in arguments or not arguments[arguments.index("--") + 1:]:
        print("Usage : auditer.py -- <commande>", file=sys.stderr)
        return 64
    commande = arguments[arguments.index("--") + 1:]
    pause = float(os.environ.get("AUDIT_PAUSE_S", "20"))
    for essai in (1, 2):
        code, sortie = _lancer(commande)
        if code == 0:
            return 0
        if _FAILLES.search(sortie):
            print(f"::error title=Audit::failles connues signalées par {commande[0]} "
                  f"(voir le rapport ci-dessus)", flush=True)
            return 1
        if essai == 1:
            print(f"Audit sans verdict (code {code}), sans doute le réseau : "
                  f"nouvel essai dans {pause:.0f} s.", flush=True)
            time.sleep(pause)
    print("::error title=Audit::erreur réseau, l'audit n'a pas pu interroger sa base "
          "de failles (deux essais) — aucune faille n'est signalée pour autant", flush=True)
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
