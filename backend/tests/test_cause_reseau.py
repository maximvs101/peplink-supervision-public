"""La cause d'un échec réseau, dite en français et sans jargon système.

Vu le 21/11 dans la Découverte : « [Errno 54] Connection reset by peer » sous
un équipement injoignable — le texte brut du système, en anglais, avec son
numéro d'erreur.
"""

from __future__ import annotations

import errno
import socket
import ssl

import httpx
import pytest

from app.cause_reseau import cause_reseau


def _enveloppe(origine: BaseException) -> httpx.ConnectError:
    """Comme httpx : une erreur de transport qui porte l'erreur système en cause."""
    try:
        raise origine
    except BaseException as exc:
        enveloppe = httpx.ConnectError("")
        enveloppe.__cause__ = exc
    return enveloppe


@pytest.mark.parametrize(
    ("origine", "attendu"),
    [
        (ConnectionResetError(errno.ECONNRESET, "Connection reset by peer"), "connexion interrompue par l'équipement"),
        (ConnectionRefusedError(errno.ECONNREFUSED, "Connection refused"), "connexion refusée par l'adresse"),
        (OSError(errno.EHOSTUNREACH, "No route to host"), "aucune route vers l'adresse"),
        (OSError(errno.ENETUNREACH, "Network is unreachable"), "réseau injoignable"),
        (socket.gaierror(socket.EAI_NONAME, "nodename nor servname provided"), "nom d'hôte inconnu"),
        (ssl.SSLError(1, "[SSL: WRONG_VERSION_NUMBER] wrong version number"), "échec de la négociation TLS"),
    ],
)
def test_une_erreur_systeme_est_dite_en_francais(origine, attendu):
    assert cause_reseau(_enveloppe(origine)) == attendu
    assert cause_reseau(origine) == attendu


def test_aucun_numero_d_erreur_ni_texte_systeme_brut():
    texte = cause_reseau(_enveloppe(ConnectionResetError(54, "Connection reset by peer")))
    assert "Errno" not in texte
    assert "reset" not in texte


def test_les_delais_et_la_fermeture_sans_reponse():
    assert cause_reseau(httpx.ConnectTimeout("")) == "délai de connexion dépassé"
    assert cause_reseau(httpx.ReadTimeout("")) == "délai de réponse dépassé"
    assert cause_reseau(httpx.RemoteProtocolError("Server disconnected")) == "connexion fermée sans réponse"


def test_une_cause_inconnue_garde_son_texte_sans_numero():
    # Un texte qu'on ne sait pas traduire reste dit, mais sans « [Errno n] ».
    assert cause_reseau(OSError(errno.EPERM, "Operation not permitted")) == "Operation not permitted"
    assert cause_reseau(RuntimeError("")) == "RuntimeError"
