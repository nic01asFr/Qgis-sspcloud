"""
Vague E2 Commit 5 (D-QGIS-009 §5, 2026-06-29) — Symbologie thematique.

Helpers pour computer une classification cartographique (categorized /
graduated) sur les features d'un layer GeoJSON et generer :
- Les breaks (jenks, quantile, equal_interval, manual)
- Les couleurs (palette ColorBrewer hardcoded)
- Les labels de classes
- La paint expression MapLibre

Use case : Marie veut colorer ses batiments par niveau de vulnerabilite
(graduated) ou par categorie d'usage (categorized) au lieu d'une seule
couleur flat par layer.

Pas de dependance externe (pas de geopandas/sklearn) pour rester leger.
Jenks implementation pure Python (algo Fisher-Jenks classique, ~80 lignes).
"""
from __future__ import annotations

import statistics
from typing import Any, Literal


# ============================================================================
# Palettes ColorBrewer subset (les plus utiles pour CEREMA)
# ============================================================================

# Sequential bleues (default DSFR Marianne)
PALETTE_BLUES = {
    3: ['#deebf7', '#9ecae1', '#3182bd'],
    4: ['#eff3ff', '#bdd7e7', '#6baed6', '#2171b5'],
    5: ['#eff3ff', '#bdd7e7', '#6baed6', '#3182bd', '#08519c'],
    6: ['#eff3ff', '#c6dbef', '#9ecae1', '#6baed6', '#3182bd', '#08519c'],
    7: ['#eff3ff', '#c6dbef', '#9ecae1', '#6baed6', '#4292c6', '#2171b5', '#084594'],
    8: ['#f7fbff', '#deebf7', '#c6dbef', '#9ecae1', '#6baed6', '#4292c6', '#2171b5', '#084594'],
    9: ['#f7fbff', '#deebf7', '#c6dbef', '#9ecae1', '#6baed6', '#4292c6', '#2171b5', '#08519c', '#08306b'],
}

# Sequential rouges (alerte / risque)
PALETTE_REDS = {
    3: ['#fee0d2', '#fc9272', '#de2d26'],
    4: ['#fee5d9', '#fcae91', '#fb6a4a', '#cb181d'],
    5: ['#fee5d9', '#fcae91', '#fb6a4a', '#de2d26', '#a50f15'],
    6: ['#fee5d9', '#fcbba1', '#fc9272', '#fb6a4a', '#de2d26', '#a50f15'],
    7: ['#fee5d9', '#fcbba1', '#fc9272', '#fb6a4a', '#ef3b2c', '#cb181d', '#99000d'],
    8: ['#fff5f0', '#fee0d2', '#fcbba1', '#fc9272', '#fb6a4a', '#ef3b2c', '#cb181d', '#99000d'],
    9: ['#fff5f0', '#fee0d2', '#fcbba1', '#fc9272', '#fb6a4a', '#ef3b2c', '#cb181d', '#a50f15', '#67000d'],
}

# Sequential verts (bonus / OK)
PALETTE_GREENS = {
    3: ['#e5f5e0', '#a1d99b', '#31a354'],
    4: ['#edf8e9', '#bae4b3', '#74c476', '#238b45'],
    5: ['#edf8e9', '#bae4b3', '#74c476', '#31a354', '#006d2c'],
    6: ['#edf8e9', '#c7e9c0', '#a1d99b', '#74c476', '#31a354', '#006d2c'],
    7: ['#edf8e9', '#c7e9c0', '#a1d99b', '#74c476', '#41ab5d', '#238b45', '#005a32'],
}

# Divergente Rouge-Bleu (mortalite vs sante, perte vs gain)
PALETTE_RDBU = {
    3: ['#ef8a62', '#f7f7f7', '#67a9cf'],
    5: ['#ca0020', '#f4a582', '#f7f7f7', '#92c5de', '#0571b0'],
    7: ['#b2182b', '#ef8a62', '#fddbc7', '#f7f7f7', '#d1e5f0', '#67a9cf', '#2166ac'],
    9: ['#67001f', '#b2182b', '#d6604d', '#f4a582', '#f7f7f7', '#92c5de', '#4393c3', '#2166ac', '#053061'],
}

