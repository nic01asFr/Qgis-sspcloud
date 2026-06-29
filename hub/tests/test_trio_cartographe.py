"""
Tests Vague E2 Commit 4 (D-QGIS-009 §4) — trio cartographe metier
obligatoire pour interactive_map.

Convention CEREMA : une carte exploitable en COPIL a TOUJOURS :
- Titre
- Légende auto-dérivée des layers
- Source datée (depuis params.source OU catalog datasources OU fallback)
- Caveat méthodologique (optionnel mais affiché si fourni)

Tests :
- Template partial _interactive_map_partial.j2 rend le trio
- Sans source/legend/caveat : hint d'incomplétude affiché
- Catalog datasources auto-fill source depuis data_url
- Legend auto-dérivée des layers du scene_manifest
"""
from __future__ import annotations


class TestInteractiveMapTrioCartographe:
    """Template _interactive_map_partial rend les 4 zones du trio."""

    def _render(self, ctx: dict) -> str:
        from pathlib import Path
        import jinja2
        env = jinja2.Environment(
            loader=jinja2.FileSystemLoader(
                str(Path(__file__).parent.parent / "hub" / "maplibre_renderer")
            )
        )
        tpl = env.get_template("_interactive_map_partial.j2")
        # Defaults
        defaults = {
            "cid": "abc123def456",
            "title": "Test carte",
            "bbox_text": " — 1 couche · 10 objets",
            "center_lng": 5.39,
            "center_lat": 43.30,
            "zoom": 13,
            "map_layers_json": "[]",
            "legend_items": None,
            "source_text": "",
            "caveat": None,
        }
        defaults.update(ctx)
        return tpl.render(**defaults)

    def test_title_always_rendered(self):
        html = self._render({"title": "Carte risque inondation"})
        assert "Carte risque inondation" in html

    def test_legend_block_rendered_when_items(self):
        html = self._render({
            "legend_items": [
                {"label": "Bâtiments exposés", "color": "#000091", "count": 14270},
                {"label": "TRI 100ans", "color": "#e1000f"},
            ]
        })
        assert "Légende" in html
        assert "Bâtiments exposés" in html
        assert "TRI 100ans" in html
        assert "#000091" in html
        assert "#e1000f" in html

    def test_legend_count_xss_escape(self):
        """Le label utilisateur passe par escape Jinja, pas le <script> MapLibre."""
        html = self._render({
            "legend_items": [
                {"label": "<script>alert(1)</script>", "color": "#000091"},
            ]
        })
        # Le label malveillant doit etre escape, pas insere brut
        assert "<script>alert(1)" not in html
        assert "&lt;script&gt;alert(1)" in html or "&lt;script&gt;" in html

    def test_source_rendered_when_provided(self):
        html = self._render({
            "source_text": "BD TOPO IGN 2024 — Licence Ouverte 2.0"
        })
        assert "Source" in html
        assert "BD TOPO IGN 2024" in html
        assert "Licence Ouverte 2.0" in html

    def test_source_xss_escape(self):
        html = self._render({"source_text": "<img src=x onerror=evil>"})
        assert "<img" not in html
        assert "&lt;img" in html

    def test_caveat_rendered_when_provided(self):
        html = self._render({
            "caveat": "Données pédagogiques, ne pas extrapoler sans validation"
        })
        assert "Caveat" in html
        assert "Données pédagogiques" in html
        # Caveat doit ressortir visuellement (background warning)
        assert "#fff8e6" in html or "#b34000" in html

    def test_hint_when_trio_completely_absent(self):
        """Sans aucun élément du trio, hint d'incomplétude affiché."""
        html = self._render({
            "source_text": "",
            "caveat": None,
            "legend_items": None,
        })
        # Hint visible pour aider l'utilisateur a completer
        assert "légende" in html.lower() or "source" in html.lower() or "caveat" in html.lower()
        assert "COPIL" in html or "métadonnées" in html

    def test_full_trio_rendered(self):
        """Cas nominal : titre + legend + source + caveat tous presents."""
        html = self._render({
            "title": "Risque inondation 4e arr",
            "legend_items": [{"label": "TRI", "color": "#e1000f"}],
            "source_text": "BD TOPO IGN 2024",
            "caveat": "Données 2024, ne pas extrapoler",
        })
        assert "Risque inondation 4e arr" in html
        assert "Légende" in html
        assert "BD TOPO IGN 2024" in html
        assert "Données 2024" in html
        # Pas de hint d'incomplétude
        assert "métadonnées pour usage COPIL" not in html
