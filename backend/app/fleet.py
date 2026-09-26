"""Définition de la flotte de routeurs supervisés (depuis routers.yaml, sinon défaut démo)."""

from __future__ import annotations

import logging
import os

import yaml
from pydantic import BaseModel, ValidationError

logger = logging.getLogger(__name__)

# Les champs d'identifiants : des chaînes, même quand YAML y lit un nombre.
_IDENTIFIANTS = frozenset({"client_id", "client_secret", "username", "password"})


class WanConfig(BaseModel):
    """Configuration d'un WAN radio : modem cellulaire ou lien Wi-Fi amont."""

    id: str
    name: str
    kind: str = "cellular"  # "cellular" | "wifi"
    operator: str = "Orange"
    mcc: int = 208
    mnc: int = 1
    # baseline radio pour le simulateur (dBm/dB) — laisse le mock varier autour.
    # Pour un WAN Wi-Fi, base_rsrp tient lieu de force de signal.
    base_rsrp: float = -95.0
    base_sinr: float = 12.0
    # kind == "wifi" uniquement :
    ssid: str | None = None
    channel: int = 112


class RouterConfig(BaseModel):
    """Configuration d'un routeur Peplink."""

    id: str
    name: str
    site_lat: float | None = None
    site_lon: float | None = None
    # mode "router" (API réelle) :
    host: str | None = None
    # Authentification par client API…
    client_id: str | None = None
    client_secret: str | None = None
    # …ou par compte admin (session cookie).
    username: str | None = None
    password: str | None = None
    verify_ssl: bool = False
    # Utiliser le GPS du routeur comme position du site (utile en véhicule mobile).
    use_gps: bool = True
    wans: list[WanConfig] = []

    # Mode propre à cet équipement, qui prime sur PEPLINK_MODE. Permet de faire
    # cohabiter une flotte simulée de démonstration et un équipement réel appairé
    # à chaud, ce qu'un mode global rendait impossible.
    mode: str | None = None
    # Numéro de série, seule clé d'identité stable : l'adresse dépend du réseau
    # où l'on se branche et le nom est modifiable depuis l'administration web.
    serial: str | None = None
    # "yaml" (fichier de flotte) ou "paired" (appairé depuis l'interface).
    source: str = "yaml"


# Flotte de démonstration utilisée si aucun routers.yaml n'est présent.
# Deux sites (Paris + Lyon), deux SIM chacun, opérateurs FR variés.
#
# `mode="mock"` est explicite et délibéré : ces équipements n'ont pas d'adresse,
# ils ne peuvent qu'être simulés. Sans cela, PEPLINK_MODE=router les faisait
# tomber en erreur de configuration — un mode global appliqué à une flotte de
# démonstration n'a pas de sens.
DEFAULT_FLEET: list[RouterConfig] = [
    RouterConfig(
        id="sng-01",
        name="SNG 01 — Flyaway Paris",
        mode="mock",
        site_lat=48.8566,
        site_lon=2.3522,
        wans=[
            WanConfig(id="cellular-1", name="SIM Orange", operator="Orange", mcc=208, mnc=1,
                      base_rsrp=-92.0, base_sinr=15.0),
            WanConfig(id="cellular-2", name="SIM SFR", operator="SFR", mcc=208, mnc=10,
                      base_rsrp=-101.0, base_sinr=8.0),
            # Lien satellite raccordé en Wi-Fi WAN, agrégé aux modems cellulaires.
            WanConfig(id="wifi-1", name="Wi-Fi WAN 5 GHz", kind="wifi",
                      ssid="STARLINK", channel=112, base_rsrp=-62.0),
        ],
    ),
    RouterConfig(
        id="sng-02",
        name="SNG 02 — DSNG Lyon",
        mode="mock",
        site_lat=45.7640,
        site_lon=4.8357,
        wans=[
            WanConfig(id="cellular-1", name="SIM Bouygues", operator="Bouygues", mcc=208, mnc=20,
                      base_rsrp=-88.0, base_sinr=18.0),
            WanConfig(id="cellular-2", name="SIM Free", operator="Free", mcc=208, mnc=15,
                      base_rsrp=-106.0, base_sinr=5.0),
        ],
    ),
]


def load_fleet(path: str, mode: str = "mock") -> list[RouterConfig]:
    """Charge la flotte depuis un YAML ; sans fichier, la démo — en mode simulateur SEULEMENT.

    En mode `router`, une flotte absente est une flotte VIDE : les équipements
    s'appairent depuis l'écran de découverte. C'est le cas normal de
    l'exécutable emporté — pas de `routers.yaml` à côté — et le repli sur la
    démo y faisait apparaître deux MAX HD4 « simulés » sur le tableau de bord
    d'un exploitant qui n'a rien demandé de tel. Vu à la première vérification
    de l'exécutable, le 19 septembre.

    La démo est rendue par COPIE. Les deux replis rendaient la liste du module
    elle-même, et `RouterConfig` est un modèle mutable : le premier appelant qui
    ajoute, retire ou modifie un équipement modifiait la flotte de démonstration
    pour tout le processus — y compris pour les appels suivants, et y compris
    entre deux tests.
    """
    repli = _demo if mode == "mock" else list
    if not path or not os.path.exists(path):
        return repli()
    with open(path, encoding="utf-8") as f:
        raw = yaml.safe_load(f) or {}
    routers = raw.get("routers") or []
    if not routers:
        return repli()
    flotte = []
    for rang, entree in enumerate(routers, start=1):
        if not isinstance(entree, dict):
            logger.warning("routers.yaml, entrée %d : ignorée (pas un dictionnaire)", rang)
            continue
        # YAML lit un identifiant fait de chiffres, sans guillemets, comme un
        # entier : on le rend à sa nature de chaîne plutôt que de le refuser.
        entree = {
            cle: str(val) if cle in _IDENTIFIANTS and isinstance(val, int | float) else val
            for cle, val in entree.items()
        }
        try:
            flotte.append(RouterConfig(**entree))
        except ValidationError as exc:
            # Entrée par entrée, et SANS la valeur fautive : le message de
            # Pydantic porte `input_value`, c'est-à-dire le mot de passe en
            # clair dans la console ; et une entrée fautive ne doit pas
            # emporter le démarrage de tout le serveur (C-15).
            champs = ", ".join(".".join(str(p) for p in e["loc"]) or "?" for e in exc.errors(include_input=False))
            logger.warning("routers.yaml, entrée %d : ignorée (champ invalide : %s)", rang, champs)
    return flotte


def _demo() -> list[RouterConfig]:
    """Une copie PROFONDE de la flotte de démonstration."""
    return [r.model_copy(deep=True) for r in DEFAULT_FLEET]
