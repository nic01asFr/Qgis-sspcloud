# Plan détaillé — Vague E2 pivot UI BlockNote (10 micro-commits)

> Référence ADR : `docs/decisions/D-QGIS-010-blocknote-editor-blocks-based.md`
> Tag cible : `v1.7.0-blocknote-editor`
> Effort total : ~21h sur 10 commits
> Date plan : 2026-06-29

Plan d'implémentation détaillé pour chaque micro-commit du pivot UI BlockNote.
Chaque commit est livrable indépendamment et apporte une fonctionnalité
incrémentale.

---

## E1 — Setup Vite + React + BlockNote "hello world" + CI Docker (2h)

### Livrable
Page `/editor/{sid}/assembly/{aid}` affiche un éditeur BlockNote vide.
Image Docker push CI OK.

### Fichiers créés/modifiés
```
qgis-sspcloud/
├─ blocknote-editor/                    ← NOUVEAU
│  ├─ package.json
│  ├─ vite.config.ts
│  ├─ tsconfig.json
│  ├─ index.html
│  ├─ .gitignore
│  └─ src/
│     ├─ main.tsx                       (entry React)
│     ├─ App.tsx                        (BlockNote hello world)
│     └─ vite-env.d.ts
├─ hub/
│  ├─ hub/main.py                       (NEW endpoint /editor/{sid}/assembly/{aid})
│  └─ Dockerfile                        (MODIFIED multi-stage)
└─ .github/workflows/
   └─ build-push-docker.yml             (MODIFIED — npm install + build)
```

### Dépendances npm
```json
{
  "dependencies": {
    "@blocknote/core": "^0.22.0",
    "@blocknote/react": "^0.22.0",
    "@blocknote/mantine": "^0.22.0",
    "react": "^18.3.0",
    "react-dom": "^18.3.0"
  },
  "devDependencies": {
    "@types/react": "^18.3.0",
    "@types/react-dom": "^18.3.0",
    "@vitejs/plugin-react": "^4.3.0",
    "typescript": "^5.5.0",
    "vite": "^5.4.0"
  }
}
```

### vite.config.ts clé
```typescript
import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';
import { resolve } from 'path';

export default defineConfig({
  plugins: [react()],
  base: '/static/blocknote-editor/',
  build: {
    outDir: resolve(__dirname, '../hub/static/blocknote-editor'),
    emptyOutDir: true,
    sourcemap: true,
  },
  server: { port: 5173, proxy: { '/studies': 'http://localhost:8000' } },
});
```

### Endpoint hub
```python
@app.get("/editor/{sid}/assembly/{aid}", response_class=HTMLResponse)
async def blocknote_editor_endpoint(
    sid: str, aid: str,
    user: dict = Depends(auth.get_current_user),
):
    """Sert l'éditeur BlockNote standalone. Le React lit aid/sid depuis URL."""
    html_path = Path(__file__).parent / "static" / "blocknote-editor" / "index.html"
    if not html_path.exists():
        raise HTTPException(503, "Bundle BlockNote non build (npm run build)")
    return FileResponse(html_path)
```

### Tests
- pytest : endpoint répond 200 avec auth, 401 sans
- npm `vite build` sans erreur (vérification CI)

### Critères de succès
- ✅ Bundle Vite output dans `hub/static/blocknote-editor/`
- ✅ Image Docker push CI verte
- ✅ GET `/editor/c9fef.../assembly/c4c9b...` retourne HTML BlockNote vide

---

## E2 — Fetch assembly read-only (1h)

### Livrable
Page éditeur charge l'assembly via API + affiche les sections en texte brut
(JSON pretty-printed). Pas d'édition encore.

### Fichiers modifiés
- `blocknote-editor/src/App.tsx` : useEffect fetch `/studies/{sid}/assemblies/{aid}`
- `blocknote-editor/src/types.ts` : types TypeScript dérivés des Pydantic Models

### Logique React
```typescript
const params = new URLSearchParams(window.location.pathname.split('/').slice(-3));
const sid = params.get('sid');
const aid = params.get('aid');

useEffect(() => {
  fetch(`/studies/${sid}/assemblies/${aid}`)
    .then(r => r.json())
    .then(setAssembly);
}, [sid, aid]);
```

### Critères de succès
- ✅ Page charge le manifest de l'assembly
- ✅ Auth cookie OIDC propagé (same-origin)
- ✅ Erreur 404/401 affichée proprement

---

## F1 — 1er custom block (kpi_grid DOM) (2h)

### Livrable
Le composant `kpi_grid` existant dans l'assembly est rendu en custom block
BlockNote DOM. Pattern de référence pour les autres blocks DOM.

