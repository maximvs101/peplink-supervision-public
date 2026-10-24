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

import asyncio
import logging
import re

from ..devices.registry import DeviceRecord, DeviceRegistry
from ..fleet import RouterConfig
from ..identite import identifiant_opaque
from ..peplink.router_api import (
    AuthenticationError,
    RouterApiError,
    RouterApiPeplinkClient,
)

logger = logging.getLogger(__name__)

# Nombre de tentatives avant suspension, côté équipement (firmware >= 7.0.1).
LOCKOUT_AFTER = 5


class PairingError(RuntimeError):
    """Échec d'appairage, avec un message destiné à l'utilisateur.

    `autre_equipement` : la connexion a abouti, mais sur un AUTRE équipement
    que celui attendu — ou sur un équipement sans série. Ce n'est ni un refus
    ni une absence : la veille recule alors bien plus loin (voir `Reconnexion`).
    """

    def __init__(
        self, message: str, *, credentials: bool = False, autre_equipement: bool = False,
    ) -> None:
        super().__init__(message)
        self.credentials = credentials
        self.autre_equipement = autre_equipement
        # Les identifiants du trousseau RÉELLEMENT essayés, posés par
        # `reconnecter` : c'est leur refus que la veille doit retenir.
        self.identifiants: object | None = None


def _device_id(serial: str | None, host: str, sel: bytes) -> str:
    """Identifiant stable, dérivé du numéro de série — mais OPAQUE.

    Il valait la série en minuscules, et se retrouvait donc dans chaque ligne
    de journal, chaque ligne de la base, chaque URL de l'API et de l'interface.
    C'est désormais son empreinte sous le sel de l'installation (voir
    `app/identite.py`) : la même série donne le même identifiant à chaque
    lancement, sans qu'on puisse en remonter à la série.

    À défaut de série, l'adresse, qui n'est stable que le temps d'un
    branchement — et ne dit rien du matériel.
    """
    if serial:
        return identifiant_opaque(serial, sel)
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
    serie_attendue: str | None = None,
) -> DeviceRecord:
    """Connecte une fois, identifie l'équipement, et l'ajoute à la supervision.

    Lève `PairingError` sans jamais réessayer. La distinction entre un refus
    d'identifiants et une injoignabilité est portée jusqu'à l'interface : elle
    décide si l'utilisateur doit corriger un mot de passe ou vérifier un câble.

    Sous le verrou d'appairage de l'HÔTE (`registry.verrou_appairage`), partagé
    avec la veille de reconnexion : deux appairages de la même adresse se
    suivent au lieu d'ouvrir deux connexions admin et deux `registry.add`.
    """
    async with registry.verrou_appairage(host):
        return await _pair_sous_verrou(
            registry, host, username=username, password=password,
            client_id=client_id, client_secret=client_secret, name=name,
            verify_ssl=verify_ssl, poller=poller, serie_attendue=serie_attendue,
        )


