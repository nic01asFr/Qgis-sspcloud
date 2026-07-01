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
import json
import logging
import os
import secrets
import time
import urllib.parse
from collections import defaultdict
from contextlib import asynccontextmanager

import httpx
from fastapi import Depends, FastAPI, Form, HTTPException, Request, Response, WebSocket, status
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, RedirectResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from starlette.websockets import WebSocketDisconnect
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
def _parse_port(raw: str | None, default: int) -> int:
    """Parse un port robustement, résilient aux env vars auto-injectées par K8s.

    Quand un Service `geoai-gpu` existe dans le namespace, Kubernetes injecte
    automatiquement GEOAI_GPU_PORT=tcp://10.233.x.y:8000 (service-link, forme
    URL) qui casse int() → ValueError au démarrage du hub. On extrait le port
    si la valeur est une URL tcp://host:port, sinon int() direct, sinon default.
    (nic01asfr ne crashait pas car start_hub.sh exporte GEOAI_GPU_PORT=8000
    explicitement ; un déploiement Onyxia frais n'a pas cet override.)
    """
    if not raw:
        return default
    v = str(raw).strip()
    if "://" in v:                      # forme K8s service-link tcp://IP:PORT
        v = v.rsplit(":", 1)[-1]
    try:
        return int(v)
    except (ValueError, TypeError):
        return default


