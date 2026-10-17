"""Collecte du témoin Ethernet : interroge un routeur pour UN WAN Ethernet.

Un tour lit `status.wan.connection` (toutes les 3 s) et range un relevé. Chaque
minute, il lit aussi l'ancienneté du châssis et le journal du routeur, dont les
faits sont rangés pour être rapprochés des coupures. Aucun endpoint `config.*`
n'est appelé.
"""

import asyncio
import logging
from datetime import datetime

import httpx

from app.models import utcnow
from app.peplink.router_api import CredentialsRefusedError, RouterApiError
from temoin.regles import FaitRouteur, Releve

log = logging.getLogger(__name__)

# Pauses après un tour injoignable, puis 30 s tant que ça dure.
RECUL_S = (3.0, 6.0, 12.0, 24.0, 30.0)
# Journal et ancienneté du châssis : une fois par minute.
PERIODE_JOURNAL_S = 60.0
# Au-delà, l'horloge du routeur n'est plus comparable à celle du poste.
DECALAGE_MAX_S = 60.0
# Une lecture d'ancienneté ratée n'est retentée qu'après ce délai.
RETENTE_INFO_S = 30.0
# Ce que le client peut ajouter au relevé WAN (la latence, une fois par minute)
# doit tenir dans ce délai : au-delà, il est abandonné pour ce tour, et le
# relevé WAN, lui, est rangé. Un complément lent ne fait jamais de faux trou.
ECHEANCE_COMPLEMENTS_S = 2.0

# Échecs de lecture : un relevé « non joignable ». Un refus d'identifiants n'en
# fait pas partie (voir `tour`).
_ECHECS = (RouterApiError, httpx.HTTPError, OSError, asyncio.TimeoutError)

# HYPOTHÈSE — à confirmer à la séance de capture (spec §6.7)
# Traduction d'un fait du journal (`RouterEvent.kind`) en genre de `FaitRouteur`.
# Les autres natures (retour, bascule de SIM, priorités, connexion admin) sont
# ignorées. « coupure » devient « test de santé » si sa cause en parle (voir
# `_genre_du_journal`).
_FAITS_DU_JOURNAL = {
    "coupure": "coupure",
    "routeur démarré": "redémarrage",
    "configuration modifiée": "désactivation",
}
_MOTS_TEST_DE_SANTE = ("health check", "health-check", "healthcheck", "dns", "ping")


def _genre_du_journal(kind: str, detail: str | None) -> str | None:
    genre = _FAITS_DU_JOURNAL.get(kind)
    if genre == "coupure" and detail:
        bas = detail.lower()
        if any(mot in bas for mot in _MOTS_TEST_DE_SANTE):
            return "test de santé"
    return genre


