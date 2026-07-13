"""agent.hub_scope_client — Fetch cible pour l'enrichissement L2 par contexte.

Chantier G9 (Sprint V0.3 P2) : quand le contexte UI est ciblé (drawer sur un
composant, panel sur un assembly, exécution d'une recipe...), la couche L2
du prompt agent doit être filtrée / enrichie autour du scope pertinent
plutôt que d'énumérer toute l'étude.

Ce client centralise les 3 fetches complémentaires appelés depuis
`build_context_summary` (memory.py) :

- `fetch_component_history(hub_url, api_key, sid, cid, limit=5)`
  historique éditorial d'un composant → `GET /studies/{sid}/components/{cid}/history`
- `fetch_assembly_components(hub_url, api_key, sid, aid)`
  composants référencés par un assembly → `GET /studies/{sid}/assemblies/{aid}`
- `fetch_recipe_recent_runs(hub_url, api_key, recipe_id, limit=3)`
  3 dernières exécutions réussies via `memory.find_sessions_by_tag(
  "recipe_run_ok", recipe_id)` (chantier G1). Pas d'endpoint hub dédié.

Contrats communs :
- **Fail-soft** : toute exception réseau / auth / parse → `[]` + log warning.
  L'agent continue de fonctionner, la section L2 sera juste plus courte.
- **Cache in-memory** : TTL 30 s. Un cache par (fonction, params) pour éviter
  de spammer le hub à chaque tour de conversation.
- **Backward-compat** : hub_url ou api_key vide → `[]` sans HTTP call.

Pattern calqué sur `briques_client.py` (chantier G7).
"""

from __future__ import annotations

import logging
import time
from typing import Any

import httpx

log = logging.getLogger("agent.hub_scope_client")

# TTL du cache in-memory (secondes). 30 s = compromis entre "l'utilisateur
# vient d'éditer le composant, l'agent doit voir la nouvelle version" et
# "ne pas spammer le hub à chaque turn de la même session".
_CACHE_TTL_SEC = 30.0

_HTTP_TIMEOUT_SEC = 3.0

# Cache in-memory : { cache_key: (timestamp, value) }
# best-effort, pas de Lock (concurrence bénigne : mêmes params → même valeur).
_CACHE: dict[str, tuple[float, list[dict]]] = {}


def reset_cache() -> None:
    """Vide le cache. Réservé aux tests unitaires."""
    _CACHE.clear()


def _cache_get(key: str) -> list[dict] | None:
    """Retourne la valeur en cache si TTL non expiré, sinon None."""
    entry = _CACHE.get(key)
    if entry is None:
        return None
    ts, val = entry
    if (time.monotonic() - ts) >= _CACHE_TTL_SEC:
        return None
    # Copie défensive pour éviter qu'un appelant mute la liste cachée.
    return list(val)


def _cache_set(key: str, value: list[dict]) -> None:
    """Stocke value en cache avec timestamp courant."""
    _CACHE[key] = (time.monotonic(), list(value))


# ── fetch_component_history ─────────────────────────────────────────────────

async def fetch_component_history(
    hub_url: str,
    api_key: str,
    sid: str,
    cid: str,
    limit: int = 5,
) -> list[dict]:
    """Fetch l'historique éditorial des N dernières versions d'un composant.

    Endpoint hub : `GET /studies/{sid}/components/{cid}/history`
    (cf. hub/hub/main.py:4359 — Sprint Composants Phase 3b).

    Retourne une liste de dicts (schéma tel quel renvoyé par le hub, chaque
    item décrit une version : version, created_at, author, summary...).
    Tronqué à `limit` items.

    Fail-soft : hub down / 404 / JSON invalide / creds absents → `[]`.
    """
    if not (hub_url and api_key and sid and cid):
        return []

    key = f"component_history::{sid}::{cid}::{limit}"
    cached = _cache_get(key)
    if cached is not None:
        return cached

    headers = {"Authorization": f"Bearer {api_key}"}
    url = f"{hub_url}/studies/{sid}/components/{cid}/history"
    try:
        async with httpx.AsyncClient(timeout=_HTTP_TIMEOUT_SEC) as c:
            r = await c.get(url, headers=headers)
    except Exception as exc:
        log.warning("fetch_component_history(%s/%s) : erreur réseau (%s)",
                    sid, cid, exc)
        _cache_set(key, [])
        return []

    if r.status_code != 200:
        log.warning("fetch_component_history(%s/%s) : status %s",
                    sid, cid, r.status_code)
        _cache_set(key, [])
        return []

    try:
        data = r.json()
    except Exception:
        log.warning("fetch_component_history(%s/%s) : JSON invalide", sid, cid)
        _cache_set(key, [])
        return []

    # Le hub peut renvoyer soit une liste directe soit {"history": [...]}.
    if isinstance(data, list):
        items = data
    elif isinstance(data, dict):
        items = data.get("history") or data.get("items") or []
    else:
        items = []

    result = [it for it in items if isinstance(it, dict)][:limit]
    _cache_set(key, result)
    return result


