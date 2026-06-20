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
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, Response, StreamingResponse
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

# Hub URL : injectee par hub._bootstrap_agent dans l'env du pod.
# Pas de fallback `qgis-mcp-bridge` (legacy, host inexistant en prod).
# La validation stricte est dans startup() ci-dessous.
_ONYXIA_USER = os.getenv("ONYXIA_USER", "")
_HUB_URL     = os.getenv("HUB_URL", "").rstrip("/")

# Clé API hub : injectee via secretKeyRef vers Secret K8s qgis-hub-apikey.
# Voir hub/main.py:_bootstrap_agent. Cle source-of-truth namespace-level.
_HUB_API_KEY = os.getenv("HUB_API_KEY", os.getenv("QGIS_API_KEY", ""))

_templates_dir = Path(__file__).parent.parent / "templates"
templates      = Jinja2Templates(directory=str(_templates_dir))

app = FastAPI(title="QGIS Agent", docs_url=None, redoc_url=None)


# ── Phase 0ter Steps 7-8 (RGPD) : Middleware OIDC agent ───────────────────────
# Sans cela, n'importe qui avec l'URL user-X-qgis-agent.user.lab.sspcloud.fr
# accede a l'agent chat de user X (fuite RGPD critique).
#
# Architecture symetrique au hub :
#   - Cookie oidc_token partage via Domain=.user.lab.sspcloud.fr (portail Step 1)
#   - Verify JWT via JWKS Keycloak SSPCloud + check preferred_username == ONYXIA_USER
#   - Whitelist : /health, /api/status, /api/version (probes K8s + version check)
#   - Inter-pod : X-Hub-Auth ou Authorization Bearer ${HUB_API_KEY} skip
#     (cf. /api/reload-llm-key webhook appele par le hub)
#   - kube-probe user-agent skip (probes K8s)

_AGENT_PUBLIC_ROUTES = ("/health", "/api/status", "/api/version")
_AGENT_INTER_POD_ROUTES = (
    "/api/reload-llm-key",
    "/api/refresh-llm-config",
    "/api/refresh-profiles",
    # Fix consolidation 2026-06-19 : ajout routes /user/* et /memory/*
    # appelees par le hub via _agent_call (cf. hub main.py:_agent_call).
    # Sans whitelist, le middleware OIDC agent bloque -> /desk/memory
    # retourne body vide -> UI desk Memoire pane affiche "Chargement..."
    # indefiniment + sections editables manquantes (identity, zones, data,
    # methods, tools, vocabulary).
    # Le check Bearer HUB_API_KEY (presented == os.environ['HUB_API_KEY'])
    # garantit que seul le hub legitime passe (idem cote hub).
    "/user",          # /user/memory, /user/preferences, /user/insights
    "/memory",        # /memory/context, /memory/extract_insights, etc.
)

_JWKS_CACHE = None
_KEYCLOAK_ISSUER = os.getenv(
    "SSPCLOUD_ISSUER",
    "https://auth.lab.sspcloud.fr/auth/realms/sspcloud",
)


def _get_jwks_client():
    """Cache PyJWKClient sur l'instance Keycloak SSPCloud."""
    global _JWKS_CACHE
    if _JWKS_CACHE is None:
        from jwt import PyJWKClient
        _JWKS_CACHE = PyJWKClient(
            f"{_KEYCLOAK_ISSUER}/protocol/openid-connect/certs",
            cache_keys=True,
        )
    return _JWKS_CACHE


def _portal_login_url_agent(request: Request) -> str:
    """URL portail pour redirect login. Fallback hardcode si PORTAL_URL absent."""
    portal = os.getenv("PORTAL_URL", "").rstrip("/")
    if not portal:
        portal = "https://user-nic01asfr-qgis-mcp-portal-bridge.user.lab.sspcloud.fr"
    from urllib.parse import quote
    return f"{portal}/?next={quote(str(request.url), safe='')}"


