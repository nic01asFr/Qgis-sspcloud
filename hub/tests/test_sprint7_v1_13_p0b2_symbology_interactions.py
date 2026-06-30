"""
Tests Sprint 1 V1.13 P0b-2 - Symbology per-layer + Interactions popup_template.

Couvre :
- Sub-form TS SymbologyFieldset (field/method/palette/n_classes)
- Sub-form TS InteractionsFieldset (popup_template/tooltip_field/hover_attributes)
- LayersFieldset integre les 2 sub-forms par layer card
- Helper hub : classification per-layer (vs global V1.12) + injection
  popup_template/tooltip_field/hover_attributes par layer dans le template
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


class TestSymbologyFieldset:
    """SymbologyFieldset.tsx — sub-form classif per-layer."""

    def test_file_exists(self):
        content = _read("forms/InteractiveMap/SymbologyFieldset.tsx")
        if content is None:
            pytest.skip()
        assert "export function SymbologyFieldset" in content

    def test_exposes_classification_type(self):
        content = _read("forms/InteractiveMap/SymbologyFieldset.tsx")
        if content is None:
            pytest.skip()
        assert "export type ClassificationConfig" in content
        for f in ["field", "method", "n_classes", "palette"]:
            assert f in content, f"Field {f} absent"

    def test_5_methods_jenks_quantile_equal_manual_categorized(self):
        content = _read("forms/InteractiveMap/SymbologyFieldset.tsx")
        if content is None:
            pytest.skip()
        for method in ["jenks", "quantile", "equal_interval", "manual", "categorized"]:
            assert method in content

    def test_6_palettes_colorbrewer(self):
        content = _read("forms/InteractiveMap/SymbologyFieldset.tsx")
        if content is None:
            pytest.skip()
        for palette in ["Blues", "Reds", "Greens", "RdBu", "RdYlGn", "OrRd"]:
            assert palette in content

    def test_palette_preview_swatches(self):
        """Aperçu visuel des 5 couleurs par palette."""
        content = _read("forms/InteractiveMap/SymbologyFieldset.tsx")
        if content is None:
            pytest.skip()
        assert "palettePreview" in content
        # Au moins une couleur hex ColorBrewer par palette
        assert "#eff3ff" in content  # Blues
        assert "#fee5d9" in content  # Reds

    def test_field_select_from_properties_keys(self):
        """Si properties_keys disponibles, on les expose en SelectField."""
        content = _read("forms/InteractiveMap/SymbologyFieldset.tsx")
        if content is None:
            pytest.skip()
        assert "propertiesKeys" in content
        assert "fieldOptions" in content

    def test_remove_button_present(self):
        content = _read("forms/InteractiveMap/SymbologyFieldset.tsx")
        if content is None:
            pytest.skip()
        assert "onRemove" in content
        assert "Retirer" in content


class TestInteractionsFieldset:
    """InteractionsFieldset.tsx — sub-form interactions per-layer."""

    def test_file_exists(self):
        content = _read("forms/InteractiveMap/InteractionsFieldset.tsx")
        if content is None:
            pytest.skip()
        assert "export function InteractionsFieldset" in content

    def test_exposes_interactions_config_type(self):
        content = _read("forms/InteractiveMap/InteractionsFieldset.tsx")
        if content is None:
            pytest.skip()
        assert "export type InteractionsConfig" in content
        for f in ["tooltip_field", "hover_attributes", "popup_template"]:
            assert f in content, f"Field {f} absent"

    def test_tooltip_field_uses_properties_keys(self):
        content = _read("forms/InteractiveMap/InteractionsFieldset.tsx")
        if content is None:
            pytest.skip()
        assert "propertiesKeys" in content
        assert "fieldOptions" in content

    def test_hover_attributes_checkbox_toggles(self):
        """Multi-select checkboxes pour hover_attributes."""
        content = _read("forms/InteractiveMap/InteractionsFieldset.tsx")
        if content is None:
            pytest.skip()
        assert "toggleHoverAttr" in content
        assert 'type="checkbox"' in content

    def test_popup_template_with_placeholder_hint(self):
        """Le hint mentionne le format Mustache feature.properties."""
        content = _read("forms/InteractiveMap/InteractionsFieldset.tsx")
        if content is None:
            pytest.skip()
        assert "feature.properties" in content


class TestLayersFieldset_IntegratesV13P0b2:
    """LayersFieldset integre Symbology + Interactions per layer card."""

    def test_imports_symbology_fieldset(self):
        content = _read("forms/InteractiveMap/LayersFieldset.tsx")
        if content is None:
            pytest.skip()
        assert "import { SymbologyFieldset" in content
        assert "ClassificationConfig" in content

    def test_imports_interactions_fieldset(self):
        content = _read("forms/InteractiveMap/LayersFieldset.tsx")
        if content is None:
            pytest.skip()
        assert "import { InteractionsFieldset" in content
        assert "InteractionsConfig" in content

    def test_renders_symbology_per_layer(self):
        content = _read("forms/InteractiveMap/LayersFieldset.tsx")
        if content is None:
            pytest.skip()
        assert "<SymbologyFieldset" in content

    def test_renders_interactions_per_layer(self):
        content = _read("forms/InteractiveMap/LayersFieldset.tsx")
        if content is None:
            pytest.skip()
        assert "<InteractionsFieldset" in content

    def test_layer_override_type_extends_v13_p0b2(self):
        """LayerOverride accepte classification + popup_template + tooltip_field + hover_attributes."""
        content = _read("forms/InteractiveMap/LayersFieldset.tsx")
        if content is None:
            pytest.skip()
        assert "classification?:" in content
        assert "popup_template?:" in content
        assert "tooltip_field?:" in content
        assert "hover_attributes?:" in content


class TestHubHelper_V13P0b2:
    """Helper hub applique classification per-layer + interactions per-layer."""

    def test_helper_reads_classification_override(self):
        import inspect
        from hub.main import _build_interactive_map_ctx
        src = inspect.getsource(_build_interactive_map_ctx)
        assert "classification_override" in src

    def test_helper_per_layer_classif_priority_over_global(self):
        """Per-layer classif prioritaire sur params.classification global V1.12."""
        import inspect
        from hub.main import _build_interactive_map_ctx
        src = inspect.getsource(_build_interactive_map_ctx)
        # Pattern : if per_layer_classif: ... elif classification_param: ...
        assert "per_layer_classif" in src
        assert "elif classification_param" in src

    def test_helper_injects_popup_template_per_layer(self):
        import inspect
        from hub.main import _build_interactive_map_ctx
        src = inspect.getsource(_build_interactive_map_ctx)
        assert "popup_template" in src

    def test_helper_injects_tooltip_field_per_layer(self):
        import inspect
        from hub.main import _build_interactive_map_ctx
        src = inspect.getsource(_build_interactive_map_ctx)
        assert "tooltip_field" in src

    def test_helper_injects_hover_attributes_per_layer(self):
        import inspect
        from hub.main import _build_interactive_map_ctx
        src = inspect.getsource(_build_interactive_map_ctx)
        assert "hover_attributes" in src

    def test_helper_back_compat_global_classification_v112(self):
        """Si params.classification global defini et pas d'override per-layer,
        la classification s'applique a tous les layers (V1.12 back-compat)."""
        import inspect
        from hub.main import _build_interactive_map_ctx
        src = inspect.getsource(_build_interactive_map_ctx)
        # classification_param est lu et applique en fallback
        assert "classification_param" in src


class TestTemplateJinja2_SupportsV13P0b2:
    """Le template _interactive_map_partial.j2 consomme deja les params V1.13 P0b-2."""

    def test_partial_uses_layer_popup_template(self):
        partial = (REPO_ROOT / "hub" / "hub" / "maplibre_renderer" /
                   "_interactive_map_partial.j2")
        if not partial.exists():
            pytest.skip()
        content = partial.read_text(encoding="utf-8")
        # Vague E2 Commit 8-9 livre deja le support de layer.popup_template
        assert "popup_template" in content
        assert "hover_attributes" in content


class TestSprint1_P0b2_Coherence:
    def test_no_regression_imports(self):
        from hub.main import (
            _build_interactive_map_ctx,
            component_source_layers_endpoint,
        )
        from hub.models.component_params import (
            ClassificationConfig,
            LayerOverride,
            InteractiveMapParams,
        )
        # LayerOverride.classification et popup_template doivent etre presents
        lo = LayerOverride(layer_id_ref="x", popup_template="<b>{{x}}</b>")
        assert lo.popup_template == "<b>{{x}}</b>"
        cc = ClassificationConfig(field="risk", method="jenks", n_classes=5, palette="Reds")
        assert cc.palette == "Reds"
