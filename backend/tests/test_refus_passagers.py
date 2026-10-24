"""Un refus passager ne condamne rien pour la vie du processus.

Chaque complément tenait son propre drapeau `_supports_*`, passé à faux sur
N'IMPORTE quel `stat: fail` — un 999 « occupé », un 401 juste après une
reconnexion — et jamais réarmé. `_get` retenait déjà l'endpoint avec un
réessai à dix minutes ; le drapeau de l'appelant, lui, le condamnait jusqu'au
redémarrage du serveur. Un seul mécanisme subsiste, dans `_get`, et il expire :

- refus de DROITS (un 401/403 qui survit à un login réussi) : retenu, redemandé
  passé `_HORS_PERIMETRE_REESSAI_S` ;
- endpoint INCONNU, dit explicitement (code 404, « unknown function »,
  « unsupported ») : retenu plus longtemps, `_NON_PROPOSE_REESSAI_S` ;
- tout autre `stat: fail` : passager, redemandé au cycle suivant.

Un refus d'IDENTIFIANTS, lui, n'est jamais rejoué : il remonte.
"""

from __future__ import annotations

import httpx
import pytest

from app.fleet import RouterConfig
from app.models import WanRadioStatus
from app.peplink import router_api
from app.peplink.router_api import (
    CredentialsRefusedError,
    RouterApiPeplinkClient,
)

OCCUPE = {"stat": "fail", "code": 999, "message": "Internal Error"}
INCONNU = {"stat": "fail", "code": 999, "message": "unknown function"}
NON_SUPPORTE = {"stat": "fail", "code": 404, "message": "Unsupported request"}
DROITS = {"stat": "fail", "code": 401, "message": "Unauthorized"}


@pytest.fixture
def horloge(monkeypatch):
    instant = [10_000.0]
    monkeypatch.setattr(router_api.time, "monotonic", lambda: instant[0])
    return instant


class _Routeur:
    """Faux routeur : chaque endpoint rend la PREMIÈRE réponse de sa file, puis `ok`."""

    def __init__(self, refus: dict[str, list[dict]]) -> None:
        self.refus = refus
        self.demandes: list[str] = []
        self.logins = 0

    def __call__(self, request: httpx.Request) -> httpx.Response:
        chemin = request.url.path.removeprefix("/api/")
        if chemin == "login":
            self.logins += 1
            return httpx.Response(200, json={"stat": "ok"})
        cle = chemin
        if "infoType" in request.url.params:
            cle = f"{chemin}?{request.url.params['infoType']}"
        if request.content:
            cle = f"{cle}#{request.content.decode()}"
        self.demandes.append(cle)
        file = self.refus.get(cle) or self.refus.get(chemin)
        if file:
            reponse = file[0]
            if len(file) > 1:
                file.pop(0)
            if reponse is not None:
                return httpx.Response(200, json=reponse)
        return httpx.Response(200, json={"stat": "ok", "response": {}})


async def _client(routeur: _Routeur) -> RouterApiPeplinkClient:
    cfg = RouterConfig(id="r", name="R", host="192.0.2.1", username="admin", password="x")
    client = RouterApiPeplinkClient(cfg)
    await client._http.aclose()
    client._http = httpx.AsyncClient(transport=httpx.MockTransport(routeur))
    return client


def _liens() -> list[WanRadioStatus]:
    return [
        WanRadioStatus(wan_id="2", wan_name="A", kind="cellular", connected=True),
        WanRadioStatus(wan_id="3", wan_name="B", kind="cellular", connected=True),
    ]


# Chaque complément : (endpoint tel que le faux routeur l'indexe, lecture, intervalle).
COMPLEMENTS = [
    ("status.traffic", lambda c: c._attach_traffic(_liens()), 0.0),
    ("status.wan.latency", lambda c: c._attach_path_latency(_liens()),
     router_api._WAN_LATENCY_INTERVAL_S),
    ("status.wan.quality", lambda c: c._lire_qualite(), router_api._QUALITE_INTERVAL_S),
    ("status.client", lambda c: c._lire_clients(), router_api._CLIENTS_INTERVAL_S),
    ("status.pepvpn?profile", lambda c: c._lire_profils(), router_api._PROFIL_INTERVAL_S),
    ("status.pepvpn?peer", lambda c: c._attach_tunnel(_liens()), 0.0),
    ("status.log", lambda c: c.get_router_events({}), 0.0),
]


@pytest.mark.parametrize(("endpoint", "lire", "intervalle"), COMPLEMENTS,
                         ids=[c[0] for c in COMPLEMENTS])
async def test_un_refus_passager_ne_condamne_pas_le_complement(horloge, endpoint, lire, intervalle):
    """Un 999 « occupé » : redemandé dès le cycle suivant."""
    routeur = _Routeur({endpoint: [OCCUPE, None]})
    client = await _client(routeur)
    await lire(client)
    horloge[0] += intervalle + 1
    await lire(client)
    assert routeur.demandes.count(endpoint) == 2, "un refus passager a condamné la lecture"
    await client.aclose()


@pytest.mark.parametrize(("endpoint", "lire", "intervalle"), COMPLEMENTS,
                         ids=[c[0] for c in COMPLEMENTS])
async def test_un_refus_de_droits_est_redemande_passe_le_delai(horloge, endpoint, lire, intervalle):
    """Un 401 qui survit au login : retenu — un login par cycle sinon — mais
    redemandé passé le délai, et non condamné jusqu'au redémarrage."""
    routeur = _Routeur({endpoint: [DROITS, DROITS, None]})
    client = await _client(routeur)
    await lire(client)
    assert routeur.demandes.count(endpoint) == 2      # la levée de doute
    # Un échec ne marque pas l'instant de lecture : seul le refus retenu peut
    # empêcher la relecture au cycle suivant.
    horloge[0] += 1
    await lire(client)
    assert routeur.demandes.count(endpoint) == 2, "retenu : pas de login par cycle"
    horloge[0] += router_api._HORS_PERIMETRE_REESSAI_S + intervalle
    await lire(client)
    assert routeur.demandes.count(endpoint) == 3, "le refus de droits n'expirait jamais"
    await client.aclose()


