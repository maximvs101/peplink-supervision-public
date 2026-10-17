"""Appairage d'un équipement : une tentative, déclenchée par l'utilisateur.

RÈGLE CENTRALE, dont tout le reste découle : **une seule tentative de connexion
par demande explicite. Jamais de relance automatique, jamais d'essai de mots de
passe.** Cinq tentatives ratées suspendent l'accès à l'administration depuis
cette adresse pendant cinq minutes, y compris avec les bons identifiants.

En conséquence :
  - un refus d'identifiants remonte tel quel, et le poller ne réessaie pas ;
  - le nombre de tentatives restantes est communiqué pour que l'interface le
    montre plutôt que de laisser l'utilisateur s'enfermer dehors ;
  - aucun identifiant d'usine n'est essayé sans demande explicite.

Le numéro de série lu après connexion sert de clé d'identité : l'adresse dépend
du réseau où l'on se branche, et le nom est modifiable depuis l'administration.
"""

from __future__ import annotations

import logging
import re

from ..devices.registry import DeviceRecord, DeviceRegistry
from ..fleet import RouterConfig
from ..peplink.router_api import (
    AuthenticationError,
    RouterApiError,
    RouterApiPeplinkClient,
)

logger = logging.getLogger(__name__)

# Nombre de tentatives avant suspension, côté équipement (firmware >= 7.0.1).
LOCKOUT_AFTER = 5


class PairingError(RuntimeError):
    """Échec d'appairage, avec un message destiné à l'utilisateur."""

    def __init__(self, message: str, *, credentials: bool = False) -> None:
        super().__init__(message)
        self.credentials = credentials


def _device_id(serial: str | None, host: str) -> str:
    """Identifiant stable et lisible, dérivé du numéro de série.

    Le série sert de clé ; à défaut, l'adresse, qui n'est stable que le temps
    d'un branchement.
    """
    if serial:
        return re.sub(r"[^a-z0-9]+", "-", serial.lower()).strip("-")
    return re.sub(r"[^a-z0-9]+", "-", host.lower()).strip("-")


async def pair(
    registry: DeviceRegistry,
    host: str,
    *,
    username: str | None = None,
    password: str | None = None,
    client_id: str | None = None,
    client_secret: str | None = None,
    name: str | None = None,
    verify_ssl: bool = False,
    poller=None,
) -> DeviceRecord:
    """Connecte une fois, identifie l'équipement, et l'ajoute à la supervision.

    Lève `PairingError` sans jamais réessayer. La distinction entre un refus
    d'identifiants et une injoignabilité est portée jusqu'à l'interface : elle
    décide si l'utilisateur doit corriger un mot de passe ou vérifier un câble.
    """
    if not (username or client_id):
        raise PairingError(
            "Renseigner un compte administrateur ou un client API.", credentials=True
        )

    probe_config = RouterConfig(
        id=f"appairage-{host}", name=name or host, host=host,
        username=username, password=password,
        client_id=client_id, client_secret=client_secret,
        verify_ssl=verify_ssl, mode="router",
    )

    client = RouterApiPeplinkClient(probe_config)
    # Le client qui a identifié l'équipement est celui qui le scrutera : le
    # fermer ici ouvrait une seconde session admin au premier relevé — deux
    # « login successful » à quatre secondes d'écart pour un appairage
    # (R-1.13). Il n'est fermé que si l'appairage n'aboutit pas.
    try:
        return await _identifier_puis_enregistrer(
            registry, client, host, name=name, username=username, password=password,
            client_id=client_id, client_secret=client_secret, verify_ssl=verify_ssl,
            poller=poller,
        )
    except BaseException:
        await client.aclose()
        raise


