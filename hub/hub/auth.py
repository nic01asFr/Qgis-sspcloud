"""
hub.auth — Authentification dual-mode.

Mode 1 — SSPCloud OIDC (agents pods, onboarding) :
  Authorization: Bearer eyJhbGci...  (token Keycloak SSPCloud)
  Validé via JWKS. TTL ~7j.

Mode 2 — Hub API key (Claude Desktop, usage stable) :
  Authorization: Bearer qgis_{username}_{hex32}
  Stockée en SQLite (bcrypt hash). Permanente jusqu'à révocation.

Le portail utilise le mode OIDC pour générer une API key via POST /auth/apikey,
que l'user colle dans sa config Claude Desktop — jamais mise à jour ensuite.
"""

from __future__ import annotations

import asyncio
import base64
import logging
import os
import secrets
import subprocess
import time
from pathlib import Path

import aiosqlite
import jwt
from jwt import PyJWKClient
from fastapi import Depends, HTTPException, Request, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

log = logging.getLogger("hub.auth")

_SSPCLOUD_ISSUER = os.getenv(
    "SSPCLOUD_ISSUER",
    "https://auth.lab.sspcloud.fr/auth/realms/sspcloud",
)
_JWKS_URL = f"{_SSPCLOUD_ISSUER}/protocol/openid-connect/certs"
_ADMIN_USERS = set(
    u.strip() for u in os.getenv("ADMIN_USERS", "").split(",") if u.strip()
)
# Defaut persistant : si /home/onyxia/work est monte (PVC, cas onboarde), on
# ecrit dedans pour que apikeys.db survive aux redeploys du hub. Sinon /tmp
# (dev local, ephemere). Le chemin matche celui de scripts/server_init.sh
# pour preserver la continuite avec les hubs deployes via start_hub.sh.
_DATA_DIR = Path(os.getenv("DATA_DIR") or (
    "/home/onyxia/work/qgis-mcp/server-data"
    if Path("/home/onyxia/work").is_dir()
    else "/tmp/qgis-mcp/server-data"
))
_DB_PATH  = _DATA_DIR / "apikeys.db"

# ── Source de verite : k8s Secret namespace-level ────────────────────────────
# Pourquoi : `apikeys.db` etait dans le PVC du hub (`/home/onyxia/work/...`).
# Or sur Onyxia, le PVC est attache au cycle de vie du service — supprimer
# /redeployer le hub detruit le PVC → DB neuve → nouvelle cle. L'agent SS
# garde l'ancienne cle dans son env → desync → tous les /mcp renvoient 401
# silencieux (le bug recurrent qu'on a chasse). Le Secret survit a TOUT
# (redeploy service, restart pod, suppression manuelle) — objet namespace-level.
def _read_pod_namespace() -> str:
    try:
        return Path("/var/run/secrets/kubernetes.io/serviceaccount/namespace").read_text().strip()
    except Exception:
        return ""

_NAMESPACE   = _read_pod_namespace()
_SECRET_NAME = "qgis-hub-apikey"
_SECRET_KEY  = "HUB_API_KEY"
# Cache memoire de la cle pour ne pas tirer un kubectl par /auth/apikey appel.
# TTL court (5 min) — un revoke explicite invalide le cache.
_cached_key: dict = {"value": "", "ts": 0.0}
_CACHE_TTL = 300.0


def _kubectl_get_secret_value(name: str, namespace: str, key: str) -> str | None:
    """Lit la valeur d'une cle d'un Secret. None si absent. Sync (a wrapper to_thread)."""
    if not namespace:
        return None
    r = subprocess.run(
        ["kubectl", "get", "secret", name, "-n", namespace,
         "-o", "jsonpath={.data." + key + "}"],
        capture_output=True, text=True, timeout=10,
    )
    if r.returncode != 0 or not r.stdout.strip():
        return None
    try:
        return base64.b64decode(r.stdout.strip()).decode()
    except Exception:
        return None


def _kubectl_create_secret(name: str, namespace: str, key: str, value: str) -> bool:
    """Cree un Secret generique avec une cle. Idempotent (AlreadyExists = OK).
    Renvoie True si Secret existe en sortie (cree ou deja la)."""
    if not namespace:
        return False
    r = subprocess.run(
        ["kubectl", "create", "secret", "generic", name,
         "-n", namespace, f"--from-literal={key}={value}"],
        capture_output=True, text=True, timeout=10,
    )
    if r.returncode == 0:
        return True
    return "AlreadyExists" in (r.stderr or "")


def _kubectl_delete_secret(name: str, namespace: str) -> bool:
    if not namespace:
        return False
    r = subprocess.run(
        ["kubectl", "delete", "secret", name, "-n", namespace, "--ignore-not-found"],
        capture_output=True, text=True, timeout=10,
    )
    return r.returncode == 0


