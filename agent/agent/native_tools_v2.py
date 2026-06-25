"""
agent.native_tools_v2 — Tools natifs MCP côté agent IA (Sprint Composants Phase 2).

Tools méta-cognitifs P0 verrouillés par 8 verdicts évaluateurs :
1. `describe_entity_schema(entity_type, kind=None)` — JSON Schema Pydantic + exemple
2. `validate_manifest(entity_type, payload)` — dry-run validation Pydantic
3. `list_entity_kinds(entity_type)` — enum stable anti-hallucination

Tools CRUD composants (consommés par l'agent via /chat) :
4. `list_components(sid, kind?)` — composants de l'étude
5. `create_component(sid, manifest)` — crée + écrit PVC + indexe DB
6. `get_component(sid, cid)` — manifest + metadata
7. `get_component_history(sid, cid)` — audit trail INSERT-only

Architecture : ces tools appellent les endpoints REST hub (via HUB_API_KEY
Bearer). Pas d'execute_python — surface contractuelle stable, auditable
via tool_calls_made, et compose proprement avec le wrapper L2 injection
(Passerelle-Archi Option X côté hub /mcp proxy).

Capitalisé : `~/.wikichat/knowledge/qgis-sspcloud-composants-axis.md` §8
"""

from __future__ import annotations

import json
import logging
import os
from typing import Any

import httpx

log = logging.getLogger("agent.native_tools_v2")

_HUB_URL = os.getenv("HUB_URL", "").rstrip("/")
_HUB_API_KEY = os.getenv("HUB_API_KEY", "")


async def _hub_call(
    method: str, path: str, json_body: dict | None = None,
    params: dict | None = None, timeout: float = 30.0,
) -> dict[str, Any]:
    """Helper appel hub authentifié Bearer HUB_API_KEY."""
    if not _HUB_URL or not _HUB_API_KEY:
        return {"error": "HUB_URL ou HUB_API_KEY non configurés côté agent"}
    headers = {"Authorization": f"Bearer {_HUB_API_KEY}"}
    url = f"{_HUB_URL}{path}"
    try:
        async with httpx.AsyncClient(timeout=timeout) as c:
            r = await c.request(method, url, json=json_body, params=params, headers=headers)
        if r.status_code >= 400:
            return {"error": f"HTTP {r.status_code}", "detail": r.text[:500]}
        try:
            return r.json() if r.status_code != 204 else {"ok": True}
        except Exception:
            return {"raw": r.text[:500]}
    except Exception as exc:
        return {"error": "network", "detail": str(exc)}


# ── Tools méta-cognitifs P0 ───────────────────────────────────────────────────

async def describe_entity_schema(
    entity_type: str, kind: str | None = None,
) -> dict[str, Any]:
    """Décrit le JSON Schema Pydantic + 1 exemple minimal valide.

    Anti-hallucination LLM : l'agent connaît la structure exacte attendue
    AVANT de construire une payload pour create_component / create_assembly.

    entity_type ∈ {component, assembly, audit_chain, component_source,
                   component_rendering, classification, ...}
    kind : filtre optionnel (ex: 'interactive_map')
    """
    params = {"kind": kind} if kind else None
    return await _hub_call("GET", f"/schema/{entity_type}", params=params)


async def list_entity_kinds(entity_type: str) -> dict[str, Any]:
    """Liste les `kind` possibles pour un entity_type (enum stable).

    Anti-hallucination : l'agent ne peut pas inventer un kind inexistant.

    entity_type ∈ {component, assembly, classification}
    """
    return await _hub_call("GET", f"/schema/{entity_type}/kinds")


async def validate_manifest(
    entity_type: str, payload: dict[str, Any],
) -> dict[str, Any]:
    """Dry-run validation Pydantic. Retourne erreurs structurées
    exploitables LLM (loc, msg, type, fix_hint).

    Permet à l'agent de tester sa payload AVANT le create_* — économise
    un tour LLM en cas d'erreur applicative tardive.
    """
    return await _hub_call(
        "POST", f"/schema/{entity_type}/validate", json_body=payload,
    )


# ── Tools CRUD composants ─────────────────────────────────────────────────────

