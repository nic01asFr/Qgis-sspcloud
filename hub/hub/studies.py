"""
hub.studies — Études (sessions logiques) sur le workspace permanent.

Une **étude** est un contexte de travail CEREMA unifié :
  - un projet QGIS (.qgz)
  - une conversation agent (profil + history)
  - un audit trail propre
  - des livrables (storymaps, exports, recettes)
  - des notes utilisateur

Plusieurs études cohabitent dans le même workspace (PVC partagé), permettant
au user de jongler entre dossiers sans tout perdre. Le Bureau noVNC affiche
*l'étude active* (un seul projet QGIS ouvert à un instant t), mais les autres
études restent intactes sur le PVC.

Phase 2 du refactor (post 2026-05-15) — fondation de Phases 4, 6, 8.

Layout sur PVC :
  /data/studies/{sid}/
    ├── project.qgz           (ou lien symbolique vers /data/projects/)
    ├── treatments.jsonl      ← audit trail propre à cette étude
    ├── exports/              ← livrables de cette étude
    │   ├── storymaps/
    │   ├── pdf/
    │   └── data/
    ├── notes.md              ← libre, user
    └── meta.json             ← {id, name, profile, ...}

  /data/.active_study         ← simple fichier texte : id de l'étude active
"""

from __future__ import annotations

import json
import os
import time
import uuid
from pathlib import Path
from typing import Any

import aiosqlite

# Defaut persistant : si /home/onyxia/work (PVC) existe, on ecrit dedans pour
# que studies.db survive aux redeploys hub (sinon toutes les etudes user
# disparaissent au moindre push d'image). Cf. auth.py meme strategie.
_DATA_DIR = Path(os.getenv("DATA_DIR") or (
    "/home/onyxia/work/qgis-mcp/server-data"
    if Path("/home/onyxia/work").is_dir()
    else "/tmp/qgis-mcp/server-data"
))
_DB_PATH  = _DATA_DIR / "studies.db"


# ── DB schema ─────────────────────────────────────────────────────────────────

