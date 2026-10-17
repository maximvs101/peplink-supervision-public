"""Stockage du témoin Ethernet : observations, relevés et faits du routeur.

SQLite en WAL, comme le superviseur. Les dates sont écrites en ISO-8601 UTC et
relues en datetimes avec fuseau. La série d'un équipement reste dans cette base :
elle ne sort jamais par `observations()`.
"""

import asyncio
import os
from datetime import datetime, timezone

import aiosqlite

from temoin.regles import FaitRouteur, Releve

# Une observation est reprise si son dernier relevé date de moins d'une heure.
REPRISE_S = 3600.0

_SCHEMA = """
CREATE TABLE IF NOT EXISTS equipements (
    hote   TEXT PRIMARY KEY,
    serie  TEXT NOT NULL,
    modele TEXT
);
CREATE TABLE IF NOT EXISTS observations (
    id     INTEGER PRIMARY KEY AUTOINCREMENT,
    serie  TEXT NOT NULL,
    wan_id TEXT NOT NULL,
    debut  TEXT NOT NULL,
    fin    TEXT
);
CREATE INDEX IF NOT EXISTS idx_observations_lien ON observations (serie, wan_id, id);
CREATE TABLE IF NOT EXISTS releves (
    id                   INTEGER PRIMARY KEY AUTOINCREMENT,
    observation          INTEGER NOT NULL REFERENCES observations (id),
    ts                   TEXT NOT NULL,
    joignable            INTEGER NOT NULL,
    connecte             INTEGER,
    message              TEXT,
    anciennete_s         INTEGER,
    desactive            INTEGER NOT NULL,
    anciennete_routeur_s INTEGER,
    latence_ms           REAL
);
CREATE INDEX IF NOT EXISTS idx_releves_obs_ts ON releves (observation, ts);
CREATE TABLE IF NOT EXISTS faits (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    observation INTEGER NOT NULL REFERENCES observations (id),
    ts          TEXT NOT NULL,
    genre       TEXT NOT NULL,
    detail      TEXT,
    UNIQUE (observation, ts, genre, detail)
);
CREATE INDEX IF NOT EXISTS idx_faits_obs_ts ON faits (observation, ts);
-- Table à part (et non une colonne d'`equipements`) : une base déjà créée la
-- reçoit par CREATE IF NOT EXISTS, sans migration.
CREATE TABLE IF NOT EXISTS liens_retenus (
    serie  TEXT PRIMARY KEY,
    wan_id TEXT NOT NULL
);
"""


def _ecrire(d: datetime) -> str:
    if d.tzinfo is None:
        d = d.replace(tzinfo=timezone.utc)
    return d.astimezone(timezone.utc).isoformat()


def _lire(s: str | None) -> datetime | None:
    return None if s is None else datetime.fromisoformat(s)


def cle_hote(hote: str) -> str:
    """L'hôte tel que le dépôt le retient : sans schéma, sans « / » final, en
    minuscules. La découverte rend `http://10.10.1.1`, la saisie `10.10.1.1` :
    c'est le même équipement, et ses identifiants retenus doivent s'y retrouver."""
    h = hote.strip().rstrip("/")
    if "://" in h:
        h = h.split("://", 1)[1]
    return h.lower()


def _bool(v: int | None) -> bool | None:
    return None if v is None else bool(v)


def _en_releves(lignes) -> list[Releve]:
    return [
        Releve(
            ts=_lire(r["ts"]),
            joignable=bool(r["joignable"]),
            connecte=_bool(r["connecte"]),
            message=r["message"],
            anciennete_s=r["anciennete_s"],
            desactive=bool(r["desactive"]),
            anciennete_routeur_s=r["anciennete_routeur_s"],
            latence_ms=r["latence_ms"],
        )
        for r in lignes
    ]


