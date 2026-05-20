"""
hub.main — FastAPI : hub multi-sessions QgisRemoteMCP sur SSPCloud K8s.

Auth  : token SSPCloud OIDC (Keycloak) en Authorization: Bearer
Sessions : pods K8s éphémères, un par agent (créés automatiquement)
Proxy : forwarding HTTP/SSE vers session pod via DNS K8s interne

Endpoint principal (URL publique SSPCloud) :
  ANY /mcp             → auto-session : crée/récupère la session QGIS de
                         l'utilisateur, puis proxifie vers le MCP.
                         C'est l'URL à mettre dans .mcp.json.

  ANY /mcp/{path}      → idem, avec chemin MCP spécifique

Endpoints de gestion :
  GET  /health
  POST /sessions        → crée une session explicitement
  GET  /sessions        → liste ses sessions
  GET  /sessions/{id}   → état d'une session
  DELETE /sessions/{id} → détruit une session

Admin (ADMIN_USERS env) :
  GET  /admin/sessions
  DELETE /admin/sessions/{id}
"""

from __future__ import annotations

import asyncio
import base64
import hashlib
import logging
import os
import secrets
import time
import urllib.parse
from collections import defaultdict
from contextlib import asynccontextmanager

import httpx
from fastapi import Depends, FastAPI, Form, HTTPException, Request, Response, status
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, StreamingResponse
from fastapi.templating import Jinja2Templates
from pathlib import Path

from hub import auth, sessions
try:
    from hub import profile_manager
    _PROFILES_AVAILABLE = True
except ImportError:
    _PROFILES_AVAILABLE = False
try:
    from hub import audit_trail
    _AUDIT_AVAILABLE = True
except ImportError:
    _AUDIT_AVAILABLE = False
try:
    from hub import s3_publication
    _S3_AVAILABLE = True
except ImportError:
    _S3_AVAILABLE = False
try:
    from hub import studies
    _STUDIES_AVAILABLE = True
except ImportError:
    _STUDIES_AVAILABLE = False

log = logging.getLogger("hub.main")

_MCP_PORT = 8100
_API_PORT = 8080
_SELF_URL  = f"http://127.0.0.1:{os.getenv('HUB_INTERNAL_PORT', '8888')}"

# ── Config GeoAI GPU pod ──────────────────────────────────────────────────────
# Injecté par qgis-mcp.service.yml → serve.env
_GEOAI_GPU_SERVICE = os.getenv("GEOAI_GPU_SERVICE_NAME", "")
_GEOAI_GPU_PORT    = int(os.getenv("GEOAI_GPU_PORT", "8000"))


def _geoai_gpu_base_url() -> str | None:
    """URL K8s interne du pod GeoAI GPU. None si non configuré."""
    if not _GEOAI_GPU_SERVICE:
        return None
    ns = sessions._NAMESPACE or (f"user-{_ONYXIA_USER}" if _ONYXIA_USER else "")
    return f"http://{_GEOAI_GPU_SERVICE}.{ns}.svc.cluster.local:{_GEOAI_GPU_PORT}"


# Codes d'autorisation OAuth en attente (TTL 10 min)
# { auth_code: { api_key, code_challenge, redirect_uri, expires } }
_pending_codes: dict[str, dict] = {}

# Cache en mémoire : username → session_id active
_active_sessions: dict[str, str] = {}
# defaultdict évite la race condition à la création du lock
_session_locks: defaultdict[str, asyncio.Lock] = defaultdict(asyncio.Lock)


# ── Bootstrap agent K8s ────────────────────────────────────────────────────────

_AGENT_IMAGE = "ghcr.io/nic01asfr/qgis-agent:latest"
_K8S_HOST    = "https://kubernetes.default.svc"


async def _bootstrap_agent() -> None:
    """
    Crée le StatefulSet qgis-agent dans le namespace du hub si absent.
    Utilise le ServiceAccount du pod (kubernetes.role: edit requis).
    Lance en tâche de fond au démarrage du hub — non bloquant.
    """
    token_file = Path("/var/run/secrets/kubernetes.io/serviceaccount/token")
    ns_file    = Path("/var/run/secrets/kubernetes.io/serviceaccount/namespace")
    if not token_file.exists():
        log.debug("Bootstrap agent: pas en K8s (dev mode), ignoré")
        return

    await asyncio.sleep(5)  # Laisser le hub démarrer entièrement

    token    = token_file.read_text().strip()
    ns       = ns_file.read_text().strip()
    username = os.getenv("ONYXIA_USER", ns.removeprefix("user-"))
    headers  = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
    host     = f"user-{username}-qgis-agent.user.lab.sspcloud.fr"

    hub_api_key = await auth.create_or_get_api_key(username)

    # Chercher le token LLM SSPCloud dans les secrets du namespace
    # Onyxia injecte SSPCloud_API_KEY dans le secret *-secretextraenv de chaque service
    llm_api_key = os.getenv("SSPCloud_API_KEY", "")

    async with httpx.AsyncClient(verify=False, timeout=15) as client:
        if not llm_api_key:
            try:
                sr = await client.get(
                    f"{_K8S_HOST}/api/v1/namespaces/{ns}/secrets",
                    headers=headers, params={"fieldSelector": "type=Opaque"},
                )
                import base64 as _b64
                for secret in (sr.json().get("items") or []):
                    name = secret.get("metadata", {}).get("name", "")
                    if "secretextraenv" in name:
                        raw = secret.get("data", {}).get("SSPCloud_API_KEY")
                        if raw:
                            llm_api_key = _b64.b64decode(raw).decode()
                            log.info("bootstrap: LLM_API_KEY trouvé dans %s", name)
                            break
            except Exception as exc:
                log.warning("bootstrap: impossible de lire LLM_API_KEY depuis secrets: %s", exc)
        # Vérifier si qgis-agent existe déjà
        r = await client.get(
            f"{_K8S_HOST}/apis/apps/v1/namespaces/{ns}/statefulsets/qgis-agent",
            headers=headers,
        )
        if r.status_code == 200:
            log.info("bootstrap: qgis-agent déjà présent dans %s", ns)
            return

        log.info("bootstrap: création qgis-agent pour %s dans %s", username, ns)

        # StatefulSet
        sts = {
            "apiVersion": "apps/v1", "kind": "StatefulSet",
            "metadata": {"name": "qgis-agent", "namespace": ns,
                         "labels": {"app": "qgis-agent"}},
            "spec": {
                "serviceName": "qgis-agent", "replicas": 1,
                "selector": {"matchLabels": {"app": "qgis-agent"}},
                "template": {
                    "metadata": {"labels": {"app": "qgis-agent"}},
                    "spec": {"containers": [{
                        "name": "agent",
                        "image": _AGENT_IMAGE,
                        "imagePullPolicy": "Always",
                        "command": ["uvicorn", "agent.main:app",
                                    "--host", "0.0.0.0", "--port", "8888"],
                        "ports": [{"containerPort": 8888}],
                        "env": [
                            {"name": "ONYXIA_USER",  "value": username},
                            {"name": "DATA_DIR",     "value": "/home/onyxia/work/qgis-agent-data"},
                            {"name": "HUB_URL",      "value": _HUB_URL},
                            {"name": "HUB_API_KEY",  "value": hub_api_key},
                            {"name": "LLM_API_KEY",  "value": llm_api_key},
                        ],
                        "readinessProbe": {
                            "httpGet": {"path": "/", "port": 8888},
                            "initialDelaySeconds": 30, "periodSeconds": 10,
                            "failureThreshold": 30,
                        },
                    }]},
                },
            },
        }
        r = await client.post(
            f"{_K8S_HOST}/apis/apps/v1/namespaces/{ns}/statefulsets",
            headers=headers, json=sts,
        )
        if r.status_code not in (200, 201):
            log.error("bootstrap StatefulSet qgis-agent: %s %s", r.status_code, r.text[:300])
            return

        # Headless Service (pour le StatefulSet DNS)
        await client.post(f"{_K8S_HOST}/api/v1/namespaces/{ns}/services",
            headers=headers,
            json={"apiVersion": "v1", "kind": "Service",
                  "metadata": {"name": "qgis-agent", "namespace": ns},
                  "spec": {"selector": {"app": "qgis-agent"}, "clusterIP": "None",
                            "ports": [{"port": 8888, "targetPort": 8888}]}})

        # ClusterIP Service (pour l'Ingress)
        await client.post(f"{_K8S_HOST}/api/v1/namespaces/{ns}/services",
            headers=headers,
            json={"apiVersion": "v1", "kind": "Service",
                  "metadata": {"name": "qgis-agent-svc", "namespace": ns},
                  "spec": {"selector": {"app": "qgis-agent"},
                            "ports": [{"port": 8888, "targetPort": 8888}]}})

        # Ingress
        await client.post(
            f"{_K8S_HOST}/apis/networking.k8s.io/v1/namespaces/{ns}/ingresses",
            headers=headers,
            json={"apiVersion": "networking.k8s.io/v1", "kind": "Ingress",
                  "metadata": {"name": "qgis-agent", "namespace": ns,
                               "annotations": {"kubernetes.io/ingress.class": "onyxia"}},
                  "spec": {"ingressClassName": "onyxia", "rules": [{
                      "host": host,
                      "http": {"paths": [{"path": "/", "pathType": "Prefix",
                                          "backend": {"service": {
                                              "name": "qgis-agent-svc",
                                              "port": {"number": 8888}}}}]},
                  }]}})

        log.info("bootstrap: qgis-agent créé — %s", host)


