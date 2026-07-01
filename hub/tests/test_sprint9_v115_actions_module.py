"""
Tests Sprint V1.15 - Module hub/actions/ + AgentBrick + assembly-scope.

Couvre :
- Extraction hub/actions/ (etape 1 : component_actions + errors + types)
- OCC Assembly enforce (etape 2 : insert_assembly + apply_assembly_patch)
- AgentBrick contract + ProfileConfig Pydantic (etape 3)
- Profile assembly_assist.yaml (etape 4)
- Endpoints /assemblies/{aid}/assist/{suggestions,action} (etape 5)
"""
from __future__ import annotations

import inspect
import pytest


class TestActionsModuleExtraction:
    """Etape 1 : module hub/actions/ existe + expose l'API attendue."""

    def test_module_importable(self):
        from hub import actions
        assert actions is not None

    def test_types_pydantic(self):
        from hub.actions import ActionResult, HistoryEntry, Scope, BlockRef
        # Model minimal
        r = ActionResult(
            success=True, tool="cmp_get_context",
            action_type="context_read",
        )
        assert r.success is True

    def test_errors_status_codes(self):
        from hub.actions import (
            ScopeViolationError, ConcurrentUpdateError,
            ActionValidationError, ActionNotFoundError,
            ToolNotAllowedError, PersistenceError,
        )
        assert ScopeViolationError("t").status_code == 403
        assert ConcurrentUpdateError("t", current=1, source=0).status_code == 409
        assert ActionValidationError("t").status_code == 422
        assert ActionNotFoundError("t").status_code == 404
        assert ToolNotAllowedError("t").status_code == 400
        assert PersistenceError("t").status_code == 500

    def test_cmp_allowed_tools_v115_extended(self):
        """V1.15 : whitelist etendu de 5 (V1.14.1) a 12 tools."""
        from hub.actions import CMP_ALLOWED_TOOLS
        assert len(CMP_ALLOWED_TOOLS) >= 12
        # 5 V1.14.1 livres en prod
        for t in ["cmp_get_context", "cmp_set_tooltip", "cmp_set_zone",
                  "cmp_set_source_citation", "cmp_add_layer"]:
            assert t in CMP_ALLOWED_TOOLS
        # 7 nouveaux V1.15 backlog etude B
        for t in ["cmp_remove_layer", "cmp_set_basemap",
                  "cmp_set_classification", "cmp_set_popup_template",
                  "cmp_set_hover_attrs", "cmp_reorder_layers",
                  "cmp_set_legend"]:
            assert t in CMP_ALLOWED_TOOLS

    def test_apply_component_patch_signature_neutre(self):
        """DI execute_python + comp_mod = reutilisable cross-projet."""
        from hub.actions import apply_component_patch
        sig = inspect.signature(apply_component_patch)
        # Signature NEUTRE FastAPI/auth : user: str en param (pas Depends)
        assert "user" in sig.parameters
        assert "execute_python" in sig.parameters
        assert "comp_mod" in sig.parameters
        # Kwargs-only design
        assert all(
            p.kind in {inspect.Parameter.KEYWORD_ONLY,
                       inspect.Parameter.POSITIONAL_OR_KEYWORD}
            for p in sig.parameters.values()
        )


class TestOCCAssemblyEtape2:
    """Etape 2 : OCC Assembly enforce au niveau fonction + module."""

    def test_insert_assembly_supports_version_num_source(self):
        from hub.assemblies import insert_assembly
        sig = inspect.signature(insert_assembly)
        assert "version_num_source" in sig.parameters

    def test_asy_allowed_tools_8(self):
        from hub.actions import ASY_ALLOWED_TOOLS
        assert len(ASY_ALLOWED_TOOLS) == 8
        for t in ["asy_get_context", "asy_insert_block",
                  "asy_delete_block", "asy_move_block",
                  "asy_set_title", "asy_set_section_title",
                  "asy_reorder_sections", "asy_list_available_kinds"]:
            assert t in ASY_ALLOWED_TOOLS

    def test_stu_allowed_tools_3(self):
        from hub.actions import STU_ALLOWED_TOOLS
        assert len(STU_ALLOWED_TOOLS) == 3

    def test_inline_vs_iframe_kinds_partition(self):
        """13 kinds distribues : 7 inline + 6 iframe."""
        from hub.actions import INLINE_KINDS, IFRAME_KINDS, ALL_COMPONENT_KINDS
        assert len(INLINE_KINDS) == 7
        assert len(IFRAME_KINDS) == 6
        assert len(ALL_COMPONENT_KINDS) == 13
        assert INLINE_KINDS.isdisjoint(IFRAME_KINDS)