# Divergente Rouge-Jaune-Vert (vulnerabilite, qualite)
PALETTE_RDYLGN = {
    3: ['#fc8d59', '#ffffbf', '#91cf60'],
    5: ['#d7191c', '#fdae61', '#ffffbf', '#a6d96a', '#1a9641'],
    7: ['#d73027', '#fc8d59', '#fee08b', '#ffffbf', '#d9ef8b', '#91cf60', '#1a9850'],
    9: ['#a50026', '#d73027', '#f46d43', '#fdae61', '#fee08b', '#d9ef8b', '#a6d96a', '#66bd63', '#1a9850'],
}

# Sequential Orange-Rouge (chaleur, intensite)
PALETTE_ORRD = {
    3: ['#fee8c8', '#fdbb84', '#e34a33'],
    5: ['#fef0d9', '#fdcc8a', '#fc8d59', '#e34a33', '#b30000'],
    7: ['#fef0d9', '#fdd49e', '#fdbb84', '#fc8d59', '#ef6548', '#d7301f', '#990000'],
}

PALETTES: dict[str, dict[int, list[str]]] = {
    "Blues": PALETTE_BLUES,
    "Reds": PALETTE_REDS,
    "Greens": PALETTE_GREENS,
    "RdBu": PALETTE_RDBU,
    "RdYlGn": PALETTE_RDYLGN,
    "OrRd": PALETTE_ORRD,
}


def get_palette(name: str, n_classes: int) -> list[str]:
    """Retourne N couleurs hex d'une palette ColorBrewer.

    Si n_classes pas exact disponible, prend la plus proche disponible
    et interpole (cas N=4 → utilise N=5 et drop 1).
    """
    if name not in PALETTES:
        # Fallback bleus si palette inconnue
        name = "Blues"
    palette = PALETTES[name]
    if n_classes in palette:
        return palette[n_classes]
    # Fallback : palette la plus proche
    available = sorted(palette.keys())
    closest = min(available, key=lambda k: abs(k - n_classes))
    colors = palette[closest]
    if len(colors) >= n_classes:
        return colors[:n_classes]
    # Pas assez de couleurs : repeter la derniere
    return colors + [colors[-1]] * (n_classes - len(colors))


def list_palettes() -> list[dict[str, Any]]:
    """Liste des palettes disponibles avec preview 5 classes."""
    return [
        {"name": name, "n_classes_available": sorted(p.keys()),
         "preview_5": p.get(5, p[sorted(p.keys())[0]])}
        for name, p in PALETTES.items()
    ]


# ============================================================================
# Computing breaks (discretisation algorithms)
# ============================================================================

def compute_quantile_breaks(values: list[float], n_classes: int) -> list[float]:
    """Quantile-based breaks : N classes a effectifs egaux.

    Retourne N+1 valeurs (les bornes), incluant min et max.
    """
    if not values or n_classes < 2:
        return []
    sorted_vals = sorted(values)
    breaks = [sorted_vals[0]]
    for i in range(1, n_classes):
        idx = int(len(sorted_vals) * i / n_classes)
        idx = min(idx, len(sorted_vals) - 1)
        breaks.append(sorted_vals[idx])
    breaks.append(sorted_vals[-1])
    return breaks


def compute_equal_interval_breaks(values: list[float], n_classes: int) -> list[float]:
    """Equal interval breaks : N classes de meme largeur.

    Retourne N+1 valeurs (min, breaks intermediaires, max).
    """
    if not values or n_classes < 2:
        return []
    vmin = min(values)
    vmax = max(values)
    if vmin == vmax:
        return [vmin, vmax]
    step = (vmax - vmin) / n_classes
    return [vmin + step * i for i in range(n_classes + 1)]


