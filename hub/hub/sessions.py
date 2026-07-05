"""
hub.sessions — Workspace QGIS permanent par user sur K8s.

Phase 1 du refactor (post 2026-05-15) : pas de pods éphémères, mais UN
StatefulSet permanent par user, scalé 0↔1 selon activité.

Pattern :
  StatefulSet  qgis-workspace-{owner}      ← idempotent par user
  Pod          qgis-workspace-{owner}-0    ← K8s gère le scale
  PVC          data-qgis-workspace-{owner}-0  ← durable, jamais supprimé sauf purge
  Service      qgis-workspace-{owner}      ← DNS stable interne
  Ingress      qgis-workspace-{owner}-novnc

Cycle :
  create_session(owner) → s'assure que le SS existe et est scalé à 1
  get_session(sid)      → état courant (replicas K8s + pod ready)
  touch_session(sid)    → reset TTL idle (évite scale 0)
  delete_session(sid)   → DEFAULT scale 0 (PVC préservé)
                          purge=True pour vrai delete (perte de données)
  cleanup_idle          → scale 0 (pas delete) après TTL inactivité

L'API publique reste compatible avec l'existant (un seul `session_id` par user,
déterministe = hash court de l'owner). Le hub continue de manipuler des
"sessions" mais elles sont maintenant adossées à un workspace permanent.
"""

from __future__ import annotations

import asyncio
import hashlib
import os
import subprocess
import time
from pathlib import Path

import aiosqlite

# Defaut persistant : si /home/onyxia/work (PVC) existe, on ecrit dedans pour
# que sessions.db survive aux redeploys (sinon une session active perd son
# binding workspace au redeploy hub). Cf. auth.py meme strategie.
_DATA_DIR = Path(os.getenv("DATA_DIR") or (
    "/home/onyxia/work/qgis-mcp/server-data"
    if Path("/home/onyxia/work").is_dir()
    else "/tmp/qgis-mcp/server-data"
))
_DB_PATH  = _DATA_DIR / "sessions.db"

def _resolve_namespace() -> str:
    """Résout le namespace K8s: env var ou lecture du SA token (fallback)."""
    ns = os.getenv("SSPCLOUD_NAMESPACE") or os.getenv("NAMESPACE", "")
    if not ns:
        try:
            ns = Path("/var/run/secrets/kubernetes.io/serviceaccount/namespace").read_text().strip()
        except Exception:
            ns = "default"
    return ns

_NAMESPACE = _resolve_namespace()
_QGIS_IMAGE = os.getenv("QGIS_IMAGE", "ghcr.io/nic01asfr/qgisremotemcp:latest")
_PULL_SECRET = os.getenv("QGIS_IMAGE_PULL_SECRET", "")

_MCP_PORT        = 8100
_API_PORT        = 8080
_VNC_PORT        = 6080
_IDLE_TTL        = int(os.getenv("SESSION_IDLE_TTL", str(2 * 3600)))   # 2h par défaut
_CPU_LIMIT       = os.getenv("QGIS_CPU_LIMIT", "2")
_MEM_LIMIT       = os.getenv("QGIS_MEM_LIMIT", "4Gi")
_PVC_SIZE        = os.getenv("QGIS_WORKSPACE_PVC_SIZE", "10Gi")
_STORAGE_CLASS   = os.getenv("QGIS_WORKSPACE_STORAGE_CLASS", "")  # vide = défaut SSPCloud

SESSION_STARTING  = "starting"
SESSION_READY     = "ready"
SESSION_SLEEPING  = "sleeping"   # NOUVEAU : workspace scale=0, PVC intact
SESSION_ERROR     = "error"
SESSION_DELETED   = "deleted"