# ── Lifespan ───────────────────────────────────────────────────────────────────

@asynccontextmanager
async def lifespan(app: FastAPI):
    await sessions.init_db()
    await auth.init_apikeys_db()
    await auth._build_jwks_cache()
    if _STUDIES_AVAILABLE:
        await studies.init_db()
    # Restaurer le cache depuis la DB (évite doubles créations après redémarrage hub)
    for s in await sessions.list_sessions():
        if s["status"] == sessions.SESSION_READY:
            _active_sessions[s["owner"]] = s["id"]
    if _active_sessions:
        log.info("Cache restauré : %d session(s) actives", len(_active_sessions))
    task = asyncio.create_task(sessions.cleanup_loop())
    asyncio.create_task(_bootstrap_agent())
    yield
    task.cancel()


app = FastAPI(
    title="QgisRemoteMCP Hub",
    description=(
        "Hub multi-sessions QGIS sur SSPCloud K8s. "
        "POST /mcp avec Authorization: Bearer <token> pour accéder à QGIS."
    ),
    lifespan=lifespan,
)


_ONYXIA_USER = os.getenv("ONYXIA_USER", "")
# HUB_URL : explicite ou dérivé depuis ONYXIA_USER (toujours injecté par SSPCloud)
_HUB_URL = (
    os.getenv("HUB_URL")
    or (f"https://user-{_ONYXIA_USER}-qgis.user.lab.sspcloud.fr"
        if _ONYXIA_USER else "")
)
# AGENT_URL : URL publique du pod agent IA (bootstrappé par le hub au démarrage)
_AGENT_URL = (
    os.getenv("AGENT_URL")
    or (f"https://user-{_ONYXIA_USER}-qgis-agent.user.lab.sspcloud.fr"
        if _ONYXIA_USER else "")
)

# Jinja2 templates pour les pages HTML (desk, workspace)
_TEMPLATES_DIR = Path(__file__).parent.parent / "templates"
_jinja = Jinja2Templates(directory=str(_TEMPLATES_DIR)) if _TEMPLATES_DIR.is_dir() else None

PROFILE_LABELS = {
    "standard": "Standard", "geoai_analyst": "IA Vision",
    "risk_analyst": "Risques", "db_analyst": "Données / DB",
    "storymap_creator": "Storymap", "map_composer": "Cartographe",
    "recipe_creator": "Recettes", "guided_tour": "Guide",
}


async def _desk_context() -> dict:
    """Agrège le contexte workspace depuis les modules internes du hub."""
    ctx: dict = {
        "username": _ONYXIA_USER, "hub_url": _HUB_URL, "agent_url": _AGENT_URL,
        "studies": [], "active_study_id": None, "active_study": None,
        "catalog_items": [], "catalog_count": 0, "profile_labels": PROFILE_LABELS,
        "session_status": "—", "session_ready": False, "novnc_url": "#",
        "insights": [], "insights_count": 0, "preferences": {},
        "recent_treatments": [],
    }
    try:
        api_key = await auth.create_or_get_api_key(_ONYXIA_USER)
        headers = {"Authorization": f"Bearer {api_key}"}
        async with httpx.AsyncClient(timeout=8, base_url=_SELF_URL) as c:
            for path, cb in [
                ("/studies", lambda d: ctx.update(studies=d)),
                ("/studies/active", lambda d: ctx.update(
                    active_study_id=d.get("id"), active_study=d) if d else None),
                ("/sessions", lambda d: ctx.update(
                    session_status={"ready":"✓","sleeping":"💤","starting":"…","error":"⚠"}.get(
                        d[0].get("status","—"), d[0].get("status","—")),
                    session_ready=(d[0].get("status") == "ready"),
                    novnc_url=d[0].get("novnc_url","#")) if d else None),
            ]:
                try:
                    r = await c.get(path, headers=headers)
                    if r.status_code == 200:
                        cb(r.json())
                except Exception:
                    pass
            if ctx.get("active_study_id"):
                try:
                    r = await c.get(f"/catalog/{_ONYXIA_USER}", headers=headers)
                    if r.status_code == 200:
                        items = [i for i in r.json().get("items", [])
                                 if i.get("study_id") == ctx["active_study_id"]][:30]
                        ctx.update(catalog_items=items, catalog_count=len(items))
                except Exception:
                    pass
    except Exception:
        pass
    return ctx


# ── OAuth 2.0 — Client Credentials (pour Claude Desktop connector) ─────────────
# Permet d'utiliser le connecteur MCP distant de Claude Desktop (bêta).
# Dans le dialogue Claude Desktop :
#   URL             → https://user-xxx-qgis-mcp-bridge.user.lab.sspcloud.fr/mcp
#   ID client OAuth → votre username SSPCloud (ex: alice)
#   Secret client   → votre clé API hub (ex: qgis_alice_xxx...)

@app.get("/.well-known/oauth-authorization-server")
async def oauth_metadata():
    """Découverte OAuth selon MCP spec 2025 — utilisé par Claude Desktop."""
    base = _HUB_URL or ""
    return JSONResponse({
        "issuer":                            base,
        "authorization_endpoint":            f"{base}/authorize",
        "token_endpoint":                    f"{base}/oauth/token",
        "grant_types_supported":             ["authorization_code", "client_credentials"],
        "response_types_supported":          ["code"],
        "code_challenge_methods_supported":  ["S256"],
        "token_endpoint_auth_methods_supported": ["client_secret_post", "none"],
    })


@app.get("/authorize", response_class=HTMLResponse)
async def oauth_authorize(
    request: Request,
    response_type: str = "code",
    client_id: str = "",
    redirect_uri: str = "",
    state: str = "",
    code_challenge: str = "",
    code_challenge_method: str = "S256",
):
    """
    Endpoint d'autorisation OAuth — Claude Desktop redirige ici.
    Si le cookie hub_api_key est valide → autorisation automatique.
    Sinon → formulaire de saisie de la clé API.
    """
    # Nettoyer les codes expirés
    now = time.time()
    for k in [k for k, v in _pending_codes.items() if v["expires"] < now]:
        del _pending_codes[k]

    # Vérifier si déjà connecté via cookie
    cookie_key = request.cookies.get("hub_api_key", "")
    user = await auth._validate_api_key(cookie_key) if cookie_key else None

    if user:
        return _issue_auth_code(user, cookie_key, code_challenge, redirect_uri, state)

    # Pas de cookie → formulaire de saisie
    params = urllib.parse.urlencode({
        "response_type": response_type, "client_id": client_id,
        "redirect_uri": redirect_uri, "state": state,
        "code_challenge": code_challenge, "code_challenge_method": code_challenge_method,
    })
    return HTMLResponse(f"""<!DOCTYPE html><html lang="fr">
<head><meta charset="UTF-8"><title>QgisRemoteMCP — Autorisation</title>
<script src="https://cdn.tailwindcss.com"></script></head>
<body class="bg-gray-50 flex items-center justify-center min-h-screen">
<div class="bg-white rounded-xl border border-gray-200 p-8 w-full max-w-md space-y-4">
  <div class="text-center"><span class="text-3xl">🗺</span>
    <h1 class="font-semibold text-gray-900 mt-2">Autoriser Claude Desktop</h1>
    <p class="text-sm text-gray-500">Saisissez votre clé API hub pour autoriser l'accès à QGIS.</p>
  </div>
  <form action="/authorize/confirm?{params}" method="POST" class="space-y-3">
    <input name="api_key" type="password" required
      placeholder="qgis_votrenom_..."
      class="w-full border border-gray-300 rounded-lg px-3 py-2 text-sm font-mono
             focus:outline-none focus:ring-2 focus:ring-blue-500"/>
    <p class="text-xs text-gray-400">
      Votre clé API est disponible sur le portail CEREMA ou via
      <code>POST /auth/apikey</code> avec votre token SSPCloud.
    </p>
    <button type="submit"
      class="w-full bg-blue-600 text-white py-2 rounded-lg text-sm font-medium hover:bg-blue-700">
      Autoriser →
    </button>
  </form>
</div></body></html>""")


@app.post("/authorize/confirm", response_class=HTMLResponse)
async def oauth_authorize_confirm(
    request: Request,
    api_key: str = Form(...),
    response_type: str = "code",
    client_id: str = "",
    redirect_uri: str = "",
    state: str = "",
    code_challenge: str = "",
    code_challenge_method: str = "S256",
):
    """Traite la saisie de la clé API et émet le code d'autorisation."""
    key = api_key.strip()
    user = await auth._validate_api_key(key)
    if not user:
        return HTMLResponse("<p>Clé invalide.</p>", status_code=401)

    resp = _issue_auth_code(user, key, code_challenge, redirect_uri, state)
    # Poser le cookie pour éviter la saisie la prochaine fois
    resp.set_cookie("hub_api_key", key, httponly=True, secure=True,
                    max_age=90 * 24 * 3600, samesite="lax")
    return resp


def _issue_auth_code(user: dict, api_key: str, code_challenge: str,
                     redirect_uri: str, state: str) -> RedirectResponse:
    """Génère un code d'autorisation et redirige vers Claude.ai."""
    code = secrets.token_urlsafe(32)
    _pending_codes[code] = {
        "api_key":        api_key,
        "code_challenge": code_challenge,
        "redirect_uri":   redirect_uri,
        "expires":        time.time() + 600,
    }
    params = {"code": code}
    if state:
        params["state"] = state
    return RedirectResponse(f"{redirect_uri}?{urllib.parse.urlencode(params)}", status_code=302)


