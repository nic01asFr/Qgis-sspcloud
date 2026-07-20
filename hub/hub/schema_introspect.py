"""
hub.schema_introspect — Tools méta-cognitifs P0 pour l'agent IA.

Sprint Composants Phase 2 (2026-06-25). Permet à l'agent IA de :
1. `describe_entity_schema(entity_type, kind=None)` — JSON Schema + exemples
2. `validate_manifest(entity_type, payload)` — dry-run validation Pydantic
3. `list_entity_kinds(entity_type)` — enum stable (anti-hallucination)

Sources :
- Pydantic V0.1 models : `hub/hub/models/`
- Capitalisé : `~/.wikichat/knowledge/qgis-sspcloud-composants-axis.md` §8 (D5 agent IA)
- Verdict évaluateur D : "manque P0 pour 'parfait' — describe_entity_schema +
  validate_manifest + list_entity_kinds + wrapper L2 injection"
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any, get_args

from pydantic import ValidationError

from hub.models import (
    Assembly, AssemblyKind, AssemblyLayout, AssemblySection, AssemblyFooter,
    AuditChain, Classification, Component, ComponentKind, ComponentRendering,
    ComponentSource, ConfidenceFactors, LLMProvenance, ComponentProvenance,
)

log = logging.getLogger(__name__)


# Sprint V0.2 Chantier 2-impl (2026-07-10) : contract-book agent MCP.
# Les 9 exemples canoniques SceneManifest V0.3.1 sont vendorises depuis
# Passerelle/sdk/js/geo-components/examples/ (source de verite npm publie).
# Le path de verite est hub/hub/schema_examples/interactive_map/.
_SCHEMA_EXAMPLES_DIR = Path(__file__).parent / "schema_examples"

_USE_CASE_TO_FILE = {
    "diagnostic_temporel":     "diagnostic-temporel.json",
    "corpus_documentaire":     "corpus-documentaire.json",
    "choropleth_demographique": "choropleth-demographique.json",
    "heatmap_rag":              "heatmap-rag.json",
    "timeline_overlay_simple": "timeline-overlay-simple.json",
    "multi_layers_narrative": "multi-layers-narrative.json",
    "maquette_3d":            "maquette-3d.json",
    "validation_terrain":     "validation-terrain-zebra.json",
    "minimal":                 "minimal.json",
}


def _load_canonical_example(kind: str, use_case: str | None) -> dict | None:
    """Charge un exemple canonique du contract-book V0.3.1 (Sprint V0.2 C2-impl).

    Retourne None si le kind n'a pas d'exemple canonique ou si le fichier
    est introuvable (le caller applique un fallback minimal).
    """
    if kind != "interactive_map":
        return None
    filename = _USE_CASE_TO_FILE.get(use_case or "minimal", "minimal.json")
    path = _SCHEMA_EXAMPLES_DIR / "interactive_map" / filename
    if not path.exists():
        log.warning(
            "Canonical example not found: %s (use_case=%s, kind=%s)",
            path, use_case, kind
        )
        return None
    try:
        with path.open("r", encoding="utf-8") as f:
            return json.load(f)
    except Exception as e:
        log.warning("Failed to load canonical example %s: %s", path, e)
        return None


# Registry entity_type → (Pydantic class, kind_literal_or_None)
ENTITY_REGISTRY: dict[str, tuple[type, Any]] = {
    "component":           (Component, ComponentKind),
    "assembly":            (Assembly, AssemblyKind),
    "audit_chain":         (AuditChain, None),
    "component_source":    (ComponentSource, None),
    "component_rendering": (ComponentRendering, None),
    "component_provenance": (ComponentProvenance, None),
    "llm_provenance":      (LLMProvenance, None),
    "confidence_factors":  (ConfidenceFactors, None),
    "assembly_layout":     (AssemblyLayout, None),
    "assembly_section":    (AssemblySection, None),
    "assembly_footer":     (AssemblyFooter, None),
    "classification":      (None, Classification),  # juste enum
}


def list_entity_kinds(entity_type: str) -> dict[str, Any]:
    """Retourne les `kind` possibles pour un entity_type donné.

    Anti-hallucination LLM : l'agent peut interroger les enums stables
    plutôt que d'inventer un kind inexistant.

    Args:
        entity_type: 'component' | 'assembly' | 'classification'

    Returns:
        {entity_type, kinds: list[str], count, descriptions: dict}
    """
    if entity_type not in ENTITY_REGISTRY:
        return {
            "entity_type": entity_type,
            "kinds": [],
            "count": 0,
            "error": f"Unknown entity_type. Available: {sorted(ENTITY_REGISTRY.keys())}",
        }
    _, kind_literal = ENTITY_REGISTRY[entity_type]
    if kind_literal is None:
        return {
            "entity_type": entity_type,
            "kinds": [],
            "count": 0,
            "note": "This entity_type has no 'kind' discriminator",
        }
    # Extract Literal values
    kinds = list(get_args(kind_literal))
    return {
        "entity_type": entity_type,
        "kinds": kinds,
        "count": len(kinds),
        "descriptions": _kind_descriptions(entity_type),
    }


def _kind_descriptions(entity_type: str) -> dict[str, str]:
    """Documentation courte par kind (utile pour le LLM).

    Sprint sec A2-R3 (2026-07-19) : ajout des 5 kinds manquants
    (kpi_grid, heading, quote, separator, timeline) que l'agent ne voyait
    pas dans /schema/component/kinds jusqu'ici.
    """
    if entity_type == "component":
        return {
            "interactive_map":  "Carte 2D + pitch 3D MapLibre — scope project/study/external/geomind",
            "scene_3d":         "Atlas 3D MapLibre + Three.js custom layer (pattern atlas_bati) — GLTF par feature",
            "chart":            "Graphique Chart.js depuis CSV ou Grist table",
            "kpi_badge":        "Indicateur clé HTML statique (valeur + label + source)",
            "legend":           "Légende HTML SVG depuis Scene Manifest subset",
            "narrative_text":   "Paragraphes narratifs Markdown via Marked.js",
            "data_table":       "Tableau filtrable datatables.net depuis CSV / Grist",
            "media_embed":      "URL média HTML5 (image, vidéo, PDF)",
            "iframe_grist":     "iframe vers URL Grist doc (édition collaborative)",
            # Sprint sec A2-R3 (2026-07-19) : Vague E2 (D-QGIS-009) + V1.20.5
            "kpi_grid":         "Bandeau responsive de N KPIs (grille CSS) — palette monochrome ou rainbow, N chiffres clés vs kpi_badge isolé",
            "heading":          "Titre H1-H4 standalone DSFR — remplace la concatenation dans narrative_text",
            "quote":            "Citation / pull-quote DSFR — sources expertes, temoignages, extraits reglementaires",
            "separator":        "Separateur horizontal entre blocks — respire visuellement les storymaps longs",
            "timeline":         "Slider temporel controller (Web Component <geo-timeline>) — pilote une carte via binding declaratif CustomEvent 'geo:bind' {prop:'time'}",
        }
    if entity_type == "assembly":
        return {
            "storymap_narrative_dsfr": "Scroll vertical scrollytelling DSFR — communication grand public",
            "dashboard":               "Grille fixe DSFR — suivi opérationnel interne",
            "sheet_a4":                "Pagination print — rapport officiel PDF",
            "modal_embed":             "Pop-up 1 composant — intégration site tiers",
            "atlas_immersive":         "Plein écran 3D Three.js — visite virtuelle",
        }
    if entity_type == "classification":
        return {
            "public":          "URL publique, indexable, partageable sans contrainte",
            "cerema_internal": "Agents CEREMA authentifiés OIDC SSPCloud (DEFAULT)",
            "restricted":     "Équipe étude + invitations explicites du owner",
            "confidential":   "Owner étude uniquement (brouillons, mémos)",
        }
    return {}


def describe_entity_schema(
    entity_type: str,
    kind: str | None = None,
    use_case: str | None = None,
) -> dict[str, Any]:
    """Retourne JSON Schema Pydantic + un exemple canonique V0.3.1 valide.

    Permet au LLM de connaitre la structure exacte attendue AVANT de
    construire un payload via `create_component` / `create_assembly`,
    et de recevoir un exemple riche adapte a son cas d'usage plutot
    qu'un `{basemap, bbox}` minimal generique.

    Sprint V0.2 Chantier 2-impl (2026-07-10) : pour kind='interactive_map',
    retourne un des 9 exemples canoniques SceneManifest V0.3.1 selon le
    parametre `use_case` (voir _USE_CASE_TO_FILE). Les fichiers sont
    vendorises depuis Passerelle (passerelle-geo-components@dev).

    Args:
        entity_type: 'component' | 'assembly' | 'audit_chain' | ...
        kind: filtre optionnel (ex: 'interactive_map')
        use_case: pour kind='interactive_map' seulement — un des patterns
            metier V0.3.1 : diagnostic_temporel | corpus_documentaire |
            choropleth_demographique | heatmap_rag | timeline_overlay_simple |
            multi_layers_narrative | maquette_3d | validation_terrain |
            minimal. Retourne un exemple canonique riche adapte.

    Returns:
        {entity_type, schema, example, kinds_available, use_cases_available?,
         filtered_kind?, filtered_use_case?}
    """
    if entity_type not in ENTITY_REGISTRY:
        return {
            "entity_type": entity_type,
            "error": (
                f"Unknown entity_type. Available: "
                f"{sorted(ENTITY_REGISTRY.keys())}"
            ),
        }
    cls, kind_literal = ENTITY_REGISTRY[entity_type]
    if cls is None:
        # Cas Classification (Literal pur)
        return list_entity_kinds(entity_type)

    schema = cls.model_json_schema()
    example = _build_example(entity_type, kind, use_case)

    result = {
        "entity_type": entity_type,
        "schema": schema,
        "example": example,
    }
    if kind_literal is not None:
        result["kinds_available"] = list(get_args(kind_literal))
        if kind:
            result["filtered_kind"] = kind
    # Sprint V0.2 C2-impl : lister use_cases dispo pour interactive_map
    if kind == "interactive_map":
        result["use_cases_available"] = sorted(_USE_CASE_TO_FILE.keys())
        if use_case:
            result["filtered_use_case"] = use_case
    return result


def _build_example(
    entity_type: str,
    kind: str | None = None,
    use_case: str | None = None,
) -> dict[str, Any]:
    """Exemple valide d'une instance — aide LLM a comprendre la forme.

    Priorite 1 (Sprint V0.2 C2-impl) : exemple canonique V0.3.1 du
    contract-book vendorise depuis Passerelle si disponible.
    Priorite 2 : fallback minimal historique (backward-compat V0.2 hub).
    """
    if entity_type == "component":
        k = kind or "interactive_map"
        # Priorite 1 : exemple canonique V0.3.1 depuis contract-book
        canonical = _load_canonical_example(k, use_case)
        if canonical is not None:
            # Wrapper Component enveloppant le SceneManifest V0.3.1 canonique
            # dans le champ params. Le manifest est la source de verite ; la
            # partie enveloppe (id, source, provenance) reste indicative.
            return {
                "id": "abc123def456",
                "version": 1,
                "kind": k,
                "title": canonical.get(
                    "title", f"Exemple canonique {use_case or 'minimal'}"
                ),
                "classification": "cerema_internal",
                "source": {
                    "scope": "project",
                    "sid": "d8a0b9718857",
                    "pid": "0780d1d825f3",
                    "scene_hash": "sha256:abc...",
                },
                "params": canonical,
                "rendering": {
                    "runtime": "maplibre",
                    "container_size": "responsive",
                    "theme": "dsfr",
                },
                "provenance": {
                    "created_by": "agent",
                    "tool_calls_made": [],
                },
            }
        # Priorite 2 : fallback minimal historique
        return {
            "id": "abc123def456",
            "version": 1,
            "kind": k,
            "title": f"Exemple {k}",
            "classification": "cerema_internal",
            "source": {
                "scope": "project",
                "sid": "d8a0b9718857",
                "pid": "0780d1d825f3",
                "scene_hash": "sha256:abc...",
            },
            "params": {} if k != "interactive_map" else {
                "basemap": "ign-plan", "bbox": [5.38, 43.29, 5.43, 43.33],
            },
            "rendering": {
                "runtime": "maplibre" if k.startswith("interactive_") else "html",
                "container_size": "responsive",
                "theme": "dsfr",
            },
            "provenance": {
                "created_by": "agent",
                "tool_calls_made": [],
            },
        }
    if entity_type == "assembly":
        k = kind or "storymap_narrative_dsfr"
        return {
            "id": "fff111aaa222",
            "version": 1,
            "sid": "d8a0b9718857",
            "kind": k,
            "title": f"Exemple {k}",
            "audience": "cerema_internal",
            "layout": {
                "type": "scroll_vertical" if "storymap" in k else "grid",
                "sections": [
                    {
                        "kind": "intro",
                        "title": "Contexte",
                        "narrative_md": "## Introduction\n\nTexte narratif…",
                        "components": [],
                    },
                    {
                        "kind": "section",
                        "title": "Carte principale",
                        "components": [{"ref": "abc123def456"}],
                    },
                ],
            },
            "footer": {
                "sources": [
                    {"corpus": "Géorisques", "ref_id": "PPRi-13055",
                     "millesime": "2024", "authority": "MTECT",
                     "licence": "Licence Ouverte 2.0", "url": ""}
                ],
                "disclaimer": "Information non opposable juridiquement.",
                "cerema_mentions_legales": True,
            },
        }
    if entity_type == "audit_chain":
        return {
            "aid": "fff111aaa222",
            "sid": "d8a0b9718857",
            "owner": "nicolaslaval",
            "classification": "cerema_internal",
            "scene_hashes": ["sha256:abc..."],
            "components_refs": ["abc123def456"],
            "recipes_used": ["risque_inondation@v3"],
            "tool_calls_made": [],
            "llm_provenance": [{
                "prompt_hash": "1234567890abcdef",
                "model_id": "qwen3-6-35b-moe@vllm-0.22",
                "tool_calls_count": 5,
            }],
            "sources": [],
            "confidence_score": 0.85,
            "confidence_level": "STRONG",
            "consensus_level": "UNANIMOUS",
            "has_contradictory_claim": False,
        }
    return {}


def validate_manifest(
    entity_type: str, payload: dict[str, Any]
) -> dict[str, Any]:
    """Dry-run validation Pydantic — retourne errors structurées exploitables LLM.

    Permet à l'agent de tester un payload AVANT d'appeler `create_*` qui
    ferait remonter une erreur applicative tardive (perte d'un tour LLM).

    Args:
        entity_type: 'component' | 'assembly' | 'audit_chain' | ...
        payload: dict candidat à valider

    Returns:
        {valid: bool, errors: list[{loc, msg, type, fix_hint}], normalized?: dict}
    """
    if entity_type not in ENTITY_REGISTRY:
        return {
            "valid": False,
            "errors": [{
                "loc": "entity_type",
                "msg": f"Unknown entity_type. Available: {sorted(ENTITY_REGISTRY.keys())}",
                "type": "unknown_entity",
                "fix_hint": "Use one of the listed entity_types.",
            }],
        }
    cls, _ = ENTITY_REGISTRY[entity_type]
    if cls is None:
        return {
            "valid": False,
            "errors": [{
                "loc": "entity_type",
                "msg": f"{entity_type} is an enum, not a validatable model",
                "type": "not_validatable",
                "fix_hint": "Use list_entity_kinds() instead.",
            }],
        }

    try:
        instance = cls.model_validate(payload)
        return {
            "valid": True,
            "errors": [],
            "normalized": instance.model_dump(mode="json"),
        }
    except ValidationError as exc:
        errors = []
        for err in exc.errors():
            loc = ".".join(str(p) for p in err.get("loc", []))
            errors.append({
                "loc": loc or "<root>",
                "msg": err.get("msg", ""),
                "type": err.get("type", "validation_error"),
                "fix_hint": _suggest_fix(err),
            })
        return {"valid": False, "errors": errors}


def _suggest_fix(err: dict[str, Any]) -> str:
    """Suggestion textuelle pour aider le LLM à corriger sa payload."""
    err_type = err.get("type", "")
    loc = err.get("loc", [])

    if err_type == "missing":
        return f"Add the missing required field at '{'.'.join(str(p) for p in loc)}'."
    if err_type in ("string_pattern_mismatch", "value_error"):
        # Probably sid/pid regex
        return (
            "Check the format: sid/pid must be exactly 12 hex chars "
            "lowercase (regex ^[0-9a-f]{12}$). Example: 'd8a0b9718857'."
        )
    if err_type == "literal_error":
        return (
            "Use one of the allowed values. Call list_entity_kinds() "
            "to discover them."
        )
    if err_type.startswith("less_than") or err_type.startswith("greater_than"):
        return f"Check numeric bounds: {err.get('msg', '')}"
    if err_type == "type_error" or "type" in err_type:
        return f"Wrong type: expected {err.get('msg', '?')}."
    return "Refer to the JSON Schema (describe_entity_schema)."


def get_supported_entity_types() -> list[str]:
    """Retourne la liste des entity_types reconnus par ce module."""
    return sorted(ENTITY_REGISTRY.keys())
