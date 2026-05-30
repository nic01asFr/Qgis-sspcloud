# Architecture Agent — qgis-sspcloud

> **Document de cadrage architectural**. Formalise la cible vers laquelle on
> converge (V2+). Le présent (V1) est documenté dans
> [`STRUCTURE_ET_PROCESS.md`](STRUCTURE_ET_PROCESS.md). La vision produit est
> dans [`CHARTE_AGENT.md`](CHARTE_AGENT.md). Légende : ✅ codé · ⏳ cadré non codé
> · 🔬 à explorer.

## Table des matières

1. [Modèle de données](#1-modèle-de-données)
2. [Typologie 4 agents (A/B/C/D)](#2-typologie-4-agents-abcd)
3. [Dimension Scope (orthogonale au Profile)](#3-dimension-scope-orthogonale-au-profile)
4. [Continuum trace → macro → template → recette](#4-continuum-trace--macro--template--recette)
5. [Décisions architecturales Q1-Q8](#5-décisions-architecturales-q1-q8)
6. [Endpoints API (existants + cibles)](#6-endpoints-api-existants--cibles)
7. [Roadmap d'implémentation V2/V3](#7-roadmap-dimplémentation-v2v3)

---

## 1. Modèle de données

Le modèle est centré sur **l'étude** comme unité atomique, autour de laquelle gravitent toutes les autres entités.

### Entités principales

```
┌─ User ──────────────────────────────┐
│ id, email, onyxia_user              │
│ (lié au compte SSPCloud)            │
└──────────┬──────────────────────────┘
           │ 1:N
           ▼
┌─ Study (étude) ─────────────────────┐
│ id, name, owner, profile_id         │
│ status (active|archived)            │
│ created_at, updated_at              │
│ Bundle : project.qgz + data/ +      │
│ treatments.jsonl + recipes/ +       │
│ exports/                            │
└──┬───┬──┬──┬──┬─────────────────────┘
   │   │  │  │  │
   │   │  │  │  └──► Publications (livrables S3)
   │   │  │  │       (storymap, pdf, dataset,
   │   │  │  │        recipe, agent_def)
   │   │  │  │
   │   │  │  └─────► Recipes (YAML versionnés SHA)
   │   │  │
   │   │  └────────► Macros (extracts treatments.jsonl)
   │   │
   │   └───────────► AgentDefinitions (personas custom)
   │
   └───────────────► ChatSessions (L1 mémoire)
                     └─ Messages (avec audit tool_calls)
```

### Profile (✅ existant)

```yaml
# hub/hub/profiles/{id}.yaml
id: risk_analyst
name: "Risques naturels"
description: "..."
image_variant: "risk"
agent_system_prompt: |
  Tu es expert en risques inondations CEREMA...
mcp_tools:
  allowed: [set_study_zone, smart_load, run_recipe, execute_python,
            run_processing, export_pdf, export_flood_map, publish_artifact]
  disabled: [delete_file, restart_qgis_engine]
geoai_watcher:
  enabled: false
```

8 profils déclarés : `standard`, `risk_analyst`, `db_analyst`, `geoai_analyst`,
`storymap_creator`, `map_composer`, `recipe_creator`, `guided_tour`.

### Scope (⏳ à implémenter V2)

Dimension orthogonale au profil. 4 valeurs :

```python
class AgentScope(Enum):
    EXPLORATION    = "exploration"     # navigation libre, pas de mutation
    PRODUCTION     = "production"      # travail actif sur étude, mutations OK
    DIFFUSION      = "diffusion"       # livrable publié, lecture seule pour lecteur final
    CAPITALISATION = "capitalisation"  # extraction patterns, création templates/recipes

@dataclass
class ScopeConstraints:
    can_mutate_study: bool
    can_publish: bool
    can_create_recipe: bool
    can_create_template: bool
    tools_whitelist: set[str] | None      # None = no override
    memory_layers: set[str]                # subset of {L1, L2, L3, L4}
    system_prompt_suffix: str
```

### Recipe (⏳ à implémenter V2 avec versioning SHA)

```yaml
# /data/studies/{sid}/recipes/{slug}.yaml
kind: recipe
slug: densite_dvf_quartier
name: "Densité de mutations DVF par quartier"
study_origin: d20d37a74895
sha: e7a8b2c1d4f5...                    # hash auto du YAML
previous_sha: a1b2c3d4e5f6...           # chaîne de versions
created_at: 2026-06-15T14:23:00
inputs:
  - name: zone
    type: string
    required: true
    example: "Béziers"
  - name: annee_min
    type: integer
    default: 2020
steps:
  - tool: set_study_zone
    args: {target: "{{zone}}"}
  - tool: smart_load
    args: {id: "dvf_mutations", filter: "annee>={{annee_min}}"}
  # ...
outputs:
  - "Couche dvf_jointe"
  - "Stats par maille"
```

Stockage : `/data/studies/{sid}/recipes/` (workspace PVC).
Index : table SQLite hub avec `(slug, sid, sha, previous_sha, owner, created_at)`.
Historique : `/data/studies/{sid}/recipes/.history/{sha}.yaml` (immutables).

### Macro (⏳ à implémenter V2/V3)

```yaml
# /data/studies/{sid}/macros/{slug}.yaml
kind: macro
slug: analyse_dvf_quartier_brute
name: "Analyse DVF (brouillon, à paramétrer)"
session_id: abc12345                    # session chat origine
study_id: d20d37a74895
markers:
  start_idx: 142                        # index dans treatments.jsonl
  end_idx: 158
steps:                                  # extrait verbatim du journal
  - {tool: set_study_zone, args: {target: "Béziers"}, ts: 1717..., success: true}
  # ...
annotations:                            # notes user optionnelles
  142: "ici je veux la zone"
  150: "calcul de densité — clé du livrable"
```

### AgentDefinition (⏳ à implémenter V3)

Persona personnalisé publiable comme livrable :

```yaml
# /data/studies/{sid}/agents/{slug}.yaml
kind: agent
slug: inondation_advisor
name: "Conseiller PPRi"
description: "Réponds aux questions du public sur le PPRi du Lavandou"
persona:
  base_profile: risk_analyst
  system_prompt_override: |
    Tu es un conseiller technique du CEREMA spécialisé en PPRi...
  scope_override: diffusion              # force read-only
  tools_whitelist:
    - memory_search
    - search_study_docs
context_attached:
  study_id: 4c176e577d70
  documents: [pprm_lavandou.pdf, rapport_2024.pdf]
  livrables: [storymap/risque_t100_lavandou, pdf/rapport_lavandou]
ttl_days: 365
acl:
  visibility: public                     # v1 seulement public
```

### Publication (✅ existant)

```yaml
# Métadonnées catalog S3 (hub indexe)
kind: storymap | pdf | dataset | recipe | flux | agent
slug: inondation_t100_beziers
owner: nicolaslaval
study_id: d20d37a74895
url: s3://...
hub_url: https://hub/published/{owner}/{kind}/{slug}
size: 11720767
content_type: text/html
published_at: 1780154677
# V2 enrichissements :
source_recipe_slug: risque_inondation_v2
source_recipe_sha: e7a8b2c1d4f5
provenance_chain: [study → recipe → run_id]
```

---

## 2. Typologie 4 agents (A/B/C/D)

L'agent n'est pas une instance unique. C'est **4 surfaces** qui partagent le code (`chat.html` + `qgis_agent.py`) mais diffèrent par leur scope, leur profil dérivé, et leurs autorités.

### Agent A — Mon Assistant (user-scope)

- **Surface** : `/workspace`, hors étude
- **Mémoire** : L3 user seul (préférences, KB cross-projets)
- **Scope par défaut** : `exploration`
- **Profil dérivé** : `standard` ou `guided_tour`
- **Capacités** :
  - Navigation des études existantes
  - Création de nouvelles études
  - Conseils méthodologiques basés sur historique cross-projets
  - Suggestion proactive de macros/recettes réutilisables (V3)
- **Restrictions** :
  - Pas de mutation d'étude active (pas d'étude sélectionnée)
  - Pas de tools spatiaux sur étude (lecture catalog uniquement)

État : ⏳ partiellement existant (vue `/workspace` rend des KPI, mais sans chat agent A intégré).

### Agent B — Agent d'Étude (study-scope)

- **Surface** : `/desk?study={sid}`
- **Mémoire** : L3 + L2 (étude active : project_state + treatments)
- **Scope par défaut** : `production`
- **Profil dérivé** : du profil déclaré à la création de l'étude (+ overrides contextuels par render sélectionné)
- **Capacités** :
  - Tous les tools mutating (run_recipe, smart_load, execute_python, publish_artifact, etc.)
  - Édition project.qgz, exports, recettes
  - Création de livrables publiables
- **Restrictions** : aucune (autorité complète sur l'étude active)

État : ✅ codé en prod (c'est l'agent actuel via iframe `/desk` chat).

### Agent C — Assistant Livrable (artifact-scope)

- **Surface** : iframe embed dans `/published/{owner}/{kind}/{slug}`
- **Mémoire** : L3 user (du visiteur s'il est authentifié) + L2 étude figée (read-only) + L4 documents attachés au livrable
- **Scope par défaut** : `diffusion`
- **Profil dérivé** : du `kind` du livrable + du profil de l'étude origine
- **Capacités** :
  - Recherche dans le livrable et ses documents annexes
  - Citation des sources (audit trail visible)
  - Comparaison avec autres publications similaires
  - Réponses contextualisées au lecteur (CEREMA, citoyens, élus)
- **Restrictions** :
  - Pas de tools mutants (pas de modification de la donnée source)
  - Pas d'accès à la mémoire L3 d'autres users
  - Pas de publish_artifact ni d'export

URL d'embed : `/embed/agent/{owner}/{kind}/{slug}` → `chat.html?embed=1&scope=diffusion&kind=storymap&slug=X`

État : ⏳ pas implémenté. Phase V3 prioritaire.

### Agent D — Co-auteur Recette (editor-scope)

- **Surface** : `/desk?edit=recipe:{slug}` (mode canvas spécialisé)
- **Mémoire** : L3 + L2 + L4 catalog recipes existantes
- **Scope par défaut** : `capitalisation`
- **Profil dérivé** : `recipe_creator` forcé
- **Capacités** :
  - Édition YAML avec validation
  - Dry-run de la recette en cours
  - Extraction depuis une macro (Programming by Demonstration)
  - Création de templates paramétrés
  - Publication recipe S3
- **Restrictions** :
  - Pas d'exécution mutating directe (sandbox dry-run)
  - Pas de tools production (focus sur capitalisation)

État : ⏳ pas implémenté. Phase V3.

---

## 3. Dimension Scope (orthogonale au Profile)

### Pourquoi orthogonal

Le **profil** = persona (qui je suis, mon expertise).
Le **scope** = autorité (ce que j'ai le droit de faire dans ce contexte).

Sans cette séparation, on devrait dupliquer 8 profils × 4 scopes = 32 profils. La composition runtime permet de garder 8 profils et de moduler leur comportement.

### Matrice profile × scope

Exemple avec `storymap_creator` :

| Scope | Comportement |
|-------|--------------|
| exploration | « Voici 3 storymaps similaires publiées. Veux-tu en démarrer une ? » — propose, ne crée pas |
| production | « J'édite ta storymap. Tools mutants disponibles. » — édition complète |
| diffusion | « Bonjour, je vous accompagne dans la lecture. » — lecture seule |
| capitalisation | « Cette storymap a été générée par recette X. Veux-tu en faire un template ? » — extraction |

### Implémentation à venir (V2)

```python
# hub/hub/scopes.py (à créer)

def derive_scope(request: Request) -> str:
    """Dérive automatiquement le scope depuis la navigation."""
    path = request.url.path
    if path.startswith("/published/"):
        return "diffusion"
    if path.startswith("/desk") and request.query_params.get("edit"):
        return "capitalisation"
    if path.startswith("/desk"):
        return "production"
    return "exploration"

SCOPE_CONSTRAINTS = {
    "exploration": ScopeConstraints(
        can_mutate_study=False, can_publish=False,
        can_create_recipe=False, can_create_template=False,
        tools_whitelist={"memory_search", "list_studies", "list_recipes",
                         "search_kb", "geocode"},
        memory_layers={"L3"},
        system_prompt_suffix="Tu es en mode EXPLORATION : pas de mutation, "
                             "tu peux suggérer mais pas exécuter."
    ),
    "production": ScopeConstraints(
        can_mutate_study=True, can_publish=True,
        can_create_recipe=False, can_create_template=False,
        tools_whitelist=None,  # tous les tools du profile
        memory_layers={"L1", "L2", "L3"},
        system_prompt_suffix=""
    ),
    "diffusion": ScopeConstraints(
        can_mutate_study=False, can_publish=False,
        can_create_recipe=False, can_create_template=False,
        tools_whitelist={"memory_search", "search_study_docs",
                         "get_features", "get_screenshot"},
        memory_layers={"L2", "L3", "L4"},
        system_prompt_suffix="Tu es en mode DIFFUSION : tu accompagnes "
                             "un lecteur. Lecture seule, citations sourcées."
    ),
    "capitalisation": ScopeConstraints(
        can_mutate_study=False, can_publish=True,
        can_create_recipe=True, can_create_template=True,
        tools_whitelist={"memory_search", "list_recipes", "validate_yaml",
                         "dry_run_recipe", "publish_artifact"},
        memory_layers={"L2", "L3", "L4"},
        system_prompt_suffix="Tu es en mode CAPITALISATION : tu extrais "
                             "des patterns réutilisables, tu ne mutes pas."
    ),
}

# Dans qgis_agent._build_prompt :
profile = _PROFILES_CACHE[profile_id]
scope = SCOPE_CONSTRAINTS[scope_id]
system_prompt = (
    profile["agent_system_prompt"]
    + scope.system_prompt_suffix
    + _QGIS_ESSENTIALS
    + context_layers
)
tools = [t for t in mcp_tools
         if (scope.tools_whitelist is None or t.name in scope.tools_whitelist)
         and t.name in (profile_tools_whitelist or all_tool_names)]
```

---

## 4. Continuum trace → macro → template → recette

### Le continuum de cristallisation

```
TRACE BRUTE           MACRO                TEMPLATE             RECETTE
treatments.jsonl  →   extract marqué   →   paramétré YAML   →   publiée S3
(auto, jamais     (scope étude,        (scope étude,        (catalog public,
édité)            user décide ce       Agent D paramétrise) réutilisable, SHA
                  qui mérite d'être                          versioning)
                  formalisé)
```

Chaque étape garde un lien vers l'étape précédente (provenance).

### Le `run_recipe` natif sait exécuter

- Une recette publiée (SHA pinned)
- Une macro brute (verbatim)
- Un template en dry-run

Même moteur, même audit trail.

### Markers macro (Q5 validé)

**Deux modes complémentaires** :

1. **Explicite** : chat command « démarre macro X / stop »
   ```
   User: "Démarre l'enregistrement macro 'analyse_dvf'"
   Agent: ▶︎ marker_start_macro posé à treatments.jsonl idx 142
   ... user fait son analyse ...
   User: "Stop, sauve"
   Agent: ⏹ marker_end posé à idx 158, macro 'analyse_dvf' sauvée
   ```

2. **Rétroactif** : drawer Historique avec timeline cliquable
   - Onglet « 🕘 Historique » du drawer Ressources
   - Liste chronologique des `treatments.jsonl` de l'étude
   - User sélectionne 2 events (début/fin) → bouton « En faire une macro »

### Paramétrage par Agent D

Agent D reçoit une macro et propose un template paramétré :

```
User: "Paramètre cette macro pour qu'elle marche sur d'autres communes"
Agent D: 
  - Détecte "Béziers" hardcodé → propose {{zone}} comme paramètre
  - Détecte bbox 4326 littérale → propose dérivée de set_study_zone({{zone}})
  - Détecte "2020" pour année min → propose {{annee_min}} avec default 2020
  - Présente le YAML proposé pour validation user
```

État : ⏳ pas implémenté. Phase V3 (après Agent C).

### Versioning recette (Q6 SHA hash)

```python
def save_recipe(sid, slug, yaml_content):
    sha = hashlib.sha256(yaml_content.encode()).hexdigest()[:12]
    previous_sha = get_current_sha(sid, slug)
    yaml_content_with_meta = yaml_content + f"\nsha: {sha}\nprevious_sha: {previous_sha}"
    
    # Storage workspace PVC
    write(f"/data/studies/{sid}/recipes/{slug}.yaml", yaml_content_with_meta)
    write(f"/data/studies/{sid}/recipes/.history/{sha}.yaml", yaml_content_with_meta)
    
    # Index hub catalog
    upsert_recipe_catalog(sid, slug, sha, previous_sha, owner, now())
```

Endpoint : `GET /studies/{sid}/recipes/{slug}/history` → chaîne des SHA.

---

## 5. Décisions architecturales Q1-Q8

Arbitrages tactiques verrouillés 2026-05-30 (validés par user). Sauf consensus explicite pour changement, ces décisions tiennent.

### Q1 — Profile × Scope : **N:M flexible**

Un profil peut tourner en plusieurs scopes. Composition runtime via `SCOPE_CONSTRAINTS[scope]` appliqué au-dessus du `profile.agent_system_prompt`.

### Q2 — Routage prod : **hub seul sert l'UI**

Hub sert `/desk`, `/workspace`, `/published/*`. Agent garde uniquement :
- `/` (chat.html embed)
- `/chat` (SSE streaming)
- `/api/*` (status, refresh-llm-config, refresh-profiles, stt)
- `/sessions/*` (memory access)
- `/memory/*` (memory direct)
- `/context/render/{sid}` (postMessage receiver)

Routes mortes côté agent supprimées (cf. commit `e0dda14`).

### Q3 — Recipe storage : **workspace PVC + catalog hub**

- **Storage** : YAML dans `/data/studies/{sid}/recipes/{slug}.yaml` (workspace PVC)
- **Index** : table SQLite hub `recipes (slug, sid, sha, previous_sha, owner, created_at)`
- **Pattern** : identique à publications (storage workspace, index hub)

### Q4 — Agent C livrable : **même pod agent + `?scope=diffusion`**

Pas de pod séparé par publication. L'agent existant accepte un query param `scope` qui mute son comportement (system prompt + tools whitelist + memory access).

URL embed : `/embed/agent/{owner}/{kind}/{slug}` → `chat.html?embed=1&scope=diffusion&kind=storymap&slug=X`

### Q5 — Macro markers : **explicite + rétroactif (les deux)**

Voir [§4](#4-continuum-trace--macro--template--recette).

### Q6 — Recipe versioning : **SHA hash + auto-history**

Voir [§4](#4-continuum-trace--macro--template--recette).

### Q7 — ACL agent publication : **public-only v1**

Tous les livrables publics. ACL (Vault, email-whitelist, JWT) repoussée à V3 si besoin émerge.

### Q8 — Migration profile_id existant : **backwards-compatible**

Scope optionnel. Dérivé automatiquement de la surface si absent :

| URL pattern | Scope dérivé |
|-------------|--------------|
| `/workspace` | `exploration` |
| `/desk` (sans edit param) | `production` |
| `/desk?edit=recipe:*` | `capitalisation` |
| `/published/*` | `diffusion` |
| `/embed/agent/*` | `diffusion` |

Pas de migration DB. Toutes les requêtes existantes continuent de marcher avec leur comportement actuel.

---

## 6. Endpoints API (existants + cibles)

### Hub — endpoints actuels (✅ V1)

```
# Auth
GET  /login?key=...                  Pose cookie hub_api_key
POST /authorize/confirm              OAuth code flow
POST /oauth/token                    Échange code → token

# UI
GET  /                                Redirect intelligent
GET  /workspace                       Vue compte
GET  /desk                            Bureau atelier
GET  /published/{o}/{k}/{slug}        Livrable public

# Études
GET  /studies                         Liste mes études
POST /studies                         Crée étude
GET  /studies/active                  Étude active
POST /studies/{sid}/activate          Activate
DELETE /studies/{sid}                 Archive
GET  /studies/{sid}/export            Export ZIP
POST /studies/{sid}/save              Save project QGIS
GET  /studies/{sid}/publications      Publications de cette étude
GET  /studies/{sid}/treatments        Audit trail

# Sessions workspace
POST /sessions                        Crée/réveille
POST /workspace/wake                  Réveil avec lock
GET  /desk/workspace-status           État polling JS

# Publications
GET  /catalog/{owner}                 Toutes publications user
GET  /desk/catalog                    Idem enrichi UI
POST /publish/{kind}/{slug}           Publie depuis workspace
DELETE /publish/{kind}/{slug}         Unpublish

# Profils
GET  /profiles                        Liste
GET  /profiles/{id}                   Détail
POST /profiles/reload                 Hot-reload YAML

# GeoAI
GET  /geoai/status                    État pod GPU
GET|POST /geoai/{path}                Proxy avec scale 0→1

# Admin
GET  /admin/workspace-info            Diagnostic SS workspace
POST /admin/workspace-fix-image       Force pull image
POST /admin/agent-config              Patch env agent

# MCP
POST /mcp                             Proxy vers workspace pod /mcp

# Mémoire desk
GET  /desk/memory                     Sections mémoire user
GET  /desk/study-files                Fichiers étude
GET  /desk/layers                     Couches QGIS courantes
GET  /desk/agent-health               Sonde same-origin
```

### Hub — endpoints à ajouter (⏳ V2)

```
# Recettes (Q3)
GET    /studies/{sid}/recipes                 Liste recettes étude
GET    /studies/{sid}/recipes/{slug}          Détail YAML + meta
PUT    /studies/{sid}/recipes/{slug}          Crée/update + SHA versioning
DELETE /studies/{sid}/recipes/{slug}          Supprime
GET    /studies/{sid}/recipes/{slug}/history  Chaîne des SHA

# Scope (Q1)
# Non-endpoint — résolu côté code dans le composeur de prompt/tools

# Macros (Q5)
GET    /studies/{sid}/macros                  Liste
POST   /studies/{sid}/macros                  Crée depuis sélection treatments
GET    /studies/{sid}/macros/{slug}           Détail
POST   /studies/{sid}/macros/{slug}/parameterize  Agent D paramétrise → template

# Agents publiables (Q4 + V3)
GET    /studies/{sid}/agents                  Liste
POST   /studies/{sid}/agents                  Déclare
PUT    /studies/{sid}/agents/{slug}           Update
DELETE /studies/{sid}/agents/{slug}           Delete
POST   /publish/agent/{slug}                  Publie sur S3
GET    /embed/agent/{owner}/{kind}/{slug}     iframe pour Agent C
```

### Agent — endpoints actuels (✅ V1)

```
GET  /                                Chat HTML (avec ?embed=1)
POST /chat                            SSE streaming
POST /chat/{sid}/stop                 Coupure user

GET  /api/status                      LLM/HUB config check
POST /api/refresh-llm-config          Proxy vers hub refresh
POST /api/refresh-profiles            Re-fetch profils depuis hub

GET  /sessions                        Liste sessions chat
GET  /sessions/{id}/messages          Historique
GET  /sessions/{id}/checkpoints       Liste checkpoints
POST /sessions/{id}/rollback/{ckpt}   Rollback projet QGIS
POST /sessions/{id}/checkpoints/purge

GET  /memory/embed/stats              Worker indexation
GET  /memory/health                   Observabilité L3
GET  /memory/context                  Build context summary
POST /memory/preference               Save preference
GET  /user/preferences                Read prefs
GET  /user/insights                   List insights
POST /user/insights                   Add insight
GET  /user/memory                     Bundle complet

GET  /projects                        Liste projects (legacy)
GET  /recipes                         Liste recettes (lecture)
POST /projects                        Crée project

POST /context/render/{sid}            postMessage receiver
GET  /context/render/{sid}            État render actif
DELETE /context/render/{sid}          Reset

GET  /profiles                        Proxy vers hub

POST /stt                             Speech-to-text Whisper
```

### Agent — endpoints à ajouter (⏳ V2/V3)

```
GET  /embed/agent/{owner}/{kind}/{slug}  Mode Agent C
                                          (scope=diffusion appliqué)
```

---

## 7. Roadmap d'implémentation V2/V3

### V1 — Publication CEREMA (en cours)

**Objectifs** : déploiement fiable + utilité démontrée + structure propre.

Phase A (livrée) : 5 commits fix cassures silencieuses + smoke test CI
Phase B (à faire) : étude vitrine + quickstart + peer test + annonce
Phase C (ce document) : ARCHITECTURE_AGENT.md + STRUCTURE_ET_PROCESS.md

### V2 — Maturation post-publication (2-4 semaines)

**Sur feedback réel des collègues CEREMA, par ordre de demande probable :**

1. **CRUD recettes UI** (Q3) — drawer Recettes + éditeur YAML + dry-run
   - Coût : 4-5 j
   - Pré-requis : aucun

2. **Scope orthogonal** (Q1 + Q8) — `derive_scope` + `SCOPE_CONSTRAINTS`
   - Coût : 1-2 j
   - Pré-requis : aucun
   - Bénéfice immédiat : préparation pour Agent C

3. **Agent C livrable MVP** (Q4) — iframe embed dans `/published/*`
   - Coût : 3-5 j
   - Pré-requis : Scope orthogonal implémenté

4. **Documents RAG par étude** (CHARTE F3) — PDF attachés + tools `search_study_docs`
   - Coût : 4-5 j
   - Pré-requis : aucun

5. **Étude bundle autoportant** (CHARTE F2) — chemins relatifs data, ZIP propre
   - Coût : 3-4 j
   - Pré-requis : utile avant share massif

### V3 — Vision long terme (3+ mois)

1. **Markers macros** (Q5 — explicite + rétroactif)
   - Coût : 4-5 j

2. **Agent D recipe editor** (Programming by Demonstration)
   - Coût : 5-7 j

3. **Templates agents user-créés** (CHARTE Phase 10, F7)
   - Coût : 3-5 j

4. **Collaboration & partage ACL** (Q7 enrichi)
   - Coût : 5-7 j

5. **Pod GPU init par défaut** (CHARTE F1, M7)
   - Coût : 1-2 j

6. **Macro learning complet** (CHARTE Phase 11)
   - Coût : 7-10 j

### Refonte technique différée

- **Refactor `hub/main.py` 3473 L** : à découper en modules `hub/routes/{auth,studies,publications,profiles,desk,admin}.py`
- **Helm chart agent dédié** : aujourd'hui auto-bootstrap, à formaliser si chart catalog SSPCloud le demande
- **Workspace image versioning automatique** : aujourd'hui manuel, à pipeliner si fréquence rebuilds augmente

---

## Maintenance de ce document

À mettre à jour quand :
- Une décision Q1-Q8 change (consensus explicite requis)
- Un agent A/B/C/D évolue dans ses capacités/restrictions
- Le modèle de données s'enrichit (nouvelle entité, nouveau champ)
- Une phase V2/V3 est implémentée (passer ⏳ → ✅)
- Un endpoint API critique est ajouté/modifié

Garder la cohérence avec :
- [`CHARTE_AGENT.md`](CHARTE_AGENT.md) : vision et principes
- [`STRUCTURE_ET_PROCESS.md`](STRUCTURE_ET_PROCESS.md) : mécanique du présent
- Code source (`hub/`, `agent/`, `docs/`)
