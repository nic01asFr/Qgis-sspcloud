"""
recipe_matcher — propose à l'agent la recette la plus pertinente pour la
requête courante. Évite que l'agent improvise avec execute_python quand
une recette existe et marche déjà.

Trigger : présence de mots-clés métier matchant une recette indexée.
Algo : intersection des mots-clés query ↔ tags/nom recette, scoring simple.
"""

from __future__ import annotations
import re
from agent.enrichers.base import EnrichmentResult
from agent import memory

# Mots vides à ignorer dans le scoring
_STOPWORDS = {
    "de", "la", "le", "les", "des", "du", "un", "une", "et", "ou", "à", "au",
    "aux", "sur", "dans", "pour", "avec", "sans", "par", "ce", "cette", "ces",
    "que", "qui", "quoi", "dont", "où", "je", "tu", "il", "elle", "nous", "vous",
    "ils", "elles", "me", "te", "se", "mon", "ma", "mes", "ton", "ta", "tes",
    "son", "sa", "ses", "notre", "votre", "leur", "leurs", "moi", "toi", "lui",
    "fait", "fais", "faire", "donne", "donner", "donnes", "calcule", "calculer",
    "analyse", "analyser", "trouve", "trouver", "charge", "charger", "exporte",
    "exporter", "génère", "générer", "produire", "produis", "crée", "créer",
    "stylise", "styliser", "compte", "compter", "comptes", "moi",
}


def _tokenize(s: str) -> set[str]:
    """Mots significatifs en minuscule (lettres uniquement, >= 3 chars)."""
    words = re.findall(r"[a-zà-ÿ]{3,}", s.lower())
    return {w for w in words if w not in _STOPWORDS}


async def enrich(user_message: str, state: dict) -> EnrichmentResult | None:
    """Cherche LA recette la plus pertinente. Skip si rien de fort."""
    try:
        recipes = await memory.list_recipes()
    except Exception:
        return None
    if not recipes:
        return None

    query_tokens = _tokenize(user_message)
    if not query_tokens:
        return None

    # Score chaque recette : intersection tokens query ↔ nom + description + tags
    best = None
    best_score = 0
    for r in recipes:
        haystack = " ".join([
            r.get("name", ""),
            r.get("description", "") or "",
            " ".join(r.get("tags", []) or []),
        ])
        recipe_tokens = _tokenize(haystack)
        if not recipe_tokens:
            continue
        intersection = query_tokens & recipe_tokens
        if not intersection:
            continue
        # Score normalisé : couverture de la query par la recette
        score = len(intersection) / max(len(query_tokens), 1)
        if score > best_score:
            best_score = score
            best = (r, intersection)

    # Seuil de pertinence : au moins 25% des mots-clés query couverts
    if not best or best_score < 0.25:
        return None

    recipe, matched = best
    return EnrichmentResult(
        type="recipe_match",
        summary=(
            f"Recette candidate : « {recipe.get('name')} » "
            f"(score {best_score:.0%}, matches : {', '.join(sorted(matched))}). "
            f"Envisage `run_recipe` avant d'improviser."
        ),
        data={
            "id": recipe.get("id"),
            "name": recipe.get("name"),
            "description": recipe.get("description"),
            "matched_keywords": sorted(matched),
            "score": round(best_score, 2),
        },
        confidence=min(best_score * 1.5, 1.0),  # 0.25 score → 0.375 confidence
    )
