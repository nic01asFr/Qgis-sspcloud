"""hub.recipes_web.polish_validator -- Validation post-LLM des invariants.

Fix H5 revue adversariale Sprint V0.4.1. Le mode `recipe_polished` (G4-b-3b)
declarait "anti-hallucination : le LLM ne peut pas modifier les chiffres,
sources, noms propres" -- mais cette garantie n'etait exprimee QUE dans le
prompt system. Rien ne verifiait programmatiquement que le texte reformule
par le LLM preservait effectivement les faits.

Ce module extrait les faits saillants du texte original (chiffres avec unite,
URLs, sigles/acronymes ALL CAPS) et verifie que le texte poli conserve
exactement le meme ensemble. Si un fait est ajoute ou supprime, on considere
que le LLM a viole le contract et on garde l'original (fail-soft).

Volontairement conservateur : mieux vaut refuser un polish valide (faux
positif) que d'accepter un polish qui hallucine (faux negatif). Le sprint
precedent P1-3 a montre qu'une hallucination silencieuse (PPR Tartifness)
survit au review humain -- la garde-fou doit etre automatique.
"""
from __future__ import annotations

import re

# Chiffres avec unite/percent : 2019, 45%, 12,5, 1.5, 100000.
# On capture avec l'unite pour distinguer "45" (nombre pur) de "45%" (pourcent).
_RE_NUMBERS = re.compile(r"\b\d+(?:[.,]\d+)?%?\b")

# URLs (http/https). Termine au premier espace ou parenthese fermante.
_RE_URL = re.compile(r"https?://[^\s\)\]]+")

# Acronymes ALL CAPS de 2+ chars : PPR, INSEE, DDRM, CEREMA, EPCI, RGPD.
# Exclut les mots courants (rare qu'un mot francais soit 2+ lettres ALL CAPS).
_RE_ACRONYM = re.compile(r"\b[A-Z]{2,}[A-Z0-9]*\b")


def _extract_facts(text: str) -> dict[str, set[str]]:
    """Extrait les faits saillants d'un texte : chiffres, URLs, acronymes."""
    return {
        "numbers": set(_RE_NUMBERS.findall(text)),
        "urls": set(_RE_URL.findall(text)),
        "acronyms": set(_RE_ACRONYM.findall(text)),
    }


def validate_polish(original: str, polished: str) -> tuple[bool, list[str]]:
    """Verifie que `polished` preserve les faits saillants de `original`.

    Retourne `(ok, violations)`. `ok=True` ssi tous les chiffres, URLs et
    acronymes du texte original sont presents dans le polished, et qu'aucun
    fait nouveau n'a ete introduit. La comparaison est set-based donc
    l'ordre et la multiplicite ne comptent pas (un chiffre repete plusieurs
    fois dans l'original peut apparaitre une seule fois dans le polished --
    acceptable pour du reformulage stylistique).

    `violations` liste les diff avec prefixe `{kind}_{action}:` pour tracing.
    """
    orig = _extract_facts(original or "")
    new = _extract_facts(polished or "")

    violations: list[str] = []
    for kind in ("numbers", "urls", "acronyms"):
        removed = orig[kind] - new[kind]
        added = new[kind] - orig[kind]
        if removed:
            violations.append(f"{kind}_removed:{sorted(removed)}")
        if added:
            violations.append(f"{kind}_added:{sorted(added)}")

    return (not violations), violations