@app.post("/oauth/token")
async def oauth_token(
    grant_type:    str = Form(...),
    code:          str = Form(None),
    code_verifier: str = Form(None),
    redirect_uri:  str = Form(None),
    client_id:     str = Form(None),
    client_secret: str = Form(None),
):
    """Échange un code (Authorization Code) ou valide client_credentials."""

    if grant_type == "authorization_code":
        if not code or code not in _pending_codes:
            raise HTTPException(400, "Code invalide ou expiré")
        pending = _pending_codes.pop(code)
        if time.time() > pending["expires"]:
            raise HTTPException(400, "Code expiré")

        # Valider PKCE
        if code_verifier and pending.get("code_challenge"):
            challenge = base64.urlsafe_b64encode(
                hashlib.sha256(code_verifier.encode()).digest()
            ).rstrip(b"=").decode()
            if challenge != pending["code_challenge"]:
                raise HTTPException(400, "PKCE invalide")

        return JSONResponse({
            "access_token": pending["api_key"],
            "token_type":   "bearer",
            "expires_in":   90 * 24 * 3600,
        })

    elif grant_type == "client_credentials":
        if not client_secret:
            raise HTTPException(400, "client_secret requis")
        user = await auth._validate_api_key(client_secret)
        if not user:
            raise HTTPException(401, "client_secret invalide")
        return JSONResponse({
            "access_token": client_secret,
            "token_type":   "bearer",
            "expires_in":   90 * 24 * 3600,
        })

    raise HTTPException(400, f"grant_type non supporté: {grant_type}")


# ── Healthcheck ────────────────────────────────────────────────────────────────

@app.get("/")
async def root():
    """Readiness probe Kubernetes — 200 sans auth."""
    return {"status": "ok", "service": "qgis-mcp-hub"}


@app.get("/health")
async def health():
    return {
        "status":    "ok",
        "service":   "qgis-mcp-hub",
        "geoai_gpu": bool(_GEOAI_GPU_SERVICE),
        "profiles":  _PROFILES_AVAILABLE,
    }


# ── Profils QGIS ───────────────────────────────────────────────────────────────

@app.get("/profiles")
async def list_profiles(user: dict = Depends(auth.get_current_user)):
    """Liste les profils disponibles (métadonnées)."""
    if not _PROFILES_AVAILABLE:
        return []
    return profile_manager.list_profiles()


@app.get("/profiles/{profile_id}")
async def get_profile(profile_id: str, user: dict = Depends(auth.get_current_user)):
    """Retourne un profil complet par ID."""
    if not _PROFILES_AVAILABLE:
        raise HTTPException(404, "Système de profils non disponible")
    p = profile_manager.get_profile(profile_id)
    if p.get("id") != profile_id:
        raise HTTPException(404, f"Profil '{profile_id}' introuvable")
    # Ne pas exposer le system prompt complet via l'API publique
    return {k: v for k, v in p.items() if k != "agent_system_prompt"}


@app.post("/profiles/reload")
async def reload_profiles(user: dict = Depends(auth.get_current_user)):
    """Force le rechargement des profils YAML (hot reload)."""
    if not _PROFILES_AVAILABLE:
        raise HTTPException(503, "Système de profils non disponible")
    profile_manager.reload()
    return {"profiles": profile_manager.list_profiles()}


# ── Templates servis aux pods de session (storymap DSFR, etc.) ────────────────
# Les agents et le MCP execute_python peuvent fetcher ces modules pour les
# importer dans /data/templates/ et les utiliser depuis QGIS.

_AVAILABLE_TEMPLATES = {
    "storymap_dsfr.py": "hub/storymap_dsfr.py",
}


@app.get("/templates")
async def list_templates(user: dict = Depends(auth.get_current_user)):
    """Liste les templates servis par le hub."""
    return {"templates": list(_AVAILABLE_TEMPLATES.keys())}


@app.get("/templates/{name}")
async def get_template(name: str, user: dict = Depends(auth.get_current_user)):
    """
    Sert un template Python (storymap_dsfr.py...) en text/x-python.
    Bootstrap typique côté agent :
      curl -H "Authorization: Bearer $KEY" $HUB_URL/templates/storymap_dsfr.py \\
           -o /data/templates/storymap_dsfr.py
    """
    rel = _AVAILABLE_TEMPLATES.get(name)
    if not rel:
        raise HTTPException(404, f"Template '{name}' inconnu")
    path = os.path.join(os.path.dirname(__file__), "..", rel)
    path = os.path.abspath(path)
    if not os.path.isfile(path):
        raise HTTPException(404, f"Fichier introuvable: {rel}")
    with open(path, "r", encoding="utf-8") as f:
        return Response(content=f.read(), media_type="text/x-python; charset=utf-8")


# ── GeoAI — proxy vers pod GPU (SAM3, DeepForest, OmniWater) ─────────────────
# Le hub centralise l'accès au GPU pod via GPURelay (scale 0→1→0 transparent).
# Les session pods appellent le hub /geoai/* — ils n'ont pas accès direct au GPU.

@app.api_route("/geoai/{path:path}", methods=["GET", "POST"])
async def geoai_proxy(
    path: str,
    request: Request,
    user: dict = Depends(auth.get_current_user),
):
    """
    Proxy vers le pod GPU GeoAI.

    Le GPURelay réveille le pod si dormant (scale 0→1, ~35s cold start).
    Les modèles sont sur PVC — pas de re-téléchargement après le 1er boot.

    Endpoints disponibles (selon modèles activés) :
      GET  /geoai/health             → statut GPU pod + modèles chargés
      GET  /geoai/models             → liste modèles disponibles
      POST /geoai/segment            → SAM3 segmentation (raster → vecteur)
      POST /geoai/detect             → DeepForest / OmniWater détection
      POST /geoai/classify           → Classification sémantique
    """
    base_url = _geoai_gpu_base_url()
    if not base_url:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="GPU GeoAI non configuré — activez l'option IA Vision au déploiement",
        )

    target_url = f"{base_url}/{path}"

    # Réveiller le GPU pod via GPURelay (scale 0→1 si nécessaire)
    loop = asyncio.get_event_loop()
    try:
        await loop.run_in_executor(None, _ensure_geoai_gpu_ready, base_url)
    except Exception as exc:
        log.warning("GPU GeoAI non disponible: %s", exc)
        raise HTTPException(503, f"GPU GeoAI indisponible: {exc}")

    # Forwarder la requête (supporte multipart pour upload de fichiers)
    return await _proxy_to_geoai(request, target_url)


def _ensure_geoai_gpu_ready(base_url: str, timeout: int = 180) -> None:
    """
    Vérifie que le GPU pod est prêt. Le réveille via GPURelay si dormant.
    Bloquant — appeler dans run_in_executor.
    """
    import time
    import urllib.request

    # Test rapide : le pod répond déjà ?
    try:
        urllib.request.urlopen(f"{base_url}/health", timeout=5)
        return  # Déjà actif
    except Exception:
        pass  # Dormant → réveiller

    if not _GEOAI_GPU_SERVICE:
        raise RuntimeError("GEOAI_GPU_SERVICE_NAME non configuré")

    # Scale up via kubectl (GPURelay pattern)
    import subprocess
    ns = sessions._NAMESPACE or (f"user-{_ONYXIA_USER}" if _ONYXIA_USER else "default")

    log.info("Réveil GPU GeoAI pod %s...", _GEOAI_GPU_SERVICE)
    # kubectl patch replicas (statefulsets/scale bloqué SSPCloud → patch direct)
    r = subprocess.run(
        ["kubectl", "patch", "statefulset",
         f"{_GEOAI_GPU_SERVICE}-jupyter-pytorch-gpu",
         "-n", ns, "--type=merge",
         "-p", '{"spec":{"replicas":1}}'],
        capture_output=True, timeout=15,
    )
    if r.returncode != 0:
        log.warning("kubectl patch replicas: %s", r.stderr[:200])

    # Attendre que le pod soit prêt
    deadline = time.time() + timeout
    while time.time() < deadline:
        time.sleep(5)
        try:
            urllib.request.urlopen(f"{base_url}/health", timeout=5)
            log.info("GPU GeoAI pod prêt")
            return
        except Exception:
            pass

    raise TimeoutError(f"GPU GeoAI pod non prêt après {timeout}s")


async def _proxy_to_geoai(request: Request, target_url: str) -> Response:
    """Proxy HTTP vers le pod GeoAI (supporte multipart/form-data pour upload)."""
    _skip = {"host", "connection", "transfer-encoding", "te", "trailers", "upgrade"}
    headers = {k: v for k, v in request.headers.items() if k.lower() not in _skip}

    body = await request.body()
    params = dict(request.query_params)

    client = httpx.AsyncClient(timeout=httpx.Timeout(600.0, connect=15.0))
    try:
        req = client.build_request(
            request.method, target_url,
            headers=headers, content=body, params=params,
        )
        resp = await client.send(req, stream=True)
    except httpx.ConnectError as exc:
        await client.aclose()
        raise HTTPException(503, f"GPU GeoAI inaccessible: {exc}")

    content_type = resp.headers.get("content-type", "application/octet-stream")
    _skip_resp = {"content-encoding", "transfer-encoding", "content-length", "connection"}
    resp_headers = {k: v for k, v in resp.headers.items() if k.lower() not in _skip_resp}

    async def stream_and_close():
        try:
            async for chunk in resp.aiter_bytes(4096):
                yield chunk
        finally:
            await resp.aclose()
            await client.aclose()

    return StreamingResponse(
        stream_and_close(),
        status_code=resp.status_code,
        media_type=content_type,
        headers=resp_headers,
    )


