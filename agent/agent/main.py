"""
agent.main — FastAPI + HTMX : interface chat du client QGIS Agent.

Endpoints :
  GET  /                  → interface chat (HTML)
  POST /chat              → message utilisateur → réponse SSE streaming
  GET  /sessions          → historique sessions
  GET  /sessions/{id}     → messages d'une session
  GET  /projects          → projets utilisateur
  GET  /profiles          → profils disponibles (depuis hub)
  POST /profile/activate  → changer de profil actif
  GET  /memory/context    → contexte mémoire actuel
  POST /memory/preference → sauvegarder une préférence
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import time
import uuid
from typing import AsyncGenerator

import httpx
from fastapi import Depends, FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, StreamingResponse
from fastapi.templating import Jinja2Templates
from pathlib import Path

from agent import memory
from agent import vector_store
from agent import embed_worker
from agent.qgis_agent import QGISAgent

# État partagé du worker d'embedding (lancé au startup, stoppé au shutdown).
_embed_task: asyncio.Task | None = None
_embed_stop: asyncio.Event | None = None

# Signaux d'arrêt par session : permet à l'utilisateur d'interrompre la boucle
# agent (loop LLM→tool→LLM) entre 2 tool calls. Lu par chat_stream après
# chaque exécution de tool. Cf. POST /chat/{sid}/stop.
# Map session_id → asyncio.Event. Event posé = stop demandé.
_stop_signals: dict[str, asyncio.Event] = {}

# Contexte de navigation par session : quel livrable l'user vient de
# sélectionner dans le desk (storymap draft, recette, publication…).
# Le routeur _resolve_active_profile l'utilise pour basculer dynamiquement
# le profil agent (storymap → storymap_creator, recipe → recipe_creator…).
# Map session_id → {"kind": "storymap|recipe|...", "id": str, "title": str|None}.
# In-memory : si le pod redémarre, le contexte retombe sur l'étude active
# (qui est persistée DB hub).
_active_renders: dict[str, dict] = {}

# Mapping render_kind → profile agent (la "table de routage contextuel").
# Si un render est actif pour la session, son kind override le profil de
# l'étude. Sinon, fallback sur profil de l'étude active (qui lui-même peut
# fallback sur "standard" si pas d'étude).
_RENDER_KIND_PROFILE: dict[str, str] = {
    "storymap":   "storymap_creator",
    "recipe":     "recipe_creator",
    "flux":       "map_composer",
    "dataset":    "db_analyst",
    "pdf":        "storymap_creator",
}


def _get_or_create_stop_signal(session_id: str) -> asyncio.Event:
    """Récupère ou crée l'Event de stop pour une session."""
    ev = _stop_signals.get(session_id)
    if ev is None:
        ev = asyncio.Event()
        _stop_signals[session_id] = ev
    return ev

log = logging.getLogger("agent.main")

# Aligner le niveau des loggers `agent.*` sur celui d'uvicorn pour que les
# `log.info(...)` du worker / vector_store / enrichers apparaissent dans stdout.
_uv_handlers = logging.getLogger("uvicorn").handlers
for _name in ("agent", "agent.main", "agent.embed_worker", "agent.vector_store",
              "agent.memory", "agent.qgis_agent",
              "agent.enrichers.memory_recall", "agent.insight_extractor"):
    _lg = logging.getLogger(_name)
    _lg.setLevel(logging.INFO)
    if _uv_handlers and not _lg.handlers:
        for _h in _uv_handlers:
            _lg.addHandler(_h)
        _lg.propagate = False

_DATA_DIR     = Path(os.getenv("DATA_DIR", "/data/agent"))
_DEFAULT_PROFILE = os.getenv("QGIS_DEFAULT_PROFILE", "standard")

# Hub URL : explicite ou auto-dérivé depuis ONYXIA_USER
_ONYXIA_USER = os.getenv("ONYXIA_USER", "")
_HUB_URL     = (
    os.getenv("HUB_URL")
    or (f"https://user-{_ONYXIA_USER}-qgis-mcp-bridge.user.lab.sspcloud.fr"
        if _ONYXIA_USER else "")
)

# Clé API hub : stockée dans Vault SSPCloud (secret hf-token ou variable agent)
_HUB_API_KEY = os.getenv("HUB_API_KEY", os.getenv("QGIS_API_KEY", ""))

