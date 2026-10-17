"""Les routes de relecture, éprouvées au niveau HTTP.

Aucun test ne montait l'application complète : les analyseurs, le poller et le
stockage étaient couverts, la COUTURE HTTP ne l'était pas. C'est là qu'un
paramètre mal formé se transforme en 500.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import httpx
import pytest
from fastapi import FastAPI

from app.api import routes
from app.fleet import RouterConfig, WanConfig
from app.models import RadioSample, TunnelLink, WanRadioStatus, WifiLink
from app.storage.db import SampleRepository


class _PollerFictif:
    """Le strict nécessaire pour que les routes reconnaissent un équipement."""

    def __init__(self, ids: list[str]) -> None:
        self._ids = ids
        self.devices: dict = {}
        self.capabilities: dict = {}
        self.latest: dict = {}

    def get_router(self, router_id: str):
        if router_id not in self._ids:
            return None
        return RouterConfig(
            id=router_id, name=router_id.upper(), mode="mock",
            wans=[WanConfig(id="4", name="SIM")],
        )

    def alerts(self, router_id: str) -> list:
        """Le faux reprend la signature RÉELLE : la route la publie désormais."""
        return []

    def tunnel_attendu(self, router_id: str) -> bool | None:
        return None

    def never_connected(self, router_id: str) -> list[str]:
        return []

    def period_for(self, router_id: str):
        return None

    def cycle_for(self, router_id: str):
        return None

    @property
    def registry(self):
        return self

    def get(self, router_id: str):
        """Le registre ne connaît ici aucun état : la fiche se lit quand même."""
        return None

    @property
    def default_mode(self) -> str:
        return "mock"


@pytest.fixture
async def client(tmp_path):
    repo = SampleRepository(str(tmp_path / "s.db"))
    await repo.connect()

    base = datetime(2026, 9, 11, 20, 0, 0, tzinfo=timezone.utc)
    for i in range(6):
        await repo.insert(
            RadioSample(
                router_id="r1",
                router_name="R1",
                ts=base + timedelta(seconds=6 * i),
                wan=WanRadioStatus(wan_id="4", wan_name="SIM", rsrp=-90.0, sinr=12.0),
            )
        )

    app = FastAPI()
    app.include_router(routes.router)
    app.state.repo = repo
    app.state.poller = _PollerFictif(["r1"])
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
        c.repo = repo   # pour les tests qui ont besoin d'écrire dans la base
        yield c
    await repo.close()


async def test_a_window_bound_without_a_TIMEZONE_is_read_as_UTC(client):
    """`?since=2026-09-11T20:00:00` — sans `Z` — produit un instant NAÏF.

    Mesuré avant correction : `/events` et `/timeline` rendaient **500**
    (`TypeError: can't compare offset-naive and offset-aware datetimes`), tandis
    que `/history` rendait 200 en passant la borne telle quelle au SQL — donc en
    comparant une chaîne sans décalage à des horodatages qui en portent un.
    Trois routes, trois comportements pour un même paramètre.

    Une borne sans fuseau est lue en UTC, et non dans le fuseau du serveur : ce
    projet ne date qu'en UTC de la scrutation au stockage, et supposer ici
    l'heure locale de la machine ferait dépendre une relecture du réglage du
    poste.
    """
    naif, conscient = "2026-09-11T19:30:00", "2026-09-11T19:30:00Z"

    for route in ("events", "timeline"):
        # `until` posé : la fenêtre reste sous le plafond de vingt-quatre
        # heures, sans quoi `started_at` serait « maintenant moins un jour ».
        fin = "&until=2026-09-11T21:00:00Z"
        sans = await client.get(f"/api/routers/r1/{route}?since={naif}{fin}")
        avec = await client.get(f"/api/routers/r1/{route}?since={conscient}{fin}")
        assert sans.status_code == 200, f"{route} : {sans.status_code}"
        assert sans.json()["started_at"] == avec.json()["started_at"]

    sans = await client.get(f"/api/routers/r1/history?metric=rtt_ms&wan=4&since={naif}")
    avec = await client.get(f"/api/routers/r1/history?metric=rtt_ms&wan=4&since={conscient}")
    assert sans.status_code == 200
    assert sans.json() == avec.json()


async def test_a_bound_outside_the_calendar_is_a_400_not_a_500(client):
    """`?until=0001-01-01T00:00:00Z` et `?since=0001-01-01T00:00:00+14:00`.

    Le premier fait reculer la fenêtre avant l'an 1, le second n'a pas de
    version UTC : Python lève `OverflowError`, que rien n'attendait, et les
    trois routes rendaient 500 — une erreur du serveur pour une borne que
    l'appelant a choisie et peut corriger.
    """
    for q in ("until=0001-01-01T00:00:00Z", "since=0001-01-01T00:00:00%2B14:00"):
        for route in ("events", "timeline", "history?metric=rtt_ms&wan=4"):
            sep = "&" if "?" in route else "?"
            r = await client.get(f"/api/routers/r1/{route}{sep}{q}")
            assert r.status_code == 400, f"{route} {q} : {r.status_code}"
            assert "calendrier" in r.json()["detail"]
    # Et l'autre extrémité passe : l'an 9999 est une fin de fenêtre valide.
    r = await client.get("/api/routers/r1/events?until=9999-12-31T23:59:59Z")
    assert r.status_code == 200


async def test_an_unknown_wan_is_a_404_not_an_empty_series(client):
    """`?wan=cellular-9` sur un équipement qui n'a que le WAN 4.

    Le 200 vide se lisait comme « rien mesuré sur ce lien » : un vieux favori
    ou une faute de frappe devenait un lien silencieux. Un WAN réel sans
    point dans la fenêtre reste, lui, un 200 vide — c'est sa vérité.
    """
    r = await client.get("/api/routers/r1/history?metric=rsrp&wan=cellular-9")
    assert r.status_code == 404
    assert "cellular-9" in r.json()["detail"]

    r = await client.get(
        "/api/routers/r1/history?metric=rsrp&wan=4&since=2020-01-01T00:00:00Z&until=2020-01-02T00:00:00Z"
    )
    assert r.status_code == 200
    assert r.json()["points"] == [] and r.json()["available"] == 0

    # Un WAN que la configuration ne liste plus mais que la base a vu — une
    # SIM retirée depuis — reste relisible : c'est tout l'objet de l'historique.
    await client.repo.insert(RadioSample(
        router_id="r1", router_name="R1",
        ts=datetime(2026, 9, 1, 12, 0, 0, tzinfo=timezone.utc),
        wan=WanRadioStatus(wan_id="5", wan_name="SIM retirée", rsrp=-100.0),
    ))
    r = await client.get("/api/routers/r1/history?metric=rsrp&wan=5")
    assert r.status_code == 200


async def test_the_history_route_passes_the_publication_rule_through(client):
    """`?min_packets=200` arrive jusqu'au stockage ; sans lui, rien ne change."""
    await client.repo.insert(RadioSample(
        router_id="r1", router_name="R1",
        ts=datetime(2026, 9, 11, 20, 0, 30, tzinfo=timezone.utc),
        wan=WanRadioStatus(wan_id="4", wan_name="SIM", tunnel=TunnelLink(
            state="ACTIVE", loss_percent=50.0, loss_percent_up=50.0, loss_percent_down=2.0,
            packets_up=4, packets_down=5000,
        )),
    ))
    q = "/api/routers/r1/history?metric=loss_percent&wan=4&since=2026-09-11T20:00:00Z&until=2026-09-11T21:00:00Z"
    sans = (await client.get(q)).json()["points"]
    avec = (await client.get(q + "&min_packets=200")).json()["points"]
    assert [p["value"] for p in sans if p["value"] is not None] == [50.0]
    assert [p["value"] for p in avec if p["value"] is not None] == [2.0]
    assert (await client.get(q + "&min_packets=0")).status_code == 422


