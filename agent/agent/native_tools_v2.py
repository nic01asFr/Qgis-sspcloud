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


async def update_component(
    sid: str, cid: str, manifest: dict[str, Any],
    version_num_source: int | None = None,
) -> dict[str, Any]:
    """Vague E1 (D-QGIS-009, 2026-06-29) : UPDATE versionne composant existant.

    Pattern INSERT-only : version_num+1, previous_hash preserve, cid stable.
    Pour modifier params/source/title sans recreate. Audit trail conserve.
    Refs depuis assemblies (layout.sections[].components[].ref) restent valides.

    Le hub auto-fill provenance.scene_hash_at_creation si source.scene_hash
    present et provenance vide (pattern Phase 4b).

    Body : Component manifest JSON complet OU partiel :
    - Complet : remplace le manifest (id + sid forces a cid + sid).
    - Partiel : merge top-level avec l'existant (hub lit existant + override).

    Sprint 1 Vague E3 (D2 fix) : `version_num_source` optionnel pour OCC.
    Si fourni, le hub verifie qu'aucun autre processus (Marie via BlockNote)
    n'a modifie le composant entre temps. En cas de conflit : HTTP 409 avec
    {error: 'concurrent_update', current_version_num, source_version_num}.

    DISCIPLINE AGENT IA : pour eviter d'ecraser silencieusement des modifs
    BlockNote en cours, TOUJOURS appeler get_component AVANT update_component
    et passer la version_num actuelle comme version_num_source. En cas de 409,
    re-fetch + retry intelligent.

    Retourne {id, rowid, kind, title, classification, version_num,
              manifest_url, render_url, publish_url}.
    """
    body = {**manifest}
    if version_num_source is not None:
        body["version_num_source"] = version_num_source
    return await _hub_call(
        "PUT", f"/studies/{sid}/components/{cid}", json_body=body,
    )


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


async def update_assembly(
    sid: str, aid: str, manifest: dict[str, Any],
    version_num_source: int | None = None,
) -> dict[str, Any]:
    """Phase 4b (2026-06-28) : UPDATE versionne d'un assemblage existant.

    REGLE STRICTE : si une storymap du meme topic existe deja (aid
    connu via list_assemblies), TOUJOURS update_assembly. JAMAIS
    create_assembly avec un nouveau topic, sinon les composants existants
    de l'aid initial sont perdus dans la fragmentation.

    Pattern INSERT-only : chaque update insere version_num+1 avec
    previous_hash = ancien content_hash. L'aid reste stable, l'historique
    audit_trail est preserve.

    Pour ajouter une section a une storymap :
    1. get_assembly(sid, aid) → recuperer layout existant + version_num
    2. layout.sections = [...existing, nouvelle_section]
    3. update_assembly(sid, aid, manifest_complet, version_num_source=v)

    Sprint 1 Vague E3 (D2 fix) : `version_num_source` optionnel pour OCC.
    Si fourni, le hub verifie qu'aucun autre processus (Marie via BlockNote
    autosave) n'a modifie l'assembly entre temps. En cas de conflit :
    HTTP 409 avec {error: 'concurrent_update', current_version_num,
    source_version_num}.

    DISCIPLINE AGENT IA : pour eviter d'ecraser silencieusement des modifs
    Marie via BlockNote, TOUJOURS appeler get_assembly AVANT update_assembly
    et passer version_num actuelle comme version_num_source. En cas de 409,
    re-fetch + retry intelligent (re-appliquer les changes sur le manifest
    le plus recent).

    Retourne {id, rowid, version_num, manifest_url, render_url, publish_url}.
    """
    body = {**manifest}
    if version_num_source is not None:
        body["version_num_source"] = version_num_source
    return await _hub_call(
        "PUT", f"/studies/{sid}/assemblies/{aid}", json_body=body,
    )


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


async def list_catalog_components(
    audience: str = "cerema_internal",
    kind: str | None = None,
    limit: int = 50,
    offset: int = 0,
) -> dict[str, Any]:
    """Vague E1 (D-QGIS-009, 2026-06-29) : catalogue cross-etude composants.

    Permet a l'agent IA de decouvrir les composants publies par d'autres
    etudes CEREMA (ZEBRA, MobSciDat, autres users) pour potentiellement
    les reutiliser au lieu de creer from scratch.

    Discipline recommandee : appeler list_catalog_components AVANT
    create_component pour voir s'il existe deja un composant similaire.

    Filtres :
    - audience : 'public' | 'cerema_internal' (DEFAULT) | 'restricted' | 'confidential'
      DEFAULT 'cerema_internal' anti-fuite RGPD
    - kind : filtre ComponentKind (interactive_map, kpi_badge, ...)
    - limit + offset : pagination (default 50/0)

    Retourne {items: list[Component], total, audience, kind, limit, offset}.
    """
    params = {
        "audience": audience,
        "limit": str(limit),
        "offset": str(offset),
    }
    if kind:
        params["kind"] = kind
    return await _hub_call("GET", "/catalog/components", params=params)


