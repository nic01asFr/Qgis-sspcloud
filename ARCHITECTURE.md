# ARCHITECTURE — qgis-sspcloud

Vue d'ensemble technique du service qgis-sspcloud : composants, flux, isolation,
contrat d'orchestration études/projets/sessions.

Version 2026-08-22 · chart 1.3.0.

---

## 1. Composants du service

**Sprint Day 5 (chart 1.3.0)** : le portail admin `nic01asfr` est retiré.
Le service se compose de **3 pods** déployés via `helm install qgis-hub`
depuis le terminal Jupyter Onyxia de l'user (namespace `user-<username>`) :

| Pod | Rôle | Image | Ingress public |
|---|---|---|---|
| `qgis-hub-0` | API REST FastAPI, orchestrateur central, proxy /agent same-origin | `ghcr.io/nic01asfr/qgis-hub:latest` | `user-<u>-qgis.user.lab.sspcloud.fr` |
| `qgis-agent-0` | LLM tool-runner, chat SSE, tool-calls, mémoire | `ghcr.io/nic01asfr/qgis-agent:latest` | `user-<u>-qgis-agent.user.lab.sspcloud.fr` |
| `qgis-workspace-<u>-0` | QGIS Desktop noVNC + BigQgisMCP tools | `ghcr.io/nic01asfr/qgisremotemcp:latest` | ingress interne |

**Coordination** : le hub est le point d'entrée unique. Il proxy les
requêtes vers le workspace (BigQgisMCP) et l'agent (via /agent proxy
same-origin Phase 1.7-B). Le workspace tourne sur un PVC `ReadWriteOnce`
→ une seule instance QGIS par user.

**Authentification** (Phase 2-1) : un seul credential `HUB_API_KEY`
(Secret K8s `qgis-hub-apikey` chart-managed, source unique). Le user
tape sa clé dans le form `/login` (POST body, zero exposition URL).
Cookie `hub_api_key` httponly 90j auto-set après validation. Ownership
check contre `ONYXIA_USER` défense in depth (Day 4.1).

---

## 2. Distinction hub vs workspace

Confusion fréquente qui est source de bugs — **retiens** :

### Hub (`qgis-hub`)
- **FastAPI Python 3.11**, port 8888
- Stocke les **études, projets, publications** en DB (SQLite + PVC)
- Sert le **desk web** (Jinja2 templates DSFR)
- Expose l'API `/mcp` que les clients MCP externes appellent (Claude
  Desktop, claude.ai, Cursor, Cline)
- Orchestre : `/studies`, `/projects`, `/publish`, `/desk`, ...
- Middleware OIDC : whitelist inter-pod (Bearer HUB_API_KEY) + cookie
  session UI (auth via portail)

### Workspace (`qgisremotemcp`)
- **QGIS Desktop** dans un container avec noVNC + BigQgisMCP FastMCP
  server
- Un **process QGIS unique** avec `QgsProject.instance()` singleton →
  1 seul projet .qgz ouvert en RAM à la fois
- Tools MCP : `add_layer`, `execute_python`, `smart_load`,
  `set_study_zone`, `export_web_map`, `export_pdf`, ... (~46 tools)
- Ces tools opèrent sur la RAM QGIS, pas sur le disque directement
- Le hub commande le workspace via `POST /execute` (Python code injecté)

### Distinction clé

| Concept | Hub | Workspace |
|---|---|---|
| Étude | Row DB `studies` | Répertoire `/data/studies/{sid}/` |
| Projet | Row DB `study_projects` | Fichier `.qgz` en RAM QGIS |
| Layer | (indexé metadata) | Objet `QgsMapLayer` en RAM |
| Livrable | Row DB `publications` + S3 | Fichier `/data/exports/...` |
| Session MCP | `session_active_state[mcp_sid]` | (aucun) |
| Session desk | Cookie OIDC | (aucun) |

**Ne pas confondre** :
- `study_switch` (hub tool) : DB + activation pod
- `new_project` (workspace tool) : nouveau .qgz vide en RAM
- Ils ne font PAS la même chose. Voir §5 pour le mapping.

---

## 3. Isolation études / projets / sessions

Depuis Sprint isolation Day 1+2+3+3.1 (2026-07-30 → 08-02).

### 3.1 Modèle de données

