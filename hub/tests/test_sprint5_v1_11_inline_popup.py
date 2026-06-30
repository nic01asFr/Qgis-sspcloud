"""
Tests v1.11.0 - 3 modes d'edition selon type de contenu (inspire Docs).

Phase A : 3 blocks texte simple (customHeading/customQuote/narrativeText)
          passent en content:'inline' editable nativement BlockNote.
Phase B : 3 blocks structures simples (kpiBadge/legend/separator) passent
          du drawer au popup floating compact (vs drawer 420px).
Phase D : Titre Assembly = textbox dedie en haut de l'editeur (vs
          customHeading H1 du 1er block).
Phase E : Hover hints differencies (caret pour inline, tooltip "Cliquer
          pour modifier" pour popup/drawer).
"""
from __future__ import annotations

from pathlib import Path
import pytest

REPO_ROOT = Path(__file__).parent.parent.parent
BLOCKNOTE_SRC = REPO_ROOT / "blocknote-editor" / "src"


def _read(rel: str) -> str | None:
    p = BLOCKNOTE_SRC / rel
    if not p.exists():
        return None
    return p.read_text(encoding="utf-8")


class TestPhaseA_InlineEditing:
    """3 blocks texte passent en content:'inline' editable."""

    @pytest.mark.parametrize("block_file", [
        "blocks/CustomHeading.tsx",
        "blocks/CustomQuote.tsx",
        "blocks/NarrativeText.tsx",
    ])
    def test_block_uses_inline_content(self, block_file):
        content = _read(block_file)
        if content is None:
            pytest.skip()
        # content: 'inline' (vs 'none' v1.10)
        assert "content: 'inline'" in content
        # contentRef doit etre utilise pour expose le slot editable
        assert "contentRef" in content

    def test_custom_heading_keeps_level_prop(self):
        """customHeading garde props.level pour H1-H4."""
        content = _read("blocks/CustomHeading.tsx")
        if content is None:
            pytest.skip()
        # propSchema garde level + cid (mais retire text qui vient maintenant de content)
        assert "level: { default: 2 }" in content
        assert "cid: { default: '' }" in content

    def test_custom_quote_keeps_author_source_props(self):
        """customQuote garde props.author + props.source pour attribution."""
        content = _read("blocks/CustomQuote.tsx")
        if content is None:
            pytest.skip()
        assert "author: { default: '' }" in content
        assert "source: { default: '' }" in content

    def test_serializer_pushes_text_to_inline_content(self):
        """Forward Assembly -> BlockNote pousse params.text dans content (vs props)."""
        content = _read("serializer.ts")
        if content is None:
            pytest.skip()
        # Variable inlineContent utilisee pour transporter le text
        assert "inlineContent" in content
        # Pour heading/quote/narrative_text, inlineContent recoit params.text
        assert "inlineContent = params.text" in content

    def test_autosave_reads_text_from_block_content(self):
        """Backward BlockNote -> Assembly lit block.content pour 3 kinds inline."""
        content = _read("autosave.ts")
        if content is None:
            pytest.skip()
        # inlineText extrait de block.content via blockTextContent
        assert "inlineText" in content
        assert "blockTextContent(block.content)" in content


class TestPhaseB_PopupCompact:
    """3 blocks structures compactes passent du drawer au popup."""

    def test_edit_panel_supports_two_modes(self):
        content = _read("EditPanel.tsx")
        if content is None:
            pytest.skip()
        # Type PanelMode + helper getPanelMode
        assert "PanelMode" in content
        assert "'drawer'" in content
        assert "'popup'" in content
        # Sets POPUP_KINDS et DRAWER_KINDS
        assert "POPUP_KINDS" in content
        assert "DRAWER_KINDS" in content

    def test_popup_kinds_are_compact_structures(self):
        """Les 3 blocks compactes sont marques 'popup', kpi_grid reste 'drawer'."""
        content = _read("EditPanel.tsx")
        if content is None:
            pytest.skip()
        # POPUP_KINDS contient les 3 compactes
        # (ordre indifferent, on cherche presence)
        assert "kpiBadge" in content
        assert "legend" in content
        assert "separator" in content
        # kpiGrid dans DRAWER_KINDS
        assert "DRAWER_KINDS = new Set(['kpiGrid'])" in content

    def test_edit_panel_positions_popup_near_block(self):
        """Mode popup positionne pres du block via anchorRect."""
        content = _read("EditPanel.tsx")
        if content is None:
            pytest.skip()
        assert "anchorRect" in content
        # Calcul de position au-dessus ou en-dessous selon espace
        assert "translateY(-100%)" in content or "anchor.bottom" in content

    def test_edit_handler_captures_block_rect(self):
        """openEditPanel capture le rect du block au click."""
        content = _read("blocks/edit-handler.ts")
        if content is None:
            pytest.skip()
        assert "getBoundingClientRect" in content
        assert "anchorRect" in content


class TestPhaseD_AssemblyTitle:
    """Titre Assembly = textbox dedie en haut (vs customHeading H1)."""

    def test_app_has_assembly_title_component(self):
        content = _read("App.tsx")
        if content is None:
            pytest.skip()
        assert "function AssemblyTitle" in content
        # Champ input editable
        assert 'type="text"' in content
        # Autosave avec debounce 30s
        assert "30_000" in content or "30000" in content
        # OCC avec versionNumSource
        assert "versionNumSource" in content

    def test_assembly_title_uses_updateAssembly(self):
        """Le titre PUT update_assembly avec OCC."""
        content = _read("App.tsx")
        if content is None:
            pytest.skip()
        assert "updateAssembly(sid, aid, newManifest, versionNumSource)" in content


class TestPhaseE_HoverHints:
    """CSS differencie INLINE (cursor text) vs POPUP/DRAWER (cursor pointer + tooltip)."""

    def test_inline_blocks_have_text_cursor(self):
        css = _read("editor-layout.css")
        if css is None:
            pytest.skip()
        # Les 3 inline blocks ont cursor: text
        # On verifie via presence du selector groupant les 3 avec cursor text
        assert 'data-content-type="customHeading"' in css
        # Cursor text quelque part dans le CSS pour ces blocks
        assert "cursor: text" in css

    def test_popup_drawer_blocks_have_pointer_and_tooltip(self):
        css = _read("editor-layout.css")
        if css is None:
            pytest.skip()
        assert 'data-content-type="kpiGrid"' in css
        assert 'data-content-type="kpiBadge"' in css
        assert 'data-content-type="legend"' in css
        assert 'data-content-type="separator"' in css
        assert "Cliquer pour modifier" in css


class TestSprint5_Coherence:
    """Coherence globale v1.11."""

    def test_package_version_bumped(self):
        pkg = REPO_ROOT / "blocknote-editor" / "package.json"
        if not pkg.exists():
            pytest.skip()
        assert '"version": "1.11.0"' in pkg.read_text(encoding="utf-8")

    def test_no_regression_imports(self):
        from hub.main import update_component_endpoint, _pre_render_component_html
        from agent.native_tools_v2 import update_component
        assert all([update_component_endpoint, _pre_render_component_html, update_component])

    def test_all_13_kinds_still_in_helper(self):
        """Le helper hub doit encore couvrir les 13 ComponentKind."""
        from hub.models.component import ComponentKind
        from typing import get_args
        kinds = get_args(ComponentKind)
        assert len(kinds) == 13
