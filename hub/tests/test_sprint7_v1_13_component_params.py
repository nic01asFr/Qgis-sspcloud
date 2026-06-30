"""
Tests Sprint 1 V1.13 P0a - InteractiveMapParams Pydantic strict.

Contexte : Component.params dict[str, Any] non type (component.py:150)
introduisait drift agent IA + Marie. Ce module ajoute un schema strict
InteractiveMapParams avec back-compat 100% V1.12.

3 axes de tests :
- back-compat V1.12 : tous les anciens champs plats acceptes via
  from_legacy_dict() qui les promeut en structure V1.13.
- V1.13 native : structure nestee (zone, layers_override, legend, atlas).
- Validation : bornes lat/lng/zoom, extra=forbid sur sous-modeles.
"""
from __future__ import annotations

import pytest

from hub.models.component_params import (
    AtlasConfig,
    ClassificationConfig,
    InteractiveMapParams,
    KIND_PARAMS_SCHEMA,
    LayerOverride,
    LegendOverride,
    ZoneConfig,
    parse_params,
)


class TestBackCompatV112:
    """Champs plats V1.12 promus dans la structure V1.13 par from_legacy_dict."""

    def test_empty_dict_yields_defaults(self):
        p = InteractiveMapParams.from_legacy_dict({})
        assert p.zone.kind == "manual"
        assert p.layers_override == []
        assert p.legend.mode == "auto"
        assert p.atlas.enabled is False
        assert p.basemap_id == "osm"
        assert p.height == 580

    def test_center_lat_lng_zoom_promoted_to_zone(self):
        p = InteractiveMapParams.from_legacy_dict({
            "center_lat": 43.30,
            "center_lng": 5.39,
            "zoom": 13,
        })
        assert p.zone.kind == "manual"
        assert p.zone.center_lat == 43.30
        assert p.zone.center_lng == 5.39
        assert p.zone.zoom == 13.0

    def test_lat_lng_legacy_alias_promoted(self):
        """params.lat/lng (alias V1.0) -> zone.center_lat/center_lng."""
        p = InteractiveMapParams.from_legacy_dict({"lat": 43.30, "lng": 5.39})
        assert p.zone.center_lat == 43.30
        assert p.zone.center_lng == 5.39

    def test_legend_items_format_promoted_to_legend(self):
        p = InteractiveMapParams.from_legacy_dict({
            "legend_items": [{"label": "Test", "color": "#000091"}],
            "legend_format": "chips",
        })
        assert p.legend.mode == "manual"
        assert p.legend.format == "chips"
        assert len(p.legend.items) == 1
        assert p.legend.items[0]["label"] == "Test"

    def test_classification_global_promoted_to_layers_override(self):
        """Classification globale V1.12 -> layers_override[0].classification."""
        p = InteractiveMapParams.from_legacy_dict({
            "classification": {
                "field": "risk_score",
                "method": "jenks",
                "n_classes": 5,
                "palette": "RdYlGn",
            },
        })
        assert len(p.layers_override) == 1
        assert p.layers_override[0].classification is not None
        assert p.layers_override[0].classification.field == "risk_score"
        assert p.layers_override[0].classification.palette == "RdYlGn"

    def test_v112_metadata_passthrough(self):
        p = InteractiveMapParams.from_legacy_dict({
            "title": "Carte risque inondation",
            "subtitle": "5670 batiments exposes",
            "description": "Analyse 4e arr.",
            "basemap_id": "plan-ign-v2",
            "source": "BD TOPO 2024",
            "caveat": "Marges PPRi a verifier",
            "height": 600,
        })
        assert p.title == "Carte risque inondation"
        assert p.subtitle == "5670 batiments exposes"
        assert p.basemap_id == "plan-ign-v2"
        assert p.source == "BD TOPO 2024"
        assert p.height == 600


class TestV113Native:
    """Structure V1.13 nestee acceptee directement."""

    def test_zone_commune_kind(self):
        p = InteractiveMapParams.model_validate({
            "zone": {"kind": "commune", "insee": "13204", "buffer_km": 2.0},
        })
        assert p.zone.kind == "commune"
        assert p.zone.insee == "13204"
        assert p.zone.buffer_km == 2.0

    def test_zone_study_kind(self):
        p = InteractiveMapParams.model_validate({
            "zone": {"kind": "study"},
        })
        assert p.zone.kind == "study"

    def test_layer_override_full(self):
        p = InteractiveMapParams.model_validate({
            "layers_override": [{
                "layer_id_ref": "batiments_bdtopo",
                "visible": True,
                "opacity": 0.8,
                "name_override": "Bati BDTOPO 2024",
                "z_index": 10,
                "classification": {
                    "field": "risk_score",
                    "method": "jenks",
                    "n_classes": 5,
                    "palette": "RdYlGn",
                },
                "popup_template": "<strong>{{ feature.properties.adresse }}</strong>",
                "tooltip_field": "name",
                "hover_attributes": ["adresse", "risk_score"],
            }],
        })
        lo = p.layers_override[0]
        assert lo.layer_id_ref == "batiments_bdtopo"
        assert lo.opacity == 0.8
        assert lo.name_override == "Bati BDTOPO 2024"
        assert lo.classification.field == "risk_score"
        assert "{{ feature.properties.adresse }}" in lo.popup_template
        assert lo.hover_attributes == ["adresse", "risk_score"]

    def test_proportional_circles(self):
        p = InteractiveMapParams.model_validate({
            "layers_override": [{
                "layer_id_ref": "ecoles",
                "proportional_field": "effectif",
                "proportional_min": 50,
                "proportional_max": 1200,
                "proportional_radius_min": 4,
                "proportional_radius_max": 30,
            }],
        })
        lo = p.layers_override[0]
        assert lo.proportional_field == "effectif"
        assert lo.proportional_radius_max == 30.0

    def test_legend_manual_mode(self):
        p = InteractiveMapParams.model_validate({
            "legend": {
                "mode": "manual",
                "format": "gradient_bar",
                "items": [
                    {"label": "Faible", "color": "#1a9850"},
                    {"label": "Eleve", "color": "#a50026"},
                ],
                "position": "right",
            },
        })
        assert p.legend.mode == "manual"
        assert p.legend.format == "gradient_bar"
        assert p.legend.position == "right"
        assert len(p.legend.items) == 2

    def test_atlas_config(self):
        p = InteractiveMapParams.model_validate({
            "atlas": {
                "enabled": True,
                "source_layer_id": "passages_pietons",
                "feature_field": "pp_id",
                "max_pages": 20,
                "layout": "grid_2x2",
                "sub_zoom": 17,
                "sub_height": 300,
            },
        })
        assert p.atlas.enabled is True
        assert p.atlas.source_layer_id == "passages_pietons"
        assert p.atlas.layout == "grid_2x2"
        assert p.atlas.sub_zoom == 17.0


