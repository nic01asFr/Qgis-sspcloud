# Isolation multi-agent — contexte étude/projet

Sprint isolation A1+A2+A3+B1 (2026-07-18).

## Problème

Le workspace QGIS Desktop est **mono-instance** : un seul processus QGIS
tourne sur display `:99` par pod, un seul `QgsProject.instance()` chargé,
un seul `active_study` sentinel côté PVC.

Conséquence directe avant ce sprint : si l'agent A switche vers `sid1`,
l'agent B qui envoie une action juste après opère aussi sur `sid1`, même
s'il pensait être sur `sid2`. Cette confusion est aujourd'hui invisible
mais réelle, et bloque tout usage multi-agent parallèle :

- deux Claude Desktop concurrents ;
- un chat interne + une recipe qui tournent en parallèle ;
- un agent externe (Cline, Continue) + un agent recipe hub.

## Modèle desk existant (rappel)

Comportement acté et intact :

- Ouvrir **nouvelle étude** → auto-crée `Projet principal` (is_default) → ouvre dessus.
- Ouvrir **étude existante** → charge le dernier `last_active` project
  (ORDER BY `is_default DESC, last_active DESC`).
- Switch d'étude via UI = autosave de la sortante + chained activate du default.

## Modèle MCP — nouveau contract

### A1 — extraction `expected_sid` depuis `session_id`

Chaque call MCP entrant peut porter un header `X-Session-Id` optionnel.
Le hub parse la convention Sprint V0.3 G1
([[reference_session_id_convention]]) pour extraire le `sid` attendu :

| Pattern session_id | sid extrait | Cas d'usage |
|---|---|---|
| `study:{sid}` | sid | Desk chat général |
| `study:{sid}:draft:{did}` | sid | Éditeur freeform |
| `study:{sid}:recipe:{rid}` | sid | Recipe run |
| `study:{sid}:recipe_edit:{slug}` | sid | Recipe create |
| `assist:{sid}:cid:{cid}` | sid | Assist component (BlockNote drawer) |
| `assist:{sid}:aid:{aid}` | sid | Assist assembly (V1.15 panel) |
| `agent:{agent_id}:sid:{sid}` | sid | **Nouveau B1** — contexte agent isolé |
| autre (UUID legacy, absent) | None | Fallback safe, aucun switch |

Si `expected_sid` diffère de l'`active_study` courant, le hub déclenche un
switch propre AVANT de forwarder le call au workspace :

1. Autosave de l'étude sortante (`save_active_pod_code`).
2. `set_active_study(expected_sid)` en DB.
3. `activate_pod_code` (sentinel + symlink + QgsProject.read).
4. Chained activate du default project de l'étude cible.

**Rétro-compat totale** : sans header `X-Session-Id`, aucun switch ne se
déclenche — comportement identique à l'existant.

### A2 — mutex sur switch physique

Un `defaultdict[str, asyncio.Lock]` par username sérialise UNIQUEMENT le
switch physique. Deux agents avec des `X-Session-Id` différents seront
servis en série propre au niveau du switch (~100-500 ms), aucun ne race
sur les sentinels PVC. Un call sans switch nécessaire (fast-path) ne
prend PAS le lock.

Deux users différents : locks distincts, aucune contention entre eux.

### A3 — instrumentation observability

Endpoint `GET /diagnostics/isolation` (réservé cles superviseur — les
clés scopées agent reçoivent 403) expose :

```json
{
  "switches_total_since_boot": 42,
  "recent_switches": [
    {"ts": "...", "username": "...", "from_sid": "...", "to_sid": "...",
     "latency_ms": 320, "source": "mcp"}
  ],
  "active_locks": {"active_study_switch": 1, "session": 3},
  "helpers": {"supported_session_id_patterns": [...], "sprint": "A1+A2+A3"}
}
```

Deque bornée à 200 éléments (audit trail), reset au restart du pod hub.

### B1 — `POST /agent-context/new`

Endpoint idempotent-par-appel qui crée un **contexte fresh** pour un
agent qui veut travailler isolé :

**Request** :
```http
POST /agent-context/new
Authorization: Bearer <api_key>
Content-Type: application/json

{
  "label": "Analyse trafic Marseille 2027-Q1",   // optionnel, défaut auto-timestampé
  "profile": "standard",                          // optionnel
  "origin": "test",                               // "user"|"demo"|"test", défaut "user"
  "agent_id": "recipe-runner-42"                  // optionnel, sanitize [a-zA-Z0-9._-]+
}
```

**Response 201** :
```json
{
  "sid": "abc123def456",
  "pid": "7890abcdef01",
  "session_id": "agent:recipe-runner-42:sid:abc123def456",
  "study": { ... manifest complet },
  "project": { ... manifest complet }
}
```

