"""Trois équipements réels, rejoués du transport jusqu'aux routes.

Le 19 septembre a montré ce qu'un test unitaire ne voit pas : chaque parseur
était vert, et c'est la CHAÎNE qui a cédé — un schéma d'URL, une session mal
datée, un champ absent qui efface l'écran. Ici la chaîne entière tourne sur ce
que trois châssis ont réellement répondu, et ce qui est vérifié n'est pas « le
bon chiffre sort » — on le connaît déjà, parseur par parseur — mais ce qu'un
équipement inconnu ne doit JAMAIS produire :

  1. une route qui échoue, sur une charge utile qu'un routeur a réellement rendue ;
  2. un nombre non fini, un identifiant devenu quantité, un secret, un « None »
     écrit en toutes lettres — dans N'IMPORTE QUELLE réponse, pas seulement
     celles qu'on a pensé à regarder ;
  3. deux sessions pour une mise sous tension, ou aucune.

Le profil d'endpoints, lui, est confronté d'un châssis à l'autre : ce que le
client demande et ce que chacun sait rendre.
"""

from __future__ import annotations

import json
import math

import pytest

from rejeu import CAPTURES, Rejeu

# Les champs qui sont des NOMS sur le fil. Un entier ici serait `fmtInt`-able.
_IDENTIFIANTS = {"cell_id", "enb_id", "tac", "sector_id", "pci", "earfcn", "channel", "vlan_id"}
# Ce qu'aucune réponse ne doit porter, sous aucune clé.
_SECRETS = {"username", "password", "psk", "secret", "token", "client_secret"}
# Les mots qu'une absence échappée écrit avec l'aplomb d'une valeur.
_INTERDITS = {"None", "nan", "NaN", "inf", "Infinity", "undefined", "null"}


def _interdit(constante: str) -> None:
    raise AssertionError(f"JSON non fini : {constante}")


def _parcourir(valeur: object, chemin: str, fautes: list[str]) -> None:
    if isinstance(valeur, dict):
        for cle, v in valeur.items():
            if cle in _SECRETS:
                fautes.append(f"{chemin}.{cle} : clé secrète publiée")
            if cle in _IDENTIFIANTS and v is not None and not isinstance(v, str):
                fautes.append(f"{chemin}.{cle} = {v!r} : un identifiant doit être une chaîne")
            _parcourir(v, f"{chemin}.{cle}", fautes)
    elif isinstance(valeur, list):
        for i, v in enumerate(valeur):
            _parcourir(v, f"{chemin}[{i}]", fautes)
    elif isinstance(valeur, float) and not math.isfinite(valeur):
        fautes.append(f"{chemin} = {valeur!r}")
    elif isinstance(valeur, str) and valeur in _INTERDITS:
        fautes.append(f"{chemin} = {valeur!r} : une absence écrite en toutes lettres")


@pytest.fixture(params=list(CAPTURES), ids=list(CAPTURES))
async def rejeu(request, tmp_path):
    async with Rejeu(request.param, CAPTURES[request.param], tmp_path) as r:
        # Deux cycles : la période observée n'existe qu'après le second, et la
        # session doit être REPRISE au second, pas rouverte.
        await r.cycles(2)
        yield r


async def _toutes_les_routes(r: Rejeu) -> dict[str, object]:
    """Chaque route de lecture, avec les paramètres que l'écran emploie."""
    nom = r.nom
    _, resume = await r.lire(f"/routers/{nom}")
    _, sessions = await r.lire(f"/routers/{nom}/sessions")
    reponses: dict[str, object] = {
        "/routers": (await r.lire("/routers"))[1],
        f"/routers/{nom}": resume,
        "/devices": (await r.lire("/devices"))[1],
        "/devices/known": (await r.lire("/devices/known"))[1],
        f"/routers/{nom}/sessions": sessions,
        f"/routers/{nom}/timeline": (await r.lire(f"/routers/{nom}/timeline?minutes=180"))[1],
        f"/routers/{nom}/events": (await r.lire(f"/routers/{nom}/events?minutes=180"))[1],
    }
    assert isinstance(sessions, list) and sessions, "aucune session après deux cycles"
    sid = sessions[0]["id"]
    reponses[f"/sessions/{sid}/summary"] = (await r.lire(f"/sessions/{sid}/summary"))[1]
    since = sessions[0]["first_seen"] or sessions[0]["started_at"]
    reponses[f"/routers/{nom}/timeline?since"] = (
        await r.lire(f"/routers/{nom}/timeline?since={since}")
    )[1]
    # L'historique de CHAQUE lien, sur chaque grandeur que l'écran propose.
    assert isinstance(resume, dict)
    for wan in resume["wans"]:
        wan_id = wan["wan"]["wan_id"]
        for metric in ("rtt_ms", "loss_percent", "rsrp", "sinr", "rsrq", "tx_kbps", "rx_kbps"):
            code, corps = await r.lire(f"/routers/{nom}/history?wan={wan_id}&metric={metric}&minutes=60")
            assert code == 200, (wan_id, metric, corps)
            reponses[f"/history/{wan_id}/{metric}"] = corps
    return reponses


