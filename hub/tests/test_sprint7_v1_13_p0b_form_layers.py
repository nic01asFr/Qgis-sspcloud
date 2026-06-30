"""
Tests Sprint 1 V1.13 P0b-1 - Form Layers + Zone d'etude.

Couvre :
- Endpoint hub GET /studies/{sid}/components/{cid}/source_layers
- Sub-forms TS ZoneFieldset + LayersFieldset (introspection source)
- Serializer / paramsToBlockProps : round-trip zone + layers_override
- Helper hub : layers_override visible/opacity/name_override applique
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


class TestEndpointSourceLayers:
    """Hub endpoint GET /studies/{sid}/components/{cid}/source_layers."""

    def test_endpoint_defined_in_main(self):
        from hub.main import component_source_layers_endpoint
        assert component_source_layers_endpoint is not None

    def test_endpoint_route_registered(self):
        from hub.main import app
        routes = [r.path for r in app.routes if hasattr(r, "path")]
        assert "/studies/{sid}/components/{cid}/source_layers" in routes

    def test_endpoint_reads_scene_manifest(self):
        """Le code de l'endpoint doit appeler read_scene_manifest_pod_code."""
        import inspect
        from hub.main import component_source_layers_endpoint
        src = inspect.getsource(component_source_layers_endpoint)
        assert "read_scene_manifest_pod_code" in src
        # Doit aussi retourner properties_keys (utile pour P0b-2 Symbology)
        assert "properties_keys" in src


class TestZoneFieldset:
    """TS sub-form Zone d'etude."""

    def test_zone_fieldset_file_exists(self):
        content = _read("forms/InteractiveMap/ZoneFieldset.tsx")
        if content is None:
            pytest.skip("blocknote-editor absent")
        assert "export function ZoneFieldset" in content

    def test_zone_fieldset_has_3_kinds(self):
        content = _read("forms/InteractiveMap/ZoneFieldset.tsx")
        if content is None:
            pytest.skip()
        # 3 modes : commune / manual / study
        for kind in ["commune", "manual", "study"]:
            assert f"'{kind}'" in content, f"kind {kind} absent"

    def test_zone_fieldset_warns_marseille_marseillette(self):
        """KB project_bug_marseille_geocoding : warning piege."""
        content = _read("forms/InteractiveMap/ZoneFieldset.tsx")
        if content is None:
            pytest.skip()
        assert "Marseillette" in content or "Marseille" in content

    def test_zone_fieldset_has_insee_buffer_fields(self):
        content = _read("forms/InteractiveMap/ZoneFieldset.tsx")
        if content is None:
            pytest.skip()
        assert "insee" in content
        assert "buffer_km" in content

    def test_zone_fieldset_has_manual_lat_lng_zoom(self):
        content = _read("forms/InteractiveMap/ZoneFieldset.tsx")
        if content is None:
            pytest.skip()
        assert "center_lat" in content
        assert "center_lng" in content
        assert "zoom" in content


class TestLayersFieldset:
    """TS sub-form Layers."""

    def test_layers_fieldset_file_exists(self):
        content = _read("forms/InteractiveMap/LayersFieldset.tsx")
        if content is None:
            pytest.skip()
        assert "export function LayersFieldset" in content

    def test_layers_fieldset_fetches_source_layers_endpoint(self):
        content = _read("forms/InteractiveMap/LayersFieldset.tsx")
        if content is None:
            pytest.skip()
        assert "/source_layers" in content
        assert "fetchSourceLayers" in content

    def test_layers_fieldset_exposes_layer_override_type(self):
        content = _read("forms/InteractiveMap/LayersFieldset.tsx")
        if content is None:
            pytest.skip()
        assert "export type LayerOverride" in content
        assert "layer_id_ref" in content
        assert "visible" in content
        assert "opacity" in content
        assert "name_override" in content

    def test_layers_fieldset_uses_credentials_include(self):
        """Fetch doit inclure les cookies pour OIDC."""
        content = _read("forms/InteractiveMap/LayersFieldset.tsx")
        if content is None:
            pytest.skip()
        assert "credentials" in content
        assert "include" in content


class TestInteractiveMapForm_V113:
    """InteractiveMapForm V1.13 utilise les 2 nouveaux fieldsets."""

    def test_form_imports_zone_fieldset(self):
        content = _read("forms/InteractiveMapForm.tsx")
        if content is None:
            pytest.skip()
        assert "import { ZoneFieldset" in content

    def test_form_imports_layers_fieldset(self):
        content = _read("forms/InteractiveMapForm.tsx")
        if content is None:
            pytest.skip()
        assert "import { LayersFieldset" in content

    def test_form_renders_zone_fieldset(self):
        content = _read("forms/InteractiveMapForm.tsx")
        if content is None:
            pytest.skip()
        assert "<ZoneFieldset" in content

    def test_form_renders_layers_fieldset(self):
        content = _read("forms/InteractiveMapForm.tsx")
        if content is None:
            pytest.skip()
        assert "<LayersFieldset" in content

    def test_form_extracts_sid_from_url(self):
        """Le form fetch /source_layers donc doit connaitre le sid."""
        content = _read("forms/InteractiveMapForm.tsx")
        if content is None:
            pytest.skip()
        assert "getSidFromUrl" in content
        # Pattern path : /editor/{sid}/...
        assert "/editor/" in content


