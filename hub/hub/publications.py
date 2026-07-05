"""
hub.publications — F17 Versioning URL stable + banniere obsolescence.

Sprint 1.4 Vague 1 Equipe B (2026-07-05). Ajoute une strate de versioning
persistant au-dessus de assemblies_index :

- Table `publications` INSERT-only : (slug, version, aid, aid_version_num,
  published_at, superseded_by, change_summary, sid, owner)
- Une row par version publiee -> `latest` = version max WHERE superseded_by IS NULL
- Quand une nouvelle version publie sur le meme slug : on met a jour
  `superseded_by = new_version` sur toutes les rows precedentes (banniere
  obsolescence declenchee cote template).

Routes exposees :
- GET /p/{slug}          -> redirige HTTP 302 vers la latest version (URL vivante)
- GET /p/{slug}/v{n}     -> rend la version immutable N (jamais modifiee ; peut
                            afficher une banniere si superseded_by NOT NULL).

Design : slug est unique par owner (pattern `assembly-{aid}` par defaut, mais
extensible en future via change_summary). La table est independante de
`assemblies_index` pour permettre versionner d'autres kinds (component, pdf,
bundle) sans casser le schema existant.
"""

from __future__ import annotations

import time
from typing import Any

import aiosqlite

import hub.studies as studies

_DB_PATH = studies._DB_PATH


# ── DB init (idempotent) ──────────────────────────────────────────────────────

async def init_db() -> None:
    """Cree la table publications si absente. Idempotent."""
    async with aiosqlite.connect(_DB_PATH) as db:
        await db.execute("""
            CREATE TABLE IF NOT EXISTS publications (
                rowid           INTEGER PRIMARY KEY AUTOINCREMENT,
                slug            TEXT NOT NULL,
                version         INTEGER NOT NULL,
                aid             TEXT NOT NULL,
                aid_version_num INTEGER NOT NULL,
                sid             TEXT NOT NULL,
                owner           TEXT NOT NULL,
                kind            TEXT NOT NULL DEFAULT 'assembly',
                published_at    INTEGER NOT NULL,
                superseded_by   INTEGER,
                change_summary  TEXT,
                integrity_hash  TEXT,
                UNIQUE(slug, version)
            )
        """)
        await db.execute("""
            CREATE INDEX IF NOT EXISTS idx_publications_slug
                ON publications(slug, version DESC)
        """)
        await db.execute("""
            CREATE INDEX IF NOT EXISTS idx_publications_owner
                ON publications(owner, published_at DESC)
        """)
        await db.commit()


# ── Insert / query ────────────────────────────────────────────────────────────

async def register_publication(
    slug: str,
    aid: str,
    aid_version_num: int,
    sid: str,
    owner: str,
    kind: str = "assembly",
    change_summary: str | None = None,
    integrity_hash: str | None = None,
) -> dict[str, Any]:
    """Enregistre une nouvelle version publiee du slug.

    - Incremente version = MAX(version)+1 sur le slug (1 si premiere fois).
    - Marque toutes les rows precedentes du meme slug avec
      `superseded_by = new_version` (banniere obsolescence).
    - INSERT-only : jamais d'UPDATE sur la row nouvellement creee.
    """
    async with aiosqlite.connect(_DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        # 1. Determine next version num pour ce slug
        cur = await db.execute(
            "SELECT COALESCE(MAX(version), 0) FROM publications WHERE slug = ?",
            (slug,),
        )
        row = await cur.fetchone()
        next_version = (row[0] if row else 0) + 1

        # 2. Marque previous rows superseded_by = next_version
        if next_version > 1:
            await db.execute(
                "UPDATE publications SET superseded_by = ? "
                "WHERE slug = ? AND superseded_by IS NULL",
                (next_version, slug),
            )

        # 3. Insert new row
        now = int(time.time())
        await db.execute(
            """INSERT INTO publications
               (slug, version, aid, aid_version_num, sid, owner, kind,
                published_at, superseded_by, change_summary, integrity_hash)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, NULL, ?, ?)""",
            (
                slug, next_version, aid, aid_version_num, sid, owner, kind,
                now, change_summary, integrity_hash,
            ),
        )
        await db.commit()

        return {
            "slug": slug,
            "version": next_version,
            "aid": aid,
            "aid_version_num": aid_version_num,
            "sid": sid,
            "owner": owner,
            "kind": kind,
            "published_at": now,
            "superseded_by": None,
            "change_summary": change_summary,
            "integrity_hash": integrity_hash,
        }


async def get_latest(slug: str) -> dict[str, Any] | None:
    """Latest version d'un slug (celle avec superseded_by IS NULL)."""
    async with aiosqlite.connect(_DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cur = await db.execute(
            """SELECT * FROM publications
               WHERE slug = ? AND superseded_by IS NULL
               ORDER BY version DESC LIMIT 1""",
            (slug,),
        )
        row = await cur.fetchone()
        return dict(row) if row else None


async def get_version(slug: str, version: int) -> dict[str, Any] | None:
    """Version immutable N d'un slug (peut etre superseded)."""
    async with aiosqlite.connect(_DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cur = await db.execute(
            "SELECT * FROM publications WHERE slug = ? AND version = ?",
            (slug, version),
        )
        row = await cur.fetchone()
        return dict(row) if row else None


async def list_versions(slug: str) -> list[dict[str, Any]]:
    """Historique complet d'un slug (toutes les versions)."""
    async with aiosqlite.connect(_DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cur = await db.execute(
            "SELECT * FROM publications WHERE slug = ? ORDER BY version DESC",
            (slug,),
        )
        rows = await cur.fetchall()
        return [dict(r) for r in rows]


async def list_by_owner(owner: str, limit: int = 100) -> list[dict[str, Any]]:
    """Toutes les publications d'un owner (latest par slug)."""
    async with aiosqlite.connect(_DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cur = await db.execute(
            """SELECT * FROM publications
               WHERE owner = ? AND superseded_by IS NULL
               ORDER BY published_at DESC LIMIT ?""",
            (owner, limit),
        )
        rows = await cur.fetchall()
        return [dict(r) for r in rows]