```
User (nic01asfr, nicolaslaval, ...)
├── DB.active_study (1 par user, table sessions)
├── DB.active_project (1 par user, table user_active_project)
└── session_active_state[mcp_session_id] (N par user, memoire hub)
    ├── mcp_sid_A: (sid=X, pid=P1, expires_at)
    ├── mcp_sid_B: (sid=Y, pid=P2, expires_at)
    └── ...

Study (sid, 12 hex)
├── DB.studies (name, profile, status, last_active)
├── /data/studies/{sid}/project.qgz  (legacy, bundle portable)
├── /data/studies/{sid}/data/         (couches adoptees)
├── /data/studies/{sid}/exports/      (livrables)
└── projects/                         (Sprint UX-3 2026-06-21)
    ├── {default_pid}/project.qgz     (Projet principal)
    └── {other_pid}/project.qgz       (Analyses secondaires)
```

### 3.2 Priorité de résolution `active_sid` / `active_pid`

Fonction : `hub.main.resolve_effective_active_sid(username, mcp_session_id, x_session_id)`

1. **`session_active_state[mcp_session_id]`** (Day 3, spec MCP 2024-11-05
   header `Mcp-Session-Id`) — chaque connexion MCP externe a son propre
   contexte, indépendant des autres connexions du même user
2. **`_extract_expected_sid(x_session_id)`** (Sprint A1 legacy) —
   agents internes (chat desk, editor, recipe worker) qui envoient un
   session_id structuré via `X-Session-Id` (patterns `study:{sid}`,
   `agent:{aid}:sid:{sid}`, `assist:{sid}:...`)
3. **`studies.get_active_study_id(username)`** — fallback DB user pour
   l'UI desk et les sessions non-instrumentées (comportement historique
   Day 2)

### 3.3 Design piste 1 (session-scoped MCP)

Depuis Day 3.1 :
- Une session MCP externe **NE mute PAS la DB user** (aucun `set_active_study`)
- Elle mute uniquement `session_active_state[mcp_session_id]`
- Le desk continue de lire DB.active → **divergence volontaire** entre
  desk et clients MCP externes
- Le badge desk `MCP: <study_name>` (Day 3.1c) signale cette divergence
  au user via l'endpoint `/diagnostics/mcp-sessions`

**Conséquence** : 2 sessions Claude Desktop du même user (même connecteur)
peuvent bosser en parallèle sur 2 études différentes sans se marcher
dessus. Le desk reste sur l'étude par défaut du user.

### 3.4 Contrainte QgsProject singleton