class Depot:
    def __init__(self, chemin: str) -> None:
        self._chemin = chemin
        self._conn: aiosqlite.Connection | None = None

    async def connect(self) -> None:
        dossier = os.path.dirname(os.path.abspath(self._chemin))
        os.makedirs(dossier, exist_ok=True)
        self._conn = await aiosqlite.connect(self._chemin)
        self._conn.row_factory = aiosqlite.Row
        # WAL : un lecteur ne bloque pas l'écrivain.
        await self._conn.execute("PRAGMA journal_mode=WAL")
        await self._conn.executescript(_SCHEMA)
        # Les bases d'avant `cle_hote` gardent l'hôte avec son schéma : ramené
        # à la clé, sans écraser une entrée déjà à la bonne forme.
        async with self._conn.execute("SELECT hote, serie, modele FROM equipements") as cur:
            anciens = [r for r in await cur.fetchall() if cle_hote(r["hote"]) != r["hote"]]
        for r in anciens:
            await self._conn.execute(
                "INSERT OR IGNORE INTO equipements (hote, serie, modele) VALUES (?, ?, ?)",
                (cle_hote(r["hote"]), r["serie"], r["modele"]),
            )
            await self._conn.execute("DELETE FROM equipements WHERE hote = ?", (r["hote"],))
        await self._conn.commit()

    async def close(self) -> None:
        if self._conn is not None:
            await self._conn.close()
            self._conn = None

    @property
    def _db(self) -> aiosqlite.Connection:
        assert self._conn is not None, "Depot.connect() n'a pas été appelé"
        return self._conn

    async def observation_courante(
        self, serie: str, wan_id: str, maintenant: datetime
    ) -> int:
        async with self._db.execute(
            "SELECT o.id, o.debut, (SELECT MAX(ts) FROM releves WHERE observation = o.id)"
            " AS dernier FROM observations o"
            " WHERE o.serie = ? AND o.wan_id = ? AND o.fin IS NULL"
            " ORDER BY o.id DESC LIMIT 1",
            (serie, wan_id),
        ) as cur:
            ligne = await cur.fetchone()
        if ligne is not None:
            repere = _lire(ligne["dernier"] or ligne["debut"])
            if (maintenant - repere).total_seconds() < REPRISE_S:
                return ligne["id"]
        return await self.nouvelle_observation(serie, wan_id, maintenant)

    async def nouvelle_observation(
        self, serie: str, wan_id: str, maintenant: datetime
    ) -> int:
        await self._db.execute(
            "UPDATE observations SET fin = ? WHERE serie = ? AND wan_id = ? AND fin IS NULL",
            (_ecrire(maintenant), serie, wan_id),
        )
        cur = await self._db.execute(
            "INSERT INTO observations (serie, wan_id, debut) VALUES (?, ?, ?)",
            (serie, wan_id, _ecrire(maintenant)),
        )
        await self._db.commit()
        return cur.lastrowid

    async def ajouter_releve(self, observation: int, r: Releve) -> None:
        await self._db.execute(
            "INSERT INTO releves (observation, ts, joignable, connecte, message,"
            " anciennete_s, desactive, anciennete_routeur_s, latence_ms)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                observation, _ecrire(r.ts), int(r.joignable),
                None if r.connecte is None else int(r.connecte), r.message,
                r.anciennete_s, int(r.desactive), r.anciennete_routeur_s, r.latence_ms,
            ),
        )
        await self._db.commit()

    async def ajouter_faits(self, observation: int, faits: list[FaitRouteur]) -> None:
        for f in faits:
            # UNIQUE ne voit pas deux NULL comme égaux : le détail absent est
            # comparé à la main.
            await self._db.execute(
                "INSERT INTO faits (observation, ts, genre, detail)"
                " SELECT ?, ?, ?, ? WHERE NOT EXISTS ("
                " SELECT 1 FROM faits WHERE observation = ? AND ts = ? AND genre = ?"
                " AND detail IS ?)",
                (observation, _ecrire(f.ts), f.genre, f.detail,
                 observation, _ecrire(f.ts), f.genre, f.detail),
            )
        await self._db.commit()

    async def releves(
        self, observation: int, depuis: datetime | None = None
    ) -> list[Releve]:
        requete = "SELECT * FROM releves WHERE observation = ?"
        args: list = [observation]
        if depuis is not None:
            requete += " AND ts >= ?"
            args.append(_ecrire(depuis))
        requete += " ORDER BY ts, id"
        async with self._db.execute(requete, args) as cur:
            lignes = await cur.fetchall()
        # Des dizaines de milliers de lignes sur plusieurs jours : la conversion
        # se fait hors de la boucle d'événements, qui fait tourner la collecte.
        return await asyncio.to_thread(_en_releves, lignes)

    async def releves_apres(
        self, observation: int, apres_id: int = 0
    ) -> tuple[list[Releve], int]:
        """Les relevés rangés après la ligne `apres_id` (dans l'ordre de
        `releves`), et le plus grand identifiant vu. Les relevés ne sont
        qu'ajoutés : de quoi tenir une copie à jour sans tout relire."""
        async with self._db.execute(
            "SELECT * FROM releves WHERE observation = ? AND id > ? ORDER BY ts, id",
            (observation, apres_id),
        ) as cur:
            lignes = await cur.fetchall()
        dernier_id = max((r["id"] for r in lignes), default=apres_id)
        return await asyncio.to_thread(_en_releves, lignes), dernier_id

    async def dernier_hors_coupure(
        self, observation: int, avant: datetime
    ) -> datetime | None:
        """L'heure du dernier relevé avant `avant` qui ne montre pas le lien
        coupé (injoignable, désactivé, connecté) ; None s'il n'y en a pas.
        Lu à rebours par l'index (observation, ts) : il s'arrête au premier."""
        async with self._db.execute(
            "SELECT ts FROM releves WHERE observation = ? AND ts < ?"
            " AND NOT (joignable = 1 AND desactive = 0 AND connecte = 0)"
            " ORDER BY ts DESC LIMIT 1",
            (observation, _ecrire(avant)),
        ) as cur:
            ligne = await cur.fetchone()
        return None if ligne is None else _lire(ligne["ts"])

    async def faits(self, observation: int) -> list[FaitRouteur]:
        async with self._db.execute(
            "SELECT ts, genre, detail FROM faits WHERE observation = ? ORDER BY ts, id",
            (observation,),
        ) as cur:
            lignes = await cur.fetchall()
        return [FaitRouteur(_lire(r["ts"]), r["genre"], r["detail"]) for r in lignes]

    async def observations(self) -> list[dict]:
        async with self._db.execute(
            "SELECT o.id, o.wan_id, o.debut,"
            " (SELECT e.modele FROM equipements e WHERE e.serie = o.serie"
            "  ORDER BY e.modele IS NULL, e.hote LIMIT 1) AS modele,"
            " (SELECT MAX(ts) FROM releves WHERE observation = o.id) AS dernier"
            " FROM observations o ORDER BY o.debut DESC, o.id DESC"
        ) as cur:
            lignes = await cur.fetchall()
        return [
            {
                "id": r["id"],
                "modele": r["modele"],
                "wan_id": r["wan_id"],
                "debut": _lire(r["debut"]),
                "dernier": _lire(r["dernier"]),
            }
            for r in lignes
        ]

    async def memoriser_equipement(
        self, hote: str, serie: str, modele: str | None
    ) -> None:
        await self._db.execute(
            "INSERT INTO equipements (hote, serie, modele) VALUES (?, ?, ?)"
            " ON CONFLICT(hote) DO UPDATE SET serie = excluded.serie,"
            " modele = excluded.modele",
            (cle_hote(hote), serie, modele),
        )
        await self._db.commit()

    async def serie_de(self, hote: str) -> str | None:
        async with self._db.execute(
            "SELECT serie FROM equipements WHERE hote = ?", (cle_hote(hote),)
        ) as cur:
            ligne = await cur.fetchone()
        return None if ligne is None else ligne["serie"]

    async def memoriser_lien(self, serie: str, wan_id: str) -> None:
        """Le lien choisi pour cet équipement, proposé à la connexion suivante."""
        await self._db.execute(
            "INSERT INTO liens_retenus (serie, wan_id) VALUES (?, ?)"
            " ON CONFLICT(serie) DO UPDATE SET wan_id = excluded.wan_id",
            (serie, wan_id),
        )
        await self._db.commit()

    async def lien_de(self, serie: str) -> str | None:
        async with self._db.execute(
            "SELECT wan_id FROM liens_retenus WHERE serie = ?", (serie,)
        ) as cur:
            ligne = await cur.fetchone()
        return None if ligne is None else ligne["wan_id"]
