"""Appairage : une tentative, déclenchée par l'utilisateur.

Cinq tentatives de connexion ratées suspendent l'accès à l'équipement pendant
cinq minutes, y compris avec les bons identifiants. Toute la conception découle
de là : aucune relance automatique, aucun essai de mot de passe, et une erreur
qui distingue « identifiants refusés » de « injoignable » pour que l'utilisateur
sache quoi corriger.
"""

import inspect
from unittest.mock import AsyncMock, patch

import httpx
import pytest

from app.devices.registry import DeviceRegistry
from app.fleet import RouterConfig
from app.models import DeviceInfo
from app.pairing.service import PairingError, _device_id, pair
from app.poller import Poller
from app.peplink.router_api import AuthenticationError, RouterApiError

INFO = DeviceInfo(model="Pepwave MAX HD4 MBX", serial="0000-0000-0010",
                  firmware="8.5.4 build 6253", name="GC-MBX-EXT8-F634")


def _patch_identify(result=None, error: Exception | None = None):
    mock = AsyncMock(side_effect=error) if error else AsyncMock(return_value=result)
    return patch("app.pairing.service.RouterApiPeplinkClient.identify", mock)


async def test_successful_pairing_uses_the_serial_as_identity():
    """L'adresse dépend du réseau, le nom est modifiable : seul le série tient."""
    registry = DeviceRegistry(default_mode="mock")
    with _patch_identify(INFO), patch(
        "app.pairing.service.RouterApiPeplinkClient.aclose", AsyncMock()
    ):
        record = await pair(registry, "192.0.2.1", username="admin", password="x")

    assert record.id == "0000-0000-0010"
    assert record.config.serial == "0000-0000-0010"
    assert record.config.source == "paired" and record.config.mode == "router"
    assert record.config.name == "GC-MBX-EXT8-F634"
    assert registry.get("0000-0000-0010") is not None
    await registry.close()


async def test_pairing_the_same_device_again_keeps_its_identifier():
    """Rebranché sur une autre adresse, c'est le même équipement."""
    registry = DeviceRegistry(default_mode="mock")
    with _patch_identify(INFO), patch(
        "app.pairing.service.RouterApiPeplinkClient.aclose", AsyncMock()
    ):
        first = await pair(registry, "192.0.2.1", username="admin", password="x")
        second = await pair(registry, "10.0.0.1", username="admin", password="x")

    assert first.id == second.id
    assert len(registry.snapshot()) == 1        # pas de doublon
    assert registry.get(second.id).config.host == "10.0.0.1"
    await registry.close()


async def test_refused_credentials_are_reported_as_such():
    """L'interface doit pouvoir dire « corrigez le mot de passe », pas « vérifiez le câble »."""
    registry = DeviceRegistry(default_mode="mock")
    with _patch_identify(error=AuthenticationError("login refusé")), patch(
        "app.pairing.service.RouterApiPeplinkClient.aclose", AsyncMock()
    ):
        with pytest.raises(PairingError) as err:
            await pair(registry, "192.0.2.1", username="admin", password="faux")

    assert err.value.credentials is True
    assert "5 minutes" in str(err.value)         # l'utilisateur est averti du blocage
    assert registry.snapshot() == []             # rien n'a été ajouté
    await registry.close()


async def test_unreachable_device_is_not_a_credential_problem():
    registry = DeviceRegistry(default_mode="mock")
    with _patch_identify(error=httpx.ConnectTimeout("délai dépassé")), patch(
        "app.pairing.service.RouterApiPeplinkClient.aclose", AsyncMock()
    ):
        with pytest.raises(PairingError) as err:
            await pair(registry, "192.0.2.1", username="admin", password="x")

    assert err.value.credentials is False
    assert "injoignable" in str(err.value)
    await registry.close()


async def test_pairing_attempts_the_connection_exactly_once():
    """Aucune relance : c'est la règle dont dépend le reste."""
    registry = DeviceRegistry(default_mode="mock")
    identify = AsyncMock(side_effect=AuthenticationError("refusé"))
    with patch("app.pairing.service.RouterApiPeplinkClient.identify", identify), patch(
        "app.pairing.service.RouterApiPeplinkClient.aclose", AsyncMock()
    ):
        with pytest.raises(PairingError):
            await pair(registry, "192.0.2.1", username="admin", password="faux")

    assert identify.await_count == 1
    await registry.close()


async def test_missing_credentials_are_refused_before_any_connection():
    """Ne pas consommer une tentative pour une demande incomplète."""
    registry = DeviceRegistry(default_mode="mock")
    identify = AsyncMock()
    with patch("app.pairing.service.RouterApiPeplinkClient.identify", identify):
        with pytest.raises(PairingError) as err:
            await pair(registry, "192.0.2.1")
    assert err.value.credentials is True
    assert identify.await_count == 0
    await registry.close()


