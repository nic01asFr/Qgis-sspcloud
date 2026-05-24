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

## 3. Les 8 principes de fonctionnement de l'agent

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
- **Diffusion vivante** : agent embed dans publi HTML (3-4j)
  - URL `/published/{user}/agent/{slug}/`
  - Iframe figée + L4 docs sans L3 user + tools restreints
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

Dernière mise à jour : 2026-05-24 (v2) — ajouts : multimodal input + chip GPU
(§3 Principe 1), dépendances infra AgentTemplate (§5), GeoAI bundle éclaté
(§7 moyen terme), décision #11 mono-repo (§9), annexe items hors charte.

Prochaine étape recommandée : **routeur contextuel** (~2-3j) puis
**Phase 10 AgentTemplate** (5-7j).
