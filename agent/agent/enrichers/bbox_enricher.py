"""
bbox_enricher — detecte une bbox WGS84 dans le message user, calcule sa
surface approchee en km2 et resout le centre via l'API BAN (reverse geocoding).

Signal fort : l'utilisateur colle un tuple lng,lat,lng,lat -> l'agent doit
comprendre la zone AVANT de choisir set_study_zone / smart_load.

Fail-soft : BAN timeout / down -> retour utile sans ville (surface calculee
en local). Pas de match -> None. Aucune exception ne remonte a l'agent.
"""

from __future__ import annotations

import math
import re

import httpx

from agent.enrichers.base import EnrichmentResult

# Regex bbox : 4 floats separes par virgules, chacun signes optionnellement.
# On accepte des espaces autour des virgules pour resister au copier-coller.
_BBOX_RE = re.compile(
    r"\b(-?\d+\.\d+)\s*,\s*(-?\d+\.\d+)\s*,\s*(-?\d+\.\d+)\s*,\s*(-?\d+\.\d+)\b"
)

# Rayon terrestre moyen (km) pour haversine.
_R_KM = 6371.0

# Timeout BAN reverse geocoding — 3s comme demande dans le brief.
_BAN_TIMEOUT_SEC = 3.0


def _valid_bbox(lng_min: float, lat_min: float, lng_max: float, lat_max: float) -> bool:
    """Coherence WGS84 : bornes lat/lng, min < max sur les 2 axes."""
    if not (-180.0 <= lng_min <= 180.0 and -180.0 <= lng_max <= 180.0):
        return False
    if not (-90.0 <= lat_min <= 90.0 and -90.0 <= lat_max <= 90.0):
        return False
    if lng_min >= lng_max or lat_min >= lat_max:
        return False
    return True


def _bbox_area_km2(
    lng_min: float, lat_min: float, lng_max: float, lat_max: float,
) -> float:
    """Surface approchee via haversine sur les 2 cotes du rectangle.

    Approximation valide pour bbox << continent. Formule : produit du
    cote horizontal (au milieu de la latitude) et du cote vertical.
    """
    lat_mid = math.radians((lat_min + lat_max) / 2.0)
    dlng = math.radians(lng_max - lng_min)
    dlat = math.radians(lat_max - lat_min)
    width_km = _R_KM * dlng * math.cos(lat_mid)
    height_km = _R_KM * dlat
    return abs(width_km * height_km)


async def _reverse_geocode(lng: float, lat: float) -> str | None:
    """Appelle l'API BAN sur le centre. Retourne un libelle court ou None."""
    try:
        async with httpx.AsyncClient(timeout=_BAN_TIMEOUT_SEC) as client:
            r = await client.get(
                "https://api-adresse.data.gouv.fr/reverse",
                params={"lon": lng, "lat": lat},
            )
            if r.status_code != 200:
                return None
            data = r.json() or {}
    except Exception:
        return None

    features = data.get("features") or []
    if not features:
        return None
    props = features[0].get("properties") or {}
    # On prefere le nom de la commune, sinon le label complet.
    city = props.get("city") or props.get("name")
    if city:
        return str(city)
    label = props.get("label")
    return str(label) if label else None


async def enrich(user_message: str, state: dict) -> EnrichmentResult | None:
    """Detecte une bbox WGS84 et enrichit avec surface + ville proche."""
    match = _BBOX_RE.search(user_message or "")
    if not match:
        return None

    try:
        lng_min = float(match.group(1))
        lat_min = float(match.group(2))
        lng_max = float(match.group(3))
        lat_max = float(match.group(4))
    except ValueError:
        return None

    if not _valid_bbox(lng_min, lat_min, lng_max, lat_max):
        return None

    surface_km2 = _bbox_area_km2(lng_min, lat_min, lng_max, lat_max)
    center_lng = (lng_min + lng_max) / 2.0
    center_lat = (lat_min + lat_max) / 2.0

    city = await _reverse_geocode(center_lng, center_lat)

    bbox_str = f"{lng_min},{lat_min},{lng_max},{lat_max}"
    km2_str = f"{surface_km2:.2f}" if surface_km2 < 100 else f"{surface_km2:.1f}"

    if city:
        summary = (
            f"BBOX detectee : {bbox_str} — surface approx {km2_str} km² — "
            f"centre proche de {city}."
        )
        confidence = 1.0
    else:
        summary = (
            f"BBOX detectee : {bbox_str} — surface approx {km2_str} km² — "
            f"centre ({center_lng:.4f}, {center_lat:.4f})."
        )
        # Un peu moins de confiance sans reverse geocoding valide.
        confidence = 0.7

    return EnrichmentResult(
        type="bbox_context",
        summary=summary,
        data={
            "bbox": [lng_min, lat_min, lng_max, lat_max],
            "surface_km2": round(surface_km2, 3),
            "center": [center_lng, center_lat],
            "city": city,
        },
        confidence=confidence,
    )
