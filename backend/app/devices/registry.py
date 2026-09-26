"""Registre des équipements supervisés, modifiable à chaud.

Remplace la flotte figée au démarrage. Trois choses lui appartiennent en propre,
et à lui seul : la configuration de chaque équipement, son client, et son état.

Pourquoi un propriétaire unique : le poller, les routes REST et le service
d'appairage manipulent tous la flotte, depuis des tâches différentes. Sans un
point de mutation unique protégé par un verrou, retirer un équipement pendant un
cycle de scrutation produit des erreurs intermittentes, impossibles à reproduire
sur le terrain.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field

from ..fleet import RouterConfig
from ..models import DeviceCapabilities, DeviceInfo
from ..peplink.base import PeplinkClient
from ..peplink.factory import make_client
from ..peplink.router_api import hote_canonique, redact

logger = logging.getLogger(__name__)


@dataclass
class DeviceRecord:
    """Un équipement et tout ce que l'application en sait."""

    config: RouterConfig
    client: PeplinkClient | None = None

    # Cinq valeurs, et cinq seulement : "en attente" tant qu'aucun cycle n'a
    # abouti, puis "connecté", "identifiants refusés" ou "injoignable" selon ce
    # que le cycle constate, et "erreur de configuration" quand l'équipement
    # n'a même pas pu recevoir de client (voir `add`). Les distinguer est ce qui
    # permet à l'utilisateur de savoir s'il doit corriger un mot de passe,
    # vérifier un câble, ou revoir sa déclaration.
    status: str = "en attente"
    # Cause de l'état ci-dessus, telle qu'affichée à l'utilisateur. Toujours
    # passée par `redact` : elle est publiée par `GET /api/routers`, route sans
    # restriction de provenance, et les messages d'erreur HTTP citent l'URL
    # appelée — donc le jeton d'accès, qui ne voyage qu'en paramètre.
    error: str | None = None

    info: DeviceInfo | None = None
    # Quand `info` a été lue, en horloge MONOTONE : l'ancienneté du châssis
    # qu'elle porte a vieilli d'autant depuis.
    info_lu_a: float | None = None
    capabilities: DeviceCapabilities | None = None
    # Sous-réseaux acheminés PAR le tunnel, tels que le pair les déclare. Sans
    # eux, un débit nul dans le bond ne se distingue pas d'une panne — alors
    # qu'un tunnel dont aucun profil n'est sélectionné dans « Send All Traffic
    # To » ne transporte légitimement que ce qui lui est explicitement routé.
    bond_routes: list[str] = field(default_factory=list)
    # Profils SpeedFusion, recopiés du client à chaque cycle (R-1.5).
    bond_profiles: list = field(default_factory=list)
    # Clients actifs du LAN, recopiés du client à chaque cycle (R-1.10).
    lan_clients: object = None
    # Depuis quand le bond tient, en secondes, selon le routeur. « Depuis quand
    # le tunnel est-il monté ? » n'avait aucune réponse dans l'outil, alors que
    # la valeur arrive dans une charge utile déjà lue à chaque sonde.
    tunnel_uptime_s: int | None = None
    # L'identité est-elle réglée — obtenue, ou abandonnée après quelques essais ?
    # Poser ce drapeau avant la tentative rendait définitif un échec passager (un
    # routeur qui finit de démarrer) ; ne jamais le poser ferait interroger
    # indéfiniment les modèles qui ne publient pas `status.system.info`.
    identified: bool = False
    identify_attempts: int = 0
    # Cycles écoulés depuis la dernière lecture de `status.system.info`. La fiche
    # d'équipement porte trois valeurs PÉRISSABLES — charge CPU, consommation,
    # état des alimentations — que l'interface affiche à côté de données live :
    # lue une seule fois au démarrage, elle annonçait toujours 3,8 % de charge
    # sur un équipement saturé. Le poller la rejoue un cycle sur
    # `_INFO_REFRESH_CYCLES`, et ce compteur est porté par l'ÉQUIPEMENT et non
    # par la boucle : deux équipements appairés à des instants différents
    # rafraîchissent à des cycles différents, au lieu de dépenser leurs 420 ms
    # de requête dans le même.
    cycles_since_info: int = 0
    # Les identifiants ont été refusés par l'équipement : la scrutation
    # l'ignore entièrement jusqu'à une action explicite de l'exploitant.
    # Cinq tentatives ratées suspendent l'accès admin cinq minutes, et le
    # poller en consommait une par cycle — soit un verrouillage en 26 s.
    auth_blocked: bool = False

    @property
    def id(self) -> str:
        return self.config.id


