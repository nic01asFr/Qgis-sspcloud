# Modèle blocks, composants et livrables — qgis-sspcloud

> Doc consolidée post-Vague E2 BlockNote (tags v1.7.0 → v1.7.5, 2026-06-29).
> Décrit le modèle d'édition par composants (Component → Assembly → BlockNote)
> ET le plan d'alignement du reste de l'app sur ce modèle.

## 1. Vue d'ensemble — 3 couches

```
┌─ Component (V0.1, atomique) ────────────────────────┐
│   13 kinds Pydantic (interactive_map, kpi_grid...)  │
│   INSERT-only versionné (version_num + content_hash)│
│   Sources Strate-aligned (corpus + millésime + auth)│
│   integrity_hash SHA256 anti-tamper                 │
│   Rendu unifié via _pre_render_component_html()     │
└────────┬────────────────────────────────────────────┘
         │ référencé par cid (12 hex)
         ↓
┌─ Assembly (composition cross-études) ───────────────┐
│   5 kinds : storymap_narrative_dsfr, dashboard,     │
│             sheet_a4, modal_embed, atlas_immersive  │
│   layout.sections[] (kind : intro/section/          │
│                      conclusion/appendix)           │
│   INSERT-only versionné + previous_hash             │
│   audit_chain transverse (OBLIGATOIRE au publish)   │
│   audience : classification 4 niveaux RGPD          │
└────────┬────────────────────────────────────────────┘
         │ mappé via componentKindToBlockType() (Vague E2)
         ↓
┌─ BlockNote document (édition Marie, V1.7.x) ────────┐
│   13 custom block types × 1:1 mapping ComponentKind │
│   7 DOM atomiques (édition inline directe)          │
│   6 iframe (preview via /studies/.../render/{cid})  │
│   Autosave 30s + OCC version_num_source             │
│   Conflict 409 → modal "Recharger" / "Forcer"       │
└─────────────────────────────────────────────────────┘
```

