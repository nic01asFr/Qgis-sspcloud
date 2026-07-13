"""
Tests unitaires hub.geo_utils (chantier G3, 2026-07-13).

Auto-reprojection GeoJSON Lambert 93 -> WGS84 pour eviter le rendu au
large de l'Afrique quand MapLibre recoit des coordonnees en metres.
"""

import pytest

from hub.geo_utils import detect_geojson_crs, reproject_geojson_to_4326


# ---------------------------------------------------------------------------
# detect_geojson_crs
# ---------------------------------------------------------------------------

class TestDetectGeojsonCrs:

    def test_urn_ogc(self):
        gj = {"crs": {"properties": {"name": "urn:ogc:def:crs:EPSG::2154"}}}
        assert detect_geojson_crs(gj) == "EPSG:2154"

    def test_urn_ogc_with_version(self):
        gj = {"crs": {"properties": {"name": "urn:ogc:def:crs:EPSG:6.9:2154"}}}
        assert detect_geojson_crs(gj) == "EPSG:2154"

    def test_epsg_uppercase_nu(self):
        gj = {"crs": {"properties": {"name": "EPSG:4326"}}}
        assert detect_geojson_crs(gj) == "EPSG:4326"

    def test_epsg_lowercase(self):
        gj = {"crs": {"properties": {"name": "epsg:3857"}}}
        assert detect_geojson_crs(gj) == "EPSG:3857"

    def test_opengis_http_url(self):
        gj = {"crs": {"properties": {"name": "http://www.opengis.net/def/crs/EPSG/0/2154"}}}
        assert detect_geojson_crs(gj) == "EPSG:2154"

    def test_absent_crs(self):
        gj = {"type": "FeatureCollection", "features": []}
        assert detect_geojson_crs(gj) is None

    def test_crs_vide(self):
        gj = {"crs": {"properties": {"name": ""}}}
        assert detect_geojson_crs(gj) is None

    def test_crs_non_epsg(self):
        gj = {"crs": {"properties": {"name": "CRS84"}}}
        assert detect_geojson_crs(gj) is None

    def test_input_pas_dict(self):
        assert detect_geojson_crs(None) is None
        assert detect_geojson_crs("string") is None
        assert detect_geojson_crs([]) is None


# ---------------------------------------------------------------------------
# reproject_geojson_to_4326
# ---------------------------------------------------------------------------

# Marseille (Blancarde/4e) : Lambert 93 -> WGS84
# ~894132, 6248762 (L93) doit tomber vers ~5.39, 43.31 (WGS84)
MARSEILLE_L93 = [894132.5, 6248762.6]
MARSEILLE_WGS84_LON = 5.39
MARSEILLE_WGS84_LAT = 43.31
TOL = 0.02  # tolerance ~2 km


def _mk_fc(geometry: dict) -> dict:
    return {
        "type": "FeatureCollection",
        "crs": {"type": "name", "properties": {"name": "urn:ogc:def:crs:EPSG::2154"}},
        "features": [
            {
                "type": "Feature",
                "properties": {"id": 1, "name": "test"},
                "geometry": geometry,
            }
        ],
    }


class TestReprojectGeojson:

    def test_point_lambert_vers_wgs84(self):
        gj = _mk_fc({"type": "Point", "coordinates": MARSEILLE_L93})
        out = reproject_geojson_to_4326(gj, "EPSG:2154")
        coords = out["features"][0]["geometry"]["coordinates"]
        assert coords[0] == pytest.approx(MARSEILLE_WGS84_LON, abs=TOL)
        assert coords[1] == pytest.approx(MARSEILLE_WGS84_LAT, abs=TOL)
        # Champ crs top-level supprime (RFC 7946)
        assert "crs" not in out
        # Properties preservees
        assert out["features"][0]["properties"] == {"id": 1, "name": "test"}
        # Type preserve
        assert out["type"] == "FeatureCollection"
        assert out["features"][0]["geometry"]["type"] == "Point"

    def test_multipolygon_bdtopo_type(self):
        # MultiPolygon avec un polygone simple (1 ring exterieur, 4 points L93
        # autour de Marseille 4e). Verifie la recursion pour structures
        # profondes [[[[[x, y], ...]]]].
        ring = [
            [893500.0, 6248500.0],
            [894500.0, 6248500.0],
            [894500.0, 6249500.0],
            [893500.0, 6249500.0],
            [893500.0, 6248500.0],
        ]
        gj = _mk_fc({"type": "MultiPolygon", "coordinates": [[ring]]})
        out = reproject_geojson_to_4326(gj, "EPSG:2154")
        new_coords = out["features"][0]["geometry"]["coordinates"]
        # Structure preservee : [MultiPolygon -> Polygon -> Ring -> Point]
        assert isinstance(new_coords, list)
        assert isinstance(new_coords[0], list)
        assert isinstance(new_coords[0][0], list)
        assert len(new_coords[0][0]) == 5  # 5 points dans le ring
        # Chaque point doit etre dans la zone Marseille en WGS84
        for pt in new_coords[0][0]:
            assert pt[0] == pytest.approx(MARSEILLE_WGS84_LON, abs=0.05)
            assert pt[1] == pytest.approx(MARSEILLE_WGS84_LAT, abs=0.05)
        assert out["features"][0]["geometry"]["type"] == "MultiPolygon"

    def test_linestring(self):
        gj = _mk_fc({
            "type": "LineString",
            "coordinates": [
                [893500.0, 6248500.0],
                [894500.0, 6249500.0],
            ],
        })
        out = reproject_geojson_to_4326(gj, "EPSG:2154")
        coords = out["features"][0]["geometry"]["coordinates"]
        assert len(coords) == 2
        for pt in coords:
            assert pt[0] == pytest.approx(MARSEILLE_WGS84_LON, abs=0.05)
            assert pt[1] == pytest.approx(MARSEILLE_WGS84_LAT, abs=0.05)

    def test_point_avec_z_preserve(self):
        gj = _mk_fc({"type": "Point", "coordinates": [894132.5, 6248762.6, 42.0]})
        out = reproject_geojson_to_4326(gj, "EPSG:2154")
        coords = out["features"][0]["geometry"]["coordinates"]
        assert len(coords) == 3
        assert coords[2] == 42.0  # z preserve tel quel

    def test_crs_toplevel_retire(self):
        gj = _mk_fc({"type": "Point", "coordinates": MARSEILLE_L93})
        assert "crs" in gj
        out = reproject_geojson_to_4326(gj, "EPSG:2154")
        assert "crs" not in out

    def test_feature_collection_vide(self):
        gj = {"type": "FeatureCollection", "features": []}
        out = reproject_geojson_to_4326(gj, "EPSG:2154")
        assert out["features"] == []

    def test_ne_modifie_pas_input(self):
        # La fonction doit retourner un nouveau dict, pas muter l'entree.
        gj = _mk_fc({"type": "Point", "coordinates": list(MARSEILLE_L93)})
        original_coords = list(gj["features"][0]["geometry"]["coordinates"])
        _ = reproject_geojson_to_4326(gj, "EPSG:2154")
        # Coordonnees d'origine intactes
        assert gj["features"][0]["geometry"]["coordinates"] == original_coords
