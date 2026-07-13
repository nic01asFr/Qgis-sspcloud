# Spec — Format `recipe_web` (chantier G4-POC)

Statut : POC — v0.1 (2026-07-13). Ne remplace pas le format recipe JSON
existant de BigQgisMCP (voir `USER_RECIPES_DIR`), il l'étend pour produire
directement un `scene_manifest` V0.3.x publiable côté web.

## 1. Objectif

Une **recette web** décrit une chaîne déterministe qui produit :

1. Des couches issues d'une exécution QGIS (WFS, raster, processing).
2. Une narration composable à partir de la bibliothèque de briques
   (`hub/hub/briques/`).
3. Un `scene_manifest` V0.3.1 (composant carte) OU un assemblage
   (storymap, dashboard, etc.) publiable en un cycle.

Le contrat clé : *la même recette web, exécutée sur les mêmes inputs et le
même catalogue de briques, produit un `scene_manifest` bit-à-bit identique.*
Aucun appel LLM dans le mode `recipe_pure`.

## 2. Structure YAML/JSON

Une recipe web est un fichier YAML (ou JSON) unique. Champs racine :

```yaml
manifest_version: "0.3.1"      # ancre la compat SceneManifest
id: diagnostic_parc_bati_temporel
title: "Diagnostic du parc bâti par période"
version: "1.0"
author: "cerema"
use_cases: [diagnostic_temporel]
depends_on_datasources: [bdtopo_batiments, arrondissements_marseille]
output_kind: component          # component | assembly

# 1. Briques importées globalement (rules, forbidden…) — appliquées à
#    tout le rendu web produit par la recipe (metadata + guardrails).
imports:
  - brique_ref: rules_global/crs_wgs84_obligatoire
  - brique_ref: rules_forbidden/no_hallucination_sources

# 2. Étapes exécutées en séquence.
steps:
  - kind: run_qgis
    id: layer_bati
    algorithm: catalog_wfs
    params:
      catalog_id: bdtopo_batiments
      zone_ref: "${context.zone_insee}"
    outputs:
      layer_id: batiments_bd_topo
      layer_name: "Bâtiments par période"
      geometry_type: polygon
      classification_field: date_d_apparition
      role: primary

  - kind: include_brique
    ref: narrative/disclaimer_rga
    template_context:
      zone_label: "${context.zone_label}"
      pct_ancien_avant_1948: 12.4
    slot: description        # dans le manifest final : quel emplacement

  - kind: render_web
    target: component        # component | assembly
    manifest_defaults:
      basemap: { id: plan-ign-v2-gris }
      zone: { kind: insee_arm, insee: "${context.zone_insee}" }
      projection: mercator
```

## 3. Nouveaux champs par rapport aux recipes existantes

| Champ | Type | Description |
|-------|------|-------------|
| `manifest_version` | str | Ancre SceneManifest cible (ex. `"0.3.1"`) |
| `imports` | list[RecipeImport] | Briques appliquées globalement |
| `steps` | list[RecipeStep] | Union discriminée sur `kind` |
| `output_kind` | `component` \| `assembly` | Nature du livrable |
| `use_cases` | list[str] | Usages CEREMA ciblés (facultatif) |
| `depends_on_datasources` | list[str] | Catalog IDs requis |

Un `RecipeImport` est un pointeur `{brique_ref: "category/id"}`. `category`
doit appartenir à `hub.briques_loader.VALID_CATEGORIES`.

## 4. Types de steps (discriminated union sur `kind`)

### 4.1 `run_qgis`

Décrit un traitement QGIS (WFS/WCS, algorithme processing, script custom).
Dans le POC G4, le moteur simule la sortie en produisant un layer stub
inséré dans le `scene_manifest` final. L'intégration réelle MCP interviendra
en G4-b.

Champs obligatoires : `id`, `algorithm`, `outputs.layer_id`.
Champs recommandés : `outputs.layer_name`, `outputs.geometry_type`,
`outputs.classification_field`, `outputs.role`.

