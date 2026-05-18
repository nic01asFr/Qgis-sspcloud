"""
hub.profile_manager — Système de profils QGIS.

Un profil définit un environnement QGIS complet :
  - Image Docker recommandée
  - Plugins activés/désactivés
  - Connexions DB disponibles
  - Sources de données pré-configurées
  - Outils MCP exposés (sous-ensemble)
  - Projets templates
  - Prompt système agent spécialisé
  - Structure /data initialisée

Les profils sont des fichiers YAML dans le dossier profiles/
adjacent à ce fichier.
"""

from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Any

log = logging.getLogger("hub.profile_manager")

_PROFILES_DIR = Path(__file__).parent / "profiles"
_DEFAULT_PROFILE_ID = os.getenv("QGIS_DEFAULT_PROFILE", "standard")

# Cache des profils chargés
_profiles: dict[str, dict] = {}


def _load_profiles() -> None:
    """Charge tous les profils YAML depuis le répertoire profiles/."""
    global _profiles
    if not _PROFILES_DIR.exists():
        log.warning("Répertoire profiles/ introuvable : %s", _PROFILES_DIR)
        _profiles = {"standard": _default_profile()}
        return

    try:
        import yaml
    except ImportError:
        log.warning("PyYAML non installé — profils YAML non chargés")
        _profiles = {"standard": _default_profile()}
        return

    _profiles = {}
    for f in _PROFILES_DIR.glob("*.yaml"):
        try:
            with open(f, encoding="utf-8") as fh:
                p = yaml.safe_load(fh)
            if p and isinstance(p, dict) and "id" in p:
                _profiles[p["id"]] = p
                log.info("Profil chargé: %s (%s)", p["id"], p.get("name", "?"))
        except Exception as e:
            log.warning("Profil %s non chargeable: %s", f.name, e)

    if not _profiles:
        _profiles = {"standard": _default_profile()}
    log.info("Profils disponibles: %s", list(_profiles.keys()))


def _default_profile() -> dict:
    """Profil minimal par défaut si aucun YAML n'est chargé."""
    return {
        "id": "standard",
        "name": "Standard",
        "description": "Profil de base",
        "image_variant": "standard",
        "requires_geoai": False,
        "mcp_tools": {"allowed": "all", "disabled": []},
        "geoai_watcher": {"enabled": False},
        "agent_system_prompt": (
            "Tu es un expert QGIS pour le CEREMA, spécialisé en analyse géospatiale. "
            "Tu as accès à plus de 60 sources de données françaises."
        ),
    }


def get_profile(profile_id: str | None = None) -> dict:
    """Retourne un profil par ID. Fallback sur standard si introuvable."""
    if not _profiles:
        _load_profiles()
    pid = profile_id or _DEFAULT_PROFILE_ID
    p = _profiles.get(pid)
    if not p:
        log.warning("Profil '%s' introuvable — fallback standard", pid)
        p = _profiles.get("standard", _default_profile())
    return p


def list_profiles() -> list[dict]:
    """Liste tous les profils disponibles (métadonnées sans system prompt)."""
    if not _profiles:
        _load_profiles()
    return [
        {
            "id": p["id"],
            "name": p.get("name", p["id"]),
            "description": p.get("description", ""),
            "image_variant": p.get("image_variant", "standard"),
            "requires_geoai": p.get("requires_geoai", False),
        }
        for p in _profiles.values()
    ]


def get_session_env(profile_id: str | None = None) -> dict[str, str]:
    """
    Retourne les variables d'environnement à injecter dans le pod QGIS.

    Le profil est géré au niveau du HUB (MCP), pas de QGIS lui-même.
    QGIS tourne toujours pareil et expose tous ses outils.
    Le hub filtre et adapte selon le profil actif.

    On n'injecte QUE ce dont le pod QGIS a vraiment besoin :
    - QGIS_GEOAI_WATCHER : activer le watcher de résultats GeoAI
    """
    p = get_profile(profile_id)
    env: dict[str, str] = {}

    # GeoAI watcher : seule variable utile pour le pod QGIS
    # Le watcher surveille /data/geoai/pending/ et charge les résultats auto
    watcher = p.get("geoai_watcher", {})
    if watcher.get("enabled"):
        env["QGIS_GEOAI_WATCHER"] = "1"

    return env


def get_agent_system_prompt(profile_id: str | None = None) -> str:
    """Retourne le system prompt agent pour ce profil."""
    p = get_profile(profile_id)
    return p.get("agent_system_prompt", "Tu es un expert QGIS pour le CEREMA.")


def get_allowed_tools(profile_id: str | None = None) -> list[str] | str:
    """
    Retourne la liste des outils MCP autorisés pour ce profil.
    "all" = tous, liste = sous-ensemble.
    """
    p = get_profile(profile_id)
    tools = p.get("mcp_tools", {})
    allowed = tools.get("allowed", "all")
    return allowed


def reload() -> None:
    """Force le rechargement de tous les profils (hot reload)."""
    global _profiles
    _profiles = {}
    _load_profiles()


# Chargement initial au démarrage du hub
_load_profiles()
