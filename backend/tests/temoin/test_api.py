"""API du témoin Ethernet : faux réseau, faux routeur, jamais le trousseau réel."""

import asyncio
from datetime import datetime, timedelta, timezone

import httpx
import pytest

from app.models import DeviceInfo, WanRadioStatus
from app.peplink.router_api import CredentialsRefusedError
from temoin import __version__, regles
from temoin.api import creer_app
from temoin.connexion import Injoignable, RefusIdentifiants
from temoin.regles import Releve
from temoin.stockage import Depot

T0 = datetime(2026, 10, 17, 10, 0, 0, tzinfo=timezone.utc)
HOTE = "192.0.2.10"
SERIE = "SERIE-TEST-1"
MODELE = "Modèle-Test X1"


def wan(wan_id="1", nom="WAN-A", **kw):
    base = dict(
        kind="ethernet", wan_id=wan_id, wan_name=nom, connected=True,
        status_message="Connected", uptime_s=120, disabled=False,
    )
    base.update(kw)
    return WanRadioStatus(**base)


class FauxClient:
    def __init__(self):
        self.radio = [wan("1", "FIBRE-SITE"), wan("2", "ADSL-SITE")]
        self.ferme = False
        self.lectures = 0
        self.erreur: Exception | None = None

    async def get_radio_status(self, echeance=None):
        self.lectures += 1
        if self.erreur is not None:
            raise self.erreur
        return list(self.radio)

    async def get_device_info(self):
        return DeviceInfo(uptime_s=5000, clock_offset_s=0)

    async def get_router_events(self, noms):
        return []

    async def aclose(self):
        self.ferme = True


class Reseau:
    """Découverte et connexion simulées ; compte chaque appel."""

    def __init__(self, depot):
        self.depot = depot
        self.recherches = 0
        self.connexions: list = []
        self.refus: Exception | None = None
        self.clients: list[FauxClient] = []
        self.radio = None  # WAN publiés par le prochain client
        self.erreur_liens: Exception | None = None

    async def chercher(self, *, depot=None):
        self.recherches += 1
        return [{"hote": HOTE, "firmware": "8.9.9", "retenu": False}]

    async def connecter(self, hote, ident, retenir, depot):
        self.connexions.append((hote, ident, retenir))
        if self.refus is not None:
            raise self.refus
        await depot.memoriser_equipement(hote, SERIE, MODELE)
        client = FauxClient()
        if self.radio is not None:
            client.radio = list(self.radio)
        client.erreur = self.erreur_liens
        self.clients.append(client)
        return client, DeviceInfo(model=MODELE, serial=SERIE, firmware="8.9.9 build 1")


class Temps:
    def __init__(self):
        self.now = T0

    def __call__(self):
        return self.now

    def avancer(self, s):
        self.now += timedelta(seconds=s)


@pytest.fixture
async def depot(tmp_path):
    d = Depot(str(tmp_path / "temoin.db"))
    await d.connect()
    yield d
    await d.close()


@pytest.fixture
def interface(tmp_path):
    dossier = tmp_path / "interface"
    dossier.mkdir()
    (dossier / "index.html").write_text("<!doctype html><title>Témoin</title>", "utf-8")
    return dossier


@pytest.fixture
def reseau(depot):
    return Reseau(depot)


@pytest.fixture
def temps():
    return Temps()


def fabriquer(depot, interface, reseau, temps, **kw):
    return creer_app(
        depot, interface=interface, chercher=reseau.chercher,
        connecter=reseau.connecter, horloge=temps, **kw,
    )