_templates_dir = Path(__file__).parent.parent / "templates"
templates      = Jinja2Templates(directory=str(_templates_dir))

app = FastAPI(title="QGIS Agent", docs_url=None, redoc_url=None)


@app.on_event("startup")
async def startup():
    global _embed_task, _embed_stop
    # Sanity check : la clé LLM doit être présente (sinon embeddings, chat,
    # extracteur d'insights se taisent silencieusement). On warn fort plutôt
    # que de partir et chercher pendant 1h.
    if not os.getenv("LLM_API_KEY"):
        log.warning(
            "LLM_API_KEY absente de l'environnement — l'agent répondra avec "
            "des erreurs sur tout appel LLM (chat, embeddings, insight extractor). "
            "Définir la variable dans le secret SSPCloud / .env."
        )
    if not os.getenv("HUB_API_KEY") and not os.getenv("QGIS_API_KEY"):
        log.warning("HUB_API_KEY/QGIS_API_KEY absentes — les tools MCP QGIS échoueront.")

    await memory.init()
    # Vector store : extension sqlite-vec + tables embed_chunks/vec_chunks idempotentes.
    try:
        await vector_store.init_vector_store()
        _embed_stop = asyncio.Event()
        _embed_task = asyncio.create_task(embed_worker.run_forever(_embed_stop))
    except Exception as e:
        log.warning("vector_store / embed_worker indisponible : %s", e)
    # Task de purge des vieux checkpoints — toutes les 6h, applique les
    # limites par défaut (20 max/session, 7j max). Permet de garder le PVC
    # propre sans intervention manuelle.
    asyncio.create_task(_checkpoint_purge_loop())
    log.info("QGIS Agent démarré | Hub: %s | Profil: %s", _HUB_URL, _DEFAULT_PROFILE)


async def _checkpoint_purge_loop() -> None:
    """Boucle background : purge les vieux checkpoints toutes les 6 heures.

    Étapes :
    1. memory.purge_old_checkpoints supprime les métadonnées SQLite et
       retourne la liste des entrées purgées (avec leur qgz_path).
    2. On appelle le hub /sessions/purge-checkpoint-files avec ces paths
       pour supprimer effectivement les .qgz sur le PVC user (sinon ils
       s'accumulent indéfiniment).
    """
    while True:
        try:
            await asyncio.sleep(6 * 3600)
            purged = await memory.purge_old_checkpoints(
                max_per_session=20, max_age_days=7,
            )
            if not purged:
                continue
            log.info("Purge auto checkpoints : %d entrées DB supprimées",
                     len(purged))
            # Demande au hub de supprimer les .qgz correspondants sur le PVC
            paths = [p["qgz_path"] for p in purged if p.get("qgz_path")]
            if paths and _HUB_URL and _HUB_API_KEY:
                try:
                    async with httpx.AsyncClient(timeout=30) as c:
                        r = await c.post(
                            f"{_HUB_URL}/sessions/purge-checkpoint-files",
                            headers={"Authorization": f"Bearer {_HUB_API_KEY}"},
                            json={"paths": paths},
                        )
                        if r.status_code < 300:
                            d = r.json()
                            log.info("Purge .qgz PVC : %d fichiers supprimés",
                                     d.get("purged", 0))
                        else:
                            log.warning("Purge .qgz PVC échec %d", r.status_code)
                except Exception as exc:
                    log.warning("Purge .qgz PVC réseau : %s", exc)
        except asyncio.CancelledError:
            break
        except Exception as exc:
            log.warning("Purge checkpoints en erreur : %s", exc)


@app.on_event("shutdown")
async def shutdown():
    global _embed_task, _embed_stop
    if _embed_stop is not None:
        _embed_stop.set()
    if _embed_task is not None:
        try:
            await asyncio.wait_for(_embed_task, timeout=5)
        except asyncio.TimeoutError:
            _embed_task.cancel()
    log.info("QGIS Agent arrêté proprement")


# ── Healthcheck ────────────────────────────────────────────────────────────────

@app.get("/health")
async def health():
    return {
        "status":   "ok",
        "service":  "qgis-agent",
        "hub_url":  _HUB_URL,
        "profile":  _DEFAULT_PROFILE,
    }


