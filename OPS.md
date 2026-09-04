# OPS — qgis-sspcloud

Runbook opérationnel : monitoring, incidents fréquents, procédures de
maintenance.

Version 2026-09-04 · chart Helm 1.3.0.

> Revu le 2026-09-04 : trois sections decrivaient un etat revolu — la
> commande de redemarrage du hub (§3.1) visait le pod Jupyter de
> l'utilisateur, la cle LLM etait annoncee perdue a chaque recreation
> (§2.2), et le renouvellement des acces S3 ne connaissait qu'une voie
> (§2.3). Chaque commande de ce document a ete executee sur les deux
> instances de reference avant publication.

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

**La clé survit désormais au redémarrage** (corrigé le 2026-09-04). Depuis le
chart 1.3.0, un Secret `qgis-llm-apikey` en est la source et l'agent le lit
via `secretKeyRef` : le pod recréé la retrouve.

Vérifié sur les deux instances après recréation des pods agent :

```
user-nic01asfr     secret=35 caracteres, env du pod=35  concordants
user-nicolaslaval  secret=35 caracteres, env du pod=35  concordants
```

Cette section annonçait l'inverse — « retape la clé sur /workspace » — ce qui
décrivait le mécanisme d'origine : formulaire → webhook → variable en RAM.

**Reste vrai dans un seul cas** : une clé saisie via `POST /workspace/llm-key`
alors que le Secret est vide. Le hub fait un merge-patch sur le Secret (cf.
`hub/hub/main.py:workspace_set_llm_key`), donc ce cas est couvert lui aussi —
la mise en garde ne vaut que pour une instance antérieure au chart 1.3.0.

### 2.3 STS MinIO SSPCloud (⚠ expire 7j)

Les env vars `AWS_ACCESS_KEY_ID` / `AWS_SECRET_ACCESS_KEY` /
`AWS_SESSION_TOKEN` injectées dans le pod par Onyxia expirent après 7
jours sans refresh automatique. Symptôme : `publish` échoue avec
`InvalidAccessKeyId`, `SignatureDoesNotMatch`, `ExpiredToken`.

Symptôme complémentaire (2026-09-04) : une requête `HEAD` n'a pas de corps,
MinIO n'y place donc aucun code d'erreur nommé et botocore se rabat sur le
statut HTTP. Un `403` nu remonte, que la détection d'expiration ne
reconnaissait pas — corrigé, mais un journal ancien peut porter cette forme.

**Le plus simple : un jeton d'identité, sans relancer aucun service.** Le
jeton OIDC d'un compte SSPCloud porte l'audience `minio-datanode` ; il suffit
donc à obtenir 7 jours d'accès auprès de STS. L'utilisateur le récupère sur
datalab.sspcloud.fr (« Mon compte »), puis :

```python
urllib.parse.urlencode({
    "Action": "AssumeRoleWithWebIdentity", "Version": "2011-06-15",
    "WebIdentityToken": jeton, "DurationSeconds": "604800",
})  # POST https://minio.lab.sspcloud.fr
```

Le résultat va dans le **Secret `passerelle-s3-creds`**, que le hub lit en
premier et ne retient que si son jeton est encore valide
(`_sts_encore_valide`) — sinon il bascule sur l'environnement du pod. Patcher
en `merge` les trois clés d'identification préserve les autres entrées du
Secret (`HF_TOKEN`, `S3_ENCRYPT_KEY`).

Cache d'une heure (`_CREDS_TTL`) : `delete pod qgis-hub-0` pour un effet
immédiat.

**Autre voie** : relancer le service depuis l'UI Onyxia. Elle fonctionne, mais
elle n'est pas la seule — cette section l'a longtemps présentée comme telle.
Attention, un `rollout restart` ne suffit pas : les variables d'environnement
sont figées dans le StatefulSet.

**Piège vérifié** : le jeton S3 d'un pod Jupyter expire lui aussi au bout de
7 jours à compter de la **création du service**, pas du démarrage du pod. Un
service Jupyter vieux de plusieurs semaines injectera donc des accès déjà
morts, et `install.sh` affichera quand même « Installation terminée ».
Constaté le 2026-09-04 : pod démarré depuis 29 h, jeton expiré depuis 5 jours.

## 3. Redéployer un pod

### 3.1 Pod hub principal
```bash
kubectl -n user-<u> delete pod qgis-hub-0
# imagePullPolicy: Always -> le pod recréé tire `qgis-hub:latest`
```

> Corrigé le 2026-09-04. Cette section indiquait
> `delete pod jupyter-python-<hash>-0`, l'architecture d'avant le Sprint
> Day 5 où le hub tournait dans le pod Jupyter de l'utilisateur. Aujourd'hui
> le hub a son propre StatefulSet, et cette commande détruirait le service
> Jupyter de l'utilisateur en croyant redémarrer le hub.

### 3.2 Pod bridge (proxy MCP secondaire) — **legacy**
```bash
kubectl -n user-<u> delete pod qgis-mcp-bridge-jupyter-python-0
```

