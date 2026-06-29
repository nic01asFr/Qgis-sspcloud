# API Éditeur BlockNote — qgis-sspcloud

> Contrat externe livré pour intégration éditeur block-based dans le desk
> qgis-sspcloud. Permet à Marie de modifier visuellement les Assembly
> après création par l'agent IA.
>
> **Vague E2** (D-QGIS-010, 2026-06-29) — Tag `v1.7.0-blocknote-editor`

## Vue d'ensemble

L'éditeur BlockNote standalone est servi par le hub FastAPI comme bundle
statique Vite. 10 commits livrés (E1+E2 setup, F-DOM+F-iframe 13 custom
blocks, G sérialisation bi-dir, H1+H2 autosave concurrency + desk
integration, H3 docs + tag).

```
┌─ Marie desk ─────┐  ┌─ /editor/.../assembly/... ─┐  ┌─ /studies/... API ─┐
│  Click "📝       │→ │ React 18 + BlockNote v0.22  │→ │ PUT /assemblies    │
│  BlockNote"      │  │ 13 custom blocks            │  │ + concurrency 409  │
│  → nouvelle tab  │  │ Autosave 30s debounce       │  │ POST /components   │
└──────────────────┘  └──────────────────────────────┘  └────────────────────┘
```

## 13 Custom blocks couvrant ComponentKind complet

| ComponentKind | BlockNote type | Rendering | Édition inline |
|---|---|---|---|
| **DOM atomiques (légers, édition inline)** | | | |
| `heading` | `customHeading` | DOM React H1-H4 | ✅ |
| `kpi_grid` | `kpiGrid` | Grid CSS + chips colorés | ✅ |
| `kpi_badge` | `kpiBadge` | KPI inline horizontal | ✅ |
| `quote` | `customQuote` | Blockquote DSFR | ✅ |
| `separator` | `separator` | HR variants | ✅ |
| `narrative_text` | `narrativeText` | Markdown paragraph | ✅ |
| `legend` | `legend` | Chips + items + source | ✅ |
| **Iframe (réutilise rendu Jinja2 hub)** | | | |
| `interactive_map` | `interactiveMap` | iframe `/render/{cid}` | Preview only |
| `chart` | `chart` | iframe `/render/{cid}` | Preview only |
| `data_table` | `dataTable` | iframe `/render/{cid}` | Preview only |
| `scene_3d` | `scene3d` | iframe `/render/{cid}` | Preview only |
| `media_embed` | `mediaEmbed` | iframe `/render/{cid}` | Preview only |
| `iframe_grist` | `iframeGrist` | iframe Grist natif | Preview only |

## Endpoints REST hub

### `GET /editor/{sid}/assembly/{aid}`

Sert l'éditeur BlockNote standalone. Auth OIDC cookie obligatoire.

Retourne `index.html` du bundle Vite (~300KB gzip).
Si bundle absent (CI Docker pas build) : HTTP 503 + message d'aide.

### `PUT /studies/{sid}/assemblies/{aid}` (enrichi Commit H1)

Update Assembly avec **optimistic concurrency control**.

Body :
```json
{
  "kind": "storymap_narrative_dsfr",
  "title": "Storymap...",
  "audience": "cerema_internal",
  "layout": { "type": "scroll_vertical", "sections": [...] },
  "version_num_source": 3   ← NEW : version chargée au début édition
}
```

Réponses :
- **200** : ok, retourne nouveau `version_num`
- **409 Conflict** : `version_num_source != current_version_num`
  ```json
  {
    "detail": {
      "error": "concurrent_update",
      "message": "Conflit : l'assembly a été modifié...",
      "current_version_num": 5,
      "source_version_num": 3
    }
  }
  ```
- L'UI BlockNote propose : "Recharger" (default) ou "Forcer écrasement"
  (re-PUT sans `version_num_source`)

### `POST /studies/{sid}/components` (existant Vague A)

Crée un nouveau Component (DOM kind) avant `update_assembly`.

L'éditeur appelle ceci pour chaque nouveau block DOM créé/modifié
(kpi_grid édité, heading ajouté...) avant d'appeler `update_assembly`
avec les `cid` réels.

## Flow autosave (Marie modifie un KPI)

```
1. Marie modifie value "47" → "52" dans kpiGrid block
2. Timer debounce reset (30s)
3. ⏱ 30s d'inactivité
4. saveBlocks() :
   a. blocksToSections(blocks) → (sections, new_components)
   b. POST /studies/{sid}/components × N (nouveaux DOM)
      → recupere cid1, cid2, ...
   c. Remplace __pending__ refs par cid réels
   d. PUT /studies/{sid}/assemblies/{aid} {
        layout: { sections: [...] },
        version_num_source: 3
      }
   e. Si 200 → versionNumSource = newVersionNum
              indicateur "✓ Sauvegardé il y a 0s (v4)"
   f. Si 409 → indicateur rouge "Conflit (v5 vs v3) — rechargez"
```

