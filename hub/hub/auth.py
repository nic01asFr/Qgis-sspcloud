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
import json
import logging
import os
import secrets
import ssl
import time
from pathlib import Path

import aiosqlite
import httpx
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
# (dev local, ephemere).
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
# Cache memoire de la cle pour ne pas tirer un API K8s par /auth/apikey appel.
# TTL court (5 min) — un revoke explicite invalide le cache.
_cached_key: dict = {"value": "", "ts": 0.0}
_CACHE_TTL = 300.0

# ── Acces direct API K8s (sans dependance kubectl binary) ─────────────────────
# Bug #15 V1.1 (2026-05-31) : le code utilisait subprocess.run(["kubectl", ...])
# mais l'image Docker hub (FROM inseefrlab/onyxia-jupyter-python) n'a PAS
# kubectl installe. Resultat : FileNotFoundError silencieuse a chaque appel
# auth -> Secret K8s jamais lu/cree -> validate_api_key retourne None -> 401
# sur tous les endpoints auth-protected -> _desk_context() recoit studies=[]
# -> UX cassee post-redeploiement fresh.
#
# Fix : utiliser httpx vers kubernetes.default.svc avec le token du
# ServiceAccount (meme pattern que dans main.py _bootstrap_agent).
_K8S_HOST = "https://kubernetes.default.svc"

def _k8s_sa_token() -> str:
    try:
        return Path("/var/run/secrets/kubernetes.io/serviceaccount/token").read_text().strip()
    except Exception:
        return ""


async def _k8s_get_secret_value(name: str, namespace: str, key: str) -> str | None:
    """Lit la valeur d'une cle d'un Secret via l'API K8s HTTP. None si absent.

    P1-5 fix (2026-07-13) : log explicitement les status codes non-200. Avant
    ce fix, un 403 RBAC (SA hub sans droit `get secrets` sur qgis-hub-apikey)
    retournait silencieusement None, forcant `create_or_get_api_key` en
    fallback DB volatile. Symptome : la cle env HUB_API_KEY (Secret injecte)
    et la cle retournee par la fonction divergeaient -> 401 sur inter-pod
    auth. Cf. rapport Sprint V0.3 validation admin nic01asfr.

    Le vrai fix cote infra est un RoleBinding sur le SA pour lui accorder
    `get,list secrets` (ressource nommee qgis-hub-apikey). Cf. deploy/rbac/
    hub-secret-reader.yaml (a appliquer manuellement post-refonte helm).
    """
    if not namespace:
        return None
    token = _k8s_sa_token()
    if not token:
        return None
    try:
        async with httpx.AsyncClient(verify=False, timeout=10) as c:
            r = await c.get(
                f"{_K8S_HOST}/api/v1/namespaces/{namespace}/secrets/{name}",
                headers={"Authorization": f"Bearer {token}"},
            )
        if r.status_code == 404:
            log.info(
                "k8s get_secret %s/%s absent (404) — cas normal a la premiere "
                "creation, sera cree via _k8s_create_secret.",
                namespace, name,
            )
            return None
        if r.status_code == 403:
            log.error(
                "k8s get_secret %s/%s FORBIDDEN (403) -- le service account "
                "du pod n'a pas de RoleBinding avec `get secrets` sur ce nom. "
                "Symptome connu : cle API volatile qui diverge du Secret K8s "
                "-> 401 silencieux sur inter-pod. Applique deploy/rbac/"
                "hub-secret-reader.yaml (Role + RoleBinding pour SA %s).",
                namespace, name, token[:12] + "...",
            )
            return None
        if r.status_code != 200:
            log.warning(
                "k8s get_secret %s/%s HTTP %d : %s",
                namespace, name, r.status_code, r.text[:200],
            )
            return None
        data_b64 = (r.json().get("data") or {}).get(key)
        if not data_b64:
            log.warning(
                "k8s get_secret %s/%s existe mais cle '%s' absente. "
                "Verifier le champ data du Secret.",
                namespace, name, key,
            )
            return None
        return base64.b64decode(data_b64).decode()
    except Exception as exc:
        log.warning("k8s get_secret %s/%s/%s echec : %s",
                    namespace, name, key, exc)
        return None


async def _k8s_create_secret(name: str, namespace: str, key: str, value: str) -> bool:
    """Cree un Secret namespace-level. Idempotent (AlreadyExists = OK)."""
    if not namespace:
        return False
    token = _k8s_sa_token()
    if not token:
        return False
    # Memes metadonnees d'appartenance Helm que dans _k8s_upsert_secret :
    # un Secret cree ici en self-heal doit rester adoptable par Helm, sinon
    # il bloque les `helm upgrade` suivants.
    release = os.environ.get("HELM_RELEASE_NAME", "qgis-hub")
    body = {
        "apiVersion": "v1",
        "kind": "Secret",
        "type": "Opaque",
        "metadata": {
            "name": name,
            "namespace": namespace,
            "labels": {"app.kubernetes.io/managed-by": "Helm"},
            "annotations": {
                "meta.helm.sh/release-name": release,
                "meta.helm.sh/release-namespace": namespace,
                "helm.sh/resource-policy": "keep",
            },
        },
        "data": {key: base64.b64encode(value.encode()).decode()},
    }
    try:
        async with httpx.AsyncClient(verify=False, timeout=10) as c:
            r = await c.post(
                f"{_K8S_HOST}/api/v1/namespaces/{namespace}/secrets",
                headers={"Authorization": f"Bearer {token}",
                         "Content-Type": "application/json"},
                json=body,
            )
        if r.status_code in (200, 201):
            return True
        # AlreadyExists est un cas normal (idempotent)
        if r.status_code == 409 or "AlreadyExists" in r.text:
            return True
        log.warning("k8s create_secret %s/%s echec %d : %s",
                    namespace, name, r.status_code, r.text[:200])
        return False
    except Exception as exc:
        log.warning("k8s create_secret %s/%s exception : %s", namespace, name, exc)
        return False


