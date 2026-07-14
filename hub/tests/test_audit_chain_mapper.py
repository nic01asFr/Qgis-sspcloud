"""Tests Sprint V0.4.2 Chantier C : audit_chain_mapper.

Verifie que le mapper `provenance -> AuditChain` :
  - Extrait correctement les sources Strate-strict
  - Skippe silencieusement les sources malformees (fail-soft)
  - Construit un LLMProvenance UNIQUEMENT si mode polished (LLM utilise)
  - Extrait le slug de la recipe utilisee
  - Ne fabrique aucun tool_call si treatments_lines vide (audit honnete)
  - Retourne un AuditChain avec integrity_hash calcule
"""
from __future__ import annotations

import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from hub.recipes_web.audit_chain_mapper import (  # noqa: E402
    _extract_llm_provenance,
    _extract_recipes_used,
    _extract_sources,
    _extract_tool_calls,
    build_audit_chain_from_provenance,
)


def test_extract_sources_valid():
    provenance = {
        "sources": [
            {
                "corpus": "BD TOPO IGN",
                "millesime": "2024",
                "authority": "IGN",
                "licence": "Etalab-2.0",
                "ref_id": None,
                "url": "https://geoservices.ign.fr",
                "statut": "verifie",
            },
        ],
    }
    sources = _extract_sources(provenance)
    assert len(sources) == 1
    assert sources[0].corpus == "BD TOPO IGN"
    assert sources[0].millesime == "2024"


def test_extract_sources_skips_incomplete():
    """Sources sans corpus ou millesime -> skippees (mieux que faux positif)."""
    provenance = {
        "sources": [
            {"corpus": "IGN", "millesime": ""},  # millesime vide
            {"millesime": "2024"},               # corpus manquant
            {"corpus": "OK", "millesime": "2023", "authority": "auth"},
        ],
    }
    sources = _extract_sources(provenance)
    assert len(sources) == 1
    assert sources[0].corpus == "OK"


def test_extract_sources_empty_provenance():
    assert _extract_sources({}) == []


def test_extract_recipes_used():
    provenance = {"recipe_used": {"slug": "diagnostic_parc_bati", "version": 1}}
    assert _extract_recipes_used(provenance) == ["diagnostic_parc_bati"]


def test_extract_recipes_used_absent():
    assert _extract_recipes_used({}) == []


def test_extract_llm_provenance_polished_mode():
    """Mode polished : LLM utilise -> 1 entree LLMProvenance."""
    provenance = {
        "polish": {
            "polish_llm_provenance": {
                "model": "qwen3-6-35b-moe",
                "blocks_polished": 3,
                "blocks_failed": 0,
                "duration_ms_total": 12500,
            },
        },
    }
    llm_prov = _extract_llm_provenance(provenance)
    assert len(llm_prov) == 1
    assert llm_prov[0].model_id == "qwen3-6-35b-moe"
    assert llm_prov[0].tool_calls_count == 3
    # prompt_hash est un SHA256 tronque de 16 chars.
    assert len(llm_prov[0].prompt_hash) == 16


def test_extract_llm_provenance_pure_mode():
    """Mode pure : aucun LLM -> liste vide."""
    provenance = {"recipe_used": {"slug": "x"}}
    assert _extract_llm_provenance(provenance) == []


def test_extract_tool_calls_from_treatments_lines():
    lines = [
        {
            "ts": "2026-07-14T10:00:00Z",
            "kind": "processing",
            "tool": "native:buffer",
            "ok": True,
            "duration_ms": 250,
            "n_features_out": 14270,
        },
        {
            "ts": "2026-07-14T10:00:01Z",
            "kind": "python",
            "tool": "execute_python",
            "ok": False,  # skippe (ok=False)
        },
    ]
    calls = _extract_tool_calls(lines)
    assert len(calls) == 1
    assert calls[0]["tool"] == "native:buffer"
    # Verifier absence des champs PII potentiels (params, inputs, outputs).
    assert "params" not in calls[0]
    assert "inputs" not in calls[0]


def test_extract_tool_calls_empty_when_no_treatments():
    """Aucun treatments.jsonl fourni -> liste vide (pas de fabrication)."""
    assert _extract_tool_calls(None) == []
    assert _extract_tool_calls([]) == []


def test_build_audit_chain_produces_integrity_hash():
    provenance = {
        "recipe_used": {"slug": "test_recipe"},
        "sources": [
            {"corpus": "IGN", "millesime": "2024", "authority": "IGN"},
        ],
    }
    audit = build_audit_chain_from_provenance(
        aid="a" * 12,
        sid="b" * 12,
        owner="test-user",
        provenance=provenance,
        components_refs=["c" * 12, "d" * 12],
    )
    assert audit.aid == "a" * 12
    assert audit.sid == "b" * 12
    assert audit.owner == "test-user"
    assert audit.recipes_used == ["test_recipe"]
    assert len(audit.sources) == 1
    assert audit.components_refs == ["c" * 12, "d" * 12]
    assert len(audit.integrity_hash) > 0  # SHA256 hex


def test_build_audit_chain_polished_has_llm_provenance():
    provenance = {
        "recipe_used": {"slug": "polished_recipe"},
        "polish": {
            "polish_llm_provenance": {
                "model": "qwen3",
                "blocks_polished": 2,
            },
        },
    }
    audit = build_audit_chain_from_provenance(
        aid="a" * 12, sid="b" * 12, owner="test-user",
        provenance=provenance, components_refs=[],
    )
    assert len(audit.llm_provenance) == 1
    assert audit.llm_provenance[0].model_id == "qwen3"


def test_build_audit_chain_no_treatments_no_tool_calls():
    """Sans treatments.jsonl fourni : tool_calls_made vide (audit honnete)."""
    audit = build_audit_chain_from_provenance(
        aid="a" * 12, sid="b" * 12, owner="test-user",
        provenance={"recipe_used": {"slug": "r"}}, components_refs=[],
    )
    assert audit.tool_calls_made == []


def test_build_audit_chain_with_treatments_populates_tool_calls():
    treatments = [
        {"ts": "T1", "kind": "processing", "tool": "native:buffer", "ok": True},
    ]
    audit = build_audit_chain_from_provenance(
        aid="a" * 12, sid="b" * 12, owner="test-user",
        provenance={"recipe_used": {"slug": "r"}},
        components_refs=[],
        treatments_lines=treatments,
    )
    assert len(audit.tool_calls_made) == 1
    assert audit.tool_calls_made[0]["tool"] == "native:buffer"
