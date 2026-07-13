"""agent.briques_client — Fetch des briques rules_global + rules_forbidden.

Chantier G7 (Sprint V0.3 P2) : injection des regles bibliotheque G5 dans le
system prompt agent. Ce module est un *client* du hub (source de verite pour
la bibliotheque de briques) et centralise :

- appel HTTP `GET {hub_url}/briques` + `GET {hub_url}/briques/{cat}/{id}`
- cache in-memory 60 secondes pour eviter de spammer le hub a chaque tour
- fail-soft : hub down / timeout / 401 -> retourne ([], []) + log warning,
  l'agent continue a fonctionner sans les briques (mais les sections
  GLOBAL_RULES + FORBIDDEN du prompt seront vides, format preserve).

Le format des briques renvoyees suit exactement la structure YAML source
(cf. hub/hub/briques/rules/global/*.yaml + rules/forbidden/*.yaml) :
    {
        "id": "crs_wgs84_obligatoire",
        "title": "CRS EPSG:4326 obligatoire pour rendu web",
        "severity": "error" | "warn" | "block",
        "rule_text": "...",
        "llm_hint": "...",  # optionnel
        "applies_to": [...],
        ...
    }

Le composeur du prompt (`qgis_agent._compose_prompt_sections`) attend ce
schema minimal ; les champs supplementaires sont ignores.
"""

from __future__ import annotations

import logging
import time
from typing import Any

import httpx

log = logging.getLogger("agent.briques_client")

# TTL du cache in-memory (secondes). Trade-off : 60s = les experts metier
# peuvent editer une brique et la voir prise en compte au tour suivant
# environ, sans que l'agent spamme le hub a chaque turn de chaque session.
_CACHE_TTL_SEC = 60.0

# Cache in-memory best-effort (pas de Lock : plusieurs fetch concurrents
# aboutiront au meme resultat, la derniere ecriture gagne, sans casse).
_CACHE: dict[str, Any] = {
    "timestamp": 0.0,
    "rules_global": [],
    "rules_forbidden": [],
}


def _cache_valid() -> bool:
    """True si le cache est encore frais (TTL non expire)."""
    return (time.monotonic() - _CACHE["timestamp"]) < _CACHE_TTL_SEC


def reset_cache() -> None:
    """Vide le cache. Reserve aux tests unitaires."""
    _CACHE["timestamp"] = 0.0
    _CACHE["rules_global"] = []
    _CACHE["rules_forbidden"] = []


async def _fetch_full_briques_for_category(
    client: httpx.AsyncClient,
    hub_url: str,
    category: str,
    headers: dict[str, str],
) -> list[dict]:
    """Fetch la liste light d'une categorie puis le detail complet pour chaque
    brique (endpoint hub `/briques/{cat}` ne renvoie que les meta legeres,
    sans `rule_text` ni `llm_hint`).

    Retourne une liste de briques completes. En cas d'erreur (categorie
    absente, brique manquante...), retourne [] silencieusement.
    """
    # 1) liste light -> ids
    try:
        r_list = await client.get(f"{hub_url}/briques/{category}", headers=headers)
    except Exception as exc:
        log.warning("briques : GET /briques/%s a echoue (%s)", category, exc)
        return []
    if r_list.status_code != 200:
        log.warning(
            "briques : GET /briques/%s status %s -> categorie ignoree",
            category, r_list.status_code,
        )
        return []
    try:
        light_list = r_list.json() or []
    except Exception:
        log.warning("briques : /briques/%s JSON invalide", category)
        return []

    # 2) detail complet par id
    full_list: list[dict] = []
    for meta in light_list:
        bid = meta.get("id")
        if not bid:
            continue
        try:
            r_detail = await client.get(
                f"{hub_url}/briques/{category}/{bid}", headers=headers,
            )
        except Exception as exc:
            log.warning("briques : GET /briques/%s/%s a echoue (%s)",
                        category, bid, exc)
            continue
        if r_detail.status_code != 200:
            log.warning("briques : GET /briques/%s/%s status %s",
                        category, bid, r_detail.status_code)
            continue
        try:
            brique = r_detail.json()
        except Exception:
            log.warning("briques : /briques/%s/%s JSON invalide", category, bid)
            continue
        if isinstance(brique, dict):
            full_list.append(brique)
    return full_list


async def fetch_briques_rules(
    hub_url: str,
    api_key: str,
    timeout: float = 3.0,
) -> tuple[list[dict], list[dict]]:
    """Fetch les briques `rules_global` + `rules_forbidden` depuis le hub.

    Retourne un tuple `(rules_global, rules_forbidden)` de listes de briques
    completes (dict avec `id`, `title`, `severity`, `rule_text`, ...).

    Contrat fail-soft : toute erreur reseau / auth / parse -> `([], [])`
    + log warning. Le prompt continuera a etre compose, les 2 sections
    GLOBAL_RULES + FORBIDDEN seront simplement vides.

    Cache : TTL = 60s. Les appels suivants dans la fenetre TTL renvoient
    la meme paire sans HTTP call.
    """
    # Cache hit
    if _cache_valid():
        return (
            list(_CACHE["rules_global"]),
            list(_CACHE["rules_forbidden"]),
        )

    # Backward-compat : hub non configure -> vide, format sections preserve
    if not hub_url or not api_key:
        log.warning(
            "briques : hub_url ou api_key absent -> pas de fetch. "
            "Sections GLOBAL_RULES + FORBIDDEN seront vides."
        )
        # On memorise ce resultat pour eviter re-warn a chaque tour
        _CACHE["timestamp"] = time.monotonic()
        _CACHE["rules_global"] = []
        _CACHE["rules_forbidden"] = []
        return ([], [])

    headers = {"Authorization": f"Bearer {api_key}"}
    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            rules_global = await _fetch_full_briques_for_category(
                client, hub_url, "rules_global", headers,
            )
            rules_forbidden = await _fetch_full_briques_for_category(
                client, hub_url, "rules_forbidden", headers,
            )
    except Exception as exc:
        log.warning("briques : fetch a echoue (%s) -> fallback []/[]", exc)
        # Cache le fallback pour eviter de re-tenter a chaque tour dans le TTL
        _CACHE["timestamp"] = time.monotonic()
        _CACHE["rules_global"] = []
        _CACHE["rules_forbidden"] = []
        return ([], [])

    # Memorise le succes
    _CACHE["timestamp"] = time.monotonic()
    _CACHE["rules_global"] = rules_global
    _CACHE["rules_forbidden"] = rules_forbidden
    return (rules_global, rules_forbidden)