# ── Login navigateur via clé API dans l'URL ──────────────────────────────────

@app.get("/login", response_class=HTMLResponse)
async def hub_login(key: str = "", request: Request = None):
    """
    Permet l'accès navigateur au hub via la clé API dans l'URL.
    Usage : https://user-xxx-qgis-mcp-bridge.../login?key=qgis_xxx_...
    Pose un cookie httponly et redirige vers GET /.
    """
    if not key or not key.startswith("qgis_"):
        return HTMLResponse("""<!DOCTYPE html><html><body style="font-family:sans-serif;text-align:center;margin-top:80px">
<h2>🗺 QgisRemoteMCP</h2>
<p>Accès : <code>https://votre-hub/login?key=votre_cle_api</code></p>
<p style="color:#888">La clé API est disponible sur le portail d'onboarding.</p>
</body></html>""")

    user = await auth._validate_api_key(key)
    if not user:
        return HTMLResponse("<p>Clé invalide ou expirée.</p>", status_code=401)

    response = RedirectResponse("/", status_code=302)
    response.set_cookie(
        "hub_api_key", key,
        httponly=True, secure=True, max_age=90 * 24 * 3600, samesite="lax",
    )
    return response


# ── Interface web hub ─────────────────────────────────────────────────────────

@app.get("/", response_class=HTMLResponse)
async def hub_home(request: Request):
    """Page d'accueil — readiness probe K8s (200 sans auth) + redirect vers /desk."""
    return RedirectResponse("/desk", status_code=302)
    username = user["username"]
    all_sessions = await sessions.list_sessions(username)
    active = [s for s in all_sessions if s["status"] == sessions.SESSION_READY]
    # noVNC URL avec paramètres optimisés :
    # autoconnect=1 : connexion automatique sans clic
    # reconnect=1 : reconnexion auto en cas de coupure
    # resize=remote : QGIS s'adapte à la fenêtre navigateur
    # show_dot=false : pas de curseur VNC visible
    # logging=warn : pas de messages debug côté client
    _novnc_base = sessions.novnc_url(active[0]["id"]) if active else None
    novnc = (
        f"{_novnc_base}?autoconnect=1&reconnect=1&resize=remote&show_dot=false&logging=warn"
        if _novnc_base else None
    )

    html = f"""<!DOCTYPE html>
<html lang="fr"><head><meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>QGIS — {username}</title>
<script src="https://cdn.tailwindcss.com"></script>
</head><body class="bg-gray-50 min-h-screen">
<header class="bg-white border-b border-gray-200 px-6 py-3 flex items-center justify-between">
  <div class="flex items-center gap-2">
    <span class="text-xl">🗺</span>
    <span class="font-semibold text-gray-800">QgisRemoteMCP</span>
  </div>
  <span class="text-sm text-gray-500 bg-gray-100 px-3 py-1 rounded-full">{username}</span>
</header>
<main class="max-w-2xl mx-auto px-6 py-8 space-y-4">
  {"" if not novnc else f'''
  <div class="bg-white rounded-xl border border-gray-200 overflow-hidden">
    <div class="px-4 py-2 bg-gray-50 border-b border-gray-100 flex items-center justify-between">
      <span class="text-sm font-medium text-gray-700">🖥 Bureau QGIS Desktop</span>
      <a href="{novnc}" target="_blank"
         class="text-xs text-blue-600 hover:underline">Ouvrir en plein écran →</a>
    </div>
    <iframe src="{novnc}" class="w-full" style="height:600px" allow="fullscreen"></iframe>
  </div>
  '''}
  {"" if novnc else f'''
  <div class="bg-white rounded-xl border border-gray-200 p-6 text-center space-y-4">
    <div class="text-4xl">🖥</div>
    <div>
      <p class="font-semibold text-gray-900">Aucune session QGIS active</p>
      <p class="text-xs text-gray-400 mt-1">
        Une session démarre en ~35s. Elle reste active 2h après le dernier usage.
      </p>
    </div>
    <button
      onclick="startSession(this)"
      class="w-full bg-blue-600 text-white py-2.5 rounded-lg font-medium text-sm
             hover:bg-blue-700 transition disabled:opacity-50 disabled:cursor-wait">
      ▶ Démarrer ma session QGIS
    </button>
    <p id="session-status" class="text-xs text-gray-500 hidden"></p>
  </div>
  <script>
  async function startSession(btn) {{
    btn.disabled = true;
    btn.textContent = "Démarrage en cours...";
    const status = document.getElementById("session-status");
    status.classList.remove("hidden");
    status.textContent = "Création de la session (~35s)...";
    try {{
      const r = await fetch("/sessions", {{
        method: "POST",
        headers: {{"Content-Type": "application/json"}},
        body: "{{}}"
      }});
      if (r.ok) {{
        status.textContent = "Session créée — chargement QGIS...";
        // Recharger la page après 20s pour afficher le bureau
        setTimeout(() => location.reload(), 20000);
        // Poll jusqu'à ce que noVNC soit prêt
        let attempts = 0;
        const poll = setInterval(async () => {{
          attempts++;
          const check = await fetch("/sessions");
          const sessions = await check.json();
          const ready = sessions.find(s => s.status === "ready");
          if (ready) {{
            clearInterval(poll);
            status.textContent = "Session prête ! Rechargement...";
            setTimeout(() => location.reload(), 1500);
          }} else if (attempts > 24) {{
            clearInterval(poll);
            location.reload();
          }}
          status.textContent = `Démarrage QGIS... (${{attempts * 5}}s)`;
        }}, 5000);
      }} else {{
        const err = await r.json();
        status.textContent = "Erreur : " + (err.detail || r.status);
        btn.disabled = false;
        btn.textContent = "▶ Démarrer ma session QGIS";
      }}
    }} catch(e) {{
      status.textContent = "Erreur réseau : " + e.message;
      btn.disabled = false;
      btn.textContent = "▶ Démarrer ma session QGIS";
    }}
  }}
  </script>
  '''}
  <div class="grid grid-cols-2 gap-3">
    <div class="bg-white rounded-lg border border-gray-200 p-4">
      <div class="font-medium text-gray-900 text-sm mb-2">📁 Fichiers /data</div>
      <a href="/sessions" class="text-xs text-blue-600 hover:underline">Voir mes sessions →</a>
    </div>
    <div class="bg-white rounded-lg border border-gray-200 p-4">
      <div class="font-medium text-gray-900 text-sm mb-2">🔑 Ma clé API</div>
      <a href="/auth/apikey" class="text-xs text-blue-600 hover:underline">
        Générer / voir ma clé →</a>
    </div>
  </div>
</main></body></html>"""
    return HTMLResponse(html)


@app.get("/sessions/{session_id}/treatments")
async def get_session_treatments(
    session_id: str,
    since: float | None = None,
    kinds: str | None = None,
    limit: int = 200,
    user: dict = Depends(auth.get_current_user),
):
    """
    Lit l'audit trail d'une session (treatments.jsonl) via execute_python
    sur le pod de session.

    Paramètres :
      since  : timestamp UNIX, ne retourne que les évènements après cette date
      kinds  : filtre CSV (ex: "processing,export") — défaut tous
      limit  : nombre max d'évènements retournés (défaut 200)
    """
    s = await sessions.get_session(session_id, user["username"])
    if not s:
        raise HTTPException(404, "Session introuvable")
    if s["status"] != sessions.SESSION_READY:
        raise HTTPException(503, f"Session non prête ({s['status']})")

    kinds_list = [k.strip() for k in kinds.split(",")] if kinds else None
    # On exécute la lecture côté pod (qui a accès au PVC user).
    # NOTE : le MCP execute_python ne capture PAS la variable `result`
    # (toujours {} dans la réponse), mais il capture stdout. On encadre
    # donc le payload JSON avec des marqueurs pour extraction fiable.
    code = f"""
import json, os
from pathlib import Path
p = Path(os.getenv("QGIS_TREATMENTS_LOG", "/data/agent/treatments.jsonl"))
events = []
if p.exists():
    with open(p, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line: continue
            try: events.append(json.loads(line))
            except Exception: pass
since = {repr(since)}
kinds = {repr(kinds_list)}
if since is not None:
    events = [e for e in events if e.get("ts", 0) >= since]
if kinds:
    events = [e for e in events if e.get("kind") in kinds]
out = {{"count": len(events), "events": events[-{int(limit)}:]}}
print("<<<TREATMENTS>>>" + json.dumps(out) + "<<<END>>>")
"""
    payload = {
        "jsonrpc": "2.0", "id": "get-treatments",
        "method": "tools/call",
        "params": {"name": "execute_python", "arguments": {"code": code}},
    }
    key = await auth.create_or_get_api_key(user["username"])
    async with httpx.AsyncClient(timeout=30) as client:
        resp = await client.post(
            _mcp_url(s),
            json=payload,
            headers={"Authorization": f"Bearer {key}"},
        )
    data = resp.json()
    content = data.get("result", {}).get("content", [{}])
    text = content[0].get("text", "") if content else ""
    import json as _json
    # Le payload réel est dans le stdout de l'exécution Python,
    # qui est sérialisé dans le champ "stdout" du wrapper MCP.
    try:
        wrapper = _json.loads(text)
        stdout = wrapper.get("stdout", "")
    except Exception:
        stdout = text
    start = stdout.find("<<<TREATMENTS>>>")
    end = stdout.find("<<<END>>>")
    if start < 0 or end < 0:
        raise HTTPException(500, f"Marqueurs absents dans stdout: {stdout[:200]}")
    return _json.loads(stdout[start + len("<<<TREATMENTS>>>"):end])


