"""Rétention de la base : ce qui reste d'un relevé au-delà de `RETENTION_JOURS`.

Un relevé par lien toutes les cinq secondes fait des dizaines de millions de
lignes par an, et rien ne les retirait. Mais un regroupement naïf détruit ce
que la base sait dire de plus précieux : une micro-coupure n'existe qu'entre
deux relevés consécutifs (un `uptime` qui recule), un volume s'intègre relevé
à relevé, une disponibilité se compte par cycle. Aucun de ces calculs ne
survit à une moyenne par minute.

L'ordre est donc celui-ci, à chaque passe, en tâche de fond :

1. FIGER chaque session close depuis plus de la durée de conservation : sa
   fiche — calculée exactement comme la route la calcule — et son journal sur
   la fenêtre que l'écran demande. Relue ensuite, la session affiche la même
   chose qu'avant, au chiffre près.
2. REGROUPER à la minute, heure par heure et équipement par équipement, les
   relevés plus vieux que la durée de conservation moins vingt-quatre heures,
   et les effacer (`SampleRepository.regrouper_lot`). La toute première purge
   d'une base est précédée d'une copie dédiée.
3. COMPACTER : rendre au système les pages libérées.

Chaque lot est une transaction courte sous le verrou d'écriture : la scrutation
n'attend jamais plus que quelques dizaines de millisecondes, et un arrêt au
milieu n'annule que le lot en cours.
"""

from __future__ import annotations

import asyncio
import json
import logging
from datetime import datetime, timedelta

from .models import Session, SessionSummary, utcnow
from .sessions import build_summary
from .storage.db import _FENETRE_SESSION_S, SampleRepository

logger = logging.getLogger(__name__)


async def calculer_fiche(repo: SampleRepository, lignes: dict) -> SessionSummary:
    """La fiche d'une session depuis ses relevés — l'unique façon de la calculer.

    La route et la rétention l'appellent toutes deux : une fiche figée est
    celle que la route aurait rendue, et non une variante recalculée à part.
    Les manœuvres, qui viennent du journal du routeur (conservé), sont
    ajoutées par la route à chaque lecture.
    """
    session = Session(**lignes)
    releves, fenetre = await repo.session_rows(
        session.router_id, lignes["started_at"], lignes["last_seen"]
    )
    # La latence du lien, selon la série du routeur, sur ce qui a été relu.
    lu_de = fenetre[0].isoformat() if fenetre else lignes["started_at"]
    lu_a = fenetre[1].isoformat() if fenetre else lignes["last_seen"]
    series = await repo.latency_series(session.router_id, lu_de, lu_a)
    # Le RSRP des trous de supervision, à l'heure du routeur (R-3.1).
    combles = await repo.filled_rsrp(session.router_id, lu_de, lu_a)
    # Hors de la boucle : sur une longue session, percentiles et intégration
    # coûtent des centaines de millisecondes.
    return await asyncio.to_thread(build_summary, session, releves, fenetre, series, combles)


def fenetre_du_journal(lignes: dict) -> tuple[datetime, datetime] | None:
    """La fenêtre sur laquelle l'écran relit le journal d'une session passée.

    Du premier relevé — `debutObserve` côté écran — à `last_seen`, rognée aux
    vingt-quatre heures que la route relit au plus (`_plafonnee`). None pour
    une session sans relevé, dont le journal est vide.
    """
    debut = lignes.get("first_seen") or lignes["started_at"]
    fin = datetime.fromisoformat(lignes["last_seen"])
    debut_dt = max(datetime.fromisoformat(debut), fin - timedelta(seconds=_FENETRE_SESSION_S))
    if debut_dt >= fin:
        return None
    return debut_dt, fin


