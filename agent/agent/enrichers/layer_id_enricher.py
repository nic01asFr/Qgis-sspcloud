"""
layer_id_enricher — detecte les identifiants de couches connues (BD TOPO,
RGE ALTI, OCS GE, cadastre, orthophoto, parcelles) dans le message user et
injecte le CRS natif + le hint reprojection pour la brique G3.

Objectif : eviter que l'agent commence par un execute_python devinant le
CRS et fasse un smart_load sans reprojection. Le CRS natif etant fixe cote
IGN, on peut le declarer statiquement.

Fail-soft : aucun match -> None. Pas d'appel reseau.
"""

from __future__ import annotations

import re

from agent.enrichers.base import EnrichmentResult

# Registre statique des couches connues. Les identifiants doivent correspondre
# aux catalog_ids exposes par smart_load / add_from_catalog cote MCP QGIS.
# CRS natif = celui du service source IGN.
# hint_g3 = rappel de la brique G3 (reprojection systematique vers CRS projet).
_LAYERS: dict[str, dict[str, str]] = {
    "bdtopo_batiments": {
        "crs_natif": "EPSG:2154",
        "description": "IGN BD TOPO batiments (Lambert 93)",
        "hint_g3": (
            "Reprojeter en EPSG:4326 avant rendu web ; garder 2154 pour "
            "calculs metriques (surface, distance)."
        ),
    },
    "bdtopo_voies": {
        "crs_natif": "EPSG:2154",
        "description": "IGN BD TOPO reseau routier",
        "hint_g3": (
            "Lignes en Lambert 93 ; conserver 2154 pour buffer/longueur, "
            "reprojeter en 4326 pour publication web."
        ),
    },
    "bdtopo_hydro": {
        "crs_natif": "EPSG:2154",
        "description": "IGN BD TOPO hydrographie",
        "hint_g3": (
            "EPSG:2154 recommande pour analyses de bassin versant, "
            "reprojection vers 4326 pour affichage MapLibre."
        ),
    },
    "rge_alti_5m": {
        "crs_natif": "EPSG:2154",
        "description": "IGN RGE ALTI 5m (raster MNT)",
        "hint_g3": (
            "Raster en Lambert 93, resolution 5m. Reechantillonner avant "
            "reprojection si passage en 4326."
        ),
    },
    "ocs_ge": {
        "crs_natif": "EPSG:2154",
        "description": "IGN OCS GE occupation du sol",
        "hint_g3": (
            "Polygones OCS classes CS/US. Conserver le CRS natif pour "
            "calcul de surface, reprojeter en fin de chaine."
        ),
    },
    "cadastre": {
        "crs_natif": "EPSG:2154",
        "description": "Parcelles cadastrales DGFiP",
        "hint_g3": (
            "Parcelles Lambert 93. Ne pas confondre le CRS de la couche "
            "avec les coordonnees geodesiques du fichier PCI."
        ),
    },
    "orthophoto": {
        "crs_natif": "EPSG:2154",
        "description": "IGN BD Ortho image aerienne",
        "hint_g3": (
            "Raster IGN en Lambert 93. WMS/WMTS Geoplateforme sert aussi "
            "en 3857 pour web ; verifier le tile matrix."
        ),
    },
    "parcelles": {
        "crs_natif": "EPSG:2154",
        "description": "Parcelles (alias cadastre)",
        "hint_g3": (
            "Identique a cadastre : Lambert 93 natif, reprojection ciblee."
        ),
    },
}

# Regex de detection : uniquement les identifiants exacts listes ci-dessus.
# On accepte bdtopo_* et rge_alti_* en pattern generique pour attraper des
# variantes futures (bdtopo_transport, rge_alti_1m...) puis on filtre.
_LAYER_RE = re.compile(
    r"\b(bdtopo_[a-z_]+|rge_alti_[a-z0-9_]+|ocs_ge|cadastre|orthophoto|parcelles)\b",
    flags=re.IGNORECASE,
)


async def enrich(user_message: str, state: dict) -> EnrichmentResult | None:
    """Detecte les layer_ids connus dans le message, injecte CRS + hint G3."""
    if not user_message:
        return None

    matches = _LAYER_RE.findall(user_message)
    if not matches:
        return None

    # Dedup en preservant l'ordre d'apparition, en normalisant en minuscule.
    seen: list[str] = []
    for raw in matches:
        norm = raw.lower()
        if norm in _LAYERS and norm not in seen:
            seen.append(norm)

    if not seen:
        return None

    lines: list[str] = []
    for lid in seen:
        entry = _LAYERS[lid]
        lines.append(
            f"- {lid} (CRS natif {entry['crs_natif']}, {entry['description']}) "
            f"— {entry['hint_g3']}"
        )

    summary = "Couches identifiees :\n" + "\n".join(lines)

    return EnrichmentResult(
        type="layer_id_context",
        summary=summary,
        data={
            "layer_ids": seen,
            "details": {lid: _LAYERS[lid] for lid in seen},
        },
        confidence=1.0,
    )
