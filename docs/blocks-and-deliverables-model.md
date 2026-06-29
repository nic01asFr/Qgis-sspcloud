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

Audit post-v1.7.5 a identifié **10 drifts D1-D10** (cf. §8.0) puis priorisés.

### 8.0 Drifts identifiés (synthèse audit)

| # | Drift | Fichiers concernés | Impact |
|---|---|---|---|
| **D1** | Tools `NATIVE_TOOLS_V2_OPENAI` ne mentionnent pas les 4 kinds Vague E2 (`kpi_grid`, `heading`, `quote`, `separator`) dans la description | `agent/agent/native_tools_v2.py:1469-1472` | LLM faible peut continuer d'halluciner `narrative_text` au lieu d'utiliser les kinds atomiques |
| **D2** | OCC asymétrique : `update_component_endpoint` NE LIT PAS `version_num_source` (vs `update_assembly_endpoint` qui le fait) | `hub/hub/main.py:4027-4145` | Écrasement silencieux Marie BlockNote vs agent IA sur le même `cid` |
| **D3** ⚠ | BlockNote crée TOUJOURS de nouveaux Components au save (jamais d'update_component) — pollution PVC/DB | `blocknote-editor/src/autosave.ts:178-237` | Marie modifie "47→52" 5 fois = 5 nouveaux cid orphelins. Audit_chain inflate |
| **D4** | 3 ComponentKind tombent dans placeholder côté hub (`scene_3d`, `media_embed`, `iframe_grist`) | `hub/hub/main.py:4937-4948` | UX dégradée 3/13 kinds |
| **D5** | Modal "✏️ Éditer" Vague E1 n'envoie pas `version_num_source` (pas d'OCC du tout) | `hub/templates/desk.html:2429+` | Pire que D2, exposé en UI directe Marie |
| **D6** | `AssemblySection.title` round-trip via heading H2 natif → fragmentation involontaire si 2 H2 côte-à-côte | `serializer.ts:62-75` + `autosave.ts:48-56` | Surprise UX V1.7.4, à fixer via block `sectionBreak` invisible V1.8 |
| **D7** | Recipes orphelines du modèle Component (pas de kind `recipe_output` re-exécutable) | `native_tools_v2.py` + `main.py` | Storymap pas re-jouable à scene_hash nouveau sans agent manuel |
| **D8** | Pas de test paramétré `ComponentKind ↔ runtime ↔ helper rendu` | `hub/tests/` | Ajouter un kind sans son runtime = failure surprise CI |
| **D9** | `AssemblyKind` autre que `storymap_narrative_dsfr` → render renvoie 501 | `main.py:4995-5000` (template_map) | Agent peut créer `dashboard`/`sheet_a4` valides Pydantic mais render 501 |
| **D10** | Pas de tool agent pour LIRE les blocks BlockNote (state non-sauvegardé invisible côté agent) | `native_tools_v2.py` | Marie demande "déplace KPI" via chat alors qu'elle a BlockNote ouvert → agent voit l'ancienne version |

### Priorisation

### P0 — À traiter Vague E3 sprint 1 (semaine 1)

#### 8.1 ⚠ NOUVEAU CRITIQUE — BlockNote : update_component vs create_component (D3)

**Problème** : `autosave.ts:saveBlocks` appelle systématiquement
`createComponent` pour chaque DOM block, même si le `cid` existe déjà.
Marie qui modifie "47→52" 5 fois génère 5 nouveaux Components orphelins.

**Pollution mesurable** :
- `components_index` row par modif (×5 sur 5min)
- PVC manifests `*.json` (idem ×5)
- `audit_chain.components_refs` au publish liste les cid jetables
- Tout fonctionne mais dette technique grossit linéairement

**Solution proposée** :
1. `serializer.ts` (forward) : déjà fait pour iframe, propager
   `props.cid = compRef.ref` aussi pour les DOM blocks (kpiGrid,
   customHeading, customQuote, separator, kpiBadge, legend, narrativeText).
2. `autosave.ts` (backward `customBlockToComponent`) :
   ```typescript
   if (props.cid && props.cid !== '') {
     // Modification d'un Component existant
     return { kind, refOnly: false, manifest, existingCid: props.cid };
   } else {
     // Nouveau Component (création)
     return { kind, refOnly: false, manifest };
   }
   ```