async def _identifier_puis_enregistrer(
    registry: DeviceRegistry,
    client: RouterApiPeplinkClient,
    host: str,
    *,
    name: str | None,
    username: str | None,
    password: str | None,
    client_id: str | None,
    client_secret: str | None,
    verify_ssl: bool,
    poller,
) -> DeviceRecord:
    """Identifie l'équipement avec `client`, puis l'enregistre en lui confiant
    ce même client. Toute erreur remonte à `pair`, qui ferme le client."""
    try:
        # Une seule lecture, en mode strict : elle authentifie et confirme que
        # l'API répond. `get_device_info` absorberait l'erreur, ce qui est le bon
        # comportement en supervision mais empêcherait ici de dire quoi corriger.
        info = await client.identify()
    except AuthenticationError as exc:
        logger.info("Appairage refusé sur %s : %s", host, exc)
        raise PairingError(
            "Identifiants refusés. Attention : cinq tentatives ratées suspendent "
            f"l'accès à l'équipement pendant 5 minutes (limite : {LOCKOUT_AFTER}).",
            credentials=True,
        ) from exc
    except RouterApiError as exc:
        raise PairingError(f"L'équipement a refusé la lecture : {exc}") from exc
    except Exception as exc:
        raise PairingError(
            f"Équipement injoignable en {host} : {type(exc).__name__}. "
            "Vérifier l'adresse et le raccordement."
        ) from exc
    if info is None:
        raise PairingError(
            "Connexion réussie, mais l'équipement n'a pas publié son identité. "
            "Il est peut-être trop ancien pour être supervisé."
        )

    # Le numéro de série d'abord : c'est la clé d'identité stable, celle qui
    # survit à un changement d'adresse.
    #
    # L'ADRESSE en second recours, et c'est le rattrapage qui manquait. Un
    # équipement déclaré dans le fichier de flotte n'a pas de série tant qu'il
    # n'a pas été identifié une première fois : avant cela il vaut `None`,
    # `find_by_serial` ne peut rien rapprocher, et appairer la même adresse
    # créait un SECOND identifiant. La base porte la trace de cet accident réel
    # — le même routeur physique supervisé deux fois, donc compté deux fois,
    # scruté deux fois et affiché deux fois. `find_by_host` existait depuis le
    # début et documentait ce défaut au lieu de le corriger : personne ne
    # l'appelait.
    #
    # L'adresse est un indice plus faible que le série — elle dépend du réseau
    # où l'on se branche —, d'où l'ordre : on ne s'y fie que si le série n'a rien
    # donné, et seulement pour un record qui n'a pas ENCORE de série. Un record
    # dont le série est connu et DIFFÉRENT est un autre équipement qui occupe
    # maintenant cette adresse : le rapprocher serait l'erreur inverse.
    existing = registry.find_by_serial(info.serial)
    if existing is None:
        candidat = registry.find_by_host(host)
        if candidat is not None and not candidat.config.serial:
            existing = candidat
            logger.info(
                "Équipement déjà connu à l'adresse %s sous l'identifiant %s : "
                "appairage rattaché plutôt que dédoublé",
                host, candidat.id,
            )
    device_id = existing.id if existing else _device_id(info.serial, host)

    config = RouterConfig(
        id=device_id,
        name=name or info.name or info.model or host,
        host=host,
        username=username, password=password,
        client_id=client_id, client_secret=client_secret,
        verify_ssl=verify_ssl,
        mode="router",
        serial=info.serial,
        source="paired",
    )
    # Ce qui portait cet identifiant AVANT est effacé. `registry.add` remplace le
    # record sans purger l'état du poller, et l'identifiant se dérive de
    # l'ADRESSE quand le série manque : un matériel remplacé sur site à la même
    # adresse héritait des compteurs du précédent — vérifié, 18 changements de
    # cellule et 5 coupures sur le PREMIER relevé du neuf, hérités et incrémentés
    # — de son dernier relevé, de sa position, et de sa session d'alimentation.
    if poller is not None:
        # Les abonnés restent : c'est le même identifiant, toujours supervisé.
        poller.forget(config.id, conserver_abonnes=True)
    record = await registry.add(config, client=client)
    record.info = info
    logger.info(
        "Équipement appairé : %s · %s · série %s", config.name, info.model or "?", info.serial
    )
    return record


async def reconnecter(registry: DeviceRegistry, repo, connu: dict, *, poller=None) -> DeviceRecord:
    """Réappaire un équipement déjà vu, avec les identifiants du trousseau.

    La même règle que l'appairage : UNE tentative, et un refus remonte tel quel.
    Une entrée refusée n'est pas effacée — l'exploitant corrige en ressaisissant,
    et le formulaire la remplace.
    """
    from .. import coffre

    ident = coffre.lire(connu["serial"])
    if ident is None:
        raise PairingError("Aucun identifiant retenu pour cet équipement.", credentials=True)
    record = await pair(
        registry, connu["host"],
        username=ident.username, password=ident.password,
        client_id=ident.client_id, client_secret=ident.client_secret,
        name=connu.get("name"), poller=poller,
    )
    if record.info and record.info.serial:
        await repo.remember_device(
            serial=record.info.serial, device_id=record.id, name=record.config.name,
            host=record.config.host, model=record.info.model, firmware=record.info.firmware,
        )
    return record


async def reconnecter_retenus(registry: DeviceRegistry, repo, poller) -> None:
    """Au lancement : une tentative pour chaque équipement dont le trousseau
    garde les identifiants, et rien d'autre.

    Ce n'est pas une relance : c'est la tentative que l'exploitant faisait à la
    main à chaque démarrage, et elle n'est jamais répétée. Un équipement absent
    du réseau ou qui refuse reste à reconnecter depuis l'écran des équipements.
    """
    from .. import coffre

    for connu in await repo.known_devices():
        if not connu.get("host") or registry.find_by_serial(connu.get("serial")) is not None:
            continue
        if coffre.lire(connu["serial"]) is None:
            continue
        try:
            await reconnecter(registry, repo, connu, poller=poller)
        except PairingError as exc:
            logger.info("Reconnexion de %s au lancement : %s", connu.get("name") or connu["serial"], exc)