@app.get("/api/status")
async def api_status():
    """État de l'agent côté UI (polling) — sert au bandeau d'avertissement
    « clé LLM manquante » dans le chat. Pas de secret renvoyé."""
    has_llm = bool(os.getenv("LLM_API_KEY"))
    has_hub = bool(os.getenv("HUB_API_KEY"))
    return {
        "has_llm_key": has_llm,
        "has_hub_key": has_hub,
        "llm_base_url": os.getenv("LLM_BASE_URL", "https://llm.lab.sspcloud.fr/api"),
        "profile": _DEFAULT_PROFILE,
    }


@app.get("/memory/embed/stats")
async def embed_stats():
    """Compteurs du worker d'indexation : indexed vs pending par source."""
    try:
        return await embed_worker.stats()
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)


@app.get("/memory/health")
async def memory_health():
    """
    Phase 9 F — observabilité élargie de la couche mémoire.

    Renvoie :
    - Compteurs par source (messages, insights, memory_doc) : indexed/pending
    - Taille de la DB SQLite + chemin
    - Latence d'un search "ping" de référence (mesure end-to-end)
    - Embed worker actif ou non
    - Configuration : EMBED_DIM, modèle, base URL (sans la clé)
    """
    import os, time, sqlite3
    from pathlib import Path
    try:
        from agent import embed_worker, vector_store
    except Exception as e:
        return JSONResponse({"error": f"modules indisponibles : {e}"}, status_code=500)

    health = {}
    # Compteurs (réutilise stats existant)
    try:
        health["counters"] = await embed_worker.stats()
    except Exception as e:
        health["counters"] = {"error": str(e)}

    # Taille DB
    try:
        db_path = Path(os.getenv("DATA_DIR", "/data/agent")) / "memory.db"
        if db_path.exists():
            size_bytes = db_path.stat().st_size
            health["db"] = {
                "path": str(db_path),
                "size_bytes": size_bytes,
                "size_mb": round(size_bytes / 1024 / 1024, 2),
            }
            # Compte total embed_chunks (pour cross-check avec counters)
            try:
                with sqlite3.connect(str(db_path)) as conn:
                    cur = conn.execute("SELECT COUNT(*) FROM embed_chunks")
                    health["db"]["total_chunks"] = cur.fetchone()[0]
            except Exception:
                pass
    except Exception as e:
        health["db"] = {"error": str(e)}

    # Ping search latency
    try:
        t0 = time.perf_counter()
        results = await vector_store.search("test ping latence", top_k=3)
        elapsed_ms = int((time.perf_counter() - t0) * 1000)
        health["search_ping"] = {
            "latency_ms": elapsed_ms,
            "results_count": len(results),
            "top_similarity": round(results[0]["similarity"], 3) if results else None,
        }
    except Exception as e:
        health["search_ping"] = {"error": str(e)}

    # Worker actif
    health["worker_running"] = bool(_embed_task and not _embed_task.done())

    # Config (sans secret)
    health["config"] = {
        "embed_dim": getattr(vector_store, "EMBED_DIM", None),
        "embed_model": os.getenv("EMBED_MODEL", "qwen3-embedding-8b"),
        "llm_base_url": os.getenv("LLM_BASE_URL", ""),
        "llm_api_key_set": bool(os.getenv("LLM_API_KEY")),
    }

    return health


@app.post("/memory/extract_insights")
async def extract_insights_endpoint(request: Request):
    """
    Déclenche manuellement l'extraction d'insights LLM pour une session.
    Body : {"session_id": "...", "username": "user"} (username optionnel).
    """
    from agent import insight_extractor
    body = await request.json()
    session_id = body.get("session_id")
    username   = body.get("username", "user")
    if not session_id:
        return JSONResponse({"error": "session_id requis"}, status_code=400)
    return await insight_extractor.extract_for_session(session_id, username)


# ── Interface principale ───────────────────────────────────────────────────────

async def _fetch_active_study_id() -> str | None:
    """Recupere l'id de l'etude active cote hub (sentinel central).

    Best-effort : si le hub n'est pas joignable ou pas d'etude active,
    retourne None. Utilise au GET / pour retrouver la derniere session
    liee a l'etude (au lieu d'une uuid orpheline) — CHARTE §2.
    """
    hub_url = os.getenv("HUB_URL", "")
    api_key = os.getenv("HUB_API_KEY", "")
    if not (hub_url and api_key):
        return None
    import httpx
    try:
        async with httpx.AsyncClient(timeout=5) as c:
            r = await c.get(
                f"{hub_url}/studies/active",
                headers={"Authorization": f"Bearer {api_key}"},
            )
            if r.status_code == 200 and r.json():
                return r.json().get("id")
    except Exception:
        pass
    return None


