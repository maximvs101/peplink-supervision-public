"""Le coffre des identifiants : le trousseau du système, jamais un fichier.

Jusqu'ici, les identifiants d'un équipement appairé ne vivaient qu'en mémoire,
le temps d'une session : les écrire en clair sur le disque aurait été pire que
de les redemander. Le prix était de les ressaisir à chaque lancement — vécu
trois fois de suite lors de la recette d'octobre 2026.

Le trousseau du système est le coffre chiffré qui manquait : Trousseau sur
macOS, Gestionnaire d'identification sur Windows, via `keyring`. Il est chiffré
par le système, lié à la session de l'utilisateur, et rien n'en sort sans elle.

Trois règles :
  - **une entrée par équipement, clé = numéro de série** — l'identité stable,
    celle qui survit à un changement d'adresse ;
  - **rien ne sort d'ici vers l'API ni vers un journal** : seul le collecteur
    les reçoit, comme lorsqu'ils étaient saisis ;
  - **un trousseau indisponible n'est pas une panne** : chaque fonction rend
    « rien » et l'outil se comporte comme avant — identifiants à ressaisir.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass

logger = logging.getLogger(__name__)

SERVICE = "peplink-supervision"


@dataclass(frozen=True)
class Identifiants:
    username: str | None = None
    password: str | None = None
    client_id: str | None = None
    client_secret: str | None = None

    def __repr__(self) -> str:  # jamais un secret dans une trace
        return f"Identifiants(username={self.username!r}, client_id={self.client_id!r}, …)"


def _trousseau():
    """Le backend retenu, ou None s'il n'en existe aucun d'utilisable."""
    try:
        import keyring
        from keyring.backends import fail

        backend = keyring.get_keyring()
        if isinstance(backend, fail.Keyring):
            return None
        return backend
    except Exception as exc:  # paquet absent, backend cassé
        logger.info("Trousseau du système indisponible : %s", type(exc).__name__)
        return None


def nom_du_trousseau() -> str | None:
    """Le nom du trousseau employé, pour `/api/health` ; None sans trousseau."""
    backend = _trousseau()
    return getattr(backend, "name", type(backend).__name__) if backend else None


def retenir(serie: str, ident: Identifiants) -> bool:
    backend = _trousseau()
    if backend is None or not serie:
        return False
    try:
        backend.set_password(SERVICE, serie, json.dumps({
            "username": ident.username, "password": ident.password,
            "client_id": ident.client_id, "client_secret": ident.client_secret,
        }))
        return True
    except Exception as exc:
        # Le type seulement : le message d'un backend peut citer ce qu'on lui a passé.
        # Jamais la série dans le journal (règle du projet).
        logger.warning("Identifiants non retenus : %s", type(exc).__name__)
        return False


def lire(serie: str) -> Identifiants | None:
    backend = _trousseau()
    if backend is None or not serie:
        return None
    try:
        brut = backend.get_password(SERVICE, serie)
        if not brut:
            return None
        d = json.loads(brut)
        ident = Identifiants(
            username=d.get("username"), password=d.get("password"),
            client_id=d.get("client_id"), client_secret=d.get("client_secret"),
        )
        return ident if (ident.username or ident.client_id) else None
    except Exception as exc:
        # Jamais la série dans le journal (règle du projet).
        logger.warning("Identifiants illisibles : %s", type(exc).__name__)
        return None


def oublier(serie: str) -> None:
    backend = _trousseau()
    if backend is None or not serie:
        return
    try:
        backend.delete_password(SERVICE, serie)
    except Exception:
        # Rien à effacer est l'issue normale d'un équipement jamais retenu.
        pass
