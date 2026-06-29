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
    source: ComponentSource
    params: dict[str, Any] = Field(
        default_factory=dict,
        description="Params kind-specific (ex: bbox, basemap, field, stops…)",
    )
    rendering: ComponentRendering
    provenance: ComponentProvenance = Field(default_factory=ComponentProvenance)
