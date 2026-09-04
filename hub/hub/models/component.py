"""
hub.models.component — Pydantic V0.1 strate COMPOSANTS.

Unité UI paramétrable consommant un pivot DATA (Scene Manifest V0.2,
.grist, GeoJSON, etc.) et destinée à être référencée par un `Assembly`.

Catalogue MVP (Sprint Composants Phase 2) :
- interactive_map  (MapLibre GL 2D + pitch 3D)
- scene_3d         (MapLibre + Three.js custom layer — pattern atlas_bati)
- chart            (Chart.js depuis CSV / Grist table)
- kpi_badge        (HTML statique depuis valeur inline / Grist record)
- legend           (HTML SVG depuis Scene Manifest subset)
- narrative_text   (Markdown via Marked.js)
- data_table       (datatables.net depuis CSV / Grist)
- media_embed      (HTML5 URL image/vidéo/PDF)
- iframe_grist     (iframe vers URL Grist doc)

Capitalisé dans la KB :
- ~/.wikichat/knowledge/qgis-sspcloud-composants-axis.md §3
- ~/.wikichat/knowledge/maplibre-threejs-pattern-axis.md §1 (pattern scene_3d)

Note importante : `audit_chain` n'est PAS un kind de composant — c'est un
attribut transverse obligatoire sur les `Assembly` publiés (cf.
`hub.models.audit_chain`).
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field

from hub.models.audit_chain import ComponentProvenance
from hub.models.classification import Classification


ComponentKind = Literal[
    "interactive_map",
    "scene_3d",
    "chart",
    "kpi_badge",
    "legend",
    "narrative_text",
    "data_table",
    "media_embed",
    "iframe_grist",
    # Vague E2 (D-QGIS-009 §3, 2026-06-29) — kinds atomiques composables Esri-canon
    "kpi_grid",          # Bandeau N chiffres clés (vs kpi_badge isolé répété)
    "heading",           # Titre H1-H4 standalone (vs concat dans narrative_text)
    "quote",             # Citation / pull-quote (sources expertes, témoignages)
    "separator",         # Séparateur horizontal entre blocks
    # V1.20.5 (2026-07-08) — composants controllers pilotant des cartes via
    # binding declaratif (pattern app.emit/on Widgets Grist skills/inter-widget.md,
    # adapte au niveau Custom Element via CustomEvent 'geo:bind').
    # Lib : cerema-geo-components v0.1.0-alpha (Passerelle/sdk/js/geo-components).
    "timeline",          # Slider temporel play/pause emet geo:bind {prop:'time'}
]


class ComponentSource(BaseModel):
    """Pointe vers les données consommées par le composant.

    `scope` discriminant :
    - `project`  : projet QGIS d'une étude qgis-sspcloud (sid + pid)
    - `study`    : étude entière (agrégation cross-projets)
    - `external` : URL absolue (CSV S3, GeoJSON S3, image, etc.)
    - `geomind`  : primitive `describe(point)` Strate / Geomind
                   (4e producteur DATA — cf. solution-geomind-axis)
    """
    scope: Literal["project", "study", "external", "geomind"]

    livraison: Literal["auto", "inline", "url", "tuiles", "vivant"] = Field(
        "auto",
        description=(
            "Comment les données doivent parvenir au client. `scope` dit d'où "
            "elles viennent, `livraison` dit sous quelle forme. "
            "`inline` : dans le document — autoportant, figé, marche hors "
            "ligne. `url` : un fichier publié à côté — livrable léger, donnée "
            "remplaçable sans le régénérer. `tuiles` : idem en PMTiles, pour "
            "le volume. `vivant` : aucune copie, la source est lue à "
            "l'affichage (WMS, XYZ, WFS, table Grist). `auto` : le hub sert "
            "la meilleure forme disponible — des tuiles dès que la couche "
            "peut être encodée, quelle que soit sa taille, et l'inline en "
            "dernier recours. Défaut. Un consommateur qui exige une forme "
            "précise la déclare : `auto` ne garantit pas l'inline, même pour "
            "une petite couche."
        ),
    )

    # scope=project|study
    sid: str | None = Field(
        None, description="Étude.id (12 hex)", pattern=r"^[0-9a-f]{12}$"
    )
    pid: str | None = Field(
        None, description="Projet.id (12 hex)", pattern=r"^[0-9a-f]{12}$"
    )
    scene_hash: str | None = Field(
        None, description="Snapshot Scene Manifest référencé"
    )
    scene_manifest_url: str | None = Field(
        None, description="ex. /studies/{sid}/projects/{pid}/scene_manifest"
    )

    # scope=external (et fallback project/study)
    data_url: str | None = Field(
        None,
        description=(
            "URL des données. Peut être absolue (S3) ou relative au hub "
            "(/files/studies/{sid}/projects/{pid}/exports/aleas.geojson)."
        ),
    )

    # scope=geomind
    geomind_query: dict[str, Any] | None = Field(
        None,
        description=(
            "Params pour describe(point). Format : "
            "{point:[lon,lat], scope:['referentiel'|'document'|...], "
            "instance:'strate'|'cerema-amenagement'|...}"
        ),
    )


class ComponentRendering(BaseModel):
    """Indications de rendu côté browser. Le hub décide le template Jinja2."""
    runtime: Literal[
        "maplibre",        # interactive_map (cf. maplibre-threejs-pattern-axis §3)
        "maplibre_three",  # scene_3d (MapLibre + Three.js custom layer §1)
        "chartjs",         # chart
        "datatables",      # data_table
        "marked",          # narrative_text
        "html",            # kpi_badge, legend
        "iframe",          # iframe_grist, media_embed (PDF)
    ]
    container_size: str = Field(
        "responsive",
        description="ex. 'responsive', 'fixed_400x400', 'aspect-16-9'",
    )
    theme: str = Field("dsfr", description="DSFR ou variante custom")


class Component(BaseModel):
    """Composant Sprint Composants V0.1.

    Versionné via `components_index` DB (INSERT-only audit trail, pattern
    recipes_index V1.5). `content_hash` = SHA256 du manifest sérialisé
    canonique (exclut id + version_num + provenance.created_at).
    """
    id: str = Field(
        ..., description="cid (12 hex uuid4)",
        pattern=r"^[0-9a-f]{12}$",
    )
    version: int = Field(1, ge=1)
    component_version: str = Field(
        "V0.1", description="Semver du schema Component lui-même"
    )

    kind: ComponentKind
    title: str = Field(..., min_length=1, max_length=300)
    description: str | None = Field(None, max_length=2000)

    classification: Classification = "cerema_internal"
    source: ComponentSource | None = Field(
        None,
        description=(
            "Source data du composant. Obligatoire pour kinds data-driven "
            "(interactive_map, chart, data_table, scene_3d, narrative_text avec "
            "data_url notes.md). Optionnel pour kinds purement compositionnels "
            "Vague E2 (heading, quote, separator, kpi_grid, image_gallery, button) "
            "qui n'ont pas de source de données externe."
        ),
    )
    params: dict[str, Any] = Field(
        default_factory=dict,
        description="Params kind-specific (ex: bbox, basemap, field, stops…)",
    )
    rendering: ComponentRendering
    provenance: ComponentProvenance = Field(default_factory=ComponentProvenance)