@app.middleware("http")
async def agent_oidc_middleware(request: Request, call_next):
    """Middleware OIDC agent symetrique au hub. Cf. hub/hub/auth.py:oidc_auth_middleware."""
    path = request.url.path
    # 1. Routes publiques
    if any(path == p or path.startswith(p + "/") for p in _AGENT_PUBLIC_ROUTES):
        return await call_next(request)
    # 2. Court-circuit kube-probe
    if "kube-probe" in request.headers.get("user-agent", "").lower():
        return await call_next(request)
    # 3. Inter-pod (hub -> agent via X-Hub-Auth ou Bearer)
    if any(path == p or path.startswith(p + "/") for p in _AGENT_INTER_POD_ROUTES):
        expected = os.environ.get("HUB_API_KEY", "")
        if expected:
            x_hub = request.headers.get("x-hub-auth", "")
            auth_hdr = request.headers.get("authorization", "")
            presented = (
                x_hub
                or (auth_hdr.removeprefix("Bearer ").strip()
                    if auth_hdr.startswith("Bearer ") else "")
            )
            if presented == expected:
                return await call_next(request)
    # 4. UI : cookie OIDC obligatoire
    token = request.cookies.get("oidc_token") or ""
    if not token:
        accept = request.headers.get("accept", "")
        if "text/html" in accept:
            return RedirectResponse(_portal_login_url_agent(request), status_code=302)
        return JSONResponse(
            {"detail": "Auth requise. Va sur le portail pour t'identifier.",
             "portal_url": _portal_login_url_agent(request)},
            status_code=401,
        )
    # 5. Decode JWT + verify ownership
    try:
        from jwt import decode as _jwt_decode, ExpiredSignatureError
        signing_key = _get_jwks_client().get_signing_key_from_jwt(token)
        claims = _jwt_decode(
            token, signing_key.key, algorithms=["RS256"],
            options={"verify_aud": False},
        )
    except Exception as exc:
        if "Signature has expired" in str(exc):
            return RedirectResponse(_portal_login_url_agent(request), status_code=302)
        return JSONResponse({"detail": f"Token invalide : {exc}"}, status_code=401)
    onyxia_user = os.environ.get("ONYXIA_USER", "")
    claimed_user = claims.get("preferred_username") or claims.get("sub", "")
    if onyxia_user and claimed_user != onyxia_user:
        return JSONResponse(
            {"detail": f"Cet espace appartient a '{onyxia_user}'. Tu es "
                       f"connecte en tant que '{claimed_user}'."},
            status_code=403,
        )
    request.state.oidc_claims = claims
    request.state.oidc_user = claimed_user
    return await call_next(request)


@app.get("/auth/whoami")
async def auth_whoami_agent(request: Request):
    """Endpoint debug agent : retourne le user authentifie (apres middleware OIDC).
    Phase 0ter symetrique au hub /auth/whoami."""
    claims = getattr(request.state, "oidc_claims", None)
    if not claims:
        return {"authenticated": False, "reason": "no_oidc_claims_in_request"}
    return {
        "authenticated": True,
        "username": claims.get("preferred_username"),
        "email": claims.get("email"),
        "name": claims.get("name"),
        "exp": claims.get("exp"),
        "onyxia_user_owner": os.environ.get("ONYXIA_USER", ""),
        "match": claims.get("preferred_username") == os.environ.get("ONYXIA_USER", ""),
    }


