"""
Vague E2 Commit 7 (D-QGIS-009 §7, 2026-06-29) — Catalogue fonds de carte.

Avant : OSM raster hardcoded dans le JS MapLibre.
Apres : catalogue de 6 fonds (basemap_id) avec sources + attribution
        dynamique selectionnable via params.basemap_id du component.

Use case : Marie choisit son fond de carte selon le livrable :
- 'osm' (default) : fond communautaire, contexte general
- 'plan-ign-v2' : Plan IGN v2, officiel
- 'ortho-ign' : orthophotos IGN, contexte aerien
- 'dsfr-sobre' : fond sobre gris DSFR pour livrables institutionnels
- 'hillshade-ign' : RGE ALTI hillshade, relief
- 'etalab' : Plan Etalab, datalab gouv

Pas de cle API requise pour ces fonds (acces ouvert).
"""
from __future__ import annotations

from typing import Any


# ============================================================================
# Catalogue fonds (basemap_id) -> MapLibre style sources + layers + attribution
# ============================================================================

BASEMAPS: dict[str, dict[str, Any]] = {
    "osm": {
        "name": "OpenStreetMap",
        "description": "Fond cartographique communautaire (default)",
        "attribution": "© OpenStreetMap contributors",
        "style": {
            "version": 8,
            "sources": {
                "osm": {
                    "type": "raster",
                    "tiles": ["https://tile.openstreetmap.org/{z}/{x}/{y}.png"],
                    "tileSize": 256,
                    "maxzoom": 19,
                    "attribution": "© OpenStreetMap contributors",
                },
            },
            "layers": [{"id": "osm", "type": "raster", "source": "osm"}],
        },
    },
    "plan-ign-v2": {
        "name": "Plan IGN v2",
        "description": "Plan officiel IGN v2 (Géoplateforme)",
        "attribution": "© IGN Géoplateforme — Licence Ouverte 2.0",
        "style": {
            "version": 8,
            "sources": {
                "plan-ign-v2": {
                    "type": "raster",
                    "tiles": [
                        "https://data.geopf.fr/wmts?REQUEST=GetTile&SERVICE=WMTS&VERSION=1.0.0"
                        "&STYLE=normal&TILEMATRIXSET=PM&FORMAT=image/png"
                        "&LAYER=GEOGRAPHICALGRIDSYSTEMS.PLANIGNV2"
                        "&TILEMATRIX={z}&TILEROW={y}&TILECOL={x}"
                    ],
                    "tileSize": 256,
                    "maxzoom": 19,
                    "attribution": "© IGN Géoplateforme",
                },
            },
            "layers": [{"id": "plan-ign-v2", "type": "raster", "source": "plan-ign-v2"}],
        },
    },
    "ortho-ign": {
        "name": "Orthophotos IGN",
        "description": "Photographies aériennes IGN BD ORTHO",
        "attribution": "© IGN BD ORTHO — Licence Ouverte 2.0",
        "style": {
            "version": 8,
            "sources": {
                "ortho-ign": {
                    "type": "raster",
                    "tiles": [
                        "https://data.geopf.fr/wmts?REQUEST=GetTile&SERVICE=WMTS&VERSION=1.0.0"
                        "&STYLE=normal&TILEMATRIXSET=PM&FORMAT=image/jpeg"
                        "&LAYER=ORTHOIMAGERY.ORTHOPHOTOS"
                        "&TILEMATRIX={z}&TILEROW={y}&TILECOL={x}"
                    ],
                    "tileSize": 256,
                    "maxzoom": 20,
                    "attribution": "© IGN BD ORTHO",
                },
            },
            "layers": [{"id": "ortho-ign", "type": "raster", "source": "ortho-ign"}],
        },
    },
    "dsfr-sobre": {
        "name": "DSFR Sobre",
        "description": "Fond sobre gris DSFR pour livrables institutionnels CEREMA",
        "attribution": "© OpenStreetMap contributors, style sobre CEREMA",
        # Style sobre : fond OSM en mode noir et blanc via CSS filter
        # ou tuiles Stamen Toner Lite (open source) en alternative
        "style": {
            "version": 8,
            "sources": {
                "stamen-toner-lite": {
                    "type": "raster",
                    "tiles": [
                        "https://tile.openstreetmap.org/{z}/{x}/{y}.png",
                    ],
                    "tileSize": 256,
                    "maxzoom": 19,
                    "attribution": "© OpenStreetMap (style sobre)",
                },
            },
            "layers": [
                {"id": "stamen-toner-lite", "type": "raster",
                 "source": "stamen-toner-lite",
                 "paint": {"raster-saturation": -1, "raster-contrast": 0.1}},
            ],
        },
    },
    "hillshade-ign": {
        "name": "RGE ALTI Hillshade",
        "description": "Ombrage de relief depuis RGE ALTI 5m IGN",
        "attribution": "© IGN RGE ALTI 5m — Licence Ouverte 2.0",
        "style": {
            "version": 8,
            "sources": {
                "rgealti-hillshade": {
                    "type": "raster",
                    "tiles": [
                        "https://data.geopf.fr/wmts?REQUEST=GetTile&SERVICE=WMTS&VERSION=1.0.0"
                        "&STYLE=estompage_grayscale&TILEMATRIXSET=PM&FORMAT=image/png"
                        "&LAYER=ELEVATION.ELEVATIONGRIDCOVERAGE.SHADOW"
                        "&TILEMATRIX={z}&TILEROW={y}&TILECOL={x}"
                    ],
                    "tileSize": 256,
                    "maxzoom": 17,
                    "attribution": "© IGN RGE ALTI",
                },
            },
            "layers": [{"id": "rgealti-hillshade", "type": "raster",
                       "source": "rgealti-hillshade"}],
        },
    },
    "etalab": {
        "name": "Plan Etalab",
        "description": "Plan officiel data.gouv.fr (Etalab)",
        "attribution": "© Etalab Data.gouv.fr",
        "style": {
            "version": 8,
            "sources": {
                "etalab": {
                    "type": "raster",
                    "tiles": [
                        "https://openmaptiles.geo.data.gouv.fr/styles/osm-bright/{z}/{x}/{y}.png",
                    ],
                    "tileSize": 256,
                    "maxzoom": 18,
                    "attribution": "© Etalab",
                },
            },
            "layers": [{"id": "etalab", "type": "raster", "source": "etalab"}],
        },
    },
}


def get_basemap_style(basemap_id: str) -> dict[str, Any]:
    """Retourne le MapLibre style JSON d'un basemap_id.

    Si basemap_id inconnu, fallback OSM.
    """
    if basemap_id not in BASEMAPS:
        basemap_id = "osm"
    return BASEMAPS[basemap_id]["style"]


def get_basemap_metadata(basemap_id: str) -> dict[str, str]:
    """Retourne metadata (name, description, attribution) d'un basemap."""
    if basemap_id not in BASEMAPS:
        basemap_id = "osm"
    bm = BASEMAPS[basemap_id]
    return {
        "id": basemap_id,
        "name": bm["name"],
        "description": bm["description"],
        "attribution": bm["attribution"],
    }


def list_basemaps() -> list[dict[str, str]]:
    """Catalogue light des fonds disponibles."""
    return [
        {
            "id": bid,
            "name": bm["name"],
            "description": bm["description"],
            "attribution": bm["attribution"],
        }
        for bid, bm in BASEMAPS.items()
    ]
