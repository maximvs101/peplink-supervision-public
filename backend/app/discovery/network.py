"""Lecture de l'environnement réseau local, sans privilège ni dépendance.

Sert à proposer des adresses où chercher un équipement Peplink. Rien ici
n'interroge un équipement : ce module ne fait que dire *où regarder*.
"""

from __future__ import annotations

import ipaddress
import logging
import re
import socket
import subprocess

logger = logging.getLogger(__name__)

# Adresses d'usine des gammes Peplink/Pepwave, testées après la passerelle.
# La passerelle reste le meilleur candidat : sur un réseau d'équipement, le
# Peplink EST la passerelle dans la quasi-totalité des cas.
DEFAULT_ADDRESSES: tuple[str, ...] = (
    "192.168.50.1",   # Pepwave MAX / MBX / Dome
    "192.168.1.1",    # Balance
    "192.168.0.1",
    "192.168.209.1",  # observé sur certaines configurations d'usine
)

# Préfixes MAC attribués à Peplink. Indice fort mais NON exhaustif : une adresse
# inconnue ne disqualifie pas, elle n'apporte simplement rien.
#
# `d4:13:f8` : constaté sur le matériel, pas recopié d'un registre — un Dome Pro
# Duo (8.5.1 build 5714) s'est déclaré tel par son API authentifiée avec la MAC
# LAN `d4:13:f8:…`, et la découverte ne l'avait reconnu que par son certificat.
PEPLINK_OUIS: frozenset[str] = frozenset({"a8:c0:ea", "00:1a:dd", "d4:13:f8"})

_TIMEOUT = 5.0


def local_address() -> str | None:
    """Adresse IPv4 de l'interface qui porte la route par défaut.

    Le connect() sur un datagramme n'émet aucun paquet : il ne fait que
    demander au système quelle interface serait utilisée. L'adresse cible est
    prise dans le préfixe de documentation TEST-NET-1, jamais routé.
    """
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        sock.connect(("192.0.2.1", 9))
        return sock.getsockname()[0]
    except OSError as exc:
        logger.debug("Adresse locale indéterminable : %s", exc)
        return None
    finally:
        sock.close()


def _run(command: list[str]) -> str:
    try:
        # `encoding=` posé : sans lui, la sortie est décodée dans l'encodage de
        # la console — cp1252 sous Windows, alors que `route print` parle en
        # cp850 — et un octet hors de la page levait une `UnicodeDecodeError`
        # que l'`except` ci-dessous ne rattrape pas : /api/discover en 500. Les
        # motifs cherchés sont des chiffres et des points ; un accent
        # remplacé ne leur fait rien.
        return subprocess.run(
            command, capture_output=True, text=True, encoding="utf-8", errors="replace",
            timeout=_TIMEOUT, check=False,
        ).stdout
    except (OSError, subprocess.SubprocessError) as exc:
        logger.debug("Commande %s indisponible : %s", command[0], exc)
        return ""


def default_gateways() -> list[str]:
    """Passerelles par défaut, selon la table de routage du système.

    Trois commandes couvrent macOS, Linux et Windows. Aucune n'est requise :
    à défaut, `candidate_hosts` retombe sur les adresses déduites du sous-réseau.
    """
    seen: list[str] = []
    for command, pattern in (
        (["netstat", "-rn", "-f", "inet"], r"^(?:default|0\.0\.0\.0)\s+(\d+\.\d+\.\d+\.\d+)"),
        (["ip", "route", "show", "default"], r"^default via (\d+\.\d+\.\d+\.\d+)"),
        (["route", "print", "-4"], r"^\s*0\.0\.0\.0\s+0\.0\.0\.0\s+(\d+\.\d+\.\d+\.\d+)"),
    ):
        for match in re.finditer(pattern, _run(command), re.MULTILINE):
            address = match.group(1)
            if address not in seen and not address.startswith("0."):
                seen.append(address)
        if seen:
            break
    return seen


def arp_table() -> dict[str, str]:
    """Adresse IPv4 -> adresse MAC, d'après le cache ARP du système.

    Lecture seule d'un cache déjà constitué : n'émet aucune trame.
    """
    output = _run(["arp", "-an"]) or _run(["arp", "-a"])
    table: dict[str, str] = {}
    for ip, mac in re.findall(
        r"\(?(\d+\.\d+\.\d+\.\d+)\)?[^\n]*?((?:[0-9a-fA-F]{1,2}[:-]){5}[0-9a-fA-F]{1,2})", output
    ):
        # macOS abrège les octets nuls ("a8:c0:ea:0:1:2") : on normalise.
        table[ip] = ":".join(part.rjust(2, "0") for part in mac.lower().replace("-", ":").split(":"))
    return table


def is_peplink_mac(mac: str | None) -> bool:
    """Le préfixe constructeur désigne-t-il Peplink ?"""
    return bool(mac) and mac.lower()[:8] in PEPLINK_OUIS


def candidate_hosts(extra: list[str] | None = None) -> list[str]:
    """Adresses où chercher un équipement, de la plus probable à la moins.

    Ordre : passerelles déclarées, puis les adresses usuelles du sous-réseau
    local, puis les adresses d'usine Peplink. Les doublons sont retirés en
    conservant le premier rang.
    """
    ordered: list[str] = list(extra or [])
    ordered += default_gateways()

    address = local_address()
    if address:
        try:
            network = ipaddress.ip_network(f"{address}/24", strict=False)
            ordered += [str(network.network_address + 1), str(network.broadcast_address - 1)]
        except ValueError:
            pass

    ordered += list(DEFAULT_ADDRESSES)

    seen: set[str] = set()
    hosts: list[str] = []
    for host in ordered:
        if host in seen:
            continue
        try:
            ipaddress.ip_address(host)
        except ValueError:
            continue
        seen.add(host)
        hosts.append(host)
    return hosts