### Fichier créé
`blocknote-editor/src/blocks/KpiGrid.tsx`

### API BlockNote custom block
```typescript
import { createReactBlockSpec } from "@blocknote/react";

export const KpiGridBlock = createReactBlockSpec(
  {
    type: "kpiGrid",
    propSchema: {
      cid: { default: "" },  // référence vers Component existant
      kpis: { default: [] }, // array of {value, label, unit?, color?}
      palette: { default: "monochrome" },
      columnsMin: { default: 140 },
    },
    content: "none",
  },
  {
    render: ({ block }) => {
      const { kpis, palette, columnsMin } = block.props;
      return (
        <div style={{ display: 'grid', gridTemplateColumns: `repeat(auto-fit, minmax(${columnsMin}px, 1fr))`, gap: 12 }}>
          {kpis.map((k, i) => (
            <KpiCard key={i} {...k} palette={palette} index={i} />
          ))}
        </div>
      );
    },
  }
);
```

### Critères de succès
- ✅ kpi_grid existant rendu dans BlockNote DOM
- ✅ Éditable inline (cliquer value pour changer)
- ✅ Mapping correct `block.props` ↔ `Component.params`

---

## F2 — 4 autres DOM atomiques (heading, kpi_badge, quote, separator) (2h)

### Livrable
5 kinds atomiques (kpi_grid + heading + kpi_badge + quote + separator) tous
rendus en BlockNote DOM.

### Fichiers créés
- `blocknote-editor/src/blocks/CustomHeading.tsx` (level 1-4, text)
- `blocknote-editor/src/blocks/KpiBadge.tsx` (1 KPI inline horizontal)
- `blocknote-editor/src/blocks/CustomQuote.tsx` (blockquote + author + source)
- `blocknote-editor/src/blocks/Separator.tsx` (HR variants rule/ornament/break)

### Critères de succès
- ✅ 5 kinds atomiques DOM supportés
- ✅ Slash menu `/heading` `/kpi_badge` `/quote` `/separator` créent ces blocks

---

## F3 — 2 derniers DOM (narrative_text + legend) (1.5h)

### Livrable
7 kinds DOM supportés (5 précédents + narrative_text markdown + legend chips).

### Fichiers créés
- `blocknote-editor/src/blocks/NarrativeText.tsx` (markdown → BlockNote natif paragraph/heading/list/bold)
- `blocknote-editor/src/blocks/Legend.tsx` (chips couleur + items + source)

### Note narrative_text
narrative_text en BlockNote est mappé sur les blocks natifs (paragraph,
heading, bulletListItem, etc.) PLUS un wrapper section title. Le markdown
côté Pydantic est parsé puis recomposé en blocks BlockNote.

### Critères de succès
- ✅ Markdown narrative_text round-trip OK
- ✅ Legend chips rendus correctement

---

## F4 — 3 iframe core (interactive_map, chart, data_table) + postMessage (2.5h)

### Livrable
10 kinds supportés (7 DOM + 3 iframe core). Communication iframe ↔ parent
fonctionnelle (height dynamique + reload).

### Fichiers créés
- `blocknote-editor/src/blocks/InteractiveMapEmbed.tsx`
- `blocknote-editor/src/blocks/ChartEmbed.tsx`
- `blocknote-editor/src/blocks/DataTableEmbed.tsx`
- `blocknote-editor/src/iframeHandler.ts` (postMessage handler)
- `hub/hub/maplibre_renderer/_interactive_map_partial.j2` (MODIFIED — postMessage('ready', height))

### Pattern iframe
```typescript
function InteractiveMapEmbed({ block }) {
  const { cid, sid } = block.props;
  const ref = useRef<HTMLIFrameElement>(null);
  const [height, setHeight] = useState(520);

  useEffect(() => {
    const handler = (e: MessageEvent) => {
      if (e.data.type === 'ready' && e.source === ref.current?.contentWindow) {
        setHeight(e.data.height);
      }
    };
    window.addEventListener('message', handler);
    return () => window.removeEventListener('message', handler);
  }, []);

  return (
    <iframe
      ref={ref}
      src={`/studies/${sid}/components/${cid}/render`}
      style={{ width: '100%', height, border: 'none' }}
    />
  );
}
```

### Modif partial Jinja
```html
<script>
  // postMessage height au parent BlockNote
  const totalHeight = document.body.scrollHeight;
  window.parent.postMessage({ type: 'ready', height: totalHeight }, '*');
</script>
```

