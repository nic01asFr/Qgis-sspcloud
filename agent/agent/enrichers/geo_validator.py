"""
geo_validator — détecte une référence de commune/zone dans le message user
et pré-vérifie sa résolution via api.gouv.fr AVANT que l'agent appelle
set_study_zone. Sauve l'agent du piège géocodage fuzzy (cf bug
"Marseille 4e" → "Marseille-en-Beauvaisis").

Trigger : pattern regex commune française. Sortie : nom résolu, code INSEE,
confiance basée sur match exact ou fuzzy.
"""

from __future__ import annotations
import re
import httpx
from agent.enrichers.base import EnrichmentResult

# Pattern dédié arrondissement municipal — très spécifique, priorité haute.
_ARR_RE = re.compile(
    r"\b(Marseille|Paris|Lyon)\s+(\d{1,2})\s*(?:e|er|ème|eme|ième)?"
    r"\s*(?:arr(?:ondissement)?)?\b",
    flags=re.IGNORECASE,
)

# Préposition locative + nom capitalisé : signal le plus fort en français.
# "sur Sète", "à Aix-en-Provence", "de Saint-Étienne", "dans Le Lavandou",
# "pour Marseille", etc. Capture le nom (groupe 1).
_LOCATIVE_RE = re.compile(
    r"\b(?:sur|à|de|du|d'|dans|pour|vers|chez)\s+"
    r"((?:Le|La|Les|L')\s+[A-ZÀ-Ö][\w'\-àâäéèêëîïôöùûüç]+"
    r"(?:[\s\-][\w'\-àâäéèêëîïôöùûüç]+)*"
    r"|Sainte?\-[A-ZÀ-Ö][\w'\-àâäéèêëîïôöùûüç]+"
    r"(?:\-[\w'\-àâäéèêëîïôöùûüç]+)*"
    r"|[A-ZÀ-Ö][a-zà-öø-ÿ]{2,}"  # simple capitalized name (Sète, Marseille, Lyon)
    r"(?:\-[a-zà-öø-ÿA-ZÀ-Ö]+){0,4})\b",  # avec ou sans suffixes hyphénés
    flags=re.UNICODE,
)

# Fallback : nom composé hyphené (Aix-en-Provence, Saint-Pol-sur-Mer, etc.).
# Inclut tirets multiples — typique des toponymes français composés.
_COMPOUND_RE = re.compile(
    r"\b[A-ZÀ-Ö][a-zà-öø-ÿ]{2,}"
    r"(?:\-(?:[a-zà-öø-ÿ]+|[A-ZÀ-Ö][a-zà-öø-ÿ]+)){2,}"
    r"|\b(?:Le|La|Les)\s+[A-ZÀ-Ö][a-zà-öø-ÿ]+"
    r"(?:[\s\-][\w'\-àâäéèêëîïôöùûüç]+)*"
    r"|\bSainte?\-[A-ZÀ-Ö][a-zà-öø-ÿ]+"
    r"(?:\-[\w'\-àâäéèêëîïôöùûüç]+)*"
)

# Arrondissements municipaux (Paris/Marseille/Lyon) : on a déjà un patch INSEE
# côté workspace pod (fix #5a), mais on flag ici en amont.
_ARR_BASE = {"paris": 75100, "marseille": 13200, "lyon": 69380}
_ARR_LIMIT = {"paris": 20, "marseille": 16, "lyon": 9}


async def enrich(user_message: str, state: dict) -> EnrichmentResult | None:
    """
    Cherche un nom de commune dans le message. Si trouvé, résout via api.gouv.fr
    et compare avec le texte original pour détecter les ambiguïtés.
    """
    candidate = _extract_commune_candidate(user_message)
    if not candidate:
        return None

    name, arr_num = candidate

    # Cas spécial arrondissement municipal → résolution directe via INSEE
    if arr_num and name.lower() in _ARR_BASE:
        return await _resolve_arrondissement(name, arr_num)

    # Cas général : recherche commune par nom
    return await _resolve_commune(name)


def _extract_commune_candidate(msg: str) -> tuple[str, int | None] | None:
    """Premier match plausible de commune dans le message.

    Ordre de priorité (du plus spécifique au plus permissif) :
    (1) arrondissement Paris/Marseille/Lyon explicite,
    (2) préposition locative + nom (sur/à/dans X),
    (3) composé hyphené (Aix-en-Provence, Saint-X).
    """
    # 1. Arrondissement municipal (très spécifique)
    arr_match = _ARR_RE.search(msg)
    if arr_match:
        return arr_match.group(1).capitalize(), int(arr_match.group(2))

    # 2. Préposition locative + nom capitalisé (signal fort)
    for m in _LOCATIVE_RE.finditer(msg):
        candidate = m.group(1).strip()
        if _is_blacklisted(candidate):
            continue
        return candidate, None

    # 3. Composé hyphené (Aix-en-Provence, Le Lavandou en isolé)
    for m in _COMPOUND_RE.finditer(msg):
        candidate = m.group(0).strip()
        if _is_blacklisted(candidate):
            continue
        return candidate, None

    return None


def _is_blacklisted(name: str) -> bool:
    """Vérifie si le nom matché ressemble en fait à un terme courant non-commune."""
    if not name:
        return True
    first_word = name.split()[0].lower()
    if first_word in _START_BLACKLIST:
        return True
    return name.lower() in _FULL_BLACKLIST