async def _k8s_upsert_secret(name: str, namespace: str, key: str, value: str) -> bool:
    """Cree OU met a jour un Secret namespace-level.

    Difference avec `_k8s_create_secret` : celui-ci traite le 409
    AlreadyExists comme un succes sans rien ecrire, donc il ne peut pas
    servir a REMPLACER une valeur existante. Ici on tente d'abord un
    merge-patch (met a jour si present), puis on retombe sur un create
    si le Secret n'existe pas encore (404).

    Utilise par POST /workspace/llm-key pour persister la cle LLM : sans
    ca, la cle ne vivait qu'en RAM du pod agent et disparaissait au
    premier redemarrage (constat terrain : has_llm_key=false 16 jours
    apres une configuration reussie).
    """
    if not namespace:
        return False
    token = _k8s_sa_token()
    if not token:
        return False
    url = f"{_K8S_HOST}/api/v1/namespaces/{namespace}/secrets/{name}"
    # Metadonnees d'appartenance Helm.
    #
    # Sans elles, Helm refuse d'adopter un Secret preexistant et TOUT
    # `helm upgrade` ulterieur echoue :
    #   "Secret qgis-llm-apikey exists and cannot be imported into the
    #    current release: invalid ownership metadata"
    # Le cas se produit des qu'un utilisateur saisit sa cle LLM via
    # /workspace avant une mise a jour du service : le Secret est cree ici,
    # par l'API Kubernetes, donc hors du suivi Helm. Constate en conditions
    # reelles le 2026-08-22.
    release = os.environ.get("HELM_RELEASE_NAME", "qgis-hub")
    payload = {
        "metadata": {
            "labels": {"app.kubernetes.io/managed-by": "Helm"},
            "annotations": {
                "meta.helm.sh/release-name": release,
                "meta.helm.sh/release-namespace": namespace,
                "helm.sh/resource-policy": "keep",
            },
        },
        "data": {key: base64.b64encode(value.encode()).decode()},
    }
    try:
        async with httpx.AsyncClient(verify=False, timeout=10) as c:
            r = await c.patch(
                url,
                headers={"Authorization": f"Bearer {token}",
                         "Content-Type": "application/merge-patch+json"},
                json=payload,
            )
        if r.status_code == 200:
            return True
        if r.status_code == 404:
            # Secret absent : premiere configuration -> create
            return await _k8s_create_secret(name, namespace, key, value)
        log.warning("k8s upsert_secret %s/%s echec %d : %s",
                    namespace, name, r.status_code, r.text[:200])
        return False
    except Exception as exc:
        log.warning("k8s upsert_secret %s/%s exception : %s", namespace, name, exc)
        return False


