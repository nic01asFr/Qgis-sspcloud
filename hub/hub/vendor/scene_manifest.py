"""Scene Manifest — Format pivot d'echange standard pour scenes geospatiales.

Specification V0.2 : https://docs.cerema.local/scene-manifest-spec.md

Scene Manifest orchestre une scene geospatiale composee :
- Metadata + source/provenance (citabilite)
- Layers avec styles (single/categorized/graduated/rule_based/3d/extrusion)
- Controls declaratifs (layer_toggle, field_filter, timeline, swipe, legend, search, geomind_lookup)
- Narration (sections storymap)
- Links vers Grist/Geomind/KB

Format : JSON + sidecar (GPKG/GeoJSON/external URLs referenced via data_url).
Pattern pivot B (donnees geospatiales, format OGC GPKG compliant).

Responsabilites C3 (ce module) :
1. Validation de la structure JSON contre spec V0.2
2. Serialization Python -> JSON (round-trip)
3. Calcul du scene_hash (SHA256 sur declarative styles normalize, sans qml_source/provenance)
4. i18n support (str ou {lang_code: str})
5. Double-track styles (qml_source optionnel + declarative obligatoire)
"""

from __future__ import annotations

import hashlib
import json
import uuid
from datetime import datetime
from typing import Any, Literal, Union

from pydantic import BaseModel, Field, field_validator, model_validator


# ============================================================================
# Style Models (double-track)
# ============================================================================

class StyleStop(BaseModel, frozen=True):
    """Un arret de gradient pour style categorized/graduated."""
    value: Union[str, int, float]
    color: str = Field(pattern=r"^#[0-9a-fA-F]{6}$", description="Couleur hexadecimale")
    opacity: float = Field(ge=0, le=1, default=1.0)


class StyleRule(BaseModel, frozen=True):
    """Regle pour style rule_based."""
    filter: str                              # expression QGIS
    color: str = Field(pattern=r"^#[0-9a-fA-F]{6}$")
    opacity: float = Field(ge=0, le=1, default=1.0)
    ordering: int = Field(default=0)


class StyleDeclarative(BaseModel, frozen=True):
    """Styles declaratifs (obligatoire) — neutre, lisibles par tous les consommateurs.

    Les champs supportent ces kinds :
    - single : couleur unique
    - categorized : couleur par valeur de champ
    - graduated : couleur par intervalle
    - rule_based : regles QGIS complexes
    - 3d_model : modele GLTF par feature
    - extrusion : polygone extrude 3D
    """
    kind: Literal["single", "categorized", "graduated", "rule_based", "3d_model", "extrusion"]

    # single
    color: str | None = Field(None, pattern=r"^#[0-9a-fA-F]{6}$")
    opacity: float = Field(ge=0, le=1, default=1.0)
    outline: dict[str, Any] | None = None

    # categorized / graduated
    field: str | None = None
    stops: list[StyleStop] = Field(default_factory=list)

    # graduated
    method: Literal["linear", "log", "quantile"] | None = None
    breaks: list[Union[str, int, float]] = Field(default_factory=list)
    ramp: str | None = None  # ex: "Viridis"

    # rule_based
    rules: list[StyleRule] = Field(default_factory=list)

    # 3d_model
    gltf_url: str | None = None
    scale_field: str | None = None
    rotation_field: str | None = None

    # extrusion
    height_field: str | None = None
    roof_color: str | None = Field(None, pattern=r"^#[0-9a-fA-F]{6}$")

    @model_validator(mode="after")
    def validate_by_kind(self) -> StyleDeclarative:
        """Validation specifique au kind."""
        if self.kind == "single" and not self.color:
            raise ValueError("single style requires color")
        if self.kind in ("categorized", "graduated") and not self.field:
            raise ValueError(f"{self.kind} style requires field")
        if self.kind == "3d_model" and not self.gltf_url:
            raise ValueError("3d_model style requires gltf_url")
        if self.kind == "extrusion" and not self.height_field:
            raise ValueError("extrusion style requires height_field")
        return self


class Style(BaseModel, frozen=True):
    """Style avec double-track : qml_source (autorite QGIS) + declarative (obligatoire)."""
    qml_source: str | None = Field(None, description="QML brut QGIS original (autorite QGIS Desktop)")
    declarative: StyleDeclarative = Field(..., description="Transcription neutre obligatoire")