# ── Études (sessions logiques par user) ──────────────────────────────────────

async def _execute_python_in_workspace(owner: str, code: str, timeout: int = 30) -> str:
    """Helper : exécute du Python sur le workspace de l'owner, renvoie stdout."""
    s = await _get_or_create_session(owner)
    api_key = await auth.create_or_get_api_key(owner)
    payload = {
        "jsonrpc": "2.0", "id": "studies-exec",
        "method": "tools/call",
        "params": {"name": "execute_python", "arguments": {"code": code}},
    }
    async with httpx.AsyncClient(timeout=timeout) as client:
        resp = await client.post(
            _mcp_url(s), json=payload,
            headers={"Authorization": f"Bearer {api_key}"},
        )
    import json as _json
    data = resp.json()
    content = data.get("result", {}).get("content", [{}])
    text = content[0].get("text", "") if content else ""
    try:
        wrapper = _json.loads(text)
        return wrapper.get("stdout", "")
    except Exception:
        return text


@app.post("/studies", status_code=status.HTTP_201_CREATED)
async def create_study(
    request: Request,
    user: dict = Depends(auth.get_current_user),
):
    """
    Crée une nouvelle étude. Body : {"name": "...", "profile": "..."}.
    Initialise le layout sur le PVC workspace (/data/studies/{sid}/).
    """
    if not _STUDIES_AVAILABLE:
        raise HTTPException(503, "Module studies indisponible")
    try:
        body = await request.json()
    except Exception:
        body = {}
    name = body.get("name", "").strip() or "Étude sans nom"
    profile = body.get("profile", "standard")

    s = await studies.create_study(user["username"], name=name, profile=profile)
    # Initialiser le layout sur le pod (mkdir + meta.json)
    try:
        await _execute_python_in_workspace(
            user["username"],
            studies.init_pod_layout_code(s["id"], s["name"], s["profile"]),
        )
    except Exception as exc:
        log.warning("Init layout étude %s : %s", s["id"], exc)
    return s


@app.get("/studies")
async def list_studies_endpoint(
    archived: bool = False,
    user: dict = Depends(auth.get_current_user),
):
    """Liste les études de l'user. archived=true pour inclure les archivées."""
    if not _STUDIES_AVAILABLE:
        return []
    return await studies.list_studies(user["username"], include_archived=archived)


@app.get("/studies/active")
async def get_active_study_endpoint(
    user: dict = Depends(auth.get_current_user),
):
    """Étude active de l'user (depuis DB hub)."""
    if not _STUDIES_AVAILABLE:
        raise HTTPException(503, "Module studies indisponible")
    active_id = await studies.get_active_study_id(user["username"])
    if not active_id:
        return None
    return await studies.get_study(active_id, user["username"])


@app.get("/studies/{sid}")
async def get_study_endpoint(
    sid: str,
    user: dict = Depends(auth.get_current_user),
):
    if not _STUDIES_AVAILABLE:
        raise HTTPException(503, "Module studies indisponible")
    s = await studies.get_study(sid, user["username"])
    if not s:
        raise HTTPException(404, "Étude introuvable")
    return s


@app.patch("/studies/{sid}")
async def update_study_endpoint(
    sid: str,
    request: Request,
    user: dict = Depends(auth.get_current_user),
):
    """Met à jour name, profile, project_path, conversation_id, status."""
    if not _STUDIES_AVAILABLE:
        raise HTTPException(503, "Module studies indisponible")
    existing = await studies.get_study(sid, user["username"])
    if not existing:
        raise HTTPException(404, "Étude introuvable")
    body = await request.json() if request else {}
    return await studies.update_study(sid, **body)


@app.post("/studies/{sid}/activate")
async def activate_study(
    sid: str,
    user: dict = Depends(auth.get_current_user),
):
    """Définit l'étude active de l'user (DB hub + fichier sentinel sur pod).

    Avant de charger la nouvelle étude, on sauvegarde le projet QGIS de
    l'étude SORTANTE si elle existe et qu'elle est encore liée au workspace.
    Sans ce hook, switcher A → B → A perd tout le travail de A.
    """
    if not _STUDIES_AVAILABLE:
        raise HTTPException(503, "Module studies indisponible")
    s = await studies.get_study(sid, user["username"])
    if not s:
        raise HTTPException(404, "Étude introuvable")
    if s["status"] != "active":
        raise HTTPException(400, f"Étude archivée (status={s['status']})")

    # Save de l'étude sortante avant le switch (si différente et non-None)
    prev_sid = await studies.get_active_study_id(user["username"])
    if prev_sid and prev_sid != sid:
        try:
            await _execute_python_in_workspace(
                user["username"], studies.save_active_pod_code(prev_sid),
            )
        except Exception as exc:
            log.warning("Save étude sortante %s avant switch : %s", prev_sid, exc)

    await studies.set_active_study(user["username"], sid)
    await studies.touch_study(sid)
    try:
        await _execute_python_in_workspace(
            user["username"], studies.activate_pod_code(sid),
        )
    except Exception as exc:
        log.warning("Activation sentinel étude %s : %s", sid, exc)
    return {"active_study": sid, "study": s}


@app.post("/studies/{sid}/save")
async def save_study(
    sid: str,
    user: dict = Depends(auth.get_current_user),
):
    """
    Sauvegarde explicite du projet QGIS de l'étude `sid` (write du .qgz).
    Appelable depuis l'UI (bouton, beforeunload sendBeacon) et par l'agent.
    Ne fait rien si le projet courant n'est pas lié à cette étude.
    """
    if not _STUDIES_AVAILABLE:
        raise HTTPException(503, "Module studies indisponible")
    s = await studies.get_study(sid, user["username"])
    if not s:
        raise HTTPException(404, "Étude introuvable")
    try:
        out = await _execute_python_in_workspace(
            user["username"], studies.save_active_pod_code(sid),
        )
        log.info("Save étude %s : %s", sid, (out or "").strip()[:300])
    except Exception as exc:
        log.warning("Save étude %s ERR : %s", sid, exc)
        return {"saved": False, "error": str(exc)}
    return {"saved": True, "sid": sid, "output": (out or "").strip()[:500]}


@app.get("/studies/{sid}/publications")
async def list_study_publications(
    sid: str,
    user: dict = Depends(auth.get_current_user),
):
    """
    Liste les publications liées à l'étude `sid`.
    Filtre le catalogue user sur `study_id == sid`.
    """
    if not _STUDIES_AVAILABLE:
        raise HTTPException(503, "Module studies indisponible")
    if not _S3_AVAILABLE:
        return {"publications": []}
    s = await studies.get_study(sid, user["username"])
    if not s:
        raise HTTPException(404, "Étude introuvable")
    try:
        catalog = s3_publication.get_catalog(user["username"])
    except Exception as exc:
        log.warning("get_catalog failed: %s", exc)
        catalog = []
    matched = [item for item in catalog if item.get("study_id") == sid]
    return {"sid": sid, "count": len(matched), "publications": matched}


@app.get("/studies/{sid}/export")
async def export_study(
    sid: str,
    user: dict = Depends(auth.get_current_user),
):
    """
    Téléchargement du bundle étude au format ZIP : project.qgz + data/ +
    notes.md + treatments.jsonl + exports/. Le ZIP est généré côté workspace
    pod (là où vivent les fichiers PVC) puis streamé.

    Phase 12 : ce ZIP est autoportant grâce à l'adoption des données par
    save_active_pod_code — les chemins du .qgz pointent vers data/ relatif.
    """
    if not _STUDIES_AVAILABLE:
        raise HTTPException(503, "Module studies indisponible")
    s = await studies.get_study(sid, user["username"])
    if not s:
        raise HTTPException(404, "Étude introuvable")

    # Force un save propre avant export pour adopter les données récentes.
    try:
        await _execute_python_in_workspace(
            user["username"], studies.save_active_pod_code(sid),
        )
    except Exception as exc:
        log.warning("Save préalable à l'export étude %s : %s", sid, exc)

    # Génération du ZIP côté workspace (où le PVC est monté)
    zip_code = f"""
import zipfile, base64, os
from pathlib import Path
sid = {sid!r}
study_dir = Path(f"/data/studies/{{sid}}")
zip_path = Path(f"/tmp/study_{{sid}}.zip")
zip_path.unlink(missing_ok=True)
with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as z:
    for root, _, files in os.walk(study_dir):
        for f in files:
            full = Path(root) / f
            arc = full.relative_to(study_dir.parent)  # zip racine = {{sid}}/
            z.write(full, arc)
size = zip_path.stat().st_size
print(f"ZIP_READY size={{size}} path={{zip_path}}")
"""
    try:
        out = await _execute_python_in_workspace(user["username"], zip_code)
        log.info("Export étude %s : %s", sid, (out or "").strip()[:200])
    except Exception as exc:
        raise HTTPException(500, f"Génération ZIP : {exc}")

    # Stream le ZIP depuis le workspace en plusieurs chunks base64 sur stdout.
    # `_execute_python_in_workspace` ne récupère que stdout, donc on print
    # le b64 délimité par des sentinelles pour le parser sans ambiguïté.
    read_code = f"""
import base64
from pathlib import Path
p = Path("/tmp/study_{sid}.zip")
if not p.exists():
    print("ERROR: zip not found")
else:
    print(f"ZIP_SIZE={{p.stat().st_size}}")
    print("---B64_START---")
    print(base64.b64encode(p.read_bytes()).decode())
    print("---B64_END---")
"""
    try:
        raw = await _execute_python_in_workspace(user["username"], read_code, timeout=60)
    except Exception as exc:
        raise HTTPException(500, f"Lecture ZIP : {exc}")

    # Extraire le b64 entre les sentinelles
    import re as _re
    match = _re.search(r"---B64_START---\s*(.*?)\s*---B64_END---", raw or "", _re.DOTALL)
    if not match:
        raise HTTPException(500, f"ZIP vide ou erreur (out={(raw or '')[:300]})")
    b64 = match.group(1).replace("\n", "").replace(" ", "")

    import base64
    zip_bytes = base64.b64decode(b64)
    from fastapi.responses import Response
    return Response(
        content=zip_bytes,
        media_type="application/zip",
        headers={
            "Content-Disposition": f'attachment; filename="etude_{sid}.zip"',
        },
    )


