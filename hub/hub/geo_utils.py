"""
Utilitaires de reprojection GeoJSON pour le hub Passerelle / qgis-sspcloud.

Contexte (chantier G3, 2026-07-13) :
- Les couches BD TOPO / RGE ALTI IGN sont fournies en Lambert 93 natif
  (EPSG:2154). Le hub inline le GeoJSON tel quel dans le HTML SSR.
- MapLibre attend systematiquement du WGS84 (EPSG:4326). Interpreter des
  coordonnees Lambert (metres, ~800000 / ~6200000) comme lng/lat les envoie
  au large de l'Afrique -> 0 feature rendu dans la bbox reelle.
- Ce module fournit :
    * detect_geojson_crs()   -> lit le champ crs.properties.name eventuel
    * reproject_geojson_to_4326() -> reprojette toutes les geometries
      supportees vers 4326 en s'appuyant sur pyproj.

Les fonctions sont ecrites en mode "fail-soft" : l'appelant est responsable
de logger un warning et de continuer si pyproj n'est pas dispo ou si un
CRS n'est pas parsable.
"""

from __future__ import annotations

import re
from typing import Any, Optional


# Regex tolerante : cherche EPSG puis capture le DERNIER groupe de chiffres
# de la chaine. Couvre :
#   urn:ogc:def:crs:EPSG::2154
#   urn:ogc:def:crs:EPSG:6.9:2154   (version OGC intercalee)
#   EPSG:2154 / epsg:2154
#   http://www.opengis.net/def/crs/EPSG/0/2154
_EPSG_CODE_RE = re.compile(r"EPSG[^0-9]*(?:\d+(?:\.\d+)?[:/])?(\d+)\s*$", re.IGNORECASE)


def detect_geojson_crs(gj_data: dict) -> Optional[str]:
    """Extrait le CRS declare dans un FeatureCollection GeoJSON legacy.

    Le GeoJSON RFC 7946 impose WGS84 implicite et deprecate le champ ``crs``,
    mais QGIS et de nombreux exports OGR (ogr2ogr par defaut) continuent
    d'ecrire un bloc ``crs.properties.name`` de type
    ``urn:ogc:def:crs:EPSG::2154`` ou ``EPSG:2154``.

    Args:
        gj_data: FeatureCollection GeoJSON parse en dict.

    Returns:
        Chaine normalisee ``"EPSG:<code>"`` si detectee, sinon ``None``.
    """
    if not isinstance(gj_data, dict):
        return None
    crs = gj_data.get("crs")
    if not isinstance(crs, dict):
        return None
    name = ((crs.get("properties") or {}).get("name") or "").strip()
    if not name:
        return None
    m = _EPSG_CODE_RE.search(name)
    if m:
        return f"EPSG:{m.group(1)}"
    # Fallback : "EPSG:2154" nu
    up = name.upper()
    if up.startswith("EPSG:"):
        tail = up.split(":", 1)[1].strip()
        if tail.isdigit():
            return f"EPSG:{tail}"
    return None


def _reproject_coords(coords: Any, transform) -> Any:
    """Reprojection recursive d'une structure coordinates GeoJSON.

    Supporte Point, LineString, Polygon, MultiPoint, MultiLineString,
    MultiPolygon. Un point est un ``[x, y]`` ou ``[x, y, z]`` (le z est
    conserve tel quel).
    """
    if not coords:
        return coords
    # Point : [x, y] ou [x, y, z]
    if isinstance(coords[0], (int, float)):
        x, y = float(coords[0]), float(coords[1])
        lon, lat = transform(x, y)
        if len(coords) >= 3:
            return [lon, lat, coords[2]]
        return [lon, lat]
    # Recursion : liste de points ou liste de rings
    return [_reproject_coords(sub, transform) for sub in coords]


def reproject_geojson_to_4326(gj_data: dict, from_crs: str) -> dict:
    """Reprojection in-place (copie) d'un FeatureCollection GeoJSON vers 4326.

    Ne modifie QUE ``feature.geometry.coordinates`` : ``properties`` et
    ``type`` sont preserves. Retire le champ ``crs`` du top-level (RFC 7946).

    Args:
        gj_data: FeatureCollection GeoJSON.
        from_crs: CRS source (ex. ``"EPSG:2154"``).

    Returns:
        Nouveau dict GeoJSON reprojete.

    Raises:
        ImportError: si pyproj n'est pas installe.
        pyproj.exceptions.CRSError: si le CRS source est invalide.
    """
    from pyproj import Transformer  # import tardif : fail-soft cote appelant

    transformer = Transformer.from_crs(from_crs, "EPSG:4326", always_xy=True)
    transform = transformer.transform

    out = dict(gj_data)
    features_in = out.get("features") or []
    features_out = []
    for feat in features_in:
        if not isinstance(feat, dict):
            features_out.append(feat)
            continue
        new_feat = dict(feat)
        geom = feat.get("geometry")
        if isinstance(geom, dict) and geom.get("coordinates") is not None:
            new_geom = dict(geom)
            new_geom["coordinates"] = _reproject_coords(geom["coordinates"], transform)
            new_feat["geometry"] = new_geom
        features_out.append(new_feat)
    out["features"] = features_out
    # RFC 7946 : WGS84 implicite, retire le champ crs top-level
    out.pop("crs", None)
    return out
