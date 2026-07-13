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


# ---------------------------------------------------------------------------
# _apply_auto_reprojection (helper prive dans hub.main) — V2 + V3 finition G3
# ---------------------------------------------------------------------------

class TestApplyAutoReprojection:
    """Contrat du helper unifie _apply_auto_reprojection.

    Ce helper est le point d'entree unique de la reprojection dans
    _build_interactive_map_ctx (Chemins A + B). Il doit :
      - reprojeter si CRS declare != EPSG:4326 -> retourner (data, True)
      - ne rien faire si deja EPSG:4326 -> retourner (data, False)
      - ne rien faire si pas de crs declare -> retourner (data, False)
      - fail-soft en cas d'erreur pyproj (renvoyer l'input as-is, False)
      - toujours retirer le champ crs top-level (RFC 7946)
    """

    def test_reprojection_lambert93_declaree(self):
        from hub.main import _apply_auto_reprojection
        gj = _mk_fc({"type": "Point", "coordinates": MARSEILLE_L93})
        out, reprojected = _apply_auto_reprojection(
            gj, layer_id="test_layer", source_hint="/tmp/x.geojson"
        )
        assert reprojected is True
        coords = out["features"][0]["geometry"]["coordinates"]
        assert coords[0] == pytest.approx(MARSEILLE_WGS84_LON, abs=TOL)
        assert coords[1] == pytest.approx(MARSEILLE_WGS84_LAT, abs=TOL)
        # Champ crs top-level retire (RFC 7946)
        assert "crs" not in out

    def test_deja_4326_pas_de_reprojection(self):
        from hub.main import _apply_auto_reprojection
        gj = {
            "type": "FeatureCollection",
            "crs": {"type": "name", "properties": {"name": "EPSG:4326"}},
            "features": [{
                "type": "Feature", "properties": {},
                "geometry": {"type": "Point", "coordinates": [5.39, 43.31]},
            }],
        }
        out, reprojected = _apply_auto_reprojection(
            gj, layer_id="x", source_hint="x"
        )
        assert reprojected is False
        # Coordonnees identiques
        assert out["features"][0]["geometry"]["coordinates"] == [5.39, 43.31]
        # crs top-level retire quand meme
        assert "crs" not in out

    def test_pas_de_crs_declare(self):
        from hub.main import _apply_auto_reprojection
        gj = {
            "type": "FeatureCollection",
            "features": [{
                "type": "Feature", "properties": {},
                "geometry": {"type": "Point", "coordinates": [5.39, 43.31]},
            }],
        }
        out, reprojected = _apply_auto_reprojection(
            gj, layer_id="x", source_hint="x"
        )
        assert reprojected is False
        assert out["features"][0]["geometry"]["coordinates"] == [5.39, 43.31]

    def test_input_pas_dict_fail_soft(self):
        from hub.main import _apply_auto_reprojection
        out, reprojected = _apply_auto_reprojection(
            None, layer_id="x", source_hint="x"
        )
        assert reprojected is False
        assert out is None

    def test_crs_invalide_fail_soft(self):
        """CRS malforme (EPSG inexistant) : ne doit pas crasher."""
        from hub.main import _apply_auto_reprojection
        gj = {
            "type": "FeatureCollection",
            "crs": {"type": "name", "properties": {"name": "EPSG:999999"}},
            "features": [{
                "type": "Feature", "properties": {},
                "geometry": {"type": "Point", "coordinates": [1.0, 2.0]},
            }],
        }
        # Ne doit pas raise
        out, reprojected = _apply_auto_reprojection(
            gj, layer_id="x", source_hint="x"
        )
        # Reprojection echouee : reprojected=False, data as-is
        assert reprojected is False
        # crs top-level retire meme si reprojection failed
        assert "crs" not in out


# ---------------------------------------------------------------------------
# V3 : contrat source.crs = "EPSG:4326" apres reprojection reussie
# ---------------------------------------------------------------------------

class TestSourceCrsForcedApresReprojection:
    """Assure que apres reprojection, un consumer qui trusterait
    ``layer.source.crs`` ne reprojetera PAS a nouveau (rendu casse).

    Cette logique vit dans _build_interactive_map_ctx (chemin A). On la
    reproduit ici comme un mini-integration test en simulant la sequence
    utilisee dans main.py :

        gj_data, reprojected = _apply_auto_reprojection(...)
        forced_crs = "EPSG:4326" if reprojected else src.get("crs", "EPSG:4326")
    """

    def test_source_crs_force_a_4326_apres_reprojection(self):
        from hub.main import _apply_auto_reprojection
        gj = _mk_fc({"type": "Point", "coordinates": MARSEILLE_L93})
        # Source declare initialement EPSG:2154 (comme un QGIS export brut)
        src = {"type": "geojson_path", "path": "/x.geojson", "crs": "EPSG:2154"}
        gj_data, reprojected = _apply_auto_reprojection(
            gj, layer_id="bdtopo", source_hint=src["path"]
        )
        assert reprojected is True
        # Simulation de la logique main.py :
        forced_crs = "EPSG:4326" if reprojected else src.get("crs", "EPSG:4326")
        assert forced_crs == "EPSG:4326"
        # Le nouveau source dict n'expose PAS EPSG:2154
        new_source = {"type": "geojson", "data": gj_data, "crs": forced_crs}
        assert new_source["crs"] == "EPSG:4326"

    def test_source_crs_preserve_si_pas_de_reprojection(self):
        """Si le layer est deja 4326, on ne doit rien changer au source.crs
        declare par l'appelant (peut etre EPSG:3857 legit pour ex.)."""
        from hub.main import _apply_auto_reprojection
        gj = {
            "type": "FeatureCollection",
            "features": [{
                "type": "Feature", "properties": {},
                "geometry": {"type": "Point", "coordinates": [5.39, 43.31]},
            }],
        }
        src = {"type": "geojson_path", "path": "/x.geojson", "crs": "EPSG:4326"}
        _, reprojected = _apply_auto_reprojection(
            gj, layer_id="x", source_hint=src["path"]
        )
        assert reprojected is False
        forced_crs = "EPSG:4326" if reprojected else src.get("crs", "EPSG:4326")
        assert forced_crs == "EPSG:4326"