def maximize_qgis_code() -> str:
    """
    Code Python à injecter dans la session QGIS pour :
    1. Fullscreen Qt (masque title bar + taskbar WM, plus de bordures)
    2. Confinement /data : monkey-patch QFileDialog pour empêcher la navigation
       en dehors de /data (clé pour la sécurité du pod root).
    3. Settings QGIS pointés sur /data (dernier répertoire utilisé).

    Idempotent (flag posé sur QgsApplication). Sûr à appeler à chaque switch
    d'étude ou au boot session.
    """
    return r"""
try:
    from qgis.utils import iface
    if iface is None:
        raise RuntimeError("iface None")
    mw = iface.mainWindow()
    mw.showFullScreen()
    print(f"QGIS_FULLSCREEN ok geometry={mw.geometry().width()}x{mw.geometry().height()}")

    # ── Confinement /data via monkey-patch QFileDialog ──
    from qgis.core import QgsApplication
    _app = QgsApplication.instance()
    if not getattr(_app, "_dialog_sandbox_installed", False):
        _app._dialog_sandbox_installed = True
        from qgis.PyQt import QtWidgets
        import os.path
        DATA_ROOT = "/data"

        def _confine(directory):
            d = str(directory) if directory else ""
            try:
                rp = os.path.realpath(d) if d else ""
                if rp == DATA_ROOT or rp.startswith(DATA_ROOT + "/"):
                    return d
            except Exception:
                pass
            return DATA_ROOT

        def _check(path):
            if not path:
                return path
            try:
                rp = os.path.realpath(path)
                if rp == DATA_ROOT or rp.startswith(DATA_ROOT + "/"):
                    return path
            except Exception:
                pass
            return ""

        _o_open  = QtWidgets.QFileDialog.getOpenFileName
        _o_opens = QtWidgets.QFileDialog.getOpenFileNames
        _o_save  = QtWidgets.QFileDialog.getSaveFileName
        _o_dir   = QtWidgets.QFileDialog.getExistingDirectory

        def _w_open(parent=None, caption='', directory='', filter='', *a, **kw):
            path, sel = _o_open(parent, caption, _confine(directory), filter, *a, **kw)
            return _check(path), sel
        def _w_opens(parent=None, caption='', directory='', filter='', *a, **kw):
            paths, sel = _o_opens(parent, caption, _confine(directory), filter, *a, **kw)
            return [p for p in paths if _check(p)], sel
        def _w_save(parent=None, caption='', directory='', filter='', *a, **kw):
            path, sel = _o_save(parent, caption, _confine(directory), filter, *a, **kw)
            return _check(path), sel
        def _w_dir(parent=None, caption='', directory='', *a, **kw):
            return _check(_o_dir(parent, caption, _confine(directory), *a, **kw))

        QtWidgets.QFileDialog.getOpenFileName      = staticmethod(_w_open)
        QtWidgets.QFileDialog.getOpenFileNames     = staticmethod(_w_opens)
        QtWidgets.QFileDialog.getSaveFileName      = staticmethod(_w_save)
        QtWidgets.QFileDialog.getExistingDirectory = staticmethod(_w_dir)

        # Settings QGIS : dernier répertoire ouvert toujours /data
        try:
            from qgis.PyQt.QtCore import QSettings
            qs = QSettings()
            for k in ("UI/lastFileNameWidgetDir", "UI/lastProjectDir",
                      "UI/lastLoadDir", "UI/lastSaveDir",
                      "Plugin-Manager/lastlocaldir"):
                qs.setValue(k, DATA_ROOT)
            qs.sync()
        except Exception as exc:
            print(f"QGIS_SETTINGS_WARN {exc}")

        print(f"QGIS_DIALOG_SANDBOX installed root={DATA_ROOT}")
    else:
        print("QGIS_DIALOG_SANDBOX already_installed")
except Exception as exc:
    print(f"QGIS_FULLSCREEN_ERR {exc}")
    import subprocess
    try:
        res = subprocess.run(
            ["xdotool", "search", "--name", "QGIS"],
            env={"DISPLAY": ":99"}, capture_output=True, text=True, timeout=5,
        )
        wids = [w for w in res.stdout.strip().split("\n") if w]
        if wids:
            subprocess.run(["xdotool", "windowsize", wids[0], "100%", "100%"],
                           env={"DISPLAY": ":99"}, timeout=5)
            print(f"QGIS_XDO_FALLBACK wid={wids[0]}")
    except Exception as exc2:
        print(f"QGIS_FALLBACK_ERR {exc2}")
"""


