"""hub.pmtiles_encoder — Conversion GeoJSON FeatureCollection -> PMTiles bytes.

Sprint sec-vague0 dette OOM piste PMTiles V0.4 Commit 2 (2026-07-21).

Contexte : la piste 1a v1-v4 (asyncio.to_thread + externalisation features
en geojson S3 + gzip Content-Encoding) resolvait le blocage GIL mais restait
bloquee par la limite MinIO SSPCloud sur uploads > 5MB (token stsonly
interdit multipart, put_object timeout sur payloads 19-38MB).

Ce module convertit un FeatureCollection GeoJSON en fichier .pmtiles
(format monofichier vector tiles v3, spec Protomaps), qui est :
- ~10x plus compact que le geojson brut (compression MVT + gzip par tuile)
- Streamable via HTTP Range Requests (chunks 16KB, chargement progressif)
- Lisible nativement par MapLibre via plugin pmtiles-protocol
- Immuable et cacheable (URL avec content hash)

Contrainte design :
- Pure Python (pas de dependency native tippecanoe) -> deployment simple
- Encoding fait dans un thread via asyncio.to_thread cote hub (pattern
  piste 1a v4) -> pas de blocage event loop uvicorn
- Fail-soft : caller (hub._externalize_large_features) doit try/except
  et fallback sur geojson gzip URL en cas d'erreur

API :
    pmtiles_bytes, metadata = geojson_to_pmtiles(
        geojson={"type": "FeatureCollection", "features": [...]},
        layer_name="batiments",
        min_zoom=6, max_zoom=16,
    )
"""
from __future__ import annotations

import contextlib
import logging
import math
from io import BytesIO
from typing import Any, Iterable

log = logging.getLogger(__name__)


@contextlib.contextmanager
def _gzip_sans_horodatage():
    """Rend l'ecriture PMTiles reproductible le temps d'un encodage.

    `pmtiles.writer` compresse ses metadonnees et son repertoire avec
    `gzip.compress()`, qui inscrit l'heure courante dans l'en-tete gzip. Deux
    encodages du meme GeoJSON produisaient donc deux fichiers differents :

        meme seconde      ce591f5a ce591f5a  identiques
        seconde suivante  ce591f5a 34bd278f  DIFFERENTS (meme longueur)
        octets qui different : 2, aux positions 131 et 185 -- les champs
        MTIME des deux en-tetes `1f8b 08 00 ....`

    Le publish nomme ensuite le fichier par `sha256(pmtiles_bytes)`. Cette
    empreinte designait donc du contenu-plus-heure : republier une couche
    inchangee creait une adresse nouvelle et laissait un objet orphelin dans
    le stockage, et l'URL d'une donnee ne permettait pas de savoir si elle
    avait change. C'est precisement ce que le mode de livraison avait ete
    introduit pour garantir.

    On force l'horodatage a zero -- la convention des archives reproductibles
    -- pendant l'appel, et on rend la fonction d'origine ensuite : le reste du
    programme continue de gzipper normalement.
    """
    import gzip

    origine = gzip.compress

    def compresse(donnees, compresslevel=9, *, mtime=None):
        return origine(donnees, compresslevel, mtime=0)

    gzip.compress = compresse
    try:
        yield
    finally:
        gzip.compress = origine


# ── Math helpers Web Mercator (WGS84 lng/lat <-> tile z/x/y) ─────────────────

def _lng_to_tile_x(lng: float, zoom: int) -> int:
    """Convertit longitude en index x de tuile a un zoom donne."""
    return int((lng + 180.0) / 360.0 * (1 << zoom))


def _lat_to_tile_y(lat: float, zoom: int) -> int:
    """Convertit latitude en index y de tuile a un zoom donne (Web Mercator)."""
    rad = math.radians(max(min(lat, 85.05112878), -85.05112878))
    n = 1 << zoom
    return int((1.0 - math.asinh(math.tan(rad)) / math.pi) / 2.0 * n)