@app.get("/", response_class=HTMLResponse)
async def index(request: Request):
    """Interface chat principale.

    Si une etude est active cote hub et qu'une session chat anterieure y est
    rattachee, on la reprend (continuite UX — l'historique reste accessible).
    Sinon nouvelle session uuid. Cf. CHARTE §2 (etude = unite du cycle).
    """
    profile_id = request.cookies.get("profile_id", _DEFAULT_PROFILE)
    sessions   = await memory.get_recent_sessions("user", limit=5)
    projects   = await memory.list_projects(profile_id)

    # Reprise de la derniere session liee a l'etude active (Fix Bug B).
    # Le query param ?new=1 force une nouvelle session (pour "Nouvelle
    # conversation" futur). Sinon, si etude active + session anterieure
    # rattachee, on la reprend.
    session_id: str | None = None
    if request.query_params.get("new") != "1":
        active_study_id = await _fetch_active_study_id()
        if active_study_id:
            session_id = await memory.get_latest_session_for_study(
                "user", active_study_id,
            )
    if not session_id:
        session_id = str(uuid.uuid4())

    return templates.TemplateResponse(request, "chat.html", {
        "profile_id":   profile_id,
        "hub_url":      _HUB_URL,
        "portal_url":   os.getenv("PORTAL_URL", ""),
        "sessions":     sessions,
        "projects":     projects[:5],
        "session_id":   session_id,
    })


# ── Chat streaming SSE ─────────────────────────────────────────────────────────

async def _resolve_active_profile(form_profile: str, session_id: str = "") -> str:
    """Routeur contextuel : choisit le profil agent selon le contexte courant.

    Ordre de priorité (du plus spécifique au plus général) :
      1. **Render actif pour cette session** (storymap sélectionnée, recette
         ouverte…) → profil mappé via _RENDER_KIND_PROFILE
      2. **Étude active** (sentinel hub) → profil métier de l'étude
      3. **form_profile** transmis (typiquement le cookie profile_id de l'user)

    Permet la "contextualisation native" décrite dans CHARTE_AGENT.md §3
    Principe 1 : l'agent suit ce que l'utilisateur fait, sans combobox.
    """
    # 1. Render actif pour cette session (le plus spécifique)
    render = _active_renders.get(session_id) if session_id else None
    if render and render.get("kind") in _RENDER_KIND_PROFILE:
        return _RENDER_KIND_PROFILE[render["kind"]]

    # 2. Étude active (fallback)
    import os, httpx
    hub_url = os.getenv("HUB_URL", "")
    api_key = os.getenv("HUB_API_KEY", "")
    if not (hub_url and api_key):
        return form_profile
    try:
        async with httpx.AsyncClient(timeout=5) as c:
            r = await c.get(
                f"{hub_url}/studies/active",
                headers={"Authorization": f"Bearer {api_key}"},
            )
            if r.status_code == 200 and r.json():
                p = r.json().get("profile")
                if p:
                    return p
    except Exception:
        pass

    # 3. Form profile (par défaut)
    return form_profile


