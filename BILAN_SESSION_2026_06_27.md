# Bilan session 2026-06-27 — Sprint Composants Phase 3b + 3c LIVRÉ

> Session de marathon : `V1.5 publication CEREMA` consolidé + branchement
> agent IA (Phase 3b) + meta-agent analyseur recipes (Phase 3c).
>
> **Acquis** : 8 commits (~2634 LOC), 9 fichiers, tag `v1.5.3c-meta-agent`,
> smoke E2E final **7/7 (100%)**.

## Récap chronologique

### Bloc 1 — V1.5 publication CEREMA (consolidation)
- Pipeline `validate → create_component → create_assembly → render → publish` validé E2E live
- URL S3 publique conservée : `https://minio.lab.sspcloud.fr/nicolaslaval/qgis-workspace/published/nicolaslaval/assembly/assembly-417b5375910f.html`
- `audit_chain.signed_hash` SHA256 : `sha256:18dc597b8f863cf...`

### Bloc 2 — Sprint Composants Phase 3b (3 commits, ~1081 LOC)
Branchement agent IA Sprint Composants V1.5 :
- `44ff628` C1 : refactor `native_tools_v2.py` format OpenAI + nouveau profile `storymap_creator_v15.yaml` + branchement `qgis_agent.py` (extend `_get_tools` + dispatch `_call_mcp_tool_raw`)
- `34eeadd` C2 : `agent/agent/hub_artifacts.py` (NEW) + cache TTL `_fetch_study_artifacts_summary` + wiring `_build_system_prompt` + `_QGIS_ESSENTIALS` item 2quinquies + `memory.build_context_summary` kwarg `study_artifacts` + règles `_build_next_action_hints` déterministes
- `70b1fdd` C3 : UI bridge `hub/templates/desk.html` quick-actions drawer Composants + Assemblages

**Smoke E2E v1** : 3/3 (3 tools V1.5 mentionnés + 0 legacy + workflow stratifié compris)

### Bloc 3 — Sprint Composants Phase 3c (5 commits, ~1553 LOC)
Meta-agent analyseur recipes pattern transverse :
- `58d12ae` C4 : schemas Pydantic `RecipeAnalysis` 2 facettes + table DB `recipe_analyses_index` + helpers PVC + 5 endpoints REST
- `c2e79f3` C5 : profile `recipe_analyzer.yaml` strict + tool natif `analyze_recipe` côté agent + LLM fallback `_MODEL_FALLBACKS` + trigger fire-and-forget au PUT recipe + endpoint agent `/internal/analyze-recipe`
- `ef4e78a` C6 : cheat-sheet réflexe 0 PLAN-PUIS-EXECUTE généralisé + `storymap_creator_v15.yaml` discipline dry-run params
- `f09900f` hotfix : endpoint interne `/internal/profiles/{id}/full` + whitelist OIDC inter-pod `/internal` prefix
- `96be3e0` iter : `_pad_short_strings` helper (sauve validation Pydantic stricte sur LLM output imparfait) + `storymap_v15` system_prompt strict `publish_assembly` (anti-`publish_artifact` legacy)

**Smoke E2E v3 FINAL** : SCORE **7/7 (100%)**
- `analyze_recipe('risque_inondation')` → `cache_status: "miss_analyzed"` → LLM gemma4 produit analyse Pydantic-valide → POST cache hub DB + PVC
- Agent enchaîne `list_entity_kinds(component)` + `list_entity_kinds(assembly)`
- Pose plan 3 phases : Analyse QGIS + Création composants V1.5 + Assemblage storymap
- Aucun tool legacy mentionné (publish_artifact, StorymapBuilder)
- Discipline plan-puis-execute respectée
- 484 SSE chunks, first_token 5.2s, total 48.9s

### Bloc 4 — Tests + Validations production
Tests endpoints + cache + DB cohérence + V1.5 cohabitation + URL S3 + qualité enrichissement :
- 6/6 tests passent (1.6s)
- 6/6 validations passent (1.5s)
- Détail analyse `risque_inondation` :
  - 3 params analysés (`timeout`, `template`, `include_fields`)
  - 5 quality_checks détectés (4 warnings + 1 info, distribution idéale)
  - Score 0.82 cohérent avec pondération `1.0 - 4×0.05`
  - Catégories : qgis_data + external_services + qgis_modules + best_practices
  - LLM model : gemma4-26b-moe (fallback qwen3)

### Bloc 5 — Recadrage architectural (user feedback)
Insight critique : pattern meta-agent **SURTOUT utile design-time** (création/édition recipes), **marginal at runtime** (recipe stabilisée).

Pour le runtime Marie persona, ce qui mérite analyse n'est plus la brique mais la **composition** :
- ContextAnalysis (Phase 3c-4) : cohérence étude+zone+couches+treatments vs choix recipe
- CompositionAnalysis (Phase 3c-5) : cohérence narrative entre N composants assemblés