def _tile_bbox(z: int, x: int, y: int) -> tuple[float, float, float, float]:
    """Renvoie (minlng, minlat, maxlng, maxlat) d'une tuile z/x/y."""
    n = 1 << z
    minlng = x / n * 360.0 - 180.0
    maxlng = (x + 1) / n * 360.0 - 180.0
    minlat = math.degrees(math.atan(math.sinh(math.pi * (1 - 2 * (y + 1) / n))))
    maxlat = math.degrees(math.atan(math.sinh(math.pi * (1 - 2 * y / n))))
    return (minlng, minlat, maxlng, maxlat)


def _tiles_covering_bbox(
    bbox: tuple[float, float, float, float], zoom: int,
) -> Iterable[tuple[int, int]]:
    """Itere les (x, y) des tuiles couvrant bbox=(minlng, minlat, maxlng, maxlat)."""
    minlng, minlat, maxlng, maxlat = bbox
    x_min = _lng_to_tile_x(minlng, zoom)
    x_max = _lng_to_tile_x(maxlng, zoom)
    y_min = _lat_to_tile_y(maxlat, zoom)  # y invert Web Mercator
    y_max = _lat_to_tile_y(minlat, zoom)
    for x in range(x_min, x_max + 1):
        for y in range(y_min, y_max + 1):
            yield (x, y)


# ── Extraction schema fields (metadata PMTiles) ───────────────────────────────

def _extract_fields(features: list[dict], sample_size: int = 100) -> dict[str, str]:
    """Extrait {field_name: MVT_type} en scannant les N premiers features."""
    fields: dict[str, str] = {}
    for f in features[:sample_size]:
        props = f.get("properties") or {}
        for k, v in props.items():
            if k in fields:
                continue
            if isinstance(v, bool):
                fields[k] = "Boolean"
            elif isinstance(v, (int, float)):
                fields[k] = "Number"
            else:
                fields[k] = "String"
    return fields


# ── Encoder principal ────────────────────────────────────────────────────────