async def test_device_without_identity_is_refused():
    """Un équipement qui ne publie pas son identité n'a pas de clé stable."""
    registry = DeviceRegistry(default_mode="mock")
    with _patch_identify(None), patch(
        "app.pairing.service.RouterApiPeplinkClient.aclose", AsyncMock()
    ):
        with pytest.raises(PairingError, match="identité"):
            await pair(registry, "192.0.2.1", username="admin", password="x")
    await registry.close()


async def test_other_api_errors_are_not_credential_errors():
    registry = DeviceRegistry(default_mode="mock")
    with _patch_identify(error=RouterApiError("code 999")), patch(
        "app.pairing.service.RouterApiPeplinkClient.aclose", AsyncMock()
    ):
        with pytest.raises(PairingError) as err:
            await pair(registry, "192.0.2.1", username="admin", password="x")
    assert err.value.credentials is False
    await registry.close()


@pytest.mark.parametrize(
    "serial,host,expected",
    [
        ("0000-0000-0010", "192.168.1.1", "0000-0000-0010"),
        ("ABCD 1234", "10.0.0.1", "abcd-1234"),
        (None, "192.168.63.1", "192-168-63-1"),
        ("", "10.0.0.1", "10-0-0-1"),
    ],
)
def test_identifier_derivation(serial, host, expected):
    assert _device_id(serial, host) == expected


async def test_re_pairing_purges_what_the_identifier_carried_before():
    """Un identifiant ré-appairé ne doit RIEN reprendre du précédent.

    `registry.add` remplace le record sans purger l'état du poller, et
    l'identifiant se dérive de l'ADRESSE quand le numéro de série manque : un
    matériel remplacé sur site à la même adresse héritait des compteurs du
    précédent — vérifié, dix-huit changements de cellule et cinq coupures sur le
    PREMIER relevé du neuf, hérités puis incrémentés puisque le relevé de
    comparaison était celui de l'ancien châssis. Étaient aussi repris son dernier
    relevé, sa position, et sa session d'alimentation.
    """
    oublies: list[str] = []

    # Le faux poller reprend la signature RÉELLE de `Poller.forget`, qui est
    # SYNCHRONE. Une première version le déclarait `async`, et le test passait
    # sur un appel que la production faisait échouer : `await` sur le `None`
    # d'une méthode synchrone lève, avant `registry.add`, donc l'équipement
    # n'était jamais mis en supervision. Un faux qui ment sur sa signature ne
    # prouve rien.
    assert not inspect.iscoroutinefunction(Poller.forget), (
        "si `forget` devient asynchrone, ce faux et l'appelant doivent suivre"
    )

    class _PollerFictif:
        def forget(self, router_id: str, *, conserver_abonnes: bool = False) -> None:
            oublies.append(router_id)
            # Un ré-appairage garde ses abonnés : c'est le même identifiant,
            # toujours supervisé — les fermer en 4404 faisait perdre le direct
            # aux onglets ouverts jusqu'au rechargement.
            assert conserver_abonnes is True

    registry = DeviceRegistry(default_mode="mock")
    with _patch_identify(INFO), patch(
        "app.pairing.service.RouterApiPeplinkClient.aclose", AsyncMock()
    ):
        record = await pair(
            registry, "192.0.2.1", username="admin", password="x", poller=_PollerFictif()
        )

    assert oublies == [record.id], "l'état du précédent n'a pas été purgé"
    await registry.close()


async def test_pairing_without_a_poller_still_works():
    """L'appairage doit rester utilisable sans supervision en cours — c'est le
    cas des tests et celui d'un démarrage à froid."""
    registry = DeviceRegistry(default_mode="mock")
    with _patch_identify(INFO), patch(
        "app.pairing.service.RouterApiPeplinkClient.aclose", AsyncMock()
    ):
        record = await pair(registry, "192.0.2.1", username="admin", password="x")
    assert record.id == "0000-0000-0010"
    await registry.close()


async def test_pairing_recognises_a_device_already_known_by_its_address():
    """Le rattrapage par adresse manquait, et la base porte la trace de l'accident.

    Un équipement déclaré dans le fichier de flotte n'a pas de numéro de série
    tant qu'il n'a pas été identifié une première fois : avant cela il vaut
    `None`, `find_by_serial` ne peut rien rapprocher, et appairer la même adresse
    créait un SECOND identifiant. Le même routeur physique se retrouvait
    supervisé deux fois — compté deux fois, scruté deux fois, affiché deux fois.

    `find_by_host` existait depuis le début et documentait ce défaut dans sa
    propre docstring au lieu de le corriger : elle n'avait aucun appelant.
    """
    registry = DeviceRegistry(default_mode="mock")
    # Équipement du fichier de flotte : une adresse, pas encore de série.
    await registry.add(RouterConfig(id="mbx-01", name="MBX", host="192.0.2.1",
                                    mode="router", source="file"))

    with _patch_identify(INFO), patch(
        "app.pairing.service.RouterApiPeplinkClient.aclose", AsyncMock()
    ):
        record = await pair(registry, "192.0.2.1", username="admin", password="x")

    assert record.id == "mbx-01", "l'appairage devait se rattacher au record existant"
    assert len(registry.snapshot()) == 1, "un doublon a été créé"
    assert registry.get("mbx-01").config.serial == "0000-0000-0010"
    await registry.close()


