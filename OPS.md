# OPS — qgis-sspcloud

Runbook opérationnel : monitoring, incidents fréquents, procédures de
maintenance.

Version 2026-08-06 · Sprint Day 5 CLOS · chart Helm 1.2.5.

> **Contexte** : depuis Sprint Day 5, chaque user déploie son propre
> service via `helm install qgis-hub` depuis son terminal Jupyter Onyxia
> (SA `jupyter-python-<hash>` avec `edit` role). Aucun pod admin central
> requis. Les procédures ci-dessous s'exécutent depuis le terminal
> Jupyter du user concerné (self-service).

---

## 1. Endpoints monitoring

| Endpoint | Auth | Info retournée |
|---|---|---|
| `/version` | public | commit SHA, version tag |
| `/healthz` | public | status pod (200 = OK) |
| `/probe` | public | K8s probe |
| `/diagnostics/isolation` | Bearer HUB_API_KEY | switches physiques, locks, session_active_state stats, day3_priority |
| `/diagnostics/mcp-sessions` | Cookie OIDC | sessions MCP par user + divergence DB |

Depuis le terminal Jupyter de l'user :
```bash
KEY=$(kubectl get secret qgis-hub-apikey -o jsonpath='{.data.HUB_API_KEY}' | base64 -d)
curl -H "Authorization: Bearer $KEY" \
  https://user-<u>-qgis.user.lab.sspcloud.fr/diagnostics/isolation | jq
```

## 2. Rotation credentials

### 2.1 HUB_API_KEY (rotation manuelle)

Source de vérité : Secret K8s `qgis-hub-apikey` dans namespace `user-<u>`.
```bash
kubectl -n user-<u> get secret qgis-hub-apikey -o jsonpath='{.data.HUB_API_KEY}' | base64 -d
```

Pour rotate :
```bash
NEW_KEY=$(openssl rand -hex 32)
kubectl -n user-<u> patch secret qgis-hub-apikey --type='json' \
  -p='[{"op":"replace","path":"/data/HUB_API_KEY","value":"'"$(echo -n $NEW_KEY | base64)"'"}]'
# Trigger reload endpoint (evite restart complet)
curl -X POST -H "Authorization: Bearer <OLD_KEY>" \
  https://user-<u>-qgis.user.lab.sspcloud.fr/api/reload-hub-key
```

### 2.2 LLM_API_KEY (Sprint Day 5 Phase 1.7-C)

Le portail admin n'existe plus. L'user saisit sa clé dans
`/workspace` → bloc **"🤖 Clé LLM (agent IA)"** → form POST.

Endpoint hub `POST /workspace/llm-key` :
1. Valide auth (cookie `hub_api_key` ou OIDC)
2. Appelle webhook interne agent `POST /api/reload-llm-key`
   (`X-Hub-Auth: HUB_API_KEY`)
3. Agent met à jour `os.environ["LLM_API_KEY"]` en RAM → zéro downtime

**Ephémère** : la clé survit tant que le pod agent tourne. Sur
`helm upgrade` qui patche `agent-statefulset`, le pod agent est recréé →
retape la clé sur `/workspace`. Persistance PVC dans backlog 4-A.

### 2.3 STS MinIO SSPCloud (⚠ expire 7j)

Les env vars `AWS_ACCESS_KEY_ID` / `AWS_SECRET_ACCESS_KEY` /
`AWS_SESSION_TOKEN` injectées dans le pod par Onyxia expirent après 7
jours sans refresh automatique. Symptôme : `publish` échoue avec
`InvalidAccessKeyId`, `SignatureDoesNotMatch`, `ExpiredToken`.

**Fix historique** : Secret K8s `passerelle-s3-creds` (voir
[[reference_perennite_bug_A_minio]] mémoire).

**Workaround immédiat** : relancer le service côté UI Onyxia (le
`kubectl rollout restart` ne suffit pas car env vars statiques).

## 3. Redéployer un pod

### 3.1 Pod hub principal
```bash
kubectl -n user-<u> delete pod jupyter-python-<hash>-0
# StatefulSet repull `qgis-hub:latest` et recréé pod
```

### 3.2 Pod bridge (proxy MCP secondaire)
```bash
kubectl -n user-<u> delete pod qgis-mcp-bridge-jupyter-python-0
```

### 3.3 Pod agent
```bash
kubectl -n user-<u> delete pod qgis-agent-0
```

### 3.4 Pod workspace QGIS
```bash
kubectl -n user-<u> delete pod qgis-workspace-<u>-0
# ⚠ perte : le projet QGIS RAM en cours est perdu (mais save dans .qgz)
```

## 4. Backups

### 4.1 SQLite hub
`/data/hub.db` et `/data/sessions.db` sont sur PVC. Snapshot pod jupyter
admin :
```bash
kubectl -n user-<u> cp jupyter-python-<hash>-0:/data/hub.db backup-hub-$(date +%F).db
```

### 4.2 Données études (bundle autoportant)
```bash
kubectl -n user-<u> exec jupyter-python-<hash>-0 -- tar czf - /data/studies | tar xzf - -C ./backup-studies-$(date +%F)/
```

Ou : le hub expose `GET /studies/{sid}/export` (ZIP autoportant) pour
récupérer une étude spécifique via HTTP.