async def init_db() -> None:
    _DATA_DIR.mkdir(parents=True, exist_ok=True)
    async with aiosqlite.connect(_DB_PATH) as db:
        await db.execute("""
            CREATE TABLE IF NOT EXISTS studies (
                id TEXT PRIMARY KEY,
                owner TEXT NOT NULL,
                name TEXT NOT NULL,
                profile TEXT,
                project_path TEXT,
                conversation_id TEXT,
                created_at INTEGER NOT NULL,
                last_active INTEGER NOT NULL,
                status TEXT NOT NULL DEFAULT 'active'
            )
        """)
        await db.execute("""
            CREATE INDEX IF NOT EXISTS idx_studies_owner ON studies(owner)
        """)
        await db.execute("""
            CREATE TABLE IF NOT EXISTS active_study (
                owner TEXT PRIMARY KEY,
                study_id TEXT NOT NULL
            )
        """)
        # V1.5 Sprint 1 : index recipes user (versioning SHA, audit).
        # Le contenu YAML/JSON vit sur le PVC workspace, cette table track les
        # metadonnees + chaine de versions. Une row par (sid, slug, sha) :
        # historique conservé (pas de UPDATE, INSERT seulement, lookup latest
        # via MAX(version_num)). status='active'|'archived' (soft delete).
        await db.execute("""
            CREATE TABLE IF NOT EXISTS recipes_index (
                rowid INTEGER PRIMARY KEY AUTOINCREMENT,
                sid TEXT NOT NULL,
                slug TEXT NOT NULL,
                sha TEXT NOT NULL,
                previous_sha TEXT,
                version_num INTEGER NOT NULL DEFAULT 1,
                owner TEXT NOT NULL,
                name TEXT,
                description TEXT,
                format TEXT NOT NULL DEFAULT 'yaml',
                status TEXT NOT NULL DEFAULT 'active',
                created_at INTEGER NOT NULL,
                published_at INTEGER,
                public_url TEXT
            )
        """)
        await db.execute("""
            CREATE INDEX IF NOT EXISTS idx_recipes_sid_slug
                ON recipes_index(sid, slug, version_num DESC)
        """)
        await db.execute("""
            CREATE INDEX IF NOT EXISTS idx_recipes_owner
                ON recipes_index(owner, status)
        """)
        # Sprint Composants Phase 3c (2026-06-27) : meta-agent analyseur recipes.
        # Index DB des RecipeAnalysis (params + quality checks). Pattern V1.5 :
        # INSERT-only, lookup latest via MAX(rowid). Cache key composite
        # (slug, source, content_hash[:12]). Re-trigger quand content_hash change.
        # JSON complet sur PVC (file_path). Le breakdown structuré reste exploitable
        # côté hub via la column json_blob (compressed analysis).
        await db.execute("""
            CREATE TABLE IF NOT EXISTS recipe_analyses_index (
                rowid INTEGER PRIMARY KEY AUTOINCREMENT,
                slug TEXT NOT NULL,
                source TEXT NOT NULL,
                content_hash TEXT NOT NULL,
                sid TEXT,
                owner TEXT,
                analyzer_model TEXT NOT NULL,
                analyzer_version INTEGER NOT NULL DEFAULT 1,
                analyzed_at INTEGER NOT NULL,
                file_path TEXT NOT NULL,
                overall_score REAL,
                cost_level TEXT,
                n_params INTEGER DEFAULT 0,
                n_warnings INTEGER DEFAULT 0,
                n_errors INTEGER DEFAULT 0,
                status TEXT NOT NULL DEFAULT 'success',
                error_detail TEXT,
                human_validated INTEGER DEFAULT 0,
                human_validator TEXT,
                human_validated_at INTEGER
            )
        """)
        await db.execute("""
            CREATE INDEX IF NOT EXISTS idx_recipe_analyses_lookup
                ON recipe_analyses_index(slug, source, content_hash, rowid DESC)
        """)
        await db.execute("""
            CREATE INDEX IF NOT EXISTS idx_recipe_analyses_admin
                ON recipe_analyses_index(human_validated, overall_score, analyzed_at DESC)
        """)
        # Sprint Composants Phase 4a (2026-06-27) : meta-agent analyseur
        # config d'agent partagé. Pattern strict recipe_analyses_index :
        # INSERT-only, cache key (sid, config_hash[:12]). Le contenu Pydantic
        # complet sur PVC, l'index DB garde metadata + breakdown.
        await db.execute("""
            CREATE TABLE IF NOT EXISTS agent_analyses_index (
                rowid INTEGER PRIMARY KEY AUTOINCREMENT,
                sid TEXT NOT NULL,
                config_hash TEXT NOT NULL,
                owner TEXT NOT NULL,
                profile TEXT NOT NULL,
                audience TEXT NOT NULL,
                analyzer_model TEXT NOT NULL,
                analyzer_version INTEGER NOT NULL DEFAULT 1,
                analyzed_at INTEGER NOT NULL,
                file_path TEXT NOT NULL,
                overall_score REAL,
                n_params INTEGER DEFAULT 0,
                n_warnings INTEGER DEFAULT 0,
                n_errors INTEGER DEFAULT 0,
                status TEXT NOT NULL DEFAULT 'success',
                error_detail TEXT,
                human_validated INTEGER DEFAULT 0
            )
        """)
        await db.execute("""
            CREATE INDEX IF NOT EXISTS idx_agent_analyses_lookup
                ON agent_analyses_index(sid, config_hash, rowid DESC)
        """)
        await db.execute("""
            CREATE INDEX IF NOT EXISTS idx_agent_analyses_owner
                ON agent_analyses_index(owner, analyzed_at DESC)
        """)
        # Sprint UX-3 (2026-06-21) : modele etude->projet 1:N.
        # Une etude (container thematique) peut contenir N projets QGIS.
        # Chaque projet a son propre .qgz + history.jsonl + .checkpoints/.
        # is_default=1 marque le projet "principal" de l'etude (1 unique par sid),
        # ouvert par defaut quand on active l'etude. Les scenarios alternatifs
        # (variantes, brouillons) sont is_default=0.
        # Cohabitation V1.5 recipes : recipes restent au niveau etude (pas par
        # projet). Une recette s'applique sur le projet actif de l'etude active.
        # history.jsonl = trace tool_calls + events par projet. Source primaire
        # pour la generation de macros (Phase 11) -> analyzer LLM extrait une
        # recette parametree de ce trace.
        await db.execute("""
            CREATE TABLE IF NOT EXISTS study_projects (
                pid TEXT PRIMARY KEY,
                sid TEXT NOT NULL,
                owner TEXT NOT NULL,
                label TEXT NOT NULL,
                qgz_path TEXT NOT NULL,
                history_path TEXT,
                created_at INTEGER NOT NULL,
                last_active INTEGER NOT NULL,
                is_default INTEGER NOT NULL DEFAULT 0,
                status TEXT NOT NULL DEFAULT 'active'
            )
        """)
        await db.execute("""
            CREATE INDEX IF NOT EXISTS idx_study_projects_sid
                ON study_projects(sid, last_active DESC)
        """)
        await db.execute("""
            CREATE INDEX IF NOT EXISTS idx_study_projects_owner
                ON study_projects(owner, status)
        """)
        # Projet actif (1 par owner). Independant de active_study : un user peut
        # avoir une etude active avec un projet specifique selectionne.
        # En pratique : quand on active une etude, on active aussi son projet
        # is_default (cf. activate_study endpoint chained). L'user peut switcher
        # de projet dans l'etude active sans changer l'etude active.
        await db.execute("""
            CREATE TABLE IF NOT EXISTS active_project (
                owner TEXT PRIMARY KEY,
                pid TEXT NOT NULL
            )
        """)
        # Sprint Composants-1 (2026-06-24) : index des exports generes par
        # projet (Grist .grist, GPKG scene, ZIP composite, etc.). Pattern
        # recipes_index/scene_manifest_index : INSERT-only, audit trail.
        # Permet de tracer qui a genere quoi quand + lookup les latest par
        # type d'export. Le fichier brut vit sur le PVC dans
        # /data/studies/{sid}/projects/{pid}/exports/{filename}.
        await db.execute("""
            CREATE TABLE IF NOT EXISTS exports_index (
                rowid INTEGER PRIMARY KEY AUTOINCREMENT,
                pid TEXT NOT NULL,
                sid TEXT NOT NULL,
                owner TEXT NOT NULL,
                export_type TEXT NOT NULL,
                filename TEXT NOT NULL,
                file_path TEXT NOT NULL,
                size_bytes INTEGER NOT NULL DEFAULT 0,
                scene_hash TEXT,
                n_tables INTEGER,
                n_records INTEGER,
                extra_json TEXT,
                status TEXT NOT NULL DEFAULT 'active',
                created_at INTEGER NOT NULL
            )
        """)
        await db.execute("""
            CREATE INDEX IF NOT EXISTS idx_exports_pid
                ON exports_index(pid, export_type, created_at DESC)
        """)
        await db.execute("""
            CREATE INDEX IF NOT EXISTS idx_exports_owner
                ON exports_index(owner, status)
        """)
        # Sprint Composants-1 (2026-06-24) : index Scene Manifest par projet.
        # Pattern recipes_index V1.5 (INSERT-only, versioning SHA, audit trail).
        # Le contenu JSON du Scene Manifest vit sur le PVC dans
        # /data/studies/{sid}/projects/{pid}/scene_manifest.json. Cette table
        # track les metadonnees + chaine de versions (qui a edite quand, et
        # quel scene_hash issue de la validation Pydantic + canonicalisation
        # cf. vendor/scene_manifest.py).
        # Une row par (pid, sha) : INSERT-only -> historique conserve.
        # Lookup latest via MAX(version_num) WHERE pid=? AND status='active'.
        await db.execute("""
            CREATE TABLE IF NOT EXISTS scene_manifest_index (
                rowid INTEGER PRIMARY KEY AUTOINCREMENT,
                pid TEXT NOT NULL,
                sid TEXT NOT NULL,
                owner TEXT NOT NULL,
                scene_hash TEXT NOT NULL,
                previous_hash TEXT,
                version_num INTEGER NOT NULL DEFAULT 1,
                manifest_version TEXT NOT NULL DEFAULT 'V0.2',
                n_layers INTEGER NOT NULL DEFAULT 0,
                size_bytes INTEGER NOT NULL DEFAULT 0,
                status TEXT NOT NULL DEFAULT 'active',
                created_at INTEGER NOT NULL,
                published_at INTEGER,
                public_url TEXT
            )
        """)
        await db.execute("""
            CREATE INDEX IF NOT EXISTS idx_scene_manifest_pid
                ON scene_manifest_index(pid, version_num DESC)
        """)
        await db.execute("""
            CREATE INDEX IF NOT EXISTS idx_scene_manifest_owner
                ON scene_manifest_index(owner, status)
        """)

        # ─── Sprint Composants Phase 0 (2026-06-25) ──────────────────────
        # Hardening uniforme + nouvelles strates COMPOSANTS et ASSEMBLAGES.
        # Décision verrouillée (8 verdicts évaluateurs) :
        #   1. INSERT-only audit trail (déjà appliqué aux tables existantes)
        #   2. provenance_json sur recipes/scene_manifest/exports (additif)
        #   3. classification audience sur scene_manifest/exports (additif)
        #   4. UNIQUE rétroactif (entity_id, version_num) pour race-safe
        #   5. table tombstones GDPR right-to-erasure
        #   6. table components_index (Sprint 2)
        #   7. table assemblies_index (Sprint 3)
        # Capitalisation :
        #   ~/.wikichat/knowledge/audit-trail-axis.md
        #   ~/.wikichat/knowledge/qgis-sspcloud-composants-axis.md

        # 1. ALTER additifs sur tables existantes (idempotents via try/except).
        # SQLite n'a pas ALTER ... IF NOT EXISTS sur les colonnes → on tente
        # et ignore le "duplicate column name" en cas de re-run init_db.
        for alter_sql in [
            # recipes_index : provenance_json
            "ALTER TABLE recipes_index ADD COLUMN provenance_json TEXT",
            # scene_manifest_index : provenance_json + classification
            "ALTER TABLE scene_manifest_index ADD COLUMN provenance_json TEXT",
            ("ALTER TABLE scene_manifest_index ADD COLUMN classification "
             "TEXT NOT NULL DEFAULT 'cerema_internal'"),
            # exports_index : provenance_json + classification
            "ALTER TABLE exports_index ADD COLUMN provenance_json TEXT",
            ("ALTER TABLE exports_index ADD COLUMN classification "
             "TEXT NOT NULL DEFAULT 'cerema_internal'"),
        ]:
            try:
                await db.execute(alter_sql)
            except Exception as exc:
                msg = str(exc).lower()
                if "duplicate column" not in msg and "already exists" not in msg:
                    raise  # vraie erreur, on ne masque pas

        # 2. UNIQUE INDEX rétroactif (race-safe insertion versions).
        # Pour recipes_index : UNIQUE(sid, slug, version_num) — match
        # pattern V1.5 INSERT-only (sid+slug = composition d'unicité d'une
        # recette dans son étude). Pour scene_manifest_index : UNIQUE(pid,
        # version_num). exports_index n'a pas de version_num (1 insert =
        # 1 row d'export), on s'appuie sur rowid AUTOINCREMENT.
        await db.execute("""
            CREATE UNIQUE INDEX IF NOT EXISTS uniq_recipes_version
                ON recipes_index(sid, slug, version_num)
        """)
        await db.execute("""
            CREATE UNIQUE INDEX IF NOT EXISTS uniq_scene_manifest_version
                ON scene_manifest_index(pid, version_num)
        """)

        # 3. Tombstones GDPR — right-to-erasure. INSERT-only audit garde
        # l'enveloppe (entity_id, owner, hash) pour audit, mais le contenu
        # PVC est purgé via entity_purge_content().
        await db.execute("""
            CREATE TABLE IF NOT EXISTS tombstones (
                rowid           INTEGER PRIMARY KEY AUTOINCREMENT,
                entity_type     TEXT NOT NULL,
                entity_id       TEXT NOT NULL,
                original_owner  TEXT NOT NULL,
                content_hash    TEXT NOT NULL,
                purged_at       INTEGER NOT NULL,
                purge_reason    TEXT,
                UNIQUE(entity_type, entity_id, purged_at)
            )
        """)
        await db.execute("""
            CREATE INDEX IF NOT EXISTS idx_tombstones_owner
                ON tombstones(original_owner, purged_at DESC)
        """)

        # 4. components_index — table strate COMPOSANTS (Sprint 2 will use).
        # Pattern recipes_index : INSERT-only, version_num + previous_hash
        # chaînage, UNIQUE(cid, version_num) race-safe.
        await db.execute("""
            CREATE TABLE IF NOT EXISTS components_index (
                rowid             INTEGER PRIMARY KEY AUTOINCREMENT,
                cid               TEXT NOT NULL,
                sid               TEXT NOT NULL,
                owner             TEXT NOT NULL,
                kind              TEXT NOT NULL,
                title             TEXT NOT NULL,
                content_hash      TEXT NOT NULL,
                previous_hash     TEXT,
                version_num       INTEGER NOT NULL DEFAULT 1,
                component_version TEXT NOT NULL DEFAULT 'V0.1',
                classification    TEXT NOT NULL DEFAULT 'cerema_internal',
                provenance_json   TEXT,
                file_path         TEXT,
                size_bytes        INTEGER NOT NULL DEFAULT 0,
                status            TEXT NOT NULL DEFAULT 'active',
                created_at        INTEGER NOT NULL,
                UNIQUE(cid, version_num)
            )
        """)
        await db.execute("""
            CREATE INDEX IF NOT EXISTS idx_components_lookup
                ON components_index(cid, status, version_num DESC)
        """)
        await db.execute("""
            CREATE INDEX IF NOT EXISTS idx_components_study
                ON components_index(sid, status, kind)
        """)

        # 5. assemblies_index — table strate ASSEMBLAGES (Sprint 3 will use).
        # audit_chain_json est OBLIGATOIRE à publish (snapshot signed_hash).
        await db.execute("""
            CREATE TABLE IF NOT EXISTS assemblies_index (
                rowid             INTEGER PRIMARY KEY AUTOINCREMENT,
                aid               TEXT NOT NULL,
                sid               TEXT NOT NULL,
                owner             TEXT NOT NULL,
                kind              TEXT NOT NULL,
                title             TEXT NOT NULL,
                content_hash      TEXT NOT NULL,
                previous_hash     TEXT,
                version_num       INTEGER NOT NULL DEFAULT 1,
                assembly_version  TEXT NOT NULL DEFAULT 'V0.1',
                classification    TEXT NOT NULL DEFAULT 'cerema_internal',
                audit_chain_json  TEXT,
                provenance_json   TEXT,
                rendered_path     TEXT,
                published_url     TEXT,
                published_at      INTEGER,
                status            TEXT NOT NULL DEFAULT 'active',
                created_at        INTEGER NOT NULL,
                UNIQUE(aid, version_num)
            )
        """)
        await db.execute("""
            CREATE INDEX IF NOT EXISTS idx_assemblies_lookup
                ON assemblies_index(aid, status, version_num DESC)
        """)
        await db.execute("""
            CREATE INDEX IF NOT EXISTS idx_assemblies_study
                ON assemblies_index(sid, status, kind)
        """)

        # Migration 2026-06-27 : URL MinIO -> URL Hub /published.
        # Bug MinIO SSPCloud : ACL canned 'public-read' ne fonctionne plus.
        # Les anciens published_url (avant fix f11da9d) etaient en URL S3
        # directe minio.lab.sspcloud.fr qui retournent 403.
        # On rewrite vers l'URL hub qui sert via /published/{owner}/{kind}/{slug}
        # (lecture S3 cote serveur). Idempotent : ne touche que les URL minio.
        import os as _os
        hub_url_env = _os.getenv("HUB_URL", "")
        if hub_url_env:
            hub_url_env = hub_url_env.rstrip("/")
            try:
                # Pattern S3 :
                #   https://minio.lab.sspcloud.fr/<owner>/qgis-workspace/published/<owner>/<kind>/<slug>
                # → /published/<owner>/<kind>/<slug>
                # SQLite REPLACE() ne fait que du literal — on fait via SELECT+UPDATE
                cur = await db.execute(
                    "SELECT aid, published_url FROM assemblies_index "
                    "WHERE published_url LIKE 'https://minio%'"
                )
                rows = await cur.fetchall()
                migrated = 0
                for aid, old_url in rows:
                    # Extract owner/kind/slug from URL S3
                    import re as _re
                    m = _re.match(
                        r"^https?://minio[^/]+/[^/]+/qgis-workspace/published/([^/]+)/([^/]+)/([^?]+)$",
                        old_url or "",
                    )
                    if not m:
                        continue
                    owner, kind, slug = m.group(1), m.group(2), m.group(3)
                    new_url = f"{hub_url_env}/published/{owner}/{kind}/{slug}"
                    await db.execute(
                        "UPDATE assemblies_index SET published_url = ? WHERE aid = ?",
                        (new_url, aid),
                    )
                    migrated += 1
                if migrated:
                    import logging as _logging
                    _logging.getLogger("hub.studies").info(
                        "Migration 2026-06-27 : %d published_url MinIO -> Hub",
                        migrated,
                    )
            except Exception as exc:
                import logging as _logging
                _logging.getLogger("hub.studies").warning(
                    "Migration MinIO->Hub skippee : %s", exc,
                )

        await db.commit()


# ─── Sprint Composants Phase 0 : helper GDPR ────────────────────────────────