async def _pair_sous_verrou(
    registry: DeviceRegistry,
    host: str,
    *,
    username: str | None,
    password: str | None,
    client_id: str | None,
    client_secret: str | None,
    name: str | None,
    verify_ssl: bool,
    poller,
    serie_attendue: str | None,
) -> DeviceRecord:
    """`pair()`, l'appelant tenant déjà le verrou d'appairage de `host`."""
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
            poller=poller, serie_attendue=serie_attendue,
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
    serie_attendue: str | None = None,
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
        # Connecté, mais sans identité : pour la veille, c'est un écart de
        # série comme un autre — elle ne peut pas savoir qui répond.
        raise PairingError(
            "Connexion réussie, mais l'équipement n'a pas publié son identité. "
            "Il est peut-être trop ancien pour être supervisé.",
            autre_equipement=serie_attendue is not None,
        )
    # Une reconnexion vise UN équipement. Un autre occupe peut-être l'adresse
    # retenue — même mot de passe, courant sur une flotte — : l'appairer en
    # superviserait un que personne n'a choisi, et la veille recommencerait à
    # chaque tour, une connexion admin toutes les trois secondes (revue du 17/10).
    if serie_attendue and info.serial != serie_attendue:
        raise PairingError(
            f"Un autre équipement répond à {host} : ce n'est pas celui qui y était retenu.",
            autre_equipement=True,
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
    device_id = existing.id if existing else _device_id(info.serial, host, registry.sel_identifiants)

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
    # Ni la série ni rien qui en dérive en clair : l'identifiant opaque suffit
    # à retrouver l'équipement dans la suite du journal.
    logger.info(
        "Équipement appairé : %s · %s · identifiant %s", config.name, info.model or "?", config.id
    )
    return record


async def reconnecter(registry: DeviceRegistry, repo, connu: dict, *, poller=None) -> DeviceRecord:
    """Réappaire un équipement déjà vu, avec les identifiants du trousseau.

    La même règle que l'appairage : UNE tentative, et un refus remonte tel quel.
    Une entrée refusée n'est pas effacée — l'exploitant corrige en ressaisissant,
    et le formulaire la remplace.
    """
    from .. import coffre

    serie = connu["serial"]
    # Le verrou d'appairage de l'HÔTE, celui que `pair()` prend aussi : la
    # veille, « Reconnecter » et le formulaire pouvaient appairer le même
    # routeur au même instant — deux connexions admin, et deux `registry.add`
    # qui se remplacent (revues du 17/10 et du 24/10). Le verrou par série ne
    # couvrait pas le formulaire, qui ne connaît pas encore la série.
    async with registry.verrou_appairage(connu["host"]):
        deja = registry.find_by_serial(serie)
        if deja is not None:
            return deja
        ident = coffre.lire(serie)
        if ident is None:
            raise PairingError("Aucun identifiant retenu pour cet équipement.", credentials=True)
        try:
            record = await _pair_sous_verrou(
                registry, connu["host"],
                username=ident.username, password=ident.password,
                client_id=ident.client_id, client_secret=ident.client_secret,
                name=connu.get("name"), verify_ssl=False, poller=poller, serie_attendue=serie,
            )
        except PairingError as exc:
            # Lus SOUS le verrou : une ressaisie pendant l'attente du verrou
            # fait que l'appelant (la veille) a lu d'autres identifiants que
            # ceux-ci. Le refus dit lesquels il concerne.
            exc.identifiants = ident
            raise
        if record.info and record.info.serial:
            await repo.remember_device(
                serial=record.info.serial, device_id=record.id, name=record.config.name,
                host=record.config.host, model=record.info.model, firmware=record.info.firmware,
            )
        return record


# Plafond du recul de la veille après un ÉCART DE SÉRIE : un autre routeur
# occupe l'adresse retenue, ou l'équipement n'a pas publié de série. Chaque
# tentative y est une connexion admin RÉUSSIE sur un routeur que personne n'a
# choisi ; au plafond ordinaire de 30 s, elles se répétaient indéfiniment. Cinq
# minutes : la situation ne se résout pas d'elle-même en quelques secondes.
_RECUL_ECART_MAX_S = 300.0


def _nom_journal(connu: dict) -> str:
    """Comment nommer un équipement retenu dans le journal : son nom, sinon
    son identifiant opaque, sinon son modèle — jamais sa série."""
    return connu.get("name") or connu.get("device_id") or connu.get("model") or "équipement retenu"


class Reconnexion:
    """La veille des équipements retenus : au lancement, puis dès qu'ils
    apparaissent sur le réseau (A-1).

    Une seule tentative au lancement ne suffisait pas : le véhicule arrive
    souvent après l'ouverture de l'outil, le routeur s'allume alors, et il
    fallait cliquer « Reconnecter ». Chaque équipement retenu mais pas encore
    supervisé est donc retenté, avec le recul de la scrutation — la cadence,
    doublée à chaque échec, jusqu'à `_RECUL_MAX_S` — pour qu'un équipement
    absent ne coûte pas un délai de connexion à chaque tour.

    Un refus d'identifiants arrête tout pour cet équipement : cinq échecs
    suspendent l'accès admin cinq minutes, et une veille qui insiste
    verrouillerait le routeur. Elle ne reprend que si le trousseau garde
    d'AUTRES identifiants — ce qu'une ressaisie y range.
    """

    def __init__(self, registry: DeviceRegistry, repo, poller, *, cadence: float) -> None:
        self._registry, self._repo, self._poller = registry, repo, poller
        self._cadence = cadence
        self._prochain: dict[str, float] = {}
        self._pause: dict[str, float] = {}
        self._refuses: dict[str, object] = {}

    async def tour(self, maintenant: float) -> None:
        """Les équipements dus, tentés EN MÊME TEMPS : un absent coûte jusqu'à
        quinze secondes de délai de connexion, et il retardait d'autant celui
        qui venait de s'allumer (revue du 17/10)."""
        dus = []
        for connu in await self._repo.known_devices():
            serie = connu.get("serial")
            if not serie or not connu.get("host"):
                continue
            if self._registry.find_by_serial(serie) is not None:
                self._prochain.pop(serie, None)
                self._pause.pop(serie, None)
                continue
            if maintenant < self._prochain.get(serie, 0.0):
                continue
            dus.append(connu)
        await asyncio.gather(*(self._tenter(c, maintenant) for c in dus))

    async def _tenter(self, connu: dict, maintenant: float) -> None:
        from .. import coffre
        from ..poller import _RECUL_MAX_S

        serie = connu["serial"]
        ident = coffre.lire(serie)
        if ident is None or self._refuses.get(serie) == ident:
            return
        self._refuses.pop(serie, None)
        try:
            await reconnecter(self._registry, self._repo, connu, poller=self._poller)
        except PairingError as exc:
            if exc.credentials:
                # Les identifiants que `reconnecter` a VRAIMENT essayés, relus
                # sous le verrou d'appairage — pas ceux lus ici avant de
                # l'attendre. Une ressaisie entre les deux faisait retenir les
                # anciens comme refusés, et le tour suivant rejouait le refus
                # des courants.
                self._refuses[serie] = exc.identifiants if exc.identifiants is not None else ident
                logger.warning(
                    "Reconnexion de %s refusée : plus de tentative tant que les identifiants "
                    "retenus ne changent pas", _nom_journal(connu),
                )
            else:
                plafond = _RECUL_ECART_MAX_S if exc.autre_equipement else _RECUL_MAX_S
                pause = min(plafond, self._pause.get(serie, self._cadence / 2) * 2)
                self._pause[serie] = pause
                self._prochain[serie] = maintenant + pause
                logger.debug("Reconnexion de %s : %s — prochain essai dans %.0f s",
                             _nom_journal(connu), exc, pause)
        else:
            self._pause.pop(serie, None)
            self._prochain.pop(serie, None)
            logger.info("Équipement retenu reconnecté : %s", _nom_journal(connu))

    async def boucle(self) -> None:
        """Un tour par cadence, pour toujours ; une erreur coûte un tour."""
        import time

        while True:
            try:
                await self.tour(time.monotonic())
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("Veille de reconnexion : tour ignoré")
            await asyncio.sleep(self._cadence)