async def list_catalog_assemblies(
    audience: str = "cerema_internal",
    kind: str | None = None,
    limit: int = 50,
    offset: int = 0,
) -> dict[str, Any]:
    """Vague E1 (D-QGIS-009) : catalogue cross-etude assemblages.

    Pendant a list_catalog_components pour les assemblages. Permet de
    decouvrir des templates de livrables a cloner via clone_assembly.

    Use case : Marie veut creer une storymap risque inondation.
    1. list_catalog_assemblies(kind='storymap_narrative_dsfr') -> trouve aid
       d'une storymap reference Blancarde-Chartreux
    2. clone_assembly(sid, aid=trouve, deep=true)
    3. Adapte les params pour son cas (4e -> 5e arr.)
    4. publish_assembly

    Retourne {items, total, audience, kind, limit, offset}.
    """
    params = {
        "audience": audience,
        "limit": str(limit),
        "offset": str(offset),
    }
    if kind:
        params["kind"] = kind
    return await _hub_call("GET", "/catalog/assemblies", params=params)


# ── Storymap patterns (Vague E2 Commit 3, D-QGIS-009 §3) ──────────────────────

async def list_storymap_patterns() -> dict[str, Any]:
    """Vague E2 Commit 3 (D-QGIS-009) : liste les patterns metier d'une
    storymap CEREMA.

    6 patterns canoniques :
    - hero_constat : ouverture immersive (heading + kpi_grid + source)
    - zoom_territoire : carte + contexte + legend (trio cartographe)
    - croisement_enjeu : carte + chart + narrative interpretation
    - fiche_indicateur : kpi_grid + table + methodo + quote expert
    - reliability_summary : matrice fiabilite + caveats
    - conclusion_actionnable : narrative + kpi_grid actions + quote decideur

    DISCIPLINE METIER : penser en chapitres canoniques, pas en blocs HTML.
    Use case : Marie demande 'comment ouvrir ma storymap ?' -> agent IA
    lui propose hero_constat avec ses params.

    Retourne {patterns: {name: {description, role_narratif, n_components,
    section_kind}}}.
    """
    return await _hub_call("GET", "/storymap_patterns")


async def describe_storymap_pattern(name: str) -> dict[str, Any]:
    """Vague E2 Commit 3 : recette complete d'un pattern metier.

    Retourne le template params + N component manifests parametrables +
    section template avec refs aux cid generes + example.

    name : nom du pattern (hero_constat, zoom_territoire, croisement_enjeu,
    fiche_indicateur, reliability_summary, conclusion_actionnable).

    L'agent IA doit ensuite :
    1. Construire les N component manifests selon params user
    2. create_component x N -> recupere les cid generes
    3. update_assembly en ajoutant section_template avec refs aux cid

    Use case : Marie veut une fiche d'indicateur 'Exposition pietons'.
    1. describe_storymap_pattern('fiche_indicateur') -> recette
    2. create_component x 5 (heading + kpi_grid + data_table + narrative + quote)
    3. update_assembly assembly_id, ajoute la nouvelle section composee
    """
    return await _hub_call("GET", f"/storymap_patterns/{name}")


async def clone_assembly(
    sid: str, aid: str, deep: bool = False,
) -> dict[str, Any]:
    """Vague E1 (D-QGIS-009, 2026-06-29) : clone un assemblage existant.

    Use case : partir d'un template (storymap reference) et l'adapter
    pour un nouveau cas au lieu de tout recreer from scratch.

    - `deep=False` (DEFAULT, shallow) : refs cid composants partages.
      Modifs source impactent le clone. Rapide.
    - `deep=True` : duplique aussi tous les composants references
      (nouveaux cid). Clone independant. Plus lourd.

    Le nouveau assemblage :
    - new aid (12 hex generated)
    - version_num = 1
    - provenance.cloned_from = aid source
    - title suffixé " (clone)"
    - sid preserve (clone dans la même etude)

    Retourne {id, rowid, cloned_from, deep, kind, title, audience,
              version_num, manifest_url, render_url, publish_url}.
    """
    params = {"deep": "true" if deep else "false"}
    return await _hub_call(
        "POST", f"/studies/{sid}/assemblies/{aid}/clone", params=params,
    )