class TestValidation:
    """Bornes Pydantic + extra=forbid sous-modeles."""

    def test_center_lat_out_of_bounds_rejected(self):
        from pydantic import ValidationError
        with pytest.raises(ValidationError):
            InteractiveMapParams.model_validate({
                "zone": {"kind": "manual", "center_lat": 999},
            })

    def test_center_lng_out_of_bounds_rejected(self):
        from pydantic import ValidationError
        with pytest.raises(ValidationError):
            InteractiveMapParams.model_validate({
                "zone": {"kind": "manual", "center_lng": 999},
            })

    def test_zoom_out_of_bounds_rejected(self):
        from pydantic import ValidationError
        with pytest.raises(ValidationError):
            InteractiveMapParams.model_validate({
                "zone": {"kind": "manual", "zoom": 99},
            })

    def test_classification_extra_field_rejected(self):
        from pydantic import ValidationError
        with pytest.raises(ValidationError):
            ClassificationConfig(
                field="x", method="jenks", n_classes=5,
                palette="Blues", unknown="bug",
            )

    def test_layer_override_extra_field_rejected(self):
        from pydantic import ValidationError
        with pytest.raises(ValidationError):
            LayerOverride(layer_id_ref="x", unknown_field="bug")

    def test_palette_literal_rejected_if_unknown(self):
        from pydantic import ValidationError
        with pytest.raises(ValidationError):
            ClassificationConfig(field="x", palette="UnknownPalette")

    def test_basemap_literal_rejected_if_unknown(self):
        from pydantic import ValidationError
        with pytest.raises(ValidationError):
            InteractiveMapParams.model_validate({"basemap_id": "google-maps"})

    def test_opacity_must_be_between_0_and_1(self):
        from pydantic import ValidationError
        with pytest.raises(ValidationError):
            LayerOverride(layer_id_ref="x", opacity=2.0)


class TestRoundtrip:
    """to_legacy_dict produit un dict consommable par le helper hub legacy."""

    def test_legacy_roundtrip_preserves_center(self):
        p1 = InteractiveMapParams.from_legacy_dict({
            "center_lat": 43.30,
            "center_lng": 5.39,
            "zoom": 13,
        })
        d = p1.to_legacy_dict()
        # Champs plats V1.12 preserves pour le helper hub
        assert d.get("center_lat") == 43.30
        assert d.get("center_lng") == 5.39
        assert d.get("zoom") == 13.0
        # Et la structure V1.13 est presente aussi
        assert d.get("zone", {}).get("kind") == "manual"

    def test_legacy_roundtrip_preserves_legend(self):
        p1 = InteractiveMapParams.from_legacy_dict({
            "legend_items": [{"label": "Eleve", "color": "#a50026"}],
            "legend_format": "chips",
        })
        d = p1.to_legacy_dict()
        assert d.get("legend_format") == "chips"
        assert len(d.get("legend_items", [])) == 1


class TestDispatcher:
    """parse_params dispatch par kind, fallback dict si kind inconnu."""

    def test_parse_params_interactive_map(self):
        p = parse_params("interactive_map", {"title": "Carte"})
        assert isinstance(p, InteractiveMapParams)
        assert p.title == "Carte"

    def test_parse_params_unknown_kind_returns_none(self):
        # Aucun schema strict pour kpi_grid en V1.13 P0a
        p = parse_params("kpi_grid", {"title": "Bandeau"})
        assert p is None

    def test_kind_params_schema_registry_has_interactive_map(self):
        assert "interactive_map" in KIND_PARAMS_SCHEMA
        assert KIND_PARAMS_SCHEMA["interactive_map"] is InteractiveMapParams


class TestJsonSchemaExport:
    """Le schema Pydantic est exportable en JSON Schema (Phase 3 lib).

    Sprint 2/3 : gen_schema.py utilisera ces JSON Schema pour generer
    les types TS de blocknote-editor/src/forms.
    """

    def test_interactive_map_params_has_json_schema(self):
        schema = InteractiveMapParams.model_json_schema()
        assert schema["type"] == "object"
        assert "title" in schema.get("properties", {})
        assert "zone" in schema.get("properties", {})
        assert "layers_override" in schema.get("properties", {})

    def test_classification_config_has_json_schema(self):
        schema = ClassificationConfig.model_json_schema()
        assert schema["type"] == "object"
        # extra=forbid -> additionalProperties=False
        assert schema.get("additionalProperties") is False
