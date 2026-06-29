# D-QGIS-010 — Éditeur block-based BlockNote pour édition visuelle Assembly

**Statut** : ✅ LIVRÉ — 5 tags consécutifs (v1.7.0 → v1.7.5) — production-ready
**Date** : 2026-06-29 (livraison + audit + consolidation)
**Auteurs** : Nicolas LAVAL, agent IA Claude (qgis-sspcloud)
**Tags / Vague** : `v1.7.0-blocknote-editor` → `v1.7.5-consolidation` — Vague E2 pivot UI

> **Note pivot architectural acté** (v1.7.1) : la sérialisation Pydantic ↔ BlockNote
> JSON est **100% TypeScript** (`blocknote-editor/src/serializer.ts` forward +
> `autosave.ts:blocksToSections` backward). Le pendant Python (`hub/hub/blocknote_serializer.py`)
> initialement prévu Commit G a été supprimé en v1.7.1 (audit P0 #3 — aucun endpoint
> hub ne l'appelait, code mort 514 LOC + 20 tests fictifs).

> **Limitations V1 actées** (v1.7.4) :
> - scene_3d / media_embed / iframe_grist : iframe `/render/{cid}` retourne un
>   placeholder texte côté hub (helper `_pre_render_component_html` ne supporte
>   pas ces kinds). UX dégradée pour 3 des 13 kinds, à upgrader Vague E3.
> - Agent IA `update_assembly` n'envoie pas `version_num_source` → peut écraser
>   silencieusement les modifs BlockNote en cours (OCC unidirectionnelle).
>   Mitigation Vague E3 : agent IA passe via le même endpoint + version_num_source.

## Contexte

Après Vague E1 (UX libre composition par agent IA) et Vague E2 base (storymap
métier + carto thématique), un constat émerge : **Marie n'a aucun moyen visuel
de modifier un livrable après création par l'agent IA**. Soit elle re-prompte
l'agent, soit elle édite du JSON brut. Pas de drag-drop, pas d'édition inline,
pas de prévisualisation live.

Or l'écosystème institutionnel français a financé via l'ANCT (Incubateur des
Territoires) le projet **BlockNoteJS** (TypeCellOS/BlockNote, MIT) qui propulse
**LaSuite Docs** (`docs.numerique.gouv.fr`). C'est l'équivalent open source du
modèle Notion / block editor, déjà aligné avec les standards État.

## Décision

Intégrer **BlockNoteJS comme éditeur block-based** pour permettre à Marie de
modifier visuellement les `Assembly` de qgis-sspcloud après leur création
initiale (par agent IA ou par template).

### Mapping ComponentKind → BlockNote custom blocks

**13 custom blocks** couvrent l'intégralité des `ComponentKind` (Vagues A + B + E1 + E2) :

| ComponentKind | BlockNote type | Rendering | Mapping `block.props` ↔ `Component.params` |
|---|---|---|---|
| **DOM atomiques (lights, édition inline immédiate)** | | | |
| `heading` | `customHeading` (level 1-4) | DOM React | `{level, text}` ↔ `params.{level, text}` |
| `kpi_grid` | `kpiGrid` | DOM React (chips colorés) | `{kpis: [{value, label, unit?, color?}], palette, columns_min}` |
| `kpi_badge` | `kpiBadge` | DOM React (1 KPI inline) | `{value, label, unit?, color?, source?}` |
| `quote` | `customQuote` | DOM React (blockquote DSFR) | `{text, author?, source?}` |
| `separator` | `separator` | DOM React (HR stylisé) | `{style, color, variant}` |
| `narrative_text` | natif BlockNote (paragraph/heading/list/bold) | DOM React natif | `{content: markdown}` → blocks BlockNote |
| `legend` | `legend` | DOM React (chips + source) | `{items: [{label, color}], source}` |
| **Iframe lourds (réutilise rendu Jinja2 + JS hub)** | | | |
| `interactive_map` | `interactiveMap` | iframe `/studies/{sid}/components/{cid}/render` | `{cid}` → ref Component existant |
| `chart` | `chart` | iframe `/render/{cid}` | `{cid}` |
| `data_table` | `dataTable` | iframe `/render/{cid}` (ou DOM table si simple) | `{cid}` ou inline `{columns, rows, source}` |
| `scene_3d` | `scene3d` | iframe `/render/{cid}` (MapLibre fill-extrusion) | `{cid}` |
| `media_embed` | `mediaEmbed` | iframe générique (vidéo, PDF, image) | `{src, type}` |
| `iframe_grist` | `iframeGrist` | iframe Grist natif | `{grist_doc_id, table, view}` |

**Stratégie mixte DOM/iframe** :
- **DOM React** (7 kinds atomiques) : édition inline immédiate, props éditables
  directement dans BlockNote sans round-trip serveur. Pas d'iframe = bundle léger.
- **iframe `/render/{cid}`** (6 kinds lourds) : réutilise le rendu Jinja2 + JS
  MapLibre/ChartJS/DataTables existant côté hub. Pas de duplication code,
  bundle ne explose pas.

**Communication iframe ↔ parent BlockNote** :
- À l'init : iframe envoie `postMessage({type: 'ready', height: <px>})` au parent
- Le parent ajuste `iframe.style.height` dynamiquement
- Au save Assembly : parent envoie `postMessage({type: 'reload-component'})`,
  iframe recharge `/render/{cid}` pour preview frais

### Stack technique

| Aspect | Valeur |
|---|---|
| Framework | React 18+ TypeScript |
| Build | Vite |
| BlockNote | v0.20+ (custom blocks stable API) |
| Théming | Mantine custom (DSFR-aligned, réutilise palette LaSuite Docs) |
| Persistence | Autosave debounce 30s → POST `/studies/{sid}/assemblies/{aid}` (Pydantic Assembly versioned, INSERT-only) |
| Collab CRDT | **Différé Vague future** (Yjs Y-doc — BlockNote y-compatible nativement) |
| Bundle size cible | ~300 KB gzip (acceptable pour usage on-demand) |

### Architecture déploiement

**Option retenue : statique bundle servi par hub FastAPI**

```
qgis-sspcloud/
├─ hub/
│  ├─ static/blocknote-editor/          ← NEW : bundle Vite compilé
│  │   ├─ index.html
│  │   ├─ assets/main-[hash].js
│  │   └─ assets/main-[hash].css
│  ├─ hub/main.py
│  │   ├─ GET  /editor/{sid}/assembly/{aid}     → index.html
│  │   ├─ GET  /studies/{sid}/assemblies/{aid}  (existant)
│  │   ├─ PUT  /studies/{sid}/assemblies/{aid}  (existant, autosave)
│  │   └─ GET  /studies/{sid}/components/{cid}/render (existant, iframe preview)
│  └─ ...
├─ blocknote-editor/                    ← NEW : source React/TS
│  ├─ package.json
│  ├─ vite.config.ts (build → ../hub/static/blocknote-editor/)
│  ├─ src/
│  │   ├─ App.tsx               (load aid depuis URL, fetch assembly)
│  │   ├─ blocks/               (8 custom blocks Vague E2)
│  │   │   ├─ KpiGrid.tsx
│  │   │   ├─ CustomQuote.tsx
│  │   │   ├─ Separator.tsx
│  │   │   ├─ InteractiveMapEmbed.tsx (iframe)
│  │   │   └─ ChartEmbed.tsx (iframe)
│  │   ├─ serializer.ts         (Assembly ↔ BlockNote JSON)
│  │   └─ autosave.ts           (debounce 30s)
│  └─ index.html
└─ ...
```

**Avantages** :
- Pas de nouveau pod K8s (intégré dans le pod hub existant)
- Bundle versionné dans le CI Docker existant
- Déploiement = un seul `set image` (cohérent avec workflow actuel)
- Aucune nouvelle infra à gérer

### Persistence : autosave 30s + optimistic concurrency control

L'éditeur effectue un **autosave debouncé 30s** :
1. À chaque modif user → timer reset
2. 30s d'inactivité → PUT `/studies/{sid}/assemblies/{aid}` avec body :
   ```json
   {
     "manifest": {...},
     "version_num_source": 3  // version chargée au début de l'édition
   }
   ```
3. Hub vérifie `version_num_source == current_version_num` :
   - OK : INSERT new version_num+1, retourne 200 + nouveau version_num
   - KO : HTTP 409 Conflict + message "Conflit — l'assembly a été modifié
     par un autre processus (agent IA chat ?). Recharger ?"
4. UI BlockNote affiche "Sauvegardé il y a Xs" + spinner pendant requête
5. Sur 409 : modal "Recharger" / "Forcer écrasement" (default = Recharger)

**Pas de save manuel obligatoire** (UX moderne Notion/OneNote).

**Optimistic concurrency** indispensable car workflow Vague E1 permet à
l'agent IA de modifier l'assembly via chat en parallèle. Sans contrôle :
risque de perdre des modifs.

**Pas de CRDT Yjs en V1** : édition mono-utilisateur. Multi-user collab
différée. Mais format BlockNote `partialBlocks` est **nativement Y-compatible**
→ migration future facile.

## Conséquences

### Positives

- ✅ Marie peut éditer visuellement après création agent IA (use case manquant)
- ✅ Drag-drop réorganisation sections + composants
- ✅ Édition inline params (KPI values, citations, niveau heading...)
- ✅ Aligné institutionnel : utilisé par État (LaSuite Docs ANCT)
- ✅ Pérenne : BlockNote v0.20+ stable, actif, financé public
- ✅ Sans impact autres projets écosystème (ZEBRA / Atlas / panoramax3d /
  Strate / geoai-kit / MobSciDat) — éditeur isolé dans qgis-sspcloud
- ✅ Y-compatible pour migration multi-user collab future

### Négatives

- ⚠️ Nouveau dossier `blocknote-editor/` (React/TS) dans le repo Python
- ⚠️ CI Docker à étendre (npm + Vite build avant Dockerfile hub)
- ⚠️ Bundle 300 KB initial (acceptable car loaded on-demand depuis desk)
- ⚠️ 2e stack technique à maintenir (Python + TypeScript) — risque
  duplication logique de validation (Pydantic ↔ TypeScript types)

### Risques + mitigations

| Risque | Mitigation |
|---|---|
| Custom blocks iframe (interactive_map) cassent scroll/drag BlockNote | Tester ASAP sur 1 iframe simple. Si bug : `pointer-events:none` overlay |
| Sérialisation lossy Assembly ↔ BlockNote JSON | Tests pytest round-trip obligatoires. Types TS générés depuis Pydantic via `datamodel-code-generator` |
| Bundle 300 KB ralentit ouverture | Lazy load uniquement via `/editor/...`. Pas dans storymap publiée |
| Migration Yjs future | Format `partialBlocks` BlockNote déjà Y-compatible |
| Conflit React versions avec autres frontend | Iframe isolation (sub-window contexte) |

## Plan d'implémentation — 10 micro-commits (optimisé 2026-06-29)

Découpage fin pour réduire risque débug et livrer incrémentalement.
Chaque commit livre quelque chose de testable.

### Bloc E — Setup minimal (3h)

| # | Commit | Effort | Livrable |
|---|---|---|---|
| **E1** | Vite + React + BlockNote "hello world" + CI Docker multi-stage | 2h | Page `/editor/...` affiche BlockNote vide sur image Docker push CI OK |
| **E2** | Endpoint hub + fetch assembly read-only | 1h | Page charge l'assembly via API + affiche les sections en texte brut |

### Bloc F — 13 custom blocks (10h)

| # | Commit | Effort | Livrable |
|---|---|---|---|
| **F1** | 1er custom block (kpi_grid DOM) — pattern de référence | 2h | kpi_grid existant rendu en BlockNote DOM |
| **F2** | 4 autres DOM atomiques (heading, kpi_badge, quote, separator) | 2h | 5 kinds atomiques DOM supportés |
| **F3** | 2 derniers DOM (narrative_text markdown + legend) | 1.5h | 7 kinds DOM supportés |
| **F4** | 3 iframe core (interactive_map, chart, data_table) + postMessage height | 2.5h | 10 kinds rendus (DOM + iframe core) |
| **F5** | 3 derniers iframe (scene_3d, media_embed, iframe_grist) | 2h | 13 kinds supportés (couverture complète) |

### Bloc G — Sérialisation bi-directionnelle (3h)

| # | Commit | Effort | Livrable |
|---|---|---|---|
| **G** | `assembly_to_blocknote_doc()` + inverse + tests round-trip pytest | 3h | Save Assembly → BlockNote JSON → load → Assembly = identique (lossless) |

### Bloc H — Intégration desk + autosave + tag (5h)

| # | Commit | Effort | Livrable |
|---|---|---|---|
| **H1** | Autosave debounce 30s + optimistic concurrency control | 2h | Marie édite, save auto 30s, conflit agent IA géré (HTTP 409 + UI recharge) |
| **H2** | Bouton "✏️ Editer" desk + drawer modal full-height | 2h | Marie ouvre l'éditeur depuis card assembly du desk |
| **H3** | Docs final + axes wikichat sync + tag `v1.7.0-blocknote-editor` | 1h | Capitalisation + tag publié |

**Total réaliste ~21h sur 10 micro-commits** (vs 15h initial sur 4 monolithiques).

### Compromis V1 acceptés

- **Théming DSFR Mantine custom** : différé Vague E4 (polish final). V1
  accepte "Mantine bleu" qui ne match pas strictement DSFR mais reste sobre.
- **Création nouveau composant depuis BlockNote** : différée. V1 = Marie
  ÉDITE les composants existants. Nouveaux composants restent via agent IA.
- **CRDT Yjs multi-user collab** : différée Vague future. Format
  `partialBlocks` BlockNote nativement Y-compatible → migration facile.
- **Tests E2E Playwright** : différés. V1 = tests unit Vitest + pytest
  round-trip suffisent.

### Stack CI/CD

**Dockerfile multi-stage** :
```dockerfile
FROM node:20-alpine AS blocknote-builder
WORKDIR /build
COPY blocknote-editor/package*.json ./
RUN npm ci
COPY blocknote-editor/ ./
RUN npm run build  # output dans dist/

FROM python:3.11-slim AS hub
WORKDIR /app
COPY --from=blocknote-builder /build/dist /app/hub/static/blocknote-editor
COPY hub/ /app/hub/
# ... reste install Python
```

**GitHub Actions** : étape `npm run build` avant Docker build, cache
`node_modules` via `actions/cache@v3`.

**Versioning bundle** : Vite hash assets auto (`main-[hash].js`),
`Cache-Control: public, max-age=31536000, immutable` sur assets, pas sur
`index.html`.

## Cohérence ADR

- D-QGIS-005 (Component V0.1) : préservé (BlockNote ne modifie pas le modèle Component)
- D-QGIS-008 (helper rendu unifié) : réutilisé via iframe `/render/{cid}` pour kinds lourds
- D-QGIS-009 (Vague E1 UX libre + Vague E2 carto) : **étendu** avec UX éditeur block-based comme 2e modalité d'interaction (chat + éditeur visuel)

## Alternatives considérées

| Alternative | Rejetée car |
|---|---|
| **Lexical** (Meta) | Moins block-based opinionated, plus de code custom à écrire |
| **TipTap raw** | Sans la couche slash-menu / block UI déjà faite par BlockNote |
| **Notion-clones** OSS | Moins matures que BlockNote, sans financement institutionnel |
| **ProseMirror raw** | Trop bas niveau, 5x plus de code à maintenir |
| **Iframe vers LaSuite Docs cross-origin** | Impossible (X-Frame-Options + cross-domain protégé). Pas de mécanisme custom blocks compatible |
| **Custom blocks SANS iframe (tout DOM)** | Devrait réimplémenter MapLibre/ChartJS côté React, duplication massive. Bundle exploserait à 1 MB+ |

## Validation

Cet ADR est validé après :

1. ✅ Revue compatibilité écosystème CEREMA (zéro impact autres projets)
2. ✅ Mapping ComponentKind → BlockNote custom blocks complet
3. ✅ Stack technique cohérente avec hub existant
4. ✅ Plan d'implémentation découpé en 4 commits livrables incrémentalement
5. ⏳ Tag `v1.6.7-carto-metier-base` taggé (cf. autres ADR pour timing)

## Référence

- BlockNoteJS : https://www.blocknotejs.org
- LaSuite Docs : https://docs.numerique.gouv.fr
- ANCT (Incubateur des Territoires) : financement BlockNote feature "contenu dépliable"
- Tag cible : `v1.7.0-blocknote-editor`
- Commits prévus : E (setup) + F (custom blocks) + G (sérialisation) + H (intégration desk)
