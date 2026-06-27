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


# ── Tools CRUD assemblages (Sprint Composants Phase 3) ────────────────────────

async def list_assemblies(
    sid: str, kind: str | None = None,
) -> dict[str, Any]:
    """Liste les assemblages de l'étude (latest version par aid)."""
    params = {"kind": kind} if kind else None
    return await _hub_call("GET", f"/studies/{sid}/assemblies", params=params)


async def create_assembly(
    sid: str, manifest: dict[str, Any],
) -> dict[str, Any]:
    """Crée un nouvel assemblage rattaché à l'étude.

    sid : 12 hex étude id (force le scope, override payload.sid si présent)
    manifest : Assembly Pydantic V0.1 payload :
      - kind : 'storymap_narrative_dsfr' | 'dashboard' | 'sheet_a4' |
               'modal_embed' | 'atlas_immersive' (Sprint 3 livre seulement
               'storymap_narrative_dsfr', autres = Sprint 4)
      - title : str
      - audience : 'public' | 'cerema_internal' (DEFAULT) | 'restricted' | 'confidential'
      - layout : {type, sections:[{kind, title?, narrative_md?, components:[{ref}, {inline}]}]}
      - footer : {sources, audit_trail_ref, disclaimer, cerema_mentions_legales}

    Recommandation : appeler validate_manifest('assembly', payload) AVANT.

    Retourne {id, rowid, kind, title, audience, manifest_url, render_url, publish_url}.
    """
    return await _hub_call(
        "POST", f"/studies/{sid}/assemblies", json_body=manifest,
    )


async def get_assembly(sid: str, aid: str) -> dict[str, Any]:
    """Retourne manifest + metadata DB de l'assemblage."""
    return await _hub_call("GET", f"/studies/{sid}/assemblies/{aid}")


async def render_assembly(sid: str, aid: str) -> dict[str, Any]:
    """Rendu HTML preview (recalculé à chaque appel).

    Retourne {html: '...'} ou {error, detail}.
    L'HTML peut être volumineux — retourné comme string.
    """
    return await _hub_call("GET", f"/studies/{sid}/assemblies/{aid}/render")


async def publish_assembly(
    sid: str, aid: str, audience: str | None = None,
) -> dict[str, Any]:
    """Publie l'assemblage sur S3 + calcule audit_chain transverse.

    OBLIGATOIRE pour avoir une URL publique partageable. Le hub :
    1. Recalcule l'audit_chain (scene_hashes + components_refs + recipes_used)
    2. Génère SHA256 signed_hash canonique anti-tamper
    3. Rend l'HTML via template Jinja2 (storymap_dsfr.html.j2)
    4. Push S3 via s3_publication.publish()
    5. Update assemblies_index.published_url + audit_chain_json

    Retourne {id, published, published_url, audit_chain: {signed_hash,
              components_refs, scene_hashes, recipes_used}}.
    """
    body = {}
    if audience:
        body["audience"] = audience
    return await _hub_call(
        "POST", f"/studies/{sid}/assemblies/{aid}/publish", json_body=body or None,
    )


async def get_assembly_history(sid: str, aid: str) -> dict[str, Any]:
    """Historique des versions (audit trail INSERT-only)."""
    return await _hub_call("GET", f"/studies/{sid}/assemblies/{aid}/history")


# ── Sprint Composants Phase 3c : meta-agent analyseur recipes ────────────────


