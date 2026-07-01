"""
Tests Sprint 2.5 V2.5 - Assistant IA contextuel scoped composant.

Couvre :
- 5 tools cmp_* natifs (cmp_get_context, cmp_set_tooltip, cmp_set_zone,
  cmp_set_source_citation, cmp_add_layer)
- Profile YAML component_assist.yaml (whitelist + system_prompt)
- Endpoints hub /assist/suggestions + /assist/action
- Frontend AssistantCard.tsx (introspection source)
- Frozenset NATIVE_TOOLS_V2_MUTATING contient les 4 mutants cmp_*
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


class TestProfileYAML:
    """Profile YAML component_assist.yaml existe + coherent."""

    def test_profile_file_exists(self):
        p = REPO_ROOT / "hub" / "hub" / "profiles" / "component_assist.yaml"
        assert p.exists(), "profile YAML absent"

    def test_profile_has_correct_id(self):
        import yaml
        p = REPO_ROOT / "hub" / "hub" / "profiles" / "component_assist.yaml"
        data = yaml.safe_load(p.read_text(encoding="utf-8"))
        assert data["id"] == "component_assist"
        assert data["profile_version"] == "1.0"

    def test_profile_native_tools_whitelist(self):
        import yaml
        p = REPO_ROOT / "hub" / "hub" / "profiles" / "component_assist.yaml"
        data = yaml.safe_load(p.read_text(encoding="utf-8"))
        allowed = data["native_tools"]["allowed"]
        # Les 5 tools cmp_* POC prioritaires (S3+S4+S5+S1)
        for t in ["cmp_get_context", "cmp_set_tooltip", "cmp_set_zone",
                  "cmp_set_source_citation", "cmp_add_layer"]:
            assert t in allowed, f"tool {t} absent whitelist"

    def test_profile_no_workspace_tools(self):
        """Component-only : pas de smart_load/run_recipe/execute_python."""
        import yaml
        p = REPO_ROOT / "hub" / "hub" / "profiles" / "component_assist.yaml"
        data = yaml.safe_load(p.read_text(encoding="utf-8"))
        mcp = data.get("mcp_tools", {}).get("allowed", [])
        assert mcp == [], "mcp_tools workspace doivent etre vides pour component-only"

    def test_profile_system_prompt_cid_aware(self):
        import yaml
        p = REPO_ROOT / "hub" / "hub" / "profiles" / "component_assist.yaml"
        data = yaml.safe_load(p.read_text(encoding="utf-8"))
        prompt = data["system_prompt"]
        # System prompt mentionne bien le scope composant
        assert "composant" in prompt.lower()
        assert "cid" in prompt.lower()
        # Vocabulaire metier Marie
        assert "marie" in prompt.lower() or "cerema" in prompt.lower()


class TestNativeToolsCmp:
    """Les 5 tools cmp_* sont importables + async."""

    def test_cmp_tools_importable(self):
        from agent.native_tools_v2 import (
            cmp_get_context, cmp_set_tooltip, cmp_set_zone,
            cmp_set_source_citation, cmp_add_layer,
        )
        assert all([
            cmp_get_context, cmp_set_tooltip, cmp_set_zone,
            cmp_set_source_citation, cmp_add_layer,
        ])

    def test_cmp_tools_are_async(self):
        import inspect
        from agent.native_tools_v2 import (
            cmp_get_context, cmp_set_tooltip, cmp_set_zone,
            cmp_set_source_citation, cmp_add_layer,
        )
        for t in [cmp_get_context, cmp_set_tooltip, cmp_set_zone,
                  cmp_set_source_citation, cmp_add_layer]:
            assert inspect.iscoroutinefunction(t)

    def test_cmp_set_tooltip_signature(self):
        import inspect
        from agent.native_tools_v2 import cmp_set_tooltip
        sig = inspect.signature(cmp_set_tooltip)
        for p in ["sid", "cid", "layer_id_ref", "field", "version_num_source"]:
            assert p in sig.parameters

    def test_cmp_set_zone_supports_3_kinds(self):
        import inspect
        from agent.native_tools_v2 import cmp_set_zone
        src = inspect.getsource(cmp_set_zone)
        for kind in ['commune', 'manual', 'study']:
            assert kind in src

    def test_cmp_add_layer_idempotent(self):
        """cmp_add_layer met a jour si layer_id_ref existe deja."""
        import inspect
        from agent.native_tools_v2 import cmp_add_layer
        src = inspect.getsource(cmp_add_layer)
        # Pattern : check layer_id_ref existant + update au lieu de duplicate
        assert "layer_id_ref" in src
        # Idempotence via boucle
        assert "for" in src

    def test_4_mutants_in_frozenset(self):
        """Les 4 tools cmp_* mutants doivent invalider cache L2 artifacts."""
        from agent.native_tools_v2 import NATIVE_TOOLS_V2_MUTATING
        for m in ["cmp_set_tooltip", "cmp_set_zone",
                  "cmp_set_source_citation", "cmp_add_layer"]:
            assert m in NATIVE_TOOLS_V2_MUTATING, f"{m} absent MUTATING"

    def test_cmp_get_context_not_mutating(self):
        """Read-only tool doit NE PAS etre dans MUTATING."""
        from agent.native_tools_v2 import NATIVE_TOOLS_V2_MUTATING
        assert "cmp_get_context" not in NATIVE_TOOLS_V2_MUTATING


class TestEndpointsAssist:
    """Endpoints hub /assist/suggestions + /assist/action."""

    def test_suggestions_endpoint_registered(self):
        from hub.main import app
        routes = [r.path for r in app.routes if hasattr(r, "path")]
        assert "/studies/{sid}/components/{cid}/assist/suggestions" in routes

    def test_action_endpoint_registered(self):
        from hub.main import app
        routes = [r.path for r in app.routes if hasattr(r, "path")]
        assert "/studies/{sid}/components/{cid}/assist/action" in routes

    def test_suggestions_endpoint_hardcoded_by_kind(self):
        """POC : 5 suggestions hardcoded pour interactive_map."""
        import inspect
        from hub.main import component_assist_suggestions_endpoint
        src = inspect.getsource(component_assist_suggestions_endpoint)
        # 5 suggestions hardcoded scenarios test Marie
        for sug_id in ["add_layer_tri", "center_marseille_4e",
                       "tooltip_adresse", "cite_tri_dgpr", "basemap_ign"]:
            assert sug_id in src, f"suggestion {sug_id} absente"

    def test_action_endpoint_whitelist_strict(self):
        """Action endpoint refuse tools hors whitelist cmp_*."""
        import inspect
        from hub.main import component_assist_action_endpoint
        src = inspect.getsource(component_assist_action_endpoint)
        assert "ALLOWED_TOOLS" in src
        assert "cmp_set_tooltip" in src
        # Reject 400 si tool inconnu
        assert "400" in src or "HTTPException" in src

    def test_action_endpoint_enforces_sid_cid_from_url(self):
        """Enforce scope : sid + cid pris de l'URL, pas du payload.

        V1.14.1 hotfix : refacto endpoint pour appel direct modules hub locaux
        (comp_mod.insert_component) au lieu de tools cmp_* qui font HTTP self-call.
        sid + cid sont forces dans new_manifest avant Component.model_validate.
        """
        import inspect
        from hub.main import component_assist_action_endpoint
        src = inspect.getsource(component_assist_action_endpoint)
        # Pattern V1.14.1 : new_manifest sid/id forces depuis URL sid/cid
        assert 'new_manifest' in src
        assert '"sid": sid' in src or "'sid': sid" in src
        assert '"id": cid' in src or "'id': cid" in src


class TestAssistantCardTsx:
    """Frontend AssistantCard.tsx cree et integre au form."""

    def test_assistant_card_file_exists(self):
        content = _read("forms/InteractiveMap/AssistantCard.tsx")
        if content is None:
            pytest.skip("blocknote-editor absent")
        assert "export function AssistantCard" in content

    def test_assistant_card_fetches_suggestions_endpoint(self):
        content = _read("forms/InteractiveMap/AssistantCard.tsx")
        if content is None:
            pytest.skip()
        assert "/assist/suggestions" in content

    def test_assistant_card_posts_action_endpoint(self):
        content = _read("forms/InteractiveMap/AssistantCard.tsx")
        if content is None:
            pytest.skip()
        assert "/assist/action" in content
        assert "'POST'" in content or '"POST"' in content

    def test_assistant_card_exposes_global_bridge(self):
        """window.__openAssistantWithPrompt bridge F8 quick win."""
        content = _read("forms/InteractiveMap/AssistantCard.tsx")
        if content is None:
            pytest.skip()
        assert "__openAssistantWithPrompt" in content

    def test_assistant_card_handles_409_conflict(self):
        """OCC conflict management."""
        content = _read("forms/InteractiveMap/AssistantCard.tsx")
        if content is None:
            pytest.skip()
        assert "409" in content
        assert "Conflit" in content or "conflit" in content

    def test_assistant_card_abort_controller(self):
        """Race condition prevention."""
        content = _read("forms/InteractiveMap/AssistantCard.tsx")
        if content is None:
            pytest.skip()
        assert "AbortController" in content

    def test_interactive_map_form_uses_assistant_card(self):
        """AssistantCard integre en HAUT du drawer (Notion-like)."""
        content = _read("forms/InteractiveMapForm.tsx")
        if content is None:
            pytest.skip()
        assert "import { AssistantCard" in content
        assert "<AssistantCard" in content


class TestSprint25_Coherence:
    def test_no_regression_imports(self):
        from hub.main import (
            component_assist_suggestions_endpoint,
            component_assist_action_endpoint,
            update_component_endpoint,
        )
        from agent.native_tools_v2 import (
            cmp_get_context, cmp_set_tooltip, cmp_set_zone,
            cmp_set_source_citation, cmp_add_layer,
        )
        assert all([
            component_assist_suggestions_endpoint,
            component_assist_action_endpoint,
            update_component_endpoint,
            cmp_get_context, cmp_set_tooltip, cmp_set_zone,
            cmp_set_source_citation, cmp_add_layer,
        ])
