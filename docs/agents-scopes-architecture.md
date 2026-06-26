# Agents scopés — architecture (clés scopées)

> État : **fondation livrée (étapes 1-3), inerte**. Data-binding + endpoint de
> mint + UI = **V2, différés** (cf. §6). Aucun impact sur V1.5 (clé superviseur
> unique). Ce document permet de reprendre la lane proprement.

## 1. Principe

Le substrat est **toujours plein et appartient au user** : 1 user = 1 workspace
QGIS contenant tout son contenu (études, projets, données, publications). On ne
restreint pas le contenu — on change la **lentille** à travers laquelle un
agent/client le voit et agit dessus.

> **La lentille est portée par la *credential de connexion*, pas par le workspace.**

- **Défaut** = clé superviseur nue `qgis_<user>_<hex32>` → accès total. C'est ce
  qu'utilisent le **client MCP du user** (claude.ai) et l'**agent du desk** par
  défaut.
- **Agent configuré/publié** = clé **scopée** `qgisk_<user>_<hex32>` → porte un
  scope résolu côté serveur, révocable/ajustable sans ré-émettre.

## 2. Le descripteur de scope

Chaque clé scopée est indexée (table `scoped_keys`) à un descripteur :

```
scope = {
  owner:   <user>            # propriétaire des données (toujours lui)
  sid:     <study_id>        # 12-hex ; étude ciblée
  pid:     <project_id|null> # null = étude entière
  persona: <profile_id>      # system prompt / comportement
  tools:   "all" | [..]      # liste blanche de tools
  data:    all | study | project
  mode:    supervisor | scoped
  actor:   owner | delegate
}
```

3 plans = **le même descripteur à restrictions croissantes** :

| Plan | mode / actor | tools / data | Session |
|------|--------------|--------------|---------|
| Supervision (défaut) | supervisor / owner | all / all | workspace live |
| Projet-édition | scoped / owner | whitelist / project | workspace live (sérialisé) |
| Publié-délégué | scoped / delegate | whitelist / project | projection **read-only** S3 |

## 3. Point d'application unique : le hub

Le hub `/mcp` est l'**arbitre unique** : il résout la clé en scope et applique le
cantonnement, pour **toute** surface (client MCP, agent desk, embed publié). Le
défaut (clé nue) = supervision = comportement historique. C'est non-régressif.

## 4. Ce qui est livré (étapes 1-3, inerte)

| Étape | Commit | Fichier | Contenu |
|-------|--------|---------|---------|
| 1 | `d22b27e` | `hub/hub/auth.py` | table `scoped_keys` (apikeys.db) + `create_scoped_key` / `_validate_scoped_key` / `revoke_scoped_key` / `list_scoped_keys`. Pas de `UNIQUE(study,project)` (plusieurs agents publiés possibles sur le même projet). |
| 2 | `079128d` | `hub/hub/auth.py` | `get_current_user` résout le préfixe `qgisk_` → renvoie le scope. `_bearer_scope()` helper. `oidc_auth_middleware` §3bis : une clé scopée valide pose `request.state.scope` sur les routes inter-pod (dont `/mcp`). Additif. |
| 3 | `a9820b8` | `hub/hub/main.py` | `_proxy_request(scope=)` applique la **whitelist de tools** : gate `tools/call` (erreur JSON-RPC `-32601`) + filtre `tools/list` (JSON + SSE). Helpers `_scope_tools_whitelist` / `_jsonrpc_obj` / `_tool_call_denied` / `_filter_tools_list_payload`. |

Tests : `hub/tests/test_scoped_keys.py` (12) + `hub/tests/test_mcp_proxy_scope.py` (6).

**Inerte** : `_scope_tools_whitelist(None)` → `None` → aucune interception. Tant
qu'aucun endpoint de mint n'existe, aucune clé scopée n'est émise → zéro
exposition. La clé superviseur passe par `_is_inter_pod_authorized`
(== `HUB_API_KEY` en hub mono-user) → `request.state.scope` reste `None` = total.

## 5. Préfixes de clés (à ne pas confondre)

- `qgis_<user>_<hex>`  → clé **superviseur** (= `HUB_API_KEY` en hub mono-user). Accès total.
- `qgisk_<user>_<hex>` → clé **scopée**. `"qgisk_".startswith("qgis_")` est **False** → distinguables.

## 6. V2 — data-binding (différé, plan verrouillé)

L'enforcement livré restreint **quels tools** un agent scopé peut appeler. Reste
à enforcer **quelles données** (binding étude/projet). Décision actée
(`#decisions`, coordination Passerelle-Archi × Composants-Architect) :

- **Tools BigQgisMCP** (ceux qui passent par `/mcp`) **n'acceptent PAS sid/pid en
  args** — ils opèrent sur l'**étude active du pod** (`activate_study` /
  `activate_project`). → binding = **bind session** (le hub force
  `activate_study(scope.sid)` + `activate_project(scope.pid)` avant `tools/call`,
  ou refuse si le pod est sur une autre étude). **PAS d'injection d'args** (elle
  casserait ces tools).
- **`native_tools_v2`** (Composants, sid-aware) = consommés en **REST hub direct**
  (Bearer `HUB_API_KEY`), **hors `/mcp`** → enforcement scope dans les endpoints
  REST + override `manifest.sid = scope.sid`.
- **`native_tools_v2` si un jour exposés via `/mcp` wrapper** (claude.ai direct)
  → whitelist-injection sur les 10 tools sid-aware.
- **Meta-cognitifs** (`describe_entity_schema`, `list_entity_kinds`,
  `validate_manifest`) → scope-agnostic, aucun binding.

Backlog V2 dans l'ordre (dépendances strictes) :

1. **Data-binding session** au proxy `/mcp` (prérequis : vérifier
   `activate_study/activate_project` côté workspace + trancher la concurrence
   single-session : refus vs switch).
2. **Endpoint mint** `POST /scoped-keys` (+ revoke/list) — **après** #1 (sinon une
   clé scopée restreint les tools mais pas les données = trompeur).
3. **Session routing read-only** (Plan publié, projection S3).
4. **Échange jeton→clé** pour embed publié sécurisé (la clé brute ne transite pas
   côté client).

## 7. Coordination (lanes)

- **Passerelle-Archi** : couche accès/scope/connexion — `auth.py` (clés, middleware),
  `main.py` `/mcp` (proxy filtrant, routage session), endpoint mint.
- **Composants-Architect** : modèle étude/projet/composant — `studies.py`, `models/*`,
  `components.py`/`assemblies.py`, `qgis_agent.py` (wrapper L2), `native_tools_v2`,
  UI configurer/publier un agent.
- Seam = le descripteur de scope + la table `scoped_keys`.
- Travail en **worktrees git séparés** (fichiers disjoints, commits interleavés
  sur `main` sans conflit).
