"""L'identifiant d'un équipement appairé : stable, et OPAQUE.

Il était le numéro de série en minuscules. Or cet identifiant est partout : dans
chaque ligne de journal (« Routeur %s… »), dans chaque ligne de la base
(`router_id`), dans les URL de l'API et dans celles de l'interface (`#/e/<id>`).
Un journal joint à un ticket, une capture d'écran de la barre d'adresse, une
copie de la base confiée pour un diagnostic : chacun publiait la série de
l'équipement — l'identité du matériel, que le projet tient hors de ses écrans.

Ce qui le remplace est une EMPREINTE : HMAC-SHA256 de la série, sous un sel
propre à l'installation, tronquée à 48 bits et préfixée `eq-`. Pourquoi ce
choix plutôt qu'un identifiant tiré au sort et mémorisé avec l'équipement :

  - déterministe : la même série donne le même identifiant à chaque lancement,
    sans table de correspondance à consulter AVANT de pouvoir nommer
    l'équipement — `pair()` n'a pas de dépôt sous la main, et un appairage par
    le formulaire d'un équipement oublié retombe sur son historique ;
  - non dérivable sans le sel : une série de Peplink a peu d'entropie, un
    simple hachage se retrouverait en essayant les séries plausibles ; le sel
    (32 octets aléatoires) l'interdit ;
  - le sel vit DANS la base (`installation`), à côté des données qu'il
    protège : il est copié par la sauvegarde du lanceur, ne se perd pas
    séparément, et une base neuve — sans historique à retrouver — en tire un
    neuf. La base garde de toute façon la série en clair dans `devices` (le
    trousseau du système est indexé par elle) : le sel n'y ajoute rien.

48 bits : pour une flotte de vingt équipements, une collision a moins d'une
chance sur mille milliards (190 paires sur 2⁴⁸ valeurs).
"""

from __future__ import annotations

import hashlib
import hmac
import re

PREFIXE = "eq-"

# La forme d'une série Peplink une fois passée par `ancien_identifiant` :
# trois groupes de quatre caractères. Sert à la migration seule, pour
# reconnaître l'historique d'un équipement oublié depuis (sa ligne `devices`
# effacée, ses relevés conservés sous l'ancien identifiant).
FORME_ANCIEN_IDENTIFIANT = re.compile(r"^[0-9a-z]{4}(-[0-9a-z]{4}){2}$")


def ancien_identifiant(serie: str) -> str:
    """L'identifiant d'AVANT : la série en minuscules, séparateurs normalisés.

    Ne sert plus qu'à deux choses : reconnaître les données à migrer, et
    normaliser la série avant l'empreinte — deux graphies d'une même série
    (`abcd-…` et `ABCD-…`) donnent le même identifiant.
    """
    return re.sub(r"[^a-z0-9]+", "-", serie.lower()).strip("-")


def identifiant_opaque(serie: str, sel: bytes) -> str:
    """L'identifiant d'un équipement de série `serie`, sous le sel `sel`.

    Idempotent sur la normalisation : `identifiant_opaque(ancien_identifiant(s))
    == identifiant_opaque(s)`, ce qui permet de migrer un historique dont on ne
    connaît plus que l'ancien identifiant.
    """
    empreinte = hmac.new(sel, ancien_identifiant(serie).encode("utf-8"), hashlib.sha256)
    return PREFIXE + empreinte.hexdigest()[:12]
