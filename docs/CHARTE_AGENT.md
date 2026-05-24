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

### Principe 1 — Contextualisation native

L'agent se positionne lui-même selon ce que l'utilisateur fait. Pas de combobox
de profil à remplir.

| Contexte de navigation | Mode agent |
|---|---|
| Pas d'étude active | Mode découverte / exploration |
| Étude active, vue desk générale | Profil métier de l'étude (risk, db, geoai…) |
| Étude + draft storymap sélectionnée | Édition storymap |
| Étude + recette ouverte | Auteur recette |
| Intent "crée storymap" détecté dans chat | Bascule édition storymap |

UI : un **chip de contexte** lit le mode actif. Pas de combobox. Override
manuel ponctuel possible si nécessaire (mais c'est l'exception).

État : ⏳ cadré. Aujourd'hui combobox 8 profils statiques + sentinel
`.active_study`. À faire : routeur contextuel + détection intent
+ remplacer combobox par chip.

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
- Résolution INSEE directe pour arrondissements (Paris/Marseille/Lyon)

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

## 6. Audit & rollback — règles dures

| Règle | Tests d'invariant | Statut |
|---|---|---|
| Chain-badges DSFR depuis tap MCP réel | `test_storymap_audit.py` (5 tests) | ✅ |
| `read_treatments` filtres cohérents | `test_audit_trail.py` (7 tests) | ✅ |
| `truncate_messages_after` cohérence | `test_checkpoints.py` (6 tests) | ✅ |
| `_log_audit_event_on_pod` non silencieux | (log warning explicit type) | ✅ |
| `purge-checkpoint-files` filtre path traversal | (whitelist `.checkpoints/`) | ✅ |
| Anti-hallucination géo en tête du prompt | (audit prompt + KB tips) | ✅ |

Tout PR touchant à `storymap_dsfr.py`, `audit_trail.py`, `qgis_agent.py` doit
faire passer ces 18 tests.

## 7. Roadmap d'évolution

### Done (production)
- Mémoire 3 couches (L1/L2/L3)
- Étude bundle autoportant (Phases 12-13)
- Stop / Resume / Rollback (Commits A/B/C)
- Audit honnête + tests d'invariant
- Refonte UX desk (panel Ressources, wake feedback, auto-activate étude)
- 8 profils statiques (à migrer en AgentTemplates système)

### En cours de cadrage
- Routeur contextuel + chip lieu de combobox (~2-3j, Phase 9.5)
- Étape 4 : data.gouv MCP + spé CEREMA (~2j)

### Court terme (~2-3 semaines)
- **Phase 10 — AgentTemplate** (5-7j) : pivot structurant
- **Phase 11 — Macro learning** (3j) : fondation prête (checkpoints)
- **Phase 13+ — Document RAG L4** (4-5j)

### Moyen terme (~1-2 semaines)
- Diffusion vivante : agent embed dans publi HTML (3-4j)
- Collaboration jetons : session/étude + ACL (2j)

### Long terme
- Pont Grist : terrain ↔ gestion (Phase 14, variable)
- Pod GPU init par défaut (vision)

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

Dernière mise à jour : 2026-05-24 — phase Stop/Rollback livrée et validée en
réel + bonus UX placeholder. Prochaine étape recommandée : routeur contextuel
puis Phase 10 AgentTemplate.