### Critères de succès
- ✅ interactive_map rendu en iframe dans BlockNote
- ✅ Iframe height dynamique (postMessage)
- ✅ chart + data_table OK

---

## F5 — 3 derniers iframe (scene_3d, media_embed, iframe_grist) (2h)

### Livrable
13 kinds supportés (couverture complète ComponentKind).

### Fichiers créés
- `blocknote-editor/src/blocks/Scene3dEmbed.tsx`
- `blocknote-editor/src/blocks/MediaEmbed.tsx`
- `blocknote-editor/src/blocks/IframeGristEmbed.tsx`

### Critères de succès
- ✅ Tous les ComponentKind supportés
- ✅ Fallback "kind inconnu" gracieux si nouveau kind ajouté futur

---

## G — Sérialisation bi-directionnelle (3h)

### Livrable
Fonctions `assembly_to_blocknote_doc()` + `blocknote_doc_to_assembly()`
côté Python + tests round-trip pytest.

### Fichier créé
`hub/hub/blocknote_serializer.py`

### API Python
```python
from hub.models import Assembly

def assembly_to_blocknote_doc(asm: Assembly) -> list[dict]:
    """Convertit un Assembly Pydantic en liste de blocks BlockNote.

    Retourne : [{ "id": str, "type": str, "props": dict, "children": list }]

    Mapping :
    - AssemblySection avec title -> heading block (level 2)
    - components[].ref -> custom block du kind correspondant
    - narrative_md -> blocks paragraph/heading parsés depuis markdown
    """
    blocks = []
    for section in asm.layout.sections:
        # Section title -> heading block
        if section.title:
            blocks.append({"id": uuid(), "type": "heading",
                          "props": {"level": 2, "text": section.title}})
        # Section narrative_md -> blocks markdown natifs
        if section.narrative_md:
            blocks.extend(markdown_to_blocknote_blocks(section.narrative_md))
        # Components -> custom blocks
        for comp_entry in section.components:
            cid = comp_entry.get("ref")
            comp = await get_component_latest(cid)
            if comp:
                blocks.append(component_to_block(comp))
    return blocks


def blocknote_doc_to_assembly(blocks: list[dict], existing_aid: str) -> dict:
    """Inverse : reconstruit un Assembly manifest depuis BlockNote blocks.

    Regroupe les blocks en sections selon le pattern :
    - Un heading H1/H2 -> nouvelle section (title = heading text)
    - Les blocks suivants -> components de cette section
    - Type 'separator' avec variant=='ornament' -> nouvelle section
    """
    sections = []
    current_section = {"kind": "section", "title": None, "components": []}
    for block in blocks:
        if block["type"] == "heading" and block["props"]["level"] <= 2:
            if current_section["components"] or current_section["title"]:
                sections.append(current_section)
            current_section = {"kind": "section",
                              "title": block["props"]["text"],
                              "components": []}
        else:
            current_section["components"].append({"ref": block["props"].get("cid")})
    if current_section["components"]:
        sections.append(current_section)
    return {
        "kind": "storymap_narrative_dsfr",
        "layout": {"type": "scroll_vertical", "sections": sections},
        # ... autres champs préservés depuis existing
    }
```

### Tests pytest round-trip
```python
def test_round_trip_simple_assembly():
    asm_in = Assembly(...)  # avec 3 sections
    blocks = assembly_to_blocknote_doc(asm_in)
    asm_out = blocknote_doc_to_assembly(blocks, existing_aid=asm_in.id)
    assert asm_in.layout == asm_out.layout  # lossless
```

### Critères de succès
- ✅ Round-trip Assembly → BlockNote → Assembly lossless
- ✅ 5+ tests pytest couvrent les cas (storymap simple, conclusion section,
  appendix, components mix, narrative_text markdown)

---

## H1 — Autosave 30s + optimistic concurrency (2h)

### Livrable
BlockNote sauve automatiquement 30s après dernière modification. Conflit
avec agent IA géré (HTTP 409).

### Fichier créé
`blocknote-editor/src/autosave.ts`

### Logique
```typescript
let saveTimer: number | null = null;
let versionNumSource: number = 0;  // chargé au début

function onBlocksChange(blocks: PartialBlock[]) {
  if (saveTimer) clearTimeout(saveTimer);
  saveTimer = setTimeout(async () => {
    const manifest = await blocknoteDocToAssembly(blocks);
    const res = await fetch(`/studies/${sid}/assemblies/${aid}`, {
      method: 'PUT',
      body: JSON.stringify({ ...manifest, version_num_source: versionNumSource }),
    });
    if (res.status === 409) {
      showConflictModal();  // "Recharger" / "Forcer écrasement"
    } else if (res.ok) {
      const updated = await res.json();
      versionNumSource = updated.version_num;
      showSavedToast();
    }
  }, 30000);  // 30s debounce
}
```

