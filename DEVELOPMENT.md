# DEVELOPMENT — qgis-sspcloud

Guide développeur : setup local, build images, redéploiement, tests.

Version 2026-08-06 · Sprint Day 5 CLOS · chart Helm 1.2.5.

> Pour l'installation user, voir [QUICKSTART.md](QUICKSTART.md) et
> [docs/day5-user-guide-visuel.md](docs/day5-user-guide-visuel.md).
> Pour l'ancien flow admin (portail nic01asfr, retiré), voir
> [docs/history/ONBOARDING-legacy.md](docs/history/ONBOARDING-legacy.md).

---

## 1. Structure du repo

```
qgis-sspcloud/
├── hub/                  # Hub FastAPI (Python) — API REST + desk web + MCP + proxy /agent
│   ├── hub/
│   │   ├── main.py       # ~12k lignes, endpoints + middlewares
│   │   ├── auth.py       # OIDC + hub_api_key cookie + inter-pod Bearer
│   │   ├── sessions.py   # Workspace scale/wake via kubectl
│   │   ├── studies.py    # Isolation étude/projet + session-scoped
│   │   └── ...
│   ├── templates/        # Jinja2 desk.html, workspace.html, storymap_dsfr.html.j2
│   ├── static/           # Assets + BlockNote bundle
│   └── tests/            # pytest
├── agent/                # Agent LLM (Python) — chat SSE + tool-runner
│   ├── agent/
│   │   ├── main.py       # Endpoints /chat, /api/status, /api/reload-llm-key
│   │   └── qgis_agent.py # LLM orchestration + tools MCP hub
│   └── tests/
├── blocknote-editor/     # Bundle React/TS (Vite) → hub/static/blocknote-editor/
├── charts/qgis-hub/      # Chart Helm officiel
│   ├── Chart.yaml
│   ├── values.yaml
│   ├── values.schema.json
│   └── templates/
├── helm-repo/            # Chart packagés + index.yaml (publié GHA)
├── docs/                 # Documentation
├── .github/workflows/    # CI/CD (build image + publish chart)
├── Dockerfile.hub        # Image hub
├── Dockerfile.agent      # Image agent
├── Dockerfile.workspace  # Image workspace (rebuild manuel)
└── install.sh            # One-liner install user
```

---

## 2. Setup local dev

### 2.1 Hub

```bash
git clone https://github.com/nic01asFr/Qgis-sspcloud
cd qgis-sspcloud/hub
python -m venv .venv && source .venv/bin/activate    # ou .venv\Scripts\activate sur Windows
pip install -e .
pytest tests/
```

Lancer le hub en dev (sans K8s) :
```bash
export ONYXIA_USER=devlocal
export HUB_API_KEY=dev-key-local
uvicorn hub.main:app --reload --port 8888
```

### 2.2 Agent

```bash
cd qgis-sspcloud/agent
python -m venv .venv && source .venv/bin/activate
pip install -e .
pytest tests/
```

Lancer l'agent en dev :
```bash
export ONYXIA_USER=devlocal
export HUB_API_KEY=dev-key-local
export HUB_URL=http://localhost:8888
export LLM_API_KEY=sk-...   # depuis llm.lab.sspcloud.fr
uvicorn agent.main:app --reload --port 8100
```

### 2.3 BlockNote editor

Bundle React/TypeScript compilé par Vite. Buildé automatiquement par
Dockerfile.hub (stage `blocknote-builder`).

Build local :
```bash
cd qgis-sspcloud/blocknote-editor
npm install --legacy-peer-deps
npx vite build
# → dist/ copié dans hub/static/blocknote-editor/ à la prochaine image
```

---

## 3. Build images Docker

### 3.1 Automatique (recommandé)

GHA workflow [`build.yml`](.github/workflows/build.yml) build + push sur
chaque commit `main` :

- `ghcr.io/nic01asfr/qgis-hub:latest` + `qgis-hub:<sha>`
- `ghcr.io/nic01asfr/qgis-agent:latest` + `qgis-agent:<sha>`

Le job `Publish Helm Chart` package le chart depuis `charts/qgis-hub/`
et met à jour `helm-repo/index.yaml`.

### 3.2 Local

```bash
docker build -t ghcr.io/nic01asfr/qgis-hub:local -f Dockerfile.hub .
docker build -t ghcr.io/nic01asfr/qgis-agent:local -f Dockerfile.agent .
```

### 3.3 Image workspace (rebuild manuel uniquement)

