"""
Tests Vague E2 Commit 8 (D-QGIS-009 §8) — Proportional symbols + heatmap.

Verifie que le partial _interactive_map_partial.j2 supporte :
- layer.proportional_field -> circle-radius = f(value) interpolate linear
- layer.heatmap_field -> heatmap layer MapLibre avec intensite
"""
from __future__ import annotations


class TestProportionalSymbols:
    """Symboles proportionnels au survol d'un attribut numerique."""

    def _render(self) -> str:
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
        )

    def test_proportional_field_logic_present(self):
        html = self._render()
        # Le code JS gere proportional_field
        assert "proportional_field" in html
        assert "circle-radius" in html
        # Interpolate linear pattern
        assert "interpolate" in html

    def test_proportional_min_max_params(self):
        html = self._render()
        # Params clamp min/max value + radius min/max
        assert "proportional_min" in html
        assert "proportional_max" in html
        assert "proportional_radius_min" in html
        assert "proportional_radius_max" in html


class TestHeatmap:
    """Heatmap layer MapLibre."""

    def _render(self) -> str:
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
        )

    def test_heatmap_field_present(self):
        html = self._render()
        assert "heatmap_field" in html
        # MapLibre type heatmap
        assert "type:'heatmap'" in html or 'type: "heatmap"' in html or "'heatmap'" in html

    def test_heatmap_paint_properties(self):
        html = self._render()
        # heatmap-weight, intensity, color, radius, opacity
        assert "heatmap-weight" in html
        assert "heatmap-intensity" in html
        assert "heatmap-color" in html
        assert "heatmap-radius" in html
        assert "heatmap-opacity" in html

    def test_heatmap_palette_DSFR_inspired(self):
        html = self._render()
        # Palette heatmap : bleu Marianne -> rouge alerte
        assert "rgba(0,0,145" in html  # bleu Marianne start
        assert "rgba(225,0,15" in html  # rouge Marianne fin