3. `autosave.ts:saveBlocks` :
   ```typescript
   if (newComp.existingCid) {
     await updateComponent(sid, newComp.existingCid, newComp.manifest, null);
     // Réutiliser le cid existant dans sections
   } else {
     const { id } = await createComponent(sid, newComp.manifest);
     createdCids.push(id);
   }
   ```
4. Ajouter `updateComponent` dans `api.ts` (5 lignes).

**Effort estimé** : 3-4h.

#### 8.2 Agent IA aligné sur OCC `version_num_source` (D2 + D5)

**Problème combiné** :
- D2 : agent IA `update_assembly` ne pose pas `version_num_source`
- D2bis : `update_component_endpoint` NE LIT PAS `version_num_source` du tout
- D5 : modal "✏️ Éditer" Vague E1 desk.html idem

**Solution proposée** :
1. **Côté hub `update_component_endpoint`** : copier-coller le bloc
   `version_num_source` (`main.py:4242-4258`) dans
   `update_component_endpoint` (`main.py:4027`). Comportement identique :
   pop + compare + 409 structuré.
2. **Agent IA tools** : `native_tools_v2.py` `update_assembly()` +
   `update_component()` acceptent un paramètre optionnel
   `version_num_source: int | None = None`.
3. **Prompt agent système** : ajouter discipline OCC :
   > "Avant `update_*`, appeler `get_*` pour récupérer `version_num` actuel
   > et le passer comme `version_num_source`. Si 409 reçu, re-fetch + retry
   > intelligent."
4. **Modal E1 desk.html** : lire `data.metadata.version_num` au load +
   envoyer dans le PUT save.

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

**Effort estimé** : 2-3h (1 endpoint hub + 2 tools agent + 1 modal + tests).

#### 8.3 Helper rendu `media_embed` / `iframe_grist` (D4 partiel)

**Problème** : 3 des 13 ComponentKind tombent dans placeholder texte côté
`_pre_render_component_html`. UX dégradée pour Marie.

**Solution proposée Vague E3 sprint 1** :
- **`media_embed`** : créer `_media_embed_partial.j2` qui détecte type MIME
  (PDF/video/image) et embed via `<embed>`/`<video>`/`<img>`. Effort : 1h.
- **`iframe_grist`** : créer `_iframe_grist_partial.j2` avec
  `<iframe src=widget_url sandbox="allow-scripts allow-same-origin">`.
  Effort : 30 min.

`scene_3d` renvoyé à Vague E3 sprint 3 (porter pattern atlas_bati Three.js
depuis legacy `hub/hub/storymap_dsfr.py`, ~1 journée).

Total P0 sprint 1 : ~1.5h.

#### 8.4 AssemblyKind limiter à `storymap_narrative_dsfr` (D9)

**Problème** : agent IA peut créer un `dashboard` valide Pydantic, mais
`render_assembly` renvoie 501 (template_map mono-kind).

**Solution courte** : filter `/schema/assembly/kinds` pour ne retourner
que `storymap_narrative_dsfr` tant que les templates Sprint 4 ne sont
pas livrés. **Effort** : 1 ligne + 1 test.

### P1 — Vague E3 sprint 2 (semaine 2)

#### 8.5 Dépréciation modal sections Vague E1 (D5 décision UX)

**Problème** : 2 boutons "Éditer" sur card desk créent confusion + le modal
E1 n'a aucune OCC (D5 critique).

**3 options post-stabilisation V1.7.x** :
1. **Déprécier "✏️ Éditer" complet** — supprimer le code Vague E1 modal
   sections (~200 LOC desk.html + endpoints). BlockNote devient l'unique
   éditeur. **Option recommandée si BlockNote stable en prod 2 semaines.**
2. **Rebrander "Métadonnées"** — modal limité à titre/audience/footer
   uniquement (plus de sections JSON). Sections = BlockNote only.
   Effort : 1-2h.
3. **Unifier** — ajouter un bouton "Voir le JSON" dans BlockNote header qui
   ouvre la modal Vague E1 en read-only. Effort : 2h.

**Recommandation finale** : Option 1 si BlockNote prouvé stable, sinon
Option 2 comme transition.

#### 8.6 Tools OpenAI : mentionner 4 kinds Vague E2 (D1)

**Problème** : description du tool `create_component` dans
`NATIVE_TOOLS_V2_OPENAI` liste 9 kinds historiques mais omet `kpi_grid`,
`heading`, `quote`, `separator`. Un LLM faible function-calling continue
d'halluciner `narrative_text`.

**Solution** : ajouter les 4 dans la description (1 minute).