# Mots français qui peuvent commencer par une majuscule en début de phrase
# sans être des communes (interrogatifs, verbes, adjectifs).
_START_BLACKLIST = {
    "quel", "quelle", "quels", "quelles", "que", "qui", "quoi", "comment",
    "pourquoi", "combien", "où",
    "donne", "donnes", "donner",
    "charge", "charges", "charger",
    "analyse", "analyses", "analyser",
    "calcule", "calcules", "calculer",
    "crée", "crées", "créer",
    "exporte", "exportes", "exporter",
    "fais", "faire", "faites",
    "trouve", "trouves", "trouver",
    "génère", "génères", "générer",
    "compte", "comptes", "compter",
    "stylise", "stylises", "styliser",
    "ajoute", "ajoutes", "ajouter",
    "vérifie", "vérifies", "vérifier",
    "regarde", "regardes", "regarder",
    "salut", "bonjour", "merci", "bonsoir", "ok", "oui", "non",
}

_FULL_BLACKLIST = {
    "la suite", "le projet", "le bati", "les couches", "la zone", "le bâti",
}


async def _resolve_arrondissement(ville: str, num: int) -> EnrichmentResult | None:
    """Résout un arrondissement municipal via son code INSEE direct."""
    v = ville.lower()
    if v not in _ARR_BASE:
        return None
    if num < 1 or num > _ARR_LIMIT[v]:
        return EnrichmentResult(
            type="geo_validation",
            summary=f"⚠️ {ville} {num}e n'existe pas (limites : 1-{_ARR_LIMIT[v]}).",
            data={"requested": f"{ville} {num}e", "valid": False},
            confidence=1.0,
            warnings=f"Numéro d'arrondissement invalide pour {ville}",
        )
    insee = _ARR_BASE[v] + num
    try:
        async with httpx.AsyncClient(timeout=4) as c:
            r = await c.get(
                f"https://geo.api.gouv.fr/communes/{insee}",
                params={"fields": "nom,code,centre"},
            )
            if r.status_code == 200:
                d = r.json()
                return EnrichmentResult(
                    type="geo_validation",
                    summary=(
                        f"« {ville} {num}e » → {d.get('nom', '?')} (INSEE {d.get('code')}). "
                        f"Centre : {d.get('centre', {}).get('coordinates', '?')}."
                    ),
                    data={
                        "requested": f"{ville} {num}e",
                        "resolved_name": d.get("nom"),
                        "insee": d.get("code"),
                        "type": "arrondissement_municipal",
                    },
                    confidence=1.0,
                )
    except Exception:
        return None
    return None


async def _resolve_commune(name: str) -> EnrichmentResult | None:
    """Résout une commune par nom. Détecte les ambiguïtés (plusieurs matches)."""
    try:
        async with httpx.AsyncClient(timeout=4) as c:
            r = await c.get(
                "https://geo.api.gouv.fr/communes",
                params={
                    "nom": name,
                    "fields": "nom,code,population,centre",
                    "limit": 5,
                    "boost": "population",
                },
            )
            if r.status_code != 200:
                return None
            results = r.json()
            if not results:
                return None
    except Exception:
        return None

    first = results[0]
    exact_matches = [c for c in results if c.get("nom", "").lower() == name.lower()]
    near_matches = [c for c in results if name.lower() in c.get("nom", "").lower()]

    # Cas idéal : 1 seul match exact
    if len(exact_matches) == 1:
        c = exact_matches[0]
        return EnrichmentResult(
            type="geo_validation",
            summary=(
                f"« {name} » → {c['nom']} (INSEE {c['code']}, "
                f"pop. {c.get('population', '?')})."
            ),
            data={
                "requested": name,
                "resolved_name": c["nom"],
                "insee": c["code"],
                "population": c.get("population"),
                "alternatives": [
                    {"nom": x["nom"], "code": x["code"]}
                    for x in results[1:5] if x["code"] != c["code"]
                ],
            },
            confidence=1.0,
        )

    # Cas ambigu : aucun match exact mais des proches
    if not exact_matches and first:
        return EnrichmentResult(
            type="geo_validation",
            summary=(
                f"⚠️ « {name} » résolu en {first['nom']} (INSEE {first['code']}) "
                f"par fuzzy match — alternatives : "
                f"{', '.join(x['nom'] for x in results[1:4])}."
            ),
            data={
                "requested": name,
                "resolved_name": first["nom"],
                "insee": first["code"],
                "alternatives": [{"nom": x["nom"], "code": x["code"]} for x in results[:5]],
            },
            confidence=0.5,  # ambiguïté → l'agent doit nuancer
            warnings=(
                f"Le nom « {name} » n'a pas de correspondance exacte. "
                f"Vérifie avec l'utilisateur avant analyse."
            ),
        )

    # Plusieurs matches exacts (homonymes) — rare mais arrive
    if len(exact_matches) > 1:
        homonymes_list = ", ".join(
            f"{c['nom']} ({c['code']})" for c in exact_matches[:3]
        )
        return EnrichmentResult(
            type="geo_validation",
            summary=(
                f"⚠️ Plusieurs communes nommées « {name} » : "
                f"{homonymes_list}. L'utilisateur doit préciser."
            ),
            data={
                "requested": name,
                "candidates": [
                    {"nom": c["nom"], "code": c["code"], "population": c.get("population")}
                    for c in exact_matches
                ],
            },
            confidence=0.3,
            warnings="Homonymie — demander un département ou un code postal.",
        )
    return None
