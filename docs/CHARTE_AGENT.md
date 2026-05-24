# Charte de fonctionnement de l'agent QGIS-CEREMA

> Document de cadrage évolutif. Tout n'est pas en place aujourd'hui, mais tout
> est cadré. Cette charte est la référence à relire avant chaque décision
> technique. Légende : ✅ codé en prod · ⏳ cadré non codé · 🔬 à explorer.

## 1. Vision produit

Ce service n'est pas "un agent IA pour QGIS". C'est **un atelier d'études
géospatiales où l'expertise métier du CEREMA se codifie, se réutilise et se
publie**.

Positionnement :
- ChatGPT généraliste = trop abstrait, ne connaît pas les données IGN/Géorisques
- QGIS Desktop seul = expert-only, savoir-faire dans la tête des chargés d'études
- **Ce service** = un assistant qui **incarne un savoir-faire métier précis**,
  qui suit l'utilisateur dans son cycle d'étude, et qui **rend ce savoir-faire
  partageable**.

L'expertise devient un **asset transmissible** : un agent template configuré
par un cartographe CEREMA peut être consommé par une DREAL sans formation SIG
approfondie.

## 2. Le cycle d'usage qui structure tout

```
1. EXPLORATION       2. PRODUCTION        3. DIFFUSION        4. CAPITALISATION
 ─────────────────    ───────────────       ───────────         ──────────────────
 "Qu'existe ?         "Je fais l'analyse,   "Je publie pour     "Je rends ça
  Quelle méthode ?"    je produis le        que d'autres        réutilisable pour
                       livrable concret"    consomment"         la prochaine étude"

 ──> Agent libre      ──> Agent intégré     ──> Agent embarqué  ──> Agent template
     (mode standalone)    (desk + étude)        (dans la publi)     (publiable)
```

L'**étude** est l'unité qui traverse tout le cycle. C'est elle qu'on archive,
qu'on partage, qu'on industrialise.

## 3. Les 9 principes de fonctionnement de l'agent

### Principe 1 — Contextualisation native (multimodale)

L'agent se positionne lui-même selon ce que l'utilisateur fait. Pas de combobox
de profil à remplir. Input texte OU vocal.

| Contexte de navigation | Mode agent |
|---|---|
| Pas d'étude active | Mode découverte / exploration |
| Étude active, vue desk générale | Profil métier de l'étude (risk, db, geoai…) |
| Étude + draft storymap sélectionnée | Édition storymap |
| Étude + recette ouverte | Auteur recette |
| Intent "crée storymap" détecté dans chat | Bascule édition storymap |
| GeoAI pod réveillé | Profil geoai_analyst dispo |

**Chips de contexte dans le bandeau** (lisible en permanence) :
- 📂 Étude active + profil agent courant
- 🧠 Modèle LLM utilisé (qwen/gemma)
- 🎯 GPU / IA état : prêt / attente / dormant / indisponible (cliquable pour réveiller)
- 🎤 Input vocal (Whisper STT) — bouton dictée dans la barre

UI : pas de combobox. Override manuel ponctuel possible si nécessaire (mais
c'est l'exception).

État : ⏳ cadré. Aujourd'hui combobox 8 profils statiques + sentinel
`.active_study` + bouton mic existant pour vocal. À faire : routeur contextuel
+ détection intent + remplacer combobox par chip + chip GPU/IA état.

### Principe 2 — Audit honnête par défaut

**Règle dure inviolable.** Tout chain-badge DSFR affiché dans un livrable
correspond à un traitement réellement exécuté et tracé dans `treatments.jsonl`.

- `StorymapBuilder.add_methodology_from_treatments(events)` est la voie canonique
- `add_methodology(steps=...)` émet un warning et marque les steps `source="manual"`
- L'agent ne fabrique jamais de steps
- Tests d'invariant (`hub/tests/test_storymap_audit.py`) verrouillent ça

