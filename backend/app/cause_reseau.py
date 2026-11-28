"""La cause d'un échec réseau, en une phrase française qui n'est jamais vide.

Partagée par le superviseur (état d'un équipement injoignable, affiché dans la
Découverte et le bandeau) et par le témoin (connexion refusée). Le texte brut
du système — « [Errno 54] Connection reset by peer », en anglais et numéroté —
s'affichait tel quel : il est traduit quand on le reconnaît, et débarrassé de
son numéro sinon.
"""

from __future__ import annotations

import errno
import re
import socket
import ssl

import httpx

from .peplink.router_api import redact

# Les erreurs système reconnues, par numéro : la valeur dépend du système
# (ECONNRESET vaut 54 sur macOS, 104 sous Linux, 10054 sous Windows), d'où la
# lecture par le nom du module `errno` et non par la valeur.
_PAR_ERRNO = {
    errno.ECONNRESET: "connexion interrompue par l'équipement",
    errno.ECONNREFUSED: "connexion refusée par l'adresse",
    errno.EHOSTUNREACH: "aucune route vers l'adresse",
    errno.ENETUNREACH: "réseau injoignable",
    errno.EHOSTDOWN: "adresse sans réponse",
    errno.ETIMEDOUT: "délai de connexion dépassé",
}
# Windows rend ses propres numéros (WSAE…) dans `winerror`.
_PAR_WINERROR = {
    10054: "connexion interrompue par l'équipement",
    10061: "connexion refusée par l'adresse",
    10065: "aucune route vers l'adresse",
    10051: "réseau injoignable",
    10060: "délai de connexion dépassé",
}
_NUMERO = re.compile(r"^\s*\[(?:Errno|WinError)\s+-?\d+\]\s*")


def _traduire(exc: BaseException) -> str | None:
    """La phrase d'une erreur reconnue, ou None."""
    if isinstance(exc, httpx.ConnectTimeout):
        return "délai de connexion dépassé"
    if isinstance(exc, (httpx.ReadTimeout, httpx.WriteTimeout, httpx.PoolTimeout)):
        return "délai de réponse dépassé"
    if isinstance(exc, httpx.RemoteProtocolError):
        return "connexion fermée sans réponse"
    if isinstance(exc, ssl.SSLError):
        return "échec de la négociation TLS"
    if isinstance(exc, socket.gaierror):
        return "nom d'hôte inconnu"
    if isinstance(exc, ConnectionResetError):
        return _PAR_ERRNO[errno.ECONNRESET]
    if isinstance(exc, ConnectionRefusedError):
        return _PAR_ERRNO[errno.ECONNREFUSED]
    if isinstance(exc, OSError):
        winerror = getattr(exc, "winerror", None)
        if winerror in _PAR_WINERROR:
            return _PAR_WINERROR[winerror]
        if exc.errno in _PAR_ERRNO:
            return _PAR_ERRNO[exc.errno]
    return None


def cause_reseau(exc: BaseException) -> str:
    """Pourquoi la requête a échoué. La chaîne des causes est remontée : httpx
    enveloppe l'erreur système dans une erreur de transport souvent sans texte."""
    chaine: list[BaseException] = []
    courante: BaseException | None = exc
    while courante is not None and all(courante is not vue for vue in chaine):
        chaine.append(courante)
        courante = courante.__cause__ or courante.__context__
    for maillon in chaine:
        phrase = _traduire(maillon)
        if phrase:
            return phrase
    for maillon in chaine:
        texte = _NUMERO.sub("", redact(str(maillon))).strip()
        if texte:
            return texte
    return type(exc).__name__
