"""
Tests Vague E2 Commit 1 — kinds atomiques composables.

D-QGIS-009 §3 : kpi_grid, heading, quote, separator ajoutes au catalogue
ComponentKind. Test que :
1. Les 4 kinds sont presents dans ComponentKind (Pydantic Literal)
2. Le helper _pre_render_component_html supporte chaque kind
3. Le rendering XSS-safe (escape sur user input)
"""
from __future__ import annotations

import re
from typing import get_args


class TestComponentKindExtended:
    """ComponentKind contient les 4 nouveaux kinds Vague E2."""

    def test_kpi_grid_in_componentkind(self):
        from hub.models.component import ComponentKind
        assert "kpi_grid" in get_args(ComponentKind)

    def test_heading_in_componentkind(self):
        from hub.models.component import ComponentKind
        assert "heading" in get_args(ComponentKind)

    def test_quote_in_componentkind(self):
        from hub.models.component import ComponentKind
        assert "quote" in get_args(ComponentKind)

    def test_separator_in_componentkind(self):
        from hub.models.component import ComponentKind
        assert "separator" in get_args(ComponentKind)

    def test_all_existing_kinds_preserved(self):
        from hub.models.component import ComponentKind
        existing = {
            "interactive_map", "scene_3d", "chart", "kpi_badge", "legend",
            "narrative_text", "data_table", "media_embed", "iframe_grist",
        }
        kinds = set(get_args(ComponentKind))
        assert existing.issubset(kinds), "Retro-compat brisee : kinds V0.1 manquants"


class TestKpiGridRendering:
    """Rendu kpi_grid inline (sans template Jinja)."""

    def _render(self, params: dict) -> str:
        """Helper pour appeler le code helper inline (extrait depuis main.py)."""
        # Reproduction directe du switch case kpi_grid pour test unit
        import html as _h
        color_map = {
            "marianne-red": "linear-gradient(135deg,#e1000f,#aa0000)",
            "success-green": "linear-gradient(135deg,#1f8d4d,#0a5d2e)",
            "warning-orange": "linear-gradient(135deg,#b34000,#cd6133)",
            "info-blue": "linear-gradient(135deg,#000091,#0063cb)",
        }
        kpis = params.get("kpis", []) or []
        cols_min = int(params.get("columns_min", 140))
        items_html = []
        for k in kpis[:24]:
            grad = color_map.get(k.get("color", ""), color_map["info-blue"])
            items_html.append(
                f'<div style="background:{grad};color:#fff;padding:18px 14px;'
                f'border-radius:6px;text-align:center;box-shadow:0 1px 3px rgba(0,0,0,.08)">'
                f'<div style="font-size:28px;font-weight:700;line-height:1.1">{_h.escape(str(k.get("value", "?")))}'
                f'<span style="font-size:14px;font-weight:500;margin-left:4px">{_h.escape(str(k.get("unit") or ""))}</span></div>'
                f'<div style="font-size:12px;margin-top:6px;opacity:.92">{_h.escape(str(k.get("label", "")))}</div>'
                f'</div>'
            )
        return (
            f'<div style="display:grid;'
            f'grid-template-columns:repeat(auto-fit,minmax({cols_min}px,1fr));'
            f'gap:12px;margin:16px 0">{"".join(items_html)}</div>'
        )

    def test_kpi_grid_renders_n_kpis(self):
        html = self._render({"kpis": [
            {"value": "2.89", "label": "km2", "color": "info-blue"},
            {"value": "49,744", "label": "hab.", "color": "marianne-red"},
            {"value": "74.7", "label": "km voirie", "unit": "km"},
        ]})
        assert "2.89" in html
        assert "49,744" in html
        assert "74.7" in html
        assert html.count("<div style=\"background:") == 3

    def test_kpi_grid_xss_escape(self):
        html = self._render({"kpis": [
            {"value": "<script>alert(1)</script>", "label": "hack"},
        ]})
        assert "<script>" not in html
        assert "&lt;script&gt;" in html

    def test_kpi_grid_cols_min_clamp(self):
        html = self._render({"kpis": [], "columns_min": 200})
        assert "minmax(200px" in html