@app.post("/chat")
async def chat(
    request:    Request,
    message:    str  = Form(...),
    session_id: str  = Form(...),
    profile_id: str  = Form(_DEFAULT_PROFILE),
):
    """Endpoint chat principal — réponse en streaming SSE.

    Si une étude est active côté hub, son profil prime sur le form_profile.
    Le LLM peut switcher de profil en cours via <switch_profile>X</switch_profile>.
    """
    # Routeur contextuel : le render actif (sélection livrable dans le desk)
    # ou l'étude active prime sur le form. Cf. CHARTE_AGENT §3 Principe 1.
    profile_id = await _resolve_active_profile(profile_id, session_id=session_id)

    # Créer la session en mémoire si nouvelle
    await memory.create_session(session_id, "user", profile_id)

    # Historique des messages pour le contexte
    history = await memory.get_session_messages(session_id, limit=20)

    # Premier message utilisateur de la session → étiquette de session
    # (sinon la sidebar affiche `Session abcd1234` illisible — cf. audit UX).
    # On tronque à 80 caractères pour rester lisible dans la sidebar.
    # On lie aussi la session a l'etude active au moment de la creation
    # (Fix Bug B — Sessions chat orphelines). Cf. CHARTE §2.
    if not history:
        title = (message or "").strip().replace("\n", " ")[:80]
        if title:
            await memory.set_session_summary(session_id, title)
        active_study_id = await _fetch_active_study_id()
        if active_study_id:
            await memory.set_session_study(session_id, active_study_id)
    history_formatted = [
        {"role": m["role"], "content": m["content"]}
        for m in history
    ]

    agent = QGISAgent(
        username   = "user",
        session_id = session_id,
        profile_id = profile_id,
    )

    # Récupère/crée le signal d'arrêt de cette session — sera vérifié par
    # chat_stream entre chaque tool call. On le reset ici pour permettre une
    # nouvelle requête après un stop précédent sur la même session.
    stop_signal = _get_or_create_stop_signal(session_id)
    stop_signal.clear()

    async def event_stream() -> AsyncGenerator[str, None]:
        try:
            async for chunk in agent.chat_stream(
                message, history=history_formatted, stop_signal=stop_signal,
            ):
                # SSE format
                yield f"data: {json.dumps({'text': chunk})}\n\n"
            yield f"data: {json.dumps({'done': True})}\n\n"
        except Exception as e:
            log.error("Erreur chat: %s", e, exc_info=True)
            yield f"data: {json.dumps({'error': str(e)})}\n\n"

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
        }
    )


# ── Routeur contextuel : render actif par session ───────────────────────────

@app.post("/context/render/{session_id}")
async def set_active_render(session_id: str, request: Request):
    """Déclare qu'un livrable est en cours d'édition/consultation.

    Le routeur _resolve_active_profile utilisera le `kind` pour basculer
    automatiquement vers le profil approprié (storymap → storymap_creator,
    recipe → recipe_creator, etc.).

    Body : {"kind": "storymap|recipe|flux|dataset|pdf", "id": "...", "title": "..."}
    """
    body = await request.json()
    kind = (body.get("kind") or "").strip()
    rid = (body.get("id") or "").strip()
    if not kind:
        return {"ok": False, "error": "kind requis"}
    _active_renders[session_id] = {
        "kind":  kind,
        "id":    rid,
        "title": body.get("title") or "",
    }
    log.info("Render actif session=%s kind=%s id=%s",
             session_id[:12], kind, rid[:12])
    return {"ok": True, "render": _active_renders[session_id],
            "profile_target": _RENDER_KIND_PROFILE.get(kind)}


@app.delete("/context/render/{session_id}")
async def clear_active_render(session_id: str):
    """Retire le render actif → profil retombe sur étude / form."""
    rendered = _active_renders.pop(session_id, None)
    return {"ok": True, "cleared": rendered}


@app.get("/context/render/{session_id}")
async def get_active_render(session_id: str):
    """Retourne le render actif pour cette session, ou null."""
    return {"render": _active_renders.get(session_id)}


@app.post("/chat/{session_id}/stop")
async def chat_stop(session_id: str):
    """Demande l'arrêt de la boucle agent pour cette session.

    L'agent vérifie ce signal entre chaque tool call. Le tool en cours
    d'exécution est laissé finir proprement (cohérence des données) ;
    l'agent break à la prochaine itération de boucle.
    """
    ev = _get_or_create_stop_signal(session_id)
    ev.set()
    return {"stopped": True, "session_id": session_id}


# ── API Mémoire ────────────────────────────────────────────────────────────────

@app.get("/sessions")
async def list_sessions():
    return await memory.get_recent_sessions("user", limit=20)


@app.get("/sessions/{session_id}/messages")
async def get_messages(session_id: str):
    return await memory.get_session_messages(session_id)


# ── Checkpoints / Rollback (Commit B) ──────────────────────────────────────────

@app.get("/sessions/{session_id}/checkpoints")
async def session_list_checkpoints(session_id: str):
    """Liste les checkpoints d'une session (du plus ancien au plus récent)."""
    return await memory.list_checkpoints(session_id, limit=100)