**Chain interne** :
1. `studies.create_study(owner, name, profile, origin)`
2. `_execute_python_in_workspace(init_pod_layout_code)` — layout PVC
3. `studies.create_project(sid, owner, "Projet principal", is_default=True)`
4. `_ensure_active_study_for_agent(username, sid)` — mutex A2 + instrumentation A3

L'agent utilise ensuite ce `session_id` dans son header `X-Session-Id`
pour tous ses calls MCP suivants. Le mécanisme A1 maintient l'isolation
automatiquement.

## Séquence exemple : 2 agents concurrents

```
                       hub                          workspace QGIS
Agent A (recipe)  ────► POST /agent-context/new
                       ├─ create_study sidA
                       ├─ create_project pidA
                       ├─ _ensure_active_study(sidA)
                       │    ├─ get_active_study → sidX
                       │    ├─ lock[user1]
                       │    ├─ save sidX ──────────► activate_pod_code sidX
                       │    ├─ set_active sidA
                       │    └─ activate ────────────► activate_pod_code sidA
                       └─ 201 {session_id="agent:A:sid:sidA"}
                       
Agent B (chat)    ────► POST /agent-context/new
                       ├─ create_study sidB
                       ├─ create_project pidB
                       └─ _ensure_active_study(sidB)
                            ├─ get_active_study → sidA
                            ├─ lock[user1] (attend fin A si en cours)
                            ├─ save sidA ────────► activate_pod_code sidA
                            ├─ set_active sidB
                            └─ activate ─────────► activate_pod_code sidB
                       201 {session_id="agent:B:sid:sidB"}

Agent A            ────► POST /mcp/... X-Session-Id: agent:A:sid:sidA
                       ├─ _ensure_active_study(sidA)
                       │    ├─ get_active_study → sidB
                       │    ├─ lock[user1]
                       │    └─ switch sidB→sidA
                       └─ proxy MCP call

Agent B            ────► POST /mcp/... X-Session-Id: agent:B:sid:sidB
                       ├─ _ensure_active_study(sidB)
                       │    ├─ get_active_study → sidA
                       │    ├─ lock[user1]
                       │    └─ switch sidA→sidB
                       └─ proxy MCP call
```

Chaque call MCP est précédé d'un switch si nécessaire. Coût amorti :
100-500ms de switch par call qui change de contexte. Les calls
consécutifs sur le même sid ne prennent PAS le lock (fast-path).

## Limitations connues

- **Workspace mono-QGIS** : deux `execute_python` concurrents sont
  sérialisés par le workspace côté MCP, indépendamment du hub. Le mutex
  A2 empêche seulement les race conditions sur le switch, pas le
  parallélisme d'exécution.
- **Latence switch** : chaque switch coûte 100-500 ms (autosave +
  activate_pod_code + QGIS project.read). Si un agent switche à chaque
  call, la latence cumulée devient sensible. Pattern recommandé : un
  agent garde son sid pour une session cohérente et batch ses calls.
- **Instrumentation en mémoire** : compteur + deque perdent au restart
  du pod hub. Un futur chantier metrics peut re-exporter vers Prometheus.

## Rollback

Aucun flag runtime — le comportement est déclenché par le header
`X-Session-Id`. Un client qui ne passe pas ce header voit le
comportement legacy (héritage de l'active_study global).

Rollback code (si régression) : revert le commit du sprint concerné.
Les 5 commits sont atomiques et rollback-safe :
- `946188a` A1 — extract + hook
- `e671797` A2 — mutex
- `6bde024` A3 — instrumentation
- `019669e` B1 — endpoint agent-context/new

## Hors périmètre

- (C) Pod QGIS-headless-agent séparé pour vrai parallélisme runtime →
  chantier futur si contention observée en prod via `/diagnostics/isolation`.
- (D) Copy-on-write / fork par agent → complexité conflictuelle non
  justifiée aujourd'hui.
- Cross-user isolation → couverte par ailleurs (un `username` = un
  pod workspace, mutex A2 par user).

## Q/R attendues

**Q : Un agent legacy qui ne passe pas `X-Session-Id` casse-t-il ?**
R : Non. `_extract_expected_sid(None) → None → early return`, comportement
identique à avant ce sprint.

**Q : Le mutex A2 bloque-t-il deux users différents ?**
R : Non. `defaultdict` crée un lock **par username**. Deux users sur des
pods différents n'ont aucune contention entre eux.

**Q : Un agent qui appelle `POST /agent-context/new` en boucle crée-t-il
des études zombies ?**
R : Oui, chaque appel crée une étude distincte. Recommandation : utiliser
`origin: "test"` pour un scratchpad jetable, ou reutiliser un sid gardé
en memoire côté agent.

**Q : Comment observer la contention en prod ?**
R : `curl -H "Authorization: Bearer $HUB_API_KEY" $HUB_URL/diagnostics/isolation`.
Regarder `switches_total_since_boot` (croissance) et `active_locks.active_study_switch`
(pic à 1+ = contention en cours).