_jwks   = PyJWKClient(_JWKS_URL, cache_keys=True)
_bearer = HTTPBearer()


# ── Base de données API keys ───────────────────────────────────────────────────

async def init_apikeys_db() -> None:
    """Init de la DB des cles API hub.

    AUCUN wipe automatique sur exception. La DB est persistante (PVC) et
    contient les cles de TOUS les users — un wipe silencieux desync l'env
    des agents (qui gardent l'ancienne cle dans leur SS) et tous leurs
    appels /mcp renvoient 401 muettement (tools vus comme `{}` cote LLM).
    CREATE TABLE IF NOT EXISTS est idempotent ; si le connect echoue
    (lock SQLite transitoire, schema mismatch), on log.error et raise pour
    investigation humaine. Un crash explicite est plus sain qu'un wipe.
    """
    _DATA_DIR.mkdir(parents=True, exist_ok=True)
    try:
        async with aiosqlite.connect(_DB_PATH) as db:
            await db.execute("""
                CREATE TABLE IF NOT EXISTS api_keys (
                    id         TEXT PRIMARY KEY,
                    username   TEXT NOT NULL UNIQUE,
                    created_at INTEGER NOT NULL,
                    last_used  INTEGER NOT NULL,
                    expires_at INTEGER DEFAULT NULL
                )
            """)
            await db.commit()
    except Exception as exc:
        log.error(
            "init_apikeys_db ECHEC sur %s : %s — la DB N'A PAS ete wipee "
            "(volontaire, voir docstring). Investiguer.", _DB_PATH, exc,
        )
        raise


async def create_or_get_api_key(username: str) -> str:
    """
    Retourne la cle HUB_API_KEY de la namespace.

    Source de verite = k8s Secret `qgis-hub-apikey` (namespace-level, survit
    aux redeploys de service). Migration douce : si le Secret n'existe pas
    encore mais qu'un `apikeys.db` legacy est present (hubs deployes AVANT
    cette refonte), bascule la cle existante vers le Secret pour preserver
    la continuite (l'agent et le hub continuent de matcher sans intervention).

    Idempotent — le portail / _bootstrap_agent peuvent l'appeler n fois.
    """
    # 1) Cache memoire
    now = time.time()
    if _cached_key["value"] and (now - _cached_key["ts"]) < _CACHE_TTL:
        return _cached_key["value"]

    # 2) Lecture Secret (cas normal en prod, post-refonte)
    if _NAMESPACE:
        existing = await asyncio.to_thread(
            _kubectl_get_secret_value, _SECRET_NAME, _NAMESPACE, _SECRET_KEY,
        )
        if existing:
            _cached_key["value"] = existing
            _cached_key["ts"] = now
            return existing

    # 3) Migration legacy : reprendre la cle depuis apikeys.db si elle existe
    legacy_key: str | None = None
    if _DB_PATH.exists():
        try:
            async with aiosqlite.connect(_DB_PATH) as db:
                row = await (await db.execute(
                    "SELECT id FROM api_keys WHERE username = ?", (username,)
                )).fetchone()
                if row:
                    legacy_key = row[0]
                    log.info(
                        "Migration apikeys.db -> Secret %s/%s pour %s",
                        _NAMESPACE, _SECRET_NAME, username,
                    )
        except Exception as exc:
            log.warning("Lecture legacy apikeys.db echec: %s", exc)

    # 4) Generation si pas de legacy
    new_key = legacy_key or f"qgis_{username}_{secrets.token_hex(16)}"

    # 5) Creation Secret (atomique cote k8s, idempotent : AlreadyExists OK)
    if _NAMESPACE:
        ok = await asyncio.to_thread(
            _kubectl_create_secret, _SECRET_NAME, _NAMESPACE, _SECRET_KEY, new_key,
        )
        if ok:
            # Relire pour gerer la race "AlreadyExists" : un autre process a
            # peut-etre cree le Secret entre etapes 2 et 5 avec une cle
            # differente. La cle dans le Secret est la verite.
            confirmed = await asyncio.to_thread(
                _kubectl_get_secret_value, _SECRET_NAME, _NAMESPACE, _SECRET_KEY,
            )
            if confirmed:
                _cached_key["value"] = confirmed
                _cached_key["ts"] = now
                return confirmed
        else:
            log.error(
                "create_or_get_api_key: echec creation Secret %s/%s — "
                "fallback DB legacy (cle volatile)", _NAMESPACE, _SECRET_NAME,
            )

    # 6) Fallback DB (dev local sans k8s, OU echec irrecuperable Secret).
    #    Sert aussi de filet : si on est ici sans Secret + sans DB, on insere
    #    new_key en DB pour stocker quelque part.
    try:
        async with aiosqlite.connect(_DB_PATH) as db:
            row = await (await db.execute(
                "SELECT id FROM api_keys WHERE username = ?", (username,)
            )).fetchone()
            if row:
                return row[0]
            ts = int(time.time())
            await db.execute(
                "INSERT INTO api_keys (id, username, created_at, last_used) "
                "VALUES (?, ?, ?, ?)",
                (new_key, username, ts, ts),
            )
            await db.commit()
            log.info("API key creee (fallback DB) pour %s", username)
            return new_key
    except Exception as exc:
        log.error("Fallback DB creation echec : %s — retourne cle ephemere", exc)
        return new_key


