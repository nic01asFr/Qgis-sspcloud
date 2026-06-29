"""
Tests Vague E2 Commit G (D-QGIS-010) — Sérialisation BlockNote -> Assembly.

Vérifie le mapping inverse :
- BLOCK_TYPE_TO_COMPONENT_KIND : couverture des 13 kinds
- block_to_component_params : conversion props -> params pour chaque kind
- blocknote_doc_to_assembly_sections : regroupement blocks en sections
"""
from __future__ import annotations


class TestBlockTypeMapping:
    """BLOCK_TYPE_TO_COMPONENT_KIND : 13 mappings inversés."""

    def test_13_mappings_present(self):
        from hub.blocknote_serializer import BLOCK_TYPE_TO_COMPONENT_KIND
        assert len(BLOCK_TYPE_TO_COMPONENT_KIND) == 13

    def test_dom_blocks_mapped(self):
        from hub.blocknote_serializer import BLOCK_TYPE_TO_COMPONENT_KIND
        assert BLOCK_TYPE_TO_COMPONENT_KIND["kpiGrid"] == "kpi_grid"
        assert BLOCK_TYPE_TO_COMPONENT_KIND["customHeading"] == "heading"
        assert BLOCK_TYPE_TO_COMPONENT_KIND["customQuote"] == "quote"
        assert BLOCK_TYPE_TO_COMPONENT_KIND["separator"] == "separator"
        assert BLOCK_TYPE_TO_COMPONENT_KIND["kpiBadge"] == "kpi_badge"
        assert BLOCK_TYPE_TO_COMPONENT_KIND["legend"] == "legend"
        assert BLOCK_TYPE_TO_COMPONENT_KIND["narrativeText"] == "narrative_text"

    def test_iframe_blocks_mapped(self):
        from hub.blocknote_serializer import BLOCK_TYPE_TO_COMPONENT_KIND
        assert BLOCK_TYPE_TO_COMPONENT_KIND["interactiveMap"] == "interactive_map"
        assert BLOCK_TYPE_TO_COMPONENT_KIND["chart"] == "chart"
        assert BLOCK_TYPE_TO_COMPONENT_KIND["dataTable"] == "data_table"
        assert BLOCK_TYPE_TO_COMPONENT_KIND["scene3d"] == "scene_3d"
        assert BLOCK_TYPE_TO_COMPONENT_KIND["mediaEmbed"] == "media_embed"
        assert BLOCK_TYPE_TO_COMPONENT_KIND["iframeGrist"] == "iframe_grist"


class TestBlockToComponentParams:
    """Conversion props block -> params Component pour chaque kind DOM."""

    def test_kpi_grid_params(self):
        from hub.blocknote_serializer import block_to_component_params
        block = {
            "type": "kpiGrid",
            "props": {
                "kpisJson": '[{"value":"42","label":"test"}]',
                "palette": "rainbow",
                "columnsMin": 200,
            },
        }
        result = block_to_component_params(block)
        assert result["kind"] == "kpi_grid"
        assert result["params"]["kpis"] == [{"value": "42", "label": "test"}]
        assert result["params"]["palette"] == "rainbow"
        assert result["params"]["columns_min"] == 200

    def test_heading_params(self):
        from hub.blocknote_serializer import block_to_component_params
        block = {
            "type": "customHeading",
            "props": {"text": "Test heading", "level": 3},
        }
        result = block_to_component_params(block)
        assert result["kind"] == "heading"
        assert result["params"]["text"] == "Test heading"
        assert result["params"]["level"] == 3

    def test_quote_params(self):
        from hub.blocknote_serializer import block_to_component_params
        block = {
            "type": "customQuote",
            "props": {
                "text": "Une citation",
                "author": "CEREMA",
                "source": "Diagnostic 2026",
            },
        }
        result = block_to_component_params(block)
        assert result["kind"] == "quote"
        assert result["params"]["text"] == "Une citation"
        assert result["params"]["author"] == "CEREMA"
        assert result["params"]["source"] == "Diagnostic 2026"

    def test_separator_params(self):
        from hub.blocknote_serializer import block_to_component_params
        block = {
            "type": "separator",
            "props": {"style": "dashed", "color": "#ff0000", "variant": "ornament"},
        }
        result = block_to_component_params(block)
        assert result["kind"] == "separator"
        assert result["params"]["style"] == "dashed"
        assert result["params"]["color"] == "#ff0000"
        assert result["params"]["variant"] == "ornament"

    def test_kpi_badge_params(self):
        from hub.blocknote_serializer import block_to_component_params
        block = {
            "type": "kpiBadge",
            "props": {
                "value": "47",
                "label": "% territoire",
                "unit": "%",
                "color": "marianne-red",
                "source": "TRI",
            },
        }
        result = block_to_component_params(block)
        assert result["kind"] == "kpi_badge"
        assert result["params"]["value"] == "47"
        assert result["params"]["color"] == "marianne-red"

    def test_iframe_kinds_ref_only(self):
        """Iframe kinds référencent un cid existant, pas de nouveau component."""
        from hub.blocknote_serializer import block_to_component_params
        for btype in ["interactiveMap", "chart", "dataTable", "scene3d",
                      "mediaEmbed", "iframeGrist"]:
            block = {"type": btype, "props": {"cid": "abc123def456"}}
            result = block_to_component_params(block)
            assert result is not None, f"{btype} unhandled"
            assert result.get("ref_only") is True
            assert result["cid"] == "abc123def456"

    def test_unknown_block_returns_none(self):
        from hub.blocknote_serializer import block_to_component_params
        result = block_to_component_params({"type": "paragraph", "props": {}})
        assert result is None


