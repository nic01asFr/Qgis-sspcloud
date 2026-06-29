# API Catalogue Composants & Assemblages — qgis-sspcloud

> Contrat externe livré pour consommateurs cross-écosystème CEREMA (Atlas
> widget Grist, ZEBRA, MobSciDat, panoramax3d, Strate, geoai-kit).
>
> **Vague E1** (D-QGIS-009, 2026-06-29) — Tag `v1.6.5-vague-e1-composition-libre`

## Vue d'ensemble

qgis-sspcloud expose 2 endpoints catalog **cross-étude** qui permettent à un
client (agent IA, user, service tier) de **découvrir** les composants et
assemblages publiés réutilisables au sein de l'écosystème CEREMA.

```
┌─────────────────────────────────────────────────────────────┐
│  ÉCOSYSTÈME CEREMA                                          │
│                                                             │
│  qgis-sspcloud  ←→  ZEBRA  ←→  MobSciDat  ←→  panoramax3d  │
│         │                                                   │
│         ▼                                                   │
│  /catalog/components       (cross-étude, marketplace)       │
│  /catalog/assemblies       (cross-étude, templates)         │
│                                                             │
└─────────────────────────────────────────────────────────────┘
```

## Endpoints

### `GET /catalog/components`

Découverte des composants réutilisables.

#### Query parameters

| Param | Type | Default | Description |
|---|---|---|---|
| `audience` | `Literal["public", "cerema_internal", "restricted", "confidential"]` | `cerema_internal` | Filtre audience (anti-fuite RGPD : default sain) |
| `kind` | `ComponentKind?` | `null` | Filtre par kind (interactive_map, kpi_badge, narrative_text, chart, data_table, legend, scene_3d, media_embed, iframe_grist) |
| `limit` | `int` | `50` | Pagination limit |
| `offset` | `int` | `0` | Pagination offset |

#### Auth

- Cookie OIDC navigateur (user CEREMA connecté)
- OR Bearer `qgisk_<user>_<hex>` (agent partagé Phase 4a)
- OR Bearer `qgis_<hex>` (API key user)

Le scope `qgisk_*` filtre automatiquement les composants visibles selon la
whitelist tools du profile.

#### Response

```json
{
  "items": [
    {
      "cid": "711bb85fb2ad",
      "sid": "c9fef0955a53",
      "owner": "nicolaslaval",
      "kind": "narrative_text",
      "title": "Introduction risque inondation 4e arr.",
      "classification": "cerema_internal",
      "content_hash": "sha256:...",
      "version_num": 3,
      "component_version": "V0.1",
      "created_at": 1782487483,
      "status": "active",
      "file_path": "/data/studies/c9fef0955a53/components/711bb85fb2ad/manifest.json",
      "size_bytes": 1234,
      "provenance_json": "{\"created_by\": \"agent\", ...}"
    }
  ],
  "total": 1,
  "audience": "cerema_internal",
  "kind": null,
  "limit": 50,
  "offset": 0
}
```

### `GET /catalog/assemblies`

Découverte des templates de livrables.

#### Query parameters

Identique à `/catalog/components`, sauf `kind` qui filtre `AssemblyKind` :
`storymap_narrative_dsfr`, `dashboard`, `sheet_a4`, `modal_embed`, `atlas_immersive`.

#### Response

```json
{
  "items": [
    {
      "aid": "c4c9b4aa6504",
      "sid": "c9fef0955a53",
      "owner": "nicolaslaval",
      "kind": "storymap_narrative_dsfr",
      "title": "Storymap risque inondation 4e arr. Marseille",
      "classification": "cerema_internal",
      "version_num": 5,
      "published_url": "https://user-nicolaslaval-qgis.user.lab.sspcloud.fr/published/nicolaslaval/assembly/assembly-c4c9b4aa6504",
      "audit_chain_json": "{\"integrity_hash\": \"sha256:...\", \"components_refs\": [...], ...}"
    }
  ],
  "total": 1,
  "audience": "cerema_internal",
  "kind": null,
  "limit": 50,
  "offset": 0
}
```

## Audience filter — discipline anti-fuite RGPD

| Audience | Sémantique | Use case |
|---|---|---|
| `public` | Vraiment public, indexable web | Composants pédagogiques sans donnée sensible |
| `cerema_internal` ⭐ **DEFAULT** | Collègues CEREMA + partenaires officiels | Default sain anti-fuite |
| `restricted` | Accès via scoped key qgisk_* uniquement | Composants sensibles, agents partagés |
| `confidential` | Archive S3 uniquement, pas listable catalog | Données stratégiques internes |

**Discipline système** : JAMAIS `audience='public'` par défaut. La discipline
agent IA (storymap_creator_v15.yaml) exige confirmation user explicite avant
de publier en audience `public`.

## Pattern d'usage agent IA

Discipline recommandée dans `storymap_creator_v15.yaml` :

```
AVANT chaque create_component, l'agent IA DOIT :

1. list_catalog_components(kind="interactive_map") pour découvrir
   composants similaires existants dans l'écosystème CEREMA
2. Si composant similaire trouvé :
   a. Soit référencer le composant existant via {ref: cid} dans
      layout.sections (pas de duplication)
   b. Soit clone_assembly d'un template puis adapter
3. Si aucun composant similaire :
   create_component from scratch
```