async def test_a_different_device_at_the_same_address_is_not_merged():
    """L'erreur inverse serait pire : deux matériels fondus en un seul.

    Un record dont le série est CONNU et différent décrit un autre équipement
    qui occupe maintenant cette adresse. L'adresse est un indice plus faible que
    le série — elle dépend du réseau où l'on se branche.
    """
    registry = DeviceRegistry(default_mode="mock")
    await registry.add(RouterConfig(id="autre-chassis", name="Autre", host="192.0.2.1",
                                    mode="router", serial="AAAA-BBBB-CCCC"))

    with _patch_identify(INFO), patch(
        "app.pairing.service.RouterApiPeplinkClient.aclose", AsyncMock()
    ):
        record = await pair(registry, "192.0.2.1", username="admin", password="x")

    assert record.id == "0000-0000-0010"
    assert len(registry.snapshot()) == 2
    await registry.close()


async def test_pairing_then_the_first_poll_open_a_single_admin_session(tmp_path, monkeypatch):
    """R-1.13 : le client qui a identifié l'équipement est celui qui le scrute.

    Il était fermé après l'identification, et le registre en construisait un
    neuf, qui se reconnectait au premier relevé : deux « login successful » à
    quatre secondes d'écart dans le journal du Dome, le 25/09 (07:49:12 et
    07:49:16), pour un seul appairage.
    """
    import json
    from pathlib import Path

    from app.storage.db import SampleRepository

    capture = json.loads((Path(__file__).parent / "fixtures" / "dome_pro_duo_8.5.4.json")
                         .read_text(encoding="utf-8"))
    logins: list[str] = []

    def routeur(request: httpx.Request) -> httpx.Response:
        chemin = request.url.path.removeprefix("/api/")
        if chemin == "login":
            logins.append(chemin)
            return httpx.Response(200, json={"stat": "ok"})
        cle = chemin
        if request.url.params.get("infoType"):
            cle = f"{chemin}.{request.url.params['infoType']}"
        return httpx.Response(200, json=capture.get(
            cle, {"stat": "fail", "code": 999, "message": "unknown function"}))

    vrai = httpx.AsyncClient
    monkeypatch.setattr(httpx, "AsyncClient",
                        lambda **kw: vrai(transport=httpx.MockTransport(routeur), **kw))

    repo = SampleRepository(str(tmp_path / "s.db"))
    await repo.connect()
    registry = DeviceRegistry(default_mode="mock")
    poller = Poller(registry, repo, interval_seconds=0.5)
    try:
        record = await pair(registry, "192.0.2.1", username="admin", password="x", poller=poller)
        await poller._poll_once()
        assert poller.latest.get(record.id), "le premier relevé a bien eu lieu"
        assert len(logins) == 1
    finally:
        await registry.close()
        await repo.close()


async def test_a_failed_pairing_still_closes_its_client():
    registry = DeviceRegistry(default_mode="mock")
    fermer = AsyncMock()
    for erreur in (AuthenticationError("refus"), httpx.ConnectTimeout("délai"), None):
        with (_patch_identify(None) if erreur is None else _patch_identify(error=erreur)), \
                patch("app.pairing.service.RouterApiPeplinkClient.aclose", fermer):
            with pytest.raises(PairingError):
                await pair(registry, "192.0.2.1", username="admin", password="x")
    assert fermer.await_count == 3
    assert registry.snapshot() == []
    await registry.close()


async def test_the_paired_client_is_adopted_under_the_final_identifier():
    registry = DeviceRegistry(default_mode="mock")
    fermer = AsyncMock()
    with _patch_identify(INFO), patch("app.pairing.service.RouterApiPeplinkClient.aclose", fermer):
        premier = await pair(registry, "192.0.2.1", username="admin", password="x")
        assert fermer.await_count == 0, "le client qui a identifié n'est pas fermé"
        assert premier.client.router_id == "0000-0000-0010"
        client_du_premier = premier.client
        # Ré-appairer : l'ancien client est fermé, le nouveau adopté.
        second = await pair(registry, "10.0.0.1", username="admin", password="x")
    assert fermer.await_count == 1
    assert second.client is not client_du_premier
    assert second.client._base.endswith("10.0.0.1")
    await registry.close()