## Architecture déploiement

### Dockerfile.hub multi-stage

```dockerfile
# Stage 1 : build Vite
FROM node:20-alpine AS blocknote-builder
WORKDIR /build
COPY blocknote-editor/package.json ./
RUN npm install --no-audit --no-fund --legacy-peer-deps
COPY blocknote-editor/ ./
RUN npx vite build

# Stage 2 : hub Python
FROM inseefrlab/onyxia-jupyter-python:py3.13.13
# ...
COPY --from=blocknote-builder /build/dist /opt/qgis-hub/hub/static/blocknote-editor/
```

### Hub FastAPI mount static

```python
app.mount(
    "/static/blocknote-editor",
    StaticFiles(directory=str(_BLOCKNOTE_STATIC_DIR)),
    name="blocknote_editor_static",
)
```

## Compatibilité écosystème CEREMA

**Zéro impact autres projets** : éditeur isolé dans qgis-sspcloud :

| Projet | Impact |
|---|---|
| qgis-sspcloud | ✅ Intégré (cet ADR) |
| ZEBRA | ✅ Zéro impact (vanilla JS widget Atlas inchangé) |
| Atlas widget Grist | ✅ Zéro impact (iframe `/published/...` inchangé) |
| MobSciDat | ✅ Zéro impact (consomme storymap URL) |
| panoramax3d | ✅ Zéro impact (3D séparé) |
| Strate | ✅ Zéro impact (backend) |
| geoai-kit | ✅ Zéro impact (JS kit MapLibre inchangé) |

## Sécurité

- Auth cookie OIDC obligatoire pour ouvrir l'éditeur
- Bundle statique public (pas de secret)
- L'éditeur fait `fetch /studies/...` same-origin → cookie propagé
- `version_num_source` empêche perte de modifs par conflit agent IA

## Compromis V1 (différés Vague future)

- ❌ **Théming DSFR Mantine strict** : V1 = "Mantine bleu" sobre, V2 Vague E4 = DSFR officiel via styles LaSuite Docs
- ❌ **Création nouveau composant depuis BlockNote** : V1 = édition uniquement, V2 = slash menu `/kpiGrid` ajoute un block vide
- ❌ **CRDT Yjs multi-user collab** : V1 = mono-utilisateur, format `partialBlocks` BlockNote nativement Y-compatible pour migration
- ❌ **Tests E2E Playwright** : V1 = tests Vitest + pytest round-trip suffisent

## Limitations connues V1

1. **Bundle 300KB** chargé à chaque ouverture éditeur (Cache-Control immutable atténue)
2. **Iframe interactive_map** : preview only, pas éditable inline (utiliser desk pour modifier scene_manifest)
3. **Autosave fixe 30s** : pas configurable user (défaut Notion/OneNote)
4. **Pas d'undo/redo cross-save** : undo limité à la session BlockNote en cours

## Évolutions Vague E3/E4

- DSFR theming strict (re-utilise styles LaSuite Docs)
- Création nouveau composant depuis BlockNote (slash menu enrichi)
- CRDT Yjs multi-user collab (Marie + collègues éditent ensemble)
- Tests Playwright E2E
- Mode mobile responsive
- Export PDF natif depuis BlockNote (`@media print` style)

## Référence (mis à jour v1.7.5)

- Tags : `v1.7.0-blocknote-editor` → `v1.7.5-consolidation` (5 vagues consécutives)
  - `v1.7.0-blocknote-editor` : livraison initiale 13 custom blocks + autosave + bouton desk
  - `v1.7.1-audit-fixes` : 5 P0 + truncation silencieuse + code mort retiré
  - `v1.7.2-p1p2-optims` : Promise.allSettled + force-overwrite + Cache-Control + whitelist OIDC
  - `v1.7.3-fullwidth` : container 100% + width:100% custom blocks
  - `v1.7.4-roundtrip-section` : frontière sections via heading H2 vide
  - `v1.7.5-consolidation` : tests pytest + docs + footer dynamique
- ADR : `docs/decisions/D-QGIS-010-blocknote-editor-blocks-based.md`
- Plan détaillé : `docs/blocknote-editor-plan.md`
- Code React : `blocknote-editor/src/` (serialisation 100% TypeScript, pas de pendant Python)
- Code Python : `hub/hub/main.py` PUT enrichi `/studies/{sid}/assemblies/{aid}` (version_num_source + skip None merge)
- Tests : **196/196 pytest PASSED** (post v1.7.1 retrait des 20 tests fictifs de l'ancien `blocknote_serializer.py`)

## Contact

Pour intégration cross-projet : voir wikichat `#qgis-sspcloud-sprint-co` ou
`#cerema-templates`.