@app.delete("/studies/{sid}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_study_endpoint(
    sid: str,
    purge: bool = False,
    user: dict = Depends(auth.get_current_user),
):
    """
    Par défaut : archive (soft delete, fichiers préservés sur PVC).
    purge=true : suppression totale (DB + dossier /data/studies/{sid}/).
    """
    if not _STUDIES_AVAILABLE:
        raise HTTPException(503, "Module studies indisponible")
    s = await studies.get_study(sid, user["username"])
    if not s:
        raise HTTPException(404, "Étude introuvable")
    if purge:
        try:
            await _execute_python_in_workspace(
                user["username"], studies.purge_pod_layout_code(sid),
            )
        except Exception as exc:
            log.warning("Purge layout étude %s : %s", sid, exc)
        await studies.purge_study(sid)
    else:
        await studies.archive_study(sid)


@app.get("/studies/{sid}/treatments")
async def get_study_treatments(
    sid: str,
    user: dict = Depends(auth.get_current_user),
    since: float | None = None,
    kinds: str | None = None,
    limit: int = 200,
):
    """Lit l'audit trail propre à une étude (depuis /data/studies/{sid}/treatments.jsonl)."""
    if not _STUDIES_AVAILABLE:
        raise HTTPException(503, "Module studies indisponible")
    s = await studies.get_study(sid, user["username"])
    if not s:
        raise HTTPException(404, "Étude introuvable")
    kinds_list = [k.strip() for k in kinds.split(",")] if kinds else None
    code = f"""
import json, os
from pathlib import Path
p = Path("/data/studies/{sid}/treatments.jsonl")
events = []
if p.exists():
    with open(p, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line: continue
            try: events.append(json.loads(line))
            except Exception: pass
since = {repr(since)}
kinds = {repr(kinds_list)}
if since is not None:
    events = [e for e in events if e.get("ts", 0) >= since]
if kinds:
    events = [e for e in events if e.get("kind") in kinds]
out = {{"count": len(events), "events": events[-{int(limit)}:]}}
print("<<<TREATMENTS>>>" + json.dumps(out) + "<<<END>>>")
"""
    stdout = await _execute_python_in_workspace(user["username"], code)
    start = stdout.find("<<<TREATMENTS>>>")
    end = stdout.find("<<<END>>>")
    if start < 0 or end < 0:
        raise HTTPException(500, f"Marqueurs absents: {stdout[:200]}")
    import json as _json
    return _json.loads(stdout[start + len("<<<TREATMENTS>>>"):end])


@app.get("/published/{owner}/{slug}")
@app.get("/published/{owner}/{kind}/{slug}")
async def serve_published(
    owner: str,
    slug: str,
    kind: str = "storymap",
    request: Request = None,
):
    """
    Sert une publication permanente depuis S3 (MinIO SSPCloud).

    URL canonique : /published/{owner}/{kind}/{slug}
    Compat       : /published/{owner}/{slug}  → kind=storymap par défaut

    Public, pas d'auth requise. La publication doit avoir été créée via
    POST /publish (pousse PVC workspace → s3://{bucket}/qgis-workspace/published/).

    Si on a un fallback à faire (workspace plus actif, S3 indisponible), c'est
    transparent côté user.
    """
    if not _S3_AVAILABLE:
        raise HTTPException(503, "Publication S3 indisponible (module non chargé)")

    safe_slug = slug.replace("..", "").replace("/", "_").replace("\\", "_")
    if not safe_slug:
        raise HTTPException(400, "Slug invalide")

    try:
        meta = s3_publication.head(owner, kind, safe_slug)
    except Exception as exc:
        raise HTTPException(503, f"S3 inaccessible: {exc}")
    if not meta:
        raise HTTPException(404, f"Publication '{kind}/{safe_slug}' introuvable")

    try:
        content = s3_publication.read(owner, kind, safe_slug)
    except Exception as exc:
        raise HTTPException(503, f"Lecture S3 échouée: {exc}")
    if content is None:
        raise HTTPException(404, "Contenu disparu entre HEAD et GET")

    return Response(
        content=content,
        media_type=meta.get("content_type", "application/octet-stream"),
        headers={"Cache-Control": "public, max-age=60"},
    )


@app.post("/publish/{kind}/{slug}")
async def publish_artifact(
    kind: str,
    slug: str,
    request: Request,
    user: dict = Depends(auth.get_current_user),
):
    """
    Publie un livrable sur S3 depuis le workspace de l'user.

    Body JSON optionnel :
      {
        "source": "/data/exports/storymaps/lavandou_t100.html"  // chemin sur PVC
      }

    Si `source` est absent, on prend le défaut selon le kind :
      storymap → /data/exports/storymaps/{slug}.html
      flux     → /data/exports/flux/{slug}.qgz
      recipe   → /data/recipes/{slug}.yaml
      dataset  → /data/exports/data/{slug}.gpkg
      pdf      → /data/exports/pdf/{slug}.pdf

    Le hub lit le fichier sur le pod workspace via execute_python, le pousse
    sur S3 avec ACL public-read, met à jour le catalogue user, et renvoie l'URL.
    """
    if not _S3_AVAILABLE:
        raise HTTPException(503, "Publication S3 indisponible")
    if kind not in s3_publication._KINDS:
        raise HTTPException(400, f"kind invalide ({sorted(s3_publication._KINDS)})")

    owner = user["username"]
    safe_slug = s3_publication._safe_slug(slug)

    # Si une étude est active, utiliser ses chemins. Sinon legacy /data/exports/*.
    active_sid: str | None = None
    if _STUDIES_AVAILABLE:
        active_sid = await studies.get_active_study_id(owner)

    if active_sid:
        base = f"/data/studies/{active_sid}/exports"
        defaults = {
            "storymap": f"{base}/storymaps/{safe_slug}.html",
            "flux":     f"{base}/flux/{safe_slug}.qgz",
            "recipe":   f"/data/studies/{active_sid}/recipes/{safe_slug}.yaml",
            "dataset":  f"{base}/data/{safe_slug}.gpkg",
            "pdf":      f"{base}/pdf/{safe_slug}.pdf",
        }
    else:
        defaults = {
            "storymap": f"/data/exports/storymaps/{safe_slug}.html",
            "flux":     f"/data/exports/flux/{safe_slug}.qgz",
            "recipe":   f"/data/recipes/{safe_slug}.yaml",
            "dataset":  f"/data/exports/data/{safe_slug}.gpkg",
            "pdf":      f"/data/exports/pdf/{safe_slug}.pdf",
        }
    body = {}
    try:
        body = await request.json()
    except Exception:
        pass
    source = (body or {}).get("source") or defaults[kind]
    if not source.startswith("/data/"):
        raise HTTPException(400, "Source doit être sous /data/")

    # Récupérer le workspace de l'user (réveille si endormi)
    s = await _get_or_create_session(owner)
    api_key = await auth.create_or_get_api_key(owner)

    code = f"""
import base64
from pathlib import Path
p = Path({source!r})
if not p.exists():
    print("<<<NOT_FOUND>>>")
else:
    data = p.read_bytes()
    print("<<<FILE_B64>>>" + base64.b64encode(data).decode() + "<<<END>>>")
"""
    payload = {
        "jsonrpc": "2.0", "id": f"pub-{safe_slug}",
        "method": "tools/call",
        "params": {"name": "execute_python", "arguments": {"code": code}},
    }
    async with httpx.AsyncClient(timeout=60) as client:
        resp = await client.post(
            _mcp_url(s), json=payload,
            headers={"Authorization": f"Bearer {api_key}"},
        )
    import json as _json
    data = resp.json()
    content_arr = data.get("result", {}).get("content", [{}])
    text = content_arr[0].get("text", "") if content_arr else ""
    try:
        wrapper = _json.loads(text)
        stdout = wrapper.get("stdout", "")
    except Exception:
        stdout = text

    if "<<<NOT_FOUND>>>" in stdout:
        raise HTTPException(404, f"Source {source} introuvable sur le workspace")
    start = stdout.find("<<<FILE_B64>>>")
    end = stdout.find("<<<END>>>")
    if start < 0 or end < 0:
        raise HTTPException(500, f"Lecture workspace échouée: {stdout[:200]}")

    import base64 as _b64
    file_bytes = _b64.b64decode(stdout[start + len("<<<FILE_B64>>>"):end])

    # Push S3 — Phase 13 : on passe study_id pour traçabilité (provenance).
    try:
        info = s3_publication.publish(owner, kind, safe_slug, file_bytes,
                                       study_id=active_sid)
    except Exception as exc:
        log.error("Publish S3 failed: %s", exc)
        raise HTTPException(500, f"Push S3 échoué: {exc}")

    # URL publique côté hub (proxy stable, masque MinIO)
    info["hub_url"] = f"{_HUB_URL}/published/{owner}/{kind}/{safe_slug}"
    return info


