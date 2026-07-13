"""
Benchmark standalone reproject_geojson_to_4326 (chantier G3 finition V4).

Genere ~14000 Polygon Lambert 93 (EPSG:2154) autour de Marseille et mesure
le temps de reprojection vers EPSG:4326 via hub.geo_utils.

Usage :
    python hub/tests/bench_reprojection.py

Ordre de grandeur cible : le workflow Marie E2E charge une couche BD TOPO
avec ~14270 batiments dans le 4e arrondissement. Si la reprojection prend
plus de 3s, on ajoute une variante vectorisee (voir cahier des charges V4).

Sortie : temps total + debit (features/s).
"""

import random
import sys
import time
from pathlib import Path

# Rendre le package hub importable sans installer le repo :
# hub/tests/bench_reprojection.py -> parent parent = hub/, on ajoute hub/
# au sys.path pour que "import hub.geo_utils" resolve hub/hub/geo_utils.py.
_HUB_ROOT = Path(__file__).resolve().parent.parent  # -> hub/
if str(_HUB_ROOT) not in sys.path:
    sys.path.insert(0, str(_HUB_ROOT))

from hub.geo_utils import reproject_geojson_to_4326  # noqa: E402


# Bbox Marseille en Lambert 93 (approx Blancarde / 4e arrondissement).
L93_XMIN = 894000.0
L93_XMAX = 896000.0
L93_YMIN = 6248000.0
L93_YMAX = 6252000.0

N_FEATURES = 14000
POLYGON_SIZE_M = 15.0  # taille moyenne d'un batiment ~15m


def _mk_polygon(cx: float, cy: float, size: float) -> dict:
    """Cree un Polygon carre autour de (cx, cy)."""
    s = size / 2.0
    ring = [
        [cx - s, cy - s],
        [cx + s, cy - s],
        [cx + s, cy + s],
        [cx - s, cy + s],
        [cx - s, cy - s],
    ]
    return {"type": "Polygon", "coordinates": [ring]}


def make_synthetic_bdtopo(n: int, seed: int = 42) -> dict:
    """Genere un FeatureCollection ~14k Polygon L93 pseudo-uniforme."""
    rng = random.Random(seed)
    features = []
    for i in range(n):
        cx = rng.uniform(L93_XMIN, L93_XMAX)
        cy = rng.uniform(L93_YMIN, L93_YMAX)
        features.append({
            "type": "Feature",
            "properties": {"id": i, "hauteur": rng.uniform(5, 30)},
            "geometry": _mk_polygon(cx, cy, POLYGON_SIZE_M),
        })
    return {
        "type": "FeatureCollection",
        "crs": {"type": "name", "properties": {"name": "urn:ogc:def:crs:EPSG::2154"}},
        "features": features,
    }


def bench(n: int = N_FEATURES) -> None:
    print(f"[bench] Generation {n} Polygon Lambert 93 autour de Marseille...")
    gj = make_synthetic_bdtopo(n)
    n_actual = len(gj["features"])
    n_points = n_actual * 5  # 5 points par ring carre

    print(f"[bench] {n_actual} features, ~{n_points} points a reprojeter")

    # Warmup pyproj (JIT init transformer)
    _ = reproject_geojson_to_4326(
        {"type": "FeatureCollection", "features": gj["features"][:10]},
        "EPSG:2154",
    )

    t0 = time.perf_counter()
    out = reproject_geojson_to_4326(gj, "EPSG:2154")
    dt = time.perf_counter() - t0

    n_out = len(out["features"])
    fps = n_actual / dt if dt > 0 else float("inf")
    pps = n_points / dt if dt > 0 else float("inf")

    print(f"[bench] Temps total : {dt*1000:.1f} ms")
    print(f"[bench] Debit       : {fps:.0f} features/s ({pps:.0f} points/s)")
    print(f"[bench] Out features: {n_out}")

    # Verification de bon sens : une feature au milieu doit tomber dans la
    # bbox Marseille WGS84 (~5.36-5.42 lon, 43.29-43.33 lat).
    sample = out["features"][0]["geometry"]["coordinates"][0][0]
    print(f"[bench] Echantillon coord out[0] : {sample}")
    assert 5.30 < sample[0] < 5.45, f"lon hors bbox : {sample[0]}"
    assert 43.27 < sample[1] < 43.35, f"lat hors bbox : {sample[1]}"

    print(f"[bench] OK. Seuil V4 = 5000 features/s. {'PASS' if fps >= 5000 else 'FAIL (vectorisation recommandee)'}")


if __name__ == "__main__":
    bench()
