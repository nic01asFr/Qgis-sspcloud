"""
hub.maplibre_style_mapper — Mapping Scene Manifest V0.2 → MapLibre style spec.

Sprint Composants Phase 2 (2026-06-25). Référence interne qgis-sspcloud
en attendant le doc formel côté `cerema-offre-de-service/docs/mapping-
maplibre.md` (Lead #1 — annonce posée wikichat, à valider mainteneur).

Sources de vérité :
- Scene Manifest V0.2 6 kinds : `hub/hub/vendor/scene_manifest.py:64`
- Pattern atlas_bati custom layer Three.js :
  `editeur-volumes.html:200-330` (SURFAC²E)
- Capitalisé : `~/.wikichat/knowledge/maplibre-threejs-pattern-axis.md` §3

Mapping kind → MapLibre layer type :

| Scene Manifest kind | MapLibre type | paint                                       |
|---------------------|---------------|---------------------------------------------|
| single              | fill/line/circle | {[prop]: "#hex", [prop]-opacity: 0..1}   |
| categorized         | fill/line/circle | {[prop]: ["case", ["==", ["get",f],v], c, default]} |
| graduated           | fill/line/circle | {[prop]: ["interpolate", ["linear"], ["get",f], b0,c0,...]} |
| rule_based          | fill/line/circle | {[prop]: ["case", filter1, c1, ..., fallback]} |
| extrusion           | fill-extrusion   | {fill-extrusion-height: ["get", height_field], ...} |
| 3d_model            | custom layer Three.js (cf. atlas_bati pattern) — pas géré ici |

`prop` = `fill-color` / `line-color` / `circle-color` selon `geometry_type`.

Sprint 4 (V2 deferred) ajoutera le mapping `3d_model` via Three.js custom
layer (pattern atlas_bati vendorisable dans geoai-kit).
"""

from __future__ import annotations

from typing import Any


# Defaut DSFR fallback (gris CEREMA pour valeurs non-matched)
DEFAULT_FALLBACK_COLOR = "#9e9e9e"


def geometry_to_layer_type(geometry_type: str) -> tuple[str, str]:
    """Retourne (maplibre_layer_type, paint_color_prop)."""
    g = (geometry_type or "Polygon").lower()
    if g in ("point", "multipoint"):
        return "circle", "circle-color"
    if g in ("linestring", "multilinestring"):
        return "line", "line-color"
    # Polygon / MultiPolygon / default
    return "fill", "fill-color"


def map_single(style: dict[str, Any], color_prop: str) -> dict[str, Any]:
    """kind=single → paint statique."""
    color = style.get("color", DEFAULT_FALLBACK_COLOR)
    opacity = float(style.get("opacity", 1.0))
    return {color_prop: color, f"{_layer_kind(color_prop)}-opacity": opacity}


def map_categorized(style: dict[str, Any], color_prop: str) -> dict[str, Any]:
    """kind=categorized → paint expression ['case', ['==', ['get', field], v1], c1, ...]"""
    field = style.get("field", "")
    stops = style.get("stops", []) or []
    fallback = style.get("fallback_color") or DEFAULT_FALLBACK_COLOR
    opacity = float(style.get("opacity", 1.0))

    if not field or not stops:
        return {color_prop: fallback, f"{_layer_kind(color_prop)}-opacity": opacity}

    expr: list[Any] = ["case"]
    for stop in stops:
        value = stop.get("value")
        color = stop.get("color", fallback)
        expr.append(["==", ["get", field], value])
        expr.append(color)
    expr.append(fallback)  # default case

    return {color_prop: expr, f"{_layer_kind(color_prop)}-opacity": opacity}


def map_graduated(style: dict[str, Any], color_prop: str) -> dict[str, Any]:
    """kind=graduated → paint expression ['interpolate', ['linear'], ['get', field], b0, c0, b1, c1, ...]"""
    field = style.get("field", "")
    stops = style.get("stops", []) or []
    fallback = style.get("fallback_color") or DEFAULT_FALLBACK_COLOR
    opacity = float(style.get("opacity", 1.0))

    if not field or len(stops) < 2:
        return {color_prop: fallback, f"{_layer_kind(color_prop)}-opacity": opacity}

    # Tri par valeur croissante (MapLibre interpolate exige ordre)
    sorted_stops = sorted(
        stops,
        key=lambda s: float(s.get("value", 0)),
    )
    expr: list[Any] = ["interpolate", ["linear"], ["get", field]]
    for stop in sorted_stops:
        try:
            expr.append(float(stop["value"]))
            expr.append(stop.get("color", fallback))
        except (KeyError, ValueError, TypeError):
            continue

    if len(expr) < 5:  # ['interpolate', ['linear'], ['get', f], b0, c0] minimum
        return {color_prop: fallback, f"{_layer_kind(color_prop)}-opacity": opacity}

    return {color_prop: expr, f"{_layer_kind(color_prop)}-opacity": opacity}


