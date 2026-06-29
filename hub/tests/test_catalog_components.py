"""
Tests Vague E1 Commit 3 (D-QGIS-009) — /catalog/components + /catalog/assemblies cross-etude.

Vérifie :
- Endpoints GET routes présents
- Native tools list_catalog_components + list_catalog_assemblies
- Audience default cerema_internal anti-RGPD
- Schemas OpenAI function-calling avec enum audience
- Profile v15 whitelist
"""

from __future__ import annotations

import pytest


class TestCatalogEndpointsVagueE1:
    """D-QGIS-009 : catalogue cross-étude composants + assemblages."""

    def test_endpoint_catalog_components_exists(self):
        """GET /catalog/components doit etre route."""
        from hub.main import app
        catalog_comp_routes = [
            r for r in app.routes
            if hasattr(r, "path") and r.path == "/catalog/components"
        ]
        assert len(catalog_comp_routes) >= 1, (
            "Endpoint GET /catalog/components manquant. D-QGIS-009 Commit 3."
        )

    def test_endpoint_catalog_assemblies_exists(self):
        """GET /catalog/assemblies doit etre route."""
        from hub.main import app
        catalog_asm_routes = [
            r for r in app.routes
            if hasattr(r, "path") and r.path == "/catalog/assemblies"
        ]
        assert len(catalog_asm_routes) >= 1


class TestNativeToolsCatalog:
    """Tools natifs list_catalog_*."""

    def test_native_tool_list_catalog_components_existe(self):
        from agent.native_tools_v2 import NATIVE_TOOLS_V2
        assert "list_catalog_components" in NATIVE_TOOLS_V2

    def test_native_tool_list_catalog_assemblies_existe(self):
        from agent.native_tools_v2 import NATIVE_TOOLS_V2
        assert "list_catalog_assemblies" in NATIVE_TOOLS_V2

    def test_catalog_tools_NOT_mutating(self):
        """Catalog tools sont LECTURE seule, pas mutants -> pas dans MUTATING."""
        from agent.native_tools_v2 import NATIVE_TOOLS_V2_MUTATING
        assert "list_catalog_components" not in NATIVE_TOOLS_V2_MUTATING
        assert "list_catalog_assemblies" not in NATIVE_TOOLS_V2_MUTATING

    def test_openai_schemas_catalog_existent(self):
        from agent.native_tools_v2 import NATIVE_TOOLS_V2_OPENAI
        names = [
            t["function"]["name"]
            for t in NATIVE_TOOLS_V2_OPENAI
            if t.get("type") == "function"
        ]
        assert "list_catalog_components" in names
        assert "list_catalog_assemblies" in names

    def test_audience_default_cerema_internal(self):
        """Schema OpenAI : audience default = cerema_internal (anti-RGPD)."""
        from agent.native_tools_v2 import NATIVE_TOOLS_V2_OPENAI
        catalog_schema = next(
            (t for t in NATIVE_TOOLS_V2_OPENAI
             if t.get("type") == "function" and t["function"]["name"] == "list_catalog_components"),
            None,
        )
        assert catalog_schema is not None
        audience_prop = catalog_schema["function"]["parameters"]["properties"]["audience"]
        assert audience_prop.get("default") == "cerema_internal", (
            "Audience default doit etre 'cerema_internal' (anti-fuite RGPD). "
            "Ne JAMAIS default 'public'."
        )


class TestProfileWhitelistCatalog:
    """Profile v15 whitelist."""

    def test_whitelist_list_catalog_components(self):
        from pathlib import Path
        profile_path = Path(__file__).parent.parent / "hub" / "profiles" / "storymap_creator_v15.yaml"
        content = profile_path.read_text(encoding="utf-8")
        assert "list_catalog_components" in content

    def test_whitelist_list_catalog_assemblies(self):
        from pathlib import Path
        profile_path = Path(__file__).parent.parent / "hub" / "profiles" / "storymap_creator_v15.yaml"
        content = profile_path.read_text(encoding="utf-8")
        assert "list_catalog_assemblies" in content