async def test_the_band_and_the_journal_cap_their_window_and_say_so(client):
    """`minutes ≤ 1440` ne bornait rien dès que `since` était posé — et l'écran
    pose `since = first_seen`, toutes les trente secondes. Une session de trois
    semaines ferait une minute de SQL par relecture sur l'unique connexion.
    La fenêtre est rognée à vingt-quatre heures, et le DIT.
    """
    for route in ("timeline", "events"):
        r = await client.get(
            f"/api/routers/r1/{route}?since=2026-08-01T00:00:00Z&until=2026-09-11T21:00:00Z"
        )
        assert r.status_code == 200
        corps = r.json()
        assert corps["capped"] is True
        assert corps["started_at"].startswith("2026-09-10T21:00:00")
        # Sous le plafond : rien n'est rogné, et c'est dit aussi.
        r = await client.get(
            f"/api/routers/r1/{route}?since=2026-09-11T19:00:00Z&until=2026-09-11T21:00:00Z"
        )
        assert r.json()["capped"] is False
        assert r.json()["started_at"].startswith("2026-09-11T19:00:00")


async def test_the_journal_takes_the_routers_own_words(client):
    """Scénarios 1 à 4 de la revue : le routeur écrit « Disabled », « WAN failed
    PING test », « switching to SIM B », « Priority changed » — l'outil écrivait
    « absent du relevé » ou se taisait. La coupure prend la cause du routeur
    quand un fait du même lien tombe à quinze secondes près ; les manœuvres et
    les connexions refusées deviennent des épisodes à part entière."""
    from app.models import RouterEvent

    t0 = datetime(2026, 9, 11, 20, 0, 0, tzinfo=timezone.utc)
    # Le fixture écrit WAN 4 connecté de t0 à t0+30 s ; on le fait tomber à +36 s.
    await client.repo.insert(RadioSample(
        router_id="r1", router_name="R1", ts=t0 + timedelta(seconds=36),
        wan=WanRadioStatus(wan_id="4", wan_name="SIM", connected=False, status_message="absent du relevé"),
    ))
    await client.repo.insert_router_events("r1", [
        # Un fait du même lien, trente et une secondes AVANT la coupure : hors de
        # la fenêtre de quinze secondes, il n'explique rien.
        RouterEvent(ts=t0 + timedelta(seconds=5), kind="coupure", wan_id="4",
                    detail="WAN failed PING test", empreinte="z" * 24),
        RouterEvent(ts=t0 + timedelta(seconds=33), kind="coupure", wan_id="4", detail="Disabled", empreinte="a" * 24),
        RouterEvent(ts=t0 + timedelta(seconds=40), kind="bascule de SIM", wan_id="4", detail="vers la SIM B", empreinte="b" * 24),
        RouterEvent(ts=t0 + timedelta(seconds=50), kind="priorités modifiées", empreinte="c" * 24),
        RouterEvent(ts=t0 + timedelta(seconds=55), kind="connexion admin refusée", empreinte="d" * 24),
        RouterEvent(ts=t0 + timedelta(seconds=56), kind="connexion admin réussie", empreinte="e" * 24),
    ])
    r = await client.get("/api/routers/r1/events?since=2026-09-11T19:59:00Z&until=2026-09-11T20:10:00Z")
    assert r.status_code == 200
    ep = {(e["kind"], e["wan_id"]): e for e in r.json()["episodes"]}
    assert ep[("coupure", "4")]["detail"] == "Disabled"
    assert ep[("bascule de SIM", "4")]["detail"] == "vers la SIM B"
    assert ("priorités modifiées", None) in ep
    assert ("connexion admin refusée", None) in ep
    # Une connexion RÉUSSIE n'est pas un épisode : c'est le fonctionnement normal.
    assert ("connexion admin réussie", None) not in ep