@app.post("/sessions/{session_id}/rollback/{checkpoint_id}")
async def session_rollback(session_id: str, checkpoint_id: str):
    """Rollback complet : restaure le projet QGIS au checkpoint + tronque
    la conversation après le point de retour.

    Orchestre : récupère métadonnée → appelle hub /restore-checkpoint pour
    le pod → truncate_messages_after côté agent → retourne les compteurs.
    """
    ckpt = await memory.get_checkpoint(checkpoint_id)
    if not ckpt:
        raise HTTPException(404, "Checkpoint introuvable")
    if ckpt["session_id"] != session_id:
        raise HTTPException(400, "Checkpoint ne correspond pas à cette session")

    if not _HUB_URL or not _HUB_API_KEY:
        raise HTTPException(503, "Hub non configuré")

    # 1. Restaurer le .qgz sur le pod via hub
    try:
        async with httpx.AsyncClient(timeout=45) as client:
            r = await client.post(
                f"{_HUB_URL}/sessions/{session_id}/restore-checkpoint",
                headers={"Authorization": f"Bearer {_HUB_API_KEY}"},
                json={
                    "checkpoint_id": checkpoint_id,
                    "study_id":      ckpt.get("study_id") or "",
                },
            )
            if r.status_code >= 300:
                raise HTTPException(
                    502, f"Hub restore-checkpoint a renvoyé {r.status_code}",
                )
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(500, f"Restore pod échec : {exc}")

    # 2. Tronquer la conversation après le message_idx
    n_truncated = await memory.truncate_messages_after(
        session_id, ckpt["message_idx"],
    )

    return {
        "ok": True,
        "checkpoint_id": checkpoint_id,
        "message_idx": ckpt["message_idx"],
        "messages_truncated": n_truncated,
        "tool_name": ckpt["tool_name"],
    }


@app.post("/sessions/{session_id}/checkpoints/purge")
async def session_purge_checkpoints(session_id: str):
    """Purge manuelle des vieux checkpoints d'une session.

    Applique les limites par défaut (20 max, 7j). La purge auto se fait aussi
    en background à intervalle régulier (cf. _start_checkpoint_purge_task).
    """
    purged = await memory.purge_old_checkpoints(session_id=session_id)
    return {"purged": len(purged), "ids": [p["id"] for p in purged]}


@app.get("/projects")
async def list_projects(profile_id: str = "standard"):
    return await memory.list_projects(profile_id)


@app.get("/recipes")
async def list_recipes(profile_id: str = "standard"):
    return await memory.list_recipes(profile_id)


@app.get("/memory/context")
async def get_context(session_id: str = "current", profile_id: str = "standard"):
    ctx = await memory.build_context_summary("user", session_id, profile_id)
    profile = await memory.get_full_profile()
    return {"context": ctx, "profile": profile}


@app.post("/memory/preference")
async def save_preference(key: str = Form(...), value: str = Form(...)):
    await memory.set_profile_value(key, value)
    return {"saved": True, "key": key}


# ── Phase 4 : couche 3 (mémoire user permanent) ──────────────────────────────

@app.get("/user/preferences")
async def get_user_preferences():
    """Préférences explicites de l'user (sous son contrôle direct)."""
    return await memory.get_full_profile()


@app.patch("/user/preferences")
async def patch_user_preferences(request: Request):
    """Mise à jour partielle des préférences user. Body JSON {key:value, ...}."""
    body = await request.json()
    if not isinstance(body, dict):
        return {"error": "body doit être un objet JSON"}
    for k, v in body.items():
        await memory.set_profile_value(k, v)
    return await memory.get_full_profile()


@app.get("/user/insights")
async def get_insights():
    """Insights agentiques : ce que l'agent a appris sur l'user."""
    return await memory.list_insights("user")


@app.post("/user/insights")
async def add_insight_endpoint(request: Request):
    """Ajout manuel d'un insight (source=explicit). Body {key, value}."""
    body = await request.json()
    if not body.get("key") or not body.get("value"):
        return {"error": "key et value requis"}
    iid = await memory.add_insight(
        key=body["key"], value=body["value"],
        source="explicit", confidence=1.0, username="user",
    )
    return {"id": iid, "saved": True}


@app.delete("/user/insights/{insight_id}", status_code=204)
async def delete_insight_endpoint(insight_id: int):
    """L'user peut supprimer un insight (contrôle direct sur la mémoire)."""
    await memory.delete_insight(insight_id)


@app.delete("/user/insights", status_code=204)
async def clear_insights_endpoint():
    """Reset complet des insights (l'user reprend la main)."""
    await memory.clear_insights("user")


