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
    """Comment chaque bloc se laisse modifier.

    Reecrit le 2026-08-25. Les tests precedents exigeaient `openEditPanel` sur
    tous les blocs et echouaient depuis le passage a l'edition inline
    (« Chantier 1 V1.20.1 ») : ils decrivaient une intention abandonnee, pas un
    defaut. Les rafistoler pour qu'ils passent aurait masque ce changement ; on
    verifie donc la regle reelle, qui est meilleure que l'ancienne.

    La regle : un bloc dont le contenu est du texte se modifie **sur place** ;
    un bloc opaque ou sans contenu passe par le **panneau**. Un iframe capture
    les clics, on ne peut rien y editer directement ; un separateur n'a rien a
    editer du tout.
    """

    EDITION_SUR_PLACE = [
        "blocks/CustomHeading.tsx", "blocks/CustomQuote.tsx",
        "blocks/NarrativeText.tsx", "blocks/KpiBadge.tsx",
        "blocks/KpiGrid.tsx", "blocks/Legend.tsx",
    ]
    EDITION_PAR_PANNEAU = ["blocks/IframeEmbed.tsx", "blocks/Separator.tsx"]

    @pytest.mark.parametrize("block_file", EDITION_SUR_PLACE)
    def test_un_bloc_textuel_s_edite_sur_place(self, block_file):
        content = _read_blocknote_file(block_file)
        if content is None:
            pytest.skip("blocknote-editor absent")
        assert "InlineEditable" in content, (
            f"{block_file} a du contenu editable mais n'utilise pas l'edition "
            f"sur place"
        )

    @pytest.mark.parametrize("block_file", EDITION_PAR_PANNEAU)
    def test_un_bloc_opaque_passe_par_le_panneau(self, block_file):
        content = _read_blocknote_file(block_file)
        if content is None:
            pytest.skip("blocknote-editor absent")
        assert "openEditPanel" in content, (
            f"{block_file} ne peut pas s'editer sur place et n'offre pas le "
            f"panneau : il serait impossible a modifier"
        )
        assert "edit-handler" in content

    def test_aucun_bloc_ne_reste_sans_moyen_d_edition(self):
        """Le vrai risque : un bloc qu'on ne peut modifier ni sur place ni par
        le panneau. Il s'afficherait normalement et resisterait a toute
        correction."""
        import pathlib as _p
        dossier = _p.Path(__file__).parent.parent.parent / "blocknote-editor" / "src" / "blocks"
        if not dossier.exists():
            pytest.skip("blocknote-editor absent")
        muets = []
        for f in sorted(dossier.glob("*.tsx")):
            if f.name in ("InlineEditable.tsx",):
                continue  # la brique d'edition elle-meme, pas un bloc
            t = f.read_text(encoding="utf-8", errors="replace")
            if "InlineEditable" not in t and "openEditPanel" not in t:
                muets.append(f.name)
        assert not muets, f"blocs sans moyen d'edition : {muets}"

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

    def test_le_panneau_d_edition_est_atteignable_depuis_l_app(self):
        """Le panneau n'est plus rendu par App mais par le panneau fusionne
        (V1.17, deux onglets). Le test exigeait `<EditPanel` dans App.tsx et
        echouait depuis : il verifiait un emplacement, pas une capacite.

        Ce qui doit rester vrai, c'est le chainage : App tient le bloc en cours
        d'edition et le transmet ; le panneau le rend. Si l'un des deux manque,
        cliquer « Modifier » n'ouvre rien.
        """
        app = _read_blocknote_file("App.tsx")
        if app is None:
            pytest.skip()
        assert "editingBlock={editingBlock}" in app, (
            "App ne transmet plus le bloc en cours d'edition"
        )
        assert "onEditPanelSaved" in app and "onEditPanelClose" in app, (
            "App ne recoit plus l'enregistrement ou la fermeture"
        )
        assert "versionNumSource={versionNumSource}" in app

        panneau = _read_blocknote_file("AgentPanel.tsx")
        if panneau is None:
            pytest.skip()
        assert "<EditPanel" in panneau, (
            "plus personne ne rend le panneau d'edition"
        )


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

    def test_version_at_least_v110(self):
        pkg = REPO_ROOT / "blocknote-editor" / "package.json"
        if not pkg.exists():
            pytest.skip()
        content = pkg.read_text(encoding="utf-8")
        # Doit etre >= 1.10.0 (v1.11.0 OK aussi)
        import re
        m = re.search(r'"version":\s*"(\d+)\.(\d+)\.(\d+)"', content)
        assert m, "version absente de package.json"
        major, minor, patch = int(m.group(1)), int(m.group(2)), int(m.group(3))
        # Sprint 4 a livre v1.10.0 minimum. v1.11+ aussi OK.
        assert (major, minor, patch) >= (1, 10, 0), f"Version {major}.{minor}.{patch} < 1.10.0"