@app.delete("/publish/{kind}/{slug}", status_code=status.HTTP_204_NO_CONTENT)
async def unpublish_artifact(
    kind: str,
    slug: str,
    user: dict = Depends(auth.get_current_user),
):
    """Dépublie un livrable (suppression S3 + catalogue)."""
    if not _S3_AVAILABLE:
        raise HTTPException(503, "Publication S3 indisponible")
    if kind not in s3_publication._KINDS:
        raise HTTPException(400, f"kind invalide ({sorted(s3_publication._KINDS)})")
    s3_publication.delete(user["username"], kind, slug)


@app.get("/catalog/{owner}")
async def get_owner_catalog(
    owner: str,
    kind: str | None = None,
    rebuild: bool = False,
):
    """
    Catalogue public des publications d'un user.

    rebuild=true → rescan S3 et reconstruit le catalogue (réservé admin/owner).
    """
    if not _S3_AVAILABLE:
        raise HTTPException(503, "Publication S3 indisponible")
    if rebuild:
        items = s3_publication.rebuild_catalog(owner)
    else:
        items = s3_publication.get_catalog(owner)
    if kind:
        items = [i for i in items if i.get("kind") == kind]
    return {"owner": owner, "count": len(items), "items": items}


@app.get("/sessions/{session_id}/novnc_url")
async def get_novnc_url(session_id: str, user: dict = Depends(auth.get_current_user)):
    """Retourne l'URL noVNC pour une session (accessible navigateur)."""
    s = await sessions.get_session(session_id, user["username"])
    if not s:
        raise HTTPException(status_code=404, detail="Session introuvable")
    if s["status"] != sessions.SESSION_READY:
        raise HTTPException(status_code=503, detail=f"Session non prête ({s['status']})")
    return {"novnc_url": sessions.novnc_url(session_id), "session_id": session_id}


# ── Téléchargement de fichiers exportés ───────────────────────────────────────
# Le serveur MCP du pod workspace expose /api/files/{name} sur localhost:8080
# (non joignable depuis le navigateur). Le hub proxy cet endpoint via le
# service K8s interne pour que l'user puisse cliquer sur les liens PDF /
# GPKG / etc. retournés par les tools (export_pdf, export_layer, etc.).

@app.get("/files/{path:path}")
async def get_workspace_file(
    request: Request,
    path: str,
    user: dict = Depends(auth.get_current_user),
):
    """Proxy GET vers `{api_url}/api/files/{path}` du pod workspace de l'user."""
    session = await _get_or_create_session(user["username"])
    api_url = session.get("api_url")
    if not api_url:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Workspace pod sans API URL configurée",
        )
    target_url = f"{api_url}/api/files/{path}"
    return await _proxy_request(request, target_url, session["id"])


# ── Endpoint MCP principal — auto-session ─────────────────────────────────────

@app.api_route("/mcp", methods=["GET", "POST", "DELETE", "PUT", "PATCH"])
@app.api_route("/mcp/{path:path}", methods=["GET", "POST", "DELETE", "PUT", "PATCH"])
async def mcp_auto_session(
    request: Request,
    path: str = "",
    user: dict = Depends(auth.get_current_user),
):
    username = user["username"]
    session = await _get_or_create_session(username)
    target_url = _mcp_url(session, path)
    return await _proxy_request(request, target_url, session["id"])


# ── API Key — émission clé stable pour Claude Desktop ────────────────────────

@app.post("/auth/apikey")
async def get_api_key(user: dict = Depends(auth.get_current_user)):
    """
    Échange un token OIDC SSPCloud (ou une API key existante) contre
    une clé hub permanente à copier dans claude_desktop_config.json.
    Idempotent : retourne toujours la même clé pour un utilisateur donné.
    """
    key = await auth.create_or_get_api_key(user["username"])
    hub_url = os.getenv("HUB_URL", "")
    return {
        "api_key": key,
        "username": user["username"],
        "hub_url": hub_url,
        "mcp_url": f"{hub_url}/mcp" if hub_url else "/mcp",
        "claude_config": {
            "mcpServers": {
                "qgis": {
                    "type": "http",
                    "url": f"{hub_url}/mcp" if hub_url else "/mcp",
                    "headers": {"Authorization": f"Bearer {key}"},
                }
            }
        },
    }


@app.delete("/auth/apikey", status_code=status.HTTP_204_NO_CONTENT)
async def revoke_api_key(user: dict = Depends(auth.get_current_user)):
    """Révoque la clé API de l'utilisateur courant."""
    await auth.revoke_api_key(user["username"])


# ── Gestion explicite des sessions ────────────────────────────────────────────

@app.post("/sessions", status_code=status.HTTP_201_CREATED)
async def create_session(
    request: Request,
    user: dict = Depends(auth.get_current_user)
):
    # Profil optionnel passé en body JSON : {"profile": "geoai_analyst"}
    profile_id = None
    try:
        body = await request.json()
        profile_id = body.get("profile") if isinstance(body, dict) else None
    except Exception:
        pass

    # Env vars supplémentaires du profil injectées dans le pod QGIS
    profile_env: dict = {}
    if _PROFILES_AVAILABLE and profile_id:
        profile_env = profile_manager.get_session_env(profile_id)

    try:
        session = await sessions.create_session(user["username"], extra_env=profile_env)
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_429_TOO_MANY_REQUESTS, detail=str(e))

    # Démarrer le GeoAI watcher si le profil l'active (en background, non bloquant)
    watcher_env = profile_env.get("QGIS_GEOAI_WATCHER", "")
    if watcher_env == "1" and _HUB_URL:
        try:
            from hub.geoai_watcher import start_watcher_in_session
            asyncio.create_task(
                start_watcher_in_session(session["id"], _HUB_URL, "")
            )
        except Exception:
            pass

    # Installer l'audit trail (instrumentation MCP) — TOUS profils.
    # C'est la base de l'explicabilité réelle des livrables (storymaps, recettes...).
    if _AUDIT_AVAILABLE:
        asyncio.create_task(
            _install_audit_trail_safe(session, user["username"])
        )

    return _session_view(session)


async def _install_audit_trail_safe(session: dict, username: str) -> None:
    """Installe l'audit trail + maximise QGIS sur Xvfb dès que la session est prête."""
    try:
        # Attendre que la session passe READY (max 2 min)
        s = await _wait_for_session(session, timeout=120)
        key = await auth.create_or_get_api_key(username)
        await audit_trail.install_audit_trail_in_session(
            hub_internal_url=_HUB_URL,
            session_mcp_url=_mcp_url(s),
            api_key=key,
        )
        # Maximiser QGIS sur Xvfb (sinon il s'ouvre à sa taille native, gris autour)
        try:
            async with httpx.AsyncClient(timeout=10) as client:
                await client.post(
                    _mcp_url(s),
                    json={
                        "jsonrpc": "2.0", "id": "qgis-maximize",
                        "method": "tools/call",
                        "params": {
                            "name": "execute_python",
                            "arguments": {"code": sessions.maximize_qgis_code()},
                        },
                    },
                    headers={"Authorization": f"Bearer {key}"},
                )
        except Exception as exc:
            log.warning("Maximize QGIS échoué : %s", exc)
    except Exception as exc:
        log.warning("Audit trail non installé sur session %s : %s", session.get("id"), exc)


@app.get("/sessions")
async def list_user_sessions(user: dict = Depends(auth.get_current_user)):
    return [_session_view(s) for s in await sessions.list_sessions(user["username"])]


@app.get("/sessions/{session_id}")
async def get_session(session_id: str, user: dict = Depends(auth.get_current_user)):
    s = await sessions.get_session(session_id, user["username"])
    if not s:
        raise HTTPException(status_code=404, detail="Session introuvable")
    return _session_view(s)


