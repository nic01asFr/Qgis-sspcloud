"""
Tests Vague E2 Commit 6 (D-QGIS-009 §6) — Interactions cartographiques.

Verifie que le template _interactive_map_partial.j2 contient bien le JS
pour :
- Hover tooltip (mousemove + mouseleave + maplibregl.Popup closeButton:false)
- Click popup avec template ou fallback liste props
- Layer toggle UI panel (si > 1 layer)
"""
from __future__ import annotations


class TestMapInteractionsTemplate:
    """Le partial _interactive_map_partial.j2 doit contenir le JS interactions."""

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

    def test_hover_tooltip_present(self):
        html = self._render()
        assert "mousemove" in html
        assert "mouseleave" in html
        assert "hover_attributes" in html

    def test_click_popup_present(self):
        html = self._render()
        assert "map.on('click'" in html
        assert "popup.setLngLat" in html

    def test_popup_template_supported(self):
        html = self._render()
        # Template placeholders {prop} parseable
        assert "popup_template" in html
        assert "popupTpl" in html

    def test_popup_fallback_lists_props(self):
        html = self._render()
        # Fallback : si pas de template, liste les props
        assert "Object.entries(props)" in html

    def test_layer_toggle_ui(self):
        html = self._render()
        # Panel UI toggle layers
        assert "layerToggleControl" in html
        assert "setLayoutProperty" in html
        assert "visibility" in html

    def test_layer_toggle_checkbox_style(self):
        html = self._render()
        # Checkbox + couleur swatch + label
        assert "type=\"checkbox\"" in html
        assert "Couches" in html  # label panel

    def test_tooltip_closeButton_false(self):
        html = self._render()
        # Tooltip survol ne ferme pas au click hors
        assert "closeButton: false" in html
        assert "closeOnClick: false" in html

    def test_popup_closeButton_true(self):
        html = self._render()
        # Popup click a un close button
        assert "closeButton: true" in html
