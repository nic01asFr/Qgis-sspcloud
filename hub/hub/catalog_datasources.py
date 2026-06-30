"""
hub.catalog_datasources — Catalogue des sources de donnees Strate-aligned.

Sprint 1 V1.13 P0d (D-QGIS-011 binding complet carto).

Avant : catalogue hardcoded dans `_build_interactive_map_ctx` (main.py:4742)
en variable locale, inaccessible cote frontend. Le form Marie ne pouvait
qu'accepter un TextField libre `source: str` -> drift garanti des citations.

Apres :
- Module dedie + exposition via endpoint `GET /catalog/datasources`
- Form Marie utilise un SelectField/Autocomplete avec les cles canoniques
- Helper hub `_build_interactive_map_ctx` continue d'utiliser ce module
  (1 source de verite, plus de drift).
- Extension future : ajout de sources Geomind / Strate / data.gouv via
  PR sur ce fichier (au lieu de toucher main.py).

KB :
- solution-geomind-axis : alignement source citation Strate (corpus + millesime)
- reference_data_gouv_cerema_catalog : datasets prioritaires data.gouv CEREMA
"""
from __future__ import annotations

from typing import TypedDict


class Datasource(TypedDict):
    """Source de donnees dans le catalogue."""
    id: str             # cle canonique (ex: "bdtopo_batiments")
    label: str          # citation complete (corpus + millesime + autorite + licence)
    short_label: str    # forme courte pour UI (ex: "BD TOPO 2024")
    authority: str      # IGN, DGFiP, DGPR, EEA, INSEE, CEREMA...
    licence: str        # "Licence Ouverte 2.0", "ODbL", etc.
    category: str       # "referentiel", "risque", "fiscalite", "environnement", ...
    url: str | None     # URL officielle (Géoplateforme, data.gouv, etc.)