def compute_natural_breaks(values: list[float], n_classes: int) -> list[float]:
    """Natural breaks (Jenks-Fisher) : minimise variance intra-classe.

    Algorithme classique Fisher-Jenks. Pure Python, O(n*n_classes).
    Pour N grand (>10000 features) preferer compute_quantile_breaks.

    Retourne N+1 valeurs (min, breaks intermediaires, max).
    """
    if not values or n_classes < 2:
        return []
    sorted_vals = sorted(values)
    n = len(sorted_vals)
    if n <= n_classes:
        return [sorted_vals[0]] + sorted_vals[:n_classes - 1] + [sorted_vals[-1]]

    # Matrices : mat1[i][j] = lower class limit, mat2[i][j] = variance
    mat1 = [[0] * (n_classes + 1) for _ in range(n + 1)]
    mat2 = [[0.0] * (n_classes + 1) for _ in range(n + 1)]
    for j in range(1, n_classes + 1):
        mat1[1][j] = 1
        mat2[1][j] = 0.0
        for i in range(2, n + 1):
            mat2[i][j] = float('inf')

    v = 0.0
    for el in range(2, n + 1):
        s1 = 0.0
        s2 = 0.0
        w = 0
        for m in range(1, el + 1):
            i3 = el - m + 1
            val = sorted_vals[i3 - 1]
            s2 += val * val
            s1 += val
            w += 1
            v = s2 - (s1 * s1) / w
            i4 = i3 - 1
            if i4 != 0:
                for j in range(2, n_classes + 1):
                    if mat2[el][j] >= (v + mat2[i4][j - 1]):
                        mat1[el][j] = i3
                        mat2[el][j] = v + mat2[i4][j - 1]
        mat1[el][1] = 1
        mat2[el][1] = v

    # Reconstruct breaks
    k = n
    kclass = [0.0] * (n_classes + 1)
    kclass[n_classes] = sorted_vals[-1]
    kclass[0] = sorted_vals[0]
    count_num = n_classes
    while count_num >= 2:
        idx = mat1[k][count_num] - 2
        kclass[count_num - 1] = sorted_vals[idx]
        k = mat1[k][count_num] - 1
        count_num -= 1
    return kclass


def format_class_labels(breaks: list[float], n_classes: int) -> list[str]:
    """Genere les labels lisibles pour chaque classe ('< 10', '10-25', etc.).

    Format adapatif :
    - integers si tous breaks sont entiers
    - 2 decimales sinon
    """
    if not breaks or len(breaks) < 2:
        return []

    def fmt(v: float) -> str:
        if v == int(v):
            return str(int(v))
        return f"{v:.2f}"

    labels = []
    for i in range(n_classes):
        lo = breaks[i]
        hi = breaks[i + 1] if i + 1 < len(breaks) else breaks[-1]
        if i == 0:
            labels.append(f"< {fmt(hi)}")
        elif i == n_classes - 1:
            labels.append(f"≥ {fmt(lo)}")
        else:
            labels.append(f"{fmt(lo)} – {fmt(hi)}")
    return labels


# ============================================================================
# Main entry : compute_classification
# ============================================================================

ClassificationType = Literal["single", "categorized", "graduated"]
ClassificationMethod = Literal["quantile", "natural_breaks", "equal_interval", "manual"]


