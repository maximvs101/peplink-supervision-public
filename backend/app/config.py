"""Configuration de l'application, chargée depuis l'environnement / fichier .env."""

from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

# Racine du backend (dossier parent de app/) : les chemins relatifs y sont résolus,
# pour que l'application démarre correctement quel que soit le répertoire courant.
BASE_DIR = Path(__file__).resolve().parent.parent


class Settings(BaseSettings):
    """Paramètres globaux (surchargables par variables d'environnement ou .env)."""

    # Le fichier `.env` : celui du dépôt, sauf si `ENV_FILE` en désigne un
    # autre — l'exécutable emporté n'a pas de dépôt, et lit celui posé à côté
    # de lui (voir lancer.py).
    model_config = SettingsConfigDict(
        env_file=os.environ.get("ENV_FILE", str(BASE_DIR / ".env")),
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # "mock" (simulateur) ou "router" (API locale réelle)
    # Validé, et non libre : le repli de `make_client` est le SIMULATEUR, si
    # bien qu'une faute de frappe dans `.env` — « Router » au lieu de
    # « router » — faisait afficher des mesures INVENTÉES sans badge, pendant
    # que `/api/health` publiait fièrement le mode saisi. Une supervision qui
    # présente du simulateur comme du réel.
    peplink_mode: Literal["router", "mock"] = "mock"
    poll_interval_seconds: float = 3.0

    database_path: str = "./data/samples.db"
    # Jours conservés à pleine résolution. Au-delà, les sessions closes sont
    # figées (fiche et journal), puis les relevés regroupés à la minute et
    # effacés, en tâche de fond (`app/retention.py`). Trente jours : une
    # intervention se relit en détail pendant un mois, et la base cesse de
    # croître au-delà. 0 désactive la rétention — tout est gardé.
    retention_jours: int = Field(30, ge=0)
    routers_config: str = "./routers.yaml"
    # L'interface compilée. Relatif à la racine du backend, comme les deux
    # chemins ci-dessus — et surchargeable, parce qu'un exécutable emporté ne
    # contient plus `../frontend/dist` : il embarque l'interface où il peut et
    # le dit ici. Figé dans main.py, ce chemin rendait l'exécutable muet : le
    # serveur répondait, l'interface était un 404. Mesuré le 19 septembre.
    frontend_dist: str = "../frontend/dist"

    # Origines autorisées à interroger l'API depuis un navigateur.
    #
    # Par défaut, le serveur de développement Vite seulement. En production
    # l'interface est servie par ce même backend, donc de même origine : aucune
    # entrée n'est nécessaire. "*" reste possible mais désactive l'envoi des
    # cookies (voir main.py) : la spécification CORS l'interdit, et Starlette
    # contourne l'interdiction en renvoyant l'origine de la requête.
    cors_origins: str = "http://localhost:5173,http://127.0.0.1:5173"

    # Noms d'hôte admis EN PLUS de `localhost` et des adresses IP littérales —
    # le nom du poste sur le réseau local, s'il sert à ouvrir l'écran. Tout
    # autre nom est refusé : c'est la parade au DNS rebinding (C-3).
    allowed_hosts: str = ""

    @property
    def allowed_host_list(self) -> list[str]:
        return [h.strip() for h in self.allowed_hosts.split(",") if h.strip()]

    @property
    def cors_origin_list(self) -> list[str]:
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]

    @property
    def cors_allows_any_origin(self) -> bool:
        return "*" in self.cors_origin_list

    @property
    def database_file(self) -> str:
        """Chemin absolu de la base (relatif -> résolu depuis la racine du backend)."""
        return self._resolve(self.database_path)

    @property
    def routers_file(self) -> str:
        """Chemin absolu du fichier de flotte."""
        return self._resolve(self.routers_config)

    @property
    def frontend_dir(self) -> Path:
        """Dossier de l'interface compilée, absolu."""
        return Path(self._resolve(self.frontend_dist))

    @staticmethod
    def _resolve(value: str) -> str:
        path = Path(value)
        return str(path if path.is_absolute() else BASE_DIR / path)


@lru_cache
def get_settings() -> Settings:
    return Settings()
