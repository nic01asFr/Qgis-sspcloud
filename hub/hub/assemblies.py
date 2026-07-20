"""
hub.assemblies — CRUD `assemblies_index` + helpers pod-code + audit_chain calc.

Sprint Composants Phase 3 (2026-06-25). Pattern recipes_index/components_index
(INSERT-only audit trail, lookup latest via MAX(version_num)).

Storage :
- DB index : `assemblies_index` table dans `studies.db` (créée Phase 0)
- Manifest JSON : `/data/studies/{sid}/assemblies/{aid}/manifest.json` sur PVC
- Rendered HTML : `/data/studies/{sid}/assemblies/{aid}/rendered/index.html`
- Publication S3 : via `s3_publication.publish(owner, 'assembly', slug, ...)`

audit_chain : OBLIGATOIRE au publish. Snapshot integrity_hash SHA256 du
(D-FORMAT-008 2026-06-29 : rename signed_hash → integrity_hash, backward-
compat 1 release de grâce). Manifest serialise canonique tamper-evident du
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
    version_num_source: int | None = None,
) -> int:
    """Insert nouvelle row assemblies_index. INSERT-only (pas d'UPDATE).

    Sprint V1.15 Etape 2 : OCC enforcement au niveau fonction (pas
    seulement endpoint). Ferme la dette D2 identifiee par l'etude B :
    `insert_assembly` acceptait `previous_hash` mais ne verifiait pas
    `version_num_source` — bypassable si consumer appelait directement.

    Si `version_num_source` fourni ET ne correspond pas a la version
    actuelle latest en DB, leve `hub.actions.ConcurrentUpdateError` avec
    detail `current_version_num`, `source_version_num` (pattern identique
    endpoint update_assembly_endpoint main.py:4838).
    """
    # OCC guard V1.15 : verifier version_num_source AVANT insert
    if version_num_source is not None:
        latest = await get_assembly_latest(assembly.id)
        current_version = int(latest.get("version_num", 1)) if latest else 0
        try:
            src_version = int(version_num_source)
        except (TypeError, ValueError):
            from hub.actions import ActionValidationError
            raise ActionValidationError("version_num_source doit etre un entier")
        if src_version != current_version:
            from hub.actions import ConcurrentUpdateError
            raise ConcurrentUpdateError(
                "L'assemblage a ete modifie par un autre processus",
                current=current_version, source=src_version,
            )
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


def move_upload_to_rendered_pod_code(
    sid: str, aid: str, uploaded_filename: str,
) -> str:
    """Sprint sec-vague0 dette OOM (2026-07-19) : nouveau chemin d'ecriture
    du HTML rendu qui contourne le pic memoire du hub.

    Ancien chemin (write_assembly_rendered_pod_code ci-dessous, deprecated) :
    HTML 38MB -> gzip 7-10MB -> b64 10-15MB -> string Python inline dans
    code exec ~50MB -> JSON RPC payload ~50MB -> pic RSS hub > 8Gi ->
    OOMKilled (exit 137). Observe sur publish V22 fedcba987654 (2026-07-19).

    Nouveau chemin (ce helper + `_upload_html_via_workspace` cote hub) :
    1. Hub streamer HTML via multipart POST /api/upload workspace
       (aucun b64 inline, ~1MB buffer chunks httpx)
    2. Workspace ecrit dans /data/{uploaded_filename} (endpoint upload
       existant, MAX_UPLOAD_SIZE=50MB dans api_server.py:572)
    3. Ce helper produit le code Python leger (~200 bytes) pour
       shutil.move() du fichier upload vers assembly_rendered_path()
       canonique + mkdir parent + confirm signal

    Le HTML canonique final reste au meme path : contract inchange.
    Uniquement le chemin d'ecriture change.
    """
    target = assembly_rendered_path(sid, aid)
    return f"""
import shutil
from pathlib import Path
src = Path("/data/{uploaded_filename}")
dst = Path({target!r})
dst.parent.mkdir(parents=True, exist_ok=True)
if src.exists():
    shutil.move(str(src), str(dst))
    print(f"ASSEMBLY_RENDER_WRITE_OK path={{dst}} size={{dst.stat().st_size}}")
else:
    print(f"ASSEMBLY_RENDER_WRITE_ERR uploaded file not found src={{src}}")
"""


def write_assembly_rendered_pod_code(
    sid: str, aid: str, html_content: str
) -> str:
    """DEPRECATED (2026-07-19) : chemin inline b64 remplace par
    `move_upload_to_rendered_pod_code` + `_upload_html_via_workspace`
    cote hub (voir docstring ci-dessus). Conserve pour rollback rapide
    en cas de regression du nouveau chemin - a supprimer apres 1-2 sprints
    de stabilite empirique."""
    path = assembly_rendered_path(sid, aid)
    # Sprint V0.4.4 (2026-07-17) : gzip + base64 au lieu de base64 seul.
    #
    # Contexte du bug : depuis que les composants interactive_map inlinent
    # de gros GeoJSON (14 270 batiments BD TOPO -> HTML ~38 MB), le
    # payload JSON POST vers workspace `execute_python` (~50 MB code
    # base64-encode) etait rejete silencieusement (body_size_limit ou
    # timeout). Le hub voyait resp.ok mais le fichier PVC n'etait jamais
    # ecrit -> `rendered_html_written_pvc:true` mais fichier reel
    # inchange -> livrable rendu = vieille version, meme apres publish
    # V11 (mtime=V10 sur PVC).
    #
    # Fix : compression gzip (facteur 5-8x sur HTML repetitif GeoJSON),
    # decompression cote workspace. Le HTML 38 MB descend a ~5-8 MB
    # payload -> passe largement sous les limites httpx / uvicorn
    # (defaults 100 MB). Atomique, un seul call.
    import base64, gzip
    gz = gzip.compress(html_content.encode("utf-8"), compresslevel=6)
    b64 = base64.b64encode(gz).decode()
    return f"""
import base64, gzip
from pathlib import Path
p = Path({path!r})
p.parent.mkdir(parents=True, exist_ok=True)
b64 = {b64!r}
content = gzip.decompress(base64.b64decode(b64)).decode('utf-8')
p.write_text(content, encoding='utf-8')
print(f'ASSEMBLY_RENDER_WRITE_OK path={{p}} size={{len(content)}}')
"""


# ── S9 Wave 1 — Compaction rolling delta jsondiff ─────────────────────────────
#
# But : reduire la taille long-terme d'assemblies_index sans perdre l'audit
# trail. On aggregate N rows > max_age_days en 1 snapshot delta_json (jsondiff)
# dans la table `assemblies_deltas_compact`. Reconstruction possible via replay
# du delta sur le base_content_hash (row la plus ancienne conservee).
#
# Contrainte cle : ne JAMAIS compresser la version latest active. Le compact
# ne touche que les versions strictement < get_assembly_latest(aid).version_num
# ET dont created_at < cutoff (max_age_days).
#
# Integrity : le compact stocke integrity_hash SHA256 canonique du (aid,
# version_range_min, version_range_max, base_content_hash, delta_json) — le
# hash chain original (previous_hash / content_hash) reste intact sur les rows
# non compressees. La reconstruction est donc auditable de bout en bout.


def _canonical_jsondiff(before: dict, after: dict) -> str:
    """Compute delta jsondiff (marshal mode) serialise canonique."""
    import jsondiff
    delta = jsondiff.diff(before, after, marshal=True)
    return json.dumps(delta, sort_keys=True, separators=(",", ":"), ensure_ascii=True)


async def compact_assembly_deltas(
    aid: str, max_age_days: int = 30
) -> int:
    """Compacte les versions old d'un assemblage en 1 snapshot delta.

    Regles :
    - Prend toutes les rows `assemblies_index` de `aid` dont created_at < cutoff
    - Exclut la row latest active (jamais compressee)
    - Si < 2 rows eligibles, no-op (rien a compacter)
    - Serialise le delta = jsondiff(row_min.canonical, row_max.canonical)
      marshal mode, stocke dans assemblies_deltas_compact
    - Ne SUPPRIME PAS les rows d'assemblies_index (INSERT-only preserve).
      Le compact est un DECODEUR complementaire pour audit long-terme.

    Returns : nombre de rows agrégées dans le compact (0 si no-op).
    """
    cutoff = int(time.time()) - max_age_days * 86400
    latest = await get_assembly_latest(aid)
    latest_version = int(latest["version_num"]) if latest else 0

    async with aiosqlite.connect(_DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cur = await db.execute(
            """SELECT rowid, aid, version_num, content_hash, provenance_json,
                      created_at
               FROM assemblies_index
               WHERE aid = ? AND created_at < ? AND version_num < ?
               ORDER BY version_num ASC""",
            (aid, cutoff, latest_version),
        )
        rows = [dict(r) for r in await cur.fetchall()]

    if len(rows) < 2:
        return 0

    row_min = rows[0]
    row_max = rows[-1]
    v_min = int(row_min["version_num"])
    v_max = int(row_max["version_num"])

    # Delta canonique : diff du plus ancien (base) vers le plus recent (dans
    # la fenetre compactee). Reconstruction par replay du delta sur row_min.
    before = {
        "content_hash": row_min["content_hash"],
        "provenance_json": row_min.get("provenance_json") or "",
    }
    after = {
        "content_hash": row_max["content_hash"],
        "provenance_json": row_max.get("provenance_json") or "",
    }
    delta_json = _canonical_jsondiff(before, after)

    # integrity_hash SHA256 canonique du compact (chain preserve)
    integrity_payload = json.dumps(
        {
            "aid": aid,
            "version_range_min": v_min,
            "version_range_max": v_max,
            "base_content_hash": row_min["content_hash"],
            "delta_json": delta_json,
        },
        sort_keys=True, separators=(",", ":"), ensure_ascii=True,
    )
    integrity_hash = "sha256:" + hashlib.sha256(
        integrity_payload.encode()
    ).hexdigest()

    async with aiosqlite.connect(_DB_PATH) as db:
        # ON CONFLICT DO NOTHING pour idempotence (re-run compact meme fenetre)
        await db.execute(
            """INSERT OR IGNORE INTO assemblies_deltas_compact
               (aid, version_range_min, version_range_max, delta_json,
                base_content_hash, integrity_hash, ts)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (
                aid, v_min, v_max, delta_json,
                row_min["content_hash"], integrity_hash, int(time.time()),
            ),
        )
        await db.commit()

    return len(rows)


async def list_assembly_deltas_compact(aid: str) -> list[dict[str, Any]]:
    """Liste les snapshots compact pour un assemblage (audit / debug)."""
    async with aiosqlite.connect(_DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cur = await db.execute(
            """SELECT * FROM assemblies_deltas_compact
               WHERE aid = ? ORDER BY version_range_max DESC""",
            (aid,),
        )
        return [dict(r) for r in await cur.fetchall()]