→ Direction future Geomind : runtime "agent qui réfléchit ET évalue son résultat"

## Architecture finale Sprint Composants

### Profiles (10 chargés)
- `risk_analyst`, `geoai_analyst`, `db_analyst`, `recipe_creator`, `map_composer`, `guided_tour`, `standard`
- `storymap_creator` (legacy, intact)
- `storymap_creator_v15` (Phase 3b)
- `recipe_analyzer` (Phase 3c, profile interne strict)

### Tools agent IA exposés
- 46 MCP tools (BigQgisMCP via /mcp)
- 13 NATIVE_TOOLS_V2 OpenAI function calling
- `analyze_recipe` Phase 3c (orchestrateur LLM + cache)
- Native recipe tools (V1.5 Sprint 1)
- Native memory tools

### Endpoints REST hub
- `/studies/{sid}/components/*` (V1.5)
- `/studies/{sid}/assemblies/*` (V1.5 + publish S3 + audit_chain)
- `/schema/{component|assembly}/*` (méta-cognitifs P0 Phase 2)
- `/schema/recipe/{slug}/analysis*` (Phase 3c)
- `/internal/profiles/{id}/full` (Phase 3c, whitelist hardcoded)
- `/internal/analyze-recipe` (agent, trigger fire-and-forget)
- `/admin/recipe-analyses/*` (review pending + validate)

### Tables SQLite
- `studies`, `projects`, `recipes_index`, `components_index`, `assemblies_index`, `exports_index`
- `tombstones` (GDPR)
- `recipe_analyses_index` (Phase 3c)

### Cache PVC
- `/data/studies/{sid}/recipes/{slug}/analysis.json` (user recipes Phase 3c)
- `/data/system_recipes_enrichments/{slug}_{hash[:12]}.json` (system recipes Phase 3c)
- `/data/studies/{sid}/components/{cid}/manifest.json` (V1.5)
- `/data/studies/{sid}/assemblies/{aid}/manifest.json` (V1.5)
- `/data/studies/{sid}/assemblies/{aid}/rendered/index.html` (V1.5)

## Pattern transverse capitalisé

`~/.wikichat/knowledge/meta-agent-enrichment-pattern-axis.md` (NEW Phase 3c)
- Pattern réutilisable cross-entités (Component/Assembly/Dataset/Profile Analysis)
- Réutilisable cross-projets (VALID enrichir σ, MobSciDat auto-doc KG, ZEBRA narratif)
- Vision Geomind : Context/CompositionAnalysis runtime = différenciateur

## Coordination Passerelle-Archi
- Lane Composants : `qgis_agent.py`, `native_tools_v2`, `models/*`, `components.py`, `assemblies.py`, `recipe_analyzer*`, profiles
- Lane Passerelle-Archi : `auth.py` scoped_keys, `/mcp` proxy filtering, endpoint mint (V2 différé)
- Frontières propres : 0 conflit fichier sur 8 commits divergents (worktrees symétriques)
- Compat ascendante V2 : refactor format n'impacte pas handlers `(sid, manifest)` → V2 override `manifest.sid = scope.sid` reste compat

## État production validé

| Composant | Statut |
|---|---|
| Hub user-nicolaslaval | ✅ image `96be3e0e468...` |
| Agent IA qgis-agent | ✅ image `96be3e0e468...` |
| Workspace QGIS noVNC | ✅ ready |
| Endpoints Phase 3c | ✅ 7/7 répondent |
| DB recipe_analyses_index | ✅ 2 entrées valides |
| Cache PVC system_recipes_enrichments | ✅ JSON valides |
| URL S3 V1.5 publiée | ✅ ACL public-read maintenu |
| Profile recipe_analyzer | ✅ système_prompt 9250 chars |
| Profile storymap_creator_v15 | ✅ tools whitelist incluant `analyze_recipe` |
| Profile storymap_creator legacy | ✅ intact (migration douce) |

## Prochaine session — directions possibles

1. **Phase 3c-3** : Generalisation pattern → ComponentAnalysis + AssemblyAnalysis (design-time)
2. **Phase 3c-4** : ContextAnalysis runtime (cohérence étude+zone+couches vs action)
3. **Phase 3c-5** : CompositionAnalysis runtime (cohérence N composants assemblés)
4. **UI desk review panel** : admin valide RecipeAnalysis non-validated (V2)
5. **Feedback Marie réel** : tester pattern en condition d'usage CEREMA
6. **Sprint 4 V2** : GPKG étendu + ZIP composite + scene_3d Three.js + dashboard + sheet_a4 + bibliothèques + Phase 11 macro learning

Tag de release : `v1.5.3c-meta-agent` (état production rollback-safe).