# ── Mémoire markdown structurée (pattern CLAUDE.md / ChatGPT memory) ────────

@app.get("/user/memory")
async def get_memory():
    """Retourne les sections + le markdown assemblé + métadonnées."""
    sections = await memory.get_memory_sections("user")
    return {
        "sections": sections,
        "schema":   memory.MEMORY_SECTIONS,
        "markdown": await memory.get_memory_doc_markdown("user"),
    }


# ── Dictée vocale : proxy vers Whisper SSPCloud ──────────────────────────────

_STT_URL = os.getenv(
    "STT_URL",
    "https://llm.lab.sspcloud.fr/api/v1/audio/transcriptions",
)
_STT_KEY = os.getenv("LLM_API_KEY", "")


@app.post("/stt")
async def speech_to_text(
    file: UploadFile = File(...),
    language: str = Form("fr"),
):
    """
    Transcrit un blob audio (webm/wav/ogg) via l'endpoint Whisper de SSPCloud.
    Évite d'exposer la clé LLM au navigateur : le bouton mic poste ici, on relaie.
    """
    audio_bytes = await file.read()
    if not audio_bytes:
        raise HTTPException(400, "Fichier audio vide")
    filename = file.filename or "voice.webm"
    content_type = file.content_type or "audio/webm"
    async with httpx.AsyncClient(timeout=60.0) as cli:
        try:
            r = await cli.post(
                _STT_URL,
                headers={"Authorization": f"Bearer {_STT_KEY}"},
                files={"file": (filename, audio_bytes, content_type)},
                data={"language": language},
            )
        except httpx.HTTPError as exc:
            raise HTTPException(502, f"STT injoignable : {exc}") from exc
    if r.status_code >= 400:
        raise HTTPException(r.status_code, f"STT erreur : {r.text[:300]}")
    payload = r.json()
    return {"text": (payload.get("text") or "").strip()}


@app.patch("/user/memory")
async def patch_memory_section(request: Request):
    """
    Met à jour une section. Body JSON : {"key": "identity", "content": "..."}.
    Pour effacer une section, passer content="" .
    """
    body = await request.json()
    key = body.get("key", "").strip()
    content = body.get("content", "")
    if not key:
        return {"error": "key requis"}
    try:
        await memory.set_memory_section(key, content, "user")
    except ValueError as exc:
        return {"error": str(exc)}
    return {"saved": True, "key": key}


# ── API Projets ────────────────────────────────────────────────────────────────

@app.post("/projects")
async def create_project(
    name:       str = Form(...),
    zone:       str = Form(""),
    profile_id: str = Form("standard"),
):
    project_id = str(uuid.uuid4())[:8]
    await memory.save_project(project_id, name, zone, profile_id)
    return {"id": project_id, "name": name}


# ── Proxy profils depuis le hub ───────────────────────────────────────────────

@app.get("/profiles")
async def list_profiles():
    """Proxy vers GET /profiles du hub QGIS."""
    if not _HUB_URL or not _HUB_API_KEY:
        return []
    try:
        async with httpx.AsyncClient(timeout=5) as client:
            resp = await client.get(
                f"{_HUB_URL}/profiles",
                headers={"Authorization": f"Bearer {_HUB_API_KEY}"},
            )
            return resp.json()
    except Exception:
        return []


# ── Bureau de travail (desk) ──────────────────────────────────────────────────

