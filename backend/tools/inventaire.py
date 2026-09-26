"""Inventaire d'une capture : quels champs l'équipement publie, et qui les lit.

    .venv/bin/python tools/inventaire.py $S/captures/status.wan.connection.json

Ne montre JAMAIS une valeur — des chemins de clés et des types. C'est ce qui
permet d'examiner une charge utile inconnue sans en exposer les secrets : les
noms de clés suffisent à décider si elle peut être lue en entier.
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

# Secrets ET données personnelles : un nom d'appareil, une MAC, une IP, un
# numéro de série ou un nom de WAN (qui porte des numéros de ligne) ne sont pas
# des secrets, mais ils n'entrent pas davantage dans le dépôt ni dans ce qu'on
# montre. `name` est large à dessein : l'examen décide ce qu'on masque, et
# masquer un nom de trop ne coûte rien.
_SECRET = re.compile(
    r"(pass|pwd|secret|psk|pre_?shared|key$|token|username|user$|login|imsi|iccid|imei|eid"
    r"|msisdn|mdn|phone|number$|pin$|puk$|serial|^sn$|mac$|^ip$|hostname|^name$)",
    re.IGNORECASE,
)
_TYPES = {bool: "bool", int: "int", float: "float", str: "str", dict: "dict", list: "list"}


def _type(valeur: object) -> str:
    if valeur is None:
        return "null"
    return _TYPES.get(type(valeur), type(valeur).__name__)


def chemins(charge: object, prefixe: str = "") -> dict[str, str]:
    """Chemin pointé → type. Listes fusionnées en `[]`, clés numériques en `*`."""
    sortie: dict[str, str] = {}
    if isinstance(charge, dict):
        for cle, valeur in charge.items():
            nom = "*" if str(cle).isdigit() else str(cle)
            chemin = f"{prefixe}.{nom}" if prefixe else nom
            if isinstance(valeur, dict | list):
                sortie.update(chemins(valeur, chemin))
            else:
                sortie.setdefault(chemin, _type(valeur))
    elif isinstance(charge, list):
        for element in charge:
            if isinstance(element, dict | list):
                sortie.update(chemins(element, f"{prefixe}[]"))
            else:
                sortie.setdefault(f"{prefixe}[]", _type(element))
    return sortie


def suspects(vus: dict[str, str]) -> list[str]:
    """Chemins dont la dernière clé évoque un secret ou un identifiant personnel."""
    return sorted(c for c in vus if _SECRET.search(c.rsplit(".", 1)[-1].rstrip("[]")))


def refus(charge: object) -> str | None:
    if isinstance(charge, dict) and charge.get("stat") == "fail":
        return f"refusé (code {charge.get('code')})"
    return None


def couches(cle: str, racine: Path) -> dict[str, bool]:
    """La clé feuille apparaît-elle, entre guillemets ou comme identifiant, dans chaque couche ?

    UN INDICE, PAS UN VERDICT. La recherche porte sur le nom BRUT de la clé :
    - « oui » peut être faux pour une feuille générique (`name`, `type`,
      `status`, `id`), présente partout sans que CE champ soit lu ;
    - « — » est souvent faux hors du collecteur : le code renomme en
      snake_case (`cellId` → `cell_id`), si bien que les colonnes base / api /
      écran disent « absent » pour des champs stockés et montrés.
    Seule la colonne « collecteur » est fiable pour une clé spécifique ; le
    reste se classe à la main sur `app/models.py`.
    """
    motif = re.compile(rf"""["'.]{re.escape(cle)}["']|\b{re.escape(cle)}\b""")
    ecran = [
        p for p in (racine.parent / "frontend" / "src").rglob("*.ts*")
        if ".test." not in p.name and "/test/" not in str(p)
    ]
    sources = {
        "collecteur": [racine / "app" / "peplink" / "router_api.py"],
        "base": [racine / "app" / "storage" / "db.py"],
        "api": [racine / "app" / "models.py"],
        "ecran": ecran,
    }
    return {
        couche: any(motif.search(f.read_text(encoding="utf-8")) for f in fichiers)
        for couche, fichiers in sources.items()
    }


def main(fichiers: list[str]) -> int:
    racine = Path(__file__).resolve().parents[1]
    for fichier in fichiers:
        charge = json.loads(Path(fichier).read_text(encoding="utf-8"))
        print(f"\n### {Path(fichier).stem}\n")
        if (r := refus(charge)) is not None:
            print(f"_{r}_")
            continue
        vus = chemins(charge)
        drapeaux = set(suspects(vus))
        print("_Indice par nom brut de clé : seule « collecteur » est fiable, voir `couches()`._\n")
        print("| chemin | type | collecteur | base | api | écran |")
        print("|---|---|---|---|---|---|")
        for chemin, genre in sorted(vus.items()):
            feuille = chemin.rsplit(".", 1)[-1].rstrip("[]")
            c = couches(feuille, racine)
            marque = " ⚠" if chemin in drapeaux else ""
            print(f"| `{chemin}`{marque} | {genre} | "
                  + " | ".join("oui" if c[k] else "—" for k in ("collecteur", "base", "api", "ecran"))
                  + " |")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