# Catalogue principal (Sprint 1 V1.13 P0d ; etend Vague E2 catalog main.py:4742)
CATALOG_DATASOURCES: list[Datasource] = [
    # ─── Referentiels IGN (BD TOPO, BD ORTHO, etc.) ─────────────────────
    {
        "id": "bdtopo_batiments",
        "label": "BD TOPO 2024 — Batiments — IGN — Licence Ouverte 2.0",
        "short_label": "BD TOPO 2024 (batiments)",
        "authority": "IGN",
        "licence": "Licence Ouverte 2.0",
        "category": "referentiel",
        "url": "https://geoservices.ign.fr/bdtopo",
    },
    {
        "id": "bdtopo_parcelles",
        "label": "BD TOPO 2024 — Parcelles — IGN — Licence Ouverte 2.0",
        "short_label": "BD TOPO 2024 (parcelles)",
        "authority": "IGN",
        "licence": "Licence Ouverte 2.0",
        "category": "referentiel",
        "url": "https://geoservices.ign.fr/bdtopo",
    },
    {
        "id": "bdtopo_adresses",
        "label": "BD TOPO 2024 — Adresses — IGN — Licence Ouverte 2.0",
        "short_label": "BD TOPO 2024 (adresses)",
        "authority": "IGN",
        "licence": "Licence Ouverte 2.0",
        "category": "referentiel",
        "url": "https://geoservices.ign.fr/bdtopo",
    },
    {
        "id": "bdortho",
        "label": "BD ORTHO 2024 — Orthophotos — IGN — Licence Ouverte 2.0",
        "short_label": "BD ORTHO 2024",
        "authority": "IGN",
        "licence": "Licence Ouverte 2.0",
        "category": "referentiel",
        "url": "https://geoservices.ign.fr/bdortho",
    },
    {
        "id": "rge_alti",
        "label": "RGE ALTI 5m — Releve altimetrique — IGN — Licence Ouverte 2.0",
        "short_label": "RGE ALTI 5m",
        "authority": "IGN",
        "licence": "Licence Ouverte 2.0",
        "category": "referentiel",
        "url": "https://geoservices.ign.fr/rgealti",
    },
    {
        "id": "admin_communes",
        "label": "Decoupage administratif — Communes — IGN ADMIN EXPRESS — Licence Ouverte 2.0",
        "short_label": "ADMIN EXPRESS",
        "authority": "IGN",
        "licence": "Licence Ouverte 2.0",
        "category": "referentiel",
        "url": "https://geoservices.ign.fr/adminexpress",
    },
    # ─── Risques (Georisques, TRI, etc.) ────────────────────────────────
    {
        "id": "georisques_api",
        "label": "Georisques API — DGPR — Licence Ouverte 2.0",
        "short_label": "Georisques (DGPR)",
        "authority": "DGPR",
        "licence": "Licence Ouverte 2.0",
        "category": "risque",
        "url": "https://www.georisques.gouv.fr/",
    },
    {
        "id": "tri_limites",
        "label": "TRI (Territoires Risque Inondation) — DGPR — Licence Ouverte 2.0",
        "short_label": "TRI inondation (DGPR)",
        "authority": "DGPR",
        "licence": "Licence Ouverte 2.0",
        "category": "risque",
        "url": "https://www.georisques.gouv.fr/risques/inondations",
    },
    {
        "id": "ppri",
        "label": "PPRi (Plan de Prevention Risque Inondation) — DGPR / DREAL — Licence Ouverte 2.0",
        "short_label": "PPRi (DREAL)",
        "authority": "DGPR",
        "licence": "Licence Ouverte 2.0",
        "category": "risque",
        "url": "https://www.georisques.gouv.fr/risques/inondations/ppri",
    },
    # ─── Fiscalite / immobilier (DVF, DGFiP) ────────────────────────────
    {
        "id": "bdtdv",
        "label": "DVF (Demandes Valeurs Foncieres) — DGFiP — Licence Ouverte 2.0",
        "short_label": "DVF (DGFiP)",
        "authority": "DGFiP",
        "licence": "Licence Ouverte 2.0",
        "category": "fiscalite",
        "url": "https://app.dvf.etalab.gouv.fr/",
    },
    # ─── Environnement / occupation sol ─────────────────────────────────
    {
        "id": "corine_land_cover",
        "label": "CORINE Land Cover 2018 — Copernicus EEA — Licence Ouverte",
        "short_label": "CORINE Land Cover 2018",
        "authority": "EEA",
        "licence": "Copernicus Open Data",
        "category": "environnement",
        "url": "https://land.copernicus.eu/pan-european/corine-land-cover",
    },
    {
        "id": "ocs_ge",
        "label": "OCS GE 2024 — Occupation du sol grande echelle — IGN — Licence Ouverte 2.0",
        "short_label": "OCS GE 2024",
        "authority": "IGN",
        "licence": "Licence Ouverte 2.0",
        "category": "environnement",
        "url": "https://geoservices.ign.fr/ocsge",
    },
    # ─── Demographie / INSEE ────────────────────────────────────────────
    {
        "id": "insee_carroyage",
        "label": "Donnees carroyees INSEE Filosofi 200m — Licence Ouverte 2.0",
        "short_label": "INSEE Filosofi 200m",
        "authority": "INSEE",
        "licence": "Licence Ouverte 2.0",
        "category": "demographie",
        "url": "https://www.insee.fr/fr/statistiques/4176290",
    },
    # ─── CEREMA ─────────────────────────────────────────────────────────
    {
        "id": "cerema_passages_pietons",
        "label": "Passages pietons detectes — CEREMA / ZEBRA — Licence Ouverte 2.0",
        "short_label": "ZEBRA passages pietons",
        "authority": "CEREMA",
        "licence": "Licence Ouverte 2.0",
        "category": "mobilite",
        "url": None,
    },
]


# Index par id pour lookup O(1)
_BY_ID: dict[str, Datasource] = {d["id"]: d for d in CATALOG_DATASOURCES}


def get_datasource(datasource_id: str) -> Datasource | None:
    """Lookup par id canonique. Retourne None si inconnu."""
    return _BY_ID.get(datasource_id)


def get_label(datasource_id: str) -> str:
    """Helper helper hub : retourne la citation complete ou '' si inconnu.

    Use case : auto-fill `source_text` dans `_build_interactive_map_ctx`
    si `params.datasource_id` defini mais `params.source` pas explicite.
    """
    ds = _BY_ID.get(datasource_id)
    return ds["label"] if ds else ""


def list_datasources(category: str | None = None) -> list[Datasource]:
    """Liste filtree par categorie (ou tout si None).

    Use case : endpoint frontend `GET /catalog/datasources?category=risque`.
    """
    if category is None:
        return list(CATALOG_DATASOURCES)
    return [d for d in CATALOG_DATASOURCES if d["category"] == category]


def list_categories() -> list[str]:
    """Liste unique des categories ('referentiel', 'risque', etc.)."""
    seen: set[str] = set()
    out: list[str] = []
    for d in CATALOG_DATASOURCES:
        if d["category"] not in seen:
            seen.add(d["category"])
            out.append(d["category"])
    return out
