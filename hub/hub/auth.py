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

import logging
import os
import secrets
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
    Retourne la clé API existante ou en génère une nouvelle.
    Idempotent — le portail peut l'appeler plusieurs fois sans effet de bord.
    La clé est stockée en clair (c'est elle-même l'ID) car elle contient
    déjà 128 bits d'entropie — suffisant sans hachage supplémentaire.
    """
    async with aiosqlite.connect(_DB_PATH) as db:
        row = await (await db.execute(
            "SELECT id FROM api_keys WHERE username = ?", (username,)
        )).fetchone()

        if row:
            await db.execute(
                "UPDATE api_keys SET last_used = ? WHERE username = ?",
                (int(time.time()), username),
            )
            await db.commit()
            return row[0]

        key = f"qgis_{username}_{secrets.token_hex(16)}"
        now = int(time.time())
        await db.execute(
            "INSERT INTO api_keys (id, username, created_at, last_used) VALUES (?, ?, ?, ?)",
            (key, username, now, now),
        )
        await db.commit()
        log.info("API key créée pour %s", username)
        return key


async def revoke_api_key(username: str) -> None:
    async with aiosqlite.connect(_DB_PATH) as db:
        await db.execute("DELETE FROM api_keys WHERE username = ?", (username,))
        await db.commit()


async def _validate_api_key(key: str) -> dict | None:
    """Valide une clé API hub. None si invalide/expirée."""
    async with aiosqlite.connect(_DB_PATH) as db:
        row = await (await db.execute(
            "SELECT username, expires_at FROM api_keys WHERE id = ?", (key,)
        )).fetchone()
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


# ── Validation OIDC SSPCloud ───────────────────────────────────────────────────

def _validate_oidc_token(token: str) -> dict:
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
