# Scene Manifest V0.2 — contrat consommé par qgis-sspcloud

> **Document de contrat livré à Passerelle-Archi pour D2 (Lead #6 maplibre-threejs-pattern-axis).**
>
> Schema actuel **réellement consommé** par le helper hub
> `_render_assembly_html` lors du pré-rendu inline d'un composant
> `interactive_map` dans une storymap publiée.

## Référence d'autorité

Schema canonique : `cerema-offre-de-service/shared/io/scene_manifest.py`
(décision D4 anti-drift). qgis-sspcloud vendorise dans
`hub/hub/vendor/scene_manifest.py` (cf. `hub/hub/vendor/VENDORED_FROM.md`).

## Format produit par qgis-sspcloud

Endpoint producteur : `POST /studies/{sid}/projects/{pid}/scene_manifest/build`

Fichier persisté : `/data/studies/{sid}/projects/{pid}/scene_manifest.json`

```json
{
  "manifest_version": "V0.2",
  "manifest_id": "56f50530-ce80-4a82-a8d4-29cecee34b80",
  "title": "Scene Manifest",
  "source": {
    "project_qgs": "/data/studies/{sid}/project.qgz",
    "study_id": "c9fef0955a53",
    "project_id": "72e3da47b1f7"
  },
  "layers": [
    {
      "id": "bâtiments__bd_topo_",
      "name": "Bâtiments (BD TOPO)",
      "geometry_type": "polygon",
      "visible": true,
      "style": {
        "qml_source": null,
        "declarative": {
          "kind": "single",
          "color": "#1d70b8",
          "opacity": 1.0
        }
      },
      "geojson_path": "/data/studies/{sid}/projects/{pid}/scene_layers/bâtiments__bd_topo_.geojson",
      "n_features": 14270,
      "geojson_size_bytes": 21196832,
      "crs": "EPSG:4326"
    }
  ]
}
```

## Champs `layer`

| Champ | Type | Origine | Notes |
|---|---|---|---|
| `id` | str (snake_case) | dérivé de `layer.name()` QGIS | Sanitize : alphanum + `_-` |
| `name` | str | QGIS `layer.name()` | Libre, UI display |
| `geometry_type` | `"point"|"line"|"polygon"|"raster"|"vector"` | détecté `layer.geometryType()` | Phase 4b 2026-06-28 |
| `visible` | bool | QGIS `layer.isVisible()` | |
| `style.qml_source` | str|null | OPTIONNEL — QML brut QGIS | Exclu du `scene_hash` |
| `style.declarative` | dict | OBLIGATOIRE — `kind` ∈ `{single, categorized, graduated, rule_based, 3d_model, extrusion}` | cf. axe scene-manifest-axis |
| `geojson_path` | str | Phase 4b NEW : chemin PVC fichier `.geojson` | `/data/studies/{sid}/projects/{pid}/scene_layers/{id}.geojson` |
| `n_features` | int | count total features | Pas de troncature |
| `geojson_size_bytes` | int | taille du fichier | Pour décisions perf |
| `crs` | str | EPSG:4326 forcé | Transform QGIS auto si projet en autre CRS |

## Format `style.declarative` (cf. axe scene-manifest-axis §StyleDeclarative)

### kind=single
```json
{ "kind": "single", "color": "#1d70b8", "opacity": 1.0 }
```

### kind=categorized
```json
{
  "kind": "categorized", "field": "nature",
  "stops": [
    {"value": "Bâtiment", "color": "#1d70b8", "opacity": 1.0},
    {"value": "Annexe", "color": "#d64d00", "opacity": 0.8}
  ],
  "opacity": 1.0
}
```

### kind=graduated
```json
{
  "kind": "graduated", "field": "hauteur",
  "breaks": [0, 5, 10, 20, 50],
  "method": "linear", "ramp": "Viridis",
  "opacity": 1.0
}
```

### kind=extrusion
```json
{
  "kind": "extrusion",
  "height_field": "hauteur",
  "roof_color": "#9c27b0",
  "opacity": 0.85
}
```

### kind=3d_model
```json
{
  "kind": "3d_model",
  "gltf_url": "https://.../arbre.gltf",
  "scale_field": "hauteur_m",
  "rotation_field": null,
  "opacity": 1.0
}
```

## Format GeoJSON dans `scene_layers/{id}.geojson`

```json
{
  "type": "FeatureCollection",
  "features": [
    {
      "type": "Feature",
      "geometry": {
        "type": "Polygon",
        "coordinates": [[[5.39189, 43.31156], ...]]
      },
      "properties": {
        "id": "BATIMENT_0001",
        "hauteur": 12.5,
        "nature": "Bâtiment"
      }
    }
  ]
}
```

Pas de troncature. CRS forcé EPSG:4326.

## Contrat attendu côté geoai-kit (Passerelle-Archi D2)

Signature proposée :
```javascript
applyManifestToMap(
  map: maplibregl.Map,
  manifest: SceneManifestV02,
  options?: {
    threejs?: boolean,        // default true si kind in {3d_model, extrusion}
    onLayerAdd?: (layer) => void,
    fetchGeoJSON?: (geojson_path: string) => Promise<FeatureCollection>,
  }
) => Promise<{added: number, errors: Array<...>}>
```

### Mapping kind → MapLibre layer type

| Scene Manifest `kind` | MapLibre layer type | Custom layer Three.js ? |
|---|---|---|
| `single` (geometry_type=point) | `circle` | non |
| `single` (geometry_type=line) | `line` | non |
| `single` (geometry_type=polygon) | `fill` | non |
| `categorized` | type natif + paint `match` expression | non |
| `graduated` | type natif + paint `step` ou `interpolate` | non |
| `rule_based` | type natif + paint `case` expression | non |
| `extrusion` | `fill-extrusion` natif MapLibre | non |
| `3d_model` | **custom layer** Three.js | **OUI** (pattern atlas_bati maplibre-threejs-pattern-axis §1) |

## Exemple complet d'une storymap publiée par qgis-sspcloud

URL publique :
`https://user-nicolaslaval-qgis.user.lab.sspcloud.fr/published/nicolaslaval/assembly/assembly-c4c9b4aa6504.html`

Audit chain :
- `integrity_hash: sha256:921d2d5b...`
- `components_refs: [711bb85fb2ad, 85a9632cdbff, 9a8b7c6d5e4f, b1c2d3e4f5a6]`
- `scene_hashes: [e13be375fcda03ce]`
- `sources: [{corpus: "BD TOPO", millesime: "2024", authority: "IGN", licence: "Licence Ouverte 2.0", statut: "verifie", url: "https://geoservices.ign.fr/bdtopo"}]`

## Évolutions à venir

- **V0.2.x** : extension `rotation_field` sur marker 2D (point ouvert axe scene-manifest)
- **V0.3** : signature Ed25519 sidecar `.sig` sur scene_manifest publié (cohérent D-FORMAT-008)
- **qgis-sspcloud Phase 4c+** : helper hub gère `scope=external` (data_url S3) et `scope=geomind` (describe(point))

## Références

- Spec autoritaire : `cerema-offre-de-service/docs/scene-manifest-spec.md`
- Modèle Pydantic : `cerema-offre-de-service/shared/io/scene_manifest.py`
- Axe wikichat : `scene-manifest-axis`
- Pattern rendu : `maplibre-threejs-pattern-axis`
- Décision D4 anti-drift : #decisions 2026-05-29
- VENDORED_FROM : `hub/hub/vendor/VENDORED_FROM.md`
