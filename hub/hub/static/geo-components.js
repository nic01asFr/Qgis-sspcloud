/**
 * geo-components — Web Components CEREMA pour cartes MapLibre et composants
 * controllers pilotés. Brique commune cross-projet (qgis-sspcloud, Widgets Grist,
 * CRM-Grist, ZEBRA, MobSciDat, atlas-territorial, SURFAC²E, cerema-livrables).
 *
 * Conçu pour le navigateur : ES module, zéro dépendance runtime propre, ouvert
 * dans un widget Grist self-contained, une PWA, une page HTML statique, un
 * iframe SSR de storymap, un shadow DOM d'application React/Vue/Lit.
 *
 * Peer-dépendance optionnelle : `geoai-kit@^0.2.0` (utilise `styleByField` si
 * présent, sinon fallback interne).
 *
 * Peer-dépendance obligatoire : `maplibre-gl@^5.6.1` chargé globalement dans
 * la page hôte (via CDN unpkg, bundler applicatif, ou tag script). Le composant
 * détecte l'absence et affiche un fallback.
 *
 * Contrat de données : `InteractiveMapParams V1.13` défini par qgis-sspcloud
 * dans `hub/hub/models/component_params.py`. Voir README.md et MIGRATION.md.
 *
 * Composants livrés v0.1 :
 *   - <geo-map>      : wrapper MapLibre reader thématique 2D
 *   - <geo-timeline> : controller slider temporel avec play/pause
 *   - <geo-legend>   : légende métier 3 formats (chips/gradient_bar/proportional)
 *
 * Pattern de binding : CustomEvent 'geo:bind' bubbling + composed, relayé par
 * un orchestrateur singleton attaché au document. Cross-iframe via postMessage
 * avec validation d'origine. Pattern inspiré de Widgets Grist
 * `skills/inter-widget.md` (convention `app.emit`/`app.on` Artefactory) et
 * `skills/bridge.md` (pattern grist-bridge callback + retarget iframe),
 * adapté au niveau Custom Elements avec un événement dédié 'geo:bind'.
 * Voir table de correspondance dans MIGRATION.md.
 *
 * @module geo-components
 * @version 0.1.0-alpha.0
 */

"use strict";

// ──────────────────────────────────────────────────────────────────────────
// 0. Constantes et catalogue
// ──────────────────────────────────────────────────────────────────────────

/** Version de la lib. */
export const LIB_VERSION = "0.2.0-alpha.6";

/**
 * Catalogue des 6 basemaps du contrat InteractiveMapParams V1.13.
 * Chaque entrée fournit un style MapLibre standalone (raster ou vector).
 * Extension V1.22 prévue : CARTO Positron, dark auto.
 */
export const BASEMAPS = {
  "osm": {
    label: "OpenStreetMap",
    style: {
      version: 8,
      sources: {
        osm: {
          type: "raster",
          tiles: ["https://tile.openstreetmap.org/{z}/{x}/{y}.png"],
          tileSize: 256,
          attribution: "© OpenStreetMap contributors",
        },
      },
      layers: [{ id: "osm", type: "raster", source: "osm" }],
    },
  },
  "plan-ign-v2": {
    label: "Plan IGN",
    style: {
      version: 8,
      sources: {
        ign: {
          type: "raster",
          tiles: [
            "https://data.geopf.fr/wmts?SERVICE=WMTS&REQUEST=GetTile&VERSION=1.0.0" +
              "&LAYER=GEOGRAPHICALGRIDSYSTEMS.PLANIGNV2&STYLE=normal" +
              "&FORMAT=image/png&TILEMATRIXSET=PM&TILEMATRIX={z}&TILEROW={y}&TILECOL={x}",
          ],
          tileSize: 256,
          attribution: "© IGN Géoplateforme",
        },
      },
      layers: [{ id: "ign", type: "raster", source: "ign" }],
    },
  },
  "ortho-ign": {
    label: "Photos aériennes IGN",
    style: {
      version: 8,
      sources: {
        ortho: {
          type: "raster",
          tiles: [
            "https://data.geopf.fr/wmts?SERVICE=WMTS&REQUEST=GetTile&VERSION=1.0.0" +
              "&LAYER=ORTHOIMAGERY.ORTHOPHOTOS&STYLE=normal" +
              "&FORMAT=image/jpeg&TILEMATRIXSET=PM&TILEMATRIX={z}&TILEROW={y}&TILECOL={x}",
          ],
          tileSize: 256,
          attribution: "© IGN Géoplateforme",
        },
      },
      layers: [{ id: "ortho", type: "raster", source: "ortho" }],
    },
  },
  "dsfr-sobre": {
    label: "Plan sobre DSFR",
    style: {
      version: 8,
      sources: {
        dsfr: {
          type: "raster",
          tiles: [
            "https://tiles.stadiamaps.com/tiles/alidade_smooth/{z}/{x}/{y}.png",
          ],
          tileSize: 256,
          attribution: "© Stadia Maps © OpenMapTiles © OpenStreetMap",
        },
      },
      layers: [{ id: "dsfr", type: "raster", source: "dsfr" }],
    },
  },
  "hillshade-ign": {
    label: "Estompage IGN",
    style: {
      version: 8,
      sources: {
        hs: {
          type: "raster",
          tiles: [
            "https://data.geopf.fr/wmts?SERVICE=WMTS&REQUEST=GetTile&VERSION=1.0.0" +
              "&LAYER=ELEVATION.SLOPES&STYLE=normal" +
              "&FORMAT=image/png&TILEMATRIXSET=PM&TILEMATRIX={z}&TILEROW={y}&TILECOL={x}",
          ],
          tileSize: 256,
          attribution: "© IGN Géoplateforme",
        },
      },
      layers: [{ id: "hs", type: "raster", source: "hs" }],
    },
  },
  "etalab": {
    label: "Etalab",
    style: {
      version: 8,
      sources: {
        etalab: {
          type: "raster",
          tiles: [
            "https://openmaptiles.geo.data.gouv.fr/styles/osm-bright/{z}/{x}/{y}.png",
          ],
          tileSize: 256,
          attribution: "© Etalab © OpenMapTiles © OpenStreetMap",
        },
      },
      layers: [{ id: "etalab", type: "raster", source: "etalab" }],
    },
  },
};

/** Palette DSFR CEREMA par défaut (fallback quand aucune classification). */
export const DEFAULT_PALETTE = [
  "#000091", // bleu Marianne
  "#e1000f", // rouge Marianne
  "#1f8d4d", // vert
  "#ff6f00", // orange
  "#9c27b0", // violet
  "#0288d1", // bleu clair
];

/** Marseille 4e arr., défaut historique cohérent avec qgis-sspcloud. */
export const DEFAULT_CENTER = [5.39, 43.30];
export const DEFAULT_ZOOM = 13;

// ──────────────────────────────────────────────────────────────────────────
// 1. Helpers internes
// ──────────────────────────────────────────────────────────────────────────

/**
 * Détecte MapLibre GL JS chargé globalement. Retourne l'objet ou null.
 * Ne charge PAS MapLibre : c'est à la page hôte de le fournir.
 */
function _detectMapLibre() {
  if (typeof window === "undefined") return null;
  return window.maplibregl || null;
}

// V0.3.1 shim (Sprint V0.2 Chantier 7, 2026-07-10) : le contract publie
// SceneManifest V0.3.1 impose params.layers[] + params.basemap.id + zone
// discriminee (insee_arm/commune/manual/study). Le composant historique
// (V1.13) lit params.layers_override + _hostContext.catalog_layers +
// params.basemap_id. Ce shim normalise V0.3.1 vers la structure legacy
// interne sans casser les consommateurs V1.13.
const _V031_INSEE_ARM_BBOX = {
  "13204": [5.379, 43.289, 5.427, 43.334],
  "13201": [5.360, 43.294, 5.394, 43.309],
  "13202": [5.363, 43.309, 5.393, 43.328],
  "13203": [5.374, 43.311, 5.408, 43.335],
  "75104": [2.348, 48.851, 2.365, 48.862],
  "75105": [2.336, 48.840, 2.362, 48.856],
  "69381": [4.827, 45.766, 4.848, 45.780]
};

function _v031ToLegacyShim(params, hostContext) {
  if (!params || typeof params !== "object") return { params, hostContext };
  // alpha.6 : detecter aussi _catalog_layers injecte direct par le partial
  // Jinja V2 (hub qgis-sspcloud) avec entrees V0.3.1 (presence de style
  // object dans au moins une entree). Sans cela, le shim court-circuitait
  // les entrees direct-injected -> classification/opacity/outline ignores.
  const catalogInjectedV031 = Array.isArray(params._catalog_layers)
    && params._catalog_layers.some(l => l && typeof l.style === "object" && l.style !== null);
  const isV031 = params.manifest_version === "0.3.1"
    || Array.isArray(params.layers)
    || catalogInjectedV031;
  if (!isV031) return { params, hostContext };

  const shimParams = { ...params };
  const shimHost = { ...(hostContext || {}) };

  // basemap.id -> basemap_id
  if (params.basemap && params.basemap.id && !shimParams.basemap_id) {
    shimParams.basemap_id = params.basemap.id;
  }

  // zone.insee_arm / zone.commune -> hostContext.commune_bbox (fallback bbox table)
  if (params.zone && (params.zone.kind === "insee_arm" || params.zone.kind === "commune")) {
    const bbox = _V031_INSEE_ARM_BBOX[params.zone.insee];
    if (bbox && !shimHost.commune_bbox) {
      shimHost.commune_bbox = bbox;
    }
  }

  // zone.kind bbox custom : mapper en manual
  if (params.zone && params.zone.bbox && !params.zone.kind) {
    shimParams.zone = { ...params.zone, kind: "manual" };
  }

  // layers[] V0.3.1 -> catalog_layers[] + layers_override[]
  if (Array.isArray(params.layers) && !shimHost.catalog_layers && !shimParams._catalog_layers) {
    const catalog = [];
    const overrides = [];
    for (const l of params.layers) {
      const { catalogEntry, override } = _v031ItemToLegacy(l);
      catalog.push(catalogEntry);
      overrides.push(override);
    }
    shimHost.catalog_layers = catalog;
    shimParams.layers_override = [...(params.layers_override || []), ...overrides];
  }

  // alpha.6 (Sprint V0.2, 2026-07-12) : quand le hub injecte _catalog_layers
  // directement (partial V2 `_interactive_map_partial_v2.j2`), le shim ci-dessus
  // court-circuite car aucun `params.layers`. Mais les entrees V0.3.1 conservent
  // leur `style.classification` / `style.opacity.reactive` / `style.outline` qui
  // ne sont PAS lus par `_buildMapLibreLayer` -> tout tombe sur bleu Marianne uni,
  // sans classification thematique, sans timeline reactive, sans outline.
  // On normalise chaque entree V0.3.1 vers le format catalog + on genere
  // layers_override en parallele. Detection : entree qui a un `style` object.
  const cat = shimParams._catalog_layers;
  if (Array.isArray(cat)
      && cat.length
      && cat.some(l => l && typeof l.style === "object" && l.style !== null)) {
    const normalizedCatalog = [];
    const derivedOverrides = [];
    for (const l of cat) {
      // Si l'entree est deja au format legacy (pas de style object), on la
      // garde telle quelle pour compat retro (Grist widget qui a deja shime).
      if (!l || typeof l.style !== "object" || l.style === null) {
        normalizedCatalog.push(l);
        continue;
      }
      const { catalogEntry, override } = _v031ItemToLegacy(l);
      normalizedCatalog.push(catalogEntry);
      derivedOverrides.push(override);
    }
    shimParams._catalog_layers = normalizedCatalog;
    // Si params.layers_override deja rempli (V1.13 ou boucle precedente),
    // on merge sans doublon par layer_id_ref (existing prioritaire).
    const existing = shimParams.layers_override || [];
    const existingIds = new Set(existing.map(o => o.layer_id_ref));
    const merged = existing.concat(
      derivedOverrides.filter(o => !existingIds.has(o.layer_id_ref))
    );
    shimParams.layers_override = merged;
  }

  return { params: shimParams, hostContext: shimHost };
}

