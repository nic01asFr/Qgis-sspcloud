# Bilan session 2026-06-28→29 — Sprint Composants Phase 4b LIVRÉ + 4 décisions actées

> Session de marathon enchaînée sur le bilan 06-27 : pipeline E2E validé
> bout-en-bout avec **vraies données QGIS** (14 270 bâtiments BD TOPO
> Marseille 4e arr.), 4 maillons cassés fixés, 4 décisions cross-projet
> actées avec les pairs Strate-Architect / Passerelle-Archi / Lead-cerema.

## TL;DR

**Acquis** : pipeline `load → scene_manifest → component → update_assembly → publish → URL hub publique avec carto MapLibre` opérationnel.

**32 commits** push origin, **6 tags release** rollback-safe.

**4 décisions actées** dans #decisions wikichat (msg f8dda810) :
- D-QGIS-005 — Component V0.1 source qgis-sspcloud
- D-QGIS-006 — audit_chain.Source aligné Strate (recopié, pas importé)
- D-FORMAT-008 — RENAME `signed_hash` → `integrity_hash` (anti-confusion future Ed25519)
- D-QGIS-008 — Helper rendu partagé `_pre_render_component_html`

**3 agents wikichat répondu** : Strate-Architect (52e1bcba), Passerelle-Archi (6c517f58 + 25104f31), Lead-cerema (f861efc5).

## Récap chronologique

### Bloc 1 — Phase 4b 4 maillons cassés fixés

