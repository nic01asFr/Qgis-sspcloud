"""
hub.data_layout — Structure /data globale de la session QGIS.

/data est un répertoire global structuré, commun à tous les profils.
Le MCP et l'agent SAVENT comment le lire et s'y adapter selon le profil actif.
QGIS lui-même n'a pas besoin de connaître le profil.

Structure :
  /data/
  ├── projects/          Projets QGIS (.qgz) — chargés/sauvegardés par l'agent
  ├── exports/           Livrables produits (PDF, HTML, GeoJSON, GeoPackage)
  ├── cache/             Cache données géo (WFS/WMS, 24h TTL)
  │   ├── bdtopo_*/      BD TOPO IGN
  │   ├── georisques_*/  Données risques
  │   ├── osm_*/         OpenStreetMap
  │   └── ...
  ├── geoai/             Résultats GPU GeoAI
  │   ├── pending/       En attente chargement QGIS (watcher actif)
  │   ├── loaded/        Déjà chargés dans QGIS
  │   └── rejected/      Rejetés après validation LLM
  ├── connections/       Connexions DB QGIS (fichiers XML)
  │   ├── postgis_*.xml
  │   └── spatialite_*.xml
  └── agent/             Mémoire agent (SQLite)
      └── memory.db

Le MCP adapte son comportement selon le profil :
  - risk_analyst    → utilise cache/georisques_*, exports/cartes_risques/
  - geoai_analyst   → surveille geoai/pending/, travaille sur cache/orthophotos/
  - db_analyst      → lit connections/*.xml, utilise cache/postgis/
  - standard        → tout le répertoire disponible
"""

from __future__ import annotations

import os
from pathlib import Path

_DATA_ROOT = Path(os.getenv("DATA_DIR", "/data"))

# Répertoires à créer au démarrage de la session (toujours, quel que soit le profil)
DIRS_ALWAYS = [
    "projects",
    "exports",
    "exports/storymaps",
    "cache",
    "geoai/pending",
    "geoai/loaded",
    "geoai/rejected",
    "connections",
    "agent",
    "templates",
]


def init_data_dirs() -> None:
    """Crée la structure /data si elle n'existe pas."""
    for d in DIRS_ALWAYS:
        (_DATA_ROOT / d).mkdir(parents=True, exist_ok=True)


def get_path(subdir: str) -> Path:
    """Retourne le chemin absolu d'un sous-répertoire."""
    return _DATA_ROOT / subdir


def profile_context(profile_id: str) -> dict:
    """
    Retourne le contexte /data pertinent pour un profil.
    Utilisé par le hub pour enrichir le system prompt de l'agent.
    """
    base = {
        "projects_dir":   str(_DATA_ROOT / "projects"),
        "exports_dir":    str(_DATA_ROOT / "exports"),
        "cache_dir":      str(_DATA_ROOT / "cache"),
        "connections_dir": str(_DATA_ROOT / "connections"),
    }

    if profile_id == "geoai_analyst":
        base["geoai_pending"] = str(_DATA_ROOT / "geoai/pending")
        base["geoai_loaded"]  = str(_DATA_ROOT / "geoai/loaded")
        base["hint"] = (
            "Les résultats GeoAI apparaissent dans /data/geoai/pending/ "
            "et sont auto-chargés dans QGIS. "
            "Orthophotos en cache dans /data/cache/orthophotos/."
        )
    elif profile_id == "risk_analyst":
        base["hint"] = (
            "Données Géorisques en cache dans /data/cache/georisques_*/. "
            "Projets risques dans /data/projects/risques/. "
            "Cartes exportées dans /data/exports/cartes_risques/."
        )
    elif profile_id == "db_analyst":
        base["hint"] = (
            "Connexions DB dans /data/connections/*.xml. "
            "Résultats requêtes dans /data/exports/db/. "
            "Cache PostGIS dans /data/cache/postgis/."
        )
    else:
        base["hint"] = (
            "Données en cache dans /data/cache/. "
            "Projets dans /data/projects/. "
            "Exports dans /data/exports/."
        )

    return base


def list_recent_exports(limit: int = 10) -> list[dict]:
    """Liste les exports récents pour l'affichage dans l'agent."""
    exports_dir = _DATA_ROOT / "exports"
    if not exports_dir.exists():
        return []
    files = sorted(exports_dir.rglob("*"), key=lambda f: f.stat().st_mtime, reverse=True)
    result = []
    for f in files[:limit]:
        if f.is_file():
            result.append({
                "name": f.name,
                "path": str(f),
                "size_kb": f.stat().st_size // 1024,
                "type": f.suffix.lower(),
            })
    return result


def list_projects() -> list[dict]:
    """Liste les projets QGIS sauvegardés."""
    projects_dir = _DATA_ROOT / "projects"
    if not projects_dir.exists():
        return []
    files = sorted(projects_dir.rglob("*.qgz"), key=lambda f: f.stat().st_mtime, reverse=True)
    return [{"name": f.stem, "path": str(f)} for f in files[:20]]