// alpha.6 : extrait d'une entree V0.3.1 layer -> {catalogEntry, override}
// legacy. Factorise pour etre reutilisable depuis les 2 chemins d'entree
// du shim (params.layers direct et _catalog_layers injecte).
function _v031ItemToLegacy(l) {
  // V0.3.2 alpha.5 : propager source complete pour permettre fetch async
  // universel (geojson_path, geojson_url, wfs, boundary_admin, grist_table, x_*).
  // Legacy V1.13 catalog_layers.geojson reste pour compat (source.type='geojson' inline).
  const geojson = (l.geojson)
    || (l.source && l.source.type === "geojson" ? l.source.data : null);
  const style = l.style || {};
  const interactions = l.interactions || {};
  const classifShim = _v031ClassificationShim(style.classification);
  const catalogEntry = {
    id: l.id,
    name: l.name,
    geometry_type: l.geometry_type,
    geojson,
    source_v031: l.source,
    n_features: l.n_features,
    bbox: l.bbox,
    classification: classifShim,
    outline: style.outline,
    hollow_point: style.hollow_point
  };
  // V0.3.1 opacity.reactive : preserver la config fade progressif pour
  // applyBinding("time") au lieu du filter binaire hide/show historique.
  const opacityStatic = typeof style.opacity === "object"
    ? (style.opacity.kind === "static" ? style.opacity.value : undefined)
    : style.opacity;
  const opacityReactive = (typeof style.opacity === "object"
                           && style.opacity.kind === "reactive")
    ? style.opacity
    : null;
  const override = {
    layer_id_ref: l.id,
    visible: style.visible !== false,
    z_index: style.z_index,
    opacity: opacityStatic,
    opacity_reactive: opacityReactive,
    classification: classifShim,
    outline: style.outline,
    hollow_point: style.hollow_point,
    popup_template: interactions.popup_template,
    tooltip_field: interactions.tooltip_field,
    hover_attributes: interactions.hover_attributes,
    name_override: l.name
  };
  return { catalogEntry, override };
}

// V0.3.2 alpha.5 (Sprint V0.2 session finale, 2026-07-11) : resolveur
// universel des 7 types de source du contract V0.3.2. Retourne une Promise
// qui resout vers un GeoJSON FeatureCollection utilisable par MapLibre.
// Pattern async : le layer est ajoute immediatement avec source vide, puis
// setData appele quand la promise resout. Consequence : la carte s'affiche
// instantanement (fond + controls), features apparaissent async.
//
// Types supportes :
//   - geojson         → source.data (immediat)
//   - geojson_path    → fetch(path, {credentials:'include'}) same-origin
//   - geojson_url     → fetch(url) public sans credentials
//   - wfs             → construct WFS GetFeature URL + fetch
//   - boundary_admin  → hostContext.boundary_resolver({catalog_id, filter})
//   - grist_table     → hostContext.grist_resolver({table, id_field, sync})
//   - x_<prefix>      → hostContext.custom_resolvers[prefix]
//   - vector/pmtiles  → PAS gere ici (utiliser addSource type=vector direct)
async function _fetchSourceData(source, hostContext) {
  if (!source || !source.type) return { type: "FeatureCollection", features: [] };
  const t = source.type;

  if (t === "geojson") {
    if (typeof source.data === "string") {
      // legacy : data peut etre une URL string
      const resp = await fetch(source.data);
      if (!resp.ok) throw new Error(`geojson data fetch failed: ${resp.status}`);
      return resp.json();
    }
    return source.data || { type: "FeatureCollection", features: [] };
  }

  if (t === "geojson_path") {
    // Path serveur (PVC hub). Fetch same-origin avec cookies OIDC.
    const url = source.fetch_url || source.path;
    const resp = await fetch(url, { credentials: "include" });
    if (!resp.ok) throw new Error(`geojson_path fetch failed: ${resp.status}`);
    return resp.json();
  }

  if (t === "geojson_url") {
    // URL publique. Fetch sans credentials.
    const resp = await fetch(source.url);
    if (!resp.ok) throw new Error(`geojson_url fetch failed: ${resp.status}`);
    return resp.json();
  }

  if (t === "wfs") {
    // Construct GetFeature URL avec parametres par defaut V0.3.2.
    const p = new URLSearchParams({
      service: "WFS",
      version: "2.0.0",
      request: "GetFeature",
      typeName: source.type_name,
      outputFormat: "application/json",
      srsName: "EPSG:4326",
    });
    if (source.max_features) p.set("count", String(source.max_features));
    if (source.filter_cql) p.set("cql_filter", source.filter_cql);
    const sep = source.url.includes("?") ? "&" : "?";
    const resp = await fetch(source.url + sep + p.toString());
    if (!resp.ok) throw new Error(`wfs fetch failed: ${resp.status}`);
    return resp.json();
  }

  if (t === "boundary_admin") {
    // Delegue au hostContext du consommateur.
    const resolver = hostContext && hostContext.boundary_resolver;
    if (typeof resolver !== "function") {
      throw new Error(
        "boundary_admin requires hostContext.boundary_resolver(source)"
      );
    }
    return await resolver(source);
  }

  if (t === "grist_table") {
    // Delegue au widget Grist parent.
    const resolver = hostContext && hostContext.grist_resolver;
    if (typeof resolver !== "function") {
      throw new Error(
        "grist_table requires hostContext.grist_resolver(source)"
      );
    }
    return await resolver(source);
  }

  if (typeof t === "string" && t.startsWith("x_")) {
    // Extension par-projet.
    const resolvers = (hostContext && hostContext.custom_resolvers) || {};
    const resolver = resolvers[t];
    if (typeof resolver !== "function") {
      throw new Error(
        `custom source type '${t}' requires hostContext.custom_resolvers['${t}']`
      );
    }
    return await resolver(source);
  }

  // Types vector/pmtiles/... : geres directement par MapLibre addSource,
  // pas via ce resolveur.
  throw new Error(`_fetchSourceData: type '${t}' non supporte comme geojson`);
}

// V0.3.1 classification : {color: {mode, field, method, breaks, palette,
// _compiled_expression}, size, label} -> {paint_expression, method, field, ...}
// attendus par _paintForClassification (V1.13).
function _v031ClassificationShim(classif) {
  if (!classif) return null;
  // Deja au format V1.13 (paint_expression direct) -> passthrough
  if (classif.paint_expression) return classif;
  // V0.3.1 nested {color: {...}}
  const color = classif.color || classif;
  if (!color || !color.mode) return classif;
  const out = {
    method: color.method || color.mode,
    field: color.field
  };
  // Priorite : expression MapLibre pre-compilee dans _compiled_expression
  if (Array.isArray(color._compiled_expression)) {
    out.paint_expression = color._compiled_expression;
  } else if (color.mode === "single" && color.value) {
    out.paint_expression = color.value;
  } else if (color.mode === "expression" && Array.isArray(color.expression)) {
    out.paint_expression = color.expression;
  }
  return out;
}

// V0.3.1 alpha.4 : normalise une position (top-left/bottom-right/...) vers
// les 4 positions acceptees par MapLibre. Fallback sur defaut si absente ou
// invalide (avoid warning MapLibre "Position undefined").
function _mlPosition(pos, fallback) {
  const valid = ["top-left", "top-right", "bottom-left", "bottom-right"];
  if (typeof pos === "string" && valid.includes(pos)) return pos;
  return fallback;
}

/**
 * Résout la zone d'étude en {center, zoom, bbox} exploitables par MapLibre.
 * Supporte les 3 modes du contrat V1.13 : commune | manual | study.
 * V0.3.1 : accepte aussi kind='insee_arm' via le shim _v031ToLegacyShim.
 * Pour "commune" et "study", nécessite une résolution externe (fournie via
 * config.resolveZone) sinon fallback sur defaults.
 */
function _resolveZone(zoneConfig, hostContext) {
  const zone = zoneConfig || {};
  const kind = zone.kind || "manual";

  if (kind === "manual") {
    return {
      center: [
        zone.center_lng ?? DEFAULT_CENTER[0],
        zone.center_lat ?? DEFAULT_CENTER[1],
      ],
      zoom: zone.zoom ?? DEFAULT_ZOOM,
      bbox: zone.bbox || null,
    };
  }

  if (kind === "study" && hostContext?.study_bbox) {
    const [w, s, e, n] = hostContext.study_bbox;
    return {
      center: [(w + e) / 2, (s + n) / 2],
      zoom: DEFAULT_ZOOM,
      bbox: [w, s, e, n],
    };
  }

  if ((kind === "commune" || kind === "insee_arm") && hostContext?.commune_bbox) {
    const [w, s, e, n] = hostContext.commune_bbox;
    return {
      center: [(w + e) / 2, (s + n) / 2],
      zoom: DEFAULT_ZOOM,
      bbox: [w, s, e, n],
    };
  }

  // Fallback si résolution impossible
  return { center: DEFAULT_CENTER, zoom: DEFAULT_ZOOM, bbox: null };
}