# ============================================================================
# Layer Models
# ============================================================================

class LayerWidget(BaseModel, frozen=True):
    """Widget attributaire pour un champ."""
    type: Literal["choice", "bool", "numeric", "date", "text"]
    values: list[str] | None = Field(None, description="Pour choice : liste des valeurs")


class LayerRelation(BaseModel, frozen=True):
    """Relation entre layers (via GPKG gpkg_extensions)."""
    to_layer: str                           # id de la couche cible
    to_field: str                           # champ dans la couche cible
    via_field: str                          # champ dans cette couche


class I18nText(BaseModel, frozen=True):
    """Champ i18n — soit str (langue defaut), soit dict {lang: str}."""

    @classmethod
    def coerce(cls, value: Union[str, dict[str, str]]) -> str | dict[str, str]:
        """Accepte str ou dict."""
        if isinstance(value, str):
            return value
        if isinstance(value, dict) and all(isinstance(k, str) and isinstance(v, str) for k, v in value.items()):
            return value
        raise ValueError("I18nText must be str or dict[str, str]")


class LayerSource(BaseModel, frozen=True):
    """Source/citabilite d'une couche (optionnel — sinon herite du top-level source)."""
    referential: str
    millesime: str
    authority: str
    license: str


class ProvenanceSource(BaseModel, frozen=True):
    """Source de provenance fine d'un layer (V0.2, raffinement session-5e5828).

    Generalise l'ancien `filter_sources[]` avec un `kind` discriminant : tous
    les layers n'ont PAS de Filter Manifest (un download WFS direct n'a pas de
    `filter_hash`). Le `kind` trace la production reelle de chaque couche sans
    fabriquer de `filter_hash` bidon.

    Liste FERMEE de `kind` versionnee semver (cf scene-manifest-spec.md V0.2).
    EXCLU du scene_hash (audit/tracabilite, pas contenu declaratif de la scene).
    """

    kind: Literal["filter", "wfs_direct", "processing", "user_edit", "grist_doc", "composite"]

    # kind=filter (pcrs /v1/filter, zebra geo_filter, geoai-kit JS)
    filter_hash: str | None = None
    filter_manifest_url: str | None = None
    profile_id: str | None = None
    profile_sha256: str | None = None
    produced_at: datetime | None = None

    # kind=wfs_direct (smart_load BDTOPO / Geoplateforme / Georisques)
    url: str | None = None
    wfs_version: str | None = None
    millesime: str | None = None
    bbox_l93: list[float] | None = Field(None, min_length=4, max_length=4)

    # kind=processing (native:buffer, gdal:warp, ... QGIS)
    algo_id: str | None = None
    params_hash: str | None = None
    qgis_version: str | None = None

    # kind=user_edit (edition manuelle QGIS desktop / plugin)
    correlation_id: str | None = None
    started_at: datetime | None = None
    finished_at: datetime | None = None

    # kind=grist_doc (binding Atlas vers source Grist)
    grist_doc_id: str | None = None
    source_table: str | None = None
    model_id: str | None = None
    style_json_binding: str | None = None

    # kind=composite (layer derivee de plusieurs sources)
    sub_sources: list[ProvenanceSource] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_by_kind(self) -> ProvenanceSource:
        """Champ minimal obligatoire par kind (anti-trace-bidon)."""
        required: dict[str, tuple[str, object | None]] = {
            "filter": ("filter_hash", self.filter_hash),
            "wfs_direct": ("url", self.url),
            "processing": ("algo_id", self.algo_id),
            "grist_doc": ("grist_doc_id", self.grist_doc_id),
        }
        if self.kind in required:
            field_name, value = required[self.kind]
            if not value:
                raise ValueError(f"{self.kind} provenance_source requires {field_name}")
        if self.kind == "composite" and not self.sub_sources:
            raise ValueError("composite provenance_source requires sub_sources")
        return self


