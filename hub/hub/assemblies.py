"""
hub.assemblies — CRUD `assemblies_index` + helpers pod-code + audit_chain calc.

Sprint Composants Phase 3 (2026-06-25). Pattern recipes_index/components_index
(INSERT-only audit trail, lookup latest via MAX(version_num)).

Storage :
- DB index : `assemblies_index` table dans `studies.db` (créée Phase 0)
- Manifest JSON : `/data/studies/{sid}/assemblies/{aid}/manifest.json` sur PVC
- Rendered HTML : `/data/studies/{sid}/assemblies/{aid}/rendered/index.html`
- Publication S3 : via `s3_publication.publish(owner, 'assembly', slug, ...)`

audit_chain : OBLIGATOIRE au publish. Snapshot signed_hash SHA256 du
chain canonique. Lien direct vers Scene Manifests + composants + recipes
ayant contribué.

Capitalisé : `~/.wikichat/knowledge/qgis-sspcloud-composants-axis.md` §4
"""

from __future__ import annotations

import hashlib
import json
import time
from datetime import datetime
from typing import Any

import aiosqlite

import hub.studies as studies
from hub.models import Assembly, AuditChain

_DB_PATH = studies._DB_PATH


# ── CRUD assemblies_index ─────────────────────────────────────────────────────

async def insert_assembly(
    assembly: Assembly,
    owner: str,
    file_path: str = "",
    rendered_path: str = "",
    audit_chain_json: str = "",
    previous_hash: str = "",
) -> int:
    """Insert nouvelle row assemblies_index. INSERT-only (pas d'UPDATE)."""
    canonical = json.dumps(
        assembly.model_dump(
            mode="json",
            exclude={"version", "provenance", "audit_chain"},  # volatiles
        ),
        sort_keys=True, separators=(",", ":"), ensure_ascii=True,
    )
    content_hash = "sha256:" + hashlib.sha256(canonical.encode()).hexdigest()

    provenance_json = json.dumps(
        assembly.provenance.model_dump(mode="json"), ensure_ascii=False,
    )

    async with aiosqlite.connect(_DB_PATH) as db:
        cur = await db.execute(
            """INSERT INTO assemblies_index
               (aid, sid, owner, kind, title, content_hash, previous_hash,
                version_num, assembly_version, classification,
                audit_chain_json, provenance_json, rendered_path,
                published_url, published_at, status, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'active', ?)""",
            (
                assembly.id, assembly.sid, owner,
                assembly.kind, assembly.title,
                content_hash, previous_hash or None,
                assembly.version, assembly.assembly_version,
                assembly.audience,
                audit_chain_json or None, provenance_json,
                rendered_path or None,
                None, None,  # published_url + published_at = remplis au publish
                int(time.time()),
            ),
        )
        await db.commit()
        return cur.lastrowid or 0