/**
 * Détecte le type de géométrie GeoJSON (point/line/fill) pour ajouter la
 * bonne layer MapLibre par défaut si aucune classification n'est fournie.
 *
 * V0.2.0 (Chantier 4 Sprint V0.2, 2026-07-10) : ajoute sniff de la geojson
 * si geometry_type non fourni. Fix bug POINT rendering remonté par
 * cerema-livrables (points rendus en fill invisibles avant fix).
 */
function _detectGeomType(geometryTypeHint, geojson) {
  const h = (geometryTypeHint || "").toLowerCase();
  if (h.includes("point")) return "point";
  if (h.includes("line") || h.includes("linestring")) return "line";
  if (h.includes("polygon")) return "fill";
  // Sniff depuis geojson si pas de hint typé
  if (geojson && Array.isArray(geojson.features) && geojson.features.length > 0) {
    const firstGeom = geojson.features[0].geometry;
    if (firstGeom && typeof firstGeom.type === "string") {
      const g = firstGeom.type.toLowerCase();
      if (g.includes("point")) return "point";
      if (g.includes("line")) return "line";
      if (g.includes("polygon")) return "fill";
    }
  }
  return "fill";
}

/**
 * V0.2.0 (Chantier 4) : détecte si une FeatureCollection est hétérogène
 * (mélange de polygones et de points). Utile pour auto-splitter en 2 layers
 * MapLibre distincts (fix bug Livrables).
 * Retourne { hasPolygon, hasPoint, hasLine, isMixed }.
 */
function _analyzeGeojson(geojson) {
  const result = { hasPolygon: false, hasPoint: false, hasLine: false, isMixed: false };
  if (!geojson || !Array.isArray(geojson.features)) return result;
  for (const f of geojson.features) {
    const t = (f.geometry && f.geometry.type || "").toLowerCase();
    if (t.includes("polygon")) result.hasPolygon = true;
    else if (t.includes("point")) result.hasPoint = true;
    else if (t.includes("line")) result.hasLine = true;
    if ((result.hasPolygon + result.hasPoint + result.hasLine) > 1) {
      result.isMixed = true;
    }
  }
  return result;
}

/**
 * V0.2.0 (Chantier 4) : construit un layer 'line' outline dérivé du même
 * source (pattern outline déclaratif V0.3.1 style.outline).
 * Absorbe le workaround Livrables (LLINE ajouté impératif après geo:map-ready).
 */
function _buildOutlineLayer(baseLayerId, sourceId, outlineConfig, opacity) {
  return {
    id: baseLayerId + "-outline",
    type: "line",
    source: sourceId,
    paint: {
      "line-color": outlineConfig.color || "#000091",
      "line-width": outlineConfig.width ?? 1,
      "line-opacity": (opacity ?? 1) * 0.9,
    },
  };
}

/**
 * V0.2.0 (Chantier 4) : construit un layer 'circle' point creux (pattern
 * V0.3.1 style.hollow_point). Absorbe le workaround Livrables (addPoints
 * impératif avec stroke coloré par field).
 */
function _buildHollowPointLayer(baseLayerId, sourceId, hollowConfig, themedColor, opacity) {
  const radius = hollowConfig.radius ?? 7;
  const strokeField = hollowConfig.stroke_color_from_field;
  return {
    id: baseLayerId,
    type: "circle",
    source: sourceId,
    paint: {
      "circle-radius": radius,
      "circle-color": "rgba(148,163,184,0.15)",
      "circle-stroke-width": 2,
      "circle-stroke-color": strokeField
        ? ["get", strokeField]
        : (themedColor || "#000091"),
      "circle-opacity": (opacity ?? 1) * 0.9,
    },
  };
}

/**
 * Applique une classification thématique à un layer MapLibre : catégorisé,
 * gradué, ou couleur fixe. Utilise `paint_expression` s'il est pré-calculé
 * (pattern qgis-sspcloud SSR), sinon dérive une expression MapLibre.
 */
function _paintForClassification(classif, fallbackColor) {
  if (!classif) return fallbackColor;
  if (classif.paint_expression) return classif.paint_expression;

  // Si classification déclarée mais pas d'expression pré-calculée,
  // dérive une expression simple (V0.1 : basique, V0.3 : full geoai-kit).
  const { field, method, palette } = classif;
  const kit = typeof window !== "undefined" && window.GeoAIKit;
  if (kit && typeof kit.styleByField === "function") {
    try {
      return kit.styleByField(classif) || fallbackColor;
    } catch (e) {
      return fallbackColor;
    }
  }

  // Fallback minimal : simple match categorized
  // P1 correctness (review 2026-07-08) : respecte le palette utilisateur du
  // contrat V1.13 s'il est fourni, sinon fallback DEFAULT_PALETTE.
  if (method === "categorized" && classif.breaks_manual) {
    const userPalette = Array.isArray(palette) ? palette : null;
    const stops = ["match", ["get", field]];
    (classif.breaks_manual || []).forEach((v, i) => {
      const color = userPalette
        ? userPalette[i % userPalette.length]
        : DEFAULT_PALETTE[i % DEFAULT_PALETTE.length];
      stops.push(v, color);
    });
    stops.push(fallbackColor);
    return stops;
  }

  return fallbackColor;
}

/**
 * Construit un layer MapLibre depuis une entrée du contrat V1.13 layers_override.
 * Gère heatmap, proportional, classification, fill/line/circle par défaut.
 */
function _buildMapLibreLayer(sceneLayer, override, index) {
  const safeId = _sanitizeId(sceneLayer.id || String(index));
  const layerId = "gc-lyr-" + safeId;
  const sourceId = "gc-src-" + safeId;
  const geom = _detectGeomType(sceneLayer.geometry_type);
  const fallbackColor = DEFAULT_PALETTE[index % DEFAULT_PALETTE.length];
  const classif = (override && override.classification) || sceneLayer.classification;
  const themedColor = _paintForClassification(classif, fallbackColor);
  const opacity = override?.opacity ?? 1.0;

  // Heatmap
  if (override?.heatmap_field && geom === "point") {
    return {
      id: layerId,
      type: "heatmap",
      source: sourceId,
      maxzoom: 18,
      paint: {
        "heatmap-weight": [
          "interpolate", ["linear"],
          ["coalesce", ["to-number", ["get", override.heatmap_field], 0], 0],
          0, 0, 100, 1,
        ],
        "heatmap-intensity": ["interpolate", ["linear"], ["zoom"], 0, 1, 18, 3],
        "heatmap-color": [
          "interpolate", ["linear"], ["heatmap-density"],
          0, "rgba(0,0,145,0)",
          0.2, "rgba(0,99,203,0.4)",
          0.4, "rgba(255,225,0,0.6)",
          0.6, "rgba(255,103,0,0.8)",
          1, "rgba(225,0,15,1)",
        ],
        "heatmap-radius": ["interpolate", ["linear"], ["zoom"], 0, 2, 18, 30],
        "heatmap-opacity": opacity,
      },
    };
  }

  // Proportional (points)
  if (geom === "point" && override?.proportional_field) {
    const propMin = override.proportional_min ?? 0;
    const propMax = override.proportional_max ?? 1000;
    const rMin = override.proportional_radius_min ?? 4;
    const rMax = override.proportional_radius_max ?? 30;
    return {
      id: layerId,
      type: "circle",
      source: sourceId,
      paint: {
        "circle-color": themedColor,
        "circle-radius": [
          "interpolate", ["linear"],
          ["coalesce", ["to-number", ["get", override.proportional_field], 0], 0],
          propMin, rMin,
          propMax, rMax,
        ],
        "circle-stroke-width": 1,
        "circle-stroke-color": "#fff",
        "circle-opacity": 0.75 * opacity,
      },
    };
  }

  // Points simples
  if (geom === "point") {
    return {
      id: layerId,
      type: "circle",
      source: sourceId,
      paint: {
        "circle-color": themedColor,
        "circle-radius": 6,
        "circle-stroke-width": 1,
        "circle-stroke-color": "#fff",
        "circle-opacity": opacity,
      },
    };
  }

  // Lignes
  if (geom === "line") {
    return {
      id: layerId,
      type: "line",
      source: sourceId,
      paint: {
        "line-color": themedColor,
        "line-width": 2,
        "line-opacity": opacity,
      },
    };
  }

  // Polygones par défaut
  return {
    id: layerId,
    type: "fill",
    source: sourceId,
    paint: {
      "fill-color": themedColor,
      "fill-opacity": 0.6 * opacity,
      "fill-outline-color": "#ffffff",
    },
  };
}

/**
 * Retourne la propriete de paint MapLibre appropriee selon le type de layer.
 * P1 correctness (review 2026-07-08) : evite crash sur layers non-polygone
 * quand on applique setPaintProperty('fill-color' | 'fill-opacity').
 *
 * @param {string} layerType 'fill' | 'circle' | 'line' | 'heatmap' | ...
 * @param {'color'|'opacity'} what
 * @returns {string|null}
 */
function _colorPaintPropFor(layerType, what) {
  const suffix = what === "opacity" ? "-opacity" : "-color";
  switch (layerType) {
    case "fill":
      // Note : le heatmap n'a pas de "single color" mais un ramp density.
      return "fill" + suffix;
    case "circle":
      return "circle" + suffix;
    case "line":
      return "line" + suffix;
    case "heatmap":
      // heatmap : opacity OK, color = ramp density (retourne null pour color).
      return what === "opacity" ? "heatmap-opacity" : null;
    case "fill-extrusion":
      return "fill-extrusion" + suffix;
    default:
      return null;
  }
}

/**
 * Sanitize un ID pour MapLibre v5 (accepte uniquement [A-Za-z0-9_-]).
 * Les caractères non-ASCII (accents, cedilles) et espaces sont remplacés
 * par des underscores. Fix V1.20.5 : scene_manifest QGIS peut produire
 * des IDs avec accents (ex: "bâtiments__bd_topo_") qui font silencieusement
 * echouer addSource/addLayer sur MapLibre v5.
 */
function _sanitizeId(s) {
  if (!s) return "unnamed";
  return String(s).replace(/[^A-Za-z0-9_-]/g, "_");
}

/**
 * Escape HTML minimal pour popup_template mustache.
 */