async def _fetch_recipe_yaml(slug: str, source: str = "auto") -> tuple[str, str, str]:
    """Récupère le contenu YAML brut d'une recipe + son hash + source effective.

    Retourne (content, content_hash, effective_source).
    Si recipe introuvable, raise RuntimeError.

    Pattern :
    - source="auto" : essaie user d'abord (/desk/recipes/{slug}), fallback system
    - source="user" : recipes user-scoped (PVC user)
    - source="system" : recipes système BigQgisMCP (/app/recipes via run_recipe)
    """
    import hashlib

    if source in ("auto", "user"):
        # Try user recipe via hub endpoint /desk/recipes/{slug}
        r = await _hub_call("GET", f"/desk/recipes/{slug}")
        if "error" not in r and r.get("content"):
            content = r["content"]
            return content, hashlib.sha256(content.encode("utf-8")).hexdigest(), "user"
        if source == "user":
            raise RuntimeError(f"User recipe '{slug}' introuvable")

    if source in ("auto", "system"):
        # Try system recipe via BigQgisMCP MCP tool list_recipes
        # Pattern : appel /mcp tools/call avec name="get_recipe", arguments={"id": slug}
        if not _HUB_URL or not _HUB_API_KEY:
            raise RuntimeError("HUB_URL/HUB_API_KEY non configurés pour MCP call")
        try:
            async with httpx.AsyncClient(timeout=10) as c:
                resp = await c.post(
                    f"{_HUB_URL}/mcp",
                    headers={
                        "Authorization": f"Bearer {_HUB_API_KEY}",
                        "Content-Type": "application/json",
                    },
                    json={
                        "jsonrpc": "2.0",
                        "id": "fetch-recipe",
                        "method": "tools/call",
                        "params": {
                            "name": "get_recipe",
                            "arguments": {"id": slug},
                        },
                    },
                )
                data = resp.json()
                # MCP retourne {"result": {"content": [{"type": "text", "text": "..."}]}}
                content_list = (data.get("result") or {}).get("content") or []
                if content_list and isinstance(content_list, list):
                    text = content_list[0].get("text", "")
                    if text and not text.startswith("Recipe not found"):
                        return text, hashlib.sha256(text.encode("utf-8")).hexdigest(), "system"
        except Exception as exc:
            log.warning("MCP get_recipe('%s') failed: %s", slug, exc)

    raise RuntimeError(f"Recipe '{slug}' introuvable (source={source})")


async def _call_llm_analyzer(
    yaml_content: str, slug: str, source: str, content_hash: str,
) -> dict[str, Any]:
    """Appel LLM avec profile recipe_analyzer pour produire RecipeAnalysis JSON.

    Pattern :
    - Modèle preferred = qwen3-6-35b-moe (function calling natif strict)
    - Fallback gemma4-26b-moe via try/except
    - Charge le profile_analyzer system_prompt depuis /profiles/recipe_analyzer
    - Envoie YAML brut comme user message + meta info (slug, source, hash)
    - Force JSON output strict
    - Parse + valide Pydantic côté caller (POST cache fait validation)

    Si LLM échoue 2 modèles : retourne dict empty_analysis status='failed'.
    """
    import json as _json
    import os
    from datetime import datetime

    llm_base_url = os.getenv("LLM_BASE_URL", "https://llm.lab.sspcloud.fr/api")
    # Resolve LLM key via env / fallback
    api_key = (
        os.getenv("LLM_API_KEY")
        or os.getenv("ONYXIA_LLM_API_KEY")
        or os.getenv("OPENAI_API_KEY")
        or ""
    )

    # Charger system_prompt du profile recipe_analyzer via l'endpoint
    # interne inter-pod (Bearer HUB_API_KEY) qui n'exclut PAS le prompt.
    # Le endpoint /profiles/{id} public filtre agent_system_prompt pour
    # sécurité.
    profile_r = await _hub_call("GET", "/internal/profiles/recipe_analyzer/full")
    if "error" in profile_r:
        return {
            "status": "failed",
            "error_detail": f"profile recipe_analyzer indisponible: {profile_r.get('error')}",
        }
    system_prompt = profile_r.get("agent_system_prompt", "")
    if not system_prompt:
        return {"status": "failed", "error_detail": "profile recipe_analyzer sans agent_system_prompt"}

    user_message = (
        f"Analyse la recipe suivante.\n\n"
        f"slug: {slug}\n"
        f"source: {source}\n"
        f"content_hash: {content_hash}\n"
        f"analyzed_at: {datetime.utcnow().isoformat()}Z\n\n"
        f"=== CONTENU YAML ===\n{yaml_content}\n"
        f"=== FIN ===\n\n"
        f"Produis le JSON RecipeAnalysis strict (rien d'autre)."
    )

    models_to_try = ["qwen3-6-35b-moe", "gemma4-26b-moe"]
    last_error = None
    for model in models_to_try:
        try:
            async with httpx.AsyncClient(timeout=120) as c:
                resp = await c.post(
                    f"{llm_base_url}/chat/completions",
                    headers={
                        "Authorization": f"Bearer {api_key}",
                        "Content-Type": "application/json",
                    },
                    json={
                        "model": model,
                        "messages": [
                            {"role": "system", "content": system_prompt},
                            {"role": "user", "content": user_message},
                        ],
                        "response_format": {"type": "json_object"},
                        "temperature": 0.2,
                        "max_tokens": 4000,
                    },
                )
                if resp.status_code != 200:
                    last_error = f"{model} HTTP {resp.status_code}: {resp.text[:200]}"
                    log.warning("LLM analyzer %s failed: %s", model, last_error)
                    continue
                data = resp.json()
                content = data["choices"][0]["message"]["content"]
                # Parse JSON
                try:
                    analysis_dict = _json.loads(content)
                except _json.JSONDecodeError as je:
                    last_error = f"{model} JSON invalide: {je}"
                    log.warning(last_error)
                    continue
                # Inject analyzer_model dans le dict si manquant (le LLM a tendance à mettre génériquement)
                analysis_dict["analyzer_model"] = model
                analysis_dict["status"] = "success"
                return analysis_dict
        except Exception as exc:
            last_error = f"{model} exception: {exc}"
            log.warning(last_error)
            continue

    return {
        "status": "failed",
        "error_detail": f"Tous modèles ont échoué: {last_error}",
    }


