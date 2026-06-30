"""
Tests Sprint 4 Vague E3 (v1.10.0) - item 8.18 Edit Panel option A.

Couvre l'option A : drawer latéral pour éditer les params des 7 DOM
custom blocks (kpi_grid, kpi_badge, heading, quote, legend, narrative_text,
separator). Résout le gap UX P0 découvert lors du test user E2E v1.9.0.

Pattern : introspection source TS depuis pytest (aligné sprint 1-3).
"""
from __future__ import annotations

import pytest
from pathlib import Path

REPO_ROOT = Path(__file__).parent.parent.parent
BLOCKNOTE_SRC = REPO_ROOT / "blocknote-editor" / "src"


def _read_blocknote_file(rel_path: str) -> str | None:
    p = BLOCKNOTE_SRC / rel_path
    if not p.exists():
        return None
    return p.read_text(encoding="utf-8")


class TestEditPanelDrawer:
    """EditPanel.tsx : drawer principal."""

    def test_edit_panel_file_exists(self):
        content = _read_blocknote_file("EditPanel.tsx")
        if content is None:
            pytest.skip("blocknote-editor absent")
        # Exports
        assert "export interface EditableBlock" in content
        assert "export function EditPanel" in content

    def test_edit_panel_handles_save_with_occ(self):
        content = _read_blocknote_file("EditPanel.tsx")
        if content is None:
            pytest.skip("blocknote-editor absent")
        # Appelle updateComponent + gere les 3 outcomes
        assert "updateComponent" in content
        assert "result.conflict" in content
        assert "result.newVersionNum" in content
        assert "versionNumSource" in content

    def test_edit_panel_iframe_kinds_readonly(self):
        """Les 6 kinds iframe doivent afficher un message read-only V1."""
        content = _read_blocknote_file("EditPanel.tsx")
        if content is None:
            pytest.skip("blocknote-editor absent")
        assert "isIframe" in content
        assert "Lecture seule" in content
        # Ces 6 kinds doivent etre dans IFRAME_KIND_LABELS
        for kind in ["interactiveMap", "chart", "dataTable", "scene3d", "mediaEmbed", "iframeGrist"]:
            assert kind in content, f"{kind} absent de IFRAME_KIND_LABELS"

    def test_edit_panel_dom_kinds_editable(self):
        """Les 7 kinds DOM doivent etre dans DOM_KIND_LABELS."""
        content = _read_blocknote_file("EditPanel.tsx")
        if content is None:
            pytest.skip("blocknote-editor absent")
        for kind in ["kpiGrid", "kpiBadge", "customHeading", "customQuote",
                     "legend", "narrativeText", "separator"]:
            assert kind in content, f"{kind} absent de DOM_KIND_LABELS"


class TestForms:
    """7 forms par DOM kind."""

    @pytest.mark.parametrize("form_file", [
        "forms/KpiGridForm.tsx",
        "forms/KpiBadgeForm.tsx",
        "forms/HeadingForm.tsx",
        "forms/QuoteForm.tsx",
        "forms/NarrativeTextForm.tsx",
        "forms/LegendForm.tsx",
        "forms/SeparatorForm.tsx",
    ])
    def test_form_file_exists(self, form_file):
        content = _read_blocknote_file(form_file)
        if content is None:
            pytest.skip("blocknote-editor absent")
        # Pattern Form export
        assert "export function " in content
        # Reçoit data + onChange standardisé
        assert "onChange:" in content
        assert "data:" in content

    def test_kpi_grid_form_has_add_remove(self):
        content = _read_blocknote_file("forms/KpiGridForm.tsx")
        if content is None:
            pytest.skip()
        assert "addKpi" in content
        assert "removeKpi" in content
        assert "moveKpi" in content  # reorder

    def test_legend_form_has_items_management(self):
        content = _read_blocknote_file("forms/LegendForm.tsx")
        if content is None:
            pytest.skip()
        assert "addItem" in content
        assert "removeItem" in content

    def test_fields_module_exports_reusable(self):
        content = _read_blocknote_file("forms/fields.tsx")
        if content is None:
            pytest.skip()
        # Reusable HTML field components
        assert "TextField" in content
        assert "TextareaField" in content
        assert "NumberField" in content
        assert "SelectField" in content
        assert "ColorField" in content
        assert "FieldSection" in content
        # DSFR colors options
        assert "DSFR_COLOR_OPTIONS" in content


class TestBlocksClickHandlers:
    """7 blocks DOM doivent avoir onClick -> openEditPanel."""

    @pytest.mark.parametrize("block_file", [
        "blocks/KpiGrid.tsx",
        "blocks/KpiBadge.tsx",
        "blocks/CustomHeading.tsx",
        "blocks/CustomQuote.tsx",
        "blocks/NarrativeText.tsx",
        "blocks/Legend.tsx",
        "blocks/Separator.tsx",
    ])
    def test_block_imports_openEditPanel(self, block_file):
        content = _read_blocknote_file(block_file)
        if content is None:
            pytest.skip("blocknote-editor absent")
        assert "openEditPanel" in content
        assert "edit-handler" in content
        # onClick avec stopPropagation
        assert "onClick" in content
        assert "stopPropagation" in content

    def test_edit_handler_helper_exists(self):
        content = _read_blocknote_file("blocks/edit-handler.ts")
        if content is None:
            pytest.skip()
        assert "export function openEditPanel" in content
        assert "window.__openEditPanel" in content or "(window as any).__openEditPanel" in content


class TestAppIntegration:
    """App.tsx : drawer + global window.__openEditPanel + state."""

    def test_app_imports_EditPanel(self):
        content = _read_blocknote_file("App.tsx")
        if content is None:
            pytest.skip()
        assert "import { EditPanel" in content
        assert "EditableBlock" in content

    def test_app_registers_global_open_panel(self):
        content = _read_blocknote_file("App.tsx")
        if content is None:
            pytest.skip()
        assert "__openEditPanel" in content
        assert "editingBlock" in content
        assert "setEditingBlock" in content

    def test_app_renders_edit_panel_with_props(self):
        content = _read_blocknote_file("App.tsx")
        if content is None:
            pytest.skip()
        assert "<EditPanel" in content
        assert "onSaved=" in content
        assert "versionNumSource={versionNumSource}" in content


class TestEditorLayoutCss:
    """Hover hint CSS sur les 7 blocks editables."""

    def test_css_has_hover_outline(self):
        css = _read_blocknote_file("editor-layout.css")
        if css is None:
            pytest.skip()
        # Outline bleu Marianne au hover sur les 7 blocks
        assert 'data-content-type="kpiGrid"' in css
        assert "outline-color: #000091" in css
        # Tooltip "Cliquer pour modifier"
        assert "Cliquer pour modifier" in css


class TestSprint4_Coherence:
    """Coherence globale du sprint 4."""

    def test_no_regression_imports(self):
        """Les imports clés restent OK."""
        from hub.main import (
            update_component_endpoint,
            log_client_error_endpoint,
        )
        from agent.native_tools_v2 import update_component
        assert all([update_component_endpoint, log_client_error_endpoint, update_component])

    def test_v110_in_package_json(self):
        pkg = REPO_ROOT / "blocknote-editor" / "package.json"
        if not pkg.exists():
            pytest.skip()
        content = pkg.read_text(encoding="utf-8")
        # Doit etre bump a 1.10.0 pour ce sprint
        assert '"version": "1.10.0"' in content