def map_rule_based(style: dict[str, Any], color_prop: str) -> dict[str, Any]:
    """kind=rule_based → paint expression ['case', filter1, c1, ..., fallback]

    Chaque rule est `{filter: <maplibre-filter-expr>, color, label}`. Le
    `filter` est censé être déjà au format MapLibre filter expression
    (ex: ["all", [">=", ["get", "h"], 10], ["<", ["get", "h"], 20]]).

    Sprint Composants Phase 2 ne fait pas la traduction QGIS-rule → MapLibre
    expression — c'est à l'éditeur form de fournir directement du MapLibre.
    """
    rules = style.get("rules", []) or []
    fallback = style.get("fallback_color") or DEFAULT_FALLBACK_COLOR
    opacity = float(style.get("opacity", 1.0))

    if not rules:
        return {color_prop: fallback, f"{_layer_kind(color_prop)}-opacity": opacity}

    expr: list[Any] = ["case"]
    for rule in rules:
        f = rule.get("filter")
        c = rule.get("color", fallback)
        if f is not None:
            expr.append(f)
            expr.append(c)
    expr.append(fallback)

    if len(expr) < 4:  # ['case', f1, c1, fallback] minimum
        return {color_prop: fallback, f"{_layer_kind(color_prop)}-opacity": opacity}

    return {color_prop: expr, f"{_layer_kind(color_prop)}-opacity": opacity}


def map_extrusion(style: dict[str, Any]) -> dict[str, Any]:
    """kind=extrusion → MapLibre layer type 'fill-extrusion' + paint dédié.

    Différent des autres kinds : retourne le layer type complet (pas juste
    le paint), car extrusion nécessite type='fill-extrusion' (pas 'fill').
    """
    height_field = style.get("height_field", "height")
    base_field = style.get("base_field")  # optionnel
    color = style.get("color", DEFAULT_FALLBACK_COLOR)
    opacity = float(style.get("opacity", 0.8))  # 0.8 défaut pour 3D lisible

    paint: dict[str, Any] = {
        "fill-extrusion-color": color,
        "fill-extrusion-height": ["coalesce", ["to-number", ["get", height_field]], 0],
        "fill-extrusion-opacity": opacity,
    }
    if base_field:
        paint["fill-extrusion-base"] = [
            "coalesce", ["to-number", ["get", base_field]], 0
        ]
    return {
        "type": "fill-extrusion",
        "paint": paint,
    }


def _layer_kind(color_prop: str) -> str:
    """Extrait 'fill', 'line', 'circle' du nom de paint property."""
    if color_prop.startswith("fill"):
        return "fill"
    if color_prop.startswith("line"):
        return "line"
    return "circle"


def apply_style_to_layer(
    layer_id: str,
    source_id: str,
    style: dict[str, Any],
    geometry_type: str = "Polygon",
) -> dict[str, Any]:
    """Construit un MapLibre layer dict à partir d'un Scene Manifest layer.style.

    Retourne {id, type, source, paint, [layout, filter, ...]} prêt pour
    `map.addLayer(layer)` côté client.

    Cas spéciaux :
    - kind=`extrusion` → type='fill-extrusion' (override geometry_type)
    - kind=`3d_model`  → renvoie None (custom layer Three.js, géré côté JS)
    """
    kind = (style or {}).get("kind", "single")

    # kind=3d_model n'est pas géré ici (custom layer JS)
    if kind == "3d_model":
        return {
            "_unsupported": True,
            "_reason": "kind=3d_model requires Three.js custom layer (cf. atlas_bati pattern)",
            "id": layer_id,
            "source": source_id,
        }

    # kind=extrusion : override layer type
    if kind == "extrusion":
        layer = map_extrusion(style)
        layer["id"] = layer_id
        layer["source"] = source_id
        return layer

    # Autres kinds : layer type dépend de la géométrie
    layer_type, color_prop = geometry_to_layer_type(geometry_type)

    paint_fn = {
        "single":      map_single,
        "categorized": map_categorized,
        "graduated":   map_graduated,
        "rule_based":  map_rule_based,
    }.get(kind, map_single)

    paint = paint_fn(style, color_prop)

    return {
        "id": layer_id,
        "type": layer_type,
        "source": source_id,
        "paint": paint,
    }


def manifest_to_maplibre_layers(manifest: dict[str, Any]) -> list[dict[str, Any]]:
    """Convertit un Scene Manifest V0.2 complet en liste de MapLibre layers.

    Utilisé par le renderer Jinja2 côté hub pour pré-générer le `style`
    MapLibre côté serveur (alternative à `applyManifestToMap` JS côté client).

    Args:
        manifest: dict du Scene Manifest V0.2 (validé Pydantic en amont)

    Returns:
        list de MapLibre layer dicts (chaque layer a id, type, source, paint).
        Layers `3d_model` ont `_unsupported=True` flag pour traitement spécial.
    """
    layers_out = []
    for layer in manifest.get("layers", []):
        layer_id = f"sm-{layer.get('id', 'unknown')}"
        source_id = f"sm-{layer.get('id', 'unknown')}-src"
        style = layer.get("style", {})
        geom = layer.get("geometry_type", "Polygon")
        layers_out.append(apply_style_to_layer(layer_id, source_id, style, geom))
    return layers_out
