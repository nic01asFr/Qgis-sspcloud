"""
briques_enricher — surface la brique bibliotheque (G5) la plus pertinente pour
la requete courante et l'injecte comme rappel court dans le prompt.

Les briques globales sont deja injectees en integralite par le composer G7
(sections GLOBAL_RULES / FORBIDDEN). Cet enricher est complementaire : il
signale a l'agent quelle brique il doit particulierement suivre pour cette
requete-la (highlight), plutot que noyer l'attention dans la liste globale.

Fail-soft : hub down / timeout -> briques_client renvoie ([], []) et on
retourne None. Aucun match keywords -> None.
"""

from __future__ import annotations

import os
import re
import unicodedata

from agent import briques_client
from agent.enrichers.base import EnrichmentResult


def _strip_accents(text: str) -> str:
    """Normalise NFKD + retire diacritiques pour matching insensible aux accents.

    Bug detecte en validation Sprint V0.3 : `reglementaire` (user) ne matchait
    pas `reglementaire` dans une brique dont le title contient `reglementaire`
    (avec accent). NFKD decompose 'e' + accent, ascii ignore retire les
    combining chars.
    """
    if not text:
        return ""
    return unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode("ascii")

# Stopwords minimalistes fr : mots courts frequents qui polluent le scoring.
# On complete avec les stopwords deja utilises par recipe_matcher pour rester
# coherent. On garde intentionnellement une liste petite (l'enricher est
# un signal grossier, pas un ranker semantique).
_STOPWORDS = {
    "avec", "sans", "pour", "dans", "sous", "sur", "vers", "chez", "entre",
    "cette", "cette", "cettes", "leur", "leurs", "notre", "votre", "mais",
    "donc", "puis", "quand", "alors", "aussi", "encore", "plus", "moins",
    "tres", "bien", "fait", "faire", "faites", "avoir", "etre", "vous",
    "nous", "elles", "ils", "elle", "lui", "toi", "moi", "que", "qui",
    "quoi", "dont", "comme", "comment", "pourquoi", "combien",
    "donne", "donner", "charge", "charger", "analyse", "analyser",
    "calcule", "calculer", "genere", "generer", "produis", "produire",
    "cree", "creer", "trouve", "trouver", "compte", "compter",
    "stylise", "styliser", "exporte", "exporter", "ajoute", "ajouter",
}


def _extract_keywords(user_message: str) -> list[str]:
    """Mots >= 4 lettres, lowercased, sans diacritiques, hors stopwords."""
    if not user_message:
        return []
    words = re.findall(r"[a-zA-Zà-ÿÀ-Ÿ]{4,}", user_message)
    kws: list[str] = []
    seen: set[str] = set()
    for w in words:
        wl = _strip_accents(w.lower())
        if wl in _STOPWORDS or wl in seen:
            continue
        seen.add(wl)
        kws.append(wl)
    return kws


def _score_brique(brique: dict, keywords: list[str]) -> int:
    """Compte le nombre de keywords user presents dans title + rule_text + llm_hint.

    Robustesse P0-1 fix (2026-07-13) :
      * Casse insensible (lower).
      * Diacritiques insensibles (NFKD strip) : `reglementaire` matche `reglementaire`.
      * Pluriels basiques : `dispositifs` matche `dispositif` (strip 's' final).

    Le scoring reste volontairement grossier -- l'enricher est un signal
    "top-1 highlight", pas un ranker semantique.
    """
    haystack = _strip_accents(" ".join([
        str(brique.get("title") or ""),
        str(brique.get("rule_text") or ""),
        str(brique.get("llm_hint") or ""),
    ]).lower())
    if not haystack:
        return 0
    score = 0
    for kw in keywords:
        if kw in haystack:
            score += 1
        elif len(kw) > 4 and kw.endswith("s") and kw[:-1] in haystack:
            # `dispositifs` (user) matche `dispositif` (brique) en supprimant le s
            score += 1
    return score


def _shorten(text: str, limit: int = 200) -> str:
    """Coupe proprement a `limit` chars (fin sur espace si possible)."""
    if not text:
        return ""
    if len(text) <= limit:
        return text
    cut = text[:limit].rsplit(" ", 1)[0]
    return cut + "..."


async def enrich(user_message: str, state: dict) -> EnrichmentResult | None:
    """Score les briques rules_global + rules_forbidden vs keywords user."""
    keywords = _extract_keywords(user_message)
    if not keywords:
        return None

    # Config hub via env, meme convention que qgis_agent.
    hub_url = os.getenv("HUB_URL", "").rstrip("/")
    hub_key = os.getenv("HUB_API_KEY", os.getenv("QGIS_API_KEY", ""))

    try:
        rules_global, rules_forbidden = await briques_client.fetch_briques_rules(
            hub_url, hub_key,
        )
    except Exception:
        return None

    all_briques = list(rules_global) + list(rules_forbidden)
    if not all_briques:
        return None

    best: dict | None = None
    best_score = 0
    for brique in all_briques:
        score = _score_brique(brique, keywords)
        if score > best_score:
            best_score = score
            best = brique

    if not best or best_score <= 0:
        return None

    bid = best.get("id") or "?"
    severity = best.get("severity") or "info"
    title = best.get("title") or "(sans titre)"
    llm_hint = _shorten(best.get("llm_hint") or best.get("rule_text") or "", 200)

    summary = (
        f"BRIQUE PERTINENTE ({bid}, {severity}) : {title} — {llm_hint}"
    )

    return EnrichmentResult(
        type="brique_match",
        summary=summary,
        data={
            "id": bid,
            "severity": severity,
            "title": title,
            "score": best_score,
            "matched_keywords_count": best_score,
        },
        confidence=min(0.5 + 0.1 * best_score, 1.0),
    )