> `qgis-mcp-bridge` sert sa propre copie du hub depuis `/opt/qgis-hub` et
> n'appartient pas à la release Helm : le redémarrer ne le met **pas** à jour,
> son `PERSONAL_INIT_SCRIPT` (`server_init.sh`) renvoyant 404 depuis le dépôt.
> C'est le bug D5 de `docs/STRUCTURE_ET_PROCESS.md`, et la décision Q2
> verrouille « hub seul sert /desk et /workspace ». À retirer, pas à
> maintenir.

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

> Corrigé le 2026-09-04. Cette section visait un pod Jupyter et le chemin
> `/data/hub.db`. Les trois étaient faux : mauvais pod, mauvais chemin, et
> `hub.db` n'existe pas. Suivre la procédure produisait une sauvegarde vide
> sans rien signaler. Chemins ci-dessous relevés dans les pods.

### 4.1 SQLite hub — pod `qgis-hub-0`, `$DATA_DIR`

`DATA_DIR=/home/onyxia/work/qgis-hub-data`, sur le PVC `qgis-hub`. Trois
bases, pas une : `studies.db`, `apikeys.db`, `sessions.db` (~260 Ko au total).

```bash
for f in studies.db apikeys.db sessions.db; do
    kubectl -n user-<u> cp "qgis-hub-0:/home/onyxia/work/qgis-hub-data/$f" \
        "backup-$f-$(date +%F).db"
done
```

### 4.2 Données études — pod `qgis-workspace-<u>-0`, `/data/studies`

Les études ne sont pas dans le hub mais dans le workspace (168 Mo sur
l'instance de référence) :

```bash
mkdir -p ./backup-studies-$(date +%F)
kubectl -n user-<u> exec qgis-workspace-<u>-0 -- tar czf - /data/studies \
    | tar xzf - -C ./backup-studies-$(date +%F)/
```

Vérifier que la sauvegarde n'est pas vide — c'est ce qui manquait :

```bash
du -sh ./backup-studies-$(date +%F)
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
kubectl -n user-<u> logs qgis-hub-0 --tail=50
# Chercher exceptions au startup (init_db, load models)
```

### 5.2 Publish échoue en 401
Depuis Day 3.1c (commit `a1e10de`), `/publish` est dans whitelist
inter-pod. Vérifier que l'image déployée contient ce fix :
```bash
kubectl -n user-<u> exec qgis-hub-0 -- python -c \
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
kubectl -n user-<u> logs qgis-hub-0 | grep "patch ingress"
kubectl -n user-<u> annotate ingress qgis-hub \
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
kubectl -n user-<u> logs qgis-hub-0 --tail=100 -f | grep -E "WARNING|ERROR"
```

Motifs anormaux :
- `ensure_active_study: switch X -> Y echec` répétés (déchec save/reload)
- `PROJECT_ALREADY_LOADED` très fréquent = skip re-read OK (normal)
- `STUDY_SAVE_SKIP n_layers=0` = save vide sur projet vide (normal)
- `gc_loop: purge N entrees` toutes les heures = GC session state (normal)
- `Cette clé n'appartient pas à ce pod` = auth cross-user rejetée (bien)

## 7. Procédures de maintenance planifiée

### 7.1 Mise à jour des images hub et agent

1. Merger PR sur `main` → CI GitHub Actions build `qgis-hub:latest` **et**
   `qgis-agent:latest` (jobs `build-hub` et `build-agent`, tous deux
   conditionnés au job `tests`)
2. Attendre CI green (~8 min)
3. Redéployer les pods concernés (voir §3)
4. Vérifier `/version` retourne le nouveau commit
5. Smoke test : `curl /diagnostics/isolation`, un `study_list` via MCP

Côté utilisateur, une seule commande couvre installation et mise à jour —
c'est ce que documente [QUICKSTART.md](QUICKSTART.md) :

```bash
curl -fsSL https://raw.githubusercontent.com/nic01asFr/Qgis-sspcloud/main/install.sh | bash
```

Elle rattache d'abord à la release les ressources qui existent sans
propriétaire (instance installée à la main, ou par un chart antérieur), sinon
`helm upgrade` refuse de les adopter et échoue en entier.

### 7.1b Mise à jour de l'image workspace — **manuelle**

La CI ne construit **pas** `qgisremotemcp` : le job `build-workspace` est
commenté dans `build.yml` (image QGIS Desktop complète, ~30 min). L'image que
`install.sh` tire est donc le dernier `latest` poussé à la main, sans que rien
n'indique quand.

```bash
cd ../BigQgisMCP
docker build -t ghcr.io/nic01asfr/qgisremotemcp:latest .
docker push ghcr.io/nic01asfr/qgisremotemcp:latest
```

Forcer le re-pull sur une instance en place, sans passer par helm :

```bash
KEY=$(kubectl -n user-<u> get secret qgis-hub-apikey \
      -o jsonpath='{.data.HUB_API_KEY}' | base64 -d)
curl -X POST -H "Authorization: Bearer $KEY" \
  https://user-<u>-qgis.user.lab.sspcloud.fr/admin/workspace-fix-image
```

⚠ Le pod workspace est recréé : le projet QGIS en RAM est perdu (il reste
sauvegardé dans le `.qgz`), cf. §3.4.

### 7.2 Nettoyage sessions MCP orphelines

Les entries `session_active_state` expirent après 24h + GC 1h. Manuel :
```bash
kubectl -n user-<u> exec qgis-hub-0 -- python -c \
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
