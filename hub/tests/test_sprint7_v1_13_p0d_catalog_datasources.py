"""
Tests Sprint 1 V1.13 P0d - Catalogue datasources + autocomplete.

Couvre :
- hub.catalog_datasources : list_datasources, get_label, get_datasource, list_categories
- Endpoint GET /catalog/datasources (avec/sans category filter)
- DatasourceAutocomplete.tsx sub-form Marie
- Serializer + EditPanel round-trip datasource_id
- _build_interactive_map_ctx utilise catalog module (vs hardcoded V1.12)
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


class TestCatalogDatasourcesModule:
    """hub.catalog_datasources : module + lookups."""

    def test_module_imports(self):
        from hub.catalog_datasources import (
            CATALOG_DATASOURCES,
            get_datasource,
            get_label,
            list_datasources,
            list_categories,
        )
        assert all([
            CATALOG_DATASOURCES, get_datasource, get_label,
            list_datasources, list_categories,
        ])

    def test_catalog_has_min_14_entries(self):
        from hub.catalog_datasources import CATALOG_DATASOURCES
        assert len(CATALOG_DATASOURCES) >= 14

    def test_catalog_has_canonical_ids(self):
        from hub.catalog_datasources import _BY_ID
        # Cles canoniques attendues (V1.12 + V1.13 P0d)
        for canonical in [
            "bdtopo_batiments", "bdtopo_parcelles", "bdtopo_adresses",
            "rge_alti", "admin_communes", "georisques_api", "tri_limites",
            "bdtdv", "corine_land_cover",
        ]:
            assert canonical in _BY_ID, f"ID canonique {canonical} absent"

    def test_get_label_returns_full_citation(self):
        from hub.catalog_datasources import get_label
        label = get_label("bdtopo_batiments")
        assert label
        assert "BD TOPO" in label
        assert "IGN" in label
        assert "Licence" in label

    def test_get_label_unknown_returns_empty(self):
        from hub.catalog_datasources import get_label
        assert get_label("unknown_xyz") == ""
        assert get_label("") == ""

    def test_get_datasource_returns_full_dict(self):
        from hub.catalog_datasources import get_datasource
        ds = get_datasource("bdtopo_batiments")
        assert ds is not None
        for field in ["id", "label", "short_label", "authority", "licence", "category"]:
            assert field in ds, f"Field {field} absent"

    def test_list_datasources_no_filter_returns_all(self):
        from hub.catalog_datasources import CATALOG_DATASOURCES, list_datasources
        assert len(list_datasources()) == len(CATALOG_DATASOURCES)

    def test_list_datasources_filter_by_category(self):
        from hub.catalog_datasources import list_datasources
        risques = list_datasources("risque")
        assert all(d["category"] == "risque" for d in risques)
        assert any(d["id"] == "georisques_api" for d in risques)
        assert any(d["id"] == "tri_limites" for d in risques)

    def test_list_categories_unique(self):
        from hub.catalog_datasources import list_categories
        cats = list_categories()
        # Categories canoniques V1.13 P0d
        assert "referentiel" in cats
        assert "risque" in cats
        # Pas de doublons
        assert len(cats) == len(set(cats))


class TestEndpointCatalogDatasources:
    """GET /catalog/datasources expose le catalog au frontend."""

    def test_endpoint_route_registered(self):
        from hub.main import app
        routes = [r.path for r in app.routes if hasattr(r, "path")]
        assert "/catalog/datasources" in routes

    def test_endpoint_defined(self):
        from hub.main import catalog_datasources_endpoint
        assert catalog_datasources_endpoint is not None

    def test_endpoint_returns_datasources_and_categories(self):
        import inspect
        from hub.main import catalog_datasources_endpoint
        src = inspect.getsource(catalog_datasources_endpoint)
        assert "list_datasources" in src
        assert "list_categories" in src

    def test_endpoint_accepts_category_query_param(self):
        import inspect
        from hub.main import catalog_datasources_endpoint
        sig = inspect.signature(catalog_datasources_endpoint)
        assert "category" in sig.parameters


class TestDatasourceAutocompleteForm:
    """DatasourceAutocomplete.tsx — sub-form Marie."""

    def test_file_exists(self):
        content = _read("forms/InteractiveMap/DatasourceAutocomplete.tsx")
        if content is None:
            pytest.skip()
        assert "export function DatasourceAutocomplete" in content

    def test_fetches_catalog_endpoint(self):
        content = _read("forms/InteractiveMap/DatasourceAutocomplete.tsx")
        if content is None:
            pytest.skip()
        assert "/catalog/datasources" in content
        assert "fetchDatasources" in content

    def test_exposes_datasource_type(self):
        content = _read("forms/InteractiveMap/DatasourceAutocomplete.tsx")
        if content is None:
            pytest.skip()
        assert "export type Datasource" in content
        for f in ["id", "label", "short_label", "authority", "category"]:
            assert f in content

    def test_category_filter_select(self):
        content = _read("forms/InteractiveMap/DatasourceAutocomplete.tsx")
        if content is None:
            pytest.skip()
        assert "categoryLabels" in content
        assert "referentiel" in content
        assert "risque" in content

    def test_auto_fills_source_on_pick(self):
        """Quand Marie pick un datasource_id, source est auto-rempli."""
        content = _read("forms/InteractiveMap/DatasourceAutocomplete.tsx")
        if content is None:
            pytest.skip()
        assert "handlePick" in content
        assert "onChangeSource" in content

    def test_free_text_fallback_preserved(self):
        """Marie peut toujours saisir source en texte libre."""
        content = _read("forms/InteractiveMap/DatasourceAutocomplete.tsx")
        if content is None:
            pytest.skip()
        assert "TextField" in content


class TestInteractiveMapForm_UsesAutocomplete:
    """InteractiveMapForm V1.13 P0d integre DatasourceAutocomplete."""

    def test_form_imports_autocomplete(self):
        content = _read("forms/InteractiveMapForm.tsx")
        if content is None:
            pytest.skip()
        assert "import { DatasourceAutocomplete" in content

    def test_form_renders_autocomplete_in_credibilite(self):
        content = _read("forms/InteractiveMapForm.tsx")
        if content is None:
            pytest.skip()
        assert "<DatasourceAutocomplete" in content


class TestSerializer_EditPanel_RoundtripDatasource:
    """datasource_id round-trip serializer + EditPanel."""

    def test_serializer_pushes_datasource_id(self):
        content = _read("serializer.ts")
        if content is None:
            pytest.skip()
        assert "props.datasource_id" in content

    def test_build_params_includes_datasource_id(self):
        content = _read("EditPanel.tsx")
        if content is None:
            pytest.skip()
        # buildParamsFromFormData ajoute data.datasource_id si defini
        assert "datasource_id" in content
        assert "data.datasource_id" in content

    def test_params_to_block_props_includes_datasource_id(self):
        content = _read("EditPanel.tsx")
        if content is None:
            pytest.skip()
        # paramsToBlockProps return inclut datasource_id pour round-trip
        assert "datasource_id: params.datasource_id" in content


class TestHubHelperUsesCatalogModule:
    """_build_interactive_map_ctx delegue au catalog module (vs hardcoded V1.12)."""

    def test_helper_uses_catalog_module(self):
        import inspect
        from hub.main import _build_interactive_map_ctx
        src = inspect.getsource(_build_interactive_map_ctx)
        # V1.13 P0d : import depuis hub.catalog_datasources (vs hardcoded dict)
        assert "catalog_datasources" in src or "get_label" in src

    def test_get_label_returns_v112_compatible_labels(self):
        """Les labels V1.13 doivent etre compatibles V1.12 (au moins le contenu)."""
        from hub.catalog_datasources import get_label
        # V1.12 hardcoded : "BD TOPO 2024 — IGN — Licence Ouverte 2.0"
        # V1.13 : "BD TOPO 2024 — Batiments — IGN — Licence Ouverte 2.0"
        label = get_label("bdtopo_batiments")
        assert "BD TOPO" in label
        assert "IGN" in label

        label_dvf = get_label("bdtdv")
        assert "DVF" in label_dvf
        assert "DGFiP" in label_dvf


class TestSprint1_P0d_Coherence:
    def test_no_regression_imports(self):
        from hub.catalog_datasources import CATALOG_DATASOURCES, get_label
        from hub.main import (
            catalog_datasources_endpoint,
            _build_interactive_map_ctx,
        )
        assert all([
            CATALOG_DATASOURCES, get_label,
            catalog_datasources_endpoint, _build_interactive_map_ctx,
        ])
