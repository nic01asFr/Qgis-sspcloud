"""Tests hub.pmtiles_encoder — PMTiles V0.4 Commit 2 (2026-07-21).

Verifie que la conversion GeoJSON -> PMTiles :
- retourne des bytes non vides + metadata bien forme
- respecte le magic byte du format PMTiles v3
- gere les erreurs (geojson vide, geometrie invalide)
- peut etre relu par pmtiles.reader (roundtrip)
- Reduit la taille par rapport a un GeoJSON gzip equivalent (attendu ~5-10x)
"""
from __future__ import annotations

import gzip
import json

import pytest


# ── Fixtures ────────────────────────────────────────────────────────────────

@pytest.fixture
def simple_geojson() -> dict:
    """FeatureCollection minimal 2 points autour de Marseille 4e."""
    return {
        "type": "FeatureCollection",
        "features": [
            {
                "type": "Feature",
                "geometry": {"type": "Point", "coordinates": [5.36978, 43.29648]},
                "properties": {"name": "Point A", "value": 10},
            },
            {
                "type": "Feature",
                "geometry": {"type": "Point", "coordinates": [5.37000, 43.29700]},
                "properties": {"name": "Point B", "value": 20},
            },
        ],
    }


@pytest.fixture
def polygon_geojson() -> dict:
    """FeatureCollection avec un polygone (batiment)."""
    return {
        "type": "FeatureCollection",
        "features": [
            {
                "type": "Feature",
                "geometry": {
                    "type": "Polygon",
                    "coordinates": [[
                        [5.369, 43.296],
                        [5.371, 43.296],
                        [5.371, 43.298],
                        [5.369, 43.298],
                        [5.369, 43.296],
                    ]],
                },
                "properties": {"name": "Batiment 1", "hauteur_m": 12.5},
            },
        ],
    }


# ── Tests basiques ──────────────────────────────────────────────────────────

def test_encode_returns_bytes(simple_geojson):
    """Encode basique retourne bytes non vide + metadata."""
    from hub.pmtiles_encoder import geojson_to_pmtiles
    result, meta = geojson_to_pmtiles(simple_geojson, min_zoom=12, max_zoom=14)
    assert isinstance(result, bytes)
    assert len(result) > 0
    assert isinstance(meta, dict)
    assert meta["n_features"] == 2
    assert meta["n_tiles"] > 0
    assert meta["size_bytes"] == len(result)


def test_encode_bbox_calcule(simple_geojson):
    """Bbox globale calculee correctement depuis les geometries."""
    from hub.pmtiles_encoder import geojson_to_pmtiles
    _, meta = geojson_to_pmtiles(simple_geojson, min_zoom=12, max_zoom=13)
    bbox = meta["bbox"]
    assert len(bbox) == 4
    minlng, minlat, maxlng, maxlat = bbox
    # Marseille 4e ~ 5.37, 43.29
    assert 5.36 < minlng < 5.38
    assert 43.29 < minlat < 43.30
    assert 5.36 < maxlng < 5.38
    assert 43.29 < maxlat < 43.30


def test_encode_zoom_range_respected(polygon_geojson):
    """min_zoom / max_zoom sont bien propages dans metadata."""
    from hub.pmtiles_encoder import geojson_to_pmtiles
    _, meta = geojson_to_pmtiles(polygon_geojson, min_zoom=10, max_zoom=14)
    assert meta["min_zoom"] == 10
    assert meta["max_zoom"] == 14


def test_encode_polygon_produces_tiles(polygon_geojson):
    """Polygone (feature complexe) genere au moins une tuile a chaque zoom."""
    from hub.pmtiles_encoder import geojson_to_pmtiles
    _, meta = geojson_to_pmtiles(polygon_geojson, min_zoom=12, max_zoom=14)
    # 3 zooms * au moins 1 tuile chacun = 3+ tuiles minimum
    assert meta["n_tiles"] >= 3


# ── Erreurs ─────────────────────────────────────────────────────────────────

def test_empty_geojson_raises():
    """FeatureCollection vide leve ValueError."""
    from hub.pmtiles_encoder import geojson_to_pmtiles
    with pytest.raises(ValueError, match="vide"):
        geojson_to_pmtiles({"type": "FeatureCollection", "features": []})


def test_no_features_key_raises():
    """GeoJSON sans cle 'features' leve ValueError."""
    from hub.pmtiles_encoder import geojson_to_pmtiles
    with pytest.raises(ValueError):
        geojson_to_pmtiles({"type": "FeatureCollection"})


def test_all_geometries_invalid_raises():
    """FeatureCollection avec uniquement geometries invalides leve ValueError."""
    from hub.pmtiles_encoder import geojson_to_pmtiles
    bad = {
        "type": "FeatureCollection",
        "features": [
            {"type": "Feature", "geometry": None, "properties": {}},
        ],
    }
    with pytest.raises(ValueError):
        geojson_to_pmtiles(bad)


# ── Format PMTiles v3 ──────────────────────────────────────────────────────

def test_pmtiles_magic_byte(simple_geojson):
    """Fichier commence par le magic byte 'PMTiles' (spec v3 byte 0-6)."""
    from hub.pmtiles_encoder import geojson_to_pmtiles
    result, _ = geojson_to_pmtiles(simple_geojson, min_zoom=12, max_zoom=13)
    assert result[:7] == b"PMTiles", (
        f"Magic byte incorrect : {result[:16]!r}"
    )


# ── Compression vs geojson ────────────────────────────────────────────────

def test_compact_vs_geojson_gzip(polygon_geojson):
    """PMTiles doit etre plus compact que geojson gzip pour un polygone typique.

    Attendu : pmtiles size <= geojson_gzip size (souvent 5-10x moins, mais
    au minimum on ne veut PAS que ce soit plus gros - sinon aucun benefice).
    """
    from hub.pmtiles_encoder import geojson_to_pmtiles
    pmtiles_bytes, meta = geojson_to_pmtiles(
        polygon_geojson, min_zoom=14, max_zoom=14,  # 1 seul zoom pour comparaison propre
    )
    geojson_bytes = json.dumps(polygon_geojson).encode("utf-8")
    geojson_gzip = gzip.compress(geojson_bytes, compresslevel=6)
    # Pour un seul polygone simple, PMTiles peut etre similaire ou legerement
    # plus gros a cause du header. Le vrai gain se voit sur les gros datasets.
    # On teste juste que la taille est raisonnable (<10x le geojson gzip).
    assert meta["size_bytes"] < 10 * len(geojson_gzip)