Image `qgisremotemcp` (QGIS Desktop + BigQgisMCP + noVNC + Xvfb) buildée
depuis le repo [BigQgisMCP](https://github.com/nic01asFr/BigQgisMCP)
(~30min). Décommenter le job `build-workspace` dans `build.yml` pour
rebuild automatique. Voir `Dockerfile.workspace` pour référence.

---

## 4. Chart Helm — dev + release

### 4.1 Bump version

Éditer `charts/qgis-hub/Chart.yaml` :
```yaml
version: 1.2.5   # bump ici
```

### 4.2 Package + regen index

```bash
helm package charts/qgis-hub -d helm-repo/
helm repo index helm-repo/ --url https://raw.githubusercontent.com/nic01asFr/Qgis-sspcloud/main/helm-repo
git add charts/qgis-hub/ helm-repo/qgis-hub-<version>.tgz helm-repo/index.yaml
git commit -m "chore(chart): bump 1.2.5"
git push origin main
```

Le GHA `Publish Helm Chart` peut aussi le faire automatiquement au push
du `Chart.yaml`.

### 4.3 Test chart en dev

```bash
helm template qgis-hub charts/qgis-hub/ --set oidc.username=devlocal --debug
helm lint charts/qgis-hub/
```

### 4.4 Deploy sur SSPCloud

Depuis un terminal Jupyter Onyxia du user cible :
```bash
export HELM_CONFIG_HOME=/home/onyxia/work/.helm-config
helm upgrade qgis-hub qgis-sspcloud/qgis-hub --version 1.2.5 \
    --reuse-values \
    --set serviceAccount.name="$KUBERNETES_SERVICE_ACCOUNT" \
    -n user-<toi>
# Force repull latest image
kubectl delete pod qgis-hub-0 qgis-agent-0 -n user-<toi>
kubectl wait --for=condition=ready pod/qgis-hub-0 -n user-<toi> --timeout=120s
```

---

## 5. Tests d'intégration E2E

### 5.1 Auth

```bash
KEY=$(kubectl get secret qgis-hub-apikey -n user-<u> -o jsonpath='{.data.HUB_API_KEY}' | base64 -d)
# Test login form
curl -X POST -d "api_key=$KEY" https://user-<u>-qgis.user.lab.sspcloud.fr/login -c cookie.txt
# Test workspace via cookie
curl -b cookie.txt https://user-<u>-qgis.user.lab.sspcloud.fr/workspace
```

### 5.2 Inter-pod

```bash
kubectl exec qgis-agent-0 -n user-<u> -- curl -s -o /dev/null -w "%{http_code}" \
  -H "Authorization: Bearer $KEY" http://qgis-hub:8888/studies
# Attendu : 200
```

### 5.3 Proxy /agent + WS noVNC

```bash
# HTTP proxy
kubectl exec qgis-hub-0 -n user-<u> -- curl -s -o /dev/null -w "%{http_code}" \
  -H "Cookie: hub_api_key=$KEY" http://localhost:8888/agent/api/status
# Attendu : 200

# HTTP proxy VNC
kubectl exec qgis-hub-0 -n user-<u> -- curl -s -o /dev/null -w "%{http_code}" \
  -H "Cookie: hub_api_key=$KEY" http://localhost:8888/workspace/vnc/vnc_lite.html
# Attendu : 200
```

---

## 6. Debug pod K8s

### 6.1 Logs

```bash
kubectl logs qgis-hub-0 -n user-<u> --tail=50
kubectl logs qgis-agent-0 -n user-<u> --tail=50
kubectl logs qgis-workspace-<u>-0 -n user-<u> --tail=50
```

### 6.2 Exec dans pod

```bash
kubectl exec -it qgis-hub-0 -n user-<u> -- bash
# Puis dans le pod :
python -c "import hub.main; print('imports OK')"
env | grep -E "HUB_|ONYXIA_"
```

### 6.3 Restart propre

```bash
kubectl delete pod qgis-hub-0 -n user-<u>
kubectl wait --for=condition=ready pod/qgis-hub-0 -n user-<u> --timeout=120s
```

---

## 7. Invariants d'architecture

Voir [README.md § Invariants](README.md#invariants-darchitecture) — 5
règles dures :

1. Audit trail = source de vérité (tests `test_storymap_audit.py`)
2. Anti-hallucination géographique (catalogue `_QGIS_ESSENTIALS`)
3. Mémoire à 3 couches (L1 session, L2 étude, L3 user, [L4 RAG WIP])
4. Persistance pod : PVC `/home/onyxia/work` uniquement
5. Tests d'invariant à chaque commit

---

## 8. Contribuer

1. Fork + branche feature depuis `main`
2. Commits atomiques, message français, format `type(scope): sujet`
3. Tests pytest passent avant PR
4. PR sur `main` avec description + tests

Charte de code : voir [docs/CHARTE_AGENT.md](docs/CHARTE_AGENT.md).
Décisions architecturales : [docs/decisions/](docs/decisions/) (ADR).
