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
export const LIB_VERSION = "0.1.0-alpha.1";

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

/**
 * Résout la zone d'étude en {center, zoom, bbox} exploitables par MapLibre.
 * Supporte les 3 modes du contrat V1.13 : commune | manual | study.
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

  if (kind === "commune" && hostContext?.commune_bbox) {
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
 */
function _detectGeomType(geometryTypeHint) {
  const h = (geometryTypeHint || "").toLowerCase();
  if (h.includes("point")) return "point";
  if (h.includes("line") || h.includes("linestring")) return "line";
  return "fill";
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
    this.map.addControl(new ml.NavigationControl({ showCompass: false }), "top-left");

    const layersOverride = params.layers_override || [];
    const catalogLayers =
      this._hostContext.catalog_layers || params._catalog_layers || [];

    this._layerIds = [];

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
          const layerDef = _buildMapLibreLayer(sceneLayer, override, i);
          this.map.addLayer(layerDef);
          this._layerIds.push(layerDef.id);

          // Interactions : hover / popup
          this._wireInteractions(ml, layerDef.id, sceneLayer, override);
        } catch (e) {
          console.warn(
            "[geo-components] addLayer failed for",
            sceneLayer.id, "->", safeId,
            e
          );
        }
      });

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
  applyBinding(detail) {
    const { prop, value } = detail || {};
    if (!this.map || !prop) return;
    const primaryLayer = this._layerIds[0];

    switch (prop) {
      case "time": {
        // Filtre temporel : ['<=', ['get', 'annee'], year]
        const year = _asYear(value);
        if (year != null && primaryLayer) {
          const timeField = detail.field || "annee";
          this.map.setFilter(primaryLayer, ["<=", ["get", timeField], year]);
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