@app.on_event("startup")
async def startup():
    global _embed_task, _embed_stop
    # Fail-fast strict : HUB_URL est obligatoire en prod. Sans elle, l'agent
    # est inutilisable (tools MCP, profils, sessions hub -> tous KO).
    # Mieux vaut refuser de demarrer plutot que tomber sur des fallback legacy
    # qui pointent vers des hosts inexistants (`qgis-mcp-bridge`, etc.).
    if not _HUB_URL:
        msg = (
            "FATAL : HUB_URL absent de l'environnement. Le pod ne peut pas "
            "demarrer. Verifier l'injection env dans le StatefulSet "
            "(hub._bootstrap_agent doit poser HUB_URL=https://user-X-qgis...)."
        )
        log.error(msg)
        raise RuntimeError(msg)
    # LLM_API_KEY : non-bloquant (bandeau UI invite a configurer datalab account
    # AI Assistant). Un user pas encore onboarde voit le bandeau dans le chat.
    if not os.getenv("LLM_API_KEY"):
        log.warning(
            "LLM_API_KEY absente de l'environnement — l'agent répondra avec "
            "des erreurs sur tout appel LLM (chat, embeddings, insight extractor). "
            "Bandeau UI invite l'utilisateur a configurer dans datalab account."
        )
    # HUB_API_KEY : injectee via secretKeyRef -> kubelet recharge a chaque
    # demarrage. Si absente, log warning (tools MCP KO mais pod demarre quand
    # meme pour l'observabilite).
    if not _HUB_API_KEY:
        log.warning(
            "HUB_API_KEY/QGIS_API_KEY absentes — les tools MCP QGIS échoueront. "
            "Verifier le Secret k8s qgis-hub-apikey."
        )

    await memory.init()
    # Vector store : extension sqlite-vec + tables embed_chunks/vec_chunks idempotentes.
    try:
        await vector_store.init_vector_store()
        _embed_stop = asyncio.Event()
        _embed_task = asyncio.create_task(embed_worker.run_forever(_embed_stop))
    except Exception as e:
        log.warning("vector_store / embed_worker indisponible : %s", e)
    # Charger les profils depuis le hub (source de verite unique).
    # Backround : sans bloquer le startup, on lance la requete. Si le hub
    # n'est pas pret immediatement (race au cold start), `_PROFILES_CACHE`
    # reste vide quelques secondes -> fallback prompt generique applique
    # le temps que le fetch reussisse. Refresh manuel possible via
    # POST /api/refresh-profiles.
    from agent import qgis_agent as _qa
    asyncio.create_task(_qa.fetch_profiles_from_hub())
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


@app.post("/api/reload-llm-key")
async def api_reload_llm_key(request: Request):
    """Webhook interne appele par le hub (cf. Option alpha 2026-06-02).

    Le hub, apres avoir lu la cle dans le Secret SSPCloud AI Assistant via
    `_bootstrap_agent`, appelle ce endpoint pour que l'agent reload sa
    `LLM_API_KEY` EN RAM (os.environ) sans restart pod. Toutes les lectures
    dynamiques de la cle (qgis_agent._llm_api_key(), insight_extractor,
    vector_store, STT) la voient au prochain call LLM.

    Avant ce mecanisme, le hub faisait `kubectl delete pod qgis-agent-0`
    pour propager la nouvelle env -> ~30s downtime + ingress 502 -> bug
    "JSON parse" dans l'UI chat (Bug C+D). Avec ce webhook, downtime = 0s,
    user voit la cle prise en compte immediatement.

    Auth via X-Hub-Auth header = HUB_API_KEY shared secret (deja inject
    en env des 2 pods via Secret K8s `qgis-hub-apikey`).

    Body : {"llm_api_key": "<sk-...>"} (string, vide => no-op).

    Retour : {"ok": true, "has_key": <bool>}.

    Compat : si le hub appelle un agent vieille image qui n'a PAS ce
    endpoint, il recevra 404 cote hub -> fallback delete pod (legacy).
    """
    expected = os.getenv("HUB_API_KEY", "")
    if not expected:
        # Cas dev / non-K8s : pas d'auth configuree, on refuse par defaut
        raise HTTPException(503, "HUB_API_KEY non configure cote agent")
    if request.headers.get("X-Hub-Auth") != expected:
        raise HTTPException(403, "X-Hub-Auth invalide")
    try:
        body = await request.json()
    except Exception:
        body = {}
    new_key = (body.get("llm_api_key") or "").strip() if isinstance(body, dict) else ""
    if new_key:
        os.environ["LLM_API_KEY"] = new_key
        log.info(
            "reload-llm-key: cle LLM rechargee in-memory (len=%d, no pod restart)",
            len(new_key),
        )
    else:
        log.warning("reload-llm-key: cle vide recue (no-op)")
    return {"ok": True, "has_key": bool(new_key)}