## Use cases écosystème

### Use case 1 — Marie compose une storymap

```
1. Marie demande à l'assistant : "Crée-moi une storymap risque inondation
   pour Marseille 5e arrondissement"
2. Agent IA :
   - list_catalog_assemblies(kind="storymap_narrative_dsfr") → trouve
     storymap référence Blancarde-Chartreux 4e arr.
   - clone_assembly(sid, aid=trouvé, deep=false)
     → nouveau aid + title " (clone)"
   - update_component(sid, cid=narrative_text, manifest=...) pour adapter
     contenu narratif au 5e arr.
   - update_assembly(sid, new_aid, manifest avec section 5e arr.)
   - publish_assembly → URL hub
```

### Use case 2 — ZEBRA publie un composant pour Atlas widget Grist

```
1. ZEBRA détecte un risque sécurité piétons sur un carrefour
2. Service ZEBRA POST /studies/zebra_sid/components avec
   Bearer qgisk_zebra_<hex> :
   {
     "kind": "interactive_map",
     "title": "Carrefour Vieux-Port — densité piétons",
     "source": {"scope": "external", "data_url": "s3://..."},
     "classification": "cerema_internal"
   }
3. Atlas widget Grist GET /catalog/components?kind=interactive_map
   → trouve le composant ZEBRA via cross-étude
4. Atlas embarque iframe /published/zebra/component/{slug}
```

### Use case 3 — Marketplace de templates

Marie cherche un template de storymap diagnostic territorial.

```
GET /catalog/assemblies?kind=storymap_narrative_dsfr&audience=cerema_internal
→ Liste des templates disponibles (Marseille 4e, autres communes CEREMA)
→ clone_assembly du plus proche → adapter
```

## Cohérence avec décisions architecturales

- ✅ **D-QGIS-005** : Component V0.1 source qgis-sspcloud — autorité préservée
- ✅ **D-QGIS-006** : Source Strate-aligned dans `audit_chain.sources[]`
- ✅ **D-FORMAT-008** : `integrity_hash` SHA256 sur audit_chain publié
- ✅ **D-QGIS-008** : helper rendu unifié `_pre_render_component_html` (Vague A)
- ✅ **D-QGIS-009** : Vague E1 UX libre composition (cet ADR)

## Versioning du catalogue

Le catalogue retourne **toujours** la **dernière version** de chaque entité
(`version_num MAX par cid/aid`). Pour accéder à des versions historiques :

```
GET /studies/{sid}/components/{cid}/history
GET /studies/{sid}/assemblies/{aid}/history
```

## Native tools agent IA

| Tool | Endpoint | Description |
|---|---|---|
| `list_catalog_components` | `GET /catalog/components` | Discovery composants cross-étude |
| `list_catalog_assemblies` | `GET /catalog/assemblies` | Discovery assemblages templates |
| `update_component` | `PUT /studies/{sid}/components/{cid}` | Modifier composant sans delete+recreate |
| `clone_assembly` | `POST /studies/{sid}/assemblies/{aid}/clone` | Cloner template assembly |

## Limitations Vague E1

1. **Filtre audience côté `/catalog/assemblies`** : actuellement implémenté
   Python (post-filter). Vague E2 : index DB classification pour optimiser.
2. **`clone_component` pas livré** : seuls les assemblages sont clonables.
   Pour cloner un composant, soit `update_component` après get_component
   (modifier id manuellement n'est pas autorisé), soit attendre Vague E2.
3. **Pagination par offset** : pas de cursor pagination. OK pour catalogue
   < 10000 items.
4. **Pas de search full-text** : seul filtre kind + audience. Recherche par
   titre/description = Vague future (Elasticsearch ?).

## Sécurité

- Endpoints `/catalog/*` : auth obligatoire (cookie OIDC, Bearer scoped key,
  ou API key)
- `audience='public'` strict : composants exposés sans login
- CSP B5 sur `/published/...` : `frame-ancestors *` permet iframe Atlas
  widget Grist + sites tiers CEREMA
- Anti-fuite RGPD : default `cerema_internal` partout, discipline système
  prompt v15 anti-publication accidentelle

## Évolutions Vague E2 envisagées

- `GET /catalog/recipes` : marketplace de recipes user (V1.5 Sprint 1)
- Full-text search via Elasticsearch sur title/description
- Tags / labels custom pour catégorisation
- Préviews iframe inline dans catalog (au lieu de slug seul)
- Statistics : "ce composant est utilisé par N assemblies"

## Référence

- Tag : `v1.6.5-vague-e1-composition-libre`
- ADR : `docs/decisions/D-QGIS-009-vague-e1-ux-libre-composition.md`
- Code : `hub/hub/main.py` (endpoints) + `agent/agent/native_tools_v2.py` (tools)
- Tests : `hub/tests/test_catalog_components.py`
- Profile agent : `hub/hub/profiles/storymap_creator_v15.yaml`

## Contact

Pour intégration cross-projet : voir wikichat `#qgis-sspcloud-sprint-co` ou
`#cerema-templates` (coord Strate-Architect / Passerelle-Archi / Lead-cerema).
