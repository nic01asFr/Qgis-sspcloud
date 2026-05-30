# Structure, responsabilités et procédures — qgis-sspcloud

> Document de référence opérationnel. Lit-le AVANT toute intervention sur le code,
> le déploiement ou les tests. Garde-le à jour à chaque évolution structurelle
> majeure. Compatible avec [`CHARTE_AGENT.md`](CHARTE_AGENT.md) qui définit la
> vision et les principes ; ce document décrit la mécanique réelle aujourd'hui.

## Table des matières

1. [Vue d'ensemble](#1-vue-densemble)
2. [Qui fait quoi, où](#2-qui-fait-quoi-où)
3. [Données et persistance](#3-données-et-persistance)
4. [Process de développement](#4-process-de-développement)
5. [Process de déploiement](#5-process-de-déploiement)
6. [Process de test](#6-process-de-test)
7. [Procédures opérationnelles](#7-procédures-opérationnelles)
8. [Pièges connus & décisions verrouillées](#8-pièges-connus--décisions-verrouillées)

---

## 1. Vue d'ensemble

### Composants en production

```
┌─────────────────────────────────────────────────────────────────────┐
│  SSPCloud Datalab (user-nicolaslaval namespace)                     │
│                                                                     │
│  ┌──────────────┐   ┌───────────────┐   ┌──────────────────────┐    │
│  │  HUB         │   │  AGENT        │   │  WORKSPACE           │    │
│  │  qgis-hub    │◄──┤  qgis-agent   │◄──┤  qgis-workspace      │    │
│  │              │   │               │   │                      │    │
│  │  FastAPI     │   │  FastAPI      │   │  QGIS Desktop        │    │
│  │  Orchestre   │   │  Chat LLM     │   │  + MCP server        │    │
│  │  Sert l'UI   │   │  Mémoire      │   │  + noVNC            │    │
│  │  Publication │   │  Tools dispatch│  │  + xvfb              │    │
│  │              │   │               │   │                      │    │
│  │  PVC 10Gi    │   │  PVC 10Gi     │   │  PVC 10Gi            │    │
│  │  Helm chart  │   │  Auto-bootstrap│  │  Auto-bootstrap      │    │
│  └──────┬───────┘   └───────┬───────┘   └──────────┬───────────┘    │
│         │                   │                       │                │
│         └───── MinIO S3 ────┴──── Vault (LLM_API_KEY) ────────────┐  │
│                (publications, livrables publics)                  │  │
└────────────────────────────────────────────────────────────────────┘
```

### Surfaces UI (servies par le HUB uniquement)

| URL | Vue | Rôle |
|-----|-----|------|
| `/` | Redirect intelligent | → `/desk` si étude active, sinon `/workspace` |
| `/workspace` | Vue compte | Études, livrables publiés, KPIs, créer une étude |
| `/desk` | Bureau atelier | 3 panneaux : drawer ressources + canvas QGIS noVNC + chat agent iframe |
| `/published/{owner}/{kind}/{slug}` | Livrable public | Storymap HTML / PDF / dataset (avec mini-header CEREMA si HTML) |
| `/login?key=...` | Onboarding clé API | Pose le cookie `hub_api_key` (90j) |

L'agent iframe est servi via le sous-domaine `user-X-qgis-agent.user.lab.sspcloud.fr` mais SEUL `chat.html?embed=1` est utile : les routes `/desk` `/workspace` côté agent existent en code mais ne sont jamais routées en prod.

### Cycle d'usage (cf. CHARTE_AGENT §2)

```
EXPLORATION   →   PRODUCTION   →   DIFFUSION   →   CAPITALISATION
   (libre)         (étude)         (livrable)         (template)
```

L'étude est l'unité atomique qui traverse ce cycle.

---

## 2. Qui fait quoi, où

### HUB — orchestrateur et UI

**Repo path** : `hub/`
**Image** : `ghcr.io/nic01asfr/qgis-hub:latest` (build CI à chaque push main)
**Chart Helm** : `charts/qgis-hub/`
**Code principal** : `hub/hub/main.py` (3 473 lignes — refactor en dette technique)

**Responsabilités** :
- Authentification (cookie `hub_api_key` browser, Bearer API key MCP, OIDC SSPCloud)
- Gestion études : `/studies/*` (CRUD), `/studies/active`, activation/archive
- Sessions workspace : `/sessions` création/réveil/scale, `/workspace/wake` avec lock anti-double
- Proxy MCP vers workspace : `/mcp` (Streamable HTTP JSON-RPC)
- Publications S3 : `POST /publish/{kind}/{slug}` (5 kinds), `/published/{owner}/{kind}/{slug}` lecture
- Catalog : `/catalog/{owner}` (depuis S3), `/desk/catalog` (enrichi pour UI)
- Profils : `/profiles`, `/profiles/{id}`, `/profiles/reload` (consomme `hub/hub/profiles/*.yaml`)
- GeoAI : `/geoai/status`, `/geoai/{path:path}` proxy avec scale-up à la demande
- Templates Jinja2 : `hub/templates/{desk,workspace}.html` (canon, source de vérité UI)
- Self-heal infrastructure : patch ingress timeout 600s, patch env workspace existants, bootstrap SS agent

**Fichiers clés** :
- `hub/hub/main.py` : routes FastAPI, bootstrap, UI render
- `hub/hub/auth.py` : 3 modes auth (cookie / API key / OIDC), `qgis-hub-apikey` Secret K8s
- `hub/hub/sessions.py` : création/management workspace QGIS pod via kubectl wrappers
- `hub/hub/studies.py` : CRUD études (table SQLite locale + fichiers sur workspace PVC)
- `hub/hub/s3_publication.py` : push/read S3 MinIO
- `hub/hub/audit_trail.py` : instrumentation MCP injectée dans workspace au session start
- `hub/hub/profile_manager.py` : loader YAML profils + cache
- `hub/hub/storymap_dsfr.py` : générateur storymap audit-honnête
- `hub/hub/profiles/*.yaml` : 8 profils déclarés (source de vérité unique)

### AGENT — chat LLM avec mémoire

**Repo path** : `agent/`
**Image** : `ghcr.io/nic01asfr/qgis-agent:latest`
**Déploiement** : auto-bootstrap par `hub._bootstrap_agent` au démarrage du hub (PAS via Helm chart)
**Code principal** : `agent/agent/qgis_agent.py` + `agent/agent/main.py`

**Responsabilités** :
- Chat streaming SSE : `POST /chat` avec history + tools dispatch
- Sessions chat : `/sessions/{id}/messages`, checkpoints, rollback `↶ Revenir avant`
- Mémoire 3 couches : `agent/memory.py` (SQLite sur PVC `/data`)
- Tools MCP dispatch : appel hub `/mcp`, traduction OpenAI tools format
- Context router : `/context/render/{session_id}` (postMessage depuis drawer publi)
- Profils : système prompt + tools whitelist (BUG ACTUEL : `_PROFILES_DIR` cassé, voir §8)
- Vector store : `agent/vector_store.py` (SQLite-vec) pour KB + insights
- Audit trail consumer : enregistre `tool_calls_made` par turn

**Fichiers clés** :
- `agent/agent/qgis_agent.py` : classe `QGISAgent`, boucle tool, prompts, garde-fous
- `agent/agent/main.py` : routes FastAPI agent, SSE, sessions HTTP
- `agent/agent/memory.py` : tables sessions/messages/insights/recipes/checkpoints
- `agent/agent/enrichers/` : memory_recall, recipe_matcher, geo_validator
- `agent/templates/chat.html` : iframe servie sur `/?embed=1` depuis hub `/desk`
- `agent/agent/tips/qgis_tips.md` : KB enrichers post-erreur

**Code mort à supprimer** (cf. §8 D4) :
- `agent/templates/desk.html` et `workspace.html`
- Routes `agent/main.py:912-991` (`/desk*`, `/workspace/wake`, `/desk/study/*/save`)

### WORKSPACE — QGIS Desktop + MCP server

**Repo path** : SÉPARÉ — `BigQgisMCP/` (au même niveau dans Github Repositories)
**Image** : `ghcr.io/nic01asfr/qgisremotemcp:latest` (build MANUEL, voir §4)
**Déploiement** : auto-bootstrap par hub `sessions.create_session` + scale 0↔1 selon usage

**Responsabilités** :
- QGIS Desktop 3.34 LTR full GUI sur Xvfb (display `:99`)
- noVNC sur :6080 (l'iframe `/desk` affiche le canvas)
- MCP server sur :8100 (JSON-RPC streamable HTTP)
- API REST sur :8080 (`/health`, `/files/*`)
- Audit trail instrumentation injectée par le hub au session start
- Stockage local PVC : projets, data, exports, cache, recipes, treatments.jsonl

**Tools MCP exposés** (~45 tools) : `set_study_zone`, `smart_load`, `execute_python`, `run_processing`, `run_recipe`, `export_pdf`, `export_web_map`, `export_flood_map`, `publish_artifact`, etc.

**Fichiers clés** (dans BigQgisMCP) :
- `main_mcp.py` : entrypoint MCP server
- `src/qgis_bridge.py` : actions PyQGIS (UNIX socket vers QGIS)
- `src/qgis_helpers.py` : helpers haut niveau (`set_study_zone`, `smart_load`, `geocode`)
- `recipes/*.yaml` : recettes builtin (`risque_inondation`, `densite_bati`, etc.)
- `Dockerfile` : image complète Ubuntu 24.04 + QGIS + Python + noVNC

### MCP — interface entre agent et workspace

L'agent NE communique PAS directement avec QGIS. Il passe par le HUB qui proxifie :

```
Agent (qgis-agent pod)
   ↓ POST {HUB_URL}/mcp
Hub (qgis-hub pod)
   ↓ POST http://qgis-workspace-{user}.user-{user}.svc.cluster.local:8100/mcp
Workspace (qgis-workspace pod)
   ↓ UNIX socket
QGIS Desktop process
```

Cette indirection permet :
- L'audit trail (le hub voit tous les tool calls)
- L'authentification (Bearer API key hub validé une fois)
- L'instrumentation injectée au session start

---

## 3. Données et persistance

### Stockage par composant

| Composant | PVC mount | Contenu | Survit à... |
|-----------|-----------|---------|-------------|
| **Hub** | `/home/onyxia/work` (10Gi) | `apikeys.db` (legacy), audit logs | Pod restart, helm upgrade |
| **Agent** | `/data` (10Gi) | `memory.db` (sessions, messages, insights), vector store | Pod restart |
| **Workspace** | `/home/onyxia/work` (10Gi) | `studies/{sid}/` (project + data + treatments + exports + recipes) | Pod restart, scale 0 |

### Structure `/data/studies/{sid}/` (workspace PVC)

```
/data/studies/{sid}/
├── project.qgz                    # Projet QGIS sauvegardé
├── data/                          # Données spatiales (gpkg, raster)
│   └── *.gpkg
├── exports/                       # Livrables locaux avant publication
│   ├── storymaps/*.html
│   ├── pdf/*.pdf
│   └── data/*.gpkg
├── recipes/                       # Recettes locales (CRUD à venir, cf. roadmap)
│   └── *.yaml
├── treatments.jsonl               # Audit trail tool calls (source de vérité)
└── meta.json                      # Métadonnées étude
```

### S3 MinIO (publications publiques)

```
Bucket: {owner}/qgis-workspace/published/
├── {owner}/
│   ├── storymap/
│   │   └── {slug}.html            # Cache-Control: public, max-age=60
│   ├── pdf/
│   │   └── {slug}.pdf
│   ├── recipe/
│   │   └── {slug}.yaml
│   ├── dataset/
│   │   └── {slug}.gpkg
│   └── flux/
│       └── {slug}.qgz
```

Métadonnées S3 : `study_id`, `published_at`, `content_type`.

### Mémoire 3 couches (CHARTE §4)

| Couche | Source | Stockage | Visibilité |
|--------|--------|----------|------------|
| **L1** session | `agent/memory.py.messages` | SQLite agent PVC | Conversation courante |
| **L2** étude | `studies.get_active_study()` + MCP `get_project_info` | Hub + workspace pod (project_state in-memory) | Étude active uniquement |
| **L3** user | `agent/memory.py.user_profile/insights/memory_doc` | SQLite agent PVC | Persistant cross-études |

⚠️ L2 est **fragmenté** entre hub (méta) et workspace (état projet QGIS). Si le workspace dort, L2 partiellement perdu (sauf reload via `_auto_activate_active_study_after_wake`).

---

## 4. Process de développement

### Bootstrap initial (nouveau dev)

```bash
# 1. Cloner les 2 repos
git clone https://github.com/nic01asfr/qgis-sspcloud
git clone https://github.com/nic01asfr/BigQgisMCP

# 2. Pour modifier hub ou agent : VS Code, env Python 3.13
cd qgis-sspcloud
python -m venv .venv
.venv/bin/pip install -e hub/  # ou agent/

# 3. Pour modifier workspace (QGIS) : besoin de QGIS local + plugins
# Voir BigQgisMCP/README.md
```

### Workflow modification HUB ou AGENT

```bash
# 1. Modifier le code
vim hub/hub/main.py  # ou agent/agent/qgis_agent.py

# 2. Vérifier syntaxe + tests locaux
python -c "import hub.main"  # smoke test imports
pytest hub/tests/ -q          # tests hub
pytest agent/tests/ -q        # tests agent

# 3. Commit + push
git add -A
git commit -m "fix(zone): description courte"
git push origin main

# 4. CI build automatique → image ghcr.io/.../qgis-{hub,agent}:latest
# (build_workflow.yml ~5 min)

# 5. Déploiement (cf. §5)
```

### Workflow modification WORKSPACE (BigQgisMCP)

```bash
# Build manuel (long, ~30 min — QGIS Desktop image complète)
cd BigQgisMCP
docker build -t ghcr.io/nic01asfr/qgisremotemcp:latest .
docker push ghcr.io/nic01asfr/qgisremotemcp:latest

# Forcer le re-pull dans le pod workspace :
# curl -X POST {HUB_URL}/admin/workspace-fix-image \
#   -H "Authorization: Bearer {hub_api_key}"
```

### Convention de commits

```
fix(zone): correction bug
feat(zone): nouvelle fonctionnalité
docs(zone): documentation
chore(zone): refactor/cleanup
test(zone): tests

zones : hub, agent, workspace, ci, docs, vague-N, sprint-X
```

Référence aux commits récents : `git log --oneline -20` montre la convention en pratique.

---

## 5. Process de déploiement

### Déploiement initial (1ère fois pour un user)

L'utilisateur passe par le **portail Onyxia SSPCloud** :
1. Datalab → Catalogue → service `qgis-hub`
2. Configurer : LLM_API_KEY (Vault), credentials S3 (auto)
3. Lancer → pod hub démarre → Ingress créé → `https://user-X-qgis.user.lab.sspcloud.fr`

Au démarrage du hub :
- `_bootstrap_agent()` crée le SS qgis-agent (auto, idempotent)
- `_patch_ingress_for_long_running()` patche les ingress timeout 600s
- Le pod workspace est créé lazy à la 1ère session

### Mise à jour Hub (commit poussé)

```bash
# 1. Attendre que CI build verte (~5 min)
gh run list --workflow=build.yml --limit 5

# 2. Pull la nouvelle image via Onyxia
#    Option A : portail Onyxia → service qgis-hub → "Mettre à jour"
#    Option B : kubectl rollout restart statefulset/qgis-hub-...
#               (besoin RBAC admin namespace)

# 3. Le pod redémarre, pull :latest (imagePullPolicy: Always)
# 4. _bootstrap_agent re-patche le SS agent + ingress
```

### Mise à jour Agent (commit poussé)

```bash
# 1. Attendre CI build verte
# 2. Forcer le restart agent (pull :latest)
curl -X GET {HUB_URL}/api/refresh-llm-config
# OU cliquer "Vérifier ma config" dans le chat
#
# Ce endpoint relit le secret LLM, patche env du SS agent,
# delete pod-0 → kubelet recrée avec imagePullPolicy: Always.
```

### Mise à jour Workspace (image manuelle)

```bash
# 1. Rebuild manuel (cf. §4)
# 2. Force re-pull dans le pod workspace
curl -X POST {HUB_URL}/admin/workspace-fix-image \
  -H "Authorization: Bearer {hub_api_key}"
```

### Validation post-déploiement (smoke check)

```bash
# Healthchecks
curl {HUB_URL}/health
curl {HUB_URL}/desk/agent-health
curl {HUB_URL}/geoai/status

# UI manuelle Chrome incognito
# → /workspace → bouton "Ouvrir le bureau"
# → /desk → drawer publi, chip GPU footer, chat fonctionnel
# → publier une storymap → vérifier /published/.../storymap/X
```

---

## 6. Process de test

### Tests unitaires existants

```bash
# Hub
cd qgis-sspcloud
pytest hub/tests/ -q
# Tests clés : test_storymap_audit, test_audit_trail, test_studies

# Agent
pytest agent/tests/ -q
# Tests clés : test_checkpoints, test_memory, test_enrichers

# Workspace (BigQgisMCP)
cd ../BigQgisMCP
pytest tests/ -q
```

### Tests d'invariant CHARTE_AGENT

Tests qui verrouillent les règles dures de la charte (audit honnête, fabrication interdite, rollback intègre). Voir `hub/tests/test_storymap_audit.py` et `hub/tests/test_audit_trail.py`.

À AJOUTER en CI obligatoire (cf. plan V1 phase A.7).

### Smoke test CI (à ajouter)

Dans `.github/workflows/build.yml`, après build :

```yaml
- name: Smoke test imports
  run: |
    docker run --rm ghcr.io/${{ env.OWNER }}/qgis-hub:${{ github.sha }} \
      python -c "import hub.main"
    docker run --rm ghcr.io/${{ env.OWNER }}/qgis-agent:${{ github.sha }} \
      python -c "import agent.main"
```

### Test E2E manuel (avant chaque release CEREMA)

```
1. Chrome incognito (cookies vierges)
2. Auth SSPCloud → arrivée /workspace
3. Cliquer "Créer ma première étude" → nom + profil → "Ouvrir"
4. Dans /desk, attendre canvas QGIS + agent ready
5. Tester chat : "Analyse risque inondation T100 sur Béziers"
6. Vérifier : run_recipe lance, livrables générés, publish_artifact succès
7. Vérifier /published/.../storymap/X (avec bandeau CEREMA)
8. Vérifier compteurs footer + drawer Publi rafraîchis
```

---

## 7. Procédures opérationnelles

### Pod hub crashe au démarrage

**Cause fréquente** : env vars manquantes (HUB_URL, LLM_API_KEY)

```bash
# Diagnostic
kubectl logs qgis-hub-... -n user-X --tail=50

# Si "GEOAI_GPU_PORT" parsing error → bug du fix 71ff470 (déjà corrigé)
# Si "ImportError" → CI smoke test devrait avoir attrapé
```

### Pod agent boucle en restart

**Cause fréquente** : LLM_API_KEY absente, HUB_API_KEY desync

```bash
# Force refresh config
curl -X GET {HUB_URL}/api/refresh-llm-config

# Si toujours KO, vérifier que Secret existe
kubectl get secret qgis-hub-apikey -n user-X -o yaml
```

### Workspace endormi (scale 0) ne se réveille pas

```bash
# Force réveil
curl -X POST {HUB_URL}/workspace/wake

# Ou via UI : /desk → bouton "Réveiller le bureau"
```

### Publication n'apparaît pas dans drawer

**Diagnostic** :
1. Vérifier `/catalog/{owner}` retourne bien la publi
2. Vérifier `study_id` dans l'item match l'étude active
3. Forcer refresh : reload `/desk`

### Memory.db corrompue (cas extrême)

PVC agent garde la SQLite. En cas de corruption :
```bash
kubectl exec qgis-agent-0 -n user-X -- ls -la /data/agent/memory.db
# Backup + suppression si nécessaire (perd l'historique mais l'étude reste)
```

### Restauration depuis ZIP étude

```bash
# Télécharger : {HUB_URL}/studies/{sid}/export → archive.zip
# Re-upload : pas d'endpoint dédié actuellement,
#   il faut décompresser dans /data/studies/{sid}/ via execute_python
```

---

## 8. Pièges connus & décisions verrouillées

### Bugs cassures silencieuses (à corriger Phase V1)

| ID | Bug | Fichier | Impact |
|----|-----|---------|--------|
| D1 | `_PROFILES_DIR = "../../../qgis-mcp-hub/..."` cassé | `agent/qgis_agent.py:54` | Profils inertes : tous les agents en prod ont le prompt générique |
| D2 | `PROFILES_DIR` env pas injectée dans agent SS | `hub/main.py:200-211` | Pas de bypass possible pour D1 |
| D3 | `mcp_tools.allowed` whitelist inerte | `agent/qgis_agent.py:74-91` | Profils ne filtrent pas les tools |
| D4 | Templates morts `agent/templates/{desk,workspace}.html` | drift hub vs agent | Code mort, confusion future |
| D5 | URLs fallback `qgis-mcp-bridge` legacy | `agent/main.py:99, 858, 918` | Host inexistant si env vars absentes |
| D6 | `save_recipe` jamais appelée | `agent/memory.py:690` | Table morte, recettes non créables |

### Décisions architecturales verrouillées (Q1-Q8)

Cf. plan V1 publication CEREMA :

- **Q1** Profile × Scope : N:M flexible (un profil tourne dans plusieurs scopes)
- **Q2** Routage prod : hub seul sert /desk et /workspace
- **Q3** Recipe storage : workspace PVC + catalog indexé par hub
- **Q4** Agent C livrable : même pod agent avec `?scope=diffusion` query param
- **Q5** Macro markers : explicite (chat command) + rétroactif (sélection timeline)
- **Q6** Recipe versioning : SHA hash + auto-history
- **Q7** ACL publication : public-only v1
- **Q8** Migration profile_id : backwards-compatible (scope dérivé de la surface si absent)

### Conventions URL / hostnames

```
HUB        : https://user-{onyxia_user}-qgis.user.lab.sspcloud.fr
AGENT      : https://user-{onyxia_user}-qgis-agent.user.lab.sspcloud.fr
WORKSPACE  : https://qgis-workspace-{onyxia_user}-novnc.user.lab.sspcloud.fr (noVNC seul)
PUBLISHED  : {HUB}/published/{owner}/{kind}/{slug}
S3 BUCKET  : minio.lab.sspcloud.fr/{owner}/qgis-workspace/...
```

### Profils disponibles (`hub/hub/profiles/*.yaml`)

| Profil | Usage métier | Modèle LLM |
|--------|--------------|------------|
| `standard` | Default général | qwen3-6-35b-moe |
| `risk_analyst` | Risques inondations, PPRi | qwen3-6-35b-moe |
| `db_analyst` | Analyses statistiques, PostGIS | qwen3-6-35b-moe |
| `geoai_analyst` | Vision IA (SAM, DeepForest) | gemma4-26b-moe |
| `storymap_creator` | Récits DSFR | qwen3-6-35b-moe |
| `map_composer` | Compositeur cartes A3/A4 | qwen3-6-35b-moe |
| `recipe_creator` | Édition recettes YAML | qwen3-6-35b-moe |
| `guided_tour` | Onboarding accompagné | qwen3-6-35b-moe |

### Endpoints critiques à connaître

```
# Hub
GET  /health                     - readiness probe
GET  /                           - redirect intelligent
GET  /workspace                  - vue compte
GET  /desk                       - bureau intégré
GET  /published/{o}/{k}/{slug}   - livrable public
POST /publish/{kind}/{slug}      - publier (auth requise)
GET  /catalog/{owner}            - liste publications
GET  /desk/catalog               - idem enrichi UI
GET  /studies                    - mes études
GET  /studies/active             - étude active
POST /workspace/wake             - réveil workspace
GET  /api/refresh-llm-config     - resync config LLM (restart agent)
GET  /geoai/status               - état pod GPU
POST /mcp                        - proxy MCP vers workspace

# Agent
GET  /                           - chat.html (ou ?embed=1)
POST /chat                       - SSE streaming
GET  /api/status                 - has_llm_key, profile
POST /api/refresh-llm-config     - proxy vers hub same-origin
GET  /sessions                   - sessions chat
GET  /sessions/{id}/messages     - historique
POST /sessions/{id}/rollback/{ckpt} - rollback projet QGIS
```

---

## Évolution de ce document

À mettre à jour quand :
- Une nouvelle vague de fix structurels est livrée (cf. Sprints/Vagues)
- Une décision architecturale change (Q1-Q8)
- Un composant majeur change de responsabilité
- Un endpoint critique est ajouté/retiré
- Un piège opérationnel est identifié

Maintenir la cohérence avec [`CHARTE_AGENT.md`](CHARTE_AGENT.md) (vision/principes).
