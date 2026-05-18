"""
agent.vector_store — fondation Phase 9 étape B.

Wrapper minimal autour de sqlite-vec et de l'API embeddings SSPCloud.
Étend la DB SQLite EXISTANTE (`memory.db`) avec 2 tables :

- `embed_chunks` (table classique) : texte + métadonnées
- `vec_chunks` (virtual table vec0) : vecteurs 1024-dim

Stratégie : Qwen3-embedding-8b natif = 4096 dims, on tronque à 1024 (MRL
Matryoshka) puis L2-normalize. 4× moins de stockage, ~98% des perfs (SOTA
mai 2025-2026).

API exposée :
    await init_vector_store(db_path)
    vec = await embed("texte à indexer")
    await add(text, source_type, source_id, username=None, metadata=None)
    results = await search("requête", top_k=5, source_type=None)
    await purge(source_type=None, before_ts=None)

Isolé derrière ce module → si sqlite-vec doit migrer, on remplace ici.
"""

from __future__ import annotations
import os
import json
import math
import time
import logging
from pathlib import Path

import aiosqlite
import sqlite_vec
import httpx

log = logging.getLogger("agent.vector_store")

# ── Config ────────────────────────────────────────────────────────────────────
_LLM_BASE_URL = os.getenv("LLM_BASE_URL", "https://llm.lab.sspcloud.fr/api")
_LLM_API_KEY  = os.getenv("LLM_API_KEY", "")
_EMBED_MODEL  = os.getenv("EMBED_MODEL", "qwen3-embedding-8b")

# Matryoshka : qwen3-embedding-8b natif 4096 dims, on tronque à 1024.
# 4× moins de stockage SQLite, ~98% perfs MTEB (Vespa benchmark 2025).
EMBED_DIM = 1024

_DATA_DIR = Path(os.getenv("DATA_DIR", "/data/agent"))
_DB_PATH  = _DATA_DIR / "memory.db"


# ── Init ──────────────────────────────────────────────────────────────────────

async def init_vector_store() -> None:
    """Charge l'extension sqlite-vec, crée les 2 tables nécessaires. Idempotent."""
    async with aiosqlite.connect(_DB_PATH) as db:
        await db.enable_load_extension(True)
        # On charge l'extension via SQL — aiosqlite marshalle vers le bon thread.
        # `sqlite_vec.load()` attend une sqlite3.Connection synchrone, inutilisable ici.
        await db.execute(f"SELECT load_extension('{sqlite_vec.loadable_path()}')")

        # Table de métadonnées (texte + tags + JSON)
        await db.execute("""
            CREATE TABLE IF NOT EXISTS embed_chunks (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                text        TEXT NOT NULL,
                source_type TEXT NOT NULL,
                source_id   TEXT,
                username    TEXT,
                created_at  INTEGER NOT NULL,
                metadata    TEXT
            )
        """)
        await db.execute(
            "CREATE INDEX IF NOT EXISTS idx_embed_source ON embed_chunks(source_type, source_id)"
        )
        await db.execute(
            "CREATE INDEX IF NOT EXISTS idx_embed_user ON embed_chunks(username)"
        )

        # Virtual table vec0 pour la recherche par similarité
        await db.execute(f"""
            CREATE VIRTUAL TABLE IF NOT EXISTS vec_chunks USING vec0(
                embedding float[{EMBED_DIM}]
            )
        """)
        await db.commit()
    log.info("vector_store initialisé (dim=%d, db=%s)", EMBED_DIM, _DB_PATH)


# ── Embedding via SSPCloud ────────────────────────────────────────────────────

async def embed(text: str) -> list[float]:
    """Appel API embeddings SSPCloud, tronque MRL 1024 dims + L2-normalize."""
    if not text or not text.strip():
        # Vecteur nul valide (toutes recherches seront éloignées de lui)
        return [0.0] * EMBED_DIM

    async with httpx.AsyncClient(timeout=30) as cli:
        r = await cli.post(
            f"{_LLM_BASE_URL}/embeddings",
            headers={
                "Authorization": f"Bearer {_LLM_API_KEY}",
                "Content-Type": "application/json",
            },
            json={"model": _EMBED_MODEL, "input": text},
        )
        r.raise_for_status()
        data = r.json()

    vec = data["data"][0]["embedding"]
    # Truncate Matryoshka
    vec = vec[:EMBED_DIM]
    # L2 normalize (essentiel pour cosine via distance euclidienne)
    norm = math.sqrt(sum(x * x for x in vec))
    if norm > 0:
        vec = [x / norm for x in vec]
    return vec


async def embed_batch(texts: list[str]) -> list[list[float]]:
    """Embed plusieurs textes en un seul appel API (économie réseau)."""
    if not texts:
        return []
    async with httpx.AsyncClient(timeout=60) as cli:
        r = await cli.post(
            f"{_LLM_BASE_URL}/embeddings",
            headers={
                "Authorization": f"Bearer {_LLM_API_KEY}",
                "Content-Type": "application/json",
            },
            json={"model": _EMBED_MODEL, "input": texts},
        )
        r.raise_for_status()
        data = r.json()
    results = []
    for item in data["data"]:
        v = item["embedding"][:EMBED_DIM]
        norm = math.sqrt(sum(x * x for x in v))
        if norm > 0:
            v = [x / norm for x in v]
        results.append(v)
    return results