class Layer(BaseModel, frozen=True):
    """Definition d'une couche dans la scene."""
    id: str                                  # identificateur unique dans la scene
    name: Union[str, dict[str, str]] = Field(description="str (langue defaut) ou {lang: str}")
    order: int = Field(ge=0)
    group: str | None = Field(None, description="Libelle de groupe plat (TOC). None = racine.")
    visible: bool = True
    geomType: Literal["Point", "LineString", "Polygon", "MultiPoint", "MultiLineString", "MultiPolygon"]
    data_url: str                            # scheme : file.gpkg#layer=X, file.geojson, https://..., grist://..., kb://..., geomind://..., wfs:...
    style: Style | None = None
    relations: list[LayerRelation] = Field(default_factory=list)
    widgets: dict[str, LayerWidget] = Field(default_factory=dict)
    source: LayerSource | None = None
    provenance_sources: list[ProvenanceSource] = Field(default_factory=list)
    """Trace fine de production de la couche (V0.2). EXCLU du scene_hash."""

    @field_validator("data_url")
    @classmethod
    def validate_data_url(cls, v: str) -> str:
        """Valide les schemes supportes (liste ouverte)."""
        valid_schemes = [
            "file.gpkg#", "file.geojson", "https://", "http://",
            "grist://", "kb://", "geomind://", "wfs:"
        ]
        if not any(v.startswith(scheme) for scheme in valid_schemes):
            # Warning log, pas erreur (extensibilite)
            pass
        return v


# ============================================================================
# Control Models
# ============================================================================

class ControlOption(BaseModel, frozen=True):
    """Option dans un controle (ex: field_filter)."""
    value: str
    label: Union[str, dict[str, str]]


class Control(BaseModel, frozen=True):
    """Controle declaratif pour UI."""
    type: Literal["layer_toggle", "field_filter", "timeline", "swipe", "legend", "search", "geomind_lookup"]

    # layer_toggle
    layers: list[str] = Field(default_factory=list)

    # field_filter
    layer: str | None = None
    field: str | None = None
    options: list[ControlOption] = Field(default_factory=list)

    # timeline
    range: list[str] | None = None          # ["2020-01-01", "2026-12-31"]

    # common
    label: Union[str, dict[str, str]] | None = None

    @model_validator(mode="after")
    def validate_by_type(self) -> Control:
        """Validation specifique au type de controle."""
        if self.type == "layer_toggle" and not self.layers:
            raise ValueError("layer_toggle requires layers")
        if self.type == "field_filter" and not (self.layer and self.field):
            raise ValueError("field_filter requires layer and field")
        if self.type == "timeline" and not (self.field and self.range):
            raise ValueError("timeline requires field and range")
        return self


# ============================================================================
# Narration Models
# ============================================================================

class NarrationSection(BaseModel, frozen=True):
    """Section d'une narration storymap."""
    id: str
    title: Union[str, dict[str, str]]
    md: Union[str, dict[str, str]] = Field(description="Markdown ou i18n")
    camera: dict[str, Any] | None = None    # {lng, lat, zoom, pitch?, bearing?}
    highlight_layers: list[str] = Field(default_factory=list)
    geomind_fact_ids: list[str] = Field(default_factory=list)


class Narration(BaseModel, frozen=True):
    """Narration editoriale (optionnelle)."""
    sections: list[NarrationSection] = Field(default_factory=list)


# ============================================================================
# Source / Provenance
# ============================================================================

class Source(BaseModel, frozen=True):
    """Citabilite de la source (obligatoire)."""
    referential: str                        # ex: "gpu-urbanisme"
    millesime: str                          # ex: "2026-05"
    authority: str                          # ex: "Cerema", "DGALN"
    license: str                            # ex: "EUPL-1.2", "Licence Ouverte 2.0"
    license_url: str | None = None


class ProvenanceEntry(BaseModel, frozen=True):
    """Entree dans la chaine de provenance cross-platform."""
    platform_id: str                        # ex: "qgis-desktop-nic01asfr"
    produced_at: datetime
    via: str | None = None                  # platform amont (None = source primaire)


class Links(BaseModel, frozen=True):
    """Cross-references vers autres systemes."""
    geomind_entities: list[str] = Field(default_factory=list)
    grist_documents: list[str] = Field(default_factory=list)
    cerema_kb_pivots: list[str] = Field(default_factory=list)


# ============================================================================
# Scene Manifest (root)
# ============================================================================

