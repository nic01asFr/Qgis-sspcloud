# SPEC — Day 5 plan complet (Phase 1.7 + Phase 2 + Phase 3 + Backlog)

Sprint Day 5 = suppression portail nic01asfr au profit d'un chart Helm autonome.
Auteur : Claude Opus 4.7 · 2026-08-05.
Statut : cadrage exhaustif consolidé pour execution rigoureuse.

---

## Contexte

**Situation actuelle** :
- Phase 1 Day 5 livrée (endpoints `/onboarding`, `/auth/token-login`, `/login-password`,
  chart Helm 1.1.3 déployé sur nicolaslaval, cookie hub_api_key 90j Day 4)
- Portail nic01asfr encore en place (repo Passerelle, pod séparé, orchestre bootstrap
  + config clé LLM + push agent-config)
- Audit user naïf : verdict AMBIGU (10 problèmes UX/code identifiés)
- Audit E2E flow user : **4 blocages P0/P1 bloquants** identifiés APRÈS Phase 1 :
  - P0-A : RBAC racine cassé (SA `qgis-hub` sans RoleBinding)
  - P0-B : Iframe agent inaccessible sans cookie OIDC cross-subdomain
  - P0-C : Commande `helm install` non documentée
  - P1-D : Clé LLM sans flow UI depuis suppression portail

**Vision cible verrouillée** :
- 1 seul credential = HUB_API_KEY (Secret K8s chart-managed)
- Récupérable via terminal Onyxia authentifié OIDC (`kubectl get secret ... | base64 -d`)
- 2 canaux d'usage SANS jamais exposer la clé en URL :
  - Auth web : POST `/login` (form), cookie hub_api_key 90j
  - API MCP externe : header `Authorization: Bearer` (Claude Desktop)
- Zéro pod admin, pas de portail nic01asfr
- Chaque user CEREMA autonome (comme n8n dans le catalogue Onyxia)

---

## Vue d'ensemble des phases

| Phase | Objectif | Effort | Bloquant ? |
|---|---|---|---|
| **1.7 STABILISATION** | Fix 4 blocages P0/P1 découverts E2E | ~10h | OUI — sans, flow user cassé |
| **2 SIMPLIFICATION** | Refactor auth (1 endpoint /login) + suppression doublons | ~6.5h | Non, mais indispensable UX |
| **3 FINALISATION** | Suppression portail + migration users + PR catalogue Onyxia | ~6h | Non, dépend Phase 1.7 + 2 |
| **Backlog** | Nice-to-have (rate limit, mutualisation, GPU, partage) | Ouvert | Non |

**Ordre d'exécution obligatoire** : 1.7 → 2 → 3 (chaque phase dépend de la précédente).

---

## Phase 1.7 — STABILISATION (bloquants P0/P1)

### Commit 1.7-A — Fix RBAC hub via secretKeyRef

**Préconditions** : chart 1.1.3 déployé, Secret K8s `qgis-hub-apikey` existe.

**Problème identifié (audit E2E §5.5)** :
- SA `qgis-hub` (statefulset.yaml:18) n'a plus de RoleBinding (Phase 1.5 avait vidé
  `serviceaccount.yaml` pour éviter 403 install user)
- `create_or_get_api_key` (hub/hub/auth.py:300) fait `_k8s_get_secret_value` qui échoue 403
- Fallback DB legacy → clé DB diverge de clé Secret K8s
- Cascade : agent envoie clé Secret en Bearer, hub valide contre DB legacy → 401 inter-pod

**Solution** :
- Basculer hub sur pattern secretKeyRef comme agent + workspace (source unique = env var)
- Le hub lit `HUB_API_KEY` env, plus jamais K8s API pour cette clé
- Suppression `_k8s_get_secret_value` du path critique `_validate_api_key`

**Fichiers touchés** :
- `charts/qgis-hub/templates/statefulset.yaml` : ajouter env var
  ```yaml
  - name: HUB_API_KEY
    valueFrom:
      secretKeyRef:
        name: qgis-hub-apikey
        key: HUB_API_KEY
  ```
- `hub/hub/auth.py::_validate_api_key` : ajouter fast-path env var
  ```python
  env_key = os.environ.get("HUB_API_KEY", "")
  if env_key and key == env_key:
      username = os.environ.get("ONYXIA_USER", _NAMESPACE.removeprefix("user-"))
      return {"username": username, "role": ..., "source": "env"}
  # ... fallback existant Secret + DB legacy
  ```
- `hub/hub/auth.py::create_or_get_api_key` : idem, fast-path env var en 1er