async def list_assemblies(
    sid: str | None = None,
    kind: str | None = None,
    owner: str | None = None,
    status: str = "active",
    limit: int = 100,
) -> list[dict[str, Any]]:
    """Liste les assemblages (latest version par aid)."""
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

    sql = (
        f"SELECT * FROM assemblies_index "
        f"WHERE {' AND '.join(wheres)} "
        f"AND (aid, version_num) IN ("
        f"  SELECT aid, MAX(version_num) FROM assemblies_index "
        f"  WHERE status = 'active' GROUP BY aid"
        f") "
        f"ORDER BY created_at DESC LIMIT ?"
    )
    params.append(limit)

    async with aiosqlite.connect(_DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cur = await db.execute(sql, params)
        rows = await cur.fetchall()
        return [dict(r) for r in rows]


async def get_assembly_latest(aid: str) -> dict[str, Any] | None:
    """Latest version d'un assemblage par aid."""
    async with aiosqlite.connect(_DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cur = await db.execute(
            """SELECT * FROM assemblies_index
               WHERE aid = ? AND status = 'active'
               ORDER BY version_num DESC LIMIT 1""",
            (aid,),
        )
        row = await cur.fetchone()
        return dict(row) if row else None


async def get_assembly_history(aid: str) -> list[dict[str, Any]]:
    """Toutes les versions (audit trail INSERT-only)."""
    async with aiosqlite.connect(_DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cur = await db.execute(
            """SELECT * FROM assemblies_index
               WHERE aid = ? ORDER BY version_num DESC""",
            (aid,),
        )
        rows = await cur.fetchall()
        return [dict(r) for r in rows]


async def update_published_info(aid: str, published_url: str) -> int:
    """Met à jour published_url + published_at sur la row latest.

    Cas particulier où on accepte un UPDATE (sinon le INSERT-only nous
    forcerait à dupliquer la row entière juste pour ajouter l'URL).
    L'audit reste préservé : version_num/content_hash inchangés.
    """
    async with aiosqlite.connect(_DB_PATH) as db:
        cur = await db.execute(
            """UPDATE assemblies_index
               SET published_url = ?, published_at = ?
               WHERE rowid IN (
                 SELECT rowid FROM assemblies_index
                 WHERE aid = ? AND status = 'active'
                 ORDER BY version_num DESC LIMIT 1
               )""",
            (published_url, int(time.time()), aid),
        )
        await db.commit()
        return cur.rowcount


async def archive_assembly(aid: str, owner: str) -> int:
    """Soft delete : INSERT new row archived."""
    latest = await get_assembly_latest(aid)
    if not latest or latest["owner"] != owner:
        return 0
    async with aiosqlite.connect(_DB_PATH) as db:
        cur = await db.execute(
            """INSERT INTO assemblies_index
               (aid, sid, owner, kind, title, content_hash, previous_hash,
                version_num, assembly_version, classification,
                audit_chain_json, provenance_json, rendered_path,
                published_url, published_at, status, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'archived', ?)""",
            (
                latest["aid"], latest["sid"], owner,
                latest["kind"], latest["title"],
                latest["content_hash"], latest["content_hash"],
                latest["version_num"] + 1,
                latest["assembly_version"], latest["classification"],
                latest["audit_chain_json"], latest["provenance_json"],
                latest["rendered_path"], latest["published_url"],
                latest["published_at"], int(time.time()),
            ),
        )
        await db.commit()
        return cur.lastrowid or 0


# ── audit_chain calcul (au publish) ───────────────────────────────────────────

async def build_audit_chain(
    assembly: Assembly,
    owner: str,
    classification: str = "cerema_internal",
) -> AuditChain:
    """Calcule l'audit_chain au moment du publish.

    Pour chaque composant référencé dans assembly.layout.sections[].components :
    1. Lookup composant via components_index → récupère scene_hash + provenance
    2. Lookup scene_manifest_index → récupère scene_hash latest du projet
    3. Lookup recipes_index → récupère slugs des recipes ayant créé le composant

    Agrège tout dans un AuditChain Pydantic + calcule integrity_hash SHA256
    canonique (D-FORMAT-008 rename, ex-signed_hash). C'est ce hash qui ancre
    la traçabilité tamper-evident.
    """
    from hub import components as comp_mod

    from hub.models.audit_chain import Source as AuditSource

    scene_hashes: list[str] = []
    components_refs: list[str] = []
    recipes_used: list[str] = []
    tool_calls_made: list[dict] = []
    sources: list[AuditSource] = []

    # Parcourir les sections + extraire refs composants
    for section in assembly.layout.sections:
        for comp_entry in section.components or []:
            if "ref" in comp_entry:
                cid = comp_entry["ref"]
                components_refs.append(cid)
                # Lookup composant
                comp_data = await comp_mod.get_component_latest(cid)
                if comp_data:
                    # Lookup scene_hash du projet associé
                    try:
                        prov = json.loads(comp_data.get("provenance_json") or "{}")
                        sh = prov.get("scene_hash_at_creation")
                        if sh and sh not in scene_hashes:
                            scene_hashes.append(sh)
                        recipe = prov.get("recipe_used")
                        if recipe and recipe not in recipes_used:
                            recipes_used.append(recipe)
                        tcm = prov.get("tool_calls_made", [])
                        if isinstance(tcm, list):
                            tool_calls_made.extend(tcm)
                    except Exception:
                        pass

    chain = AuditChain(
        aid=assembly.id, sid=assembly.sid, owner=owner,
        classification=classification,  # type: ignore[arg-type]
        scene_hashes=scene_hashes,
        components_refs=components_refs,
        recipes_used=recipes_used,
        tool_calls_made=tool_calls_made,
        sources=sources,
        created_at=datetime.utcnow(),
    )
    chain.integrity_hash = chain.compute_integrity_hash()
    return chain


# ── Pod-code helpers (PVC) ────────────────────────────────────────────────────

def assembly_manifest_path(sid: str, aid: str) -> str:
    return f"/data/studies/{sid}/assemblies/{aid}/manifest.json"


def assembly_rendered_path(sid: str, aid: str) -> str:
    return f"/data/studies/{sid}/assemblies/{aid}/rendered/index.html"


def write_assembly_manifest_pod_code(
    sid: str, aid: str, content_json: str
) -> str:
    path = assembly_manifest_path(sid, aid)
    return f"""
from pathlib import Path
p = Path({path!r})
p.parent.mkdir(parents=True, exist_ok=True)
p.write_text({content_json!r}, encoding='utf-8')
print(f'ASSEMBLY_WRITE_OK path={{p}}')
"""


def read_assembly_manifest_pod_code(sid: str, aid: str) -> str:
    path = assembly_manifest_path(sid, aid)
    return f"""
import base64
from pathlib import Path
p = Path({path!r})
if not p.exists():
    print('ASSEMBLY_NOT_FOUND')
else:
    data = p.read_bytes()
    b64 = base64.b64encode(data).decode()
    print(f'ASSEMBLY_READ_OK b64={{b64}}')
"""


def write_assembly_rendered_pod_code(
    sid: str, aid: str, html_content: str
) -> str:
    path = assembly_rendered_path(sid, aid)
    # html_content peut être grand → on encode base64 pour éviter le quoting
    import base64
    b64 = base64.b64encode(html_content.encode("utf-8")).decode()
    return f"""
import base64
from pathlib import Path
p = Path({path!r})
p.parent.mkdir(parents=True, exist_ok=True)
b64 = {b64!r}
content = base64.b64decode(b64).decode('utf-8')
p.write_text(content, encoding='utf-8')
print(f'ASSEMBLY_RENDER_WRITE_OK path={{p}} size={{len(content)}}')
"""