@dataclass
class DeviceRegistry:
    """Flotte vivante : ajout et retrait sans redémarrage."""

    default_mode: str = "mock"
    _records: dict[str, DeviceRecord] = field(default_factory=dict)
    _lock: asyncio.Lock = field(default_factory=asyncio.Lock)

    # --- lecture ---
    def snapshot(self) -> list[DeviceRecord]:
        """Copie de la liste des équipements, sûre à parcourir.

        Le poller itère dessus pendant qu'un appairage peut survenir : sans
        cette copie, la mutation du dictionnaire lèverait en pleine scrutation.
        """
        return list(self._records.values())

    def get(self, device_id: str) -> DeviceRecord | None:
        return self._records.get(device_id)

    def find_by_serial(self, serial: str | None) -> DeviceRecord | None:
        """Retrouve un équipement par son numéro de série, clé d'identité stable."""
        if not serial:
            return None
        return next((r for r in self._records.values() if r.config.serial == serial), None)

    def find_by_host(self, host: str | None) -> DeviceRecord | None:
        """Retrouve un équipement par son adresse, second recours à l'appairage.

        Appelé par `pair()` en second recours, après `find_by_serial`. Ce
        rattrapage a longtemps manqué — la méthode existait, sa docstring
        décrivait le défaut, et personne ne l'appelait. Or le numéro de série d'un
        équipement déclaré dans le fichier de flotte n'est connu qu'APRÈS sa
        première identification réussie — avant, il vaut None, aucune
        correspondance n'est possible, et appairer la même adresse crée un
        second identifiant. La base porte la trace de cet accident réel : le
        même routeur physique supervisé deux fois sous deux identifiants, donc
        compté deux fois, scruté deux fois, et affiché deux fois.

        L'adresse est un indice plus faible que le série — elle dépend du réseau
        où l'on se branche — d'où « second recours » : à n'interroger qu'une fois
        la recherche par série restée vide. Le câblage appartient à
        `pairing/service.py`.
        """
        cible = hote_canonique(host)
        if cible is None:
            return None
        # Sur l'identité, pas sur la graphie : `http://x`, `x` et `x/` sont un
        # seul équipement.
        return next(
            (r for r in self._records.values() if hote_canonique(r.config.host) == cible),
            None,
        )

    # --- mutation ---
    async def add(self, config: RouterConfig, client=None) -> DeviceRecord:
        """Ajoute ou remplace un équipement, et lui construit son client.

        Un client qui ne peut pas être construit — hôte manquant en mode router,
        par exemple — ne fait pas échouer l'ajout : l'équipement est enregistré
        en erreur et reste visible dans l'interface. Un seul équipement mal
        configuré ne doit pas emporter toute la supervision.

        `client` : un client DÉJÀ authentifié, adopté plutôt que reconstruit.
        C'est celui de l'appairage : le fermer puis en bâtir un neuf ouvrait
        une seconde session admin au premier relevé (R-1.13). Il prend
        l'identifiant définitif, connu seulement après l'identification.
        """
        async with self._lock:
            # L'ancien record SORT du registre avant que sa fermeture ne cède
            # la main : pendant `aclose()`, le cycle en cours le tenait encore
            # pour « toujours suivi », toutes ses gardes passaient, et il
            # réécrivait ce que `forget()` venait de purger — le nouveau châssis
            # démarrait avec le `previous` de l'ancien. Reproduit lors de
            # l'audit, sur le ré-appairage d'un équipement déjà supervisé.
            existing = self._records.pop(config.id, None)
            if existing is not None:
                await self._close(existing)

            record = DeviceRecord(config=config)
            try:
                if client is not None:
                    client.adopter(config)
                    record.client = client
                else:
                    record.client = make_client(config, self.default_mode)
            except Exception as exc:
                record.status = "erreur de configuration"
                # Même règle que pour les erreurs de cycle : ce message est
                # publié tel quel par l'API, il ne doit rien porter de secret.
                record.error = redact(str(exc))
                logger.warning("Équipement %s inutilisable : %s", config.id, record.error)

            self._records[config.id] = record
            logger.info(
                "Équipement %s enregistré (%s, source %s)",
                config.id, config.mode or self.default_mode, config.source,
            )
            return record

    async def remove(self, device_id: str) -> bool:
        """Retire un équipement et libère son client."""
        async with self._lock:
            record = self._records.pop(device_id, None)
            if record is None:
                return False
            await self._close(record)
            logger.info("Équipement %s retiré de la supervision", device_id)
            return True

    async def close(self) -> None:
        async with self._lock:
            for record in self._records.values():
                await self._close(record)
            self._records.clear()

    @staticmethod
    async def _close(record: DeviceRecord) -> None:
        if record.client is None:
            return
        try:
            await record.client.aclose()
        except Exception as exc:
            logger.debug("Fermeture du client %s : %s", record.id, exc)