class SceneManifest(BaseModel, frozen=True):
    """Scene Manifest — orchestration d'une scene geospatiale.

    Champs obligatoires :
    - format, version, scene_id, scene_hash
    - title, crs, bbox
    - source
    - layers (au moins 1)

    Champs optionnels :
    - description, provenance, basemap, sun, camera
    - controls, narration, links
    """

    # Identite
    format: Literal["scene-manifest"] = "scene-manifest"
    version: str = Field(pattern=r"^\d+\.\d+\.\d+$")  # semver
    scene_id: str = Field(description="UUID v4")
    scene_hash: str = Field(description="SHA256 de la partie declarative (sans qml_source/provenance)")

    # Metadata
    title: Union[str, dict[str, str]]
    description: Union[str, dict[str, str]] | None = None

    # Geographie
    crs: str = Field(pattern=r"^EPSG:\d+$|^urn:ogc:def:crs:.*$")  # ex: "EPSG:4326"
    bbox: list[float] = Field(min_length=4, max_length=4)  # [lon_min, lat_min, lon_max, lat_max]

    # Citabilite
    source: Source = Field(..., description="Source obligatoire pour citabilite")

    # Optionnels
    provenance: list[ProvenanceEntry] = Field(default_factory=list)

    # Tracabilite cross-services (V0.2, OpenTelemetry-like). EXCLUS du scene_hash :
    # invariant D-MAPPING-007 — le scene_hash ne porte JAMAIS d'identifiant de
    # correlation ni de tracabilite, seulement le contenu declaratif de la scene.
    correlation_id: str | None = Field(None, description="UUID racine (session/requete agent)")
    span_id: str | None = Field(None, description="UUID operation atomique (optionnel)")
    parent_span_id: str | None = Field(None, description="UUID span parent (optionnel)")

    # Vue 3D
    basemap: str | None = Field(None, description="ex: 'ign-plan', 'osm'")
    sun: dict[str, Any] | None = None       # {date, lat, lng}
    camera: dict[str, Any] | None = None    # {lng, lat, zoom, pitch?, bearing?}

    # Contenu
    layers: list[Layer] = Field(..., min_length=1)
    controls: list[Control] = Field(default_factory=list)
    narration: Narration | None = None
    links: Links = Field(default_factory=Links)

    @field_validator("scene_id")
    @classmethod
    def validate_uuid(cls, v: str) -> str:
        """Valide UUID v4."""
        try:
            uuid.UUID(v, version=4)
        except ValueError:
            raise ValueError("scene_id must be a valid UUID v4")
        return v

    @field_validator("bbox")
    @classmethod
    def validate_bbox(cls, v: list[float]) -> list[float]:
        """Valide bbox [lon_min, lat_min, lon_max, lat_max]."""
        lon_min, lat_min, lon_max, lat_max = v
        if not (-180 <= lon_min < lon_max <= 180):
            raise ValueError("Invalid bbox longitudes")
        if not (-90 <= lat_min < lat_max <= 90):
            raise ValueError("Invalid bbox latitudes")
        return v


# ============================================================================
# Serialization & Hashing
# ============================================================================

def normalize_for_hash(obj: Any) -> str:
    """Normalise un objet pour hash stable (deterministe JSON)."""
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=True, default=str)


#: Champs top-level EXCLUS du scene_hash. Invariant D-MAPPING-007 (grave par
#: qgis-agent) : le scene_hash ne porte JAMAIS d'identifiant de correlation ni
#: de tracabilite, seulement le contenu declaratif de la scene. `scene_hash`
#: (auto-reference) et `provenance` (chaine d'ingestion) restent exclus comme
#: en V0.1 ; V0.2 ajoute la triplette de correlation OpenTelemetry-like.
#: V0.2.2 (D-MAPPING-008, arbitrage qgis-agent) ajoute `version` : metadonnee
#: de format de serialisation, pas contenu declaratif -> l'identite d'une scene
#: reste stable a travers les migrations de format (V0.2.x <-> V0.3 a contenu
#: egal donnent le meme scene_hash). La compat schema reste validee par
#: from_dict, separement de l'identite.
TOP_LEVEL_POP: frozenset[str] = frozenset(
    {"scene_hash", "version", "provenance", "correlation_id", "span_id", "parent_span_id"}
)