**Validations** :
- `kubectl exec qgis-hub-0 -- printenv HUB_API_KEY` retourne la clé Secret
- `curl /workspace` avec cookie affiche cette même clé (pas divergence)
- Test E2E inter-pod : agent tools MCP → hub 200 (pas 401)

**Rollback** : revert commit, chart 1.1.3 fonctionne (mais garde le bug)

**Effort** : 2h

---

### Commit 1.7-B — Proxy /agent same-origin

**Préconditions** : Commit 1.7-A validé (clé cohérente pour test E2E).

**Problème identifié (audit E2E §4)** :
- `POST /login-password` pose UNIQUEMENT cookie `hub_api_key` (subdomain hub)
- Iframe agent est sur subdomain différent (`qgis-agent.user.lab.sspcloud.fr`)
- Middleware OIDC agent exige cookie `oidc_token` cross-subdomain (Domain=. seulement posé par `/onboarding` ou portail)
- Résultat : iframe `<iframe src="{{agent_url}}/?embed=1">` → 302 vers /onboarding, user ne peut PAS chatter

**Solution architecturale** : le hub proxifie l'agent (comme il proxy déjà `/workspace/vnc/*`).
- Endpoint hub `/agent/{path:path}` (HTTP + SSE stream) forwarde vers
  `qgis-agent.<ns>.svc.cluster.local:8888/{path}`
- Utilise le pattern `_proxy_request` existant (main.py:10982) qui supporte déjà
  `httpx AsyncClient stream=True` + `StreamingResponse aiter_bytes` (SSE natif)
- L'agent middleware OIDC devient inutile pour les requêtes UI (venues du hub proxy
  avec header inter-pod X-Hub-Auth). On garde le middleware pour les appels externes
  directs (backup, path `qgis-agent.user.lab.sspcloud.fr` reste dispo).

**Fichiers touchés** :
- `hub/hub/main.py` : nouveau endpoint `@app.api_route("/agent/{path:path}", ...)` calqué
  sur `_proxy_request`, cible `_AGENT_INTERNAL_URL = "http://qgis-agent.{ns}.svc.cluster.local:8888"`
- `hub/hub/main.py::_desk_context` : `agent_url` devient path relatif `/agent/` (au lieu
  de `_AGENT_URL` full URL)
- `hub/templates/desk.html` : `<iframe src="{{ agent_url }}/?embed=1">` reste inchangé
  (grâce au ctx.agent_url = "/agent/")
- `agent/agent/main.py::agent_oidc_middleware` : ajouter early-return si User-Agent
  contient marker hub proxy OU header `X-Hub-Proxy: true` injecté par le proxy hub