#### 8.7 Test paramétré ComponentKind ↔ runtime ↔ helper (D8)

**Problème** : pas de test cross-vérifiant que tous les `ComponentKind` ont
au moins 1 `runtime` valide ET sont au moins en placeholder dans
`_pre_render_component_html`. Risque : ajouter un nouveau kind sans son
runtime = failure surprise CI.

**Solution** : test paramétré dans `test_vague_e2_atomic_kinds.py` qui
itère sur les 13 kinds + vérifie présence dans Literal runtime + retour
non-vide du helper.

**Effort** : 30 min.

#### 8.8 Storymap publiée → garantie de cohérence avec BlockNote preview

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

#### 8.9 Helper rendu `scene_3d` (D4 complément)

Porter le pattern atlas_bati Three.js depuis legacy
`hub/hub/storymap_dsfr.py` vers `_scene_3d_partial.j2`. ~1 journée.

#### 8.10 Section break custom block (D6 fix V1.8)

**Problème** : 2 headings H2 natifs côte-à-côte créent une fragmentation
involontaire de section au round-trip (V1.7.4 acknowledged).

**Solution V1.8** : remplacer le délimiteur "heading H2 natif" par un block
custom `sectionBreak` invisible/typographique. Migration round-trip avec
backfill v1.7.x → v1.8.

**Effort** : 4-6h (block + serializer.ts + autosave.ts + tests round-trip
+ backfill).

#### 8.11 BlockNote : DSFR theming strict (différé v1.7.0)

**État** : BlockNote utilise actuellement Mantine "light" (bleu sobre).
Pas strictement DSFR. La storymap publiée EST en DSFR strict → divergence
WYSIWYG.

**Solution Vague E3** : override CSS Mantine variables pour mapper sur DSFR
(`--bn-color-primary: #000091` Marianne blue, etc.). Effort estimé : 3-5h.

#### 8.12 Création nouveau composant depuis BlockNote (slash menu)

**État** : Marie peut MODIFIER des composants existants dans BlockNote, pas en
créer de nouveaux. Pour créer, elle doit demander à l'agent IA.

**Solution Vague E3** : enrichir le slash menu BlockNote avec
`/interactiveMap` `/kpiGrid` etc. qui créent un block vide + ouvrent un panneau
de config (sélection scene_manifest / params). Effort : 5-8h.

#### 8.13 Tests Vitest unit + cohérence cross-langage

**État** : 8 tests pytest critiques livrés v1.7.5 (couvrent côté hub). Côté
React : aucun test Vitest. Côté serializer.ts : pas de test round-trip JS.

**Solution Vague E3** :
- Ajouter Vitest minimal sur `blocksToSections` (round-trip + edge cases).
- Effort : 2-3h.

#### 8.14 Monitoring client errors

**État** : aucune télémétrie côté React → erreurs autosave invisibles côté hub.

**Solution Vague E3** :
- `window.addEventListener('unhandledrejection', ...)` qui POST vers
  `/api/log/client-error`. Endpoint hub stocke dans `client_errors_index`.
- Effort : 2h.

### P3 — Bonus stratégique (non priorisé V1)

#### 8.15 Pousser draft buffer BlockNote vers hub + tool `get_draft_blocks` (D10)

**Problème** : Marie demande "déplace le KPI grid sous la carte" via chat
alors qu'elle a l'éditeur ouvert. L'agent ne voit pas l'état non-sauvegardé.
Mitigation actuelle : autosave 30s. Mais entre 2 saves, l'agent a une vue
stale.

**Solution proposée** :
- POST debounce 3-5s `/studies/{sid}/assemblies/{aid}/draft_blocks` côté
  React → stocke un buffer JSON volatile côté hub.
- Tool agent `get_draft_blocks(sid, aid)` lit ce buffer.
- À l'autosave, buffer effacé (remplacé par save Pydantic).

**Effort** : 4-6h.

#### 8.16 Block `recipe_output` exécutable live (D7)

Nouveau ComponentKind `recipe_output` qui pointe vers slug recette + params.
À l'ouverture BlockNote, ré-exécute la recipe en arrière-plan et met à jour
le composant si les données ont changé. Bouton "🔄 Rafraîchir" dans desk.html
et BlockNote.

**Effort** : 1 semaine.

#### 8.17 CRDT Yjs multi-user collab

BlockNote v0.22 supporte nativement Yjs. Permettrait à Marie + collègues
d'éditer simultanément.