async def test_the_session_sheet_counts_maneuvers_apart_from_outages(client):
    """Une bascule de SIM, une configuration modifiée, une désactivation sont
    des MANŒUVRES : la fiche les compte à part, au lieu de les laisser se lire
    comme des pannes (revue, scénarios 1, 2 et 4)."""
    from app.models import RouterEvent

    t0 = datetime(2026, 9, 11, 20, 0, 0, tzinfo=timezone.utc)
    sid = await client.repo.open_session("r1", t0 - timedelta(minutes=1))
    await client.repo.touch_session(sid, t0 + timedelta(minutes=5), 6)
    await client.repo.insert_router_events("r1", [
        RouterEvent(ts=t0 + timedelta(seconds=10), kind="bascule de SIM", wan_id="4", empreinte="a" * 24),
        RouterEvent(ts=t0 + timedelta(seconds=20), kind="coupure", wan_id="4", detail="Disabled", empreinte="b" * 24),
        RouterEvent(ts=t0 + timedelta(seconds=25), kind="coupure", wan_id="4", detail="WAN failed PING test",
                    empreinte="c" * 24),
        RouterEvent(ts=t0 + timedelta(hours=2), kind="bascule de SIM", wan_id="4", empreinte="d" * 24),
    ])
    r = await client.get(f"/api/sessions/{sid}/summary")
    assert r.status_code == 200
    lien = next(l for l in r.json()["links"] if l["wan_id"] == "4")
    assert lien["maneuvers"] == 2   # la bascule et la désactivation ; pas la panne, pas hors session


async def test_the_absolute_latency_guard_is_PUBLISHED_and_not_copied(client):
    """Le seuil vivait en double, de part et d'autre de la couture.

    `_SEUIL_EPISODE_MS` côté serveur, `LATENCY_ABSOLUTE_MS` côté interface, pour
    une seule et même règle éditoriale — et ce projet a déjà payé le prix de deux
    déclarations d'un même seuil qui divergent.
    """
    fiche = (await client.get("/api/routers/r1")).json()
    journal = (await client.get("/api/routers/r1/events")).json()
    assert fiche["latency_threshold_ms"] == journal["latency_threshold_ms"]
    assert fiche["latency_threshold_ms"] > 0


async def test_an_unknown_router_is_a_404_on_every_replay_route(client):
    for route in ("events", "timeline", "history?metric=rtt_ms&wan=4"):
        rep = await client.get(f"/api/routers/inconnu/{route}")
        assert rep.status_code == 404, route