function _escapeHtml(s) {
  if (s === null || s === undefined) return "";
  return String(s)
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;")
    .replace(/'/g, "&#39;");
}

/**
 * Applique un popup_template mustache-like {prop} sur les propriétés d'une
 * feature. Escape HTML par défaut (contrat qgis-sspcloud V1.13).
 */
function _renderPopup(template, props) {
  return (template || "").replace(/\{(\w+)\}/g, (m, k) => {
    const v = props[k];
    return v === undefined ? "" : _escapeHtml(v);
  });
}

/**
 * Parse un attribut HTML `params` (JSON string) en objet. Retourne {} si vide
 * ou invalide (log warn).
 */
function _parseParams(raw) {
  if (!raw) return {};
  try {
    return typeof raw === "string" ? JSON.parse(raw) : raw;
  } catch (e) {
    console.warn("[geo-components] params attribute JSON invalide:", e);
    return {};
  }
}

// ──────────────────────────────────────────────────────────────────────────
// 2. Orchestrateur bindings — singleton attaché au document
// ──────────────────────────────────────────────────────────────────────────

/**
 * GeoBindings orchestrateur : reçoit les CustomEvent 'geo:bind' émis par les
 * composants controllers (timeline, scenario_switch, filter_chips) et route
 * vers le composant carte target via son API impérative applyBinding().
 *
 * Fonctionnement in-DOM (même page) : le CustomEvent bulle jusqu'au document
 * grâce à {bubbles: true, composed: true} (traverse les Shadow DOM).
 *
 * Fonctionnement cross-iframe : les composants émettent aussi via
 * window.parent.postMessage({type: 'geo:bind', detail: {...}}, '*') si détectent
 * qu'ils sont dans un iframe enfant. L'orchestrateur du parent relaye vers
 * l'iframe cible.
 *
 * Convention événement (compatible Widgets Grist skills/inter-widget.md) :
 *   detail: { from: cid_source, target: cid_target, prop: 'time'|'scenario'|..., value }
 *
 * Enregistrement singleton : le premier composant à s'installer sur la page
 * enregistre les listeners. install() est idempotent.
 */
export class GeoBindings {
  constructor() {
    this._installed = false;
    /**
     * P1 security (review 2026-07-08) : whitelist des origines autorisees pour
     * les postMessage entrants. Defaut = [location.origin] (uniquement les
     * iframes servies depuis la meme origine que la page hote).
     *
     * Le consommateur peut relacher en amont :
     *   GeoBindings.allowedOrigins.push('https://widgets.grist.example.fr')
     * OU declarer une variable globale AVANT que la lib charge :
     *   window.__GEO_BINDINGS_TRUSTED_ORIGINS = ['https://...']
     */
    this.allowedOrigins = [];
    if (typeof location !== "undefined" && location.origin) {
      this.allowedOrigins.push(location.origin);
    }
    if (typeof window !== "undefined" && Array.isArray(window.__GEO_BINDINGS_TRUSTED_ORIGINS)) {
      window.__GEO_BINDINGS_TRUSTED_ORIGINS.forEach((o) => {
        if (typeof o === "string" && !this.allowedOrigins.includes(o)) {
          this.allowedOrigins.push(o);
        }
      });
    }
  }

  install() {
    if (this._installed) return;
    if (typeof document === "undefined") return;

    // Listener in-DOM
    document.addEventListener("geo:bind", (ev) => this._route(ev.detail));

    // Listener cross-iframe avec validation origin
    if (typeof window !== "undefined") {
      window.addEventListener("message", (ev) => {
        if (!ev.data || ev.data.type !== "geo:bind") return;
        // P1 security : rejette les postMessage d'origines non whitelistees.
        // Prevent UI spoofing depuis iframe hostile qui pourrait piloter
        // un <geo-map> a distance sans consentement.
        if (!this._isAllowedOrigin(ev.origin)) {
          console.warn(
            "[geo-components] postMessage geo:bind rejete, origin non autorisee:",
            ev.origin
          );
          return;
        }
        this._route(ev.data.detail, true);
      });
    }

    this._installed = true;
  }

  _isAllowedOrigin(origin) {
    if (!origin) return false;
    // 'null' est l'origin d'un iframe sandbox sans allow-same-origin — rejete.
    if (origin === "null") return false;
    return this.allowedOrigins.includes(origin);
  }

  _route(detail, fromMessage) {
    if (!detail || !detail.target) return;
    const target = this._resolveTarget(detail.target);
    if (target && typeof target.applyBinding === "function") {
      try {
        target.applyBinding(detail);
      } catch (e) {
        console.warn("[geo-components] applyBinding failed on", detail.target, e);
      }
      return;
    }
    // Cross-iframe fallback : chercher un iframe avec data-cid correspondant
    const iframe = document.querySelector(
      'iframe[data-cid="' + detail.target + '"]'
    );
    if (iframe && iframe.contentWindow && !fromMessage) {
      iframe.contentWindow.postMessage(
        { type: "geo:bind", detail },
        "*"
      );
    }
  }

  /**
   * Resolution tolerante du target : cherche successivement plusieurs
   * conventions d'ID pour absorber les differences de nommage entre
   * projets consommateurs (qgis-sspcloud prefixe geo-map par 'geomap_' + cid[:8],
   * d'autres pourraient utiliser cid entier ou 'map-' + cid).
   *
   * Ordre de resolution :
   *   1. Match exact sur id HTML : #{target}
   *   2. data-cid exact : [data-cid="{target}"]
   *   3. Prefixes courants sur target[:8] : #geomap_{target[:8]}, #map-{target[:8]}
   *   4. Contient le target (fuzzy) : [id*="{target}"] parmi les geo-map/geo-* connus
   */
  _resolveTarget(rawTarget) {
    if (!rawTarget) return null;
    const trimmed = String(rawTarget).trim();
    if (!trimmed) return null;

    // 1. Match exact
    let el = document.querySelector("#" + CSS.escape(trimmed));
    if (el) return el;

    // 2. data-cid exact
    el = document.querySelector('[data-cid="' + trimmed.replace(/"/g, '\\"') + '"]');
    if (el) return el;

    // 3. Prefixes courants sur les 8 premiers hex du target (convention cid[:8])
    const short = trimmed.slice(0, 8);
    for (const prefix of ["geomap_", "map-", "geo-map-"]) {
      el = document.querySelector("#" + CSS.escape(prefix + short));
      if (el) return el;
    }

    // 4. Fuzzy sur les Web Components de la lib qui exposent applyBinding
    const candidates = document.querySelectorAll(
      "geo-map, geo-chart, geo-timeline, geo-legend"
    );
    for (const c of candidates) {
      const cid = c.getAttribute("data-cid") || c.id || "";
      if (cid === trimmed || cid.includes(short) || trimmed.includes(cid)) {
        return c;
      }
    }
    return null;
  }

  /**
   * Publie un event depuis un composant. Émet le CustomEvent local ET, si
   * on est dans un iframe enfant, relaye vers le parent via postMessage.
   */
  static publish(sourceEl, detail) {
    const event = new CustomEvent("geo:bind", {
      bubbles: true,
      composed: true,
      detail,
    });
    sourceEl.dispatchEvent(event);
    if (typeof window !== "undefined" && window.parent !== window) {
      try {
        window.parent.postMessage({ type: "geo:bind", detail }, "*");
      } catch (e) {
        /* cross-origin sandbox */
      }
    }
  }
}

/** Singleton exporté. Auto-installé à la première utilisation d'un composant. */
export const geoBindings = new GeoBindings();

// ──────────────────────────────────────────────────────────────────────────
// 3. <geo-map> — Wrapper MapLibre reader thématique 2D
// ──────────────────────────────────────────────────────────────────────────

/**
 * Custom Element <geo-map> — consomme InteractiveMapParams V1.13.
 *
 * Attributs HTML :
 *   - id (obligatoire pour bindings, standard HTML)
 *   - params (JSON string : InteractiveMapParams V1.13)
 *   - style (CSS ; height/width recommandés)
 *
 * Properties JS :
 *   - .params (object, alternative à l'attribut)
 *   - .hostContext (object, {study_bbox, commune_bbox, catalog_layers} pour
 *     résoudre les zones et layers by-id du contrat V1.13)
 *
 * API impérative (post-init) :
 *   - map      : instance maplibregl.Map (accès brut)
 *   - setFilter(layerId, filter)
 *   - setPaintProperty(layerId, prop, value)
 *   - setLayoutProperty(layerId, prop, value)
 *   - fitBoundsTo(bbox)
 *   - setLayerVisibility(layerId, visible)
 *   - setLayerOpacity(layerId, opacity)
 *   - applyBinding({prop, value})   — appelé par l'orchestrateur
 *
 * Événements dispatch :
 *   - 'geo:map-ready' quand map.on('load') a résolu
 *   - 'geo:map-error' si MapLibre indisponible ou erreur init
 */
export class GeoMap extends HTMLElement {
  static get observedAttributes() {
    return ["params"];
  }

  constructor() {
    super();
    this.attachShadow({ mode: "open" });
    this.map = null;
    this._layerIds = [];
    this._params = {};
    this._hostContext = {};
  }

  connectedCallback() {
    geoBindings.install();
    // Upgrade dance en connectedCallback (pas constructor) : la spec Custom
    // Elements appelle attributeChangedCallback APRES le constructor lors de
    // l'upgrade. Si on faisait la dance dans le constructor, notre restore
    // via setter serait ecrase par attributeChangedCallback qui reparse
    // l'attribut string (sans _catalog_layers). connectedCallback tourne
    // APRES attributeChangedCallback, donc ici on peut sereinement re-appliquer
    // les expandos posees par le script inline serveur-side (partial v2).
    // On ecrit directement dans _params/_hostContext pour eviter le double
    // _render() qu'un passage par le setter provoquerait.
    for (const propName of ["params", "hostContext"]) {
      if (Object.prototype.hasOwnProperty.call(this, propName)) {
        const value = this[propName];
        delete this[propName];
        if (propName === "params") this._params = value || {};
        else this._hostContext = value || {};
      }
    }
    this._render();
  }

  disconnectedCallback() {
    if (this.map) {
      try { this.map.remove(); } catch (e) { /* ignore */ }
      this.map = null;
    }
  }

  attributeChangedCallback(name, oldVal, newVal) {
    if (name === "params" && oldVal !== newVal) {
      this._params = _parseParams(newVal);
      if (this.isConnected) this._render();
    }
  }

  // Properties JS
  get params() { return this._params; }
  set params(v) {
    this._params = v || {};
    if (this.isConnected) this._render();
  }

  get hostContext() { return this._hostContext; }
  set hostContext(v) {
    this._hostContext = v || {};
    if (this.isConnected) this._render();
  }

  _render() {
    // P1 correctness (review 2026-07-08) : libere l'instance MapLibre existante
    // AVANT de reconstruire le DOM. Sans cela, chaque changement de params fuit
    // un contexte WebGL (limite navigateur ~16 avant que la carte cesse d'afficher).
    if (this.map) {
      try { this.map.remove(); } catch (e) { /* ignore */ }
      this.map = null;
      this._layerIds = [];
    }

    // Merge attribut + property
    if (!Object.keys(this._params).length) {
      this._params = _parseParams(this.getAttribute("params"));
    }

    const params = this._params;
    const shadow = this.shadowRoot;
    shadow.innerHTML = "";

    // Base styles Shadow DOM
    const style = document.createElement("style");
    style.textContent = `
      :host { display: block; position: relative; width: 100%; }
      .gc-wrap { background: #fff; border-radius: 6px; overflow: hidden;
                 border: 1px solid #e5e5e5; }
      .gc-hdr { padding: 14px 20px; border-bottom: 1px solid #e5e5e5;
                background: #f6f6f6; }
      .gc-title { color: #000091; font-size: 15px; font-weight: 700;
                  display: block; font-family: Marianne, system-ui, sans-serif; }
      .gc-subtitle { color: #444; font-size: 13px; margin-top: 2px;
                     font-family: Marianne, system-ui, sans-serif; }
      .gc-desc { padding: 10px 20px; background: #fafbfd;
                 border-bottom: 1px solid #e5e5e5; font-size: 13px;
                 color: #333; line-height: 1.5;
                 font-family: Marianne, system-ui, sans-serif; }
      .gc-map { width: 100%; }
      .gc-src { padding: 8px 20px; background: #fafafa;
                border-top: 1px solid #f0f0f0; font-size: 11px;
                color: #666; font-style: italic;
                font-family: Marianne, system-ui, sans-serif; }
      .gc-caveat { padding: 8px 20px; background: #fff8e6;
                   border-top: 1px solid #ffe1a8; font-size: 11px;
                   color: #7a4b00;
                   font-family: Marianne, system-ui, sans-serif; }
      .gc-fallback { padding: 40px; text-align: center; color: #666;
                     font-family: Marianne, system-ui, sans-serif; }
      /* V1.20.6 : slot overlay pour timeline/legende in-map. Positionne
         absolute sur la carte (au-dessus de la basemap + layers). L'element
         insere dans ce slot est ancre visuellement a la carte, quel que
         soit le contexte hote (storymap, widget Grist Atlas, ...). */
      .gc-overlay { position: absolute; left: 16px; right: 16px; bottom: 16px;
                    z-index: 5; pointer-events: none; }
      .gc-overlay ::slotted(*) { pointer-events: auto; }
    `;
    shadow.appendChild(style);

    const wrap = document.createElement("div");
    wrap.className = "gc-wrap";
    wrap.style.position = "relative";
    shadow.appendChild(wrap);

    // Trio TITRE + description
    if (params.title || params.subtitle) {
      const hdr = document.createElement("div");
      hdr.className = "gc-hdr";
      if (params.title) {
        const t = document.createElement("strong");
        t.className = "gc-title";
        t.textContent = params.title;
        hdr.appendChild(t);
      }
      if (params.subtitle) {
        const s = document.createElement("div");
        s.className = "gc-subtitle";
        s.textContent = params.subtitle;
        hdr.appendChild(s);
      }
      wrap.appendChild(hdr);
    }
    if (params.description) {
      const d = document.createElement("div");
      d.className = "gc-desc";
      d.textContent = params.description;
      wrap.appendChild(d);
    }

    // Canvas map
    const height = params.height || 480;
    const mapDiv = document.createElement("div");
    mapDiv.className = "gc-map";
    mapDiv.style.height = height + "px";
    mapDiv.style.position = "relative";
    wrap.appendChild(mapDiv);

    // V1.20.6 : slot overlay (position absolute au-dessus du canvas MapLibre).
    // Un <geo-timeline slot="overlay"> insere dans le light DOM du <geo-map>
    // sera rendu en overlay ancre visuellement sur la carte (pattern StoryMap
    // ESRI / Kepler). Cross-projet : le meme HTML fonctionne dans une storymap
    // qgis-sspcloud, un widget Grist Atlas, une single page HTML embed.
    const overlay = document.createElement("div");
    overlay.className = "gc-overlay";
    const slot = document.createElement("slot");
    slot.setAttribute("name", "overlay");
    overlay.appendChild(slot);
    mapDiv.appendChild(overlay);

    // Trio SOURCE datée
    if (params.source) {
      const src = document.createElement("div");
      src.className = "gc-src";
      src.textContent = "Source : " + params.source;
      wrap.appendChild(src);
    }
    if (params.caveat) {
      const cav = document.createElement("div");
      cav.className = "gc-caveat";
      cav.innerHTML =
        '<strong style="color:#b34000">⚠ Caveat :</strong> ' +
        _escapeHtml(params.caveat);
      wrap.appendChild(cav);
    }

    // Init MapLibre
    const ml = _detectMapLibre();
    if (!ml) {
      mapDiv.innerHTML =
        '<div class="gc-fallback">MapLibre GL JS indisponible. ' +
        "Vérifiez le chargement de maplibre-gl@^5.6.1 dans la page hôte.</div>";
      this.dispatchEvent(new CustomEvent("geo:map-error", {
        detail: { reason: "maplibre_missing" },
      }));
      return;
    }

    try {
      this._initMap(ml, mapDiv, params);
    } catch (e) {
      console.error("[geo-components] Init map failed:", e);
      mapDiv.innerHTML =
        '<div class="gc-fallback">Erreur d\'initialisation de la carte.</div>';
      this.dispatchEvent(new CustomEvent("geo:map-error", {
        detail: { reason: "init_failed", error: String(e) },
      }));
    }
  }

  _initMap(ml, container, params) {
    // V0.3.1 shim (Chantier 7) : normalise SceneManifest V0.3.1 vers la
    // structure interne V1.13 attendue par le reste du _initMap.
    const shim = _v031ToLegacyShim(params, this._hostContext);
    params = shim.params;
    this._hostContext = shim.hostContext;

    // Résolution zone
    const zone = _resolveZone(params.zone, this._hostContext);

    // Basemap
    const basemapId = params.basemap_id || "osm";
    const basemap = BASEMAPS[basemapId] || BASEMAPS["osm"];

    this.map = new ml.Map({
      container,
      style: basemap.style,
      center: zone.center,
      zoom: zone.zoom,
    });

    // V0.3.1 alpha.4 : declaratifs params.scalebar / params.north_arrow.
    // Sans north_arrow declare, on garde le comportement historique
    // NavigationControl sans compass. Avec, on affiche compass a la position
    // demandee. scalebar => ScaleControl natif MapLibre a la position/unit.
    const northCfg = params.north_arrow;
    const scaleCfg = params.scalebar;
    const showCompass = !!northCfg;
    const northPos = northCfg && northCfg.position ? northCfg.position : "top-left";
    this.map.addControl(
      new ml.NavigationControl({ showCompass, visualizePitch: showCompass }),
      _mlPosition(northPos, "top-left"),
    );
    if (scaleCfg) {
      const unit = scaleCfg.unit === "imperial" ? "imperial" : "metric";
      const scalePos = _mlPosition(scaleCfg.position, "bottom-right");
      this.map.addControl(
        new ml.ScaleControl({ maxWidth: 120, unit }),
        scalePos,
      );
    }

    const layersOverride = params.layers_override || [];
    const catalogLayers =
      this._hostContext.catalog_layers || params._catalog_layers || [];

    this._layerIds = [];
    // V0.3.1 (Sprint V0.2 alpha.4) : cache config opacity.reactive par layerId
    // pour permettre au binding 'time' de faire un fade progressif au lieu
    // d'un setFilter binaire hide/show. Cle = layer id MapLibre applique.
    this._reactiveOpacity = {};

    // P2 contract-drift V1.13 (review 2026-07-08) : respecter LayerOverride.z_index.
    // Le contrat V1.13 declare z_index (int) pour reordonner les layers a
    // l'affichage. On trie une copie des catalogLayers par z_index (fallback
    // ordre du catalog).
    const orderedLayers = catalogLayers
      .map((scene, i) => {
        const override = layersOverride.find((o) => o.layer_id_ref === scene.id) || null;
        return { scene, override, catalogIndex: i };
      })
      .sort((a, b) => {
        const az = a.override?.z_index ?? a.catalogIndex;
        const bz = b.override?.z_index ?? b.catalogIndex;
        return az - bz;
      });

    // V1.20.5 patch (2026-07-08) : si le style basemap est deja charge
    // (typiquement lors d'un re-render apres set params), appliquer les
    // layers immediatement au lieu d'attendre l'event "load" qui ne
    // tirera jamais. Sinon on attache le callback normal.
    const applyLayers = () => {
      // Ajout des layers depuis catalog_layers, avec overrides appliqués
      orderedLayers.forEach(({ scene: sceneLayer, override, catalogIndex: i }) => {
        if (override && override.visible === false) return;

        const safeId = _sanitizeId(sceneLayer.id || String(i));
        const sourceId = "gc-src-" + safeId;
        try {
          this.map.addSource(sourceId, {
            type: "geojson",
            data: sceneLayer.geojson || { type: "FeatureCollection", features: [] },
          });

          // V0.3.2 alpha.5 : fetch async des sources non-inline
          // (geojson_path, geojson_url, wfs, boundary_admin, grist_table, x_*).
          // La carte s'affiche instantanement, features arrivent async.
          if (sceneLayer.source_v031
              && sceneLayer.source_v031.type !== "geojson"
              && !sceneLayer.geojson) {
            const hostCtx = this._hostContext || {};
            _fetchSourceData(sceneLayer.source_v031, hostCtx)
              .then((geojson) => {
                const src = this.map.getSource(sourceId);
                if (src && typeof src.setData === "function") {
                  src.setData(geojson);
                }
                this.dispatchEvent(new CustomEvent("geo:source-loaded", {
                  detail: {
                    layerId: sceneLayer.id,
                    features: (geojson.features || []).length,
                  },
                }));
              })
              .catch((e) => {
                console.warn(
                  "[geo-components] fetch source failed for",
                  sceneLayer.id, "->", sceneLayer.source_v031, e
                );
                this.dispatchEvent(new CustomEvent("geo:source-error", {
                  detail: {
                    layerId: sceneLayer.id,
                    error: String(e).slice(0, 200),
                  },
                }));
              });
          }

          // V0.2.0 Chantier 4 : détection type + auto-split polygon+point
          // Analyse la geojson pour détecter FC hétérogène et éviter le bug
          // POINT invisibles (remontée cerema-livrables).
          const analysis = _analyzeGeojson(sceneLayer.geojson);
          const isMixedFC = analysis.isMixed && analysis.hasPolygon && analysis.hasPoint;

          if (isMixedFC) {
            // FC mixte polygone+point : rendre 2 layers distincts sur même source
            // - un layer 'fill' avec filter Polygon
            // - un layer 'circle' avec filter Point
            const fillLayer = _buildMapLibreLayer(
              { ...sceneLayer, geometry_type: "polygon" },
              override, i
            );
            fillLayer.filter = ["match", ["geometry-type"], ["Polygon", "MultiPolygon"], true, false];
            this.map.addLayer(fillLayer);
            this._layerIds.push(fillLayer.id);
            this._wireInteractions(ml, fillLayer.id, sceneLayer, override);

            const pointLayer = _buildMapLibreLayer(
              { ...sceneLayer, geometry_type: "point", id: sceneLayer.id + "-pt" },
              override, i
            );
            pointLayer.filter = ["match", ["geometry-type"], ["Point", "MultiPoint"], true, false];
            this.map.addLayer(pointLayer);
            this._layerIds.push(pointLayer.id);
            this._wireInteractions(ml, pointLayer.id, sceneLayer, override);
          } else {
            // Cas standard : 1 seul layer, avec hollow_point si demandé
            let layerDef;
            const styleOverride = override || {};
            if (
              styleOverride.hollow_point &&
              styleOverride.hollow_point.enabled &&
              (_detectGeomType(sceneLayer.geometry_type, sceneLayer.geojson) === "point")
            ) {
              // V0.3.1 style.hollow_point : construction directe du circle creux
              const fallbackColor = DEFAULT_PALETTE[i % DEFAULT_PALETTE.length];
              const classif = (override && override.classification) || sceneLayer.classification;
              const themedColor = _paintForClassification(classif, fallbackColor);
              const opacity = override?.opacity ?? 1.0;
              layerDef = _buildHollowPointLayer(
                "gc-lyr-" + safeId, sourceId,
                styleOverride.hollow_point,
                themedColor, opacity
              );
            } else {
              layerDef = _buildMapLibreLayer(sceneLayer, override, i);
            }
            this.map.addLayer(layerDef);
            this._layerIds.push(layerDef.id);

            // V0.3.1 alpha.4 : memoriser config opacity.reactive pour fade
            // progressif via applyBinding('time').
            if (styleOverride.opacity_reactive) {
              this._reactiveOpacity[layerDef.id] = styleOverride.opacity_reactive;
            }

            // V0.3.1 style.outline : ajout automatique layer 'line' sur même source
            if (
              styleOverride.outline &&
              styleOverride.outline.enabled &&
              (layerDef.type === "fill" || layerDef.type === "fill-extrusion")
            ) {
              const opacity = override?.opacity ?? 1.0;
              const outlineLayer = _buildOutlineLayer(
                layerDef.id, sourceId,
                styleOverride.outline, opacity
              );
              this.map.addLayer(outlineLayer);
              this._layerIds.push(outlineLayer.id);
            }

            // Interactions : hover / popup
            this._wireInteractions(ml, layerDef.id, sceneLayer, override);
          }
        } catch (e) {
          console.warn(
            "[geo-components] addLayer failed for",
            sceneLayer.id, "->", safeId,
            e
          );
        }
      });

      // V0.3.1 alpha.4 : legend.mode='auto' + from_layer -> derive chips
      // depuis classification._compiled_expression du layer designe et
      // injecte une <geo-legend> auto-generee dans le shadow DOM du <geo-map>.
      const legendCfg = params.legend;
      if (legendCfg && (legendCfg.mode === "auto" || legendCfg.items)) {
        const items = Array.isArray(legendCfg.items)
          ? legendCfg.items
          : this._autoLegendItems(legendCfg, catalogLayers, orderedLayers);
        if (items && items.length) {
          this._injectLegend(legendCfg, items);
        }
      }

      // fitBounds auto sur zone.bbox ou premier layer avec features
      if (zone.bbox) {
        this.map.fitBounds(zone.bbox, { padding: 40, maxZoom: 16 });
      } else if (catalogLayers.length && catalogLayers[0].geojson) {
        try {
          const b = new ml.LngLatBounds();
          (catalogLayers[0].geojson.features || []).forEach((f) => {
            const walk = (c) => {
              if (typeof c[0] === "number") { b.extend(c); return; }
              c.forEach(walk);
            };
            walk((f.geometry && f.geometry.coordinates) || []);
          });
          if (!b.isEmpty()) this.map.fitBounds(b, { padding: 40, maxZoom: 16 });
        } catch (e) { /* ignore */ }
      }

      this.dispatchEvent(new CustomEvent("geo:map-ready", {
        detail: { map: this.map, layerIds: this._layerIds },
      }));
    };
    // V1.20.5 patch : evite race condition apres re-render (setter params
    // ou hostContext). Si le style basemap est deja charge, applique
    // immediatement ; sinon attend l'event "load" comme d'habitude.
    if (this.map.isStyleLoaded && this.map.isStyleLoaded()) {
      applyLayers();
    } else {
      this.map.on("load", applyLayers);
    }
  }

  _wireInteractions(ml, layerId, sceneLayer, override) {
    const hoverAttrs = (override && override.hover_attributes) || [];
    const popupTpl = override && override.popup_template;
    const tooltipField = override && override.tooltip_field;
    // P2 contract-drift V1.13 (review 2026-07-08) : name_override utilise dans
    // le titre du popup a la place du nom brut du scene_layer.
    const displayName =
      (override && override.name_override) || sceneLayer.name || "Feature";

    // P2 contract-drift V1.13 : tooltip_field (attribut unique quick tooltip)
    // Prioritaire sur hover_attributes si les deux sont definis : tooltip_field
    // etant "quick label", hover_attributes est le "tooltip etendu".
    if (tooltipField && !hoverAttrs.length) {
      const quickTip = new ml.Popup({
        closeButton: false, closeOnClick: false, maxWidth: "220px",
      });
      this.map.on("mousemove", layerId, (e) => {
        this.map.getCanvas().style.cursor = "pointer";
        const v = e.features[0]?.properties?.[tooltipField];
        if (v !== undefined && v !== null) {
          quickTip
            .setLngLat(e.lngLat)
            .setHTML('<div style="font-size:12px">' + _escapeHtml(String(v)) + "</div>")
            .addTo(this.map);
        }
      });
      this.map.on("mouseleave", layerId, () => {
        this.map.getCanvas().style.cursor = "";
        quickTip.remove();
      });
    }

    // Tooltip hover etendu (hover_attributes whitelist)
    if (hoverAttrs.length) {
      const tooltip = new ml.Popup({
        closeButton: false, closeOnClick: false, maxWidth: "240px",
      });
      this.map.on("mousemove", layerId, (e) => {
        this.map.getCanvas().style.cursor = "pointer";
        const props = (e.features[0] && e.features[0].properties) || {};
        const rows = hoverAttrs
          .filter((a) => props[a] !== undefined)
          .map((a) =>
            '<div style="padding:2px 0"><strong>' +
            _escapeHtml(a) + "</strong> : " +
            _escapeHtml(String(props[a]).slice(0, 80)) + "</div>"
          )
          .join("");
        if (rows) {
          tooltip
            .setLngLat(e.lngLat)
            .setHTML('<div style="font-size:12px">' + rows + "</div>")
            .addTo(this.map);
        }
      });
      this.map.on("mouseleave", layerId, () => {
        this.map.getCanvas().style.cursor = "";
        tooltip.remove();
      });
    }

    // Popup click
    const popup = new ml.Popup({ closeButton: true, maxWidth: "320px" });
    this.map.on("click", layerId, (e) => {
      const props = (e.features[0] && e.features[0].properties) || {};
      let html;
      if (popupTpl) {
        html = _renderPopup(popupTpl, props);
      } else {
        const entries = Object.entries(props).slice(0, 10);
        html =
          '<div style="font-size:13px"><strong style="color:#000091">' +
          _escapeHtml(displayName) + "</strong>" +
          entries
            .map(
              ([k, v]) =>
                '<div style="padding:3px 0;border-top:1px solid #eee;margin-top:3px">' +
                '<span style="color:#666;font-size:11px">' + _escapeHtml(k) +
                "</span><br><strong>" +
                _escapeHtml(String(v).slice(0, 120)) + "</strong></div>"
            )
            .join("") + "</div>";
      }
      popup.setLngLat(e.lngLat).setHTML(html).addTo(this.map);
    });
  }

  // ── API impérative ─────────────────────────────────────────────────────
  // P1 correctness (review 2026-07-08) : chaque methode guarde l'existence du
  // layer via getLayer() pour eviter les crashes MapLibre "The layer X does
  // not exist in the map's style" (typiquement sur ids stales, layer
  // supprime, ou binding recu avant map.on('load')).

  setFilter(layerId, filter) {
    if (!this.map || !this.map.getLayer(layerId)) return;
    this.map.setFilter(layerId, filter);
  }
  setPaintProperty(layerId, prop, value) {
    if (!this.map || !this.map.getLayer(layerId)) return;
    this.map.setPaintProperty(layerId, prop, value);
  }
  setLayoutProperty(layerId, prop, value) {
    if (!this.map || !this.map.getLayer(layerId)) return;
    this.map.setLayoutProperty(layerId, prop, value);
  }
  fitBoundsTo(bbox) {
    if (this.map && bbox && bbox.length === 4) {
      this.map.fitBounds(bbox, { padding: 40, maxZoom: 16 });
    }
  }
  setLayerVisibility(layerId, visible) {
    if (!this.map || !this.map.getLayer(layerId)) return;
    this.setLayoutProperty(layerId, "visibility", visible ? "visible" : "none");
  }
  setLayerOpacity(layerId, opacity) {
    if (!this.map) return;
    const layer = this.map.getLayer(layerId);
    if (!layer) return;
    const paintProp = _colorPaintPropFor(layer.type, "opacity");
    if (paintProp) this.map.setPaintProperty(layerId, paintProp, opacity);
  }

  /**
   * Change la couleur d'un layer selon son type (fill/circle/line).
   * P1 correctness (review 2026-07-08) : evite crash sur layers non-polygone.
   */
  setLayerColor(layerId, color) {
    if (!this.map) return;
    const layer = this.map.getLayer(layerId);
    if (!layer) return;
    const paintProp = _colorPaintPropFor(layer.type, "color");
    if (paintProp) this.map.setPaintProperty(layerId, paintProp, color);
    else console.warn("[geo-components] setLayerColor: type layer non supporte:", layer.type);
  }

  /**
   * Point d'entrée de l'orchestrateur bindings. Applique un binding reçu.
   */
  // V0.3.1 alpha.4 : derive items de legende auto depuis
  // classification._compiled_expression du layer from_layer.
  _autoLegendItems(legendCfg, catalogLayers, orderedLayers) {
    const fromId = legendCfg.from_layer;
    if (!fromId) return null;
    const target = orderedLayers.find(o => o.scene && o.scene.id === fromId);
    if (!target) return null;
    const classif = (target.override && target.override.classification)
                    || target.scene.classification;
    if (!classif) return null;
    // Support 2 formats : V1.13 paint_expression direct, ou V0.3.1 shimme
    const expr = classif.paint_expression;
    if (!Array.isArray(expr) || expr.length < 3) return null;
    const items = [];
    if (expr[0] === "step") {
      // ["step", ["get", field], color0, threshold1, color1, threshold2, color2, ...]
      const firstColor = expr[2];
      items.push({ label: "avant " + expr[3], color: firstColor });
      for (let i = 3; i + 1 < expr.length; i += 2) {
        const thr = expr[i];
        const col = expr[i + 1];
        const next = expr[i + 2];
        const label = (i + 2 < expr.length)
          ? thr + "-" + next
          : "≥ " + thr;
        items.push({ label, color: col });
      }
    } else if (expr[0] === "match") {
      // ["match", ["get", field], v1, c1, v2, c2, ..., fallback]
      for (let i = 2; i + 1 < expr.length; i += 2) {
        items.push({ label: String(expr[i]), color: expr[i + 1] });
      }
    }
    return items;
  }

  // V0.3.1 alpha.4 : injecte une legende auto-generee dans le shadow DOM.
  // Utilise le meme rendering que <geo-legend> mais integre inline.
  _injectLegend(legendCfg, items) {
    const shadow = this.shadowRoot;
    if (!shadow) return;
    const pos = legendCfg.position || "bottom-left";
    const title = legendCfg.title || "Légende";
    const wrap = document.createElement("div");
    wrap.className = "gc-legend";
    const posStyles = {
      "top-left":     "top:8px;left:8px;",
      "top-right":    "top:8px;right:8px;",
      "bottom-left":  "bottom:8px;left:8px;",
      "bottom-right": "bottom:44px;right:8px;",
    };
    const styleAttr = posStyles[pos] || posStyles["bottom-left"];
    wrap.setAttribute("style",
      "position:absolute;" + styleAttr +
      "background:rgba(255,255,255,0.94);padding:8px 12px;border-radius:4px;" +
      "box-shadow:0 1px 4px rgba(0,0,0,0.15);font-family:Marianne,system-ui,sans-serif;" +
      "font-size:11px;color:#333;z-index:5;max-width:260px;"
    );
    const t = document.createElement("div");
    t.setAttribute("style",
      "color:#000091;text-transform:uppercase;font-size:10px;letter-spacing:0.5px;" +
      "font-weight:700;margin-bottom:6px;");
    t.textContent = title;
    wrap.appendChild(t);
    for (const it of items) {
      const chip = document.createElement("div");
      chip.setAttribute("style",
        "display:flex;align-items:center;gap:6px;margin:2px 0;");
      const sw = document.createElement("span");
      sw.setAttribute("style",
        "width:14px;height:10px;background:" + it.color + ";" +
        "border:1px solid rgba(0,0,0,0.15);border-radius:2px;flex-shrink:0;");
      chip.appendChild(sw);
      const lbl = document.createElement("span");
      lbl.textContent = it.label;
      chip.appendChild(lbl);
      wrap.appendChild(chip);
    }
    // Injecter dans .gc-map pour que la legende suive la carte
    const mapDiv = shadow.querySelector(".gc-map");
    if (mapDiv) mapDiv.appendChild(wrap);
  }

  applyBinding(detail) {
    const { prop, value } = detail || {};
    if (!this.map || !prop) return;
    const primaryLayer = this._layerIds[0];

    switch (prop) {
      case "time": {
        // V0.3.1 alpha.4 : si le layer a opacity.reactive prop=time, on
        // applique un fade progressif (matched/before/after/null_value)
        // au lieu du filter binaire hide/show V1.13.
        const year = _asYear(value);
        if (year == null || !primaryLayer) break;
        const timeField = detail.field || "annee";

        for (const lid of this._layerIds) {
          const cfg = this._reactiveOpacity[lid];
          if (cfg && cfg.prop === "time") {
            // Fade progressif : expression case selon (field vs value)
            const field = cfg.field || timeField;
            const matched = cfg.matched ?? 0.85;
            const before = cfg.before ?? 0.85;
            const after = cfg.after ?? 0.12;
            const nullValue = cfg.null_value ?? 0.35;
            const expr = [
              "case",
              ["==", ["typeof", ["get", field]], "null"], nullValue,
              ["<", ["to-number", ["get", field]], year], before,
              [">", ["to-number", ["get", field]], year], after,
              matched,
            ];
            const layer = this.map.getLayer(lid);
            if (!layer) continue;
            const paintKey =
              layer.type === "fill" ? "fill-opacity" :
              layer.type === "line" ? "line-opacity" :
              layer.type === "circle" ? "circle-opacity" :
              layer.type === "fill-extrusion" ? "fill-extrusion-opacity" :
              null;
            if (paintKey) {
              try { this.map.setPaintProperty(lid, paintKey, expr); }
              catch (e) { /* pas critique */ }
            }
          } else if (lid === primaryLayer) {
            // Fallback V1.13 : filter binaire sur le layer primaire
            try {
              this.map.setFilter(lid, ["<=", ["get", timeField], year]);
            } catch (e) { /* ignore */ }
          }
        }
        break;
      }
      case "scenario":
        // P1 correctness (review 2026-07-08) : utiliser setLayerColor qui
        // detecte le type du layer (fill/circle/line) au lieu de hard-coder
        // fill-color (crashait sur circle/line).
        if (detail.style_map && primaryLayer) {
          const color = detail.style_map[value];
          if (color) this.setLayerColor(primaryLayer, color);
        }
        break;
      case "filter":
        if (primaryLayer && value) this.setFilter(primaryLayer, value);
        break;
      case "bbox":
        this.fitBoundsTo(value);
        break;
      case "layers":
        if (value && typeof value === "object") {
          Object.entries(value).forEach(([lid, visible]) => {
            this.setLayerVisibility(lid, !!visible);
          });
        }
        break;
      case "opacity":
        if (value && typeof value === "object") {
          Object.entries(value).forEach(([lid, opacity]) => {
            this.setLayerOpacity(lid, +opacity);
          });
        }
        break;
      default:
        console.warn("[geo-components] Unknown binding prop:", prop);
    }
  }
}

/** Utilitaire : normalise une valeur de binding "time" en année entière. */
function _asYear(v) {
  if (v == null) return null;
  if (typeof v === "number") return Math.floor(v);
  const s = String(v);
  const m = s.match(/(\d{4})/);
  return m ? +m[1] : null;
}

// ──────────────────────────────────────────────────────────────────────────
// 4. <geo-timeline> — Controller slider temporel
// ──────────────────────────────────────────────────────────────────────────

/**
 * Custom Element <geo-timeline> — slider avec play/pause/step. Émet des
 * events geo:bind {prop:'time', value} pour piloter un <geo-map> target.
 *
 * Attributs HTML :
 *   - id (obligatoire)
 *   - min (année ou date ISO)
 *   - max
 *   - step (défaut 1)
 *   - value (initial ; défaut = min)
 *   - target (id du <geo-map> à piloter)
 *   - field (champ GeoJSON à filtrer ; défaut 'annee')
 *   - format ('year' | 'date-fr' ; défaut 'year')
 *   - play-speed (multiplier vitesse play ; défaut '1x')
 *   - label (texte affiché à gauche du slider)
 */
export class GeoTimeline extends HTMLElement {
  static get observedAttributes() {
    return ["min", "max", "step", "value", "target", "field", "format", "label"];
  }

  constructor() {
    super();
    this.attachShadow({ mode: "open" });
    this._playing = false;
    this._playTimer = null;
  }

  connectedCallback() {
    geoBindings.install();
    this._render();
    // V1.20.6 : auto-anchor overlay in-map. Si mode="overlay" (default) et
    // que le target resout localement a un <geo-map>, se deplacer dans le
    // light DOM de la carte avec slot="overlay" pour un rendu ancre visuellement.
    // Sinon (cross-iframe : target dans un autre document), rester en flow
    // et compter sur postMessage via GeoBindings.
    this._autoAnchor();
  }

  disconnectedCallback() {
    this._stopPlay();
  }

  _autoAnchor() {
    // Deja ancre (setAttribute slot deja fait), pas de re-move.
    if (this.getAttribute("slot") === "overlay") return;
    const mode = this.getAttribute("mode") || "overlay";
    if (mode !== "overlay") return;
    const rawTarget = this.getAttribute("target");
    if (!rawTarget) return;
    // Utilise le resolveur tolerant de GeoBindings (multi-conventions ID).
    const targetEl = geoBindings._resolveTarget
      ? geoBindings._resolveTarget(rawTarget)
      : document.querySelector("#" + CSS.escape(rawTarget));
    if (!targetEl || targetEl.tagName !== "GEO-MAP") return;
    // Cacher toute la section source (evite titre "Timeline" orphelin et
    // gros espace vide). Prefere story-section (ancetre du story-component)
    // pour supprimer aussi le h2 titre. Fallback : parent direct.
    let hideCandidate = this.parentElement;
    while (hideCandidate && hideCandidate !== document.body) {
      const cls = hideCandidate.className || "";
      if (typeof cls === "string" && /\bstory-section\b/.test(cls)) break;
      hideCandidate = hideCandidate.parentElement;
    }
    if (hideCandidate && hideCandidate !== document.body) {
      hideCandidate.setAttribute("data-gc-overlay-source", "1");
      hideCandidate.style.display = "none";
    }
    // Move dans le light DOM du <geo-map> : le slot name="overlay" cote
    // shadow DOM va le rendre en position absolute sur la carte.
    this.setAttribute("slot", "overlay");
    // Cosmetique : override styles pour un look overlay (fond opaque,
    // shadow subtile) le composant restant lisible sur la basemap.
    this.style.pointerEvents = "auto";
    this.style.boxShadow = "0 2px 8px rgba(0,0,0,0.15)";
    targetEl.appendChild(this);  // move DOM : trigger disconnectedCallback + connectedCallback
  }

  attributeChangedCallback() {
    if (this.isConnected) this._render();
  }

  _render() {
    const shadow = this.shadowRoot;
    shadow.innerHTML = "";

    const min = +(this.getAttribute("min") || 1900);
    const max = +(this.getAttribute("max") || 2024);
    const step = +(this.getAttribute("step") || 1);
    const value = +(this.getAttribute("value") || min);
    const label = this.getAttribute("label") || "Année";

    const style = document.createElement("style");
    style.textContent = `
      :host { display: block; padding: 10px 14px; background: #fff;
              border: 1px solid #e5e5e5; border-radius: 6px;
              font-family: Marianne, system-ui, sans-serif; }
      .row { display: flex; align-items: center; gap: 10px; }
      .lbl { font-size: 12px; color: #666; text-transform: uppercase;
             letter-spacing: 0.5px; font-weight: 600; }
      .btn { padding: 4px 10px; background: #000091; color: #fff;
             border: none; border-radius: 3px; cursor: pointer;
             font-family: inherit; font-size: 13px; font-weight: 600; }
      .btn:hover { background: #1212a3; }
      .val { color: #000091; font-weight: 700; font-size: 14px;
             min-width: 60px; text-align: center; }
      input[type=range] { flex: 1; accent-color: #000091; }
    `;
    shadow.appendChild(style);

    const row = document.createElement("div");
    row.className = "row";

    const l = document.createElement("span");
    l.className = "lbl";
    l.textContent = label;
    row.appendChild(l);

    const btn = document.createElement("button");
    btn.className = "btn";
    btn.textContent = "▶";
    btn.setAttribute("aria-label", "Play / Pause");
    btn.addEventListener("click", () => {
      if (this._playing) this._stopPlay(); else this._startPlay();
      btn.textContent = this._playing ? "⏸" : "▶";
    });
    row.appendChild(btn);

    const range = document.createElement("input");
    range.type = "range";
    range.min = min;
    range.max = max;
    range.step = step;
    range.value = value;
    range.setAttribute("aria-label", label);
    range.addEventListener("input", (e) => {
      this._publish(+e.target.value);
      valEl.textContent = this._format(+e.target.value);
    });
    row.appendChild(range);

    const valEl = document.createElement("span");
    valEl.className = "val";
    valEl.textContent = this._format(value);
    row.appendChild(valEl);

    shadow.appendChild(row);

    this._range = range;
    this._valEl = valEl;

    // Publie la valeur initiale pour synchroniser le target
    this._publish(value);
  }

  _publish(value) {
    const target = this.getAttribute("target") || "";
    const field = this.getAttribute("field") || "annee";
    if (!target) return;
    GeoBindings.publish(this, {
      from: this.id,
      target,
      prop: "time",
      field,
      value,
    });
  }

  _format(v) {
    const format = this.getAttribute("format") || "year";
    if (format === "year") return String(Math.floor(v));
    if (format === "date-fr") {
      try {
        return new Date(v).toLocaleDateString("fr-FR");
      } catch (e) { return String(v); }
    }
    return String(v);
  }

  _startPlay() {
    const speed = this.getAttribute("play-speed") || "1x";
    const intervalMs =
      speed === "0.5x" ? 800 :
      speed === "2x" ? 200 :
      speed === "4x" ? 100 : 400;
    this._playing = true;
    this._playTimer = setInterval(() => {
      const step = +(this.getAttribute("step") || 1);
      const max = +(this.getAttribute("max") || 2024);
      let v = +this._range.value + step;
      if (v > max) v = +this.getAttribute("min");
      this._range.value = v;
      this._valEl.textContent = this._format(v);
      this._publish(v);
    }, intervalMs);
  }

  _stopPlay() {
    this._playing = false;
    if (this._playTimer) {
      clearInterval(this._playTimer);
      this._playTimer = null;
    }
  }
}

// ──────────────────────────────────────────────────────────────────────────
// 5. <geo-legend> — Légende métier CEREMA
// ──────────────────────────────────────────────────────────────────────────

/**
 * Custom Element <geo-legend> — portage 1:1 du partial _legend_partial.j2 de
 * qgis-sspcloud. 3 formats supportés : chips (défaut), gradient_bar,
 * proportional.
 *
 * Attributs HTML :
 *   - format ('chips'|'gradient_bar'|'proportional' ; défaut 'chips')
 *   - items (JSON string : [{label, color, count?, size?}])
 *   - position ('bottom'|'right'|'floating' ; défaut 'bottom')
 *   - title (défaut 'Légende')
 */
export class GeoLegend extends HTMLElement {
  static get observedAttributes() {
    return ["format", "items", "position", "title"];
  }

  constructor() {
    super();
    this.attachShadow({ mode: "open" });
  }

  connectedCallback() {
    this._render();
  }

  attributeChangedCallback() {
    if (this.isConnected) this._render();
  }

  _render() {
    const shadow = this.shadowRoot;
    shadow.innerHTML = "";

    const format = this.getAttribute("format") || "chips";
    const title = this.getAttribute("title") || "Légende";
    // P2 contract-drift V1.13 (review 2026-07-08) : position bottom|right|floating.
    const position = this.getAttribute("position") || "bottom";
    let items = [];
    try {
      items = JSON.parse(this.getAttribute("items") || "[]");
    } catch (e) {
      console.warn("[geo-components] geo-legend items JSON invalide:", e);
    }

    // Positionnement selon attribut position (V1.13)
    const positionStyles = {
      bottom: ":host { position: relative; }",
      right: ":host { position: relative; max-width: 300px; }",
      floating:
        ":host { position: absolute; bottom: 20px; left: 20px; z-index: 10; " +
        "box-shadow: 0 2px 8px rgba(0,0,0,0.15); background: rgba(255,255,255,0.96); }",
    };

    const style = document.createElement("style");
    style.textContent = (positionStyles[position] || positionStyles.bottom) + `
      :host { display: block; padding: 12px 20px; background: #fafafa;
              border: 1px solid #e5e5e5; border-radius: 6px;
              font-family: Marianne, system-ui, sans-serif;
              font-size: 12px; color: #444; }
      .ttl { color: #000091; text-transform: uppercase; font-size: 11px;
             letter-spacing: 0.5px; font-weight: 700; display: block;
             margin-bottom: 8px; }
      .chips { display: flex; flex-wrap: wrap; gap: 14px; align-items: center; }
      .chip { display: inline-flex; align-items: center; gap: 6px; }
      .swatch { width: 14px; height: 14px; border-radius: 3px;
                border: 1px solid rgba(0,0,0,0.1); }
      .cnt { color: #888; font-style: italic; }
      .grad { display: flex; height: 14px; border-radius: 3px; overflow: hidden;
              border: 1px solid rgba(0,0,0,0.1); max-width: 520px; }
      .grad > div { flex: 1; }
      .grad-labels { display: flex; font-size: 10px; color: #666;
                     margin-top: 4px; max-width: 520px; }
      .grad-labels > div { flex: 1; text-align: center; padding: 0 2px; }
      .prop { display: flex; align-items: flex-end; gap: 14px; flex-wrap: wrap; }
      .prop-item { display: flex; flex-direction: column;
                   align-items: center; gap: 2px; }
      .prop-circle { border-radius: 50%; border: 1px solid #fff;
                     box-shadow: 0 0 0 1px rgba(0,0,0,0.2); opacity: 0.75; }
    `;
    shadow.appendChild(style);

    const t = document.createElement("strong");
    t.className = "ttl";
    t.textContent = title;
    shadow.appendChild(t);

    if (format === "gradient_bar") {
      const bar = document.createElement("div");
      bar.className = "grad";
      items.forEach((it) => {
        const div = document.createElement("div");
        div.style.background = it.color;
        bar.appendChild(div);
      });
      shadow.appendChild(bar);
      const labels = document.createElement("div");
      labels.className = "grad-labels";
      items.forEach((it) => {
        const div = document.createElement("div");
        div.textContent = it.label || "";
        labels.appendChild(div);
      });
      shadow.appendChild(labels);
    } else if (format === "proportional") {
      const wrap = document.createElement("div");
      wrap.className = "prop";
      items.forEach((it) => {
        const item = document.createElement("div");
        item.className = "prop-item";
        const size = it.size || 20;
        const circ = document.createElement("div");
        circ.className = "prop-circle";
        circ.style.width = size + "px";
        circ.style.height = size + "px";
        circ.style.background = it.color;
        item.appendChild(circ);
        const lbl = document.createElement("span");
        lbl.style.fontSize = "10px";
        lbl.style.color = "#666";
        lbl.textContent = it.label || "";
        item.appendChild(lbl);
        wrap.appendChild(item);
      });
      shadow.appendChild(wrap);
    } else {
      // chips
      const wrap = document.createElement("div");
      wrap.className = "chips";
      items.forEach((it) => {
        const chip = document.createElement("span");
        chip.className = "chip";
        const sw = document.createElement("span");
        sw.className = "swatch";
        sw.style.background = it.color;
        chip.appendChild(sw);
        const txt = document.createElement("span");
        txt.textContent = it.label || "";
        if (it.count !== undefined && it.count !== null) {
          const em = document.createElement("em");
          em.className = "cnt";
          em.textContent = " (" + it.count + ")";
          txt.appendChild(em);
        }
        chip.appendChild(txt);
        wrap.appendChild(chip);
      });
      shadow.appendChild(wrap);
    }
  }
}

// ──────────────────────────────────────────────────────────────────────────
// 6. Auto-registration Custom Elements
// ──────────────────────────────────────────────────────────────────────────

if (typeof customElements !== "undefined") {
  if (!customElements.get("geo-map")) {
    customElements.define("geo-map", GeoMap);
  }
  if (!customElements.get("geo-timeline")) {
    customElements.define("geo-timeline", GeoTimeline);
  }
  if (!customElements.get("geo-legend")) {
    customElements.define("geo-legend", GeoLegend);
  }
}

// ──────────────────────────────────────────────────────────────────────────
// 7. Export public
// ──────────────────────────────────────────────────────────────────────────

export default {
  LIB_VERSION,
  BASEMAPS,
  DEFAULT_PALETTE,
  GeoMap,
  GeoTimeline,
  GeoLegend,
  GeoBindings,
  geoBindings,
};