- `hub/hub/auth.py::_OIDC_MIDDLEWARE_PUBLIC` : ajouter `/agent/` (public car proxy
  interne, l'auth user se fait via cookie hub_api_key qui reste sur origin hub)
- Chart `agent-ingress.yaml` : garder mais rendre optionnel (`values.agent.ingress.enabled`
  peut passer à false, l'iframe passera par proxy hub)

**Test SSE** : depuis Chrome, chat agent envoie message → stream `text/event-stream`
via `/agent/api/chat` → doit arriver chunk par chunk sans buffering.

**Validations** :
- Sans cookie oidc_token, iframe `<iframe src="/agent/?embed=1">` → HTTP 200 (pas 302)
- Chat agent fonctionnel (SSE stream OK)
- Cookie hub_api_key seul suffit

**Rollback** : revert commit + les iframes retomberont sur ancien path `_AGENT_URL`
(mais toujours cassé sans cookie oidc_token)

**Effort** : 4h

---

### Commit 1.7-C — Endpoint UI clé LLM

**Préconditions** : Commits 1.7-A et 1.7-B validés.

**Problème identifié (audit E2E §5)** :
- Aujourd'hui : portail lit OWUI/Vault + push clé LLM à l'agent via `/admin/agent-config`
- Sans portail : `helm upgrade --set llm.apiKey=...` (CLI, non documenté)
- Aucun endpoint UI simple pour "saisir ma clé LLM"

**Solution** : endpoint hub UI `/workspace/llm-key` + form POST + patch env agent
via mécanisme existant (endpoint `/api/reload-llm-key` webhook agent, déjà Day 4).

**Fichiers touchés** :
- `hub/hub/main.py` : nouveau endpoint `GET /workspace/llm-key` (form HTML) +
  `POST /workspace/llm-key` qui valide clé + patch env agent + trigger webhook
- `hub/templates/workspace.html` : nouveau bloc "🤖 Configurer ma clé LLM" (details
  pliable, comme le bloc "🔑 Ma clé personnelle" Day 4.3a)
- `agent/agent/main.py::/api/reload-llm-key` : existe déjà (Day 4), à réutiliser
- Doc : lien "Où récupérer ma clé LLM" → `https://datalab.sspcloud.fr/account/AI-Assistant`
  (ou `llm.lab.sspcloud.fr/api-keys` selon flow SSPCloud)

**Alternative** : formulaire au moment du `helm install` via values Onyxia UI
(le user a déjà `--set llm.apiKey=...`). Endpoint UI = confort en cas de rotation.

**Validations** :
- Saisir clé dans `/workspace/llm-key` form → chat agent devient fonctionnel
- Sans clé LLM → bandeau chat "clé LLM manquante" avec CTA vers ce form

**Effort** : 2h

---

### Commit 1.7-D — Doc install CLI + values minimum

**Préconditions** : Aucune (peut être fait en parallèle de A/B/C).

**Problème identifié (audit E2E §1)** :
- Aucun `.md` ne mentionne `helm install qgis-hub https://.../qgis-hub-1.1.3.tgz --set ...`
- Auto-fill `x-onyxia.overwriteDefaultWith` ne s'applique que via Onyxia UI, pas CLI
- User CLI a defaults `""` partout → workspace, login, LLM cassés

**Solution** : documenter la commande CLI complète + workflow "3 étapes après install".

**Fichiers touchés** :
- `README.md` : nouvelle section "Installation CLI" avec commande complète et
  5 values minimum obligatoires
- `charts/qgis-hub/templates/NOTES.txt` : ajouter warning si `oidc.username` ou
  `ingress.hostname` vides (le user a oublié de les passer)
- `docs/QUICKSTART.md` : nouveau, flow user en 5 commandes terminal
- `USER_GUIDE.md` section 6 : renseigner clairement où récupérer clé LLM
- Prérequis Onyxia : `kubernetes.role: edit` avec capture screen

**Validations** :
- User naïf peut copier-coller la commande README et ça marche
- NOTES.txt affiche warning explicite si values incomplètes

**Effort** : 1.5h

---

### Récapitulatif Phase 1.7

| Commit | Effort | Ordre |
|---|---|---|
| 1.7-A Fix RBAC hub | 2h | 1er (base pour B, C) |
| 1.7-B Proxy /agent | 4h | 2e (débloque iframe) |
| 1.7-C Endpoint UI LLM key | 2h | 3e (nice UX après B) |
| 1.7-D Doc install CLI | 1.5h | Parallélisable (avant Phase 2) |
| **Total Phase 1.7** | **~10h** | |

Après Phase 1.7 : flow user complet FONCTIONNEL sans portail (install + login +
chat agent + config LLM + publish). C'est la base solide pour Phase 2 simplification.

---

## Phase 2 — SIMPLIFICATION (rationalisation code UX)

Cadrée précédemment via subagent exhaustif. 5 commits atomiques.

### Commit 2-1 — B.1 + B.2 fusion (contrainte FastAPI 2 handlers /login)

- Rename `/login-password` (GET + POST) → `/login` (GET + POST)
- Supprimer ancien `GET /login?key=` (magic URL Day 4.2)
- Supprimer `hub_login_magic_url` du context desk (main.py:1284, 1291)
- Supprimer bloc "🪄 Lien magique bookmarquable" dans workspace.html (~57 lignes)
- Supprimer `values.security.password` + `SECURITY_PASSWORD` env chart
- `NOTES.txt` refonte (1 section "Récupérer HUB_API_KEY" + form /login)
- Whitelist middleware nettoyée
- Tests unit ajoutés (4 tests /login form)

**Effort** : 2h

### Commit 2-2 — B.3 cleanup PORTAL_URL

- Renommer `_portal_login_redirect_url` → `_hub_login_redirect_url` (hub + agent)
- Supprimer fallback env `PORTAL_URL` partout
- Supprimer branche JS `PORTAL_URL` popup chat.html
- Retirer injection `portal_url` dans template chat + `/admin/agent-config` body
- Tests unit ajoutés (4 tests)

**Effort** : 1.5h

### Commit 2-3 — Chart bump 1.2.0

- Chart.yaml 1.1.3 → 1.2.0
- `helm package` + regenerate `helm-repo/index.yaml`
- Test `helm upgrade qgis-hub 1.1.3 → 1.2.0` sur nicolaslaval
- Valider POST /login form marche avec HUB_API_KEY

**Effort** : 30 min

### Commit 2-4 — Doc refonte

- `ARCHITECTURE.md:251-252` : section auth `/login`
- `USER_GUIDE.md:38` : phrase récup clé via kubectl
- `docs/ARCHITECTURE_AGENT.md:508` : ligne endpoint
- `docs/STRUCTURE_ET_PROCESS.md:55` : tableau ligne `/login`
- `ONBOARDING.md` → renommé `ADMIN-GUIDE.md` (audience corrigée)

**Effort** : 1.5h

### Commit 2-5 — QUICKSTART.md + README rewrite

- `QUICKSTART.md` : flow user en 5 commandes terminal
- `README.md` : glossaire + install unique + `kubernetes.role: edit` avec capture

**Effort** : 1h (partiellement fait Phase 1.7-D)

### Récapitulatif Phase 2

| Commit | Effort |
|---|---|
| 2-1 B.1+B.2 fusion | 2h |
| 2-2 B.3 PORTAL_URL cleanup | 1.5h |
| 2-3 Chart bump 1.2.0 | 30 min |
| 2-4 Doc refonte | 1.5h |
| 2-5 QUICKSTART + README | 1h |
| **Total Phase 2** | **~6.5h** |

---

## Phase 3 — FINALISATION (suppression portail + migration)

### Commit 3-A — Suppression portail nic01asfr

**Préconditions** : Phase 1.7 + Phase 2 déployés + validés E2E sur 2 users
(nic01asfr + nicolaslaval).

- `helm uninstall qgis-mcp-portal-bridge` côté user-nic01asfr
- Archive repo `Passerelle/examples/qgis-mcp-portal/` (README "obsolète, remplacé
  par chart qgis-hub Day 5")
- Supprimer `install.sh` racine du repo qgis-sspcloud (redondant avec chart)
- Docs annexes : retirer toutes mentions "portail nic01asfr" restantes

**Effort** : 2h

### Commit 3-B — Migration users existants

**Préconditions** : Commit 3-A prêt à déployer.

**Impact** : users existants (nic01asfr + nicolaslaval + tout autre user onboardé
via portail) doivent basculer sur nouveau flow.

- Script one-shot : pour chaque user connu, envoyer notification avec instructions :
  - "Va sur `datalab.sspcloud.fr > my-services > qgis-hub > Values` récupère ta clé"
  - "Ou tape dans terminal : `kubectl get secret qgis-hub-apikey ...`"
  - "Puis ouvre `https://user-<toi>-qgis.user.lab.sspcloud.fr/login` et colle ta clé"
- Cookie `oidc_token` legacy expire naturellement (~1h TTL Keycloak)
- Doc migration dans CHANGELOG.md ou release notes

**Effort** : 2h

### Commit 3-C — PR catalogue Onyxia officiel

**Objectif long terme** : chart apparaît dans `datalab.sspcloud.fr/catalog` comme
n8n, jupyter-python.

- Fork `github.com/InseeFrLab/helm-charts-interactive-services`
- Ajouter `qgis-hub/` dans catégorie approriée (probablement `ide` ou nouveau
  `geospatial`)
- PR + description + tests (le repo InseeFrLab a des tests CI helm-lint)
- **Délai review INSEE incertain : 1 semaine à 2 mois**

**Alternative pragma** : demander à admins SSPCloud d'ajouter `helm-repo/index.yaml`
comme catalogue custom avec `status: TEST` (1 ligne YAML dans Onyxia API values).

**Effort** : 4h (rédaction PR) + 1-8 semaines attente review

### Récapitulatif Phase 3

| Commit | Effort | Bloquant ? |
|---|---|---|
| 3-A Suppression portail | 2h | Non (une fois Phase 1.7+2 valides) |
| 3-B Migration users | 2h | Non |
| 3-C PR catalogue Onyxia | 4h + attente | Non (long terme) |
| **Total Phase 3** | **~8h + attente review** | |

---

## Phase 4 — BACKLOG (nice-to-have, non prioritaire)

### 4-A — Persistance state DB (Day 3 backlog)

Rendre `session_active_state` (Day 3) persistant en DB au lieu de mémoire process.
Survit aux restart pod. Effort ~3h.

### 4-B — refresh-sts-creds.sh (rotation STS MinIO 7j)

Script cron qui refresh env vars AWS_* du pod avant expiration 7 jours SSPCloud.
Documenté dans OPS.md §7. Effort ~2h.

### 4-C — Fix study_zone BigQgisMCP

Bug côté workspace (repo externe BigQgisMCP) : `set_study_zone` bbox perdue entre
2 calls MCP. Workaround actuel : passer bbox à chaque `smart_load`. Effort ~3h.

### 4-D — Rate limiting POST /login

Bruteforce peu réaliste sur HUB_API_KEY hex32 (haute entropie), mais defense en
profondeur. Effort ~1h.

### 4-E — Mutualisation partial jinja `_auth_form.html.j2`

`/authorize/confirm` (OAuth Claude Desktop), `/login` (form) et `/onboarding` (form)
ont 3 pages HTML similaires. Extraire un partial jinja. Effort ~2h.

### 4-F — GPU pod (SAM3 segmentation IA)

Actuellement bootstrap runtime via `_bootstrap_geoai_gpu`. Migrer en template chart
optional (`values.gpu.enabled`). Effort ~3h.

### 4-G — Partage cross-user (invitation projet)

Non requis par vision cible actuelle. Backlog produit. Effort ouvert.

---

## Règles de rigueur applicables à chaque commit

Pour chaque commit de Phase 1.7 / 2 / 3, obligatoire :

1. **Préconditions vérifiées** : les commits précédents sont mergés + validés E2E
2. **Test syntaxe** : `python -c "import ast; ast.parse(...)"` pour chaque .py touché
3. **Tests unit régression** : `pytest tests/` — au moins les 57/57 Sprint isolation
4. **Nouveaux tests** : chaque nouvelle route/fonction critique = 1+ test unit
5. **Commit atomique** : 1 concern par commit, message conventionnel + Co-Authored-By
6. **Push + CI wait** : attendre `Build & Push Docker images` success avant deploy
7. **Deploy test** : `helm upgrade` sur nicolaslaval + `kubectl delete pod` si nécessaire
8. **Validation E2E** : test empirique concret (curl / Chrome / kubectl exec)
9. **Doc update** : chaque changement d'endpoint / values / behavior = doc synchronisée
10. **Rollback documenté** : chaque commit doit être revert-able sans casser main

---

## Zones incohérentes découvertes hors périmètre (à traiter en Phase 3+ ou backlog)

1. **`/authorize/confirm` (OAuth Claude Desktop)** contient form saisie clé similaire
   à `/login` — doublon à mutualiser (backlog 4-E)
2. **`hub/hub/auth.py:775`** commentaire obsolète "restart hub perd la clé" (faux
   depuis Secret K8s chart-managed) — à nettoyer Phase 2-4 doc
3. **Chart labels** `app.kubernetes.io/managed-by=Helm` absent du `_helpers.tpl` —
   `kubectl get pods -l app.kubernetes.io/managed-by=Helm` dans NOTES.txt retourne
   rien. Fix simple dans Commit 1.7-A ou 2-3.
4. **`ONBOARDING.md §5`** contient encore le flow 3 étapes portail (obsolète après
   Phase 3-A) — à retirer Commit 2-4 doc refonte

---

## Ordre d'exécution consolidé (roadmap linéaire)

```
[PHASE 1.7 STABILISATION]
1.7-A Fix RBAC hub secretKeyRef (2h)          ← DEMARRER ICI
   ↓
1.7-B Proxy /agent same-origin (4h)
   ↓
1.7-C Endpoint UI clé LLM (2h)
   ↓
1.7-D Doc install CLI (1.5h)   (parallélisable)

[PHASE 2 SIMPLIFICATION]
2-1 B.1+B.2 fusion /login unique (2h)
   ↓
2-2 B.3 cleanup PORTAL_URL (1.5h)
   ↓
2-3 Chart bump 1.2.0 (30 min)
   ↓
2-4 Doc refonte (1.5h)
   ↓
2-5 QUICKSTART + README (1h)

[PHASE 3 FINALISATION]
3-A Suppression portail nic01asfr (2h)
   ↓
3-B Migration users existants (2h)
   ↓
3-C PR catalogue Onyxia (4h + attente review INSEE)

[BACKLOG]
4-A à 4-G : selon priorité produit
```

**Total effort actif** : ~24-26h répartis sur 3-4 jours plein-temps.
**Total wall-clock** : ~1 semaine si focus, + attente review PR catalogue.

---

## Statut du plan

- ✅ Cadré exhaustivement par 2 subagents indépendants (portail audit + Phase 2 cadrage)
- ✅ 4 blocages P0/P1 identifiés + solutions concrètes
- ✅ Vision cible verrouillée avec user (4 décisions Q1-Q4)
- ✅ Ordre d'exécution logique (dépendances explicites)
- ✅ Règles de rigueur applicables à chaque commit
- ⏳ En attente de validation user pour démarrer Commit 1.7-A

Signal GO/NO-GO attendu pour démarrer Phase 1.7.