async def analyze_recipe(
    slug: str, source: str = "auto",
) -> dict[str, Any]:
    """Tool natif : retourne RecipeAnalysis (cache HIT ou nouvelle analyse).

    Workflow :
    1. Récupère YAML brut de la recipe + content_hash
    2. Cache lookup hub GET /schema/recipe/{slug}/analysis
    3. Si HIT (même content_hash) : retourne immédiat
    4. Si MISS : appel LLM via _call_llm_analyzer (qwen3 + fallback gemma4)
    5. POST cache hub → DB + PVC persistance
    6. Retourne analysis dict

    L'agent principal (Marie persona) consomme ce dict :
    - params_analysis pour la discipline plan-puis-execute
    - quality_checks pour informer des warnings éventuels
    - overall_score pour pondérer la confiance
    """
    # 1. Récupérer YAML + hash
    try:
        yaml_content, content_hash, effective_source = await _fetch_recipe_yaml(slug, source)
    except Exception as exc:
        return {"error": "recipe_not_found", "detail": str(exc), "slug": slug}

    # 2. Cache lookup
    cache_r = await _hub_call(
        "GET", f"/schema/recipe/{slug}/analysis",
        params={"source": effective_source, "content_hash": content_hash},
    )
    if cache_r.get("found") and cache_r.get("analysis"):
        log.info("analyze_recipe cache HIT slug=%s source=%s", slug, effective_source)
        return {
            "cache_status": "hit",
            "analysis": cache_r["analysis"],
            "metadata": cache_r.get("metadata"),
        }

    # 3. Cache MISS → appel LLM
    log.info("analyze_recipe cache MISS slug=%s source=%s, calling LLM", slug, effective_source)
    analysis = await _call_llm_analyzer(yaml_content, slug, effective_source, content_hash)

    # Forcer les champs critiques (anti-hallucination LLM)
    analysis["slug"] = slug
    analysis["source"] = effective_source
    analysis["content_hash"] = content_hash

    # 4. POST cache (même si status=failed, on persiste pour éviter re-call)
    post_r = await _hub_call(
        "POST", f"/schema/recipe/{slug}/analysis",
        json_body=analysis,
    )

    if "error" in post_r:
        log.warning("POST cache analysis failed: %s", post_r)
        return {
            "cache_status": "miss_persist_failed",
            "analysis": analysis,
            "persist_error": post_r.get("error"),
        }

    return {
        "cache_status": "miss_analyzed",
        "analysis": analysis,
        "persisted_rowid": post_r.get("rowid"),
    }


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

    # CRUD assemblages (Sprint Composants Phase 3)
    "list_assemblies": {
        "fn": list_assemblies,
        "description": "Liste les assemblages de l'étude (latest version par aid).",
        "params": {"sid": "str", "kind": "str optionnel (filtre)"},
    },
    "create_assembly": {
        "fn": create_assembly,
        "description": (
            "Crée un assemblage (storymap_narrative_dsfr Phase 3 livré). "
            "L'assemblage référence des composants par {ref: cid} dans "
            "layout.sections[].components. Tu peux appeler create_component "
            "avant pour créer les composants nécessaires. "
            "RECOMMANDATION : validate_manifest('assembly', payload) d'abord."
        ),
        "params": {
            "sid": "str étude id",
            "manifest": "dict Assembly (kind, title, audience, layout, footer)",
        },
    },
    "get_assembly": {
        "fn": get_assembly,
        "description": "Retourne manifest + metadata DB de l'assemblage.",
        "params": {"sid": "str", "aid": "str (12 hex assemblage id)"},
    },
    "render_assembly": {
        "fn": render_assembly,
        "description": (
            "Rendu HTML preview de l'assemblage (sans publication S3, "
            "recalculé à chaque appel). Utile pour valider visuellement "
            "avant publish_assembly."
        ),
        "params": {"sid": "str", "aid": "str"},
    },
    "publish_assembly": {
        "fn": publish_assembly,
        "description": (
            "PUBLIE l'assemblage sur S3 avec audit_chain transverse SIGNÉ. "
            "Génère URL publique partageable + signed_hash SHA256 anti-tamper. "
            "ATTENTION : pose la classification audience (cerema_internal "
            "default — JAMAIS public par défaut, anti-fuite RGPD)."
        ),
        "params": {
            "sid": "str", "aid": "str",
            "audience": "str optionnel ('public'|'cerema_internal'|'restricted'|'confidential')",
        },
    },
    "get_assembly_history": {
        "fn": get_assembly_history,
        "description": "Historique des versions assemblage (audit trail).",
        "params": {"sid": "str", "aid": "str"},
    },
    # Sprint Composants Phase 3c (2026-06-27) : meta-agent analyseur recipes
    "analyze_recipe": {
        "fn": analyze_recipe,
        "description": (
            "Analyse une recipe (user ou système BigQgisMCP) et retourne "
            "RecipeAnalysis : params + impact métier + quality_checks. "
            "Cache HIT instant si déjà analysée, sinon trigger LLM (~10s). "
            "DISCIPLINE PLAN-PUIS-EXECUTE : appelle ce tool AVANT run_recipe "
            "pour proposer params + impact à l'user."
        ),
        "params": {
            "slug": "str (identifiant recipe)",
            "source": "str optionnel ('user'|'system'|'auto' default)",
        },
    },
}