# ── Identifiants déterministes par user ───────────────────────────────────────

def _session_id_for(owner: str) -> str:
    """ID stable et court à partir du username. Un user = un workspace = un id."""
    return hashlib.sha1(owner.encode()).hexdigest()[:12]


def _workspace_name(owner: str) -> str:
    """Nom K8s du StatefulSet/Service/Ingress. DNS-safe."""
    # Onyxia username déjà DNS-safe ; on sécurise au cas où.
    safe = "".join(c if c.isalnum() else "-" for c in owner.lower()).strip("-")
    return f"qgis-workspace-{safe}"


# ── Base de données sessions ───────────────────────────────────────────────────

async def init_db() -> None:
    _DATA_DIR.mkdir(parents=True, exist_ok=True)
    async with aiosqlite.connect(_DB_PATH) as db:
        await db.execute("""
            CREATE TABLE IF NOT EXISTS sessions (
                id TEXT PRIMARY KEY,
                owner TEXT NOT NULL,
                status TEXT NOT NULL,
                created_at INTEGER NOT NULL,
                last_active INTEGER NOT NULL,
                pod_name TEXT NOT NULL,
                svc_name TEXT NOT NULL,
                mcp_url TEXT NOT NULL,
                api_url TEXT NOT NULL,
                parent_session_id TEXT
            )
        """)
        # Migration additive Sprint 1.5 (S7 Wave 1) : parent_session_id pour
        # cascade parent-child (traversal ancestors). ALTER idempotent, ignore
        # "duplicate column" si la table pre-existait sans cette colonne.
        try:
            await db.execute(
                "ALTER TABLE sessions ADD COLUMN parent_session_id TEXT"
            )
        except Exception as exc:
            msg = str(exc).lower()
            if "duplicate column" not in msg and "already exists" not in msg:
                raise
        await db.commit()


async def _save(s: dict) -> None:
    # S7 Wave 1 : preserve parent_session_id sur INSERT OR REPLACE.
    # Comme create_session ne peuple pas ce champ, on merge avec la valeur
    # actuelle si presente (evite d'ecraser un parent deja pose).
    payload = dict(s)
    if "parent_session_id" not in payload:
        payload["parent_session_id"] = None
        existing = await _get(payload["id"])
        if existing and existing.get("parent_session_id"):
            payload["parent_session_id"] = existing["parent_session_id"]
    async with aiosqlite.connect(_DB_PATH) as db:
        await db.execute("""
            INSERT OR REPLACE INTO sessions
            (id, owner, status, created_at, last_active, pod_name, svc_name, mcp_url, api_url, parent_session_id)
            VALUES (:id, :owner, :status, :created_at, :last_active, :pod_name, :svc_name, :mcp_url, :api_url, :parent_session_id)
        """, payload)
        await db.commit()