async def _fetch_desk_context() -> dict:
    """Agrège le contexte workspace depuis le hub local."""
    ctx: dict = {
        "studies": [], "active_study_id": None, "active_study": None,
        "catalog_items": [], "catalog_count": 0,
        "session_status": "—", "novnc_url": "#",
        "agent_url": f"https://user-{_ONYXIA_USER}-qgis-agent-bridge.user.lab.sspcloud.fr",
        "insights": [], "insights_count": 0,
        "preferences": {}, "recent_treatments": [],
        "session_ready": False,
    }
    if not _HUB_URL or not _HUB_API_KEY:
        return ctx
    headers = {"Authorization": f"Bearer {_HUB_API_KEY}"}
    async with httpx.AsyncClient(timeout=8) as client:
        for path, key in [("/studies", "studies"), ("/studies/active", None),
                          ("/sessions", None)]:
            try:
                r = await client.get(f"{_HUB_URL}{path}", headers=headers)
                if r.status_code != 200:
                    continue
                data = r.json()
                if path == "/studies":
                    ctx["studies"] = data
                elif path == "/studies/active" and data:
                    ctx["active_study_id"] = data.get("id")
                    ctx["active_study"] = data
                elif path == "/sessions" and data:
                    st = data[0].get("status", "—")
                    ctx["session_status"] = {"ready": "✓", "sleeping": "💤",
                                              "starting": "…", "error": "⚠"}.get(st, st)
                    ctx["session_ready"] = ctx["session_status"] == "✓"
                    novnc = data[0].get("novnc_url", "")
                    if novnc:
                        ctx["novnc_url"] = novnc
            except Exception:
                pass
        # Publications de l'étude active
        if ctx.get("active_study_id"):
            try:
                r = await client.get(
                    f"{_HUB_URL}/catalog/{_ONYXIA_USER}", headers=headers)
                if r.status_code == 200:
                    items = r.json().get("items", [])
                    sid = ctx["active_study_id"]
                    ctx["catalog_items"] = [i for i in items if i.get("study_id") == sid][:30]
                    ctx["catalog_count"] = len(ctx["catalog_items"])
            except Exception:
                pass
    return ctx


PROFILE_LABELS = {
    "standard": "Standard", "geoai_analyst": "IA Vision",
    "risk_analyst": "Risques", "db_analyst": "Données / DB",
    "storymap_creator": "Storymap", "map_composer": "Cartographe",
    "recipe_creator": "Recettes", "guided_tour": "Guide",
}


@app.get("/desk", response_class=HTMLResponse)
async def desk_page(request: Request):
    """Bureau de travail unifié — sidebar études | canvas QGIS noVNC | chat agent."""
    ctx = await _fetch_desk_context()
    ctx["username"] = _ONYXIA_USER
    ctx["hub_url"] = _HUB_URL
    ctx["agent_url"] = f"https://user-{_ONYXIA_USER}-qgis-agent-bridge.user.lab.sspcloud.fr"
    ctx["profile_labels"] = PROFILE_LABELS
    return templates.TemplateResponse(request, "desk.html", ctx)


@app.get("/desk/memory")
async def desk_memory():
    rows = await memory.recent_messages(username=_ONYXIA_USER, limit=20)
    return rows


@app.post("/desk/memory/insights")
async def desk_save_insight(request: Request):
    body = await request.json()
    await memory.add_insight(key=body["key"], value=body["value"],
                              source="desk", confidence=0.9,
                              username=_ONYXIA_USER)
    return {"ok": True}


@app.get("/desk/layers")
async def desk_layers():
    if not _HUB_URL or not _HUB_API_KEY:
        return []
    try:
        async with httpx.AsyncClient(timeout=5) as client:
            r = await client.get(f"{_HUB_URL}/layers",
                                  headers={"Authorization": f"Bearer {_HUB_API_KEY}"})
            return r.json() if r.status_code == 200 else []
    except Exception:
        return []


@app.post("/workspace/wake")
async def workspace_wake(request: Request):
    """Réveille le workspace QGIS endormi (scale 0→1).

    Si return_to=desk est fourni (formulaire HTML), on redirige vers /desk
    plutôt que de renvoyer du JSON brut — sinon le navigateur affiche
    `{"ok": true}` à la place du bureau (cf. loader cassé identifié dans
    l'audit UX 2026-05-17).
    """
    return_to = request.query_params.get("return_to", "")
    ok = False
    if _HUB_URL and _HUB_API_KEY:
        try:
            async with httpx.AsyncClient(timeout=30) as client:
                r = await client.post(
                    f"{_HUB_URL}/sessions",
                    headers={"Authorization": f"Bearer {_HUB_API_KEY}"},
                    json={},
                )
                ok = r.status_code < 300
        except Exception:
            ok = False
    if return_to == "desk":
        return RedirectResponse("/desk", status_code=302)
    return {"ok": ok}


@app.post("/desk/study/{sid}/save")
async def desk_save_study(sid: str, request: Request):
    body = await request.json()
    if not _HUB_URL or not _HUB_API_KEY:
        return {"ok": False}
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            r = await client.post(
                f"{_HUB_URL}/studies/{sid}/save",
                headers={"Authorization": f"Bearer {_HUB_API_KEY}"},
                json=body,
            )
            return r.json() if r.status_code == 200 else {"ok": False}
    except Exception:
        return {"ok": False}