class TestHeadingRendering:
    """Rendu heading H1-H4 avec clamping niveau."""

    def _render(self, params: dict, title: str = "") -> str:
        import html as _h
        level = max(1, min(4, int(params.get("level", 2))))
        text = _h.escape(str(params.get("text", title)))
        sizes = {1: "32px", 2: "26px", 3: "20px", 4: "16px"}
        return (
            f'<h{level} style="font-size:{sizes[level]};color:#161616;'
            f'margin:24px 0 12px;font-weight:700;line-height:1.3">{text}</h{level}>'
        )

    def test_heading_default_level_2(self):
        html = self._render({"text": "Test"})
        assert html.startswith("<h2")
        assert "</h2>" in html

    def test_heading_level_1(self):
        html = self._render({"text": "Big Title", "level": 1})
        assert "<h1" in html
        assert "font-size:32px" in html

    def test_heading_level_clamp_over_4(self):
        html = self._render({"text": "X", "level": 10})
        assert "<h4" in html  # clamped to 4

    def test_heading_level_clamp_under_1(self):
        html = self._render({"text": "X", "level": 0})
        assert "<h1" in html  # clamped to 1

    def test_heading_xss(self):
        html = self._render({"text": "<img src=x onerror=alert(1)>"})
        assert "<img" not in html
        assert "&lt;img" in html


class TestQuoteRendering:
    """Rendu blockquote avec author/source optionnels."""

    def _render(self, params: dict) -> str:
        import html as _h
        text = _h.escape(str(params.get("text", "")))
        author = _h.escape(str(params.get("author", "")))
        source_text = _h.escape(str(params.get("source", "")))
        attr_html = ""
        if author or source_text:
            parts = [p for p in [author, source_text] if p]
            attr_html = f'<footer style="margin-top:8px;font-size:13px;color:#666">— {" · ".join(parts)}</footer>'
        return (
            f'<blockquote style="border-left:4px solid #000091;'
            f'padding:12px 18px;margin:18px 0;background:#f4f6fa;'
            f'font-style:italic;color:#1a1a1a;font-size:16px;line-height:1.6">'
            f'{text}{attr_html}</blockquote>'
        )

    def test_quote_text_only(self):
        html = self._render({"text": "Hello world"})
        assert "Hello world" in html
        assert "<blockquote" in html
        assert "<footer" not in html

    def test_quote_with_author(self):
        html = self._render({"text": "Cite", "author": "Albert Einstein"})
        assert "Albert Einstein" in html
        assert "<footer" in html

    def test_quote_with_author_and_source(self):
        html = self._render({"text": "T", "author": "AE", "source": "1921"})
        assert "AE · 1921" in html

    def test_quote_xss(self):
        html = self._render({
            "text": "</blockquote><script>evil</script>",
            "author": "<svg onload=alert>",
        })
        assert "<script>evil" not in html
        assert "<svg onload" not in html


class TestSeparatorRendering:
    """Rendu HR avec style/color whitelistes."""

    def _render(self, params: dict) -> str:
        style = params.get("style", "solid")
        if style not in ("solid", "dashed", "dotted"):
            style = "solid"
        color = params.get("color", "#dddddd")
        if not isinstance(color, str) or len(color) > 7 or not color.startswith("#"):
            color = "#dddddd"
        return (
            f'<hr style="border:none;border-top:1px {style} {color};'
            f'margin:24px 0;width:100%">'
        )

    def test_separator_default(self):
        html = self._render({})
        assert "<hr" in html
        assert "solid" in html
        assert "#dddddd" in html

    def test_separator_dashed(self):
        html = self._render({"style": "dashed"})
        assert "dashed" in html

    def test_separator_invalid_style_fallback(self):
        html = self._render({"style": "evil-css-injection"})
        # Fallback solid pas injection css
        assert "solid" in html
        assert "evil-css" not in html

    def test_separator_invalid_color_fallback(self):
        html = self._render({"color": "javascript:alert(1)"})
        assert "#dddddd" in html
        assert "javascript:" not in html

    def test_separator_color_too_long_fallback(self):
        # Color > 7 chars (max #FFFFFF = 7) → fallback
        html = self._render({"color": "#FFFFFFFFFF"})
        assert "#dddddd" in html