async def test_every_read_route_answers_on_a_real_capture(rejeu):
    nom = rejeu.nom
    for chemin in (
        f"/routers/{nom}", "/routers", "/devices", "/devices/known",
        f"/routers/{nom}/sessions", f"/routers/{nom}/timeline?minutes=180",
        f"/routers/{nom}/events?minutes=180",
    ):
        code, corps = await rejeu.lire(chemin)
        assert code == 200, (chemin, corps)

    _, resume = await rejeu.lire(f"/routers/{nom}")
    assert resume["status"] == "connecté", resume["error"]
    assert resume["wans"], "aucun lien supervisé sur une capture qui en porte"
    assert resume["device"] is not None, "la fiche d'identité n'a pas été lue"
    assert resume["capabilities"]["wan_types"], "aucun inventaire de WAN"


async def test_no_response_carries_a_non_finite_number_a_secret_or_a_written_absence(rejeu):
    # Le texte brut d'abord : un `NaN` sérialisé n'est pas du JSON, et
    # `json.loads` l'accepterait en silence sans `parse_constant`.
    fautes: list[str] = []
    for chemin, corps in (await _toutes_les_routes(rejeu)).items():
        texte = json.dumps(corps)
        json.loads(texte, parse_constant=_interdit)
        _parcourir(corps, chemin, fautes)
    assert fautes == [], "\n".join(fautes)


async def test_two_cycles_make_one_session_with_a_first_sample(rejeu):
    _, sessions = await rejeu.lire(f"/routers/{rejeu.nom}/sessions")
    assert len(sessions) == 1, [s["started_at"] for s in sessions]
    (session,) = sessions
    assert session["first_seen"] is not None
    assert session["started_at"] <= session["first_seen"] <= session["last_seen"]
    assert session["samples"] > 0


async def test_a_rate_never_travels_without_its_denominator(rejeu):
    """Un taux sans dénominateur est une opinion. La fiche de session publie
    la disponibilité du bond avec ses cycles ; l'historique de perte, avec ses
    paquets — ou rien."""
    _, sessions = await rejeu.lire(f"/routers/{rejeu.nom}/sessions")
    _, fiche = await rejeu.lire(f"/sessions/{sessions[0]['id']}/summary")
    dispo = fiche["availability"]
    if dispo is not None:
        # Un pourcentage publié suppose des cycles observés ET un bond qui a
        # porté ; sans l'un ou l'autre, le pourcentage est nul — pas 0 %. Le
        # MAX HD2 rejoué est le second cas : observé, jamais dans le bond (R-3.6).
        if dispo["at_least_one_pct"] is not None:
            assert dispo["observed_cycles"] > 0 and dispo["cycles_in_bond"] > 0, dispo
        else:
            assert dispo["observed_cycles"] == 0 or dispo["cycles_in_bond"] == 0, dispo
    _, resume = await rejeu.lire(f"/routers/{rejeu.nom}")
    for wan in resume["wans"]:
        _, serie = await rejeu.lire(
            f"/routers/{rejeu.nom}/history?wan={wan['wan']['wan_id']}&metric=loss_percent&minutes=60"
        )
        # Ce contrôle ne PROUVE rien sur les rejeux, et il faut le dire (C-28) :
        # le rejeu relit deux fois la même capture, les compteurs du tunnel
        # n'avancent pas, et aucun taux de perte n'en sort. La règle est tenue
        # ailleurs, sur des relevés construits pour : test_storage.py,
        # test_a_rate_travels_with_its_denominator et
        # test_an_absent_counter_is_not_a_zero_denominator (mutant B11).
        for point in serie["points"]:
            assert "denominator" in point, point
            if point["value"] is not None and point["value"] > 0:
                assert point["denominator"] is not None, point