Le workspace = 1 process QGIS = 1 projet .qgz en RAM à un instant t.
Quand une session MCP demande un `switch`, le hub :
1. Save prev projet (dual-write legacy + pid-scope, Day 1 Fix #1)
2. `activate_project_pod_code` (charge le .qgz cible en RAM)

Ce switch physique prend ~1-2s (I/O disk). Le lock
`_active_study_switch_locks[username]` sérialise les switches concurrents
pour éviter les races sur les sentinels PVC (Day 1 Fix #3).

Si 2 sessions MCP demandent des switches rapides, elles sont sérialisées
proprement. Latence acceptable en pratique (les switches sont rares).

---

## 4. Flux typiques

### 4.1 Client MCP externe (Claude Desktop) crée une étude et charge des données

```
Client MCP (Mcp-Session-Id=abc123)
  ↓ POST /mcp {"method":"tools/call","params":{"name":"study_create","arguments":{"name":"..."}}}
Hub /mcp_auto_session
  ↓ resolve_effective_active_sid(user, abc123, None) → session_active_state[abc123] = (None,None) OR fallback DB
  ↓ dispatch_hub_tool("study_create", args, user, mcp_session_id=abc123)
  ↓ studies.create_study + create_project(default)
  ↓ session_active_state.set_active(abc123, new_sid, default_pid, username=user)
  ↓ execute_python_in_workspace(activate_pod_code + activate_project_pod_code)
Workspace QGIS RAM
  ↓ Charge /data/studies/{new_sid}/projects/{default_pid}/project.qgz (vide)

Client MCP
  ↓ POST /mcp {"method":"tools/call","params":{"name":"smart_load","arguments":{"id":"bdtopo_batiments","bbox":[...]}}}
Hub /mcp_auto_session
  ↓ resolve_effective → session_active_state[abc123] = (new_sid, default_pid)
  ↓ _ensure_active_study_for_agent(user, new_sid, expected_pid=default_pid, mcp_session_id=abc123)
    → active_actual = session_state.get(abc123)[0] = new_sid → PAS de switch (fast-path)
  ↓ Forward /mcp au workspace → smart_load 300 features
Workspace QGIS RAM
  ↓ 300 layers ajoutés en RAM (pas encore save)
```

### 4.2 Divergence session MCP vs desk

```
User a 2 conversations Claude Desktop :
  - Session A (mcp_sid_A) : study_create("PCRS Sorgues") → sidA
  - Session B (mcp_sid_B) : study_switch(sidB) → sidB (autre étude)

DB.active_study reste sur l'ancienne étude X (aucune mutation par MCP)
Desk affiche : "ÉTUDE : X"
Badge Day 3.1c affiche : "MCP: PCRS Sorgues, [nom de sidB]" (divergence)

Chaque session MCP a son propre contexte, le desk garde sa vue globale.
```

### 4.3 UI desk switch projet

```
User clique "Analyse Marseille 4e" dans dropdown desk
  ↓ POST /workspace/project/{sid}/{pid}/activate
Hub activate_project_endpoint
  ↓ save prev (via save_active_project_pod_code(sid, DB.active_pid))
  ↓ set_active_project(user, pid) (mute DB)
  ↓ activate_project_pod_code(sid, pid) (charge .qgz cible en RAM)
Workspace QGIS RAM
  ↓ Reload .qgz cible

Note Day 3.1 : ce chemin est desk = mute DB. Les sessions MCP
session-scoped ne sont PAS impactees (elles ont leur propre state).
```

---

## 5. Mapping tools (hub natifs vs workspace)

| Concept | Hub-tools (namespace `study_*`) | Workspace-tools (BigQgisMCP) |
|---|---|---|
| Créer étude | `study_create(name)` | — |
| Lister études | `study_list()` | — |
| Basculer étude | `study_switch(sid)` | — |
| Créer projet dans étude | `study_project_create(label, sid?)` | — |
| Lister projets étude | `study_project_list(sid?)` | — |
| Basculer projet | `study_project_switch(pid)` | — |
| Nouveau .qgz vide (workspace) | — | `new_project` |
| Ouvrir un .qgz | — | `open_project(path)` |
| Sauver le .qgz | — | `save_project` |
| Ajouter une couche | — | `add_layer(uri)` |
| Charger data catalog | — | `smart_load(id, bbox?)` |
| Exécuter PyQGIS | — | `execute_python(code)` |
| Exporter | — | `export_web_map`, `export_pdf`, `export_layer` |
| Publier livrable | — | `publish_artifact(kind, slug)` (appelle hub `/publish/{kind}/{slug}`) |

Les hub-tools gèrent la couche **DB + orchestration**. Les workspace-tools
manipulent le **fichier .qgz en RAM QGIS**. Un `study_switch` bascule à la
fois DB (si non session-scoped) ET le fichier RAM.

---

## 6. Auth et sécurité

### 6.1 Modes d'auth (Day 4 UX Auth Persistante)

Depuis Sprint UX Day 4 (2026-08-02), le user a **une seule fois** besoin
du portail OIDC (bootstrap), ensuite un cookie persistant `hub_api_key`
(90j) le maintient authentifié.

Priorité d'auth pour routes UI :
1. **Cookie `hub_api_key`** (Day 4, TTL 90j HttpOnly Secure) : cookie
   navigateur stable, auto-set après validation OIDC réussie. Le user
   n'a plus à repasser par le portail à chaque expiration cookie OIDC.
2. **Cookie `oidc_token`** (fallback, TTL courte Keycloak) : utilisé
   pour bootstrap initial ou si le cookie `hub_api_key` est absent (ex :
   perte cache navigateur).

Priorité d'auth pour routes inter-pod et API :
3. **Bearer HUB_API_KEY** (inter-pod, agents internes) : token stable
   par user, source de vérité = Secret K8s `qgis-hub-apikey`. Utilisé
   par les self-calls hub→hub, les agents internes, et les clients MCP
   externes (Claude Desktop, claude.ai) via le connecteur.
4. **Bearer scope key** (agents publiés futurs, actuellement inerte) :
   token scopé qui autorise un subset des tools MCP.

Endpoint `/auth/apikey` : retourne la clé API personnelle stable du
user (idempotent, à copier dans `claude_desktop_config.json`).

Endpoint `/login?key=qgis_...` : pose manuellement le cookie
`hub_api_key` (usage : perte cookie, changement navigateur).

### 6.2 Middleware OIDC

`auth.py::oidc_auth_middleware` (ordre des étapes) :
- Routes publiques (`/version`, `/healthz`, `/probe`, `/published/*`)
- Whitelist inter-pod (Bearer HUB_API_KEY) : `/mcp`, `/studies`,
  `/projects`, `/admin`, `/api/recipes-web`, `/briques`, `/diagnostics`,
  `/agent-context`, `/publish` (Day 3.1c), `/internal`, `/schema`
- Kube-probe court-circuit (guard : sans X-Forwarded-For = interne)
- Fallback : cookie OIDC obligatoire, sinon 302 vers portail

### 6.3 Isolation cross-user (SSPCloud namespace)

Chaque user a son propre namespace K8s → RBAC K8s empêche `nic01asfr`
d'accéder aux pods `nicolaslaval` via kubectl.

**Isolation cross-tenant HTTP hub (Sprint securite Day 4.1, 2026-08-03)** :

Une tentative d'accès cross-tenant (ex : user nic01asfr avec cookie
`hub_api_key=qgis_nic01asfr_...` navigue vers pod nicolaslaval) est
bloquée à 2 niveaux :

1. **Authentification (Secret K8s pod-scoped)** : `_validate_api_key`
   compare la clé contre le Secret K8s `qgis-hub-apikey` du **namespace
   courant** (nicolaslaval). La clé nic01asfr ne matche pas → retour
   `None` → middleware tombe sur fallback OIDC → 302 redirect portail.
2. **Autorisation (défense en profondeur Day 4.1)** : si un jour
   `_validate_api_key` retournait un user cross-namespace (via DB legacy
   ou régression future), le middleware compare ensuite
   `user_from_cookie.username` vs `os.environ.ONYXIA_USER` → 403 explicite
   avec message identique au chemin OIDC étape 5.

Le chemin OIDC (étape 5) fait aussi ce check `claimed_user != onyxia_user`
→ 403. Les 2 chemins d'auth sont désormais alignés pour l'ownership check.

Vérification empirique 2026-08-03 : `curl -H "Cookie: hub_api_key=$NIC01_KEY"
https://user-nicolaslaval-qgis.user.lab.sspcloud.fr/desk` → HTTP 302
portail (bloqué au niveau 1). Le test HTTP 200 sur le pod légitime
nic01asfr confirme aucune régression sur le cas nominal.

---

## 7. Observability

### Endpoint `/diagnostics/isolation`
Superviseur (Bearer HUB_API_KEY). Retourne :
- `switches_total_since_boot` : compteur global switches physiques pod
- `recent_switches` : 200 derniers switches (ts, from_sid, to_sid, latency_ms, source)
- `active_locks` : nombre de locks actifs
- `session_active_state.total_entries` / `active_entries`
- `helpers.day3_priority` : documentation résolution

### Endpoint `/diagnostics/mcp-sessions` (Day 3.1c)
Par user. Retourne les sessions MCP session-scoped actives + flag
`diverges_from_db`. Utilisé par le badge desk.

### Metrics futurs (backlog)
- Prometheus scrape
- Grafana dashboard latence switches / count sessions

---

## 8. Fichiers de référence

- Sprint isolation Day 1 : commit `18f73a1` — dual-write pid-scope +
  save intra-etude + lock A2 étendu
- Sprint isolation Day 2 : commit `8b6b427` — 6 hub-tools `study_*` +
  badge desk Fix #6
- Sprint isolation Day 3 : commit `dba6c97` — session_active_state +
  resolve_effective_active_sid
- Sprint isolation Day 3.1 : commit `95a6ad9` + `98b0964` — vrai
  session-scoped (no DB mutation) + fix faux positif switch
- Sprint isolation Day 3.1c : commit `a1e10de` — fix publish 401 +
  badge UI desk MCP divergence

SPEC design : [docs/spec-day3-session-scoped-active-study.md](docs/spec-day3-session-scoped-active-study.md)
