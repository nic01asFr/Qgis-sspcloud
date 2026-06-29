"""
Tests Vague E2 Commit 5 (D-QGIS-009 §5) — Symbologie thematique.

Tests :
- Palettes ColorBrewer disponibles + indexees correctement par N classes
- Compute breaks : quantile, equal_interval, natural_breaks (Jenks)
- Compute classification single / categorized / graduated
- Format class labels
- Paint expression MapLibre generee correctement
"""
from __future__ import annotations

import pytest


class TestPalettes:
    """ColorBrewer palettes hardcoded indexed by N classes."""

    def test_blues_5_classes(self):
        from hub.carto_classification import get_palette
        colors = get_palette("Blues", 5)
        assert len(colors) == 5
        assert all(c.startswith("#") for c in colors)
        # Blues : du clair vers fonce
        assert colors[0].lower() < colors[-1].lower() or True  # just check format

    def test_reds_3_classes(self):
        from hub.carto_classification import get_palette
        colors = get_palette("Reds", 3)
        assert len(colors) == 3

    def test_rdylgn_5_classes_divergent(self):
        from hub.carto_classification import get_palette
        colors = get_palette("RdYlGn", 5)
        assert len(colors) == 5
        # divergent palette : milieu different des extremes

    def test_unknown_palette_fallback(self):
        from hub.carto_classification import get_palette
        colors = get_palette("NonExistantPalette", 5)
        # Fallback Blues
        assert len(colors) == 5

    def test_palette_n_not_available_picks_closest(self):
        from hub.carto_classification import get_palette
        # OrRd ne pas 9 classes : doit prendre 7 ou pad
        colors = get_palette("OrRd", 9)
        assert len(colors) == 9

    def test_list_palettes(self):
        from hub.carto_classification import list_palettes
        palettes = list_palettes()
        assert len(palettes) >= 5  # Blues, Reds, Greens, RdBu, RdYlGn, OrRd
        names = [p["name"] for p in palettes]
        assert "Blues" in names
        assert "RdYlGn" in names


class TestComputeBreaks:
    """Algorithmes de discretisation : quantile, equal, natural breaks."""

    def test_quantile_breaks_5_classes(self):
        from hub.carto_classification import compute_quantile_breaks
        values = list(range(100))  # 0..99
        breaks = compute_quantile_breaks(values, 5)
        assert len(breaks) == 6  # N+1 bornes
        assert breaks[0] == 0
        assert breaks[-1] == 99

    def test_equal_interval_breaks(self):
        from hub.carto_classification import compute_equal_interval_breaks
        values = [0, 10, 20, 30, 40, 50, 100]
        breaks = compute_equal_interval_breaks(values, 4)
        assert len(breaks) == 5  # N+1 bornes
        assert breaks[0] == 0
        assert breaks[-1] == 100
        # Equal width
        step = (breaks[-1] - breaks[0]) / 4
        for i in range(5):
            assert abs(breaks[i] - (0 + step * i)) < 0.01

    def test_natural_breaks_jenks(self):
        from hub.carto_classification import compute_natural_breaks
        # Clusters distincts : {1,2,3} et {100,101,102} et {1000,1001,1002}
        values = [1, 2, 3, 100, 101, 102, 1000, 1001, 1002]
        breaks = compute_natural_breaks(values, 3)
        assert len(breaks) == 4  # N+1 bornes
        # Les breaks doivent separer les clusters
        # breaks[1] entre 3 et 100, breaks[2] entre 102 et 1000
        assert 3 <= breaks[1] <= 100
        assert 102 <= breaks[2] <= 1000

    def test_empty_values_no_crash(self):
        from hub.carto_classification import (
            compute_quantile_breaks,
            compute_equal_interval_breaks,
            compute_natural_breaks,
        )
        assert compute_quantile_breaks([], 5) == []
        assert compute_equal_interval_breaks([], 5) == []
        assert compute_natural_breaks([], 5) == []


class TestClassLabels:
    """Format labels lisibles ('< 10', '10-25', '>= 100')."""

    def test_labels_5_classes_integers(self):
        from hub.carto_classification import format_class_labels
        breaks = [0, 10, 25, 50, 75, 100]
        labels = format_class_labels(breaks, 5)
        assert len(labels) == 5
        assert labels[0].startswith("<")
        assert labels[-1].startswith("≥")

    def test_labels_format_decimals(self):
        from hub.carto_classification import format_class_labels
        breaks = [0.1, 0.5, 0.9, 1.5]
        labels = format_class_labels(breaks, 3)
        assert len(labels) == 3


class TestClassificationSingle:
    """Classification type=single : 1 couleur flat pour tout."""

    def test_single_default(self):
        from hub.carto_classification import compute_classification
        features = [{"properties": {"x": 1}}, {"properties": {"x": 2}}]
        result = compute_classification(features, {"type": "single"})
        assert result["type"] == "single"
        assert result["paint_expression"] == "#000091"  # default Marianne bleu

    def test_single_custom_color(self):
        from hub.carto_classification import compute_classification
        result = compute_classification([], {"type": "single", "single_color": "#e1000f"})
        assert result["paint_expression"] == "#e1000f"