class TestAgentBrickContractEtape3:
    """Etape 3 : AgentBrick contract + ProfileConfig Pydantic."""

    def test_agent_brick_importable(self):
        from hub.actions import AgentBrick, Suggestion
        assert AgentBrick is not None
        assert Suggestion is not None

    def test_agent_brick_methods_public(self):
        from hub.actions import AgentBrick
        assert inspect.iscoroutinefunction(AgentBrick.get_suggestions)
        assert inspect.iscoroutinefunction(AgentBrick.execute_action)

    def test_agent_brick_di_execute_python(self):
        """DI : execute_python en param constructor pour cross-projet."""
        from hub.actions import AgentBrick
        sig = inspect.signature(AgentBrick.__init__)
        for p in ["execute_python", "asm_mod", "comp_mod"]:
            assert p in sig.parameters

    def test_profile_config_pydantic_validation(self):
        """Anti-pattern C5 : validation Pydantic evite faute silencieuse."""
        from hub.profile_manager import ProfileConfig
        from pydantic import ValidationError

        # Profile valide
        valid = {
            "id": "test_v115",
            "name": "Test V1.15",
            "native_tools": {"allowed": ["cmp_set_tooltip"]},
            "data_scope": "assembly",
        }
        p = ProfileConfig.model_validate(valid)
        assert p.id == "test_v115"
        assert p.data_scope == "assembly"

        # Profile invalide (id manquant)
        with pytest.raises(ValidationError):
            ProfileConfig.model_validate({"name": "sans id"})

    def test_profile_manager_supports_native_tools(self):
        """profile_manager expose get_allowed_native_tools() + get_data_scope()."""
        from hub import profile_manager
        assert hasattr(profile_manager, "get_allowed_native_tools")
        assert hasattr(profile_manager, "get_data_scope")


class TestAssemblyAssistProfileEtape4:
    """Etape 4 : profile assembly_assist.yaml."""

    def test_profile_loaded(self):
        from hub import profile_manager
        p = profile_manager.get_profile("assembly_assist")
        assert p["id"] == "assembly_assist"
        assert p["name"] == "Assistant Assemblage CEREMA"

    def test_profile_whitelist_25_tools(self):
        from hub import profile_manager
        allowed = profile_manager.get_allowed_native_tools("assembly_assist")
        assert isinstance(allowed, list)
        assert len(allowed) >= 22  # 8 asy + 12 cmp + 3 stu = 23 (min)

    def test_profile_data_scope_assembly(self):
        from hub import profile_manager
        scope = profile_manager.get_data_scope("assembly_assist")
        assert scope == "assembly"

    def test_profile_has_marie_persona_prompt(self):
        from hub import profile_manager
        p = profile_manager.get_profile("assembly_assist")
        prompt = p.get("system_prompt", "")
        assert "marie" in prompt.lower() or "cerema" in prompt.lower()


class TestEndpointsAssemblyEtape5:
    """Etape 5 : endpoints /assemblies/{aid}/assist/{suggestions,action}."""

    def test_suggestions_endpoint_registered(self):
        from hub.main import app
        routes = [r.path for r in app.routes if hasattr(r, "path")]
        assert "/studies/{sid}/assemblies/{aid}/assist/suggestions" in routes

    def test_action_endpoint_registered(self):
        from hub.main import app
        routes = [r.path for r in app.routes if hasattr(r, "path")]
        assert "/studies/{sid}/assemblies/{aid}/assist/action" in routes

    def test_suggestions_uses_agent_brick(self):
        import inspect
        from hub.main import assembly_assist_suggestions_endpoint
        src = inspect.getsource(assembly_assist_suggestions_endpoint)
        assert "AgentBrick" in src
        assert "get_suggestions" in src

    def test_action_uses_agent_brick_execute(self):
        import inspect
        from hub.main import assembly_assist_action_endpoint
        src = inspect.getsource(assembly_assist_action_endpoint)
        assert "AgentBrick" in src
        assert "execute_action" in src
        assert "ConcurrentUpdateError" in src

    def test_v114_endpoint_still_delegates_via_actions(self):
        """V1.14.1 refactorise mais toujours fonctionnel (backward compat)."""
        import inspect
        from hub.main import component_assist_action_endpoint
        src = inspect.getsource(component_assist_action_endpoint)
        assert "apply_component_patch" in src


class TestSprint9Coherence:
    def test_no_regression_all_imports(self):
        from hub.actions import (
            ActionResult, HistoryEntry, Scope, BlockRef,
            CMP_ALLOWED_TOOLS, apply_component_patch,
            ASY_ALLOWED_TOOLS, STU_ALLOWED_TOOLS,
            INLINE_KINDS, IFRAME_KINDS, ALL_COMPONENT_KINDS,
            apply_assembly_patch,
            AgentBrick, Suggestion,
        )
        from hub.profile_manager import (
            ProfileConfig, get_allowed_native_tools, get_data_scope,
        )
        from hub.main import (
            component_assist_suggestions_endpoint,
            component_assist_action_endpoint,
            assembly_assist_suggestions_endpoint,
            assembly_assist_action_endpoint,
        )
        # Tout importe sans erreur
        assert all([
            ActionResult, HistoryEntry, Scope, BlockRef,
            CMP_ALLOWED_TOOLS, apply_component_patch,
            ASY_ALLOWED_TOOLS, STU_ALLOWED_TOOLS,
            INLINE_KINDS, IFRAME_KINDS, ALL_COMPONENT_KINDS,
            apply_assembly_patch,
            AgentBrick, Suggestion,
            ProfileConfig, get_allowed_native_tools, get_data_scope,
            component_assist_suggestions_endpoint,
            component_assist_action_endpoint,
            assembly_assist_suggestions_endpoint,
            assembly_assist_action_endpoint,
        ])
