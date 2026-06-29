"""
hub.components — CRUD `components_index` + helpers pod-code PVC.

Sprint Composants Phase 2 (2026-06-25). Pattern recipes_index V1.5
(INSERT-only audit trail, versioning SHA, lookup latest via MAX(version_num)).

Storage :
- DB index : `components_index` table dans `studies.db` (créée Phase 0)
- Manifest JSON : `/data/studies/{sid}/components/{cid}/manifest.json` sur PVC
                  workspace user (via execute_python tool BigQgisMCP)

Audit trail :
- INSERT-only (jamais UPDATE) : chaque édition crée une nouvelle row
- content_hash SHA256 canonique du manifest sérialisé
- previous_hash chaînage version N → N-1
- UNIQUE(cid, version_num) — race-safe (créé Phase 0)

Capitalisé : `~/.wikichat/knowledge/qgis-sspcloud-composants-axis.md` §3
"""

from __future__ import annotations

import hashlib
import json
import time
from typing import Any

import aiosqlite

import hub.studies as studies
from hub.models import Component

_DB_PATH = studies._DB_PATH


# ── CRUD components_index ─────────────────────────────────────────────────────

async def insert_component(
    component: Component,
    owner: str,
    sid: str,
    file_path: str = "",
    size_bytes: int = 0,
    previous_hash: str = "",
) -> int:
    """Insert nouvelle row components_index. INSERT-only (pas d'UPDATE).

    Le content_hash est calculé sur le dump JSON canonique de Component
    (sort_keys=True), incluant id mais excluant version (= rotated each
    insert) et provenance.created_at (= timestamp variable).

    Args:
        component: instance Pydantic validée
        owner: username CEREMA OIDC
        sid: étude.id (12 hex)
        file_path: chemin sur PVC du manifest.json
        size_bytes: taille fichier
        previous_hash: hash de la version N-1 (chaînage)

    Returns:
        rowid de la row insérée (0 si erreur)
    """
    # Hash canonical du contenu (exclus champs volatiles)
    canonical = json.dumps(
        component.model_dump(
            mode="json",
            exclude={"version", "provenance"},  # volatiles
        ),
        sort_keys=True, separators=(",", ":"), ensure_ascii=True,
    )
    content_hash = "sha256:" + hashlib.sha256(canonical.encode()).hexdigest()

    provenance_json = json.dumps(
        component.provenance.model_dump(mode="json"),
        ensure_ascii=False,
    )

    async with aiosqlite.connect(_DB_PATH) as db:
        cur = await db.execute(
            """INSERT INTO components_index
               (cid, sid, owner, kind, title, content_hash, previous_hash,
                version_num, component_version, classification,
                provenance_json, file_path, size_bytes, status, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'active', ?)""",
            (
                component.id, sid, owner, component.kind, component.title,
                content_hash, previous_hash or None,
                component.version, component.component_version,
                component.classification, provenance_json,
                file_path, size_bytes, int(time.time()),
            ),
        )
        await db.commit()
        return cur.lastrowid or 0


async def list_components(
    sid: str | None = None,
    kind: str | None = None,
    owner: str | None = None,
    status: str = "active",
    classification: str | None = None,
    limit: int = 100,
    offset: int = 0,
) -> list[dict[str, Any]]:
    """Liste les composants. Si sid donné, scope étude. Sinon scope user.

    Vague E1 D-QGIS-009 (2026-06-29) : ajout filtres `classification` et
    `offset` pour endpoint /catalog/components cross-étude.
    """
    wheres = ["status = ?"]
    params: list[Any] = [status]
    if sid:
        wheres.append("sid = ?")
        params.append(sid)
    if kind:
        wheres.append("kind = ?")
        params.append(kind)
    if owner:
        wheres.append("owner = ?")
        params.append(owner)
    if classification:
        wheres.append("classification = ?")
        params.append(classification)

    sql = (
        f"SELECT * FROM components_index "
        f"WHERE {' AND '.join(wheres)} "
        f"AND (cid, version_num) IN ("
        f"  SELECT cid, MAX(version_num) FROM components_index "
        f"  WHERE status = 'active' GROUP BY cid"
        f") "
        f"ORDER BY created_at DESC LIMIT ? OFFSET ?"
    )
    params.append(limit)
    params.append(offset)

    async with aiosqlite.connect(_DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cur = await db.execute(sql, params)
        rows = await cur.fetchall()
        return [dict(r) for r in rows]


async def get_component_latest(cid: str) -> dict[str, Any] | None:
    """Latest version d'un composant par cid (lookup MAX(version_num))."""
    async with aiosqlite.connect(_DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cur = await db.execute(
            """SELECT * FROM components_index
               WHERE cid = ? AND status = 'active'
               ORDER BY version_num DESC LIMIT 1""",
            (cid,),
        )
        row = await cur.fetchone()
        return dict(row) if row else None


async def get_component_history(cid: str) -> list[dict[str, Any]]:
    """Toutes les versions d'un composant (audit trail INSERT-only)."""
    async with aiosqlite.connect(_DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cur = await db.execute(
            """SELECT * FROM components_index
               WHERE cid = ? ORDER BY version_num DESC""",
            (cid,),
        )
        rows = await cur.fetchall()
        return [dict(r) for r in rows]


async def archive_component(cid: str, owner: str) -> int:
    """Soft delete : status='archived'. INSERT nouvelle row archived
    (préserve les rows précédentes pour audit)."""
    latest = await get_component_latest(cid)
    if not latest or latest["owner"] != owner:
        return 0
    async with aiosqlite.connect(_DB_PATH) as db:
        cur = await db.execute(
            """INSERT INTO components_index
               (cid, sid, owner, kind, title, content_hash, previous_hash,
                version_num, component_version, classification,
                provenance_json, file_path, size_bytes, status, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'archived', ?)""",
            (
                latest["cid"], latest["sid"], owner,
                latest["kind"], latest["title"],
                latest["content_hash"], latest["content_hash"],  # self ref pour archived
                latest["version_num"] + 1,
                latest["component_version"], latest["classification"],
                latest["provenance_json"], latest["file_path"],
                latest["size_bytes"], int(time.time()),
            ),
        )
        await db.commit()
        return cur.lastrowid or 0


# ── Pod-code helpers (PVC manifest.json) ──────────────────────────────────────

def component_manifest_path(sid: str, cid: str) -> str:
    """Chemin canonique du manifest.json sur PVC workspace."""
    return f"/data/studies/{sid}/components/{cid}/manifest.json"


def write_component_manifest_pod_code(
    sid: str, cid: str, content_json: str
) -> str:
    """Génère du Python execute_python pour écrire le manifest sur PVC."""
    path = component_manifest_path(sid, cid)
    return f"""
import json, hashlib
from pathlib import Path
p = Path({path!r})
p.parent.mkdir(parents=True, exist_ok=True)
content = {content_json!r}
p.write_text(content, encoding='utf-8')
size = len(content.encode('utf-8'))
h = hashlib.sha256(content.encode('utf-8')).hexdigest()
print(f'COMPONENT_WRITE_OK path={{p}} size={{size}} hash=sha256:{{h}}')
"""


def read_component_manifest_pod_code(sid: str, cid: str) -> str:
    """Génère du Python execute_python pour lire le manifest depuis PVC."""
    path = component_manifest_path(sid, cid)
    return f"""
import base64
from pathlib import Path
p = Path({path!r})
if not p.exists():
    print('COMPONENT_NOT_FOUND')
else:
    data = p.read_bytes()
    b64 = base64.b64encode(data).decode()
    print(f'COMPONENT_READ_OK b64={{b64}}')
"""