async def _k8s_delete_secret(name: str, namespace: str) -> bool:
    """Supprime un Secret. Idempotent (404 = deja absent = OK)."""
    if not namespace:
        return False
    token = _k8s_sa_token()
    if not token:
        return False
    try:
        async with httpx.AsyncClient(verify=False, timeout=10) as c:
            r = await c.delete(
                f"{_K8S_HOST}/api/v1/namespaces/{namespace}/secrets/{name}",
                headers={"Authorization": f"Bearer {token}"},
            )
        return r.status_code in (200, 404)
    except Exception:
        return False


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
            # Cles scopees (agents configures / publies) — additif, n'altere PAS
            # api_keys. Une cle scopee porte un scope (etude/projet/persona/tools/
            # data/mode/actor) au lieu de l'acces supervision total de la cle nue.
            # Le scope est resolu cote serveur (cle = bearer opaque) -> ajustable/
            # revocable sans re-emettre. sid/pid = 12-hex (seam Composants).
            # PAS de UNIQUE(username,study,project) : plusieurs agents publies
            # (personas/tools distincts) peuvent cibler le meme projet.
            await db.execute("""
                CREATE TABLE IF NOT EXISTS scoped_keys (
                    id          TEXT PRIMARY KEY,
                    parent_key  TEXT NOT NULL,
                    username    TEXT NOT NULL,
                    study_id    TEXT NOT NULL,
                    project_id  TEXT,
                    persona     TEXT,
                    tools_json  TEXT NOT NULL DEFAULT '"all"',
                    data_scope  TEXT NOT NULL DEFAULT 'project',
                    mode        TEXT NOT NULL DEFAULT 'scoped',
                    actor       TEXT NOT NULL DEFAULT 'owner',
                    label       TEXT,
                    created_at  INTEGER NOT NULL,
                    expires_at  INTEGER,
                    revoked_at  INTEGER,
                    -- Sprint Composants Phase 4a (2026-06-27) : publication
                    audience          TEXT DEFAULT 'cerema_internal',
                    published_url     TEXT,
                    published_at      INTEGER,
                    audit_chain_json  TEXT
                )
            """)
            # Phase 4a migration retroactive (ALTER si table preexistante)
            for _alter in (
                "ALTER TABLE scoped_keys ADD COLUMN audience TEXT DEFAULT 'cerema_internal'",
                "ALTER TABLE scoped_keys ADD COLUMN published_url TEXT",
                "ALTER TABLE scoped_keys ADD COLUMN published_at INTEGER",
                "ALTER TABLE scoped_keys ADD COLUMN audit_chain_json TEXT",
            ):
                try:
                    await db.execute(_alter)
                except Exception:
                    pass  # column existe deja (idempotent)
            await db.execute(
                "CREATE INDEX IF NOT EXISTS idx_scoped_active "
                "ON scoped_keys(username, study_id) WHERE revoked_at IS NULL"
            )
            await db.execute(
                "CREATE INDEX IF NOT EXISTS idx_scoped_published "
                "ON scoped_keys(username, published_at DESC) "
                "WHERE published_url IS NOT NULL AND revoked_at IS NULL"
            )
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
    # 0) Fast-path env var (Sprint Day 5 Phase 1.7-A, 2026-08-05).
    # Le chart injecte HUB_API_KEY via secretKeyRef depuis Secret K8s
    # qgis-hub-apikey (source de verite unique). Evite dependance
    # K8s API get secrets (RoleBinding retire Phase 1.5). Le Secret
    # existe forcement (cree par chart hook pre-install randAlphaNum).
    import os as _os
    env_key = _os.environ.get("HUB_API_KEY", "").strip()
    if env_key:
        _cached_key["value"] = env_key
        _cached_key["ts"] = time.time()
        return env_key

    # 1) Cache memoire
    now = time.time()
    if _cached_key["value"] and (now - _cached_key["ts"]) < _CACHE_TTL:
        return _cached_key["value"]

    # 2) Lecture Secret (cas normal en prod, post-refonte)
    if _NAMESPACE:
        existing = await _k8s_get_secret_value(_SECRET_NAME, _NAMESPACE, _SECRET_KEY)
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
        ok = await _k8s_create_secret(_SECRET_NAME, _NAMESPACE, _SECRET_KEY, new_key)
        if ok:
            # Relire pour gerer la race "AlreadyExists" : un autre process a
            # peut-etre cree le Secret entre etapes 2 et 5 avec une cle
            # differente. La cle dans le Secret est la verite.
            confirmed = await _k8s_get_secret_value(_SECRET_NAME, _NAMESPACE, _SECRET_KEY)
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
        await _k8s_delete_secret(_SECRET_NAME, _NAMESPACE)
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

    Sprint Day 5 Phase 1.7-A (2026-08-05) : Fix RBAC racine.
    Ordre de validation :
    0) Fast-path env var HUB_API_KEY (injectee par chart via secretKeyRef Secret
       K8s qgis-hub-apikey). Source unique, evite dependance K8s API get secrets
       (RoleBinding retire Phase 1.5 pour eviter forbidden install user).
    1) Secret K8s direct read via _k8s_get_secret_value (fallback si env absent,
       necessite RoleBinding get secrets - marche pour SA `default` du namespace
       user Onyxia si role=edit).
    2) DB legacy (compat upgrade en cours, cle emise avant refonte Secret).
    """
    if not key:
        return None
    # 0) Fast-path env var (Phase 1.7-A) : source de verite unique = env
    # injectee par chart au boot du pod. Zero appel API K8s pour cette validation.
    # Fix Phase 1.7-B (2026-08-05) : n'utilise QUE ONYXIA_USER (env var
    # standard SSPCloud) au lieu de _NAMESPACE module-level qui pouvait
    # etre invisible dans certains contextes async middleware -> NameError.
    import os as _os
    env_key = _os.environ.get("HUB_API_KEY", "").strip()
    if env_key and key == env_key:
        username = _os.environ.get("ONYXIA_USER", "").strip()
        return {
            "username": username,
            "role": "admin" if username in _ADMIN_USERS else "user",
            "source": "apikey_env",
        }
    # 1) Secret K8s direct (fallback si env absent OU cle differente)
    if _NAMESPACE:
        current = await _k8s_get_secret_value(_SECRET_NAME, _NAMESPACE, _SECRET_KEY)
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


# ── Cles scopees (agents configures / publies) ────────────────────────────────
# Etape 1 (additive) : couche de donnees uniquement. Rien dans get_current_user
# ni le middleware ne consomme encore ces cles -> zero impact sur l'existant.
# Le cablage (accept-path middleware + scope dans get_current_user + enforcement
# au proxy /mcp) viendra dans une etape ulterieure, une fois cette base validee.

_SCOPED_PREFIX = "qgisk_"
_SCOPED_TOOLS_ALL = "all"


async def create_scoped_key(
    username: str,
    study_id: str,
    project_id: str | None = None,
    persona: str | None = None,
    tools: list[str] | str = _SCOPED_TOOLS_ALL,
    data_scope: str = "project",
    mode: str = "scoped",
    actor: str = "owner",
    label: str | None = None,
    expires_at: int | None = None,
) -> str:
    """Emet une cle scopee pour un agent configure/publie. Retourne le bearer.

    `tools` : liste blanche de noms de tools, ou "all". `parent_key` trace la
    cle superviseur dont elle derive (pour audit / revocation en cascade).
    """
    key = f"{_SCOPED_PREFIX}{username}_{secrets.token_hex(16)}"
    tools_json = json.dumps(
        tools if isinstance(tools, list) else _SCOPED_TOOLS_ALL,
        ensure_ascii=False,
    )
    parent_key = await create_or_get_api_key(username)
    now = int(time.time())
    async with aiosqlite.connect(_DB_PATH) as db:
        await db.execute(
            "INSERT INTO scoped_keys (id, parent_key, username, study_id, "
            "project_id, persona, tools_json, data_scope, mode, actor, label, "
            "created_at, expires_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (key, parent_key, username, study_id, project_id, persona,
             tools_json, data_scope, mode, actor, label, now, expires_at),
        )
        await db.commit()
    log.info(
        "Cle scopee creee pour %s (study=%s project=%s mode=%s actor=%s)",
        username, study_id, project_id, mode, actor,
    )
    return key


async def _validate_scoped_key(key: str) -> dict | None:
    """Valide une cle scopee. Retourne {username, role, source, scope} ou None.

    Le `scope` est la lentille resolue cote serveur : sid/pid/persona/tools/
    data/mode/actor. None si cle inconnue, revoquee ou expiree.
    """
    if not key or not key.startswith(_SCOPED_PREFIX):
        return None
    if not _DB_PATH.exists():
        return None
    try:
        async with aiosqlite.connect(_DB_PATH) as db:
            row = await (await db.execute(
                "SELECT username, study_id, project_id, persona, tools_json, "
                "data_scope, mode, actor, expires_at, revoked_at "
                "FROM scoped_keys WHERE id = ?", (key,),
            )).fetchone()
    except Exception:
        return None
    if not row:
        return None
    (username, study_id, project_id, persona, tools_json, data_scope,
     mode, actor, expires_at, revoked_at) = row
    if revoked_at:
        return None
    if expires_at and time.time() > expires_at:
        return None
    try:
        tools = json.loads(tools_json) if tools_json else _SCOPED_TOOLS_ALL
    except Exception:
        tools = _SCOPED_TOOLS_ALL
    return {
        "username": username,
        "role": "admin" if username in _ADMIN_USERS else "user",
        "source": "scoped",
        "scope": {
            "owner":   username,
            "sid":     study_id,
            "pid":     project_id,
            "persona": persona,
            "tools":   tools,        # "all" | ["tool_a", ...]
            "data":    data_scope,   # all | study | project
            "mode":    mode,         # supervisor | scoped
            "actor":   actor,        # owner | delegate
        },
    }


async def revoke_scoped_key(key: str) -> None:
    """Revoque une cle scopee (soft delete : revoked_at). Idempotent."""
    if not _DB_PATH.exists():
        return
    try:
        async with aiosqlite.connect(_DB_PATH) as db:
            await db.execute(
                "UPDATE scoped_keys SET revoked_at = ? "
                "WHERE id = ? AND revoked_at IS NULL",
                (int(time.time()), key),
            )
            await db.commit()
    except Exception as exc:
        log.warning("revoke_scoped_key echec: %s", exc)


async def list_scoped_keys(
    username: str, include_revoked: bool = False
) -> list[dict]:
    """Liste les cles scopees d'un user (sans exposer la valeur de la cle)."""
    if not _DB_PATH.exists():
        return []
    query = (
        "SELECT id, study_id, project_id, persona, tools_json, data_scope, "
        "mode, actor, label, created_at, expires_at, revoked_at "
        "FROM scoped_keys WHERE username = ?"
    )
    if not include_revoked:
        query += " AND revoked_at IS NULL"
    query += " ORDER BY created_at DESC"
    try:
        async with aiosqlite.connect(_DB_PATH) as db:
            db.row_factory = aiosqlite.Row
            rows = await (await db.execute(query, (username,))).fetchall()
    except Exception:
        return []
    out = []
    for r in rows:
        d = dict(r)
        # Ne jamais exposer l'id (= le bearer) en clair dans un listing.
        d["id_masked"] = (d.pop("id", "")[:14] + "…")
        try:
            d["tools"] = json.loads(d.pop("tools_json", '"all"'))
        except Exception:
            d["tools"] = _SCOPED_TOOLS_ALL
        out.append(d)
    return out