_GEOAI_GPU_SERVICE = os.getenv("GEOAI_GPU_SERVICE_NAME", "")
_GEOAI_GPU_PORT    = _parse_port(os.getenv("GEOAI_GPU_PORT"), 8000)


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

    # Chercher la cle LLM SSPCloud dans les secrets du namespace.
    # Onyxia stocke la config AI Assistant (datalab account -> Profil) dans
    # `<service>-secretassistant`, cle `config.json` :
    #   {"api_keys": {"OPENAI_API_KEY": "sk-..."}, ...}
    # On extrait UNIQUEMENT la cle. Le modele et la base URL LLM sont
    # gerés cote agent (_MODEL_BY_PROFILE dans agent/qgis_agent.py) : ils
    # pointent vers les modeles SSPCloud reellement disponibles
    # (qwen3-6-35b-moe, gemma4-26b-moe), pas vers le model_provider_id par
    # defaut du config.json datalab (souvent un modele inexistant).
    llm_api_key = ""

    async with httpx.AsyncClient(verify=False, timeout=15) as client:
        try:
            sr = await client.get(
                f"{_K8S_HOST}/api/v1/namespaces/{ns}/secrets",
                headers=headers, params={"fieldSelector": "type=Opaque"},
            )
            import base64 as _b64
            import json as _json
            for secret in (sr.json().get("items") or []):
                name = secret.get("metadata", {}).get("name", "")
                if "secretassistant" not in name:
                    continue
                raw = secret.get("data", {}).get("config.json")
                if not raw:
                    continue
                try:
                    cfg = _json.loads(_b64.b64decode(raw).decode())
                except Exception:
                    continue
                key = (cfg.get("api_keys") or {}).get("OPENAI_API_KEY", "")
                if not key:
                    continue  # secret existe mais cle pas encore renseignee
                llm_api_key = key
                log.info("bootstrap: LLM_API_KEY trouve dans %s", name)
                break
        except Exception as exc:
            log.warning("bootstrap: impossible de lire LLM_API_KEY depuis secrets: %s", exc)
        # Vérifier si qgis-agent existe déjà
        r = await client.get(
            f"{_K8S_HOST}/apis/apps/v1/namespaces/{ns}/statefulsets/qgis-agent",
            headers=headers,
        )
        if r.status_code == 200:
            # Agent existant : reconfigurer son env pour pointer sur le Secret
            # k8s `qgis-hub-apikey` (refonte : Secret = source de verite, plus
            # de valeur inline). Cas typique : SS deja deployee avec une cle
            # inline en dur (legacy). On bascule vers secretKeyRef → le pod
            # lira la cle COURANTE du Secret a chaque (re)demarrage, plus
            # jamais de desync entre l'env de l'agent et la DB du hub.
            patch_env = [
                {"name": "HUB_API_KEY", "valueFrom": {"secretKeyRef": {
                    "name": "qgis-hub-apikey",
                    "key":  "HUB_API_KEY",
                }}},
                {"name": "LLM_API_KEY",  "value": llm_api_key},
                {"name": "HUB_URL",      "value": _HUB_URL},
                # DATA_DIR aligne sur le mountPath du PVC (voir spec creation
                # ci-dessous). Pour les SS existants pre-PVC, ce path sera
                # juste un dir vide rootfs — il faudra delete + recreate le
                # SS pour beneficier du PVC reellement persistant.
                {"name": "DATA_DIR",     "value": "/data"},
                {"name": "ONYXIA_USER",  "value": username},
            ]
            existing_env = (r.json().get("spec", {}).get("template", {})
                            .get("spec", {}).get("containers", [{}])[0]
                            .get("env", []))
            new_env = [e for e in existing_env
                       if e["name"] not in {p["name"] for p in patch_env}]
            new_env.extend(patch_env)

            patch_url = f"{_K8S_HOST}/apis/apps/v1/namespaces/{ns}/statefulsets/qgis-agent"
            patch_headers = {**headers, "Content-Type": "application/strategic-merge-patch+json"}
            patch_body = {"spec": {"template": {"spec":
                {"containers": [{"name": "agent", "env": new_env}]}}}}

            patched_ok = False
            for attempt in range(3):
                try:
                    pr = await client.patch(patch_url, headers=patch_headers, json=patch_body)
                    if pr.status_code >= 300:
                        raise RuntimeError(f"HTTP {pr.status_code}: {pr.text[:200]}")
                    log.info("bootstrap: qgis-agent env patché pour %s (try %d/3)",
                             username, attempt + 1)
                    patched_ok = True
                    break
                except Exception as exc:
                    log.warning("bootstrap: patch agent try %d/3 échec: %s: %s",
                                attempt + 1, type(exc).__name__, exc)
                    if attempt < 2:
                        await asyncio.sleep(2)
            if not patched_ok:
                log.error(
                    "bootstrap: patch agent IMPOSSIBLE après 3 tentatives — "
                    "l'agent garde une HUB_API_KEY potentiellement stale, "
                    "ses appels /mcp risquent de renvoyer 401 silencieux. "
                    "Verifier RBAC k8s ou redeployer l'agent SS a la main."
                )
                return  # ne pas supprimer le pod : env pas à jour

            # Patch sts OK : spec K8s a jour (futurs restarts pod liront la
            # bonne env). Maintenant on propage la cle EN RAM au pod courant
            # sans le restart -> downtime 0s, plus de Bug C+D structurel.
            #
            # Option alpha (2026-06-02) : webhook agent /api/reload-llm-key
            # plutot que delete pod. L'agent expose un endpoint qui met a jour
            # os.environ["LLM_API_KEY"] -> les lectures dynamiques cote agent
            # (qgis_agent._llm_api_key(), vector_store, insight_extractor, STT)
            # voient la nouvelle cle au prochain call LLM. Aucun restart pod.
            #
            # Compat ascendante : si l'agent tourne sur une vieille image sans
            # ce endpoint, le webhook retourne 404 -> on tombe dans le fallback
            # legacy `delete pod qgis-agent-0`.
            reloaded_in_ram = False
            try:
                # Service DNS interne cluster : qgis-agent.<ns>.svc.cluster.local:8888
                # (le Service `qgis-agent` est cree plus bas dans _bootstrap_agent
                # quand le sts est nouveau ; pour un sts existant il existe deja).
                webhook_url = f"http://qgis-agent.{ns}.svc.cluster.local:8888/api/reload-llm-key"
                wr = await client.post(
                    webhook_url,
                    json={"llm_api_key": llm_api_key},
                    headers={"X-Hub-Auth": hub_api_key,
                             "Content-Type": "application/json"},
                    timeout=5,
                )
                if wr.status_code == 200:
                    reloaded_in_ram = True
                    log.info(
                        "bootstrap: webhook agent reload-llm-key OK pour %s "
                        "(zero downtime, no pod restart)", username,
                    )
                elif wr.status_code == 404:
                    log.info(
                        "bootstrap: agent vieille image (404 sur /api/reload-llm-key) "
                        "-> fallback delete pod pour propager la nouvelle env",
                    )
                else:
                    log.warning(
                        "bootstrap: webhook agent HTTP %d (%s) -> fallback delete pod",
                        wr.status_code, wr.text[:200],
                    )
            except Exception as wexc:
                log.warning(
                    "bootstrap: webhook agent KO (%s: %s) -> fallback delete pod",
                    type(wexc).__name__, wexc,
                )

            if reloaded_in_ram:
                return  # Pas de restart pod : tout est in-RAM, downtime = 0s

            # Fallback legacy : delete pod -> kubelet recree avec env spec a jour.
            # Cas typiques : agent pas encore demarre (pas de listener webhook),
            # vieille image agent sans le endpoint, pb reseau cluster-interne.
            try:
                del_headers = {k: v for k, v in headers.items() if k != "Content-Type"}
                dr = await client.delete(
                    f"{_K8S_HOST}/api/v1/namespaces/{ns}/pods/qgis-agent-0",
                    headers=del_headers,
                )
                if dr.status_code >= 300 and dr.status_code != 404:
                    log.warning("bootstrap: delete pod agent HTTP %d: %s",
                                dr.status_code, dr.text[:200])
                else:
                    log.info("bootstrap: pod qgis-agent-0 supprimé pour redémarrage (fallback)")
            except Exception as exc:
                log.warning("bootstrap: suppression pod agent échouée: %s", exc)
            return

        log.info("bootstrap: création qgis-agent pour %s dans %s", username, ns)

        # StatefulSet avec PVC dedie pour la memoire agent.
        # Sans PVC, le path DATA_DIR=/home/onyxia/work/qgis-agent-data atterrit
        # sur le rootfs overlay du pod -> tout perdu au moindre restart
        # (sessions chat, messages, insights, projets, recettes). Bug observe
        # 2026-05-30 : Fix Bug B (sessions liees a l'etude) fonctionnait dans
        # le pod courant mais s'effacait au redeploy. Fix : un volumeClaimTemplate
        # nomme `data` que le SS materialise en PVC `data-qgis-agent-0` (10 Gi
        # rook-ceph-block, la storageClass standard SSPCloud, cf. workspace QGIS).
        sts = {
            "apiVersion": "apps/v1", "kind": "StatefulSet",
            "metadata": {"name": "qgis-agent", "namespace": ns,
                         "labels": {"app": "qgis-agent"}},
            "spec": {
                "serviceName": "qgis-agent", "replicas": 1,
                "selector": {"matchLabels": {"app": "qgis-agent"}},
                "volumeClaimTemplates": [{
                    "metadata": {"name": "data"},
                    "spec": {
                        "accessModes":      ["ReadWriteOnce"],
                        "storageClassName": "rook-ceph-block",
                        "resources": {"requests": {"storage": "10Gi"}},
                    },
                }],
                "template": {
                    "metadata": {"labels": {"app": "qgis-agent"}},
                    "spec": {
                        # Le PVC `data` est cree par kubelet avec ownership
                        # root par defaut. L'agent tourne en USER 1000
                        # (cf. Dockerfile.agent l.13) -> sans fsGroup le pod
                        # crashe au startup avec PermissionError /data/sessions.
                        # fsGroup demande a kubelet de chgrp+chmod le volume
                        # avec gid=1000 a la creation, rendant /data
                        # writable par l'agent.
                        "securityContext": {"fsGroup": 1000},
                        "containers": [{
                        "name": "agent",
                        "image": _AGENT_IMAGE,
                        "imagePullPolicy": "Always",
                        "command": ["uvicorn", "agent.main:app",
                                    "--host", "0.0.0.0", "--port", "8888"],
                        "ports": [{"containerPort": 8888}],
                        "env": [
                            {"name": "ONYXIA_USER",  "value": username},
                            # DATA_DIR pointe sur le mountPath du PVC ci-dessous
                            # pour que memory.py l'utilise (cf. agent/memory.py:30).
                            {"name": "DATA_DIR",     "value": "/data"},
                            {"name": "HUB_URL",      "value": _HUB_URL},
                            # HUB_API_KEY via Secret namespace-level :
                            # kubelet injecte la valeur courante a chaque
                            # demarrage du pod. Plus de desync possible avec
                            # la cle du hub (cf. auth.create_or_get_api_key).
                            {"name": "HUB_API_KEY", "valueFrom": {"secretKeyRef": {
                                "name": "qgis-hub-apikey",
                                "key":  "HUB_API_KEY",
                            }}},
                            {"name": "LLM_API_KEY",  "value": llm_api_key},
                        ],
                        "volumeMounts": [
                            {"name": "data", "mountPath": "/data"},
                        ],
                        "readinessProbe": {
                            # Probe vers /health (route lite : retourne juste
                            # {status: ok}, zero query DB, zero call hub).
                            # Auparavant path:"/" + timeoutSeconds:1 hardcode
                            # par chart Onyxia -> chaque probe declenchait
                            # queries SQLite + fetch hub _fetch_active_study_id
                            # -> cold start 5-17 min observable (cf. fix
                            # 2026-06-12 sur nicolaslaval / rbouzige).
                            "httpGet": {"path": "/health", "port": 8888},
                            "initialDelaySeconds": 5, "periodSeconds": 5,
                            "timeoutSeconds": 3, "failureThreshold": 12,
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
                               "annotations": {
                                   "kubernetes.io/ingress.class": "onyxia",
                                   # Les tools longs (run_recipe, smart_load lourds) et le
                                   # flux SSE du chat depassent les 60s par defaut de
                                   # l'ingress -> 504 / flux coupe -> agent "decroche".
                                   # On aligne sur les bridges dev (proxy-read-timeout 600)
                                   # et le novnc (3600). proxy-body-size 0 = pas de limite.
                                   "nginx.ingress.kubernetes.io/proxy-read-timeout": "600",
                                   "nginx.ingress.kubernetes.io/proxy-send-timeout": "600",
                                   "nginx.ingress.kubernetes.io/proxy-connect-timeout": "30",
                                   "nginx.ingress.kubernetes.io/proxy-body-size": "0",
                               }},
                  "spec": {"ingressClassName": "onyxia", "rules": [{
                      "host": host,
                      "http": {"paths": [{"path": "/", "pathType": "Prefix",
                                          "backend": {"service": {
                                              "name": "qgis-agent-svc",
                                              "port": {"number": 8888}}}}]},
                  }]}})

        log.info("bootstrap: qgis-agent créé — %s", host)


async def _patch_own_ingress_timeout() -> None:
    """Pose proxy-read/send-timeout=600 sur l'ingress public DU HUB.

    L'ingress du hub est créé par le launcher Onyxia (chart jupyter-python) qui
    ne pose AUCUNE annotation de timeout → défaut 60s. Or l'agent appelle
    `{HUB_URL}/mcp` via cet ingress public ; `run_recipe` (analyse lourde >60s)
    renvoie alors 504 et le projet reste vide. Les services dev (bridges) ont
    600 via leur `.service.yml` ; le chemin onboardé ne passe pas par là.

    On retrouve l'ingress par son hostname (user-{owner}-qgis…) et on patche ses
    annotations. Idempotent (no-op si déjà à jour). Nécessite kubernetes.role=edit
    (déjà requis pour _bootstrap_agent).
    """
    token_file = Path("/var/run/secrets/kubernetes.io/serviceaccount/token")
    ns_file    = Path("/var/run/secrets/kubernetes.io/serviceaccount/namespace")
    if not token_file.exists():
        log.debug("Patch ingress hub: pas en K8s (dev mode), ignoré")
        return
    token    = token_file.read_text().strip()
    ns       = ns_file.read_text().strip()
    username = os.getenv("ONYXIA_USER", ns.removeprefix("user-"))
    hub_host = f"user-{username}-qgis.user.lab.sspcloud.fr"
    headers  = {"Authorization": f"Bearer {token}"}
    ann = {
        "nginx.ingress.kubernetes.io/proxy-read-timeout": "600",
        "nginx.ingress.kubernetes.io/proxy-send-timeout": "600",
        "nginx.ingress.kubernetes.io/proxy-connect-timeout": "30",
        "nginx.ingress.kubernetes.io/proxy-body-size": "0",
    }
    try:
        async with httpx.AsyncClient(verify=False, timeout=15) as client:
            r = await client.get(
                f"{_K8S_HOST}/apis/networking.k8s.io/v1/namespaces/{ns}/ingresses",
                headers=headers,
            )
            if r.status_code != 200:
                log.warning("Patch ingress hub: list KO (%d)", r.status_code)
                return
            # Cibles : l'ingress du hub (trouvé par hostname, créé par le
            # launcher) ET l'ingress agent "qgis-agent" (créé par le hub avant
            # le fix create-path — self-heal des déploiements existants). Tous
            # deux portent du trafic long (>60s) : /mcp pour le hub, SSE chat
            # pour l'agent.
            targets: list[tuple[str, dict]] = []
            for ing in (r.json().get("items") or []):
                name  = ing["metadata"]["name"]
                hosts = [ru.get("host", "")
                         for ru in ing.get("spec", {}).get("rules", [])]
                if hub_host in hosts or name == "qgis-agent":
                    targets.append((name, ing["metadata"].get("annotations", {}) or {}))
            if not targets:
                log.warning("Patch ingress: aucun ingress cible (%s / qgis-agent)", hub_host)
                return
            for name, cur in targets:
                if all(cur.get(k) == v for k, v in ann.items()):
                    log.info("Patch ingress: %s déjà à jour (no-op)", name)
                    continue
                pr = await client.patch(
                    f"{_K8S_HOST}/apis/networking.k8s.io/v1/namespaces/{ns}/ingresses/{name}",
                    headers={**headers, "Content-Type": "application/merge-patch+json"},
                    json={"metadata": {"annotations": ann}},
                )
                if pr.status_code in (200, 201):
                    log.info("Patch ingress: %s → proxy-read-timeout 600", name)
                else:
                    log.warning("Patch ingress: %s KO (%d) %s",
                                name, pr.status_code, pr.text[:200])
    except Exception as exc:
        log.warning("Patch ingress hub échoué: %s: %s", type(exc).__name__, exc)


# ── Bootstrap GeoAI GPU pod ───────────────────────────────────────────────────

_GEOAI_IMAGE       = "ghcr.io/nic01asfr/geoai-gpu:latest"
_GEOAI_SS_NAME     = "geoai-gpu-jupyter-pytorch-gpu"
_GEOAI_SVC_NAME    = "geoai-gpu"
_GEOAI_BOOT_ANNOT  = "geoai.qgis-sspcloud.io/bootstrap-done"
_GEOAI_BOOT_TIMEOUT = 1800  # 30 min max (pull image gros + queue GPU SSPCloud)


def _geoai_manifests(ns: str) -> dict:
    """Manifests K8s pour le pod GPU GeoAI — modèles bundlés dans l'image,
    PVC réduit à 5Gi (seulement cache HF inférence + sorties)."""
    sts = {
        "apiVersion": "apps/v1", "kind": "StatefulSet",
        "metadata": {
            "name": _GEOAI_SS_NAME, "namespace": ns,
            "labels": {"app": _GEOAI_SVC_NAME},
        },
        "spec": {
            "serviceName": _GEOAI_SS_NAME,
            "replicas": 1,
            "selector": {"matchLabels": {"app": _GEOAI_SVC_NAME}},
            "template": {
                "metadata": {"labels": {"app": _GEOAI_SVC_NAME}},
                "spec": {"containers": [{
                    "name": "geoai",
                    "image": _GEOAI_IMAGE,
                    "imagePullPolicy": "Always",
                    "command": ["sh", "-c",
                                "uvicorn geoai_server.main:app --host 0.0.0.0 "
                                "--port 8000 --log-level warning"],
                    "ports": [{"containerPort": 8000}],
                    "env": [
                        {"name": "GEOAI_MODELS", "value": "sam3,deepforest"},
                        {"name": "SAMGEO3_BACKEND", "value": "transformers"},
                        # IMPORTANT : modèles sur PVC pour persister entre
                        # scale 0→1 (sinon perdus à chaque cycle). HF_HOME
                        # de l'image pointe sur /home/onyxia/work/... aussi.
                        {"name": "GEOAI_MODELS_DIR", "value": "/home/onyxia/work/geoai/models"},
                        {"name": "HF_HOME", "value": "/home/onyxia/work/geoai/models/huggingface"},
                        {"name": "GPU_AUTO_ALLOC_ENABLED", "value": "0"},
                    ],
                    "resources": {
                        "limits": {"nvidia.com/gpu": 1},
                        "requests": {"cpu": "1", "memory": "4Gi"},
                    },
                    "readinessProbe": {
                        "httpGet": {"path": "/health", "port": 8000},
                        "initialDelaySeconds": 120,
                        "periodSeconds": 15,
                        "failureThreshold": 80,
                    },
                    "volumeMounts": [
                        {"name": "home", "mountPath": "/home/onyxia/work"}
                    ],
                }]},
            },
            "volumeClaimTemplates": [{
                "metadata": {"name": "home"},
                "spec": {
                    "accessModes": ["ReadWriteOnce"],
                    "resources": {"requests": {"storage": "5Gi"}},
                },
            }],
        },
    }
    svc_headless = {
        "apiVersion": "v1", "kind": "Service",
        "metadata": {"name": _GEOAI_SS_NAME, "namespace": ns},
        "spec": {
            "clusterIP": "None",
            "selector": {"app": _GEOAI_SVC_NAME},
            "ports": [{"port": 8000, "targetPort": 8000}],
        },
    }
    svc_cluster = {
        "apiVersion": "v1", "kind": "Service",
        "metadata": {"name": _GEOAI_SVC_NAME, "namespace": ns},
        "spec": {
            "selector": {"app": _GEOAI_SVC_NAME},
            "ports": [{"port": 8000, "targetPort": 8000}],
        },
    }
    return {"sts": sts, "svc_headless": svc_headless, "svc_cluster": svc_cluster}


async def _bootstrap_geoai_gpu() -> None:
    """
    Crée / vérifie le pod GPU GeoAI au démarrage du hub.

    Modèles (SAM2, DeepForest, optionnellement SAM3) sont bundlés dans
    l'image — on a juste besoin de pull l'image + initialiser CUDA pour
    valider que tout est opérationnel.

    Workflow :
      1. SS absent → créer SS + Services (replicas=1, force image pull)
      2. SS présent + annotation bootstrap-done=true → skip, set
         _GEOAI_GPU_SERVICE et exit
      3. SS présent + annotation absente → scale 1 pour re-warmup
      4. Poll /health max ~30 min (pull image gros, queue GPU SSPCloud)
      5. Au 200 → patch annotation bootstrap-done=true + scale 0
      6. Set _GEOAI_GPU_SERVICE = "geoai-gpu" (active le proxy /geoai/*)

    Failure modes gracieux :
      - SS create échoue (RBAC, quota) → log warn, hub continue sans GPU
      - Pod stays Pending >30min → log warn, annotation pas écrite, retry
        au prochain hub restart
    """
    global _GEOAI_GPU_SERVICE

    token_file = Path("/var/run/secrets/kubernetes.io/serviceaccount/token")
    ns_file    = Path("/var/run/secrets/kubernetes.io/serviceaccount/namespace")
    if not token_file.exists():
        log.debug("bootstrap geoai-gpu: pas en K8s, ignoré")
        return

    # Laisser le hub + agent démarrer d'abord
    await asyncio.sleep(8)

    token = token_file.read_text().strip()
    ns    = ns_file.read_text().strip()
    headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
    patch_headers = {**headers,
                     "Content-Type": "application/strategic-merge-patch+json"}
    manifests = _geoai_manifests(ns)
    base_url = f"http://{_GEOAI_SVC_NAME}.{ns}.svc.cluster.local:8000"

    async with httpx.AsyncClient(verify=False, timeout=30) as client:
        # Étape 1/2 — État SS existant
        r = await client.get(
            f"{_K8S_HOST}/apis/apps/v1/namespaces/{ns}/statefulsets/{_GEOAI_SS_NAME}",
            headers=headers,
        )
        if r.status_code == 200:
            ann = r.json().get("metadata", {}).get("annotations", {}) or {}
            if ann.get(_GEOAI_BOOT_ANNOT) == "true":
                log.info("bootstrap geoai-gpu: déjà bootstrapé, skip")
                _GEOAI_GPU_SERVICE = _GEOAI_SVC_NAME
                return
            # Re-warmup : scale 1
            log.info("bootstrap geoai-gpu: SS existant sans annotation, scale 1 pour re-warmup")
            try:
                await client.patch(
                    f"{_K8S_HOST}/apis/apps/v1/namespaces/{ns}/statefulsets/{_GEOAI_SS_NAME}",
                    headers=patch_headers,
                    json={"spec": {"replicas": 1}},
                )
            except Exception as exc:
                log.warning("bootstrap geoai-gpu: scale 1 échoué: %s", exc)
        elif r.status_code == 404:
            # Création complète
            log.info("bootstrap geoai-gpu: création initiale SS + Services dans %s", ns)
            r1 = await client.post(
                f"{_K8S_HOST}/apis/apps/v1/namespaces/{ns}/statefulsets",
                headers=headers, json=manifests["sts"],
            )
            if r1.status_code not in (200, 201):
                log.warning("bootstrap geoai-gpu: création SS échouée %s: %s",
                            r1.status_code, r1.text[:300])
                return
            # Services (idempotents : 409 si existe déjà = ok)
            for svc in (manifests["svc_headless"], manifests["svc_cluster"]):
                rs = await client.post(
                    f"{_K8S_HOST}/api/v1/namespaces/{ns}/services",
                    headers=headers, json=svc,
                )
                if rs.status_code not in (200, 201, 409):
                    log.warning("bootstrap geoai-gpu: création Service %s : %s",
                                svc["metadata"]["name"], rs.status_code)
        else:
            log.warning("bootstrap geoai-gpu: lecture SS http=%s, abandon", r.status_code)
            return

        # Étape 3 — Attente /health 200 (max ~30 min)
        log.info("bootstrap geoai-gpu: attente /health (pull image + GPU init, jusqu'à 30min)")
        deadline = asyncio.get_event_loop().time() + _GEOAI_BOOT_TIMEOUT
        health_ok = False
        while asyncio.get_event_loop().time() < deadline:
            await asyncio.sleep(15)
            try:
                hr = await client.get(f"{base_url}/health", timeout=5)
                if hr.status_code == 200:
                    health_ok = True
                    log.info("bootstrap geoai-gpu: /health 200 — pod opérationnel")
                    break
            except Exception:
                pass

        if not health_ok:
            log.warning(
                "bootstrap geoai-gpu: timeout après %ds (GPU SSPCloud indisponible ou "
                "pull image en cours). Retry au prochain redémarrage hub.",
                _GEOAI_BOOT_TIMEOUT,
            )
            return

        # Étape 4 — POST /warmup pour télécharger les modèles SAM/DeepForest
        # sur le PVC pendant que le GPU est encore alloué.
        # Si l'endpoint n'existe pas (image GPU ancienne sans /warmup), on
        # skip immédiatement : pas la peine de retry 15 min, et le hub reste
        # compatible avec d'anciennes images.
        log.info("bootstrap geoai-gpu: téléchargement modèles via /warmup")
        warmup_deadline = asyncio.get_event_loop().time() + 900  # 15 min
        warmup_ok = False
        warmup_not_supported = False
        while asyncio.get_event_loop().time() < warmup_deadline:
            try:
                wr = await client.post(f"{base_url}/warmup", timeout=900)
                if wr.status_code == 404:
                    log.info(
                        "bootstrap geoai-gpu: /warmup absent dans cette image "
                        "(404) — skip warmup, modèles seront chargés au 1er usage"
                    )
                    warmup_not_supported = True
                    break
                if wr.status_code == 200:
                    data = wr.json()
                    if data.get("all_loaded"):
                        warmup_ok = True
                        log.info(
                            "bootstrap geoai-gpu: warmup terminé, modèles=%s",
                            data.get("loaded"),
                        )
                        break
                    log.warning(
                        "bootstrap geoai-gpu: warmup partiel %s — retry",
                        data.get("results"),
                    )
                else:
                    log.warning(
                        "bootstrap geoai-gpu: warmup http=%s — retry",
                        wr.status_code,
                    )
            except Exception as exc:
                log.debug("bootstrap geoai-gpu: warmup attente: %s", exc)
            await asyncio.sleep(20)

        if not warmup_ok and not warmup_not_supported:
            log.warning(
                "bootstrap geoai-gpu: warmup non confirmé après timeout. "
                "L'install continue, le 1er usage user pourrait être lent."
            )

        # Étape 5 — Annoter bootstrap-done puis scale 0 (libère GPU)
        await client.patch(
            f"{_K8S_HOST}/apis/apps/v1/namespaces/{ns}/statefulsets/{_GEOAI_SS_NAME}",
            headers=patch_headers,
            json={"metadata": {"annotations": {_GEOAI_BOOT_ANNOT: "true"}}},
        )
        await client.patch(
            f"{_K8S_HOST}/apis/apps/v1/namespaces/{ns}/statefulsets/{_GEOAI_SS_NAME}",
            headers=patch_headers,
            json={"spec": {"replicas": 0}},
        )
        _GEOAI_GPU_SERVICE = _GEOAI_SVC_NAME
        log.info("bootstrap geoai-gpu: terminé (scale 0, GPU libéré, prêt à servir)")


# ── Lifespan ───────────────────────────────────────────────────────────────────

async def _migrate_studies_to_projects() -> None:
    """Sprint UX-3 (2026-06-21) : migration idempotente etudes -> 1:N projets.

    Pour chaque study sans entree dans `study_projects`, on cree 1 default
    project (is_default=1, label='Projet principal'). Le copy effectif du
    .qgz legacy (/data/studies/{sid}/project.qgz) vers le nouveau path
    (/data/studies/{sid}/projects/{pid}/project.qgz) est DEFER a la 1ere
    activation : le hub n'a pas d'acces direct au PVC, il faut passer par
    execute_python cote pod (et le pod peut etre endormi au boot).

    Cette fonction est idempotente : si study_projects contient deja une row
    pour la study, on skip. -> peut tourner a chaque boot.
    """
    if not _STUDIES_AVAILABLE:
        return
    try:
        import aiosqlite
        async with aiosqlite.connect(studies._DB_PATH) as db:
            db.row_factory = aiosqlite.Row
            cursor = await db.execute("""
                SELECT s.id, s.owner
                FROM studies s
                LEFT JOIN study_projects sp ON sp.sid = s.id
                WHERE sp.pid IS NULL
            """)
            rows = await cursor.fetchall()
            n_migrated = 0
            for row in rows:
                sid, owner = row["id"], row["owner"]
                # create_project insere la row + UNSET les autres is_default
                # (idempotent : aucun autre car la query ci-dessus garantit
                # pas de project deja existant pour ce sid).
                project = await studies.create_project(
                    sid=sid, owner=owner,
                    label="Projet principal", is_default=True,
                )
                n_migrated += 1
                log.info(
                    "Migration studies->projects : sid=%s -> pid=%s",
                    sid, project["pid"],
                )
            if n_migrated > 0:
                log.info(
                    "Migration studies_to_projects : %d study(ies) migr(ee)s "
                    "(filesystem copy defere a la 1ere activation)",
                    n_migrated,
                )
    except Exception as exc:
        log.warning(
            "Migration studies_to_projects KO (degraded mode) : %s", exc,
        )


@asynccontextmanager
async def lifespan(app: FastAPI):
    await sessions.init_db()
    await auth.init_apikeys_db()
    await auth._build_jwks_cache()
    if _STUDIES_AVAILABLE:
        await studies.init_db()
        # Sprint UX-3 (2026-06-21) : migration idempotente etude -> projet 1:N.
        # Pour chaque study existante sans entree dans study_projects, on cree
        # 1 default project. Le copy effectif du .qgz legacy est defere a la
        # premiere activation (laisse pour _activate_project_pod_side).
        # Si la DB n'est pas joignable ou l'op echoue, on log et on continue
        # (degraded). La migration est idempotente -> peut tourner a chaque
        # boot sans casser.
        await _migrate_studies_to_projects()
    # Restaurer le cache depuis la DB (évite doubles créations après redémarrage hub)
    for s in await sessions.list_sessions():
        if s["status"] == sessions.SESSION_READY:
            _active_sessions[s["owner"]] = s["id"]
    if _active_sessions:
        log.info("Cache restauré : %d session(s) actives", len(_active_sessions))

    # CRITIQUE (Bug #8 V1.1) : patcher l'ingress proxy-read-timeout=600 AVANT
    # de servir des requêtes. Si on le fait en background (asyncio.create_task),
    # les premières requêtes longues (run_recipe ~5min, smart_load BD TOPO ~2min)
    # tombent en 504 pendant que le patch tourne — observé E2E 2026-05-31 : la
    # recette `risque_inondation` perd 5/17 steps sur 504 timeout.
    # Cap dur à 20s pour ne pas bloquer le startup si l'API K8s est lente/down ;
    # en cas d'échec, on continue (degraded mode, observable dans les logs).
    try:
        await asyncio.wait_for(_patch_own_ingress_timeout(), timeout=20)
    except Exception as exc:
        log.warning(
            "Startup: patch ingress timeout/échec (%s) — hub démarre quand même "
            "mais les requêtes longues risquent 504 jusqu'au prochain restart.",
            exc,
        )

    task = asyncio.create_task(sessions.cleanup_loop())
    asyncio.create_task(_bootstrap_agent())
    asyncio.create_task(_bootstrap_geoai_gpu())
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

# Phase 0ter (RGPD) : middleware OIDC qui protege l'acces UI par check
# preferred_username == ONYXIA_USER. Sans cela, n'importe qui avec l'URL hub
# accede a l'espace user. Cf. hub/hub/auth.py:oidc_auth_middleware pour la
# logique de whitelist (healthchecks, inter-pod Bearer, kube-probe).
app.middleware("http")(auth.oidc_auth_middleware)

# Vague E2 Commit E1 (D-QGIS-010 2026-06-29) : editeur BlockNote standalone
# bundle Vite mount statiquement. Le bundle est build par CI Docker
# multi-stage (node:20-alpine -> hub/hub/static/blocknote-editor/).
# Endpoint GET /editor/{sid}/assembly/{aid} retourne index.html.
_BLOCKNOTE_STATIC_DIR = Path(__file__).parent / "static" / "blocknote-editor"

# Audit v1.7.2 P1 #3 : wrapper StaticFiles qui pose Cache-Control selon
# le nom de fichier. Les assets Vite ont un hash dans le nom (index-abc123.js)
# donc immuables -> public, max-age=1y, immutable. index.html change a chaque
# build sans hash -> no-cache pour invalider.
import re as _re_static
_VITE_HASHED_ASSET = _re_static.compile(r"-[a-zA-Z0-9_-]{8,}\.(js|css|woff2?|ttf|png|jpg|jpeg|gif|svg|webp)$")


class _BlockNoteStaticFiles(StaticFiles):
    """StaticFiles avec Cache-Control adapte au bundle Vite."""

    async def get_response(self, path: str, scope):
        response = await super().get_response(path, scope)
        try:
            if response.status_code == 200:
                if _VITE_HASHED_ASSET.search(path):
                    response.headers["Cache-Control"] = (
                        "public, max-age=31536000, immutable"
                    )
                else:
                    response.headers["Cache-Control"] = (
                        "no-cache, must-revalidate"
                    )
        except Exception:
            pass
        return response


if _BLOCKNOTE_STATIC_DIR.exists():
    app.mount(
        "/static/blocknote-editor",
        _BlockNoteStaticFiles(directory=str(_BLOCKNOTE_STATIC_DIR)),
        name="blocknote_editor_static",
    )
    log.info("BlockNote editor bundle mounted: %s", _BLOCKNOTE_STATIC_DIR)
else:
    log.warning(
        "BlockNote editor bundle ABSENT (%s). Build via 'npm run build' "
        "dans blocknote-editor/, ou via CI Docker multi-stage.",
        _BLOCKNOTE_STATIC_DIR,
    )


@app.get("/auth/whoami")
async def auth_whoami(request: Request):
    """Endpoint debug : retourne le user authentifie (apres middleware OIDC).
    Utile pour verifier que le cookie OIDC est bien recu et decode."""
    claims = getattr(request.state, "oidc_claims", None)
    if not claims:
        return {"authenticated": False, "reason": "no_oidc_claims_in_request"}
    return {
        "authenticated": True,
        "username": claims.get("preferred_username"),
        "email": claims.get("email"),
        "name": claims.get("name"),
        "exp": claims.get("exp"),
        "onyxia_user_owner": _ONYXIA_USER,
        "match": claims.get("preferred_username") == _ONYXIA_USER,
    }


# ── Phase 0ter Steps 4-5 (RGPD) : Reverse proxy noVNC same-origin ─────────────
# Avant ce fix, l'iframe workspace pointait directement sur l'ingress public
# `qgis-workspace-X-novnc.user.lab.sspcloud.fr` (sans middleware OIDC). Le
# reverse proxy via hub permet :
#   - Same-origin (cookie OIDC du hub partage via Domain=.user.lab.sspcloud.fr)
#   - Le middleware hub protege l'acces (owner check)
#   - Pas de modification cote BigQgisMCP (workspace reste accessible en interne
#     via service cluster, mais pas expose publiquement avec auth)
#
# Architecture :
#   Browser -> hub/workspace/vnc/{path}        (HTTP GET assets noVNC HTML/JS)
#   Browser -> hub/workspace/vnc/websockify    (WS bidirectionnel canal noVNC)
#                  ↓ middleware OIDC ✓
#                  ↓ reverse proxy
#   workspace internal qgis-workspace-X.user-X.svc.cluster.local:6080
#
# Workspace endormi (scale 0) : upstream call echoue -> retourne 503 avec
# message clair. Le bouton "Reveiller le bureau" du desk.html reste utilisable.

def _workspace_internal_host() -> str:
    """DNS interne du workspace pour reverse proxy (skip ingress public)."""
    user = _ONYXIA_USER
    ns = sessions._NAMESPACE or (f"user-{user}" if user else "default")
    return f"qgis-workspace-{user}.{ns}.svc.cluster.local"


@app.api_route(
    "/workspace/vnc/{path:path}",
    methods=["GET", "POST", "PUT", "DELETE", "OPTIONS", "HEAD"],
)
async def proxy_workspace_vnc_http(path: str, request: Request):
    """Reverse proxy HTTP pour les assets statiques noVNC (vnc_lite.html, JS, CSS).
    Middleware OIDC s'applique deja (owner check via cookie)."""
    upstream_host = _workspace_internal_host()
    upstream_url = f"http://{upstream_host}:6080/{path}"
    qs = str(request.query_params)
    if qs:
        upstream_url += f"?{qs}"
    # Strip headers qui ne doivent pas etre forwardes
    fwd_headers = {
        k: v for k, v in request.headers.items()
        if k.lower() not in ("host", "cookie", "authorization", "content-length")
    }
    try:
        async with httpx.AsyncClient(timeout=30.0, follow_redirects=False) as client:
            proxied = await client.request(
                method=request.method,
                url=upstream_url,
                content=await request.body(),
                headers=fwd_headers,
            )
        # Strip headers de reponse qui peuvent casser le tunnel
        resp_headers = {
            k: v for k, v in proxied.headers.items()
            if k.lower() not in ("content-encoding", "transfer-encoding", "connection")
        }
        return Response(
            content=proxied.content,
            status_code=proxied.status_code,
            headers=resp_headers,
            media_type=proxied.headers.get("content-type"),
        )
    except httpx.ConnectError:
        # Workspace endormi (scale 0) ou pas encore Ready
        return JSONResponse(
            {"detail": "Workspace endormi. Reveille-le depuis le desk."},
            status_code=503,
        )
    except Exception as exc:
        log.warning("proxy_workspace_vnc_http error: %s", exc)
        return JSONResponse({"detail": f"Proxy error: {exc}"}, status_code=502)


@app.websocket("/workspace/vnc/websockify")
async def proxy_workspace_vnc_ws(client_ws: WebSocket):
    """Reverse proxy WebSocket pour le canal noVNC (binary frames bidirectionnel).
    Le middleware HTTP middleware ne s'applique PAS aux upgrades WS (FastAPI bug).
    On verifie manuellement le cookie OIDC avant accept.
    """
    # Auth manuelle pour WebSocket (middleware HTTP ne s'applique pas)
    token = client_ws.cookies.get("oidc_token") or ""
    if not token:
        await client_ws.close(code=4401, reason="No OIDC cookie")
        return
    try:
        from jwt import decode as _jwt_decode
        signing_key = auth._jwks.get_signing_key_from_jwt(token)
        claims = _jwt_decode(
            token, signing_key.key, algorithms=["RS256"],
            options={"verify_aud": False},
        )
        if claims.get("preferred_username") != _ONYXIA_USER:
            await client_ws.close(code=4403, reason=f"Owner mismatch (expected {_ONYXIA_USER})")
            return
    except Exception as exc:
        await client_ws.close(code=4401, reason=f"Token invalide: {exc}")
        return

    # Tunnel WS bidirectionnel : client_ws <-> upstream noVNC websockify
    # Subprotocol "binary" : noVNC client moderne ne le demande PAS (la version
    # actuelle vnc_lite.html envoie pas de Sec-WebSocket-Protocol). Si on
    # declarait "binary" unilateralement -> handshake fail RFC 6455. On
    # forwarde uniquement si le client le demande, sinon on accepte sans.
    requested = list(client_ws.scope.get("subprotocols") or [])
    accept_protocol = "binary" if "binary" in requested else None
    await client_ws.accept(subprotocol=accept_protocol)
    upstream_host = _workspace_internal_host()
    upstream_url = f"ws://{upstream_host}:6080/websockify"
    import websockets, asyncio as _asyncio
    try:
        connect_kwargs = {}
        if accept_protocol:
            connect_kwargs["subprotocols"] = [accept_protocol]
        async with websockets.connect(upstream_url, **connect_kwargs) as upstream:
            async def client_to_upstream():
                try:
                    while True:
                        msg = await client_ws.receive_bytes()
                        await upstream.send(msg)
                except WebSocketDisconnect:
                    pass
                except Exception as e:
                    log.debug("ws c2u: %s", e)

            async def upstream_to_client():
                try:
                    async for msg in upstream:
                        if isinstance(msg, (bytes, bytearray)):
                            await client_ws.send_bytes(msg)
                        else:
                            await client_ws.send_text(msg)
                except Exception as e:
                    log.debug("ws u2c: %s", e)

            await _asyncio.gather(
                client_to_upstream(), upstream_to_client(),
                return_exceptions=True,
            )
    except Exception as exc:
        log.warning("proxy_workspace_vnc_ws upstream connect failed: %s", exc)
        try:
            await client_ws.close(code=1011, reason=f"Upstream KO: {exc}")
        except Exception:
            pass


def _resolve_onyxia_user() -> str:
    """Résout le username depuis ONYXIA_USER env ou namespace K8s (fallback).
    Onyxia n'injecte pas toujours ONYXIA_USER selon la version du chart."""
    user = os.getenv("ONYXIA_USER", "")
    if not user:
        try:
            ns = Path("/var/run/secrets/kubernetes.io/serviceaccount/namespace").read_text().strip()
            user = ns.removeprefix("user-")
        except Exception:
            pass
    return user

_ONYXIA_USER = _resolve_onyxia_user()
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

# Sprint Composants Phase 2+3 (2026-06-25) : Environment Jinja2 dédié au
# renderer maplibre (templates standalone HTML). Permet d'utiliser
# .get_template().render() en bypass de _jinja.TemplateResponse qui exige
# un Request object FastAPI (incompatible avec le pattern render hub-side).
import jinja2 as _jinja2_lib
_HUB_HUB_DIR = Path(__file__).parent
_maplibre_jinja = _jinja2_lib.Environment(
    loader=_jinja2_lib.FileSystemLoader([
        str(_HUB_HUB_DIR / "maplibre_renderer"),
        str(_HUB_HUB_DIR),
    ]),
    autoescape=False,  # composants/assemblages gèrent leur propre escape via |e
) if _HUB_HUB_DIR.is_dir() else None

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
        # Sprint UX-3 Commit 3 (2026-06-21) : projet actif + liste des projets
        # de l'etude active (pour dropdown switch dans desk header).
        "active_project": None, "projects_in_active_study": [],
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
            # Sprint UX-3 Commit 3 (2026-06-21) : projet actif + projets de
            # l'etude active (alimente le menu deroulant header desk).
            if ctx.get("active_study_id"):
                try:
                    r = await c.get("/projects/active", headers=headers)
                    if r.status_code == 200:
                        ctx["active_project"] = r.json()
                    r2 = await c.get(
                        f"/studies/{ctx['active_study_id']}/projects",
                        headers=headers,
                    )
                    if r2.status_code == 200:
                        ctx["projects_in_active_study"] = r2.json()
                except Exception as exc:
                    log.debug("Fetch projects desk_context : %s", exc)

            if ctx.get("active_study_id"):
                try:
                    r = await c.get(f"/catalog/{_ONYXIA_USER}", headers=headers)
                    if r.status_code == 200:
                        all_items = r.json().get("items", [])
                        # Enrichir avec hub_url + size_kb (cf. /desk/catalog).
                        # Sans ca, le template Jinja {{item.hub_url}} resout
                        # vide et le href tombe sur la page courante (/desk).
                        for it in all_items:
                            kind = it.get("kind", "")
                            slug = it.get("slug", "")
                            if kind and slug and not it.get("hub_url"):
                                it["hub_url"] = (
                                    f"{_HUB_URL}/published/{_ONYXIA_USER}/{kind}/{slug}"
                                )
                            sz = it.get("size")
                            if sz and not it.get("size_kb"):
                                it["size_kb"] = max(1, round(sz / 1024))
                        items = [i for i in all_items
                                 if i.get("study_id") == ctx["active_study_id"]][:30]
                        ctx.update(catalog_items=items,
                                   catalog_count=len(items),
                                   catalog_total=len(all_items))
                except Exception:
                    pass
            else:
                # Pas d'étude active : on remonte le catalogue global pour que
                # le footer du desk affiche au moins le total et que le drawer
                # propose les publications non rattachées à une étude.
                try:
                    r = await c.get(f"/catalog/{_ONYXIA_USER}", headers=headers)
                    if r.status_code == 200:
                        all_items = r.json().get("items", [])
                        ctx.update(catalog_total=len(all_items))
                except Exception:
                    pass
    except Exception:
        pass

    # Derniers traitements significatifs (footer du desk) : best-effort, ne
    # crée pas de session, n'éveille pas le workspace. Si le pod est endormi
    # ou indisponible, on garde la liste vide (template affiche "Aucun
    # traitement récent"). On lit l'étude active si dispo, sinon le log global.
    if ctx.get("session_ready") and _AUDIT_AVAILABLE:
        try:
            sid = ctx.get("active_study_id")
            log_path = (f"/data/studies/{sid}/treatments.jsonl"
                        if sid else "/data/agent/treatments.jsonl")
            code = (
                "import json, os\n"
                "from pathlib import Path\n"
                f"p = Path({log_path!r})\n"
                "events = []\n"
                "if p.exists():\n"
                "    with open(p, 'r', encoding='utf-8') as f:\n"
                "        for line in f:\n"
                "            line = line.strip()\n"
                "            if not line: continue\n"
                "            try: events.append(json.loads(line))\n"
                "            except Exception: pass\n"
                "events = [e for e in events if e.get('ok') is not False "
                "and e.get('kind') in ('processing','export','python')]\n"
                "events = events[-8:]\n"
                "print('<<<TREATMENTS>>>' + json.dumps(events) + '<<<END>>>')\n"
            )
            stdout = await _execute_python_in_workspace(
                _ONYXIA_USER, code, timeout=3,
            )
            start = stdout.find("<<<TREATMENTS>>>")
            end = stdout.find("<<<END>>>")
            if start >= 0 and end > start:
                import json as _json
                events = _json.loads(stdout[start + len("<<<TREATMENTS>>>"):end])
                labels: list[str] = []
                for e in events:
                    label = (e.get("summary") or e.get("tool")
                             or e.get("kind") or "").strip()
                    if not label:
                        continue
                    if len(label) > 40:
                        label = label[:37] + "…"
                    labels.append(label)
                # Dédupliquer en conservant l'ordre (les plus récents en dernier)
                seen: set[str] = set()
                dedup: list[str] = []
                for lbl in reversed(labels):
                    if lbl in seen:
                        continue
                    seen.add(lbl)
                    dedup.append(lbl)
                ctx["recent_treatments"] = dedup[:6]
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
        "registration_endpoint":             f"{base}/oauth/register",
        "grant_types_supported":             ["authorization_code", "client_credentials"],
        "response_types_supported":          ["code"],
        "code_challenge_methods_supported":  ["S256"],
        "token_endpoint_auth_methods_supported": ["client_secret_post", "none"],
    })


@app.post("/oauth/register")
async def oauth_register(request: Request):
    """Dynamic Client Registration (RFC 7591) — exige par le connecteur MCP
    distant de claude.ai / Claude Desktop. Sans ce endpoint, claude.ai echoue
    avec `registration_endpoint_missing`.

    Enregistrement permissif : l'authentification reelle repose sur PKCE +
    l'api_key hub (cf. /oauth/token), pas sur un client_secret enregistre.
    On accepte donc toute demande et on renvoie un client_id genere. Les
    redirect_uris sont echoes tels quels (claude.ai verifie la coherence)."""
    try:
        body = await request.json()
    except Exception:
        body = {}
    client_id = f"mcp-{secrets.token_hex(8)}"
    return JSONResponse({
        "client_id":                  client_id,
        "client_id_issued_at":        int(time.time()),
        "redirect_uris":              body.get("redirect_uris", []),
        "token_endpoint_auth_method": "none",
        "grant_types":                ["authorization_code"],
        "response_types":             ["code"],
        "client_name":                body.get("client_name", "Claude MCP"),
    }, status_code=201)


@app.get("/.well-known/oauth-protected-resource")
async def oauth_protected_resource():
    """Resource metadata MCP (spec 2025-06-18) — pointe claude.ai vers le
    serveur d'autorisation. Requise par le connecteur distant pour relier
    la ressource (/mcp) a son authorization server."""
    base = _HUB_URL or ""
    return JSONResponse({
        "resource":                 f"{base}/mcp",
        "authorization_servers":    [base],
        "bearer_methods_supported": ["header"],
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
#
# La route GET "/" est définie plus bas (ligne 1424, `hub_home`) avec
# RedirectResponse intelligent vers /desk ou /workspace selon l'état de l'user.
# Le readiness probe K8s utilise `/health` (cf. charts/qgis-hub/templates/
# statefulset.yaml:86,92) — pas besoin d'une route "/" JSON dédiée qui
# masquerait `hub_home` (FastAPI prend la 1ère déclaration).


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


@app.get("/internal/profiles/{profile_id}/full")
async def get_profile_full_internal(
    profile_id: str,
    user: dict = Depends(auth.get_current_user),
):
    """Sprint Composants Phase 3c (2026-06-27) : retourne le profil COMPLET
    incluant agent_system_prompt.

    Réservé inter-pod (Bearer HUB_API_KEY) — utilisé par l'agent pour le
    meta-agent recipe_analyzer (_call_llm_analyzer). Le middleware OIDC
    inter-pod whitelist /internal/* prefix.

    Sécurité : whitelist seulement les profils internes (recipe_analyzer,
    component_analyzer V2, ...) pour limiter l'exposition.
    """
    if not _PROFILES_AVAILABLE:
        raise HTTPException(404, "Système de profils non disponible")
    # Whitelist profils internes exposables (V2 : étendre liste si nouveau meta-agent)
    _INTERNAL_PROFILES = {"recipe_analyzer", "agent_config_analyzer"}
    if profile_id not in _INTERNAL_PROFILES:
        raise HTTPException(
            403,
            f"Profil '{profile_id}' non exposable en /internal "
            f"(whitelist : {sorted(_INTERNAL_PROFILES)})",
        )
    p = profile_manager.get_profile(profile_id)
    if p.get("id") != profile_id:
        raise HTTPException(404, f"Profil '{profile_id}' introuvable")
    return p  # Tout y compris agent_system_prompt


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


@app.get("/geoai/status")
async def geoai_status(request: Request):
    """État du pod GPU GeoAI pour l'UI agent (chip footer, polling court).

    Pas d'auth (info publique côté user pour son propre namespace).
    """
    out: dict = {
        "ss_exists":       False,
        "bootstrap_done":  False,
        "replicas":        None,
        "pod_phase":       None,
        "health":          None,
        "state":           "not_installed",
    }

    token_file = Path("/var/run/secrets/kubernetes.io/serviceaccount/token")
    ns_file    = Path("/var/run/secrets/kubernetes.io/serviceaccount/namespace")
    if not token_file.exists():
        out["state"] = "not_in_k8s"
        return JSONResponse(out)

    token = token_file.read_text().strip()
    ns    = ns_file.read_text().strip()
    headers = {"Authorization": f"Bearer {token}"}
    base_url = f"http://{_GEOAI_SVC_NAME}.{ns}.svc.cluster.local:8000"

    async with httpx.AsyncClient(verify=False, timeout=8) as client:
        r = await client.get(
            f"{_K8S_HOST}/apis/apps/v1/namespaces/{ns}/statefulsets/{_GEOAI_SS_NAME}",
            headers=headers,
        )
        if r.status_code != 200:
            return JSONResponse(out)
        out["ss_exists"] = True
        ss = r.json()
        out["replicas"] = ss.get("spec", {}).get("replicas")
        ann = ss.get("metadata", {}).get("annotations", {}) or {}
        out["bootstrap_done"] = ann.get(_GEOAI_BOOT_ANNOT) == "true"

        # Pod state
        pr = await client.get(
            f"{_K8S_HOST}/api/v1/namespaces/{ns}/pods/{_GEOAI_SS_NAME}-0",
            headers=headers,
        )
        if pr.status_code == 200:
            pod = pr.json()
            out["pod_phase"] = pod.get("status", {}).get("phase")
            cs = pod.get("status", {}).get("containerStatuses", []) or []
            if cs:
                state = cs[0].get("state", {})
                waiting = state.get("waiting") or {}
                if waiting:
                    out["waiting_reason"] = waiting.get("reason")
        else:
            out["pod_phase"] = "absent"

        # Health probe (si pod possiblement up)
        if out["replicas"] and out["pod_phase"] == "Running":
            try:
                hr = await client.get(f"{base_url}/health", timeout=5)
                if hr.status_code == 200:
                    out["health"] = hr.json()
            except Exception:
                pass

    # Dérivation de l'état UI-friendly
    replicas = out["replicas"] or 0
    if out["bootstrap_done"] and replicas == 0:
        out["state"] = "ready"
    elif replicas >= 1 and out.get("health"):
        out["state"] = "active"
    elif replicas >= 1 and out["pod_phase"] == "Pending":
        out["state"] = "queued"
    elif replicas >= 1:
        out["state"] = "preparing"
    else:
        out["state"] = "installed"

    return JSONResponse(out)


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
    """Page d'accueil — readiness probe K8s + redirect intelligent humain.

    Le chart Onyxia jupyter-python hardcode son readinessProbe sur `GET /`
    avec timeoutSeconds=2. Kubelet suit les redirects 3xx, donc rediriger
    vers /workspace (lourd : DB queries + httpx K8s API) faisait
    occasionnellement timeout le probe -> pod marque Ready=False sans
    raison reelle (le hub continue de servir). Court-circuit pour
    User-Agent "kube-probe/*" : 200 OK statique immediat. Les humains
    gardent leur redirect intelligent vers /desk ou /workspace.
    """
    if "kube-probe" in request.headers.get("user-agent", "").lower():
        return Response(content="ok", media_type="text/plain", status_code=200)
    # UX wish 2026-06-26 (user feedback Sprint Composants) :
    # toujours rediriger vers /workspace (vue d'ensemble des etudes +
    # accueil) plutot que /desk (vue bureau d'une etude active). Permet
    # a l'user de choisir activement son etude au lieu d'etre projete
    # dans la derniere active. /workspace gere lui-meme l'auto-wake
    # workspace QGIS (cf. desk_page + nouveau auto-wake).
    return RedirectResponse("/workspace", status_code=302)
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
    # Sprint UX-3 Commit 2 (2026-06-21) : chained create default project.
    # Toute nouvelle etude commence avec 1 projet 'principal' (is_default=1).
    # L'user peut creer des projets supplementaires via POST /studies/{sid}/projects
    # apres coup. Le code pod-side (create dossier + .qgz) est best-effort :
    # log warning + continue si le pod workspace est endormi.
    try:
        default_project = await studies.create_project(
            sid=s["id"], owner=user["username"],
            label="Projet principal", is_default=True,
        )
        await _execute_python_in_workspace(
            user["username"],
            studies.create_project_pod_code(
                s["id"], default_project["pid"],
                default_project["label"],
                copy_from=None,  # nouvelle etude : pas de legacy a copier
            ),
        )
        # Decore la response avec le default project (UI peut l'afficher direct)
        s["default_project"] = default_project
    except Exception as exc:
        log.warning("Init default project pour etude %s : %s", s["id"], exc)
    return s


@app.get("/studies")
async def list_studies_endpoint(
    archived: bool = False,
    user: dict = Depends(auth.get_current_user),
):
    """Liste les études de l'user.

    archived=true : inclure les études archivées.
    Sprint UX-3 (2026-06-21) : enrichit chaque study avec :
    - project_count : nombre de projets actifs dans l'etude
    - default_project : projet 'principal' (label + pid) pour UI workspace
      (badge 'Projet : X' sur la card etude).
    """
    if not _STUDIES_AVAILABLE:
        return []
    rows = await studies.list_studies(
        user["username"], include_archived=archived,
    )
    # Enrichissement compteurs (best-effort, ne casse pas le payload base si DB
    # study_projects pas encore migree).
    try:
        for s in rows:
            s["project_count"] = await studies.count_projects(s["id"])
            default = await studies.get_default_project(s["id"])
            if default:
                s["default_project"] = {
                    "pid":   default["pid"],
                    "label": default["label"],
                }
    except Exception as exc:
        log.warning("Enrichissement project_count /studies KO : %s", exc)
    return rows


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

    # Sprint UX-3 Commit 2 (2026-06-21) : chained activate du projet default.
    # Comportement attendu user 'Bureau de travail s'ouvre toujours dans le
    # cadre d'une etude + dernier projet chronologique ouvert par defaut'.
    # get_default_project ORDER BY is_default DESC, last_active DESC -> donne
    # exactement ca : projet 'principal' sinon le plus recemment actif.
    # Best-effort : si pas de projet (ancienne etude non-migree, race condition),
    # on saute la cascade sans casser l'activation etude.
    project_active = None
    try:
        default_p = await studies.get_default_project(sid)
        if default_p is None:
            # Etude sans projet : creer 1 default automatiquement (filet de
            # securite pour studies anciennes non-migrees ou bug d'init).
            default_p = await studies.create_project(
                sid=sid, owner=user["username"],
                label="Projet principal", is_default=True,
            )
            log.info(
                "activate_study : etude %s sans projet -> default cree pid=%s",
                sid, default_p["pid"],
            )
        await studies.set_active_project(user["username"], default_p["pid"])
        await studies.touch_project(default_p["pid"])
        try:
            await _execute_python_in_workspace(
                user["username"],
                studies.activate_project_pod_code(sid, default_p["pid"]),
            )
        except Exception as exc:
            log.warning(
                "Activation pod-side projet %s : %s", default_p["pid"], exc,
            )
        project_active = default_p
    except Exception as exc:
        log.warning("Chained activate projet pour etude %s : %s", sid, exc)

    return {
        "active_study":   sid,
        "study":          s,
        "active_project": project_active,
    }


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


# ── Sprint UX-3 Commit 2 : endpoints projects (1 etude -> N projets) ──────────
# Pattern strictement parallele aux endpoints studies. Pre-requis : etude
# accessible owner-checked via studies.get_study(sid, user["username"]).

@app.get("/studies/{sid}/projects")
async def list_projects_endpoint(
    sid: str,
    archived: bool = False,
    user: dict = Depends(auth.get_current_user),
):
    """Liste les projets d'une etude. Tri is_default DESC, last_active DESC
    (le projet principal en premier, puis les autres par recence)."""
    if not _STUDIES_AVAILABLE:
        raise HTTPException(503, "Module studies indisponible")
    s = await studies.get_study(sid, user["username"])
    if not s:
        raise HTTPException(404, "Étude introuvable")
    return await studies.list_projects(sid, include_archived=archived)


@app.post("/studies/{sid}/projects", status_code=status.HTTP_201_CREATED)
async def create_project_endpoint(
    sid: str,
    request: Request,
    user: dict = Depends(auth.get_current_user),
):
    """Cree un nouveau projet dans l'etude. Body : {label, is_default?}.

    is_default=True : UNSET les autres is_default de l'etude (1 unique).
    Cote pod : cree le dossier + project.qgz vide (placeholder) + history.jsonl.
    """
    if not _STUDIES_AVAILABLE:
        raise HTTPException(503, "Module studies indisponible")
    s = await studies.get_study(sid, user["username"])
    if not s:
        raise HTTPException(404, "Étude introuvable")
    try:
        body = await request.json()
    except Exception:
        body = {}
    label = body.get("label", "").strip() or "Nouveau projet"
    is_default = bool(body.get("is_default", False))

    project = await studies.create_project(
        sid=sid, owner=user["username"],
        label=label, is_default=is_default,
    )
    try:
        await _execute_python_in_workspace(
            user["username"],
            studies.create_project_pod_code(
                sid, project["pid"], project["label"], copy_from=None,
            ),
        )
    except Exception as exc:
        log.warning(
            "create_project pod-side pour pid=%s : %s", project["pid"], exc,
        )
    return project


@app.get("/studies/{sid}/projects/{pid}")
async def get_project_endpoint(
    sid: str, pid: str,
    user: dict = Depends(auth.get_current_user),
):
    if not _STUDIES_AVAILABLE:
        raise HTTPException(503, "Module studies indisponible")
    s = await studies.get_study(sid, user["username"])
    if not s:
        raise HTTPException(404, "Étude introuvable")
    p = await studies.get_project(pid, user["username"])
    if not p or p["sid"] != sid:
        raise HTTPException(404, "Projet introuvable dans cette étude")
    return p


@app.patch("/studies/{sid}/projects/{pid}")
async def patch_project_endpoint(
    sid: str, pid: str,
    request: Request,
    user: dict = Depends(auth.get_current_user),
):
    """Update partiel. Champs autorises : label, is_default, status."""
    if not _STUDIES_AVAILABLE:
        raise HTTPException(503, "Module studies indisponible")
    s = await studies.get_study(sid, user["username"])
    if not s:
        raise HTTPException(404, "Étude introuvable")
    p = await studies.get_project(pid, user["username"])
    if not p or p["sid"] != sid:
        raise HTTPException(404, "Projet introuvable dans cette étude")
    try:
        body = await request.json()
    except Exception:
        body = {}
    return await studies.update_project(pid, **body)


@app.delete("/studies/{sid}/projects/{pid}")
async def archive_project_endpoint(
    sid: str, pid: str,
    user: dict = Depends(auth.get_current_user),
):
    """Archive (soft delete). Le projet disparait de la liste par defaut mais
    les fichiers restent sur PVC (audit / restauration manuelle possible)."""
    if not _STUDIES_AVAILABLE:
        raise HTTPException(503, "Module studies indisponible")
    s = await studies.get_study(sid, user["username"])
    if not s:
        raise HTTPException(404, "Étude introuvable")
    p = await studies.get_project(pid, user["username"])
    if not p or p["sid"] != sid:
        raise HTTPException(404, "Projet introuvable dans cette étude")
    # Securite : on refuse l'archivage du projet default s'il y a d'autres projets
    # actifs (sinon l'etude n'a plus de default). L'user doit set is_default sur
    # un autre projet avant d'archiver l'ancien default.
    if p["is_default"]:
        others = [pp for pp in await studies.list_projects(sid) if pp["pid"] != pid]
        if others:
            raise HTTPException(
                409,
                "Impossible d'archiver le projet principal sans en désigner un "
                "autre comme principal au préalable (PATCH is_default=1).",
            )
    await studies.archive_project(pid)
    return {"archived": True, "pid": pid}


@app.post("/studies/{sid}/projects/{pid}/activate")
async def activate_project_endpoint(
    sid: str, pid: str,
    user: dict = Depends(auth.get_current_user),
):
    """Active un projet specifique d'une etude. Switch QGIS proj cote pod via
    activate_project_pod_code (sentinel + symlink + proj.read).

    Pre-requis : l'etude doit etre l'etude active (sinon on l'active aussi).
    Effet de bord : touch_project pour mettre a jour last_active (utile pour
    'dernier projet ouvert chronologiquement').
    """
    if not _STUDIES_AVAILABLE:
        raise HTTPException(503, "Module studies indisponible")
    s = await studies.get_study(sid, user["username"])
    if not s:
        raise HTTPException(404, "Étude introuvable")
    p = await studies.get_project(pid, user["username"])
    if not p or p["sid"] != sid:
        raise HTTPException(404, "Projet introuvable dans cette étude")
    if p["status"] != "active":
        raise HTTPException(400, f"Projet archivé (status={p['status']})")

    # Si l'etude n'est pas active, l'activer en cascade (mais SANS re-trigger
    # le chained activate de son default project -> on prefere notre pid choisi)
    prev_sid = await studies.get_active_study_id(user["username"])
    if prev_sid != sid:
        # Save de l'etude sortante avant le switch (idempotent au pattern
        # activate_study).
        if prev_sid:
            try:
                await _execute_python_in_workspace(
                    user["username"], studies.save_active_pod_code(prev_sid),
                )
            except Exception as exc:
                log.warning("Save etude sortante %s : %s", prev_sid, exc)
        await studies.set_active_study(user["username"], sid)
        await studies.touch_study(sid)
        try:
            await _execute_python_in_workspace(
                user["username"], studies.activate_pod_code(sid),
            )
        except Exception as exc:
            log.warning("Activation sentinel etude %s : %s", sid, exc)

    await studies.set_active_project(user["username"], pid)
    await studies.touch_project(pid)
    try:
        await _execute_python_in_workspace(
            user["username"], studies.activate_project_pod_code(sid, pid),
        )
    except Exception as exc:
        log.warning("Activation pod-side projet %s : %s", pid, exc)
    return {"active_project": pid, "project": p, "active_study": sid}


@app.get("/projects/active")
async def get_active_project_endpoint(
    user: dict = Depends(auth.get_current_user),
):
    """Projet actif courant (depuis DB hub). Renvoie None si aucun projet
    n'a ete active (cas user vierge avant 1ere activation)."""
    if not _STUDIES_AVAILABLE:
        raise HTTPException(503, "Module studies indisponible")
    active_pid = await studies.get_active_project_id(user["username"])
    if not active_pid:
        return None
    p = await studies.get_project(active_pid, user["username"])
    if not p or p["status"] != "active":
        return None
    return p


# ── Sprint Composants-1 (2026-06-24) : endpoints Scene Manifest V0.2 ──────────
# Pattern parallele aux endpoints recipes V1.5 : GET (read latest) + POST
# (rebuild from QGIS layers) + PUT (overwrite with new content + validation
# Pydantic + scene_hash). DELETE = soft archive (status='archived' en DB,
# fichier PVC conserve pour audit).

@app.get("/studies/{sid}/projects/{pid}/scene_manifest")
async def get_scene_manifest_endpoint(
    sid: str, pid: str,
    user: dict = Depends(auth.get_current_user),
):
    """Lit le Scene Manifest courant pour ce projet. Retourne :
    - 200 + JSON manifest si existe
    - 200 + null si pas encore initialise (cote PVC)
    - 404 si etude/projet introuvable
    """
    if not _STUDIES_AVAILABLE:
        raise HTTPException(503, "Module studies indisponible")
    s = await studies.get_study(sid, user["username"])
    if not s:
        raise HTTPException(404, "Étude introuvable")
    p = await studies.get_project(pid, user["username"])
    if not p or p["sid"] != sid:
        raise HTTPException(404, "Projet introuvable dans cette étude")

    # Recupere le fichier sur PVC via execute_python (pas d'acces direct
    # hub -> PVC). Le pod doit etre UP (sinon erreur silencieuse, retourne
    # latest DB en fallback ou null).
    try:
        stdout = await _execute_python_in_workspace(
            user["username"], studies.read_scene_manifest_pod_code(sid, pid),
        )
    except Exception as exc:
        log.warning("scene_manifest read pod-side : %s", exc)
        stdout = ""

    import base64
    if "SCENE_MANIFEST_READ_OK" in stdout:
        # Format marker stdout : 'SCENE_MANIFEST_READ_OK b64=<base64>'
        try:
            b64 = stdout.split("b64=", 1)[1].split()[0].strip()
            content = base64.b64decode(b64).decode("utf-8")
            import json as _json
            data = _json.loads(content)
            latest = await studies.scene_manifest_get_latest(pid)
            return {
                "manifest": data,
                "version_num": latest.get("version_num") if latest else None,
                "scene_hash": latest.get("scene_hash") if latest else None,
                "exists": True,
            }
        except Exception as exc:
            log.warning("scene_manifest parse : %s", exc)
            return {"manifest": None, "exists": False, "error": str(exc)}
    # Pas trouve sur PVC
    return {"manifest": None, "exists": False}


@app.post("/studies/{sid}/projects/{pid}/scene_manifest/build")
async def build_scene_manifest_endpoint(
    sid: str, pid: str,
    user: dict = Depends(auth.get_current_user),
):
    """Genere un Scene Manifest initial depuis les couches QGIS du projet
    courant. Sprint C-1 : mapping basique kind=single + color default.
    Sprint C-2 ajoutera l'editeur pour personnaliser kind/field/stops.

    Output : retourne le manifest genere + index en DB (scene_hash via
    Pydantic canonicalisation).
    """
    if not _STUDIES_AVAILABLE:
        raise HTTPException(503, "Module studies indisponible")
    s = await studies.get_study(sid, user["username"])
    if not s:
        raise HTTPException(404, "Étude introuvable")
    p = await studies.get_project(pid, user["username"])
    if not p or p["sid"] != sid:
        raise HTTPException(404, "Projet introuvable dans cette étude")

    try:
        stdout = await _execute_python_in_workspace(
            user["username"],
            studies.build_scene_manifest_from_qgis_pod_code(sid, pid),
            timeout=45,
        )
    except Exception as exc:
        log.warning("scene_manifest build pod-side : %s", exc)
        raise HTTPException(503, f"Workspace indisponible : {exc}")

    # Parse marker stdout : '<<<JSON_MANIFEST>>>{...}<<<END>>>'
    start = stdout.find("<<<JSON_MANIFEST>>>")
    end = stdout.find("<<<END>>>")
    if start < 0 or end < 0:
        raise HTTPException(500, f"Erreur build scene_manifest : {stdout[:300]}")
    import json as _json
    try:
        manifest_json = stdout[start + len("<<<JSON_MANIFEST>>>"):end]
        manifest_data = _json.loads(manifest_json)
    except Exception as exc:
        raise HTTPException(500, f"Parse manifest invalide : {exc}")

    # Validation Pydantic + scene_hash via vendored SceneManifest.
    # Si le module impose des champs non remplis par notre build initial,
    # on log mais on n'echoue pas (Sprint C-1 = mapping minimal).
    scene_hash = ""
    n_layers = len(manifest_data.get("layers", []))
    try:
        from hub.vendor import scene_manifest as _sm
        # Sprint C-2 fera la validation stricte. Ici on calcule juste le
        # scene_hash via la fonction canonicalisation si disponible.
        if hasattr(_sm, "compute_scene_hash"):
            scene_hash = _sm.compute_scene_hash(manifest_data)
        else:
            # Fallback : hash de la serialization canonique
            import hashlib
            canonical = _json.dumps(manifest_data, sort_keys=True, ensure_ascii=False)
            scene_hash = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
    except Exception as exc:
        log.warning("scene_hash compute : %s (fallback hashlib)", exc)
        import hashlib
        canonical = _json.dumps(manifest_data, sort_keys=True, ensure_ascii=False)
        scene_hash = hashlib.sha256(canonical.encode("utf-8")).hexdigest()

    size_bytes = len(manifest_json.encode("utf-8"))
    previous = await studies.scene_manifest_get_latest(pid)
    previous_hash = previous.get("scene_hash") if previous else ""

    await studies.scene_manifest_insert(
        pid=pid, sid=sid, owner=user["username"],
        scene_hash=scene_hash, n_layers=n_layers, size_bytes=size_bytes,
        previous_hash=previous_hash,
    )
    latest = await studies.scene_manifest_get_latest(pid)
    return {
        "manifest": manifest_data,
        "scene_hash": scene_hash,
        "version_num": latest.get("version_num") if latest else 1,
        "n_layers": n_layers,
        "size_bytes": size_bytes,
    }


@app.put("/studies/{sid}/projects/{pid}/scene_manifest")
async def put_scene_manifest_endpoint(
    sid: str, pid: str,
    request: Request,
    user: dict = Depends(auth.get_current_user),
):
    """Overwrite le Scene Manifest avec un nouveau contenu. Le client envoie
    le JSON complet, le hub valide (Sprint C-2 : Pydantic strict) puis ecrit
    sur PVC + indexe nouvelle version en DB."""
    if not _STUDIES_AVAILABLE:
        raise HTTPException(503, "Module studies indisponible")
    s = await studies.get_study(sid, user["username"])
    if not s:
        raise HTTPException(404, "Étude introuvable")
    p = await studies.get_project(pid, user["username"])
    if not p or p["sid"] != sid:
        raise HTTPException(404, "Projet introuvable dans cette étude")

    try:
        manifest_data = await request.json()
    except Exception:
        raise HTTPException(400, "Body JSON invalide")

    # Sprint C-2 fera la validation Pydantic stricte avec SceneManifest
    # depuis vendor. Ici on serialise tel-quel + scene_hash.
    import json as _json
    canonical = _json.dumps(manifest_data, sort_keys=True, ensure_ascii=False)
    try:
        from hub.vendor import scene_manifest as _sm
        if hasattr(_sm, "compute_scene_hash"):
            scene_hash = _sm.compute_scene_hash(manifest_data)
        else:
            import hashlib
            scene_hash = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
    except Exception:
        import hashlib
        scene_hash = hashlib.sha256(canonical.encode("utf-8")).hexdigest()

    pretty = _json.dumps(manifest_data, ensure_ascii=False, indent=2)
    try:
        await _execute_python_in_workspace(
            user["username"],
            studies.write_scene_manifest_pod_code(sid, pid, pretty),
        )
    except Exception as exc:
        raise HTTPException(503, f"Workspace indisponible : {exc}")

    previous = await studies.scene_manifest_get_latest(pid)
    previous_hash = previous.get("scene_hash") if previous else ""
    n_layers = len(manifest_data.get("layers", []))
    size_bytes = len(pretty.encode("utf-8"))
    await studies.scene_manifest_insert(
        pid=pid, sid=sid, owner=user["username"],
        scene_hash=scene_hash, n_layers=n_layers, size_bytes=size_bytes,
        previous_hash=previous_hash,
    )
    latest = await studies.scene_manifest_get_latest(pid)
    return {
        "saved": True,
        "scene_hash": scene_hash,
        "version_num": latest.get("version_num") if latest else 1,
    }


@app.get("/studies/{sid}/projects/{pid}/scene_manifest/history")
async def scene_manifest_history_endpoint(
    sid: str, pid: str,
    user: dict = Depends(auth.get_current_user),
):
    """Historique des versions du Scene Manifest pour ce projet (audit
    trail INSERT-only). Retourne toutes les versions DB triees par
    version_num DESC."""
    if not _STUDIES_AVAILABLE:
        raise HTTPException(503, "Module studies indisponible")
    s = await studies.get_study(sid, user["username"])
    if not s:
        raise HTTPException(404, "Étude introuvable")
    p = await studies.get_project(pid, user["username"])
    if not p or p["sid"] != sid:
        raise HTTPException(404, "Projet introuvable dans cette étude")
    return await studies.scene_manifest_history(pid)


# ── Sprint Composants-1 Phase B (2026-06-24) : export Grist (.grist) ──────────
# Wrapper hub qui appelle le tool MCP 'export_grist' de BigQgisMCP avec :
# - output_path force dans projects/{pid}/exports/ (au lieu du /data/ legacy)
# - scene_manifest_json embarque automatiquement si une version existe
# Indexe le resultat dans exports_index pour audit trail.

async def _call_mcp_tool_in_workspace(
    owner: str, tool_name: str, arguments: dict, timeout: int = 120,
) -> dict:
    """Helper generique : appelle un tool MCP arbitraire sur le pod workspace.
    Variant de _execute_python_in_workspace qui n'est pas hardcode sur
    execute_python. Retourne le payload JSON-RPC result complet."""
    s = await _get_or_create_session(owner)
    api_key = await auth.create_or_get_api_key(owner)
    payload = {
        "jsonrpc": "2.0", "id": f"hub-mcp-{tool_name}",
        "method": "tools/call",
        "params": {"name": tool_name, "arguments": arguments},
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
    # Le wrapper MCP encode souvent le result en JSON dans le 1er content text.
    try:
        return _json.loads(text)
    except Exception:
        return {"raw_text": text, "_parse_error": True}


@app.post("/studies/{sid}/projects/{pid}/export_grist")
async def export_grist_endpoint(
    sid: str, pid: str,
    request: Request,
    user: dict = Depends(auth.get_current_user),
):
    """Genere un fichier .grist (SQLite Grist) depuis le projet QGIS actif.

    Wraps BigQgisMCP `export_grist` MCP tool avec :
    - output_path force dans {sid}/projects/{pid}/exports/{doc_name}.grist
    - scene_manifest_json embarque si une version Scene Manifest existe
      (cherche scene_manifest_index latest pour ce pid puis lit le fichier)

    Body (optionnel) : {document_name?, max_features_per_layer?, include_stats?,
                        detect_relationships?, timezone?, embed_scene_manifest?}

    Indexe le resultat en DB (exports_index) pour audit trail.
    Retourne le payload BigQgisMCP enrichi d'un download_url interne.
    """
    if not _STUDIES_AVAILABLE:
        raise HTTPException(503, "Module studies indisponible")
    s = await studies.get_study(sid, user["username"])
    if not s:
        raise HTTPException(404, "Étude introuvable")
    p = await studies.get_project(pid, user["username"])
    if not p or p["sid"] != sid:
        raise HTTPException(404, "Projet introuvable dans cette étude")

    try:
        body = await request.json()
    except Exception:
        body = {}

    # 1. Resolve doc_name + output_path
    import re as _re
    raw_name = body.get("document_name") or s["name"] or f"export_{pid[:8]}"
    doc_name = _re.sub(r'[^\w\-]', '_', raw_name).strip('_') or f"export_{pid[:8]}"
    filename = f"{doc_name}.grist"
    output_path = f"/data/studies/{sid}/projects/{pid}/exports/{filename}"

    # 2. Embarquer Scene Manifest s'il existe et que le user le veut
    embed_sm = body.get("embed_scene_manifest", True)
    scene_manifest_json = ""
    scene_hash_used = ""
    if embed_sm:
        try:
            sm_latest = await studies.scene_manifest_get_latest(pid)
            if sm_latest:
                # Lire le fichier JSON sur PVC via execute_python
                stdout = await _execute_python_in_workspace(
                    user["username"],
                    studies.read_scene_manifest_pod_code(sid, pid),
                )
                if "SCENE_MANIFEST_READ_OK" in stdout:
                    import base64
                    b64 = stdout.split("b64=", 1)[1].split()[0].strip()
                    scene_manifest_json = base64.b64decode(b64).decode("utf-8")
                    scene_hash_used = sm_latest.get("scene_hash", "")
        except Exception as exc:
            log.warning("Lecture scene_manifest pour export Grist : %s", exc)

    # 3. Construire les params pour le tool MCP BigQgisMCP
    mcp_params = {
        "document_name": doc_name,
        "output_path": output_path,
        "max_features_per_layer": int(body.get("max_features_per_layer", 50000)),
        "include_stats": bool(body.get("include_stats", True)),
        "detect_relationships": bool(body.get("detect_relationships", True)),
        "timezone": body.get("timezone", "Europe/Paris"),
    }
    if scene_manifest_json:
        mcp_params["scene_manifest_json"] = scene_manifest_json

    # 4. Appel MCP
    try:
        result = await _call_mcp_tool_in_workspace(
            user["username"], "export_grist", mcp_params, timeout=180,
        )
    except Exception as exc:
        log.error("export_grist MCP call : %s", exc)
        raise HTTPException(503, f"Workspace indisponible pour l'export : {exc}")

    if not result.get("success"):
        raise HTTPException(
            500,
            f"Echec export_grist : {result.get('error') or result.get('raw_text', 'inconnu')[:300]}",
        )

    # 5. Indexer en DB
    import json as _json
    extra = {
        "scene_manifest_embedded": result.get("scene_manifest_embedded", False),
        "pages": result.get("pages", {}),
        "layers": result.get("layers", {}),
    }
    try:
        await studies.exports_insert(
            pid=pid, sid=sid, owner=user["username"],
            export_type="grist",
            filename=filename,
            file_path=output_path,
            size_bytes=int(result.get("size_bytes", 0)),
            scene_hash=scene_hash_used,
            n_tables=int(result.get("tables", 0)),
            n_records=int(result.get("total_records", 0)),
            extra_json=_json.dumps(extra, ensure_ascii=False),
        )
    except Exception as exc:
        log.warning("exports_index insert : %s", exc)

    # 6. Construire le download_url interne (proxy via /files/{path:path})
    # Le tool BigQgisMCP retourne un download_url localhost interne au pod.
    # On le remplace par le proxy hub pour que le browser puisse y acceder.
    relative = output_path.lstrip("/").removeprefix("data/")
    download_url = f"/files/{relative}"

    return {
        **result,
        "download_url": download_url,
        "scene_hash": scene_hash_used,
    }


@app.get("/studies/{sid}/projects/{pid}/exports")
async def list_project_exports_endpoint(
    sid: str, pid: str,
    export_type: str | None = None,
    user: dict = Depends(auth.get_current_user),
):
    """Liste les exports generes pour ce projet (audit trail).
    Query param export_type=grist|gpkg|zip_composite pour filtrer."""
    if not _STUDIES_AVAILABLE:
        raise HTTPException(503, "Module studies indisponible")
    s = await studies.get_study(sid, user["username"])
    if not s:
        raise HTTPException(404, "Étude introuvable")
    p = await studies.get_project(pid, user["username"])
    if not p or p["sid"] != sid:
        raise HTTPException(404, "Projet introuvable dans cette étude")
    return await studies.exports_list(pid, export_type=export_type)


# ── Sprint Composants Phase 2 (2026-06-25) : endpoints COMPOSANTS + SCHEMA ────
# Feature flag COMPONENTS_ENABLED (default false en prod, true en dev).
# Quand false : retourne 503 sur tous les /components/* endpoints.
# Activé via env var sur le pod hub. Sprint 3 ajoutera ASSEMBLIES_ENABLED.

_COMPONENTS_ENABLED = os.getenv("COMPONENTS_ENABLED", "true").lower() == "true"


def _check_components_enabled():
    if not _COMPONENTS_ENABLED:
        raise HTTPException(
            status_code=503,
            detail="Feature COMPONENTS_ENABLED désactivée. Set env var "
                   "COMPONENTS_ENABLED=true pour activer.",
        )


# ── Schema introspection (méta-cognition agent IA P0) ────────────────────────

@app.get("/schema/{entity_type}")
async def schema_describe_endpoint(
    entity_type: str,
    kind: str | None = None,
    user: dict = Depends(auth.get_current_user),
):
    """Retourne JSON Schema Pydantic + exemple minimal valide pour
    entity_type (component, assembly, audit_chain, ...).

    Permet à l'agent IA d'inspecter la structure attendue avant
    d'appeler create_component / create_assembly.
    """
    from hub import schema_introspect as si
    return si.describe_entity_schema(entity_type, kind=kind)


# Sprint 3 P2 (8.14) - Monitoring client errors
# Buffer simple en RAM (premier 100 erreurs) pour traçabilité erreurs JavaScript
# côté BlockNote ou autre frontend. Pas de persistence DB (volontaire V1).
_CLIENT_ERROR_BUFFER: list[dict] = []
_CLIENT_ERROR_BUFFER_MAX = 100


@app.post("/api/log/client-error")
async def log_client_error_endpoint(
    request: Request,
    user: dict = Depends(auth.get_current_user),
):
    """Sprint 3 P2 (8.14) : recoit les erreurs JavaScript des clients BlockNote.

    Permet de detecter les autosave fails, les bundle errors, etc. sans
    monitoring externe Sentry. Ring-buffer 100 entries en RAM (volatile).

    Body attendu :
        {message: str, stack?: str, url?: str, line?: int, ua?: str,
         context?: dict}
    """
    import time as _time
    try:
        payload = await request.json()
    except Exception:
        raise HTTPException(400, "Body JSON invalide")

    entry = {
        "ts": _time.time(),
        "user": user.get("username", "anonymous"),
        "message": str(payload.get("message", ""))[:500],
        "stack": str(payload.get("stack", ""))[:2000],
        "url": str(payload.get("url", ""))[:300],
        "line": payload.get("line"),
        "ua": str(payload.get("ua", ""))[:200],
        "context": payload.get("context"),
    }
    _CLIENT_ERROR_BUFFER.append(entry)
    # Ring buffer : drop le plus ancien si overflow
    if len(_CLIENT_ERROR_BUFFER) > _CLIENT_ERROR_BUFFER_MAX:
        _CLIENT_ERROR_BUFFER.pop(0)
    log.warning(
        "client error from %s : %s (%s)",
        entry["user"], entry["message"][:100], entry["url"][:80],
    )
    return {"ok": True, "count_in_buffer": len(_CLIENT_ERROR_BUFFER)}


@app.get("/api/log/client-errors")
async def get_client_errors_endpoint(
    user: dict = Depends(auth.get_current_user),
    limit: int = 50,
):
    """Sprint 3 P2 (8.14) : lit le ring buffer client errors (auth obligatoire).

    Retourne les `limit` dernieres erreurs (default 50, max 100).
    """
    limit = min(max(limit, 1), _CLIENT_ERROR_BUFFER_MAX)
    return {
        "count": len(_CLIENT_ERROR_BUFFER),
        "limit": limit,
        "errors": _CLIENT_ERROR_BUFFER[-limit:],
    }


@app.get("/schema/{entity_type}/kinds")
async def schema_kinds_endpoint(
    entity_type: str,
    user: dict = Depends(auth.get_current_user),
):
    """Liste les `kind` possibles pour un entity_type (anti-hallucination LLM).

    Ex: /schema/component/kinds → {kinds: ['interactive_map', 'scene_3d', ...]}

    Sprint 1 Vague E3 fix D9 : pour `assembly`, on filtre temporairement à
    `storymap_narrative_dsfr` tant que les templates render des autres kinds
    (`dashboard`, `sheet_a4`, `modal_embed`, `atlas_immersive`) ne sont pas
    livres (Sprint 4 ulterieur). Sinon l'agent IA cree un Assembly valide
    Pydantic mais render_assembly renvoie 501 sans message clair.
    """
    from hub import schema_introspect as si
    result = si.list_entity_kinds(entity_type)
    if entity_type == "assembly":
        # Restreindre temporairement aux kinds dont le template Jinja2 existe
        supported = {"storymap_narrative_dsfr"}
        kinds = result.get("kinds", [])
        result["kinds"] = [k for k in kinds if k in supported]
        result["_filter_note"] = (
            "Sprint 1 E3 (D9) : seuls les kinds avec template render livre sont "
            "exposes. Les autres (dashboard, sheet_a4, modal_embed, atlas_immersive) "
            "sont valides Pydantic mais render_assembly renvoie 501. Sprint 4 a venir."
        )
    return result


@app.post("/schema/{entity_type}/validate")
async def schema_validate_endpoint(
    entity_type: str,
    request: Request,
    user: dict = Depends(auth.get_current_user),
):
    """Dry-run validation Pydantic. Retourne erreurs structurées exploitables
    par l'agent IA pour corriger sa payload AVANT le create_*."""
    from hub import schema_introspect as si
    try:
        payload = await request.json()
    except Exception:
        raise HTTPException(400, "Body JSON invalide")
    return si.validate_manifest(entity_type, payload)


# ── BlockNote editor (Vague E2 Commit E1, D-QGIS-010 2026-06-29) ──────────────

@app.get("/editor/{sid}/assembly/{aid}", response_class=HTMLResponse)
async def blocknote_editor_endpoint(
    sid: str,
    aid: str,
    user: dict = Depends(auth.get_current_user),
):
    """Sert l'editeur BlockNote standalone pour edition d'un Assembly.

    D-QGIS-010 Vague E2 pivot UI : permet a Marie de modifier visuellement
    un livrable apres creation par l'agent IA (chat Vague E1) ou via patterns
    metier (Vague E2 base).

    Le bundle BlockNote (build via Vite multi-stage Docker) est servi
    statiquement par mount /static/blocknote-editor/. Cet endpoint retourne
    l'index.html qui charge le bundle React.

    Le React parse sid/aid depuis URL et fetch l'assembly via API hub.
    """
    if not _BLOCKNOTE_STATIC_DIR.exists():
        raise HTTPException(
            503,
            "Bundle BlockNote non installe. Build via 'npm run build' dans "
            "blocknote-editor/, ou via CI Docker multi-stage (D-QGIS-010)."
        )
    index_path = _BLOCKNOTE_STATIC_DIR / "index.html"
    if not index_path.exists():
        raise HTTPException(503, "Bundle BlockNote incomplet (index.html absent).")
    return FileResponse(index_path)


# ── Storymap patterns (Vague E2 Commit 3, D-QGIS-009 §3) ──────────────────────

@app.get("/storymap_patterns")
async def list_storymap_patterns_endpoint(
    user: dict = Depends(auth.get_current_user),
):
    """Liste les patterns metier d'une storymap CEREMA.

    6 patterns canoniques (hero_constat, zoom_territoire, croisement_enjeu,
    fiche_indicateur, reliability_summary, conclusion_actionnable).

    Use case agent IA : decouvrir AVANT de composer une storymap from
    scratch. Permet de penser en chapitres metier au lieu de blocs HTML.
    """
    from hub import storymap_patterns as sp
    return {"patterns": sp.list_patterns()}


@app.get("/storymap_patterns/{name}")
async def get_storymap_pattern_endpoint(
    name: str,
    user: dict = Depends(auth.get_current_user),
):
    """Recette complete d'un pattern : params_schema + components_template +
    section_template + example.

    L'agent IA construit ensuite les N component manifests + cree N components
    + ajoute la section au layout de son assembly.
    """
    from hub import storymap_patterns as sp
    try:
        return sp.describe_pattern(name)
    except ValueError as exc:
        raise HTTPException(404, str(exc))


# ── Components CRUD (Sprint Composants Phase 2) ──────────────────────────────

# ── Sprint Composants Phase 4a (2026-06-27) : agents partagés ────────────────
# Pattern publication agents = scoped_keys + colonnes publication (audience,
# published_url, published_at, audit_chain_json). Réutilise infra V1.5
# Passerelle-Archi (table scoped_keys + helpers create/validate/revoke).
#
# Workflow :
#  1. POST /studies/{sid}/scoped-keys → mint clé qgisk_<user>_<hex>
#  2. POST /schema/agent-config/analyze → meta-agent analyse pré-mint
#  3. POST /studies/{sid}/scoped-keys/{kid}/publish → audit_chain + URL widget S3
#  4. GET /studies/{sid}/scoped-keys → liste owned + publiés
#  5. DELETE /studies/{sid}/scoped-keys/{kid} → soft revoke


@app.post("/studies/{sid}/scoped-keys")
async def mint_scoped_key_endpoint(
    sid: str,
    payload: dict,
    user: dict = Depends(auth.get_current_user),
):
    """Mint une scope key pour un agent partagé.

    Payload : {profile, audience, expires_at, data_scope, tools_whitelist,
               project_id, label}

    Retourne {key_id, masked_key, ...metadata}. La clé brute N'EST pas
    retournée 2 fois — l'user doit la copier ici ou utiliser la liste
    qui ne montre que masked.

    NB : la clé brute est exceptionnellement renvoyée DANS CE response
    (et seulement ici) pour copy-clipboard immédiat. Documenter UX :
    "Copiez maintenant, vous ne verrez plus la clé en clair".
    """
    if not _STUDIES_AVAILABLE:
        raise HTTPException(503, "Module studies indisponible")
    # Verifier que l'étude appartient au user
    study = await studies.get_study(sid, user["username"])
    if not study:
        raise HTTPException(404, "Étude introuvable ou pas owner")
    # Defaults
    profile = payload.get("profile", "storymap_creator_v15")
    audience = payload.get("audience", "cerema_internal")
    expires_at = payload.get("expires_at")
    data_scope = payload.get("data_scope", "project")
    tools = payload.get("tools_whitelist", "all")
    project_id = payload.get("project_id")
    label = payload.get("label", f"Agent partagé {sid[:6]}")
    # Mint via auth helper existant
    key = await auth.create_scoped_key(
        username=user["username"],
        study_id=sid,
        project_id=project_id,
        persona=profile,
        tools=tools,
        data_scope=data_scope,
        mode="scoped",
        actor="delegate",  # publié = délégué
        label=label,
        expires_at=expires_at,
    )
    # Update audience (pas dans helper existant)
    import aiosqlite
    async with aiosqlite.connect(auth._DB_PATH) as db:
        await db.execute(
            "UPDATE scoped_keys SET audience = ? WHERE id = ?",
            (audience, key),
        )
        await db.commit()
    return {
        "key": key,  # bearer brut — copier maintenant
        "key_masked": key[:14] + "…",
        "sid": sid,
        "project_id": project_id,
        "profile": profile,
        "audience": audience,
        "expires_at": expires_at,
        "data_scope": data_scope,
        "label": label,
        "warning_copy_now": "Cette clé ne sera plus affichée en clair. Copiez-la maintenant.",
    }


@app.get("/studies/{sid}/scoped-keys")
async def list_scoped_keys_endpoint(
    sid: str,
    include_revoked: bool = False,
    only_published: bool = False,
    user: dict = Depends(auth.get_current_user),
):
    """Liste les agents partagés de l'utilisateur pour cette étude."""
    if not _STUDIES_AVAILABLE:
        raise HTTPException(503, "Module studies indisponible")
    if only_published:
        all_keys = await auth.list_scoped_keys_published(user["username"])
    else:
        all_keys = await auth.list_scoped_keys(user["username"], include_revoked)
    # Filter sid
    filtered = [k for k in all_keys if k.get("study_id") == sid]
    return {"sid": sid, "count": len(filtered), "agents": filtered}


@app.post("/studies/{sid}/scoped-keys/{kid}/publish")
async def publish_agent_endpoint(
    sid: str,
    kid: str,
    payload: dict | None = None,
    user: dict = Depends(auth.get_current_user),
):
    """Publie un agent partagé : calcule audit_chain + génère URL widget.

    Workflow :
    1. Vérifier que kid appartient au user
    2. Récupérer metadata (profile, audience, scope)
    3. Résoudre composants/recipes/assemblies visibles depuis sid
    4. Calculer audit_chain.integrity_hash SHA256 (D-FORMAT-008 ex-signed_hash)
    5. Générer URL widget (https://hub.../agent-share/{key_short})
    6. UPDATE scoped_keys (published_url, published_at, audit_chain_json)

    NB : pas d'upload S3 du widget — il est servi dynamiquement par
    l'endpoint /agent-share/{key_short} qui appelle /chat avec scope.
    """
    if not _STUDIES_AVAILABLE:
        raise HTTPException(503, "Module studies indisponible")
    if not kid.startswith(auth._SCOPED_PREFIX):
        raise HTTPException(400, "key_id doit commencer par 'qgisk_'")
    # Récupérer meta
    meta = await auth.get_scoped_key_meta(kid)
    if not meta or meta.get("username") != user["username"]:
        raise HTTPException(404, "Agent partagé introuvable ou pas owner")
    if meta.get("revoked_at"):
        raise HTTPException(400, "Cet agent a été révoqué")
    if meta.get("study_id") != sid:
        raise HTTPException(400, f"key_id étude {meta.get('study_id')} != URL sid {sid}")

    audience = (payload or {}).get("audience") or meta.get("audience", "cerema_internal")
    # Résoudre contexte étude
    components_visible: list[str] = []
    recipes_visible: list[str] = []
    assemblies_visible: list[str] = []
    has_restricted = False
    try:
        from hub import components as comp_mod
        from hub import assemblies as asm_mod
        comps = await comp_mod.list_components(sid=sid, owner=user["username"])
        components_visible = [c["cid"] for c in comps if c.get("cid")]
        for c in comps:
            if c.get("classification") in ("restricted", "confidential"):
                has_restricted = True
        asms = await asm_mod.list_assemblies(sid=sid, owner=user["username"])
        assemblies_visible = [a["aid"] for a in asms if a.get("aid")]
    except Exception as exc:
        log.warning("publish_agent : resolve context failed: %s", exc)
    try:
        recipes_list = await studies.recipe_index_list(sid)
        recipes_visible = [r["slug"] for r in recipes_list if r.get("slug")]
    except Exception:
        pass

    # Audit chain agent
    import json as _json
    import hashlib as _hash
    audit_chain = {
        "key_id_masked": kid[:14] + "…",
        "sid": sid,
        "pid": meta.get("project_id"),
        "owner": user["username"],
        "profile": meta.get("persona"),
        "audience": audience,
        "data_scope": meta.get("data_scope"),
        "tools_whitelist": meta.get("tools"),
        "components_visible": components_visible,
        "recipes_visible": recipes_visible,
        "assemblies_visible": assemblies_visible,
        "expires_at": meta.get("expires_at"),
        "created_at": meta.get("created_at"),
        "published_at": int(time.time()),
        "has_restricted_components": has_restricted,
    }
    canonical = _json.dumps(audit_chain, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    audit_chain["integrity_hash"] = "sha256:" + _hash.sha256(canonical.encode("utf-8")).hexdigest()
    # Backward-compat legacy field (1 release de grâce, D-FORMAT-008)
    audit_chain["signed_hash"] = audit_chain["integrity_hash"]

    # URL widget : page d'atterrissage côté hub (sert /chat-embed avec Bearer)
    key_short = kid.replace(auth._SCOPED_PREFIX, "")[:12]
    # Use _HUB_URL (peut être hub user-X) → endpoint /agent-share/{key_short}
    published_url = f"{_HUB_URL.rstrip('/')}/agent-share/{key_short}" if _HUB_URL else f"/agent-share/{key_short}"

    ok = await auth.update_scoped_key_publication(
        key_id=kid,
        published_url=published_url,
        audit_chain_json=_json.dumps(audit_chain, ensure_ascii=False),
        audience=audience,
    )
    if not ok:
        raise HTTPException(503, "UPDATE scoped_keys publication échec")

    return {
        "key_id_masked": kid[:14] + "…",
        "published": True,
        "published_url": published_url,
        "audience": audience,
        "audit_chain": {
            "integrity_hash": audit_chain["integrity_hash"],
            "signed_hash": audit_chain["signed_hash"],  # legacy backward-compat D-FORMAT-008
            "components_visible_count": len(components_visible),
            "recipes_visible_count": len(recipes_visible),
            "assemblies_visible_count": len(assemblies_visible),
            "has_restricted_components": has_restricted,
        },
    }


@app.delete("/studies/{sid}/scoped-keys/{kid}", status_code=204)
async def revoke_agent_endpoint(
    sid: str,
    kid: str,
    user: dict = Depends(auth.get_current_user),
):
    """Révoque un agent partagé (soft delete : revoked_at)."""
    if not kid.startswith(auth._SCOPED_PREFIX):
        raise HTTPException(400, "key_id doit commencer par 'qgisk_'")
    meta = await auth.get_scoped_key_meta(kid)
    if not meta or meta.get("username") != user["username"]:
        raise HTTPException(404, "Agent partagé introuvable ou pas owner")
    if meta.get("study_id") != sid:
        raise HTTPException(400, "Étude ne correspond pas")
    await auth.revoke_scoped_key(kid)
    return None


# ── Sprint Composants Phase 4a : meta-agent analyseur config ─────────────────


@app.get("/schema/agent-config/analysis")
async def get_agent_config_analysis_endpoint(
    sid: str,
    config_hash: str | None = None,
    user: dict = Depends(auth.get_current_user),
):
    """Lookup cache AgentConfigAnalysis pour une étude.

    Si config_hash fourni : exact match. Sinon : dernière analyse connue.
    """
    if not _STUDIES_AVAILABLE:
        raise HTTPException(503, "Module studies indisponible")
    meta = await studies.agent_analyses_get_latest(sid, config_hash)
    if not meta:
        return {"found": False, "sid": sid, "config_hash_requested": config_hash}

    # Lire JSON complet PVC (via _execute_python_in_workspace)
    file_path = meta.get("file_path", "")
    analysis_json = None
    try:
        code = f"""
import base64, json
from pathlib import Path
p = Path({file_path!r})
if not p.exists():
    print('AGENT_ANALYSIS_NOT_FOUND')
else:
    data = p.read_bytes()
    b64 = base64.b64encode(data).decode()
    print(f'AGENT_ANALYSIS_READ_OK b64={{b64}}')
"""
        stdout = await _execute_python_in_workspace(user["username"], code)
        for line in stdout.splitlines():
            if line.startswith("AGENT_ANALYSIS_READ_OK b64="):
                import base64 as _b64, json as _json
                b64 = line[len("AGENT_ANALYSIS_READ_OK b64="):].strip()
                analysis_json = _json.loads(_b64.b64decode(b64).decode("utf-8"))
                break
    except Exception as exc:
        log.warning("agent analysis read PVC: %s", exc)

    return {
        "found": True,
        "metadata": meta,
        "analysis": analysis_json,
    }


@app.post("/schema/agent-config/analysis")
async def post_agent_config_analysis_endpoint(
    payload: dict,
    user: dict = Depends(auth.get_current_user),
):
    """Persiste un AgentConfigAnalysis (DB + PVC). Appelé par l'agent
    (tool natif analyze_agent_config) après LLM call.
    """
    if not _STUDIES_AVAILABLE:
        raise HTTPException(503, "Module studies indisponible")
    from hub.models import AgentConfigAnalysis
    from pydantic import ValidationError
    import json as _json

    try:
        analysis = AgentConfigAnalysis.model_validate(payload)
    except ValidationError as exc:
        raise HTTPException(
            422,
            {"error": "AgentConfigAnalysis validation failed",
             "detail": exc.errors()[:10]},
        )

    # Path PVC
    file_path = (
        f"/data/studies/{analysis.sid}/agents/"
        f"analysis_{analysis.short_hash()}.json"
    )
    analysis_json = analysis.model_dump_json(indent=2)

    # Persist PVC
    try:
        code = f"""
from pathlib import Path
p = Path({file_path!r})
p.parent.mkdir(parents=True, exist_ok=True)
p.write_text({analysis_json!r}, encoding='utf-8')
print(f'AGENT_ANALYSIS_WRITE_OK path={{p}}')
"""
        stdout = await _execute_python_in_workspace(user["username"], code)
        if "AGENT_ANALYSIS_WRITE_OK" not in stdout:
            raise RuntimeError(f"PVC write KO: {stdout[:200]}")
    except Exception as exc:
        raise HTTPException(503, f"PVC write failed: {exc}")

    # Count warnings/errors
    n_w = sum(1 for c in analysis.quality_checks if c.severity == "warning")
    n_e = sum(1 for c in analysis.quality_checks if c.severity == "error")

    rowid = await studies.agent_analyses_insert(
        sid=analysis.sid,
        config_hash=analysis.config_hash,
        owner=user["username"],
        profile=analysis.profile,
        audience=analysis.audience,
        analyzer_model=analysis.analyzer_model,
        file_path=file_path,
        overall_score=analysis.overall_score,
        n_params=len(analysis.params_analysis),
        n_warnings=n_w,
        n_errors=n_e,
        status=analysis.status,
        error_detail=analysis.error_detail,
        analyzer_version=analysis.analyzer_version,
    )

    return {
        "ok": True,
        "rowid": rowid,
        "sid": analysis.sid,
        "config_hash": analysis.config_hash,
        "file_path": file_path,
        "overall_score": analysis.overall_score,
        "n_warnings": n_w,
        "n_errors": n_e,
    }


# Ajouter recipe_analyzer + agent_config_analyzer à la whitelist internal profiles
# (déjà fait pour recipe_analyzer dans Phase 3c hotfix).


# ── Sprint Composants Phase 3c (2026-06-27) : meta-agent analyseur recipes ───
# Endpoints REST pour le cache RecipeAnalysis (DB index + PVC JSON).
# Pattern : (slug, source, content_hash[:12]) cache key. Lookup hub-side,
# enrichissement LLM côté agent (via tool natif analyze_recipe). Trigger
# automatique au PUT recipe via webhook fire-and-forget vers l'agent.


@app.get("/schema/recipe/{slug}/analysis")
async def get_recipe_analysis_endpoint(
    slug: str,
    source: str = "auto",
    content_hash: str | None = None,
    user: dict = Depends(auth.get_current_user),
):
    """Lookup cache RecipeAnalysis (Phase 3c).

    source : "user" | "system" | "auto" (auto cherche user d'abord, fallback system)
    content_hash : optionnel — si fourni, exact match (cache lookup tool natif).
                   Sinon, dernière analyse connue (admin review).

    Retourne :
    - {found: false, ...} si miss → l'agent doit déclencher l'analyse
    - {found: true, analysis: RecipeAnalysis} si HIT
    """
    if not _STUDIES_AVAILABLE:
        raise HTTPException(503, "Module studies indisponible")

    from hub import recipe_analyzer_cache as cache_mod

    # Recherche selon source
    sources_to_try = (
        ["user", "system"] if source == "auto"
        else ["user"] if source == "user"
        else ["system"]
    )

    found_meta = None
    for src in sources_to_try:
        meta = await studies.recipe_analyses_get_latest(slug, src, content_hash)
        if meta:
            found_meta = meta
            break

    if not found_meta:
        return {
            "found": False,
            "slug": slug,
            "source_requested": source,
            "content_hash_requested": content_hash,
        }

    # Lire le JSON complet depuis PVC
    file_path = found_meta.get("file_path", "")
    try:
        stdout = await _execute_python_in_workspace(
            user["username"],
            cache_mod.read_recipe_analysis_pod_code(file_path),
        )
        analysis_json = cache_mod.parse_read_marker(stdout)
    except Exception as exc:
        log.warning("read recipe analysis PVC failed: %s", exc)
        analysis_json = None

    return {
        "found": True,
        "metadata": found_meta,
        "analysis": analysis_json,
        "pvc_read_status": "ok" if analysis_json else "failed",
    }


@app.post("/schema/recipe/{slug}/analysis")
async def post_recipe_analysis_endpoint(
    slug: str,
    payload: dict,
    user: dict = Depends(auth.get_current_user),
):
    """Persiste une RecipeAnalysis (DB index + PVC JSON).

    Appelé par l'agent (tool natif analyze_recipe) après que le LLM
    profile_analyzer a produit une analyse. Body = RecipeAnalysis serialisé.

    Source détectée depuis le payload (analysis.source).
    """
    if not _STUDIES_AVAILABLE:
        raise HTTPException(503, "Module studies indisponible")

    from hub import recipe_analyzer_cache as cache_mod
    from hub.models import RecipeAnalysis
    from pydantic import ValidationError
    import json as _json

    # Validation Pydantic stricte
    try:
        analysis = RecipeAnalysis.model_validate(payload)
    except ValidationError as exc:
        raise HTTPException(
            422,
            {
                "error": "RecipeAnalysis validation failed",
                "detail": exc.errors()[:10],
            },
        )

    # Vérifier cohérence slug URL vs payload
    if analysis.slug != slug:
        raise HTTPException(400, f"slug mismatch: url={slug} vs payload={analysis.slug}")

    # Path PVC selon source
    if analysis.source == "user":
        # Pour user recipes, on a besoin du sid de l'étude active
        active_sid = await studies.get_active_study_id(user["username"])
        if not active_sid:
            raise HTTPException(400, "User recipe analysis : aucune étude active")
        file_path = cache_mod.user_recipe_analysis_path(active_sid, slug)
    else:
        file_path = cache_mod.system_recipe_analysis_path(
            slug, analysis.content_hash,
        )

    # Sérialiser JSON
    analysis_json = analysis.model_dump_json(indent=2)

    # Persist PVC
    try:
        stdout = await _execute_python_in_workspace(
            user["username"],
            cache_mod.write_recipe_analysis_pod_code(file_path, analysis_json),
        )
        if "RECIPE_ANALYSIS_WRITE_OK" not in stdout:
            raise RuntimeError(f"PVC write KO: {stdout[:200]}")
    except Exception as exc:
        raise HTTPException(503, f"PVC write failed: {exc}")

    # Compter warnings/errors pour breakdown DB
    n_warnings = sum(1 for c in analysis.quality_checks if c.severity == "warning")
    n_errors = sum(1 for c in analysis.quality_checks if c.severity == "error")

    # Insert DB
    active_sid = await studies.get_active_study_id(user["username"]) if analysis.source == "user" else None
    rowid = await studies.recipe_analyses_insert(
        slug=analysis.slug,
        source=analysis.source,
        content_hash=analysis.content_hash,
        analyzer_model=analysis.analyzer_model,
        file_path=file_path,
        overall_score=analysis.overall_score,
        cost_level=analysis.cost_level,
        n_params=len(analysis.params_analysis),
        n_warnings=n_warnings,
        n_errors=n_errors,
        status=analysis.status,
        error_detail=analysis.error_detail,
        sid=active_sid,
        owner=user["username"],
        analyzer_version=analysis.analyzer_version,
    )

    return {
        "ok": True,
        "rowid": rowid,
        "slug": slug,
        "source": analysis.source,
        "content_hash": analysis.content_hash,
        "file_path": file_path,
        "overall_score": analysis.overall_score,
        "n_params": len(analysis.params_analysis),
        "n_warnings": n_warnings,
        "n_errors": n_errors,
    }


@app.get("/schema/recipe/{slug}/analysis/history")
async def get_recipe_analysis_history_endpoint(
    slug: str,
    source: str = "user",
    limit: int = 20,
    user: dict = Depends(auth.get_current_user),
):
    """Historique des versions d'analyse (par content_hash)."""
    if not _STUDIES_AVAILABLE:
        raise HTTPException(503, "Module studies indisponible")
    if source not in ("user", "system"):
        raise HTTPException(400, "source doit être 'user' ou 'system'")
    history = await studies.recipe_analyses_history(slug, source, limit)
    return {"slug": slug, "source": source, "count": len(history), "history": history}


@app.get("/admin/recipe-analyses/review")
async def admin_recipe_analyses_review_endpoint(
    limit: int = 50,
    user: dict = Depends(auth.get_current_user),
):
    """Admin review : analyses non-validées trié par score croissant.

    Endpoint admin (V2 UI desk panel "Recipes Quality Review").
    """
    if not _STUDIES_AVAILABLE:
        raise HTTPException(503, "Module studies indisponible")
    pending = await studies.recipe_analyses_review_pending(limit)
    return {"count": len(pending), "pending_review": pending}


@app.post("/admin/recipe-analyses/{rowid}/validate")
async def admin_validate_recipe_analysis_endpoint(
    rowid: int,
    payload: dict | None = None,
    user: dict = Depends(auth.get_current_user),
):
    """Admin marque une analyse comme human_validated.

    Body optionnel : {"notes": "fix_hint #2 appliqué le 2026-06-30"}
    """
    if not _STUDIES_AVAILABLE:
        raise HTTPException(503, "Module studies indisponible")
    notes = (payload or {}).get("notes")
    ok = await studies.recipe_analyses_mark_validated(
        rowid=rowid, validator=user["username"], notes=notes,
    )
    if not ok:
        raise HTTPException(404, f"Analysis rowid={rowid} introuvable")
    return {"ok": True, "rowid": rowid, "validator": user["username"]}


@app.get("/studies/{sid}/components")
async def list_components_endpoint(
    sid: str,
    kind: str | None = None,
    user: dict = Depends(auth.get_current_user),
):
    """Liste les composants de l'étude (latest version par cid)."""
    _check_components_enabled()
    if not _STUDIES_AVAILABLE:
        raise HTTPException(503, "Module studies indisponible")
    s = await studies.get_study(sid, user["username"])
    if not s:
        raise HTTPException(404, "Étude introuvable")
    from hub import components as comp_mod
    return await comp_mod.list_components(sid=sid, kind=kind, owner=user["username"])


@app.post("/studies/{sid}/components", status_code=status.HTTP_201_CREATED)
async def create_component_endpoint(
    sid: str,
    request: Request,
    user: dict = Depends(auth.get_current_user),
):
    """Crée un nouveau composant rattaché à l'étude.

    Body : Component manifest JSON (validé Pydantic V0.1). L'id est
    auto-généré si absent (12 hex uuid4 tronqué).
    """
    _check_components_enabled()
    if not _STUDIES_AVAILABLE:
        raise HTTPException(503, "Module studies indisponible")
    s = await studies.get_study(sid, user["username"])
    if not s:
        raise HTTPException(404, "Étude introuvable")

    from hub.models import Component
    from hub import components as comp_mod

    try:
        payload = await request.json()
    except Exception:
        raise HTTPException(400, "Body JSON invalide")

    # Auto-génération id si absent
    if not payload.get("id"):
        payload["id"] = studies._new_id()

    try:
        comp = Component.model_validate(payload)
    except Exception as exc:
        raise HTTPException(422, f"Validation Pydantic : {exc}")

    # Sprint Composants Phase 4b (2026-06-28) : auto-fill
    # provenance.scene_hash_at_creation si source.scene_hash present mais
    # provenance vide. Evite la discipline agent defaillante et garantit
    # que build_audit_chain propage le scene_hash dans audit.scene_hashes.
    try:
        src_scene_hash = getattr(comp.source, "scene_hash", None) if comp.source else None
        if src_scene_hash and not comp.provenance.scene_hash_at_creation:
            comp.provenance.scene_hash_at_creation = src_scene_hash
    except Exception:
        pass

    # Écrire manifest sur PVC
    import json as _json
    content_json = _json.dumps(
        comp.model_dump(mode="json"), ensure_ascii=False, indent=2,
    )
    try:
        stdout = await _execute_python_in_workspace(
            user["username"],
            comp_mod.write_component_manifest_pod_code(sid, comp.id, content_json),
        )
    except Exception as exc:
        log.warning("write component manifest pod-side : %s", exc)
        stdout = ""

    # Indexer en DB
    file_path = comp_mod.component_manifest_path(sid, comp.id)
    size_bytes = len(content_json.encode("utf-8"))
    rowid = await comp_mod.insert_component(
        component=comp, owner=user["username"], sid=sid,
        file_path=file_path, size_bytes=size_bytes,
    )

    return {
        "id": comp.id,
        "rowid": rowid,
        "kind": comp.kind,
        "title": comp.title,
        "classification": comp.classification,
        "manifest_url": f"/studies/{sid}/components/{comp.id}",
        "render_url":   f"/studies/{sid}/components/{comp.id}/render",
    }


@app.get("/studies/{sid}/components/{cid}")
async def get_component_endpoint(
    sid: str, cid: str,
    user: dict = Depends(auth.get_current_user),
):
    """Retourne le manifest latest du composant (depuis PVC + métadonnées DB)."""
    _check_components_enabled()
    from hub import components as comp_mod
    latest = await comp_mod.get_component_latest(cid)
    if not latest or latest["sid"] != sid:
        raise HTTPException(404, "Composant introuvable dans cette étude")
    if latest["owner"] != user["username"]:
        # TODO Sprint Composants Phase 3 : ACL classification (public visible
        # par tous, cerema_internal par CEREMA, restricted par invités…)
        raise HTTPException(403, "Pas owner — ACL non implémentée (Phase 3)")

    # Lire le contenu manifest depuis PVC
    try:
        stdout = await _execute_python_in_workspace(
            user["username"],
            comp_mod.read_component_manifest_pod_code(sid, cid),
        )
    except Exception as exc:
        log.warning("read component manifest pod : %s", exc)
        stdout = ""

    import base64, json as _json
    manifest_data = None
    if "COMPONENT_READ_OK" in stdout:
        try:
            b64 = stdout.split("b64=", 1)[1].split()[0].strip()
            manifest_data = _json.loads(base64.b64decode(b64).decode())
        except Exception as exc:
            log.warning("parse component manifest : %s", exc)

    return {
        "metadata": dict(latest),
        "manifest": manifest_data,
        "exists_on_pvc": manifest_data is not None,
    }


@app.get("/studies/{sid}/components/{cid}/assist/suggestions")
async def component_assist_suggestions_endpoint(
    sid: str, cid: str,
    user: dict = Depends(auth.get_current_user),
):
    """Sprint 2.5 V2.5 A2c — Suggestions contextuelles pour Assistant IA composant.

    Retourne 5 suggestions courtes (imperatif < 50 char) contextualisees au
    kind du composant (POC : hardcoded par kind, dynamiques via LLM en iter 2).

    Pattern Notion AI : Marie voit chips au mount du drawer, clique une
    action concrete plutot que de taper en NL. Etude C UX 2 modes :
    "smart escalation" (Marie ne choisit jamais mode, action choisit).

    Format reponse :
    {
        "suggestions": [
            {"id": "add_layer_tri", "label": "Ajouter le perimetre TRI",
             "prompt": "...", "action": "add_layer_tri"},
            ...
        ],
        "component_kind": "interactive_map",
        "cid": "b1c2d3e4f5a6",
    }
    """
    _check_components_enabled()
    from hub import components as comp_mod
    latest = await comp_mod.get_component_latest(cid)
    if not latest or latest["sid"] != sid:
        raise HTTPException(404, "Composant introuvable")
    if latest["owner"] != user["username"]:
        raise HTTPException(403, "Pas owner")

    kind = latest.get("kind", "interactive_map")

    # Sprint 2.5 POC : suggestions hardcoded par kind. Iter 2 : suggestions
    # dynamiques via LLM pre-pass caches (cid, params_hash) TTL 1h.
    _SUGGESTIONS_BY_KIND = {
        "interactive_map": [
            {
                "id": "add_layer_tri",
                "label": "Ajouter le perimetre TRI inondation",
                "prompt": "Ajoute le perimetre TRI Georisques inondation a cette carte",
                "hint": "Necessite l'agent IA complet (workspace QGIS)",
            },
            {
                "id": "center_marseille_4e",
                "label": "Centrer sur Marseille 4e arr.",
                "prompt": "Centre la carte sur Marseille 4e arrondissement (INSEE 13204)",
                "tool": "cmp_set_zone",
                "tool_args": {"kind": "commune", "insee": "13204"},
            },
            {
                "id": "tooltip_adresse",
                "label": "Au survol, afficher l'adresse",
                "prompt": "Configure la bulle au survol pour afficher l'adresse du batiment",
                "tool": "cmp_set_tooltip",
                "tool_args_partial": {"field": "adresse"},
                "requires_layer_selection": True,
            },
            {
                "id": "cite_tri_dgpr",
                "label": "Citer la source TRI DGPR",
                "prompt": "Cite la source TRI (Territoires Risque Inondation) DGPR",
                "tool": "cmp_set_source_citation",
                "tool_args": {"datasource_id": "tri_limites"},
            },
            {
                "id": "basemap_ign",
                "label": "Passer au fond Plan IGN v2",
                "prompt": "Change le fond de carte pour le Plan IGN v2 officiel francais",
                "tool": "cmp_set_basemap",
                "tool_args": {"basemap_id": "plan-ign-v2"},
            },
        ],
        # Autres kinds ajoutes iter 2 (kpi_grid, chart, etc.)
    }

    suggestions = _SUGGESTIONS_BY_KIND.get(kind, [])
    return {
        "suggestions": suggestions,
        "component_kind": kind,
        "cid": cid,
        "sid": sid,
        "assistant_available": True,
    }


@app.post("/studies/{sid}/components/{cid}/assist/action")
async def component_assist_action_endpoint(
    sid: str, cid: str,
    payload: dict,
    user: dict = Depends(auth.get_current_user),
):
    """Sprint 2.5 V2.5 A2c — Execute une action tool cmp_* directement.

    Endpoint bornΘ : uniquement les tools cmp_* whitelistes profil
    component_assist.yaml. Enforcement du scope composant : cid dans URL
    matche automatiquement les tools cmp_*.

    Payload :
    - `tool`: nom du tool cmp_* (ex: "cmp_set_zone")
    - `args`: dict d'args pour le tool (sid + cid injectes)

    Format reponse :
    {"success": true, "result": {...}, "version_num_after": N}
    ou HTTP 400 si tool hors whitelist / args invalides.
    HTTP 409 si conflict OCC (Marie a modifie entre-temps).

    Sprint 2.5 POC minimal : appel synchrone tool + return result. Iter 2 :
    audit_trail redaction + streaming SSE pour tools longs + retry LLM.
    """
    _check_components_enabled()
    from hub import components as comp_mod
    latest = await comp_mod.get_component_latest(cid)
    if not latest or latest["sid"] != sid:
        raise HTTPException(404, "Composant introuvable")
    if latest["owner"] != user["username"]:
        raise HTTPException(403, "Pas owner")

    tool = payload.get("tool")
    args = payload.get("args") or {}

    # Whitelist stricte : uniquement les tools cmp_* Sprint 2.5 A2b.
    ALLOWED_TOOLS = {
        "cmp_get_context",
        "cmp_set_tooltip",
        "cmp_set_zone",
        "cmp_set_source_citation",
        "cmp_add_layer",
    }
    if tool not in ALLOWED_TOOLS:
        raise HTTPException(
            400,
            f"Tool '{tool}' non autorise pour l'assistant composant. "
            f"Tools disponibles : {sorted(ALLOWED_TOOLS)}",
        )

    # Sprint 2.5 V1.14.1 hotfix : le hub s'auto-appelle sans HUB_URL configure.
    # Refacto : appel direct des modules hub locaux (components + PVC) au lieu
    # de passer par les tools cmp_* qui font HTTP self-call.
    #
    # Pattern identique a update_component_endpoint mais scope au cid de l'URL.

    from hub import components as comp_mod

    # Lecture manifest actuel via comp_mod (pas d'HTTP)
    latest = await comp_mod.get_component_latest(cid)
    if not latest or latest["sid"] != sid:
        raise HTTPException(404, "Composant introuvable")
    if latest["owner"] != user["username"]:
        raise HTTPException(403, "Pas owner")

    # Lecture manifest depuis PVC via pod_code (comme _build_interactive_map_ctx)
    import base64 as _b64s
    import json as _json2

    try:
        stdout = await _execute_python_in_workspace(
            user["username"],
            comp_mod.read_component_manifest_pod_code(sid, cid),
        )
    except Exception as exc:
        log.warning("assist/action read manifest : %s", exc)
        raise HTTPException(500, "Lecture manifest impossible")

    manifest = None
    if "COMPONENT_READ_OK" in stdout:
        try:
            b64 = stdout.split("b64=", 1)[1].split()[0].strip()
            manifest = _json2.loads(_b64s.b64decode(b64).decode())
        except Exception as exc:
            log.warning("assist/action parse manifest : %s", exc)
    if not manifest:
        raise HTTPException(500, "Manifest illisible")

    # Applique la mutation Component.params en fonction du tool
    params = dict(manifest.get("params") or {})

    if tool == "cmp_get_context":
        return {
            "success": True,
            "tool": tool,
            "result": {
                "kind": manifest.get("kind"),
                "title": manifest.get("title"),
                "params": params,
            },
        }

    if tool == "cmp_set_source_citation":
        datasource_id = args.get("datasource_id")
        if not datasource_id:
            raise HTTPException(400, "cmp_set_source_citation requiert 'datasource_id'")
        params["datasource_id"] = datasource_id
        # Auto-fill params.source depuis le catalog (hub.catalog_datasources)
        from hub.catalog_datasources import get_label
        label = get_label(datasource_id)
        if label:
            params["source"] = label

    elif tool == "cmp_set_zone":
        kind = args.get("kind")
        if kind not in {"commune", "manual", "study"}:
            raise HTTPException(400, "cmp_set_zone.kind ∈ {commune, manual, study}")
        zone = {"kind": kind}
        for k in ("insee", "buffer_km", "center_lat", "center_lng", "zoom"):
            if args.get(k) is not None:
                zone[k] = args[k]
        params["zone"] = zone

    elif tool == "cmp_set_tooltip":
        layer_id_ref = args.get("layer_id_ref")
        field = args.get("field")
        if not layer_id_ref or not field:
            raise HTTPException(400, "cmp_set_tooltip requiert 'layer_id_ref' et 'field'")
        overrides = list(params.get("layers_override") or [])
        found = False
        for ov in overrides:
            if ov.get("layer_id_ref") == layer_id_ref:
                ov["tooltip_field"] = field
                found = True
                break
        if not found:
            overrides.append({"layer_id_ref": layer_id_ref, "tooltip_field": field})
        params["layers_override"] = overrides

    elif tool == "cmp_add_layer":
        scene_layer_id = args.get("scene_layer_id")
        if not scene_layer_id:
            raise HTTPException(400, "cmp_add_layer requiert 'scene_layer_id'")
        overrides = list(params.get("layers_override") or [])
        ov = None
        for existing_ov in overrides:
            if existing_ov.get("layer_id_ref") == scene_layer_id:
                ov = existing_ov
                break
        if ov is None:
            ov = {"layer_id_ref": scene_layer_id}
            overrides.append(ov)
        ov["visible"] = args.get("visible", True)
        ov["opacity"] = args.get("opacity", 1.0)
        if args.get("z_index") is not None:
            ov["z_index"] = args["z_index"]
        if args.get("tooltip_field"):
            ov["tooltip_field"] = args["tooltip_field"]
        params["layers_override"] = overrides

    # Ecrire manifest patched + insert nouvelle version (INSERT-only pattern
    # identique update_component_endpoint main.py:4590)
    new_manifest = {**manifest, "params": params, "sid": sid, "id": cid}

    from hub.models import Component
    try:
        component = Component.model_validate(new_manifest)
    except Exception as exc:
        raise HTTPException(422, f"Manifest invalide apres mutation : {exc}")

    # Bump version pour insert INSERT-only versioning
    component.version = int(latest.get("version_num", 1)) + 1

    # Ecrire nouveau manifest sur PVC (overwrite)
    content_json = _json2.dumps(
        component.model_dump(mode="json"), ensure_ascii=False, indent=2,
    )
    try:
        write_code = comp_mod.write_component_manifest_pod_code(sid, cid, content_json)
        write_out = await _execute_python_in_workspace(user["username"], write_code)
        if "COMPONENT_WRITE_OK" not in write_out:
            raise Exception(f"Ecriture PVC failed: {write_out[:200]}")
    except Exception as exc:
        log.warning("assist/action write manifest : %s", exc)
        raise HTTPException(500, f"Ecriture manifest : {exc}")

    # INSERT-only versioning DB via comp_mod.insert_component
    try:
        await comp_mod.insert_component(
            component=component,
            owner=user["username"],
            sid=sid,
            file_path=comp_mod.component_manifest_path(sid, cid),
            size_bytes=len(content_json.encode("utf-8")),
            previous_hash=latest.get("content_hash", ""),
        )
    except Exception as exc:
        log.warning("assist/action insert DB : %s", exc)
        raise HTTPException(500, f"Insert DB : {exc}")

    return {
        "success": True,
        "tool": tool,
        "cid": cid,
        "version_num_after": component.version,
    }


@app.get("/studies/{sid}/components/{cid}/history")
async def component_history_endpoint(
    sid: str, cid: str,
    user: dict = Depends(auth.get_current_user),
):
    """Historique des versions du composant (audit trail INSERT-only)."""
    _check_components_enabled()
    from hub import components as comp_mod
    history = await comp_mod.get_component_history(cid)
    if not history:
        raise HTTPException(404, "Composant introuvable")
    if history[0]["sid"] != sid:
        raise HTTPException(404, "Composant pas dans cette étude")
    return history


@app.get("/catalog/datasources")
async def catalog_datasources_endpoint(
    category: str | None = None,
    user: dict = Depends(auth.get_current_user),
):
    """Sprint 1 V1.13 P0d — Catalogue des sources de donnees Strate-aligned.

    Use case : autocomplete dans le form Marie InteractiveMapForm pour
    eviter le drift des citations (avant : TextField libre `source: str`).

    Query param optionnel : `category` (referentiel/risque/fiscalite/
    environnement/demographie/mobilite). Sans param, retourne tout.

    Format reponse :
    {
        "datasources": [
            {
                "id": "bdtopo_batiments",
                "label": "BD TOPO 2024 — Batiments — IGN — Licence Ouverte 2.0",
                "short_label": "BD TOPO 2024 (batiments)",
                "authority": "IGN",
                "licence": "Licence Ouverte 2.0",
                "category": "referentiel",
                "url": "https://geoservices.ign.fr/bdtopo"
            },
            ...
        ],
        "categories": ["referentiel", "risque", ...]
    }
    """
    from hub.catalog_datasources import list_datasources, list_categories
    return {
        "datasources": list_datasources(category),
        "categories": list_categories(),
    }


@app.get("/studies/{sid}/components/{cid}/source_layers")
async def component_source_layers_endpoint(
    sid: str, cid: str,
    user: dict = Depends(auth.get_current_user),
):
    """Sprint 1 V1.13 P0b — Retourne la liste des layers du scene_manifest
    referenced par le composant interactive_map.

    Marie utilise ce endpoint dans le sub-form Layers pour :
    - Voir la liste des layers disponibles (sans deviner les layer_id_ref)
    - Voir les properties_keys disponibles (sans lire le GeoJSON)
    - Configurer un layers_override par layer dans InteractiveMapParams V1.13

    Format reponse :
    {
        "layers": [
            {
                "id": "batiments_bdtopo",
                "name": "Batiments BD TOPO 2024",
                "geometry_type": "polygon",
                "n_features": 14270,
                "properties_keys": ["id", "hauteur", "usage_1", "adresse", ...],
            },
            ...
        ],
        "scene_hash": "abc123...",
        "scene_pid": "xyz789...",
    }

    404 si composant pas un interactive_map ou pas de source.scene_hash.
    """
    _check_components_enabled()
    from hub import components as comp_mod
    import base64 as _b64, json as _json

    latest = await comp_mod.get_component_latest(cid)
    if not latest or latest["sid"] != sid:
        raise HTTPException(404, "Composant introuvable")
    if latest["owner"] != user["username"]:
        raise HTTPException(403, "Pas owner")

    # Lire le manifest
    try:
        stdout = await _execute_python_in_workspace(
            user["username"],
            comp_mod.read_component_manifest_pod_code(sid, cid),
        )
    except Exception as exc:
        log.warning("source_layers read manifest : %s", exc)
        raise HTTPException(500, "Lecture manifest impossible")

    manifest = None
    if "COMPONENT_READ_OK" in stdout:
        try:
            b64 = stdout.split("b64=", 1)[1].split()[0].strip()
            manifest = _json.loads(_b64.b64decode(b64).decode())
        except Exception as exc:
            log.warning("source_layers parse manifest : %s", exc)
    if not manifest:
        raise HTTPException(404, "Manifest illisible")

    if manifest.get("kind") != "interactive_map":
        raise HTTPException(400, "Seuls les interactive_map ont des source_layers")

    source = manifest.get("source", {}) or {}
    params = manifest.get("params", {}) or {}
    scene_hash = source.get("scene_hash") or params.get("scene_hash")
    scene_pid = source.get("pid") or params.get("pid")

    # Resilience source.pid (meme logique que _build_interactive_map_ctx) :
    # si scene_hash defini mais pas pid, fallback sur le 1er projet de l'etude.
    if scene_hash and not scene_pid:
        try:
            _projects = await studies.list_projects(sid)
            if _projects:
                scene_pid = _projects[0]["pid"]
        except Exception as exc:
            log.warning("source_layers list_projects fallback : %s", exc)

    if not scene_hash or not scene_pid:
        # Fallback : si layers inline dans params (legacy), les retourner
        layers_inline = params.get("layers", [])
        return {
            "layers": [{
                "id": l.get("id", f"layer_{i}"),
                "name": l.get("name", f"Layer {i}"),
                "geometry_type": l.get("geometry_type", "unknown"),
                "n_features": l.get("n_features"),
                "properties_keys": [],
            } for i, l in enumerate(layers_inline)],
            "scene_hash": scene_hash,
            "scene_pid": scene_pid,
        }

    # Lire le scene_manifest
    try:
        scene_code = studies.read_scene_manifest_pod_code(sid, scene_pid)
        scene_out = await _execute_python_in_workspace(user["username"], scene_code)
        if "SCENE_MANIFEST_READ_OK" not in scene_out:
            raise HTTPException(500, "Scene manifest illisible")
        scene_b64 = scene_out.split("b64=", 1)[1].split()[0].strip()
        scene_obj = _json.loads(_b64.b64decode(scene_b64).decode())
    except HTTPException:
        raise
    except Exception as exc:
        log.warning("source_layers read scene_manifest : %s", exc)
        raise HTTPException(500, f"Erreur lecture scene_manifest : {exc}")

    scene_layers = scene_obj.get("layers", []) or []
    result_layers = []
    for i_l, l in enumerate(scene_layers):
        lid = l.get("id", f"layer_{i_l}")
        # Properties keys : on tente de lire le premier feature du GeoJSON
        properties_keys: list[str] = []
        geojson_inline = l.get("geojson")
        geojson_path = l.get("geojson_path")
        try:
            features = []
            if geojson_inline:
                features = (geojson_inline.get("features") or [])[:1]
            elif geojson_path:
                # Lecture sample : on lit le fichier complet mais on n'en
                # extrait que la 1ere feature. Coute 1 read pod par layer
                # affiche dans le form, acceptable (Marie n'edite qu'un comp
                # a la fois).
                gj_code = studies.read_scene_layer_geojson_pod_code(sid, scene_pid, lid)
                gj_out = await _execute_python_in_workspace(user["username"], gj_code)
                if "SCENE_LAYER_READ_OK" in gj_out:
                    gj_b64 = gj_out.split("b64=", 1)[1].split()[0].strip()
                    geojson = _json.loads(_b64.b64decode(gj_b64).decode())
                    features = (geojson.get("features") or [])[:1]
            if features:
                props = features[0].get("properties") or {}
                properties_keys = sorted(props.keys())
        except Exception as exc:
            log.warning("source_layers properties_keys %s : %s", lid, exc)

        result_layers.append({
            "id": lid,
            "name": l.get("name", lid),
            "geometry_type": l.get("geometry_type", "unknown"),
            "n_features": l.get("n_features"),
            "properties_keys": properties_keys,
        })

    return {
        "layers": result_layers,
        "scene_hash": scene_hash,
        "scene_pid": scene_pid,
    }


@app.get("/studies/{sid}/components/{cid}/render", response_class=HTMLResponse)
async def render_component_endpoint(
    sid: str, cid: str,
    user: dict = Depends(auth.get_current_user),
):
    """Rendu HTML preview du composant standalone (iframe-embeddable).

    Fix Reviewer-VagueA 2026-06-29 : utilise désormais le helper unifié
    `_pre_render_component_html` (D-QGIS-008) pour cohérence avec
    `_render_assembly_html` (pré-rendu inline storymap) et
    `publish_component_endpoint` (S3 publish). Supporte tous les kinds
    livrés Vague A : narrative_text, kpi_badge, legend, interactive_map,
    chart, data_table.
    """
    _check_components_enabled()
    from hub import components as comp_mod

    latest = await comp_mod.get_component_latest(cid)
    if not latest or latest["sid"] != sid:
        raise HTTPException(404, "Composant introuvable")

    # Lire manifest depuis PVC
    try:
        stdout = await _execute_python_in_workspace(
            user["username"],
            comp_mod.read_component_manifest_pod_code(sid, cid),
        )
    except Exception as exc:
        log.error("render: read manifest failed : %s", exc)
        raise HTTPException(503, "Workspace indisponible")

    import base64, json as _json
    if "COMPONENT_READ_OK" not in stdout:
        raise HTTPException(404, "Manifest PVC introuvable")
    try:
        b64 = stdout.split("b64=", 1)[1].split()[0].strip()
        manifest = _json.loads(base64.b64decode(b64).decode())
    except Exception as exc:
        raise HTTPException(500, f"Parse manifest : {exc}")

    # Helper unifié (D-QGIS-008) — mêmes templates partials que storymap inline.
    try:
        body_html = await _pre_render_component_html(
            manifest, sid, user["username"], cid,
        )
    except Exception as exc:
        import traceback as _tb
        log.error("render_component %s/%s : %s", sid, cid, exc)
        raise HTTPException(
            status_code=500,
            detail={
                "error_class": type(exc).__name__,
                "error_message": str(exc)[:300],
                "traceback_tail": _tb.format_exc().splitlines()[-5:],
            },
        )

    # Envelope HTML standalone DSFR-inspire (mêmes CDN que storymap_dsfr).
    title = manifest.get("title", cid)
    standalone_html = (
        "<!DOCTYPE html><html lang='fr'><head>"
        "<meta charset='UTF-8'>"
        f"<title>{title}</title>"
        "<meta name='viewport' content='width=device-width,initial-scale=1'>"
        "<link rel='stylesheet' href='https://unpkg.com/maplibre-gl@4/dist/maplibre-gl.css'>"
        "<script src='https://unpkg.com/maplibre-gl@4/dist/maplibre-gl.js'></script>"
        "<script src='https://unpkg.com/chart.js@4.4.0/dist/chart.umd.js'></script>"
        "<style>body{margin:0;font-family:Marianne,system-ui,sans-serif;background:#f6f6f6;padding:20px}</style>"
        "</head><body>"
        f"{body_html}"
        "</body></html>"
    )
    return HTMLResponse(content=standalone_html)


@app.get("/studies/{sid}/components/{cid}/render_legacy", response_class=HTMLResponse)
async def _render_component_endpoint_legacy(
    sid: str, cid: str,
    user: dict = Depends(auth.get_current_user),
):
    """LEGACY (sera supprimé v1.7) — rendu via templates Jinja2 standalone
    `kpi_badge.html.j2` / `interactive_map.html.j2` etc. Conservé pour
    debug si pré-rendu unifié casse. Cf. _pre_render_component_html.
    """
    _check_components_enabled()
    if not _jinja:
        raise HTTPException(503, "Templates Jinja2 non disponibles")
    from hub import components as comp_mod
    from hub import maplibre_style_mapper as msm

    latest = await comp_mod.get_component_latest(cid)
    if not latest or latest["sid"] != sid:
        raise HTTPException(404, "Composant introuvable")

    try:
        stdout = await _execute_python_in_workspace(
            user["username"],
            comp_mod.read_component_manifest_pod_code(sid, cid),
        )
    except Exception as exc:
        log.error("render_legacy: read manifest failed : %s", exc)
        raise HTTPException(503, "Workspace indisponible")

    import base64, json as _json
    if "COMPONENT_READ_OK" not in stdout:
        raise HTTPException(404, "Manifest PVC introuvable")
    try:
        b64 = stdout.split("b64=", 1)[1].split()[0].strip()
        manifest = _json.loads(base64.b64decode(b64).decode())
    except Exception as exc:
        raise HTTPException(500, f"Parse manifest : {exc}")

    kind = manifest.get("kind", "interactive_map")
    template_map = {
        "interactive_map": "maplibre_renderer/interactive_map.html.j2",
        "legend":          "maplibre_renderer/legend.html.j2",
        "kpi_badge":       "maplibre_renderer/kpi_badge.html.j2",
        "narrative_text":  "maplibre_renderer/narrative_text.html.j2",
    }
    template_name = template_map.get(kind)
    if not template_name:
        raise HTTPException(
            501,
            f"Kind '{kind}' non supporté par le legacy renderer. "
            f"Kinds dispo : {list(template_map.keys())}. "
            f"Utiliser /render (unifié D-QGIS-008) à la place.",
        )

    if not _maplibre_jinja:
        raise HTTPException(503, "Maplibre Jinja2 indisponible")

    # Préparer context selon kind
    ctx: dict = {"component": manifest}
    params = manifest.get("params", {})

    if kind == "interactive_map":
        # Chercher Scene Manifest du projet pour générer maplibre_layers
        source = manifest.get("source", {})
        pid = source.get("pid")
        sm_data = None
        if pid:
            sm_latest = await studies.scene_manifest_get_latest(pid)
            if sm_latest:
                try:
                    sm_stdout = await _execute_python_in_workspace(
                        user["username"],
                        studies.read_scene_manifest_pod_code(sid, pid),
                    )
                    if "SCENE_MANIFEST_READ_OK" in sm_stdout:
                        sm_b64 = sm_stdout.split("b64=", 1)[1].split()[0].strip()
                        sm_data = _json.loads(base64.b64decode(sm_b64).decode())
                except Exception as exc:
                    log.warning("read scene_manifest for render: %s", exc)
        if sm_data:
            ctx["maplibre_layers"] = msm.manifest_to_maplibre_layers(sm_data)
        else:
            ctx["maplibre_layers"] = []
        ctx["data_inline"] = params.get("data_inline")
        ctx["data_url"] = source.get("data_url", "")
        ctx["basemap_url"] = params.get("basemap_url")

    elif kind == "legend":
        ctx["legend_entries"] = params.get("entries", [])

    elif kind == "kpi_badge":
        ctx["kpi"] = params.get("kpi", {
            "value": "—", "label": manifest.get("title", "KPI"),
        })

    elif kind == "narrative_text":
        ctx["markdown_content"] = params.get("markdown", "")

    # Sprint Composants Phase 2 fix : utiliser Environment Jinja2 direct
    # au lieu de Jinja2Templates.TemplateResponse (qui exige Request object).
    try:
        tpl_short = template_name.replace("maplibre_renderer/", "")
        tpl = _maplibre_jinja.get_template(tpl_short)
        html = tpl.render(**ctx)
        return HTMLResponse(content=html)
    except Exception as exc:
        import traceback as _tb
        log.error("render_component %s/%s/%s : %s", sid, cid, kind, exc)
        tb_str = _tb.format_exc()
        # Renvoyer l'erreur structurée pour debug (temporaire Phase 2)
        raise HTTPException(
            status_code=500,
            detail={
                "error_class": type(exc).__name__,
                "error_message": str(exc)[:300],
                "template_tried": tpl_short,
                "ctx_keys": list(ctx.keys()),
                "traceback_tail": tb_str.splitlines()[-5:] if tb_str else [],
            },
        )


@app.put("/studies/{sid}/components/{cid}")
async def update_component_endpoint(
    sid: str, cid: str, request: Request,
    user: dict = Depends(auth.get_current_user),
):
    """Vague E1 (D-QGIS-009, 2026-06-29) : update versionne composant existant.

    Pattern INSERT-only identique update_assembly_endpoint (Phase 4b) :
    chaque update insere une nouvelle row components_index avec version_num+1
    et previous_hash = ancien content_hash. Le cid est preserve, audit trail
    complet, et le manifest PVC est ecrase.

    Body : Component manifest JSON complet OU partiel.
    - Si complet : remplace le manifest (id + sid preserves).
    - Si partiel : merge sur le manifest existant (top-level fields remplaces).

    Auto-fill provenance.scene_hash_at_creation si source.scene_hash present
    et provenance vide (pattern Phase 4b create_component_endpoint).

    Usage agent : pour modifier params/source/title d'un composant sans
    devoir le supprimer et le recreer (preserve cid, audit trail, refs
    depuis assemblies).
    """
    _check_components_enabled()
    if not _STUDIES_AVAILABLE:
        raise HTTPException(503, "Module studies indisponible")
    s = await studies.get_study(sid, user["username"])
    if not s:
        raise HTTPException(404, "Étude introuvable")

    from hub.models import Component
    from hub import components as comp_mod

    # Verifier que le composant existe et appartient au scope
    latest = await comp_mod.get_component_latest(cid)
    if not latest:
        raise HTTPException(404, "Composant introuvable")
    if latest["sid"] != sid or latest["owner"] != user["username"]:
        raise HTTPException(403, "Pas owner ou hors scope etude")

    try:
        payload = await request.json()
    except Exception:
        raise HTTPException(400, "Body JSON invalide")

    # Sprint 1 Vague E3 fix D2 : OCC `version_num_source` aussi pour Component
    # (identique au pattern update_assembly_endpoint livre v1.7.1).
    # Si l'editeur BlockNote envoie version_num_source, on verifie qu'aucun
    # autre processus (agent IA via chat) n'a modifie le composant entre temps.
    version_num_source = payload.pop("version_num_source", None)
    if version_num_source is not None:
        current_version = latest.get("version_num", 1)
        try:
            source_version = int(version_num_source)
        except (TypeError, ValueError):
            raise HTTPException(
                400,
                "version_num_source doit etre un entier",
            )
        if source_version != int(current_version):
            raise HTTPException(
                409,
                {
                    "error": "concurrent_update",
                    "message": (
                        f"Conflit : le composant a ete modifie par un autre processus "
                        f"(version actuelle {current_version}, source {source_version}). "
                        f"Recharger pour voir les modifications les plus recentes."
                    ),
                    "current_version_num": current_version,
                    "source_version_num": source_version,
                },
            )

    # Force scope + identite stable
    payload["sid"] = sid
    payload["id"] = cid

    # Si l'agent envoie un manifest partiel (sans kind par exemple), lire
    # l'existant et merger top-level. Le kind doit etre present pour valider.
    if not payload.get("kind") or not payload.get("source") or not payload.get("rendering"):
        try:
            existing_code = comp_mod.read_component_manifest_pod_code(sid, cid)
            existing_out = await _execute_python_in_workspace(user["username"], existing_code)
            if "COMPONENT_READ_OK" not in existing_out:
                raise HTTPException(503, "Lecture manifest existant impossible")
            import base64 as _b64
            ex_b64 = existing_out.split("b64=", 1)[1].split()[0].strip()
            import json as _json2
            existing_manifest = _json2.loads(_b64.b64decode(ex_b64).decode())
            # Merge : top-level fields du payload override existing.
            # Audit fix v1.7.1 P0 #5 : skip les keys avec valeur None pour
            # eviter d'ecraser des champs existants (notamment layout pour
            # assembly, params pour component) avec un null que JSON.stringify
            # cote client peut emettre involontairement.
            for k, v in payload.items():
                if v is None:
                    continue
                existing_manifest[k] = v
            payload = existing_manifest
        except HTTPException:
            raise
        except Exception as exc:
            log.warning("merge existing component manifest : %s", exc)

    try:
        comp = Component.model_validate(payload)
    except Exception as exc:
        raise HTTPException(422, f"Validation Pydantic : {exc}")

    # Auto-fill provenance.scene_hash_at_creation (pattern Phase 4b)
    # Si source.scene_hash present mais provenance vide, propage automatiquement.
    try:
        src_scene_hash = getattr(comp.source, "scene_hash", None) if comp.source else None
        if src_scene_hash and not comp.provenance.scene_hash_at_creation:
            comp.provenance.scene_hash_at_creation = src_scene_hash
    except Exception:
        pass

    # Ecrire nouveau manifest sur PVC (overwrite : la version PVC suit DB)
    import json as _json
    content_json = _json.dumps(
        comp.model_dump(mode="json"), ensure_ascii=False, indent=2,
    )
    try:
        await _execute_python_in_workspace(
            user["username"],
            comp_mod.write_component_manifest_pod_code(sid, comp.id, content_json),
        )
    except Exception as exc:
        log.warning("write updated component manifest : %s", exc)

    # INSERT nouvelle version (INSERT-only versioning)
    new_version = (latest.get("version_num") or 1) + 1
    comp.version = new_version
    file_path = comp_mod.component_manifest_path(sid, comp.id)
    size_bytes = len(content_json.encode("utf-8"))
    rowid = await comp_mod.insert_component(
        component=comp, owner=user["username"], sid=sid,
        file_path=file_path, size_bytes=size_bytes,
        previous_hash=latest.get("content_hash") or "",
    )
    return {
        "id": comp.id, "rowid": rowid, "kind": comp.kind, "title": comp.title,
        "classification": comp.classification, "version_num": new_version,
        "manifest_url":   f"/studies/{sid}/components/{comp.id}",
        "render_url":     f"/studies/{sid}/components/{comp.id}/render",
        "publish_url":    f"/studies/{sid}/components/{comp.id}/publish",
    }


@app.delete("/studies/{sid}/components/{cid}", status_code=status.HTTP_204_NO_CONTENT)
async def archive_component_endpoint(
    sid: str, cid: str,
    user: dict = Depends(auth.get_current_user),
):
    """Soft delete : status='archived'. INSERT new row archived (audit OK)."""
    _check_components_enabled()
    from hub import components as comp_mod
    rowid = await comp_mod.archive_component(cid, user["username"])
    if not rowid:
        raise HTTPException(404, "Composant introuvable ou pas owner")
    return None


# ── Sprint Composants Phase 3 (2026-06-25) : endpoints ASSEMBLAGES ────────────
# Feature flag ASSEMBLIES_ENABLED. Implémente CRUD + render + publish S3 avec
# audit_chain transverse obligatoire au publish.

_ASSEMBLIES_ENABLED = os.getenv("ASSEMBLIES_ENABLED", "true").lower() == "true"


def _check_assemblies_enabled():
    if not _ASSEMBLIES_ENABLED:
        raise HTTPException(
            status_code=503,
            detail="Feature ASSEMBLIES_ENABLED désactivée. Set env var "
                   "ASSEMBLIES_ENABLED=true pour activer.",
        )


@app.get("/studies/{sid}/assemblies")
async def list_assemblies_endpoint(
    sid: str, kind: str | None = None,
    user: dict = Depends(auth.get_current_user),
):
    """Liste les assemblages de l'étude (latest version par aid)."""
    _check_assemblies_enabled()
    if not _STUDIES_AVAILABLE:
        raise HTTPException(503, "Module studies indisponible")
    s = await studies.get_study(sid, user["username"])
    if not s:
        raise HTTPException(404, "Étude introuvable")
    from hub import assemblies as asm_mod
    return await asm_mod.list_assemblies(sid=sid, kind=kind, owner=user["username"])


@app.put("/studies/{sid}/assemblies/{aid}")
async def update_assembly_endpoint(
    sid: str, aid: str, request: Request,
    user: dict = Depends(auth.get_current_user),
):
    """Phase 4b (2026-06-28) : update versionne d'un assemblage existant.

    Pattern INSERT-only : chaque update insere une nouvelle row
    assemblies_index avec version_num+1 et previous_hash = ancien
    content_hash. L'aid est preserve, l'audit trail complet.

    Body : Assembly manifest JSON complet OU partiel.
    - Si complet : remplace le manifest (id + sid preserves).
    - Si partiel : merge sur le manifest existant (top-level fields
      remplaces, layout.sections ETENDU si payload contient `_append_sections`).

    Usage agent : pour ajouter une section a une storymap existante sans
    perdre les composants deja la, envoyer le manifest complet avec layout.sections
    enrichi des sections existantes + nouvelles. C'est plus simple a controler
    cote agent que la logique de merge.
    """
    _check_assemblies_enabled()
    if not _STUDIES_AVAILABLE:
        raise HTTPException(503, "Module studies indisponible")
    s = await studies.get_study(sid, user["username"])
    if not s:
        raise HTTPException(404, "Étude introuvable")

    from hub.models import Assembly
    from hub import assemblies as asm_mod

    # Verifier que l'assemblage existe et appartient au scope
    latest = await asm_mod.get_assembly_latest(aid)
    if not latest:
        raise HTTPException(404, "Assemblage introuvable")
    if latest["sid"] != sid or latest["owner"] != user["username"]:
        raise HTTPException(403, "Pas owner ou hors scope etude")

    try:
        payload = await request.json()
    except Exception:
        raise HTTPException(400, "Body JSON invalide")

    # Vague E2 Commit H1 (D-QGIS-010) : optimistic concurrency control
    # Si l'editeur BlockNote envoie version_num_source (charge au debut
    # de l'edition), on verifie qu'aucun autre processus (agent IA chat
    # via workflow Vague E1) n'a modifie l'assembly entre temps.
    # En cas de stale -> 409 Conflict + l'UI BlockNote propose de recharger.
    version_num_source = payload.pop("version_num_source", None)
    if version_num_source is not None:
        current_version = latest.get("version_num", 1)
        if int(version_num_source) != int(current_version):
            raise HTTPException(
                409,
                {
                    "error": "concurrent_update",
                    "message": (
                        f"Conflit : l'assembly a ete modifie par un autre processus "
                        f"(version actuelle {current_version}, source {version_num_source}). "
                        f"Recharger pour voir les modifications les plus recentes."
                    ),
                    "current_version_num": current_version,
                    "source_version_num": int(version_num_source),
                },
            )

    # Force scope + identite stable
    payload["sid"] = sid
    payload["id"] = aid
    # Si l'agent envoie un manifest complet, on le valide tel quel.
    # Sinon il faut lire l'existant et merger.
    if not payload.get("layout"):
        # Manifest partiel - lire l'existant pour completer
        try:
            existing_code = asm_mod.read_assembly_manifest_pod_code(sid, aid)
            existing_out = await _execute_python_in_workspace(user["username"], existing_code)
            if "ASSEMBLY_READ_OK" not in existing_out:
                raise HTTPException(503, "Lecture manifest existant impossible")
            import base64 as _b64
            ex_b64 = existing_out.split("b64=", 1)[1].split()[0].strip()
            import json as _json2
            existing_manifest = _json2.loads(_b64.b64decode(ex_b64).decode())
            # Merge : top-level fields du payload override existing.
            # Audit fix v1.7.1 P0 #5 : skip les keys avec valeur None pour
            # eviter d'ecraser des champs existants (notamment layout pour
            # assembly, params pour component) avec un null que JSON.stringify
            # cote client peut emettre involontairement.
            for k, v in payload.items():
                if v is None:
                    continue
                existing_manifest[k] = v
            payload = existing_manifest
        except HTTPException: raise
        except Exception as exc:
            log.warning("merge existing manifest : %s", exc)

    try:
        asm = Assembly.model_validate(payload)
    except Exception as exc:
        raise HTTPException(422, f"Validation Pydantic : {exc}")

    # Écrire nouveau manifest sur PVC (overwrite : la version PVC suit DB)
    import json as _json
    content_json = _json.dumps(
        asm.model_dump(mode="json"), ensure_ascii=False, indent=2,
    )
    try:
        await _execute_python_in_workspace(
            user["username"],
            asm_mod.write_assembly_manifest_pod_code(sid, asm.id, content_json),
        )
    except Exception as exc:
        log.warning("write updated assembly manifest : %s", exc)

    # INSERT nouvelle version. asm.version doit etre incremente cote payload
    # OU on force ici. Le hub remplit version_num via le model Assembly,
    # mais c'est plus sur de surcharger depuis la DB.
    new_version = (latest.get("version_num") or 1) + 1
    asm.version = new_version
    file_path = asm_mod.assembly_manifest_path(sid, asm.id)
    rowid = await asm_mod.insert_assembly(
        assembly=asm, owner=user["username"], file_path=file_path,
        previous_hash=latest.get("content_hash") or "",
    )
    return {
        "id": asm.id, "rowid": rowid, "kind": asm.kind, "title": asm.title,
        "audience": asm.audience, "version_num": new_version,
        "manifest_url":   f"/studies/{sid}/assemblies/{asm.id}",
        "render_url":     f"/studies/{sid}/assemblies/{asm.id}/render",
        "publish_url":    f"/studies/{sid}/assemblies/{asm.id}/publish",
    }


@app.post("/studies/{sid}/assemblies", status_code=status.HTTP_201_CREATED)
async def create_assembly_endpoint(
    sid: str, request: Request,
    user: dict = Depends(auth.get_current_user),
):
    """Crée un nouvel assemblage rattaché à l'étude (sid scope)."""
    _check_assemblies_enabled()
    if not _STUDIES_AVAILABLE:
        raise HTTPException(503, "Module studies indisponible")
    s = await studies.get_study(sid, user["username"])
    if not s:
        raise HTTPException(404, "Étude introuvable")

    from hub.models import Assembly
    from hub import assemblies as asm_mod

    try:
        payload = await request.json()
    except Exception:
        raise HTTPException(400, "Body JSON invalide")

    # Force le sid au scope étude actif
    payload["sid"] = sid
    if not payload.get("id"):
        payload["id"] = studies._new_id()

    try:
        asm = Assembly.model_validate(payload)
    except Exception as exc:
        raise HTTPException(422, f"Validation Pydantic : {exc}")

    # Écrire manifest sur PVC
    import json as _json
    content_json = _json.dumps(
        asm.model_dump(mode="json"), ensure_ascii=False, indent=2,
    )
    try:
        await _execute_python_in_workspace(
            user["username"],
            asm_mod.write_assembly_manifest_pod_code(sid, asm.id, content_json),
        )
    except Exception as exc:
        log.warning("write assembly manifest : %s", exc)

    file_path = asm_mod.assembly_manifest_path(sid, asm.id)
    rowid = await asm_mod.insert_assembly(
        assembly=asm, owner=user["username"], file_path=file_path,
    )
    return {
        "id": asm.id, "rowid": rowid, "kind": asm.kind, "title": asm.title,
        "audience": asm.audience,
        "manifest_url":   f"/studies/{sid}/assemblies/{asm.id}",
        "render_url":     f"/studies/{sid}/assemblies/{asm.id}/render",
        "publish_url":    f"/studies/{sid}/assemblies/{asm.id}/publish",
    }


@app.get("/studies/{sid}/assemblies/{aid}")
async def get_assembly_endpoint(
    sid: str, aid: str,
    user: dict = Depends(auth.get_current_user),
):
    """Manifest assemblage + metadata DB."""
    _check_assemblies_enabled()
    from hub import assemblies as asm_mod
    latest = await asm_mod.get_assembly_latest(aid)
    if not latest or latest["sid"] != sid:
        raise HTTPException(404, "Assemblage introuvable")
    if latest["owner"] != user["username"]:
        raise HTTPException(403, "Pas owner (ACL Phase 3 ultérieure)")

    # Lire manifest depuis PVC
    try:
        stdout = await _execute_python_in_workspace(
            user["username"],
            asm_mod.read_assembly_manifest_pod_code(sid, aid),
        )
    except Exception as exc:
        log.warning("read assembly manifest : %s", exc)
        stdout = ""

    import base64, json as _json
    manifest_data = None
    if "ASSEMBLY_READ_OK" in stdout:
        try:
            b64 = stdout.split("b64=", 1)[1].split()[0].strip()
            manifest_data = _json.loads(base64.b64decode(b64).decode())
        except Exception as exc:
            log.warning("parse assembly manifest : %s", exc)

    return {
        "metadata": dict(latest),
        "manifest": manifest_data,
        "exists_on_pvc": manifest_data is not None,
    }


@app.get("/studies/{sid}/assemblies/{aid}/history")
async def assembly_history_endpoint(
    sid: str, aid: str,
    user: dict = Depends(auth.get_current_user),
):
    """Audit trail INSERT-only."""
    _check_assemblies_enabled()
    from hub import assemblies as asm_mod
    h = await asm_mod.get_assembly_history(aid)
    if not h or h[0]["sid"] != sid:
        raise HTTPException(404, "Assemblage introuvable")
    return h


# ── Helper rendu partagé (D-QGIS-008) ────────────────────────────────────────


def _markdown_to_html_basique(md: str) -> str:
    """Convertit markdown simple en HTML (H1-H3 + paragraphes).

    Pas de dépendance externe (Marked.js etc.). Suffisant pour
    narrative_text V1. Conversions : ## titre → <h2>, paragraphes
    multi-lignes joints.
    """
    import html as _h
    lines = (md or "").split("\n")
    rendered = []
    in_para: list[str] = []

    def _flush_para():
        if in_para:
            rendered.append(f'<p>{_h.escape(" ".join(in_para))}</p>')
            in_para.clear()

    for ln in lines:
        s = ln.strip()
        if s.startswith("### "):
            _flush_para()
            rendered.append(f"<h3>{_h.escape(s[4:])}</h3>")
        elif s.startswith("## "):
            _flush_para()
            rendered.append(f"<h2 style='color:#000091'>{_h.escape(s[3:])}</h2>")
        elif s.startswith("# "):
            _flush_para()
            rendered.append(f"<h1 style='color:#000091'>{_h.escape(s[2:])}</h1>")
        elif not s:
            _flush_para()
        else:
            in_para.append(s)
    _flush_para()
    return "".join(rendered)


async def _build_interactive_map_ctx(
    comp_manifest: dict,
    sid: str,
    username: str,
    cid: str,
) -> dict:
    """Construit le contexte Jinja2 pour _interactive_map_partial.j2.

    Lit scene_manifest + GeoJSON depuis PVC, prépare layers JSON inline.
    Logique migrée depuis _render_assembly_html (Phase 4b).
    """
    import base64 as _b64s
    import json as _json2

    params = comp_manifest.get("params", {}) or {}
    source = comp_manifest.get("source", {}) or {}
    scene_hash = source.get("scene_hash") or params.get("scene_hash")
    title = comp_manifest.get("title", "")
    bbox_text = ""
    layers_inline = params.get("layers", [])
    map_layers_js = "[]"

    # Résilience source.pid : fallback projet par défaut étude
    scene_pid = source.get("pid") or params.get("pid")
    if scene_hash and not scene_pid:
        try:
            _projects = await studies.list_projects(sid)
            if _projects:
                scene_pid = _projects[0]["pid"]
        except Exception:
            pass

    # Sprint 1 V1.13 P0b-1 : lecture des layers_override Marie pour appliquer
    # visibility/opacity/name_override sur les scene_layers (sans dupliquer
    # les GeoJSON ni l'autorite du scene_manifest).
    layers_override_list = params.get("layers_override") or []
    layers_override_by_id: dict = {}
    if isinstance(layers_override_list, list):
        for ov in layers_override_list:
            if isinstance(ov, dict) and ov.get("layer_id_ref"):
                layers_override_by_id[ov["layer_id_ref"]] = ov

    if scene_hash and scene_pid:
        try:
            scene_code = studies.read_scene_manifest_pod_code(sid, scene_pid)
            scene_out = await _execute_python_in_workspace(username, scene_code)
            if "SCENE_MANIFEST_READ_OK" in scene_out:
                scene_b64 = scene_out.split("b64=", 1)[1].split()[0].strip()
                scene_obj = _json2.loads(_b64s.b64decode(scene_b64).decode())
                scene_layers = scene_obj.get("layers", []) or []
                geo_layers = []
                for i_l, l in enumerate(scene_layers):
                    lid = l.get("id", f"layer_{i_l}")
                    override = layers_override_by_id.get(lid, {})
                    # Visibility filter (V1.13 P0b-1) : skip si Marie a masque
                    if override.get("visible") is False:
                        continue
                    geojson_path = l.get("geojson_path")
                    geojson = l.get("geojson")  # fallback inline legacy
                    if not geojson and geojson_path:
                        try:
                            gj_code = studies.read_scene_layer_geojson_pod_code(sid, scene_pid, lid)
                            gj_out = await _execute_python_in_workspace(username, gj_code)
                            if "SCENE_LAYER_READ_OK" in gj_out:
                                gj_b64 = gj_out.split("b64=", 1)[1].split()[0].strip()
                                geojson = _json2.loads(_b64s.b64decode(gj_b64).decode())
                        except Exception as _e:
                            log.warning("read scene_layer %s : %s", lid, _e)
                    if geojson:
                        layer_dict = {
                            "id": lid,
                            # V1.13 P0b-1 : name_override prioritaire si defini
                            "name": override.get("name_override") or l.get("name", lid),
                            "geojson": geojson,
                            "style": l.get("style", {}),
                            "geometry_type": l.get("geometry_type", "polygon"),
                            "n_features": l.get("n_features", 0),
                            # V1.13 P0b-1 : opacity propagee au paint MapLibre
                            "opacity": float(override.get("opacity", 1.0)) if override else 1.0,
                            "z_index": override.get("z_index") if override else None,
                        }
                        # V1.13 P0b-2 : classification per-layer (vs global V1.12)
                        if override.get("classification"):
                            layer_dict["classification_override"] = override["classification"]
                        # V1.13 P0b-2 : interactions per-layer
                        if override.get("popup_template"):
                            layer_dict["popup_template"] = override["popup_template"]
                        if override.get("tooltip_field"):
                            layer_dict["tooltip_field"] = override["tooltip_field"]
                        if override.get("hover_attributes"):
                            layer_dict["hover_attributes"] = override["hover_attributes"]
                        geo_layers.append(layer_dict)
                if geo_layers:
                    total = sum(l.get("n_features", 0) for l in geo_layers)
                    bbox_text = f" — {len(geo_layers)} couche{'s' if len(geo_layers) > 1 else ''} · {total} objets"
                map_layers_js = _json2.dumps(geo_layers)
        except Exception as exc:
            log.warning("interactive_map scene_manifest read %s : %s", cid, exc)
    elif layers_inline:
        # Legacy : layers inline + filter visibility V1.13 si defini
        filtered = [
            l for l in layers_inline
            if layers_override_by_id.get(l.get("id"), {}).get("visible") is not False
        ]
        bbox_text = f" — {len(filtered)} couche{'s' if len(filtered) > 1 else ''}"
        map_layers_js = _json2.dumps(filtered)

    # ── Vague E2 Commit 5 (D-QGIS-009 §5) — Symbologie thematique ──
    # Si params.classification (global V1.12) ou layer.classification_override
    # (per-layer V1.13 P0b-2) defini, calculer les breaks/colors/paint_expression
    # et enrichir chaque layer avec son helper de classification ready-to-inline
    # en MapLibre. Per-layer prioritaire sur global.
    classification_param = params.get("classification") or {}
    try:
        from hub.carto_classification import compute_classification
        layers_data = _json2.loads(map_layers_js)
        had_per_layer = False
        for layer_obj in layers_data:
            features = (layer_obj.get("geojson") or {}).get("features") or []
            if not features:
                continue
            # V1.13 P0b-2 : classification_override per-layer prioritaire
            per_layer_classif = layer_obj.pop("classification_override", None)
            if per_layer_classif:
                had_per_layer = True
                try:
                    classif = compute_classification(features, per_layer_classif)
                    layer_obj["classification"] = classif
                except Exception as exc:
                    log.warning("Classification per-layer %s/%s : %s",
                                cid, layer_obj.get("id"), exc)
            elif classification_param:
                # V1.12 legacy : classification globale s'applique a tous
                # les layers qui n'ont pas d'override.
                try:
                    classif = compute_classification(features, classification_param)
                    layer_obj["classification"] = classif
                except Exception as exc:
                    log.warning("Classification globale %s : %s", cid, exc)
        if had_per_layer or classification_param:
            map_layers_js = _json2.dumps(layers_data)
    except Exception as exc:
        log.warning("Classification thematique %s : %s", cid, exc)

    # ── Vague E2 Commit 4 (D-QGIS-009 §4) — Trio cartographe metier ──
    # Une carte CEREMA exploitable en COPIL a TOUJOURS : Titre + Legende +
    # Source datee + Caveat (optionnel mais recommande). Sans ces 4, la
    # carte est jolie mais inutilisable metier.
    palette = ['#000091', '#e1000f', '#1f8d4d', '#ff6f00', '#9c27b0', '#0288d1']

    # Legende auto-derivee depuis les layers du scene_manifest
    # Vague E2 Commit 5 + 10 : si layer.classification existe, legende riche
    # (gradient_bar pour graduated, chips pour categorized).
    # Si layer.proportional_field defini, legende 'proportional' (3 cercles).
    legend_items = params.get("legend_items")
    legend_format = params.get("legend_format")  # override explicite
    if legend_items is None:
        try:
            layers_data = _json2.loads(map_layers_js)
            if layers_data:
                legend_items = []
                for i_layer, layer_obj in enumerate(layers_data):
                    classif = layer_obj.get("classification") or {}
                    ctype = classif.get("type")
                    if ctype == "graduated" and legend_format is None:
                        legend_format = "gradient_bar"
                    elif layer_obj.get("proportional_field") and legend_format is None:
                        legend_format = "proportional"
                        # Legende proportional : 3 tailles (small/medium/big)
                        prop_min = layer_obj.get("proportional_min", 0)
                        prop_max = layer_obj.get("proportional_max", 1000)
                        prop_mid = (prop_min + prop_max) / 2
                        themed = palette[i_layer % len(palette)]
                        legend_items.extend([
                            {"label": f"≤ {prop_min:.0f}", "color": themed, "size": 8},
                            {"label": f"~ {prop_mid:.0f}", "color": themed, "size": 16},
                            {"label": f"≥ {prop_max:.0f}", "color": themed, "size": 26},
                        ])
                        continue  # skip default branch
                    if ctype in ("graduated", "categorized"):
                        colors = classif.get("colors", [])
                        labels = classif.get("labels", [])
                        for lbl, col in zip(labels, colors):
                            legend_items.append({
                                "label": lbl, "color": col,
                                "layer": layer_obj.get("name"),
                            })
                    elif not layer_obj.get("proportional_field"):
                        # Flat color fallback
                        legend_items.append({
                            "label": layer_obj.get("name", layer_obj.get("id", f"layer {i_layer}")),
                            "color": palette[i_layer % len(palette)],
                            "count": layer_obj.get("n_features"),
                        })
        except Exception:
            legend_items = None
    # legend_format default 'chips' si non override par classification/proportional

    # Source datee : auto-fill depuis le catalog datasources si data_url
    # contient un datasource_id reconnu. V1.13 P0d : delegue a
    # hub.catalog_datasources (1 source de verite, expose aussi via
    # endpoint /catalog/datasources pour autocomplete frontend).
    source_text = params.get("source") or ""
    if not source_text:
        ds_ref = (
            params.get("datasource_id")
            or (source.get("data_url") or "").split("/")[-1]
        )
        if ds_ref:
            from hub.catalog_datasources import get_label
            source_text = get_label(ds_ref)
    # Fallback si scene_manifest mais pas de source datee : signal IGN/CEREMA
    if not source_text and scene_hash:
        source_text = "Scene Manifest QGIS — CEREMA"

    caveat = params.get("caveat") or None

    # Vague E2 Commit 7 (D-QGIS-009 §7) : catalogue fonds de carte
    # Si params.basemap_id defini, lookup catalogue. Default OSM.
    from hub.carto_basemaps import get_basemap_style, get_basemap_metadata
    basemap_id = params.get("basemap_id", "osm")
    basemap_style_json = _json2.dumps(get_basemap_style(basemap_id))
    basemap_meta = get_basemap_metadata(basemap_id)

    # v1.12 : params editables via EditPanel drawer (form InteractiveMapForm).
    # Priorite params.title sur comp_manifest.title si Marie l'a modifie via UI.
    title_from_params = params.get("title")
    final_title = title_from_params or title

    # Sprint 1 V1.13 P0c : resolution zone d'etude via hub.zone_resolver.
    # 3 sources possibles (priorite decroissante) :
    #   1. params.zone (V1.13 structure) - kind=commune (INSEE via geo.api.gouv.fr) /
    #      manual (center_lng/lat/zoom direct) / study (lookup study.zone)
    #   2. params.center_* + zoom (V1.12 legacy plats)
    #   3. defaults Marseille 4e (test territoire CEREMA)
    from hub.zone_resolver import resolve_zone, DEFAULT_CENTER_LNG, DEFAULT_CENTER_LAT, DEFAULT_ZOOM

    fallback_lng = params.get("center_lng", params.get("lng", DEFAULT_CENTER_LNG))
    fallback_lat = params.get("center_lat", params.get("lat", DEFAULT_CENTER_LAT))
    fallback_zoom = params.get("zoom", DEFAULT_ZOOM)

    zone_param = params.get("zone")
    try:
        zone_resolved = await resolve_zone(
            zone_param if isinstance(zone_param, dict) else None,
            sid,
            fallback_lng=fallback_lng,
            fallback_lat=fallback_lat,
            fallback_zoom=fallback_zoom,
        )
        final_lng = zone_resolved["center_lng"]
        final_lat = zone_resolved["center_lat"]
        final_zoom = zone_resolved["zoom"]
    except Exception as exc:
        log.warning("zone_resolver %s : %s, fallback defaults", cid, exc)
        final_lng = fallback_lng
        final_lat = fallback_lat
        final_zoom = fallback_zoom
    return {
        "cid": cid,
        "title": final_title,
        # v1.12 : nouveaux champs editables Marie
        "subtitle": params.get("subtitle", ""),
        "description": params.get("description", ""),
        "height": int(params.get("height") or 580),
        "bbox_text": bbox_text,
        "center_lng": final_lng,
        "center_lat": final_lat,
        "zoom": final_zoom,
        "map_layers_json": map_layers_js,
        # Vague E2 Commit 4 — trio cartographe metier
        "legend_items": legend_items,
        "legend_format": legend_format,  # Vague E2 Commit 10
        "source_text": source_text,
        "caveat": caveat,
        # Vague E2 Commit 7 — catalogue fonds
        "basemap_style_json": basemap_style_json,
        "basemap_name": basemap_meta["name"],
        "basemap_attribution": basemap_meta["attribution"],
    }


async def _pre_render_component_html(
    comp_manifest: dict,
    sid: str,
    username: str,
    cid: str,
) -> str:
    """Source unique du rendu HTML composant (D-QGIS-008 acté 2026-06-29).

    Consommé par :
    - _render_assembly_html (pré-rendu inline storymap)
    - render_component_endpoint (rendu standalone iframe)

    Templates partials `_{kind}_partial.j2` dans
    hub/hub/maplibre_renderer/ (sans <head>/<body>, embeddable).
    """
    import html as _h
    kind = comp_manifest.get("kind", "unknown")
    params = comp_manifest.get("params", {}) or {}

    if not _maplibre_jinja:
        return f'<div style="padding:20px;color:#888;font-style:italic">Jinja indisponible.</div>'

    try:
        if kind == "narrative_text":
            # Accepte plusieurs noms de champ (Bug fix 2026-06-27)
            md = (params.get("content") or params.get("markdown")
                  or params.get("text") or params.get("body") or "")
            # A4 (Vague A Commit 3) : si source.data_url pointe vers
            # un fichier markdown étude (notes.md), fetch et utilise.
            source = comp_manifest.get("source", {}) or {}
            data_url = source.get("data_url", "")
            if data_url and not md:
                try:
                    import re as _re
                    # Whitelist : /files/{sid}/[\w./-]+\.md
                    m = _re.match(r"^/files/([0-9a-f]{12})/([\w./-]+\.md)$", data_url)
                    if m:
                        data_sid = m.group(1)
                        path = m.group(2)
                        # FIX Reviewer 2026-06-29 (path traversal A4) :
                        # regex autorise / dans path mais '..' doit etre rejete.
                        # Aussi : enforcer que chemin resolu reste sous /data/studies/{sid}/.
                        if ".." in path or path.startswith("/"):
                            log.warning("notes.md path traversal rejete : %s", data_url)
                            raise ValueError("path traversal rejete")
                        read_code = (
                            "from pathlib import Path\n"
                            "import base64\n"
                            f"base = Path('/data/studies/{data_sid}').resolve()\n"
                            f"target = (base / '{path}').resolve()\n"
                            "# Garde anti-path-traversal : target doit etre sous base\n"
                            "try:\n"
                            "    target.relative_to(base)\n"
                            "except ValueError:\n"
                            "    print('PATH_TRAVERSAL_REJECTED')\n"
                            "else:\n"
                            "    if target.exists():\n"
                            "        content = target.read_text(encoding='utf-8')\n"
                            "        b64 = base64.b64encode(content.encode('utf-8')).decode('ascii')\n"
                            "        print(f'FILE_READ_OK b64={b64}')\n"
                            "    else:\n"
                            "        print('FILE_NOT_FOUND')\n"
                        )
                        out = await _execute_python_in_workspace(username, read_code)
                        if "FILE_READ_OK" in out:
                            import base64 as _b
                            b64 = out.split("b64=", 1)[1].split()[0].strip()
                            md = _b.b64decode(b64).decode("utf-8")
                except Exception as exc:
                    log.warning("notes.md fetch %s : %s", data_url, exc)
            content_html = _markdown_to_html_basique(md)
            tpl = _maplibre_jinja.get_template("_narrative_text_partial.j2")
            return tpl.render(content_html=content_html)

        elif kind == "kpi_badge":
            # Accepte value/label/unit OU value/label/icon (Bug fix 2026-06-27)
            color_token = params.get("color", "")
            gradient = (
                "linear-gradient(135deg,#e1000f,#aa0000)" if color_token == "marianne-red"
                else "linear-gradient(135deg,#1f8d4d,#0a5d2e)" if color_token == "success-green"
                else "linear-gradient(135deg,#000091,#0063cb)"  # default blue CEREMA
            )
            kpi = {
                "value": params.get("value", "?"),
                "unit": params.get("unit") or "",
                "label": params.get("label", ""),
                "source": params.get("source", ""),
                "gradient": gradient,
            }
            tpl = _maplibre_jinja.get_template("_kpi_badge_partial.j2")
            return tpl.render(kpi=kpi)

        elif kind == "legend":
            items = params.get("items", []) or []
            source_text = params.get("source", "")
            # B3 (Vague B 2026-06-29) : auto-fill source depuis catalog datasources
            # si source.data_url cite un datasource_id reconnu (ex: 'bdtopo_batiments').
            # Pattern : producteur cite la source officielle CEREMA (millésime + licence).
            if not source_text:
                ds_ref = (params.get("datasource_id")
                          or (comp_manifest.get("source") or {}).get("data_url", "").split("/")[-1])
                if ds_ref:
                    # Catalog datasources hardcodés CEREMA (sous-ensemble usual).
                    # Pour catalog complet : appel /mcp list_datasources (V2 si besoin).
                    catalog = {
                        "bdtopo_batiments": "BD TOPO 2024 — IGN — Licence Ouverte 2.0",
                        "bdtopo_parcelles": "BD TOPO 2024 — IGN — Licence Ouverte 2.0",
                        "bdtopo_adresses": "BD TOPO 2024 — IGN — Licence Ouverte 2.0",
                        "bdtdv": "DVF (Demandes Valeurs Foncières) — DGFiP — Licence Ouverte 2.0",
                        "georisques_api": "Géorisques API — DGPR — Licence Ouverte 2.0",
                        "tri_limites": "TRI (Territoires Risque Inondation) — DGPR — Licence Ouverte 2.0",
                        "corine_land_cover": "CORINE Land Cover 2018 — Copernicus EEA — Licence Ouverte",
                        "admin_communes": "Découpage administratif — IGN ADMIN EXPRESS — Licence Ouverte 2.0",
                        "rge_alti": "RGE ALTI 5m — IGN — Licence Ouverte 2.0",
                    }
                    source_text = catalog.get(ds_ref, "")
            tpl = _maplibre_jinja.get_template("_legend_partial.j2")
            return tpl.render(
                items=items,
                title=params.get("title") or comp_manifest.get("title", ""),
                source=source_text,
            )

        elif kind == "interactive_map":
            ctx = await _build_interactive_map_ctx(comp_manifest, sid, username, cid)
            tpl = _maplibre_jinja.get_template("_interactive_map_partial.j2")
            return tpl.render(**ctx)

        elif kind == "chart":
            # A2 (Vague A Commit 3) : Chart.js v4 inline.
            # Schema params : {chart_type, labels, datasets, source?}
            tpl = _maplibre_jinja.get_template("_chart_partial.j2")
            return tpl.render(
                cid=cid,
                title=comp_manifest.get("title", ""),
                chart_type=params.get("chart_type", "bar"),
                labels=params.get("labels", []),
                datasets=params.get("datasets", []),
                source=params.get("source", ""),
            )

        elif kind == "data_table":
            # A2 (Vague A Commit 3) : tableau HTML + CSS Grid sobre.
            # Schema params : {columns: [{key, label, align?}], rows: list[dict], source?, max_rows?}
            tpl = _maplibre_jinja.get_template("_data_table_partial.j2")
            return tpl.render(
                cid=cid,
                title=comp_manifest.get("title", ""),
                columns=params.get("columns", []),
                rows=params.get("rows", []),
                source=params.get("source", ""),
                max_rows=params.get("max_rows", 100),
            )

        elif kind == "kpi_grid":
            # Vague E2 (D-QGIS-009 §3, 2026-06-29) : grid responsive de N KPIs
            # Params : {kpis: [{value, label, unit?, color?, reliability?}, ...],
            #          columns_min?: 140, palette?: 'monochrome'|'rainbow'}
            #
            # Vague E2 Commit 2 (P3) : palette MONOCHROME bleu marianne par
            # defaut (DSFR sobre). 'rainbow' uniquement si user demande
            # explicitement palette='rainbow'. 'color' override par-KPI
            # reserve aux alertes semantiques (marianne-red = danger).
            palette = params.get("palette", "monochrome")
            # Monochrome = degrade subtil de bleu Marianne (1 couleur dominante)
            monochrome_gradients = [
                "linear-gradient(135deg,#000091,#0063cb)",  # bleu fonce
                "linear-gradient(135deg,#1212a1,#1d75d0)",  # legerement plus clair
                "linear-gradient(135deg,#2424b0,#3d87d4)",
                "linear-gradient(135deg,#3636bf,#5099d7)",
            ]
            color_map = {
                "marianne-red": "linear-gradient(135deg,#e1000f,#aa0000)",
                "success-green": "linear-gradient(135deg,#1f8d4d,#0a5d2e)",
                "warning-orange": "linear-gradient(135deg,#b34000,#cd6133)",
                "info-blue": "linear-gradient(135deg,#000091,#0063cb)",
            }
            kpis = params.get("kpis", []) or []
            cols_min = int(params.get("columns_min", 140))
            items_html = []
            for idx, k in enumerate(kpis[:24]):
                # Si user a explicitement specifie color, respect.
                # Sinon : monochrome (default) -> shading bleu indexed,
                #         OR rainbow -> info-blue default si pas de color.
                user_color = k.get("color")
                if user_color and user_color in color_map:
                    grad = color_map[user_color]
                elif palette == "monochrome":
                    grad = monochrome_gradients[idx % len(monochrome_gradients)]
                else:  # rainbow legacy
                    grad = color_map["info-blue"]
                items_html.append(
                    f'<div style="background:{grad};color:#fff;padding:18px 14px;'
                    f'border-radius:6px;text-align:center;box-shadow:0 1px 3px rgba(0,0,0,.08)">'
                    f'<div style="font-size:28px;font-weight:700;line-height:1.1">{_h.escape(str(k.get("value", "?")))}'
                    f'<span style="font-size:14px;font-weight:500;margin-left:4px">{_h.escape(str(k.get("unit") or ""))}</span></div>'
                    f'<div style="font-size:12px;margin-top:6px;opacity:.92">{_h.escape(str(k.get("label", "")))}</div>'
                    f'</div>'
                )
            return (
                f'<div style="display:grid;'
                f'grid-template-columns:repeat(auto-fit,minmax({cols_min}px,1fr));'
                f'gap:12px;margin:16px 0">{"".join(items_html)}</div>'
            )

        elif kind == "heading":
            # Vague E2 : titre H1-H4 standalone (au lieu de markdown ## dans narrative_text).
            # Params : {text, level?: 2}
            level = max(1, min(4, int(params.get("level", 2))))
            text = _h.escape(str(params.get("text", comp_manifest.get("title", ""))))
            sizes = {1: "32px", 2: "26px", 3: "20px", 4: "16px"}
            return (
                f'<h{level} style="font-size:{sizes[level]};color:#161616;'
                f'margin:24px 0 12px;font-weight:700;line-height:1.3">{text}</h{level}>'
            )

        elif kind == "quote":
            # Vague E2 : citation / pull-quote (sources expertes, témoignages CEREMA).
            # Params : {text, author?, source?}
            text = _h.escape(str(params.get("text", "")))
            author = _h.escape(str(params.get("author", "")))
            source_text = _h.escape(str(params.get("source", "")))
            attr_html = ""
            if author or source_text:
                parts = [p for p in [author, source_text] if p]
                attr_html = f'<footer style="margin-top:8px;font-size:13px;color:#666">— {" · ".join(parts)}</footer>'
            return (
                f'<blockquote style="border-left:4px solid #000091;'
                f'padding:12px 18px;margin:18px 0;background:#f4f6fa;'
                f'font-style:italic;color:#1a1a1a;font-size:16px;line-height:1.6">'
                f'{text}{attr_html}</blockquote>'
            )

        elif kind == "separator":
            # Vague E2 : séparateur horizontal entre blocks.
            # Params : {style?: "solid"|"dashed"|"dotted", color?: "#ddd",
            #          variant?: "rule"|"break"|"ornament"}
            # Vague E2 Commit 2 (P7) : separator plus visible
            # (border 2px + margin 40px) + variants narratifs
            style = params.get("style", "solid")
            if style not in ("solid", "dashed", "dotted"):
                style = "solid"
            color = params.get("color", "#000091")  # default bleu marianne (vs gris invisible)
            if not isinstance(color, str) or len(color) > 7 or not color.startswith("#"):
                color = "#000091"
            variant = params.get("variant", "rule")
            if variant == "ornament":
                # Trait court centre (beat narratif fort)
                return (
                    f'<hr style="border:none;border-top:3px {style} {color};'
                    f'margin:48px auto;width:80px">'
                )
            elif variant == "break":
                # Espacement fort sans trait (pause respiratoire)
                return f'<div style="height:60px"></div>'
            # rule (default) : trait pleine largeur, visible
            return (
                f'<hr style="border:none;border-top:2px {style} {color};'
                f'margin:40px 0;width:100%;opacity:.6">'
            )

        elif kind == "media_embed":
            # Sprint 1 Vague E3 fix D4 : vrai rendu (vs placeholder texte).
            # Detecte type MIME via params.mime ou extension url.
            source = comp_manifest.get("source", {}) or {}
            url = params.get("url") or source.get("data_url", "")
            mime = params.get("mime", "")
            if not mime and url:
                # Auto-detect via extension
                url_lower = url.lower()
                if url_lower.endswith(".pdf"):
                    mime = "application/pdf"
                elif url_lower.endswith((".mp4", ".webm", ".ogv")):
                    mime = f"video/{url_lower.rsplit('.', 1)[-1]}"
                elif url_lower.endswith((".png", ".jpg", ".jpeg", ".gif", ".webp", ".svg")):
                    mime = f"image/{url_lower.rsplit('.', 1)[-1].replace('jpg', 'jpeg')}"
                else:
                    mime = "text/html"  # fallback iframe
            tpl = _maplibre_jinja.get_template("_media_embed_partial.j2")
            return tpl.render(
                title=params.get("title") or comp_manifest.get("title", ""),
                url=url,
                mime=mime,
                caption=params.get("caption", ""),
                source=params.get("source", ""),
                height=params.get("height", 400),
            )

        elif kind == "iframe_grist":
            # Sprint 1 Vague E3 fix D4 : vrai rendu iframe Grist.
            source = comp_manifest.get("source", {}) or {}
            widget_url = (
                params.get("widget_url") or params.get("url")
                or source.get("data_url", "")
            )
            tpl = _maplibre_jinja.get_template("_iframe_grist_partial.j2")
            return tpl.render(
                title=params.get("title") or comp_manifest.get("title", ""),
                widget_url=widget_url,
                height=params.get("height", 500),
                caption=params.get("caption", ""),
                source=params.get("source", ""),
            )

        else:
            # Fallback : kind non géré inline (scene_3d differé Vague E3 sprint 3,
            # chart pre-Vague A, data_table pre-Vague A) → placeholder
            return (
                f'<div style="padding:24px;text-align:center;background:#f4f6fa;'
                f'border-radius:6px;color:#666">'
                f'<p style="margin:0 0 8px;font-weight:600">Composant {_h.escape(kind)}</p>'
                f'<p style="margin:0;font-size:13px">'
                f'<a href="/studies/{sid}/components/{cid}/render" target="_blank">'
                f'Voir l\'aperçu interactif</a>'
                f'</p></div>'
            )
    except Exception as exc:
        log.warning("pre_render_component %s/%s : %s", kind, cid, exc)
        return (
            f'<div style="padding:20px;color:#888;font-style:italic">'
            f'Composant {cid[:8]} indisponible ({type(exc).__name__}).</div>'
        )


async def _render_assembly_html(
    sid: str, aid: str, username: str,
) -> tuple[str, dict]:
    """Helper : rend l'HTML de l'assemblage via template Jinja2 + retourne
    (html_content, audit_chain_dict)."""
    from hub import assemblies as asm_mod
    from hub.models import Assembly

    latest = await asm_mod.get_assembly_latest(aid)
    if not latest or latest["sid"] != sid:
        raise HTTPException(404, "Assemblage introuvable")
    if latest["owner"] != username:
        raise HTTPException(403, "Pas owner")

    # Lire manifest
    try:
        stdout = await _execute_python_in_workspace(
            username, asm_mod.read_assembly_manifest_pod_code(sid, aid),
        )
    except Exception as exc:
        raise HTTPException(503, f"Workspace : {exc}")

    import base64, json as _json
    if "ASSEMBLY_READ_OK" not in stdout:
        raise HTTPException(404, "Manifest PVC introuvable")
    b64 = stdout.split("b64=", 1)[1].split()[0].strip()
    manifest = _json.loads(base64.b64decode(b64).decode())

    asm = Assembly.model_validate(manifest)

    # Build audit_chain
    chain = await asm_mod.build_audit_chain(asm, username, asm.audience)

    # Render template selon kind
    template_map = {
        "storymap_narrative_dsfr": "maplibre_renderer/storymap_dsfr.html.j2",
        # Sprint 4 : dashboard, sheet_a4, modal_embed, atlas_immersive
    }
    template_name = template_map.get(asm.kind)
    if not template_name:
        raise HTTPException(
            501,
            f"Kind '{asm.kind}' non supporté Phase 3. Kinds : {list(template_map.keys())}",
        )

    if not _maplibre_jinja:
        raise HTTPException(503, "Maplibre Jinja2 indisponible")

    # Pré-rendu inline des composants via helper unifié D-QGIS-008
    # (consommé aussi par render_component_endpoint pour cohérence).
    from hub import components as comp_mod
    rendered_components: dict[str, str] = {}
    # Vague E2 Commit 2 : map cid -> kind pour le template (decide
    # .story-component--atomic vs --map vs --full, skip section.title si
    # 1er composant = heading).
    rendered_components_kinds: dict[str, str] = {}
    for section in asm.layout.sections:
        for comp_entry in (section.components or []):
            cid = (comp_entry.dict() if hasattr(comp_entry, 'dict') else comp_entry).get("ref") if isinstance(comp_entry, dict) or hasattr(comp_entry, 'dict') else None
            if not cid or cid in rendered_components:
                continue
            try:
                comp_meta = await comp_mod.get_component_latest(cid)
                if not comp_meta:
                    rendered_components[cid] = (
                        f'<div style="padding:20px;color:#666;font-style:italic">'
                        f'Composant {cid[:8]} introuvable.</div>'
                    )
                    continue
                # Lire manifest PVC
                code = comp_mod.read_component_manifest_pod_code(sid, cid)
                stdout = await _execute_python_in_workspace(username, code)
                if "COMPONENT_READ_OK" not in stdout:
                    rendered_components[cid] = (
                        f'<div style="padding:20px;color:#666;font-style:italic">'
                        f'Composant {cid[:8]} indisponible.</div>'
                    )
                    continue
                import base64 as _b64
                b64_data = stdout.split("b64=", 1)[1].split()[0].strip()
                comp_manifest = _json.loads(_b64.b64decode(b64_data).decode())
                # Stocke le kind pour le template (Vague E2 Commit 2)
                rendered_components_kinds[cid] = comp_manifest.get("kind", "")
                # Helper unifié (D-QGIS-008) — templates partials Jinja2
                rendered_components[cid] = await _pre_render_component_html(
                    comp_manifest, sid, username, cid,
                )
            except Exception as exc:
                log.warning("pre-render component %s : %s", cid, exc)
                rendered_components[cid] = (
                    f'<div style="padding:20px;color:#888;font-style:italic">'
                    f'Composant indisponible ({type(exc).__name__}).</div>'
                )


    try:
        tpl_short = template_name.replace("maplibre_renderer/", "")
        tpl = _maplibre_jinja.get_template(tpl_short)
        html = tpl.render(
            assembly=asm.model_dump(mode="json"),
            sections=[s.model_dump(mode="json") for s in asm.layout.sections],
            rendered_components=rendered_components,
            rendered_components_kinds=rendered_components_kinds,
            audit_chain=chain.model_dump(mode="json"),
            footer=asm.footer.model_dump(mode="json"),
        )
        return html, chain.model_dump(mode="json")
    except Exception as exc:
        import traceback as _tb
        log.error("render_assembly %s/%s : %s", sid, aid, exc)
        tb_str = _tb.format_exc()
        raise HTTPException(
            status_code=500,
            detail={
                "error_class": type(exc).__name__,
                "error_message": str(exc)[:300],
                "template_tried": tpl_short,
                "traceback_tail": tb_str.splitlines()[-5:] if tb_str else [],
            },
        )


@app.get("/studies/{sid}/assemblies/{aid}/render", response_class=HTMLResponse)
async def render_assembly_endpoint(
    sid: str, aid: str,
    user: dict = Depends(auth.get_current_user),
):
    """Rendu HTML preview (sans persistance ni publish). Recalcule à chaque appel."""
    _check_assemblies_enabled()
    html, _chain = await _render_assembly_html(sid, aid, user["username"])
    return HTMLResponse(content=html)


@app.post("/studies/{sid}/components/{cid}/publish")
async def publish_component_endpoint(
    sid: str, cid: str, request: Request,
    user: dict = Depends(auth.get_current_user),
):
    """A3 (Vague A Commit 3) — Publie un composant standalone S3 + URL hub.

    Body optionnel : {audience?: 'public'|'cerema_internal'|'restricted'|'confidential'}

    Workflow :
    1. Lit manifest composant depuis components_index + PVC
    2. Rend HTML standalone via template Jinja2 partial (DSFR sobre)
    3. Upload S3 via /publish/component/{slug} generic endpoint
    4. Retourne URL hub /published/{owner}/component/{slug}

    URL produite est embeddable iframe par sites tiers (CSP B5 frame-ancestors *).
    Use case : Atlas widget Grist iframe d'un interactive_map qgis-sspcloud,
    sites CEREMA tiers, agents partagés.
    """
    _check_components_enabled()
    from hub import components as comp_mod

    comp_meta = await comp_mod.get_component_latest(cid)
    if not comp_meta or comp_meta["sid"] != sid:
        raise HTTPException(404, "Composant introuvable")
    if comp_meta["owner"] != user["username"]:
        raise HTTPException(403, "Pas owner du composant")

    try:
        body = await request.json()
    except Exception:
        body = {}
    audience = body.get("audience", "cerema_internal")

    # Lire manifest depuis PVC
    code = comp_mod.read_component_manifest_pod_code(sid, cid)
    stdout = await _execute_python_in_workspace(user["username"], code)
    if "COMPONENT_READ_OK" not in stdout:
        raise HTTPException(503, "Lecture manifest impossible")
    import base64 as _b
    b64 = stdout.split("b64=", 1)[1].split()[0].strip()
    comp_manifest = _json.loads(_b.b64decode(b64).decode())

    # Render HTML standalone : envelope + partial inline
    component_html = await _pre_render_component_html(
        comp_manifest, sid, user["username"], cid,
    )
    standalone_html = (
        "<!DOCTYPE html><html lang='fr'><head>"
        "<meta charset='UTF-8'>"
        f"<title>{comp_manifest.get('title', cid)}</title>"
        "<meta name='viewport' content='width=device-width,initial-scale=1'>"
        "<link rel='stylesheet' href='https://unpkg.com/maplibre-gl@4/dist/maplibre-gl.css'>"
        "<script src='https://unpkg.com/maplibre-gl@4/dist/maplibre-gl.js'></script>"
        "<script src='https://unpkg.com/chart.js@4.4.0/dist/chart.umd.js'></script>"
        "<style>body{margin:0;font-family:Marianne,system-ui,sans-serif;background:#f6f6f6;padding:20px}</style>"
        "</head><body>"
        f"{component_html}"
        "</body></html>"
    )

    # Upload S3 directement via s3_publication.publish()
    if not _S3_AVAILABLE:
        raise HTTPException(503, "Publication S3 indisponible")
    slug = f"component-{cid}"
    owner = user["username"]
    try:
        result = s3_publication.publish(
            owner=owner,
            kind="component",
            slug=slug,
            content=standalone_html.encode("utf-8"),
            content_type="text/html; charset=utf-8",
            study_id=sid,
        )
        # URL hub (anti-MinIO ACL bug fix f11da9d)
        hub_base = (_HUB_URL or _SELF_URL or "").rstrip("/")
        published_url = (
            f"{hub_base}/published/{owner}/component/{slug}"
            if hub_base else result.get("url")
        )
        return {
            "id": cid,
            "published": True,
            "published_url": published_url,
            "audience": audience,
            "kind": comp_meta["kind"],
            "title": comp_meta["title"],
            "size_bytes": result.get("size", 0),
        }
    except Exception as exc:
        log.error("publish_component %s/%s : %s", sid, cid, exc)
        raise HTTPException(503, f"Publication échec : {exc}")


@app.post("/studies/{sid}/assemblies/{aid}/clone", status_code=status.HTTP_201_CREATED)
async def clone_assembly_endpoint(
    sid: str, aid: str,
    deep: bool = False,
    user: dict = Depends(auth.get_current_user),
):
    """Vague E1 (D-QGIS-009, 2026-06-29) : clone un assemblage existant.

    Use case : Marie part d'un template (storymap risque inondation reference)
    et l'adapte pour son cas (4e arrondissement -> 5e arrondissement) au lieu
    de tout recreer from scratch via agent IA.

    Comportement :
    - `deep=false` (DEFAULT, shallow) : refs cid composants partagés. Modifs
      sur les composants source impactent le clone. Avantage : rapide,
      audit trail simple.
    - `deep=true` : duplique aussi tous les composants référencés (nouveaux
      cid). Le clone devient indépendant. Plus lourd mais utile pour fork
      réel.

    Le nouveau assemblage :
    - new aid (12 hex)
    - version_num = 1
    - provenance.cloned_from = aid source
    - sid preserve (clone dans la même étude)
    - audience défaut héritée de l'assemblage source
    """
    _check_assemblies_enabled()
    if not _STUDIES_AVAILABLE:
        raise HTTPException(503, "Module studies indisponible")
    s = await studies.get_study(sid, user["username"])
    if not s:
        raise HTTPException(404, "Étude introuvable")

    from hub.models import Assembly
    from hub import assemblies as asm_mod
    from hub import components as comp_mod
    from hub.models import Component

    # Verifier que la source existe et est lisible
    latest = await asm_mod.get_assembly_latest(aid)
    if not latest:
        raise HTTPException(404, "Assemblage source introuvable")
    if latest["sid"] != sid or latest["owner"] != user["username"]:
        raise HTTPException(403, "Pas owner ou hors scope etude")

    # Lire manifest source PVC
    try:
        stdout = await _execute_python_in_workspace(
            user["username"],
            asm_mod.read_assembly_manifest_pod_code(sid, aid),
        )
    except Exception as exc:
        raise HTTPException(503, f"Workspace : {exc}")

    if "ASSEMBLY_READ_OK" not in stdout:
        raise HTTPException(404, "Manifest PVC source introuvable")

    import base64 as _b64
    b64 = stdout.split("b64=", 1)[1].split()[0].strip()
    source_manifest = _json.loads(_b64.b64decode(b64).decode())

    # Construire new manifest : nouvel aid + clear version + trace cloned_from
    new_aid = studies._new_id()
    new_manifest = dict(source_manifest)  # shallow copy
    new_manifest["id"] = new_aid
    new_manifest["sid"] = sid
    new_manifest["version"] = 1
    new_manifest.setdefault("provenance", {})["cloned_from"] = aid
    new_manifest["provenance"]["created_by"] = "agent"
    # Title : ajout " (clone)" pour distinguer
    if not new_manifest.get("title", "").endswith("(clone)"):
        new_manifest["title"] = f"{new_manifest.get('title', 'Assemblage')} (clone)"

    # Deep clone : dupliquer chaque composant référencé
    if deep and new_manifest.get("layout", {}).get("sections"):
        for section in new_manifest["layout"]["sections"]:
            for comp_entry in (section.get("components") or []):
                if "ref" in comp_entry:
                    old_cid = comp_entry["ref"]
                    # Lire manifest composant source + créer copie avec nouveau cid
                    try:
                        comp_stdout = await _execute_python_in_workspace(
                            user["username"],
                            comp_mod.read_component_manifest_pod_code(sid, old_cid),
                        )
                        if "COMPONENT_READ_OK" in comp_stdout:
                            cb64 = comp_stdout.split("b64=", 1)[1].split()[0].strip()
                            comp_manifest = _json.loads(_b64.b64decode(cb64).decode())
                            new_cid = studies._new_id()
                            comp_manifest["id"] = new_cid
                            comp_manifest["sid"] = sid
                            comp_manifest["version"] = 1
                            comp_manifest.setdefault("provenance", {})["cloned_from"] = old_cid
                            comp_obj = Component.model_validate(comp_manifest)
                            # Ecrire + insert nouveau composant
                            new_comp_json = _json.dumps(
                                comp_obj.model_dump(mode="json"),
                                ensure_ascii=False, indent=2,
                            )
                            await _execute_python_in_workspace(
                                user["username"],
                                comp_mod.write_component_manifest_pod_code(
                                    sid, new_cid, new_comp_json,
                                ),
                            )
                            await comp_mod.insert_component(
                                component=comp_obj, owner=user["username"], sid=sid,
                                file_path=comp_mod.component_manifest_path(sid, new_cid),
                                size_bytes=len(new_comp_json.encode("utf-8")),
                                previous_hash="",  # Premier dans la nouvelle chaîne
                            )
                            comp_entry["ref"] = new_cid
                    except Exception as exc:
                        log.warning("deep clone component %s : %s", old_cid, exc)

    # Valider le nouveau manifest
    try:
        asm = Assembly.model_validate(new_manifest)
    except Exception as exc:
        raise HTTPException(422, f"Validation Pydantic clone : {exc}")

    # Écrire manifest PVC
    content_json = _json.dumps(asm.model_dump(mode="json"), ensure_ascii=False, indent=2)
    try:
        await _execute_python_in_workspace(
            user["username"],
            asm_mod.write_assembly_manifest_pod_code(sid, asm.id, content_json),
        )
    except Exception as exc:
        log.warning("write cloned assembly manifest : %s", exc)

    # INSERT new row assemblies_index
    file_path = asm_mod.assembly_manifest_path(sid, asm.id)
    rowid = await asm_mod.insert_assembly(
        assembly=asm, owner=user["username"], file_path=file_path,
    )
    return {
        "id": asm.id,
        "rowid": rowid,
        "cloned_from": aid,
        "deep": deep,
        "kind": asm.kind,
        "title": asm.title,
        "audience": asm.audience,
        "version_num": 1,
        "manifest_url":   f"/studies/{sid}/assemblies/{asm.id}",
        "render_url":     f"/studies/{sid}/assemblies/{asm.id}/render",
        "publish_url":    f"/studies/{sid}/assemblies/{asm.id}/publish",
    }


@app.post("/studies/{sid}/assemblies/{aid}/publish")
async def publish_assembly_endpoint(
    sid: str, aid: str, request: Request,
    user: dict = Depends(auth.get_current_user),
):
    """Publie l'assemblage sur S3 + calcule audit_chain + indexe published_url.

    Body optionnel : {audience?: 'public'|'cerema_internal'|'restricted'|'confidential'}
    """
    _check_assemblies_enabled()
    from hub import assemblies as asm_mod
    from hub import s3_publication

    # Render + audit_chain
    html, chain_dict = await _render_assembly_html(sid, aid, user["username"])

    # Sprint Composants Phase 3 fix (2026-06-26) : wrap toutes les étapes
    # post-render dans try/except avec traceback structurée. La signature
    # s3_publication.publish() retourne info["url"] (pas info["public_url"]).
    import base64 as _base64, json as _json
    import traceback as _tb

    audit_chain_written = False
    published_url = None
    write_pvc_error = None
    db_update_error = None
    s3_publish_error = None

    # 1. Persiste rendered HTML sur PVC (best-effort)
    try:
        await _execute_python_in_workspace(
            user["username"],
            asm_mod.write_assembly_rendered_pod_code(sid, aid, html),
        )
    except Exception as exc:
        write_pvc_error = str(exc)[:200]
        log.warning("write assembly rendered : %s", exc)

    # 2. Update audit_chain_json sur la row latest
    try:
        import aiosqlite  # scope local — pas dans imports top-level main.py
        async with aiosqlite.connect(studies._DB_PATH) as db:
            await db.execute(
                """UPDATE assemblies_index
                   SET audit_chain_json = ?
                   WHERE rowid IN (
                     SELECT rowid FROM assemblies_index
                     WHERE aid = ? AND status = 'active'
                     ORDER BY version_num DESC LIMIT 1
                   )""",
                (_json.dumps(chain_dict, ensure_ascii=False), aid),
            )
            await db.commit()
        audit_chain_written = True
    except Exception as exc:
        db_update_error = str(exc)[:200]
        log.error("update audit_chain_json : %s", exc)

    # 3. Publish S3 via s3_publication.publish()
    slug = f"assembly-{aid}"
    try:
        info = s3_publication.publish(
            owner=user["username"], kind="assembly", slug=slug,
            content=html.encode("utf-8"),
            content_type="text/html; charset=utf-8",
            study_id=sid,
        )
        # Bug fix 2026-06-27 : MinIO SSPCloud n'accepte plus l'ACL canned
        # public-read sur les objets uploades (retourne AccessDenied 403).
        # On retourne donc l'URL via le hub /published/{owner}/{kind}/{slug}
        # qui sert le contenu apres lecture S3 (auth Bearer/cookie OK ou
        # endpoint publique whitelist OIDC).
        hub_base = (_HUB_URL or _SELF_URL or "").rstrip("/")
        published_url = (
            f"{hub_base}/published/{user['username']}/assembly/{slug}"
            if hub_base else info.get("url")
        )
    except Exception as exc:
        s3_publish_error = str(exc)[:200]
        log.error("publish S3 assembly : %s", exc)

    # 4. Update published_url en DB (si S3 a marché)
    if published_url:
        try:
            await asm_mod.update_published_info(aid, published_url)
        except Exception as exc:
            log.error("update_published_info : %s", exc)

    # Renvoie résultat structuré (toujours 200, état détaillé dans body)
    return {
        "id": aid,
        "published": published_url is not None,
        "published_url": published_url,
        "audit_chain": {
            # D-FORMAT-008 : integrity_hash + signed_hash legacy backward-compat
            "integrity_hash": chain_dict.get("integrity_hash") or chain_dict.get("signed_hash"),
            "signed_hash": chain_dict.get("integrity_hash") or chain_dict.get("signed_hash"),
            "components_refs": chain_dict.get("components_refs", []),
            "scene_hashes": chain_dict.get("scene_hashes", []),
            "recipes_used": chain_dict.get("recipes_used", []),
        },
        "diagnostics": {
            "audit_chain_written_to_db": audit_chain_written,
            "rendered_html_written_pvc": write_pvc_error is None,
            "rendered_html_size_bytes": len(html.encode("utf-8")),
            "errors": {
                "write_pvc": write_pvc_error,
                "db_update": db_update_error,
                "s3_publish": s3_publish_error,
            },
        },
    }


@app.delete("/studies/{sid}/assemblies/{aid}", status_code=status.HTTP_204_NO_CONTENT)
async def archive_assembly_endpoint(
    sid: str, aid: str,
    user: dict = Depends(auth.get_current_user),
):
    """Soft delete (INSERT row archived)."""
    _check_assemblies_enabled()
    from hub import assemblies as asm_mod
    rowid = await asm_mod.archive_assembly(aid, user["username"])
    if not rowid:
        raise HTTPException(404, "Assemblage introuvable ou pas owner")
    return None


@app.get("/catalog/components")
async def catalog_components_endpoint(
    audience: Literal["public", "cerema_internal", "restricted", "confidential"] = "cerema_internal",
    kind: str | None = None,
    limit: int = 50,
    offset: int = 0,
    user: dict = Depends(auth.get_current_user),
):
    """Vague E1 (D-QGIS-009, 2026-06-29) : catalogue cross-étude des composants.

    Permet à l'agent IA et au user de découvrir les composants réutilisables
    publiés par d'autres études CEREMA (ZEBRA, MobSciDat, autres users).

    Filtres :
    - `audience` (default 'cerema_internal' anti-fuite RGPD) :
      * 'public' : composants vraiment publics (peu nombreux)
      * 'cerema_internal' : collègues CEREMA (default sain)
      * 'restricted' : scoped key requise
      * 'confidential' : archive (généralement pas listée)
    - `kind` : filtre ComponentKind (interactive_map, kpi_badge, ...)
    - `limit` + `offset` : pagination (default 50/0)

    Use case agent IA : avant de créer un composant from scratch, check
    le catalogue pour voir s'il existe déjà un composant similaire à
    réutiliser ou cloner.

    Use case user : browse marketplace de composants CEREMA.

    Retourne {items: list[Component], total, audience, kind, limit, offset}.
    """
    _check_components_enabled()
    from hub import components as comp_mod

    rows = await comp_mod.list_components(
        sid=None,  # Cross-étude (pas de filtre sid)
        kind=kind,
        classification=audience,
        limit=limit,
        offset=offset,
    )
    return {
        "items": rows,
        "total": len(rows),
        "audience": audience,
        "kind": kind,
        "limit": limit,
        "offset": offset,
    }


@app.get("/catalog/assemblies")
async def catalog_assemblies_endpoint(
    audience: Literal["public", "cerema_internal", "restricted", "confidential"] = "cerema_internal",
    kind: str | None = None,
    limit: int = 50,
    offset: int = 0,
    user: dict = Depends(auth.get_current_user),
):
    """Vague E1 (D-QGIS-009) : catalogue cross-étude des assemblages.

    Pendant à catalog_components_endpoint pour les assemblages (storymap,
    dashboard, sheet_a4...). Permet de découvrir des templates de livrables
    réutilisables (à cloner via POST /assemblies/{aid}/clone).

    Pattern identique catalogue composants : filtres audience/kind +
    pagination. Default audience='cerema_internal' anti-fuite RGPD.
    """
    _check_assemblies_enabled()
    from hub import assemblies as asm_mod

    rows = await asm_mod.list_assemblies(
        sid=None,  # Cross-étude
        kind=kind,
        # NB : list_assemblies n'a pas encore filtre classification, on filtre Python
    )
    # Filter par audience côté Python (extension list_assemblies future)
    filtered = [r for r in rows if r.get("classification") == audience]
    # Pagination
    paginated = filtered[offset:offset + limit]
    return {
        "items": paginated,
        "total": len(filtered),
        "audience": audience,
        "kind": kind,
        "limit": limit,
        "offset": offset,
    }


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


# ── Checkpoints / Rollback agent (Commit B) ───────────────────────────────────
# Pattern : l'agent appelle ces endpoints juste avant d'exécuter un tool
# mutating. Le hub fait le snapshot Python côté workspace pod + log dans
# audit_trail. L'agent enregistre la métadonnée dans SQLite (/data/agent/).

async def _log_audit_event_on_pod(username: str, log_path: str, evt: dict) -> None:
    """Append un évènement audit dans `treatments.jsonl` côté pod workspace.

    Le hub n'a pas d'accès direct au PVC user, donc on délègue l'écriture
    à un execute_python. Pour éviter les pièges d'injection f-string (repr
    de dict avec strings contenant quotes/newlines casse le code généré),
    on sérialise d'abord evt en JSON puis on insère cette string repr-safe
    dans le code Python.

    Best-effort : si l'écriture échoue, on log un warning mais on ne
    propage pas l'exception (l'audit n'est pas critique pour la fonction
    principale du caller).
    """
    try:
        evt_json = json.dumps(evt, ensure_ascii=False)
        code = (
            f"from pathlib import Path\n"
            f"p = Path({log_path!r})\n"
            f"p.parent.mkdir(parents=True, exist_ok=True)\n"
            f"line = {evt_json!r} + '\\n'\n"
            f"with open(p, 'a', encoding='utf-8') as f:\n"
            f"    f.write(line)\n"
            f"print('AUDIT_LOG_OK kind=' + {evt.get('kind', '?')!r})\n"
        )
        out = await _execute_python_in_workspace(username, code, timeout=5)
        if "AUDIT_LOG_OK" not in (out or ""):
            log.warning("Audit log peut avoir échoué silencieusement : %s",
                        (out or "")[:200])
    except Exception as exc:
        log.warning("Audit log kind=%s échec : %s", evt.get("kind", "?"), exc)

@app.post("/sessions/{session_id}/checkpoint")
async def session_checkpoint(
    session_id: str,
    request: Request,
    user: dict = Depends(auth.get_current_user),
):
    """Snapshot le projet QGIS courant avant un tool mutating.

    Reçoit {checkpoint_id, tool_name}. Exécute snapshot_active_pod_code sur
    le workspace via execute_python. Log dans audit_trail kind=checkpoint.
    Retourne {qgz_path, study_id, audit_ts} pour que l'agent stocke la
    métadonnée dans SQLite.
    """
    if not _STUDIES_AVAILABLE:
        raise HTTPException(503, "Module studies indisponible")
    body = await request.json()
    ckpt_id = body.get("checkpoint_id", "").strip()
    tool_name = body.get("tool_name", "").strip()
    if not ckpt_id or not tool_name:
        raise HTTPException(400, "checkpoint_id et tool_name requis")

    active_sid = await studies.get_active_study_id(user["username"])
    if not active_sid:
        # Sans étude active, pas de dossier .checkpoints/ → on skip silencieusement
        return {"ok": False, "reason": "no_active_study"}

    try:
        await _execute_python_in_workspace(
            user["username"],
            studies.snapshot_active_pod_code(active_sid, ckpt_id, tool_name),
            timeout=15,
        )
    except Exception as exc:
        log.warning("Snapshot pré-%s pour ckpt %s : %s", tool_name, ckpt_id, exc)
        raise HTTPException(500, f"Snapshot échec: {exc}")

    audit_ts = time.time()
    qgz_path = f"/data/studies/{active_sid}/.checkpoints/{ckpt_id}.qgz"

    # Trace dans le journal d'audit (treatments.jsonl) — observabilité unifiée
    if _AUDIT_AVAILABLE:
        await _log_audit_event_on_pod(
            user["username"],
            f"/data/studies/{active_sid}/treatments.jsonl",
            {
                "ts": audit_ts, "kind": "checkpoint",
                "tool": tool_name, "ok": True,
                "summary": f"Snapshot avant {tool_name}",
                "params": {"checkpoint_id": ckpt_id, "session_id": session_id},
                "outputs": [qgz_path],
            },
        )

    return {
        "ok": True,
        "checkpoint_id": ckpt_id,
        "study_id": active_sid,
        "qgz_path": qgz_path,
        "audit_ts": audit_ts,
    }


@app.post("/sessions/purge-checkpoint-files")
async def purge_checkpoint_files(
    request: Request,
    user: dict = Depends(auth.get_current_user),
):
    """Supprime les .qgz snapshots listés sur le PVC user.

    Appelé par la boucle de purge de l'agent (toutes les 6h), juste après
    la suppression des métadonnées SQLite. Sans ça, les .qgz s'accumulent
    indéfiniment sur le PVC (cf. limitation #5 du suivi Stop/Rollback).

    Sécurité : filtre strict des paths pour éviter le path traversal —
    seuls les fichiers dans /data/studies/{sid}/.checkpoints/ sont
    acceptés (le user a accès à son propre PVC mais on durcit quand même).
    """
    body = await request.json()
    paths = body.get("paths", [])
    if not isinstance(paths, list):
        raise HTTPException(400, "paths doit être une liste")
    # Filtre strict : path doit ressembler à /data/studies/<sid>/.checkpoints/<ckpt>.qgz
    safe_paths = [
        p for p in paths
        if isinstance(p, str)
        and p.startswith("/data/studies/")
        and "/.checkpoints/" in p
        and p.endswith(".qgz")
        and ".." not in p
    ]
    if not safe_paths:
        return {"purged": 0, "skipped": len(paths)}
    code = (
        "from pathlib import Path\n"
        f"paths = {safe_paths!r}\n"
        "n_purged = 0\n"
        "for p in paths:\n"
        "    try:\n"
        "        Path(p).unlink()\n"
        "        n_purged += 1\n"
        "    except FileNotFoundError:\n"
        "        pass\n"
        "    except Exception as exc:\n"
        "        print(f'PURGE_ERR {p}: {exc}')\n"
        "print(f'PURGED_QGZ={n_purged}')\n"
    )
    try:
        await _execute_python_in_workspace(user["username"], code, timeout=10)
        return {"purged": len(safe_paths), "skipped": len(paths) - len(safe_paths)}
    except Exception as exc:
        log.warning("Purge .qgz files : %s", exc)
        return {"purged": 0, "skipped": len(paths), "error": str(exc)}


@app.post("/sessions/{session_id}/restore-checkpoint")
async def session_restore_checkpoint(
    session_id: str,
    request: Request,
    user: dict = Depends(auth.get_current_user),
):
    """Restaure le projet QGIS à un checkpoint donné.

    Reçoit {checkpoint_id, study_id}. Exécute restore_checkpoint_pod_code.
    Log audit kind=rollback. L'agent appelle cet endpoint depuis son
    rollback workflow ; il s'occupe ensuite de truncate_messages_after.
    """
    if not _STUDIES_AVAILABLE:
        raise HTTPException(503, "Module studies indisponible")
    body = await request.json()
    ckpt_id = body.get("checkpoint_id", "").strip()
    sid = body.get("study_id", "").strip()
    if not ckpt_id or not sid:
        raise HTTPException(400, "checkpoint_id et study_id requis")

    try:
        await _execute_python_in_workspace(
            user["username"],
            studies.restore_checkpoint_pod_code(sid, ckpt_id),
            timeout=30,
        )
    except Exception as exc:
        log.warning("Restore ckpt %s : %s", ckpt_id, exc)
        raise HTTPException(500, f"Restore échec: {exc}")

    if _AUDIT_AVAILABLE:
        await _log_audit_event_on_pod(
            user["username"],
            f"/data/studies/{sid}/treatments.jsonl",
            {
                "ts": time.time(), "kind": "rollback", "ok": True,
                "summary": f"Restauré au checkpoint {ckpt_id}",
                "params": {"checkpoint_id": ckpt_id, "session_id": session_id},
            },
        )

    return {"ok": True, "checkpoint_id": ckpt_id, "study_id": sid}


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


# ── V1.5 Sprint 1 : Recipes CRUD endpoints ───────────────────────────────────
# Pattern : SQLite hub track metadata + versioning SHA (recipes_index).
#           Contenu YAML/JSON vit sur le PVC workspace `/data/studies/{sid}/recipes/`.
#           Hub <-> Pod via execute_python + markers stdout (clone pattern
#           treatments). Cf. studies.py:save_recipe_pod_code & co.
# Endpoints :
#   GET    /studies/{sid}/recipes                  - liste les recipes actives
#   GET    /studies/{sid}/recipes/{slug}            - lit le contenu YAML/JSON
#   PUT    /studies/{sid}/recipes/{slug}            - save (create ou update -> +version)
#   DELETE /studies/{sid}/recipes/{slug}            - soft delete (archive)
#   GET    /studies/{sid}/recipes/{slug}/history    - toutes les versions

def _parse_recipe_save_marker(stdout: str) -> dict:
    """Parse `RECIPE_SAVE_OK sid=... slug=... fmt=... sha=... path=...`."""
    for line in stdout.splitlines():
        if line.startswith("RECIPE_SAVE_OK"):
            parts = dict(p.split("=", 1) for p in line.split()[1:] if "=" in p)
            return parts
    return {}


def _parse_recipe_read_marker(stdout: str) -> dict:
    """Parse `RECIPE_READ_OK sid=... slug=... fmt=... b64=...` ou NOT_FOUND."""
    import base64
    for line in stdout.splitlines():
        if line.startswith("RECIPE_READ_NOT_FOUND"):
            return {"found": False}
        if line.startswith("RECIPE_READ_OK"):
            parts = dict(p.split("=", 1) for p in line.split()[1:] if "=" in p)
            if "b64" in parts:
                try:
                    parts["content"] = base64.b64decode(parts["b64"]).decode("utf-8")
                except Exception:
                    parts["content"] = ""
            return {"found": True, **parts}
    return {"found": False}


def _parse_recipe_list_marker(stdout: str) -> list[dict]:
    """Parse `RECIPE_LIST_OK <json_array>`."""
    import json as _json
    for line in stdout.splitlines():
        if line.startswith("RECIPE_LIST_OK"):
            payload = line[len("RECIPE_LIST_OK"):].strip()
            try:
                return _json.loads(payload)
            except Exception:
                return []
    return []


@app.get("/studies/{sid}/recipes")
async def list_study_recipes(
    sid: str,
    user: dict = Depends(auth.get_current_user),
):
    """Liste les recipes user d'une etude (latest version uniquement).

    Croise PVC (source de verite contenu) + DB hub (metadata + versioning).
    Si une recipe est sur PVC mais pas en DB (legacy / restore manuel), on
    l'ajoute quand meme avec `source: pvc_only` pour ne pas masquer.
    """
    if not _STUDIES_AVAILABLE:
        raise HTTPException(503, "Module studies indisponible")
    s = await studies.get_study(sid, user["username"])
    if not s:
        raise HTTPException(404, "Etude introuvable")
    # DB : recipes indexees
    db_rows = await studies.recipe_index_list(sid)
    db_by_slug = {r["slug"]: r for r in db_rows}
    # PVC : recipes sur le PVC workspace
    code = studies.list_recipes_pod_code(sid)
    stdout = await _execute_python_in_workspace(user["username"], code)
    pvc_files = _parse_recipe_list_marker(stdout)
    pvc_by_slug = {f["slug"]: f for f in pvc_files}
    # Merge (PVC autoritatif sur la presence ; DB sur les metadata)
    result = []
    for slug, pvc in pvc_by_slug.items():
        meta = db_by_slug.get(slug, {})
        result.append({
            "slug": slug,
            "format": pvc.get("format", "yaml"),
            "size": pvc.get("size", 0),
            "sha": pvc.get("sha", ""),
            "name": meta.get("name") or slug,
            "description": meta.get("description") or "",
            "version_num": meta.get("version_num", 0),
            "published_at": meta.get("published_at"),
            "public_url": meta.get("public_url"),
            "indexed": slug in db_by_slug,
        })
    return {"recipes": result, "count": len(result)}


@app.get("/studies/{sid}/recipes/{slug}")
async def get_study_recipe(
    sid: str,
    slug: str,
    user: dict = Depends(auth.get_current_user),
):
    """Lit le contenu d'une recipe user (YAML ou JSON brut)."""
    if not _STUDIES_AVAILABLE:
        raise HTTPException(503, "Module studies indisponible")
    s = await studies.get_study(sid, user["username"])
    if not s:
        raise HTTPException(404, "Etude introuvable")
    code = studies.read_recipe_pod_code(sid, slug)
    stdout = await _execute_python_in_workspace(user["username"], code)
    parsed = _parse_recipe_read_marker(stdout)
    if not parsed.get("found"):
        raise HTTPException(404, f"Recipe '{slug}' introuvable dans l'etude {sid}")
    meta = await studies.recipe_index_get_latest(sid, slug)
    return {
        "slug": slug,
        "format": parsed.get("fmt", "yaml"),
        "content": parsed.get("content", ""),
        "version_num": meta["version_num"] if meta else 0,
        "sha": meta["sha"] if meta else "",
        "name": meta["name"] if meta else slug,
        "description": meta["description"] if meta else "",
        "published_at": meta["published_at"] if meta else None,
        "public_url": meta["public_url"] if meta else None,
    }


@app.get("/studies/{sid}/recipes/{slug}/history")
async def get_study_recipe_history(
    sid: str,
    slug: str,
    user: dict = Depends(auth.get_current_user),
):
    """Toutes les versions historiques d'une recipe (audit trail)."""
    if not _STUDIES_AVAILABLE:
        raise HTTPException(503, "Module studies indisponible")
    s = await studies.get_study(sid, user["username"])
    if not s:
        raise HTTPException(404, "Etude introuvable")
    rows = await studies.recipe_index_history(sid, slug)
    return {"versions": rows, "count": len(rows)}


@app.put("/studies/{sid}/recipes/{slug}")
async def save_study_recipe(
    sid: str,
    slug: str,
    payload: dict,
    user: dict = Depends(auth.get_current_user),
):
    """Cree ou update une recipe user (chaque save = nouvelle version_num).

    Body : {"content": "<yaml string>", "format": "yaml|json",
            "name": "...", "description": "..."}
    """
    if not _STUDIES_AVAILABLE:
        raise HTTPException(503, "Module studies indisponible")
    s = await studies.get_study(sid, user["username"])
    if not s:
        raise HTTPException(404, "Etude introuvable")
    content = (payload.get("content") or "").strip()
    if not content:
        raise HTTPException(400, "Body champ 'content' obligatoire")
    fmt = payload.get("format", "yaml").lower()
    if fmt not in ("yaml", "json"):
        raise HTTPException(400, "format doit etre 'yaml' ou 'json'")
    # Save sur PVC (recupere le SHA via marker)
    code = studies.save_recipe_pod_code(sid, slug, content, fmt)
    stdout = await _execute_python_in_workspace(user["username"], code)
    marker = _parse_recipe_save_marker(stdout)
    sha = marker.get("sha", "")
    if not sha:
        raise HTTPException(500, f"Save recipe foire (marker absent): {stdout[:200]}")
    # Index en DB (versioning incremental)
    previous = await studies.recipe_index_get_latest(sid, slug)
    rowid = await studies.recipe_index_insert(
        sid=sid, slug=slug, sha=sha, owner=user["username"],
        name=payload.get("name", "") or slug,
        description=payload.get("description", "") or "",
        fmt=fmt,
        previous_sha=previous["sha"] if previous else "",
    )
    latest = await studies.recipe_index_get_latest(sid, slug)
    return {
        "slug": slug,
        "sha": sha,
        "version_num": latest["version_num"] if latest else 1,
        "rowid": rowid,
        "format": fmt,
    }


@app.delete("/studies/{sid}/recipes/{slug}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_study_recipe(
    sid: str,
    slug: str,
    user: dict = Depends(auth.get_current_user),
):
    """Soft delete : archive sur PVC (rename .archived.ts) + status='archived' en DB."""
    if not _STUDIES_AVAILABLE:
        raise HTTPException(503, "Module studies indisponible")
    s = await studies.get_study(sid, user["username"])
    if not s:
        raise HTTPException(404, "Etude introuvable")
    # PVC : rename fichier en .archived.ts
    code = studies.delete_recipe_pod_code(sid, slug)
    await _execute_python_in_workspace(user["username"], code)
    # DB : marque toutes les versions en archived
    await studies.recipe_index_archive(sid, slug)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


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
    # Bug fix 2026-06-27 : stripper l'extension du slug si presente
    # (s3_key() ajoute l'extension selon le kind). L'URL retournee par
    # publish_assembly_endpoint n'en met pas, mais des URL externes
    # (clic user sur lien .html) en mettent. Resilience : accepter les 2.
    for _ext in (".html", ".pdf", ".yaml", ".json", ".gpkg", ".qgz", ".zip"):
        if safe_slug.endswith(_ext):
            safe_slug = safe_slug[:-len(_ext)]
            break
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

    # Injection mini-header CEREMA pour les storymaps HTML.
    # Ajoute une banniere fixe haut de page (32px) avec : "CEREMA QGIS",
    # auteur, etude source si dispo, date de publication. Donne du contexte
    # au lecteur final (livrable plus orphelin). Le content-type doit etre
    # text/html et le contenu doit contenir </body> (ce que generent les
    # exports Leaflet/storymap).
    ct = (meta.get("content_type", "") or "").lower()
    if kind in ("storymap", "flux") and "html" in ct:
        try:
            html_str = content.decode("utf-8", errors="ignore")
            import datetime as _dt
            published_at = meta.get("last_modified") or meta.get("published_at")
            date_str = ""
            if isinstance(published_at, (int, float)):
                date_str = _dt.datetime.fromtimestamp(published_at).strftime("%d/%m/%Y")
            elif published_at:
                try:
                    date_str = str(published_at)[:10]
                except Exception:
                    pass
            banner = (
                '<div id="cerema-publi-banner" style="position:fixed;top:0;left:0;right:0;'
                'height:32px;background:#000091;color:#fff;display:flex;align-items:center;'
                'padding:0 14px;font:13px system-ui,sans-serif;z-index:99999;gap:14px;'
                'box-shadow:0 1px 4px rgba(0,0,0,.15)">'
                '<span style="font-weight:700">CEREMA</span>'
                '<span style="opacity:.7">· QGIS</span>'
                f'<span style="opacity:.85">— Publié par <strong>{owner}</strong></span>'
                + (f'<span style="opacity:.7">· {date_str}</span>' if date_str else '')
                + '<span style="margin-left:auto;opacity:.6;font-size:11px">'
                'Livrable public — généré par l\'agent QGIS CEREMA'
                '</span></div>'
                '<style>body{padding-top:32px !important}</style>'
            )
            if "</body>" in html_str:
                html_str = html_str.replace("</body>", banner + "</body>", 1)
                content = html_str.encode("utf-8")
        except Exception as exc:
            log.warning("Banner injection failed for %s/%s: %s", kind, safe_slug, exc)

    # B5 (Vague B 2026-06-29) : CSP + Cache-Control + iframe permission.
    # Validé par Passerelle-Archi msg 6c517f58 : frame-ancestors * wildcard V1
    # (iframe Atlas Grist + sites tiers CEREMA). Whitelist sources MapLibre +
    # Chart.js (unpkg) + tiles OSM. Cache 1h URL versionnée (slug stable).
    headers = {
        "Cache-Control": "public, max-age=3600",
        "Content-Security-Policy": (
            "default-src 'self' https://unpkg.com https://tile.openstreetmap.org "
            "https://*.minio.lab.sspcloud.fr; "
            "script-src 'self' 'unsafe-inline' https://unpkg.com; "
            "style-src 'self' 'unsafe-inline' https://unpkg.com; "
            "img-src * data: blob:; "
            "connect-src 'self' https://*.minio.lab.sspcloud.fr "
            "https://tile.openstreetmap.org; "
            "frame-ancestors *"
        ),
        "X-Content-Type-Options": "nosniff",
        "Referrer-Policy": "no-referrer",
    }
    return Response(
        content=content,
        media_type=meta.get("content_type", "application/octet-stream"),
        headers=headers,
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


@app.delete("/publications", status_code=status.HTTP_200_OK)
async def purge_all_user_publications(
    user: dict = Depends(auth.get_current_user),
    confirm: str = "",
):
    """Purge TOUTES les publications de l'user authentifie.

    Operation DESTRUCTIVE et IRREVERSIBLE — supprime les fichiers S3 +
    catalogue. Cas d'usage : nettoyage apres tests de dev / artefacts
    residuels qui faussent le compteur "X publications de l'etude".

    Garde-fou : ?confirm=PURGE_ALL (chaine litterale) requis pour eviter
    une suppression accidentelle.

    Reponse : {"owner", "deleted", "total_listed", "errors"}.
    """
    if not _S3_AVAILABLE:
        raise HTTPException(503, "Publication S3 indisponible")
    if confirm != "PURGE_ALL":
        raise HTTPException(
            400,
            "Operation destructive : ajouter ?confirm=PURGE_ALL pour valider.",
        )
    result = s3_publication.purge_all_publications(user["username"])
    log.warning("purge_all_user_publications: %s -> %d supprimees / %d listees",
                user["username"], result["deleted"], result["total_listed"])
    return result


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
    # Cle scopee -> filtrage tools applique au proxy. None (cle superviseur /
    # OIDC) = acces total, aucune interception.
    scope = user.get("scope")
    session = await _get_or_create_session(username)
    target_url = _mcp_url(session, path)
    return await _proxy_request(request, target_url, session["id"], scope=scope)


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

    # Re-hydrater l'etude active dans le pod workspace fraichement cree.
    # Sans ce hook, /sessions cree un pod vierge -> l'agent appelle des tools
    # qui muent un projet vide -> _autosave_active_study ecrit un .qgz vide
    # qui ECRASE le travail precedent. Le bug etait silencieux car le hook
    # n'existait que dans /workspace/wake (clic user). Cf. CHARTE_AGENT §4
    # (etude active = projet charge) et la docstring l.2937 de
    # _auto_activate_active_study_after_wake qui anticipait deja ce cas.
    if _STUDIES_AVAILABLE:
        task = asyncio.create_task(
            _auto_activate_active_study_after_wake(user["username"])
        )
        _background_anchors.add(task)
        task.add_done_callback(_background_anchors.discard)

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


@app.post("/admin/agent-config")
async def update_agent_config(request: Request):
    """Met à jour HUB_API_KEY et LLM_API_KEY dans le pod agent et redémarre."""
    admin_token = os.getenv("ADMIN_TOKEN", "")
    auth_header = request.headers.get("Authorization", "")
    if admin_token and auth_header != f"Bearer {admin_token}":
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Admin token requis")

    body = await request.json()
    hub_api_key = body.get("hub_api_key", "")
    llm_api_key = body.get("llm_api_key", "")
    portal_url  = body.get("portal_url", "")

    token_file = Path("/var/run/secrets/kubernetes.io/serviceaccount/token")
    ns_file = Path("/var/run/secrets/kubernetes.io/serviceaccount/namespace")
    if not token_file.exists():
        return JSONResponse({"ok": False, "error": "not in K8s"})

    token = token_file.read_text().strip()
    ns = ns_file.read_text().strip()
    headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}

    async with httpx.AsyncClient(verify=False, timeout=15) as client:
        # Lire env actuelles du StatefulSet agent
        r = await client.get(
            f"{_K8S_HOST}/apis/apps/v1/namespaces/{ns}/statefulsets/qgis-agent",
            headers=headers,
        )
        if r.status_code != 200:
            return JSONResponse({"ok": False, "error": "agent not found"})

        existing_env = (r.json().get("spec", {}).get("template", {})
                        .get("spec", {}).get("containers", [{}])[0].get("env", []))
        # PORTAL_URL est patché à la volée pour que le bandeau "clé LLM
        # manquante" côté UI agent puisse appeler la popup refresh sur
        # le bon portail (cross-origin maîtrisé).
        patch_vars = {
            "HUB_API_KEY": hub_api_key,
            "LLM_API_KEY": llm_api_key,
            "PORTAL_URL":  portal_url,
        }
        # Fix 2026-06-05 : ne retirer du existing_env QUE les vars qu'on va
        # effectivement remplacer (valeur non vide). Sinon le filter ci-dessous
        # supprimait HUB_API_KEY (valueFrom secretKeyRef du Secret partage)
        # quand le portail envoyait hub_api_key="" -> agent demarrait sans
        # HUB_API_KEY -> calls /mcp en 401 silencieux. Cas observe E2E avec
        # nicolaslaval : DB portail purgee -> _get_user_hub_creds None ->
        # fallback get_api_key echoue -> hub_api_key="" envoye au hub ->
        # secretKeyRef ecrase.
        keys_to_replace = {k for k, v in patch_vars.items() if v}
        new_env = [e for e in existing_env if e["name"] not in keys_to_replace]
        new_env.extend([{"name": k, "value": v} for k, v in patch_vars.items() if v])

        patch_headers = {**headers,
                         "Content-Type": "application/strategic-merge-patch+json"}
        await client.patch(
            f"{_K8S_HOST}/apis/apps/v1/namespaces/{ns}/statefulsets/qgis-agent",
            headers=patch_headers,
            json={"spec": {"template": {"spec": {
                "containers": [{"name": "agent", "env": new_env}],
            }}}},
        )
        # Redémarrer le pod
        try:
            del_headers = {k: v for k, v in headers.items() if k != "Content-Type"}
            await client.delete(
                f"{_K8S_HOST}/api/v1/namespaces/{ns}/pods/qgis-agent-0",
                headers=del_headers,
            )
            log.info("admin/agent-config: pod qgis-agent-0 supprimé pour redémarrage")
        except Exception as exc:
            log.warning("admin/agent-config: suppression pod échouée: %s", exc)

    return JSONResponse({"ok": True})


@app.get("/api/refresh-llm-config", response_class=HTMLResponse)
@app.post("/api/refresh-llm-config")
async def refresh_llm_config(request: Request):
    """Re-lit la config LLM SSPCloud et resynchronise l'agent.

    Declenche par le bouton "Verifier ma config" du bandeau agent quand
    l'utilisateur vient de renseigner sa cle dans datalab.sspcloud.fr/account
    (section AI Assistant). L'agent UI ouvre cette URL dans une popup
    courte (cross-origin : seul le portail a le cookie OIDC permettant
    d'agir sur le namespace).

    Mecanisme : rappelle `_bootstrap_agent()`, qui relit
    `*-secretassistant/config.json` du namespace, patche l'env du SS
    qgis-agent (LLM_API_KEY/LLM_MODEL/LLM_BASE_URL) et supprime le pod
    qgis-agent-0 -> redemarrage avec la cle fraiche.
    """
    try:
        await _bootstrap_agent()
        return HTMLResponse(
            "<!doctype html><html lang=fr><meta charset=utf-8>"
            "<title>Cle LLM resynchronisee</title>"
            "<style>body{font:14px system-ui;padding:24px;color:#333}</style>"
            "<body><p><strong>Configuration LLM rechargee.</strong></p>"
            "<p>L'agent redemarre avec la nouvelle cle. Vous pouvez "
            "fermer cette fenetre.</p>"
            "<script>setTimeout(()=>window.close(),1500)</script>"
        )
    except Exception as exc:
        log.exception("refresh-llm-config: %s", exc)
        return HTMLResponse(
            f"<!doctype html><html lang=fr><meta charset=utf-8>"
            f"<body><p>Erreur de resynchronisation : {exc}</p>",
            status_code=500,
        )


@app.get("/admin/workspace-info")
async def admin_workspace_info(request: Request):
    """Diagnostic : retourne l'image et l'état du pod workspace pour debug.

    Pas d'auth (ADMIN_TOKEN optionnel) : lecture seule, namespace local uniquement.
    """
    admin_token = os.getenv("ADMIN_TOKEN", "")
    auth_header = request.headers.get("Authorization", "")
    if admin_token and auth_header != f"Bearer {admin_token}":
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Admin token requis")

    token_file = Path("/var/run/secrets/kubernetes.io/serviceaccount/token")
    ns_file = Path("/var/run/secrets/kubernetes.io/serviceaccount/namespace")
    if not token_file.exists():
        return JSONResponse({"ok": False, "error": "not in K8s"})

    token = token_file.read_text().strip()
    ns = ns_file.read_text().strip()
    headers = {"Authorization": f"Bearer {token}"}

    out: dict = {"namespace": ns, "default_image": sessions._QGIS_IMAGE}

    async with httpx.AsyncClient(verify=False, timeout=15) as client:
        # Liste StatefulSets workspace
        r = await client.get(
            f"{_K8S_HOST}/apis/apps/v1/namespaces/{ns}/statefulsets",
            headers=headers,
            params={"labelSelector": "app=qgis-workspace"},
        )
        if r.status_code != 200:
            out["error"] = f"list SS http={r.status_code}"
            return JSONResponse(out)
        items = r.json().get("items", [])
        out["statefulsets"] = []
        for ss in items:
            name = ss.get("metadata", {}).get("name")
            spec = ss.get("spec", {})
            template = spec.get("template", {}).get("spec", {})
            containers = template.get("containers", [{}])
            img = containers[0].get("image") if containers else None
            replicas = spec.get("replicas")
            status_obj = ss.get("status", {})
            out["statefulsets"].append({
                "name": name,
                "image": img,
                "replicas": replicas,
                "readyReplicas": status_obj.get("readyReplicas"),
                "currentReplicas": status_obj.get("currentReplicas"),
            })

            # État du pod {name}-0
            pr = await client.get(
                f"{_K8S_HOST}/api/v1/namespaces/{ns}/pods/{name}-0",
                headers=headers,
            )
            if pr.status_code == 200:
                pod = pr.json()
                phase = pod.get("status", {}).get("phase")
                cs = pod.get("status", {}).get("containerStatuses", []) or []
                cs_summary = []
                for c in cs:
                    state = c.get("state", {})
                    waiting = state.get("waiting") or {}
                    terminated = state.get("terminated") or {}
                    cs_summary.append({
                        "ready": c.get("ready"),
                        "restartCount": c.get("restartCount"),
                        "waiting_reason": waiting.get("reason"),
                        "waiting_message": waiting.get("message"),
                        "terminated_reason": terminated.get("reason"),
                    })
                out["statefulsets"][-1]["pod_phase"] = phase
                out["statefulsets"][-1]["pod_containers"] = cs_summary
            elif pr.status_code == 404:
                out["statefulsets"][-1]["pod_phase"] = "absent"
            else:
                out["statefulsets"][-1]["pod_error"] = pr.status_code

    return JSONResponse(out)


@app.post("/admin/workspace-fix-image")
async def admin_workspace_fix_image(request: Request):
    """Patche l'image des StatefulSets workspace existants vers _QGIS_IMAGE
    (la valeur actuelle dans sessions.py) et supprime le pod pour forcer un re-pull.

    Utile quand un workspace a été créé avec une image privée inaccessible.
    """
    admin_token = os.getenv("ADMIN_TOKEN", "")
    auth_header = request.headers.get("Authorization", "")
    if admin_token and auth_header != f"Bearer {admin_token}":
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Admin token requis")

    body = {}
    try:
        body = await request.json()
    except Exception:
        pass
    target_image = body.get("image") or sessions._QGIS_IMAGE

    token_file = Path("/var/run/secrets/kubernetes.io/serviceaccount/token")
    ns_file = Path("/var/run/secrets/kubernetes.io/serviceaccount/namespace")
    if not token_file.exists():
        return JSONResponse({"ok": False, "error": "not in K8s"})

    token = token_file.read_text().strip()
    ns = ns_file.read_text().strip()
    headers = {"Authorization": f"Bearer {token}"}
    patch_headers = {**headers,
                     "Content-Type": "application/strategic-merge-patch+json"}

    patched: list = []
    async with httpx.AsyncClient(verify=False, timeout=20) as client:
        r = await client.get(
            f"{_K8S_HOST}/apis/apps/v1/namespaces/{ns}/statefulsets",
            headers=headers,
            params={"labelSelector": "app=qgis-workspace"},
        )
        if r.status_code != 200:
            return JSONResponse({"ok": False, "error": f"list SS http={r.status_code}"})
        items = r.json().get("items", [])
        for ss in items:
            name = ss.get("metadata", {}).get("name")
            if not name:
                continue
            # Patch container image + s'assurer que replicas=1
            pr = await client.patch(
                f"{_K8S_HOST}/apis/apps/v1/namespaces/{ns}/statefulsets/{name}",
                headers=patch_headers,
                json={"spec": {
                    "replicas": 1,
                    "template": {"spec": {
                        "containers": [{
                            "name": "qgis",
                            "image": target_image,
                            "imagePullPolicy": "Always",
                        }],
                    }},
                }},
            )
            # Supprimer le pod pour forcer un re-pull immédiat
            try:
                del_headers = {k: v for k, v in headers.items() if k != "Content-Type"}
                await client.delete(
                    f"{_K8S_HOST}/api/v1/namespaces/{ns}/pods/{name}-0",
                    headers=del_headers,
                )
            except Exception:
                pass
            patched.append({"name": name, "patch_http": pr.status_code,
                            "image": target_image})

    return JSONResponse({"ok": True, "patched": patched})


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
            # HUB_URL + HUB_API_KEY sont désormais injectés de façon centralisée
            # dans sessions.create_session (tous les chemins de création), donc
            # plus besoin de les passer ici explicitement.
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


# ── Enforcement scope (cles scopees) au proxy MCP ─────────────────────────────
# Quand une cle scopee porte une whitelist de tools, le hub filtre le flux MCP
# JSON-RPC : retire les tools hors whitelist de `tools/list` et rejette les
# `tools/call` vers un tool non autorise. Pour `tools:all` / cle superviseur
# (scope None ou mode supervisor) -> aucune interception, zero overhead.
# NB: l'injection sid/pid (binding etude/projet) n'est PAS faite ici — elle
# depend du schema des tools (lesquels acceptent sid/pid) et sera coordonnee
# avec Composants (native_tools_v2 / describe_entity_schema) en etape ulterieure.

def _scope_tools_whitelist(scope: dict | None) -> list | None:
    """Whitelist de tools si la cle est scopee ET restreinte, sinon None
    (None = pas de filtrage : superviseur ou tools == "all")."""
    if not scope or scope.get("mode") == "supervisor":
        return None
    tools = scope.get("tools")
    return tools if isinstance(tools, list) else None


def _jsonrpc_obj(body: bytes) -> dict | None:
    """Parse un corps JSON-RPC objet unique. None si invalide/batch."""
    try:
        obj = json.loads(body)
    except Exception:
        return None
    return obj if isinstance(obj, dict) else None


def _tool_call_denied(obj: dict, whitelist: list) -> JSONResponse | None:
    """Reponse d'erreur JSON-RPC (HTTP 200) si tools/call vers un tool hors
    whitelist, sinon None (autorise)."""
    name = (obj.get("params") or {}).get("name")
    if name is not None and name not in whitelist:
        return JSONResponse(
            {
                "jsonrpc": "2.0",
                "id": obj.get("id"),
                "error": {
                    "code": -32601,
                    "message": (
                        f"Tool '{name}' non autorise pour cet agent "
                        f"(scope restreint a {len(whitelist)} tools)."
                    ),
                },
            },
            status_code=200,
        )
    return None


def _filter_tools_list_payload(raw: bytes, content_type: str, whitelist: list) -> bytes:
    """Retire les tools hors whitelist d'une reponse tools/list (JSON ou SSE)."""
    wl = set(whitelist)

    def _filt(obj):
        try:
            tools = (obj.get("result") or {}).get("tools")
            if isinstance(tools, list):
                obj["result"]["tools"] = [t for t in tools if t.get("name") in wl]
        except Exception:
            pass
        return obj

    text = raw.decode("utf-8", "replace")
    if "text/event-stream" in (content_type or ""):
        out = []
        for line in text.split("\n"):
            if line.startswith("data:"):
                payload = line[5:].strip()
                try:
                    out.append("data: " + json.dumps(_filt(json.loads(payload))))
                except Exception:
                    out.append(line)
            else:
                out.append(line)
        return "\n".join(out).encode("utf-8")
    try:
        return json.dumps(_filt(json.loads(text))).encode("utf-8")
    except Exception:
        return raw


async def _proxy_request(
    request: Request, target_url: str, session_id: str, scope: dict | None = None,
) -> Response:
    """Proxy HTTP vers un pod de session (JSON ou SSE stream).

    `scope` (cle scopee) optionnel : si une whitelist de tools s'applique, le
    flux MCP est filtre (gate tools/call + filtrage tools/list). Sinon le proxy
    reste transparent (comportement historique).
    """
    _skip_headers = {"host", "connection", "transfer-encoding", "te", "trailers", "upgrade"}
    headers = {k: v for k, v in request.headers.items() if k.lower() not in _skip_headers}
    body = await request.body()
    params = dict(request.query_params)

    # Enforcement scope : cote requete (gate tools/call) + flag filtrage reponse.
    whitelist = _scope_tools_whitelist(scope)
    _filter_tools_list = False
    if whitelist is not None and body:
        obj = _jsonrpc_obj(body)
        if obj is not None:
            method = obj.get("method")
            if method == "tools/call":
                denied = _tool_call_denied(obj, whitelist)
                if denied is not None:
                    return denied
            elif method == "tools/list":
                _filter_tools_list = True

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

    # Filtrage tools/list : reponse courte -> on bufferise, filtre, renvoie en
    # one-shot (pas de stream). Tout autre flux reste streame (zero overhead).
    if _filter_tools_list and whitelist is not None:
        try:
            raw = await resp.aread()
        finally:
            await resp.aclose()
            await client.aclose()
            await sessions.touch_session(session_id)
        filtered = _filter_tools_list_payload(raw, content_type, whitelist)
        return Response(
            content=filtered,
            status_code=resp.status_code,
            media_type=content_type,
            headers=resp_headers,
        )

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
    """Bureau de travail unifié : sidebar études | canvas QGIS noVNC | chat agent.

    UX wish 2026-06-26 (user feedback Sprint Composants) : déclenche
    automatiquement le wake du workspace QGIS au chargement de /desk.
    L'iframe noVNC du bureau dépend du workspace pod : si endormi
    (scale-to-zero), l'iframe affiche "no available server" pendant
    plusieurs secondes. En lançant /workspace/wake en background dès le
    GET /desk, le workspace est en cours de réveil quand l'user voit la
    page → soit déjà ready, soit prêt en quelques secondes au lieu de
    30-60s sans pre-wake.
    """
    if not _jinja:
        raise HTTPException(503, "Templates non disponibles")

    # Auto-wake workspace en background — best-effort, ignore les erreurs.
    # Self-call HTTP vers /workspace/wake (qui gere deja le lock + scale + DB
    # active_study). Fire-and-forget, ne bloque pas le rendu de /desk.
    try:
        api_key_local = await auth.create_or_get_api_key(_ONYXIA_USER)
        async def _auto_wake_bg():
            try:
                async with httpx.AsyncClient(timeout=30, base_url=_SELF_URL) as c:
                    await c.post(
                        "/workspace/wake",
                        headers={"Authorization": f"Bearer {api_key_local}"},
                    )
            except Exception as exc:
                log.warning("auto-wake on /desk : %s", exc)
        task = asyncio.create_task(_auto_wake_bg())
        _background_anchors.add(task)
        task.add_done_callback(_background_anchors.discard)
    except Exception:
        pass  # never block /desk rendering

    ctx = await _desk_context()
    return _jinja.TemplateResponse(request, "desk.html", ctx)


@app.get("/workspace", response_class=HTMLResponse)
async def workspace_page(request: Request):
    """Vue études + catalogue + accès outils."""
    if not _jinja:
        raise HTTPException(503, "Templates non disponibles")
    ctx = await _desk_context()
    return _jinja.TemplateResponse(request, "workspace.html", ctx)


# Anchor des tâches background lancées par /workspace/wake — sinon asyncio
# peut les GC avant qu'elles se terminent (les références faibles ne suffisent
# pas dans certaines configs uvicorn). On les garde tant qu'elles tournent.
_background_anchors: set = set()

# Locks par owner pour serialiser les operations de creation/reveil
# workspace. Sans ca, deux requetes /workspace/wake en rafale (double-clic
# bouton, popup + redirect, retry navigation) peuvent declencher deux
# kubectl apply en parallele. _kubectl_apply est idempotent (le manifest
# resultant est le meme) mais on garde la barriere pour eviter le bruit
# logs et garantir une seule sequence "creation Service -> SS -> Ingress
# -> background hook auto-activate" a la fois.
_workspace_locks: dict = {}


def _wake_lock(owner: str) -> asyncio.Lock:
    """Retourne le lock partage pour les operations workspace d'un owner."""
    lock = _workspace_locks.get(owner)
    if lock is None:
        lock = asyncio.Lock()
        _workspace_locks[owner] = lock
    return lock


async def _auto_activate_active_study_after_wake(owner: str):
    """Au réveil du workspace, recharge le projet QGIS de l'étude active.

    Sans ce hook, le pod redémarre avec un projet vierge alors que l'utilisateur
    avait une étude active : l'iframe noVNC affiche QGIS vide, l'utilisateur
    pense que son travail est perdu. Lancé en background pour ne pas bloquer
    le redirect du wake.
    """
    if not _STUDIES_AVAILABLE:
        return
    try:
        active_sid = await studies.get_active_study_id(owner)
        if not active_sid:
            return
        log.info("Auto-activate étude %s après wake (background)", active_sid)
        await _execute_python_in_workspace(
            owner, studies.activate_pod_code(active_sid), timeout=60,
        )
    except Exception as exc:
        log.warning("Auto-activate post-wake : %s", exc)


@app.post("/workspace/wake")
@app.get("/workspace/wake")
async def workspace_wake(request: Request):
    """Réveille le workspace QGIS endormi (scale 0→1) + recharge l'étude active.

    Serialise via _wake_lock pour proteger des doubles appels (double-clic
    bouton, popup+redirect concurrents). Si le workspace est deja en cours
    de reveil ou ready, la seconde requete attend la fin de la premiere
    puis retourne immediatement (idempotent).
    """
    return_to = request.query_params.get("return_to", "")
    lock = _wake_lock(_ONYXIA_USER)
    async with lock:
        was_ready_before = False
        try:
            api_key = await auth.create_or_get_api_key(_ONYXIA_USER)
            async with httpx.AsyncClient(timeout=30, base_url=_SELF_URL) as c:
                # Sprint UX-3 optim (2026-06-21) : check status AVANT de
                # creer/relancer la session. Si deja ready, on saute le
                # _auto_activate background (pod n'a pas redemarre, le
                # projet QGIS courant est deja loaded). Reduit le wake d'un
                # pod ready de ~1.5s + flicker canvas a ~50ms (sessions GET).
                try:
                    r = await c.get(
                        "/sessions",
                        headers={"Authorization": f"Bearer {api_key}"},
                    )
                    if r.status_code == 200:
                        rows = r.json() or []
                        was_ready_before = bool(
                            rows and rows[0].get("status") == "ready"
                        )
                except Exception:
                    pass
                await c.post(
                    "/sessions",
                    headers={"Authorization": f"Bearer {api_key}"},
                    json={},
                )
        except Exception:
            pass
        # Recharge le projet QGIS de l'étude active dès que le pod est prêt
        # (en background — le wait pod ready peut prendre 30-60s). Skip si
        # le pod etait deja ready avant le wake (idempotent : projet QGIS
        # toujours loaded en RAM, pas besoin de re-execute_python coûteux).
        if not was_ready_before:
            task = asyncio.create_task(
                _auto_activate_active_study_after_wake(_ONYXIA_USER)
            )
            _background_anchors.add(task)
            task.add_done_callback(_background_anchors.discard)
        else:
            log.debug(
                "wake: pod was_ready_before -> skip _auto_activate background"
            )
    if return_to == "desk":
        return RedirectResponse("/desk", status_code=302)
    return {"ok": True}


@app.post("/workspace/study/new")
async def workspace_create_study(request: Request):
    form = await request.form()
    name = form.get("name", "").strip()
    profile = form.get("profile", "standard")
    return_to = request.query_params.get("return_to", "")
    target = "/desk" if return_to == "desk" else "/workspace"
    if not name:
        return RedirectResponse(f"{target}?error=name_required", status_code=302)
    # Bug #14 V1.1 : ancien code masquait toute exception silencieusement -> on
    # ne savait pas pourquoi l'etude restait inactive apres creation. On log
    # explicitement chaque erreur + retry 2x sur l'activation (souvent
    # transitoire si la table active_study n'est pas encore initialisee).
    try:
        api_key = await auth.create_or_get_api_key(_ONYXIA_USER)
        # Bug B fix v2 (2026-06-02) : timeout 15s -> 60s. Le endpoint /studies
        # appelle _execute_python_in_workspace(init_pod_layout_code) qui a un
        # timeout MCP interne de 30s (cf. L1656). Quand le pod workspace est
        # endormi (scale=0), le MCP attend 30s avant de timeout, ce qui faisait
        # un ReadTimeout cote ce client (timeout 15s) -> exception inattendue ->
        # ?error=exception alors que l'etude EST creee. Avec 60s on absorbe le
        # worst case + le wake auto qu'on declenche apres a le temps de demarrer.
        async with httpx.AsyncClient(timeout=60, base_url=_SELF_URL) as c:
            r = await c.post("/studies",
                             headers={"Authorization": f"Bearer {api_key}"},
                             json={"name": name, "profile": profile})
            if r.status_code not in (200, 201):
                log.error("workspace_create_study: POST /studies HTTP %d : %s",
                          r.status_code, r.text[:200])
                return RedirectResponse(f"{target}?error=create_failed",
                                        status_code=302)
            new_id = r.json().get("id")
            if not new_id:
                log.error("workspace_create_study: POST /studies sans 'id' : %s",
                          r.text[:200])
                return RedirectResponse(f"{target}?error=no_id", status_code=302)
            # Activation avec retry — _validate_api_key peut etre transitoirement
            # KO si le Secret K8s vient d'etre cree (cache 5min stale).
            activate_ok = False
            for attempt in range(2):
                try:
                    ar = await c.post(
                        f"/studies/{new_id}/activate",
                        headers={"Authorization": f"Bearer {api_key}"},
                    )
                    if ar.status_code in (200, 201, 204):
                        activate_ok = True
                        break
                    log.warning(
                        "workspace_create_study: activate %s try %d/2 HTTP %d : %s",
                        new_id, attempt + 1, ar.status_code, ar.text[:200],
                    )
                except Exception as exc:
                    log.warning(
                        "workspace_create_study: activate %s try %d/2 exception : %s",
                        new_id, attempt + 1, exc,
                    )
                await asyncio.sleep(0.5)
            if not activate_ok:
                log.error(
                    "workspace_create_study: activation %s echec apres 2 tentatives "
                    "— l'etude existe mais n'est PAS active. User devra l'activer "
                    "manuellement via /workspace.", new_id,
                )
                return RedirectResponse(f"{target}?error=activate_failed",
                                        status_code=302)
            log.info("workspace_create_study: etude %s (%s) creee + activee OK",
                     new_id, name[:30])

            # Bug B fix (2026-06-01) : la création + activation appellent
            # `_execute_python_in_workspace` pour init_pod_layout_code et
            # activate_pod_code (qui crée le projet QGIS vide si absent).
            # Mais ces appels MCP échouent silencieusement quand le pod
            # workspace est endormi (scale=0). Résultat observé en E2E :
            # l'utilisateur arrive sur /desk avec une étude active mais
            # le QGIS Desktop reste endormi et sans projet ouvert -> doit
            # cliquer manuellement "Réveiller le bureau" puis "Nouveau
            # projet".
            # Solution : déclencher /workspace/wake en fire-and-forget. Le
            # endpoint wake :
            #   1. Scale le SS workspace 0->1 (apply manifest idempotent)
            #   2. Lance _auto_activate_active_study_after_wake() en
            #      background qui attend que le pod soit Ready puis appelle
            #      activate_pod_code(active_sid) -> charge le .qgz existant
            #      ou crée un projet QGIS vide + setFileName.
            # Ainsi quand l'utilisateur atterrit sur /desk, le workspace
            # se réveille tout seul et le projet QGIS s'ouvre tout seul.
            try:
                await c.post("/workspace/wake",
                             headers={"Authorization": f"Bearer {api_key}"})
                log.info("workspace_create_study: wake auto déclenché pour étude %s",
                         new_id)
            except Exception as wake_exc:
                # Non-fatal — l'utilisateur peut toujours cliquer "Réveiller
                # le bureau" manuellement depuis /desk.
                log.warning(
                    "workspace_create_study: wake auto échoué (non-fatal, "
                    "user peut wake manuel depuis /desk) : %s", wake_exc,
                )
    except Exception as exc:
        log.exception("workspace_create_study: exception inattendue : %s", exc)
        return RedirectResponse(f"{target}?error=exception", status_code=302)
    return RedirectResponse(target, status_code=302)


@app.post("/workspace/study/{sid}/activate")
async def workspace_activate_study(sid: str, request: Request):
    try:
        api_key = await auth.create_or_get_api_key(_ONYXIA_USER)
        # Bug B fix v2 (2026-06-02) : timeout 15s -> 60s pour absorber le worst
        # case ou _execute_python_in_workspace (appele par /studies/{sid}/activate
        # via activate_pod_code) timeout 30s MCP quand le workspace est endormi.
        async with httpx.AsyncClient(timeout=60, base_url=_SELF_URL) as c:
            await c.post(f"/studies/{sid}/activate",
                         headers={"Authorization": f"Bearer {api_key}"})
            # Bug B fix (2026-06-01) : trigger wake aussi à l'activation
            # d'une étude existante depuis l'UI. Même raison que dans
            # workspace_create_study — sans wake, le _execute_python_in_workspace
            # de activate_pod_code échoue silencieusement et le projet QGIS de
            # l'étude n'est pas chargé. Idempotent : si le workspace est déjà
            # Ready, _auto_activate_active_study_after_wake ré-exécutera
            # activate_pod_code (qui se contente de read le .qgz à nouveau).
            try:
                await c.post("/workspace/wake",
                             headers={"Authorization": f"Bearer {api_key}"})
            except Exception as wake_exc:
                log.warning(
                    "workspace_activate_study: wake auto échoué (non-fatal) : %s",
                    wake_exc,
                )
    except Exception:
        pass
    return_to = request.query_params.get("return_to", "")
    target = "/desk" if return_to == "desk" else "/workspace"
    return RedirectResponse(target, status_code=302)


@app.post("/workspace/study/{sid}/archive")
async def workspace_archive_study(sid: str, request: Request):
    try:
        api_key = await auth.create_or_get_api_key(_ONYXIA_USER)
        async with httpx.AsyncClient(timeout=15, base_url=_SELF_URL) as c:
            await c.delete(f"/studies/{sid}",
                           headers={"Authorization": f"Bearer {api_key}"})
    except Exception:
        pass
    return_to = request.query_params.get("return_to", "")
    target = "/desk" if return_to == "desk" else "/workspace"
    return RedirectResponse(target, status_code=302)


# ── Sprint UX-3 Commit 3 : UI wrappers projects (form POST -> 302 redirect) ──
# Pattern strictement parallele aux workspace_activate_study + workspace_create_study.
# Permet aux formulaires dans desk.html dropdown de fonctionner sans JS fetch.

@app.post("/workspace/project/{sid}/{pid}/activate")
async def workspace_activate_project(sid: str, pid: str, request: Request):
    """UI wrapper : POST form depuis le dropdown desk -> activate project +
    redirect /desk (ou /workspace selon return_to)."""
    try:
        api_key = await auth.create_or_get_api_key(_ONYXIA_USER)
        async with httpx.AsyncClient(timeout=60, base_url=_SELF_URL) as c:
            await c.post(
                f"/studies/{sid}/projects/{pid}/activate",
                headers={"Authorization": f"Bearer {api_key}"},
            )
            # Wake si endormi (meme rationale que workspace_activate_study)
            try:
                await c.post(
                    "/workspace/wake",
                    headers={"Authorization": f"Bearer {api_key}"},
                )
            except Exception as wake_exc:
                log.warning(
                    "workspace_activate_project: wake echoue (non-fatal): %s",
                    wake_exc,
                )
    except Exception as exc:
        log.warning("workspace_activate_project sid=%s pid=%s: %s", sid, pid, exc)
    return_to = request.query_params.get("return_to", "")
    target = "/desk" if return_to == "desk" else "/workspace"
    return RedirectResponse(target, status_code=302)


@app.post("/workspace/project/{sid}/new")
async def workspace_create_project(sid: str, request: Request):
    """UI wrapper : creer un nouveau projet dans l'etude + l'activer + redirect.

    Body form-urlencoded : label (required), is_default (optional checkbox).
    Apres creation, le nouveau projet devient automatiquement actif -> l'user
    voit son nouveau projet vide dans le canvas.
    """
    try:
        form = await request.form()
    except Exception:
        form = {}
    label = (form.get("label") or "").strip() or "Nouveau projet"
    is_default = bool(form.get("is_default"))
    new_pid = None
    try:
        api_key = await auth.create_or_get_api_key(_ONYXIA_USER)
        async with httpx.AsyncClient(timeout=60, base_url=_SELF_URL) as c:
            r = await c.post(
                f"/studies/{sid}/projects",
                json={"label": label, "is_default": is_default},
                headers={"Authorization": f"Bearer {api_key}"},
            )
            if r.status_code in (200, 201):
                new_pid = r.json().get("pid")
                # Active le nouveau projet immediatement (effet 'on cree + on
                # bascule' = comportement attendu UX).
                if new_pid:
                    await c.post(
                        f"/studies/{sid}/projects/{new_pid}/activate",
                        headers={"Authorization": f"Bearer {api_key}"},
                    )
            try:
                await c.post(
                    "/workspace/wake",
                    headers={"Authorization": f"Bearer {api_key}"},
                )
            except Exception:
                pass
    except Exception as exc:
        log.warning("workspace_create_project sid=%s: %s", sid, exc)
    return_to = request.query_params.get("return_to", "")
    target = "/desk" if return_to == "desk" else "/workspace"
    return RedirectResponse(target, status_code=302)


@app.post("/desk/study/{sid}/save")
async def desk_save_study(sid: str):
    """Sauve le projet QGIS de l'étude `sid` (appelé par le beforeunload
    sendBeacon du desk.html).

    Endpoint same-origin pour que `navigator.sendBeacon` côté browser ne
    soit pas bloqué CORS. Délègue à studies.save_active_pod_code via
    execute_python (même path que /studies/{sid}/save, mais sans auth
    Bearer requise puisqu'on est sur l'origine hub avec cookie/CSRF).
    """
    if not _STUDIES_AVAILABLE:
        return {"ok": False}
    try:
        await _execute_python_in_workspace(
            _ONYXIA_USER, studies.save_active_pod_code(sid), timeout=15,
        )
        return {"ok": True, "sid": sid}
    except Exception as exc:
        log.warning("Save desk étude %s : %s", sid, exc)
        return {"ok": False, "error": str(exc)}


# ── Proxy mémoire vers l'agent IA ─────────────────────────────────────────────

async def _agent_call(method: str, path: str, **kwargs):
    """Proxifie un appel vers le pod agent IA.

    Fix consolidation 2026-06-19 : ajoute Authorization Bearer HUB_API_KEY
    pour que le middleware OIDC agent (Phase 0ter Steps 7-8) autorise via
    sa whitelist inter-pod. Sans cela, les calls /desk/memory etc.
    retournent 401 silencieux -> panneau Memoire UI affiche "Chargement..."
    indefiniment puis sections editables manquantes.
    """
    api_key = await auth.create_or_get_api_key(_ONYXIA_USER)
    headers = dict(kwargs.pop("headers", {}) or {})
    headers.setdefault("Authorization", f"Bearer {api_key}")
    async with httpx.AsyncClient(timeout=10, base_url=_AGENT_URL or "http://127.0.0.1:8100") as c:
        return await c.request(method, path, headers=headers, **kwargs)


# ── V1.5 Sprint 1.3 : proxies /desk/recipes/* pour la UI desk ────────────────
# Pattern symetrique a /desk/study-files (auth implicite via _ONYXIA_USER +
# middleware OIDC Phase 0ter qui bloque les strangers). La UI desk dans le
# browser n'a PAS de cookie hub_api_key (set seulement via /login?key=...),
# donc on lui offre ces endpoints sans Depends(auth.get_current_user).
# Logique metier deleguee aux fonctions studies.recipe_index_* (S1.1).

@app.get("/desk/recipes")
async def desk_recipes_list():
    """UI desk : liste les recipes user de l'etude active (latest active)."""
    if not _STUDIES_AVAILABLE:
        return {"recipes": [], "count": 0}
    active_sid = await studies.get_active_study_id(_ONYXIA_USER)
    if not active_sid:
        return {"recipes": [], "count": 0}
    db_rows = await studies.recipe_index_list(active_sid)
    db_by_slug = {r["slug"]: r for r in db_rows}
    # PVC : best-effort (timeout court), fallback DB-only si workspace endormi
    try:
        all_sessions = await sessions.list_sessions(_ONYXIA_USER)
        if all_sessions and all_sessions[0].get("status") == sessions.SESSION_READY:
            code = studies.list_recipes_pod_code(active_sid)
            stdout = await _execute_python_in_workspace(_ONYXIA_USER, code)
            pvc_files = _parse_recipe_list_marker(stdout)
            pvc_by_slug = {f["slug"]: f for f in pvc_files}
        else:
            pvc_by_slug = {}
    except Exception:
        pvc_by_slug = {}
    # Union DB + PVC (preferer PVC pour size+sha, DB pour name+version)
    slugs = set(db_by_slug.keys()) | set(pvc_by_slug.keys())
    result = []
    for slug in sorted(slugs):
        meta = db_by_slug.get(slug, {})
        pvc = pvc_by_slug.get(slug, {})
        # Si workspace dort : on a juste les meta DB
        result.append({
            "slug": slug,
            "format": pvc.get("format") or meta.get("format", "yaml"),
            "size": pvc.get("size", 0),
            "sha": pvc.get("sha") or meta.get("sha", ""),
            "name": meta.get("name") or slug,
            "description": meta.get("description") or "",
            "version_num": meta.get("version_num", 0),
            "published_at": meta.get("published_at"),
            "public_url": meta.get("public_url"),
            "indexed": slug in db_by_slug,
        })
    return {"recipes": result, "count": len(result), "sid": active_sid}


@app.get("/desk/recipes/{slug}")
async def desk_recipe_get(slug: str):
    """UI desk : lit le contenu YAML/JSON d'une recipe user de l'etude active."""
    if not _STUDIES_AVAILABLE:
        raise HTTPException(503, "Module studies indisponible")
    active_sid = await studies.get_active_study_id(_ONYXIA_USER)
    if not active_sid:
        raise HTTPException(404, "Aucune etude active")
    code = studies.read_recipe_pod_code(active_sid, slug)
    stdout = await _execute_python_in_workspace(_ONYXIA_USER, code)
    parsed = _parse_recipe_read_marker(stdout)
    if not parsed.get("found"):
        raise HTTPException(404, f"Recipe '{slug}' introuvable")
    meta = await studies.recipe_index_get_latest(active_sid, slug)
    return {
        "slug": slug,
        "format": parsed.get("fmt", "yaml"),
        "content": parsed.get("content", ""),
        "version_num": meta["version_num"] if meta else 0,
        "sha": meta["sha"] if meta else "",
        "name": meta["name"] if meta else slug,
        "description": meta["description"] if meta else "",
    }


@app.put("/desk/recipes/{slug}")
async def desk_recipe_save(slug: str, payload: dict):
    """UI desk : create/update une recipe user (auto-version)."""
    if not _STUDIES_AVAILABLE:
        raise HTTPException(503, "Module studies indisponible")
    active_sid = await studies.get_active_study_id(_ONYXIA_USER)
    if not active_sid:
        raise HTTPException(404, "Aucune etude active")
    content = (payload.get("content") or "").strip()
    if not content:
        raise HTTPException(400, "Body 'content' obligatoire")
    fmt = payload.get("format", "yaml").lower()
    if fmt not in ("yaml", "json"):
        raise HTTPException(400, "format doit etre 'yaml' ou 'json'")
    code = studies.save_recipe_pod_code(active_sid, slug, content, fmt)
    stdout = await _execute_python_in_workspace(_ONYXIA_USER, code)
    marker = _parse_recipe_save_marker(stdout)
    sha = marker.get("sha", "")
    if not sha:
        raise HTTPException(500, f"Save KO: {stdout[:200]}")
    previous = await studies.recipe_index_get_latest(active_sid, slug)
    await studies.recipe_index_insert(
        sid=active_sid, slug=slug, sha=sha, owner=_ONYXIA_USER,
        name=payload.get("name", "") or slug,
        description=payload.get("description", "") or "",
        fmt=fmt,
        previous_sha=previous["sha"] if previous else "",
    )
    latest = await studies.recipe_index_get_latest(active_sid, slug)

    # Sprint Composants Phase 3c (2026-06-27) : trigger fire-and-forget
    # de l'analyse meta-agent. L'agent recipe_analyzer (LLM) génère un
    # RecipeAnalysis async, persisté côté hub. Au prochain analyze_recipe
    # tool call, cache HIT instantané.
    #
    # Best-effort : si l'agent IA est down, ne bloque pas le save user.
    # Le content_hash changera au prochain save → re-trigger auto.
    try:
        task = asyncio.create_task(
            _trigger_recipe_analysis_async(slug=slug, source="user")
        )
        _background_anchors.add(task)
        task.add_done_callback(_background_anchors.discard)
    except Exception as exc:
        log.warning("trigger recipe analysis async failed: %s", exc)

    return {
        "slug": slug,
        "sha": sha,
        "version_num": latest["version_num"] if latest else 1,
        "format": fmt,
    }


async def _trigger_recipe_analysis_async(
    slug: str, source: str = "user",
) -> None:
    """Sprint Composants Phase 3c : fire-and-forget vers l'agent IA pour
    analyser une recipe.

    L'agent IA expose un endpoint interne POST /internal/analyze-recipe
    (à créer) qui orchestre le tool natif analyze_recipe.

    Best-effort : si agent down, on log et abandonne. content_hash change
    au prochain save → retry auto.
    """
    if not _AGENT_URL:
        log.debug("AGENT_URL non configuré, skip trigger recipe analysis")
        return
    api_key = await auth.create_or_get_api_key(_ONYXIA_USER)
    try:
        async with httpx.AsyncClient(timeout=60, base_url=_AGENT_URL) as c:
            await c.post(
                "/internal/analyze-recipe",
                headers={"Authorization": f"Bearer {api_key}"},
                json={"slug": slug, "source": source},
            )
        log.info("triggered async recipe analysis : slug=%s source=%s", slug, source)
    except Exception as exc:
        log.warning("trigger recipe analysis failed slug=%s : %s", slug, exc)


@app.delete("/desk/recipes/{slug}", status_code=status.HTTP_204_NO_CONTENT)
async def desk_recipe_delete(slug: str):
    """UI desk : soft-delete une recipe (archive)."""
    if not _STUDIES_AVAILABLE:
        raise HTTPException(503, "Module studies indisponible")
    active_sid = await studies.get_active_study_id(_ONYXIA_USER)
    if not active_sid:
        raise HTTPException(404, "Aucune etude active")
    code = studies.delete_recipe_pod_code(active_sid, slug)
    await _execute_python_in_workspace(_ONYXIA_USER, code)
    await studies.recipe_index_archive(active_sid, slug)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@app.get("/desk/recipes/{slug}/history")
async def desk_recipe_history(slug: str):
    """UI desk : toutes les versions d'une recipe."""
    if not _STUDIES_AVAILABLE:
        raise HTTPException(503, "Module studies indisponible")
    active_sid = await studies.get_active_study_id(_ONYXIA_USER)
    if not active_sid:
        return {"versions": [], "count": 0}
    rows = await studies.recipe_index_history(active_sid, slug)
    return {"versions": rows, "count": len(rows)}


@app.get("/desk/study-files")
async def desk_study_files():
    """Liste les fichiers de l'étude active (data/, exports/, notes.md).

    Lecture côté workspace via execute_python. Retourne liste plate avec
    {name, kind, size_kb, path} — consommé par le panel Ressources du desk.
    Si workspace endormi, retourne liste vide (UI affiche placeholder).
    """
    if not _STUDIES_AVAILABLE:
        return {"files": []}
    try:
        active_sid = await studies.get_active_study_id(_ONYXIA_USER)
        if not active_sid:
            return {"files": []}
        # Best-effort : ne réveille pas le pod, timeout court.
        all_sessions = await sessions.list_sessions(_ONYXIA_USER)
        if not all_sessions or all_sessions[0].get("status") != sessions.SESSION_READY:
            return {"files": []}
        code = (
            "import json\n"
            "from pathlib import Path\n"
            f"sid = {active_sid!r}\n"
            "base = Path(f'/data/studies/{sid}')\n"
            "out = []\n"
            "if base.exists():\n"
            "    for sub, kind in [('data','data'),('exports','export')]:\n"
            "        d = base / sub\n"
            "        if d.exists():\n"
            "            for p in sorted(d.rglob('*')):\n"
            "                if p.is_file():\n"
            "                    sz = p.stat().st_size\n"
            "                    out.append({'name': p.name, 'kind': kind,\n"
            "                                'size_kb': round(sz/1024, 1),\n"
            "                                'path': str(p.relative_to(base))})\n"
            "    notes = base / 'notes.md'\n"
            "    if notes.exists():\n"
            "        sz = notes.stat().st_size\n"
            "        out.append({'name': 'notes.md', 'kind': 'note',\n"
            "                    'size_kb': round(sz/1024, 1),\n"
            "                    'path': 'notes.md'})\n"
            "print('<<<FILES>>>' + json.dumps(out[:60]) + '<<<END>>>')\n"
        )
        stdout = await _execute_python_in_workspace(_ONYXIA_USER, code, timeout=5)
        start = stdout.find("<<<FILES>>>")
        end = stdout.find("<<<END>>>")
        if start < 0 or end <= start:
            return {"files": []}
        import json as _json
        return {"files": _json.loads(stdout[start + len("<<<FILES>>>"):end])}
    except Exception:
        return {"files": []}


@app.get("/desk/workspace-status")
async def desk_workspace_status():
    """État du workspace QGIS pour le polling JS du desk (réveil).

    Retourne {"status": "ready|starting|sleeping|error", "novnc_url": "..."}.
    Sert au feedback visuel de la commande 'Réveiller le bureau' : le JS poll
    cet endpoint toutes les 2s, et bascule en iframe noVNC dès que ready.
    """
    try:
        all_sessions = await sessions.list_sessions(_ONYXIA_USER)
        if not all_sessions:
            return {"status": "sleeping", "novnc_url": ""}
        s = all_sessions[0]
        return {
            "status": s.get("status", "—"),
            "novnc_url": s.get("novnc_url", ""),
        }
    except Exception:
        return {"status": "error", "novnc_url": ""}


@app.get("/desk/agent-health")
async def desk_agent_health():
    """Sonde same-origin de l'état de l'agent IA pour le loader du desk.

    Le client JS du desk ne peut pas interroger l'agent cross-origin (no-cors
    masque toutes les erreurs HTTP, cf. loader cassé identifié dans l'audit
    UX 2026-05-17). On proxifie via le hub pour avoir un signal fiable.
    """
    try:
        r = await _agent_call("GET", "/health")
        return {"ready": r.status_code < 300}
    except Exception:
        return {"ready": False}


@app.get("/desk/catalog")
async def desk_catalog():
    """Renvoie l'etat catalogue + counters pour le desk.

    Endpoint cookie-auth-friendly (utilise _ONYXIA_USER cote serveur, pas
    de header X-Hub-API-Key requis) appele par desk.html apres un
    publish_artifact reussi pour rafraichir compteur footer + drawer
    sans recharger toute la page. Cf. postMessage 'qgis_publish_done'
    emis depuis chat.html (agent iframe).

    Retourne :
      {
        "active_study_id": str|None,
        "items": [...]  // publications de l'etude active (max 30)
        "study_count": int,
        "total_count": int,  // toutes etudes confondues pour le footer
        "study_labels": {sid: label}  // mapping pour badges UI
      }
    """
    out = {
        "active_study_id": None,
        "items":           [],
        "study_count":     0,
        "total_count":     0,
        "study_labels":    {},
    }
    try:
        api_key = await auth.create_or_get_api_key(_ONYXIA_USER)
        headers = {"Authorization": f"Bearer {api_key}"}
        active_sid = None
        async with httpx.AsyncClient(timeout=8, base_url=_SELF_URL) as c:
            try:
                r = await c.get("/studies/active", headers=headers)
                if r.status_code == 200:
                    d = r.json() or {}
                    active_sid = d.get("id")
            except Exception:
                pass
            try:
                # Build study_id -> label mapping pour les badges UI
                r = await c.get("/studies", headers=headers)
                if r.status_code == 200:
                    for st in (r.json() or []):
                        sid = st.get("id")
                        if sid:
                            out["study_labels"][sid] = (
                                st.get("name") or st.get("label")
                                or sid[:8]
                            )
            except Exception:
                pass
            try:
                r = await c.get(f"/catalog/{_ONYXIA_USER}", headers=headers)
                if r.status_code == 200:
                    all_items = r.json().get("items", [])
                    # Enrichir chaque item avec hub_url + size_kb (champs UI).
                    # Le catalog S3 stocke `url` (MinIO direct) et `size` (octets),
                    # mais le drawer cote UI veut un lien stable via le hub
                    # (proxy /published/...) et une taille humaine en ko.
                    for it in all_items:
                        kind = it.get("kind", "")
                        slug = it.get("slug", "")
                        if kind and slug and not it.get("hub_url"):
                            it["hub_url"] = (
                                f"{_HUB_URL}/published/{_ONYXIA_USER}/{kind}/{slug}"
                            )
                        sz = it.get("size")
                        if sz and not it.get("size_kb"):
                            it["size_kb"] = max(1, round(sz / 1024))
                    out["total_count"] = len(all_items)
                    if active_sid:
                        items = [i for i in all_items
                                 if i.get("study_id") == active_sid][:30]
                        out["items"]       = items
                        out["study_count"] = len(items)
                    out["active_study_id"] = active_sid
            except Exception:
                pass
    except Exception as exc:
        log.warning("desk_catalog: %s", exc)
    return out


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
    """Liste les couches QGIS du projet courant via le MCP.

    Retourne aussi un champ `state` pour que le panel Ressources sache
    distinguer "projet vide" (workspace OK, projet sans couches) de
    "workspace endormi" (impossible de lire pour l'instant) — UX moins
    trompeuse que `layers=[]` ambigu.
    """
    # Check workspace ready en premier (best-effort, ne réveille pas).
    try:
        all_sessions = await sessions.list_sessions(_ONYXIA_USER)
        if not all_sessions or all_sessions[0].get("status") != sessions.SESSION_READY:
            return {"layers": [], "state": "workspace_sleeping"}
    except Exception:
        return {"layers": [], "state": "unknown"}
    try:
        api_key = await auth.create_or_get_api_key(_ONYXIA_USER)
        async with httpx.AsyncClient(timeout=10, base_url=_SELF_URL) as c:
            r = await c.post("/mcp",
                             headers={"Authorization": f"Bearer {api_key}"},
                             json={"jsonrpc": "2.0", "id": 1,
                                   "method": "tools/call",
                                   "params": {"name": "get_project_info", "arguments": {}}})
        content = r.json().get("result", {}).get("content", [{}])
        text = content[0].get("text", "") if content else ""
        info = json.loads(text) if text else {}
        return {"layers": info.get("layers", []), "state": "ok"}
    except Exception:
        return {"layers": [], "state": "error"}