@pytest.mark.parametrize(("endpoint", "lire", "intervalle"), COMPLEMENTS,
                         ids=[c[0] for c in COMPLEMENTS])
async def test_un_endpoint_inconnu_est_retenu_puis_redemande(horloge, endpoint, lire, intervalle):
    routeur = _Routeur({endpoint: [INCONNU, None]})
    client = await _client(routeur)
    await lire(client)
    horloge[0] += router_api._HORS_PERIMETRE_REESSAI_S + intervalle
    await lire(client)
    assert routeur.demandes.count(endpoint) == 1, "un endpoint inconnu se redemandait"
    horloge[0] += router_api._NON_PROPOSE_REESSAI_S
    await lire(client)
    assert routeur.demandes.count(endpoint) == 2, "un endpoint inconnu ne se redemandait jamais"
    await client.aclose()


@pytest.mark.parametrize(("endpoint", "lire", "intervalle"), COMPLEMENTS,
                         ids=[c[0] for c in COMPLEMENTS])
async def test_un_refus_d_identifiants_remonte_toujours(endpoint, lire, intervalle):
    def refuse(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/login":
            return httpx.Response(200, json={"stat": "fail", "message": "refusé"})
        return httpx.Response(200, json={"stat": "ok", "response": {}})

    cfg = RouterConfig(id="r", name="R", host="192.0.2.1", username="admin", password="x")
    client = RouterApiPeplinkClient(cfg)
    await client._http.aclose()
    client._http = httpx.AsyncClient(transport=httpx.MockTransport(refuse))
    with pytest.raises(CredentialsRefusedError):
        await lire(client)
    await client.aclose()


async def test_la_temperature_refusee_pour_un_wan_ne_retire_que_ce_wan(horloge):
    """404 « Unsupported request » sur un WAN : c'est CE WAN que le firmware
    refuse. L'autre reste lu, et le refusé est redemandé au bout du délai."""
    routeur = _Routeur({
        'status.cellularModule.temperature#{"connId":2}': [NON_SUPPORTE, None],
    })
    client = await _client(routeur)
    for _ in range(2):
        await client._attach_modem_temperature(_liens())
        horloge[0] += router_api._TEMPERATURE_INTERVAL_S + 1
    demandes = [d for d in routeur.demandes if d.startswith("status.cellularModule")]
    assert demandes.count('status.cellularModule.temperature#{"connId":2}') == 1
    assert demandes.count('status.cellularModule.temperature#{"connId":3}') == 2
    horloge[0] += router_api._NON_PROPOSE_REESSAI_S
    await client._attach_modem_temperature(_liens())
    assert routeur.demandes.count('status.cellularModule.temperature#{"connId":2}') == 2
    await client.aclose()


async def test_un_refus_de_droits_sur_la_temperature_vaut_pour_tous_les_modems(horloge):
    """Les droits portent sur l'endpoint, pas sur le WAN : un seul login de
    levée de doute, et le second modem n'est pas demandé."""
    routeur = _Routeur({"status.cellularModule.temperature": [DROITS]})
    client = await _client(routeur)
    await client._attach_modem_temperature(_liens())
    assert routeur.logins == 2
    assert routeur.demandes == [
        'status.cellularModule.temperature#{"connId":2}',
        'status.cellularModule.temperature#{"connId":2}',
    ]
    await client.aclose()


async def test_le_debit_par_wan_n_est_nie_que_sur_un_refus_retenu(horloge):
    """`per_wan_throughput` suivait le drapeau : un 999 passager le niait pour
    toujours. Il ne dit non que tant qu'un refus est retenu."""
    routeur = _Routeur({"status.traffic": [OCCUPE, None]})
    client = await _client(routeur)
    await client._attach_traffic(_liens())
    assert (await client.get_capabilities()).per_wan_throughput is True

    routeur.refus["status.traffic"] = [INCONNU, None]
    await client._attach_traffic(_liens())
    assert (await client.get_capabilities()).per_wan_throughput is False
    horloge[0] += router_api._NON_PROPOSE_REESSAI_S + 1
    await client._attach_traffic(_liens())
    assert (await client.get_capabilities()).per_wan_throughput is True
    await client.aclose()


async def test_une_fiche_sondee_sur_un_refus_passager_reste_a_confirmer():
    """`info.capability` refusé d'un 999 « occupé » : la fiche sondée n'est pas
    définitive — un refus passager ne tranche rien."""
    routeur = _Routeur({"info.capability": [OCCUPE, None]})
    client = await _client(routeur)
    caps = await client.get_capabilities()
    assert caps.source == "probed" and caps.provisional is True
    caps = await client.get_capabilities()
    assert caps.source == "declared"
    await client.aclose()


async def test_nouveaux_identifiants_effacent_tout_ce_qui_est_retenu(horloge):
    routeur = _Routeur({"status.traffic": [INCONNU, None], "status.wan.quality": [DROITS, DROITS, None]})
    client = await _client(routeur)
    await client._attach_traffic(_liens())
    await client._lire_qualite()
    client.allow_new_credentials()
    await client._attach_traffic(_liens())
    await client._lire_qualite()
    assert routeur.demandes.count("status.traffic") == 2
    assert routeur.demandes.count("status.wan.quality") == 3
    await client.aclose()
