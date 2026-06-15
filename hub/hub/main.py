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
    try:
        # Si pas de cookie auth, redirect simple vers /workspace (sans toucher
        # studies pour eviter latence sur les readiness probes anonymes).
        cookie_key = request.cookies.get("hub_api_key", "")
        if not cookie_key.startswith("qgis_"):
            return RedirectResponse("/workspace", status_code=302)
        # User authentifie : on regarde s'il a une etude active.
        if _STUDIES_AVAILABLE:
            try:
                active_sid = await studies.get_active_study_id(_ONYXIA_USER)
                if active_sid:
                    return RedirectResponse("/desk", status_code=302)
            except Exception:
                pass
    except Exception:
        pass
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
        try:
            api_key = await auth.create_or_get_api_key(_ONYXIA_USER)
            async with httpx.AsyncClient(timeout=30, base_url=_SELF_URL) as c:
                await c.post("/sessions", headers={"Authorization": f"Bearer {api_key}"}, json={})
        except Exception:
            pass
        # Recharge le projet QGIS de l'étude active dès que le pod est prêt
        # (en background — le wait pod ready peut prendre 30-60s).
        task = asyncio.create_task(_auto_activate_active_study_after_wake(_ONYXIA_USER))
        _background_anchors.add(task)
        task.add_done_callback(_background_anchors.discard)
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
    """Proxifie un appel vers le pod agent IA."""
    async with httpx.AsyncClient(timeout=10, base_url=_AGENT_URL or "http://127.0.0.1:8100") as c:
        return await c.request(method, path, **kwargs)


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
