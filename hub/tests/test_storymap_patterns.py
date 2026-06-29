"""
Tests Vague E2 Commit 3 (D-QGIS-009 §3) — 6 patterns metier storymap.

Tests :
- 6 patterns presents dans le dict PATTERNS
- list_patterns() retourne metadata light
- describe_pattern(name) retourne recette complete + params_schema + example
- Chaque pattern a au moins 2 components_template
- Chaque pattern a un section_template avec kind in (intro|section|conclusion|appendix)
- describe_pattern lance ValueError sur name inconnu
- Endpoints REST GET /storymap_patterns + /storymap_patterns/{name}
- Native tools agent + dispatch + schemas OpenAI
"""
from __future__ import annotations


VALID_SECTION_KINDS = {"intro", "section", "conclusion", "appendix"}

EXPECTED_PATTERNS = {
    "hero_constat",
    "zoom_territoire",
    "croisement_enjeu",
    "fiche_indicateur",
    "reliability_summary",
    "conclusion_actionnable",
}


class TestStorymapPatternsModule:
    """Module hub.storymap_patterns expose les 6 patterns canoniques."""

    def test_six_patterns_existent(self):
        from hub.storymap_patterns import PATTERNS
        assert set(PATTERNS.keys()) == EXPECTED_PATTERNS

    def test_each_pattern_has_required_fields(self):
        from hub.storymap_patterns import PATTERNS
        required = {"name", "description", "role_narratif", "params_schema",
                   "components_template", "section_template"}
        for name, p in PATTERNS.items():
            missing = required - set(p.keys())
            assert not missing, f"Pattern '{name}' missing : {missing}"

    def test_each_pattern_has_n_components(self):
        from hub.storymap_patterns import PATTERNS
        for name, p in PATTERNS.items():
            n = len(p["components_template"])
            assert 2 <= n <= 6, f"Pattern '{name}' a {n} components (attendu 2-6)"

    def test_each_pattern_section_kind_valide(self):
        from hub.storymap_patterns import PATTERNS
        for name, p in PATTERNS.items():
            k = p["section_template"]["kind"]
            assert k in VALID_SECTION_KINDS, (
                f"Pattern '{name}' section.kind='{k}' invalide "
                f"(attendu : {VALID_SECTION_KINDS})"
            )

    def test_hero_constat_section_kind_intro(self):
        """hero_constat doit etre intro (impact visuel ouverture)."""
        from hub.storymap_patterns import PATTERNS
        assert PATTERNS["hero_constat"]["section_template"]["kind"] == "intro"

    def test_conclusion_actionnable_section_kind_conclusion(self):
        """conclusion_actionnable doit etre conclusion (call-out)."""
        from hub.storymap_patterns import PATTERNS
        assert PATTERNS["conclusion_actionnable"]["section_template"]["kind"] == "conclusion"

    def test_reliability_summary_section_kind_appendix(self):
        """reliability_summary mis en appendix (typo reduite, fin de doc)."""
        from hub.storymap_patterns import PATTERNS
        assert PATTERNS["reliability_summary"]["section_template"]["kind"] == "appendix"


class TestStorymapPatternsAPI:
    """Helpers list_patterns / describe_pattern / get_pattern_names."""

    def test_list_patterns_returns_six(self):
        from hub.storymap_patterns import list_patterns
        result = list_patterns()
        assert set(result.keys()) == EXPECTED_PATTERNS

    def test_list_patterns_each_has_metadata_light(self):
        from hub.storymap_patterns import list_patterns
        for name, meta in list_patterns().items():
            assert "name" in meta
            assert "description" in meta
            assert "role_narratif" in meta
            assert "n_components" in meta
            assert "section_kind" in meta

    def test_describe_pattern_returns_full_recipe(self):
        from hub.storymap_patterns import describe_pattern
        result = describe_pattern("hero_constat")
        assert result["name"] == "hero_constat"
        assert "components_template" in result
        assert "section_template" in result
        assert "params_schema" in result

    def test_describe_unknown_pattern_raises(self):
        from hub.storymap_patterns import describe_pattern
        import pytest
        with pytest.raises(ValueError, match="inconnu"):
            describe_pattern("inexistant")

    def test_get_pattern_names_sorted(self):
        from hub.storymap_patterns import get_pattern_names
        names = get_pattern_names()
        assert names == sorted(names)
        assert set(names) == EXPECTED_PATTERNS


class TestStorymapPatternsEndpoints:
    """Endpoints REST GET /storymap_patterns + /storymap_patterns/{name}."""

    def test_endpoint_list_exists(self):
        from hub.main import app
        routes = [r.path for r in app.routes]
        assert "/storymap_patterns" in routes

    def test_endpoint_describe_exists(self):
        from hub.main import app
        routes = [r.path for r in app.routes]
        assert "/storymap_patterns/{name}" in routes


class TestNativeToolsStorymapPatterns:
    """Native tools agent : list_storymap_patterns + describe_storymap_pattern."""

    def test_native_tool_list_present(self):
        from agent.native_tools_v2 import NATIVE_TOOLS_V2
        assert "list_storymap_patterns" in NATIVE_TOOLS_V2

    def test_native_tool_describe_present(self):
        from agent.native_tools_v2 import NATIVE_TOOLS_V2
        assert "describe_storymap_pattern" in NATIVE_TOOLS_V2

    def test_storymap_patterns_tools_NOT_mutating(self):
        from agent.native_tools_v2 import NATIVE_TOOLS_V2_MUTATING
        # Read-only tools, ne doivent PAS invalider le cache L2 hub artifacts
        assert "list_storymap_patterns" not in NATIVE_TOOLS_V2_MUTATING
        assert "describe_storymap_pattern" not in NATIVE_TOOLS_V2_MUTATING

    def test_openai_schemas_present(self):
        from agent.native_tools_v2 import NATIVE_TOOLS_V2_OPENAI
        names = {s["function"]["name"] for s in NATIVE_TOOLS_V2_OPENAI}
        assert "list_storymap_patterns" in names
        assert "describe_storymap_pattern" in names

    def test_describe_schema_enum_correct(self):
        """Le schema OpenAI describe_storymap_pattern doit avoir l'enum des 6 patterns."""
        from agent.native_tools_v2 import NATIVE_TOOLS_V2_OPENAI
        schema = next(
            s for s in NATIVE_TOOLS_V2_OPENAI
            if s["function"]["name"] == "describe_storymap_pattern"
        )
        enum_vals = schema["function"]["parameters"]["properties"]["name"]["enum"]
        assert set(enum_vals) == EXPECTED_PATTERNS


class TestProfileV15WhitelistPatterns:
    """Profile storymap_creator_v15 doit whitelister les 2 nouveaux tools."""

    def test_v15_whitelist_contains_patterns_tools(self):
        from pathlib import Path
        import yaml
        path = Path(__file__).parent.parent / "hub" / "profiles" / "storymap_creator_v15.yaml"
        with path.open(encoding="utf-8") as f:
            cfg = yaml.safe_load(f)
        # Structure : mcp_tools.allowed contient native_tools_v2 declares
        tools = (cfg.get("mcp_tools") or {}).get("allowed", []) or []
        assert "list_storymap_patterns" in tools
        assert "describe_storymap_pattern" in tools