def compute_classification(
    features: list[dict[str, Any]],
    classification: dict[str, Any],
) -> dict[str, Any]:
    """Calcule une classification thematique sur les features GeoJSON.

    Args:
        features : liste de GeoJSON features (avec properties dict)
        classification : dict params Vague E2 :
            {
                "type": "single" | "categorized" | "graduated",
                "field": str (attribut a discretiser),
                "method": "quantile" | "natural_breaks" | "equal_interval" | "manual",
                "classes": int (default 5),
                "palette": str (default "Blues"),
                "breaks": list[float] (manual only),
                "null_color": str (default "#cccccc"),
                "single_color": str (type=single),
                "categories": list[{value, color, label}] (type=categorized manual),
            }

    Returns:
        {
            "type": str,
            "field": str,
            "breaks": list[float],
            "colors": list[str],
            "labels": list[str],
            "null_color": str,
            "categories": list[{value, color, label, count}] (if categorized),
            "paint_expression": list (MapLibre paint expression ready to inline),
        }
    """
    ctype = classification.get("type", "single")
    field = classification.get("field", "")
    palette = classification.get("palette", "Blues")
    null_color = classification.get("null_color", "#cccccc")
    n_classes = int(classification.get("classes", 5))
    n_classes = max(2, min(9, n_classes))

    # Type "single" : une seule couleur, pas de classification
    if ctype == "single":
        single_color = classification.get("single_color", "#000091")
        return {
            "type": "single",
            "field": field,
            "colors": [single_color],
            "labels": [classification.get("label", "Toutes valeurs")],
            "null_color": null_color,
            "breaks": [],
            "paint_expression": single_color,
        }

    # Extraire les valeurs de l'attribut depuis features
    raw_values: list[Any] = []
    for f in features:
        props = (f.get("properties") or {})
        val = props.get(field)
        if val is not None:
            raw_values.append(val)

    # Type "categorized" : groupes distincts par valeur d'attribut
    if ctype == "categorized":
        # Si categories explicites fournies (manual), use them
        cats = classification.get("categories")
        if not cats:
            # Auto-derive : top N valeurs distinctes
            from collections import Counter
            value_counts = Counter([str(v) for v in raw_values])
            top_n = value_counts.most_common(n_classes)
            colors = get_palette(palette, len(top_n))
            cats = [
                {"value": val, "color": colors[i],
                 "label": str(val), "count": cnt}
                for i, (val, cnt) in enumerate(top_n)
            ]
        # Paint expression categorized : ['match', ['get', field], cat1, color1, ..., null_color]
        paint = ["match", ["get", field]]
        for cat in cats:
            paint.append(cat["value"])
            paint.append(cat["color"])
        paint.append(null_color)
        return {
            "type": "categorized",
            "field": field,
            "categories": cats,
            "colors": [c["color"] for c in cats],
            "labels": [c["label"] for c in cats],
            "breaks": [],
            "null_color": null_color,
            "paint_expression": paint,
        }

    # Type "graduated" : N classes discretisees sur valeurs numeriques
    # Cast values en float
    numeric_values: list[float] = []
    for v in raw_values:
        try:
            numeric_values.append(float(v))
        except (TypeError, ValueError):
            continue

    if not numeric_values:
        # Fallback single si pas de values numeriques exploitables
        return {
            "type": "graduated", "field": field,
            "breaks": [], "colors": [null_color], "labels": ["No data"],
            "null_color": null_color, "paint_expression": null_color,
        }

    # Calcul des breaks selon method
    method = classification.get("method", "quantile")
    if method == "manual":
        breaks = classification.get("breaks", [])
        if not breaks:
            breaks = compute_quantile_breaks(numeric_values, n_classes)
    elif method == "natural_breaks":
        breaks = compute_natural_breaks(numeric_values, n_classes)
    elif method == "equal_interval":
        breaks = compute_equal_interval_breaks(numeric_values, n_classes)
    else:  # quantile (default)
        breaks = compute_quantile_breaks(numeric_values, n_classes)

    # N classes -> N couleurs
    colors = get_palette(palette, n_classes)
    labels = format_class_labels(breaks, n_classes)

    # Paint expression graduated : ['step', ['get', field], color0, break1, color1, ..., breakN-1, colorN-1]
    paint = ["step", ["coalesce", ["to-number", ["get", field], -1], -1], null_color]
    # First break is min, so first color applies to all values >= min (skip first break)
    for i in range(n_classes):
        if i == 0:
            # All values >= breaks[0] (min) start with colors[0]
            # We use a step starting at breaks[1] for clarity
            continue
        paint.append(breaks[i])
        paint.append(colors[i])
    # Rebuild expression with proper step semantics
    paint = ["step", ["coalesce", ["to-number", ["get", field], -1], -1], colors[0]]
    for i in range(1, n_classes):
        paint.append(breaks[i])
        paint.append(colors[i])

    return {
        "type": "graduated",
        "field": field,
        "method": method,
        "breaks": breaks,
        "colors": colors,
        "labels": labels,
        "null_color": null_color,
        "n_classes": n_classes,
        "n_features_classified": len(numeric_values),
        "paint_expression": paint,
    }