class Retention:
    """La tâche de fond qui fige, regroupe et compacte."""

    def __init__(
        self, repo: SampleRepository, jours: int, seuil_rtt: float, *, pause_s: float = 0.2,
    ) -> None:
        self._repo = repo
        # 0 : rétention désactivée, tout est gardé à pleine résolution.
        self._jours = jours
        # Le garde-fou absolu du journal (`_SEUIL_EPISODE_MS` des routes) : le
        # journal figé l'a été avec lui, et ne vaut que pour lui.
        self._seuil_rtt = seuil_rtt
        # Entre deux lots, la main à la scrutation et aux routes.
        self._pause_s = pause_s
        # Les sessions dont la fiche n'a pas pu être figée, et pourquoi :
        # publiées par `/api/health`, dites une fois au journal.
        self.echecs: dict[int, str] = {}

    async def figer(self, lignes: dict) -> None:
        """Fige la fiche et le journal d'une session, sur ses relevés bruts."""
        fiche = await calculer_fiche(self._repo, lignes)
        fenetre = fenetre_du_journal(lignes)
        episodes: list[dict] = []
        brassage: list[dict] = []
        if fenetre is not None:
            episodes, brassage = await self._repo.events(
                lignes["router_id"], fenetre[0], fenetre[1], self._seuil_rtt)
        journal_de = (fenetre[0] if fenetre else datetime.fromisoformat(lignes["last_seen"]))
        await self._repo.figer_session(
            lignes, self._seuil_rtt, journal_de.isoformat(), fiche.model_dump_json(),
            json.dumps({"episodes": episodes, "brassage": brassage}),
        )

    async def passe(self, maintenant: datetime | None = None) -> dict[str, int]:
        """Une passe complète : figer, regrouper, compacter. Idempotente."""
        if self._jours <= 0:
            return {}
        limite = (maintenant or utcnow()) - timedelta(days=self._jours)
        figees = 0
        for lignes in await self._repo.sessions_a_figer(limite):
            # Une fiche qui échoue arrêtait TOUTE la passe, avant le
            # regroupement, et cela chaque heure. Les autres sessions
            # continuent ; celle-ci reste brute — le regroupement s'arrête au
            # début de toute session close non figée — et sera retentée.
            try:
                await self.figer(lignes)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                cause = f"{type(exc).__name__}: {exc}"
                if self.echecs.get(lignes["id"]) != cause:
                    logger.warning(
                        "Rétention : fiche de la session %s impossible à figer (%s) ; "
                        "ses relevés restent bruts", lignes["id"], cause)
                self.echecs[lignes["id"]] = cause
                continue
            self.echecs.pop(lignes["id"], None)
            figees += 1
            await asyncio.sleep(self._pause_s)

        a_faire = [r for r in await self._repo.equipements_releves()
                   if await self._repo.a_regrouper(r, limite)]
        if a_faire and not await self._repo.retention_commencee():
            # Une copie dédiée avant la toute première purge : le lanceur en
            # fait une à chaque lancement, mais il en élague les anciennes.
            await self._repo.copie_avant_retention()
            logger.info("Rétention : copie de la base faite avant la première purge")
        lots = 0
        for router_id in a_faire:
            while await self._repo.regrouper_lot(router_id, limite):
                lots += 1
                await asyncio.sleep(self._pause_s)
        pages = await self._repo.compacter()
        return {"sessions_figees": figees, "lots": lots, "pages_rendues": pages}

    async def boucle(self, attente_initiale_s: float = 300.0, cadence_s: float = 3600.0) -> None:
        """Une passe par heure, la première quelques minutes après le démarrage
        — jamais pendant : l'écran s'ouvre et la scrutation démarre d'abord."""
        if self._jours <= 0:
            logger.info("Rétention désactivée (RETENTION_JOURS=0) : tout est gardé")
            return
        await asyncio.sleep(attente_initiale_s)
        while True:
            try:
                bilan = await self.passe()
                if any(bilan.values()):
                    logger.info(
                        "Rétention : %d session(s) figée(s), %d heure(s) regroupée(s), "
                        "%d page(s) rendue(s)",
                        bilan["sessions_figees"], bilan["lots"], bilan["pages_rendues"],
                    )
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # la passe suivante reprendra au même endroit
                logger.warning("Rétention : passe interrompue (%s)", exc)
            await asyncio.sleep(cadence_s)