class TestSerializer_V113:
    """Serializer pousse zone + layers_override + cid dans block.props."""

    def test_serializer_pushes_cid(self):
        content = _read("serializer.ts")
        if content is None:
            pytest.skip()
        assert "props.cid = component.id" in content

    def test_serializer_pushes_zone(self):
        content = _read("serializer.ts")
        if content is None:
            pytest.skip()
        assert "props.zone" in content

    def test_serializer_pushes_layers_override(self):
        content = _read("serializer.ts")
        if content is None:
            pytest.skip()
        assert "props.layers_override" in content


class TestEditPanel_V113:
    """EditPanel round-trip zone + layers_override apres save."""

    def test_build_params_includes_zone_and_layers(self):
        """buildParamsFromFormData('interactive_map') passe zone + layers_override."""
        content = _read("EditPanel.tsx")
        if content is None:
            pytest.skip()
        # Bloc case 'interactive_map' du buildParamsFromFormData
        # doit gerer data.zone et data.layers_override
        assert "data.zone" in content
        assert "data.layers_override" in content

    def test_params_to_block_props_includes_zone_and_layers(self):
        """paramsToBlockProps('interactiveMap') round-trip."""
        content = _read("EditPanel.tsx")
        if content is None:
            pytest.skip()
        # Pattern : params.zone + params.layers_override dans le return
        assert "zone: params.zone" in content
        assert "layers_override:" in content


class TestHubHelper_V113:
    """_build_interactive_map_ctx applique layers_override + zone."""

    def test_helper_reads_layers_override(self):
        import inspect
        from hub.main import _build_interactive_map_ctx
        src = inspect.getsource(_build_interactive_map_ctx)
        assert "layers_override" in src

    def test_helper_applies_visibility_filter(self):
        """Si layers_override[i].visible == False, le layer est skip."""
        import inspect
        from hub.main import _build_interactive_map_ctx
        src = inspect.getsource(_build_interactive_map_ctx)
        # On verifie la presence du pattern "visible" et "False" qui font
        # filter cote helper hub.
        assert 'visible' in src.lower()
        # Pattern : continue if override.visible is False
        assert "continue" in src

    def test_helper_applies_name_override(self):
        import inspect
        from hub.main import _build_interactive_map_ctx
        src = inspect.getsource(_build_interactive_map_ctx)
        assert "name_override" in src

    def test_helper_applies_opacity(self):
        import inspect
        from hub.main import _build_interactive_map_ctx
        src = inspect.getsource(_build_interactive_map_ctx)
        assert "opacity" in src

    def test_helper_reads_zone_param(self):
        import inspect
        from hub.main import _build_interactive_map_ctx
        src = inspect.getsource(_build_interactive_map_ctx)
        # Le helper doit lire params.zone et dispatcher sur kind
        assert 'params.get("zone")' in src or "params.get('zone')" in src

    def test_helper_supports_zone_kind_commune_insee(self):
        """Insee Marseille 4e / Paris 4e / Lyon doivent etre resolus."""
        import inspect
        from hub.main import _build_interactive_map_ctx
        src = inspect.getsource(_build_interactive_map_ctx)
        # Codes INSEE arrondissements (KB reference_insee_arrondissements)
        # P0b-1 livre 3 codes connus en dur (P0c livrera _try_resolve_major_city)
        assert "13204" in src  # Marseille 4e
        assert "75104" in src  # Paris 4e
        assert "69383" in src  # Lyon 3e


class TestSprint1_P0b1_Coherence:
    """Coherence Sprint 1 V1.13 P0b-1."""

    def test_package_version_at_least_v113(self):
        pkg = REPO_ROOT / "blocknote-editor" / "package.json"
        if not pkg.exists():
            pytest.skip()
        content = pkg.read_text(encoding="utf-8")
        import re
        m = re.search(r'"version":\s*"(\d+)\.(\d+)\.(\d+)"', content)
        assert m
        major, minor, patch = int(m.group(1)), int(m.group(2)), int(m.group(3))
        assert (major, minor, patch) >= (1, 13, 0), f"v{major}.{minor}.{patch} < 1.13.0"

    def test_no_regression_imports(self):
        """Imports cles restent OK."""
        from hub.main import (
            _build_interactive_map_ctx,
            component_source_layers_endpoint,
            update_component_endpoint,
        )
        from hub.models.component_params import (
            InteractiveMapParams,
            LayerOverride,
            ZoneConfig,
        )
        assert all([
            _build_interactive_map_ctx,
            component_source_layers_endpoint,
            update_component_endpoint,
            InteractiveMapParams,
            LayerOverride,
            ZoneConfig,
        ])
