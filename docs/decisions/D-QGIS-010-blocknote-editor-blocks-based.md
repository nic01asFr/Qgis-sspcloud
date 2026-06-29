# D-QGIS-010 — Éditeur block-based BlockNote pour édition visuelle Assembly

**Statut** : 🟡 Acté en intention (cadrage technique validé, implémentation à suivre)
**Date** : 2026-06-29
**Auteurs** : Nicolas LAVAL, agent IA Claude (qgis-sspcloud)
**Tags / Vague** : `v1.7.0-blocknote-editor` (cible) — Vague E2 pivot UI

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

8 custom blocks couvrent les `ComponentKind` Vague E2 :

| ComponentKind | BlockNote type | Rendering | Mapping `block.props` ↔ `Component.params` |
|---|---|---|---|
| `heading` | `customHeading` (level 1-4) | DOM React | `{level, text}` ↔ `{level, text}` |
| `kpi_grid` | `kpiGrid` | DOM React (chips) | `{kpis: [{value, label, unit?, color?}], palette, columns_min}` |
| `quote` | `customQuote` | DOM React (blockquote) | `{text, author?, source?}` |
| `separator` | `separator` | DOM React (HR) | `{style, color, variant}` |
| `narrative_text` | natif (paragraph/heading/list/bold) | DOM React natif BlockNote | `{content: markdown}` (markdown → blocks BlockNote) |
| `interactive_map` | `interactiveMap` | iframe `/studies/{sid}/components/{cid}/render` | `{cid}` ↔ ref vers Component existant |
| `chart` | `chart` | iframe `/render/{cid}` | `{cid}` |
| `data_table` | `dataTable` | iframe `/render/{cid}` (ou DOM si table simple) | `{cid}` ou `{columns, rows, source}` |

**Stratégie mixte DOM/iframe** (cf. discussion architecturale du 2026-06-29) :
- **DOM React** pour kinds **atomiques** : `heading`, `kpi_grid`, `quote`,
  `separator`, `narrative_text` (light, édition inline immédiate).
- **iframe** `/render/{cid}` pour kinds **lourds** : `interactive_map`,
  `chart`, `data_table` (réutilise le rendu Jinja2 + JS hub, pas de
  réimplémentation côté React).

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

### Persistence : autosave 30s

L'éditeur effectue un **autosave debouncé 30s** :
1. À chaque modif user → timer reset
2. 30s d'inactivité → POST `/studies/{sid}/assemblies/{aid}` (INSERT-only,
   `version_num+1`)
3. Indicateur UI "Sauvegardé il y a Xs" + reconnexion auto si réseau coupé

**Pas de save manuel obligatoire** (UX moderne Notion/OneNote).

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

## Plan d'implémentation

| # | Commit | Effort | Description |
|---|---|---|---|
| E | Setup BlockNote standalone | ~3h | Vite + React + BlockNote v0.20+ + bundle output `hub/static/blocknote-editor/` + endpoint `GET /editor/{sid}/assembly/{aid}` |
| F | 8 custom blocks Vague E2 | ~5h | DOM blocks (heading/kpi_grid/quote/separator/narrative_text) + iframe blocks (interactive_map/chart/data_table) |
| G | Sérialisation bi-dir | ~3h | `assembly_to_blocknote_doc(asm)` + inverse + tests round-trip pytest |
| H | Intégration desk + tag v1.7.0 | ~4h | Bouton "✏️ Editer" sur card + iframe modal + autosave handler + docs + tag `v1.7.0-blocknote-editor` |

**Total ~15h** sur 4 commits.

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