@app.post("/api/reload-hub-key")
async def api_reload_hub_key(request: Request):
    """Webhook interne : rotation de HUB_API_KEY (Mini-Phase 0bis Bug 5.9).

    Symetrique a /api/reload-llm-key mais pour HUB_API_KEY.

    Probleme adresse : si le hub regenere sa cle (rotation, redeploiement, reset
    Secret K8s), l'agent garde son ancienne HUB_API_KEY env -> tous les calls
    /mcp sont rejetes "Cle API invalide" -> tools MCP KO silencieux (cf. bug
    nic01asfr 2026-06-15 : 2 tools natifs au lieu de 43, rien dans QGIS).

    Auth : X-Hub-Auth header = ANCIENNE HUB_API_KEY (que l'agent connait encore).
    Le hub qui declenche la rotation connait l'ancienne (vient de la lire avant
    de generer la nouvelle) -> peut authentifier. Window de 5 min apres rotation
    suffit pour propager.

    Body : {"new_hub_api_key": "<qgis_username_hex32>"} (string).

    Retour : {"ok": true, "rotated": <bool>}.
    """
    expected_old = os.getenv("HUB_API_KEY", "")
    if not expected_old:
        raise HTTPException(503, "HUB_API_KEY non configure cote agent")
    if request.headers.get("X-Hub-Auth") != expected_old:
        raise HTTPException(403, "X-Hub-Auth invalide (ancien HUB_API_KEY attendu)")
    try:
        body = await request.json()
    except Exception:
        body = {}
    new_key = (body.get("new_hub_api_key") or "").strip() if isinstance(body, dict) else ""
    if not new_key:
        return {"ok": True, "rotated": False, "reason": "empty key"}
    os.environ["HUB_API_KEY"] = new_key
    # qgis_agent.py utilise _HUB_API_KEY (lu au import time L67). Rebind dynamique :
    try:
        from agent import qgis_agent as _qa
        _qa._HUB_API_KEY = new_key
    except Exception as e:
        log.warning("reload-hub-key: rebind qgis_agent._HUB_API_KEY echoue: %s", e)
    log.info(
        "reload-hub-key: cle HUB rotee in-memory (len=%d, no pod restart)",
        len(new_key),
    )
    return {"ok": True, "rotated": True}


@app.post("/api/refresh-llm-config")
async def api_refresh_llm_config():
    """Proxy same-origin vers le hub `/api/refresh-llm-config`.

    Permet au bouton 'Vérifier ma config' du bandeau agent d'éliminer la
    popup cross-origin (bloquée par certains navigateurs apres async).
    L'agent appelle le hub server-side avec HUB_API_KEY (deja injectée en
    env), le hub re-lit le secretassistant et patche l'env du SS agent.

    Reponse JSON minimale : {ok: bool, restarted: bool}.
    Apres succes, l'agent va redemarrer (sigterm depuis kubelet). Le client
    UI continue son polling /api/status -> bandeau cache des que LLM_API_KEY
    est detecte au reboot.
    """
    import httpx as _httpx
    hub_url = os.getenv("HUB_URL", "").rstrip("/")
    hub_key = os.getenv("HUB_API_KEY", "")
    if not hub_url:
        return JSONResponse(
            {"ok": False, "error": "HUB_URL non configure"}, status_code=503,
        )
    headers = {"Authorization": f"Bearer {hub_key}"} if hub_key else {}
    try:
        async with _httpx.AsyncClient(timeout=20) as c:
            r = await c.post(f"{hub_url}/api/refresh-llm-config", headers=headers)
            if r.status_code >= 300:
                return JSONResponse(
                    {"ok": False, "error": f"hub HTTP {r.status_code}"},
                    status_code=502,
                )
        return {"ok": True, "restarted": True}
    except Exception as exc:
        log.exception("refresh-llm-config proxy: %s", exc)
        return JSONResponse({"ok": False, "error": str(exc)}, status_code=500)