### Modif côté hub
`PUT /studies/{sid}/assemblies/{aid}` :
```python
@app.put("/studies/{sid}/assemblies/{aid}")
async def update_assembly_endpoint(...):
    body = await request.json()
    version_num_source = body.pop("version_num_source", None)
    current = await get_assembly_latest(aid)
    if version_num_source is not None and current["version_num"] != version_num_source:
        raise HTTPException(
            409,
            f"Conflit : version actuelle {current['version_num']}, "
            f"source {version_num_source}. Recharger."
        )
    # ... INSERT new version
```

### Critères de succès
- ✅ Save auto 30s après dernière modif (debounce)
- ✅ HTTP 409 si conflit, UI propose recharge
- ✅ Indicateur "Sauvegardé il y a Xs"

---

## H2 — Bouton "✏️ Editer" desk + drawer modal (2h)

### Livrable
Marie clique "Editer" sur card assembly du desk → ouvre drawer modal
plein-écran avec iframe vers `/editor/{sid}/assembly/{aid}`.

### Fichiers modifiés
- `hub/templates/desk.html` (NEW button "✏️ Editer" + JS openEditorDrawer)
- `hub/templates/desk.html` CSS (NEW drawer styles)

### UX
- Bouton "✏️ Editer" sur chaque card livrable
- Click → drawer s'ouvre à droite (80% largeur)
- Iframe src=`/editor/{sid}/assembly/{aid}`
- Bouton "✕ Fermer" en haut-droite
- Quand close : refresh card desk (manifest peut avoir changé)

### Critères de succès
- ✅ Bouton visible sur card assembly
- ✅ Drawer s'ouvre/ferme proprement
- ✅ Refresh manifest desk après close

---

## H3 — Docs final + axes wikichat + tag v1.7.0 (1h)

### Livrable
- Docs `docs/api/blocknote-editor.md` (contrat externe)
- BILAN_SESSION_2026_06_29.md update bloc "Pivot BlockNote LIVRÉ"
- 2 axes wikichat sync (composants-axis + publication-flow-axis)
- Tag `v1.7.0-blocknote-editor` push + annonce wikichat

### Critères de succès
- ✅ Tag publié sur GitHub
- ✅ Annonce wikichat #qgis-sspcloud-sprint-co
- ✅ README mis à jour

---

## Timeline cible

| Jour | Commits | Cumul effort |
|---|---|---|
| J1 matin | E1 + E2 | 3h |
| J1 après-midi | F1 + F2 | 4h (7h total) |
| J2 matin | F3 + F4 | 4h (11h total) |
| J2 après-midi | F5 + G | 5h (16h total) |
| J3 matin | H1 + H2 | 4h (20h total) |
| J3 après-midi | H3 + tag + buffer | 1h+ (21h+ total) |

**Buffer** : prévoir ~3h buffer (bugs custom blocks iframe, sérialisation
edge cases). Total réaliste 24h sur 3 jours-developer.

## Risques résiduels + plan de secours

| Risque | Probabilité | Impact | Plan B |
|---|---|---|---|
| Custom blocks iframe cassent drag BlockNote | Moyenne | Élevé | Pointer-events:none overlay au drag |
| Sérialisation narrative_text markdown lossy | Moyenne | Moyen | Garder le markdown brut côté Assembly, le re-parser à chaque load |
| BlockNote upgrade casse l'API custom blocks | Faible | Élevé | Pin version exacte `@blocknote/core@0.22.0` |
| CI Docker Vite échoue | Faible | Élevé | Build local en backup, push manuel image |
| Conflit Pydantic ↔ TypeScript types | Moyenne | Faible | Types TS minimaux, validation finale Pydantic au save |

## Validation finale du plan

Ce plan est validé après :
1. ✅ Revue compatibilité écosystème CEREMA (ADR D-QGIS-010)
2. ✅ Mapping 13 ComponentKind → custom blocks complet
3. ✅ Architecture statique bundle hub FastAPI
4. ✅ Découpage 10 micro-commits incrémentables
5. ✅ Optimistic concurrency control vs agent IA chat
6. ✅ Stack CI Docker multi-stage défini
7. ✅ Compromis V1 actés (théming DSFR différé, création nouveau composant différée)
8. ✅ Effort réaliste 21h (vs 15h initial) reconnu

**Prêt pour démarrer E1.**
