"""
agent.memory — Mémoire long-terme du client QGIS Agent.

Stocke sur PVC (/data/agent/) :
  - Profil utilisateur : zone habituelle, préférences, style cartographique
  - Historique sessions : analyses faites, livrables produits
  - Projets actifs : contexte géographique, couches chargées
  - Recettes apprises : workflows validés réutilisables
  - Cache données : zones déjà téléchargées depuis WFS/IGN

Architecture :
  SQLite pour les métadonnées et l'index
  Fichiers JSON pour les contenus longs (prompts, contextes)
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import time
from pathlib import Path
from typing import Any

import aiosqlite

log = logging.getLogger("agent.memory")

_DATA_DIR = Path(os.getenv("DATA_DIR", "/data/agent"))
_DB_PATH  = _DATA_DIR / "memory.db"


async def init() -> None:
    """Initialise la base de données mémoire."""
    _DATA_DIR.mkdir(parents=True, exist_ok=True)
    (_DATA_DIR / "sessions").mkdir(exist_ok=True)
    (_DATA_DIR / "projects").mkdir(exist_ok=True)
    (_DATA_DIR / "recipes").mkdir(exist_ok=True)

    async with aiosqlite.connect(_DB_PATH) as db:
        await db.executescript("""
            CREATE TABLE IF NOT EXISTS user_profile (
                key        TEXT PRIMARY KEY,
                value      TEXT NOT NULL,
                updated_at INTEGER NOT NULL
            );

            CREATE TABLE IF NOT EXISTS sessions (
                id          TEXT PRIMARY KEY,
                username    TEXT NOT NULL,
                profile_id  TEXT NOT NULL DEFAULT 'standard',
                started_at  INTEGER NOT NULL,
                ended_at    INTEGER,
                zone        TEXT,
                summary     TEXT,
                tags        TEXT,
                -- Lien session <-> etude active au moment de la conversation.
                -- NULL = exploration libre (pas d'etude). CHARTE §2 : "L'etude est
                -- l'unite qui traverse tout le cycle". Permet de retrouver la
                -- derniere conversation d'une etude au reload du desk.
                study_id    TEXT
            );
            CREATE INDEX IF NOT EXISTS idx_sessions_username_study
                ON sessions(username, study_id, started_at DESC);

            CREATE TABLE IF NOT EXISTS messages (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                session_id  TEXT NOT NULL,
                role        TEXT NOT NULL,
                content     TEXT NOT NULL,
                tool_calls  TEXT,
                created_at  INTEGER NOT NULL,
                FOREIGN KEY (session_id) REFERENCES sessions(id)
            );

            CREATE TABLE IF NOT EXISTS projects (
                id          TEXT PRIMARY KEY,
                name        TEXT NOT NULL,
                zone        TEXT,
                profile_id  TEXT,
                qgz_path    TEXT,
                created_at  INTEGER NOT NULL,
                updated_at  INTEGER NOT NULL,
                metadata    TEXT
            );

            CREATE TABLE IF NOT EXISTS recipes (
                id          TEXT PRIMARY KEY,
                name        TEXT NOT NULL,
                profile_id  TEXT,
                description TEXT,
                steps       TEXT NOT NULL,
                times_used  INTEGER DEFAULT 0,
                created_at  INTEGER NOT NULL,
                last_used   INTEGER
            );

            CREATE TABLE IF NOT EXISTS data_cache (
                key         TEXT PRIMARY KEY,
                zone_wkt    TEXT,
                source      TEXT NOT NULL,
                file_path   TEXT,
                expires_at  INTEGER NOT NULL,
                created_at  INTEGER NOT NULL
            );

            -- Phase 4 : couche 3 enrichie (mémoire user permanent, structurée)
            -- "Insights" = ce que l'agent a APPRIS sur l'user au fil du dialogue.
            -- L'user peut les consulter, les éditer, les supprimer (sous son contrôle).
            CREATE TABLE IF NOT EXISTS agent_insights (
                id         INTEGER PRIMARY KEY AUTOINCREMENT,
                username   TEXT NOT NULL DEFAULT 'user',
                key        TEXT NOT NULL,
                value      TEXT NOT NULL,
                source     TEXT NOT NULL DEFAULT 'implicit', -- 'implicit' (LLM) | 'explicit' (user)
                confidence REAL DEFAULT 0.7,                  -- 0..1, juge l'agent
                created_at INTEGER NOT NULL,
                last_seen  INTEGER NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_insights_username ON agent_insights(username);
            CREATE INDEX IF NOT EXISTS idx_insights_key ON agent_insights(key);

            -- Phase 5 : Checkpoints / Rollback agent (Commit B)
            -- Snapshot du projet QGIS pris avant chaque tool mutating
            -- (cf. _MUTATING_TOOLS dans qgis_agent.py). Permet à l'utilisateur
            -- de revenir en arrière à un état antérieur : restaure le .qgz +
            -- tronque les messages de la conversation après ce point.
            -- qgz_path est relatif au PVC user : /data/studies/{sid}/.checkpoints/
            CREATE TABLE IF NOT EXISTS checkpoints (
                id          TEXT PRIMARY KEY,
                session_id  TEXT NOT NULL,
                message_idx INTEGER NOT NULL,        -- index dans messages au moment du snapshot
                study_id    TEXT,                    -- étude active au snapshot (peut être NULL)
                qgz_path    TEXT NOT NULL,           -- relatif au PVC user
                tool_name   TEXT NOT NULL,           -- tool qui s'apprêtait à muter l'état
                audit_ts    REAL,                    -- cross-ref treatments.jsonl
                created_at  INTEGER NOT NULL,
                FOREIGN KEY (session_id) REFERENCES sessions(id)
            );
            CREATE INDEX IF NOT EXISTS idx_ckpt_session ON checkpoints(session_id, message_idx);
            CREATE INDEX IF NOT EXISTS idx_ckpt_created ON checkpoints(created_at);
        """)

        # Migration : pour les DB pre-existantes creees avant l'ajout de
        # sessions.study_id. Le CREATE TABLE ci-dessus ne s'applique qu'aux
        # bases neuves (IF NOT EXISTS). On detecte la colonne et l'ajoute si
        # absente, sans casser les donnees existantes.
        cols = [
            r[1] for r in
            await (await db.execute("PRAGMA table_info(sessions)")).fetchall()
        ]
        if "study_id" not in cols:
            await db.execute("ALTER TABLE sessions ADD COLUMN study_id TEXT")
            await db.execute(
                "CREATE INDEX IF NOT EXISTS idx_sessions_username_study "
                "ON sessions(username, study_id, started_at DESC)"
            )
            log.info("Migration : sessions.study_id ajoute (DB pre-existante)")

        await db.commit()
    log.info("Mémoire initialisée : %s", _DB_PATH)


async def set_session_study(session_id: str, study_id: str | None) -> None:
    """Associe une session chat a son etude active au moment de la conversation.

    Appele depuis POST /chat apres _resolve_active_profile. Permet de retrouver
    "la derniere conversation tenue sur cette etude" au reload du desk.
    CHARTE §2 : etude = unite du cycle.
    """
    async with aiosqlite.connect(_DB_PATH) as db:
        await db.execute(
            "UPDATE sessions SET study_id = ? WHERE id = ?",
            (study_id, session_id),
        )
        await db.commit()


async def get_latest_session_for_study(
    username: str, study_id: str
) -> str | None:
    """Retourne l'id de la derniere session liee a cette etude pour cet user.

    Utilise au GET / pour reprendre la conversation en cours sur l'etude
    active, plutot que de creer une uuid fraiche orpheline.
    Retourne None si aucune session ou si study_id vide.
    """
    if not study_id:
        return None
    async with aiosqlite.connect(_DB_PATH) as db:
        cur = await db.execute(
            "SELECT id FROM sessions WHERE username = ? AND study_id = ? "
            "ORDER BY started_at DESC LIMIT 1",
            (username, study_id),
        )
        row = await cur.fetchone()
        return row[0] if row else None


# ── Phase 4 : Insights agentiques (couche 3) ──────────────────────────────────

async def add_insight(
    key: str,
    value: str,
    source: str = "implicit",
    confidence: float = 0.7,
    username: str = "user",
) -> int:
    """
    Ajoute (ou met à jour si key existe) un insight agentique.
    Retourne l'id. Source: 'implicit' (LLM auto) ou 'explicit' (user direct).
    """
    now = int(time.time())
    async with aiosqlite.connect(_DB_PATH) as db:
        # Si même (username, key) existe, mettre à jour value + last_seen
        existing = await (await db.execute(
            "SELECT id FROM agent_insights WHERE username = ? AND key = ?",
            (username, key),
        )).fetchone()
        if existing:
            await db.execute(
                "UPDATE agent_insights SET value = ?, source = ?, "
                "confidence = ?, last_seen = ? WHERE id = ?",
                (value, source, confidence, now, existing[0]),
            )
            await db.commit()
            return existing[0]
        cur = await db.execute(
            "INSERT INTO agent_insights "
            "(username, key, value, source, confidence, created_at, last_seen) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (username, key, value, source, confidence, now, now),
        )
        await db.commit()
        return cur.lastrowid


async def list_insights(
    username: str = "user", limit: int = 50,
) -> list[dict]:
    async with aiosqlite.connect(_DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        rows = await (await db.execute(
            "SELECT * FROM agent_insights WHERE username = ? "
            "ORDER BY last_seen DESC LIMIT ?",
            (username, limit),
        )).fetchall()
    return [dict(r) for r in rows]


async def delete_insight(insight_id: int) -> None:
    async with aiosqlite.connect(_DB_PATH) as db:
        await db.execute(
            "DELETE FROM agent_insights WHERE id = ?", (insight_id,),
        )
        await db.commit()


async def clear_insights(username: str = "user") -> int:
    async with aiosqlite.connect(_DB_PATH) as db:
        cur = await db.execute(
            "DELETE FROM agent_insights WHERE username = ?", (username,),
        )
        await db.commit()
        return cur.rowcount


# ── Phase 4b : Mémoire markdown structurée principale (édition user directe) ──
# Pattern industrie : CLAUDE.md / ChatGPT memory / Letta core memory.
# Un document markdown structuré en sections cohérentes pour un usage CEREMA,
# lu par l'agent à chaque turn, éditable section par section.
#
# Les `insights` ci-dessus restent pour l'apprentissage automatique (LLM-driven
# via <remember>), mais le markdown est le contrat d'identité de l'user, sous
# son contrôle direct.

# Sections structurées — l'ordre est l'ordre d'affichage et de lecture LLM
MEMORY_SECTIONS = [
    {
        "key": "identity",
        "title": "Identité & rôle",
        "hint": "Qui je suis, mon métier au CEREMA, ma direction, mes responsabilités.",
        "placeholder": "Ex: Chargé d'études risques inondations au CEREMA DTerMed, "
                       "DTVB/AU. Je travaille sur les PPRi et les TRI.",
    },
    {
        "key": "zones",
        "title": "Zones de travail habituelles",
        "hint": "Communes, départements, périmètres récurrents.",
        "placeholder": "Ex: Var (83), surtout littoral entre Hyères et Saint-Tropez. "
                       "Études fréquentes : Le Lavandou, Cavalaire, Saint-Cyr.",
    },
    {
        "key": "data",
        "title": "Données & sources préférées",
        "hint": "Fournisseurs, jeux de données, échelles, formats habituels.",
        "placeholder": "Ex: BD TOPO IGN (bâti, routes), Géorisques (TRI T100), "
                       "RGE Alti 1m, orthophotos PCRS 5cm quand dispo.",
    },
    {
        "key": "carto",
        "title": "Préférences cartographiques",
        "hint": "Palettes, fonds, projection, typographie, formats d'export.",
        "placeholder": "Ex: Palette YlOrRd pour intensités, fond CartoDB Positron, "
                       "EPSG:2154, Marianne 9pt minimum, exports PDF A3 paysage 300dpi.",
    },
    {
        "key": "methods",
        "title": "Méthodes & habitudes",
        "hint": "Workflows récurrents, recettes, ordre de traitement habituel.",
        "placeholder": "Ex: Toujours commencer par smart_load communes pour cadrage, "
                       "puis croiser bâti × emprise TRI, exporter en GPKG + storymap.",
    },
    {
        "key": "notes",
        "title": "Notes libres",
        "hint": "Tout autre fait stable utile pour l'agent.",
        "placeholder": "Ex: Préfère réponses sobres, sans emoji superflu. "
                       "Sensibilité au DSFR pour les livrables institutionnels.",
    },
]


def default_memory_doc() -> str:
    """Génère le markdown par défaut à partir des sections."""
    parts = []
    for s in MEMORY_SECTIONS:
        parts.append(f"## {s['title']}\n_{s['hint']}_\n\n")
    return "".join(parts).rstrip() + "\n"


async def _ensure_memory_doc_table() -> None:
    async with aiosqlite.connect(_DB_PATH) as db:
        await db.execute("""
            CREATE TABLE IF NOT EXISTS memory_doc (
                username   TEXT PRIMARY KEY,
                sections   TEXT NOT NULL,
                updated_at INTEGER NOT NULL
            )
        """)
        await db.commit()


async def get_memory_sections(username: str = "user") -> dict:
    """
    Retourne le dict {section_key: content} de l'user.
    Vide pour les sections non-éditées.
    """
    await _ensure_memory_doc_table()
    async with aiosqlite.connect(_DB_PATH) as db:
        row = await (await db.execute(
            "SELECT sections FROM memory_doc WHERE username = ?", (username,),
        )).fetchone()
    if not row:
        return {s["key"]: "" for s in MEMORY_SECTIONS}
    try:
        data = json.loads(row[0])
        # Assurer toutes les clés présentes
        for s in MEMORY_SECTIONS:
            data.setdefault(s["key"], "")
        return data
    except Exception:
        return {s["key"]: "" for s in MEMORY_SECTIONS}


async def set_memory_section(key: str, content: str, username: str = "user") -> None:
    """Met à jour UNE section. content peut être vide pour effacer la section."""
    valid_keys = {s["key"] for s in MEMORY_SECTIONS}
    if key not in valid_keys:
        raise ValueError(f"section inconnue: {key}")
    sections = await get_memory_sections(username)
    sections[key] = content.strip()
    await _ensure_memory_doc_table()
    async with aiosqlite.connect(_DB_PATH) as db:
        await db.execute(
            "INSERT OR REPLACE INTO memory_doc (username, sections, updated_at) "
            "VALUES (?, ?, ?)",
            (username, json.dumps(sections, ensure_ascii=False), int(time.time())),
        )
        await db.commit()


async def get_memory_doc_markdown(username: str = "user") -> str:
    """
    Assemble le markdown complet à partir des sections.
    Format injecté dans le system prompt de l'agent.
    """
    sections = await get_memory_sections(username)
    parts = []
    for s in MEMORY_SECTIONS:
        content = sections.get(s["key"], "").strip()
        if content:
            parts.append(f"## {s['title']}\n{content}\n")
    return "\n".join(parts) if parts else ""


# ── Profil utilisateur ─────────────────────────────────────────────────────────

async def get_profile_value(key: str, default: Any = None) -> Any:
    async with aiosqlite.connect(_DB_PATH) as db:
        row = await (await db.execute(
            "SELECT value FROM user_profile WHERE key = ?", (key,)
        )).fetchone()
    return json.loads(row[0]) if row else default


async def set_profile_value(key: str, value: Any) -> None:
    async with aiosqlite.connect(_DB_PATH) as db:
        await db.execute("""
            INSERT OR REPLACE INTO user_profile (key, value, updated_at)
            VALUES (?, ?, ?)
        """, (key, json.dumps(value), int(time.time())))
        await db.commit()


async def get_full_profile() -> dict:
    async with aiosqlite.connect(_DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        rows = await (await db.execute("SELECT key, value FROM user_profile")).fetchall()
    return {r["key"]: json.loads(r["value"]) for r in rows}


# ── Sessions ───────────────────────────────────────────────────────────────────

async def create_session(session_id: str, username: str, profile_id: str = "standard") -> None:
    async with aiosqlite.connect(_DB_PATH) as db:
        await db.execute("""
            INSERT OR IGNORE INTO sessions (id, username, profile_id, started_at)
            VALUES (?, ?, ?, ?)
        """, (session_id, username, profile_id, int(time.time())))
        await db.commit()


async def close_session(session_id: str, summary: str = "", zone: str = "") -> None:
    async with aiosqlite.connect(_DB_PATH) as db:
        await db.execute("""
            UPDATE sessions SET ended_at=?, summary=?, zone=? WHERE id=?
        """, (int(time.time()), summary, zone, session_id))
        await db.commit()


async def set_session_summary(session_id: str, summary: str) -> None:
    """Met à jour uniquement le résumé d'une session sans la clore.

    Utilisé pour étiqueter automatiquement les sessions actives avec le
    premier message utilisateur — sinon get_recent_sessions() renvoie des
    titres `Session abcd1234` illisibles dans la sidebar du chat.
    """
    async with aiosqlite.connect(_DB_PATH) as db:
        await db.execute(
            "UPDATE sessions SET summary=? WHERE id=? AND (summary IS NULL OR summary='')",
            (summary, session_id),
        )
        await db.commit()


async def add_message(session_id: str, role: str, content: str,
                      tool_calls: list | None = None) -> None:
    async with aiosqlite.connect(_DB_PATH) as db:
        await db.execute("""
            INSERT INTO messages (session_id, role, content, tool_calls, created_at)
            VALUES (?, ?, ?, ?, ?)
        """, (session_id, role, content,
               json.dumps(tool_calls) if tool_calls else None,
               int(time.time())))
        await db.commit()


async def get_session_messages(session_id: str, limit: int = 50) -> list[dict]:
    async with aiosqlite.connect(_DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        rows = await (await db.execute("""
            SELECT role, content, tool_calls, created_at
            FROM messages WHERE session_id = ?
            ORDER BY created_at DESC LIMIT ?
        """, (session_id, limit))).fetchall()
    return [dict(r) for r in reversed(rows)]


async def get_recent_sessions(username: str, limit: int = 10) -> list[dict]:
    async with aiosqlite.connect(_DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        rows = await (await db.execute("""
            SELECT id, profile_id, started_at, ended_at, zone, summary
            FROM sessions WHERE username = ?
            ORDER BY started_at DESC LIMIT ?
        """, (username, limit))).fetchall()
    return [dict(r) for r in rows]


async def count_session_messages(session_id: str) -> int:
    """Nombre de messages persistés pour cette session.

    Utilisé par le hook checkpoint pour calculer un message_idx COHÉRENT
    avec la DB (compter en DB, pas dans le contexte LLM qui inclut
    system_prompt + history). Le bon point de retour pour rollback est
    "juste après le dernier message DB au moment du tool".
    """
    async with aiosqlite.connect(_DB_PATH) as db:
        row = await (await db.execute(
            "SELECT COUNT(*) FROM messages WHERE session_id = ?", (session_id,),
        )).fetchone()
    return row[0] if row else 0


async def truncate_messages_after(session_id: str, message_idx: int) -> int:
    """Supprime tous les messages de la session après l'index donné.

    Utilisé par le rollback : restaure le projet QGIS au snapshot + tronque
    la conversation à ce point pour que l'historique reflète l'état réel.
    Retourne le nombre de messages supprimés.

    message_idx = position 0-based dans la liste ordonnée par created_at.
    Tronque > message_idx (garde les message_idx + 1 premiers).
    """
    async with aiosqlite.connect(_DB_PATH) as db:
        # Récupérer les IDs au-delà de l'index
        rows = await (await db.execute(
            "SELECT id FROM messages WHERE session_id = ? ORDER BY created_at LIMIT -1 OFFSET ?",
            (session_id, message_idx + 1),
        )).fetchall()
        ids = [r[0] for r in rows]
        if not ids:
            return 0
        placeholders = ",".join("?" * len(ids))
        await db.execute(
            f"DELETE FROM messages WHERE id IN ({placeholders})", ids,
        )
        await db.commit()
    return len(ids)


# ── Checkpoints (Commit B — Rollback agent) ───────────────────────────────────

async def create_checkpoint(
    checkpoint_id: str,
    session_id: str,
    message_idx: int,
    qgz_path: str,
    tool_name: str,
    study_id: str | None = None,
    audit_ts: float | None = None,
) -> None:
    """Enregistre un checkpoint pris avant un tool mutating.

    Le snapshot .qgz est déjà écrit sur le PVC par le hub (cf.
    studies.snapshot_active_pod_code). Cette fonction enregistre seulement
    la métadonnée dans SQLite côté agent.
    """
    async with aiosqlite.connect(_DB_PATH) as db:
        await db.execute(
            """
            INSERT INTO checkpoints
              (id, session_id, message_idx, study_id, qgz_path, tool_name,
               audit_ts, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (checkpoint_id, session_id, message_idx, study_id, qgz_path,
             tool_name, audit_ts, int(time.time())),
        )
        await db.commit()


async def list_checkpoints(session_id: str, limit: int = 100) -> list[dict]:
    """Liste les checkpoints d'une session, du plus ancien au plus récent."""
    async with aiosqlite.connect(_DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        rows = await (await db.execute(
            """
            SELECT id, session_id, message_idx, study_id, qgz_path, tool_name,
                   audit_ts, created_at
            FROM checkpoints WHERE session_id = ?
            ORDER BY message_idx ASC, created_at ASC LIMIT ?
            """,
            (session_id, limit),
        )).fetchall()
    return [dict(r) for r in rows]


async def get_checkpoint(checkpoint_id: str) -> dict | None:
    """Retourne un checkpoint par id, ou None."""
    async with aiosqlite.connect(_DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        row = await (await db.execute(
            "SELECT * FROM checkpoints WHERE id = ?", (checkpoint_id,),
        )).fetchone()
    return dict(row) if row else None


async def delete_checkpoint(checkpoint_id: str) -> None:
    """Supprime la métadonnée du checkpoint (le .qgz est nettoyé par la purge)."""
    async with aiosqlite.connect(_DB_PATH) as db:
        await db.execute(
            "DELETE FROM checkpoints WHERE id = ?", (checkpoint_id,),
        )
        await db.commit()


async def purge_old_checkpoints(
    session_id: str | None = None,
    max_per_session: int = 20,
    max_age_days: int = 7,
) -> list[dict]:
    """Purge les checkpoints au-delà de max_per_session OU plus vieux que
    max_age_days. Renvoie la liste des checkpoints purgés (utile pour que
    l'appelant supprime aussi les .qgz sur le PVC).

    Si session_id est None, applique à toutes les sessions.
    """
    now = int(time.time())
    age_cutoff = now - (max_age_days * 86400)
    purged: list[dict] = []
    async with aiosqlite.connect(_DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        if session_id:
            sessions_to_check = [session_id]
        else:
            sids = await (await db.execute(
                "SELECT DISTINCT session_id FROM checkpoints"
            )).fetchall()
            sessions_to_check = [r[0] for r in sids]
        for sid in sessions_to_check:
            # Trop anciens
            old_rows = await (await db.execute(
                "SELECT * FROM checkpoints WHERE session_id = ? AND created_at < ?",
                (sid, age_cutoff),
            )).fetchall()
            # Au-delà de max_per_session (garde les plus récents).
            # Tie-breaker sur message_idx DESC puis id DESC : si plusieurs
            # checkpoints partagent le même created_at (création rapide),
            # on conserve ceux avec le plus grand message_idx (= plus tard
            # dans la conversation) — ordre déterministe.
            extras_rows = await (await db.execute(
                """
                SELECT * FROM checkpoints WHERE session_id = ?
                ORDER BY created_at DESC, message_idx DESC, id DESC
                LIMIT -1 OFFSET ?
                """,
                (sid, max_per_session),
            )).fetchall()
            to_purge_ids: set[str] = set()
            for r in list(old_rows) + list(extras_rows):
                purged.append(dict(r))
                to_purge_ids.add(r["id"])
            if to_purge_ids:
                placeholders = ",".join("?" * len(to_purge_ids))
                await db.execute(
                    f"DELETE FROM checkpoints WHERE id IN ({placeholders})",
                    list(to_purge_ids),
                )
        await db.commit()
    return purged


# ── Projets ────────────────────────────────────────────────────────────────────

async def save_project(project_id: str, name: str, zone: str = "",
                       profile_id: str = "standard", qgz_path: str = "",
                       metadata: dict | None = None) -> None:
    now = int(time.time())
    async with aiosqlite.connect(_DB_PATH) as db:
        await db.execute("""
            INSERT OR REPLACE INTO projects
            (id, name, zone, profile_id, qgz_path, created_at, updated_at, metadata)
            VALUES (?, ?, ?, ?, ?, COALESCE(
                (SELECT created_at FROM projects WHERE id=?), ?), ?, ?)
        """, (project_id, name, zone, profile_id, qgz_path,
              project_id, now, now, json.dumps(metadata or {})))
        await db.commit()


async def list_projects(profile_id: str | None = None) -> list[dict]:
    async with aiosqlite.connect(_DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        if profile_id:
            rows = await (await db.execute(
                "SELECT * FROM projects WHERE profile_id=? ORDER BY updated_at DESC",
                (profile_id,)
            )).fetchall()
        else:
            rows = await (await db.execute(
                "SELECT * FROM projects ORDER BY updated_at DESC"
            )).fetchall()
    return [dict(r) for r in rows]


# ── Recettes ───────────────────────────────────────────────────────────────────

async def save_recipe(recipe_id: str, name: str, steps: list,
                      profile_id: str = "standard", description: str = "") -> None:
    now = int(time.time())
    async with aiosqlite.connect(_DB_PATH) as db:
        await db.execute("""
            INSERT OR REPLACE INTO recipes
            (id, name, profile_id, description, steps, created_at)
            VALUES (?, ?, ?, ?, ?, COALESCE((SELECT created_at FROM recipes WHERE id=?), ?))
        """, (recipe_id, name, profile_id, description,
               json.dumps(steps), recipe_id, now))
        await db.commit()


async def list_recipes(profile_id: str | None = None) -> list[dict]:
    async with aiosqlite.connect(_DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        if profile_id:
            rows = await (await db.execute(
                "SELECT * FROM recipes WHERE profile_id=? ORDER BY times_used DESC",
                (profile_id,)
            )).fetchall()
        else:
            rows = await (await db.execute(
                "SELECT * FROM recipes ORDER BY times_used DESC"
            )).fetchall()
    return [dict(r) for r in rows]


# ── Résumé contexte pour le LLM ───────────────────────────────────────────────

async def build_context_summary(
    username: str, session_id: str,
    profile_id: str = "standard",
    active_study: dict | None = None,
    active_study_treatments: list[dict] | None = None,
    project_state: dict | None = None,
) -> str:
    """
    Phase 4 : construit un résumé en 3 couches pour l'system prompt LLM.

    Couche 1 (conversationnelle) : géré par history dans /chat, pas ici.
    Couche 2 (étude active + ÉTAT PROJET QGIS) : fournis par l'appelant.
    Couche 3 (user permanent) : markdown structuré + insights agentiques.

    project_state : dict retourné par MCP `get_project_info` — contient les
    couches actuellement chargées dans QgsProject. L'agent peut ainsi ÉVITER
    de recharger une couche déjà présente et orienter ses traitements.
    """
    # ── Couche 3 (user permanent) ─────────────────────────────────────────
    # Le markdown structuré est le PILIER (pattern CLAUDE.md / ChatGPT memory).
    # Les insights et profile_value restent en complément (apprentissage auto).
    memory_md = await get_memory_doc_markdown(username)
    profile = await get_full_profile()
    insights = await list_insights(username, limit=15)
    recent_sessions = await get_recent_sessions(username, limit=5)
    projects = await list_projects()
    recipes = await list_recipes()

    layer3 = []

    # Document markdown structuré (principal, géré directement par l'user)
    if memory_md:
        layer3.append("--- À PROPOS DE MOI (édité par moi-même) ---\n" + memory_md)

    # Préférences explicites héritées (rétro-compat couche legacy)
    legacy_prefs = []
    for k, label in (
        ("preferred_zone", "Zone habituelle"),
        ("preferred_crs", "CRS préféré"),
        ("export_format", "Format export préféré"),
        ("metier", "Métier CEREMA"),
        ("style_pref", "Style cartographique préféré"),
        ("langue", "Langue de rendu préférée"),
    ):
        v = profile.get(k)
        if v:
            legacy_prefs.append(f"{label} : {v}")
    if legacy_prefs:
        layer3.append("Autres préférences :\n" + "\n".join(f"- {p}" for p in legacy_prefs))

    # Insights agentiques (mémoire long terme apprise auto, complément markdown)
    if insights:
        layer3.append("Faits auto-détectés sur l'user (à confirmer/promouvoir) :")
        for ins in insights[:10]:
            src = "📌" if ins["source"] == "explicit" else "🧠"
            layer3.append(f"  {src} {ins['key']} = {ins['value']}")

    # Sessions passées (résumés)
    completed = [
        s for s in recent_sessions
        if s.get("summary") and s["id"] != session_id
    ][:3]
    if completed:
        hist = " | ".join(s["summary"][:80] for s in completed)
        layer3.append(f"Sessions passées : {hist}")

    # Recettes capitalisées
    if recipes:
        rec_list = ", ".join(r["name"] for r in recipes[:5])
        layer3.append(f"Recettes disponibles : {rec_list}")

    # Projets locaux (qgz)
    active_projects = [p for p in projects[:3] if p.get("zone")]
    if active_projects:
        proj_list = ", ".join(
            f"{p['name']} ({p.get('zone', '?')})" for p in active_projects
        )
        layer3.append(f"Projets QGIS récents : {proj_list}")

    # ── Couche 2 (étude active + état projet QGIS) ───────────────────────
    layer2 = []

    # 2a — État RÉEL du projet QGIS : couches chargées, zone, CRS.
    # L'agent doit consulter cette section AVANT toute décision de chargement.
    if project_state and isinstance(project_state, dict):
        layers = project_state.get("layers") or []
        if layers:
            # Format compact : nom (type, count, crs)
            layer_lines = []
            for ly in layers[:15]:
                nm   = ly.get("name", "?")
                gtyp = ly.get("geometry_type") or ly.get("type", "?")
                fc   = ly.get("feature_count")
                crs  = ly.get("crs", "")
                bits = [str(gtyp)]
                if fc is not None:
                    bits.append(f"{fc} entités")
                if crs:
                    bits.append(crs)
                layer_lines.append(f"  • {nm} ({', '.join(bits)})")
            layer2.append(
                f"Couches chargées dans le projet QGIS ({len(layers)} total) :\n"
                + "\n".join(layer_lines)
                + "\n  ⚠️ Vérifie cette liste AVANT de proposer un nouveau "
                  "chargement — la couche peut déjà être là."
            )
        proj_crs = project_state.get("project_crs") or project_state.get("crs")
        if proj_crs:
            layer2.append(f"CRS du projet : {proj_crs}")
        proj_zone = project_state.get("study_zone") or project_state.get("zone")
        if proj_zone:
            if isinstance(proj_zone, str):
                layer2.append(f"Zone d'étude active : {proj_zone}")
            else:
                # Dict : exposer nom ET bbox ET center pour que l'agent puisse
                # les réutiliser comme args de tools (run_recipe, smart_load,
                # set_study_zone, etc.) sans inventer/géocoder à nouveau.
                zname = proj_zone.get("name", "?")
                zbbox = proj_zone.get("bbox") or proj_zone.get("extent")
                zcenter = proj_zone.get("center") or proj_zone.get("centroid")
                zcrs = proj_zone.get("crs", "EPSG:4326")
                zline = f"Zone d'étude active : « {zname} »"
                if zbbox:
                    zline += f"\n  bbox ({zcrs}) : {zbbox}"
                if zcenter:
                    zline += f"\n  centre : {zcenter}"
                zline += (
                    "\n  ⚠️ Quand un tool prend `bbox`, `zone` ou `extent`, "
                    "RÉUTILISE cette bbox plutôt que d'inventer un string "
                    "générique (« Marseille » au lieu des coordonnées casse "
                    "les recettes spatiales)."
                )
                layer2.append(zline)

    # 2b — Méta-étude (côté hub : nom, profil, audit trail)
    if active_study:
        layer2.append(f"Étude active : « {active_study.get('name', '?')} »")
        if active_study.get("profile"):
            layer2.append(f"Profil étude : {active_study['profile']}")
        if active_study.get("project_path"):
            layer2.append(f"Projet QGIS : {active_study['project_path']}")
        if active_study_treatments:
            n = len(active_study_treatments)
            # Compter par kind
            kinds: dict[str, int] = {}
            for t in active_study_treatments:
                k = t.get("kind", "?")
                kinds[k] = kinds.get(k, 0) + 1
            kinds_str = ", ".join(f"{k}={v}" for k, v in kinds.items())
            layer2.append(f"Audit étude : {n} évènements ({kinds_str})")
            # Derniers traitements significatifs
            sig = [t for t in active_study_treatments[-5:]
                   if t.get("kind") in ("processing", "export")]
            if sig:
                last_str = " → ".join(
                    f"{t.get('tool','?').split(':')[-1]}({t.get('n_features_out','-')})"
                    for t in sig[-3:]
                )
                layer2.append(f"Derniers traitements : {last_str}")

    # ── Assemblage 3 couches ──────────────────────────────────────────────
    parts = []
    if layer2:
        parts.append("=== Étude en cours ===\n" + "\n".join(f"- {x}" for x in layer2))
    if layer3:
        parts.append("=== Contexte utilisateur (mémoire long terme) ===\n"
                     + "\n".join(f"- {x}" for x in layer3))

    return "\n\n".join(parts) + ("\n" if parts else "")
