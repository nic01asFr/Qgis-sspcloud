"""
hub.zone_resolver — Resolution de la zone d'etude d'un composant interactive_map.

Sprint 1 V1.13 P0c (D-QGIS-011 binding complet carto).

Le ComponentParam `zone` peut etre :
- `kind="manual"` : center_lat/center_lng/zoom saisis (resolution triviale)
- `kind="commune"` : code INSEE + buffer_km optionnel
                     -> resolu via API geo.api.gouv.fr
- `kind="study"` : heritage de la zone d'etude active
                   -> resolu via studies.get_study(sid).zone

Ce module isole la logique de resolution geographique pour la rendre :
1. Testable independamment du helper monolithique _build_interactive_map_ctx
2. Reutilisable par d'autres helpers (scene_3d, atlas_map V2.1)
3. Refactorable en lib `cerema-storymap-models` (Phase 1 lib Sprint 2)

KB importante : `project_bug_marseille_geocoding` (Marseille/Paris/Lyon SEUL
sans numero arrondissement -> Marseillette/etc.). Le resolveur PRIORISE
l'INSEE strict avant tout matching fuzzy par nom.
"""
from __future__ import annotations

import logging
from typing import Any, Awaitable, Callable

log = logging.getLogger(__name__)


# Defaults Marseille 4e arr. (test territoire CEREMA E2E)
DEFAULT_CENTER_LNG = 5.39
DEFAULT_CENTER_LAT = 43.30
DEFAULT_ZOOM = 13


def _zoom_from_bbox(bbox: list[float] | tuple[float, ...]) -> float:
    """Estimation grossiere du zoom MapLibre depuis la bbox.

    Heuristique : largeur en degres -> zoom approximatif Web Mercator.
    Plus precis qu'une valeur fixe quand on connait la bbox d'une commune.

    bbox = [west, south, east, north] EPSG:4326.
    """
    try:
        if not bbox or len(bbox) < 4:
            return DEFAULT_ZOOM
        west, south, east, north = float(bbox[0]), float(bbox[1]), float(bbox[2]), float(bbox[3])
        width_deg = max(abs(east - west), abs(north - south))
        if width_deg <= 0:
            return DEFAULT_ZOOM
        # Heuristique : zoom = log2(360 / largeur). 360deg = zoom 0,
        # 0.01deg ≈ zoom 15.
        import math
        zoom = math.log2(360.0 / width_deg)
        # Borne pratique : 10 (region) - 18 (rue)
        return float(max(10, min(18, zoom)))
    except Exception:
        return DEFAULT_ZOOM


async def resolve_commune_zone(
    insee: str,
    buffer_km: float | None = None,
    *,
    http_get: Callable[[str], Awaitable[dict[str, Any] | None]] | None = None,
) -> dict[str, float] | None:
    """Resout un code INSEE vers {center_lat, center_lng, zoom, bbox}.

    Appelle geo.api.gouv.fr `/communes/{insee}?fields=centre,contour&format=json&geometry=centre`
    pour avoir le centre + le contour de la commune.

    KB project_bug_marseille_geocoding : on utilise EXCLUSIVEMENT le code
    INSEE (pas le nom) pour eviter le piege Marseille->Marseillette.

    Args:
        insee : code INSEE 5 chiffres (peut etre un arrondissement Paris/
                Marseille/Lyon : 75101-20, 13201-16, 69381-89).
        buffer_km : buffer optionnel en km autour de la commune (etend bbox).
        http_get : injectable pour tests (default : urllib.request).

    Returns:
        {center_lat, center_lng, zoom, bbox: [w,s,e,n]} ou None si echec.
    """
    if not insee:
        return None
    insee = str(insee).strip()
    if not insee.isdigit() or len(insee) not in (4, 5):
        # 4 chiffres = INSEE qui commence par 0 (corse 2A/2B exclus mais
        # 5 chiffres reste norme). On accepte 4-5 pour resilience.
        log.warning("zone_resolver INSEE format invalide : %s", insee)
        return None

    url = (
        f"https://geo.api.gouv.fr/communes/{insee}"
        f"?fields=code,nom,centre,contour&format=json&geometry=centre"
    )

    if http_get is None:
        # Default impl avec urllib + timeout. Pas de dep aiohttp/httpx
        # pour rester light (helper hub).
        async def _default_get(u: str) -> dict[str, Any] | None:
            import asyncio
            import json
            import urllib.request

            def _fetch() -> dict[str, Any] | None:
                try:
                    req = urllib.request.Request(u, headers={"User-Agent": "qgis-sspcloud/1.13"})
                    with urllib.request.urlopen(req, timeout=4.0) as resp:
                        return json.loads(resp.read().decode("utf-8"))
                except Exception as exc:
                    log.warning("zone_resolver fetch %s : %s", u, exc)
                    return None

            return await asyncio.to_thread(_fetch)

        http_get = _default_get

    data = await http_get(url)
    if not data:
        return None

    # geo.api.gouv.fr retourne :
    # { code, nom, centre: { type: "Point", coordinates: [lng, lat] },
    #   contour: { type: "Polygon", coordinates: [[[lng, lat], ...]] } }
    try:
        centre = (data.get("centre") or {}).get("coordinates") or []
        if len(centre) < 2:
            return None
        center_lng, center_lat = float(centre[0]), float(centre[1])

        # bbox depuis le contour (si dispo)
        bbox = None
        contour = (data.get("contour") or {}).get("coordinates")
        if contour and isinstance(contour, list):
            try:
                # Polygon : contour[0] = ring exterieur = liste [lng, lat]
                ring = contour[0] if contour and isinstance(contour[0], list) else []
                if ring:
                    lngs = [float(p[0]) for p in ring if len(p) >= 2]
                    lats = [float(p[1]) for p in ring if len(p) >= 2]
                    if lngs and lats:
                        bbox = [min(lngs), min(lats), max(lngs), max(lats)]
            except Exception:
                bbox = None

        # Buffer km optionnel : etend la bbox (et le zoom diminue)
        if bbox and buffer_km and buffer_km > 0:
            # 1 deg latitude ~ 111 km ; 1 deg longitude ~ 111 * cos(lat) km
            import math
            dlat = buffer_km / 111.0
            dlng = buffer_km / (111.0 * max(0.01, math.cos(math.radians(center_lat))))
            bbox = [bbox[0] - dlng, bbox[1] - dlat, bbox[2] + dlng, bbox[3] + dlat]

        zoom = _zoom_from_bbox(bbox) if bbox else DEFAULT_ZOOM

        return {
            "center_lat": center_lat,
            "center_lng": center_lng,
            "zoom": zoom,
            "bbox": bbox,
        }
    except Exception as exc:
        log.warning("zone_resolver parse %s : %s", insee, exc)
        return None


