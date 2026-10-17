"""Stockage du témoin Ethernet : observations, relevés et faits du routeur.

SQLite en WAL, comme le superviseur. Les dates sont écrites en ISO-8601 UTC et
relues en datetimes avec fuseau. La série d'un équipement reste dans cette base :
elle ne sort jamais par `observations()`.
"""

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
"""


def _ecrire(d: datetime) -> str:
    if d.tzinfo is None:
        d = d.replace(tzinfo=timezone.utc)
    return d.astimezone(timezone.utc).isoformat()


def _lire(s: str | None) -> datetime | None:
    return None if s is None else datetime.fromisoformat(s)


def _bool(v: int | None) -> bool | None:
    return None if v is None else bool(v)


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
            (hote, serie, modele),
        )
        await self._db.commit()

    async def serie_de(self, hote: str) -> str | None:
        async with self._db.execute(
            "SELECT serie FROM equipements WHERE hote = ?", (hote,)
        ) as cur:
            ligne = await cur.fetchone()
        return None if ligne is None else ligne["serie"]