async def test_two_spellings_of_the_SAME_instant_read_the_same_window(client):
    """`14:00:00+02:00` et `12:00:00Z` désignent le même instant.

    La borne part en SQL par `.isoformat()`, où les horodatages sont comparés
    comme des CHAÎNES — toutes écrites en `+00:00`. Une borne à décalage non nul
    sélectionnait donc à partir de son heure LOCALE lue comme de l'UTC. Mesuré
    sur le serveur : 653 points contre 220, le premier passant de 12:00 à 14:00.
    """
    utc = "2026-09-11T19:30:00Z"
    decale = "2026-09-11T21:30:00%2B02:00"
    for route, cle in (("events", "episodes"), ("timeline", "lanes")):
        fin = "&until=2026-09-11T21:00:00Z"
        a = (await client.get(f"/api/routers/r1/{route}?since={utc}{fin}")).json()
        b = (await client.get(f"/api/routers/r1/{route}?since={decale}{fin}")).json()
        assert a["started_at"] == b["started_at"], route
        assert len(a[cle]) == len(b[cle]), route

    a = (await client.get(f"/api/routers/r1/history?metric=rtt_ms&wan=4&since={utc}")).json()
    b = (await client.get(f"/api/routers/r1/history?metric=rtt_ms&wan=4&since={decale}")).json()
    assert a == b


async def test_the_three_replay_routes_read_the_same_window(client):
    """Un même trio `minutes`/`since`/`until`, trois routes, une seule règle.

    `timeline` et `events` posaient leurs bornes correctement, en cinq lignes
    recopiées à l'identique. `history` avait sa propre version, et elle ancrait
    le début sur MAINTENANT au lieu de `until` :

        ?until=14:00  ->  ts >= 15:53 AND ts < 14:00

    Une fenêtre inversée ne lève pas et ne rend pas 400 : elle rend `200`,
    `points=0`, `available=0` — indiscernable d'un routeur qui n'aurait rien
    mesuré. Mesuré sur le serveur en marche avant correction. C'est le défaut
    « rien ne se cache dans le silence », commis par la seule des trois routes
    qui n'appliquait pas la règle commune.
    """
    routes = [
        "/api/routers/r1/history?wan=4&metric=sinr",
        "/api/routers/r1/timeline",
        "/api/routers/r1/events",
    ]

    # `since` postérieur à `until` : la fenêtre est vide par construction, et
    # les TROIS routes doivent le dire au lieu de rendre une réponse creuse.
    for route in routes:
        r = await client.get(f"{route}&since=2026-09-13T14:00:00Z&until=2026-09-13T13:00:00Z"
                       if "?" in route
                       else f"{route}?since=2026-09-13T14:00:00Z&until=2026-09-13T13:00:00Z")
        assert r.status_code == 400, f"{route} a accepté une fenêtre inversée"
        assert "vide" in r.json()["detail"]

    # `until` seul : la fenêtre glisse depuis la FIN demandée, pas depuis
    # maintenant. Les trois routes acceptent, aucune ne rend une fenêtre vide.
    for route in routes:
        sep = "&" if "?" in route else "?"
        r = await client.get(f"{route}{sep}until=2026-09-13T14:00:00Z&minutes=60")
        assert r.status_code == 200, f"{route} a refusé un `until` seul"


async def test_an_out_of_range_session_id_is_a_bad_request_not_a_crash(client):
    """`OverflowError` dans le thread de la base rendait 500.

    Mesuré au curl sur le serveur : `/api/sessions/99999999999999999999/summary`
    → **500**, avec `OverflowError: Python int too large to convert to SQLite
    INTEGER` levé DANS le thread d'aiosqlite. `abc` valait déjà 422 et `-1`
    valait 404 : trois écritures d'un même paramètre, trois comportements, dont
    un qui accusait le serveur d'une panne pour une demande malformée.
    """
    assert (await client.get("/api/sessions/99999999999999999999/summary")).status_code == 422
    assert (await client.get("/api/sessions/0/summary")).status_code == 422
    assert (await client.get("/api/sessions/-1/summary")).status_code == 422
    assert (await client.get("/api/sessions/abc/summary")).status_code == 422
    # Un identifiant valide mais inconnu reste un 404 : la demande est bien formée.
    assert (await client.get("/api/sessions/424242/summary")).status_code == 404
    # Le nommage partage la borne.
    r = await client.post("/api/sessions/99999999999999999999/label", json={"label": "x"})
    assert r.status_code == 422


async def test_pairing_refuses_an_unbounded_host_or_name(client):
    """Le corps vient d'un formulaire, et il finit en base puis à l'écran."""
    r = await client.post("/api/devices/pair", json={"host": "a" * 254, "username": "u",
                                                     "password": "p"})
    assert r.status_code == 422
    r = await client.post("/api/devices/pair", json={"host": "", "username": "u",
                                                     "password": "p"})
    assert r.status_code == 422
    r = await client.post("/api/devices/pair", json={"host": "192.0.2.1", "name": "n" * 121,
                                                     "username": "u", "password": "p"})
    assert r.status_code == 422