### 4.2 `include_brique`

Résout une brique de la bibliothèque et l'injecte dans le manifest.

- `narrative/*.md.j2` → rendu Jinja2 avec `template_context`, résultat
  placé selon `slot` (par défaut `description`).
- `rules_global/*` et `rules_forbidden/*` → concaténation de `rule_text` +
  traçage dans `provenance.rules_enforced`.
- `compositions/*` → merge JSON à la racine du manifest (les clés du
  manifest gagnent en cas de conflit).
- `palettes/*` → placée sous `context.palettes[brique_id]` du manifest.
- `use_cases/*` → placée sous `provenance.use_case_ref` (pas de rendu).

### 4.3 `render_web`

Étape terminale (une seule autorisée par recipe) : agrège tout ce qui a été
produit dans un `scene_manifest`. Champs :

- `target` : `component` (produit `scene_manifest.json` direct) ou
  `assembly` (produit un manifest d'assembly qui inline le composant).
- `manifest_defaults` : dict fusionné dans le manifest de sortie.

## 5. Modes d'exécution

Le format supporte deux modes dont un seul est implémenté dans ce POC.

### 5.1 `recipe_pure` (implémenté)

- Aucun appel LLM.
- Sortie déterministe : deux runs sur mêmes entrées → même
  `scene_manifest` bit-à-bit.
- `produced_at` provient de `context["timestamp"]` (jamais `datetime.now()`).
- Toute erreur d'import ou de step est fatale (`RecipeImportError`,
  `RecipeStepError`).

### 5.2 `recipe_polished` (extension future, non implémenté)

- Autorise un polish LLM **strictement limité à `narrative_text`** :
  reformulation, correction, jamais d'invention de données.
- Reste borné par les briques `rules_forbidden`.
- Non-déterministe : produced_at + trace LLM dans `provenance.polish`.
- À implémenter après review du POC (G4-c).

## 6. Contrat de sortie (`RecipeWebOutput`)

```json
{
  "scene_manifest": { "manifest_version": "0.3.1", "title": "…", "…": "…" },
  "provenance": {
    "recipe_id": "diagnostic_parc_bati_temporel",
    "recipe_version": "1.0",
    "mode": "recipe_pure",
    "produced_at": "2026-07-13T10:00:00+02:00",
    "steps_order": ["layer_bati", "disclaimer_rga", "render_web"],
    "briques_used": [
      "rules_global/crs_wgs84_obligatoire",
      "rules_forbidden/no_hallucination_sources",
      "narrative/disclaimer_rga"
    ],
    "rules_enforced": [
      { "ref": "rules_global/crs_wgs84_obligatoire", "severity": "error" }
    ]
  },
  "briques_used": [
    "rules_global/crs_wgs84_obligatoire",
    "rules_forbidden/no_hallucination_sources",
    "narrative/disclaimer_rga"
  ]
}
```

## 7. Exemple concret

Voir `examples/diagnostic_parc_bati_temporel.yaml` : bâti Marseille 4e
(BD TOPO), classification date d'apparition, disclaimer RGA, output
`component`.

## 8. Roadmap post-POC (G4-b, G4-c)

- **G4-b — Intégration MCP + endpoints REST** : câbler `run_qgis` sur les
  vrais tools BigQgisMCP (`smart_load`, `run_processing`), exposer
  `POST /api/recipes-web/execute` sur le hub, wire dans le desk.
- **G4-c — `recipe_polished`** : ajouter un mode polish LLM borné (voir §5.2).
- **G4-d — UI recipe browser** : lister les recipes web dans le desk,
  aperçu du manifest produit, one-click publish.

## 9. Contraintes non fonctionnelles

- Français strict : logs, docstrings, messages d'erreur.
- Pas de dépendance nouvelle : PyYAML et Jinja2 déjà présents dans le hub.
- Chargement lazy des briques via `hub.briques_loader.get_brique`.
- Tests d'idempotence obligatoires pour `recipe_pure`.