async def revoke_api_key(username: str) -> None:
    """Revoque la cle (supprime Secret + DB legacy)."""
    if _NAMESPACE:
        await asyncio.to_thread(_kubectl_delete_secret, _SECRET_NAME, _NAMESPACE)
    _cached_key["value"] = ""
    _cached_key["ts"] = 0.0
    if _DB_PATH.exists():
        try:
            async with aiosqlite.connect(_DB_PATH) as db:
                await db.execute(
                    "DELETE FROM api_keys WHERE username = ?", (username,)
                )
                await db.commit()
        except Exception:
            pass


async def _validate_api_key(key: str) -> dict | None:
    """Valide une cle API hub.

    Compare contre le Secret de la namespace courante (post-refonte) ET
    contre la DB legacy si elle existe (compat upgrade en cours).
    """
    if not key:
        return None
    # 1) Secret (source de verite)
    if _NAMESPACE:
        current = await asyncio.to_thread(
            _kubectl_get_secret_value, _SECRET_NAME, _NAMESPACE, _SECRET_KEY,
        )
        if current and current == key:
            username = _NAMESPACE.removeprefix("user-")
            return {
                "username": username,
                "role": "admin" if username in _ADMIN_USERS else "user",
                "source": "apikey",
            }
    # 2) DB legacy (compat upgrade)
    if _DB_PATH.exists():
        try:
            async with aiosqlite.connect(_DB_PATH) as db:
                row = await (await db.execute(
                    "SELECT username, expires_at FROM api_keys WHERE id = ?",
                    (key,),
                )).fetchone()
        except Exception:
            return None
        if not row:
            return None
        username, expires_at = row
        if expires_at and time.time() > expires_at:
            return None
        return {
            "username": username,
            "role": "admin" if username in _ADMIN_USERS else "user",
            "source": "apikey",
        }
    return None


# ── Validation OIDC SSPCloud ───────────────────────────────────────────────────

def _validate_oidc_token(token: str) -> dict:
    """Valide un token OIDC SSPCloud (Keycloak datalab).

    Note session : SSPCloud Keycloak gere son propre cycle d'expiration des
    tokens (parametres upstream, hors controle hub). Cote utilisateur final
    naviguant via le navigateur, la session HUB persistante (cookie
    hub_api_key, max_age 90j) reste valide independamment, et un 401 ici
    declenche un retry transparent via /authorize si Keycloak session
    encore vive, ou un re-login complet sinon.
    """
    try:
        signing_key = _jwks.get_signing_key_from_jwt(token)
        data = jwt.decode(
            token,
            signing_key.key,
            algorithms=["RS256"],
            options={"verify_aud": False},
        )
    except jwt.ExpiredSignatureError:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Token SSPCloud expiré",
        )
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=f"Token invalide : {exc}",
        )
    username = data.get("preferred_username") or data.get("sub", "unknown")
    return {
        "username": username,
        "role": "admin" if username in _ADMIN_USERS else "user",
        "source": "oidc",
        "raw": data,
    }


# ── Dépendances FastAPI ────────────────────────────────────────────────────────

async def get_current_user(
    request: Request = None,
    creds: HTTPAuthorizationCredentials = Depends(HTTPBearer(auto_error=False)),
) -> dict:
    """
    Triple validation :
    1. Cookie hub_api_key (navigateur après /login?key=...)
    2. Bearer API key hub (qgis_...)
    3. Bearer token OIDC SSPCloud
    """
    # 1. Cookie navigateur (accès web via /login?key=...)
    if request:
        cookie_key = request.cookies.get("hub_api_key", "")
        if cookie_key.startswith("qgis_"):
            user = await _validate_api_key(cookie_key)
            if user:
                return user

    # 2. Bearer token (MCP agents ou API key)
    if not creds:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Not authenticated")

    token = creds.credentials
    if token.startswith("qgis_"):
        user = await _validate_api_key(token)
        if user:
            return user
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Clé API invalide ou expirée",
        )

    return _validate_oidc_token(token)


async def require_admin(user: dict = Depends(get_current_user)) -> dict:
    if user.get("role") != "admin":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Accès admin requis",
        )
    return user


async def _build_jwks_cache() -> None:
    """Pré-chauffe le cache JWKS au démarrage."""
    try:
        _jwks.get_jwk_set(refresh=True)
    except Exception as exc:
        log.warning("Pré-chauffe JWKS échouée: %s", exc)
