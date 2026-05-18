"""
agent.embed_worker — worker async d'indexation sémantique en arrière-plan.

Tourne en boucle dans la même event loop que FastAPI. À chaque tick :

1. Scanne `messages`, `agent_insights`, `memory_doc` pour les enregistrements
   qui n'ont PAS encore d'embedding dans `embed_chunks`.
2. Batch jusqu'à `_BATCH` items, appelle `vector_store.embed_batch()` (un seul
   round-trip SSPCloud), insère dans `embed_chunks` + `vec_chunks`.
3. Dort `_IDLE_SEC` si rien à faire.

Pas de queue, pas de threads : on s'appuie sur le schéma SQL existant et la
table `embed_chunks` comme journal idémpotent. (source_type, source_id) =
clef logique unique.

Lancé par `main.py` au startup, arrêté au shutdown.
"""

from __future__ import annotations
import asyncio
import json
import logging
import os
import struct
import time
from pathlib import Path

import aiosqlite
import sqlite_vec

from agent import vector_store as vs

log = logging.getLogger("agent.embed_worker")

_DATA_DIR = Path(os.getenv("DATA_DIR", "/data/agent"))
_DB_PATH  = _DATA_DIR / "memory.db"
_BATCH    = int(os.getenv("EMBED_BATCH", "8"))
_IDLE_SEC = int(os.getenv("EMBED_IDLE_SEC", "30"))
_MIN_LEN  = 12  # on ignore les messages trop courts ("ok", "merci", "go")


# ── Lecture des sources non-indexées ────────────────────────────────────────

async def _fetch_pending(db: aiosqlite.Connection, limit: int) -> list[dict]:
    """Renvoie jusqu'à `limit` items non-indexés, toutes sources confondues."""
    items: list[dict] = []

    # 1) messages user/assistant non-vides
    q_msgs = """
        SELECT m.id, m.session_id, m.role, m.content, s.username
        FROM messages m
        LEFT JOIN sessions s ON s.id = m.session_id
        LEFT JOIN embed_chunks c
            ON c.source_type = 'message' AND c.source_id = CAST(m.id AS TEXT)
        WHERE c.id IS NULL
          AND m.role IN ('user', 'assistant')
          AND LENGTH(m.content) >= ?
        ORDER BY m.id ASC
        LIMIT ?
    """
    async with db.execute(q_msgs, (_MIN_LEN, limit)) as cur:
        async for r in cur:
            items.append({
                "source_type": "message",
                "source_id": str(r[0]),
                "text": r[3],
                "username": r[4] or "user",
                "metadata": {"session_id": r[1], "role": r[2]},
            })
    if len(items) >= limit:
        return items

    # 2) agent_insights (extraits par étape D, mais on en supporte déjà le flux)
    q_ins = """
        SELECT i.id, i.username, i.key, i.value, i.confidence
        FROM agent_insights i
        LEFT JOIN embed_chunks c
            ON c.source_type = 'insight' AND c.source_id = CAST(i.id AS TEXT)
        WHERE c.id IS NULL
        ORDER BY i.id ASC
        LIMIT ?
    """
    remaining = limit - len(items)
    async with db.execute(q_ins, (remaining,)) as cur:
        async for r in cur:
            items.append({
                "source_type": "insight",
                "source_id": str(r[0]),
                "text": f"{r[2]}: {r[3]}",
                "username": r[1],
                "metadata": {"key": r[2], "confidence": r[4]},
            })
    if len(items) >= limit:
        return items

    # 3) memory_doc : on indexe section par section pour granularité search
    q_md = """
        SELECT username, sections, updated_at
        FROM memory_doc md
        WHERE NOT EXISTS (
            SELECT 1 FROM embed_chunks c
            WHERE c.source_type = 'memory_doc'
              AND c.username = md.username
              AND c.created_at >= md.updated_at
        )
    """
    remaining = limit - len(items)
    async with db.execute(q_md) as cur:
        async for r in cur:
            if remaining <= 0:
                break
            try:
                sections = json.loads(r[1])
            except Exception:
                continue
            if not isinstance(sections, dict):
                continue
            for section_name, section_text in sections.items():
                if not section_text or len(section_text) < _MIN_LEN:
                    continue
                items.append({
                    "source_type": "memory_doc",
                    "source_id": f"{r[0]}::{section_name}",
                    "text": section_text,
                    "username": r[0],
                    "metadata": {"section": section_name},
                })
                remaining -= 1
                if remaining <= 0:
                    break

    return items