async def resolve_study_zone(
    sid: str,
    *,
    studies_module: Any = None,
) -> dict[str, float] | None:
    """Resout la zone d'etude active vers {center_lat, center_lng, zoom, bbox}.

    Lookup `studies.get_study(sid).zone` (si l'etude porte une zone explicite
    issue de set_study_zone agent IA).

    Args:
        sid : study id 12 hex
        studies_module : injectable pour tests (default : hub.studies)

    Returns:
        Zone resolue ou None si l'etude n'a pas de zone definie.
    """
    if not sid:
        return None
    if studies_module is None:
        from hub import studies as studies_module

    try:
        study = await studies_module.get_study(sid)
    except Exception as exc:
        log.warning("zone_resolver get_study(%s) : %s", sid, exc)
        return None

    if not study:
        return None

    # Pattern conventionnel : study.zone = {insee, bbox, center_lat, center_lng, zoom}
    # (set_study_zone agent IA ecrit dans ce format).
    zone = (
        study.get("zone")
        if isinstance(study, dict)
        else getattr(study, "zone", None)
    )
    if not zone:
        return None

    # Cas 1 : zone deja resolue (set_study_zone agent IA -> bbox + center stockes)
    if isinstance(zone, dict) and zone.get("center_lat") is not None:
        return {
            "center_lat": float(zone["center_lat"]),
            "center_lng": float(zone.get("center_lng", DEFAULT_CENTER_LNG)),
            "zoom": float(zone.get("zoom", DEFAULT_ZOOM)),
            "bbox": zone.get("bbox"),
        }

    # Cas 2 : zone porte juste un INSEE -> resolu en cascade
    if isinstance(zone, dict) and zone.get("insee"):
        return await resolve_commune_zone(zone["insee"], buffer_km=zone.get("buffer_km"))

    return None


async def resolve_zone(
    zone_param: dict[str, Any] | None,
    sid: str | None,
    *,
    fallback_lng: float = DEFAULT_CENTER_LNG,
    fallback_lat: float = DEFAULT_CENTER_LAT,
    fallback_zoom: float = DEFAULT_ZOOM,
    studies_module: Any = None,
    http_get: Callable[[str], Awaitable[dict[str, Any] | None]] | None = None,
) -> dict[str, float | list[float] | None]:
    """Dispatcher principal : resout n'importe quel ZoneConfig vers center/zoom/bbox.

    Use case typique :
        zone_resolved = await resolve_zone(params.get("zone"), sid)
        ctx["center_lng"] = zone_resolved["center_lng"]
        ctx["center_lat"] = zone_resolved["center_lat"]
        ctx["zoom"] = zone_resolved["zoom"]

    Backward-compat : si zone_param est None ou vide, retourne les fallbacks.
    Aucune exception levee (logs warnings cote helpers).
    """
    result: dict[str, Any] = {
        "center_lng": fallback_lng,
        "center_lat": fallback_lat,
        "zoom": fallback_zoom,
        "bbox": None,
    }
    if not isinstance(zone_param, dict):
        return result

    kind = zone_param.get("kind") or "manual"

    if kind == "manual":
        if zone_param.get("center_lng") is not None:
            result["center_lng"] = float(zone_param["center_lng"])
        if zone_param.get("center_lat") is not None:
            result["center_lat"] = float(zone_param["center_lat"])
        if zone_param.get("zoom") is not None:
            result["zoom"] = float(zone_param["zoom"])
        if zone_param.get("bbox"):
            result["bbox"] = list(zone_param["bbox"])
        return result

    if kind == "commune":
        insee = zone_param.get("insee")
        if insee:
            resolved = await resolve_commune_zone(
                str(insee),
                buffer_km=zone_param.get("buffer_km"),
                http_get=http_get,
            )
            if resolved:
                result.update(resolved)
        return result

    if kind == "study":
        resolved = await resolve_study_zone(sid or "", studies_module=studies_module)
        if resolved:
            result.update(resolved)
        return result

    return result