@app.post("/api/refresh-profiles")
async def api_refresh_profiles():
    """Re-fetch les profils depuis le hub et repeuple `_PROFILES_CACHE`.

    A appeler apres modification d'un YAML cote hub + /profiles/reload.
    Pas de restart pod necessaire — le cache est en memoire et le
    prochain turn lira le profil mis a jour.

    Reponse : {ok, count, ids}.
    """
    from agent import qgis_agent as _qa
    try:
        count = await _qa.fetch_profiles_from_hub()
        return {
            "ok":    count > 0,
            "count": count,
            "ids":   list(_qa._PROFILES_CACHE.keys()),
        }
    except Exception as exc:
        log.exception("refresh-profiles: %s", exc)
        return JSONResponse({"ok": False, "error": str(exc)}, status_code=500)


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
    # Court-circuit kube-probe (clone du fix Bug #17 cote hub_home).
    # La readinessProbe sts qgis-agent historique hardcode path:"/" avec
    # timeoutSeconds:1. Sans ce court-circuit, chaque probe declenche queries
    # SQLite + fetch hub (_fetch_active_study_id), ce qui timeout en cold start
    # -> Ready=False pendant 5-17 min. Diagnostique 2026-06-12 (nicolaslaval +
    # rbouzige). Le nouveau sts (cf hub _bootstrap_agent) utilise /health mais
    # les pods existants gardent path:"/" tant que le sts n'est pas re-cree.
    if "kube-probe" in request.headers.get("user-agent", "").lower():
        return Response(content="ok", media_type="text/plain", status_code=200)

    profile_id = request.cookies.get("profile_id", _DEFAULT_PROFILE)
    sessions   = await memory.get_recent_sessions("user", limit=5)
    projects   = await memory.list_projects(profile_id)

    # Reprise de la derniere session liee a l'etude active (Fix Bug B).
    # Le query param ?new=1 force une nouvelle session (pour "Nouvelle
    # conversation" futur). Sinon, si etude active + session anterieure
    # rattachee, on la reprend.
    session_id: str | None = None
    session_resumed: bool = False
    if request.query_params.get("new") != "1":
        active_study_id = await _fetch_active_study_id()
        if active_study_id:
            session_id = await memory.get_latest_session_for_study(
                "user", active_study_id,
            )
            if session_id:
                session_resumed = True
    if not session_id:
        session_id = str(uuid.uuid4())

    return templates.TemplateResponse(request, "chat.html", {
        "profile_id":      profile_id,
        "hub_url":         _HUB_URL,
        "portal_url":      os.getenv("PORTAL_URL", ""),
        "sessions":        sessions,
        "projects":        projects[:5],
        "session_id":      session_id,
        "session_resumed": session_resumed,
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
            # Fix consolidation 2026-06-20 : str(e) etait parfois vide
            # (httpx.RemoteProtocolError sans message, ValueError() etc.) ->
            # UI bloquee sur "Analyse en cours..." indefiniment, user
            # confondait avec hang infini. On ajoute class name + traceback
            # short pour avoir au moins 1 info utile cote browser.
            err_class = type(e).__name__
            err_msg = str(e) or f"{err_class} (no message)"
            log.error("Erreur chat (%s): %s", err_class, e, exc_info=True)
            yield f"data: {json.dumps({'error': err_msg, 'error_class': err_class})}\n\n"

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
# _STT_KEY supprime : lecture dynamique via os.environ a chaque appel STT
# (cf. Option alpha 2026-06-02, webhook /api/reload-llm-key).


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
                headers={"Authorization": f"Bearer {os.environ.get('LLM_API_KEY', '')}"},
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


# ── Routes /desk* et /workspace/wake retirees (A.4) ─────────────────────────
# En prod, le hub sert ces routes via son propre Ingress (Q2 valide 2026-05-30).
# L'agent ne sert que l'iframe chat.html et son API backend (/chat, /sessions/*,
# /memory/*, /context/render/*, /api/refresh-*, /stt). Templates desk.html /
# workspace.html cote agent etaient morts (drift vs hub canon) et supprimes
# avec ce commit.