@pytest.fixture
async def api(depot, interface, reseau, temps):
    app = fabriquer(depot, interface, reseau, temps)
    async with app.router.lifespan_context(app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://127.0.0.1:8765") as c:
            yield c


async def laisser_tourner():
    for _ in range(20):
        await asyncio.sleep(0)


def rel(s, connecte=True, message="Connected", anciennete=None, joignable=True):
    return Releve(
        ts=T0 + timedelta(seconds=s), joignable=joignable,
        connecte=connecte if joignable else None, message=message if joignable else None,
        anciennete_s=anciennete if anciennete is not None else (s + 100),
        desactive=False, anciennete_routeur_s=10_000 + s, latence_ms=12.5,
    )


async def test_la_sante_dit_la_version_et_le_coffre(api):
    r = await api.get("/api/sante")
    assert r.status_code == 200
    assert r.json()["status"] == "ok" and r.json()["version"] == __version__
    assert "coffre" in r.json()


async def test_le_parcours_va_de_la_recherche_a_la_mesure(api, reseau, depot, temps):
    reponses = []

    async def lire(methode, url, **kw):
        r = await api.request(methode, url, **kw)
        reponses.append(r)
        return r

    r = await lire("GET", "/api/etat")
    assert r.json() == {
        "phase": "recherche",
        "equipements": [{"hote": HOTE, "firmware": "8.9.9", "retenu": False}],
    }
    # Le sondage de l'écran (3 s) ne relance pas la découverte du réseau.
    await lire("GET", "/api/etat")
    temps.avancer(3)
    await lire("GET", "/api/etat")
    assert reseau.recherches == 1
    temps.avancer(11)
    await lire("GET", "/api/etat")
    assert reseau.recherches == 2

    r = await lire("POST", "/api/connexion", json={
        "hote": HOTE, "username": "admin", "password": "mdp-de-test", "retenir": True,
    })
    assert r.status_code == 200
    hote, ident, retenir = reseau.connexions[0]
    assert hote == HOTE and ident.username == "admin" and retenir is True

    r = await lire("GET", "/api/etat")
    assert r.json() == {
        "phase": "choix_lien",
        "liens": [{"wan_id": "1", "connecte": True}, {"wan_id": "2", "connecte": True}],
        "modele": MODELE,
        "lien_retenu": None,
    }
    # Les liens sont lus une fois, à la connexion : le sondage ne relit pas le routeur.
    await lire("GET", "/api/etat")
    assert reseau.clients[0].lectures == 1

    r = await lire("POST", "/api/lien", json={"wan_id": "9"})
    assert r.status_code == 422

    r = await lire("POST", "/api/lien", json={"wan_id": "1"})
    assert r.status_code == 200
    obs = r.json()["observation"]
    await laisser_tourner()

    r = await lire("GET", "/api/etat")
    assert r.json() == {"phase": "mesure", "observation": obs, "lien": "WAN 1", "modele": MODELE}

    # La vraie collecte a tourné : un relevé est rangé.
    assert len(await depot.releves(obs)) >= 1

    r = await lire("GET", "/api/observation")
    corps = r.json()
    assert r.status_code == 200
    assert corps["id"] == obs and corps["lien"] == "WAN 1" and corps["modele"] == MODELE
    assert corps["etat_lien"] == "ok" and corps["horloge_decalee"] is False

    r = await lire("GET", "/api/observations")
    assert [o["id"] for o in r.json()] == [obs]
    assert r.json()[0]["lien"] == "WAN 1" and r.json()[0]["en_cours"] is True

    temps.avancer(60)
    r = await lire("POST", "/api/observations/nouvelle")
    assert r.status_code == 200
    nouvelle = r.json()["observation"]
    assert nouvelle != obs
    await laisser_tourner()
    r = await lire("GET", "/api/etat")
    assert r.json()["observation"] == nouvelle
    assert len(await depot.releves(nouvelle)) >= 1

    # Ni la série, ni le nom du WAN ne sortent jamais par l'API.
    for rep in reponses:
        assert SERIE not in rep.text
        assert "FIBRE-SITE" not in rep.text and "ADSL-SITE" not in rep.text


async def test_l_arret_annule_la_collecte_et_ferme_le_client(
    depot, interface, reseau, temps
):
    app = fabriquer(depot, interface, reseau, temps)
    async with app.router.lifespan_context(app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://127.0.0.1") as c:
            await c.post("/api/connexion", json={"hote": HOTE})
            await c.post("/api/lien", json={"wan_id": "1"})
            await laisser_tourner()
    assert reseau.clients[0].ferme is True
    # Le dépôt appartient à l'appelant : il reste ouvert.
    assert await depot.observations()


async def test_un_refus_rend_401_et_ne_retente_rien(api, reseau):
    reseau.refus = RefusIdentifiants("Identifiants refusés.")
    r = await api.post("/api/connexion", json={
        "hote": HOTE, "username": "admin", "password": "faux", "retenir": False,
    })
    assert r.status_code == 401
    assert r.json() == {"detail": "Identifiants refusés."}
    assert len(reseau.connexions) == 1

    r = await api.get("/api/etat")
    assert r.json() == {"phase": "identifiants", "hote": HOTE, "motif": None}
    await api.get("/api/etat")
    assert len(reseau.connexions) == 1


async def test_sans_identifiant_saisi_le_trousseau_est_consulte(api, reseau):
    reseau.refus = RefusIdentifiants("identifiants requis")
    r = await api.post("/api/connexion", json={"hote": HOTE})
    assert r.status_code == 401
    assert reseau.connexions == [(HOTE, None, True)]
    assert (await api.get("/api/etat")).json()["phase"] == "identifiants"


async def test_un_routeur_injoignable_rend_502(api, reseau):
    reseau.refus = Injoignable("Équipement injoignable (ConnectError).")
    r = await api.post("/api/connexion", json={"hote": HOTE})
    assert r.status_code == 502
    assert (await api.get("/api/etat")).json()["phase"] == "recherche"


async def test_une_reconnexion_ferme_le_client_precedent(api, reseau):
    await api.post("/api/connexion", json={"hote": HOTE})
    await api.post("/api/lien", json={"wan_id": "1"})
    await laisser_tourner()
    await api.post("/api/connexion", json={"hote": HOTE})
    assert reseau.clients[0].ferme is True
    assert (await api.get("/api/etat")).json()["phase"] == "choix_lien"


async def test_lien_et_nouvelle_observation_exigent_leur_prealable(api):
    assert (await api.post("/api/lien", json={"wan_id": "1"})).status_code == 409
    assert (await api.post("/api/observations/nouvelle")).status_code == 409
    assert (await api.get("/api/observation")).status_code == 404


class CollecteRefusee:
    def __init__(self, *a, **kw):
        self.etat_lien = "ok"
        self.horloge_decalee = False

    async def boucle(self):
        raise CredentialsRefusedError("refus")


async def test_un_refus_pendant_la_collecte_redemande_les_identifiants(
    depot, interface, reseau, temps
):
    app = fabriquer(depot, interface, reseau, temps, fabrique_collecte=CollecteRefusee)
    async with app.router.lifespan_context(app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://127.0.0.1") as c:
            await c.post("/api/connexion", json={"hote": HOTE})
            await c.post("/api/lien", json={"wan_id": "1"})
            await laisser_tourner()
            assert (await c.get("/api/etat")).json() == {
                "phase": "identifiants", "hote": HOTE,
                "motif": "Mesure arrêtée : identifiants refusés par le routeur",
            }
            assert reseau.clients[0].ferme is True
            assert len(reseau.connexions) == 1


async def test_une_panne_imprevue_relance_la_collecte_apres_30_s(
    depot, interface, reseau, temps
):
    pauses = []
    tours = []

    class CollecteFragile(CollecteRefusee):
        async def boucle(self):
            tours.append(1)
            if len(tours) == 1:
                raise RuntimeError("panne imprévue")
            await asyncio.Event().wait()

    async def sommeil(s):
        pauses.append(s)

    app = fabriquer(depot, interface, reseau, temps,
                    fabrique_collecte=CollecteFragile, sommeil=sommeil)
    async with app.router.lifespan_context(app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://127.0.0.1") as c:
            await c.post("/api/connexion", json={"hote": HOTE})
            await c.post("/api/lien", json={"wan_id": "1"})
            await laisser_tourner()
            assert pauses == [30.0] and len(tours) == 2
            assert (await c.get("/api/etat")).json()["phase"] == "mesure"


async def observation_passee(depot):
    await depot.memoriser_equipement(HOTE, SERIE, MODELE)
    obs = await depot.nouvelle_observation(SERIE, "2", T0)
    releves = [rel(s) for s in range(0, 30, 3)]
    releves += [rel(s, connecte=False, message="No Cable Detected") for s in range(30, 45, 3)]
    releves += [rel(s, anciennete=s - 45) for s in range(45, 90, 3)]
    for r in releves:
        await depot.ajouter_releve(obs, r)
    return obs


async def test_l_observation_rend_verdict_bande_et_coupures(api, depot, temps):
    obs = await observation_passee(depot)
    temps.now = T0 + timedelta(seconds=87)

    r = await api.get("/api/observation", params={"id": obs})
    assert r.status_code == 200
    corps = r.json()
    assert corps["lien"] == "WAN 2" and corps["modele"] == MODELE
    assert corps["verdict"] == {"etat": "stable", "depuis": "2026-10-17T10:00:45+00:00"}
    assert corps["chiffres"]["coupures"] == 1
    assert corps["chiffres"]["coupe_s"] == pytest.approx(15.0)
    assert [s["etat"] for s in corps["bande"]] == ["debout", "coupe", "debout"]
    assert corps["bande"][1]["debut"] == "2026-10-17T10:00:30+00:00"
    assert corps["coupures"] == [{
        "debut": "2026-10-17T10:00:30+00:00", "fin": "2026-10-17T10:00:45+00:00",
        "genre": "coupure", "cause": "câble", "duree_s": 15.0, "fin_observee": True,
    }]
    # Une observation passée n'a pas de collecte vivante.
    assert corps["etat_lien"] is None and corps["horloge_decalee"] is False
    assert SERIE not in r.text


async def test_la_fenetre_restreint_bande_et_coupures(api, depot, temps):
    obs = await observation_passee(depot)
    await depot.ajouter_releve(obs, rel(2 * 3600))
    temps.now = T0 + timedelta(seconds=2 * 3600)

    tout = (await api.get("/api/observation", params={"id": obs})).json()
    heure = (await api.get("/api/observation", params={"id": obs, "fenetre": "1h"})).json()
    assert tout["chiffres"]["coupures"] == 1
    assert heure["chiffres"]["coupures"] == 0 and heure["coupures"] == []
    assert all(s["debut"] >= "2026-10-17T11:00:00+00:00" for s in heure["bande"])
    jour = (await api.get("/api/observation", params={"id": obs, "fenetre": "24h"})).json()
    assert jour["chiffres"]["coupures"] == 1

    r = await api.get("/api/observation", params={"id": obs, "fenetre": "7j"})
    assert r.status_code == 422
    assert (await api.get("/api/observation", params={"id": 999})).status_code == 404


async def test_les_csv_se_telechargent(api, depot):
    obs = await observation_passee(depot)

    r = await api.get(f"/api/observations/{obs}/coupures.csv")
    assert r.status_code == 200
    assert r.headers["content-type"] == "text/csv; charset=utf-8"
    assert r.headers["content-disposition"] == (
        f'attachment; filename="temoin-coupures-{obs}.csv"'
    )
    texte = r.content.decode("utf-8")
    assert texte.startswith("﻿")
    assert "WAN 2" in texte and MODELE in texte and ";coupure;câble" in texte
    assert SERIE not in texte

    r = await api.get(f"/api/observations/{obs}/releves.csv")
    assert r.status_code == 200
    assert r.headers["content-disposition"] == (
        f'attachment; filename="temoin-releves-{obs}.csv"'
    )
    assert "12,5" in r.content.decode("utf-8")

    assert (await api.get("/api/observations/999/coupures.csv")).status_code == 404
    assert (await api.get("/api/observations/999/releves.csv")).status_code == 404


async def test_un_hote_rebascule_est_refuse(depot, interface, reseau, temps):
    app = fabriquer(depot, interface, reseau, temps)
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(
        transport=transport, base_url="http://attaquant.example:8765"
    ) as c:
        r = await c.get("/api/etat")
        assert r.status_code == 400
        r = await c.post("/api/connexion", json={"hote": HOTE, "username": "a", "password": "b"})
        assert r.status_code == 400
    assert reseau.recherches == 0 and reseau.connexions == []


async def test_une_page_tierce_ne_declenche_pas_de_connexion(api, reseau):
    r = await api.post(
        "/api/connexion", json={"hote": HOTE},
        headers={"Origin": "https://attaquant.example", "Sec-Fetch-Site": "cross-site"},
    )
    assert r.status_code == 403
    r = await api.post("/api/connexion", json={"hote": HOTE},
                       headers={"Origin": "https://attaquant.example"})
    assert r.status_code == 403
    assert reseau.connexions == []
    r = await api.post("/api/connexion", json={"hote": HOTE},
                       headers={"Origin": "http://127.0.0.1:8765"})
    assert r.status_code == 200


async def test_l_interface_est_servie_a_la_racine(api):
    r = await api.get("/")
    assert r.status_code == 200 and "Témoin" in r.text
    assert (await api.get("/api/sante")).status_code == 200


async def test_sans_dossier_d_interface_l_api_repond(depot, tmp_path, reseau, temps):
    app = fabriquer(depot, tmp_path / "absent", reseau, temps)
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://127.0.0.1") as c:
        assert (await c.get("/api/sante")).status_code == 200


async def test_le_lien_choisi_est_propose_a_la_connexion_suivante(api, reseau, depot):
    await api.post("/api/connexion", json={"hote": HOTE})
    await api.post("/api/lien", json={"wan_id": "2"})
    await laisser_tourner()
    assert await depot.lien_de(SERIE) == "2"

    await api.post("/api/connexion", json={"hote": HOTE})
    etat = (await api.get("/api/etat")).json()
    # Proposé, pas démarré : c'est l'écran qui décide.
    assert etat["phase"] == "choix_lien" and etat["lien_retenu"] == "2"


async def test_un_lien_retenu_qui_n_est_plus_offert_n_est_pas_propose(api, reseau, depot):
    await depot.memoriser_lien(SERIE, "3")
    await api.post("/api/connexion", json={"hote": HOTE})
    assert (await api.get("/api/etat")).json()["lien_retenu"] is None


async def test_un_seul_lien_offert_est_preselectionne(api, reseau):
    reseau.radio = [wan("4", "FIBRE-SITE")]
    await api.post("/api/connexion", json={"hote": HOTE})
    assert (await api.get("/api/etat")).json()["lien_retenu"] == "4"


async def test_une_panne_a_la_lecture_des_liens_rend_502_sans_500_ensuite(api, reseau):
    reseau.erreur_liens = httpx.ConnectError("panne")
    r = await api.post("/api/connexion", json={"hote": HOTE})
    assert r.status_code == 502
    assert reseau.clients[0].ferme is True
    r = await api.get("/api/etat")
    assert r.status_code == 200 and r.json()["phase"] == "recherche"


async def test_un_refus_a_la_lecture_des_liens_redemande_les_identifiants(api, reseau):
    reseau.erreur_liens = CredentialsRefusedError("refus")
    r = await api.post("/api/connexion", json={"hote": HOTE})
    assert r.status_code == 401
    assert reseau.clients[0].ferme is True
    assert (await api.get("/api/etat")).json() == {"phase": "identifiants", "hote": HOTE, "motif": None}
    assert len(reseau.connexions) == 1


async def test_un_echec_de_connexion_efface_la_demande_d_identifiants(api, reseau):
    reseau.refus = RefusIdentifiants("identifiants requis")
    await api.post("/api/connexion", json={"hote": HOTE})
    reseau.refus = Injoignable("Équipement injoignable.")
    await api.post("/api/connexion", json={"hote": "192.0.2.11"})
    assert (await api.get("/api/etat")).json()["phase"] == "recherche"


async def test_l_observation_dit_lien_introuvable_et_horloge_decalee(
    depot, interface, reseau, temps
):
    class CollecteInquiete(CollecteRefusee):
        def __init__(self, *a, **kw):
            self.etat_lien = "introuvable"
            self.horloge_decalee = True

        async def boucle(self):
            await asyncio.Event().wait()

    app = fabriquer(depot, interface, reseau, temps, fabrique_collecte=CollecteInquiete)
    async with app.router.lifespan_context(app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://127.0.0.1") as c:
            await c.post("/api/connexion", json={"hote": HOTE})
            await c.post("/api/lien", json={"wan_id": "1"})
            await laisser_tourner()
            corps = (await c.get("/api/observation")).json()
            assert corps["etat_lien"] == "introuvable" and corps["horloge_decalee"] is True


async def test_une_page_tierce_ne_choisit_ni_lien_ni_observation(api, reseau):
    await api.post("/api/connexion", json={"hote": HOTE})
    tiers = {"Origin": "https://attaquant.example", "Sec-Fetch-Site": "cross-site"}
    r = await api.post("/api/lien", json={"wan_id": "1"}, headers=tiers)
    assert r.status_code == 403
    assert (await api.get("/api/etat")).json()["phase"] == "choix_lien"

    await api.post("/api/lien", json={"wan_id": "1"})
    await laisser_tourner()
    obs = (await api.get("/api/etat")).json()["observation"]
    r = await api.post("/api/observations/nouvelle", headers=tiers)
    assert r.status_code == 403
    assert (await api.get("/api/etat")).json()["observation"] == obs



async def test_un_wan_disparu_se_dit_introuvable_au_verdict(
    depot, interface, reseau, temps
):
    class CollecteSansLien(CollecteRefusee):
        def __init__(self, *a, **kw):
            self.etat_lien = "introuvable"
            self.horloge_decalee = False

        async def boucle(self):
            await asyncio.Event().wait()

    app = fabriquer(depot, interface, reseau, temps, fabrique_collecte=CollecteSansLien)
    async with app.router.lifespan_context(app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://127.0.0.1") as c:
            await c.post("/api/connexion", json={"hote": HOTE})
            obs = (await c.post("/api/lien", json={"wan_id": "1"})).json()["observation"]
            await depot.ajouter_releve(obs, rel(0))
            # Plus aucun relevé depuis une minute : sans le lien introuvable, ce
            # serait « aucune mesure » (poste en veille), une fausse raison.
            temps.now = T0 + timedelta(seconds=60)
            corps = (await c.get("/api/observation")).json()
            assert corps["verdict"] == {
                "etat": "introuvable", "depuis": "2026-10-17T10:00:00+00:00",
            }


def _rogner_json(bande, depuis):
    return [
        {**s, "debut": max(s["debut"], depuis)} for s in bande if s["fin"] > depuis
    ]


async def test_la_fenetre_d_une_heure_est_la_fin_du_calcul_entier(api, depot):
    # 2 h 30 de relevés toutes les 10 s : une coupure à 0 h 30 (hors fenêtre),
    # une à cheval sur le début de la fenêtre (1 h 30), une micro-coupure et une
    # coupure dans la fenêtre.
    await depot.memoriser_equipement(HOTE, SERIE, MODELE)
    obs = await depot.nouvelle_observation(SERIE, "1", T0)
    for s in range(0, 9001, 10):
        tombe = 1800 <= s < 1900 or 5280 <= s < 5420 or 7200 <= s < 7260
        anciennete = s - 8000 if s >= 8000 else s + 100
        await depot.ajouter_releve(obs, rel(s, connecte=not tombe, anciennete=anciennete))
    params = {"id": obs}
    tout = (await api.get("/api/observation", params=params)).json()
    heure = (await api.get("/api/observation", params={**params, "fenetre": "1h"})).json()
    depuis = "2026-10-17T11:30:00+00:00"  # dernier relevé (12:30) moins une heure

    assert heure["bande"] == _rogner_json(tout["bande"], depuis)
    attendues = [
        c for c in tout["coupures"]
        if (c["debut"] >= depuis if c["genre"] == "micro-coupure" else c["fin"] > depuis)
    ]
    assert heure["coupures"] == attendues
    assert [c["debut"][11:19] for c in attendues] == ["11:28:00", "12:00:00", "12:13:20"]
    assert heure["verdict"] == tout["verdict"]
    assert heure["chiffres"]["coupe_s"] == pytest.approx(20 + 60)


async def test_le_verdict_d_une_fenetre_remonte_au_dela_de_la_fenetre(api, depot, temps):
    await depot.memoriser_equipement(HOTE, SERIE, MODELE)
    obs = await depot.nouvelle_observation(SERIE, "1", T0)
    for s in range(0, 9001, 10):
        await depot.ajouter_releve(obs, rel(s))
    temps.now = T0 + timedelta(seconds=9005)
    heure = (await api.get("/api/observation", params={"id": obs, "fenetre": "1h"})).json()
    # Stable depuis le tout premier relevé, 2 h 30 avant : ni la fenêtre ni le
    # premier recul (1 h) ne le tronquent.
    assert heure["verdict"] == {"etat": "stable", "depuis": "2026-10-17T10:00:00+00:00"}


async def test_une_coupure_interrompue_par_un_trou_le_dit(api, depot, temps):
    await depot.memoriser_equipement(HOTE, SERIE, MODELE)
    obs = await depot.nouvelle_observation(SERIE, "1", T0)
    for s in [*range(0, 30, 3)]:
        await depot.ajouter_releve(obs, rel(s))
    for s in range(30, 45, 3):
        await depot.ajouter_releve(obs, rel(s, connecte=False))
    for s in range(1800, 1830, 3):
        await depot.ajouter_releve(obs, rel(s))
    [c] = (await api.get("/api/observation", params={"id": obs})).json()["coupures"]
    assert c["fin_observee"] is False and c["fin"] == "2026-10-17T10:00:42+00:00"


async def test_le_verdict_se_prolonge_sans_tout_relire(api, depot, temps, monkeypatch):
    await depot.memoriser_equipement(HOTE, SERIE, MODELE)
    obs = await depot.nouvelle_observation(SERIE, "1", T0)
    for s in range(0, 9001, 10):
        await depot.ajouter_releve(obs, rel(s))
    lectures = []
    lire = depot.releves

    async def releves_comptes(ident, depuis=None):
        lectures.append(depuis)
        return await lire(ident, depuis=depuis)

    monkeypatch.setattr(depot, "releves", releves_comptes)

    async def verdict_1h(s):
        temps.now = T0 + timedelta(seconds=s + 2)
        corps = (await api.get("/api/observation", params={"id": obs, "fenetre": "1h"})).json()
        return corps["verdict"]

    assert await verdict_1h(9000) == {"etat": "stable", "depuis": "2026-10-17T10:00:00+00:00"}
    assert None in lectures  # premier sondage : toute l'observation, une fois
    lectures.clear()
    await depot.ajouter_releve(obs, rel(9010))
    assert await verdict_1h(9010) == {"etat": "stable", "depuis": "2026-10-17T10:00:00+00:00"}
    assert None not in lectures
    # Une micro-coupure, puis une coupure : le verdict suit, toujours sans tout relire.
    await depot.ajouter_releve(obs, rel(9020, anciennete=1))
    assert await verdict_1h(9020) == {"etat": "stable", "depuis": "2026-10-17T12:30:20+00:00"}
    await depot.ajouter_releve(obs, rel(9030, connecte=False))
    assert await verdict_1h(9030) == {"etat": "coupe", "depuis": "2026-10-17T12:30:30+00:00"}
    assert None not in lectures


async def test_hypothese_un_redemarrage_se_voit_au_verdict_prolonge(api, depot, temps):
    # Routeur injoignable 1 h 30 (un essai toutes les 30 s), plus long que la
    # fenêtre et sa marge : le verdict « injoignable » est retenu d'un sondage à
    # l'autre. Puis le routeur revient tout jeune et le WAN renégocie : le
    # verdict dit le redémarrage depuis le premier injoignable, comme la bande.
    await depot.memoriser_equipement(HOTE, SERIE, MODELE)
    obs = await depot.nouvelle_observation(SERIE, "1", T0)
    for s in range(0, 1000, 10):
        await depot.ajouter_releve(obs, rel(s))
    for s in range(1000, 6400, 30):
        await depot.ajouter_releve(obs, rel(s, joignable=False))

    async def sonder(s, fenetre):
        temps.now = T0 + timedelta(seconds=s)
        return (await api.get("/api/observation",
                              params={"id": obs, "fenetre": fenetre})).json()

    debut = "2026-10-17T10:16:40+00:00"
    assert (await sonder(6372, "1h"))["verdict"] == {"etat": "injoignable", "depuis": debut}
    for s in range(6400, 6460, 30):
        await depot.ajouter_releve(obs, rel(s, joignable=False))
    # 100 s d'ancienneté : moins que la suite entière (5 460 s), plus que ce qui
    # suit le dernier relevé examiné (90 s).
    for s in (6460, 6470):
        await depot.ajouter_releve(obs, Releve(
            ts=T0 + timedelta(seconds=s), joignable=True, connecte=False,
            message="Connecting", anciennete_s=None, desactive=False,
            anciennete_routeur_s=100 + s - 6460))
    attendu = {"etat": "redemarrage", "depuis": debut}
    heure = await sonder(6472, "1h")
    assert heure["verdict"] == attendu
    assert heure["coupures"] == []
    assert (await sonder(6472, "tout"))["verdict"] == attendu


async def test_une_coupure_commencee_avant_la_fenetre_garde_son_vrai_debut(api, depot):
    # Coupure de 0 h 10 à 1 h 56, observation relue à 2 h 30 sur la dernière
    # heure : la lecture commence en pleine coupure (fenêtre et marge), son
    # début reste 0 h 10, comme sur l'observation entière.
    await depot.memoriser_equipement(HOTE, SERIE, MODELE)
    obs = await depot.nouvelle_observation(SERIE, "1", T0)
    for s in range(0, 9001, 10):
        await depot.ajouter_releve(obs, rel(s, connecte=not 600 <= s < 7000))
    tout = (await api.get("/api/observation", params={"id": obs})).json()
    heure = (await api.get("/api/observation", params={"id": obs, "fenetre": "1h"})).json()
    assert heure["coupures"] == tout["coupures"]
    assert heure["coupures"][0]["debut"] == "2026-10-17T10:10:00+00:00"
    # La bande, elle, reste rognée au début de la fenêtre.
    assert heure["bande"][0] == {
        "debut": "2026-10-17T11:30:00+00:00", "fin": "2026-10-17T11:56:40+00:00",
        "etat": "coupe"}


async def test_une_coupure_en_cours_avant_la_fenetre_garde_son_vrai_debut(api, depot, temps):
    await depot.memoriser_equipement(HOTE, SERIE, MODELE)
    obs = await depot.nouvelle_observation(SERIE, "1", T0)
    for s in range(0, 9001, 10):
        await depot.ajouter_releve(obs, rel(s, connecte=s < 600))
    temps.now = T0 + timedelta(seconds=9002)
    tout = (await api.get("/api/observation", params={"id": obs})).json()
    for fenetre in ("1h", "24h"):
        corps = (await api.get("/api/observation", params={"id": obs, "fenetre": fenetre})).json()
        assert corps["coupures"] == tout["coupures"]
        assert corps["coupures"][0]["debut"] == "2026-10-17T10:10:00+00:00"
        assert corps["verdict"] == tout["verdict"] == {
            "etat": "coupe", "depuis": "2026-10-17T10:10:00+00:00"}


async def test_la_fenetre_entiere_ne_relit_que_les_nouveaux_releves(api, depot, temps, monkeypatch):
    obs = await observation_passee(depot)
    lignes = []
    lire = depot.releves_apres

    async def releves_comptes(ident, apres_id=0):
        releves, dernier = await lire(ident, apres_id)
        lignes.append(len(releves))
        return releves, dernier

    monkeypatch.setattr(depot, "releves_apres", releves_comptes)
    # La copie tenue à jour sert l'observation EN COURS.
    session = api._transport.app.state.session
    session.observation = obs
    temps.now = T0 + timedelta(seconds=90)
    premier = (await api.get("/api/observation", params={"id": obs})).json()
    assert lignes == [30]
    second = (await api.get("/api/observation", params={"id": obs})).json()
    assert lignes == [30, 0] and second == premier
    await depot.ajouter_releve(obs, rel(90))
    await depot.ajouter_releve(obs, rel(93, connecte=False))
    temps.now = T0 + timedelta(seconds=95)
    troisieme = (await api.get("/api/observation", params={"id": obs})).json()
    assert lignes == [30, 0, 2]
    # Même résultat qu'une lecture entière.
    session.releves_lus = None
    assert (await api.get("/api/observation", params={"id": obs})).json() == troisieme
    assert lignes == [30, 0, 2, 32]
    assert troisieme["verdict"] == {"etat": "coupe", "depuis": "2026-10-17T10:01:33+00:00"}


async def test_une_heure_reculee_fait_tout_relire(api, depot, temps):
    obs = await observation_passee(depot)
    temps.now = T0 + timedelta(seconds=90)
    await api.get("/api/observation", params={"id": obs})
    # Un relevé daté avant la fin de la copie (horloge du poste reculée).
    await depot.ajouter_releve(obs, rel(50, connecte=False))
    corps = (await api.get("/api/observation", params={"id": obs})).json()
    session = api._transport.app.state.session
    session.releves_lus = None
    assert (await api.get("/api/observation", params={"id": obs})).json() == corps


async def test_une_observation_passee_ne_chasse_pas_la_copie_en_cours(api, depot, temps, monkeypatch):
    obs = await observation_passee(depot)
    autre = await depot.nouvelle_observation(SERIE, "2", T0)
    await depot.ajouter_releve(autre, rel(0))
    session = api._transport.app.state.session
    session.observation = obs
    temps.now = T0 + timedelta(seconds=90)
    await api.get("/api/observation", params={"id": obs})
    copie = session.releves_lus
    await api.get("/api/observation", params={"id": autre})
    assert session.releves_lus is copie


def test_le_calcul_par_morceaux_rend_le_calcul_entier():
    from temoin.api import PAS_FIGE, _calculer, _calculer_tout

    # Trois heures à 3 s : debout, une coupure, une micro-coupure, un trou.
    releves = []
    for i in range(3600):
        s = i * 3 + (120 if i > 3000 else 0)
        releves.append(regles.Releve(
            ts=T0 + timedelta(seconds=s), joignable=True,
            connecte=not 1000 <= i < 1010, message="Connected",
            anciennete_s=5 if i == 2000 else 100 + i, desactive=False,
            anciennete_routeur_s=None))
    faits = [regles.FaitRouteur(T0 + timedelta(seconds=3003), "test de santé", None)]
    fin = releves[-1].ts + timedelta(seconds=2)
    attendu = _calculer(releves, faits, fin, None)
    segments, liste, chiffres, fige = _calculer_tout(releves, faits, fin, None)
    assert fige is not None
    assert (segments, liste, chiffres) == attendu
    # Le début figé resservi, avec quelques relevés de plus : toujours identique.
    plus = releves + [regles.Releve(
        ts=releves[-1].ts + timedelta(seconds=3 * k), joignable=True, connecte=True,
        message="Connected", anciennete_s=200 + k, desactive=False,
        anciennete_routeur_s=None) for k in range(1, 4)]
    fin2 = plus[-1].ts
    resultat = _calculer_tout(plus, faits, fin2, fige)
    assert resultat[3] is fige and resultat[:3] == _calculer(plus, faits, fin2, None)
    assert PAS_FIGE > 3


async def test_des_identifiants_incomplets_ne_vont_pas_au_routeur(api, reseau):
    for corps in ({"hote": HOTE, "username": "admin"},
                  {"hote": HOTE, "client_id": "id"}):
        r = await api.post("/api/connexion", json=corps)
        assert r.status_code == 422
    assert reseau.connexions == []
