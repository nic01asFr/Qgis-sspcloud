# D-QGIS-009 — Vague E1 UX libre composition (reporte nouveaux kinds en Vague E2)

**Date** : 2026-06-29
**Statut** : ACTÉ
**Auteur** : Composants-Architect (suite recadrage user : "storymap = composition libre, pas format figé")
**Décisions liées** : D-QGIS-005 (Component V0.1 source qgis-sspcloud), D-QGIS-008 (helper rendu unifié)

## Conclusion

Vague E1 cible **l'UX libre de composition de livrables** (PATCH composants + clone assemblages + catalogue cross-étude + UI desk éditeur sections), **avant** d'ajouter de nouveaux kinds composants (reportés Vague E2).

Component V0.1 reste inchangé (toujours 9 kinds : narrative_text, kpi_badge, legend, interactive_map, chart, data_table, scene_3d, media_embed, iframe_grist). Pas de bump V0.2.

## Rationale

### Constat initial — équipe design wikichat (commit 4aa7ccf)

3 agents wikichat (Design-Lead + Editorial-Architect + Components-Designer) ont produit 1900 lignes de specs pour Vague E1 proposant :
- 5 nouveaux kinds : `kpi_grid`, `audit_chain_narrative`, `chapter_section`, `reliability_matrix`, `pipeline_signature`
- Layout sec-map sticky 2 colonnes
- Patterns Blancarde-Chartreux (fr-tabs, fr-accordion, footer signé)

### Recadrage user décisif

> "Storymap n'est pas un format figé. Va voir les storymaps proposées par Esri
> et la solution de composition pour bien comprendre. C'est l'idée d'avoir des
> composants et des livrables, de standardiser et harmoniser des composants
> pour que l'agent et le user puissent éditer et créer des livrables sur
> mesure intégrés dans tout ce que l'on a fait."

ArcGIS StoryMaps modèle = **palette de blocks composables librement**, pas structure imposée chapter/onglets/footer.

### Audit codebase rapporté

Audit Explore agent (2026-06-29) sur 7 axes :
- ✅ 21 native tools cohérents agent IA + MCP (dispatch + schemas + whitelist v15)
- ✅ Discovery API active (/schema/{entity}/{kinds,validate})
- ✅ Discipline PLAN-PUIS-EXECUTE explicite system_prompt v15
- 🚨 **Bug P0** : `publish_component` + `update_assembly` absents de `NATIVE_TOOLS_V2_MUTATING`
- ❌ **3 gaps critiques pour composition libre** : PATCH /components/{cid} (composants pas éditables), /catalog/components (pas de catalogue cross-étude), clone_assembly (pas de fork pattern)
- ❌ **UI desk** : pas d'éditeur sections (composition agent-driven only)

### Décision

**Inverser l'ordre** : E1 = UX libre composition (ce qui débloque), E2 = nouveaux kinds (enrichit catalogue une fois composition possible).

Ajouter 5 nouveaux kinds sans pouvoir les ÉDITER après création = on multiplie la friction. Le vrai gap n'est pas le manque de kinds (9 actuels couvrent déjà beaucoup) mais l'impossibilité de **composer librement** (user et agent).

## Périmètre Vague E1 retenu

### 5 items + 1 bug fix P0

| # | Item | Type | Effort |
|---|---|---|---|
| 0 | Bug P0 : `publish_component` + `update_assembly` dans `NATIVE_TOOLS_V2_MUTATING` | fix | 5 min |
| 1 | PATCH `/studies/{sid}/components/{cid}` + native tool `update_component` | endpoint + tool | ~2h |
| 2 | POST `/studies/{sid}/assemblies/{aid}/clone?deep=false` + tool `clone_assembly` + `ComponentProvenance.cloned_from: str \| None` | endpoint + tool + model | ~2h30 |
| 3 | GET `/catalog/components?audience=&kind=` cross-étude + tool `list_catalog_components` | endpoint + tool | ~2h |
| 4 | UI desk éditeur sections (réordonner + delete/duplicate + form params dynamic + preview iframe + save PUT) | UI HTML/JS | ~3-4h |
| 5 | System prompt v15 update + `docs/api/components-catalog.md` + ADR final | docs | ~1h |
| | **Total** | | **~12-14h** |

### Décisions de design