### 4.3 Publications S3
Les publications S3 sont sur MinIO SSPCloud (bucket `nic01asfr-qgis-*`).
Backup via `aws s3 sync` depuis pod jupyter admin (auth STS via env vars,
voir §2.3).

## 5. Incidents fréquents

### 5.1 Hub 502 après restart pod
Onyxia probe timeout 2s trop court. Fix historique Bug #17 : le hub
retourne 200 immédiatement au kube-probe. Voir mémoire
`[[project_bug_17_mcp_cold_start_502]]`.

Si 502 persiste plus de 30s :
```bash
kubectl -n user-<u> logs jupyter-python-<hash>-0 --tail=50
# Chercher exceptions au startup (init_db, load models)
```

### 5.2 Publish échoue en 401
Depuis Day 3.1c (commit `a1e10de`), `/publish` est dans whitelist
inter-pod. Vérifier que l'image déployée contient ce fix :
```bash
kubectl -n user-<u> exec jupyter-python-<hash>-0 -- python -c \
  "from hub.auth import _OIDC_MIDDLEWARE_INTER_POD; print('/publish' in _OIDC_MIDDLEWARE_INTER_POD)"
```

### 5.3 Study_zone bbox perdu entre 2 calls MCP
Bug côté BigQgisMCP (workspace). Workaround : passer `bbox` explicite à
chaque `smart_load` au lieu de compter sur `set_study_zone` persistant.
Fix backlog côté BigQgisMCP.

### 5.4 Ingress 60s timeout tue run_recipe
Fix historique Bug ingress : le hub patch son propre ingress au startup
avec `proxy-read-timeout: 600s`. Voir
`[[project_onboarded_ingress_60s_timeout]]`.

Si le patch échoue au startup :
```bash
kubectl -n user-<u> logs jupyter-python-<hash>-0 | grep "patch ingress"
kubectl -n user-<u> annotate ingress jupyter-python-<hash>-ui \
  nginx.ingress.kubernetes.io/proxy-read-timeout=600 --overwrite
```

### 5.5 Cross-user desk HTML accessible
`nic01asfr` peut voir le HTML desk de `nicolaslaval` (avec noms
d'études). L'iframe agent bloque, mais le HTML lui-même est servi. Point
identifié 2026-08-02, à corriger dans middleware OIDC (check ONYXIA_USER
strict sur toutes routes UI, pas juste iframe agent).

## 6. Monitoring proactif

### 6.1 Métriques à surveiller

Via `/diagnostics/isolation` :
- `switches_total_since_boot` : croissance normale ~1 par action user.
  Anomalie : >100/min = boucle infinie ou race.
- `active_locks.active_study_switch` : normalement 0 hors switch actif.
  Anomalie : bloqué à N>0 pendant >30s = deadlock potentiel.
- `session_active_state.active_entries` : croissance normale ~1 par
  session MCP active. Anomalie : >100 = leak ou GC stopped.

Via `/diagnostics/mcp-sessions` (par user) :
- `count_diverging` : sessions MCP scopées ≠ DB. Normal en usage
  multi-session, anormal si toujours >0 sur user unique connecté 1 seul
  Claude Desktop.

### 6.2 Logs à surveiller

```bash
kubectl -n user-<u> logs jupyter-python-<hash>-0 --tail=100 -f | grep -E "WARNING|ERROR"
```

Motifs anormaux :
- `ensure_active_study: switch X -> Y echec` répétés (déchec save/reload)
- `PROJECT_ALREADY_LOADED` très fréquent = skip re-read OK (normal)
- `STUDY_SAVE_SKIP n_layers=0` = save vide sur projet vide (normal)
- `gc_loop: purge N entrees` toutes les heures = GC session state (normal)
- `Cette clé n'appartient pas à ce pod` = auth cross-user rejetée (bien)

## 7. Procédures de maintenance planifiée

### 7.1 Mise à jour image hub

1. Merger PR sur `main` → CI GitHub Actions build `qgis-hub:latest`
2. Attendre CI green (~8 min)
3. Redéployer pods (voir §3)
4. Vérifier `/version` retourne le nouveau commit
5. Smoke test : `curl /diagnostics/isolation`, un `study_list` via MCP

### 7.2 Nettoyage sessions MCP orphelines

Les entries `session_active_state` expirent après 24h + GC 1h. Manuel :
```bash
kubectl -n user-<u> exec jupyter-python-<hash>-0 -- python -c \
  "import asyncio; from hub import session_active_state as sas; asyncio.run(sas._reset_for_tests()); print('reset done')"
```

⚠ Attention : coupe toutes les sessions MCP externes en cours, celles-ci
fallback sur DB.active_study.

### 7.3 Migration schéma DB
Idempotent au startup via `hub/hub/studies.py::init_db()`. Ajouter une
migration = éditer la fonction, redéployer, elle s'applique au boot.

## 8. Références mémoire

- `[[reference_perennite_bug_A_minio]]` : procédure STS MinIO 7j expire
- `[[reference_option_alpha_webhook_llm]]` : webhook LLM reload
- `[[project_bug_17_mcp_cold_start_502]]` : fix probe cold start
- `[[project_onboarded_ingress_60s_timeout]]` : fix ingress timeout
- `[[reference_qgis_sspcloud_deploy_process]]` : deploy détaillé
- `[[reference_qgis_sspcloud_bugs_d1_d6]]` : bugs historiques D1-D6
- `[[project_sprint_isolation_day3_livre]]` : détail Sprint Day 3
