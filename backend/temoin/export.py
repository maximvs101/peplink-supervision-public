"""Exports CSV du témoin Ethernet (fonctions pures).

Même convention que l'export du superviseur : BOM UTF-8, séparateur « ; »,
virgule décimale, pour s'ouvrir tel quel dans Excel en français. Ni série, ni
position, ni nom de WAN : seules les clés listées dans `_CHAMPS_ENTETE` sortent.
"""

from datetime import datetime, timezone, tzinfo

from temoin.regles import Coupure, Releve

_BOM = "﻿"
_FIN_LIGNE = "\r\n"
_FORMAT = "%Y-%m-%d %H:%M:%S"
_CHAMPS_ENTETE = ("modele", "firmware", "wan", "debut", "fin")
_LIBELLES = {"modele": "modèle", "firmware": "firmware", "wan": "lien",
             "debut": "début", "fin": "fin"}

_COLONNES_COUPURES = "debut;fin;duree_s;type;cause"
_COLONNES_RELEVES = "instant;mesure;connecte;message;anciennete_s;latence_ms"


def _local(d: datetime, fuseau: tzinfo | None) -> datetime:
    if d.tzinfo is None:
        d = d.replace(tzinfo=timezone.utc)
    return d.astimezone(fuseau)  # fuseau None : celui du poste


def _instant(d: datetime | None, fuseau: tzinfo | None) -> str:
    return "" if d is None else _local(d, fuseau).strftime(_FORMAT)


def _decalage(fuseau: tzinfo | None, repere: datetime | None = None) -> str:
    # Décalage à l'instant de début d'observation (heure d'été ou d'hiver).
    dec = _local(repere or datetime.now(timezone.utc), fuseau).utcoffset()
    minutes = int(dec.total_seconds() // 60) if dec is not None else 0
    signe = "-" if minutes < 0 else "+"
    h, m = divmod(abs(minutes), 60)
    return f"UTC{signe}{h:02d}:{m:02d}"


def _nombre(v: float | int | None) -> str:
    if v is None:
        return ""
    if isinstance(v, float) and not v.is_integer():
        return f"{round(v, 1)}".replace(".", ",")
    return str(int(v))


def _texte(v: str | None) -> str:
    """Cellule de texte : sans séparateur ni saut de ligne ni guillemet, et
    sans amorce de formule (= + - @) que le tableur exécuterait."""
    if not v:
        return ""
    for c in (";", "\r", "\n", '"'):
        v = v.replace(c, " ")
    v = " ".join(v.split())
    if v and v[0] in "=+-@":
        v = "'" + v
    return v


def _entete(entete: dict, fuseau: tzinfo | None) -> list[str]:
    lignes = []
    for cle in _CHAMPS_ENTETE:
        val = entete.get(cle)
        if cle == "wan":
            val = f"WAN {val}" if val not in (None, "") else ""
        elif isinstance(val, datetime):
            val = _instant(val, fuseau)
        lignes.append(f"# {_LIBELLES[cle]};{_texte(None if val is None else str(val))}")
    debut = entete.get("debut")
    lignes.append(f"# fuseau;{_decalage(fuseau, debut if isinstance(debut, datetime) else None)}")
    return lignes


def _assembler(lignes: list[str]) -> str:
    return _BOM + _FIN_LIGNE.join(lignes) + _FIN_LIGNE


def coupures_csv(entete: dict, coupures: list[Coupure], fuseau: tzinfo | None = None) -> str:
    lignes = [*_entete(entete, fuseau), _COLONNES_COUPURES]
    for c in coupures:
        genre = c.genre
        if c.genre == "coupure" and c.fin is None:
            genre += " (en cours)"
        elif c.genre == "coupure" and not c.fin_observee:
            # Interrompue par un trou ou un routeur injoignable : la durée est un minimum.
            genre += " (fin non observée)"
        lignes.append(";".join([
            _instant(c.debut, fuseau), _instant(c.fin, fuseau),
            _nombre(c.duree_s), genre, _texte(c.cause),
        ]))
    return _assembler(lignes)


def releves_csv(entete: dict, releves: list[Releve], fuseau: tzinfo | None = None) -> str:
    lignes = [*_entete(entete, fuseau), _COLONNES_RELEVES]
    for r in releves:
        if r.desactive:
            connecte = "désactivé"
        else:
            connecte = "" if r.connecte is None else ("oui" if r.connecte else "non")
        lignes.append(";".join([
            _instant(r.ts, fuseau), "oui" if r.joignable else "non", connecte,
            _texte(r.message), _nombre(r.anciennete_s), _nombre(r.latence_ms),
        ]))
    return _assembler(lignes)