État : ✅ codé en prod. À surveiller à chaque nouveau code touchant à
`storymap_dsfr` ou `audit_trail`.

### Principe 3 — Économe en ressources et en attention

L'agent évite trois gaspillages :

**a. Boucle d'erreur stérile**
- Détection fuzzy par `_extract_error_signature`
- Warning à ≥2 mêmes erreurs (injection système message demandant changement d'approche)
- Auto-stop dur à ≥3 mêmes erreurs (impossible de tourner en rond)
- Tip KB injecté automatiquement sur erreur connue (cf. `qgis_tips.md`)

**b. Tool calls redondants**
- Mémoire L2 cache `project_state` (CRS, layers) → évite `get_project_info` répété
- Le system_prompt enrichi avec L2 contient déjà ce que l'agent demanderait

**c. Hallucination géographique**
- Catalogue obligatoire en tête du prompt (overpass, geo.api.gouv.fr, data.geopf.fr, data.gouv CEREMA)
- Anti-hallucination : jamais d'URL inventée
- Cas concret durci : **arrondissements Paris/Marseille/Lyon** non indexés par
  `/communes?nom=` → résolution INSEE directe (Paris 75101-20, Marseille
  13201-16, Lyon 69381-89) avec regex bidirectionnelle (ville↔numéro)

État : ✅ codé en prod. Catalogue data.gouv CEREMA à enrichir lors de
l'Étape 4 (data.gouv MCP).

### Principe 4 — Rollback systématique

Chaque action mutante = snapshot pré-tool. L'agent **peut tenter**, l'utilisateur
**peut revenir**. Pas d'auto-censure "et si je casse quelque chose ?".

- Snapshot automatique avant tools ∈ `_MUTATING_TOOLS` (smart_load, processing.run, etc.)
- `.qgz` versionnés dans `/data/studies/{sid}/.checkpoints/`
- Bouton "↶ Revenir avant" sur chaque blockquote tool mutating
- Re-attaché après reload via matching ordinal (`reattachRollbackButtonsFromHistory`)
- Fallback rollback automatique si stop + timeout grace 30s
- Purge auto > 20/session ou > 7j (SQLite + fichiers PVC)

État : ✅ codé en prod (Commits A/B/C livrés).

### Principe 5 — Pédagogue par capture passive

L'agent capitalise sans demander à l'utilisateur de structurer :

- **Insights L3 user** : faits stables capturés via `<remember>` (mémoire transverse toutes études)
- **Macro learning** (⏳ Phase 11) : trace tool_calls → analyzer LLM → recette paramétrée
- **Suggestion proactive** : *"Tu fais ce process pour la 3e fois sur 3 communes. Veux-tu que je l'enregistre comme recette ?"*

État : insights ✅ codé. Macro learning ⏳ cadré (~3j). Suggestion
proactive ⏳ après macro learning.

### Principe 6 — Transparent sur son raisonnement

Pour conserver la confiance et permettre l'audit :

- Chain-badges visibles dans les storymaps publiées (provenance des chiffres)
- Tool calls visibles dans le chat avec args et résultats (foldés mais accessibles)
- Sources catalogue citées (URL data.gouv, endpoint IGN, etc.)
- `treatments.jsonl` consultable via API par session ou par étude
- Copy button sur blocs de code et résultats (⏳ à ajouter)

État : ✅ majoritairement codé. Reste : copy button sources cliquables.

### Principe 7 — Permission scope par contexte

L'agent n'a pas les mêmes droits selon où il vit. La catégorisation est
**implicite au contexte**, pas une étiquette à choisir :

| Phase du cycle | Mémoire visible | Tools autorisés |
|---|---|---|
| Exploration (pas d'étude) | L3 user, L4 système | Lecture seule |
| Production (étude, propriétaire) | L1+L2+L3+L4 étude | Tous (mutants OK) |
| Diffusion (publi, lecteur tiers) | L4 étude seule (pas L3 user) | Restreints (lecture, search docs, replay recipe) |
| Capitalisation (création template) | Tout du producteur | Édition métadonnées + tools_allowed |

État : Production ✅. Exploration ⚠ pas isolée (même agent qu'en
Production). Diffusion ❌ pas encore implémentée (= "catégorie C").
Capitalisation ⏳ AgentTemplate Phase 10.

### Principe 8 — Transmission par bundle, pas par documentation

L'expertise se transmet en **paquets exécutables** (AgentTemplate), pas en docs
PDF. Le bundle contient :

```
AgentTemplate
 ├── persona.md            (rôle, ton, contraintes métier)
 ├── system_prompt.md      (consignes spécifiques)
 ├── docs/                 (RAG attaché — peut pointer vers docs étude)
 ├── recettes/             (workflows pré-validés réutilisables)
 ├── insights/             (mémoire pédagogique PROPRE à ce template)
 ├── tools_allowed.json    (whitelist MCP)
 └── meta.json             (render_phase: exploration|production|diffusion|capitalisation)
```

Lifecycle :
1. Créé depuis une étude par un cartographe
2. Utilisé localement comme profil agent dynamique
3. Publié comme livrable : URL `/published/{user}/agent/{slug}/`
4. Consommé par un tiers (DREAL, journaliste, citoyen…)

État : ⏳ Phase 10 cadrée non codée (~5-7j). Aujourd'hui : 8 profils statiques
en YAML dans `hub/hub/profiles/`. Migration prévue : ces 8 profils deviennent
des AgentTemplates système livrés par défaut.

### Principe 9 — Typologie fichiers explicite (3 familles)

L'agent traite distinctement trois familles de fichiers, chacune avec son
lifecycle, ses tools, ses permissions et son rôle dans le cycle d'usage :

| Famille | Direction | Exemples | Tools agent | Stockage | Indexé RAG ? |
|---|---|---|---|---|---|
| **A. Données spatiales** | Entrant → QGIS | .tif, .gpkg, .csv | add_layer, run_processing, smart_load | `data/` | ❌ |
| **B. Connaissance** | Contextuel → agent | .pdf, .docx, .md, notes | search_uploaded_docs, read_uploaded_doc | `docs/` | ✅ (L4) |
| **C. Livrables** | Sortant → public | storymap, recette, AgentTemplate | publish, embed_agent | `exports/` + S3 | docs intégrés ✅ |

Chaque référence fichier porte un `kind` explicite. Cela garantit :
- Tools adéquats par famille (pas de `add_layer` sur un PDF)
- Permissions correctes selon phase du cycle (cf. §11 table permissions)
- Lifecycle approprié (adoption / indexation / publication)
- UI claire (un onglet par famille dans panel Ressources)
- ZIP export propre (seules les bonnes catégories incluses)

État : ⏳ cadré, partiellement implémenté. Famille A : tools ✅, panel UI ✅.
Famille B : tools ⏳ Phase 13+. Famille C : tools partiels (publish existe),
embed_agent ⏳ Phase 10. Détail complet : voir §11 Fichiers.

## 4. Architecture mémoire — 4 couches

| Couche | Stockage | Portée | Persistant après rollback ? |
|---|---|---|---|
| **L1** Conversation | SQLite `messages` | Turn courant | ❌ tronqué par rollback (par design) |
| **L2** Étude active | `project_state` + `treatments.jsonl` | Étude X, tous agents de cette étude | ⚠ projet QGIS restauré, treatments append-only conserve trace + event `kind=rollback` |
| **L3** User permanent | SQLite `user_memory` + `agent_insights` | Toutes études de cet user | ✅ jamais perdu (mémoire pédagogique transverse) |
| **L4** Docs étude (RAG) | SQLite-vec (⏳) | Étude X, tous agents de cette étude | ✅ docs immutables |
| **L4'** Insights AgentTemplate | À spécifier (Phase 10) | Cet agent uniquement | ✅ |

Règle : un rollback affecte L1 (truncate) et L2 (.qgz restore). Il **préserve**
L3 et L4. C'est délibéré — la mémoire pédagogique doit transcender les essais
ratés.

## 5. AgentTemplate — le pivot de Phase 10

Format détaillé (voir Principe 8 pour la structure). Trois usages :

1. **Profil dynamique local** : l'AgentTemplate s'active comme "mode" de l'agent
   pour la session courante. Le routeur contextuel le sélectionne automatiquement.

2. **Bundle exporté** : zip de l'AgentTemplate, partageable hors plateforme
   (clé USB, mail).

3. **Publication HTML** : URL `/published/{user}/agent/{slug}/` qui sert un HTML
   avec iframe chat figée. Le lecteur tiers interagit avec cet agent.

**Sécurité (Diffusion / agent publié) :**
- Lecteur anonymisé (pas de L3 du producteur exposée)
- L4 docs de l'étude accessible (= les sources citées)
- Tools restreints par `tools_allowed` (typiquement : search_docs, query_data, replay_recipe)
- Pas de mutating tools
- Pas de chain L2 vers le projet QGIS vivant du producteur

**Dépendances infra possibles d'un AgentTemplate :**

Un template peut déclarer des dépendances d'infrastructure qui doivent être
satisfaites pour qu'il soit utilisable. Au démarrage, le hub vérifie / réveille
ces dépendances.

| Dépendance | Quand | Exemple template |
|---|---|---|
| Pod GPU GeoAI (SAM2/SAM3/DeepForest/OmniWater) | Tools `detect_*`, `segment_*` | `geoai_analyst` |
| Pod workspace QGIS Desktop scale > 0 | Tools mutating MCP | tous templates Production |
| Catalogue data.gouv CEREMA en cache | Tools search_datasets | `risk_analyst`, `db_analyst` |
| Vector store SQLite-vec initialisé | RAG L4 | tout template avec docs RAG |
| n8n bridge (si workflow externe) | Tools webhook | template "automation" futur |

État : ⏳ Phase 10 — modèle `meta.json` du template inclut `requires[]` ;
le hub bootstrap au besoin (cf. `_bootstrap_geoai_gpu`).

## 6. Audit & rollback — règles dures + tests

| Règle | Tests d'invariant | Statut |
|---|---|---|
| Chain-badges DSFR depuis tap MCP réel | `hub/tests/test_storymap_audit.py` (5 tests) | ✅ |
| `read_treatments` filtres cohérents | `hub/tests/test_audit_trail.py` (7 tests) | ✅ |
| `truncate_messages_after` cohérence | `agent/tests/test_checkpoints.py` (6 tests) | ✅ |
| `_log_audit_event_on_pod` non silencieux | (log warning explicit type) | ✅ |
| `purge-checkpoint-files` filtre path traversal | (whitelist `.checkpoints/`) | ✅ |
| Anti-hallucination géo en tête du prompt | (audit prompt + KB tips) | ✅ |

**Tests d'invariant : 18 (5+7+6).** Tout PR touchant à `storymap_dsfr.py`,
`audit_trail.py`, `memory.py`, `qgis_agent.py`, `studies.py` doit les faire
passer.

Le repo `qgis-mcp-portal` (autre repo) couvre 64 tests `api/` historiques.
Mention pour info, pas obligatoires pour ce repo. À fusionner si décision
mono-repo (#11) prise.

**Pipeline CI/CD** :
- Push sur main → GitHub Actions build & push `ghcr.io/nic01asfr/qgis-{agent,hub}:latest`
- Workflow `build.yml` build en ~2 min par image
- `imagePullPolicy: Always` côté K8s → restart pod = re-pull la nouvelle image
- Patterns CI durs : free-disk-space oui, **pas** cache-from (double l'empreinte),
  `set -eu` sans pipefail (SIGPIPE head→141)

## 7. Roadmap d'évolution

### Done (production)
- Mémoire 3 couches (L1/L2/L3)
- Étude bundle autoportant (Phases 12-13)
- Stop / Resume / Rollback (Commits A/B/C)
- Audit honnête + tests d'invariant
- Refonte UX desk (panel Ressources, wake feedback, auto-activate étude)
- 8 profils statiques (à migrer en AgentTemplates système)

### En cours de cadrage
- Routeur contextuel + chips de bandeau (~2-3j, Phase 9.5)
- Étape 4 : data.gouv MCP + spé CEREMA (~2j)
- Décision mono-repo : `qgis-sspcloud` absorbe workspace + portail admin ? (cf. décision §9 #11)

### Court terme (~2-3 semaines)
- **Phase 10 — AgentTemplate** (5-7j) : pivot structurant
  - Format `meta.json` + `tools_allowed` + `requires[]`
  - Migration 8 profils statiques → templates système
  - UI création/édition depuis l'étude
- **Phase 11 — Macro learning** (3j) : fondation prête (checkpoints + tool_calls_made)
- **Phase 13+ — Document RAG L4** (4-5j) : SQLite-vec + 3 tools MCP

### Moyen terme (~1-2 semaines)
- **Phase Fichiers — Upload natif & classification famille A/B** (1-2j)
  - POST `/studies/{sid}/upload` (multipart) côté hub
  - Drag-drop sur zone chat + bouton 📤 panel Ressources
  - Auto-classification (extension + MIME) → kind + famille
  - Adoption auto via add_layer (A) ou attach_doc (B placeholder)
- **Diffusion vivante** : agent embed dans publi HTML (3-4j)
  - URL `/published/{user}/agent/{slug}/`
  - Iframe figée + L4 docs sans L3 user + tools restreints
  - `embed_agent_in_publication(slug, template_id)`
- **Collaboration jetons** : session/étude + ACL + expiration (2j)
- **GeoAI fully bundled** : SAM2+DeepForest bundlés (✅), SAM3 runtime via HF_TOKEN (opt-in user Vault), OmniWater à retravailler upstream
- **Pod GPU lazy init** (chip bandeau cliquable) : code en place, à durcir UX

### Long terme
- Pont Grist : terrain ↔ gestion (Phase 14, variable)
- Pod GPU init par défaut au premier déploiement (vision)
- 3 catégories agents (A/B/C) → reformulés en 4 phases du cycle (cf. §2)
- Catalogue data.gouv CEREMA en cache permanent (spé organisations/cerema/datasets)

## 8. Invariants permanents

À respecter dans tout nouveau code, sans exception :

1. **Tout en français** : code, logs, UI, commentaires, commit messages
2. **Pas d'emojis dans le code** (UI labels OK, code source non)
3. **Conventions strictes** : pas d'ad-hoc, suivre les patterns existants
4. **Anti-hallucination géo** : règle en tête du prompt, jamais d'URL inventée
5. **Audit trail honnête** : chain-badges depuis MCP réel uniquement
6. **Pod persistence** : pip+env dans `setup.py` + `kubectl set env`, jamais shell
7. **Clés LLM via Vault** : lecture à la volée, jamais persistées en clair
8. **Tests d'invariant** : garder les 18 tests verts à chaque commit

## 9. Décisions architecturales structurantes

| # | Décision | Justification |
|---|---|---|
| 1 | Étude comme unité de cohérence (pas le projet QGIS seul) | Permet bundle, partage, capitalisation, archivage |
| 2 | Mémoire 4 couches au lieu d'une seule | Sépare conversation / étude / user / docs — chaque couche a son lifecycle |
| 3 | Snapshot bloquant avant mutating | MVP simplicité ; v2 async optimisable plus tard si besoin |
| 4 | Auto-purge 20 checkpoints / 7j | Cap PVC raisonnable, ajustable via env |
| 5 | ZIP étude n'inclut PAS `.checkpoints/` par défaut | Poids; option future "export historique complet" |
| 6 | Stop laisse finir tool en cours + fallback rollback si timeout | Cohérence données + filet de sécurité |
| 7 | Insights L3 préservés après rollback | Mémoire pédagogique transverse, indépendante des essais |
| 8 | Profils → AgentTemplates système (pas combobox) | Routeur contextuel = mode natif, override = exception |
| 9 | Agent publié anonymise le lecteur | Anti-fuite données privées du producteur |
| 10 | Macro learning bâti sur checkpoints existants | Réutilise l'infra Rollback, pas de nouveau journal |
| 11 | **Consolidation mono-repo `qgis-sspcloud`** (à trancher) | Workspace + portail admin actuellement dans `Passerelle/examples/qgis-mcp-portal`. Critères : maintenabilité, CI/CD unifié, simplification déploiement. À décider AVANT Phase 10 (touche plusieurs sous-systèmes). |
| 12 | **Typologie 3 familles A/B/C des fichiers** | Données entrantes (QGIS) / Connaissance contextuelle (RAG) / Livrables sortants (publiables). Tools, permissions, lifecycle et UI distincts par famille. Cf. §11 et Principe 9. Évite confusion "tout est juste un fichier", garantit sandboxing cat C. |

## 11. Fichiers — typologie 3 familles + transverses

Cette section consolide le traitement des fichiers, axe transversal aussi
structurant que la mémoire ou l'audit. Cf. Principe 9.

### 11.1 — Les 3 familles

```
┌─────────────────────────────────────────────────────────────────────┐
│ FAMILLE A — DONNÉES                  (Entrant — consommé par QGIS)  │
│ raster, vector, tabular                                              │
│ → manipulées comme couches dans le projet QGIS                       │
│ → adoptées dans studies/{sid}/data/ pour autoporter l'étude          │
│ → tracées dans treatments.jsonl (chain-badges)                       │
└─────────────────────────────────────────────────────────────────────┘
┌─────────────────────────────────────────────────────────────────────┐
│ FAMILLE B — CONNAISSANCE         (Contextuel — consommé par l'agent)│
│ document (PDF, DOCX, MD), notes                                      │
│ → indexées dans vector store L4 (SQLite-vec)                         │
│ → l'agent y fait référence dans ses réponses ("selon le rapport X")  │
│ → stockées dans studies/{sid}/docs/                                  │
└─────────────────────────────────────────────────────────────────────┘
┌─────────────────────────────────────────────────────────────────────┐
│ FAMILLE C — LIVRABLES                  (Sortant — publié au monde)  │
│ storymap, export PDF, dataset filtré, recette, AgentTemplate, …     │
│ → produits du cycle (Production / Capitalisation)                    │
│ → URL canonique /published/{user}/{kind}/{slug}/ + stockage S3      │
│ → lifecycle : draft → publié → archivé → unpublished                 │
│ → agent C embarqué possible (storymap interactive, agent guichet…)   │
└─────────────────────────────────────────────────────────────────────┘
```

### 11.2 — Typologie complète

| Famille | Kind | Stockage | Tools MCP | RAG ? | ZIP étude | Publi S3 |
|---|---|---|---|---|---|---|
| **A** | raster | `data/` | add_layer, run_processing | ❌ | ✅ | ❌ |
| **A** | vector | `data/` | add_layer, run_processing | ❌ | ✅ | ❌ |
| **A** | tabular | `data/` | add_layer, query_resource | ❌ | ✅ | ❌ |
| **B** | document | `docs/` | search_uploaded_docs, read_uploaded_doc | ✅ | ✅ option | dans publi RAG |
| **B** | notes | `notes.md` | read_study_notes | ✅ | ✅ | ❌ |
| **C** | storymap | `exports/` + S3 | publish, embed_agent | ❌ | ✅ `publications/` | ✅ |
| **C** | pdf | `exports/` + S3 | publish | ❌ | ✅ | ✅ |
| **C** | flux | `exports/` + S3 | publish | ❌ | ✅ | ✅ |
| **C** | dataset | `exports/` + S3 | publish | ❌ | ✅ | ✅ |
| **C** | recipe | `recipes/` + S3 | publish, run_recipe | ❌ | ✅ | ✅ |
| **C** | agent | `agents/{tpl}/` + S3 | publish_template, embed_agent | docs intégrés ✅ | ✅ | ✅ |
| **C** | capture | `exports/` | save_capture | ❌ | ✅ | option |
| **C** | bundle | (généré à la demande) | export_study_zip | ❌ | (c'est lui) | option |
| trans | upload | `uploads/` | upload_file, move_file | ❌ | ❌ par défaut | ❌ |
| trans | cache | `/data/cache/` | smart_load | ❌ | ❌ jamais | ❌ |

### 11.3 — Permissions par famille × phase du cycle

| Famille | Exploration | Production | Diffusion (cat C) | Capitalisation |
|---|---|---|---|---|
| A. Données | ❌ pas d'étude | ✅ tout | ❌ pas exposé | ✅ |
| B. Connaissance (docs) | partiel (sys) | ✅ tout | ✅ **lecture** (sources citées) | ✅ |
| C. Livrables produits | ❌ | ✅ | ✅ download autorisé | ✅ |
| C. Recettes | partiel | ✅ | ✅ replay si tools_allowed | ✅ |
| C. AgentTemplates | ❌ | ✅ | ❌ (sauf publi indépendante) | ✅ |
| transverse Uploads | propres | ✅ tout étude | ❌ jamais exposé | ✅ |

### 11.4 — Tools MCP dédiés par famille

```python
# Famille A — Données spatiales
add_layer(uri, layer_type, name)
add_from_file(path)              # auto-classify A
run_processing(algo, params)
smart_load(catalog_id, bbox)

# Famille B — Connaissance  (Phase 13+)
search_uploaded_docs(query, sid?)
read_uploaded_doc(path, section?)
list_uploaded_docs(sid?)
attach_doc_to_study(path)

# Famille C — Livrables  (partiel, Phase 10)
publish(slug, kind, source_path)
unpublish(slug)
list_publications(user?, kind?, status?)
republish(slug, source_path)
embed_agent_in_publication(slug, template_id)
publish_template(tpl_id)

# Communs (transversaux)
list_files(directory?, kind?)
upload_file(name, url?)
download_file(path)
delete_file(path)
move_file(src, dst)               # adoption uploads → data ou docs
```

### 11.5 — Panel Ressources cible (5 onglets)

```
Drawer "📁 Ressources de l'étude"
 ├── 🗺 Couches           ← Famille A (data/)              ✅ existe
 ├── 📖 Docs              ← Famille B (docs/ + notes)      ⏳ Phase 13+
 ├── 📚 Livrables          ← Famille C (exports + publi)   partiel
 │    └── filtres : 📖 Storymap · 📄 PDF · 📊 Dataset · 📋 Recette · 🤖 Agent
 ├── 📁 Fichiers           ← Transverses (uploads)         ✅ existe
 └── 🤖 Templates          ← Famille C sous-set (AgentTemplates dispo) ⏳ Phase 10
```

### 11.6 — Lifecycle distinct par famille

| Famille | Auto-purge | Adoption / Indexation | Inclus ZIP | Publi S3 |
|---|---|---|---|---|
| Cache WFS/WMS | > 30j | — | ❌ | ❌ |
| Uploads transients | > 7j si non adopté | → data/ ou docs/ | ❌ par défaut | ❌ |
| A. Données (`data/`) | Jamais | adopt via add_layer | ✅ | ❌ |
| B. Docs (`docs/`) | Jamais | indexé RAG L4 | ✅ option | ✅ accessible RAG |
| C. Exports/Livrables | Jamais | published → S3 | ✅ | ✅ |
| C. Recettes | Jamais | publié → S3 | ✅ | ✅ replay |
| C. AgentTemplates | Jamais | publié indépendamment | ✅ | ✅ |

### 11.7 — Workflows métier types

**Upload PCRS dalle locale** :
1. User drag-drop `.tif` dans le chat (ou bouton 📤)
2. Hub `POST /studies/{sid}/upload` → stocke dans `uploads/`
3. Auto-classification : raster → Famille A
4. Agent propose : *"Tu as téléversé X.tif (250 Mo, raster). Je l'ajoute au projet ?"*
5. `add_layer` adopte le fichier dans `data/`
6. Si plugin pcrs_detect actif : bascule auto sur "Dalles locales"

**Document métier** :
1. User drag-drop `.pdf` dans le chat
2. Auto-classification : document → Famille B
3. Agent propose : *"Ajouter aux documents indexés de l'étude ?"*
4. `attach_doc_to_study` déplace vers `docs/` + déclenche indexation RAG
5. L'agent peut désormais citer le document dans ses réponses

**Livrable storymap + agent embarqué** :
1. Production aboutie, treatments.jsonl rempli
2. User : *"publie cette storymap"* → agent génère HTML DSFR
3. `publish(slug="risque-marseille", kind="storymap", source=...)` → S3
4. Agent propose : *"Embarquer un agent pour répondre aux questions des lecteurs ?"*
5. `embed_agent_in_publication(slug, template_id=...)` → iframe agent figée
6. URL `/published/{user}/storymap/risque-marseille/` interactive

## 10. Pour relire cette charte avant chaque décision

Devant un choix technique, poser ces 5 questions :

1. **Quel principe (1-8) cette décision touche ?**
2. **Est-ce que ça renforce ou affaiblit l'auditabilité (Principe 2) ?**
3. **L'utilisateur peut-il revenir en arrière (Principe 4) ?**
4. **Est-ce que ça s'inscrit dans le cycle 4 phases ou ça crée une voie parallèle ?**
5. **Une fois codé, est-ce que ça serait extractible en AgentTemplate (Principe 8) ?**

Si une décision affaiblit l'auditabilité, casse le rollback, ou crée une voie
parallèle au cycle 4 phases, il faut **soit changer l'approche, soit le faire
en conscience et documenter pourquoi**.

---

**Cette charte est évolutive.** Quand une brique passe de ⏳ à ✅, mettre à
jour le statut. Quand une décision architecturale nouvelle est prise, l'ajouter
à la section 9.

## Annexe — Items connus hors charte directe

Ces notes mémoire sont conservées comme références techniques transversales,
sans place explicit dans la charte (pas de principe / décision à en tirer) :

- `reference_sse_buffering` — chunks SSE >64 KB côté JS, buffer `\n\n` obligatoire
- `reference_sspcloud_rbac_namespace` — pas de Role custom dans namespace user OIDC
- `reference_github_actions_ci_disk_space` — patterns CI Docker volumineux
- `reference_n8n_rest_api`, `reference_n8n_mcp_ssrf_k8s` — intégrations n8n (projets connexes)
- `project_passerelle_client`, `project_compute_v2_validated` — autres repos
  (Passerelle binaire Go, compute distribué)

Dernière mise à jour : 2026-05-24 (v3) — ajouts : §11 Fichiers (typologie 3
familles A/B/C entrant/contextuel/sortant), Principe 9 typologie fichiers
explicite, décision #12, roadmap Phase Fichiers Upload natif, panel
Ressources 5 onglets cible (Couches + Docs + Livrables + Fichiers +
Templates).

Prochaine étape recommandée : **routeur contextuel** (~30-45 min restantes)
puis **Phase Fichiers Upload natif** (1-2j) puis **Phase 10 AgentTemplate**
(5-7j).
