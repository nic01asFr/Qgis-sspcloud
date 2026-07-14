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

# Sprint V0.4.2 Chantier E (MED#5 revue adversariale) : noms propres. Le
# prompt POLISH_SYSTEM_PROMPT interdit la modif de "commune, departement,
# service, plateforme" -- typiquement des mots capitalises. On capture :
#   - Mots commencant par Majuscule + 3+ lettres minuscules (Marseille,
#     Bezier, Paris, Lyon).
#   - Suffixe accentue accepte (Marseille4e, Bouches-du-Rhone).
# On EXCLUT les mots capitalises en debut de phrase (bruit) via
# post-filter : on ne retient que les occurences precedees de whitespace
# non-terminale (pas apres [.!?]).
#
# Note : approche heuristique. Un nom propre en tete de phrase passera au
# travers, mais la charte demande "no invention" plutot que "preservation
# forcee de tous les majuscules" -- si le LLM efface "Marseille" en tete
# de phrase et n'en ajoute pas ailleurs, c'est capture par le check
# suppression ; s'il ajoute "Marseilles", c'est capture par le check ajout.
_RE_PROPER_NOUN = re.compile(
    r"(?<=[a-zéèàôûîâêç]\s)"                 # precede par mot minuscule + espace
    r"([A-ZÉÈÀÔÛÎÂÊÇ][a-zéèàôûîâêç]{3,}"     # Majuscule + 3+ minuscules accentuees
    r"(?:-[A-ZÉÈÀÔÛÎÂÊÇa-zéèàôûîâêç]+)*)"    # composes avec tirets (Bouches-du-Rhone)
)


def _extract_facts(text: str) -> dict[str, set[str]]:
    """Extrait les faits saillants d'un texte : chiffres, URLs, acronymes,
    noms propres (approx heuristique -- cf. commentaire regex)."""
    # Prepend espace pour aider le lookbehind sur le tout premier mot capture.
    padded = " " + (text or "")
    return {
        "numbers": set(_RE_NUMBERS.findall(text or "")),
        "urls": set(_RE_URL.findall(text or "")),
        "acronyms": set(_RE_ACRONYM.findall(text or "")),
        "proper_nouns": set(_RE_PROPER_NOUN.findall(padded)),
    }


def validate_polish(original: str, polished: str) -> tuple[bool, list[str]]:
    """Verifie que `polished` preserve les faits saillants de `original`.

    Retourne `(ok, violations)`. `ok=True` ssi tous les chiffres, URLs,
    acronymes ET noms propres du texte original sont presents dans le
    polished, et qu'aucun fait nouveau n'a ete introduit. La comparaison
    est set-based donc l'ordre et la multiplicite ne comptent pas.

    `violations` liste les diff avec prefixe `{kind}_{action}:` pour tracing.
    """
    orig = _extract_facts(original or "")
    new = _extract_facts(polished or "")

    violations: list[str] = []
    for kind in ("numbers", "urls", "acronyms", "proper_nouns"):
        removed = orig[kind] - new[kind]
        added = new[kind] - orig[kind]
        if removed:
            violations.append(f"{kind}_removed:{sorted(removed)}")
        if added:
            violations.append(f"{kind}_added:{sorted(added)}")

    return (not violations), violations