@app.delete("/sessions/{session_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_session(session_id: str, user: dict = Depends(auth.get_current_user)):
    s = await sessions.get_session(session_id, user["username"])
    if not s:
        raise HTTPException(status_code=404, detail="Session introuvable")
    _active_sessions.pop(user["username"], None)
    await sessions.delete_session(session_id)


# ── Admin ──────────────────────────────────────────────────────────────────────

@app.get("/admin/sessions")
async def admin_list_sessions(_: dict = Depends(auth.require_admin)):
    return [_session_view(s) for s in await sessions.list_sessions()]


@app.delete("/admin/sessions/{session_id}", status_code=status.HTTP_204_NO_CONTENT)
async def admin_delete_session(session_id: str, _: dict = Depends(auth.require_admin)):
    for user, sid in list(_active_sessions.items()):
        if sid == session_id:
            _active_sessions.pop(user, None)
    await sessions.delete_session(session_id)


# ── Helpers ────────────────────────────────────────────────────────────────────

async def _get_or_create_session(username: str) -> dict:
    """
    Retourne la session active de l'utilisateur.
    Crée si absente, attend si en démarrage.
    Nettoie les sessions en erreur automatiquement.
    """
    # Cache mémoire d'abord
    if username in _active_sessions:
        sid = _active_sessions[username]
        s = await sessions.get_session(sid, username)
        if s and s["status"] == sessions.SESSION_READY:
            return s
        _active_sessions.pop(username, None)

    # defaultdict crée le lock à la première utilisation sans race condition
    async with _session_locks[username]:
        # Re-vérifier après acquisition du lock
        if username in _active_sessions:
            s = await sessions.get_session(_active_sessions[username], username)
            if s and s["status"] == sessions.SESSION_READY:
                return s

        existing = await sessions.list_sessions(username)

        # Nettoyer les sessions en erreur avant de créer la suivante
        for s in existing:
            if s["status"] == sessions.SESSION_ERROR:
                log.info("Nettoyage session erreur %s pour %s", s["id"], username)
                await sessions.delete_session(s["id"])

        active = [
            s for s in existing
            if s["status"] in (sessions.SESSION_READY, sessions.SESSION_STARTING)
        ]

        if active:
            s = active[0]
        else:
            log.info("Création session QGIS pour %s", username)
            s = await sessions.create_session(username)

        s = await _wait_for_session(s, timeout=120)
        _active_sessions[username] = s["id"]
        return s


async def _wait_for_session(s: dict, timeout: int = 120) -> dict:
    """
    Attend qu'une session passe à l'état READY.
    Supporte STARTING (boot initial) et SLEEPING (réveil scale 0→1).
    """
    deadline = asyncio.get_event_loop().time() + timeout
    waiting_states = (sessions.SESSION_STARTING, sessions.SESSION_SLEEPING)
    while s["status"] in waiting_states:
        if asyncio.get_event_loop().time() >= deadline:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail=f"Workspace QGIS non prêt après {timeout}s",
            )
        await asyncio.sleep(5)
        s = await sessions.get_session(s["id"])

    if s["status"] == sessions.SESSION_ERROR:
        # Auto-recovery : scale 0 (préserve PVC), laisser _get_or_create rescale
        log.warning("Pod workspace %s en erreur, scale 0 pour recréation", s["id"])
        await sessions.delete_session(s["id"], purge=False)
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Workspace QGIS interrompu — réessayez dans 5s (recréation en cours)",
        )

    if s["status"] != sessions.SESSION_READY:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=f"Workspace QGIS en erreur (état: {s['status']})",
        )
    return s


def _mcp_url(session: dict, path: str = "") -> str:
    base = session["mcp_url"]  # "http://svc:8100/mcp"
    return f"{base}/{path}" if path else base


def _session_view(s: dict) -> dict:
    return {
        "id":          s["id"],
        "owner":       s["owner"],
        "status":      s["status"],
        "created_at":  s["created_at"],
        "last_active": s["last_active"],
        "novnc_url":   sessions.novnc_url(s["id"]),
    }


async def _proxy_request(request: Request, target_url: str, session_id: str) -> Response:
    """Proxy HTTP vers un pod de session (JSON ou SSE stream)."""
    _skip_headers = {"host", "connection", "transfer-encoding", "te", "trailers", "upgrade"}
    headers = {k: v for k, v in request.headers.items() if k.lower() not in _skip_headers}
    body = await request.body()
    params = dict(request.query_params)

    client = httpx.AsyncClient(timeout=httpx.Timeout(300.0, connect=10.0))
    try:
        req = client.build_request(
            request.method, target_url,
            headers=headers, content=body, params=params,
        )
        resp = await client.send(req, stream=True)
    except httpx.ConnectError as exc:
        await client.aclose()
        log.warning("Proxy connect error → session %s : %s", session_id, exc)
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Pod QGIS inaccessible",
        )

    content_type = resp.headers.get("content-type", "application/json")
    _skip_resp = {"content-encoding", "transfer-encoding", "content-length", "connection"}
    resp_headers = {k: v for k, v in resp.headers.items() if k.lower() not in _skip_resp}

    async def stream_and_close():
        try:
            async for chunk in resp.aiter_bytes(4096):
                yield chunk
        finally:
            await resp.aclose()
            await client.aclose()
            await sessions.touch_session(session_id)

    return StreamingResponse(
        stream_and_close(),
        status_code=resp.status_code,
        media_type=content_type,
        headers=resp_headers,
    )


# ── Bureau de travail (desk) + workspace ──────────────────────────────────────

@app.get("/desk", response_class=HTMLResponse)
async def desk_page(request: Request):
    """Bureau de travail unifié : sidebar études | canvas QGIS noVNC | chat agent."""
    if not _jinja:
        raise HTTPException(503, "Templates non disponibles")
    ctx = await _desk_context()
    return _jinja.TemplateResponse(request, "desk.html", ctx)


@app.get("/workspace", response_class=HTMLResponse)
async def workspace_page(request: Request):
    """Vue études + catalogue + accès outils."""
    if not _jinja:
        raise HTTPException(503, "Templates non disponibles")
    ctx = await _desk_context()
    return _jinja.TemplateResponse(request, "workspace.html", ctx)


@app.post("/workspace/wake")
async def workspace_wake():
    """Réveille le workspace QGIS endormi (scale 0→1)."""
    try:
        api_key = await auth.create_or_get_api_key(_ONYXIA_USER)
        async with httpx.AsyncClient(timeout=30, base_url=_SELF_URL) as c:
            await c.post("/sessions", headers={"Authorization": f"Bearer {api_key}"}, json={})
        return {"ok": True}
    except Exception:
        return {"ok": False}


@app.post("/workspace/study/new")
async def workspace_create_study(request: Request):
    form = await request.form()
    name = form.get("name", "").strip()
    profile = form.get("profile", "standard")
    if not name:
        return RedirectResponse("/workspace?error=name_required", status_code=302)
    try:
        api_key = await auth.create_or_get_api_key(_ONYXIA_USER)
        async with httpx.AsyncClient(timeout=15, base_url=_SELF_URL) as c:
            r = await c.post("/studies",
                             headers={"Authorization": f"Bearer {api_key}"},
                             json={"name": name, "profile": profile})
            new_id = r.json().get("id") if r.status_code == 200 else None
            if new_id:
                await c.post(f"/studies/{new_id}/activate",
                             headers={"Authorization": f"Bearer {api_key}"})
    except Exception:
        pass
    return RedirectResponse("/workspace", status_code=302)


@app.post("/workspace/study/{sid}/activate")
async def workspace_activate_study(sid: str):
    try:
        api_key = await auth.create_or_get_api_key(_ONYXIA_USER)
        async with httpx.AsyncClient(timeout=15, base_url=_SELF_URL) as c:
            await c.post(f"/studies/{sid}/activate",
                         headers={"Authorization": f"Bearer {api_key}"})
    except Exception:
        pass
    return RedirectResponse("/workspace", status_code=302)


@app.post("/workspace/study/{sid}/archive")
async def workspace_archive_study(sid: str):
    try:
        api_key = await auth.create_or_get_api_key(_ONYXIA_USER)
        async with httpx.AsyncClient(timeout=15, base_url=_SELF_URL) as c:
            await c.delete(f"/studies/{sid}",
                           headers={"Authorization": f"Bearer {api_key}"})
    except Exception:
        pass
    return RedirectResponse("/workspace", status_code=302)


# ── Proxy mémoire vers l'agent IA ─────────────────────────────────────────────

async def _agent_call(method: str, path: str, **kwargs):
    """Proxifie un appel vers le pod agent IA."""
    async with httpx.AsyncClient(timeout=10, base_url=_AGENT_URL or "http://127.0.0.1:8100") as c:
        return await c.request(method, path, **kwargs)


@app.get("/desk/memory")
async def desk_get_memory():
    r = await _agent_call("GET", "/user/memory")
    return r.json()


@app.patch("/desk/memory")
async def desk_patch_memory(request: Request):
    body = await request.json()
    r = await _agent_call("PATCH", "/user/memory", json=body)
    return r.json()


@app.patch("/desk/memory/preferences")
async def desk_set_pref(request: Request):
    body = await request.json()
    r = await _agent_call("PATCH", "/user/preferences", json=body)
    return r.json()


@app.delete("/desk/memory/preferences/{key}", status_code=204)
async def desk_del_pref(key: str):
    await _agent_call("PATCH", "/user/preferences", json={key: ""})


@app.post("/desk/memory/insights")
async def desk_add_insight(request: Request):
    body = await request.json()
    r = await _agent_call("POST", "/user/insights", json=body)
    return r.json()


@app.delete("/desk/memory/insights/{insight_id}", status_code=204)
async def desk_del_insight(insight_id: int):
    await _agent_call("DELETE", f"/user/insights/{insight_id}")


@app.get("/desk/layers")
async def desk_layers():
    """Liste les couches QGIS du projet courant via le MCP."""
    try:
        api_key = await auth.create_or_get_api_key(_ONYXIA_USER)
        async with httpx.AsyncClient(timeout=10, base_url=_SELF_URL) as c:
            r = await c.post("/mcp",
                             headers={"Authorization": f"Bearer {api_key}"},
                             json={"jsonrpc": "2.0", "id": 1,
                                   "method": "tools/call",
                                   "params": {"name": "get_project_info", "arguments": {}}})
        import json as _json
        content = r.json().get("result", {}).get("content", [{}])
        text = content[0].get("text", "") if content else ""
        info = _json.loads(text) if text else {}
        return {"layers": info.get("layers", [])}
    except Exception:
        return {"layers": []}