def test_the_journal_keeps_the_most_telling_episodes_not_the_most_recent():
    """La troncature affirmait garder « les plus marquants ». Elle ne le faisait pas.

    Le tri par date était appliqué AVANT la coupe : `episodes[:120]` gardait donc
    les 120 plus RÉCENTS. Une coupure franche au début d'une fenêtre de trois
    heures tombait au profit d'un changement de message de statut de la dernière
    minute. Le SQL classait pourtant bien ses épisodes de latence par pic
    décroissant (`ORDER BY pic DESC`) — un ordre que le retri par date effaçait.
    """
    from datetime import datetime, timedelta, timezone

    from app.api.routes import _EPISODES_MAX, _GRAVITE, _marquant
    from app.models import Episode

    t0 = datetime(2026, 9, 13, 12, 0, tzinfo=timezone.utc)

    def ep(kind: str, minute: int, peak: float | None = None) -> Episode:
        return Episode(kind=kind, wan_id="4", link_kind="cellular",
                       at=t0 + timedelta(minutes=minute), until=None,
                       samples=1, peak_ms=peak, detail=None)

    # Une coupure au tout début, puis largement de quoi déborder la limite.
    coupure = ep("coupure", 0)
    bruit = [ep("signalement", m + 1) for m in range(_EPISODES_MAX + 50)]

    anciens = sorted([coupure, *bruit], key=lambda e: e.at, reverse=True)[:_EPISODES_MAX]
    assert coupure not in anciens, "l'ancien tri gardait bien les plus récents"

    retenus = sorted([coupure, *bruit], key=_marquant, reverse=True)[:_EPISODES_MAX]
    assert coupure in retenus, "la coupure la plus ancienne doit survivre au bruit"

    # L'ordre de gravité est celui qu'on annonce : le genre prime sur l'ampleur.
    assert _marquant(ep("coupure", 0)) > _marquant(ep("latence haute", 0, 9_000))
    assert _marquant(ep("latence", 0, 1_200)) > _marquant(ep("latence haute", 0, 1_200))
    assert _marquant(ep("hors du bond", 0)) > _marquant(ep("micro-coupure", 0))
    # Les rétablissements passent en dernier : ils n'ont de sens qu'avec leur début.
    assert _marquant(ep("retour", 0)) < _marquant(ep("signalement", 0))

    # Un genre inconnu — le jour où le stockage en ajoutera un — ne doit pas
    # disparaître en silence sous les rétablissements.
    assert _marquant(ep("quelque chose de neuf", 0)) > _marquant(ep("retour", 0))

    # Tous les genres que le stockage sait produire sont classés — ceux du
    # journal du routeur compris.
    from app.api.routes import _EPISODES_ROUTEUR
    assert set(_GRAVITE) == {
        "coupure", "retour", "micro-coupure", "désactivation", "latence", "latence haute",
        "signalement", "hors du bond", "de retour dans le bond",
        "position perdue", "position retrouvée", "changement d'opérateur",
    } | _EPISODES_ROUTEUR


def test_the_application_compresses_what_a_tab_downloads_all_day():
    """Mesuré sur le MAX HD2, un onglet ouvert : la bande d'un stationnement de
    quatre heures pesait 75,7 Ko par lecture, deux lectures par minute — les
    deux tiers de tout ce que l'onglet télécharge — et 3,2 Ko compressée. Le
    JSON d'une bande est répétitif par nature : deux cents tranches aux mêmes
    clés, la plupart à `null`.

    On inspecte la pile de l'application RÉELLE plutôt que de remonter un
    serveur : ce qui compte est que `GZipMiddleware` y soit, et au-dessus des
    routes — pas qu'une fausse application sache compresser.
    """
    from fastapi.middleware.gzip import GZipMiddleware

    from app.main import app

    piles = [m.cls for m in app.user_middleware]
    assert GZipMiddleware in piles, piles
    assert app.user_middleware[[m.cls for m in app.user_middleware].index(GZipMiddleware)].kwargs == {
        "minimum_size": 1024
    }


@pytest.mark.parametrize("route", ["timeline", "events"])
@pytest.mark.parametrize("since, until", [
    ("0001-01-01T00:00:00Z", "0001-01-01T12:00:00Z"),
    ("0001-01-01T00:00:10Z", "0001-01-01T00:00:20Z"),
])
async def test_extreme_bounds_are_a_bad_request_not_a_crash(client, route, since, until):
    """C-36 : `_plafonnee` (fin − 24 h) et la fenêtre de cause (début − 15 s)
    débordaient du calendrier : OverflowError, donc 500. Une borne hors de la
    plage des relevés possibles est une requête mal formée."""
    r = await client.get(f"/api/routers/r1/{route}", params={"since": since, "until": until})
    assert r.status_code == 400, r.text


