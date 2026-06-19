"""
agent.qgis_agent — Agent QGIS spécialisé via Agno + SSPCloud LLM.

Architecture :
  - Agno Agent : orchestration LLM + outils MCP
  - SSPCloud LLM API : Gemma4/Qwen3 (compatible OpenAI)
  - MCP Client : appels vers le hub /mcp
  - Mémoire : SQLite PVC via memory.py
  - Profil : system prompt adapté via profile_manager
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import time
import uuid
from typing import AsyncGenerator

import httpx

from agent import memory

log = logging.getLogger("agent.qgis_agent")

# ── Config SSPCloud LLM ────────────────────────────────────────────────────────
_LLM_BASE_URL = os.getenv("LLM_BASE_URL", "https://llm.lab.sspcloud.fr/api")


def _llm_api_key() -> str:
    """Lecture dynamique de la cle LLM (cf. Option alpha 2026-06-02).

    Avant : `_LLM_API_KEY = os.getenv("LLM_API_KEY", "")` figeait la valeur
    au moment de l'import du module Python. Une mise a jour de l'env via
    le webhook /api/reload-llm-key (POST par le hub apres lecture du Secret
    SSPCloud AI Assistant) n'etait pas visible : il fallait un restart pod.
    En lisant os.environ a chaque appel LLM, on rend l'agent capable de
    pick-up la nouvelle cle sans restart -> bug C+D structurellement
    resolu, downtime ~0s.

    Cache impossible : le caller fait potentiellement 100+ chunks SSE par
    requete, mais le cout d'un os.environ.get est negligeable (~µs).
    """
    return os.environ.get("LLM_API_KEY", "")

# Modèle par profil :
# qwen3-6-35b-moe : function calling natif + thinking separe (reasoning_content)
#   → optimal pour agentique (appelle les tools direct au lieu d'expliquer).
#   Down SSPCloud 2026-06-09 -> 2026-06-12 (~3 jours), retabli avec
#   system_fingerprint=vllm-0.22.0. Restaure comme default (re-test 2026-06-12).
# gemma4-26b-moe  : vision base64 natif + chat + function calling.
#   Reste optimal pour validation images GeoAI (profil geoai_analyst).
#   Moins agressif sur tool_calls que qwen3 -> tend a narrer ("je vais X")
#   au lieu d'agir. Acceptable fallback chat si qwen3 down.
# Override possible via env LLM_MODEL (kubectl set env sts/qgis-agent LLM_MODEL=...).
# Backlog : discovery dynamique au boot (~30 LOC) pour basculer auto si rotation.
_MODEL_BY_PROFILE = {
    "geoai_analyst": "gemma4-26b-moe",     # vision pour validation détections
    "default":       "qwen3-6-35b-moe",    # function calling natif, agentique
}

def _get_model(profile_id: str) -> str:
    return _MODEL_BY_PROFILE.get(profile_id, _MODEL_BY_PROFILE["default"])

# ── Config Hub MCP ─────────────────────────────────────────────────────────────
# HUB_URL est OBLIGATOIRE : injecte par hub.main._bootstrap_agent dans l'env du
# pod agent. Sans elle, l'agent ne peut pas atteindre le hub -> tools MCP KO,
# profils non chargeables, etc. Fail-fast au demarrage plutot que de silently
# tomber sur un fallback legacy `qgis-mcp-bridge` qui n'existe plus en prod.
_ONYXIA_USER = os.getenv("ONYXIA_USER", "")
_HUB_URL     = os.getenv("HUB_URL", "").rstrip("/")
_HUB_KEY     = os.getenv("HUB_API_KEY", os.getenv("QGIS_API_KEY", ""))

if not _HUB_URL:
    # Pas de raise immediate (le module est importe au moment ou les env vars
    # ne sont peut-etre pas encore disponibles dans certains contextes de
    # test). On loggue fort ; l'enforcement strict est dans agent.main.startup.
    log.warning(
        "qgis_agent : HUB_URL absent au load du module. "
        "L'agent ne pourra pas atteindre le hub. Verifiez l'injection env "
        "dans le StatefulSet (hub._bootstrap_agent)."
    )

# ── Système de profils ─────────────────────────────────────────────────────────
#
# Source de verite : YAML cote hub (`hub/hub/profiles/*.yaml`). L'agent les
# fetch via HTTP au demarrage du pod et les met en cache permanent (Option β
# validee 2026-05-30). Bug historique : un `_PROFILES_DIR` filesystem pointait
# vers un repo inexistant -> tous les profils etaient inertes en prod.
#
# Cache module-level : peuple par `fetch_profiles_from_hub()` appele dans le
# startup event de agent.main. Refresh manuel via POST /api/refresh-profiles
# (utile apres modification YAML cote hub + /profiles/reload).
_PROFILES_CACHE: dict[str, dict] = {}


async def fetch_profiles_from_hub() -> int:
    """Recupere tous les profils depuis le hub et peuple `_PROFILES_CACHE`.

    Appele au startup de l'agent (agent.main.startup) et par l'endpoint
    /api/refresh-profiles. En cas d'echec (hub down au boot), on log un
    warning mais le pod demarre quand meme avec cache vide -> fallback
    prompt generique cf. `_load_profile_prompt`.

    Returns:
        Nombre de profils charges en cache.
    """
    global _PROFILES_CACHE
    if not _HUB_URL:
        log.warning("fetch_profiles : HUB_URL absent, profils non charges")
        return 0
    if not _HUB_KEY:
        log.warning("fetch_profiles : HUB_API_KEY absent, profils non charges")
        return 0
    headers = {"Authorization": f"Bearer {_HUB_KEY}"}
    try:
        async with httpx.AsyncClient(timeout=15) as client:
            # /profiles renvoie la liste des id + meta (pas le full YAML)
            r_list = await client.get(f"{_HUB_URL}/profiles", headers=headers)
            if r_list.status_code != 200:
                log.warning("fetch_profiles : /profiles HTTP %d", r_list.status_code)
                return 0
            profile_ids = [p.get("id") for p in r_list.json() if p.get("id")]
            # Pour chaque id, fetch le YAML complet (avec agent_system_prompt,
            # mcp_tools.allowed, etc.) via /profiles/{id}
            new_cache: dict[str, dict] = {}
            for pid in profile_ids:
                try:
                    r = await client.get(f"{_HUB_URL}/profiles/{pid}", headers=headers)
                    if r.status_code == 200:
                        data = r.json()
                        if isinstance(data, dict):
                            new_cache[pid] = data
                except Exception as exc:
                    log.warning("fetch_profiles : %s indispo : %s", pid, exc)
            _PROFILES_CACHE = new_cache
            log.info("fetch_profiles : %d profils en cache (%s)",
                     len(_PROFILES_CACHE), ", ".join(_PROFILES_CACHE.keys()))
            return len(_PROFILES_CACHE)
    except Exception as exc:
        log.warning("fetch_profiles : echec global : %s", exc)
        return 0


def _load_profile_prompt(profile_id: str) -> str:
    """Charge le system prompt d'un profil depuis le cache module-level.

    Fallback chaine vide si profil absent du cache (par exemple si le
    fetch initial a echoue). Le code amont (qgis_agent._build_prompt)
    detecte la chaine vide et applique un prompt generique de secours.
    """
    profile = _PROFILES_CACHE.get(profile_id, {})
    return profile.get("agent_system_prompt", "")


def _get_profile_tools_whitelist(profile_id: str) -> list[str] | None:
    """Retourne la whitelist mcp_tools.allowed du profil, ou None.

    Convention YAML : `mcp_tools.allowed = "all"` ou `[liste, de, tools]`.
    Retourne None si le profil est absent du cache OU si allowed="all" :
    dans ces deux cas, pas de filtrage cote `_get_mcp_tools` (compat
    arriere). Sinon une liste de noms exacts pour intersection.
    """
    profile = _PROFILES_CACHE.get(profile_id, {})
    mcp_tools_cfg = profile.get("mcp_tools", {}) or {}
    allowed = mcp_tools_cfg.get("allowed")
    if allowed is None or allowed == "all":
        return None
    if isinstance(allowed, list):
        return [str(t) for t in allowed]
    return None


# ── Outils MCP disponibles ─────────────────────────────────────────────────────

async def _get_mcp_tools(profile_id: str = "standard") -> list[dict]:
    """Récupère la liste des outils MCP du hub, filtrée selon le profil.

    Le YAML profil declare `mcp_tools.allowed = "all"` (tous les tools) OU
    une liste explicite. Le filtrage applique l'intersection avec la liste
    full retournee par le hub. Si le profil n'est pas dans le cache ou si
    `allowed = "all"`, comportement legacy : on retourne tous les tools.

    Egalement applique `mcp_tools.disabled = [...]` (blacklist additionnelle)
    si declare. Permet : risk_analyst exclut explicitement `delete_file`,
    storymap_creator exclut `restart_qgis_engine`, etc.
    """
    if not _HUB_URL or not _HUB_KEY:
        return []
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.post(
                f"{_HUB_URL}/mcp",
                json={"jsonrpc": "2.0", "id": 1, "method": "tools/list"},
                headers={"Authorization": f"Bearer {_HUB_KEY}"},
            )
            data = resp.json()
            all_tools = data.get("result", {}).get("tools", [])
    except Exception as e:
        log.warning("MCP tools non récupérés: %s", e)
        return []

    # Filtrage par profil : whitelist (allowed) + blacklist (disabled).
    # Si profil pas dans le cache (fetch initial pas encore reussi, ou
    # profile_id custom), on revient au comportement legacy (tous les tools).
    profile = _PROFILES_CACHE.get(profile_id, {})
    mcp_tools_cfg = profile.get("mcp_tools", {}) or {}
    allowed = mcp_tools_cfg.get("allowed")
    disabled = set(mcp_tools_cfg.get("disabled", []) or [])

    filtered = all_tools
    if isinstance(allowed, list) and allowed:
        allowed_set = set(allowed)
        filtered = [t for t in filtered if t.get("name") in allowed_set]
    if disabled:
        filtered = [t for t in filtered if t.get("name") not in disabled]

    if len(filtered) != len(all_tools):
        log.info(
            "MCP tools filtres pour profil '%s' : %d/%d (allowed=%s, disabled=%d)",
            profile_id, len(filtered), len(all_tools),
            "list" if isinstance(allowed, list) else (allowed or "all"),
            len(disabled),
        )
    else:
        log.info("MCP tools (profil '%s', non filtre) : %d", profile_id, len(filtered))
    return filtered


def _extract_error_signature(tool_result: str) -> str | None:
    """Extrait une signature fuzzy d'erreur d'un résultat MCP, ou None.

    Sert à détecter les boucles : on neutralise les variables (types quoted,
    chiffres, paths) pour reconnaître "même classe d'erreur" même quand
    l'agent essaie des variantes.
    Ex: « unexpected type 'int' » et « unexpected type 'list' » → même sig.

    Bug historique : `json.loads(tool_result)` échouait quand le résultat
    contient un JSON pretty-printed multi-ligne suivi de `\\n![img](data:...)`.
    Le fallback `splitlines()[0]` retournait alors juste « { », fusionnant
    toutes les erreurs distinctes en une même signature et déclenchant la
    garde anti-boucle à tort. Fix : extraire le champ "error" via regex
    (même approche que _maybe_enrich_with_kb_hint), résiliente au mix
    JSON + markdown.
    """
    if not tool_result:
        return None
    lower = tool_result.lower()
    error_markers = ('"error"', '"success": false', 'traceback', 'exception')
    if not any(m in lower for m in error_markers):
        return None

    raw = ""
    # 1) Essai parse JSON pur
    try:
        data = json.loads(tool_result)
        if isinstance(data, dict):
            err = data.get("error") or ""
            if isinstance(err, str) and err:
                raw = err.splitlines()[0]
    except Exception:
        pass

    # 2) Fallback regex sur le champ "error" (résiliente au mix JSON+markdown)
    if not raw:
        head = tool_result[:5000]
        m_err = re.search(r'"error"\s*:\s*"((?:[^"\\]|\\.)*)"', head)
        if m_err:
            try:
                err = m_err.group(1).encode().decode('unicode_escape', errors='ignore')
            except Exception:
                err = m_err.group(1)
            if err:
                raw = err.splitlines()[0]

    # 3) Fallback regex sur "traceback" (capture la dernière ligne exception)
    if not raw:
        head = tool_result[:5000]
        m_tb = re.search(r'"traceback"\s*:\s*"((?:[^"\\]|\\.)*)"', head)
        if m_tb:
            try:
                tb = m_tb.group(1).encode().decode('unicode_escape', errors='ignore')
            except Exception:
                tb = m_tb.group(1)
            # Dernière ligne non vide d'une traceback = la classe d'exception
            tb_lines = [ln.strip() for ln in tb.splitlines() if ln.strip()]
            if tb_lines:
                raw = tb_lines[-1]

    # 4) Dernier recours : 1re ligne non triviale du payload
    if not raw:
        for line in tool_result.splitlines():
            stripped = line.strip()
            # On ignore les lignes structurelles JSON ({, }, [, ], "key": {)
            if stripped and stripped not in ('{', '}', '[', ']') and not re.match(r'^"[^"]+":\s*[{\[]?\s*$', stripped):
                raw = stripped
                break
        if not raw:
            return None

    # Fuzzify : retire valeurs entre guillemets/apostrophes, nombres, paths
    fuzzy = re.sub(r"'[^']*'|\"[^\"]*\"", "X", raw)
    fuzzy = re.sub(r"\b\d+\b", "N", fuzzy)
    fuzzy = re.sub(r"/[^\s\"']+", "PATH", fuzzy)
    return fuzzy[:120]


_INFRA_FAILURE_PATTERNS = (
    "QGIS command timed out after",      # qgis_command timeout côté MCP
    "Cannot connect to QGIS api_server", # connect refused
    "Bridge HTTP error:",                # HTTP-level failure (502/503/504)
    "504 Gateway Timeout",               # smart_load trop lourd
    "502 Bad Gateway",                   # api_server down
    "503 Service Unavailable",           # bridge KO
)


def _is_infra_failure(tool_result: str) -> bool:
    """True si l'erreur tool est un SYMPTÔME D'INFRASTRUCTURE (bridge QGIS
    dégradé) plutôt qu'une erreur de code utilisateur.

    Critères :
    - Timeout du bridge (`QGIS command timed out`)
    - Connect refused (`Cannot connect`)
    - HTTP layer error (`Bridge HTTP error`, `504/502/503`)
    - Erreur muette `{"error": ""}` (typique d'un SSE coupé prématurément
      ou d'une réponse tronquée — observé sur run_recipe)

    Sert à distinguer "le moteur est KO" de "le code de l'agent a un bug"
    (NoneType, QgsFields slice, etc. = erreurs métier). Sur les premiers
    cas, l'agent doit ESCALADER à l'user (proposer un redémarrage),
    pas retenter en boucle.
    """
    if not tool_result:
        return False
    # Pattern direct sur la string
    for p in _INFRA_FAILURE_PATTERNS:
        if p in tool_result:
            return True
    # Erreur muette : `"error": ""` (ou `"error":""` sans espace)
    if re.search(r'"error"\s*:\s*""', tool_result):
        return True
    return False


async def _maybe_enrich_with_kb_hint(response: str) -> str:
    """Hook auto post-erreur : si la réponse tool contient une erreur, cherche
    dans la KB qgis_tips (vector_store source_type="qgis_tip") un tip dont le
    symptom matche sémantiquement. Si match ≥ 0.55, suffixe la réponse avec
    un bloc 💡 KB tip (pattern + note). Sinon retourne la réponse inchangée.

    Pourquoi côté backend, pas comme tool explicite : zéro latence ajoutée,
    zéro discipline LLM requise, l'agent voit error+fix dans le même payload.
    """
    if not response:
        return response
    error_text = ""
    # Détection erreur : la réponse tool est un mix `{json}\n--- Context: ...\n![img](data:...)`
    # (texte + screenshot inline). json.loads échouerait sur le mix global, donc
    # on extrait directement les champs "error" et "traceback" via regex sur les
    # 5000 premiers caractères (le JSON tool est toujours au début, l'image
    # base64 est volumineuse mais en fin).
    head = response[:5000]
    m_err = re.search(r'"error"\s*:\s*"((?:[^"\\]|\\.)*)"', head)
    m_tb  = re.search(r'"traceback"\s*:\s*"((?:[^"\\]|\\.)*)"', head)
    if m_err:
        err = m_err.group(1).encode().decode('unicode_escape', errors='ignore')
        tb = ""
        if m_tb:
            tb = m_tb.group(1).encode().decode('unicode_escape', errors='ignore')
        error_text = (err + " " + tb)[:600].strip()
    # Fallback : si pas de JSON-like détecté mais "error" ou "traceback" dans head
    elif "error" in head.lower()[:200] or "traceback" in head.lower()[:200]:
        error_text = head[:600].strip()

    if not error_text:
        return response

    try:
        from agent import vector_store
        hits = await vector_store.search(
            query=error_text, top_k=1, source_type="qgis_tip",
        )
    except Exception:
        return response

    if not hits:
        return response
    h = hits[0]
    if h.get("similarity", 0.0) < 0.55:
        return response

    meta = h.get("metadata") or {}
    title = meta.get("title") or h.get("source_id") or "tip"
    pattern = meta.get("pattern", "")
    note = meta.get("note", "")

    # Hint avec DIRECTIVE IMPÉRATIVE : l'agent doit appliquer le pattern et
    # retenter SILENCIEUSEMENT. L'utilisateur final n'est PAS codeur — il ne
    # doit pas voir l'erreur Python technique ni le hint. L'agent corrige en
    # background et passe à la suite comme si l'erreur n'avait pas existé.
    hint = (
        f"\n\n💡 [KB AUTO-CORRECTION — INSTRUCTION INTERNE, ne pas narrer à l'user]"
        f"\nTip pertinent : **{title}**"
        f"\nPattern à appliquer immédiatement :\n```python\n{pattern}\n```"
    )
    if note:
        hint += f"\nNote : {note}"
    hint += (
        f"\n\n⚠️ ACTION REQUISE : applique ce pattern et RETENTE tout de suite. "
        f"N'explique PAS l'erreur technique à l'user (il n'est pas codeur). "
        f"Continue silencieusement, comme si la 1ère tentative n'avait pas eu lieu."
    )
    return response + hint


async def _call_mcp_tool(tool_name: str, arguments: dict, username: str = "user") -> str:
    """Wrapper : appelle _call_mcp_tool_raw + enrichit la réponse via la KB
    qgis_tips si une erreur est détectée (hook auto post-erreur)."""
    response = await _call_mcp_tool_raw(tool_name, arguments, username)
    return await _maybe_enrich_with_kb_hint(response)


async def _resolve_active_sid(username: str) -> str | None:
    """V1.5 Sprint 1 : resolve le sid de l'etude active de l'user via le hub.

    Utilise par les tools natifs recipes (qui ont besoin du sid pour faire
    les appels REST /studies/{sid}/recipes/...). Cache pas car l'etude peut
    changer en cours de session (switch via UI desk).
    """
    if not _HUB_URL or not _HUB_KEY:
        return None
    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            r = await client.get(
                f"{_HUB_URL}/studies/active",
                headers={"Authorization": f"Bearer {_HUB_KEY}"},
            )
            if r.status_code != 200:
                return None
            data = r.json()
            return data.get("id")
    except Exception:
        return None


async def _call_mcp_tool_raw(tool_name: str, arguments: dict, username: str = "user") -> str:
    """Appelle un outil. Court-circuite les outils natifs (mémoire + recipes
    user) côté agent avant de tenter le MCP hub QGIS."""

    # V1.5 Sprint 1 : tools natifs recipes -> dispatch hub REST.
    _RECIPE_TOOLS = {
        "save_recipe", "get_recipe", "list_recipes_for_study",
        "delete_recipe", "get_recipe_history",
    }
    # NB : on EXCLUT `get_recipe` du dispatch natif si le LLM passe `id` (kwarg
    # du tool MCP system) au lieu de `slug` -> on retombe sur le MCP normal.
    if tool_name in _RECIPE_TOOLS and (
        tool_name != "get_recipe" or "slug" in arguments
    ):
        if not _HUB_URL or not _HUB_KEY:
            return json.dumps({"error": "Hub non configure (recipes user)"})
        sid = arguments.get("sid") or await _resolve_active_sid(username)
        if not sid:
            return json.dumps({"error": "Aucune etude active (cree-en une d'abord)"})
        slug = arguments.get("slug", "")
        try:
            async with httpx.AsyncClient(timeout=30.0) as client:
                headers = {"Authorization": f"Bearer {_HUB_KEY}"}
                if tool_name == "save_recipe":
                    body = {
                        "content": arguments.get("content", ""),
                        "format": arguments.get("format", "yaml"),
                        "name": arguments.get("name", ""),
                        "description": arguments.get("description", ""),
                    }
                    r = await client.put(
                        f"{_HUB_URL}/studies/{sid}/recipes/{slug}",
                        json=body, headers=headers,
                    )
                elif tool_name == "get_recipe":
                    r = await client.get(
                        f"{_HUB_URL}/studies/{sid}/recipes/{slug}",
                        headers=headers,
                    )
                elif tool_name == "list_recipes_for_study":
                    r = await client.get(
                        f"{_HUB_URL}/studies/{sid}/recipes",
                        headers=headers,
                    )
                elif tool_name == "delete_recipe":
                    r = await client.delete(
                        f"{_HUB_URL}/studies/{sid}/recipes/{slug}",
                        headers=headers,
                    )
                elif tool_name == "get_recipe_history":
                    r = await client.get(
                        f"{_HUB_URL}/studies/{sid}/recipes/{slug}/history",
                        headers=headers,
                    )
                else:
                    return json.dumps({"error": f"Tool inconnu: {tool_name}"})
            if r.status_code in (200, 204):
                if r.status_code == 204:
                    return json.dumps({"ok": True, "slug": slug, "deleted": True})
                return r.text  # JSON deja
            return json.dumps({
                "error": f"hub {tool_name} HTTP {r.status_code}",
                "detail": r.text[:300],
            })
        except Exception as e:
            return json.dumps({"error": f"{tool_name} fail: {e}"})

    # Outils natifs côté agent — n'appellent pas le hub MCP.
    if tool_name in ("memory_search", "memory_similar"):
        try:
            from agent import vector_store
            query    = arguments.get("query", "")
            top_k    = int(arguments.get("top_k", 5))
            # memory_similar : forcé sur insights (cf. tool description).
            if tool_name == "memory_similar":
                src_type = "insight"
            else:
                src_type = arguments.get("source_type")
            uname    = username
            results  = await vector_store.search(
                query=query, top_k=top_k,
                source_type=src_type, username=uname,
            )
            # Présentation compacte pour le LLM
            return json.dumps({
                "query": query,
                "count": len(results),
                "results": [
                    {
                        "similarity":  round(r["similarity"], 3),
                        "source_type": r["source_type"],
                        "text":        r["text"][:300],
                        "metadata":    r.get("metadata"),
                    }
                    for r in results
                ],
            }, ensure_ascii=False)
        except Exception as e:
            return json.dumps({"error": f"memory_search fail: {e}"})

    if not _HUB_URL or not _HUB_KEY:
        return json.dumps({"error": "Hub non configuré"})

    # Timeout dynamique par tool — certains tools peuvent légitimement tourner
    # plusieurs minutes (run_recipe = jusqu'à 20 min côté MCP serveur). Un
    # timeout client trop court provoquait des `{"error": ""}` muets sur les
    # recettes lourdes (observé sur risque_inondation Marseille).
    _LONG_RUNNING_TOOLS = {
        "run_recipe":          1800,  # 20+ min serveur, on prend large
        "smart_load":           600,  # WFS download lourds (BD TOPO commune)
        "execute_python":       900,  # spatial joins sur 100k+ features
        "set_study_zone":       300,
        "export_flood_map":     600,
        "export_web_map":       600,
        "export_temporal_map":  600,
        "add_from_catalog":     600,
    }
    tool_timeout = _LONG_RUNNING_TOOLS.get(tool_name, 120)
    # Bug #17a/b fix (cold start workspace MCP) : retry avec backoff sur
    # erreurs transitoires. Le workspace pod peut etre Ready (Xvfb + QGIS
    # OK) mais le serveur MCP intra-pod (port 8100) pas encore joignable
    # pendant les premieres ~90s. Sans retry, l'agent abandonne au premier
    # essai avec un JSON parse error muet et l'experience premier-tour est
    # cassee. Ref mémoire project_bug_17_mcp_cold_start_502.
    #
    # Conditions de retry :
    #   - HTTP 502/503/504 (hub/ingress en cours de boot)
    #   - Body vide (le hub _proxy_request renvoie "" si pod KO)
    #   - JSON parse failure (le body est du HTML d'erreur ingress)
    # Pas de retry sur 4xx (bug applicatif, retry ne resout pas).
    _RETRY_DELAYS = [2.0, 5.0, 10.0]  # cumul 17s = max acceptable UX
    try:
        async with httpx.AsyncClient(timeout=tool_timeout) as client:
            data = None
            last_error = None
            for attempt in range(len(_RETRY_DELAYS) + 1):
                try:
                    resp = await client.post(
                        f"{_HUB_URL}/mcp",
                        json={
                            "jsonrpc": "2.0",
                            "id":      str(uuid.uuid4()),
                            "method":  "tools/call",
                            "params":  {"name": tool_name, "arguments": arguments},
                        },
                        headers={"Authorization": f"Bearer {_HUB_KEY}"},
                    )
                    # Status 5xx ou body vide -> transient, retry
                    if resp.status_code in (502, 503, 504):
                        last_error = f"HTTP {resp.status_code} (transient)"
                        if attempt < len(_RETRY_DELAYS):
                            log.warning(
                                "MCP cold start? %s -> retry %d/%d in %.0fs",
                                last_error, attempt + 1, len(_RETRY_DELAYS),
                                _RETRY_DELAYS[attempt],
                            )
                            await asyncio.sleep(_RETRY_DELAYS[attempt])
                            continue
                        break
                    body_text = resp.text
                    if not body_text.strip():
                        last_error = "body vide (cold start?)"
                        if attempt < len(_RETRY_DELAYS):
                            log.warning(
                                "MCP body vide -> retry %d/%d in %.0fs",
                                attempt + 1, len(_RETRY_DELAYS),
                                _RETRY_DELAYS[attempt],
                            )
                            await asyncio.sleep(_RETRY_DELAYS[attempt])
                            continue
                        break
                    try:
                        data = resp.json()
                        break  # success
                    except (json.JSONDecodeError, ValueError) as je:
                        last_error = f"JSON parse fail: body[:80]={body_text[:80]!r}"
                        if attempt < len(_RETRY_DELAYS):
                            log.warning(
                                "MCP JSON parse fail -> retry %d/%d in %.0fs",
                                attempt + 1, len(_RETRY_DELAYS),
                                _RETRY_DELAYS[attempt],
                            )
                            await asyncio.sleep(_RETRY_DELAYS[attempt])
                            continue
                        break
                except httpx.ConnectError as ce:
                    # Reseau pas pret (pod en cours de scale up)
                    last_error = f"ConnectError: {ce}"
                    if attempt < len(_RETRY_DELAYS):
                        log.warning(
                            "MCP ConnectError -> retry %d/%d in %.0fs",
                            attempt + 1, len(_RETRY_DELAYS),
                            _RETRY_DELAYS[attempt],
                        )
                        await asyncio.sleep(_RETRY_DELAYS[attempt])
                        continue
                    raise
            if data is None:
                return json.dumps({
                    "error": (
                        f"MCP cold start ou hub down apres {len(_RETRY_DELAYS)+1} essais : "
                        f"{last_error}. Attends ~30s et reessaye."
                    )
                })
            result = data.get("result", {})
            content = result.get("content", [])
            if content and isinstance(content, list):
                # Concatène texte + image (en data URL markdown) — l'image
                # est rendue inline par marked.parse côté chat.
                parts: list[str] = []
                for c in content:
                    ctype = c.get("type")
                    if ctype == "text":
                        parts.append(c.get("text", ""))
                    elif ctype == "image":
                        b64 = c.get("data", "")
                        mime = c.get("mimeType", "image/png")
                        if b64:
                            parts.append(
                                f"![{tool_name}](data:{mime};base64,{b64})"
                            )
                joined = "\n".join(p for p in parts if p)
                # Réécriture des URLs internes au pod workspace → URL hub
                # publique. Le serveur MCP du workspace ignore qu'il est
                # derrière un proxy ; il retourne `localhost:8080/api/files/X`
                # qui n'est joignable que depuis l'intérieur du pod. Le hub
                # expose `/files/{path}` qui proxy vers le service K8s.
                joined = re.sub(
                    r"http://localhost:\d+/api/files/",
                    f"{_HUB_URL}/files/",
                    joined,
                )
                return joined
            return json.dumps(result)
    except httpx.TimeoutException as e:
        # Erreur de timeout explicite (au lieu de str(e)="" muet sur certaines
        # exceptions httpx) — l'agent et le détecteur infra peuvent réagir.
        return json.dumps({
            "error": f"MCP client timeout après {tool_timeout}s sur tool `{tool_name}` "
                     f"(httpx.{type(e).__name__})"
        })
    except Exception as e:
        # Fallback class name si str(e) est vide (cas observé : SSE coupé,
        # certaines RemoteProtocolError, etc. → "" empêchait l'agent et le
        # détecteur infra de classifier l'erreur).
        msg = str(e) or f"{type(e).__name__} (no message)"
        return json.dumps({"error": f"MCP call failed: {msg}"})


# ── Conversion outils MCP → format OpenAI function calling ────────────────────

def _mcp_tool_to_openai(tool: dict) -> dict:
    """Convertit un outil MCP en format OpenAI function calling."""
    schema = tool.get("inputSchema", {"type": "object", "properties": {}})
    return {
        "type": "function",
        "function": {
            "name":        tool["name"],
            "description": tool.get("description", ""),
            "parameters":  schema,
        }
    }


# ── Outils natifs côté agent (mémoire sémantique) ────────────────────────
# Dispatchés localement par _call_mcp_tool, donc 0 latence réseau, et l'agent
# voit la mémoire comme n'importe quel autre tool.
# Tools dont l'appel laisse une trace mesurable dans le projet QGIS courant
# (couche ajoutée/retirée, style modifié, géoprocessing, etc.). Servent de
# trigger pour l'auto-save post-turn de l'étude active. Lecture-seule
# (get_*, list_*, export_*) volontairement exclus.
_MUTATING_TOOLS: frozenset[str] = frozenset({
    "smart_load",
    "add_layer",
    "add_from_catalog",
    "remove_layer",
    "set_layer_style",
    "set_layer_visibility",
    "run_processing",
    "run_recipe",
    "execute_python",
    "apply_layout_template",
    "set_study_zone",
    "new_project",
    "open_project",
    "save_project",
    "upload_file",
    "zoom_to",
})


# V1.5 Sprint 1 : tools natifs recipes user (CRUD + list).
# Pattern : meme dispatch que _NATIVE_MEMORY_TOOLS (court-circuite le hub MCP,
# mais appelle quand meme le hub via REST `/studies/{sid}/recipes/...`).
# Le `sid` est resolu automatiquement via /studies/active si absent (l'agent
# travaille toujours dans l'etude courante du user).
_NATIVE_RECIPE_TOOLS: list[dict] = [
    {
        "type": "function",
        "function": {
            "name": "save_recipe",
            "description": (
                "Sauvegarde une recette user dans l'etude courante. Premier "
                "save = creation, save suivant avec meme slug = nouvelle "
                "version (versioning SHA conserve l'historique). Le contenu "
                "est du YAML decrivant la recette (id, name, parameters, "
                "steps[tool, params, code]). Format identique aux recettes "
                "system mais editable."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "slug": {
                        "type": "string",
                        "description": "Identifiant court de la recette (ex: 'ma_recette_inondation')",
                    },
                    "content": {
                        "type": "string",
                        "description": "Contenu YAML complet de la recette.",
                    },
                    "name": {
                        "type": "string",
                        "description": "Nom affiche dans l'UI (optionnel, fallback slug).",
                    },
                    "description": {
                        "type": "string",
                        "description": "Description courte de ce que fait la recette (optionnel).",
                    },
                    "format": {
                        "type": "string",
                        "enum": ["yaml", "json"],
                        "default": "yaml",
                    },
                },
                "required": ["slug", "content"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_recipe",
            "description": (
                "Retourne le contenu YAML d'une recette user de l'etude "
                "courante. Pour les recettes system (deja embarquees dans "
                "/app/recipes), utilise plutot le tool MCP `get_recipe`."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "slug": {"type": "string"},
                },
                "required": ["slug"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "list_recipes_for_study",
            "description": (
                "Liste les recettes user de l'etude courante (uniquement les "
                "actives, latest version). Retourne slug, format, sha, "
                "version_num, name, description. Differe du tool MCP "
                "`list_recipes` qui liste TOUTES les recipes (system + user) "
                "vues par le workspace QGIS."
            ),
            "parameters": {
                "type": "object",
                "properties": {},
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "delete_recipe",
            "description": (
                "Soft-delete une recette user (archive). Recuperable en "
                "consultant l'historique (`get_recipe_history`). Le fichier "
                "sur le PVC est renomme `.archived.<ts>`, pas supprime."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "slug": {"type": "string"},
                },
                "required": ["slug"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_recipe_history",
            "description": (
                "Toutes les versions d'une recette (actives + archivees) "
                "avec leur SHA, version_num, created_at. Utile pour audit "
                "ou restauration manuelle."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "slug": {"type": "string"},
                },
                "required": ["slug"],
            },
        },
    },
]


_NATIVE_MEMORY_TOOLS: list[dict] = [
    {
        "type": "function",
        "function": {
            "name": "memory_search",
            "description": (
                "Recherche sémantique dans la mémoire (messages passés, insights, "
                "mémoire structurée). À utiliser SEULEMENT si le 'Rappel sémantique "
                "automatique' déjà présent dans ton system prompt ne contient pas "
                "l'information précise dont tu as besoin (valeur exacte, session "
                "antérieure particulière, détail manquant). N'appelle PAS en plus "
                "si le rappel auto suffit — tu gaspilles un tour LLM."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "Requête en langage naturel.",
                    },
                    "top_k": {
                        "type": "integer",
                        "description": "Nombre de résultats à retourner (défaut 5).",
                        "default": 5,
                    },
                    "source_type": {
                        "type": "string",
                        "description": (
                            "Filtrer sur un type de source : 'message' "
                            "(échanges passés), 'insight' (préférences/habitudes "
                            "apprises), 'memory_doc' (mémoire structurée user). "
                            "Omettre pour chercher partout."
                        ),
                        "enum": ["message", "insight", "memory_doc"],
                    },
                },
                "required": ["query"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "memory_similar",
            "description": (
                "Variante de memory_search ciblée sur les insights métier "
                "(préférences, méthodes, zones). Mêmes règles : si le rappel "
                "auto présent dans ton prompt système couvre déjà la question, "
                "n'appelle pas. À réserver aux cas où il faut sonder largement "
                "le profil utilisateur."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {"type": "string"},
                    "top_k": {"type": "integer", "default": 3},
                },
                "required": ["query"],
            },
        },
    },
]


# ── Agent principal ────────────────────────────────────────────────────────────

class QGISAgent:
    """
    Agent QGIS spécialisé.
    Orchestre LLM SSPCloud + outils MCP + mémoire long-terme.
    """

    def __init__(self, username: str, session_id: str, profile_id: str = "standard"):
        self.username   = username
        self.session_id = session_id
        self.profile_id = profile_id
        self._tools_cache: list[dict] | None = None
        # L2 project state mis a jour au debut de chaque turn par chat_stream.
        # Lu par le tool loop pour detecter les args spatiaux generiques
        # (ex: zone="Marseille") qui contredisent une bbox plus precise deja
        # active dans le projet QGIS courant. Cf. _zone_context_warning.
        self._project_state: dict | None = None

    async def _get_tools(self) -> list[dict]:
        if self._tools_cache is None:
            mcp_tools = await _get_mcp_tools(self.profile_id)
            self._tools_cache = [_mcp_tool_to_openai(t) for t in mcp_tools]
            # Outils natifs mémoire : exposés à l'agent comme n'importe quel
            # tool MCP. Le dispatch local court-circuite le hub QGIS.
            self._tools_cache.extend(_NATIVE_MEMORY_TOOLS)
            # V1.5 Sprint 1 : tools natifs recipes user. Dispatch local appelle
            # le hub REST /studies/{sid}/recipes/... Filtrage cohérent avec le
            # mcp_tools whitelist du profil : si pas "all" et `save_recipe`
            # absent de la whitelist, on n'expose pas les recipe tools natifs.
            whitelist = _get_profile_tools_whitelist(self.profile_id)
            if whitelist is None or "save_recipe" in whitelist:
                self._tools_cache.extend(_NATIVE_RECIPE_TOOLS)
        return self._tools_cache

    async def set_profile(self, new_profile_id: str) -> None:
        """
        Switch en cours de conversation. Recharge les tools + system prompt
        au prochain turn. Réinitialise le cache.
        """
        if new_profile_id == self.profile_id:
            return
        self.profile_id = new_profile_id
        self._tools_cache = None  # forcer rechargement

    _SWITCH_INSTRUCTIONS = (
        "\n\n— PROFIL DYNAMIQUE (ROUTAGE CONTEXTUEL) —\n"
        "Ton profil suit le contexte de l'utilisateur (étude active, livrable "
        "sélectionné). Tu peux ET DOIS changer de profil dès que la demande "
        "user bascule clairement sur un autre champ — pas besoin que l'user "
        "le demande explicitement.\n\n"
        "Triggers d'intent à détecter (réagis dès que l'user formule une de "
        "ces intentions) :\n"
        "  • « crée/produis/rédige une storymap »          → storymap_creator\n"
        "  • « écris/sauve une recette »                    → recipe_creator\n"
        "  • « détecte/segmente avec SAM/DeepForest »       → geoai_analyst\n"
        "  • « charge un .gpkg/.csv », « requête SQL »      → db_analyst\n"
        "  • « croise risque/PPRi/T100/inondation »         → risk_analyst\n"
        "  • « compose/exporte une carte »                  → map_composer\n"
        "  • « explique-moi le service / comment faire ? »  → guided_tour\n\n"
        "Pour switcher, inclus dans ta réponse (une seule fois, en début "
        "ou fin) :\n"
        "<switch_profile>NOM_PROFIL</switch_profile>\n"
        "Le système l'intercepte, ne montre rien à l'user, et applique le "
        "switch pour les turns suivants. Profils dispo : standard, "
        "geoai_analyst, risk_analyst, db_analyst, recipe_creator, "
        "storymap_creator, map_composer, guided_tour."
    )

    _REMEMBER_INSTRUCTIONS = (
        "\n\n— MÉMOIRE LONG TERME —\n"
        "Quand l'user te révèle une préférence durable, un fait stable sur lui, "
        "son métier, sa zone d'étude habituelle, ou un style cartographique "
        "récurrent, tu peux le mémoriser pour les conversations futures. "
        "Inclus dans ta réponse (caché à l'user) :\n"
        "<remember>clé:valeur</remember>\n"
        "Exemples :\n"
        "  <remember>preferred_zone:Le Lavandou</remember>\n"
        "  <remember>metier:chargé d'études risques inondations</remember>\n"
        "  <remember>style_pref:palette YlOrRd pour les chiffres clés</remember>\n"
        "À utiliser parcimonieusement : seulement quand l'info est explicitement "
        "ou très clairement durable. L'user pourra voir/effacer ces insights "
        "via le portail."
    )

    async def _fetch_active_study_context(self):
        """Récupère l'étude active + treatments depuis le hub.
        Utilise les constantes module qui ont résolu les fallbacks (URL
        dérivée d'ONYXIA_USER, clé QGIS_API_KEY si HUB_API_KEY absente)."""
        import httpx as _httpx
        if not (_HUB_URL and _HUB_KEY):
            return None, None
        headers = {"Authorization": f"Bearer {_HUB_KEY}"}
        try:
            async with _httpx.AsyncClient(timeout=5) as c:
                ra = await c.get(f"{_HUB_URL}/studies/active", headers=headers)
                if ra.status_code != 200 or not ra.json():
                    return None, None
                study = ra.json()
                rt = await c.get(
                    f"{_HUB_URL}/studies/{study['id']}/treatments",
                    headers=headers, params={"limit": 30},
                )
                treats = rt.json().get("events", []) if rt.status_code == 200 else []
                return study, treats
        except Exception as exc:
            log.warning("Fetch active study failed: %s", exc)
            return None, None

    async def _autosave_active_study(self) -> None:
        """
        Post-turn hook : si un tool modifiant a tourné, appelle l'endpoint
        hub `/studies/{active}/save` pour persister le projet QGIS sur le
        PVC à `/data/studies/{sid}/project.qgz`. Fire-and-forget — toute
        erreur est avalée (un crash de save ne doit pas remonter à l'user).

        On utilise les constantes module `_HUB_URL` / `_HUB_KEY` qui ont
        déjà résolu le fallback `QGIS_API_KEY` et l'URL dérivée d'ONYXIA_USER.
        """
        import httpx as _httpx
        if not (_HUB_URL and _HUB_KEY):
            log.debug("Auto-save skip : hub non configuré (url=%r key=%r)",
                      bool(_HUB_URL), bool(_HUB_KEY))
            return
        headers = {"Authorization": f"Bearer {_HUB_KEY}"}
        try:
            async with _httpx.AsyncClient(timeout=10) as c:
                ra = await c.get(f"{_HUB_URL}/studies/active", headers=headers)
                if ra.status_code != 200 or not ra.json():
                    log.debug("Auto-save skip : pas d'étude active (status=%d)",
                              ra.status_code)
                    return
                sid = ra.json().get("id")
                if not sid:
                    return
                rs = await c.post(f"{_HUB_URL}/studies/{sid}/save", headers=headers)
                if rs.status_code == 200:
                    log.info("Auto-save étude %s OK", sid)
                else:
                    log.warning("Auto-save étude %s status=%d body=%s",
                                sid, rs.status_code, rs.text[:200])
        except Exception as exc:
            log.warning("Auto-save étude fail : %s", exc)

    # Outils dont les args spatiaux generiques peuvent ecraser le contexte L2.
    # On garde-fou : si l'agent passe zone="Marseille" mais que le project_state
    # a deja une bbox "Marseille 4e arrondissement" active, on injecte une note
    # dans le tool result pour signaler le mismatch. L'agent peut soit confirmer
    # (intention de re-elargir) soit re-appeler avec bbox precise.
    _ZONE_SENSITIVE_TOOLS = {
        "set_study_zone": ["target", "name"],
        "run_recipe":     ["zone", "target", "bbox"],
        "smart_load":     ["bbox"],
        "add_from_catalog": ["bbox"],
        "export_flood_map":     ["bbox"],
        "export_web_map":       ["bbox"],
        "export_temporal_map":  ["bbox"],
    }

    # Villes majeures dont le nom seul est ambigu vis-a-vis des arrondissements.
    # Cf. memory _MAJOR_CITY_INSEE de BigQgisMCP : `?nom=Marseille` renvoie
    # Marseillette (Aude) ; sans numero on suppose la commune principale.
    _AMBIGUOUS_MAJOR_CITIES = {"marseille", "paris", "lyon"}

    def _zone_context_warning(self, fn_name: str, fn_args: dict) -> str | None:
        """Retourne un message d'alerte si l'agent passe un nom de ville
        majeure generique alors que le project_state (L2) a deja une bbox
        plus precise active. Retourne None sinon.

        Strategie defensive : on n'ecrit PAS dans fn_args (l'agent peut avoir
        l'intention legitime de reset la zone). On injecte juste une note
        visible dans le tool result, ce qui guide l'agent au turn suivant.
        """
        if fn_name not in self._ZONE_SENSITIVE_TOOLS:
            return None
        ps = self._project_state or {}
        # Cherche une zone active dans le project_state
        zone_active = None
        for k in ("study_zone", "active_zone", "zone"):
            v = ps.get(k)
            if isinstance(v, dict) and v.get("bbox"):
                zone_active = v
                break
        if not zone_active:
            return None
        zone_name = (zone_active.get("name") or "").strip()
        # Si le nom de zone L2 n'est pas un arrondissement/sous-zone, pas
        # d'alerte (rien a perdre a re-confirmer la zone principale).
        import re as _re
        if not _re.search(r"\d|arrondissement|quartier|secteur|district",
                          zone_name, _re.IGNORECASE):
            return None
        # Verifie si un arg attendu pour ce tool contient un nom de ville majeur seul
        suspect = None
        for arg_name in self._ZONE_SENSITIVE_TOOLS[fn_name]:
            v = fn_args.get(arg_name)
            if not isinstance(v, str):
                continue
            v_norm = v.strip().lower()
            # Pas de chiffre, pas de virgule -> probable nom de ville seul
            if _re.search(r"\d", v_norm):
                continue
            for city in self._AMBIGUOUS_MAJOR_CITIES:
                if _re.search(rf"\b{city}\b", v_norm):
                    suspect = (arg_name, v, city)
                    break
            if suspect:
                break
        if not suspect:
            return None
        arg_name, value, city = suspect
        bbox = zone_active.get("bbox")
        return (
            f"\n\n⚠️ NOTE CONTEXTE L2 : Tu as passe `{arg_name}=\"{value}\"` mais "
            f"la zone d'etude active est deja `{zone_name}` avec une bbox plus "
            f"precise : {bbox}. Si tu voulais cibler cette sous-zone, refais le "
            f"call avec `bbox={bbox}` au lieu du nom generique. Si tu veux "
            f"vraiment re-elargir a {city.title()} entier, ignore cette note.\n"
        )

    async def _fetch_project_state(self) -> dict | None:
        """L2 enrichi : récupère l'état du projet QGIS courant.

        Liste des layers chargés, zone d'étude active (variables projet),
        CRS du projet, etc. C'est ce que l'agent VOIT pour décider s'il faut
        recharger ou réutiliser des données existantes.

        Best-effort : si l'appel échoue (workspace endormi, MCP injoignable),
        renvoie None et l'agent fonctionne sans cette info.
        """
        try:
            result = await _call_mcp_tool("get_project_info", {})
            if not result:
                return None
            data = json.loads(result)
            return data if isinstance(data, dict) else None
        except Exception as exc:
            log.warning("Fetch project state failed: %s", exc)
            return None

    # Cheat-sheet PyQGIS minimal injecté dans tous les profils. Évite que
    # l'agent boucle sur les pièges classiques (format EXTENT, etc.).
    # À enrichir au fil des erreurs observées en production.
    _QGIS_ESSENTIALS = """
# ⛔ AVANT TOUT execute_python — 5 RÉFLEXES OBLIGATOIRES

1. ⚙️ **Algo natif d'abord** : avant 30 lignes de PyQGIS, demande-toi
   « existe-t-il un `native:*` ? » Si tu hésites → `search_algorithms("mot-clé")`.
   Top 5 essentiels pour 80% des cas :
   - `native:countpointsinpolygon` — densité / count par maille
   - `native:creategrid` — maillage rectangulaire/hexa
   - `native:joinattributestable` — jointure attributaire
   - `native:zonalstatistics` — stats raster par zone
   - `native:fieldcalculator` — ajout/transformation champ

2. 📖 **Recipes d'abord** : `list_recipes()` avant de tout coder.
   `densite_bati`, `occupation_sol`, `risque_inondation`, etc. existent
   et sont validées — `run_recipe(id, params)` plutôt que ré-écrire.

2bis. 📚 **CATALOGUE d'abord — JAMAIS d'URL externe inventée** :
   Pour charger des données : `list_datasources()` puis `smart_load(id)`
   ou `add_from_catalog(id)`. Le catalogue contient les sources validées
   pour SSPCloud (IGN Géoplateforme, Géorisques, OSM via WFS officiel,
   DVF, BD TOPO, Corine Land Cover…).

   Si le catalogue ne contient PAS exactement ta clé (ex: "quartiers") :
   - ✅ ÉLARGIS la recherche : `list_datasources()` complet, regarde
     les sources proches (limites administratives, IRIS, OSM places…)
   - ✅ Si toujours rien : DEMANDE à l'user quelle source il préfère,
     OU propose 2-3 alternatives **présentes dans le catalogue**
   - ❌ NE JAMAIS inventer une URL externe (overpass.foo.bar, etc.).
     Les LLM hallucinent des mirrors qui n'existent pas → boucle d'erreurs.

   Endpoints externes autorisés (en dernier recours, si vraiment nécessaire) :
   - `https://overpass-api.de/api/interpreter` (Overpass officiel)
   - `https://overpass.openstreetmap.fr/api/interpreter` (mirror OSM France)
   - `https://geo.api.gouv.fr/*` (API officielle communes/INSEE)
   - `https://data.geopf.fr/*` (Géoplateforme IGN)
   Format Overpass : **GET avec data URL-encoded**, pas POST avec body brut
   (sinon 406 Apache). Toujours timeout=25 dans le QL.

   Sources connues pour quartiers/sub-communes :
   - IRIS INSEE via `https://geo.api.gouv.fr/communes/{insee}?fields=contour`
     ou couche `iris_2024` Géoplateforme (échelle infra-communale officielle)
   - OSM `place=quarter` ou `boundary=administrative admin_level=10`
     via Overpass officiel
   - Open Data municipal (data.marseille.fr, opendata.lyon.fr, etc.)

2ter. 🎯 **CONTEXTE L2 = SOURCE DE VÉRITÉ POUR LES ARGS SPATIAUX** :
   Quand le contexte indique une « Zone d'étude active » avec une `bbox` ou
   `center` précis (cf. section MÉMOIRE L2 du système prompt), tu DOIS
   réutiliser ces coordonnées comme args de tool — pas inventer un string
   générique parce que l'user a dit « Marseille » ou « ma zone ».

   ❌ MAUVAIS : `run_recipe(id="risque_inondation", zone="Marseille")`
      (la recette ne sait pas géocoder « Marseille » → échoue)

   ✅ BON : `run_recipe(id="risque_inondation",
                          bbox=[5.3748, 43.289, 5.4243, 43.325],
                          zone="4e arrondissement Marseille")`
      (tu lis bbox dans le contexte L2 « Zone d'étude active »)

   Tools impactés : `run_recipe`, `smart_load`, `set_study_zone`,
   `add_from_catalog`, tout tool qui prend `bbox`/`zone`/`extent`/`aoi`.
   Si bbox absente du contexte L2 mais nom de zone seul → appelle d'abord
   `set_study_zone(target=...)` pour la géocoder, puis utilise la bbox
   retournée.

2quater. 📤 **LIVRABLES PARTAGEABLES = `publish_artifact` OBLIGATOIRE** :
   Quand tu produis un livrable destiné à être consulté par l'user (storymap
   HTML, PDF, GeoPackage, recette YAML), tu DOIS appeler `publish_artifact`
   AVANT de donner un lien à l'user.

   ❌ MAUVAIS : `export_web_map` retourne un `download_url` type
      `https://…-qgis-mcp-bridge.user.lab.sspcloud.fr/files/X.html` que tu
      donnes directement à l'user → clic browser sans Bearer header →
      `{"detail":"Not authenticated"}` → user frustré.

   ✅ BON après tout `export_web_map`/`export_pdf`/`export_flood_map`/
      `export_temporal_map` :
      1. Récupère le `path` retourné par l'export (`/data/...html`)
      2. Choisis un slug URL-safe (ex: `risque_inondation_marseille`)
      3. `publish_artifact(kind="storymap", slug="risque_inondation_marseille")`
         (kind = storymap/pdf/dataset/recipe/flux selon le cas)
      4. La réponse contient `hub_url` — URL publique stable sans auth.
         C'est CE lien que tu donnes à l'user, pas le `download_url`.

   Si `publish_artifact` retourne `HUB_API_KEY absent` (env workspace pas
   configurée) : DIS-LE à l'user en clair "la publication automatique n'est
   pas encore activée sur ce workspace, voici le chemin du fichier dans
   l'étude que tu peux télécharger depuis le panneau Fichiers du desk".
   PAS de lien `/files/...` qui échoue.

3. 🪤 **Pièges PyQGIS** — si `execute_python` est inévitable :
   - **JAMAIS** `int(feat["champ"])` direct (QVariant trap) →
     `v = feat["champ"]; if v is not None: v = int(v)`
   - **Singulier** : `dataProvider().addAttribute(field)` (PAS addAttributeS)
   - Imports explicites : `from qgis.core import (QgsProject, QgsVectorLayer,
     QgsFeature, QgsField, QgsExpression)` — pas de `from ... import (Qgs,`
     tronqué ou `Qgs` non importé → `NameError` garanti

4. ⛔ **STOP après 2 échecs `execute_python` identiques** : pivote
   OBLIGATOIREMENT vers `run_processing` (algo natif) ou demande à l'user.
   PAS de 3e tentative Python brute sur le même thème d'erreur.

5. 💬 **Échec = communication CLAIRE et NON-TECHNIQUE** : si tu finis par
   échouer après plusieurs tentatives, dis « Je rencontre une difficulté
   technique sur X, j'essaie une autre approche » — JAMAIS de traceback,
   JAMAIS de nom de classe Python, JAMAIS « QVariant » à l'user. Il n'est
   PAS codeur.

5bis. 🚫 **PAS de phrases d'intention répétées** : annonce ton intention
   UNE SEULE FOIS en début de turn (ex: « Je calcule la densité puis je
   construis la storymap »), puis APPELLE le tool IMMÉDIATEMENT. Pas de
   « Je vais... » répété 5 fois sans agir. Une phrase = un acte.

6. 🤫 **Mode thinking auto sur 💡 KB tip** : si tu reçois un bloc
   `💡 [KB AUTO-CORRECTION...]` après une erreur tool, c'est une instruction
   INTERNE invisible à l'user. APPLIQUE le pattern et RETENTE tout de suite,
   **silencieusement**. Ne dis pas « j'ai eu une erreur QVariant », ne
   commente pas l'erreur, ne mentionne pas le hint. L'user voit seulement
   le résultat final qui marche, comme si tu avais réussi du 1er coup.

# ⛔ RÈGLE ABSOLUE — AGIR D'ABORD, NARRER ENSUITE, FAITS UNIQUEMENT

## 1. AGIR via les outils — ne JAMAIS narrer en pseudo-code

Tu disposes d'outils MCP (set_study_zone, smart_load, run_processing,
execute_python, etc.). Tu DOIS les utiliser pour AGIR. Tu ne dois
JAMAIS écrire « voici le code que je vais exécuter » sous forme de
texte/pseudo-code — appelle l'outil DIRECTEMENT. Le user verra le
résultat dans la session.

Interdit :
- ❌ « Je vais maintenant exécuter ce script : ```python ...``` »
- ❌ « J'attends la confirmation pour ... » (n'attends rien, appelle l'outil)
- ❌ « Comme je n'ai pas la connaissance préalable, je vais d'abord ... »
  (ÇA SE FAIT EN APPELANT L'OUTIL, PAS EN LE DISANT)

Attendu :
- ✅ Appel d'outil immédiat (set_study_zone, smart_load, etc.)
- ✅ Brève phrase d'intention AVANT (« Je définis la zone... ») mais SANS
  écrire le code en texte ni attendre — appel direct du tool.

## 2. Pas d'invention factuelle géographique

Tu n'as PAS de connaissance géographique fine fiable sur la France.
Tu ne CONNAIS PAS la liste des quartiers de chaque arrondissement,
les monuments, les rues, les hameaux, les caractéristiques urbaines.

INTERDIT (zéro tolérance) :
- ❌ Citer le nom d'un quartier, lieu-dit, rue, monument sans l'avoir
  lu dans un résultat d'outil de cette session.
- ❌ Statistique démographique sans qu'elle vienne d'un outil.
- ❌ "Description" ou "caractérisation" qualitative basée sur connaissance
  pré-entraînée. Connaître code INSEE 13204 ne te dit RIEN sur
  les quartiers du 4e.

Quand on te demande de décrire une zone : retourne UNIQUEMENT les
champs du résultat de set_study_zone (name, code, bbox, centre). Pour
plus de contexte, propose explicitement de charger les couches OSM
places ou toponymes IGN via smart_load.

Chaque phrase de ta réponse doit pouvoir être tracée à un résultat
d'outil ou à la demande utilisateur. Sinon : SUPPRIME-la.

## 3. BBOX d'extraction ≠ emprise administrative

`smart_load` et la plupart des fournisseurs (WFS, BD TOPO) chargent les
entités dans le **rectangle d'enveloppe (bbox)** de la zone d'étude, PAS
dans le polygone administratif. Une bbox d'arrondissement inclut
inévitablement des entités situées HORS de la limite administrative
(coin diagonalement opposé, bords débordants).

INTERDIT (gros caillou, zéro tolérance) :
- ❌ « 50 110 bâtiments DANS le 4e arrondissement » (alors qu'on a chargé par bbox)
- ❌ Toute statistique présentée comme « pour la commune X » sans clip préalable
- ❌ Confondre étendue rectangulaire (bbox) et périmètre administratif

CORRECT :
- ✅ « 50 110 bâtiments dans l'emprise (bbox) du 4e arrondissement »
- ✅ « X entités dans la bbox de Marseille 4e (incluant des bords hors commune) »
- ✅ Si l'utilisateur veut un compte exact « dans la commune » : proposer
  explicitement de clipper d'abord avec `native:clip` contre l'emprise
  administrative (récupérable depuis `geo.api.gouv.fr` ou couche `admin_communes`)
  avant de donner un chiffre.

Règle pratique : avant de chiffrer « dans X », vérifier qu'on a fait un
clip. Sinon, dire « dans l'emprise / la bbox de X » ou ne pas chiffrer.

# Rappels PyQGIS essentiels

## EXTENT des algorithmes processing
Le param `EXTENT` est une CHAÎNE au format `"xmin,xmax,ymin,ymax [EPSG:NNNN]"`
(NB: l'ordre est xmin,xmax,ymin,ymax, PAS xmin,ymin,xmax,ymax). Tu peux aussi
passer un QgsRectangle ou un nom de couche dont l'étendue sera utilisée.

```python
# CORRECT : extent string
processing.run("native:creategrid", {
    "TYPE": 2,  # rectangle
    "EXTENT": "892748.95,896884.91,6246148.59,6250270.70 [EPSG:2154]",
    "HSPACING": 200, "VSPACING": 200,
    "HOVERLAY": 0,  "VOVERLAY": 0,
    "CRS": "EPSG:2154",
    "OUTPUT": "memory:grille_200m",
})

# CORRECT : extent depuis couche
ext = layer.extent()
extent_str = f"{ext.xMinimum()},{ext.xMaximum()},{ext.yMinimum()},{ext.yMaximum()} [EPSG:{layer.crs().postgisSrid()}]"
```

## Récupérer un layer chargé
```python
project = QgsProject.instance()
layer = project.mapLayersByName("Bâti BDTOPO - Marseille 4e")[0]
# OU par layer_id si tu l'as :
layer = project.mapLayer(layer_id)
```

## Joindre une couche au calque maillage (count features)
```python
result = processing.run("native:countpointsinpolygon", {
    "POLYGONS": grid_layer,    # ou layer_id, ou "memory:grille_200m"
    "POINTS":   bati_layer,    # NB: marche aussi pour des polygones (count)
    "WEIGHT":   "",
    "CLASSFIELD": "",
    "FIELD":    "NUMPOINTS",
    "OUTPUT":   "memory:densite",
})
density_layer = result["OUTPUT"]
QgsProject.instance().addMapLayer(density_layer)
```

## Style graduated (choroplèthe) — UTILISER LA FACTORY, JAMAIS LE CONSTRUCTEUR
```python
from qgis.core import (
    QgsGraduatedSymbolRenderer, QgsClassificationQuantile,
    QgsSymbol, QgsStyle,
)

# ÉTAPE 1 : symbole de base (polygone par défaut)
base = QgsSymbol.defaultSymbol(layer.geometryType())

# ÉTAPE 2 : ramp de couleurs (rouge YlOrRd standard)
ramp = QgsStyle.defaultStyle().colorRamp("YlOrRd")

# ÉTAPE 3 : renderer en quantile 5 classes — FACTORY .createRenderer()
renderer = QgsGraduatedSymbolRenderer("NUMPOINTS")
renderer.setClassificationMethod(QgsClassificationQuantile())
renderer.updateClasses(layer, 5)
renderer.updateColorRamp(ramp)

layer.setRenderer(renderer)
layer.triggerRepaint()
iface.mapCanvas().refresh()
```

⚠️ Le constructeur `QgsGraduatedSymbolRenderer(...)` n'accepte qu'un attribut
(str) en argument 1. Si tu vois `unexpected type 'int'` ou `'list'`, tu passes
mal les arguments — utilise la version ci-dessus.

## NULL en PyQGIS (piège récurrent)
`NULL` n'est PAS un littéral Python — utiliser `NULL` directement déclenche
`NameError: name 'NULL' is not defined`. Trois patterns selon le contexte :

```python
# 1) Attribut d'une feature
feat = next(layer.getFeatures())
if feat.attribute("hauteur") is None:  # OR NULL natif Python
    ...

# 2) Filter via expression QGIS (NULL fonctionne ici, c'est du SQL-like)
expr = QgsExpression('"hauteur" >= 30 AND "hauteur" IS NOT NULL')
req = QgsFeatureRequest(expr)
features = layer.getFeatures(req)

# 3) Type QVariant via from PyQt5.QtCore import QVariant
# Rare, utile quand on construit des features programmatiquement.
```

Pour compter avec gestion des NULL :
```python
total = layer.featureCount()
nulls = sum(1 for f in layer.getFeatures() if f.attribute("hauteur") is None)
valid = total - nulls
```

## Gestion des erreurs
Si tu rebondis 2× sur la même erreur, CHANGE D'APPROCHE — n'insiste pas.
Vérifie les types attendus (string vs liste vs object), demande confirmation
à l'utilisateur si nécessaire, ou propose une méthode alternative.

## Communication finale
Toujours conclure par un message en français résumant en LANGUE NATURELLE
ce qui a été fait, ce qui a échoué le cas échéant, et la prochaine étape
suggérée. NE LAISSE JAMAIS un traceback brut comme dernière sortie utilisateur.

## 3. Hypothèses ≠ faits — toujours étiqueter

Quand on te demande « que représente cela ? » ou « pourquoi ? » et que tu
n'as PAS vérifié l'explication via un outil, tu ne peux PAS lister une
catégorisation comme si elle était factuelle.

Mauvaise réponse :
> Les cellules vides correspondent à : espaces verts, infrastructures
> de transport, zones de servitudes, zones de pente accidentées.

(L'agent ne sait PAS lesquelles sont laquelle. C'est une LISTE de causes
possibles présentée comme une décomposition réelle. INTERDIT.)

Bonne réponse :
> Les cellules vides peuvent correspondre à des espaces non bâtis :
> parcs, voiries, terrains vagues, reliefs. Je n'ai PAS vérifié laquelle
> de ces catégories explique réellement chaque cellule.
> Je peux croiser avec la couche OSM landuse ou Corine Land Cover si tu
> veux une réponse vérifiée.

Pattern : « peut-être... possiblement... à vérifier... » au lieu de la
forme affirmative. Marqueur explicite « hypothèse non vérifiée ».

## Substitutions = à signaler explicitement
Si tu ne trouves PAS exactement ce qu'a demandé l'utilisateur (template
absent, paramètre indisponible, données partielles), tu DOIS le signaler
explicitement dans le message final. Exemples :

- Demande « A4 paysage » mais seul `a3_landscape` ou `a4_portrait`
  disponibles → utilise le plus proche ET DIS « J'ai utilisé A4 portrait
  faute d'A4 paysage disponible. Veux-tu A3 paysage ? »
- Demande données 2024 mais seules 2023 dispo → utilise 2023 ET signale.
- Demande analyse précise mais couche partielle → fait avec ce qui est
  dispo ET indique la limite des données.

La substitution silencieuse est BANNIE — elle trompe l'utilisateur sur
ce qu'il a réellement reçu.

## Rappel final
La règle absolue en tête de prompt s'applique à CHAQUE phrase de ta réponse.
Avant d'envoyer : relire mentalement, si une phrase contient un fait qui
ne vient pas d'un outil cette session, la supprimer.
"""

    async def _build_system_prompt(self, user_message: str | None = None) -> str:
        """
        System prompt = profil + cheat-sheet PyQGIS + couches mémoire 1+2+3
        + enrichers déterministes (si user_message fourni) + directives.

        Couche 1 (conversation) : géré par history dans messages[].
        Couche 2 (étude active + état projet QGIS) : fetched parallèle.
        Couche 3 (user permanent) : depuis SQLite local memory.
        Enrichers (Phase 9 étape A) : regex-based, asyncio.gather. Fournissent
        un "contexte enrichi" propre à la requête courante, AVANT que le LLM
        ne décide de ses tools.
        """
        profile_prompt = _load_profile_prompt(self.profile_id)
        if not profile_prompt:
            profile_prompt = (
                "Tu es un expert QGIS pour le CEREMA, spécialisé en analyse géospatiale. "
                "Tu as accès à des outils QGIS (set_study_zone, smart_load, execute_python, "
                "run_processing, export_pdf, export_web_map...). "
                "Utilise-les pour répondre aux demandes d'analyse spatiale."
            )

        # Tâches parallèles : couche 2 + état projet + enrichers (si user_msg)
        from agent import enrichers
        active_study_task  = asyncio.create_task(self._fetch_active_study_context())
        project_state_task = asyncio.create_task(self._fetch_project_state())
        # State partagé aux enrichers : ids des messages déjà en L1 pour que
        # memory_recall n'affiche pas en doublon ce que le LLM voit déjà.
        recent_ids = []
        try:
            recent = await memory.get_session_messages(self.session_id, limit=20)
            recent_ids = [m.get("id") for m in recent if m.get("id")]
        except Exception:
            pass
        enrich_state = {"recent_message_ids": recent_ids}
        enrich_task = (
            asyncio.create_task(enrichers.run_all(user_message, enrich_state))
            if user_message else None
        )

        active_study, active_treats = await active_study_task
        project_state = await project_state_task
        # Stocke pour le tool loop (cf. _zone_context_warning ci-dessous)
        self._project_state = project_state
        enrich_results = await enrich_task if enrich_task else []

        # Couches 2 + 3 assemblées dans memory.build_context_summary
        ctx = await memory.build_context_summary(
            self.username, self.session_id, self.profile_id,
            active_study=active_study,
            active_study_treatments=active_treats,
            project_state=project_state,
        )

        # Contexte enrichi spécifique à la requête (si applicable)
        enrich_block = enrichers.format_for_prompt(enrich_results) if enrich_results else ""
        enrich_section = f"\n\n{enrich_block}" if enrich_block else ""

        return (
            f"{profile_prompt}\n{self._QGIS_ESSENTIALS}\n\n{ctx}{enrich_section}"
            f"{self._SWITCH_INSTRUCTIONS}{self._REMEMBER_INSTRUCTIONS}"
        ).strip()

    async def chat_stream(
        self,
        user_message: str,
        history: list[dict] | None = None,
        stop_signal: "asyncio.Event | None" = None,
    ) -> AsyncGenerator[str, None]:
        """
        Génère une réponse en streaming avec appels d'outils MCP.
        Yield des chunks de texte + events d'outils.

        `stop_signal` : Event asyncio que l'utilisateur peut poser via
        POST /chat/{sid}/stop pour interrompre la boucle entre tool calls.
        Le tool en cours d'exécution est laissé finir, on break ensuite.
        Si None, le stop manuel est désactivé (utile pour les tests).
        """
        # Sauvegarder le message user
        await memory.add_message(self.session_id, "user", user_message)

        system_prompt = await self._build_system_prompt(user_message=user_message)
        tools         = await self._get_tools()

        # Construire les messages
        messages = [{"role": "system", "content": system_prompt}]
        if history:
            # Fix consolidation 2026-06-19 : strip les data-URLs d'images
            # AVANT d'envoyer au LLM (image enfle de ~300 KB le contexte).
            # Le storage SQLite garde les images intactes pour l'affichage
            # UI (cf. add_message au-dessous). Strip est cible : on ne
            # touche que les bulles assistant avec markdown image.
            _IMG_RE = re.compile(r'!\[[^\]]*\]\(data:image/[^)]+\)')
            history_for_llm = []
            for msg in history[-20:]:
                content = msg.get("content", "") or ""
                if "data:image/" in content:
                    content = _IMG_RE.sub(
                        '[image affichée précédemment à l\'utilisateur]',
                        content,
                    )
                history_for_llm.append({**msg, "content": content})
            messages.extend(history_for_llm)
        messages.append({"role": "user", "content": user_message})

        full_response = ""
        tool_calls_made = []
        # On capte le hub_url du dernier publish_artifact reussi pour pouvoir
        # patcher en fin de turn les liens fantomes [undefined](undefined)
        # que le LLM genere parfois (cf. hotfix infra ci-dessous).
        last_publish_info: dict = {}

        # Boucle agent : LLM → tool calls → LLM → ...
        # 20 itérations = budget pour analyse multi-étapes + construction
        # livrable + publication dans le même turn (ex: storymap end-to-end).
        max_iterations = 20
        final_finish_reason = None
        for iteration in range(max_iterations):
            # Vérifier le signal d'arrêt user (posé via POST /chat/{sid}/stop).
            # Check en début d'itération : si l'user a cliqué Stop pendant
            # qu'un tool s'exécutait, on a laissé finir le tool puis on break ici.
            if stop_signal is not None and stop_signal.is_set():
                log.info("Stop signal détecté pour session %s à l'itération %d",
                         self.session_id, iteration)
                yield "\n\n⛔ **Arrêté par l'utilisateur.** La conversation est conservée — tu peux poursuivre ou revenir en arrière.\n"
                full_response += "\n\n⛔ Arrêté par l'utilisateur."
                break

            # Appel LLM
            model = os.getenv("LLM_MODEL", _get_model(self.profile_id))
            payload = {
                "model":      model,
                "messages":   messages,
                "stream":     True,
                "max_tokens": 4096,
            }
            if tools:
                payload["tools"] = tools
                # Laisser le modèle décider : auto sur 1ère itération,
                # puis auto sur les suivantes (évite boucle infinie)
                payload["tool_choice"] = "auto"

            # Trace LLM call : aide a diagnostiquer les hangs silencieux comme
            # qwen3-6-35b-moe DOWN 2026-06-09 (modele liste mais inerte cote
            # SSPCloud, /chat/completions ne stream rien -> agent bloque jusqu'au
            # timeout). Sans ce log on perd 30+ min a comprendre ou ca coince.
            log.info(
                "LLM call iter=%d model=%s msgs=%d tools=%d url=%s",
                iteration, model, len(messages), len(tools) if tools else 0,
                _LLM_BASE_URL,
            )
            llm_t0 = asyncio.get_event_loop().time()

            async with httpx.AsyncClient(timeout=120) as client:
                async with client.stream(
                    "POST",
                    f"{_LLM_BASE_URL}/chat/completions",
                    json=payload,
                    headers={
                        "Authorization": f"Bearer {_llm_api_key()}",
                        "Content-Type":  "application/json",
                    }
                ) as resp:
                    # Fail-fast sur 4xx/5xx : sans cela, un 400 "Model not found"
                    # ou 503 fait que aiter_lines() ne yield rien -> agent hang
                    # silencieusement jusqu'au timeout 120s. On loggue + leve
                    # une erreur visible (catchee dans event_stream -> SSE 'error').
                    if resp.status_code >= 400:
                        body = b""
                        try:
                            body = await resp.aread()
                        except Exception:
                            pass
                        body_text = body.decode("utf-8", errors="replace")[:500]
                        log.error(
                            "LLM HTTP %d on model=%s : %s",
                            resp.status_code, model, body_text,
                        )
                        raise RuntimeError(
                            f"LLM API a renvoye HTTP {resp.status_code} "
                            f"pour le modele '{model}'. Reponse: {body_text}"
                        )

                    chunk_text     = ""
                    tool_call_data: dict[int, dict] = {}
                    finish_reason  = None

                    async for line in resp.aiter_lines():
                        if not line.startswith("data: "):
                            continue
                        data = line[6:]
                        if data == "[DONE]":
                            break
                        try:
                            d = json.loads(data)
                            delta = d["choices"][0].get("delta", {})
                            finish_reason = d["choices"][0].get("finish_reason")

                            # Texte — ignorer reasoning_content (Qwen3 thinking séparé)
                            # Qwen3 : thinking dans delta.reasoning_content (champ séparé)
                            # Gemma4 : thinking dans delta.content (tokens <|channel>)
                            if delta.get("content"):
                                raw = delta["content"]
                                # Filtrer tokens thinking Gemma4 dans content
                                import re as _re
                                clean = _re.sub(r'<\|[^>]*>', '', raw)
                                clean = _re.sub(r'<[^>]*\|>', '', clean)
                                # Cacher les directives à l'user (appliquées en fin de turn)
                                clean = _re.sub(
                                    r'<switch_profile>[^<]*</switch_profile>',
                                    '', clean, flags=_re.IGNORECASE,
                                )
                                clean = _re.sub(
                                    r'<remember>[^<]*</remember>',
                                    '', clean, flags=_re.IGNORECASE,
                                )
                                if clean:
                                    chunk_text += clean
                                    full_response += clean
                                    yield clean
                            # reasoning_content (Qwen3) → on l'ignore (pensées internes)

                            # Tool calls (accumulation)
                            for tc in delta.get("tool_calls", []):
                                idx = tc["index"]
                                if idx not in tool_call_data:
                                    tool_call_data[idx] = {
                                        "id":       tc.get("id", ""),
                                        "type":     "function",
                                        "function": {"name": "", "arguments": ""},
                                    }
                                if tc.get("id"):
                                    tool_call_data[idx]["id"] = tc["id"]
                                fn = tc.get("function", {})
                                if fn.get("name"):
                                    tool_call_data[idx]["function"]["name"] += fn["name"]
                                if fn.get("arguments"):
                                    tool_call_data[idx]["function"]["arguments"] += fn["arguments"]
                        except Exception:
                            pass

            final_finish_reason = finish_reason
            # Pas de tool calls → fin du turn LLM.
            # NB : on n'utilise PAS finish_reason="stop" pour break car Gemma4
            # renvoie souvent "stop" même quand des tool_calls sont présents.
            # Seule la présence/absence de tool_call_data détermine la suite.
            if not tool_call_data:
                # Garde-fou : si le LLM termine SANS message narratif final
                # (chunk_text vide à ce dernier tour), forcer un récap court
                # pour ne JAMAIS laisser l'user devant un turn qui se termine
                # juste après un résultat tool brut sans explication.
                # Cas typique : agent fait 2 tools puis "stop" sans expliquer.
                if not chunk_text.strip():
                    if tool_calls_made:
                        # Au moins 1 tool a tourné — forcer un récap LLM rapide
                        messages.append({
                            "role": "system",
                            "content": (
                                "Conclus MAINTENANT le tour avec un message "
                                "non-technique à l'user : (1) ce qui a été "
                                "fait, (2) ce qui reste, (3) propose la suite. "
                                "PAS de tool call, JUSTE un message."
                            ),
                        })
                        try:
                            async with httpx.AsyncClient(timeout=60) as client_f:
                                async with client_f.stream(
                                    "POST",
                                    f"{_LLM_BASE_URL}/chat/completions",
                                    json={
                                        "model": model,
                                        "messages": messages,
                                        "stream": True,
                                        "max_tokens": 600,
                                    },
                                    headers={
                                        "Authorization": f"Bearer {_llm_api_key()}",
                                        "Content-Type":  "application/json",
                                    }
                                ) as resp_f:
                                    async for line in resp_f.aiter_lines():
                                        if not line.startswith("data: "):
                                            continue
                                        data = line[6:]
                                        if data == "[DONE]":
                                            break
                                        try:
                                            d = json.loads(data)
                                            ct = d["choices"][0].get("delta", {}).get("content") or ""
                                            if ct:
                                                full_response += ct
                                                yield ct
                                        except Exception:
                                            pass
                        except Exception:
                            fb = (
                                "\n\n_J'ai exécuté quelques actions mais je n'ai "
                                "pas pu finaliser proprement ce tour. Veux-tu "
                                "que je continue sur la suite ou que je résume "
                                "ce qui a été fait ?_"
                            )
                            full_response += fb
                            yield fb
                    elif not full_response.strip():
                        # Aucun tool ni message — bulle vide totale
                        fb = (
                            "_Je n'ai pas généré de réponse. Reformule ta "
                            "demande et je m'en occupe._"
                        )
                        full_response += fb
                        yield fb
                break
            # Tool calls présents → les exécuter et boucler pour la suite
            tool_calls = list(tool_call_data.values())
            messages.append({
                "role":       "assistant",
                "content":    chunk_text or None,
                "tool_calls": tool_calls,
            })

            for tc in tool_calls:
                fn_name = tc["function"]["name"]
                try:
                    fn_args = json.loads(tc["function"]["arguments"] or "{}")
                except Exception:
                    fn_args = {}

                # ── Hook checkpoint pré-mutating ───────────────────────────
                # Avant chaque tool modifiant l'état projet, on demande au hub
                # de prendre un snapshot du .qgz courant. L'agent enregistre
                # la métadonnée dans memory.checkpoints. En cas d'échec du
                # snapshot, on continue quand même (best-effort) — l'absence
                # de checkpoint empêche juste le rollback ultérieur sur ce point.
                # ÉMIS AVANT le blockquote markdown pour que le commentaire HTML
                # apparaisse comme prev-sibling du blockquote dans le DOM,
                # ce que `attachRollbackButtons` côté JS attend.
                current_ckpt_id: str | None = None
                current_ckpt_study: str | None = None
                if fn_name in _MUTATING_TOOLS and _HUB_URL and _HUB_KEY:
                    try:
                        ckpt_id = uuid.uuid4().hex[:12]
                        # Index du DERNIER message persisté en DB au moment du
                        # checkpoint = point de retour pour rollback.
                        # truncate_messages_after(idx) garde [0..idx] et jette
                        # tout ce qui suit (= la future réponse assistant de
                        # ce turn). On compte DB, pas le contexte LLM (qui
                        # inclut system_prompt + history non persistés).
                        db_count = await memory.count_session_messages(self.session_id)
                        msg_idx = max(db_count - 1, 0)
                        async with httpx.AsyncClient(timeout=30) as ck_client:
                            ck_resp = await ck_client.post(
                                f"{_HUB_URL}/sessions/{self.session_id}/checkpoint",
                                headers={"Authorization": f"Bearer {_HUB_KEY}"},
                                json={
                                    "checkpoint_id": ckpt_id,
                                    "tool_name":     fn_name,
                                },
                            )
                            if ck_resp.status_code < 300:
                                ck_data = ck_resp.json()
                                await memory.create_checkpoint(
                                    checkpoint_id=ckpt_id,
                                    session_id=self.session_id,
                                    message_idx=msg_idx,
                                    qgz_path=ck_data.get("qgz_path", ""),
                                    tool_name=fn_name,
                                    study_id=ck_data.get("study_id"),
                                    audit_ts=ck_data.get("audit_ts"),
                                )
                                # Mémoriser pour fallback rollback si stop+timeout
                                current_ckpt_id = ckpt_id
                                current_ckpt_study = ck_data.get("study_id")
                                # Marqueur HTML invisible posé AVANT le blockquote
                                # du tool. attachRollbackButtons cherchera le
                                # nextSibling BLOCKQUOTE → bouton "↶ Revenir avant".
                                yield f"\n\n<!--ckpt:{ckpt_id}-->\n"
                                log.info("Checkpoint %s pris avant %s",
                                         ckpt_id, fn_name)
                            else:
                                log.warning("Checkpoint refusé par hub (%d)",
                                            ck_resp.status_code)
                    except Exception as exc:
                        # Log explicite : certaines exceptions httpx ont str("")
                        # vide → on inclut le type pour diagnostic.
                        log.warning(
                            "Checkpoint pré-%s échoué : %s: %s",
                            fn_name, type(exc).__name__, exc or "(no msg)",
                        )

                log.info("Tool call: %s(%s)", fn_name, str(fn_args)[:100])
                # Le blockquote markdown est À LA FOIS yield (rendu streaming
                # côté UI) ET accumulé dans full_response (persisté en DB pour
                # que le rendu au reload soit cohérent + matching des
                # checkpoints aux tools possible côté reattachRollbackButtons).
                _bq_header = f"\n\n> **`{fn_name}`**"
                full_response += _bq_header
                yield _bq_header
                if fn_args:
                    def _short_arg(v):
                        s = str(v).replace("\n", " ").replace("\r", " ").replace("`", "'")
                        s = " ".join(s.split())
                        return s[:40] + ("…" if len(s) > 40 else "")
                    args_preview = ", ".join(
                        f"`{k}={_short_arg(v)}`" for k, v in fn_args.items()
                    )
                    _bq_args = f" — {args_preview}"
                    full_response += _bq_args
                    yield _bq_args
                full_response += "\n"
                yield "\n"

                # ── Exécution du tool avec surveillance stop_signal ────────
                # Commit C : si l'user clique Stop pendant un tool MUTATING,
                # on laisse une grace period (STOP_TOOL_GRACE_SEC, défaut 30s)
                # pour que le tool finisse proprement. Si timeout, on cancel
                # le tool ET on déclenche un rollback automatique au snapshot
                # pré-tool — exactement la sémantique demandée par l'user :
                # "stoppe le tool et revert sur état juste avant en fallback
                # si problème, fiable".
                # Pour les tools NON mutating (lecture seule), pas de rollback
                # possible/nécessaire — on attend juste la fin du tool.
                if (fn_name in _MUTATING_TOOLS and stop_signal is not None):
                    tool_task = asyncio.create_task(
                        _call_mcp_tool(fn_name, fn_args, username=self.username)
                    )
                    stop_wait = asyncio.create_task(stop_signal.wait())
                    done, pending = await asyncio.wait(
                        {tool_task, stop_wait},
                        return_when=asyncio.FIRST_COMPLETED,
                    )
                    if stop_wait in done and tool_task not in done:
                        # Stop demandé pendant l'exécution du tool mutating.
                        grace = int(os.getenv("STOP_TOOL_GRACE_SEC", "30"))
                        log.info(
                            "Stop pendant %s — grace %ds avant rollback fallback",
                            fn_name, grace,
                        )
                        # Triple piège : TimeoutError (grace écoulée),
                        # CancelledError (générateur SSE cancelled par le
                        # client qui abort), Exception générique → on traite
                        # tous comme "tool n'a pas fini proprement" et on
                        # déclenche le fallback rollback.
                        timed_out_or_cancelled = False
                        try:
                            result = await asyncio.wait_for(tool_task, timeout=grace)
                        except asyncio.TimeoutError:
                            timed_out_or_cancelled = True
                            log.info("Tool %s n'a pas fini dans grace %ds", fn_name, grace)
                        except asyncio.CancelledError:
                            timed_out_or_cancelled = True
                            log.info("Tool %s cancelled (SSE coupé)", fn_name)
                        except Exception as exc:
                            timed_out_or_cancelled = True
                            log.warning("Tool %s a échoué : %s", fn_name, exc)
                        if timed_out_or_cancelled:
                            tool_task.cancel()
                            try:
                                await tool_task
                            except (asyncio.CancelledError, Exception):
                                pass
                            rollback_msg = (
                                f"\n\n⛔ **Stop + timeout sur `{fn_name}`** "
                                f"(> {grace}s) → rollback automatique au point précédent.\n"
                            )
                            if current_ckpt_id and current_ckpt_study \
                               and _HUB_URL and _HUB_KEY:
                                try:
                                    async with httpx.AsyncClient(timeout=45) as rb_c:
                                        rb_resp = await rb_c.post(
                                            f"{_HUB_URL}/sessions/{self.session_id}/restore-checkpoint",
                                            headers={"Authorization": f"Bearer {_HUB_KEY}"},
                                            json={
                                                "checkpoint_id": current_ckpt_id,
                                                "study_id":      current_ckpt_study,
                                            },
                                        )
                                        if rb_resp.status_code < 300:
                                            rollback_msg += (
                                                "✅ Projet QGIS restauré à l'état d'avant le tool.\n"
                                            )
                                        else:
                                            rollback_msg += (
                                                f"⚠️ Rollback échec ({rb_resp.status_code}) — "
                                                "vérifie l'état projet manuellement.\n"
                                            )
                                except Exception as exc:
                                    rollback_msg += f"⚠️ Rollback échec : {exc}\n"
                            else:
                                rollback_msg += (
                                    "(pas de checkpoint disponible — aucune restauration possible)\n"
                                )
                            yield rollback_msg
                            result = json.dumps({
                                "success": False,
                                "error":   "tool_cancelled_by_stop",
                                "tool":    fn_name,
                            })
                    else:
                        # Tool fini avant le stop → résultat normal.
                        stop_wait.cancel()
                        try:
                            await stop_wait
                        except (asyncio.CancelledError, Exception):
                            pass
                        result = tool_task.result()
                else:
                    # Tool non mutating OU pas de stop_signal : exécution directe.
                    result = await _call_mcp_tool(fn_name, fn_args, username=self.username)

                # Garde-fou L2 : si l'agent a passe un nom de ville generique
                # alors qu'une sous-zone precise est active dans le project
                # state, on prepend une note dans le result. L'agent peut
                # decider quoi faire au turn suivant.
                zone_warning = self._zone_context_warning(fn_name, fn_args)
                if zone_warning:
                    log.info(
                        "Zone context mismatch sur %s args=%s -> note injectee",
                        fn_name, fn_args,
                    )
                    result = result + zone_warning

                tool_calls_made.append({"tool": fn_name, "args": fn_args, "result": result[:200]})

                # Capter hub_url des publish_artifact reussis pour le hotfix
                # `[undefined](undefined)` applique en fin de turn.
                #
                # Strategie a 2 paliers parce que _call_mcp_tool peut concatener
                # plusieurs chunks `text` MCP, ajouter du contexte KB
                # (_maybe_enrich_with_kb_hint), ou retourner du JSON pretty
                # entoure de texte. json.loads brut ne marche pas toujours.
                if fn_name == "publish_artifact":
                    hub_url, kind_val = None, None
                    # Palier 1 : JSON pur
                    try:
                        parsed = json.loads(result)
                        if isinstance(parsed, dict):
                            hub_url  = parsed.get("hub_url")
                            kind_val = parsed.get("kind")
                    except Exception:
                        pass
                    # Palier 2 : regex tolerante (string contenant du JSON
                    # embed ou texte autour). On extrait directement hub_url
                    # qui doit etre une URL absolue https.
                    if not hub_url:
                        m = re.search(r'"hub_url"\s*:\s*"(https?://[^"]+)"', result)
                        if m:
                            hub_url = m.group(1)
                        mk = re.search(r'"kind"\s*:\s*"([^"]+)"', result)
                        if mk:
                            kind_val = mk.group(1)
                    if hub_url:
                        last_publish_info = {
                            "hub_url": hub_url,
                            "kind":    kind_val or "livrable",
                            "slug":    fn_args.get("slug") or "",
                        }
                        log.info(
                            "publish_artifact OK capte : hub_url=%s kind=%s",
                            hub_url, kind_val or "?",
                        )

                result_clean = result.strip()
                if result_clean and result_clean not in ("{}", "null", "[]"):
                    if "![" in result_clean and "](data:" in result_clean:
                        yield f"\n{result_clean}\n"
                    else:
                        result_preview = result_clean[:300] + (
                            "..." if len(result_clean) > 300 else ""
                        )
                        yield f"\n```\n{result_preview}\n```\n"

                # Yield direct du lien livrable AVANT la synthese LLM : garantit
                # qu'on voit toujours la bonne URL meme si le LLM hallucine
                # [undefined](undefined) ensuite. Le post-turn hot-fix etait
                # casse architecturalement (modifie full_response apres
                # streaming, le DOM n'est jamais maj cote client). Solution :
                # rendre le lien visible immediatement et injecter une
                # directive dans le tool result envoye au LLM (cf. bloc ci-dessous).
                if last_publish_info and fn_name == "publish_artifact":
                    pub_url  = last_publish_info["hub_url"]
                    pub_kind = last_publish_info["kind"]
                    visible_link = (
                        f"\n\n📎 **Livrable publie** : "
                        f"[Voir le {pub_kind}]({pub_url})\n"
                    )
                    yield visible_link
                    full_response += visible_link

                llm_result = re.sub(
                    r'!\[[^\]]*\]\(data:image/[^)]+\)',
                    '[image affichée à l\'utilisateur]',
                    result,
                )
                # Pour publish_artifact, appendre une directive explicite au tool
                # result envoye au LLM : "si tu veux mentionner le livrable dans
                # ta synthese, utilise EXACTEMENT cette ligne". Evite que qwen3
                # invente [undefined](undefined) par mauvais parsing du JSON.
                if last_publish_info and fn_name == "publish_artifact":
                    llm_result += (
                        f"\n\n>>> DIRECTIVE SYNTHESE : Le lien du livrable a "
                        f"deja ete affiche a l'utilisateur juste au-dessus. "
                        f"Tu n'as PAS besoin de le repeter. Confirme simplement "
                        f"la publication en 1-2 phrases factuelles (kind, slug, "
                        f"contenu). NE GENERE PAS de markdown [texte](url) "
                        f"pointant vers le livrable -- c'est deja fait. <<<"
                    )
                messages.append({
                    "role":         "tool",
                    "tool_call_id": tc["id"],
                    "content":      llm_result,
                })

                # Détection bridge dégradé : 2+ tools différents qui échouent
                # sur des symptômes infra consécutifs → l'agent doit escalader,
                # pas continuer de spammer le bridge mort.
                if _is_infra_failure(llm_result):
                    recent_infra = [
                        c for c in tool_calls_made[-5:]
                        if _is_infra_failure(c.get("result", ""))
                    ]
                    distinct_tools = {c["tool"] for c in recent_infra}
                    if len(recent_infra) >= 2 and len(distinct_tools) >= 2:
                        log.warning(
                            "Bridge dégradé détecté : %d échecs infra sur %d tools distincts (%s)",
                            len(recent_infra), len(distinct_tools), distinct_tools,
                        )
                        # Inject system msg : forcer l'agent à expliquer + proposer
                        # le redémarrage. Pas d'auto-stop (l'user peut décider).
                        messages.append({
                            "role": "system",
                            "content": (
                                f"⚠️ INFRA BRIDGE DÉGRADÉ — {len(recent_infra)} échecs sur "
                                f"{len(distinct_tools)} tools différents en quelques étapes "
                                f"(timeouts, erreurs muettes, gateway timeout). Le moteur QGIS "
                                f"est probablement bloqué ou en surcharge. NE LANCE PLUS de tools "
                                f"spatiaux maintenant. Choix : (A) propose UNE fois `restart_qgis_engine` "
                                f"en expliquant à l'user en clair non-technique pourquoi tu redémarres, "
                                f"puis attends 15s et retente UNE seule fois le tool qui a échoué — OU "
                                f"(B) termine le tour en disant à l'user que le moteur QGIS semble "
                                f"bloqué et qu'il peut réessayer dans 30s. NE BOUCLE PAS sur d'autres "
                                f"tools spatiaux. NE redémarre PAS deux fois de suite."
                            ),
                        })

                err_sig = _extract_error_signature(llm_result)
                if err_sig:
                    same_failures = [
                        c for c in tool_calls_made
                        if c["tool"] == fn_name
                        and _extract_error_signature(c.get("result", "")) == err_sig
                    ]
                    if len(same_failures) >= 3:
                        # ≥3 mêmes erreurs : l'avertissement précédent (≥2) n'a
                        # pas changé le comportement → auto-stop dur pour ne
                        # pas consommer le budget en pure perte. L'user verra
                        # le message et pourra reformuler ou rollback.
                        log.warning(
                            "Auto-stop boucle d'erreur : %s × %d sur %s",
                            err_sig, len(same_failures), fn_name,
                        )
                        if stop_signal is not None:
                            stop_signal.set()
                        yield (
                            f"\n\n⚠️ **Boucle d'erreur détectée** "
                            f"(`{fn_name}` × {len(same_failures)} : « {err_sig} »). "
                            "Je m'arrête pour ne pas tourner en rond. Reformule "
                            "ta demande différemment, ou ↶ reviens à l'étape "
                            "d'avant si tu as enregistré un point de retour.\n"
                        )
                    elif len(same_failures) >= 2:
                        log.warning(
                            "Boucle d'erreur détectée : %s × %d sur %s",
                            err_sig, len(same_failures), fn_name,
                        )
                        messages.append({
                            "role": "system",
                            "content": (
                                f"⚠️ Tu as rencontré {len(same_failures)} fois "
                                f"la même erreur avec `{fn_name}` : « {err_sig} ». "
                                "ARRÊTE de retenter la même approche. Soit tu "
                                "changes complètement de méthode (autre algorithme, "
                                "autre param format, code Python brut au lieu de "
                                "processing.run, etc.), soit tu termines en "
                                "expliquant à l'utilisateur ce qui bloque et "
                                "ce que tu lui suggères. NE BOUCLE PAS."
                            ),
                        })
        else:
            # max_iterations atteint sans break : forcer un dernier appel LLM
            # de conclusion pour ne JAMAIS laisser l'user devant une conv figée.
            messages.append({
                "role": "system",
                "content": (
                    "Tu as atteint la limite d'étapes pour ce tour. CONCLUS "
                    "maintenant avec un message NON-TECHNIQUE pour l'utilisateur "
                    "expliquant : (1) ce qui a été réalisé concrètement, (2) ce "
                    "qui reste à faire pour atteindre son objectif initial, (3) "
                    "une question simple pour qu'il décide de la suite. PAS de "
                    "tool calls. Juste un message en français clair, sans jargon "
                    "Python, sans mention d'« itérations » ou de tools."
                ),
            })
            try:
                async with httpx.AsyncClient(timeout=60) as client:
                    async with client.stream(
                        "POST",
                        f"{_LLM_BASE_URL}/chat/completions",
                        json={
                            "model": model,
                            "messages": messages,
                            "stream": True,
                            "max_tokens": 1024,
                        },
                        headers={
                            "Authorization": f"Bearer {_llm_api_key()}",
                            "Content-Type":  "application/json",
                        }
                    ) as resp_final:
                        async for line in resp_final.aiter_lines():
                            if not line.startswith("data: "):
                                continue
                            data = line[6:]
                            if data == "[DONE]":
                                break
                            try:
                                d = json.loads(data)
                                delta = d["choices"][0].get("delta", {})
                                content = delta.get("content") or ""
                                if content:
                                    full_response += content
                                    yield content
                            except Exception:
                                pass
            except Exception:
                fallback = (
                    "\n\n_Je n'ai pas pu terminer entièrement le travail dans "
                    "ce tour. Veux-tu que je continue sur la suite, ou que je "
                    "résume ce qui a été fait jusqu'ici ?_"
                )
                full_response += fallback
                yield fallback

        # Détecter directives <remember> en fin de turn → insights mémoire
        import re as _re_end
        remember_matches = _re_end.findall(
            r'<remember>\s*([^<:]+)\s*:\s*([^<]+?)\s*</remember>',
            full_response, flags=_re_end.IGNORECASE,
        )
        if remember_matches:
            for key, value in remember_matches:
                key_clean = key.strip()[:80]
                value_clean = value.strip()[:300]
                if key_clean and value_clean:
                    try:
                        await memory.add_insight(
                            key=key_clean, value=value_clean,
                            source="implicit", confidence=0.8,
                            username=self.username,
                        )
                        log.info("Insight enregistré: %s = %s", key_clean, value_clean)
                    except Exception as exc:
                        log.warning("Add insight err: %s", exc)
            # Signal discret
            yield f"\n\n*🧠 Mémorisé : {len(remember_matches)} insight(s).*"
            # Nettoyer du full_response avant save
            full_response = _re_end.sub(
                r'<remember>[^<]*</remember>\s*',
                '', full_response, flags=_re_end.IGNORECASE,
            )

        # Détecter directive de switch profil dans la réponse complète
        m = _re_end.search(
            r'<switch_profile>\s*([a-zA-Z0-9_]+)\s*</switch_profile>',
            full_response, flags=_re_end.IGNORECASE,
        )
        if m:
            new_profile = m.group(1).strip().lower()
            log.info("LLM directive switch_profile → %s", new_profile)
            await self.set_profile(new_profile)
            # Persister côté hub : étude active de l'user prend ce profil
            try:
                if _HUB_URL and _HUB_KEY:
                    async with httpx.AsyncClient(timeout=10) as c:
                        active = await c.get(
                            f"{_HUB_URL}/studies/active",
                            headers={"Authorization": f"Bearer {_HUB_KEY}"},
                        )
                        if active.status_code == 200 and active.json():
                            sid = active.json()["id"]
                            await c.patch(
                                f"{_HUB_URL}/studies/{sid}",
                                headers={"Authorization": f"Bearer {_HUB_KEY}"},
                                json={"profile": new_profile},
                            )
            except Exception as exc:
                log.warning("Persist switch profile : %s", exc)
            # Signal discret au front (markdown italic)
            yield f"\n\n*Profil basculé sur **{new_profile}**.*"
            # Nettoyer la directive du full_response sauvegardé
            full_response = _re_end.sub(
                r'<switch_profile>\s*[a-zA-Z0-9_]+\s*</switch_profile>\s*',
                '', full_response, flags=_re_end.IGNORECASE,
            )

        # Post-turn cleanup : on retire les eventuels [undefined](undefined)
        # residuels dans full_response (cas ou le LLM en a genere malgre la
        # directive). Pas de yield ici (texte deja stream cote UI) -- on
        # nettoie juste le snapshot persiste en memoire pour ne pas polluer
        # le contexte des turns suivants. Le lien correct a deja ete yield
        # juste apres publish_artifact (cf. visible_link plus haut).
        if last_publish_info:
            pub_url  = last_publish_info["hub_url"]
            pub_kind = last_publish_info["kind"]
            full_response = re.sub(
                r'\[(?:undefined|[^\]]*)\]\(undefined\)',
                f"[Voir le {pub_kind}]({pub_url})",
                full_response,
            )

        # Sauvegarder la réponse complète AVEC les images en memoire SQLite.
        #
        # Fix consolidation 2026-06-19 : on garde les data-URLs d'images dans
        # la persistance pour que l'utilisateur les revoie au reload de la
        # page (avant : le strip au WRITE faisait perdre les screenshots a
        # la reouverture de session).
        #
        # Le strip pour eviter l'enflure du contexte LLM (300 KB/screenshot)
        # est deplace dans le chargement de l'historique pour le LLM (cf.
        # _strip_images_from_history). DB grossit un peu mais user voit ses
        # screenshots persistes -> compromis acceptable (image ~300 KB jpeg).
        await memory.add_message(
            self.session_id, "assistant", full_response,
            tool_calls=tool_calls_made if tool_calls_made else None
        )

        # Auto-extraction d'insights : tous les 6 messages user dans la session.
        # Fire-and-forget — n'impacte pas la réponse en cours. Le LLM appelle
        # est rate-limité naturellement par le seuil + l'idempotence d'add_insight.
        try:
            msgs = await memory.get_session_messages(self.session_id, limit=200)
            user_count = sum(1 for m in msgs if m.get("role") == "user")
            if user_count >= 3 and user_count % 6 == 0:
                from agent import insight_extractor
                asyncio.create_task(
                    insight_extractor.extract_for_session(
                        self.session_id, self.username or "user",
                    )
                )
        except Exception as e:
            log.debug("auto insight_extractor skip: %s", e)

        # Auto-save du projet QGIS si ce turn a modifié l'état (tool whitelist).
        # BLOQUANT (pas fire-and-forget) pour garantir la coherence avec les
        # snapshots pre-mutating (CHARTE §9 decision #3 : "bloquant avant
        # mutating") et avec treatments.jsonl (ecrit synchrone). Si l'agent
        # crash entre create_task et flush HTTP, on perdrait le .qgz a jour
        # tandis que treatments garderait trace -> rollback restaurerait un
        # etat desync. Cf. CHARTE Principe 2 (audit honnete) + Principe 4
        # (snapshots bloquants). Optimisation async = phase ulterieure
        # documentee si la latence devient un probleme observable.
        if any(c["tool"] in _MUTATING_TOOLS for c in tool_calls_made):
            try:
                await self._autosave_active_study()
            except Exception as exc:
                log.error(
                    "Auto-save bloquant a echoue (turn termine quand meme) : "
                    "%s: %s", type(exc).__name__, exc,
                )
