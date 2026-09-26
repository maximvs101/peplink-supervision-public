"""Chronologie de ce que la supervision PUBLIE pendant un scénario.

    .venv/bin/python tools/observer.py <id-du-dome> 300

Une ligne à chaque fait qui change, datée depuis le lancement : c'est ce qui
mesure « en combien de cycles l'outil le dit ». Une requête qui échoue est
notée et l'observation continue — un équipement qui redémarre en fait échouer
plusieurs, et c'est une partie de ce qu'on observe.
"""

from __future__ import annotations

import json
import sys
import time
import urllib.request


def faits(resume: dict) -> dict[str, str]:
    f = {
        "statut": str(resume.get("status")),
        "alertes": ",".join(sorted(f"{a.get('kind')}/{a.get('wan_id')}" for a in resume.get("alerts") or [])),
        "tunnel_attendu": str(resume.get("tunnel_attendu")),
        "jamais_connectes": ",".join(resume.get("never_connected") or []),
    }
    for s in resume.get("wans") or []:
        w = s.get("wan") or {}
        p = f"w{w.get('wan_id')}"
        active = next((x.get("slot") for x in w.get("sim_slots") or [] if x.get("active")), None)
        f.update({
            f"{p}.connecte": "oui" if w.get("connected") else "non",
            f"{p}.message": str(w.get("status_message")),
            f"{p}.rat": str(w.get("rat")),
            f"{p}.bande": str(w.get("band")),
            f"{p}.cellule": str(w.get("cell_id")),
            f"{p}.priorite": str(w.get("priority")),
            f"{p}.sim_active": str(active),
            f"{p}.quota": json.dumps(w.get("allowance"), sort_keys=True),
        })
    return f


def ecarts(avant: dict[str, str], apres: dict[str, str]) -> list[str]:
    cles = sorted(set(avant) | set(apres))
    return [
        f"{k}: {avant.get(k, '—')} → {apres.get(k, '—')}"
        for k in cles if avant.get(k, "—") != apres.get(k, "—")
    ]


def main(router_id: str, secondes: float, api: str = "http://localhost:8000/api") -> int:
    debut = time.monotonic()
    precedent: dict[str, str] = {}
    while (t := time.monotonic() - debut) < secondes:
        try:
            resume = json.load(urllib.request.urlopen(f"{api}/routers/{router_id}", timeout=5))
            courant = faits(resume)
            for ligne in ecarts(precedent, courant) if precedent else [f"état initial : {courant}"]:
                print(f"t+{t:6.1f}s  {ligne}", flush=True)
            precedent = courant
        except Exception as exc:  # l'observation ne s'arrête pas
            print(f"t+{t:6.1f}s  ÉCHEC {type(exc).__name__}: {exc}", flush=True)
        time.sleep(1.0)
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1], float(sys.argv[2])))
