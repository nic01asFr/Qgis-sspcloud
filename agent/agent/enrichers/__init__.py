"""
enrichers — sous-agents déterministes (regex + asyncio) qui enrichissent le
contexte de l'agent principal AVANT l'appel LLM.

Pattern Anthropic "Building Effective Agents" / Adaptive RAG :
- déclenchés par regex/mots-clés (PAS un LLM router, latence + coût)
- s'exécutent en parallèle via asyncio.gather
- retournent EnrichmentResult | None
- les non-None sont injectés dans le system prompt section "Contexte enrichi"

Pas de pivot d'archi : chaque enricher est une fonction async pure.
"""

from agent.enrichers.base import EnrichmentResult
from agent.enrichers import (
    geo_validator,
    reference_data,
    recipe_matcher,
    memory_recall,
    bbox_enricher,
    layer_id_enricher,
    briques_enricher,
)
import asyncio

# Ordre = priorité d'affichage dans le prompt. Pas de dépendance entre eux.
# Les 3 enrichers G6 (bbox, layer_id, brique) sont places en tete du bloc
# geo-technique : ils cadrent la zone / la donnee / la regle avant que
# l'agent voie la resolution INSEE ou la recette candidate.
_ENRICHERS = [
    bbox_enricher.enrich,
    geo_validator.enrich,
    layer_id_enricher.enrich,
    reference_data.enrich,
    briques_enricher.enrich,
    recipe_matcher.enrich,
    memory_recall.enrich,
]


async def run_all(user_message: str, state: dict | None = None) -> list[EnrichmentResult]:
    """Lance tous les enrichers en parallèle, retourne ceux qui ont matché."""
    state = state or {}
    results = await asyncio.gather(
        *[e(user_message, state) for e in _ENRICHERS],
        return_exceptions=True,
    )
    return [
        r for r in results
        if isinstance(r, EnrichmentResult)
    ]


def format_for_prompt(results: list[EnrichmentResult]) -> str:
    """Formate les enrichments pour injection dans le system prompt."""
    if not results:
        return ""
    lines = ["=== Contexte enrichi pour cette requête ==="]
    for r in results:
        warn = f"  ⚠️ {r.warnings}" if r.warnings else ""
        conf = f" (confiance {r.confidence:.0%})" if r.confidence < 1.0 else ""
        lines.append(f"• [{r.type}]{conf} {r.summary}")
        if warn:
            lines.append(warn)
    # Si on a déjà chargé du rappel sémantique automatique, éviter que l'agent
    # rappelle memory_search/memory_similar inutilement (gaspillage tokens + tour).
    if any(r.type == "memory_recall" for r in results):
        lines.append(
            "→ Ce rappel ci-dessus est suffisant pour la plupart des cas. "
            "N'appelle memory_search/memory_similar QUE si tu manques d'un détail "
            "précis non couvert (ex: une valeur exacte, une session particulière)."
        )
    return "\n".join(lines)