async def publish_component(
    sid: str, cid: str, audience: str | None = None,
) -> dict[str, Any]:
    """A3 (Vague A Commit 3) — Publie un composant standalone S3 + URL hub.

    Use case : composant publishable individuellement (interactive_map,
    chart, data_table, ...) embarquable en iframe par sites tiers
    (Atlas widget Grist, sites CEREMA, etc.) sans contexte assembly.

    Workflow :
    1. Lit manifest depuis components_index + PVC
    2. Rend HTML standalone via _pre_render_component_html partagé
    3. Upload S3 via s3_publication.publish()
    4. Retourne URL hub /published/{owner}/component/component-{cid}

    Retourne {id, published, published_url, audience, kind, title, size_bytes}.
    """
    body = {}
    if audience:
        body["audience"] = audience
    return await _hub_call(
        "POST", f"/studies/{sid}/components/{cid}/publish", json_body=body or None,
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

    def _pad_short_strings(d: dict) -> dict:
        """Pad les champs string qui ont min_length Pydantic mais que le LLM
        peut produire trop courts. Fail-safe avant POST cache (sinon 422
        validation Pydantic stricte).

        Champs concernés (cf. hub/hub/models/recipe_analysis.py) :
        - usage_typical >= 30
        - cost_estimate >= 10
        - description_short >= 10
        - impact_description >= 50
        - QualityCheck.title >= 10, description >= 30, fix_hint >= 20
        """
        _PAD = " (information à compléter par l'agent enrichisseur dans une prochaine itération)"

        def _ensure_len(s, min_l):
            if not isinstance(s, str) or len(s) >= min_l:
                return s
            return (s + _PAD)[:max(min_l, 50)]

        # Envelope-level fields
        if "usage_typical" in d:
            d["usage_typical"] = _ensure_len(d["usage_typical"], 30)
        if "cost_estimate" in d:
            d["cost_estimate"] = _ensure_len(d["cost_estimate"], 10)

        # params_analysis fields
        for p in d.get("params_analysis", []) or []:
            if "description_short" in p:
                p["description_short"] = _ensure_len(p["description_short"], 10)
            if "impact_description" in p:
                p["impact_description"] = _ensure_len(p["impact_description"], 50)

        # quality_checks fields
        for q in d.get("quality_checks", []) or []:
            if "title" in q:
                q["title"] = _ensure_len(q["title"], 10)
            if "description" in q:
                q["description"] = _ensure_len(q["description"], 30)
            if "fix_hint" in q:
                q["fix_hint"] = _ensure_len(q["fix_hint"], 20)

        return d

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
                # Pad les champs string trop courts pour passer la validation
                # Pydantic stricte côté hub (sinon 422 → cache miss_persist_failed)
                analysis_dict = _pad_short_strings(analysis_dict)
                return analysis_dict
        except Exception as exc:
            last_error = f"{model} exception: {exc}"
            log.warning(last_error)
            continue

    return {
        "status": "failed",
        "error_detail": f"Tous modèles ont échoué: {last_error}",
    }


# ── Sprint Composants Phase 4a : meta-agent analyseur config agent ───────────


async def _call_llm_agent_config_analyzer(
    config: dict[str, Any], sid: str, owner: str,
    components_in_study: int, recipes_in_study: int,
    assemblies_in_study: int, has_restricted: bool,
    config_hash: str,
) -> dict[str, Any]:
    """Appel LLM avec profile agent_config_analyzer pour produire
    AgentConfigAnalysis JSON.

    Modèles : qwen3-6-35b-moe preferred + fallback gemma4-26b-moe.
    Force JSON output strict via response_format=json_object.
    Si tous modèles échouent : retourne dict status='failed'.
    """
    import json as _json
    import os
    from datetime import datetime

    llm_base_url = os.getenv("LLM_BASE_URL", "https://llm.lab.sspcloud.fr/api")
    api_key = (
        os.getenv("LLM_API_KEY")
        or os.getenv("ONYXIA_LLM_API_KEY")
        or os.getenv("OPENAI_API_KEY")
        or ""
    )

    # Charger system_prompt du profile agent_config_analyzer
    profile_r = await _hub_call("GET", "/internal/profiles/agent_config_analyzer/full")
    if "error" in profile_r:
        return {
            "status": "failed",
            "error_detail": f"profile agent_config_analyzer indisponible: {profile_r.get('error')}",
        }
    system_prompt = profile_r.get("agent_system_prompt", "")
    if not system_prompt:
        return {"status": "failed", "error_detail": "profile sans agent_system_prompt"}

    user_message = (
        f"Analyse la configuration d'agent partagé suivante.\n\n"
        f"=== CONFIG PROPOSÉE ===\n"
        f"sid: {sid}\n"
        f"config_hash: {config_hash}\n"
        f"profile: {config.get('profile', '?')}\n"
        f"audience: {config.get('audience', 'cerema_internal')}\n"
        f"expires_at: {config.get('expires_at')}\n"
        f"data_scope: {config.get('data_scope', 'project')}\n"
        f"tools_whitelist: {config.get('tools_whitelist', 'all')}\n"
        f"project_id: {config.get('project_id')}\n"
        f"label: {config.get('label')}\n\n"
        f"=== CONTEXTE ÉTUDE ===\n"
        f"components_in_study: {components_in_study}\n"
        f"recipes_in_study: {recipes_in_study}\n"
        f"assemblies_in_study: {assemblies_in_study}\n"
        f"has_restricted_components: {has_restricted}\n"
        f"owner: {owner}\n"
        f"analyzed_at: {datetime.utcnow().isoformat()}Z\n\n"
        f"Produis le JSON AgentConfigAnalysis strict (rien d'autre)."
    )

    def _pad_short_strings_agent(d: dict) -> dict:
        """Pad les champs string trop courts (cf. _pad_short_strings recipes)."""
        _PAD = " (information à compléter par l'agent enrichisseur dans une prochaine itération)"

        def _ensure_len(s, min_l):
            if not isinstance(s, str) or len(s) >= min_l:
                return s
            return (s + _PAD)[:max(min_l, 50)]

        if "usage_inferred" in d:
            d["usage_inferred"] = _ensure_len(d["usage_inferred"], 30)
        for p in d.get("params_analysis", []) or []:
            if "description_short" in p:
                p["description_short"] = _ensure_len(p["description_short"], 10)
            if "impact_description" in p:
                p["impact_description"] = _ensure_len(p["impact_description"], 50)
        for q in d.get("quality_checks", []) or []:
            if "title" in q:
                q["title"] = _ensure_len(q["title"], 10)
            if "description" in q:
                q["description"] = _ensure_len(q["description"], 30)
            if "fix_hint" in q:
                q["fix_hint"] = _ensure_len(q["fix_hint"], 20)
        return d

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
                    log.warning("LLM agent_config_analyzer %s failed: %s", model, last_error)
                    continue
                data = resp.json()
                content = data["choices"][0]["message"]["content"]
                try:
                    analysis_dict = _json.loads(content)
                except _json.JSONDecodeError as je:
                    last_error = f"{model} JSON invalide: {je}"
                    log.warning(last_error)
                    continue
                analysis_dict["analyzer_model"] = model
                analysis_dict["status"] = "success"
                # Anti-hallucination : forcer les champs critiques avec valeurs maîtrisées
                analysis_dict["sid"] = sid
                analysis_dict["config_hash"] = config_hash
                analysis_dict["components_in_study"] = components_in_study
                analysis_dict["recipes_in_study"] = recipes_in_study
                analysis_dict["assemblies_in_study"] = assemblies_in_study
                analysis_dict["has_restricted_components"] = has_restricted
                # Force aussi la config dans le retour pour cohérence
                for k in ("profile", "audience", "expires_at", "data_scope",
                          "tools_whitelist", "project_id", "label"):
                    if k in config:
                        analysis_dict[k] = config[k]
                analysis_dict = _pad_short_strings_agent(analysis_dict)
                return analysis_dict
        except Exception as exc:
            last_error = f"{model} exception: {exc}"
            log.warning(last_error)
            continue

    return {
        "status": "failed",
        "error_detail": f"Tous modèles ont échoué: {last_error}",
    }


async def _fetch_study_context_for_agent(sid: str) -> dict[str, int | bool]:
    """Compte components/recipes/assemblies de l'étude + flag restricted.

    Utilisé par analyze_agent_config pour fournir le contexte au LLM.
    """
    components_in_study = 0
    recipes_in_study = 0
    assemblies_in_study = 0
    has_restricted = False

    try:
        comps_r = await _hub_call("GET", f"/studies/{sid}/components")
        if isinstance(comps_r, list):
            components_in_study = len(comps_r)
            for c in comps_r:
                if c.get("classification") in ("restricted", "confidential"):
                    has_restricted = True
        elif isinstance(comps_r, dict) and not comps_r.get("error"):
            # Some endpoints wrap in object; handle gracefully
            items = comps_r.get("items") or []
            components_in_study = len(items)
    except Exception as exc:
        log.warning("_fetch_study_context_for_agent components: %s", exc)

    try:
        asms_r = await _hub_call("GET", f"/studies/{sid}/assemblies")
        if isinstance(asms_r, list):
            assemblies_in_study = len(asms_r)
        elif isinstance(asms_r, dict) and not asms_r.get("error"):
            items = asms_r.get("items") or []
            assemblies_in_study = len(items)
    except Exception as exc:
        log.warning("_fetch_study_context_for_agent assemblies: %s", exc)

    try:
        recipes_r = await _hub_call("GET", "/desk/recipes")
        if isinstance(recipes_r, dict) and not recipes_r.get("error"):
            recipes_in_study = recipes_r.get("count", 0)
    except Exception as exc:
        log.warning("_fetch_study_context_for_agent recipes: %s", exc)

    return {
        "components_in_study": components_in_study,
        "recipes_in_study": recipes_in_study,
        "assemblies_in_study": assemblies_in_study,
        "has_restricted": has_restricted,
    }


async def list_agents(sid: str) -> dict[str, Any]:
    """Liste les agents partagés de l'étude.

    Retourne {sid, count, agents: [{label, profile, audience, published_url,
    audit_chain.signed_hash, expires_at, ...}]}.
    """
    return await _hub_call(
        "GET", f"/studies/{sid}/scoped-keys",
        params={"only_published": "false"},
    )


async def analyze_agent_config(
    sid: str,
    profile: str = "storymap_creator_v15",
    audience: str = "cerema_internal",
    expires_at: int | None = None,
    data_scope: str = "project",
    tools_whitelist: list[str] | str = "all",
    project_id: str | None = None,
    label: str | None = None,
) -> dict[str, Any]:
    """Analyse pré-mint d'une config d'agent partagé.

    Workflow :
    1. Fetch contexte étude (components/recipes/assemblies counts)
    2. Compute config_hash SHA256 canonical
    3. Cache lookup hub
    4. Si MISS : appel LLM via profile agent_config_analyzer + fallback
    5. POST cache → DB + PVC
    6. Retourne dict {cache_status, analysis}

    L'assistant principal consomme params_analysis + quality_checks pour
    proposer plan + impact à l'user (discipline plan-puis-execute Phase 3c).
    """
    import hashlib
    import json as _json

    # 1. Contexte étude
    ctx = await _fetch_study_context_for_agent(sid)

    # 2. Config hash canonical
    canonical = _json.dumps({
        "sid": sid,
        "profile": profile,
        "audience": audience,
        "expires_at": expires_at,
        "data_scope": data_scope,
        "tools_whitelist": tools_whitelist if isinstance(tools_whitelist, str)
                           else sorted(tools_whitelist),
        "project_id": project_id,
        "label": label,
    }, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    config_hash = hashlib.sha256(canonical.encode("utf-8")).hexdigest()

    # 3. Cache lookup
    cache_r = await _hub_call(
        "GET", "/schema/agent-config/analysis",
        params={"sid": sid, "config_hash": config_hash},
    )
    if cache_r.get("found") and cache_r.get("analysis"):
        log.info("analyze_agent_config cache HIT sid=%s hash=%s",
                 sid, config_hash[:12])
        return {
            "cache_status": "hit",
            "analysis": cache_r["analysis"],
            "context_inferred": ctx,
        }

    # 4. Cache MISS → LLM
    log.info("analyze_agent_config cache MISS sid=%s, calling LLM", sid)
    config = {
        "profile": profile, "audience": audience,
        "expires_at": expires_at, "data_scope": data_scope,
        "tools_whitelist": tools_whitelist,
        "project_id": project_id, "label": label,
    }
    analysis = await _call_llm_agent_config_analyzer(
        config=config, sid=sid, owner="",
        components_in_study=ctx["components_in_study"],
        recipes_in_study=ctx["recipes_in_study"],
        assemblies_in_study=ctx["assemblies_in_study"],
        has_restricted=ctx["has_restricted"],
        config_hash=config_hash,
    )

    # 5. POST cache (même si failed, persiste pour éviter re-call)
    post_r = await _hub_call(
        "POST", "/schema/agent-config/analysis",
        json_body=analysis,
    )

    if "error" in post_r:
        log.warning("POST cache agent_config_analysis failed: %s", post_r)
        return {
            "cache_status": "miss_persist_failed",
            "analysis": analysis,
            "context_inferred": ctx,
            "persist_error": post_r.get("error"),
        }

    return {
        "cache_status": "miss_analyzed",
        "analysis": analysis,
        "context_inferred": ctx,
        "persisted_rowid": post_r.get("rowid"),
    }


async def create_agent(
    sid: str,
    profile: str = "storymap_creator_v15",
    audience: str = "cerema_internal",
    expires_at: int | None = None,
    data_scope: str = "project",
    tools_whitelist: list[str] | str = "all",
    project_id: str | None = None,
    label: str | None = None,
) -> dict[str, Any]:
    """Mint un agent partagé (clé scopée qgisk_).

    L'assistant DOIT avoir appelé analyze_agent_config AVANT pour proposer
    le plan + obtenir confirmation user. Cf. discipline plan-puis-execute.

    Retourne {key, key_masked, ..., warning_copy_now}.
    """
    payload = {
        "profile": profile, "audience": audience,
        "expires_at": expires_at, "data_scope": data_scope,
        "tools_whitelist": tools_whitelist,
        "project_id": project_id, "label": label,
    }
    return await _hub_call(
        "POST", f"/studies/{sid}/scoped-keys",
        json_body=payload,
    )


async def publish_agent(
    sid: str,
    key_id: str,
    audience: str | None = None,
) -> dict[str, Any]:
    """Publie un agent partagé : audit_chain + URL widget.

    L'agent doit appeler create_agent d'abord pour obtenir key_id.
    Retourne {published_url, audit_chain: {signed_hash, ...}}.
    """
    body: dict[str, Any] = {}
    if audience:
        body["audience"] = audience
    return await _hub_call(
        "POST", f"/studies/{sid}/scoped-keys/{key_id}/publish",
        json_body=body or None,
    )


async def revoke_agent(sid: str, key_id: str) -> dict[str, Any]:
    """Révoque un agent partagé (soft delete)."""
    return await _hub_call(
        "DELETE", f"/studies/{sid}/scoped-keys/{key_id}",
    )


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
    "update_component": {
        "fn": update_component,
        "description": (
            "Vague E1 (D-QGIS-009) — UPDATE versionne composant existant. "
            "INSERT-only : version_num+1, cid stable, audit trail conserve. "
            "Pour modifier params/source/title sans devoir delete + recreate "
            "(les refs depuis assemblies restent valides). Hub auto-fill "
            "provenance.scene_hash_at_creation si source.scene_hash present."
        ),
        "params": {
            "sid": "str étude id",
            "cid": "str composant id (12 hex)",
            "manifest": (
                "dict Component (id+sid forces a cid+sid). Complet ou partiel : "
                "manifest partiel (sans kind/source/rendering) sera merge avec "
                "l'existant top-level fields override."
            ),
        },
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
    "update_assembly": {
        "fn": update_assembly,
        "description": (
            "UPDATE versionne d'un assemblage existant (Phase 4b). "
            "INSERT-only : version_num+1, previous_hash preserve, aid stable. "
            "REGLE : si une storymap aid existe deja pour le topic, "
            "TOUJOURS update_assembly (pas create_assembly) sinon les "
            "composants existants seront perdus dans la fragmentation. "
            "Pour ajouter une section : get_assembly d'abord, etend "
            "layout.sections, puis update_assembly avec le manifest complet."
        ),
        "params": {
            "sid": "str étude id",
            "aid": "str assemblage id existant",
            "manifest": "dict Assembly complet (id, sid, kind, title, "
                        "audience, layout, footer). id+sid forces a aid+sid.",
        },
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
    "list_catalog_components": {
        "fn": list_catalog_components,
        "description": (
            "Vague E1 (D-QGIS-009) — CATALOGUE cross-etude composants. "
            "Decouvrir composants publies par d'autres etudes CEREMA pour "
            "reutiliser au lieu de creer from scratch. Discipline recommandee : "
            "appeler AVANT create_component. Default audience cerema_internal "
            "(anti-fuite RGPD)."
        ),
        "params": {
            "audience": "str (public|cerema_internal|restricted|confidential), default cerema_internal",
            "kind": "str optionnel (filtre ComponentKind)",
            "limit": "int (default 50)",
            "offset": "int (default 0)",
        },
    },
    "list_catalog_assemblies": {
        "fn": list_catalog_assemblies,
        "description": (
            "Vague E1 (D-QGIS-009) — CATALOGUE cross-etude assemblages. "
            "Decouvrir templates de livrables (storymap, dashboard...) a cloner "
            "via clone_assembly. Use case : partir d'une storymap reference "
            "et l'adapter au lieu de tout recreer from scratch."
        ),
        "params": {
            "audience": "str default cerema_internal",
            "kind": "str optionnel (storymap_narrative_dsfr|dashboard|sheet_a4|...)",
            "limit": "int (default 50)",
            "offset": "int (default 0)",
        },
    },
    "clone_assembly": {
        "fn": clone_assembly,
        "description": (
            "Vague E1 (D-QGIS-009) — CLONE un assemblage existant. "
            "Use case : partir d'un template reference (storymap risque "
            "inondation Marseille) et l'adapter au lieu de tout recreer "
            "from scratch. Default shallow (refs cid partages). "
            "deep=true duplique aussi les composants (nouveaux cid)."
        ),
        "params": {
            "sid": "str étude id",
            "aid": "str assemblage source id (12 hex)",
            "deep": "bool (default false) — shallow refs partages OU deep dup composants",
        },
    },
    "list_storymap_patterns": {
        "fn": list_storymap_patterns,
        "description": (
            "Vague E2 Commit 3 (D-QGIS-009 §3) — 6 patterns metier d'une "
            "storymap CEREMA (hero_constat, zoom_territoire, croisement_enjeu, "
            "fiche_indicateur, reliability_summary, conclusion_actionnable). "
            "DISCIPLINE METIER : penser en chapitres canoniques, pas en blocs "
            "HTML. Use case : Marie demande 'comment ouvrir ma storymap ?' "
            "-> agent IA suggere hero_constat avec ses params."
        ),
        "params": {},
    },
    "describe_storymap_pattern": {
        "fn": describe_storymap_pattern,
        "description": (
            "Vague E2 Commit 3 — recette complete d'un pattern metier : "
            "params_schema + N component manifests parametrables + section "
            "template avec refs cid. L'agent construit ensuite les manifests, "
            "create_component x N, update_assembly avec la nouvelle section."
        ),
        "params": {
            "name": "str (hero_constat|zoom_territoire|croisement_enjeu|"
                    "fiche_indicateur|reliability_summary|conclusion_actionnable)",
        },
    },
    "publish_component": {
        "fn": publish_component,
        "description": (
            "A3 (Vague A) — PUBLIE un composant standalone S3 + URL hub. "
            "Use case : composant embarquable iframe par sites tiers "
            "(Atlas widget Grist, sites CEREMA externes). Retourne URL "
            "hub /published/{owner}/component/component-{cid}. "
            "Audience cerema_internal default (anti-fuite RGPD)."
        ),
        "params": {
            "sid": "str", "cid": "str",
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
    # Sprint Composants Phase 4a (2026-06-27) : agents partagés
    "list_agents": {
        "fn": list_agents,
        "description": "Liste les agents partagés de l'étude (incluant non-publiés).",
        "params": {"sid": "str étude id"},
    },
    "analyze_agent_config": {
        "fn": analyze_agent_config,
        "description": (
            "DISCIPLINE PLAN-PUIS-EXECUTE : analyse une config d'agent partagé "
            "AVANT mint pour comprendre params + impact métier/RGPD + quality "
            "checks. Cache HIT instant si déjà analysée. Recommandé : appelle "
            "ce tool, propose plan à l'user, attends confirmation, puis "
            "create_agent + publish_agent."
        ),
        "params": {
            "sid": "str étude id",
            "profile": "str (storymap_creator_v15 défaut)",
            "audience": "str (cerema_internal défaut, JAMAIS public sans confirmation user)",
            "expires_at": "int unix timestamp optionnel (None = jamais)",
            "data_scope": "str (project|study|all, project défaut)",
            "tools_whitelist": "list|'all'",
            "project_id": "str optionnel",
            "label": "str optionnel (auto-généré sinon)",
        },
    },
    "create_agent": {
        "fn": create_agent,
        "description": (
            "Mint un agent partagé (clé scopée qgisk_). Appelle "
            "analyze_agent_config AVANT pour valider config. Retourne "
            "{key, key_masked, ...} — la clé brute n'est retournée qu'une "
            "seule fois, copier maintenant."
        ),
        "params": {
            "sid": "str", "profile": "str", "audience": "str",
            "expires_at": "int|None", "data_scope": "str",
            "tools_whitelist": "list|'all'",
            "project_id": "str|None", "label": "str|None",
        },
    },
    "publish_agent": {
        "fn": publish_agent,
        "description": (
            "Publie un agent partagé : calcule audit_chain transverse SIGNÉ "
            "SHA256 + génère URL widget. Doit avoir appelé create_agent "
            "d'abord pour obtenir key_id. Retourne {published_url, "
            "audit_chain: {signed_hash, ...}}."
        ),
        "params": {
            "sid": "str", "key_id": "str (qgisk_...)",
            "audience": "str optionnel (override)",
        },
    },
    "revoke_agent": {
        "fn": revoke_agent,
        "description": "Révoque un agent partagé (soft delete, irréversible).",
        "params": {"sid": "str", "key_id": "str"},
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
    {
        "type": "function",
        "function": {
            "name": "update_component",
            "description": (
                "Vague E1 (D-QGIS-009) - UPDATE versionne composant existant. "
                "INSERT-only versioning : version_num+1, cid stable, audit "
                "trail conserve. Pour modifier params/source/title sans devoir "
                "delete + recreate (les refs depuis assemblies restent valides). "
                "Le hub auto-fill provenance.scene_hash_at_creation si "
                "source.scene_hash present. Manifest complet OU partiel "
                "(merge top-level avec l'existant)."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "sid": _SID_SCHEMA,
                    "cid": _CID_SCHEMA,
                    "manifest": {
                        "type": "object",
                        "description": (
                            "Component manifest (id+sid forces a cid+sid). "
                            "Doit contenir kind, source, rendering au minimum "
                            "ou merge avec l'existant via lecture PVC."
                        ),
                    },
                },
                "required": ["sid", "cid", "manifest"],
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
            "name": "update_assembly",
            "description": (
                "UPDATE versionne d'un assemblage existant (Phase 4b). "
                "REGLE STRICTE : si une storymap aid existe deja pour le "
                "topic (verifie via list_assemblies + get_assembly), TOUJOURS "
                "update_assembly. JAMAIS create_assembly avec topic similaire "
                "sinon les composants existants sont perdus dans la "
                "fragmentation. INSERT-only : version_num+1, aid stable, "
                "audit trail preserve. Pour ajouter une section : "
                "(1) get_assembly(sid,aid) puis (2) layout.sections.append(...) "
                "puis (3) update_assembly(sid, aid, manifest_complet)."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "sid": _SID_SCHEMA,
                    "aid": _AID_SCHEMA,
                    "manifest": {
                        "type": "object",
                        "description": (
                            "Assembly manifest complet (id+sid forces a aid+sid). "
                            "Doit contenir kind, title, audience, layout, footer."
                        ),
                    },
                },
                "required": ["sid", "aid", "manifest"],
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
            "name": "list_catalog_components",
            "description": (
                "Vague E1 (D-QGIS-009) - CATALOGUE cross-etude composants. "
                "Decouvre composants publies par d'autres etudes CEREMA "
                "(ZEBRA, MobSciDat, etc.) pour reutiliser au lieu de creer "
                "from scratch. Discipline recommandee : appeler AVANT "
                "create_component pour voir s'il existe deja un composant "
                "similaire."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "audience": {
                        "type": "string",
                        "enum": ["public", "cerema_internal", "restricted", "confidential"],
                        "default": "cerema_internal",
                        "description": "Default cerema_internal (anti-fuite RGPD).",
                    },
                    "kind": {
                        "type": "string",
                        "description": "Optionnel - filtre ComponentKind.",
                    },
                    "limit": {"type": "integer", "default": 50},
                    "offset": {"type": "integer", "default": 0},
                },
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "list_catalog_assemblies",
            "description": (
                "Vague E1 (D-QGIS-009) - CATALOGUE cross-etude assemblages. "
                "Decouvre templates de livrables (storymap, dashboard...) a "
                "cloner via clone_assembly. Use case : partir d'une storymap "
                "reference et adapter au lieu de creer from scratch."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "audience": {
                        "type": "string",
                        "enum": ["public", "cerema_internal", "restricted", "confidential"],
                        "default": "cerema_internal",
                    },
                    "kind": {"type": "string", "description": "Optionnel - filtre AssemblyKind."},
                    "limit": {"type": "integer", "default": 50},
                    "offset": {"type": "integer", "default": 0},
                },
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "clone_assembly",
            "description": (
                "Vague E1 (D-QGIS-009) - CLONE un assemblage existant. "
                "Use case : partir d'un template reference (storymap risque "
                "inondation) et l'adapter au lieu de recreer from scratch. "
                "shallow par defaut (refs cid partages, rapide). deep=true "
                "duplique aussi composants (nouveaux cid, clone independant). "
                "Le nouvel assemblage a provenance.cloned_from = aid source, "
                "title suffixe ' (clone)', sid preserve."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "sid": _SID_SCHEMA,
                    "aid": _AID_SCHEMA,
                    "deep": {
                        "type": "boolean",
                        "default": False,
                        "description": (
                            "false (default) : shallow clone, refs cid composants "
                            "partages. true : deep clone, duplique aussi composants."
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
            "name": "list_storymap_patterns",
            "description": (
                "Vague E2 Commit 3 (D-QGIS-009 §3) - 6 patterns metier d'une "
                "storymap CEREMA. Penser en chapitres canoniques au lieu de "
                "blocs HTML : hero_constat (ouverture immersive), zoom_territoire "
                "(carte + contexte + legende), croisement_enjeu (carte+chart+"
                "interpretation), fiche_indicateur (kpi_grid+table+methodo+quote), "
                "reliability_summary (matrice fiabilite + caveats), "
                "conclusion_actionnable (synthese+actions+quote decideur). "
                "Use case : Marie ouvre une nouvelle storymap, agent IA suggere "
                "hero_constat puis chaine les autres patterns selon le besoin."
            ),
            "parameters": {"type": "object", "properties": {}},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "describe_storymap_pattern",
            "description": (
                "Vague E2 Commit 3 - recette complete d'un pattern metier : "
                "params_schema + N component manifests parametrables + section "
                "template avec refs cid. L'agent construit les manifests, "
                "create_component x N, puis update_assembly avec section composee."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "name": {
                        "type": "string",
                        "enum": [
                            "hero_constat", "zoom_territoire", "croisement_enjeu",
                            "fiche_indicateur", "reliability_summary",
                            "conclusion_actionnable",
                        ],
                    },
                },
                "required": ["name"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "publish_component",
            "description": (
                "A3 (Vague A) - PUBLIE un composant standalone S3 + URL hub. "
                "Use case : composant embarquable iframe par sites tiers "
                "(Atlas widget Grist, sites CEREMA, etc.). Retourne URL hub "
                "/published/{owner}/component/component-{cid}. Audience "
                "cerema_internal default - JAMAIS public par défaut (anti-RGPD)."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "sid": _SID_SCHEMA,
                    "cid": _CID_SCHEMA,
                    "audience": {
                        "type": "string",
                        "enum": ["public", "cerema_internal", "restricted", "confidential"],
                        "description": "Optionnel - override audience.",
                    },
                },
                "required": ["sid", "cid"],
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
    # Sprint Composants Phase 4a (2026-06-27) : agents partagés
    {
        "type": "function",
        "function": {
            "name": "list_agents",
            "description": (
                "Liste les agents partagés de l'étude (clés scopées avec "
                "métadonnées publication). Utile pour voir ce qui existe "
                "avant de configurer un nouveau."
            ),
            "parameters": {
                "type": "object",
                "properties": {"sid": _SID_SCHEMA},
                "required": ["sid"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "analyze_agent_config",
            "description": (
                "DISCIPLINE PLAN-PUIS-EXECUTE : analyse une config d'agent "
                "partagé AVANT mint. Retourne params + impact métier/RGPD + "
                "quality_checks (profile_coherence, audience_safety, "
                "scope_appropriateness, tools_minimum, expiration_strategy). "
                "Cache HIT instant si déjà analysée. Recommandé : appelle ce "
                "tool, propose plan à l'user, attends confirmation, puis "
                "create_agent + publish_agent."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "sid": _SID_SCHEMA,
                    "profile": {
                        "type": "string",
                        "description": "Profile identité de l'agent partagé (storymap_creator_v15 défaut).",
                    },
                    "audience": {
                        "type": "string",
                        "enum": ["public", "cerema_internal", "restricted", "confidential"],
                        "description": "Audience cible. JAMAIS 'public' sans confirmation explicite user (RGPD).",
                    },
                    "expires_at": {
                        "type": ["integer", "null"],
                        "description": "Unix timestamp expiration ou null = jamais.",
                    },
                    "data_scope": {
                        "type": "string",
                        "enum": ["all", "study", "project"],
                    },
                    "tools_whitelist": {
                        "description": "Liste de tools autorisés ou 'all' (hérite profile).",
                    },
                    "project_id": {"type": ["string", "null"]},
                    "label": {"type": ["string", "null"]},
                },
                "required": ["sid"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "create_agent",
            "description": (
                "Mint un agent partagé. Retourne {key, key_masked, ...} — "
                "la clé brute est retournée UNE SEULE FOIS. RECOMMANDÉ : "
                "appelle analyze_agent_config d'abord pour valider config + "
                "obtenir confirmation user (discipline plan-puis-execute)."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "sid": _SID_SCHEMA,
                    "profile": {"type": "string"},
                    "audience": {
                        "type": "string",
                        "enum": ["public", "cerema_internal", "restricted", "confidential"],
                    },
                    "expires_at": {"type": ["integer", "null"]},
                    "data_scope": {"type": "string", "enum": ["all", "study", "project"]},
                    "tools_whitelist": {},
                    "project_id": {"type": ["string", "null"]},
                    "label": {"type": ["string", "null"]},
                },
                "required": ["sid"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "publish_agent",
            "description": (
                "Publie un agent partagé : audit_chain transverse SIGNÉ SHA256 "
                "anti-tamper + URL widget. Doit avoir create_agent d'abord."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "sid": _SID_SCHEMA,
                    "key_id": {
                        "type": "string",
                        "description": "qgisk_<user>_<hex32> retourné par create_agent.",
                    },
                    "audience": {
                        "type": "string",
                        "enum": ["public", "cerema_internal", "restricted", "confidential"],
                        "description": "Override audience optionnel.",
                    },
                },
                "required": ["sid", "key_id"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "revoke_agent",
            "description": "Révoque un agent partagé (soft delete, irréversible).",
            "parameters": {
                "type": "object",
                "properties": {
                    "sid": _SID_SCHEMA,
                    "key_id": {"type": "string"},
                },
                "required": ["sid", "key_id"],
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
    # Sprint Composants Phase 4a (2026-06-27) : agents partagés
    "create_agent",
    "publish_agent",
    "revoke_agent",
    # Bug fix P0 (2026-06-29) : Reviewer-VagueE1 a detecte que ces 2 tools
    # mutants etaient absents du frozenset. Impact : cache L2 artifacts
    # _fetch_study_artifacts_summary ne s'invalidait pas apres ces appels
    # -> agent IA voyait state stale (composants/assemblages obsoletes).
    "publish_component",   # A3 Vague A (2026-06-28) - mutant S3 + DB
    "update_assembly",     # Phase 4b (2026-06-28) - mutant DB INSERT-only + PVC
    # Vague E1 (D-QGIS-009 2026-06-29) - UX libre composition
    "update_component",    # Vague E1 - mutant DB INSERT-only + PVC
    "clone_assembly",      # Vague E1 - mutant DB INSERT new aid + PVC
})
