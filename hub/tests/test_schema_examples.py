"""
Test contract-book agent MCP — Sprint V0.2 Chantier 2-impl (2026-07-10).

Verifie que les 9 exemples canoniques SceneManifest V0.3.1 vendorises
depuis Passerelle sont correctement charges par describe_entity_schema
et que le use_case pattern retourne le bon exemple.

Reference : Passerelle/sdk/js/geo-components/docs/AGENT-CONTRACT-BOOK.md
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from hub.schema_introspect import (
    _load_canonical_example,
    _USE_CASE_TO_FILE,
    describe_entity_schema,
)


_EXAMPLES_DIR = Path(__file__).parent.parent / "hub" / "schema_examples" / "interactive_map"


def test_examples_dir_exists():
    """Le dossier de vendorisation doit exister."""
    assert _EXAMPLES_DIR.is_dir(), (
        f"Dossier {_EXAMPLES_DIR} manquant. Vendorise depuis "
        "Passerelle/sdk/js/geo-components/examples/"
    )


def test_9_use_cases_have_files():
    """Chaque use_case du mapping doit avoir un fichier existant + JSON valide."""
    missing = []
    invalid = []
    for use_case, filename in _USE_CASE_TO_FILE.items():
        path = _EXAMPLES_DIR / filename
        if not path.exists():
            missing.append((use_case, filename))
            continue
        try:
            with path.open(encoding="utf-8") as f:
                data = json.load(f)
        except Exception as e:
            invalid.append((use_case, filename, str(e)))
            continue
        # Verification structure V0.3.1 basique
        assert "manifest_version" in data, f"{filename}: manifest_version manquant"
        assert data["manifest_version"] == "0.3.1", (
            f"{filename}: version {data['manifest_version']} != 0.3.1"
        )
        assert "layers" in data, f"{filename}: layers manquant"
        assert isinstance(data["layers"], list), f"{filename}: layers pas liste"

    assert not missing, f"Fichiers manquants : {missing}"
    assert not invalid, f"Fichiers invalides : {invalid}"


def test_load_canonical_example_diagnostic_temporel():
    """Cas concret : diagnostic_temporel doit charger 2 layers + qgis-sspcloud producer."""
    ex = _load_canonical_example("interactive_map", "diagnostic_temporel")
    assert ex is not None
    assert ex["manifest_version"] == "0.3.1"
    assert len(ex["layers"]) == 2  # batiments + arrondissement
    assert ex["provenance"]["producer"] == "qgis-sspcloud"


def test_load_canonical_example_unknown_use_case_fallback_minimal():
    """use_case inconnu -> minimal.json (fallback graceful)."""
    ex = _load_canonical_example("interactive_map", "pattern_inexistant")
    assert ex is not None
    assert ex["manifest_version"] == "0.3.1"
    # minimal.json doit avoir title "Carte minimale"
    assert "minimal" in ex.get("title", "").lower() or ex.get("title")


def test_load_canonical_example_non_interactive_map_returns_none():
    """Kind autre que interactive_map n'a pas d'exemple canonique."""
    ex = _load_canonical_example("narrative_text", "diagnostic_temporel")
    assert ex is None


def test_describe_entity_schema_returns_use_cases_available():
    """describe_entity_schema pour kind=interactive_map liste les 9 use_cases."""
    res = describe_entity_schema("component", "interactive_map")
    assert "use_cases_available" in res
    assert len(res["use_cases_available"]) == 9
    assert "diagnostic_temporel" in res["use_cases_available"]
    assert "maquette_3d" in res["use_cases_available"]


def test_describe_entity_schema_use_case_returns_canonical_in_params():
    """use_case='diagnostic_temporel' -> example.params contient le manifest V0.3.1."""
    res = describe_entity_schema(
        "component", "interactive_map", use_case="diagnostic_temporel",
    )
    assert res["filtered_use_case"] == "diagnostic_temporel"
    params = res["example"]["params"]
    assert params["manifest_version"] == "0.3.1"
    assert len(params["layers"]) == 2


def test_describe_entity_schema_assembly_no_use_cases_available():
    """Kind assembly ne liste PAS use_cases_available (pas concerne)."""
    res = describe_entity_schema("assembly")
    assert "use_cases_available" not in res


def test_describe_entity_schema_backward_compat_minimal_fallback():
    """Sans use_case, kind=interactive_map utilise l'exemple canonique 'minimal'."""
    res = describe_entity_schema("component", "interactive_map")
    params = res["example"]["params"]
    # minimal.json est le fallback par defaut
    assert params.get("manifest_version") == "0.3.1"