async def test_a_partial_sheet_counts_only_the_maneuvers_of_its_window(client):
    """C-35 : sur une session de trois jours, la fiche ne relit que les
    dernières 24 h — mais les manœuvres étaient comptées de started_at à
    last_seen. Une bascule du premier jour figurait dans une fiche qui dit
    ne porter que sur la veille."""
    from app.models import RouterEvent

    t0 = datetime(2026, 9, 11, 20, 0, 0, tzinfo=timezone.utc)
    sid = await client.repo.open_session("r1", t0 - timedelta(days=3))
    await client.repo.touch_session(sid, t0 + timedelta(minutes=5), 6)
    await client.repo.insert_router_events("r1", [
        RouterEvent(ts=t0 - timedelta(days=2), kind="bascule de SIM", wan_id="4", empreinte="e" * 24),
        RouterEvent(ts=t0 + timedelta(seconds=10), kind="bascule de SIM", wan_id="4", empreinte="f" * 24),
    ])
    fiche = (await client.get(f"/api/sessions/{sid}/summary")).json()
    assert fiche["partial"] is True
    lien = next(l for l in fiche["links"] if l["wan_id"] == "4")
    assert lien["maneuvers"] == 1, "une manœuvre hors de la fenêtre relue a été comptée"


async def test_the_sheet_latency_uses_the_router_series_at_10_s(client):
    """R-3.2 : la fiche résume la latence du lien sur la série du ROUTEUR, un
    point / 10 s, et non plus sur la copie du dernier point qu'un relevé porte
    une fois par minute — sans compter deux fois ce qui est dans les deux."""
    t0 = datetime(2026, 9, 11, 20, 0, 0, tzinfo=timezone.utc)
    sid = await client.repo.open_session("r1", t0 - timedelta(minutes=1))
    await client.repo.touch_session(sid, t0 + timedelta(minutes=5), 6)
    await client.repo.insert_latency_series("r1", {"4": [
        (t0 + timedelta(seconds=10 * i), 40.0 if i < 20 else 20.0) for i in range(30)]})
    r = await client.get(f"/api/sessions/{sid}/summary")
    lien = next(l for l in r.json()["links"] if l["wan_id"] == "4")
    assert lien["path_latency_samples"] == 30
    assert (lien["path_latency_p50_ms"], lien["path_latency_max_ms"]) == (40.0, 40.0)


async def test_the_sheet_counts_the_filled_radio_points_and_says_how_many(client):
    """R-3.1 : la médiane de RSRP couvre toute la session — nos relevés, et
    l'heure du routeur dans les trous seulement —, et la fiche dit combien de
    points elle a comblés (choix de l'exploitant : les compter)."""
    t0 = datetime(2026, 9, 11, 20, 0, 0, tzinfo=timezone.utc)
    sid = await client.repo.open_session("r1", t0 - timedelta(minutes=1))
    await client.repo.touch_session(sid, t0 + timedelta(minutes=10), 6)
    # Les six relevés de la fixture (RSRP -90, 20:00:00 → 20:00:30) ; le
    # routeur couvre 20:00:00 → 20:04:50 à -110 : 30 points, dont ceux à moins
    # de 15 s d'un relevé n'entrent pas.
    await client.repo.insert_quality_series("r1", {"4": [
        (t0 + timedelta(seconds=10 * i), {"rsrp": -110.0, "rsrq": None, "sinr": None, "rat": "LTE"})
        for i in range(30)]})
    r = await client.get(f"/api/sessions/{sid}/summary")
    lien = next(l for l in r.json()["links"] if l["wan_id"] == "4")
    assert lien["rsrp_filled_samples"] == 25
    assert lien["rsrp_median"] == -110.0, "25 points comblés contre 6 relevés"


