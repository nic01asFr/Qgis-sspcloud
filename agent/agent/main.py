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
from fastapi.responses import HTMLResponse, JSONResponse, StreamingResponse
from fastapi.templating import Jinja2Templates
from pathlib import Path

from agent import memory
from agent import vector_store
from agent import embed_worker
from agent.qgis_agent import QGISAgent

# État partagé du worker d'embedding (lancé au startup, stoppé au shutdown).
_embed_task: asyncio.Task | None = None
_embed_stop: asyncio.Event | None = None

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
    log.info("QGIS Agent démarré | Hub: %s | Profil: %s", _HUB_URL, _DEFAULT_PROFILE)


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

@app.get("/")
async def root():
    """Readiness probe Kubernetes — 200 sans auth."""
    return {"status": "ok", "service": "qgis-agent"}


@app.get("/health")
async def health():
    return {
        "status":   "ok",
        "service":  "qgis-agent",
        "hub_url":  _HUB_URL,
        "profile":  _DEFAULT_PROFILE,
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

@app.get("/", response_class=HTMLResponse)
async def index(request: Request):
    """Interface chat principale."""
    profile_id = request.cookies.get("profile_id", _DEFAULT_PROFILE)
    sessions   = await memory.get_recent_sessions("user", limit=5)
    projects   = await memory.list_projects(profile_id)

    return templates.TemplateResponse(request, "chat.html", {
        "profile_id":   profile_id,
        "hub_url":      _HUB_URL,
        "sessions":     sessions,
        "projects":     projects[:5],
        "session_id":   str(uuid.uuid4()),
    })


# ── Chat streaming SSE ─────────────────────────────────────────────────────────

async def _resolve_active_profile(form_profile: str) -> str:
    """
    Si le hub a une étude active, son profil prime sur le form.
    Permet au profil de "suivre" l'étude active de l'user automatiquement.
    """
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
    # L'étude active prime sur le form (permet au profil de suivre l'étude)
    profile_id = await _resolve_active_profile(profile_id)

    # Créer la session en mémoire si nouvelle
    await memory.create_session(session_id, "user", profile_id)

    # Historique des messages pour le contexte
    history = await memory.get_session_messages(session_id, limit=20)
    history_formatted = [
        {"role": m["role"], "content": m["content"]}
        for m in history
    ]

    agent = QGISAgent(
        username   = "user",
        session_id = session_id,
        profile_id = profile_id,
    )

    async def event_stream() -> AsyncGenerator[str, None]:
        try:
            async for chunk in agent.chat_stream(message, history=history_formatted):
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


# ── API Mémoire ────────────────────────────────────────────────────────────────

@app.get("/sessions")
async def list_sessions():
    return await memory.get_recent_sessions("user", limit=20)


@app.get("/sessions/{session_id}/messages")
async def get_messages(session_id: str):
    return await memory.get_session_messages(session_id)


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
async def workspace_wake():
    """Réveille le workspace QGIS endormi (scale 0→1)."""
    if not _HUB_URL or not _HUB_API_KEY:
        return {"ok": False}
    try:
        async with httpx.AsyncClient(timeout=30) as client:
            r = await client.post(f"{_HUB_URL}/sessions",
                                   headers={"Authorization": f"Bearer {_HUB_API_KEY}"},
                                   json={})
            return {"ok": r.status_code < 300}
    except Exception:
        return {"ok": False}


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