# ── Sprint Composants Phase 4a (2026-06-27) : publication scoped_keys ────────


async def get_scoped_key_meta(key_id: str) -> dict | None:
    """Retourne metadata d'une scoped_key (sans la cle elle-meme exposee).

    Inclut les colonnes publication (audience, published_url, published_at,
    audit_chain_json) pour l'UI desk et les tools natifs Phase 4a.
    """
    if not _DB_PATH.exists():
        return None
    try:
        async with aiosqlite.connect(_DB_PATH) as db:
            db.row_factory = aiosqlite.Row
            row = await (await db.execute(
                "SELECT id, parent_key, username, study_id, project_id, "
                "persona, tools_json, data_scope, mode, actor, label, "
                "created_at, expires_at, revoked_at, "
                "audience, published_url, published_at, audit_chain_json "
                "FROM scoped_keys WHERE id = ?", (key_id,),
            )).fetchone()
            if not row:
                return None
            d = dict(row)
            d["id_masked"] = d.pop("id", "")[:14] + "…"
            try:
                d["tools"] = json.loads(d.pop("tools_json", '"all"'))
            except Exception:
                d["tools"] = _SCOPED_TOOLS_ALL
            return d
    except Exception as exc:
        log.warning("get_scoped_key_meta echec: %s", exc)
        return None


async def update_scoped_key_publication(
    key_id: str,
    published_url: str,
    audit_chain_json: str,
    audience: str = "cerema_internal",
) -> bool:
    """UPDATE colonnes publication apres publish_agent.

    Cf. publish_assembly pattern V1.5 : INSERT+UPDATE pour publication info.
    """
    if not _DB_PATH.exists():
        return False
    try:
        async with aiosqlite.connect(_DB_PATH) as db:
            cur = await db.execute(
                "UPDATE scoped_keys SET "
                "published_url = ?, published_at = ?, "
                "audit_chain_json = ?, audience = ? "
                "WHERE id = ? AND revoked_at IS NULL",
                (published_url, int(time.time()), audit_chain_json,
                 audience, key_id),
            )
            await db.commit()
            return (cur.rowcount or 0) > 0
    except Exception as exc:
        log.warning("update_scoped_key_publication echec: %s", exc)
        return False


async def list_scoped_keys_published(username: str) -> list[dict]:
    """Liste les agents partages publies (published_url IS NOT NULL) actifs.

    Trie par published_at DESC. Inclut audience + audit_chain_json metadata.
    Pour UI desk 8e onglet "Mes agents partages".
    """
    if not _DB_PATH.exists():
        return []
    try:
        async with aiosqlite.connect(_DB_PATH) as db:
            db.row_factory = aiosqlite.Row
            rows = await (await db.execute(
                "SELECT id, study_id, project_id, persona, tools_json, "
                "data_scope, mode, actor, label, created_at, expires_at, "
                "audience, published_url, published_at, audit_chain_json "
                "FROM scoped_keys WHERE username = ? AND revoked_at IS NULL "
                "AND published_url IS NOT NULL ORDER BY published_at DESC",
                (username,),
            )).fetchall()
            out = []
            for r in rows:
                d = dict(r)
                d["id_masked"] = d.pop("id", "")[:14] + "…"
                try:
                    d["tools"] = json.loads(d.pop("tools_json", '"all"'))
                except Exception:
                    d["tools"] = _SCOPED_TOOLS_ALL
                out.append(d)
            return out
    except Exception as exc:
        log.warning("list_scoped_keys_published echec: %s", exc)
        return []