# ── Format OpenAI function calling pour exposition au LLM ─────────────────────
# Sprint Composants Phase 3b (2026-06-26) : refactor format.
# Le catalogue NATIVE_TOOLS_V2 ci-dessus garde {fn, description, params}
# pour le dispatch interne (lookup par nom + appel direct du callable async).
# Le LLM, lui, doit recevoir un JSONSchema strict pour comprendre les types
# de chaque argument et les champs required. D'où ce 2e export.

# JSONSchemas réutilisés (sid = 12 hex étude id, partagé par tous les CRUD).
_SID_SCHEMA = {
    "type": "string",
    "pattern": r"^[0-9a-f]{12}$",
    "description": "Identifiant 12 hex de l'étude (autorésolu via /studies/active si absent).",
}
_CID_SCHEMA = {
    "type": "string",
    "pattern": r"^[0-9a-f]{12}$",
    "description": "Identifiant 12 hex du composant.",
}
_AID_SCHEMA = {
    "type": "string",
    "pattern": r"^[0-9a-f]{12}$",
    "description": "Identifiant 12 hex de l'assemblage.",
}

NATIVE_TOOLS_V2_OPENAI: list[dict[str, Any]] = [
    # ── Méta-cognitifs P0 ───────────────────────────────────────────────────
    {
        "type": "function",
        "function": {
            "name": "describe_entity_schema",
            "description": (
                "ANTI-HALLUCINATION : retourne le JSON Schema Pydantic + un exemple "
                "minimal valide d'une entité Sprint Composants V1.5 (component, "
                "assembly, audit_chain, classification...). Appelle CE TOOL AVANT "
                "de construire ta payload pour create_component / create_assembly. "
                "Évite les boucles d'erreurs de validation."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "entity_type": {
                        "type": "string",
                        "enum": ["component", "assembly", "audit_chain",
                                 "classification", "component_source",
                                 "component_rendering", "assembly_layout",
                                 "assembly_section", "assembly_footer"],
                        "description": "Type d'entité Pydantic à décrire.",
                    },
                    "kind": {
                        "type": "string",
                        "description": (
                            "Optionnel : filtre par kind (ex: 'interactive_map' "
                            "pour component, 'storymap_narrative_dsfr' pour assembly)."
                        ),
                    },
                },
                "required": ["entity_type"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "list_entity_kinds",
            "description": (
                "ANTI-HALLUCINATION : liste les `kind` autorisés (enum stable). "
                "Évite d'inventer un kind inexistant. Pour component : "
                "interactive_map, scene_3d, chart, kpi_badge, legend, "
                "narrative_text, data_table, media_embed, iframe_grist. "
                "Pour assembly : storymap_narrative_dsfr, dashboard, sheet_a4, "
                "modal_embed, atlas_immersive."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "entity_type": {
                        "type": "string",
                        "enum": ["component", "assembly", "classification"],
                    },
                },
                "required": ["entity_type"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "validate_manifest",
            "description": (
                "ÉCONOMIE TOUR LLM : dry-run validation Pydantic. Retourne "
                "{valid: bool, errors: [{loc, msg, type, fix_hint}]}. Appelle "
                "ce tool AVANT create_component / create_assembly, corrige "
                "selon fix_hint, re-valide jusqu'à valid=true."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "entity_type": {
                        "type": "string",
                        "enum": ["component", "assembly"],
                    },
                    "payload": {
                        "type": "object",
                        "description": (
                            "Manifest candidat (Component ou Assembly Pydantic). "
                            "Doit avoir kind, title, source/layout, rendering/audience, etc."
                        ),
                    },
                },
                "required": ["entity_type", "payload"],
            },
        },
    },

    # ── CRUD Composants ─────────────────────────────────────────────────────
    {
        "type": "function",
        "function": {
            "name": "list_components",
            "description": (
                "Liste les composants de l'étude active (latest version par cid). "
                "Utile pour voir ce qui existe déjà avant d'en créer un nouveau "
                "(évite duplicate)."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "sid": _SID_SCHEMA,
                    "kind": {
                        "type": "string",
                        "description": (
                            "Optionnel : filtre par kind (ex: 'narrative_text')."
                        ),
                    },
                },
                "required": ["sid"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "create_component",
            "description": (
                "Crée un composant Sprint Composants V1.5 rattaché à l'étude. "
                "Le manifest est validé Pydantic côté hub + persisté DB + PVC. "
                "RECOMMANDATION : appelle validate_manifest('component', payload) "
                "d'abord. Retourne {id, manifest_url, render_url}."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "sid": _SID_SCHEMA,
                    "manifest": {
                        "type": "object",
                        "description": (
                            "Component Pydantic V0.1. Champs OBLIGATOIRES : "
                            "kind (enum), title (str), source (dict scope+sid+pid?), "
                            "rendering (dict runtime+container_size+theme). "
                            "Champs OPTIONNELS : classification (default cerema_internal "
                            "anti-RGPD), params (dict kind-spécifique)."
                        ),
                    },
                },
                "required": ["sid", "manifest"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_component",
            "description": "Retourne manifest + metadata DB du composant.",
            "parameters": {
                "type": "object",
                "properties": {"sid": _SID_SCHEMA, "cid": _CID_SCHEMA},
                "required": ["sid", "cid"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_component_history",
            "description": (
                "Historique des versions du composant (audit trail INSERT-only)."
            ),
            "parameters": {
                "type": "object",
                "properties": {"sid": _SID_SCHEMA, "cid": _CID_SCHEMA},
                "required": ["sid", "cid"],
            },
        },
    },

    # ── CRUD Assemblages ────────────────────────────────────────────────────
    {
        "type": "function",
        "function": {
            "name": "list_assemblies",
            "description": (
                "Liste les assemblages de l'étude (latest version par aid). "
                "Utile pour voir s'il existe déjà un draft à compléter."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "sid": _SID_SCHEMA,
                    "kind": {
                        "type": "string",
                        "description": "Optionnel : filtre par kind.",
                    },
                },
                "required": ["sid"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "create_assembly",
            "description": (
                "Crée un assemblage HTML composite (storymap_narrative_dsfr, "
                "dashboard, sheet_a4...). L'assemblage référence des composants "
                "par {ref: cid} dans layout.sections[].components. Tu peux "
                "appeler create_component avant pour créer les composants. "
                "RECOMMANDATION : valide d'abord."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "sid": _SID_SCHEMA,
                    "manifest": {
                        "type": "object",
                        "description": (
                            "Assembly Pydantic V0.1. Champs OBLIGATOIRES : "
                            "kind, title, audience (default cerema_internal), "
                            "layout (type+sections). Champs RECOMMANDÉS : footer "
                            "(sources+disclaimer+cerema_mentions_legales)."
                        ),
                    },
                },
                "required": ["sid", "manifest"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_assembly",
            "description": "Retourne manifest + metadata DB de l'assemblage.",
            "parameters": {
                "type": "object",
                "properties": {"sid": _SID_SCHEMA, "aid": _AID_SCHEMA},
                "required": ["sid", "aid"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "render_assembly",
            "description": (
                "Rendu HTML preview de l'assemblage (recalculé à chaque appel, "
                "sans publication S3). Utile pour valider visuellement avant "
                "publish_assembly. Retourne {html: '<DOCTYPE...'} ou {error}."
            ),
            "parameters": {
                "type": "object",
                "properties": {"sid": _SID_SCHEMA, "aid": _AID_SCHEMA},
                "required": ["sid", "aid"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "publish_assembly",
            "description": (
                "PUBLIE l'assemblage sur S3 avec audit_chain transverse SIGNÉ "
                "SHA256 anti-tamper. Génère URL publique partageable. ATTENTION : "
                "audience cerema_internal default - JAMAIS public par défaut "
                "(anti-fuite RGPD). Retourne {published_url, audit_chain: "
                "{signed_hash, components_refs}}."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "sid": _SID_SCHEMA,
                    "aid": _AID_SCHEMA,
                    "audience": {
                        "type": "string",
                        "enum": ["public", "cerema_internal", "restricted", "confidential"],
                        "description": (
                            "Optionnel - override audience. JAMAIS 'public' "
                            "sans confirmation explicite user."
                        ),
                    },
                },
                "required": ["sid", "aid"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_assembly_history",
            "description": "Historique des versions assemblage (audit trail).",
            "parameters": {
                "type": "object",
                "properties": {"sid": _SID_SCHEMA, "aid": _AID_SCHEMA},
                "required": ["sid", "aid"],
            },
        },
    },
    # Sprint Composants Phase 3c (2026-06-27) : meta-agent analyseur recipes
    {
        "type": "function",
        "function": {
            "name": "analyze_recipe",
            "description": (
                "DISCIPLINE PLAN-PUIS-EXECUTE : analyse une recipe AVANT de "
                "la lancer pour comprendre ses params + impact métier + "
                "quality_checks. Cache HIT instant si déjà analysée, sinon "
                "trigger LLM (~10s). Workflow recommandé : "
                "(1) analyze_recipe(slug) (2) propose params + impact à "
                "l'user (3) attendre confirmation/ajustement (4) run_recipe. "
                "Sécurise les choix params (T100 vs T1000, audience cerema vs "
                "public, etc.) et détecte les warnings techniques."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "slug": {
                        "type": "string",
                        "description": "Identifiant de la recipe (ex: 'risque_inondation').",
                    },
                    "source": {
                        "type": "string",
                        "enum": ["user", "system", "auto"],
                        "description": (
                            "Source recipe. 'auto' essaie user d'abord, "
                            "fallback system. Recommandé : 'auto'."
                        ),
                    },
                },
                "required": ["slug"],
            },
        },
    },
]


# Tools qui mutent l'état côté hub composants/assemblages. Trigger
# d'invalidation du cache L2 artifacts (cf. _ARTIFACT_MUTATING_TOOLS dans
# qgis_agent.py).
NATIVE_TOOLS_V2_MUTATING: frozenset[str] = frozenset({
    "create_component",
    "create_assembly",
    "publish_assembly",
})
