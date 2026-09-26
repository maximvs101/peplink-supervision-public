"""Identification d'un équipement Peplink, sans aucun identifiant.

Trois indices, du moins au plus probant, tous obtenus sans authentification :

  1. le préfixe MAC dans le cache ARP (indice, non exhaustif) ;
  2. le nom porté par le certificat TLS, propre aux équipements Peplink ;
  3. la réponse de `GET /api/info.firmware`, **preuve** que l'API est là et
     que le firmware est exploitable.

Le point 3 est le seul endpoint lisible sans session, avec `info.time` : tout
le reste renvoie 401 (mesuré sur MAX HD4 MBX 8.5.4). Ces refus **n'alimentent
pas** le compteur anti-force-brute — vérifié : douze refus consécutifs suivis
d'une connexion admin réussie —, donc sonder est sans risque. Les tentatives de
CONNEXION ratées, elles, comptent : cinq suspendent l'accès pendant 5 minutes.
D'où la règle absolue de ce module : **il n'envoie jamais d'identifiant.**
"""

from __future__ import annotations

import asyncio
import logging
import ssl

import httpx

from ..models import DiscoveredDevice
from .network import arp_table, candidate_hosts, is_peplink_mac

logger = logging.getLogger(__name__)

# Court : on sonde des adresses souvent vides, et l'utilisateur attend.
_TIMEOUT = 2.5

# Nom porté par le certificat des équipements Peplink.
_CERT_MARKERS = ("peplink", "pepwave")


# Étiquettes DER des chaînes de caractères : PrintableString, UTF8String, IA5String.
_DER_STRING_TAGS = (0x13, 0x0C, 0x16)
_MAX_LABEL = 128


def _der_string_at(der: bytes, position: int) -> str | None:
    """Chaîne DER complète contenant l'octet `position`, ou None.

    Une chaîne DER est précédée de son étiquette et de sa longueur. Plutôt que
    de deviner où elle s'arrête — ce qui capture l'octet de structure suivant —
    on remonte jusqu'à l'en-tête et on lit la longueur déclarée.
    """
    for start in range(position, max(-1, position - _MAX_LABEL), -1):
        if start < 2 or der[start - 2] not in _DER_STRING_TAGS:
            continue
        length = der[start - 1]
        if length < 0x80 and start <= position < start + length:
            try:
                return der[start:start + length].decode("utf-8")
            except UnicodeDecodeError:
                return None
    return None


async def _certificate_name(host: str, port: int = 443) -> str | None:
    """Nom de constructeur porté par le certificat, s'il y en a un.

    Le certificat n'est pas validé — il est auto-signé — et Python renvoie un
    dictionnaire VIDE pour un certificat décodé quand la vérification est
    désactivée. On lit donc la forme binaire, qui reste disponible, et on y
    cherche le marqueur constructeur.
    """
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    context.check_hostname = False
    context.verify_mode = ssl.CERT_NONE
    try:
        _, writer = await asyncio.wait_for(
            asyncio.open_connection(host, port, ssl=context), timeout=_TIMEOUT
        )
    except (OSError, asyncio.TimeoutError, ssl.SSLError):
        return None
    try:
        der = writer.get_extra_info("ssl_object").getpeercert(binary_form=True) or b""
        lowered = der.lower()
        for marker in _CERT_MARKERS:
            position = lowered.find(marker.encode())
            if position >= 0:
                return _der_string_at(der, position)
        return None
    except (AttributeError, ValueError):
        return None
    finally:
        writer.close()
        try:
            await writer.wait_closed()
        except (OSError, ssl.SSLError):
            pass


def _echec_tls(exc: BaseException) -> bool:
    """L'erreur de connexion vient-elle de la poignée de main TLS ?

    httpx enveloppe l'exception d'origine sans en changer la classe : on
    remonte la chaîne des causes jusqu'à trouver — ou non — une `ssl.SSLError`.
    """
    vue: set[int] = set()
    courante: BaseException | None = exc
    while courante is not None and id(courante) not in vue:
        if isinstance(courante, ssl.SSLError):
            return True
        vue.add(id(courante))
        courante = courante.__cause__ or courante.__context__
    return False