async def test_the_session_exports_as_a_csv_shareable_by_default(client):
    """R-3.7 : emporter une session. « À partager » par défaut : opérateur nommé
    par son code réseau, ni SSID ni BSSID, aucune position (A-12) ; le
    complet ajoute ce qu'un tiers n'a pas à voir. Séparateur « ; » et virgule
    décimale, marque d'encodage : Excel en français l'ouvre tel quel."""
    t0 = datetime(2026, 9, 11, 20, 0, 0, tzinfo=timezone.utc)
    sid = await client.repo.open_session("r1", t0 - timedelta(minutes=1))
    await client.repo.touch_session(sid, t0 + timedelta(minutes=5), 7)
    await client.repo.insert(RadioSample(
        router_id="r1", router_name="R1", ts=t0 + timedelta(seconds=40),
        site_lat=48.856614, site_lon=2.352222,
        wan=WanRadioStatus(wan_id="3", wan_name="Bo 89330000000000000000 - Or 0600000000",
                           operator="Mobile S Mobile", plmn="208-10", rsrp=-97.5, sinr=8.0,
                           wifi=WifiLink(ssid="MonReseau", bssid="02:00:00:00:00:09")),
    ))
    # Un WAN Wi-Fi : l'outil range son SSID en guise d'opérateur, et le message
    # du routeur le répète.
    await client.repo.insert(RadioSample(
        router_id="r1", router_name="R1", ts=t0 + timedelta(seconds=45),
        wan=WanRadioStatus(wan_id="6", wan_name="Wi-Fi WAN", kind="wifi", operator="MonReseau",
                           status_message="Connected to MonReseau",
                           wifi=WifiLink(ssid="MonReseau", bssid="02:00:00:00:00:09")),
    ))
    r = await client.get(f"/api/sessions/{sid}/export.csv")
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("text/csv")
    assert f"session-{sid}.csv" in r.headers["content-disposition"]
    texte = r.content.decode("utf-8")
    assert texte.startswith("﻿")
    lignes = texte.lstrip("﻿").splitlines()
    entete = lignes[0].split(";")
    assert entete[:3] == ["instant", "wan", "connecte"]
    # Aucune position dans la version à partager, pas même arrondie (A-12).
    assert not {"ssid", "bssid", "operateur_modem", "latitude", "longitude"} & set(entete)
    sim3 = dict(zip(entete, next(l for l in lignes[1:] if ";3;" in l).split(";"), strict=True))
    assert sim3["operateur"] == "SFR" and sim3["plmn"] == "208-10"
    assert sim3["rsrp"] == "-97,5"
    assert "48,857" not in r.text and "2,352" not in r.text
    for trace in ("MonReseau", "02:00:00:00:00:09", "Mobile S Mobile", "89330000", "0600000000"):
        assert trace not in texte
    # Une absence reste une cellule vide, jamais un zéro.
    assert sim3["rsrq"] == ""

    complet = (await client.get(f"/api/sessions/{sid}/export.csv?complet=true")).content.decode("utf-8")
    entete = complet.lstrip("﻿").splitlines()[0].split(";")
    assert {"ssid", "bssid", "operateur_modem", "latitude", "longitude"} <= set(entete)
    assert "48,856614" in complet and "Mobile S Mobile" in complet
    # Le nom du WAN n'est jamais dans la base : il ne peut pas fuir.
    assert "89330000" not in complet


async def test_the_router_hour_exports_in_its_own_file(client):
    t0 = datetime(2026, 9, 11, 20, 0, 0, tzinfo=timezone.utc)
    sid = await client.repo.open_session("r1", t0 - timedelta(minutes=1))
    await client.repo.touch_session(sid, t0 + timedelta(minutes=5), 6)
    await client.repo.insert_latency_series("r1", {"4": [(t0, 30.0), (t0 + timedelta(hours=2), 99.0)]})
    await client.repo.insert_quality_series("r1", {"4": [
        (t0, {"rsrp": -101.0, "rsrq": None, "sinr": 7.5, "rat": "5G"})]})
    r = await client.get(f"/api/sessions/{sid}/export-routeur.csv")
    assert r.status_code == 200 and f"session-{sid}-routeur.csv" in r.headers["content-disposition"]
    lignes = r.content.decode("utf-8").lstrip("﻿").splitlines()
    assert lignes[0].split(";") == ["instant", "wan", "latence_lien_ms", "rsrp", "rsrq", "sinr", "techno"]
    assert lignes[1:] == ["2026-09-11T20:00:00+00:00;4;30;-101;;7,5;5G"], "hors session : dehors"


async def test_the_csv_export_is_built_by_batches_off_the_event_loop(client, monkeypatch):
    """C-23 : l'export relisait toute la session, puis bâtissait le CSV dans
    le handler async, sans plafond ni to_thread — environ 680 ms de Python
    synchrone sur une session de dix heures, des dizaines de secondes sur trois
    semaines, pendant lesquelles la scrutation était figée et les budgets des
    équipements expiraient.

    Désormais : des lots relus par clé, mis en forme hors de la boucle, et
    envoyés au fil de l'eau — le fichier reste identique, ligne pour ligne.
    """
    import threading

    from app.api import routes

    t0 = datetime(2026, 9, 11, 20, 0, 0, tzinfo=timezone.utc)
    sid = await client.repo.open_session("r1", t0 - timedelta(minutes=1))
    await client.repo.touch_session(sid, t0 + timedelta(minutes=5), 6)
    entier = (await client.get(f"/api/sessions/{sid}/export.csv")).content

    fils: list[bool] = []
    origine = routes._mettre_en_forme

    def espion(*args, **kwargs):
        fils.append(threading.current_thread() is threading.main_thread())
        return origine(*args, **kwargs)

    monkeypatch.setattr(routes, "_mettre_en_forme", espion)
    monkeypatch.setattr(routes, "_LOT_EXPORT", 2)
    par_lots = (await client.get(f"/api/sessions/{sid}/export.csv")).content
    assert par_lots == entier, "le découpage en lots a changé le fichier"
    assert len(fils) >= 3, "les six relevés doivent passer en plusieurs lots"
    assert not any(fils), "la mise en forme a tourné sur la boucle d'événements"