async def list_components(
    sid: str, kind: str | None = None,
) -> dict[str, Any]:
    """Liste les composants de l'étude (latest version par cid).

    sid : 12 hex étude id
    kind : filtre optionnel par kind composant
    """
    params = {"kind": kind} if kind else None
    return await _hub_call(
        "GET", f"/studies/{sid}/components", params=params,
    )


async def create_component(
    sid: str, manifest: dict[str, Any],
) -> dict[str, Any]:
    """Crée un nouveau composant.

    sid : 12 hex étude id
    manifest : Component Pydantic V0.1 payload. L'id sera auto-généré
               si absent. Doit contenir au minimum :
               - kind (ex: 'interactive_map')
               - title
               - source (ex: {scope: 'project', sid, pid, scene_hash})
               - rendering (ex: {runtime: 'maplibre', container_size: 'responsive'})

    Recommandation : appeler validate_manifest() AVANT pour éviter les
    erreurs tardives.

    Retourne {id, rowid, kind, title, classification, manifest_url, render_url}.
    """
    return await _hub_call(
        "POST", f"/studies/{sid}/components", json_body=manifest,
    )


async def get_component(sid: str, cid: str) -> dict[str, Any]:
    """Retourne manifest + métadonnées DB du composant."""
    return await _hub_call("GET", f"/studies/{sid}/components/{cid}")


async def get_component_history(sid: str, cid: str) -> dict[str, Any]:
    """Historique des versions (audit trail INSERT-only)."""
    return await _hub_call("GET", f"/studies/{sid}/components/{cid}/history")


# ── Catalogue tools natifs (pour exposition MCP côté agent) ───────────────────

NATIVE_TOOLS_V2 = {
    # Méta-cognition P0
    "describe_entity_schema": {
        "fn": describe_entity_schema,
        "description": (
            "Retourne le JSON Schema Pydantic d'une entité (component, assembly, "
            "audit_chain, classification, ...) + un exemple minimal valide. "
            "ANTI-HALLUCINATION : appelle ce tool AVANT de construire un payload "
            "pour create_component / create_assembly."
        ),
        "params": {
            "entity_type": "str (component|assembly|audit_chain|...)",
            "kind": "str optionnel (ex: 'interactive_map')",
        },
    },
    "list_entity_kinds": {
        "fn": list_entity_kinds,
        "description": (
            "Liste les `kind` autorisés pour un entity_type (enum stable). "
            "Ex: component → [interactive_map, scene_3d, chart, kpi_badge, ...]. "
            "ANTI-HALLUCINATION : utilise cette liste, n'invente pas de kind."
        ),
        "params": {"entity_type": "str (component|assembly|classification)"},
    },
    "validate_manifest": {
        "fn": validate_manifest,
        "description": (
            "Dry-run Pydantic. Retourne {valid: bool, errors: [{loc, msg, fix_hint}]}. "
            "ÉCONOMIE TOUR LLM : valide ta payload AVANT create_*, corrige selon "
            "fix_hint, re-valide jusqu'à valid=true."
        ),
        "params": {
            "entity_type": "str",
            "payload": "dict (Component / Assembly / ... manifest candidat)",
        },
    },

    # CRUD composants
    "list_components": {
        "fn": list_components,
        "description": (
            "Liste les composants d'une étude (latest version par cid)."
        ),
        "params": {
            "sid": "str (12 hex étude id)",
            "kind": "str optionnel (filtre)",
        },
    },
    "create_component": {
        "fn": create_component,
        "description": (
            "Crée un composant rattaché à l'étude. Manifest validé Pydantic V0.1 "
            "côté hub. id auto-généré si absent. RECOMMANDATION : appelle "
            "validate_manifest() d'abord."
        ),
        "params": {
            "sid": "str étude id",
            "manifest": "dict Component (kind, title, source, rendering, params?)",
        },
    },
    "get_component": {
        "fn": get_component,
        "description": "Retourne le manifest + metadata DB du composant.",
        "params": {"sid": "str", "cid": "str (12 hex composant id)"},
    },
    "get_component_history": {
        "fn": get_component_history,
        "description": (
            "Historique des versions du composant (audit trail INSERT-only)."
        ),
        "params": {"sid": "str", "cid": "str"},
    },
}