# ── fetch_assembly_components ───────────────────────────────────────────────

async def fetch_assembly_components(
    hub_url: str,
    api_key: str,
    sid: str,
    aid: str,
) -> list[dict]:
    """Fetch les composants référencés par un assembly.

    Endpoint hub : `GET /studies/{sid}/assemblies/{aid}` (cf. main.py:5147).
    La liste des composants attendus dans la clé `components_refs` du
    manifest (cf. audit_chain_json documenté dans hub_artifacts.py).

    Retourne une liste de dicts. Chaque item peut être minimal :
    `{"cid": "..."}` ou enrichi si le hub joint le résumé du composant.

    Fail-soft : `[]` en cas d'erreur.
    """
    if not (hub_url and api_key and sid and aid):
        return []

    key = f"assembly_components::{sid}::{aid}"
    cached = _cache_get(key)
    if cached is not None:
        return cached

    headers = {"Authorization": f"Bearer {api_key}"}
    url = f"{hub_url}/studies/{sid}/assemblies/{aid}"
    try:
        async with httpx.AsyncClient(timeout=_HTTP_TIMEOUT_SEC) as c:
            r = await c.get(url, headers=headers)
    except Exception as exc:
        log.warning("fetch_assembly_components(%s/%s) : erreur réseau (%s)",
                    sid, aid, exc)
        _cache_set(key, [])
        return []

    if r.status_code != 200:
        log.warning("fetch_assembly_components(%s/%s) : status %s",
                    sid, aid, r.status_code)
        _cache_set(key, [])
        return []

    try:
        data = r.json()
    except Exception:
        log.warning("fetch_assembly_components(%s/%s) : JSON invalide", sid, aid)
        _cache_set(key, [])
        return []

    if not isinstance(data, dict):
        _cache_set(key, [])
        return []

    # Format attendu : {"components": [...], ...} ou {"components_refs": [...]}
    comps: list[dict] = []
    raw_comps = data.get("components")
    if isinstance(raw_comps, list):
        for it in raw_comps:
            if isinstance(it, dict):
                comps.append(it)
            elif isinstance(it, str):
                comps.append({"cid": it})
    else:
        refs = data.get("components_refs") or []
        if isinstance(refs, list):
            for ref in refs:
                if isinstance(ref, str):
                    comps.append({"cid": ref})
                elif isinstance(ref, dict) and ref.get("cid"):
                    comps.append(ref)

    _cache_set(key, comps)
    return comps


# ── fetch_recipe_recent_runs ────────────────────────────────────────────────

async def fetch_recipe_recent_runs(
    hub_url: str,
    api_key: str,
    recipe_id: str,
    limit: int = 3,
) -> list[dict]:
    """Fetch les N dernières exécutions réussies d'une recipe.

    Pas d'endpoint hub dédié : on interroge la mémoire agent locale via
    `memory.find_sessions_by_tag("recipe_run_ok", recipe_id)` (chantier G1).

    Retourne une liste de dicts `{"session_id": "..."}`. Le composeur L2
    n'a besoin que du session_id pour l'annoncer dans le prompt ; les détails
    (durée, tools utilisés) restent hors scope de ce chantier.

    Les paramètres `hub_url` et `api_key` sont acceptés pour homogénéité
    de signature avec les 2 autres fetchers ; ils ne sont pas utilisés
    aujourd'hui mais permettent, sans casser d'appelant, de brancher un
    endpoint dédié plus tard.

    Fail-soft : toute exception → `[]`.
    """
    _ = hub_url, api_key  # placeholder pour extension future
    if not recipe_id:
        return []

    key = f"recipe_recent_runs::{recipe_id}::{limit}"
    cached = _cache_get(key)
    if cached is not None:
        return cached

    try:
        # Import différé pour éviter cycle memory.py <-> hub_scope_client.py
        from agent import memory  # noqa: WPS433
        session_ids = await memory.find_sessions_by_tag(
            "recipe_run_ok", recipe_id, limit=limit,
        )
    except Exception as exc:
        log.warning("fetch_recipe_recent_runs(%s) : lookup mémoire échoué (%s)",
                    recipe_id, exc)
        _cache_set(key, [])
        return []

    result: list[dict] = [{"session_id": sid} for sid in session_ids[:limit]]
    _cache_set(key, result)
    return result


# ── Résumé debug (usage manuel) ─────────────────────────────────────────────

def cache_snapshot() -> dict[str, Any]:
    """Retourne un snapshot lisible du cache (usage debug/tests)."""
    now = time.monotonic()
    return {
        k: {"age_s": round(now - ts, 2), "n_items": len(val)}
        for k, (ts, val) in _CACHE.items()
    }
