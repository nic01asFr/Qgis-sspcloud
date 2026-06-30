"""
hub.models.component_params — schemas Pydantic stricts par ComponentKind.

Sprint 1 V1.13 P0a (D-QGIS-011, 2026-06-30) — Binding complet composant carte.

Contexte : `Component.params: dict[str, Any]` (component.py:150) n'a aucune
contrainte de schema. Marie et l'agent IA peuvent forger n'importe quoi :
drift agent, drift Scene Manifest V0.2, perte de customisation au rebuild
du scene_manifest. ~25 cles implicites dans `_build_interactive_map_ctx`
(main.py:4588) sans validation.

Ce module definit les sous-modeles Pydantic STRICTS par kind, avec :
1. Validation : Pydantic refuse les params hors-schema (extra="forbid"
   sauf champs legacy explicitement listes pour back-compat).
2. Defaults canoniques : un seul endroit pour les valeurs par defaut
   (vs duplication dans Form TS + helper hub + agent tool).
3. Backward-compat V1.12 : tous les champs sont optionnels, parsing
   `from_legacy_dict(params)` accepte les anciennes cles plates (title,
   center_lng, etc.) et les promeut dans la structure V1.13.
4. Source de verite TS : JSON Schema generable via `model_json_schema()`
   pour generer les types TS du form Marie (Phase 3 lib).

Convention :
- Champs V1.12 deja livres = optionnels (back-compat 100%).
- Champs V1.13 nouveaux (layers_override, zone, popup_template, atlas) =
  optionnels, vides par defaut.
- Les helpers hub continuent de lire `params.get(...)` jusqu'a migration
  progressive vers `InteractiveMapParams.parse_obj(params)`.

Capitalise dans la KB :
- qgis-sspcloud-composants-axis (decision V1.13 binding cross-projet)
- maplibre-threejs-pattern-axis (mapping Scene Manifest -> MapLibre)
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


# ============================================================================
# Catalogue de valeurs Literal partagees
# ============================================================================

BasemapId = Literal[
    "osm",
    "plan-ign-v2",
    "ortho-ign",
    "dsfr-sobre",
    "hillshade-ign",
    "etalab",
]
"""6 fonds de carte du catalogue hub.carto_basemaps."""


PaletteName = Literal["Blues", "Reds", "Greens", "RdBu", "RdYlGn", "OrRd"]
"""6 palettes ColorBrewer du catalogue hub.carto_classification."""


ClassificationMethod = Literal[
    "jenks",
    "quantile",
    "equal_interval",
    "manual",
    "categorized",
]
"""Methodes de classification supportees par compute_classification."""


LegendFormat = Literal["chips", "gradient_bar", "proportional"]
"""3 formats de legende auto-derives (D-QGIS-009 §10)."""


ZoneKind = Literal["commune", "manual", "study"]
"""Modes de definition de la zone d'etude affichee :
- commune : picker INSEE + buffer_km (resolution via _try_resolve_major_city)
- manual : center_lat/center_lng/zoom/bbox saisis directement
- study : herite la bbox de l'etude active (lookup hub)
"""


# ============================================================================
# Sous-modeles : ClassificationConfig
# ============================================================================


class ClassificationConfig(BaseModel):
    """Symbologie thematique d'un layer (graduated ou categorized).

    Consomme par hub.carto_classification.compute_classification.
    Genere paint_expression MapLibre + breaks + colors + labels.
    """

    model_config = ConfigDict(extra="forbid")

    field: str = Field(..., description="Attribut GeoJSON a classifier")
    method: ClassificationMethod = "jenks"
    n_classes: int = Field(5, ge=2, le=9)
    palette: PaletteName = "Blues"
    breaks_manual: list[float] | None = Field(
        None,
        description="Breaks manuels (override calcul auto). Doit avoir n_classes+1 valeurs.",
    )
    labels_override: list[str] | None = Field(
        None,
        description="Labels custom par classe (override auto). Doit avoir n_classes valeurs.",
    )


# ============================================================================
# Sous-modeles : LayerOverride
# ============================================================================


class LayerOverride(BaseModel):
    """Override par layer du scene_manifest.

    Le scene_manifest QGIS reste source de verite pour `geojson_path` +
    `geometry_type` + `n_features`. L'override Marie ne modifie QUE les
    aspects rendu (visibilite, opacite, symbologie, interactions).

    Identification : `layer_id_ref` doit matcher scene_layer.id du
    scene_manifest V0.2.
    """

    model_config = ConfigDict(extra="forbid")

    layer_id_ref: str = Field(
        ..., description="Reference scene_manifest.layers[].id (must match)"
    )
    visible: bool = True
    opacity: float = Field(1.0, ge=0.0, le=1.0)
    name_override: str | None = Field(
        None, description="Override scene_layer.name pour la legende"
    )
    z_index: int | None = Field(
        None, description="Ordre d'affichage (plus eleve = au-dessus)"
    )

    # Symbology
    classification: ClassificationConfig | None = None
    proportional_field: str | None = Field(
        None, description="Attribut numerique pour cercles proportionnels"
    )
    proportional_min: float | None = None
    proportional_max: float | None = None
    proportional_radius_min: float = Field(4.0, ge=1.0, le=50.0)
    proportional_radius_max: float = Field(30.0, ge=1.0, le=100.0)
    heatmap_field: str | None = Field(
        None, description="Attribut pour layer heatmap (alternative classification)"
    )

    # Interactions
    hover_attributes: list[str] | None = Field(
        None,
        description="Liste blanche d'attributs a afficher en tooltip au survol",
    )
    popup_template: str | None = Field(
        None,
        description=(
            "Template HTML popup au clic. Placeholders type Mustache : "
            "{{ feature.properties.adresse }}. Escape par defaut."
        ),
        max_length=4000,
    )
    tooltip_field: str | None = Field(
        None, description="Attribut unique a afficher en tooltip rapide"
    )


# ============================================================================
# Sous-modeles : ZoneConfig
# ============================================================================


class ZoneConfig(BaseModel):
    """Zone d'etude affichee sur la carte.

    3 modes :
    - commune : Marie choisit une commune INSEE + buffer_km.
      Le helper hub appelle _try_resolve_major_city (KB project_bug_marseille_geocoding)
      pour resoudre INSEE -> bbox + center + zoom.
    - manual : Marie saisit center/zoom/bbox directement.
    - study : Heritage automatique de la zone d'etude active
      (lookup studies.get_study(sid).zone).

    Defaults : Marseille 4e arr. (test territoire CEREMA E2E).
    """

    model_config = ConfigDict(extra="forbid")

    kind: ZoneKind = "manual"

    # kind="commune"
    insee: str | None = Field(
        None, description="Code INSEE commune (5 chiffres, ex: 13204)"
    )
    buffer_km: float | None = Field(
        None, ge=0, le=50, description="Buffer en km autour de la commune"
    )

    # kind="manual"
    center_lat: float | None = Field(None, ge=-90, le=90)
    center_lng: float | None = Field(None, ge=-180, le=180)
    zoom: float | None = Field(None, ge=0, le=22)
    bbox: tuple[float, float, float, float] | None = Field(
        None,
        description="[west, south, east, north] EPSG:4326",
    )

    # kind="study" : pas de champs (resolution dynamique cote hub)


# ============================================================================
# Sous-modeles : LegendOverride
# ============================================================================


class LegendOverride(BaseModel):
    """Configuration de la legende affichee sur la carte.

    Mode auto (default) : helper hub derive items + format des layers
    (chips pour categorized, gradient_bar pour graduated, proportional
    pour cercles proportionnels).

    Mode manual : Marie redefinit items + format explicitement
    (use case : reordering, masquage layer techniques, labels custom).
    """

    model_config = ConfigDict(extra="forbid")

    mode: Literal["auto", "manual"] = "auto"
    format: LegendFormat | None = None
    items: list[dict[str, Any]] | None = Field(
        None,
        description=(
            "Items legende manuels. Format : "
            "[{label, color, size?, layer?, count?}]"
        ),
    )
    position: Literal["bottom", "right", "floating"] = "bottom"


# ============================================================================
# Sous-modeles : AtlasConfig (Sprint 3 V2.1, P0 ZEBRA storymap classification)
# ============================================================================


class AtlasConfig(BaseModel):
    """Mode atlas : 1 carte par feature d'un layer source.

    Use case ZEBRA : storymap classification pietons -> 1 mini-carte par
    passage piaton avec spotlight + zoom dessus + popup template specifique.

    Use case general : reporting territorial repete (1 carte par commune,
    par EPCI, par site Natura 2000, etc.).

    Sprint 3 V2.1 V2b — pour l'instant le champ existe mais le rendu
    `_interactive_map_atlas_partial.j2` n'est pas implemente (V1.13 = field
    inerte, V2.1 = activation).
    """

    model_config = ConfigDict(extra="forbid")

    enabled: bool = False
    source_layer_id: str | None = Field(
        None,
        description="layer_id_ref du scene_manifest qui contient les N features",
    )
    feature_field: str | None = Field(
        None, description="Attribut PK pour identifier les features (ex: pp_id)"
    )
    max_pages: int = Field(12, ge=1, le=100)
    layout: Literal["single", "grid_2x2", "grid_3x3"] = "single"
    sub_zoom: float | None = Field(
        None, ge=10, le=22, description="Zoom de chaque mini-carte (vs zone globale)"
    )
    sub_height: int | None = Field(
        None, ge=200, le=600, description="Hauteur de chaque mini-carte en px"
    )


# ============================================================================
# Schema principal : InteractiveMapParams V1.13
# ============================================================================


class InteractiveMapParams(BaseModel):
    """Schema strict params du ComponentKind="interactive_map".

    Le modele est CONSERVATEUR sur la back-compat :
    - Tous les champs V1.12 restent acceptes plats (title, subtitle,
      description, basemap_id, source, caveat, height, center_lat,
      center_lng, zoom).
    - Les nouveaux champs V1.13 (layers_override, zone, legend, atlas)
      sont optionnels et vides par defaut.

    Migration progressive : le helper hub `_build_interactive_map_ctx`
    continue de lire `params.get(...)`. Une fois ce module deploye, le
    helper peut migrer vers `InteractiveMapParams.parse_obj(params)` au
    fil des sprints (P0e tests vs P0a livraison ici).
    """

    # extra="allow" : on accepte les anciennes cles plates V1.12 +
    # cles techniques (scene_hash, scene_pid, datasource_id, lng, lat,
    # classification, legend_items, legend_format) pour back-compat 100%.
    model_config = ConfigDict(extra="allow")

    # ── Identite & metadonnees Marie (V1.12 OK) ─────────────────────────
    title: str | None = Field(None, max_length=300)
    subtitle: str | None = Field(None, max_length=300)
    description: str | None = Field(None, max_length=2000)
    caveat: str | None = Field(
        None,
        max_length=2000,
        description="Mise en garde / disclaimer carto (PPRi, marge erreur, etc.)",
    )

    # ── Source data (V1.12 OK + V1.13 enrichi) ──────────────────────────
    source: str | None = Field(
        None,
        max_length=500,
        description="Citation source texte libre (legacy V1.12)",
    )
    datasource_id: str | None = Field(
        None,
        description=(
            "Cle catalogue datasources hub (V1.13). "
            "Permet auto-fill source_text + lookup metadata. "
            "Cf. GET /catalog/datasources."
        ),
    )

    # ── Apparence (V1.12 OK) ────────────────────────────────────────────
    basemap_id: BasemapId = "osm"
    height: int = Field(580, ge=320, le=800)

    # ── Zone d'etude (V1.13 nouveau) ────────────────────────────────────
    zone: ZoneConfig = Field(default_factory=ZoneConfig)

    # ── Layers overrides (V1.13 nouveau) ────────────────────────────────
    layers_override: list[LayerOverride] = Field(
        default_factory=list,
        description=(
            "Overrides par scene_layer.id du scene_manifest. Le scene_manifest "
            "reste source de verite des donnees ; cet array override le rendu."
        ),
    )

    # ── Legende (V1.13 nouveau, defaut auto = back-compat V1.12) ────────
    legend: LegendOverride = Field(default_factory=LegendOverride)

    # ── Atlas (V2.1 V2b, inerte en V1.13) ───────────────────────────────
    atlas: AtlasConfig = Field(default_factory=AtlasConfig)

    # ── Champs LEGACY V1.12 plats (back-compat, deprecate progressif) ───
    center_lat: float | None = Field(None, ge=-90, le=90)
    center_lng: float | None = Field(None, ge=-180, le=180)
    zoom: float | None = Field(None, ge=0, le=22)

    @classmethod
    def from_legacy_dict(cls, params: dict[str, Any]) -> "InteractiveMapParams":
        """Parse un dict params V1.12 (back-compat 100%).

        Promeut les anciennes cles plates dans la structure V1.13 :
        - params.center_lat/lng/zoom -> zone.kind='manual' + zone.center_*
        - params.legend_items + params.legend_format -> legend.items + format
        - params.classification (global) -> layers_override[0].classification
          (si layers_override vide, sinon ignore — l'utilisateur a deja
          migre vers la structure V1.13).

        N'ecrase JAMAIS un champ V1.13 deja defini par l'utilisateur.
        """
        if not isinstance(params, dict):
            return cls()

        # Construction zone depuis champs plats V1.12
        if "zone" not in params and any(
            k in params for k in ("center_lat", "center_lng", "zoom", "lat", "lng")
        ):
            zone_dict: dict[str, Any] = {"kind": "manual"}
            if "center_lat" in params:
                zone_dict["center_lat"] = params["center_lat"]
            elif "lat" in params:
                zone_dict["center_lat"] = params["lat"]
            if "center_lng" in params:
                zone_dict["center_lng"] = params["center_lng"]
            elif "lng" in params:
                zone_dict["center_lng"] = params["lng"]
            if "zoom" in params:
                zone_dict["zoom"] = params["zoom"]
            params = {**params, "zone": zone_dict}

        # Construction legend depuis legacy
        if "legend" not in params and (
            "legend_items" in params or "legend_format" in params
        ):
            legend_dict: dict[str, Any] = {"mode": "manual"}
            if params.get("legend_format"):
                legend_dict["format"] = params["legend_format"]
            if params.get("legend_items"):
                legend_dict["items"] = params["legend_items"]
            params = {**params, "legend": legend_dict}

        # Promotion classification globale vers layers_override[0]
        # SEULEMENT si layers_override pas deja defini (sinon l'utilisateur
        # a deja migre).
        if (
            "classification" in params
            and isinstance(params["classification"], dict)
            and not params.get("layers_override")
        ):
            # Heuristique : on ne sait pas le layer_id_ref donc on cree un
            # override "" et le helper hub interprete "" comme "premier
            # layer du scene_manifest" (fallback legacy).
            classif = params["classification"]
            override = {
                "layer_id_ref": params.get("classification_layer_id", ""),
                "classification": classif,
            }
            params = {**params, "layers_override": [override]}

        return cls.model_validate(params)

    def to_legacy_dict(self) -> dict[str, Any]:
        """Serialise en dict V1.12-compatible (champs plats + V1.13 nestes).

        Le helper hub continue de lire les deux structures jusqu'a
        migration totale (Sprint 2 P1b).
        """
        d = self.model_dump(mode="json", exclude_none=True)
        # Aplatir zone.center_* en center_lat/lng/zoom pour back-compat
        # _build_interactive_map_ctx ligne 4779-4781.
        zone = d.get("zone") or {}
        if zone.get("kind") == "manual":
            if zone.get("center_lat") is not None:
                d.setdefault("center_lat", zone["center_lat"])
            if zone.get("center_lng") is not None:
                d.setdefault("center_lng", zone["center_lng"])
            if zone.get("zoom") is not None:
                d.setdefault("zoom", zone["zoom"])
        # Aplatir legend.items/format en legend_items/format
        legend = d.get("legend") or {}
        if legend.get("mode") == "manual":
            if legend.get("items"):
                d.setdefault("legend_items", legend["items"])
            if legend.get("format"):
                d.setdefault("legend_format", legend["format"])
        return d


# ============================================================================
# Registry : kind -> Pydantic schema class (futurs sprints)
# ============================================================================
#
# Pattern futur (Sprint 2 P1a JSON Schema -> TS types) :
#
#   KIND_PARAMS_SCHEMA: dict[str, type[BaseModel]] = {
#       "interactive_map": InteractiveMapParams,
#       "scene_3d": Scene3dParams,           # V2.1
#       "chart": ChartParams,                # V2.1
#       "kpi_grid": KpiGridParams,           # V1.13 si besoin
#       ...
#   }
#
# Pour V1.13 P0a on livre uniquement InteractiveMapParams (le composant
# le plus riche, qui justifie le sprint).

KIND_PARAMS_SCHEMA: dict[str, type[BaseModel]] = {
    "interactive_map": InteractiveMapParams,
}


def parse_params(kind: str, params: dict[str, Any]) -> BaseModel | None:
    """Parse params en schema strict si kind connu, sinon None.

    Usage cote helper hub :

        from hub.models.component_params import parse_params
        parsed = parse_params("interactive_map", comp_manifest["params"])
        if parsed is not None:
            # parsed = InteractiveMapParams strict, IDE autocomplete, etc.
            zone = parsed.zone
        else:
            # Fallback dict.get pour kinds pas encore migres
            zone = comp_manifest["params"].get("zone")
    """
    schema = KIND_PARAMS_SCHEMA.get(kind)
    if schema is None:
        return None
    # Pour InteractiveMapParams on passe par from_legacy_dict (back-compat)
    if schema is InteractiveMapParams:
        return InteractiveMapParams.from_legacy_dict(params)
    return schema.model_validate(params)