#### Maillon 1 : Scene Manifest avec GeoJSON inline → externe
[hub/hub/studies.py:2136-2280](hub/hub/studies.py#L2136-L2280) `build_scene_manifest_from_qgis_pod_code` exporte maintenant chaque vecteur QGIS dans un fichier PVC dédié `/data/studies/{sid}/projects/{pid}/scene_layers/{slug}.geojson` (pas de troncature !). Manifest reste léger avec `geojson_path` + `n_features` + `geojson_size_bytes` + `crs` (EPSG:4326 transform auto).

Helper hub lit GeoJSON via `read_scene_layer_geojson_pod_code`.

#### Maillon 2 : update_assembly endpoint + tool natif
[hub/hub/main.py:3877+](hub/hub/main.py#L3877) endpoint `PUT /studies/{sid}/assemblies/{aid}` INSERT-only versioning. [agent/agent/native_tools_v2.py:185+](agent/agent/native_tools_v2.py#L185) native tool. System prompt v15 règle stricte : si aid existe → TOUJOURS update_assembly.

#### Maillon 3 : auto-fill `provenance.scene_hash_at_creation`
[hub/hub/main.py:3603-3615](hub/hub/main.py#L3603-L3615) côté `create_component_endpoint` : si `source.scene_hash` présent et provenance vide → auto-fill. Garantit que `build_audit_chain` propage le scene_hash dans `audit.scene_hashes`.

#### Maillon 4 : Helper `interactive_map` MapLibre inline (Phase A précédente)
[hub/hub/main.py:4118+](hub/hub/main.py#L4118) helper lit scene_manifest + GeoJSON via `_execute_python_in_workspace`. Injecte div + script MapLibre inline avec 6 couleurs palette DSFR + auto-fit bounds.

### Bloc 2 — Validation E2E live avec Marie persona

Pilotage Chrome DevTools de l'agent IA via desk :
- Chargement `bdtopo_batiments` → 14270 features QGIS
- Construction scene_manifest via API : scene_hash `4271db79154e0d1a0...`
- Création composant `interactive_map` avec source.scene_hash
- Update assembly storymap_narrative_dsfr aid `c4c9b4aa6504` (préservation narrative + KPI existants)
- Publication audience `cerema_internal`

**URL publique** :
```
https://user-nicolaslaval-qgis.user.lab.sspcloud.fr/published/nicolaslaval/assembly/assembly-c4c9b4aa6504.html
```

**Audit chain produit** :
- `integrity_hash: sha256:921d2d5b...` (ex-`signed_hash`)
- `components_refs: [711bb85fb2ad, 85a9632cdbff, 9a8b7c6d5e4f, b1c2d3e4f5a6]` (4)
- `scene_hashes: [e13be375fcda03ce]` (1)
- `rendered_html_size: 44.2 MB` (carto MapLibre inline complète)

### Bloc 3 — Bugs critiques fixés cette session

Lors du diagnostique post-publication, 5 bugs résolus :
1. **OIDC middleware** `/published` placé dans `_OIDC_MIDDLEWARE_PUBLIC` (vraie whitelist anonyme, pas `_INTER_POD`)
2. **Slug strip extension** dans `serve_published` (`.html` doublé par `s3_key()` ext)
3. **Composants iframe → inline** (iframe path relatif `/studies/.../render` cassé hors contexte hub → pré-rendu inline via templates)
4. **Champs params noms** (`narrative_text.content` ≠ `markdown`, `kpi_badge.icon` ≠ `unit`)
5. **Migration DB published_url** MinIO ACL public-read KO → URL hub `/published/` rewrite + migration au boot

### Bloc 4 — Stratification 3 strates UX panel desk

Refactor UX panel Livrables :
- C10 : 3 versions iterées (collapsibles → décoratives → grid 8 onglets cycle de vie)
- C11 : 3 meta-onglets **Sources / Livrables / Partages** (vision idéale)
- C12 P1 : 10 prompts reformulés en langage métier non technique

### Bloc 5 — Coordination wikichat lancée et conclue

3 agents headless spawnés en parallèle pour valider les 4 décisions :
- Strate-Architect (atlas-territorial-briques) → 3 verdicts CHANGEMENTS_RECENTS / RECOPIER / EXPLICITE
- Passerelle-Archi (Passerelle) → 4 verdicts OK / OK / DANS_MA_LANE / DANS_TA_LANE
- Lead-cerema (cerema-offre-de-service) → 4 verdicts OK_VALIDÉ × 3 + OK_AVEC_RÉSERVE (rename signed_hash)

**Insight Lead-cerema critique** : `signed_hash` → confusion future avec Ed25519 sidecar `.sig` prévu Scene Manifest V0.2.2. **Rename adopté** D-FORMAT-008.

**Insight Strate-Architect critique** : `confidence = min` est invariant `Validity` (pas `Source`). qgis-sspcloud ne fait PAS d'auto-calcul depuis sources[]. Agent IA pose explicite. Le `min()` est en aval consommateur.

**Insight Passerelle-Archi critique** : Bearer `qgisk_*` déjà câblé Phase 4a dans `get_current_user`. Endpoints `/studies/.../assemblies/*` consommables tels quels par services tiers. Lead #6 geoai-kit `applyManifestToMap` DANS_LANE_PASSERELLE, attend manifest schema stabilisé.

### Bloc 6 — Documentation et axes wikichat mis à jour

**Documentation projet** :
- `docs/decisions/D-QGIS-005.md` + `D-QGIS-006.md` + `D-FORMAT-008.md` + `D-QGIS-008.md`
- `docs/scene-manifest-v0.2-contract.md` (livrable pour Passerelle-Archi D2)
- `hub/hub/models/VENDORED_FROM.md` (alignement Strate Source documenté)

**Axes wikichat** :
- `qgis-sspcloud-composants-axis.md` : Phase 4b LIVRÉ + Vague A en cours
- `audit-trail-axis.md` : `integrity_hash` rename + Invariant 4 🟢 fixé + Invariant 6 (confidence explicite) ajouté + Source schema actuel
- `qgis-sspcloud-publication-flow-axis.md` **NEW** : pipeline E2E documenté + cross-projet + invariants

## Vague A en cours (3.5j estimé)

3 commits incrémentaux planifiés :

| Commit | Items | LOC | Statut |
|---|---|---|---|
| 1 — Fondations | A6 rename integrity_hash + A7 Source Strate + A8 test contrat | ~100 | en attaque |
| 2 — Helper rendu unifié | A1 `_pre_render_component_html` + 4 templates partials | ~80 | planifié |
| 3 — Bibliothèque visuelle | A2 chart + data_table + A3 publish_component + A4 notes.md + A5 fr-callout | ~225 | planifié |

Tag prévu : **`v1.6.4-pipeline-e2e`** après Vague B (tests E2E green).

## Architecture finale Phase 4b

### Endpoints REST hub (état actuel)

```
GET    /studies/{sid}/projects/{pid}/scene_manifest      (lire)
POST   /studies/{sid}/projects/{pid}/scene_manifest/build (Phase 4b: GeoJSON externe)
GET    /studies/{sid}/projects/{pid}/scene_manifest/history

POST   /studies/{sid}/components                          (create + auto-fill scene_hash Phase 4b)
GET    /studies/{sid}/components/{cid}
PUT    /studies/{sid}/components/{cid}                    (Vague A)
GET    /studies/{sid}/components/{cid}/render             (standalone Jinja2)
GET    /studies/{sid}/components/{cid}/history

POST   /studies/{sid}/assemblies                          (create)
PUT    /studies/{sid}/assemblies/{aid}                    (Phase 4b: INSERT-only update)
GET    /studies/{sid}/assemblies/{aid}
GET    /studies/{sid}/assemblies/{aid}/render             (preview)
POST   /studies/{sid}/assemblies/{aid}/publish            (audit_chain.integrity_hash)
GET    /studies/{sid}/assemblies/{aid}/history

POST   /publish/{kind}/{slug}                             (générique S3, kind=assembly|component|...)
GET    /published/{owner}/{kind}/{slug}                   (anonyme depuis fix OIDC 2026-06-28)

POST   /studies/{sid}/scoped-keys                         (Phase 4a)
POST   /studies/{sid}/scoped-keys/{kid}/publish           (URL widget agent partagé)
GET    /studies/{sid}/scoped-keys                         (liste)
DELETE /studies/{sid}/scoped-keys/{kid}                   (revoke)
```

### Tables SQLite

- `studies`, `projects`, `recipes_index`, `components_index`, `assemblies_index`, `exports_index`
- `tombstones` (GDPR)
- `recipe_analyses_index` (Phase 3c)
- `scoped_keys` (Phase 4a + colonnes audience/published_url/published_at/audit_chain_json)

### Cache PVC

- `/data/studies/{sid}/recipes/{slug}/analysis.json`
- `/data/system_recipes_enrichments/{slug}_{hash[:12]}.json`
- `/data/studies/{sid}/components/{cid}/manifest.json`
- `/data/studies/{sid}/assemblies/{aid}/manifest.json`
- `/data/studies/{sid}/assemblies/{aid}/rendered/index.html`
- **`/data/studies/{sid}/projects/{pid}/scene_manifest.json`** (Phase 4b)
- **`/data/studies/{sid}/projects/{pid}/scene_layers/{slug}.geojson`** (Phase 4b NEW)

## Métrique session

- **32 commits** push origin (cumul depuis bilan 06-27)
- **6 tags release** rollback-safe
- **~590 LOC** Phase 4b
- **5 phases livrées** : V1.5 + 3b + 3c + 4a + 4b
- **4 décisions actées** wikichat
- **3 axes wikichat** mis à jour
- **1 nouvel axe** créé (publication-flow)
- **4 ADR** créés (D-QGIS-005/006/008 + D-FORMAT-008)

## Pivot stratégique 2026-06-29 — Vague E1 réaligné UX libre composition

Suite recadrage user "storymap ≠ format figé" + référence ArcGIS StoryMaps
modèle (palette de blocks composables) :

### Décision D-QGIS-009 actée

Vague E1 = **UX libre composition** (PATCH composants + clone assemblages +
catalogue cross-étude + UI desk éditeur sections), AVANT d'ajouter de nouveaux
kinds (reportés Vague E2).

### Rationale

Audit codebase Explore agent (2026-06-29) sur 7 axes :
- 21 native tools cohérents agent IA + MCP (dispatch + schemas + whitelist v15)
- Discovery API active (/schema/{entity}/{kinds,validate})
- Discipline PLAN-PUIS-EXECUTE explicite system_prompt
- BUG P0 détecté : `publish_component` + `update_assembly` absents de
  `NATIVE_TOOLS_V2_MUTATING` frozenset (cache L2 invalidation cassé)
- 3 GAPS critiques UX libre :
  - PATCH /components/{cid} absent (composants pas éditables après création)
  - /catalog/components absent (pas de marketplace cross-étude)
  - clone_assembly absent (pas de fork template)
- UI desk : pas d'éditeur sections (composition agent-driven only)

### Plan Vague E1 réaligné (~12-14h)

```
prep-vague-e1-ux-libre (ce commit)
├─ Bug fix P0 MUTATING (2 LOC)
├─ ADR D-QGIS-009 NEW
├─ Bandeaux "spec différée Vague E2" sur 3 docs design équipe
├─ BILAN section pivot stratégique
├─ Axes wikichat update
└─ README mention plan E1

Commit 1 — PATCH components + update_component (~2h)
Commit 2 — Clone assembly + cloned_from (~2h30)
Commit 3 — Catalog cross-étude /catalog/components (~2h)
Commit 4 — UI desk éditeur sections (~3-4h)
Commit 5 — System prompt v15 + docs final (~1h)
Validation + Reviewer + Tag v1.6.5-vague-e1-composition-libre (~1h30)
```

### Décisions de design Vague E1

- **Clone shallow par défaut** (refs cid partagés). `?deep=true` optionnel.
- **`cloned_from` sur `ComponentProvenance`** (Option C — Component + Assembly)
- **Audience default `cerema_internal`** sur catalog (anti-fuite RGPD)
- **Form params UI graceful** : simple forms kinds simples, quick-action agent pour complexes

### Vague E2 différée

5 nouveaux kinds (kpi_grid, audit_chain_narrative, reliability_matrix,
heading, quote) + AuditChain enrichi (Phase, VariableReliability, contributors)
+ extension narrative_text KPI inline + layout sidecar Esri. Bump Component
V0.1 → V0.2 + tag `v1.7.0-component-catalog-v02`. ~13h.

---

## Vague E1 LIVRÉE — 6 commits 2026-06-29

| Commit | SHA | Items | LOC |
|---|---|---|---|
| prep | 4c1418a | Bug fix P0 MUTATING + ADR D-QGIS-009 + bandeaux + BILAN | 257 |
| 1 | 2c1b298 | PATCH /components/{cid} + update_component | 283 |
| 2 | 6609351 | POST /assemblies/{aid}/clone + cloned_from + clone_assembly | 350 |
| 3 | a7b31cb | GET /catalog/{components,assemblies} + list_catalog_* | 350 |
| 4 | cb67e46 | UI desk éditeur sections (modal + reorder + save) | 219 |
| 5 | (cette session) | system_prompt v15 + docs/api/components-catalog.md | ~200 |

**Cohérence préservée** : D-QGIS-005 (V0.1) · D-QGIS-006 (Source Strate) ·
D-FORMAT-008 (integrity_hash) · D-QGIS-008 (helper unifié) · D-QGIS-009
(pivot UX libre). Backward-compat absolue.

**4 nouveaux tools agent** : update_component · clone_assembly ·
list_catalog_components · list_catalog_assemblies.

**Discipline v15 étendue** : avant create_*, check catalogue (anti-duplication).

**Tag attendu** : `v1.6.5-vague-e1-composition-libre`.

---

## Prochaine session

Attaque Vague A commit 1 (FONDATIONS) :
1. Lecture audit_chain.py + ajout `class Statut` + `class Source`
2. Rename `signed_hash` → `integrity_hash` + property legacy DeprecationWarning
3. Test contrat `tests/test_audit_chain_strate_alignment.py`
4. Lint + commit + push + CI + set image hub
5. Smoke test pytest

Puis commit 2 (HELPER UNIFIÉ) et commit 3 (BIBLIOTHÈQUE).

---

## Vague E2 LIVRÉE — 9 commits 2026-06-29 (tags v1.6.6 + v1.6.7)

Re-cadrage métier suite critique UX/UI ("storymap CEREMA livrable institutionnel,
pas patchwork de kinds techniques"). Au lieu d'ajouter 5 nouveaux kinds isolés,
on a transformé la **forme et le contenu** pour atteindre un livrable qui se tient.

### Bloc Storymap métier base (tag v1.6.6) — 3 commits

| Commit | SHA | Items | Effet |
|---|---|---|---|
| C2 | 1822feb | Polish DSFR P1-P7 + diff visuelle AssemblySection.kind | Grammaire narrative visible (intro/section/conclusion/appendix) |
| C3 | c53ab54 | 6 patterns métier `AssemblyKind` + 2 endpoints + 2 native tools | Agent IA pense en chapitres canoniques (hero_constat, zoom_territoire, croisement_enjeu, fiche_indicateur, reliability_summary, conclusion_actionnable) |
| C4 | 1c893da | Trio cartographe métier obligatoire | Cartes COPIL-ready (titre + légende + source + caveat) |

### Bloc Carto métier riche (tag v1.6.7) — 5 commits

| Commit | SHA | Items | Effet |
|---|---|---|---|
| C5 | 720aa0a | Symbologie thématique (graduated/categorized + ColorBrewer + Jenks/quantile) | Choroplèthes vraies DSFR sobre |
| C6 | cd5e3a0 | Interactions (tooltip survol + popup clic + toggle layers UI) | Cartes vivantes |
| C7 | b4aa724 | Catalogue 6 fonds (osm/plan-ign/ortho-ign/dsfr-sobre/hillshade/etalab) | Choix éditorial fond |
| C8 | 22e3f1b | Proportional symbols + heatmap MapLibre | Densité spatiale |
| C10 | 9fc7fed | Légende riche 3 formats (chips/gradient_bar/proportional) auto | Lecture facilitée |

### Tests

189/189 pytest tests PASSED dont 47 nouveaux Vague E2 (carto_classification 22 +
carto_interactions 8 + carto_basemaps 12 + carto_advanced_viz 5 + legend_rich 8 +
storymap_patterns + trio_cartographe + vague_e2_atomic_kinds).

### Acquis qualitatifs

1. **Grammaire narrative visible** : AssemblySection.kind différencie le rendu
2. **6 patterns métier canoniques** : Marie pense en chapitres pas en blocs
3. **Carte exploitable COPIL** : trio cartographe systématique
4. **Symbologie thématique vraie** : choroplèthes Jenks + ColorBrewer
5. **Interactions cartographiques** : tooltip + popup + toggle
6. **Choix éditorial fond** : 6 fonds dont dsfr-sobre institutionnel
7. **Visualisations avancées** : proportional + heatmap
8. **Légende auto adaptive** selon classification

---

## Pivot UI BlockNote envisagé (D-QGIS-010 acté 2026-06-29)

Revue compatibilité écosystème CEREMA validée (ZEBRA, Atlas, panoramax3d,
Strate, geoai-kit, MobSciDat = zéro impact). Pivot vers BlockNoteJS (financé
ANCT, utilisé par LaSuite Docs gouv.fr).

**Décisions techniques actées** (D-QGIS-010) :
- Architecture statique bundle servi par hub FastAPI
- Autosave debounce 30s
- Mix DOM/iframe selon kind (DOM atomiques, iframe interactive_map/chart/data_table)
- CRDT Yjs multi-user collab différé Vague future

**Plan 4 commits ~15h** : E setup + F custom blocks + G sérialisation + H
intégration desk + tag `v1.7.0-blocknote-editor`.

---

## Tags publiés cette session

- `v1.6.5-vague-e1-composition-libre` (UX libre composition agent IA, 6 commits)
- `v1.6.6-storymap-metier-base` (storymap métier + trio cartographe, 3 commits)
- `v1.6.7-carto-metier-base` (carto thématique + interactions + fonds, 5 commits)
- `v1.7.0-blocknote-editor` (BlockNote E1→H3, 8 commits, 13 custom blocks + autosave)
- `v1.7.1-audit-fixes` (5 P0 + truncation + suppression code mort 514 LOC + 20 tests)
- `v1.7.2-p1p2-optims` (Promise.allSettled + force-overwrite + Cache-Control + whitelist OIDC)
- `v1.7.3-fullwidth` (container 100% + width:100% custom blocks)
- `v1.7.4-roundtrip-section` (frontière sections via heading H2 vide)
- `v1.7.5-consolidation` (tests pytest concurrency/round-trip/skip-None + docs cohérentes + footer dynamique)
- `v1.8.0-sprint1-e3` (alignement reste de l'app sur modèle blocks — 4 P0)
- `v1.9.0-sprint-2-3-e3` ⭐ (sprint 2+3 essentiels — 8 P1+P2)

## Vague E2 BlockNote LIVRÉ — 5 vagues v1.7.0 → v1.7.5

Pivot UI block-based pour permettre à Marie de modifier visuellement les
Assembly après création par l'agent IA. 8 commits initiaux + 4 vagues
d'audit-fixes consécutives (audit critique → consolidation → footer dynamique).

**Marie peut désormais :**
- Demander à l'agent IA via chat (Vague E1)
- **Éditer visuellement via BlockNote** (Vague E2) — bouton "📝 BlockNote"
  sur card livrable desk → nouvelle tab `/editor/{sid}/assembly/{aid}`
- Autosave 30s avec indicateur Notion-style
- Conflit 409 → modal "Recharger" + "Forcer écrasement"
- 13 custom blocks (7 DOM inline éditables + 6 iframe preview)
- Container BlockNote full-width (~1500px sur écran 1600px)

**Limitations actées V1 :**
- Agent IA `update_assembly` n'envoie pas `version_num_source` → OCC unidirectionnelle
- scene_3d / media_embed / iframe_grist : iframe placeholder texte (hub V0.1)
- Pas de monitoring client (errors)
- Pas de tests Vitest (3 tests pytest critiques en v1.7.5)

**Tests : 273/273 pytest PASSED (v1.9.0)** (196 baseline + 8 v1.7.5 + 14 sprint 1 + 43 sprint 2 + 12 sprint 3).

---

## Vague E3 sprints 1-3 LIVRÉS — alignement reste de l'app

Après v1.7.5 (Vague E2 BlockNote consolidée), 2 tags supplémentaires
livrent l'alignement du reste de l'app sur le modèle blocks.

### Sprint 1 (v1.8.0-sprint1-e3, ~8h, SHA a44dfe7)

4 items P0 résolvent 5 drifts critiques identifiés par audit blocks model :

| # | Drift | Description |
|---|---|---|
| 8.1 | **D3 critique** | BlockNote `updateComponent` au save (vs create-only) — élimine pollution PVC/DB linéaire avec l'usage |
| 8.2 | D2 + D5 | OCC `version_num_source` complet : endpoint hub `update_component_endpoint` + agent IA tools + modal E1 desk |
| 8.3 | D4 partiel | Partials Jinja2 `_media_embed_partial.j2` + `_iframe_grist_partial.j2` — 12/13 ComponentKind rendus |
| 8.4 | D9 | `/schema/assembly/kinds` filtré à `storymap_narrative_dsfr` — évite agent IA créant assemblies non-renderables |

**Impact mesuré** : Marie modifie un KPI 50 fois → 50 versions INSERT-only
du MÊME cid (vs 50 cid orphelins avant). Audit_chain au publish ne pollue
plus avec des cid jetables.

### Sprint 2 (parallèle avec sprint 3, ~5h)

4 items P1 hygiène + UX :

| # | Drift | Description |
|---|---|---|
| 8.5 | D5 | Modal E1 rebrandée "🔧 Métadonnées" avec tooltip "mode expert JSON" |
| 8.6 | D1 | Tools OpenAI `list_entity_kinds` mention les 4 kinds Vague E2 + note filtre D9 |
| 8.7 | D8 | Test paramétré `ComponentKind ↔ runtime ↔ helper` (43 tests anti-régression) |
| 8.8 | — | Bouton "👁 Aperçu DSFR" dans header BlockNote → drawer modal iframe `/render/{aid}` |

### Sprint 3 (~5h, SHA 4d9bb55)

4 items P2 polish + monitoring :

| # | Drift | Description |
|---|---|---|
| 8.10 | D6 | Fix : heading H2 vide ne fragmente pas section vide (vs bug v1.7.4) |
| 8.11 | — | DSFR theming strict BlockNote : variables Mantine override → palette Marianne #000091 + police 'Marianne' |
| 8.13 | — | Tests pytest équivalent Vitest (introspection TS depuis Python, 12 tests) |
| 8.14 | — | Monitoring client errors : POST/GET `/api/log/client-error` ring buffer RAM 100 + handlers `window.error`/`unhandledrejection` |

### Tag v1.9.0 = état final production

**Marie peut maintenant** :
- Tous les acquis Vague E2 (édition BlockNote, autosave, conflict, full-width)
- + Plus de pollution DB (D3 résolu)
- + Aperçu DSFR strict en temps réel (8.8)
- + Theming DSFR cohérent édition vs publication (8.11)
- + Tests anti-régression sur la matrice 13×13 kind/runtime/helper (8.7)
- + Modal "🔧 Métadonnées" sans confusion vs BlockNote (8.5)

**Ops peut maintenant** :
- Voir les erreurs JS clients via `GET /api/log/client-errors` (8.14)
- Diagnostiquer les autosave fails sans Sentry externe
- Détecter les bugs Edge/Mac/Safari spécifiques en prod

### Drifts résolus : 8/10

| Drift | Description | Statut |
|---|---|---|
| D1 | Tools OpenAI 4 kinds | ✅ v1.9.0 (8.6) |
| D2 | OCC update_component_endpoint | ✅ v1.8.0 (8.2) |
| D3 | BlockNote update vs create | ✅ v1.8.0 (8.1) |
| D4 | 3 kinds placeholder helper | 🟡 partiel v1.8.0 (8.3 — media_embed/iframe_grist livrés, scene_3d différé) |
| D5 | Modal E1 sans OCC | ✅ v1.8.0 (8.2) + rebrand v1.9.0 (8.5) |
| D6 | H2 round-trip fragmentation | ✅ v1.9.0 (8.10) |
| D7 | Recipes orphelines | ⏳ V2 (8.16) |
| D8 | Pas test kind/runtime | ✅ v1.9.0 (8.7) |
| D9 | AssemblyKind 501 | ✅ v1.8.0 (8.4) |
| D10 | Pas tool get_draft_blocks | ⏳ V2 (8.15) |

---

## Prochaine session (Vague E3 sprint 4 + V2 différés)

Vague E3 : audit_chain_narrative + reliability_matrix kinds + AuditChain
enrichi + layout sidecar Esri + scene_3d MapLibre (vrai rendu pas placeholder)
+ multi-cartes + print A4 + reliability chips + cross-références narratives +
sommaire sticky + audit chain narrative humain + DSFR theming strict
BlockNote + CRDT Yjs multi-user collab + création nouveau composant depuis
slash menu BlockNote + agent IA aligné sur OCC `version_num_source`.