class Collecte:
    def __init__(
        self,
        client,
        depot,
        observation: int,
        wan_id: str,
        *,
        cadence: float = 3.0,
        sommeil=asyncio.sleep,
        horloge=utcnow,
    ) -> None:
        self._client = client
        self._depot = depot
        self._observation = observation
        self._wan_id = wan_id
        self._cadence = cadence
        self._sommeil = sommeil
        self._horloge = horloge
        self.etat_lien = "ok"
        self.horloge_decalee = False
        self._noms: dict[str, str] = {}
        self._uptime_routeur: int | None = None
        self._lu_a: datetime | None = None  # heure de la dernière lecture d'ancienneté
        self._journal_a: datetime | None = None
        self._relire_info = True  # vrai au départ et après tout tour injoignable
        self._tentative_a: datetime | None = None  # dernier échec de lecture d'ancienneté
        self._echecs_de_suite = 0

    async def tour(self) -> Releve | None:
        """Une lecture, rangée. None si le WAN choisi est introuvable."""
        maintenant = self._horloge()
        echeance = asyncio.get_running_loop().time() + ECHEANCE_COMPLEMENTS_S
        try:
            etats = await self._client.get_radio_status(echeance=echeance)
        except CredentialsRefusedError:
            # Jamais de relance : cinq refus verrouillent l'accès admin.
            raise
        except _ECHECS as e:
            log.debug("lecture en échec : %s", type(e).__name__)
            self._relire_info = True
            # Injoignable n'est pas introuvable : on ne sait plus rien du lien.
            self.etat_lien = "ok"
            # Le routeur a pu redémarrer : son ancienneté d'avant ne s'extrapole
            # plus. Sans nouvelle lecture réussie, elle reste inconnue.
            self._uptime_routeur = None
            self._lu_a = None
            self._echecs_de_suite += 1
            rel = Releve(
                ts=maintenant, joignable=False, connecte=None, message=None,
                anciennete_s=None, desactive=False, anciennete_routeur_s=None,
            )
            await self._depot.ajouter_releve(self._observation, rel)
            return rel

        self._echecs_de_suite = 0
        self._noms = {e.wan_name: e.wan_id for e in etats}
        lien = next(
            (e for e in etats if e.wan_id == self._wan_id and e.kind == "ethernet"),
            None,
        )
        # Le client écarte de sa liste les WAN désactivés (`enable: false`) et
        # les range à part : un lien désactivé n'est pas un lien disparu.
        desactive = lien is None and self._wan_id in (
            getattr(self._client, "wans_desactives", None) or set()
        )
        if lien is None and not desactive:
            self.etat_lien = "introuvable"
            return None
        self.etat_lien = "ok"

        await self._lire_minute(maintenant)

        if desactive:
            rel = Releve(
                ts=maintenant, joignable=True, connecte=False, message=None,
                anciennete_s=None, desactive=True,
                anciennete_routeur_s=self._anciennete_routeur(maintenant),
            )
        else:
            rel = Releve(
                ts=maintenant,
                joignable=True,
                connecte=lien.connected,
                message=lien.status_message,
                anciennete_s=lien.uptime_s,
                desactive=False,
                anciennete_routeur_s=self._anciennete_routeur(maintenant),
                # Posée par le client une fois par minute, None entre deux.
                latence_ms=lien.path_latency_ms,
            )
        await self._depot.ajouter_releve(self._observation, rel)
        return rel

    def _anciennete_routeur(self, maintenant: datetime) -> int | None:
        if self._uptime_routeur is None or self._lu_a is None:
            return None
        return int(self._uptime_routeur + (maintenant - self._lu_a).total_seconds())

    async def _lire_minute(self, maintenant: datetime) -> None:
        """Ancienneté du châssis et journal, sans jamais faire échouer le relevé."""
        echu = (
            self._journal_a is None
            or (maintenant - self._journal_a).total_seconds() >= PERIODE_JOURNAL_S
        )
        if not (echu or self._relire_info):
            return
        if (
            self._tentative_a is not None
            and (maintenant - self._tentative_a).total_seconds() < RETENTE_INFO_S
        ):
            return
        try:
            info = await self._client.get_device_info()
        except CredentialsRefusedError:
            raise
        except _ECHECS as e:
            log.debug("ancienneté du routeur illisible : %s", type(e).__name__)
            info = None
        if info is None:
            # Le client rend None quand il échoue : sans l'écart d'horloge, on ne
            # sait pas si le journal est rapprochable. On retente plus tard.
            self._relire_info = True
            self._tentative_a = maintenant
            return
        self._relire_info = False
        self._tentative_a = None
        if info.uptime_s is not None:
            self._uptime_routeur = int(info.uptime_s)
            self._lu_a = maintenant
        if info.clock_offset_s is not None:
            self.horloge_decalee = abs(info.clock_offset_s) > DECALAGE_MAX_S
        if echu:
            self._journal_a = maintenant
            await self._lire_journal()

    async def _lire_journal(self) -> None:
        if self.horloge_decalee:
            # Les heures du journal ne sont plus celles du poste : on ne les
            # rapproche pas des coupures.
            return
        try:
            evenements = await self._client.get_router_events(dict(self._noms))
        except CredentialsRefusedError:
            raise
        except _ECHECS as e:
            log.debug("journal illisible : %s", type(e).__name__)
            return
        faits = []
        for ev in evenements:
            if ev.wan_id not in (None, self._wan_id):
                continue
            genre = _genre_du_journal(ev.kind, ev.detail)
            if genre is not None:
                faits.append(FaitRouteur(ts=ev.ts, genre=genre, detail=ev.detail))
        if faits:
            await self._depot.ajouter_faits(self._observation, faits)

    async def boucle(self) -> None:
        """Tours à la cadence ; recul 3/6/12/24/30 s tant que le routeur est injoignable."""
        while True:
            rel = await self.tour()
            if rel is not None and not rel.joignable:
                i = min(self._echecs_de_suite, len(RECUL_S)) - 1
                pause = RECUL_S[i]
            else:
                pause = self._cadence
            await self._sommeil(pause)
