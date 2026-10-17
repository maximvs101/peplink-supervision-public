"""Stockage du témoin Ethernet : observations, relevés, faits, équipements."""

from datetime import datetime, timedelta, timezone

import pytest

from temoin.regles import FaitRouteur, Releve
from temoin.stockage import Depot

T0 = datetime(2026, 10, 17, 10, 0, 0, tzinfo=timezone.utc)
SERIE = "SERIE-TEST-1"


def t(s):
    return T0 + timedelta(seconds=s)


def releve(ts, **kw):
    base = dict(
        ts=ts, joignable=True, connecte=True, message="Connected",
        anciennete_s=120, desactive=False, anciennete_routeur_s=5000,
    )
    base.update(kw)
    return Releve(**base)


@pytest.fixture
async def depot(tmp_path):
    d = Depot(str(tmp_path / "sous" / "temoin.db"))
    await d.connect()
    yield d
    await d.close()


async def test_une_relance_dans_l_heure_reprend_l_observation(depot):
    obs = await depot.observation_courante(SERIE, "1", t(0))
    await depot.ajouter_releve(obs, releve(t(0)))
    assert await depot.observation_courante(SERIE, "1", t(59 * 60)) == obs
    assert await depot.observation_courante(SERIE, "1", t(61 * 60)) != obs


async def test_un_autre_lien_a_sa_propre_observation(depot):
    a = await depot.observation_courante(SERIE, "1", t(0))
    b = await depot.observation_courante(SERIE, "2", t(1))
    assert a != b


async def test_sans_releve_on_reprend_si_le_debut_est_recent(depot):
    obs = await depot.observation_courante(SERIE, "1", t(0))
    assert await depot.observation_courante(SERIE, "1", t(30 * 60)) == obs
    assert await depot.observation_courante(SERIE, "1", t(2 * 3600)) != obs


async def test_nouvelle_observation_clot_la_precedente(depot):
    ancienne = await depot.observation_courante(SERIE, "1", t(0))
    await depot.ajouter_releve(ancienne, releve(t(0)))
    neuve = await depot.nouvelle_observation(SERIE, "1", t(10))
    assert neuve != ancienne
    assert await depot.observation_courante(SERIE, "1", t(20)) == neuve


async def test_un_fait_relu_n_est_pas_double(depot):
    obs = await depot.nouvelle_observation(SERIE, "1", t(0))
    f = FaitRouteur(t(5), "coupure", "WAN 1 down")
    await depot.ajouter_faits(obs, [f])
    await depot.ajouter_faits(obs, [f, FaitRouteur(t(9), "redémarrage", None)])
    assert await depot.faits(obs) == [f, FaitRouteur(t(9), "redémarrage", None)]


async def test_les_releves_se_relisent_a_l_identique(depot):
    obs = await depot.nouvelle_observation(SERIE, "1", t(0))
    a = releve(t(0), latence_ms=12.5)
    b = releve(t(3), joignable=False, connecte=None, message=None,
               anciennete_s=None, anciennete_routeur_s=None, desactive=True)
    await depot.ajouter_releve(obs, b)
    await depot.ajouter_releve(obs, a)
    lus = await depot.releves(obs)
    assert lus == [a, b]
    assert lus[0].ts.tzinfo is not None
    assert await depot.releves(obs, depuis=t(2)) == [b]


async def test_les_observations_ne_publient_pas_la_serie(depot):
    await depot.memoriser_equipement("192.0.2.10", SERIE, "Modèle X")
    obs = await depot.nouvelle_observation(SERIE, "1", t(0))
    await depot.ajouter_releve(obs, releve(t(7)))
    liste = await depot.observations()
    assert "serie" not in liste[0]
    assert SERIE not in str(liste)
    assert liste[0] == {"id": obs, "modele": "Modèle X", "wan_id": "1",
                        "debut": t(0), "dernier": t(7)}


async def test_observations_recentes_d_abord_et_modele_inconnu(depot):
    a = await depot.nouvelle_observation("SERIE-TEST-2", "1", t(0))
    b = await depot.nouvelle_observation(SERIE, "3", t(100))
    liste = await depot.observations()
    assert [o["id"] for o in liste] == [b, a]
    assert liste[0]["modele"] is None and liste[0]["dernier"] is None


async def test_l_adresse_retrouve_la_serie(depot):
    assert await depot.serie_de("192.0.2.10") is None
    await depot.memoriser_equipement("192.0.2.10", SERIE, None)
    assert await depot.serie_de("192.0.2.10") == SERIE
    await depot.memoriser_equipement("192.0.2.10", "SERIE-TEST-2", "Modèle Y")
    assert await depot.serie_de("192.0.2.10") == "SERIE-TEST-2"