async def entity_purge_content(
    entity_type: str,
    entity_id: str,
    purge_reason: str = "user_request",
) -> int:
    """GDPR right-to-erasure. Purge le contenu PVC mais conserve l'enveloppe
    audit (rowid, owner, content_hash) dans `tombstones` pour traçabilité.

    Stratégie :
    1. UPDATE entity table : status='purged', content/manifest_json='', etc.
       (selon entity_type, géré côté CRUD spécifique au moment de l'appel)
    2. INSERT tombstones row avec content_hash original

    Cette fonction enregistre uniquement le tombstone. La purge PVC effective
    (suppression fichier) doit être faite par le CRUD spécifique de l'entity
    avant d'appeler cette fonction.

    Args:
        entity_type: 'component', 'assembly', 'recipe', 'scene_manifest', ...
        entity_id:   ID 12 hex de l'entité
        purge_reason: 'user_request', 'gdpr_request', 'admin_purge', ...

    Returns:
        rowid du tombstone créé (0 si erreur)
    """
    # Lookup content_hash original depuis la table appropriée
    async with aiosqlite.connect(_DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        content_hash = ""
        original_owner = ""

        # Table mapping (entity_type, id_column, hash_column)
        # Schemas réels :
        #   recipes_index           : id=slug,     hash=sha
        #   scene_manifest_index    : id=pid,      hash=scene_hash
        #   exports_index           : id=filename, hash=scene_hash
        #   components_index (S2)   : id=cid,      hash=content_hash
        #   assemblies_index (S3)   : id=aid,      hash=content_hash
        table_map = {
            "component":      ("components_index",      "cid",      "content_hash"),
            "assembly":       ("assemblies_index",      "aid",      "content_hash"),
            "recipe":         ("recipes_index",         "slug",     "sha"),
            "scene_manifest": ("scene_manifest_index",  "pid",      "scene_hash"),
            "export":         ("exports_index",         "filename", "scene_hash"),
        }
        if entity_type not in table_map:
            return 0
        table_name, id_col, hash_col = table_map[entity_type]

        try:
            cur = await db.execute(
                f"SELECT {hash_col} AS h, owner FROM {table_name} "
                f"WHERE {id_col} = ? ORDER BY rowid DESC LIMIT 1",
                (entity_id,),
            )
            row = await cur.fetchone()
            if row:
                content_hash = row["h"] or ""
                original_owner = row["owner"] or ""
        except Exception:
            pass

        # Insert tombstone (INSERT-only audit)
        cur = await db.execute(
            """INSERT INTO tombstones
               (entity_type, entity_id, original_owner, content_hash,
                purged_at, purge_reason)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (entity_type, entity_id, original_owner, content_hash,
             int(time.time()), purge_reason),
        )
        await db.commit()
        return cur.lastrowid or 0


# ── Helpers ───────────────────────────────────────────────────────────────────

def _new_id() -> str:
    return uuid.uuid4().hex[:12]


def _safe_name(name: str) -> str:
    return name.strip()[:120] or "Étude sans nom"


# ── CRUD ──────────────────────────────────────────────────────────────────────

async def create_study(
    owner: str,
    name: str,
    profile: str = "standard",
    project_path: str | None = None,
) -> dict:
    sid = _new_id()
    now = int(time.time())
    name_safe = _safe_name(name)
    study = {
        "id":              sid,
        "owner":           owner,
        "name":            name_safe,
        "profile":         profile,
        "project_path":    project_path or f"/data/studies/{sid}/project.qgz",
        "conversation_id": None,
        "created_at":      now,
        "last_active":     now,
        "status":          "active",
    }
    async with aiosqlite.connect(_DB_PATH) as db:
        await db.execute("""
            INSERT INTO studies
            (id, owner, name, profile, project_path, conversation_id,
             created_at, last_active, status)
            VALUES (:id, :owner, :name, :profile, :project_path, :conversation_id,
                    :created_at, :last_active, :status)
        """, study)
        await db.commit()
    return study


async def get_study(sid: str, owner: str | None = None) -> dict | None:
    async with aiosqlite.connect(_DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        row = await (await db.execute(
            "SELECT * FROM studies WHERE id = ?", (sid,)
        )).fetchone()
    if not row:
        return None
    s = dict(row)
    if owner and s["owner"] != owner:
        return None
    return s


async def list_studies(owner: str, include_archived: bool = False) -> list[dict]:
    async with aiosqlite.connect(_DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        if include_archived:
            rows = await (await db.execute(
                "SELECT * FROM studies WHERE owner = ? ORDER BY last_active DESC",
                (owner,)
            )).fetchall()
        else:
            rows = await (await db.execute(
                "SELECT * FROM studies WHERE owner = ? AND status = 'active' "
                "ORDER BY last_active DESC",
                (owner,)
            )).fetchall()
    return [dict(r) for r in rows]


async def touch_study(sid: str) -> None:
    async with aiosqlite.connect(_DB_PATH) as db:
        await db.execute(
            "UPDATE studies SET last_active = ? WHERE id = ?",
            (int(time.time()), sid),
        )
        await db.commit()


async def update_study(sid: str, **fields) -> dict | None:
    """Mise à jour partielle. Champs autorisés : name, profile, project_path,
    conversation_id, status."""
    allowed = {"name", "profile", "project_path", "conversation_id", "status"}
    fields = {k: v for k, v in fields.items() if k in allowed}
    if not fields:
        return await get_study(sid)
    setters = ", ".join(f"{k} = ?" for k in fields)
    values = list(fields.values()) + [sid]
    async with aiosqlite.connect(_DB_PATH) as db:
        await db.execute(f"UPDATE studies SET {setters} WHERE id = ?", values)
        await db.commit()
    return await get_study(sid)


async def archive_study(sid: str) -> None:
    """Archive (soft delete) — préserve les fichiers, marque status='archived'."""
    async with aiosqlite.connect(_DB_PATH) as db:
        await db.execute(
            "UPDATE studies SET status = 'archived' WHERE id = ?", (sid,)
        )
        await db.commit()


async def purge_study(sid: str) -> None:
    """Suppression totale — DB row + dossier /data/studies/{sid}/."""
    import shutil
    async with aiosqlite.connect(_DB_PATH) as db:
        await db.execute("DELETE FROM studies WHERE id = ?", (sid,))
        await db.execute("DELETE FROM active_study WHERE study_id = ?", (sid,))
        await db.commit()
    # NOTE : la suppression du dossier sur PVC se fait côté pod via execute_python
    # (le hub n'a pas accès direct au PVC user). Voir endpoint correspondant.


# ── Étude active (par owner) ──────────────────────────────────────────────────

async def get_active_study_id(owner: str) -> str | None:
    async with aiosqlite.connect(_DB_PATH) as db:
        row = await (await db.execute(
            "SELECT study_id FROM active_study WHERE owner = ?", (owner,)
        )).fetchone()
    return row[0] if row else None


async def set_active_study(owner: str, sid: str) -> None:
    async with aiosqlite.connect(_DB_PATH) as db:
        await db.execute(
            "INSERT OR REPLACE INTO active_study (owner, study_id) VALUES (?, ?)",
            (owner, sid),
        )
        await db.commit()


# ── V1.5 Sprint 1 : recipes_index CRUD DB ─────────────────────────────────────

async def recipe_index_insert(
    sid: str, slug: str, sha: str, owner: str,
    name: str = "", description: str = "", fmt: str = "yaml",
    previous_sha: str = "",
) -> int:
    """Insert une nouvelle row recipes_index. Auto-increment version_num.

    Pattern : pas de UPDATE -> nouvelle row a chaque save = audit trail.
    Lookup latest via MAX(version_num) WHERE sid=? AND slug=? AND status='active'.
    """
    import time
    async with aiosqlite.connect(_DB_PATH) as db:
        # version_num = max(precedente)+1 ou 1 si premiere
        cur = await db.execute(
            "SELECT MAX(version_num) FROM recipes_index WHERE sid=? AND slug=?",
            (sid, slug),
        )
        row = await cur.fetchone()
        next_v = (row[0] or 0) + 1
        cur = await db.execute(
            """INSERT INTO recipes_index
               (sid, slug, sha, previous_sha, version_num, owner, name,
                description, format, status, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'active', ?)""",
            (sid, slug, sha, previous_sha or None, next_v, owner,
             name or slug, description, fmt, int(time.time())),
        )
        await db.commit()
        return cur.lastrowid or 0


async def recipe_index_get_latest(sid: str, slug: str) -> dict | None:
    """Retourne la derniere version active d'une recipe (None si absente)."""
    async with aiosqlite.connect(_DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cur = await db.execute(
            """SELECT * FROM recipes_index
               WHERE sid=? AND slug=? AND status='active'
               ORDER BY version_num DESC LIMIT 1""",
            (sid, slug),
        )
        row = await cur.fetchone()
        return dict(row) if row else None


async def recipe_index_list(sid: str) -> list[dict]:
    """Liste les recipes actives d'une etude (latest version uniquement)."""
    async with aiosqlite.connect(_DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cur = await db.execute(
            """SELECT r1.* FROM recipes_index r1
               INNER JOIN (
                   SELECT sid, slug, MAX(version_num) AS max_v
                   FROM recipes_index
                   WHERE sid=? AND status='active'
                   GROUP BY sid, slug
               ) r2 ON r1.sid=r2.sid AND r1.slug=r2.slug AND r1.version_num=r2.max_v
               ORDER BY r1.created_at DESC""",
            (sid,),
        )
        rows = await cur.fetchall()
        return [dict(r) for r in rows]


async def recipe_index_history(sid: str, slug: str) -> list[dict]:
    """Toutes les versions (actives + archivees) d'une recipe, plus recent d'abord."""
    async with aiosqlite.connect(_DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cur = await db.execute(
            """SELECT * FROM recipes_index
               WHERE sid=? AND slug=?
               ORDER BY version_num DESC""",
            (sid, slug),
        )
        rows = await cur.fetchall()
        return [dict(r) for r in rows]


async def recipe_index_archive(sid: str, slug: str) -> int:
    """Soft delete : marque toutes les versions de (sid, slug) comme archived.

    Pattern Q4 audit : on conserve l'historique pour audit + possibilite de
    restauration. Le fichier sur PVC est renomme `.archived.<ts>` par
    delete_recipe_pod_code (cf. studies.py:save_recipe_pod_code).
    """
    async with aiosqlite.connect(_DB_PATH) as db:
        cur = await db.execute(
            "UPDATE recipes_index SET status='archived' WHERE sid=? AND slug=?",
            (sid, slug),
        )
        await db.commit()
        return cur.rowcount


async def recipe_index_mark_published(
    sid: str, slug: str, version_num: int, public_url: str
) -> None:
    """Note qu'une version a ete publiee S3 (lien public_url)."""
    import time
    async with aiosqlite.connect(_DB_PATH) as db:
        await db.execute(
            """UPDATE recipes_index
               SET published_at=?, public_url=?
               WHERE sid=? AND slug=? AND version_num=?""",
            (int(time.time()), public_url, sid, slug, version_num),
        )
        await db.commit()


# ── Sprint Composants Phase 3c : CRUD recipe_analyses_index ──────────────────
# Pattern V1.5 INSERT-only audit trail. Cache key composite
# (slug, source, content_hash). Le contenu Pydantic complet est sur PVC, l'index
# DB garde le metadata + breakdown statistique pour les queries admin (review,
# filter par human_validated, sort par overall_score).

async def recipe_analyses_insert(
    slug: str, source: str, content_hash: str,
    analyzer_model: str, file_path: str,
    overall_score: float, cost_level: str,
    n_params: int = 0, n_warnings: int = 0, n_errors: int = 0,
    status: str = "success", error_detail: str | None = None,
    sid: str | None = None, owner: str | None = None,
    analyzer_version: int = 1,
) -> int:
    """Insert nouvelle row recipe_analyses_index. INSERT-only (pas d'UPDATE)."""
    import time as _t
    async with aiosqlite.connect(_DB_PATH) as db:
        cur = await db.execute(
            """INSERT INTO recipe_analyses_index
               (slug, source, content_hash, sid, owner, analyzer_model,
                analyzer_version, analyzed_at, file_path, overall_score,
                cost_level, n_params, n_warnings, n_errors, status, error_detail)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (slug, source, content_hash, sid, owner, analyzer_model,
             analyzer_version, int(_t.time()), file_path, overall_score,
             cost_level, n_params, n_warnings, n_errors, status, error_detail),
        )
        await db.commit()
        return cur.lastrowid or 0


async def recipe_analyses_get_latest(
    slug: str, source: str, content_hash: str | None = None,
) -> dict | None:
    """Retourne le dernier RecipeAnalysis matching (slug, source, [hash]).

    Si content_hash fourni : exact match (cache lookup). Sinon : dernière
    analyse connue toutes versions (admin review).
    Retourne None si aucun.
    """
    async with aiosqlite.connect(_DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        if content_hash:
            cur = await db.execute(
                """SELECT * FROM recipe_analyses_index
                   WHERE slug = ? AND source = ? AND content_hash = ?
                   ORDER BY rowid DESC LIMIT 1""",
                (slug, source, content_hash),
            )
        else:
            cur = await db.execute(
                """SELECT * FROM recipe_analyses_index
                   WHERE slug = ? AND source = ?
                   ORDER BY rowid DESC LIMIT 1""",
                (slug, source),
            )
        row = await cur.fetchone()
        return dict(row) if row else None


async def recipe_analyses_history(
    slug: str, source: str, limit: int = 20,
) -> list[dict]:
    """Historique des analyses (versions par content_hash)."""
    async with aiosqlite.connect(_DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cur = await db.execute(
            """SELECT * FROM recipe_analyses_index
               WHERE slug = ? AND source = ?
               ORDER BY rowid DESC LIMIT ?""",
            (slug, source, limit),
        )
        rows = await cur.fetchall()
        return [dict(r) for r in rows]


async def recipe_analyses_review_pending(limit: int = 50) -> list[dict]:
    """Admin review : retourne les analyses non human-validated.

    Trié par overall_score croissant (les pires en premier) puis date.
    Utile pour UI desk panel "Recipes Quality Review" (Phase 3c-2 V2).
    """
    async with aiosqlite.connect(_DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cur = await db.execute(
            """SELECT * FROM recipe_analyses_index
               WHERE human_validated = 0 AND status = 'success'
               ORDER BY overall_score ASC, analyzed_at DESC
               LIMIT ?""",
            (limit,),
        )
        rows = await cur.fetchall()
        return [dict(r) for r in rows]


async def recipe_analyses_mark_validated(
    rowid: int, validator: str, notes: str | None = None,
) -> bool:
    """Admin valide manuellement une analyse (V2 UI review)."""
    import time as _t
    async with aiosqlite.connect(_DB_PATH) as db:
        cur = await db.execute(
            """UPDATE recipe_analyses_index
               SET human_validated = 1,
                   human_validator = ?,
                   human_validated_at = ?
               WHERE rowid = ?""",
            (validator, int(_t.time()), rowid),
        )
        await db.commit()
        return cur.rowcount > 0


# ── Sprint Composants Phase 4a : CRUD agent_analyses_index ───────────────────
# Pattern strict recipe_analyses_index : INSERT-only audit trail. Cache key
# (sid, config_hash). Le contenu Pydantic complet sur PVC, l'index DB garde
# metadata + breakdown statistique pour queries admin.

async def agent_analyses_insert(
    sid: str, config_hash: str, owner: str, profile: str, audience: str,
    analyzer_model: str, file_path: str,
    overall_score: float,
    n_params: int = 0, n_warnings: int = 0, n_errors: int = 0,
    status: str = "success", error_detail: str | None = None,
    analyzer_version: int = 1,
) -> int:
    """Insert nouvelle row agent_analyses_index. INSERT-only."""
    import time as _t
    async with aiosqlite.connect(_DB_PATH) as db:
        cur = await db.execute(
            """INSERT INTO agent_analyses_index
               (sid, config_hash, owner, profile, audience, analyzer_model,
                analyzer_version, analyzed_at, file_path, overall_score,
                n_params, n_warnings, n_errors, status, error_detail)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (sid, config_hash, owner, profile, audience, analyzer_model,
             analyzer_version, int(_t.time()), file_path, overall_score,
             n_params, n_warnings, n_errors, status, error_detail),
        )
        await db.commit()
        return cur.lastrowid or 0


async def agent_analyses_get_latest(
    sid: str, config_hash: str | None = None,
) -> dict | None:
    """Retourne la derniere analyse matching (sid, [config_hash]).

    Si config_hash fourni : exact match (cache lookup). Sinon : derniere
    analyse connue pour cette etude (admin review).
    """
    async with aiosqlite.connect(_DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        if config_hash:
            cur = await db.execute(
                """SELECT * FROM agent_analyses_index
                   WHERE sid = ? AND config_hash = ?
                   ORDER BY rowid DESC LIMIT 1""",
                (sid, config_hash),
            )
        else:
            cur = await db.execute(
                """SELECT * FROM agent_analyses_index
                   WHERE sid = ?
                   ORDER BY rowid DESC LIMIT 1""",
                (sid,),
            )
        row = await cur.fetchone()
        return dict(row) if row else None


# ── Sprint Composants-1 : CRUD exports_index ─────────────────────────────────
# Pattern recipes_index : INSERT-only, audit trail. Track les exports generes
# (Grist .grist, GPKG, ZIP composite, etc.) avec type + metadata + scene_hash
# pour faire le lien avec les versions de Scene Manifest.

async def exports_insert(
    pid: str, sid: str, owner: str,
    export_type: str, filename: str, file_path: str,
    size_bytes: int = 0,
    scene_hash: str = "",
    n_tables: int | None = None,
    n_records: int | None = None,
    extra_json: str = "",
) -> int:
    """Insert nouvelle row exports_index. INSERT-only (pas d'UPDATE)."""
    import time as _t
    async with aiosqlite.connect(_DB_PATH) as db:
        cur = await db.execute(
            """INSERT INTO exports_index
               (pid, sid, owner, export_type, filename, file_path, size_bytes,
                scene_hash, n_tables, n_records, extra_json, status, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'active', ?)""",
            (pid, sid, owner, export_type, filename, file_path, size_bytes,
             scene_hash or None, n_tables, n_records,
             extra_json or None, int(_t.time())),
        )
        await db.commit()
        return cur.lastrowid or 0


async def exports_list(pid: str, export_type: str | None = None) -> list[dict]:
    """Liste les exports d'un projet, plus recents d'abord.
    Filtre optionnel par type (grist, gpkg, zip_composite)."""
    async with aiosqlite.connect(_DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        if export_type:
            cur = await db.execute(
                """SELECT * FROM exports_index
                   WHERE pid = ? AND export_type = ? AND status = 'active'
                   ORDER BY created_at DESC""",
                (pid, export_type),
            )
        else:
            cur = await db.execute(
                """SELECT * FROM exports_index
                   WHERE pid = ? AND status = 'active'
                   ORDER BY created_at DESC""",
                (pid,),
            )
        rows = await cur.fetchall()
        return [dict(r) for r in rows]


async def exports_archive(rowid: int) -> int:
    """Soft delete : status='archived'. Le fichier reste sur PVC."""
    async with aiosqlite.connect(_DB_PATH) as db:
        cur = await db.execute(
            "UPDATE exports_index SET status='archived' WHERE rowid=?",
            (rowid,),
        )
        await db.commit()
        return cur.rowcount


# ── Sprint Composants-1 : CRUD scene_manifest_index ─────────────────────────
# Pattern strictement parallele aux recipes_index : INSERT-only, audit trail,
# lookup latest via MAX(version_num). Le contenu JSON Scene Manifest V0.2 vit
# sur le PVC dans projects/{pid}/scene_manifest.json. Cette table track les
# metadonnees pour faciliter le versioning et l'audit.

async def scene_manifest_insert(
    pid: str, sid: str, owner: str, scene_hash: str,
    n_layers: int = 0, size_bytes: int = 0,
    previous_hash: str = "",
    manifest_version: str = "V0.2",
) -> int:
    """Insert nouvelle row scene_manifest_index. Auto-increment version_num.

    Pattern recipes_index : pas de UPDATE, INSERT seulement. La row precedente
    reste en DB (audit), seule la version_num la plus elevee est consideree
    'active' par scene_manifest_get_latest.
    """
    import time as _t
    async with aiosqlite.connect(_DB_PATH) as db:
        cur = await db.execute(
            "SELECT MAX(version_num) FROM scene_manifest_index WHERE pid = ?",
            (pid,),
        )
        row = await cur.fetchone()
        next_v = (row[0] or 0) + 1
        cur = await db.execute(
            """INSERT INTO scene_manifest_index
               (pid, sid, owner, scene_hash, previous_hash, version_num,
                manifest_version, n_layers, size_bytes, status, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'active', ?)""",
            (pid, sid, owner, scene_hash, previous_hash or None, next_v,
             manifest_version, n_layers, size_bytes, int(_t.time())),
        )
        await db.commit()
        return cur.lastrowid or 0


async def scene_manifest_get_latest(pid: str) -> dict | None:
    """Retourne la derniere version active du Scene Manifest pour un projet.
    None si aucune version enregistree."""
    async with aiosqlite.connect(_DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cur = await db.execute(
            """SELECT * FROM scene_manifest_index
               WHERE pid = ? AND status = 'active'
               ORDER BY version_num DESC LIMIT 1""",
            (pid,),
        )
        row = await cur.fetchone()
        return dict(row) if row else None


async def scene_manifest_history(pid: str) -> list[dict]:
    """Toutes les versions (actives + archivees) pour un projet, plus recent
    d'abord. Permet l'audit complet ou la restauration manuelle."""
    async with aiosqlite.connect(_DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cur = await db.execute(
            """SELECT * FROM scene_manifest_index
               WHERE pid = ? ORDER BY version_num DESC""",
            (pid,),
        )
        rows = await cur.fetchall()
        return [dict(r) for r in rows]


async def scene_manifest_archive(pid: str) -> int:
    """Soft delete : marque toutes les versions du manifest comme archived.
    Le fichier JSON reste sur PVC pour audit / restauration."""
    async with aiosqlite.connect(_DB_PATH) as db:
        cur = await db.execute(
            "UPDATE scene_manifest_index SET status='archived' WHERE pid=?",
            (pid,),
        )
        await db.commit()
        return cur.rowcount


def scene_manifest_path(sid: str, pid: str) -> str:
    """Chemin canonique du fichier Scene Manifest JSON sur PVC workspace."""
    return f"/data/studies/{sid}/projects/{pid}/scene_manifest.json"


# ── Sprint UX-3 : CRUD study_projects (1 etude -> N projets QGIS) ─────────────
# Pattern strictement parallele au CRUD studies existant (create/get/list/touch/
# update/archive + active_*). Cf. block etudes plus haut pour la conv naming.

async def create_project(
    sid: str,
    owner: str,
    label: str,
    is_default: bool = False,
) -> dict:
    """Cree un projet dans l'etude. Si is_default=True, on UNSET le precedent
    is_default de l'etude (1 unique par sid).

    Le qgz_path et history_path suivent le layout standardise :
      /data/studies/{sid}/projects/{pid}/project.qgz
      /data/studies/{sid}/projects/{pid}/history.jsonl
    """
    pid = _new_id()
    now = int(time.time())
    label_safe = label.strip()[:120] or "Projet sans nom"
    project = {
        "pid":          pid,
        "sid":          sid,
        "owner":        owner,
        "label":        label_safe,
        "qgz_path":     f"/data/studies/{sid}/projects/{pid}/project.qgz",
        "history_path": f"/data/studies/{sid}/projects/{pid}/history.jsonl",
        "created_at":   now,
        "last_active":  now,
        "is_default":   1 if is_default else 0,
        "status":       "active",
    }
    async with aiosqlite.connect(_DB_PATH) as db:
        if is_default:
            # UNSET tout autre is_default de cette etude (1 unique principal).
            await db.execute(
                "UPDATE study_projects SET is_default = 0 WHERE sid = ? AND is_default = 1",
                (sid,),
            )
        await db.execute("""
            INSERT INTO study_projects
            (pid, sid, owner, label, qgz_path, history_path,
             created_at, last_active, is_default, status)
            VALUES (:pid, :sid, :owner, :label, :qgz_path, :history_path,
                    :created_at, :last_active, :is_default, :status)
        """, project)
        await db.commit()
    return project


async def get_project(pid: str, owner: str | None = None) -> dict | None:
    async with aiosqlite.connect(_DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        row = await (await db.execute(
            "SELECT * FROM study_projects WHERE pid = ?", (pid,)
        )).fetchone()
    if not row:
        return None
    p = dict(row)
    if owner and p["owner"] != owner:
        return None
    return p


async def list_projects(sid: str, include_archived: bool = False) -> list[dict]:
    """Liste les projets d'une etude. Tri last_active DESC -> dernier ouvert
    en premier (pratique UX 'dernier projet chronologique')."""
    async with aiosqlite.connect(_DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        if include_archived:
            rows = await (await db.execute(
                "SELECT * FROM study_projects WHERE sid = ? "
                "ORDER BY is_default DESC, last_active DESC",
                (sid,)
            )).fetchall()
        else:
            rows = await (await db.execute(
                "SELECT * FROM study_projects WHERE sid = ? AND status = 'active' "
                "ORDER BY is_default DESC, last_active DESC",
                (sid,)
            )).fetchall()
    return [dict(r) for r in rows]


async def touch_project(pid: str) -> None:
    async with aiosqlite.connect(_DB_PATH) as db:
        await db.execute(
            "UPDATE study_projects SET last_active = ? WHERE pid = ?",
            (int(time.time()), pid),
        )
        await db.commit()


async def update_project(pid: str, **fields) -> dict | None:
    """Mise a jour partielle. Champs autorises : label, is_default, status."""
    allowed = {"label", "is_default", "status"}
    fields = {k: v for k, v in fields.items() if k in allowed}
    if not fields:
        return await get_project(pid)
    # is_default special : si on set is_default=1, UNSET tous les autres de l'etude
    if fields.get("is_default") == 1:
        p = await get_project(pid)
        if p:
            async with aiosqlite.connect(_DB_PATH) as db:
                await db.execute(
                    "UPDATE study_projects SET is_default = 0 "
                    "WHERE sid = ? AND pid <> ?",
                    (p["sid"], pid),
                )
                await db.commit()
    setters = ", ".join(f"{k} = ?" for k in fields)
    values = list(fields.values()) + [pid]
    async with aiosqlite.connect(_DB_PATH) as db:
        await db.execute(
            f"UPDATE study_projects SET {setters} WHERE pid = ?", values
        )
        await db.commit()
    return await get_project(pid)


async def archive_project(pid: str) -> None:
    """Archive (soft delete) un projet. Files restent sur PVC."""
    async with aiosqlite.connect(_DB_PATH) as db:
        await db.execute(
            "UPDATE study_projects SET status = 'archived' WHERE pid = ?", (pid,)
        )
        await db.commit()


async def get_default_project(sid: str) -> dict | None:
    """Retourne le projet 'principal' (is_default=1) ou le plus recent en
    fallback (last_active DESC). Utile pour 'ouvrir l'etude' = ouvrir le
    projet le plus pertinent."""
    async with aiosqlite.connect(_DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cur = await db.execute(
            "SELECT * FROM study_projects WHERE sid = ? AND status = 'active' "
            "ORDER BY is_default DESC, last_active DESC LIMIT 1",
            (sid,)
        )
        row = await cur.fetchone()
    return dict(row) if row else None


async def count_projects(sid: str, include_archived: bool = False) -> int:
    """Compte les projets d'une etude (pour badge UI 'N projets')."""
    async with aiosqlite.connect(_DB_PATH) as db:
        if include_archived:
            cur = await db.execute(
                "SELECT COUNT(*) FROM study_projects WHERE sid = ?", (sid,)
            )
        else:
            cur = await db.execute(
                "SELECT COUNT(*) FROM study_projects "
                "WHERE sid = ? AND status = 'active'",
                (sid,)
            )
        row = await cur.fetchone()
    return row[0] if row else 0


# ── Projet actif (par owner) ──────────────────────────────────────────────────
# Parallele a active_study : 1 row par owner avec pid courant. Set par les
# endpoints activate_project + cascade via activate_study (le projet is_default
# de l'etude devient actif).

async def get_active_project_id(owner: str) -> str | None:
    async with aiosqlite.connect(_DB_PATH) as db:
        row = await (await db.execute(
            "SELECT pid FROM active_project WHERE owner = ?", (owner,)
        )).fetchone()
    return row[0] if row else None


async def set_active_project(owner: str, pid: str) -> None:
    async with aiosqlite.connect(_DB_PATH) as db:
        await db.execute(
            "INSERT OR REPLACE INTO active_project (owner, pid) VALUES (?, ?)",
            (owner, pid),
        )
        await db.commit()


async def get_or_create_default(owner: str, profile: str = "standard") -> dict:
    """
    Récupère l'étude active de l'user, sinon en crée une par défaut.
    Utilisé en fallback quand un endpoint a besoin d'une étude mais l'user
    n'en a jamais créé explicitement.
    """
    active_id = await get_active_study_id(owner)
    if active_id:
        s = await get_study(active_id, owner)
        if s and s["status"] == "active":
            return s

    studies = await list_studies(owner)
    if studies:
        s = studies[0]
        await set_active_study(owner, s["id"])
        return s

    # Aucune étude → en créer une par défaut
    s = await create_study(owner, name="Étude par défaut", profile=profile)
    await set_active_study(owner, s["id"])
    return s


# ── Helpers PVC layout (executés côté pod via execute_python) ─────────────────

def study_data_dir(sid: str) -> str:
    """Chemin du dossier de l'étude sur le PVC workspace."""
    return f"/data/studies/{sid}"


def study_treatments_path(sid: str) -> str:
    return f"/data/studies/{sid}/treatments.jsonl"


def study_exports_dir(sid: str) -> str:
    return f"/data/studies/{sid}/exports"


def study_recipes_dir(sid: str) -> str:
    """V1.5 Sprint 1 : dossier recipes user de l'etude sur le PVC workspace."""
    return f"/data/studies/{sid}/recipes"


# ── Helpers recipes pod-side (V1.5 Sprint 1) ──────────────────────────────────
# Pattern : clone de init_pod_layout_code, generent du code Python a executer
# cote pod workspace via execute_python. Le pod a un acces direct au PVC, donc
# read/write/list/delete = primitives Path simples + markers stdout pour
# remonter le resultat au hub via le bridge UNIX socket.

def save_recipe_pod_code(sid: str, slug: str, content: str, fmt: str = "yaml") -> str:
    """Code Python pour ecrire une recipe user sur le PVC workspace.

    Le content est inject literal via repr -> safe contre injection (Python
    parse une seule string, pas d'eval). Cree le dossier recipes/ si absent.
    Retourne le SHA256 du contenu via marker stdout pour audit hub-side.
    """
    return f"""
from pathlib import Path
import hashlib
sid = {sid!r}
slug = {slug!r}
fmt = {fmt!r}
content = {content!r}
recipes_dir = Path(f"/data/studies/{{sid}}/recipes")
recipes_dir.mkdir(parents=True, exist_ok=True)
# Strip extensions parasites du slug (safety)
clean_slug = slug.replace("/", "_").replace("..", "_")
target = recipes_dir / f"{{clean_slug}}.{{fmt}}"
target.write_text(content, encoding="utf-8")
sha = hashlib.sha256(content.encode("utf-8")).hexdigest()
print(f"RECIPE_SAVE_OK sid={{sid}} slug={{clean_slug}} fmt={{fmt}} sha={{sha}} path={{target}}")
"""


def read_recipe_pod_code(sid: str, slug: str) -> str:
    """Code Python pour lire une recipe user depuis le PVC workspace.

    Cherche .yaml puis .yml puis .json (priorite YAML cote user). Retourne le
    contenu encode base64 via marker stdout (pour eviter probleme escape multi-
    ligne dans la lecture stdout JSON cote hub). Le hub decode + retourne au
    client.
    """
    return f"""
from pathlib import Path
import base64
sid = {sid!r}
slug = {slug!r}
recipes_dir = Path(f"/data/studies/{{sid}}/recipes")
found = None
for ext in (".yaml", ".yml", ".json"):
    candidate = recipes_dir / f"{{slug}}{{ext}}"
    if candidate.exists():
        found = candidate
        break
if found is None:
    print(f"RECIPE_READ_NOT_FOUND sid={{sid}} slug={{slug}}")
else:
    content = found.read_text(encoding="utf-8")
    b64 = base64.b64encode(content.encode("utf-8")).decode("ascii")
    print(f"RECIPE_READ_OK sid={{sid}} slug={{slug}} fmt={{found.suffix.lstrip('.')}} b64={{b64}}")
"""


def list_recipes_pod_code(sid: str) -> str:
    """Code Python pour lister les recipes user d'une etude (PVC).

    Skip les fichiers `.archived.<ts>` (soft delete) : pattern reconnu par
    delete_recipe_pod_code = `{slug}.archived.<ts>{ext}`. Sans ce filtrage,
    les archived apparaissent dans la liste UI/agent -> confusion user.
    """
    return f"""
from pathlib import Path
import json, hashlib
sid = {sid!r}
recipes_dir = Path(f"/data/studies/{{sid}}/recipes")
out = []
if recipes_dir.is_dir():
    for ext in ("*.yaml", "*.yml", "*.json"):
        for p in sorted(recipes_dir.glob(ext)):
            # Skip archived (soft delete : `{{slug}}.archived.<ts>{{ext}}`)
            if ".archived." in p.name:
                continue
            try:
                content = p.read_text(encoding="utf-8")
                out.append({{
                    "slug": p.stem,
                    "format": p.suffix.lstrip("."),
                    "size": len(content),
                    "sha": hashlib.sha256(content.encode("utf-8")).hexdigest(),
                }})
            except Exception:
                pass
print("RECIPE_LIST_OK " + json.dumps(out, ensure_ascii=False))
"""


def delete_recipe_pod_code(sid: str, slug: str) -> str:
    """Code Python pour supprimer une recipe user sur le PVC workspace.

    Soft delete : on renomme en `{slug}.archived.{ext}` plutot que rm direct,
    permet restauration manuelle si besoin (audit trail).
    """
    return f"""
from pathlib import Path
import time
sid = {sid!r}
slug = {slug!r}
recipes_dir = Path(f"/data/studies/{{sid}}/recipes")
removed = []
for ext in (".yaml", ".yml", ".json"):
    p = recipes_dir / f"{{slug}}{{ext}}"
    if p.exists():
        ts = int(time.time())
        archived = recipes_dir / f"{{slug}}.archived.{{ts}}{{ext}}"
        p.rename(archived)
        removed.append(str(archived))
print(f"RECIPE_DELETE_OK sid={{sid}} slug={{slug}} archived={{removed}}")
"""


def init_pod_layout_code(sid: str, name: str, profile: str) -> str:
    """
    Code Python à exécuter sur le pod workspace pour initialiser le layout
    de l'étude (dossiers + meta.json + .active_study).
    """
    return f"""
from pathlib import Path
import json
sid = {sid!r}
study_dir = Path(f"/data/studies/{{sid}}")
for sub in ("exports/storymaps", "exports/pdf", "exports/data"):
    (study_dir / sub).mkdir(parents=True, exist_ok=True)
meta = {{
    "id":      sid,
    "name":    {name!r},
    "profile": {profile!r},
}}
(study_dir / "meta.json").write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
# Bootstrap notes vide si absent
notes_p = study_dir / "notes.md"
if not notes_p.exists():
    notes_p.write_text(f"# {{meta['name']}}\\n\\nNotes de l'étude.\\n", encoding="utf-8")
print(f"STUDY_INIT_OK sid={{sid}} dir={{study_dir}}")
"""


def activate_pod_code(sid: str) -> str:
    """
    Code Python pour activer l'étude côté pod :
    1. Écrit le sentinel /data/.active_study (lu par audit_trail)
    2. Charge le projet QGIS de l'étude dans le Bureau si .qgz existe
    3. Maximise QGIS sur Xvfb pour éviter le rendu en petit
    """
    return f"""
import subprocess
from pathlib import Path
sid = {sid!r}
Path("/data/.active_study").write_text(sid, encoding="utf-8")
print(f"ACTIVE_STUDY={{sid}}")

# V1.5 Sprint 1 : symlink /data/studies/active/recipes -> {{sid}}/recipes
# pour que BigQgisMCP qgis_bridge.py voie les recipes user de l'etude active
# via USER_RECIPES_DIR=/data/studies/active/recipes (env injected via STS
# extra_env). Le symlink est re-cree a chaque activation -> follow study switch.
try:
    recipes_dir = Path(f"/data/studies/{{sid}}/recipes")
    recipes_dir.mkdir(parents=True, exist_ok=True)
    active_link = Path("/data/studies/active")
    if active_link.is_symlink() or active_link.exists():
        active_link.unlink(missing_ok=True)
    active_link.symlink_to(f"/data/studies/{{sid}}")
    print(f"ACTIVE_STUDY_SYMLINK ok -> {{sid}}")
except Exception as e:
    print(f"ACTIVE_STUDY_SYMLINK warn: {{e}}")

qgz = Path(f"/data/studies/{{sid}}/project.qgz")

# Phase 12 : on ne récupère plus depuis /data/.autosave.qgz car ce fichier
# contient des chemins absolus volatils (/data/cache/...) qui causent les
# dialogs "Handle Unavailable Layers" au prochain restart pod. Le flux
# propre est désormais : smart_load → save_active_pod_code adopte dans
# {{sid}}/data/ → .qgz portable. Pas de bonus magique.

# ── Anti-freeze : neutraliser les 2 dialogs modales bloquantes au load ────
# 1. "Handle Unavailable Layers" : QGIS pop une modale quand des couches du
#    .qgz référencent des sources introuvables (cache supprimé, gpkg manquant).
#    Solution : BadLayerHandler silent qui drop les bad layers sans interaction.
# 2. "An error has occurred while executing Python code" : macros Python
#    embedded dans le .qgz qui crashent. Solution : désactiver l'exécution
#    des macros au load via QSettings.
try:
    from qgis.core import QgsProjectBadLayerHandler
    from qgis.PyQt.QtCore import QSettings

    class _SilentBadLayerHandler(QgsProjectBadLayerHandler):
        def __init__(self):
            super().__init__()
            self.dropped = []
        def handleBadLayers(self, layers):
            # Pas de dialog. On log la liste pour observabilité.
            for el in layers:
                try:
                    name = el.namedItem("layername").toElement().text()
                    self.dropped.append(name)
                except Exception:
                    pass

    _bad_handler = _SilentBadLayerHandler()

    # Désactive l'exécution automatique des macros embedded dans le .qgz.
    # Évite "An error has occurred while executing Python code" si une macro
    # référence du code obsolète. L'utilisateur peut les réactiver à la main.
    _settings = QSettings()
    _settings.setValue("qgis/enableMacros", 0)  # 0 = Never, 1 = Ask, 2 = Always
    _settings.setValue("Qgis/askToSaveProjectChanges", False)
    # Sprint UX-3 fix (2026-06-21) : forcer locale FR pour le user CEREMA.
    # 3 cles QSettings que QGIS lit au demarrage / changement de locale :
    # - locale/userLocale : la valeur principale (ex: 'fr')
    # - locale/overrideFlag : True pour ignorer la locale OS et utiliser userLocale
    # - locale/globalLocale : optionnel, pour les contenus 'global' (ex: aide)
    # QGIS sait gerer le hot-reload partiel (menu, dialogs) sans restart du
    # process, MAIS les widgets deja crees gardent leur libelle anglais ->
    # pour un effet complet il faut redemarrer QGIS. Acceptable car la
    # 1ere activation post-deploy redemarre le pod de toute facon.
    _settings.setValue("locale/userLocale", "fr")
    _settings.setValue("locale/overrideFlag", True)
    _settings.setValue("locale/globalLocale", "fr")
    _settings.sync()
except Exception as _exc:
    print(f"PROJECT_SAFETY_SETUP_ERR {{_exc}}")
    _bad_handler = None

if qgz.exists():
    try:
        from qgis.core import QgsProject
        proj = QgsProject.instance()
        # Sprint UX-3 optim (2026-06-21) : skip le re-read si le projet est
        # deja charge avec le bon path ET au moins 1 couche. Evite le triple
        # load typique : activate_study -> activate_project chained -> wake
        # background _auto_activate. Chacun re-read = ~3-5s + canvas flicker
        # disgracieux. Idempotent au pattern, garde le filename canonique.
        current = (proj.fileName() or "").strip()
        n_layers_existing = len(proj.mapLayers())
        if current == str(qgz) and n_layers_existing > 0:
            print(f"PROJECT_ALREADY_LOADED path={{qgz}} n_layers={{n_layers_existing}}")
            ok = True
        else:
            if _bad_handler is not None:
                proj.setBadLayerHandler(_bad_handler)
            ok = proj.read(str(qgz))
            # read() positionne déjà fileName() ; on s'assure que le projet pointe
            # bien sur le path canonique de l'étude (pas un autre chemin résolu).
            proj.setFileName(str(qgz))
        try:
            from qgis.utils import iface
            if iface is not None:
                # Auto-zoom sur l'étendue de toutes les couches visibles : sans ça
                # le canvas reste sur l'extent par défaut (souvent vide) et l'user
                # voit Layers ✓ mais map blanche → confusion.
                try:
                    iface.zoomToActiveLayer()
                except Exception:
                    pass
                try:
                    iface.mapCanvas().zoomToFullExtent()
                except Exception:
                    pass
                iface.mapCanvas().refresh()
        except Exception:
            pass
        dropped_info = ""
        if _bad_handler is not None and getattr(_bad_handler, "dropped", None):
            dropped_info = f" bad_layers_dropped={{_bad_handler.dropped}}"
        print(f"PROJECT_LOADED ok={{ok}} path={{qgz}}{{dropped_info}}")
    except Exception as exc:
        print(f"PROJECT_LOAD_ERR {{exc}}")
else:
    # Pas de .qgz et pas d'autosave récent : nouveau projet vide. On fixe le
    # fileName ET on persiste un .qgz vide sur disque, sinon QGIS considère
    # qu'aucun projet n'est ouvert et affiche le Welcome screen ("New Empty
    # Project" card) au lieu du workspace projet. Cf. retour user E2E 2026-06-02 :
    # apres creation d'etude QGIS restait sur Welcome alors que techniquement
    # le projet etait clear+bind.
    try:
        from qgis.core import QgsProject
        QgsProject.instance().clear()
        qgz.parent.mkdir(parents=True, exist_ok=True)
        QgsProject.instance().setFileName(str(qgz))
        # Bug B v3 (2026-06-02) : write() persiste le .qgz vide sur disque.
        wrote = QgsProject.instance().write()
        # Bug B v3 complement (2026-06-07) : write() seul ne ferme pas le
        # Welcome widget QGIS qui flotte sur le canvas. Il faut OUVRIR
        # explicitement le projet via iface.addProject() pour que QGIS
        # dismiss le Welcome et active la barre de titre + tabs projet.
        # iface.zoomToFullExtent() seul ne suffit pas car il agit sur le
        # canvas (deja vide) sans toucher au widget Welcome au-dessus.
        try:
            from qgis.utils import iface
            if iface is not None:
                # iface.addProject(qgz) charge le projet comme si l'user
                # avait fait File -> Open Project. Cela ferme le Welcome
                # widget et bascule l'UI en mode "projet ouvert".
                try:
                    iface.addProject(str(qgz))
                except Exception as _open_exc:
                    print(f"NEW_PROJECT_ADD_PROJECT_ERR {{_open_exc}}")
                # zoomToFullExtent + refresh derriere pour assurer un canvas
                # affiche meme si addProject n'a pas trigger un redraw.
                try:
                    iface.mapCanvas().zoomToFullExtent()
                    iface.mapCanvas().refresh()
                except Exception:
                    pass
        except Exception as _iface_exc:
            print(f"NEW_PROJECT_IFACE_REFRESH_ERR {{_iface_exc}}")
        print(f"PROJECT_CREATED_AND_SAVED path={{qgz}} wrote={{wrote}}")
    except Exception as exc:
        print(f"NEW_PROJECT_ERR {{exc}}")

# Filet de sécurité : fermer toute dialog modale survivante via xdotool.
# Couvre les cas où la modale apparaît malgré nos précautions (timing,
# autre source d'erreur Python embedded, etc.).
try:
    res = subprocess.run(["xdotool", "search", "--name",
                          "Handle Unavailable Layers|error has occurred"],
                         env={{"DISPLAY": ":99"}}, capture_output=True,
                         text=True, timeout=3)
    for w in [w for w in res.stdout.strip().split("\\n") if w]:
        subprocess.run(["xdotool", "windowclose", w],
                       env={{"DISPLAY": ":99"}}, timeout=2)
        print(f"DIALOG_CLOSED wid={{w}}")
except Exception:
    pass

# Maximise QGIS sur Xvfb. Le zoom-to-extent est géré côté PyQGIS plus haut
# (iface.mapCanvas().zoomToFullExtent()) ; ne PAS envoyer Ctrl+Shift+F via
# xdotool : c'est le raccourci "Data Source Manager" dans QGIS 3, pas Zoom Full.
try:
    res = subprocess.run(["xdotool", "search", "--name", "QGIS"],
                         env={{"DISPLAY": ":99"}}, capture_output=True, text=True, timeout=3)
    wids = [w for w in res.stdout.strip().split("\\n") if w]
    if wids:
        subprocess.run(["xdotool", "windowsize", wids[0], "100%", "100%"],
                       env={{"DISPLAY": ":99"}}, timeout=3)
        subprocess.run(["xdotool", "windowmove", wids[0], "0", "0"],
                       env={{"DISPLAY": ":99"}}, timeout=3)
except Exception:
    pass
"""


def save_active_pod_code(sid: str) -> str:
    """
    Code Python pour sauvegarder l'étude `sid` côté pod.

    Phase 12 — étude = bundle autoportant :
    AVANT le write(), on adopte les sources de données dans `{sid}/data/` :
    pour chaque couche dont la datasource pointe vers /data/cache/ (cache
    partagé, volatil), on copie le fichier dans /data/studies/{sid}/data/ et
    on relink la couche dessus. Le .qgz devient autoportant — même si le
    cache est nettoyé, l'étude reste rechargeable.
    """
    return f"""
import re
import shutil as _sh
from pathlib import Path
sid = {sid!r}
target = Path(f"/data/studies/{{sid}}/project.qgz")
data_dir = target.parent / "data"
try:
    from qgis.core import QgsProject, QgsDataSourceUri, QgsVectorLayer, QgsRasterLayer
    proj = QgsProject.instance()
    fname = proj.fileName() or ""
    n_layers = len(proj.mapLayers())
    dirty = proj.isDirty() if hasattr(proj, "isDirty") else True

    # ── Adoption des sources /data/cache/ → /data/studies/{{sid}}/data/ ──
    adopted = []
    failed = []
    if n_layers > 0:
        data_dir.mkdir(parents=True, exist_ok=True)
        # Snapshot des layers pour itérer sans muter la collection en boucle.
        layers_snapshot = list(proj.mapLayers().values())
        for layer in layers_snapshot:
            src = layer.source() or ""
            # source format gpkg: "/data/cache/foo.gpkg|layername=bar"
            # ou simple: "/data/cache/foo.tif"
            m = re.match(r"^(/data/cache/[^|]+)(.*)$", src)
            if not m:
                continue
            cache_path = Path(m.group(1))
            suffix = m.group(2)  # "" ou "|layername=..."
            if not cache_path.exists():
                failed.append({{"name": layer.name(), "src": src, "reason": "cache missing"}})
                continue
            adopted_path = data_dir / cache_path.name
            if not adopted_path.exists():
                try:
                    _sh.copy2(str(cache_path), str(adopted_path))
                except Exception as exc:
                    failed.append({{"name": layer.name(), "reason": f"copy fail: {{exc}}"}})
                    continue
            new_src = f"{{adopted_path}}{{suffix}}"
            # Re-link la couche sur la nouvelle source. setDataSource est le
            # moyen recommandé en QGIS 3.x.
            try:
                provider = layer.dataProvider().name()
                layer.setDataSource(new_src, layer.name(), provider)
                adopted.append({{"name": layer.name(), "from": str(cache_path),
                                 "to": str(adopted_path)}})
            except Exception as exc:
                failed.append({{"name": layer.name(), "reason": f"setDataSource fail: {{exc}}"}})

    # ── Write project ──
    if n_layers > 0 or not target.exists():
        target.parent.mkdir(parents=True, exist_ok=True)
        proj.setFileName(str(target))
        # Chemins relatifs au .qgz pour rendre le bundle portable.
        # (data/ est sibling de project.qgz dans {{sid}}/)
        try:
            proj.writeEntry("Paths", "Absolute", False)
        except Exception:
            pass
        ok = proj.write(str(target))
        print(
            f"STUDY_SAVE_OK sid={{sid}} ok={{ok}} n_layers={{n_layers}} "
            f"adopted={{len(adopted)}} failed={{len(failed)}}"
        )
        if adopted:
            print(f"  adopted_layers={{[a['name'] for a in adopted]}}")
        if failed:
            print(f"  failed_adoptions={{failed}}")
    else:
        print(f"STUDY_SAVE_SKIP sid={{sid}} n_layers=0 target_exists={{target.exists()}}")
except Exception as exc:
    print(f"STUDY_SAVE_ERR {{exc}}")
"""


def snapshot_active_pod_code(sid: str, checkpoint_id: str, tool_name: str) -> str:
    """Code Python pour snapshot le projet QGIS courant AVANT un tool mutating.

    Stratégie : write du .qgz dans `/data/studies/{sid}/.checkpoints/{ckpt}.qgz`,
    plus léger que save_active_pod_code car PAS d'adoption des sources cache —
    le but est de figer rapidement l'état pour permettre un rollback, pas de
    produire un bundle autoportant. Si rollback est demandé, on relit ce .qgz
    sur le pod via le pattern proj.read() existant (cf. activate_pod_code).
    """
    return f"""
from pathlib import Path
sid = {sid!r}
ckpt_id = {checkpoint_id!r}
tool_name = {tool_name!r}
ckpt_dir = Path(f"/data/studies/{{sid}}/.checkpoints")
ckpt_dir.mkdir(parents=True, exist_ok=True)
target = ckpt_dir / f"{{ckpt_id}}.qgz"
try:
    from qgis.core import QgsProject
    proj = QgsProject.instance()
    n_layers = len(proj.mapLayers())
    if n_layers == 0 and not target.exists():
        # Pas de couches → snapshot vide quand même (placeholder), permet
        # un rollback "tout enlever" cohérent. proj.write crée un .qgz minimal.
        proj.setFileName(str(target))
        try:
            proj.writeEntry("Paths", "Absolute", False)
        except Exception:
            pass
        ok = proj.write(str(target))
        print(f"CKPT_SAVE_OK id={{ckpt_id}} tool={{tool_name}} n_layers=0 ok={{ok}}")
    else:
        # Snapshot l'état courant. On ne modifie PAS le fileName du proj
        # (pour ne pas perturber save_active_pod_code suivant), donc on
        # passe via writeAsBinaryProject ou write avec un path explicite.
        prev_fname = proj.fileName() or ""
        proj.setFileName(str(target))
        try:
            proj.writeEntry("Paths", "Absolute", False)
        except Exception:
            pass
        ok = proj.write(str(target))
        # Restaurer le fileName d'origine pour ne pas perturber les saves
        # suivants qui s'attendent à pointer sur project.qgz, pas le snapshot.
        if prev_fname:
            proj.setFileName(prev_fname)
        print(f"CKPT_SAVE_OK id={{ckpt_id}} tool={{tool_name}} n_layers={{n_layers}} ok={{ok}}")
except Exception as exc:
    print(f"CKPT_SAVE_ERR {{exc}}")
"""


def restore_checkpoint_pod_code(sid: str, checkpoint_id: str) -> str:
    """Code Python pour restaurer un .qgz snapshot dans QGIS courant.

    Réutilise le pattern de activate_pod_code (BadLayerHandler silencieux +
    proj.read + auto-zoom). Le .qgz restauré écrase l'état courant.
    """
    return f"""
from pathlib import Path
sid = {sid!r}
ckpt_id = {checkpoint_id!r}
src = Path(f"/data/studies/{{sid}}/.checkpoints/{{ckpt_id}}.qgz")
if not src.exists():
    print(f"CKPT_RESTORE_ERR not_found {{src}}")
else:
    try:
        from qgis.core import QgsProject, QgsProjectBadLayerHandler

        class _SilentBadLayerHandler(QgsProjectBadLayerHandler):
            def __init__(self):
                super().__init__()
                self.dropped = []
            def handleBadLayers(self, layers):
                for el in layers:
                    try:
                        n = el.namedItem("layername").toElement().text()
                        self.dropped.append(n)
                    except Exception:
                        pass

        proj = QgsProject.instance()
        proj.setBadLayerHandler(_SilentBadLayerHandler())
        ok = proj.read(str(src))
        # On restaure le fileName canonique de l'étude (pas le snapshot path)
        # pour que les saves ultérieurs continuent d'écrire dans project.qgz.
        canonical = Path(f"/data/studies/{{sid}}/project.qgz")
        proj.setFileName(str(canonical))
        try:
            from qgis.utils import iface
            if iface is not None:
                canvas = iface.mapCanvas()
                if canvas is not None:
                    canvas.zoomToFullExtent()
        except Exception:
            pass
        print(f"CKPT_RESTORE_OK id={{ckpt_id}} ok={{ok}}")
    except Exception as exc:
        print(f"CKPT_RESTORE_ERR {{exc}}")
"""


# ── Sprint UX-3 : helpers pod-side projets (parallele etude) ──────────────────
# Pattern : meme strategie que create_pod_layout_code + activate_pod_code mais
# scope projet. Le hub n'a pas d'acces direct au PVC -> execute_python cote pod.

def create_project_pod_code(
    sid: str, pid: str, label: str, copy_from: str | None = None,
) -> str:
    """Cree le dossier projet + project.qgz + history.jsonl + meta.json.

    Si copy_from est defini (ex: /data/studies/{sid}/project.qgz legacy),
    copie le .qgz existant vers le nouveau path. Sinon le projet.qgz est cree
    a la 1ere save (laisse vide pour l'instant). Cette fonction est appelee :
    - Lors de la creation explicite d'un projet (POST /studies/{sid}/projects)
    - Lors de la migration au startup (chaque etude existante -> 1 default)
    - Lors de l'activation si le dossier projet n'existe pas (lazy)
    """
    copy_from_repr = repr(copy_from) if copy_from else "None"
    return f"""
from pathlib import Path
import json, shutil as _sh, time
sid = {sid!r}
pid = {pid!r}
label = {label!r}
copy_from = {copy_from_repr}
proj_dir = Path(f"/data/studies/{{sid}}/projects/{{pid}}")
proj_dir.mkdir(parents=True, exist_ok=True)
(proj_dir / ".checkpoints").mkdir(exist_ok=True)
qgz = proj_dir / "project.qgz"
hist = proj_dir / "history.jsonl"
if not hist.exists():
    hist.touch()
meta_p = proj_dir / "meta.json"
if not meta_p.exists():
    meta_p.write_text(json.dumps({{
        "pid": pid, "sid": sid, "label": label,
        "created_at": int(time.time()),
    }}, ensure_ascii=False, indent=2), encoding="utf-8")
copied = False
if copy_from:
    src = Path(copy_from)
    if src.exists() and not qgz.exists():
        try:
            _sh.copy2(str(src), str(qgz))
            copied = True
        except Exception as exc:
            print(f"PROJECT_COPY_ERR {{exc}}")
print(f"PROJECT_CREATE_OK sid={{sid}} pid={{pid}} qgz_exists={{qgz.exists()}} copied={{copied}}")
"""


def activate_project_pod_code(sid: str, pid: str) -> str:
    """Active un projet : sentinel + symlink + load QGIS proj.

    En complement de activate_pod_code (etude) :
    - Sentinel /data/.active_project = pid
    - Symlink /data/studies/active/project_active -> projects/{pid}/
      (active_study symlink doit deja etre en place)
    - QGIS proj.read(projects/{pid}/project.qgz) avec BadLayerHandler silent
      (meme pattern que activate_pod_code)
    """
    return f"""
import subprocess
from pathlib import Path
sid = {sid!r}
pid = {pid!r}
Path("/data/.active_project").write_text(pid, encoding="utf-8")
print(f"ACTIVE_PROJECT={{pid}}")

# Symlink projets actif (dans /data/studies/active/ qui pointe deja sur {{sid}})
try:
    active_proj_link = Path("/data/studies/active/project_active")
    if active_proj_link.is_symlink() or active_proj_link.exists():
        active_proj_link.unlink(missing_ok=True)
    active_proj_link.symlink_to(f"/data/studies/{{sid}}/projects/{{pid}}")
    print(f"ACTIVE_PROJECT_SYMLINK ok -> {{sid}}/projects/{{pid}}")
except Exception as e:
    print(f"ACTIVE_PROJECT_SYMLINK warn: {{e}}")

# Lazy create du dossier projet si absent (idempotent)
proj_dir = Path(f"/data/studies/{{sid}}/projects/{{pid}}")
proj_dir.mkdir(parents=True, exist_ok=True)
(proj_dir / ".checkpoints").mkdir(exist_ok=True)
hist = proj_dir / "history.jsonl"
if not hist.exists():
    hist.touch()

# Load QGIS proj : meme pattern que activate_pod_code (BadLayerHandler silent)
qgz = proj_dir / "project.qgz"
try:
    from qgis.core import QgsProject, QgsProjectBadLayerHandler
    from qgis.PyQt.QtCore import QSettings

    class _SilentBadLayerHandler(QgsProjectBadLayerHandler):
        def __init__(self):
            super().__init__()
            self.dropped = []
        def handleBadLayers(self, layers):
            for el in layers:
                try:
                    name = el.namedItem("layername").toElement().text()
                    self.dropped.append(name)
                except Exception:
                    pass

    _bad_handler = _SilentBadLayerHandler()
    _settings = QSettings()
    _settings.setValue("qgis/enableMacros", 0)
    _settings.setValue("Qgis/askToSaveProjectChanges", False)
    # Sprint UX-3 fix (2026-06-21) : QGIS en francais (idem activate_pod_code).
    _settings.setValue("locale/userLocale", "fr")
    _settings.setValue("locale/overrideFlag", True)
    _settings.setValue("locale/globalLocale", "fr")
    _settings.sync()
except Exception as _exc:
    print(f"PROJECT_SAFETY_SETUP_ERR {{_exc}}")
    _bad_handler = None

# Sprint UX-3 lazy migration (2026-06-22) : si le nouveau path projects/{pid}/
# project.qgz n'existe pas MAIS que le legacy /data/studies/{{sid}}/project.qgz
# existe (etude creee avant le passage 1:N -> commit 674c153), on copie le
# legacy au nouveau path. Migration filesystem deferee au 1er activate (la DB
# row study_projects a ete creee au boot hub, mais la copie effective du .qgz
# attendait que le pod workspace soit UP pour s'executer).
# Le legacy est conserve en .migrated.<ts> pour audit.
import shutil as _sh
import time as _t
legacy_qgz = Path(f"/data/studies/{{sid}}/project.qgz")
if not qgz.exists() and legacy_qgz.exists() and legacy_qgz.is_file():
    try:
        sz = legacy_qgz.stat().st_size
        if sz > 0:
            qgz.parent.mkdir(parents=True, exist_ok=True)
            _sh.copy2(str(legacy_qgz), str(qgz))
            # Renomme le legacy pour eviter qu'un futur code le re-utilise.
            # On garde le fichier pour rollback / audit, mais hors du chemin
            # de lecture normal.
            _archive = legacy_qgz.with_suffix(f".qgz.migrated.{{int(_t.time())}}")
            try:
                legacy_qgz.rename(_archive)
            except Exception:
                pass
            print(f"LEGACY_QGZ_MIGRATED from={{legacy_qgz}} to={{qgz}} size={{sz}}")
        else:
            print(f"LEGACY_QGZ_EMPTY skip {{legacy_qgz}}")
    except Exception as exc:
        print(f"LEGACY_QGZ_MIGRATE_ERR {{exc}}")

if qgz.exists():
    try:
        from qgis.core import QgsProject
        proj = QgsProject.instance()
        # Sprint UX-3 optim (2026-06-21) : skip re-read si deja charge avec
        # le bon path + couches. Idem activate_pod_code -> evite double load
        # quand le chained activate_study->activate_project tombe sur le
        # meme projet (cas le plus courant : 1 projet default par etude).
        current = (proj.fileName() or "").strip()
        n_layers_existing = len(proj.mapLayers())
        if current == str(qgz) and n_layers_existing > 0:
            print(f"PROJECT_ALREADY_LOADED path={{qgz}} n_layers={{n_layers_existing}}")
            ok = True
        else:
            if _bad_handler is not None:
                proj.setBadLayerHandler(_bad_handler)
            ok = proj.read(str(qgz))
            proj.setFileName(str(qgz))
        try:
            from qgis.utils import iface
            if iface is not None:
                try:
                    iface.mapCanvas().zoomToFullExtent()
                except Exception:
                    pass
                iface.mapCanvas().refresh()
        except Exception:
            pass
        print(f"PROJECT_LOAD_OK ok={{ok}} path={{qgz}}")
    except Exception as exc:
        print(f"PROJECT_LOAD_ERR {{exc}}")
else:
    # Projet vide : on cree un .qgz placeholder pour eviter le Welcome widget
    # QGIS au load. Meme strategie que activate_pod_code legacy.
    #
    # Sprint UX-3 fix (2026-06-22) : ajout de iface.addProject() + canvas
    # refresh manquants. Sans ces appels, write() seul ne ferme pas le
    # Welcome widget QGIS qui flotte sur le canvas -> user voit 'QGIS sans
    # projet ouvert' avec la liste des projets recents au lieu d'un canvas
    # de nouveau projet vide propre. Pattern repris de activate_pod_code
    # legacy qui avait deja ce fix (Bug B v3 2026-06-02 + complement 2026-06-07).
    try:
        from qgis.core import QgsProject
        QgsProject.instance().clear()
        qgz.parent.mkdir(parents=True, exist_ok=True)
        QgsProject.instance().setFileName(str(qgz))
        try:
            QgsProject.instance().writeEntry("Paths", "Absolute", False)
        except Exception:
            pass
        wrote = QgsProject.instance().write()
        # Ferme le Welcome widget + bascule l'UI en mode 'projet ouvert'.
        # iface.addProject(qgz) charge le projet comme si user faisait
        # File -> Open Project, ce qui dismiss le Welcome.
        try:
            from qgis.utils import iface
            if iface is not None:
                try:
                    iface.addProject(str(qgz))
                except Exception as _open_exc:
                    print(f"NEW_PROJECT_ADD_PROJECT_ERR {{_open_exc}}")
                try:
                    iface.mapCanvas().zoomToFullExtent()
                    iface.mapCanvas().refresh()
                except Exception:
                    pass
        except Exception as _iface_exc:
            print(f"NEW_PROJECT_IFACE_REFRESH_ERR {{_iface_exc}}")
        print(f"PROJECT_CREATED_AND_SAVED path={{qgz}} wrote={{wrote}}")
    except Exception as exc:
        print(f"PROJECT_NEW_ERR {{exc}}")
"""


# ── Helpers FS path projets (parallele helpers etude) ─────────────────────────

def project_dir(sid: str, pid: str) -> str:
    """Chemin dossier projet sur PVC."""
    return f"/data/studies/{sid}/projects/{pid}"


def project_qgz_path(sid: str, pid: str) -> str:
    return f"/data/studies/{sid}/projects/{pid}/project.qgz"


def project_history_path(sid: str, pid: str) -> str:
    return f"/data/studies/{sid}/projects/{pid}/history.jsonl"


def project_checkpoints_dir(sid: str, pid: str) -> str:
    return f"/data/studies/{sid}/projects/{pid}/.checkpoints"


def build_scene_manifest_from_qgis_pod_code(sid: str, pid: str) -> str:
    """Code Python a executer cote pod workspace pour generer un Scene Manifest
    initial a partir des couches QGIS courantes.

    Sprint Composants-1 (2026-06-24) : sync auto QGIS -> StyleDeclarative.

    Strategie initiale (Sprint C-1) : un mapping basique kind=single + color
    par defaut + opacity 1.0 pour chaque couche QGIS. Les sprints suivants
    (C-2) exposeront un editeur form pour configurer kind/field/stops.

    Output stdout : marker JSON_MANIFEST suivi du JSON. Le hub recupere via
    parse stdout (pattern execute_python existant).
    """
    return f"""
from pathlib import Path
import json, uuid
sid = {sid!r}
pid = {pid!r}
try:
    from qgis.core import QgsProject, QgsMapLayerType
    proj = QgsProject.instance()
    layers = list(proj.mapLayers().values())
    manifest = {{
        "manifest_version": "V0.2",
        "manifest_id": str(uuid.uuid4()),
        "title": proj.title() or "Scene Manifest",
        "source": {{
            "project_qgs": str(proj.fileName() or ""),
            "study_id": sid,
            "project_id": pid,
        }},
        "layers": [],
    }}
    # Defaults StyleDeclarative single par couche (Sprint C-2 ajoutera form
    # editor pour personnaliser kind/field/stops).
    DEFAULT_COLORS = [
        "#1d70b8", "#d64d00", "#18753c", "#e1000f", "#6a6af4",
        "#a558a0", "#695b00", "#3558a2", "#b34000", "#005e6a",
    ]
    for i, layer in enumerate(layers):
        try:
            name = layer.name() or f"layer_{{i}}"
            slug = name.lower().replace(" ", "_").replace("/", "_")
            geom = "vector"
            if hasattr(layer, "type"):
                t = layer.type()
                if t == QgsMapLayerType.RasterLayer:
                    geom = "raster"
                elif t == QgsMapLayerType.VectorLayer:
                    geom = "vector"
            color = DEFAULT_COLORS[i % len(DEFAULT_COLORS)]
            layer_entry = {{
                "id": slug,
                "name": name,
                "geometry_type": geom,
                "visible": layer.isVisible() if hasattr(layer, "isVisible") else True,
                "style": {{
                    "qml_source": None,
                    "declarative": {{
                        "kind": "single",
                        "color": color,
                        "opacity": 1.0,
                    }},
                }},
            }}
            manifest["layers"].append(layer_entry)
        except Exception as _layer_exc:
            print(f"SCENE_MANIFEST_LAYER_ERR layer={{i}} err={{_layer_exc}}")
    # Persiste le fichier JSON sur PVC. Le hub valide et indexe ensuite.
    target = Path(f"/data/studies/{{sid}}/projects/{{pid}}/scene_manifest.json")
    target.parent.mkdir(parents=True, exist_ok=True)
    txt = json.dumps(manifest, ensure_ascii=False, indent=2)
    target.write_text(txt, encoding="utf-8")
    print(f"SCENE_MANIFEST_BUILT n_layers={{len(manifest['layers'])}} size={{len(txt)}}")
    print("<<<JSON_MANIFEST>>>" + txt + "<<<END>>>")
except Exception as exc:
    print(f"SCENE_MANIFEST_BUILD_ERR {{exc}}")
"""


def read_scene_manifest_pod_code(sid: str, pid: str) -> str:
    """Lit le fichier scene_manifest.json existant sur PVC, retourne le JSON
    via marker stdout. Si absent, retourne SCENE_MANIFEST_NOT_FOUND."""
    return f"""
from pathlib import Path
import base64
sid = {sid!r}
pid = {pid!r}
target = Path(f"/data/studies/{{sid}}/projects/{{pid}}/scene_manifest.json")
if not target.exists():
    print(f"SCENE_MANIFEST_NOT_FOUND sid={{sid}} pid={{pid}}")
else:
    content = target.read_text(encoding="utf-8")
    b64 = base64.b64encode(content.encode("utf-8")).decode("ascii")
    print(f"SCENE_MANIFEST_READ_OK b64={{b64}}")
"""


def write_scene_manifest_pod_code(sid: str, pid: str, content: str) -> str:
    """Ecrit (overwrite) le fichier scene_manifest.json sur PVC. Le hub
    fait la validation Pydantic + canonicalisation + calcul scene_hash AVANT
    d'appeler cette fonction. Retourne SHA256 via marker stdout."""
    return f"""
from pathlib import Path
import hashlib
sid = {sid!r}
pid = {pid!r}
content = {content!r}
target = Path(f"/data/studies/{{sid}}/projects/{{pid}}/scene_manifest.json")
target.parent.mkdir(parents=True, exist_ok=True)
target.write_text(content, encoding="utf-8")
sha = hashlib.sha256(content.encode("utf-8")).hexdigest()
print(f"SCENE_MANIFEST_WRITE_OK sha={{sha}} size={{len(content)}}")
"""


def purge_pod_layout_code(sid: str) -> str:
    """Code Python pour supprimer le dossier de l'étude sur le pod."""
    return f"""
import shutil
from pathlib import Path
sid = {sid!r}
study_dir = Path(f"/data/studies/{{sid}}")
if study_dir.exists():
    shutil.rmtree(study_dir, ignore_errors=True)
    print(f"PURGED sid={{sid}}")
else:
    print(f"NOT_FOUND sid={{sid}}")
# Si c'était l'étude active, désactiver
active_p = Path("/data/.active_study")
if active_p.exists() and active_p.read_text().strip() == sid:
    active_p.unlink()
    print("ACTIVE_CLEARED")
"""