def geojson_to_pmtiles(
    geojson: dict,
    layer_name: str = "features",
    min_zoom: int = 6,
    max_zoom: int = 16,
) -> tuple[bytes, dict]:
    """Convertit FeatureCollection GeoJSON -> bytes fichier PMTiles v3.

    Args:
        geojson: FeatureCollection standard {"type": "FeatureCollection",
                 "features": [...]}. Les geometries doivent etre en WGS84
                 (EPSG:4326). Reprojection amont responsabilite du caller
                 (cf. hub.geo_utils._apply_auto_reprojection).
        layer_name: nom du source-layer MVT (utilise par MapLibre pour
                    referencer la couche via `source-layer: {layer_name}`).
                    Recommandation : slug alphanumerique du layer.id.
        min_zoom, max_zoom: plage de zoom des tuiles generees.
                            6-16 = France entiere -> quartier detaille
                            (defaut adapte a CEREMA / observatoire).

    Returns:
        Tuple (pmtiles_bytes, metadata) avec metadata = {
            "n_features": int,      # nombre features source
            "bbox": [minlng, minlat, maxlng, maxlat],  # bbox globale WGS84
            "n_tiles": int,          # nombre tuiles generees
            "size_bytes": int,       # taille fichier .pmtiles
            "min_zoom": int, "max_zoom": int,
        }

    Raises:
        ValueError si geojson vide ou sans geometrie valide.
        ImportError si pmtiles/mapbox-vector-tile/shapely non installes.
    """
    from pmtiles.writer import Writer
    from pmtiles.tile import zxy_to_tileid, TileType, Compression
    import mapbox_vector_tile
    from shapely.geometry import shape, mapping, box

    features = geojson.get("features") or []
    if not features:
        raise ValueError("GeoJSON vide (aucun feature)")

    # 1. Calcul bbox globale via shapely
    minlng, minlat = float("inf"), float("inf")
    maxlng, maxlat = float("-inf"), float("-inf")
    features_shapely: list[tuple[dict, Any]] = []
    for f in features:
        try:
            geom = shape(f["geometry"])
            if geom.is_empty:
                continue
            bx = geom.bounds
            minlng = min(minlng, bx[0])
            minlat = min(minlat, bx[1])
            maxlng = max(maxlng, bx[2])
            maxlat = max(maxlat, bx[3])
            features_shapely.append((f, geom))
        except Exception as exc:
            log.debug("skip feature invalid : %s", exc)
            continue

    if minlng == float("inf"):
        raise ValueError("Aucune geometrie valide dans le GeoJSON")

    global_bbox = (minlng, minlat, maxlng, maxlat)
    log.debug(
        "pmtiles encode : %d features, bbox=%s, zoom=%d-%d",
        len(features_shapely), global_bbox, min_zoom, max_zoom,
    )

    # 2. Pour chaque zoom : calculer les tuiles, clipper features, encoder MVT
    buf = BytesIO()
    writer = Writer(buf)
    n_tiles = 0

    for z in range(min_zoom, max_zoom + 1):
        for (x, y) in _tiles_covering_bbox(global_bbox, z):
            tile_bbox_ll = _tile_bbox(z, x, y)
            tile_shp = box(*tile_bbox_ll)
            tile_features: list[dict] = []
            for f, geom in features_shapely:
                try:
                    if not geom.intersects(tile_shp):
                        continue
                    clipped = geom.intersection(tile_shp)
                    if clipped.is_empty:
                        continue
                    tile_features.append({
                        "geometry": mapping(clipped),
                        "properties": f.get("properties", {}),
                    })
                except Exception:
                    continue
            if not tile_features:
                continue
            try:
                mvt_layer = {
                    "name": layer_name,
                    "features": tile_features,
                }
                mvt_bytes = mapbox_vector_tile.encode(
                    [mvt_layer],
                    quantize_bounds=tile_bbox_ll,
                )
            except Exception as exc:
                log.debug("skip tile z=%d x=%d y=%d encode error: %s", z, x, y, exc)
                continue
            tile_id = zxy_to_tileid(z, x, y)
            writer.write_tile(tile_id, mvt_bytes)
            n_tiles += 1

    # 3. Header PMTiles v3 + finalize
    center_lng = (minlng + maxlng) / 2
    center_lat = (minlat + maxlat) / 2
    center_zoom = min(max((min_zoom + max_zoom) // 2, min_zoom), max_zoom)
    header = {
        "tile_type": TileType.MVT,
        "tile_compression": Compression.GZIP,
        "min_zoom": min_zoom,
        "max_zoom": max_zoom,
        "min_lon_e7": int(minlng * 1e7),
        "min_lat_e7": int(minlat * 1e7),
        "max_lon_e7": int(maxlng * 1e7),
        "max_lat_e7": int(maxlat * 1e7),
        "center_zoom": center_zoom,
        "center_lon_e7": int(center_lng * 1e7),
        "center_lat_e7": int(center_lat * 1e7),
    }
    metadata_dict = {
        "name": layer_name,
        "vector_layers": [
            {
                "id": layer_name,
                "fields": _extract_fields(features),
                "minzoom": min_zoom,
                "maxzoom": max_zoom,
            },
        ],
    }
    # Sans cela, deux encodages identiques donnent deux fichiers, donc
    # deux adresses publiees pour une donnee inchangee.
    with _gzip_sans_horodatage():
        writer.finalize(header, metadata_dict)

    pmtiles_bytes = buf.getvalue()
    return pmtiles_bytes, {
        "n_features": len(features_shapely),
        "bbox": [minlng, minlat, maxlng, maxlat],
        "n_tiles": n_tiles,
        "size_bytes": len(pmtiles_bytes),
        "min_zoom": min_zoom,
        "max_zoom": max_zoom,
    }
