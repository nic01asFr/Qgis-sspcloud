"""
Tests Vague E2 Commit 7 (D-QGIS-009 §7) — Catalogue fonds de carte.

6 fonds : osm, plan-ign-v2, ortho-ign, dsfr-sobre, hillshade-ign, etalab.
"""
from __future__ import annotations


EXPECTED_BASEMAPS = {"osm", "plan-ign-v2", "ortho-ign", "dsfr-sobre",
                     "hillshade-ign", "etalab"}


class TestBasemapsCatalog:
    """Catalogue de fonds disponibles."""

    def test_six_basemaps_present(self):
        from hub.carto_basemaps import BASEMAPS
        assert set(BASEMAPS.keys()) == EXPECTED_BASEMAPS

    def test_each_basemap_has_required_fields(self):
        from hub.carto_basemaps import BASEMAPS
        required = {"name", "description", "attribution", "style"}
        for bid, bm in BASEMAPS.items():
            assert required.issubset(set(bm.keys())), f"Basemap '{bid}' missing keys"

    def test_each_basemap_has_valid_maplibre_style(self):
        """Style JSON MapLibre version 8 + sources + layers."""
        from hub.carto_basemaps import BASEMAPS
        for bid, bm in BASEMAPS.items():
            style = bm["style"]
            assert style["version"] == 8, f"Basemap {bid} version invalid"
            assert "sources" in style
            assert "layers" in style
            assert len(style["sources"]) >= 1
            assert len(style["layers"]) >= 1


class TestGetBasemapStyle:
    """Helper get_basemap_style."""

    def test_get_osm_style(self):
        from hub.carto_basemaps import get_basemap_style
        style = get_basemap_style("osm")
        assert style["version"] == 8
        assert "osm" in style["sources"]

    def test_get_plan_ign(self):
        from hub.carto_basemaps import get_basemap_style
        style = get_basemap_style("plan-ign-v2")
        assert "plan-ign-v2" in style["sources"]
        # IGN tiles
        tiles = style["sources"]["plan-ign-v2"]["tiles"][0]
        assert "geopf.fr" in tiles

    def test_get_unknown_fallback_osm(self):
        from hub.carto_basemaps import get_basemap_style
        style = get_basemap_style("inexistant")
        assert "osm" in style["sources"]


class TestGetBasemapMetadata:
    """Helper get_basemap_metadata."""

    def test_metadata_osm(self):
        from hub.carto_basemaps import get_basemap_metadata
        meta = get_basemap_metadata("osm")
        assert meta["id"] == "osm"
        assert "OpenStreetMap" in meta["name"]
        assert "OpenStreetMap" in meta["attribution"]

    def test_metadata_unknown_fallback(self):
        from hub.carto_basemaps import get_basemap_metadata
        meta = get_basemap_metadata("zzz")
        assert meta["id"] == "osm"  # fallback


class TestListBasemaps:
    """list_basemaps light catalog."""

    def test_list_six(self):
        from hub.carto_basemaps import list_basemaps
        bms = list_basemaps()
        assert len(bms) == 6
        ids = {bm["id"] for bm in bms}
        assert ids == EXPECTED_BASEMAPS

    def test_each_has_id_name_description_attribution(self):
        from hub.carto_basemaps import list_basemaps
        for bm in list_basemaps():
            assert "id" in bm
            assert "name" in bm
            assert "description" in bm
            assert "attribution" in bm


class TestBasemapPartialIntegration:
    """Le partial _interactive_map_partial.j2 utilise basemap_style_json."""

    def _render(self, basemap_style_json: str = '') -> str:
        from pathlib import Path
        import jinja2
        env = jinja2.Environment(
            loader=jinja2.FileSystemLoader(
                str(Path(__file__).parent.parent / "hub" / "maplibre_renderer")
            )
        )
        tpl = env.get_template("_interactive_map_partial.j2")
        return tpl.render(
            cid="abc12345def6", title="Test", bbox_text="", center_lng=5.39,
            center_lat=43.30, zoom=13, map_layers_json="[]",
            legend_items=None, source_text="", caveat=None,
            basemap_style_json=basemap_style_json,
            basemap_name="OSM", basemap_attribution="© OSM",
        )

    def test_uses_basemap_style_json(self):
        html = self._render('{"version":8,"sources":{"plan-ign":{"type":"raster"}},"layers":[]}')
        # Le style JSON est inline dans le JS MapLibre
        assert "plan-ign" in html
        assert "basemapStyle" in html

    def test_fallback_osm_when_no_basemap(self):
        html = self._render('')
        # Sans basemap_style_json, le code JS fallback OSM hardcoded
        assert "tile.openstreetmap.org" in html