# ── Insertion batch ──────────────────────────────────────────────────────

async def _insert_batch(db: aiosqlite.Connection, items: list[dict],
                        vectors: list[list[float]]) -> None:
    """Insère items + vecteurs en une seule transaction."""
    now = int(time.time())
    for item, vec in zip(items, vectors):
        blob = struct.pack(f"{vs.EMBED_DIM}f", *vec)
        cur = await db.execute(
            "INSERT INTO embed_chunks (text, source_type, source_id, username, created_at, metadata) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (
                item["text"][:8000],  # cap raisonnable, on n'archive pas tout
                item["source_type"],
                item["source_id"],
                item["username"],
                now,
                json.dumps(item["metadata"]) if item.get("metadata") else None,
            ),
        )
        rowid = cur.lastrowid
        await db.execute(
            "INSERT INTO vec_chunks(rowid, embedding) VALUES (?, ?)",
            (rowid, blob),
        )
    await db.commit()


# ── Boucle principale ───────────────────────────────────────────────

async def _tick() -> int:
    """Un cycle : fetch pending, embed batch, insert. Renvoie le count traité."""
    async with aiosqlite.connect(_DB_PATH) as db:
        await db.enable_load_extension(True)
        await db.execute(f"SELECT load_extension('{sqlite_vec.loadable_path()}')")

        items = await _fetch_pending(db, _BATCH)
        if not items:
            return 0

        texts = [it["text"] for it in items]
        try:
            vectors = await vs.embed_batch(texts)
        except Exception as e:
            log.warning("embed_batch fail (%d items): %s", len(items), e)
            return 0
        if len(vectors) != len(items):
            log.warning("embed_batch size mismatch: %d items → %d vecs",
                        len(items), len(vectors))
            return 0

        await _insert_batch(db, items, vectors)
        log.info("indexed %d chunks (sources: %s)", len(items),
                 ",".join(sorted({it["source_type"] for it in items})))
        return len(items)


async def run_forever(stop_event: asyncio.Event | None = None) -> None:
    """Boucle infinie : tick puis dort, jusqu'au stop_event."""
    log.info("embed_worker démarré (batch=%d, idle=%ds, db=%s)",
             _BATCH, _IDLE_SEC, _DB_PATH)
    stop_event = stop_event or asyncio.Event()
    while not stop_event.is_set():
        try:
            n = await _tick()
        except Exception as e:
            log.exception("embed_worker tick fail: %s", e)
            n = 0
        # Si on a travaillé à plein, on enchaîne direct (rattrapage initial).
        # Sinon idle pour laisser la main à l'event loop.
        delay = 0.1 if n >= _BATCH else _IDLE_SEC
        try:
            await asyncio.wait_for(stop_event.wait(), timeout=delay)
        except asyncio.TimeoutError:
            pass
    log.info("embed_worker arrêté")


# ── API standalone (debug) ─────────────────────────────────────────

async def run_once() -> int:
    """Un seul tick (utile pour tests / CLI)."""
    return await _tick()


async def stats() -> dict:
    """Compteurs : pending vs indexed, par source_type."""
    async with aiosqlite.connect(_DB_PATH) as db:
        out: dict = {"indexed": {}, "pending": {}}
        for st in ("message", "insight", "memory_doc"):
            cur = await db.execute(
                "SELECT COUNT(*) FROM embed_chunks WHERE source_type = ?", (st,),
            )
            out["indexed"][st] = (await cur.fetchone())[0]

        # Pending messages
        cur = await db.execute("""
            SELECT COUNT(*) FROM messages m
            LEFT JOIN embed_chunks c ON c.source_type='message' AND c.source_id=CAST(m.id AS TEXT)
            WHERE c.id IS NULL AND m.role IN ('user','assistant') AND LENGTH(m.content) >= ?
        """, (_MIN_LEN,))
        out["pending"]["message"] = (await cur.fetchone())[0]

        # Pending insights
        cur = await db.execute("""
            SELECT COUNT(*) FROM agent_insights i
            LEFT JOIN embed_chunks c ON c.source_type='insight' AND c.source_id=CAST(i.id AS TEXT)
            WHERE c.id IS NULL
        """)
        out["pending"]["insight"] = (await cur.fetchone())[0]

        return out
