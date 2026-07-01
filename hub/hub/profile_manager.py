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
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError

log = logging.getLogger("hub.profile_manager")


# ============================================================================
# Sprint V1.15 Etape 3 : Pydantic ProfileConfig validation
#
# Ferme l'anti-pattern C5 (etude C) : faute de frappe silencieuse
# (native_tools.allowes vs allowed) passait, whitelist vide, agent inutile.
#
# Support :
# - mcp_tools.allowed (legacy V1.0)
# - native_tools.allowed (V1.14.1 nouveau, component_assist.yaml,
#   assembly_assist.yaml)
# - data_scope enum (V1.15 : study | project | component | assembly |
#   carto-mode | standalone)
# ============================================================================


class ToolsConfig(BaseModel):
    """Config des tools autorises (mcp ou native)."""
    model_config = ConfigDict(extra="allow")  # accepte champs legacy

    allowed: list[str] | Literal["all"] | None = None
    disabled: list[str] | None = None


class GeoAIWatcherConfig(BaseModel):
    model_config = ConfigDict(extra="allow")
    enabled: bool = False


class ProfileMetadata(BaseModel):
    model_config = ConfigDict(extra="allow")

    category: str | None = None
    sprint: str | None = None
    livre_date: str | None = None
    scoped_data: str | None = None
    budget_context_kb: float | None = None
    budget_llm_per_open_panel: float | None = None


class ProfileConfig(BaseModel):
    """Schema strict d'un profil YAML CEREMA (V1.15).

    Validation Pydantic au boot : faute de frappe = erreur explicite,
    pas silencieuse whitelist vide.
    """
    model_config = ConfigDict(extra="allow")

    id: str = Field(..., min_length=1, description="Identifiant profil")
    name: str = Field(..., min_length=1)
    description: str = ""
    profile_version: str = "1.0"
    requires_geoai: bool = False

    # Legacy V1.0 (mcp_tools)
    mcp_tools: ToolsConfig = Field(default_factory=ToolsConfig)

    # V1.14.1 nouveau (native_tools) — utilise par component_assist,
    # assembly_assist
    native_tools: ToolsConfig = Field(default_factory=ToolsConfig)

    geoai_watcher: GeoAIWatcherConfig = Field(default_factory=GeoAIWatcherConfig)

    system_prompt: str | None = None
    agent_system_prompt: str | None = None  # alias legacy
    data_sources: list[str] = Field(default_factory=list)
    data_scope: str | None = None  # study | project | component | assembly | ...

    image_variant: str = "standard"
    metadata: ProfileMetadata = Field(default_factory=ProfileMetadata)

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
                raw = yaml.safe_load(fh)
            if not raw or not isinstance(raw, dict) or "id" not in raw:
                log.warning("Profil %s : structure invalide (id manquant)", f.name)
                continue
            # V1.15 Etape 3 : validation Pydantic (evite silencieux
            # native_tools.allowes vs allowed)
            try:
                config = ProfileConfig.model_validate(raw)
                _profiles[config.id] = config.model_dump(mode="json", exclude_none=True)
                log.info("Profil chargé: %s (%s)", config.id, config.name)
            except ValidationError as ve:
                log.error("Profil %s : validation Pydantic FAILED : %s",
                          f.name, ve.errors()[:3])
                # Ne PAS ajouter au registre : profil bugge = fail explicite
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


def get_allowed_native_tools(profile_id: str | None = None) -> list[str] | str:
    """V1.15 Etape 3 : whitelist native_tools (cmp_*/asy_*/stu_*).

    Fallback :
    - V1.14.1+ profils : `native_tools.allowed`
    - Legacy V1.0 profils : `mcp_tools.allowed` (compat)
    - Aucune whitelist : "all"
    """
    p = get_profile(profile_id)
    native = p.get("native_tools", {})
    allowed = native.get("allowed")
    if allowed is not None:
        return allowed
    return p.get("mcp_tools", {}).get("allowed", "all")


def get_data_scope(profile_id: str | None = None) -> str:
    """V1.15 : retourne data_scope pour scoped_keys V2.

    Valeurs : study | project | component | assembly | carto-mode | standalone.
    """
    p = get_profile(profile_id)
    return p.get("data_scope") or p.get("metadata", {}).get("scoped_data") or "study"


def reload() -> None:
    """Force le rechargement de tous les profils (hot reload)."""
    global _profiles
    _profiles = {}
    _load_profiles()


# Chargement initial au démarrage du hub
_load_profiles()