**Effort** : 1-2 semaines (back + front + persistence Y.Doc).

---

## 9. Backlog dette technique tracée (17 items) — état post-v1.9.0

| # | Item | Drift | Tag livraison | Statut |
|---|---|---|---|---|
| **P0 sprint 1** (LIVRÉ v1.8.0, ~8h) | | | | |
| 8.1 | BlockNote `update_component` au save vs create (pollution DB) | **D3 ⚠** | v1.8.0 | ✅ LIVRÉ |
| 8.2 | Agent IA + modal E1 OCC `version_num_source` + endpoint hub `update_component` | D2 + D5 | v1.8.0 | ✅ LIVRÉ |
| 8.3 | Helper render `media_embed` + `iframe_grist` partials Jinja2 | D4 partiel | v1.8.0 | ✅ LIVRÉ (scene_3d différé) |
| 8.4 | `/schema/assembly/kinds` limiter à `storymap_narrative_dsfr` | D9 | v1.8.0 | ✅ LIVRÉ |
| **P1 sprint 2** (LIVRÉ v1.9.0, ~3h) | | | | |
| 8.5 | Dépréciation modal Vague E1 (option 2 rebrand "Métadonnées") | D5 | v1.9.0 | ✅ LIVRÉ (option 2) |
| 8.6 | Tools OpenAI : ajouter 4 kinds Vague E2 dans description | D1 | v1.9.0 | ✅ LIVRÉ |
| 8.7 | Test paramétré ComponentKind ↔ runtime ↔ helper | D8 | v1.9.0 | ✅ LIVRÉ (43 tests) |
| 8.8 | Bouton "👁 Aperçu DSFR strict" depuis BlockNote (drawer modal) | — | v1.9.0 | ✅ LIVRÉ |
| **P2 sprint 3** (LIVRÉ v1.9.0 partiel, ~5h) | | | | |
| 8.9 | Helper render `scene_3d` (Three.js fill-extrusion) | D4 complément | — | ⏳ Vague E3 sprint 4 (1j) |
| 8.10 | Fix H2 vide ne fragmente pas section vide | D6 | v1.9.0 | ✅ LIVRÉ |
| 8.11 | DSFR theming strict BlockNote (Mantine override) | — | v1.9.0 | ✅ LIVRÉ |
| 8.12 | Création composant via slash menu BlockNote | — | — | ⏳ Vague E3 sprint 4 (5-8h) |
| 8.13 | Tests Vitest unit `blocksToSections` (pytest equivalent) | — | v1.9.0 | ✅ LIVRÉ (12 tests pytest) |
| 8.14 | Monitoring client errors (window.error + endpoint hub) | — | v1.9.0 | ✅ LIVRÉ |
| **P3 V2 stratégique (~3-5 semaines effort cumulé)** | | | | |
| 8.15 | Draft buffer BlockNote + tool `get_draft_blocks` agent | D10 | — | ⏳ V2 |
| 8.16 | Block `recipe_output` exécutable live | D7 | — | ⏳ V2 |
| 8.17 | CRDT Yjs multi-user collab | — | — | ⏳ V2 |

**LIVRÉ v1.7.0 → v1.9.0** : 12/17 items (~21h effort réel) — Vague E2 complète + Vague E3 sprints 1-3 essentiels.
**Restant Vague E3 sprint 4** : 8.9 + 8.12 (~13-16h)
**Restant V2** : 8.15 + 8.16 + 8.17 (~3-5 semaines)

**Drifts identifiés audit : 8/10 résolus, 2 différés V2 (D7 + D10).**

## Référence

- ADR : [D-QGIS-005](decisions/D-QGIS-005-composants-format-v0-1.md) (Component V0.1) + [D-QGIS-006](decisions/D-QGIS-006-component-source-strate-aligned.md) (Source Strate) + [D-QGIS-008](decisions/D-QGIS-008-helper-pre-render-composant.md) (helper unifié) + [D-QGIS-009](decisions/D-QGIS-009-vague-e1-ux-libre-composition.md) (Vague E1 UX libre) + [D-QGIS-010](decisions/D-QGIS-010-blocknote-editor-blocks-based.md) (BlockNote)
- API : [docs/api/blocknote-editor.md](api/blocknote-editor.md)
- Plan : [docs/blocknote-editor-plan.md](blocknote-editor-plan.md)
- Bilan : [BILAN_SESSION_2026_06_29.md](../BILAN_SESSION_2026_06_29.md)