#: Champs par-layer EXCLUS du scene_hash (audit de production de la couche,
#: pas contenu declaratif). `provenance_sources` generalise l'ancien
#: `filter_sources` (V0.2).
PER_LAYER_POP: frozenset[str] = frozenset({"provenance_sources"})


def compute_scene_hash(manifest_dict: dict[str, Any]) -> str:
    """Calcule le scene_hash (SHA256) sur le contenu declaratif normalise.

    IMPORTANT : exclut les champs de tracabilite (cf TOP_LEVEL_POP / PER_LAYER_POP)
    et reduit chaque style a sa partie declarative (qml_source ecarte), pour une
    stabilite cross-runtime (Python <-> JS) et cross-services.
    """
    # Clone et supprime les champs a exclure (top-level)
    data_for_hash = json.loads(json.dumps(manifest_dict))
    for key in TOP_LEVEL_POP:
        data_for_hash.pop(key, None)

    for layer in data_for_hash.get("layers", []):
        # Exclut la trace de production de la couche
        for key in PER_LAYER_POP:
            layer.pop(key, None)
        # Normalise les styles : garder UNIQUEMENT la partie declarative
        if "style" in layer and layer["style"]:
            style = layer["style"]
            style_declarative = style.get("declarative", {})
            layer["style"] = style_declarative

    normalized = normalize_for_hash(data_for_hash)
    return "sha256:" + hashlib.sha256(normalized.encode()).hexdigest()


def manifest_to_dict(manifest: SceneManifest) -> dict[str, Any]:
    """Serialise un SceneManifest en dict JSON-seriazable."""
    return json.loads(manifest.model_dump_json(exclude_none=True))


def dict_to_manifest(data: dict[str, Any]) -> SceneManifest:
    """Deserialise un dict JSON en SceneManifest avec validation."""
    return SceneManifest(**data)


def from_dict(data: dict[str, Any], auto_hash: bool = True) -> SceneManifest:
    """Factory : cree un SceneManifest depuis un dict JSON.

    Args:
        data: Dict JSON brut (peut manquer scene_hash)
        auto_hash: Si True et scene_hash manque/vide, calcule automatiquement

    Returns:
        SceneManifest valide

    Note (V0.2.1) : le hash auto est calcule sur la forme **canonique
    materialisee** (`manifest_to_dict`, defauts inclus), identique a ce que
    `to_json` stocke/transporte. Le scene_hash est donc independant des champs
    optionnels omis par le producteur : deux producteurs (ou Python vs JS)
    convergent sur le meme digest des lors qu'ils decrivent la meme scene.
    """
    if auto_hash and not data.get("scene_hash"):
        data = dict(data)  # clone
        provisional = dict_to_manifest({**data, "scene_hash": "sha256:pending"})
        data["scene_hash"] = compute_scene_hash(manifest_to_dict(provisional))

    return dict_to_manifest(data)


def to_json(manifest: SceneManifest, indent: int | None = 2) -> str:
    """Serialise un SceneManifest en JSON."""
    return json.dumps(manifest_to_dict(manifest), indent=indent, default=str)


def from_json(json_str: str, auto_hash: bool = False) -> SceneManifest:
    """Deserialise un SceneManifest depuis JSON."""
    data = json.loads(json_str)
    return from_dict(data, auto_hash=auto_hash)


__all__ = [
    # Styles
    "Style",
    "StyleDeclarative",
    "StyleStop",
    "StyleRule",
    # Layers
    "Layer",
    "LayerSource",
    "LayerRelation",
    "LayerWidget",
    "ProvenanceSource",
    # Controls
    "Control",
    "ControlOption",
    # Narration
    "Narration",
    "NarrationSection",
    # Source/Provenance
    "Source",
    "ProvenanceEntry",
    "Links",
    # Root
    "SceneManifest",
    # Functions
    "from_dict",
    "from_json",
    "to_json",
    "manifest_to_dict",
    "dict_to_manifest",
    "compute_scene_hash",
    "normalize_for_hash",
    # Hash exclusion sets (contrat cross-impl)
    "TOP_LEVEL_POP",
    "PER_LAYER_POP",
]