async def test_what_identifies_the_fleet_is_kept_to_this_computer(client):
    """C-37 : l'export COMPLET (SSID, BSSID, position exacte) et
    /devices/known (les séries appairées) n'avaient pas require_loopback,
    alors que le serveur de développement écoute sur le LAN et que
    HOST=0.0.0.0 est documenté : un téléphone du réseau les téléchargeait."""
    from httpx import ASGITransport, AsyncClient

    t0 = datetime(2026, 9, 11, 20, 0, 0, tzinfo=timezone.utc)
    sid = await client.repo.open_session("r1", t0 - timedelta(minutes=1))
    await client.repo.touch_session(sid, t0 + timedelta(minutes=5), 6)
    distant = AsyncClient(transport=ASGITransport(app=client._transport.app, client=("192.168.1.20", 5000)),
                          base_url="http://test")
    assert (await distant.get("/api/devices/known")).status_code == 403
    assert (await distant.get(f"/api/sessions/{sid}/export.csv?complet=true")).status_code == 403
    # Ce qu'un tiers peut voir reste ouvert : l'export « à partager ».
    assert (await distant.get(f"/api/sessions/{sid}/export.csv")).status_code == 200
    await distant.aclose()


async def test_an_unknown_session_does_not_export(client):
    assert (await client.get("/api/sessions/999999/export.csv")).status_code == 404


async def test_the_export_names_the_rows_published_without_a_network_code(client):
    """Vu dans l'export de la session 11 : « Orange » partout, et « Orange F »
    sur les relevés publiés SANS code réseau — juste après une reconnexion. Ils
    se rangent sous le réseau d'un libellé connu, comme la fiche le fait déjà
    (R-3.4) ; la colonne `plmn`, elle, reste ce que le routeur a publié."""
    t0 = datetime(2026, 9, 11, 20, 0, 0, tzinfo=timezone.utc)
    sid = await client.repo.open_session("r1", t0 - timedelta(minutes=1))
    await client.repo.touch_session(sid, t0 + timedelta(minutes=5), 9)
    for i, (op, plmn) in enumerate([("Orange F", "208-01"), ("Orange F", None),
                                    ("Mobile S Mobile", None), ("Mobile S Mobile", "208-10")]):
        await client.repo.insert(RadioSample(
            router_id="r1", router_name="R1", ts=t0 + timedelta(seconds=40 + 5 * i),
            wan=WanRadioStatus(wan_id="3", wan_name="W", operator=op, plmn=plmn),
        ))
    texte = (await client.get(f"/api/sessions/{sid}/export.csv")).content.decode("utf-8").lstrip("﻿")
    lignes = texte.splitlines()
    entete = lignes[0].split(";")
    sim3 = [dict(zip(entete, l.split(";"), strict=True)) for l in lignes[1:] if ";3;" in l]
    assert [(l["operateur"], l["plmn"]) for l in sim3] == [
        ("Orange", "208-01"), ("Orange", ""), ("SFR", ""), ("SFR", "208-10")]


async def test_the_timeline_publishes_the_disabled_readings(client):
    """C-20 : le relevé d'adieu d'un WAN désactivé — tel que le collecteur
    l'écrit — ressort de la route compté comme une désactivation, pour que la
    bande ne le peigne pas en panne."""
    from app.models import STATUT_DESACTIVE

    base = datetime(2026, 9, 11, 20, 1, 0, tzinfo=timezone.utc)
    await client.repo.insert(RadioSample(
        router_id="r1", router_name="R1", ts=base,
        wan=WanRadioStatus(wan_id="7", wan_name="SIM 2", connected=False, disabled=True,
                           status_message=STATUT_DESACTIVE),
    ))
    r = await client.get("/api/routers/r1/timeline?since=2026-09-11T20:00:00Z&until=2026-09-11T20:02:00Z")
    lanes = {l["wan_id"]: l["slices"] for l in r.json()["lanes"]}
    assert sum(s["disabled"] for s in lanes["7"]) == 1
    assert all(s["disabled"] == 0 for s in lanes["4"])