class TestBlockNoteDocToAssembly:
    """Conversion document BlockNote -> sections Assembly + new_components."""

    def test_simple_heading_creates_section(self):
        from hub.blocknote_serializer import blocknote_doc_to_assembly_sections
        blocks = [
            {"type": "heading", "props": {"level": 2}, "content": "Ma section"},
            {"type": "paragraph", "content": "Un paragraphe"},
        ]
        sections, new_comps = blocknote_doc_to_assembly_sections(blocks)
        assert len(sections) == 1
        assert sections[0]["title"] == "Ma section"
        assert "Un paragraphe" in sections[0]["narrative_md"]
        assert new_comps == []

    def test_multiple_headings_split_sections(self):
        from hub.blocknote_serializer import blocknote_doc_to_assembly_sections
        blocks = [
            {"type": "heading", "props": {"level": 2}, "content": "Section A"},
            {"type": "paragraph", "content": "Texte A"},
            {"type": "heading", "props": {"level": 2}, "content": "Section B"},
            {"type": "paragraph", "content": "Texte B"},
        ]
        sections, _ = blocknote_doc_to_assembly_sections(blocks)
        assert len(sections) == 2
        assert sections[0]["title"] == "Section A"
        assert sections[1]["title"] == "Section B"

    def test_dom_block_creates_new_component(self):
        """Un custom block DOM crée un nouveau Component à publier."""
        from hub.blocknote_serializer import blocknote_doc_to_assembly_sections
        blocks = [
            {
                "type": "kpiGrid",
                "props": {
                    "kpisJson": '[{"value":"42","label":"Test"}]',
                    "palette": "monochrome",
                },
            },
        ]
        sections, new_comps = blocknote_doc_to_assembly_sections(blocks)
        assert len(new_comps) == 1
        assert new_comps[0]["kind"] == "kpi_grid"
        assert new_comps[0]["params"]["kpis"] == [{"value": "42", "label": "Test"}]
        # La section référence un placeholder __pending__ que le caller remplacera
        # par le cid réel après create_component côté hub
        assert sections[0]["components"][0]["ref"] == "__pending__"

    def test_iframe_block_uses_existing_cid(self):
        """Un custom block iframe référence un cid existant, pas de new_component."""
        from hub.blocknote_serializer import blocknote_doc_to_assembly_sections
        blocks = [
            {
                "type": "interactiveMap",
                "props": {"cid": "b1c2d3e4f5a6"},
            },
        ]
        sections, new_comps = blocknote_doc_to_assembly_sections(blocks)
        assert len(new_comps) == 0
        assert sections[0]["components"][0]["ref"] == "b1c2d3e4f5a6"

    def test_complex_mixed_document(self):
        """Test round-trip-like : H2 + paragraph + custom DOM + iframe."""
        from hub.blocknote_serializer import blocknote_doc_to_assembly_sections
        blocks = [
            {"type": "heading", "props": {"level": 2}, "content": "Diagnostic"},
            {"type": "paragraph", "content": "Contexte initial."},
            {
                "type": "kpiGrid",
                "props": {
                    "kpisJson": '[{"value":"47","label":"%"}]',
                    "palette": "monochrome",
                },
            },
            {
                "type": "interactiveMap",
                "props": {"cid": "abc123def456"},
            },
            {"type": "heading", "props": {"level": 2}, "content": "Conclusion"},
            {
                "type": "customQuote",
                "props": {"text": "Synthèse", "author": "CEREMA"},
            },
        ]
        sections, new_comps = blocknote_doc_to_assembly_sections(blocks)
        # 2 sections (Diagnostic + Conclusion)
        assert len(sections) == 2
        # 2 nouveaux DOM components à créer (kpi_grid + quote)
        assert len(new_comps) == 2
        # 1 ref vers cid existant (interactive_map)
        all_refs = [
            r["ref"]
            for s in sections
            for r in s["components"]
        ]
        assert "abc123def456" in all_refs

    def test_xss_safe_parse_json_list(self):
        """_parse_json_list gracieux sur input malformé."""
        from hub.blocknote_serializer import _parse_json_list
        assert _parse_json_list("invalid json") == []
        assert _parse_json_list("{}") == []  # dict pas list
        assert _parse_json_list(None) == []
        assert _parse_json_list([1, 2, 3]) == [1, 2, 3]