Sources de vérité Pydantic :
- [hub/hub/models/component.py:37](../hub/hub/models/component.py#L37) — `ComponentKind` Literal[13]
- [hub/hub/models/assembly.py:32](../hub/hub/models/assembly.py#L32) — `AssemblyKind` Literal[5] + `AssemblySection.kind` Literal[4]

## 2. ComponentKind — 13 types atomiques

| Kind | Rendu hub partial | BlockNote type | Édition Marie | Acteur principal |
|---|---|---|---|---|
| **DOM atomiques (édition inline BlockNote)** | | | | |
| `kpi_grid` | `_kpi_grid_partial.j2` | `kpiGrid` | ✅ Direct | Agent IA + Marie |
| `heading` | `_heading_partial.j2` | `customHeading` | ✅ Direct | Agent IA + Marie |
| `kpi_badge` | `_kpi_badge_partial.j2` | `kpiBadge` | ✅ Direct | Agent IA + Marie |
| `quote` | `_quote_partial.j2` | `customQuote` | ✅ Direct | Agent IA + Marie |
| `separator` | inline HTML | `separator` | ✅ Direct | Agent IA + Marie |
| `legend` | `_legend_partial.j2` | `legend` | ✅ Direct | Agent IA + Marie |
| `narrative_text` | `_narrative_text_partial.j2` | `narrativeText` | ✅ Direct | Agent IA + Marie |
| **Iframe lourds (preview via /render/{cid})** | | | | |
| `interactive_map` | `_interactive_map_partial.j2` (MapLibre) | `interactiveMap` | Preview only | Agent IA (recipe) |
| `chart` | `_chart_partial.j2` (Chart.js) | `chart` | Preview only | Agent IA (recipe) |
| `data_table` | `_data_table_partial.j2` (DataTables) | `dataTable` | Preview only | Agent IA (recipe) |
| `scene_3d` | ⚠ **placeholder V1** | `scene3d` | Preview only | Agent IA (recipe Vague E3) |
| `media_embed` | ⚠ **placeholder V1** | `mediaEmbed` | Preview only | Marie (PDF upload) |
| `iframe_grist` | ⚠ **placeholder V1** | `iframeGrist` | Preview only | Marie (lien Grist) |

**Limitation V1 actée** : `scene_3d` / `media_embed` / `iframe_grist` tombent dans
le fallback `_pre_render_component_html` (placeholder texte "Voir l'aperçu
interactif"). À upgrader Vague E3.

Le mapping `componentKindToBlockType()` est défini côté React dans
[blocknote-editor/src/blocks/index.ts](../blocknote-editor/src/blocks/index.ts)
et son inverse `BLOCK_TYPE_TO_COMPONENT_KIND` dans
[blocknote-editor/src/autosave.ts](../blocknote-editor/src/autosave.ts) — **invariant
critique : maintenir la bijection 13×13.**

## 3. Assembly — composition cross-études

```
Assembly {
  id, sid, kind, title, audience,        # identité scope
  layout: {                              # composition
    type: 'scroll_vertical' | 'grid' | 'paginated' | 'fullscreen' | ...,
    sections: [
      AssemblySection {
        kind: 'intro' | 'section' | 'conclusion' | 'appendix',
        title: str | None,
        narrative_md: str | None,        # markdown Marked.js
        components: [{ref: 'cid'}, ...]  # 1:N components atomiques
      },
      ...
    ]
  },
  footer: { sources, audit_trail_ref, disclaimer, ... },  # DSFR obligatoire
  audit_chain: { signed_hash, components_refs, scene_hashes, ... },  # publish
  provenance, version, content_hash      # invariants audit trail
}
```

**Patterns métier livrés** (Vague E2 base, v1.6.6) : 6 patterns canoniques pour
storymap (`hero_constat`, `zoom_territoire`, `croisement_enjeu`,
`fiche_indicateur`, `reliability_summary`, `conclusion_actionnable`) →
[hub/hub/storymap_patterns.py](../hub/hub/storymap_patterns.py).

**Grammaire narrative** : `AssemblySection.kind` détermine la différenciation
visuelle (CSS `--atomic` intro vs section vs conclusion vs appendix), pas le
contenu. La grammaire IGN/DSFR recommande intro→sections→conclusion.

## 4. Cycle de vie : Création → Édition → Publication

```
┌─ 1. CRÉATION (agent IA) ─────────────────────────┐
│                                                  │
│  Marie : "Crée une storymap risque inondation"   │
│           ↓                                      │
│  Agent IA :                                      │
│    a. list_catalog_components → réutiliser ?     │
│    b. recipe pyqgis exec → scene_manifest        │
│    c. create_component(kind='interactive_map',   │
│                        source={scene_hash:...})  │
│       → {cid}                                    │
│    d. create_component(kind='kpi_grid', ...)     │
│       → {cid2}                                   │
│    e. create_assembly(kind='storymap_...',       │
│                       layout.sections=[          │
│                         {components:[{ref:cid},  │
│                                      {ref:cid2}]}│
│                       ])                         │
│       → {aid, version_num:1}                     │
│                                                  │
│  Storymap exists in DB (draft, audience defaut)  │
└──────────────────────┬───────────────────────────┘
                       │
┌─ 2. ÉDITION (Marie via BlockNote) ───────────────┐
│                                                  │
│  Marie clique "📝 BlockNote" sur card desk       │
│           ↓                                      │
│  GET /editor/{sid}/assembly/{aid}                │
│    → React bundle Vite (Cache immutable)         │
│  fetchAssembly → manifest + version_num:1        │
│  Promise.allSettled fetchComponent × N           │
│    → 13 custom blocks rendus dans BlockNote      │
│                                                  │
│  Marie édite un kpi_grid : 47% → 52%             │
│  Timer 30s reset à chaque keystroke              │
│           ↓ 30s inactivité                       │
│  blocksToSections() → (sections, new_components) │
│  POST /studies/{sid}/components × N (DOM modifs) │
│    → {cid_new}                                   │
│  PUT /studies/{sid}/assemblies/{aid} {           │
│    layout.sections: [...new_cids],               │
│    version_num_source: 1                         │
│  }                                                │
│    200 → version_num:2 + 'Sauvegardé v2'         │
│    409 → modal "Recharger" / "Forcer écrasement" │
│                                                  │
│  L'aid reste stable. Audit trail INSERT-only.    │
└──────────────────────┬───────────────────────────┘
                       │
┌─ 3. PUBLICATION ─────────────────────────────────┐
│                                                  │
│  Marie : Quick action "🚀 Publier" sur card desk │
│  ou demande agent IA "Publie cette storymap"     │
│           ↓                                      │
│  POST /studies/{sid}/assemblies/{aid}/publish    │
│    1. Recalcule audit_chain transverse           │
│       (scene_hashes + components_refs +          │
│        recipes_used)                             │
│    2. Génère SHA256 signed_hash canonique        │
│    3. Rend HTML via storymap_dsfr.html.j2        │
│       (Jinja2, MÊME helper _pre_render_*)        │
│    4. Push S3 + audit_chain JSON                 │
│    5. Update assemblies_index.published_url      │
│                                                  │
│  URL publique : https://.../published/{slug}/    │
│  Accessible via card desk "👁 Voir"              │
└──────────────────────────────────────────────────┘
```

## 5. 3 acteurs, 1 modèle partagé

| Acteur | Surface | Crée | Modifie | OCC |
|---|---|---|---|---|
| **Agent IA** | Tools `native_tools_v2.py` | ✅ `create_component`, `create_assembly` | ✅ `update_component`, `update_assembly` | ⚠ **Pas envoyé** (legacy) |
| **Marie (BlockNote)** | `/editor/{sid}/assembly/{aid}` | ✅ Indirectement (POST components au save) | ✅ Édition inline + autosave | ✅ `version_num_source` |
| **Marie (Vague E1 modal sections)** | Bouton "✏️ Éditer" desk | ❌ Pas de création | ✅ Édition manifest JSON | ❌ Pas d'OCC |
| **Marie (chat agent IA)** | Onglet Chat desk | ✅ Via agent IA | ✅ Via agent IA | ⚠ Hérite du legacy agent IA |
| **Publication** | `storymap_dsfr.html.j2` | ❌ Read-only | ❌ Read-only | N/A |

**Asymétrie OCC livrée V1.7.x** : BlockNote envoie `version_num_source` et
détecte les modifs concurrentes ; l'agent IA ne l'envoie pas → **risque
théorique** : Marie édite BlockNote, demande à l'agent IA "ajoute un KPI" en
parallèle, l'agent peut écraser silencieusement les modifs BlockNote en cours.
À résoudre Vague E3 (cf. §8 plan).

## 6. Helper rendu unifié (D-QGIS-008)

Source unique pour le rendu HTML d'un Component :
[hub/hub/main.py:4676 `_pre_render_component_html()`](../hub/hub/main.py#L4676).

Consommé par 3 surfaces :
1. **`GET /studies/{sid}/components/{cid}/render`** — endpoint standalone iframe
   (utilisé par les 6 iframe blocks BlockNote)
2. **`_render_assembly_html`** — pré-rendu inline storymap publiée
3. **`render_legacy`** — endpoint debug si pré-rendu unifié casse

**Bénéfice critique** : ce que Marie voit dans BlockNote (via iframe) est
**identique** à ce qui sera publié dans la storymap finale. Pas de divergence
édition vs publi.

Templates partials : `hub/hub/maplibre_renderer/_{kind}_partial.j2` (embeddable,
sans `<head>/<body>`). Couverture :
- ✅ `interactive_map`, `chart`, `data_table`, `kpi_badge`, `kpi_grid`,
  `heading`, `quote`, `separator`, `legend`, `narrative_text`
- ❌ `scene_3d`, `media_embed`, `iframe_grist` → fallback placeholder

## 7. Invariants

Règles dures à respecter sous peine de casser la chaîne d'explicabilité CEREMA :

1. **Audit trail INSERT-only** : tout `update_*` insère une nouvelle row
   `*_index` avec `version_num+1` et `previous_hash = ancien content_hash`.
   L'id (cid/aid) est stable, l'historique complet préservé. Cf.
   `hub/hub/components.py` + `hub/hub/assemblies.py`.

2. **Source Strate-aligned** (D-QGIS-006) : `ComponentSource` discriminé par
   `scope` (project/study/external/geomind) + corpus + millésime + authority +
   licence. Pas de fallback URL anonyme.

3. **integrity_hash SHA256** (D-FORMAT-008) : calculé sur le manifest sérialisé
   canonique (exclut id + version_num + provenance.created_at). Anti-tamper.

4. **Helper rendu unifié** (D-QGIS-008) : un seul chemin de rendu pour chaque
   `kind`, partagé entre édition BlockNote (iframe) et publication storymap.

5. **Bijection 13 ComponentKind ↔ 13 BlockNote types** : si on ajoute un
   ComponentKind, ajouter le block React correspondant ET le partial Jinja2.
   Source : `componentKindToBlockType()` et inverse.

6. **OCC `version_num_source`** : v1.7.x livré côté BlockNote. **À étendre à
   l'agent IA Vague E3** pour résoudre l'asymétrie.

7. **`AssemblySection.kind`** : 4 valeurs (intro/section/conclusion/appendix).
   Détermine différenciation visuelle, pas le contenu (n'a pas son équivalent
   côté BlockNote — round-trip préservé via heading H2 vides, v1.7.4).

8. **`narrative_md`** : pas tronqué côté serializer.ts (audit P0 v1.7.1). Le
   texte complet est round-tripé sans perte.

## 8. Plan d'alignement reste de l'app sur le modèle blocks

L'audit roadmap post-v1.7.5 a identifié 4 zones d'alignement à traiter.
Priorisées par impact pour Marie + cohérence architecturale :

### P0 — À traiter Vague E3 sprint 1 (semaine 1)

#### 8.1 Agent IA aligné sur OCC `version_num_source`

**Problème** : `update_assembly` côté agent IA ne pose pas
`version_num_source` → écrasement silencieux possible.

**Solution proposée** :
1. Modifier `agent/agent/native_tools_v2.py` `update_assembly()` :
   ```python
   async def update_assembly(
       sid, aid, manifest, version_num_source: int | None = None,
   ):
       body = {**manifest}
       if version_num_source is not None:
           body["version_num_source"] = version_num_source
       return await _hub_call("PUT", f"/studies/{sid}/assemblies/{aid}",
                              json_body=body)
   ```
2. Forcer l'agent à appeler `get_assembly` AVANT `update_assembly` pour
   récupérer `version_num` actuel et le passer comme `version_num_source`.
   Mise à jour du prompt système agent (ajout discipline OCC).
3. Si 409 : l'agent re-fetch + retry intelligent (vs Marie qui décide
   manuellement). Comportement par défaut = retry avec version_num actuel
   (le hub renvoie à l'agent les modifs récentes de Marie).

**Effort estimé** : 1-2h (1 endpoint + 1 prompt update + test).

#### 8.2 Helper rendu `scene_3d` / `media_embed` / `iframe_grist`

**Problème** : 3 des 13 ComponentKind tombent dans placeholder texte côté
`_pre_render_component_html`. UX dégradée pour Marie (preview Scene 3D ne
montre rien d'utile).

**Solution proposée** :
- **`scene_3d`** : créer `_scene_3d_partial.j2` avec MapLibre fill-extrusion
  basique (cf. `panoramax3d` repo qui a déjà la logique 3D buildings).
  Effort : 4-6h (réutiliser code panoramax3d).
- **`media_embed`** : créer `_media_embed_partial.j2` qui détecte type MIME
  (PDF/video/image) et embed via `<embed>`/`<video>`/`<img>`. Effort : 1h.
- **`iframe_grist`** : créer `_iframe_grist_partial.j2` avec
  `<iframe src=widget_url>`. Effort : 30 min.

Total : ~6-8h Vague E3 sprint 1.

### P1 — Vague E3 sprint 2 (semaine 2)

#### 8.3 Dépréciation modal sections Vague E1 ou positionnement clair

**Problème** : 2 boutons "Éditer" sur card desk créent confusion :
- "✏️ Éditer" (Vague E1) : modal JSON sections, pas d'OCC
- "📝 BlockNote" (Vague E2) : éditeur visuel block-based, OCC complet

**3 options** :
1. **Déprécier "✏️ Éditer"** — supprimer le code Vague E1 modal sections
   (~200 LOC desk.html + endpoints). Risque : si BlockNote a un bug en prod,
   pas de fallback. **Recommandé après stabilisation V1.7.x.**
2. **Garder comme "mode expert JSON"** — renommer le bouton "🔧 Édition JSON
   avancée" et le cacher derrière un menu "Plus d'actions...". Effort : 30 min.
3. **Unifier** — ajouter un bouton "Voir le JSON" dans BlockNote header qui
   ouvre la modal Vague E1 en read-only. Effort : 2h.

**Recommandation** : option 2 (cacher derrière menu) pour V1.8, puis option 1
(déprécier complet) Vague E3 sprint 3.

#### 8.4 Storymap publiée → garantie de cohérence avec BlockNote preview

**État actuel** : le helper rendu est unifié (✅ D-QGIS-008), donc l'iframe
BlockNote `/render/{cid}` et la storymap publiée utilisent **le même HTML**.
Pas de drift code.

**Risque** : `storymap_dsfr.html.j2` ajoute des wrappers DSFR (header, footer,
sources, mentions légales) que BlockNote n'a PAS. Marie voit un rendu BlockNote
"nu" et peut être surprise du résultat publié.

**Solution proposée** :
- Ajouter un bouton "👁 Aperçu storymap (DSFR strict)" dans BlockNote header
  qui ouvre `/studies/{sid}/assemblies/{aid}/render` dans un drawer modal.
- Effort : 1h (juste un bouton + iframe modal).

### P2 — Vague E3 sprint 3 (polish)

#### 8.5 BlockNote : DSFR theming strict (différé v1.7.0, à acter)

**État** : BlockNote utilise actuellement le thème Mantine "light" (bleu sobre).
Pas strictement DSFR. La storymap publiée EST en DSFR strict.

**Solution Vague E3** : override CSS Mantine variables pour mapper sur DSFR
(`--bn-color-primary: #000091` Marianne blue, etc.). Effort estimé : 3-5h.

#### 8.6 Création nouveau composant depuis BlockNote (slash menu)

**État** : Marie peut MODIFIER des composants existants dans BlockNote, pas en
créer de nouveaux. Pour créer, elle doit demander à l'agent IA.

**Solution Vague E3** : enrichir le slash menu BlockNote avec
`/interactiveMap` `/kpiGrid` etc. qui créent un block vide + ouvrent un panneau
de config (sélection scene_manifest / params). Effort : 5-8h.

#### 8.7 Tests Vitest unit + cohérence cross-langage

**État** : 8 tests pytest critiques livrés v1.7.5 (couvrent côté hub). Côté
React : aucun test Vitest. Côté serializer.ts : pas de test round-trip JS.

**Solution Vague E3** :
- Ajouter Vitest minimal sur `blocksToSections` (round-trip + edge cases).
- Effort : 2-3h.

#### 8.8 Monitoring client errors

**État** : aucune télémétrie côté React → erreurs autosave invisibles côté hub.

**Solution Vague E3** :
- `window.addEventListener('unhandledrejection', ...)` qui POST vers
  `/api/log/client-error`. Endpoint hub stocke dans `client_errors_index`.
- Effort : 2h.

### P3 — Bonus stratégique (non priorisé)

#### 8.9 CRDT Yjs multi-user collab

BlockNote v0.22 supporte nativement Yjs. Permettrait à Marie + collègues
d'éditer simultanément. Effort : 1-2 semaines (back + front + persistence Y.Doc).

#### 8.10 Block `recipe_output` exécutable live

Idée : un nouveau ComponentKind `recipe_output` qui pointe vers un slug de
recette + params. À l'ouverture BlockNote, on ré-exécute la recipe en arrière-plan
et on met à jour le composant si les données ont changé. Effort : 1 semaine.

---

## 9. Backlog dette technique tracée

| # | Item | Vague cible | Effort |
|---|---|---|---|
| 8.1 | Agent IA OCC `version_num_source` | E3 sprint 1 | 1-2h |
| 8.2 | Helper render scene_3d/media_embed/iframe_grist | E3 sprint 1 | 6-8h |
| 8.3 | Déprécier modal sections Vague E1 | E3 sprint 2 | 30min-2h |
| 8.4 | Aperçu DSFR strict depuis BlockNote | E3 sprint 2 | 1h |
| 8.5 | DSFR theming strict BlockNote | E3 sprint 3 | 3-5h |
| 8.6 | Création composant via slash menu | E3 sprint 3 | 5-8h |
| 8.7 | Tests Vitest unit (blocksToSections) | E3 sprint 3 | 2-3h |
| 8.8 | Monitoring client errors | E3 sprint 3 | 2h |
| 8.9 | CRDT Yjs multi-user collab | E4 | 1-2sem |
| 8.10 | Block `recipe_output` exécutable | E4 | 1sem |

**Total estimé Vague E3 (8.1 → 8.8)** : 21-32h sur 3 sprints.

## Référence

- ADR : [D-QGIS-005](decisions/D-QGIS-005-composants-format-v0-1.md) (Component V0.1) + [D-QGIS-006](decisions/D-QGIS-006-component-source-strate-aligned.md) (Source Strate) + [D-QGIS-008](decisions/D-QGIS-008-helper-pre-render-composant.md) (helper unifié) + [D-QGIS-009](decisions/D-QGIS-009-vague-e1-ux-libre-composition.md) (Vague E1 UX libre) + [D-QGIS-010](decisions/D-QGIS-010-blocknote-editor-blocks-based.md) (BlockNote)
- API : [docs/api/blocknote-editor.md](api/blocknote-editor.md)
- Plan : [docs/blocknote-editor-plan.md](blocknote-editor-plan.md)
- Bilan : [BILAN_SESSION_2026_06_29.md](../BILAN_SESSION_2026_06_29.md)