async def test_the_endpoint_profile_differs_per_chassis_and_the_client_adapts(rejeu):
    """Ce que le client a DEMANDÉ, et ce que ce châssis a su rendre.

    Le Dome Pro Duo n'a que trois endpoints captés : `info.capability` y est
    refusé, et le client reconstitue la fiche par sondage. Les deux MAX la
    déclarent. Aucun des trois ne doit avoir vu le client insister sur un
    endpoint refusé cycle après cycle.
    """
    demandes = rejeu.client.endpoints_demandes
    refuses = [e for e in set(demandes) if e not in rejeu.capture]
    # Un endpoint absent est demandé au plus une fois par famille : la sonde
    # de capacités, puis plus jamais. `status.pepvpn` nu est la sonde elle-même.
    for endpoint in refuses:
        assert demandes.count(endpoint) <= 2, (endpoint, demandes.count(endpoint))
    _, resume = await rejeu.lire(f"/routers/{rejeu.nom}")
    attendu = "probed" if "info.capability" not in rejeu.capture else "declared"
    assert resume["capabilities"]["source"] == attendu
    # Ce que chaque châssis apporte de PLUS que les autres, pour mémoire.
    assert "status.wan.connection" in rejeu.capture


async def test_the_collector_never_asks_the_config_family(rejeu):
    """C-27 : la règle la plus stricte du projet — ne jamais lire `config.*`,
    qui restitue la clé pré-partagée SpeedFusion, les identifiants APN et la
    clé Wi-Fi en clair — n'était gardée que sur la LISTE de capture. Le rejeu
    ne vérifiait que `count <= 2` : un `config.*` demandé à chaque cycle, sur
    deux cycles, passait. Le transport du rejeu refuse désormais toute lecture
    de cette famille, et ce test le constate sur les trois châssis."""
    demandes = rejeu.client.endpoints_demandes
    assert demandes, "le rejeu n'a rien demandé : le test ne prouverait rien"
    assert not [e for e in demandes if e.startswith("config.")]


async def test_the_screen_fixtures_are_what_the_routes_render_today():
    """`frontend/src/test/reel/*.json` est ce que l'écran monte comme équipement
    réel. Un modèle qui change sans que l'exportation soit rejouée ferait tester
    l'écran contre un serveur d'hier — le cas même de la page noire du 19
    septembre. À rejouer : `.venv/bin/python tools/exporter_rejeu.py`.
    """
    import sys
    from pathlib import Path

    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
    from exporter_rejeu import DESTINATION, tout

    attendu = await tout()
    for nom, contenu in attendu.items():
        fichier = DESTINATION / f"{nom}.json"
        assert fichier.exists(), f"{fichier} manque : .venv/bin/python tools/exporter_rejeu.py"
        assert json.loads(fichier.read_text(encoding="utf-8")) == contenu, (
            f"{fichier.name} n'est plus ce que les routes rendent : "
            ".venv/bin/python tools/exporter_rejeu.py"
        )


async def test_a_replay_that_fails_to_open_leaves_the_clock_alone(tmp_path, monkeypatch):
    """`_horloge` était remplacée avant `connect()` : si celui-ci levait,
    `__aexit__` n'était jamais appelé et tous les tests suivants tournaient sur
    une horloge gelée."""
    from app import models
    from app.storage.db import SampleRepository

    horloge = models._horloge

    async def casse(self):
        raise OSError("disque")

    monkeypatch.setattr(SampleRepository, "connect", casse)
    nom, chemin = next(iter(CAPTURES.items()))
    with pytest.raises(OSError):
        async with Rejeu(nom, chemin, tmp_path):
            pass
    assert models._horloge is horloge, "l'horloge du rejeu a fui"
