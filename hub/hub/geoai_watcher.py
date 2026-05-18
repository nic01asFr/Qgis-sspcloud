"""
hub.geoai_watcher — Surveillance /data/geoai/pending/ → auto-load QGIS.

Surveille le répertoire /data/geoai/pending/ dans les sessions QGIS actives.
Quand un fichier GeoJSON/GeoPackage apparaît, il est automatiquement chargé
comme couche QGIS avec un style adapté au type de données.

Activé par : QGIS_GEOAI_WATCHER=1 dans les env vars de session.

Types détectés et styles appliqués :
  - Détections DeepForest  → couche verte (arbres)
  - Segments SAM3          → couche bleue (masques)
  - Détections OmniWater   → couche bleu foncé (eau)
  - GeoJSON générique      → couche orange

Le watcher tourne dans la session QGIS via un thread Python
lancé par le startup script QGIS.
"""

from __future__ import annotations

import hashlib
import json
import os
import time
from pathlib import Path

# ── Code injecté dans la session QGIS (via execute_python) ────────────────────

WATCHER_CODE = '''
import os, time, threading, json
from pathlib import Path
from qgis.core import (QgsProject, QgsVectorLayer, QgsFillSymbol,
                        QgsCategorizedSymbolRenderer, QgsRendererCategory,
                        QgsRuleBasedRenderer)
from qgis.PyQt.QtCore import QTimer
from qgis.PyQt.QtGui import QColor

_PENDING_DIR = Path(os.getenv("QGIS_GEOAI_WATCH_DIR", "/data/geoai/pending/"))
_LOADED_DIR  = Path(os.getenv("QGIS_GEOAI_LOADED_DIR", "/data/geoai/loaded/"))
_LOADED_DIR.mkdir(parents=True, exist_ok=True)
_PENDING_DIR.mkdir(parents=True, exist_ok=True)

# Couleurs par type de fichier GeoAI
_STYLES = {
    "deepforest": {"color": "#22cc44", "label": "Arbres détectés"},
    "sam3":       {"color": "#4488ff", "label": "Segments SAM3"},
    "sam2":       {"color": "#66aaff", "label": "Segments SAM2"},
    "omniwater":  {"color": "#0044cc", "label": "Surfaces en eau"},
    "default":    {"color": "#ff8800", "label": "Détections GeoAI"},
}

def _detect_style(path: Path) -> dict:
    name = path.stem.lower()
    for key in _STYLES:
        if key in name:
            return _STYLES[key]
    return _STYLES["default"]

def _load_file(path: Path):
    """Charge un fichier GeoAI comme couche QGIS avec style adapté."""
    style = _detect_style(path)
    layer_name = f"GeoAI — {style['label']} ({path.stem[:20]})"

    if path.suffix.lower() in (".geojson", ".json"):
        layer = QgsVectorLayer(str(path), layer_name, "ogr")
    elif path.suffix.lower() == ".gpkg":
        layer = QgsVectorLayer(str(path), layer_name, "ogr")
    else:
        return

    if not layer.isValid():
        print(f"[geoai-watcher] Couche invalide : {path.name}")
        return

    # Style fill
    sym = QgsFillSymbol.createSimple({
        "color": style["color"],
        "outline_color": "#ffffff",
        "outline_width": "0.6"
    })
    sym.setOpacity(0.75)
    layer.setRenderer(layer.renderer() or
                      QgsVectorLayer("Polygon?crs=EPSG:4326", "", "memory").renderer())
    layer.renderer().setSymbol(sym)

    QgsProject.instance().addMapLayer(layer)
    iface.mapCanvas().setExtent(layer.extent())
    iface.mapCanvas().refresh()

    # Déplacer vers loaded/
    dest = _LOADED_DIR / path.name
    path.rename(dest)
    print(f"[geoai-watcher] Chargé : {layer_name}")

def _watch():
    """Boucle de surveillance toutes les 3 secondes."""
    seen = set()
    while True:
        try:
            for f in _PENDING_DIR.iterdir():
                if f.name in seen:
                    continue
                if f.suffix.lower() in (".geojson", ".json", ".gpkg"):
                    seen.add(f.name)
                    # Attendre que le fichier soit complet (stable 1s)
                    size0 = f.stat().st_size
                    time.sleep(1)
                    if f.exists() and f.stat().st_size == size0:
                        _load_file(f)
        except Exception as e:
            pass
        time.sleep(3)

# Démarrer en thread background
_watcher_thread = threading.Thread(target=_watch, daemon=True)
_watcher_thread.start()
print("[geoai-watcher] Surveillance démarrée sur", _PENDING_DIR)
result = {"watcher": "started", "watch_dir": str(_PENDING_DIR)}
'''


async def start_watcher_in_session(session_id: str, hub_url: str, api_key: str) -> bool:
    """
    Injecte le code du watcher dans une session QGIS via execute_python MCP.
    Appelé après la création d'une session avec le profil geoai_analyst.
    """
    import httpx

    try:
        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.post(
                f"{hub_url}/mcp",
                json={
                    "jsonrpc": "2.0",
                    "id":      "watcher-start",
                    "method":  "tools/call",
                    "params":  {
                        "name":      "execute_python",
                        "arguments": {"code": WATCHER_CODE}
                    }
                },
                headers={"Authorization": f"Bearer {api_key}"},
            )
            data = resp.json()
            result = data.get("result", {})
            content = result.get("content", [{}])
            text = content[0].get("text", "") if content else ""
            return "watcher" in text.lower() or "started" in text.lower()
    except Exception as e:
        return False