async def probe(host: str, mac: str | None = None) -> DiscoveredDevice | None:
    """Interroge une adresse. Retourne None si ce n'est pas un Peplink joignable."""
    evidence: list[str] = []

    if is_peplink_mac(mac):
        evidence.append(f"adresse MAC Peplink ({mac})")

    certificate = await _certificate_name(host)
    if certificate:
        evidence.append(f"certificat « {certificate} »")

    firmware = None
    # HTTPS d'abord, HTTP seulement si le port 443 est FERMÉ. La sonde
    # n'essayait qu'HTTPS : sur un Peplink sans TLS — rencontré, 8.5.4 build
    # 5808, port 80 seul ouvert — elle le voyait par son adresse MAC mais
    # rendait `api_reachable=False`, et l'appairage aurait visé une origine qui
    # n'existe pas.
    #
    # Mais le repli ne doit jouer QUE sur un refus de connexion. Une première
    # version l'engageait sur n'importe quel échec HTTPS — délai dépassé sur un
    # routeur chargé, réponse `stat: fail` — et rendait alors `http://` pour un
    # équipement qui parle TLS, dont le certificat venait d'être lu : le
    # formulaire d'appairage en héritait, et le mot de passe partait ensuite
    # en clair à chaque cycle, pour toute la durée de l'appairage. Un
    # certificat vu vaut preuve que l'équipement parle TLS ; un délai dépassé
    # n'est pas un port fermé.
    schema_retenu: str | None = None
    api_a_repondu = False
    async with httpx.AsyncClient(verify=False, timeout=_TIMEOUT) as http:
        for schema in ("https", "http"):
            if schema == "http" and (certificate is not None or api_a_repondu):
                break
            try:
                payload = (await http.get(f"{schema}://{host}/api/info.firmware")).json()
            except httpx.ConnectError as exc:
                if _echec_tls(exc):
                    # Le port est OUVERT et parle TLS : c'est la poignée de main
                    # qui a échoué — protocole, suite, certificat que la pile
                    # refuse. httpx range cet échec dans `ConnectError`, avec le
                    # port fermé, et le repli tentait alors l'HTTP sur un
                    # équipement TLS dont le certificat n'avait pas pu être lu
                    # — pour le même motif. Le mot de passe serait parti en
                    # clair. On ne conclut rien, et on ne descend pas.
                    logger.debug("TLS refusé en %s sur %s : %s", schema, host, exc)
                    break
                # Port fermé : le seul cas qui autorise à essayer l'autre schéma.
                logger.debug("Port fermé en %s sur %s : %s", schema, host, exc)
                continue
            except Exception as exc:  # délai dépassé, réponse illisible : on s'arrête là
                logger.debug("Pas d'API Peplink lisible en %s sur %s : %s", schema, host, exc)
                break
            api_a_repondu = True
            if payload.get("stat") == "ok":
                banks = payload.get("response") or {}
                in_use = next(
                    (b for b in banks.values() if isinstance(b, dict) and b.get("inUse")), None
                )
                firmware = (in_use or {}).get("version")
                schema_retenu = schema
                evidence.append(
                    "API locale Peplink accessible"
                    + (" — en HTTP, sans TLS" if schema == "http" else "")
                )
            break

    if not evidence:
        return None

    return DiscoveredDevice(
        # L'hôte porte son schéma quand ce n'est pas HTTPS : c'est lui que le
        # formulaire d'appairage pré-remplit, et le client le respecte tel quel.
        host=f"http://{host}" if schema_retenu == "http" else host,
        mac=mac,
        firmware=firmware,
        certificate_name=certificate,
        # « Joignable » veut dire que l'API a RÉPONDU `ok`, pas qu'on a su lire
        # un banc de firmware dedans : sinon l'écran cachait le bouton
        # d'appairage à un équipement qui venait de répondre.
        api_reachable=schema_retenu is not None,
        evidence=evidence,
    )


async def discover(extra_hosts: list[str] | None = None) -> list[DiscoveredDevice]:
    """Cherche des équipements Peplink sur le réseau local.

    Sonde en parallèle une courte liste d'adresses probables — passerelles,
    bornes usuelles du sous-réseau, adresses d'usine — jamais un balayage de
    sous-réseau complet : c'est lent, bruyant, et inutile puisqu'un Peplink de
    terrain est presque toujours la passerelle.

    Aucun identifiant n'est envoyé. Les équipements trouvés doivent ensuite être
    appairés explicitement.
    """
    # Les deux lectures de l'environnement réseau lancent des SOUS-PROCESSUS
    # (`netstat`, `ip`, `arp`) avec un délai de garde de cinq secondes chacun.
    # Elles étaient appelées telles quelles depuis cette coroutine, donc sur la
    # boucle : au nominal c'est 12 ms mesurées, mais le plafond est de quinze
    # secondes si une commande pend — et pendant ce temps la boucle ne tourne
    # plus DU TOUT. La scrutation de toute la flotte s'arrête, les relevés
    # vieillissent, et l'écran passe au gris sans qu'aucun lien n'ait bougé.
    # Un délai de garde de cinq secondes est une promesse qu'il peut durer.
    hosts, macs = await asyncio.gather(
        asyncio.to_thread(candidate_hosts, extra_hosts),
        asyncio.to_thread(arp_table),
    )
    logger.info("Découverte : %d adresse(s) sondée(s)", len(hosts))

    results = await asyncio.gather(
        *(probe(host, macs.get(host)) for host in hosts), return_exceptions=True
    )

    found: list[DiscoveredDevice] = []
    for host, result in zip(hosts, results):
        if isinstance(result, BaseException):
            logger.debug("Sondage de %s interrompu : %s", host, result)
        elif result is not None:
            found.append(result)
    return found
