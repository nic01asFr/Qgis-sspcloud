"""
reference_data — récupère les données de référence INSEE sur une commune
(population, superficie, densité) quand l'user pose une question quantitative
sur une zone. Remplace l'hallucination "habitants/quartiers" par des faits.

Trigger : présence de commune ET mots-clés statistiques.
"""

from __future__ import annotations
import re
import httpx
from agent.enrichers.base import EnrichmentResult
from agent.enrichers.geo_validator import _extract_commune_candidate, _ARR_BASE

# Mots-clés qui suggèrent l'user veut des chiffres de contexte sur la zone
_STATS_KEYWORDS = re.compile(
    r"\b(population|habitants?|densité|superficie|surface|"
    r"contexte|profil|caractéristiques|description|démographie)\b",
    flags=re.IGNORECASE,
)


async def enrich(user_message: str, state: dict) -> EnrichmentResult | None:
    """Si le message contient une commune + question quantitative, retourne les faits INSEE."""
    if not _STATS_KEYWORDS.search(user_message):
        return None

    candidate = _extract_commune_candidate(user_message)
    if not candidate:
        return None

    name, arr_num = candidate

    # Code INSEE direct pour arrondissement
    insee = None
    if arr_num and name.lower() in _ARR_BASE:
        insee = _ARR_BASE[name.lower()] + arr_num
    else:
        # Résolution par nom
        try:
            async with httpx.AsyncClient(timeout=4) as c:
                r = await c.get(
                    "https://geo.api.gouv.fr/communes",
                    params={"nom": name, "fields": "code", "limit": 1, "boost": "population"},
                )
                if r.status_code == 200 and r.json():
                    insee = r.json()[0]["code"]
        except Exception:
            return None

    if not insee:
        return None

    # Récupération données de référence
    try:
        async with httpx.AsyncClient(timeout=4) as c:
            r = await c.get(
                f"https://geo.api.gouv.fr/communes/{insee}",
                params={"fields": "nom,code,codeDepartement,population,surface,centre"},
            )
            if r.status_code != 200:
                return None
            d = r.json()
    except Exception:
        return None

    pop = d.get("population")
    surf_ha = d.get("surface")  # API renvoie en hectares
    surf_km2 = surf_ha / 100.0 if surf_ha else None
    density = (pop / surf_km2) if (pop and surf_km2 and surf_km2 > 0) else None

    parts = [f"Pop. {pop:,} hab.".replace(",", " ")] if pop else []
    if surf_km2:
        parts.append(f"surface {surf_km2:.1f} km²")
    if density:
        parts.append(f"densité {density:.0f} hab/km²")

    if not parts:
        return None

    return EnrichmentResult(
        type="reference_data",
        summary=(
            f"Référence INSEE pour {d.get('nom', '?')} ({d.get('code', '?')}) : "
            + ", ".join(parts) + "."
        ),
        data={
            "insee": d.get("code"),
            "nom": d.get("nom"),
            "departement": d.get("codeDepartement"),
            "population": pop,
            "surface_km2": surf_km2,
            "density_hab_km2": density,
        },
        confidence=1.0,
    )