async def _get(session_id: str) -> dict | None:
    async with aiosqlite.connect(_DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        row = await (await db.execute(
            "SELECT * FROM sessions WHERE id = ?", (session_id,)
        )).fetchone()
    return dict(row) if row else None


async def _delete_row(session_id: str) -> None:
    async with aiosqlite.connect(_DB_PATH) as db:
        await db.execute("DELETE FROM sessions WHERE id = ?", (session_id,))
        await db.commit()


async def _list(owner: str | None = None) -> list[dict]:
    async with aiosqlite.connect(_DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        if owner:
            rows = await (await db.execute(
                "SELECT * FROM sessions WHERE owner = ? ORDER BY created_at DESC", (owner,)
            )).fetchall()
        else:
            rows = await (await db.execute(
                "SELECT * FROM sessions ORDER BY created_at DESC"
            )).fetchall()
    return [dict(r) for r in rows]


# ── Manifests K8s ──────────────────────────────────────────────────────────────

def _statefulset_manifest(owner: str, extra_env: dict | None = None) -> str:
    ws = _workspace_name(owner)
    pull_secret = (
        f"\n      imagePullSecrets:\n      - name: {_PULL_SECRET}"
        if _PULL_SECRET else ""
    )
    # Variables d'env du profil
    profile_env_yaml = ""
    if extra_env:
        for k, v in extra_env.items():
            v_safe = str(v).replace('"', '\\"')
            profile_env_yaml += f'        - name: {k}\n          value: "{v_safe}"\n'

    storage_class_line = (
        f"      storageClassName: {_STORAGE_CLASS}\n"
        if _STORAGE_CLASS else ""
    )

    return f"""apiVersion: apps/v1
kind: StatefulSet
metadata:
  name: {ws}
  namespace: {_NAMESPACE}
  labels:
    app: qgis-workspace
    owner: "{owner}"
spec:
  serviceName: {ws}
  replicas: 1
  selector:
    matchLabels:
      app: qgis-workspace
      owner: "{owner}"
  template:
    metadata:
      labels:
        app: qgis-workspace
        owner: "{owner}"
    spec:{pull_secret}
      containers:
      - name: qgis
        image: {_QGIS_IMAGE}
        imagePullPolicy: Always
        ports:
        - containerPort: {_MCP_PORT}
        - containerPort: {_API_PORT}
        - containerPort: {_VNC_PORT}
        env:
        - name: MULTI_USER_MODE
          value: "false"
        - name: WORKSPACE_OWNER
          value: "{owner}"
        - name: QGIS_RESOLUTION
          value: "1920x1080x24"
        - name: MCP_PORT
          value: "8100"
        - name: VNC_HOST
          value: "localhost"
        - name: DATA_DIR
          value: "/data"
        - name: PYTHONUNBUFFERED
          value: "1"
        # V1.5 Sprint 1 : recipes user de l'etude active.
        # `/data/studies/active` est un symlink maintenu par activate_pod_code
        # (cf. studies.py:activate_pod_code) -> {{active_sid}}. BigQgisMCP
        # qgis_bridge.py lit cette env (commit 3529d1c) et merge user recipes
        # avec system recipes /app/recipes dans list_recipes.
        - name: USER_RECIPES_DIR
          value: "/data/studies/active/recipes"
        # HUB_API_KEY via Secret namespace-level (jamais inline). Le tool
        # publish_artifact du workspace POST vers {{HUB_URL}}/publish avec
        # cette cle ; en la rattachant au Secret on garantit qu'elle suit
        # la cle COURANTE du hub (pas de desync apres redeploy hub).
        - name: HUB_API_KEY
          valueFrom:
            secretKeyRef:
              name: qgis-hub-apikey
              key: HUB_API_KEY
{profile_env_yaml}        resources:
          limits:
            cpu: "{_CPU_LIMIT}"
            memory: "{_MEM_LIMIT}"
          requests:
            cpu: "500m"
            memory: "2Gi"
        readinessProbe:
          httpGet:
            path: /health
            port: {_MCP_PORT}
          initialDelaySeconds: 30
          periodSeconds: 10
          failureThreshold: 18
        volumeMounts:
        - name: data
          mountPath: /data
  volumeClaimTemplates:
  - metadata:
      name: data
    spec:
      accessModes: ["ReadWriteOnce"]
{storage_class_line}      resources:
        requests:
          storage: {_PVC_SIZE}
"""


def _service_manifest(owner: str) -> str:
    ws = _workspace_name(owner)
    return f"""apiVersion: v1
kind: Service
metadata:
  name: {ws}
  namespace: {_NAMESPACE}
  labels:
    app: qgis-workspace
    owner: "{owner}"
spec:
  type: ClusterIP
  selector:
    app: qgis-workspace
    owner: "{owner}"
  ports:
  - name: mcp
    port: {_MCP_PORT}
    targetPort: {_MCP_PORT}
  - name: api
    port: {_API_PORT}
    targetPort: {_API_PORT}
  - name: novnc
    port: {_VNC_PORT}
    targetPort: {_VNC_PORT}
"""


def _novnc_ingress_manifest(owner: str) -> str:
    """Ingress K8s pour accès noVNC direct depuis le navigateur."""
    ws = _workspace_name(owner)
    host = f"{ws}-novnc.user.lab.sspcloud.fr"
    return f"""apiVersion: networking.k8s.io/v1
kind: Ingress
metadata:
  name: {ws}-novnc
  namespace: {_NAMESPACE}
  annotations:
    nginx.ingress.kubernetes.io/proxy-read-timeout: "3600"
    nginx.ingress.kubernetes.io/proxy-send-timeout: "3600"
    nginx.ingress.kubernetes.io/proxy-connect-timeout: "30"
    nginx.ingress.kubernetes.io/proxy-body-size: "0"
spec:
  ingressClassName: onyxia
  rules:
  - host: {host}
    http:
      paths:
      - path: /
        pathType: Prefix
        backend:
          service:
            name: {ws}
            port:
              number: {_VNC_PORT}
  tls:
  - hosts:
    - {host}
"""


# ── Kubectl helpers ────────────────────────────────────────────────────────────

def _kubectl_apply(manifest: str) -> tuple[bool, str]:
    r = subprocess.run(
        ["kubectl", "apply", "-f", "-"],
        input=manifest, capture_output=True, text=True, timeout=15,
    )
    return r.returncode == 0, (r.stderr or r.stdout)[:300]


def _kubectl_delete(resource: str, name: str) -> None:
    subprocess.run(
        ["kubectl", "delete", resource, name, "-n", _NAMESPACE, "--ignore-not-found"],
        capture_output=True, timeout=20,
    )


def _kubectl_patch_replicas(ss_name: str, replicas: int) -> bool:
    """Scale 0↔1 via patch (statefulsets/scale bloqué par SSPCloud)."""
    body = '{"spec":{"replicas":%d}}' % replicas
    r = subprocess.run(
        ["kubectl", "patch", "statefulset", ss_name,
         "-n", _NAMESPACE, "--type=merge", "-p", body],
        capture_output=True, text=True, timeout=15,
    )
    return r.returncode == 0


def _kubectl_set_env(ss_name: str, env: dict) -> bool:
    """Patch des env vars sur un StatefulSet existant (kubectl set env).

    Idempotent : kubectl ne déclenche un rolling restart que si une valeur
    change réellement. Sert à garantir HUB_URL/HUB_API_KEY sur les workspaces
    créés AVANT l'injection centralisée (sinon publish_artifact échoue car ces
    vars ne sont posées qu'à la création du manifest). La clé API étant stable
    (create_or_get_api_key), les appels suivants sont des no-op (pas de restart)."""
    env = {k: v for k, v in (env or {}).items() if v}
    if not env:
        return True
    args = ["kubectl", "set", "env", f"statefulset/{ss_name}",
            "-n", _NAMESPACE, "-c", "qgis"]
    args += [f"{k}={v}" for k, v in env.items()]
    r = subprocess.run(args, capture_output=True, text=True, timeout=15)
    if r.returncode != 0:
        print(f"[sessions] set env {ss_name} échec: {(r.stderr or r.stdout)[:200]}")
    return r.returncode == 0


def _ss_exists(ss_name: str) -> bool:
    r = subprocess.run(
        ["kubectl", "get", "statefulset", ss_name,
         "-n", _NAMESPACE, "--ignore-not-found", "-o", "name"],
        capture_output=True, text=True, timeout=10,
    )
    return bool(r.stdout.strip())


def _ss_replicas(ss_name: str) -> int:
    r = subprocess.run(
        ["kubectl", "get", "statefulset", ss_name,
         "-n", _NAMESPACE, "-o", "jsonpath={.spec.replicas}"],
        capture_output=True, text=True, timeout=10,
    )
    try:
        return int(r.stdout.strip())
    except (ValueError, TypeError):
        return 0


def _pod_ready(pod_name: str) -> bool:
    r = subprocess.run(
        ["kubectl", "get", "pod", pod_name, "-n", _NAMESPACE,
         "-o", "jsonpath={.status.conditions[?(@.type==\"Ready\")].status}"],
        capture_output=True, text=True, timeout=10,
    )
    return r.stdout.strip() == "True"


def _pod_failed(pod_name: str) -> bool:
    """Détecte un pod en échec persistant (pas un pod absent — scale 0 est légitime)."""
    r = subprocess.run(
        ["kubectl", "get", "pod", pod_name, "-n", _NAMESPACE,
         "--ignore-not-found",
         "-o", "jsonpath={.status.phase}|||{.status.containerStatuses[0].state.terminated.reason}"],
        capture_output=True, text=True, timeout=10,
    )
    if not r.stdout.strip():
        return False  # pas de pod = ok si scale=0
    output = r.stdout.strip()
    return any(s in output for s in ("Failed", "OOMKilled", "Evicted"))


def _ingress_exists(ws_name: str) -> bool:
    r = subprocess.run(
        ["kubectl", "get", "ingress", f"{ws_name}-novnc",
         "-n", _NAMESPACE, "--ignore-not-found", "-o", "name"],
        capture_output=True, text=True, timeout=10,
    )
    return bool(r.stdout.strip())


def _ensure_novnc_ingress(owner: str) -> None:
    if not _ingress_exists(_workspace_name(owner)):
        _kubectl_apply(_novnc_ingress_manifest(owner))


# ── Interface publique ─────────────────────────────────────────────────────────

async def create_session(owner: str, extra_env: dict | None = None) -> dict:
    """
    Crée ou réveille le workspace de l'owner.

    - Si le StatefulSet n'existe pas : le crée (replicas=1).
    - S'il existe et replicas=0 : scale à 1 (réveil).
    - S'il existe et replicas=1 : pas de no-op, on rafraîchit la DB.

    extra_env permet d'injecter le profil au démarrage (env vars du pod).
    Note : changer le profil après le 1er démarrage nécessite un restart
    du pod (kubectl rollout restart) — à voir en Phase 5 (profil dynamique).
    """
    # Injection HUB_URL inline dans l'env workspace (var simple, pas de
    # rotation possible). HUB_API_KEY n'est PLUS injectee ici : elle est
    # referencee via secretKeyRef directement dans _statefulset_manifest
    # (Secret `qgis-hub-apikey`, source de verite namespace-level — survit
    # aux redeploys hub, jamais de desync entre workspace et hub).
    extra_env = dict(extra_env or {})
    _hub_url = os.getenv("HUB_URL", "") or (
        f"https://user-{owner}-qgis.user.lab.sspcloud.fr" if owner else ""
    )
    if _hub_url:
        extra_env.setdefault("HUB_URL", _hub_url)
    # On garantit toutefois que le Secret existe AVANT de creer le workspace,
    # sinon kubelet refuserait de demarrer le pod (secretKeyRef vers Secret
    # absent = pod en CreateContainerConfigError).
    try:
        from hub import auth  # lazy : evite cycle import au load
        await auth.create_or_get_api_key(owner)
    except Exception as exc:
        print(f"[sessions] Secret qgis-hub-apikey non garanti pour {owner}: {exc}")

    ws = _workspace_name(owner)
    session_id = _session_id_for(owner)
    pod_name = f"{ws}-0"
    svc_host = f"{ws}.{_NAMESPACE}.svc.cluster.local"
    mcp_url = f"http://{svc_host}:{_MCP_PORT}/mcp"
    api_url = f"http://{svc_host}:{_API_PORT}"
    now = int(time.time())

    existed = _ss_exists(ws)
    if not existed:
        # Création complète : Service d'abord, puis StatefulSet, puis Ingress
        ok_svc, err_svc = _kubectl_apply(_service_manifest(owner))
        ok_ss, err_ss  = _kubectl_apply(_statefulset_manifest(owner, extra_env=extra_env or {}))
        _kubectl_apply(_novnc_ingress_manifest(owner))
        if not (ok_svc and ok_ss):
            status = SESSION_ERROR
            print(f"[sessions] erreur création workspace {owner}: svc={err_svc} ss={err_ss}")
        else:
            status = SESSION_STARTING
    else:
        # Workspace existe déjà : on s'assure qu'il est scale 1
        replicas = _ss_replicas(ws)
        if replicas == 0:
            _kubectl_patch_replicas(ws, 1)
            status = SESSION_STARTING
        else:
            status = SESSION_STARTING if not _pod_ready(pod_name) else SESSION_READY
        # Garantir HUB_URL meme sur un workspace cree AVANT l'injection
        # centralisee. HUB_API_KEY n'est plus pousse ici : il est attache
        # via secretKeyRef dans le manifest. Pour les workspaces existants
        # avec HUB_API_KEY en valeur inline (legacy pre-refonte), un patch
        # API direct serait necessaire ; en pratique on laisse l'inline
        # cohabiter — `auth.create_or_get_api_key` synchronise le Secret a
        # la cle ACTUELLE, donc le publish_artifact du workspace reussit
        # tant que la cle inline correspond a la cle courante.
        _kubectl_set_env(ws, {"HUB_URL": extra_env["HUB_URL"]} if "HUB_URL" in extra_env else {})
        # Toujours s'assurer que l'ingress existe (sessions créées avant déploiement feature)
        _ensure_novnc_ingress(owner)

    session = {
        "id": session_id,
        "owner": owner,
        "status": status,
        "created_at": now if not existed else (await _get(session_id) or {}).get("created_at", now),
        "last_active": now,
        "pod_name": pod_name,
        "svc_name": ws,
        "mcp_url": mcp_url,
        "api_url": api_url,
    }
    await _save(session)
    return session


async def get_session(session_id: str, owner: str | None = None) -> dict | None:
    """État courant d'une session. Refresh depuis K8s à chaque appel."""
    s = await _get(session_id)
    if not s:
        return None
    if owner and s["owner"] != owner:
        return None

    ws = s["svc_name"]
    pod_name = s["pod_name"]

    # Si on a scale 0, on est en SLEEPING (PVC intact mais pod absent)
    if not _ss_exists(ws):
        # SS supprimé → vraie disparition
        s["status"] = SESSION_DELETED
        await _save(s)
        return s

    replicas = _ss_replicas(ws)
    if replicas == 0:
        if s["status"] != SESSION_SLEEPING:
            s["status"] = SESSION_SLEEPING
            await _save(s)
        return s

    # replicas >= 1 : vérifier l'état réel du pod
    if _pod_failed(pod_name):
        s["status"] = SESSION_ERROR
        await _save(s)
        return s

    if _pod_ready(pod_name):
        if s["status"] != SESSION_READY:
            s["status"] = SESSION_READY
            await _save(s)
        _ensure_novnc_ingress(s["owner"])
    elif s["status"] != SESSION_STARTING:
        s["status"] = SESSION_STARTING
        await _save(s)

    return s


async def touch_session(session_id: str) -> None:
    """Met à jour last_active. Empêche le scale-down par cleanup_idle."""
    async with aiosqlite.connect(_DB_PATH) as db:
        await db.execute(
            "UPDATE sessions SET last_active = ? WHERE id = ?",
            (int(time.time()), session_id),
        )
        await db.commit()


def novnc_url(session_id: str) -> str:
    """URL publique noVNC. session_id est dérivé de l'owner."""
    # On a besoin de retrouver l'owner depuis l'id pour construire l'URL.
    # Lecture sync depuis DB pour rester compatible avec l'appel non-async existant.
    import sqlite3
    try:
        con = sqlite3.connect(_DB_PATH)
        cur = con.execute("SELECT owner FROM sessions WHERE id = ?", (session_id,))
        row = cur.fetchone()
        con.close()
        if row:
            return f"https://{_workspace_name(row[0])}-novnc.user.lab.sspcloud.fr"
    except Exception:
        pass
    return ""


async def delete_session(session_id: str, purge: bool = False) -> None:
    """
    Par défaut : scale 0 (preserve PVC + données).
    purge=True : suppression totale (StatefulSet + PVC + Service + Ingress).
                 Données perdues. À utiliser que sur demande explicite user.
    """
    s = await _get(session_id)
    if not s:
        return

    ws = s["svc_name"]
    if not purge:
        # Endormissement : scale 0, garde le PVC
        _kubectl_patch_replicas(ws, 0)
        s["status"] = SESSION_SLEEPING
        await _save(s)
        return

    # Purge complète
    _kubectl_delete("ingress", f"{ws}-novnc")
    _kubectl_delete("statefulset", ws)
    _kubectl_delete("service", ws)
    # PVC : conservation par défaut (politique SSPCloud Retain).
    # Suppression explicite via kubectl si l'user a vraiment confirmé.
    pvc_name = f"data-{ws}-0"
    _kubectl_delete("pvc", pvc_name)
    await _delete_row(session_id)


async def list_sessions(owner: str | None = None) -> list[dict]:
    """Liste les workspaces. 0 ou 1 par user en pratique."""
    return await _list(owner)


async def cleanup_idle_sessions() -> int:
    """
    Scale à 0 les workspaces inactifs depuis plus de SESSION_IDLE_TTL.
    Ne supprime JAMAIS le PVC : les données de l'user sont préservées.
    """
    cutoff = int(time.time()) - _IDLE_TTL
    sessions = await _list()
    sleeped = 0
    for s in sessions:
        if s["last_active"] < cutoff and s["status"] == SESSION_READY:
            await delete_session(s["id"], purge=False)
            sleeped += 1
    return sleeped


async def cleanup_loop() -> None:
    """Boucle de scale-down idle — démarre en tâche FastAPI."""
    while True:
        await asyncio.sleep(600)  # toutes les 10 min
        try:
            n = await cleanup_idle_sessions()
            if n:
                print(f"[sessions] {n} workspace(s) idle endormi(s)")
        except Exception as exc:
            print(f"[sessions] cleanup erreur: {exc}")


# ── S7 Wave 1 — Cascade parent-child ──────────────────────────────────────────
#
# Vision : une session enfant heritee (child) peut declarer sa session parente
# pour permettre traversal des ancestors (audit, propagation lifecycle, etc.).
# La colonne parent_session_id est optionnelle (NULL = session racine).

class SessionNotFoundError(Exception):
    """Session cible d'un set_session_parent introuvable."""


class CircularParentError(Exception):
    """Detection d'un cycle parent-child (self-loop ou boucle transitivite)."""


async def set_session_parent(
    child_session_id: str, parent_session_id: str
) -> None:
    """Definit parent_session_id sur une session enfant.

    Verifications :
    - Les 2 sessions doivent exister
    - Pas de self-loop (child != parent)
    - Pas de cycle transitif (parent ne doit pas etre descendant de child)

    Idempotent : re-set la meme relation ne change rien.
    """
    if child_session_id == parent_session_id:
        raise CircularParentError(
            f"Session {child_session_id} ne peut etre son propre parent"
        )
    child = await _get(child_session_id)
    if not child:
        raise SessionNotFoundError(f"Session enfant {child_session_id} introuvable")
    parent = await _get(parent_session_id)
    if not parent:
        raise SessionNotFoundError(f"Session parente {parent_session_id} introuvable")
    # Detection cycle : traverse ancestors du parent, si child_session_id
    # apparait -> cycle. On borne la profondeur pour eviter boucle infinie
    # sur DB corrompue.
    ancestors_of_parent = await get_session_ancestors(parent_session_id)
    if child_session_id in ancestors_of_parent:
        raise CircularParentError(
            f"Cycle detecte : {parent_session_id} est deja descendant de "
            f"{child_session_id}"
        )
    async with aiosqlite.connect(_DB_PATH) as db:
        await db.execute(
            "UPDATE sessions SET parent_session_id = ? WHERE id = ?",
            (parent_session_id, child_session_id),
        )
        await db.commit()


async def get_session_ancestors(session_id: str) -> list[str]:
    """Retourne la chaine d'ancetres (parent, grand-parent, ...).

    Ordre : du parent direct au plus lointain ancetre. Session racine
    retourne []. Bornage max_depth=32 pour protection anti-cycle si la
    DB est corrompue (garde-fou defensif).
    """
    ancestors: list[str] = []
    seen: set[str] = {session_id}
    current = session_id
    max_depth = 32
    for _ in range(max_depth):
        s = await _get(current)
        if not s:
            break
        parent = s.get("parent_session_id")
        if not parent:
            break
        if parent in seen:
            # cycle DB corrompu : stop propre plutot que boucle infinie
            break
        ancestors.append(parent)
        seen.add(parent)
        current = parent
    return ancestors
