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

## Prochaine session

Attaque Vague A commit 1 (FONDATIONS) :
1. Lecture audit_chain.py + ajout `class Statut` + `class Source`
2. Rename `signed_hash` → `integrity_hash` + property legacy DeprecationWarning
3. Test contrat `tests/test_audit_chain_strate_alignment.py`
4. Lint + commit + push + CI + set image hub
5. Smoke test pytest

Puis commit 2 (HELPER UNIFIÉ) et commit 3 (BIBLIOTHÈQUE).
