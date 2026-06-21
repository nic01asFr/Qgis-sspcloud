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
        await db.commit()


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
    try:
        from qgis.core import QgsProject
        QgsProject.instance().clear()
        QgsProject.instance().setFileName(str(qgz))
        try:
            QgsProject.instance().writeEntry("Paths", "Absolute", False)
        except Exception:
            pass
        wrote = QgsProject.instance().write()
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