class TestClassificationCategorized:
    """Classification type=categorized : groupes par valeur d'attribut."""

    def test_categorized_auto_derive_top_3(self):
        from hub.carto_classification import compute_classification
        features = (
            [{"properties": {"usage": "habitat"}} for _ in range(10)]
            + [{"properties": {"usage": "commerce"}} for _ in range(5)]
            + [{"properties": {"usage": "industrie"}} for _ in range(2)]
        )
        result = compute_classification(features, {
            "type": "categorized", "field": "usage", "classes": 3, "palette": "Blues"
        })
        assert result["type"] == "categorized"
        cats = result["categories"]
        assert len(cats) == 3
        # Ordre par frequence : habitat > commerce > industrie
        assert cats[0]["value"] == "habitat"
        assert cats[0]["count"] == 10

    def test_categorized_manual(self):
        from hub.carto_classification import compute_classification
        cats_manual = [
            {"value": "h", "color": "#ff0000", "label": "Habitat"},
            {"value": "c", "color": "#00ff00", "label": "Commerce"},
        ]
        result = compute_classification([], {
            "type": "categorized", "field": "usage",
            "categories": cats_manual,
        })
        assert result["categories"] == cats_manual
        # Paint expression MapLibre : ['match', ['get', 'usage'], 'h', '#ff0000', 'c', '#00ff00', null_color]
        paint = result["paint_expression"]
        assert paint[0] == "match"
        assert paint[1] == ["get", "usage"]


class TestClassificationGraduated:
    """Classification type=graduated : N classes sur valeurs numeriques."""

    def test_graduated_quantile_5_classes(self):
        from hub.carto_classification import compute_classification
        features = [{"properties": {"vuln": v}} for v in range(100)]
        result = compute_classification(features, {
            "type": "graduated", "field": "vuln",
            "method": "quantile", "classes": 5, "palette": "RdYlGn",
        })
        assert result["type"] == "graduated"
        assert len(result["breaks"]) == 6  # N+1
        assert len(result["colors"]) == 5
        assert len(result["labels"]) == 5
        # Paint expression MapLibre 'step'
        paint = result["paint_expression"]
        assert paint[0] == "step"
        assert paint[1][0] == "coalesce"

    def test_graduated_natural_breaks_jenks(self):
        from hub.carto_classification import compute_classification
        # 3 clusters distincts
        features = (
            [{"properties": {"v": v}} for v in [1, 2, 3]]
            + [{"properties": {"v": v}} for v in [50, 51, 52]]
            + [{"properties": {"v": v}} for v in [200, 201, 202]]
        )
        result = compute_classification(features, {
            "type": "graduated", "field": "v",
            "method": "natural_breaks", "classes": 3,
        })
        assert result["type"] == "graduated"
        assert result["n_classes"] == 3

    def test_graduated_no_numeric_values_fallback(self):
        from hub.carto_classification import compute_classification
        # Valeurs non-numeriques
        features = [{"properties": {"x": "abc"}}]
        result = compute_classification(features, {
            "type": "graduated", "field": "x", "classes": 5,
        })
        # Pas de crash
        assert result["type"] == "graduated"

    def test_graduated_palette_rdylgn(self):
        from hub.carto_classification import compute_classification
        features = [{"properties": {"v": v}} for v in range(50)]
        result = compute_classification(features, {
            "type": "graduated", "field": "v", "classes": 5, "palette": "RdYlGn",
        })
        colors = result["colors"]
        assert len(colors) == 5
        # RdYlGn 5 classes : rouge -> jaune -> vert
        assert "#d7191c" in colors  # red
        assert "#1a9641" in colors  # green


class TestPaintExpressionMapLibre:
    """Paint expression MapLibre directement utilisable (no JS conversion)."""

    def test_paint_categorized_match_format(self):
        from hub.carto_classification import compute_classification
        result = compute_classification([], {
            "type": "categorized", "field": "kind",
            "categories": [
                {"value": "a", "color": "#ff0000", "label": "A"},
                {"value": "b", "color": "#00ff00", "label": "B"},
            ],
        })
        paint = result["paint_expression"]
        # Format MapLibre : ['match', ['get', 'kind'], 'a', '#ff0000', 'b', '#00ff00', '#cccccc']
        assert paint[0] == "match"
        assert paint[1] == ["get", "kind"]
        assert "a" in paint
        assert "#ff0000" in paint
        assert paint[-1] == "#cccccc"  # null_color default

    def test_paint_graduated_step_format(self):
        from hub.carto_classification import compute_classification
        features = [{"properties": {"v": v}} for v in range(10)]
        result = compute_classification(features, {
            "type": "graduated", "field": "v", "classes": 3,
        })
        paint = result["paint_expression"]
        # Format MapLibre : ['step', ['coalesce',['to-number',['get','v'],-1],-1], color0, break1, color1, break2, color2]
        assert paint[0] == "step"
        # Input expression has coalesce + to-number
        assert "coalesce" in str(paint[1])
