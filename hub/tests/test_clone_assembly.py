"""
Tests Vague E1 Commit 2 (D-QGIS-009) — clone_assembly endpoint + cloned_from.

Vérifie que :
- POST /studies/{sid}/assemblies/{aid}/clone endpoint route présente
- clone_assembly tool natif dispatch + schema OpenAI
- ComponentProvenance.cloned_from field présent
- Backward-compat : cloned_from = None par défaut (assemblages existants OK)
- v15 profile whitelist clone_assembly
"""

from __future__ import annotations

import pytest


class TestCloneAssemblyVagueE1:
    """D-QGIS-009 : clone d'assemblage existant pattern fork."""

    def test_endpoint_route_clone_exists(self):
        """POST /studies/{sid}/assemblies/{aid}/clone doit etre route."""
        from hub.main import app
        clone_routes = [
            r for r in app.routes
            if hasattr(r, "path") and "/assemblies/{aid}/clone" in r.path
        ]
        assert len(clone_routes) >= 1, (
            "Endpoint POST /studies/{sid}/assemblies/{aid}/clone manquant. "
            "D-QGIS-009 Vague E1 Commit 2 attendu."
        )

    def test_native_tool_clone_assembly_existe(self):
        """clone_assembly doit etre dans NATIVE_TOOLS_V2 dispatch."""
        from agent.native_tools_v2 import NATIVE_TOOLS_V2
        assert "clone_assembly" in NATIVE_TOOLS_V2
        entry = NATIVE_TOOLS_V2["clone_assembly"]
        assert "fn" in entry
        assert "description" in entry
        assert "D-QGIS-009" in entry["description"] or "Vague E1" in entry["description"]

    def test_native_tool_clone_dans_mutating(self):
        """clone_assembly doit etre dans MUTATING (cache L2 invalidation)."""
        from agent.native_tools_v2 import NATIVE_TOOLS_V2_MUTATING
        assert "clone_assembly" in NATIVE_TOOLS_V2_MUTATING

    def test_openai_schema_clone_assembly_existe(self):
        """Schema OpenAI function-calling clone_assembly présent."""
        from agent.native_tools_v2 import NATIVE_TOOLS_V2_OPENAI
        names = [
            t["function"]["name"]
            for t in NATIVE_TOOLS_V2_OPENAI
            if t.get("type") == "function"
        ]
        assert "clone_assembly" in names

    def test_openai_schema_clone_a_deep_param(self):
        """Schema clone_assembly doit avoir param 'deep' boolean."""
        from agent.native_tools_v2 import NATIVE_TOOLS_V2_OPENAI
        clone_schema = next(
            (t for t in NATIVE_TOOLS_V2_OPENAI
             if t.get("type") == "function" and t["function"]["name"] == "clone_assembly"),
            None,
        )
        assert clone_schema is not None
        props = clone_schema["function"]["parameters"]["properties"]
        assert "deep" in props
        assert props["deep"]["type"] == "boolean"


class TestComponentProvenanceClonedFrom:
    """D-QGIS-009 : ComponentProvenance.cloned_from field."""

    def test_cloned_from_field_existe(self):
        """ComponentProvenance.cloned_from: str | None = None"""
        from hub.models.audit_chain import ComponentProvenance
        assert "cloned_from" in ComponentProvenance.model_fields
        field_info = ComponentProvenance.model_fields["cloned_from"]
        # str | None
        assert field_info.annotation == (str | None)

    def test_cloned_from_default_none(self):
        """Default None pour backward-compat absolue."""
        from hub.models.audit_chain import ComponentProvenance
        prov = ComponentProvenance()
        assert prov.cloned_from is None

    def test_cloned_from_accepte_string(self):
        """cloned_from peut etre rempli avec cid/aid."""
        from hub.models.audit_chain import ComponentProvenance
        prov = ComponentProvenance(cloned_from="aabbccddeeff")
        assert prov.cloned_from == "aabbccddeeff"

    def test_assembly_provenance_partage_cloned_from(self):
        """Option C : Assembly.provenance utilise ComponentProvenance, donc
        cloned_from accessible aussi pour assemblies (sémantique : aid source)."""
        from hub.models.assembly import Assembly
        from hub.models.audit_chain import ComponentProvenance
        # Assembly.provenance: ComponentProvenance (cf. assembly.py:141)
        assert Assembly.model_fields["provenance"].annotation == ComponentProvenance

    def test_v15_profile_whitelist_clone_assembly(self):
        """clone_assembly doit etre dans le profile v15."""
        from pathlib import Path
        profile_path = Path(__file__).parent.parent / "hub" / "profiles" / "storymap_creator_v15.yaml"
        with open(profile_path, "r", encoding="utf-8") as f:
            content = f.read()
        assert "clone_assembly" in content