async def _bearer_scope(request: "Request") -> dict | None:
    """Si le header Authorization porte une cle scopee valide, retourne son
    scope ({sid, pid, tools, data, mode, persona, actor}), sinon None.

    Utilise par le middleware pour poser request.state.scope sur les routes
    inter-pod (dont /mcp) quand l'appelant presente une cle scopee plutot que
    la cle superviseur. Cle superviseur / OIDC -> None (= acces total).
    """
    auth_header = request.headers.get("authorization", "")
    if not auth_header.startswith("Bearer "):
        return None
    token = auth_header.removeprefix("Bearer ").strip()
    if not token.startswith(_SCOPED_PREFIX):
        return None
    user = await _validate_scoped_key(token)
    return user["scope"] if user else None


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
    Quadruple validation (Phase 0ter + fix 2026-06-24) :
    1. Cookie hub_api_key (navigateur après /login?key=...)
    2. request.state.oidc_user (cookie oidc_token deja valide par middleware OIDC)
    3. Bearer API key hub (qgis_...)
    4. Bearer token OIDC SSPCloud
    """
    # 1. Cookie navigateur (accès web via /login?key=...)
    if request:
        cookie_key = request.cookies.get("hub_api_key", "")
        if cookie_key.startswith("qgis_"):
            user = await _validate_api_key(cookie_key)
            if user:
                return user

        # 2. (NEW 2026-06-24) Fallback OIDC : si le middleware oidc_auth_middleware
        # a deja valide le cookie oidc_token et injecte request.state.oidc_user,
        # alors l'user est legitime. On genere/recupere son api_key automatiquement
        # pour permettre aux XHR du browser de marcher sans avoir besoin du cookie
        # hub_api_key (gap rouvert par cookie OIDC cross-subdomain Phase 0ter Step 1).
        # Ce chemin court-circuite la necessite pour le user de passer par
        # /login?key=... apres chaque restart du hub.
        oidc_user = getattr(request.state, "oidc_user", None)
        if oidc_user:
            api_key = await create_or_get_api_key(oidc_user)
            user = await _validate_api_key(api_key)
            if user:
                return user

    # 3. Bearer token (MCP agents ou API key)
    if not creds:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Not authenticated")

    token = creds.credentials
    # Cle scopee (agent configure/publie) — prefixe qgisk_ (distinct de qgis_).
    # Retourne {username, role, source:"scoped", scope:{...}} ; le scope est
    # ensuite lu par le proxy /mcp pour le filtrage tools + injection sid/pid.
    if token.startswith(_SCOPED_PREFIX):
        user = await _validate_scoped_key(token)
        if user:
            return user
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Clé scopée invalide, révoquée ou expirée",
        )
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


# ── Phase 0ter (RGPD) : Middleware OIDC pour protéger l'accès UI ──────────────
# Sans cela, n'importe qui avec l'URL `user-X-qgis.user.lab.sspcloud.fr/desk`
# accède à l'espace user-X (fuite RGPD critique). Le portail set un cookie
# oidc_token avec Domain=.user.lab.sspcloud.fr (cf. commit Phase 0ter Step 1),
# le middleware vérifie ce cookie et 403 si le preferred_username ne matche
# pas ONYXIA_USER (le owner du namespace).

# Routes publiques : healthchecks K8s + endpoints d'identification
_OIDC_MIDDLEWARE_PUBLIC = (
    "/health",        # readinessProbe K8s
    "/healthz",       # alias
    "/api/version",   # Phase 1 auto-update (poll public)
    # OAuth 2.0 / decouverte MCP (connecteur distant claude.ai) — fix 2026-06-25.
    # claude.ai fait la decouverte et l'echange code->token COTE SERVEUR, sans
    # cookie navigateur. Sans whitelist, ces routes renvoient 401 "Auth requise"
    # et le connecteur ne peut pas aboutir. Surs en public :
    #   - .well-known/* : metadonnees statiques, aucun secret.
    #   - /oauth/token : auth interne (code a usage unique + PKCE, ou
    #     client_secret = api_key validee par _validate_api_key).
    #   - /authorize(+/confirm) : le formulaire de consentement exige la saisie
    #     de l'api_key, validee avant d'emettre un code.
    # La garde RGPD reste intacte : /desk, /workspace, /studies... restent gates.
    "/.well-known/oauth-authorization-server",
    "/.well-known/oauth-protected-resource",
    "/authorize",     # couvre aussi /authorize/confirm via le check startswith
    "/oauth/token",
    "/oauth/register",  # Dynamic Client Registration (RFC 7591) - claude.ai
    # Bug fix 2026-06-27 : MinIO SSPCloud n'accepte plus ACL canned public-read
    # sur objets uploades (AccessDenied 403). Le hub sert via /published/...
    # apres lecture S3 cote serveur. Cet endpoint est PUBLIC par design
    # (assemblages publishables avec audience), accessible aux collegues
    # CEREMA via SSO ET aux tiers via lien direct. Pas d'OIDC requis.
    "/published",
    # Sprint sec-rbac RBAC-3 (2026-07-19) : URLs stables versionees /p/{slug}
    # sont censees etre publiques comme un DOI (voir docstring
    # resolve_latest_publication_endpoint main.py:7365 : "Cette route est
    # publique - jamais changer, toujours pointer vers l'etat courant"). Le
    # middleware OIDC les gate implicitement aujourd'hui -> tiers non-logges
    # ne peuvent pas les consommer -> le sens "DOI" est casse. Fix : ajouter
    # /p au whitelist public + appliquer le meme audience gate que
    # /published (via _serve_published_audience_gate) dans les endpoints
    # /p/{slug} et /p/{slug}/v{version} eux-memes.
    "/p",
    # Sprint sec-rgpd P0-3 (2026-07-19) : robots.txt statique public.
    "/robots.txt",
    # Correctif 2026-08-22 : icone d'onglet. Sans whitelist, le middleware
    # repondait 401 a chaque page et polluait la console du navigateur.
    # Aucun secret dans une icone.
    "/favicon.ico",
    # Les contrats publies. Un contrat derriere authentification n'en est pas
    # un : aucun projet tiers ne peut s'y referer. Ne sert que des schemas,
    # aucune donnee d'etude (cf. hub/contracts.py).
    "/schemas",
    # Audit v1.7.2 P1 #3 (D-QGIS-010) : bundle Vite BlockNote = JS/CSS pur
    # statique, AUCUN secret. La page principale /editor/{sid}/assembly/{aid}
    # reste protegee OIDC (elle sert l'index.html via FileResponse). Whitelist
    # /static/blocknote-editor evite un round-trip JWT decode + JWKS fetch
    # par asset (browser charge ~10 chunks Vite) -> perf + scaling.
    "/static/blocknote-editor",
    # V1.20.5 (2026-07-08) : lib cerema-geo-components v0.1.0-alpha vendorisee
    # dans hub/hub/static/. Whitelist /static pour servir geo-components.js
    # (JS pur statique, aucun secret) sans round-trip OIDC. Consommee par
    # storymap_dsfr.html.j2 + _interactive_map_partial_v2.j2. Le middleware
    # fait path.startswith(p + "/") donc "/static" sans trailing slash matche
    # "/static/geo-components.js" via "/static/".
    "/static",
    # Sprint Day 5 Phase 2-1 (2026-08-05) : /login unifie (form POST cle
    # HUB_API_KEY). Publique car user arrive sans cookie au 1er acces.
    # Zero exposition URL : la cle est saisie dans le form body (POST),
    # jamais en query string. Ancien flow /login?key= (Day 4.2) retire :
    # le GET /login avec `?key=` fait un redirect immediat vers /login form.
    "/login",
    # Sprint Day 5 Phase 1 (2026-08-04) : /onboarding + /auth/token-login
    # absorbent le role de bootstrap du portail nic01asfr. Le user colle son
    # token OIDC dans /onboarding (form HTML), POST vers /auth/token-login qui
    # valide le token via JWKS Keycloak SSPCloud + pose cookie oidc_token
    # cross-subdomain (Domain=.user.lab.sspcloud.fr) + cookie hub_api_key
    # (Day 4 auto-set idempotent). Objectif : disparition portail au profit
    # d'un chart Helm Onyxia officiel. Ces routes DOIVENT etre publiques
    # car l'user arrive sans cookie (bootstrap initial).
    "/onboarding",
    "/auth/token-login",
    # Sprint Day 5 Phase 2-1 (2026-08-05) : /login-password legacy garde
    # pour redirect 301 vers /login (compat bookmarks users). Retirer apres
    # 3 mois. Publique car aucun secret ne transite (juste redirect).
    "/login-password",
)

# Routes inter-pods : Bearer HUB_API_KEY = clé hub partagée entre hub et agent
# (agent appelle /mcp du hub, portail appelle /api/hub-status, etc.)
_OIDC_MIDDLEWARE_INTER_POD = (
    "/mcp",                     # agent -> hub MCP
    "/api/hub-status",          # portail polling
    "/api/admin",               # actions admin (ADMIN_TOKEN ou HUB_API_KEY)
    "/api/reload-llm-key",      # webhook portail
    "/api/reload-hub-key",      # webhook futur (Mini-Phase 0bis)
    # Fix consolidation 2026-06-19 : /studies/* doit etre joignable via
    # Bearer HUB_API_KEY pour :
    #   - hub self-call /desk/catalog -> /studies/active (sinon footer
    #     "0 etudes" + dropdown etude vide cote desk.html)
    #   - agent _resolve_active_sid /studies/active (sinon tool natif
    #     list_recipes_for_study retourne "aucune etude active" alors
    #     que l'etude EST active en DB)
    #   - agent save_recipe dispatch /studies/{sid}/recipes/{slug}
    # Le Depends(get_current_user) cote endpoints continue de valider le
    # Bearer + extraire l'identite -> meme niveau d'auth qu'avant.
    "/studies",
    # Memes raisons : self-calls hub->hub identifies en audit grep
    # (cf. main.py:3630, 4104, 1067 etc.) -> sans whitelist, les
    # catalog/list, sessions/create, wake echouent silencieusement.
    "/catalog",                 # hub -> hub catalog S3 listing
    "/sessions",                # hub -> hub list/create sessions workspace
    "/workspace/wake",          # hub -> hub wake workspace (api/start-study)
    # Sprint UX-3 Commit 2 (2026-06-21) : endpoints projects (1:N etude->projet).
    # Inclus dans /studies/* (deja whitelist) via le startswith check, MAIS
    # /projects/active est top-level -> whitelist explicite. Permet a l'agent
    # (Bearer HUB_API_KEY) d'appeler /projects/active dans _resolve_active_project.
    "/projects",
    # Sprint Composants Phase 2 (2026-06-25) : endpoints schema introspection
    # pour l'agent IA meta-cognitif (describe_entity_schema, validate_manifest,
    # list_entity_kinds). Permet a l'agent de connaitre les Pydantic schemas
    # AVANT d'appeler create_component / create_assembly (anti-hallucination,
    # cf. verdict evaluateur D).
    # /studies/{sid}/components/* et /studies/{sid}/projects/{pid}/components/*
    # sont deja couverts via /studies prefix.
    "/schema",
    # Sprint Composants Phase 3c (2026-06-27) : endpoints internes inter-pod
    # pour le meta-agent recipe_analyzer.
    # /internal/profiles/{id}/full : agent recupere agent_system_prompt
    # complet (filtre dans /profiles/{id} public).
    "/internal",
    # Fix consolidation 2026-07-13 : /admin/* doit être joignable via Bearer
    # HUB_API_KEY pour le call portail -> hub `configure_agent_after_deploy`
    # (POST /admin/agent-config). Sans cette whitelist, TOUT nouvel onboarding
    # user via portail échoue silencieusement en background depuis fd51f0a
    # (15 juin) : la row `deployments` reçoit `status="ok"` (car sauvée AVANT
    # le call config-agent), mais l'agent tourne sans LLM_API_KEY -> l'user
    # voit le bandeau "clé LLM manquante" à vie sans possibilité de refresh.
    # L'endpoint /admin/agent-config fait déjà son propre check ADMIN_TOKEN
    # avant d'accepter la config, même niveau de protection.
    # Bug jumeau `/studies` fixé 2026-06-19 (d09ed60) ; oubli complété ici.
    "/admin",
    # Chantier G8 / G4-b (Sprint V0.3) : endpoints recipes web deterministes.
    # L'agent appelle POST /api/recipes-web/execute en mode mute (recipe_run)
    # avec Bearer HUB_API_KEY. Sans whitelist -> 401 silencieux dans le stream
    # SSE (`event: recipe_error`) et l'user voit un livrable qui ne s'execute
    # jamais. GET /api/recipes-web/list est aussi couvert.
    "/api/recipes-web",
    # Chantier G5 / G7 (Sprint V0.3) : endpoints bibliotheque briques.
    # briques_client (G7) + briques_enricher (G6) appellent GET /briques/*
    # avec Bearer HUB_API_KEY pour composer les sections GLOBAL_RULES +
    # FORBIDDEN du prompt structure. Sans whitelist -> 401 silencieux
    # -> prompt LLM sans briques -> anti-hallucination cassee.
    "/briques",
    # Sprint isolation multi-agent (2026-07-18) : les 2 nouveaux endpoints
    # doivent accepter Bearer HUB_API_KEY (superviseur) sinon le middleware
    # OIDC retourne 401 avant que le Depends(auth.get_current_user) puisse
    # valider le token. Meme pattern que /studies + /admin ci-dessus.
    #   - /diagnostics/isolation : observability endpoint reserve superviseurs
    #     (get_current_user rejette les cles scopees agent avec 403 in-endpoint,
    #     donc auth double-layer safe).
    #   - /agent-context/new : endpoint appele en Bearer par un agent (Claude
    #     Desktop / recipe / chat) pour creer un contexte etude+projet fresh
    #     isole. get_current_user valide le Bearer normalement.
    "/diagnostics",
    "/agent-context",
    # Sprint isolation Day 3.1c (2026-08-02) : /publish/{kind}/{slug} appele
    # en self-call workspace->hub via publish_artifact MCP tool + Authorization
    # Bearer HUB_API_KEY. Sans whitelist, le middleware OIDC redirige vers
    # portail (401 JSON) avant que l'endpoint puisse verifier la cle. Bug
    # empirique 2026-08-02 test E2E : publish storymap echouait toujours.
    # Whitelist les endpoints publish top-level et sub-endpoints (component,
    # assembly, livrable, pdf, agent) qui suivent la meme convention.
    "/publish",
    # NB : /published a ete deplace dans _OIDC_MIDDLEWARE_PUBLIC (vraie
    # whitelist anonyme). Inter-pod ne suffit pas pour acces tiers internet.
)


async def _is_inter_pod_authorized(request: "Request") -> bool:
    """True si la requête vient d'un pod interne avec Bearer HUB_API_KEY valide.

    Fix 2026-06-19 : compare contre la SOURCE DE VERITE (Secret K8s
    qgis-hub-apikey) au lieu de os.environ qui peut etre vide ou stale
    si la cle a rotate apres le boot du pod. Utilise le helper
    `create_or_get_api_key` qui cache la valeur in-memory + relit Secret
    si TTL expire (cf. _CACHE_TTL ligne 56). Sans ce fix, les self-calls
    hub->hub avec Bearer HUB_API_KEY etaient rejetes silencieusement par
    le middleware OIDC (alors meme que la route etait whitelist).
    """
    auth_header = request.headers.get("authorization", "")
    if not auth_header.startswith("Bearer "):
        return False
    presented = auth_header.removeprefix("Bearer ").strip()
    if not presented:
        return False
    # Source de verite : Secret K8s qgis-hub-apikey via cache create_or_get_api_key.
    # `_NAMESPACE` correspond a `user-<ONYXIA_USER>` -> removeprefix donne le user.
    try:
        expected = await create_or_get_api_key(
            _NAMESPACE.removeprefix("user-") if _NAMESPACE else ""
        )
    except Exception:
        # Fallback env var (compat ascendante si Secret K8s indisponible)
        import os
        expected = os.environ.get("HUB_API_KEY", "")
    return bool(expected) and presented == expected


def _portal_login_redirect_url(request: "Request") -> str:
    """URL de redirection pour login utilisateur.

    Sprint Day 5 Phase 1.3 (2026-08-04) : redirige vers la page hub
    /onboarding au lieu du portail nic01asfr externe. Cela absorbe le
    role bootstrap du portail cote hub (Phase 1.1 endpoint /onboarding
    + /auth/token-login). Le nom de la fonction est conserve pour
    retro-compat avec les 3 usages middleware, mais la cible a change.

    Sprint Day 5 Phase 2-2 (2026-08-05) + fix 2026-08-06 : redirect vers
    /login (form cle HUB_API_KEY) au lieu de /onboarding (token OIDC).
    Le user vierge sort de install.sh avec sa cle imprimee -> /login est
    le flow canonique Phase 2-1. /onboarding reste dispo en secours mais
    n'est plus la cible par defaut du redirect middleware.
    """
    base = str(request.base_url).rstrip("/")
    return f"{base}/login"


async def oidc_auth_middleware(request: "Request", call_next):
    """Middleware FastAPI : vérifie cookie OIDC + match preferred_username == ONYXIA_USER.

    Whitelist :
      - Routes publiques (healthchecks)
      - Routes inter-pod si Authorization: Bearer HUB_API_KEY match env
      - kube-probe user-agent (court-circuit identique a Bug #17 fix)

    Sinon : décode cookie oidc_token, check claims.preferred_username ==
    ONYXIA_USER, sinon 403/redirect portail.
    """
    import os
    from fastapi.responses import JSONResponse, RedirectResponse
    path = request.url.path

    # 1. Routes publiques (probe, version)
    if any(path == p or path.startswith(p + "/") for p in _OIDC_MIDDLEWARE_PUBLIC):
        return await call_next(request)

    # 2. Court-circuit kube-probe (idem Bug #17 fix L1490 hub_home).
    # Fix H1 revue adversariale Sprint V0.4.1 : exige que la requete provienne
    # bien du cluster K8s. Un vrai kube-probe (kubelet -> pod) n'a JAMAIS de
    # header X-Forwarded-For (pas de hop ingress). Un attaquant Internet qui
    # forge le User-Agent passe forcement par l'ingress SSPCloud qui ajoute
    # ce header. Sans ce check, `curl -H "User-Agent: kube-probe"` depuis
    # Internet bypass toute l'auth. Casse la garde RGPD Phase 0ter.
    if (
        "kube-probe" in request.headers.get("user-agent", "").lower()
        and "x-forwarded-for" not in request.headers
    ):
        return await call_next(request)

    # 3. Routes inter-pod avec Bearer HUB_API_KEY
    if any(path == p or path.startswith(p + "/") for p in _OIDC_MIDDLEWARE_INTER_POD):
        if await _is_inter_pod_authorized(request):
            return await call_next(request)
        # 3bis. Cle scopee (agent configure/publie) : authentifie + porte un
        # scope. On pose request.state.scope pour que le proxy /mcp applique le
        # filtrage tools + injection sid/pid (etape ulterieure). Tant qu'aucun
        # endpoint de mint n'existe, aucune cle scopee n'est emise -> chemin
        # inerte en prod. La cle superviseur passe deja via _is_inter_pod ci-dessus
        # (== HUB_API_KEY en hub mono-user) -> request.state.scope reste None = total.
        scoped = await _bearer_scope(request)
        if scoped:
            request.state.scope = scoped
            return await call_next(request)
        # Sinon on tombe sur le check OIDC ci-dessous (fallback UI)

    # Sprint UX Auth Persistante Day 4 (2026-08-02) : le user n'a pas a
    # repasser par le portail OIDC a chaque expiration cookie oidc_token.
    # Si un cookie `hub_api_key` valide est present (TTL 90j pose par
    # /login?key=... ou par le portail lors de l'onboarding), on laisse
    # passer directement. Le user retient sa cle API personnelle stable
    # (visible sur /workspace ou via GET /auth/apikey) au lieu de re-coller
    # le token OIDC eyJ... a chaque fois.
    #
    # Priorite d'auth pour routes UI :
    #   3ter. Cookie hub_api_key (nouveau, longue TTL)
    #   4. Cookie OIDC (fallback, bootstrap initial ou perte cle API)
    _cookie_key = request.cookies.get("hub_api_key", "")
    if _cookie_key.startswith("qgis_"):
        try:
            _user_from_cookie = await _validate_api_key(_cookie_key)
            if _user_from_cookie:
                # Sprint securite Day 4.1 (2026-08-03) : check ownership
                # ONYXIA_USER identique au chemin OIDC (etape 5) pour empecher
                # un user X d'acceder au pod hub d'un user Y avec sa cle X.
                # Sans ce check, la Day 4 introduisait un bypass cross-tenant :
                # cookie hub_api_key laissait passer sans verifier que
                # l'identite derriere la cle matche le pod cible.
                _cookie_user = _user_from_cookie.get("username", "")
                _pod_user = os.environ.get("ONYXIA_USER", "")
                if _pod_user and _cookie_user != _pod_user:
                    return JSONResponse(
                        {"detail": f"Cet espace appartient a '{_pod_user}'. "
                                   f"Tu es connecte en tant que "
                                   f"'{_cookie_user}'. Connecte-toi sur "
                                   f"ton propre espace via le portail."},
                        status_code=403,
                    )
                # Injecte dans state pour downstream (compat get_current_user)
                request.state.oidc_user = _cookie_user
                return await call_next(request)
        except Exception as exc:
            import traceback as _tb
            log.warning(
                "middleware: hub_api_key cookie validation failed : %s\n%s",
                exc, _tb.format_exc(),
            )

    # 4. Routes UI : cookie OIDC obligatoire (fallback si pas de hub_api_key)
    token = request.cookies.get("oidc_token") or ""
    if not token:
        # Pas de cookie -> redirect vers portail pour saisie token
        # (Browser navigation : auto follow 302). Si XHR/fetch (Accept ne
        # contient pas text/html), retourne 401 JSON a la place.
        accept = request.headers.get("accept", "")
        if "text/html" in accept:
            return RedirectResponse(_portal_login_redirect_url(request), status_code=302)
        # Auto-decouverte OAuth (MCP spec 2025-06-18) : sur /mcp, on indique a
        # claude.ai ou trouver le serveur d'autorisation via WWW-Authenticate.
        headers = None
        if path == "/mcp" or path.startswith("/mcp/"):
            base = os.environ.get("HUB_URL") or (
                f"https://user-{onyxia}-qgis.user.lab.sspcloud.fr"
                if (onyxia := os.environ.get("ONYXIA_USER", "")) else ""
            )
            if base:
                rm = f"{base}/.well-known/oauth-protected-resource"
                headers = {"WWW-Authenticate": f'Bearer resource_metadata="{rm}"'}
        return JSONResponse(
            {"detail": "Auth requise. Va sur le portail pour t'identifier.",
             "portal_url": _portal_login_redirect_url(request)},
            status_code=401,
            headers=headers,
        )

    # 5. Décode JWT + vérifie ownership
    onyxia_user = os.environ.get("ONYXIA_USER", "")
    try:
        signing_key = _jwks.get_signing_key_from_jwt(token)
        claims = jwt.decode(
            token, signing_key.key, algorithms=["RS256"],
            options={"verify_aud": False},
        )
    except jwt.ExpiredSignatureError:
        return RedirectResponse(_portal_login_redirect_url(request), status_code=302)
    except Exception as exc:
        return JSONResponse(
            {"detail": f"Token invalide : {exc}"},
            status_code=401,
        )

    claimed_user = claims.get("preferred_username") or claims.get("sub", "")
    if onyxia_user and claimed_user != onyxia_user:
        return JSONResponse(
            {"detail": f"Cet espace appartient a '{onyxia_user}'. Tu es "
                       f"connecte en tant que '{claimed_user}'. Connecte-toi "
                       f"sur ton propre espace via le portail."},
            status_code=403,
        )

    # OK : injecte claims dans request.state pour downstream
    request.state.oidc_claims = claims
    request.state.oidc_user = claimed_user

    # Sprint UX Auth Persistante Day 4 (2026-08-02) : auto-set cookie
    # hub_api_key apres la 1ere validation OIDC reussie. Le user n'a plus
    # besoin de recoller son token OIDC a chaque expiration cookie oidc_token
    # (courte TTL Keycloak SSPCloud). Cookie stable 90j = onboarding OIDC
    # une seule fois, ensuite acces navigateur transparent.
    #
    # Cle API mint automatiquement si absente (idempotent, Secret K8s
    # persistant). Ajoute au response en fin de traitement via wrapper.
    _response = await call_next(request)
    try:
        # Ne repose pas le cookie s'il est deja present (idempotence + evite
        # de rewrite le cookie a chaque requete)
        if not request.cookies.get("hub_api_key"):
            api_key = await create_or_get_api_key(claimed_user)
            if api_key:
                _response.set_cookie(
                    "hub_api_key", api_key,
                    httponly=True, secure=True,
                    max_age=90 * 24 * 3600, samesite="lax",
                    path="/",
                )
                log.info(
                    "auto-set cookie hub_api_key pour user %s (OIDC bootstrap)",
                    claimed_user,
                )
    except Exception as exc:
        log.warning("auto-set hub_api_key cookie echec pour %s : %s",
                    claimed_user, exc)
    return _response