# ── Insert ────────────────────────────────────────────────────────────────────

async def add(
    text: str,
    source_type: str,
    source_id: str | None = None,
    username: str | None = None,
    metadata: dict | None = None,
) -> int:
    """Embed + insère dans embed_chunks + vec_chunks. Retourne le rowid."""
    if not text or not text.strip():
        return -1
    vec = await embed(text)
    return await _add_with_vec(text, vec, source_type, source_id, username, metadata)


async def _add_with_vec(
    text: str, vec: list[float],
    source_type: str, source_id: str | None,
    username: str | None, metadata: dict | None,
) -> int:
    import struct
    blob = struct.pack(f"{EMBED_DIM}f", *vec)
    async with aiosqlite.connect(_DB_PATH) as db:
        await db.enable_load_extension(True)
        await db.execute(f"SELECT load_extension('{sqlite_vec.loadable_path()}')")
        cur = await db.execute(
            "INSERT INTO embed_chunks (text, source_type, source_id, username, created_at, metadata) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (text, source_type, source_id, username, int(time.time()),
             json.dumps(metadata) if metadata else None),
        )
        rowid = cur.lastrowid
        await db.execute(
            "INSERT INTO vec_chunks(rowid, embedding) VALUES (?, ?)",
            (rowid, blob),
        )
        await db.commit()
    return rowid


# ── Search ────────────────────────────────────────────────────────────────────

async def search(
    query: str, top_k: int = 5,
    source_type: str | None = None,
    username: str | None = None,
) -> list[dict]:
    """Recherche les top-k chunks les plus proches sémantiquement.

    Filtres optionnels : source_type (message/insight/recipe), username.
    """
    import struct
    qvec = await embed(query)
    qblob = struct.pack(f"{EMBED_DIM}f", *qvec)

    async with aiosqlite.connect(_DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        await db.enable_load_extension(True)
        await db.execute(f"SELECT load_extension('{sqlite_vec.loadable_path()}')")

        # Filtres SQL dynamiques
        where = []
        params: list = [qblob, top_k]
        if source_type:
            where.append("c.source_type = ?")
            params.append(source_type)
        if username:
            where.append("c.username = ?")
            params.append(username)
        where_sql = (" AND " + " AND ".join(where)) if where else ""

        sql = f"""
            SELECT c.id, c.text, c.source_type, c.source_id, c.username,
                   c.metadata, c.created_at, v.distance
            FROM vec_chunks v
            JOIN embed_chunks c ON c.id = v.rowid
            WHERE v.embedding MATCH ? AND k = ?
            {where_sql}
            ORDER BY v.distance ASC
        """
        cur = await db.execute(sql, params)
        rows = await cur.fetchall()

    results = []
    for r in rows:
        m = json.loads(r["metadata"]) if r["metadata"] else None
        results.append({
            "id":          r["id"],
            "text":        r["text"],
            "source_type": r["source_type"],
            "source_id":   r["source_id"],
            "username":    r["username"],
            "metadata":    m,
            "created_at":  r["created_at"],
            "distance":    r["distance"],
            # Similarité cosine = 1 - (distance²/2) pour vecteurs normalisés
            "similarity":  max(0.0, 1.0 - (r["distance"] ** 2) / 2.0),
        })
    return results


# ── Maintenance ───────────────────────────────────────────────────────────────

async def purge(source_type: str | None = None, before_ts: int | None = None) -> int:
    """Supprime des chunks. Retourne le count supprimé."""
    async with aiosqlite.connect(_DB_PATH) as db:
        await db.enable_load_extension(True)
        await db.execute(f"SELECT load_extension('{sqlite_vec.loadable_path()}')")

        conditions = []
        params: list = []
        if source_type:
            conditions.append("source_type = ?")
            params.append(source_type)
        if before_ts:
            conditions.append("created_at < ?")
            params.append(before_ts)
        if not conditions:
            return 0  # safety : pas de purge totale par défaut
        where_sql = " AND ".join(conditions)

        # Récupère les ids à supprimer
        cur = await db.execute(
            f"SELECT id FROM embed_chunks WHERE {where_sql}", params,
        )
        ids = [r[0] for r in await cur.fetchall()]
        if not ids:
            return 0

        placeholders = ",".join("?" * len(ids))
        await db.execute(f"DELETE FROM embed_chunks WHERE id IN ({placeholders})", ids)
        await db.execute(f"DELETE FROM vec_chunks WHERE rowid IN ({placeholders})", ids)
        await db.commit()
    return len(ids)


async def count(source_type: str | None = None) -> int:
    """Compte les chunks indexés. Utile pour observabilité."""
    async with aiosqlite.connect(_DB_PATH) as db:
        if source_type:
            cur = await db.execute(
                "SELECT COUNT(*) FROM embed_chunks WHERE source_type = ?",
                (source_type,),
            )
        else:
            cur = await db.execute("SELECT COUNT(*) FROM embed_chunks")
        row = await cur.fetchone()
        return row[0] if row else 0