- **Clone shallow par défaut** (refs cid partagés, modifications source impactent clone). `?deep=true` optionnel pour duplication composants.
- **`cloned_from` sur `ComponentProvenance`** (Option C) au lieu de créer `AssemblyProvenance` distinct. Cohérent : composants ET assemblages peuvent être clonés. Backward-compat absolue (default `None`).
- **Audience default `cerema_internal`** sur `/catalog/components` (anti-fuite RGPD).
- **Form params UI editor** : approche graceful — JSON Schema → HTML form simple pour kinds simples (kpi_badge, narrative_text). Kinds complexes (interactive_map) → quick-action copy-prompt agent IA.

### Non-objectifs Vague E1

- ❌ Pas de nouveaux ComponentKind (reportés Vague E2)
- ❌ Pas de layout sidecar (reporté Vague E2)
- ❌ Pas de full DSFR CDN (cohérent "DSFR-inspiré sobre")
- ❌ Pas de `chapter_section` qui fige fr-tabs (anti-composition libre)
- ❌ Pas de `pipeline_signature` kind (devient extension `AssemblyFooter`)

## Vague E2 différée (post-E1)

Une fois UX libre opérationnelle :

| Item | Type | Effort estimé |
|---|---|---|
| 5 nouveaux kinds : `kpi_grid`, `audit_chain_narrative`, `reliability_matrix`, `heading`, `quote` | Component | ~6h |
| `AuditChain` enrichi : `Phase`, `VariableReliability`, `contributors`, `n_treatments`, `n_maps` | Pydantic ext | ~1h |
| Extension `narrative_text` markdown `{{ kpi:V \| L }}` parser | regex | ~30 min |
| `AssemblyFooter` enrichi : `contributors`, `version_label`, `n_treatments`, `n_maps` | Pydantic ext | ~30 min |
| Layout `sidecar` (scrollytelling Esri) `AssemblySection.layout: Literal[..., "sidecar"]` | layout | ~4h |
| Bump Component V0.1 → V0.2 + migration test contrat | versioning | ~1h |
| Tag `v1.7.0-component-catalog-v02` | release | — |
| **Total Vague E2** | | **~13h** |

## Conséquences

### Pour qgis-sspcloud
- Component V0.1 stable jusqu'à Vague E2
- Tag E1 : `v1.6.5-vague-e1-composition-libre` (pas de bump majeur)
- Tag E2 : `v1.7.0-component-catalog-v02` (bump majeur car nouveaux kinds + V0.2)

### Pour pairs écosystème (Passerelle-Archi, Strate-Architect, Lead-cerema)
- Aucun impact Scene Manifest V0.2 (D4 préservé)
- Aucun impact `audit_chain.Source` Strate (D-QGIS-006 préservé)
- Aucun impact contrat `scene-manifest-v0.2-contract.md` livré pour D2 geoai-kit
- Catalogue cross-étude `/catalog/components` ouvre nouveaux usages (ZEBRA/MobSciDat/Atlas peuvent browser composants CEREMA)

### Pour docs design équipe (commit 4aa7ccf)
- `components-vague-e1-specs.md` : valide pour Vague E2 (spec différée)
- `design-livrables-cerema.md` : valide pour Vague E2 (full DSFR strict reporté, nous gardons DSFR-inspiré sobre)
- `editorial-narrative-pattern.md` : valide tel quel (pattern éditorial cross-Vague)

## Référence wikichat

- Audit codebase 7 axes : Explore agent 2026-06-29
- Bug P0 MUTATING : Reviewer-VagueE1 (Explore agent)
- Pivot stratégique : recadrage user 2026-06-29 ("storymap = composition libre, pas format figé")

## Plan d'implémentation

5 commits + 1 commit préparation (ce ADR + bug fix + bandeaux docs design) :

```
prep-vague-e1-ux-libre
├─ Bug P0 fix NATIVE_TOOLS_V2_MUTATING
├─ ADR D-QGIS-009 (ce document)
├─ Bandeaux "spec reportée Vague E2" sur 3 docs design
├─ BILAN section pivot stratégique
├─ Axes wikichat update
└─ README mention plan E1

Commit 1 — PATCH components + update_component
Commit 2 — Clone assembly + cloned_from
Commit 3 — Catalog cross-étude
Commit 4 — UI desk éditeur sections
Commit 5 — System prompt v15 + docs final
Validation + Reviewer + Tag v1.6.5-vague-e1-composition-libre
```
