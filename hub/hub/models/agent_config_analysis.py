"""
hub.models.agent_config_analysis — Schema Pydantic V0.1 pour l'analyse
meta-agent de configuration d'un agent partagé (Sprint Composants Phase 4a).

Pattern strictement identique à RecipeAnalysis (Phase 3c) :

1. params_analysis : description + impact métier des paramètres de config
   (audience, expires, profile, tools_whitelist, data_scope)
   → consommé par l'assistant conversationnel pour la discipline
     plan-puis-execute (« voici mon plan, voici les params ajustables »)

2. quality_checks : 5 catégories de checks pré-mint
   - profile_coherence : profile cohérent avec composants/recipes de l'étude
   - audience_safety : audience appropriée vs sensibilité données
   - scope_appropriateness : data_scope cohérent avec use case
   - tools_minimum : whitelist trop large (risque exposition)
   - expiration_strategy : expires_at cohérent avec use case

Capitalisation : KB axe agents-publishing-pattern + meta-agent-enrichment-pattern.
"""
from __future__ import annotations

import hashlib
import json
from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator


# ── Facette 1 : description params + impact métier ──────────────────────────


class ConfigParamEnrichment(BaseModel):
    """Description enrichie d'un paramètre de config d'agent partagé."""

    name: str = Field(..., description="Nom du param (audience, expires, profile...).")
    type: Literal["string", "int", "bool", "enum", "list", "dict", "any"]
    default: Any = Field(None)
    description_short: str = Field(..., min_length=10, max_length=200)
    impact_description: str = Field(
        ..., min_length=50, max_length=500,
        description="Impact métier (juridique, RGPD, accès, etc.).",
    )
    impact_level: Literal["low", "medium", "high", "critical"]
    alternatives: list[str] = Field(default_factory=list)


# ── Facette 2 : quality_checks 5 catégories ──────────────────────────────────


AgentConfigQualityCategory = Literal[
    "profile_coherence",       # profile vs composants étude
    "audience_safety",         # audience vs sensibilité données
    "scope_appropriateness",   # data_scope vs use case
    "tools_minimum",           # whitelist trop large
    "expiration_strategy",     # expires_at cohérent
]


class ConfigQualityCheck(BaseModel):
    """Check qualité pré-mint d'une config d'agent partagé.

    Severite dominante warning/info. error rare (audience public alors que
    composants restricted dans l'étude, expires absent pour use case
    sensible, etc.).
    """

    category: AgentConfigQualityCategory
    severity: Literal["info", "warning", "error"]
    title: str = Field(..., min_length=10, max_length=120)
    description: str = Field(..., min_length=30, max_length=600)
    affected_resource: str = Field(
        ..., description="Ressource affectée (ex: 'cid=abc12345', 'profile=storymap_v15')."
    )
    fix_hint: str = Field(..., min_length=20, max_length=400)
    suggested_param_override: dict[str, Any] | None = Field(
        None,
        description="Param override suggéré (ex: {'audience': 'restricted'}) pour appliquer le fix.",
    )


# ── Schema principal AgentConfigAnalysis ────────────────────────────────────


class AgentConfigAnalysis(BaseModel):
    """Analyse pré-mint d'une config d'agent partagé.

    Pattern identique RecipeAnalysis Phase 3c. Permet à l'assistant
    conversationnel de proposer plan + impact + warnings AVANT mint
    et publish.
    """

    # ── Identité ────────────────────────────────────────────────────────
    sid: str = Field(..., pattern=r"^[0-9a-f]{12}$", description="Étude cible.")
    config_hash: str = Field(
        ..., min_length=64, max_length=64,
        description="SHA256 hex canonical de la config proposée — invalidation cache.",
    )

    # ── Config analysée ─────────────────────────────────────────────────
    profile: str = Field(..., description="ex: 'storymap_creator_v15'")
    audience: Literal["public", "cerema_internal", "restricted", "confidential"]
    expires_at: int | None = Field(None, description="Unix timestamp ou None = jamais")
    data_scope: Literal["all", "study", "project"] = "project"
    tools_whitelist: list[str] | str = Field(default="all")
    project_id: str | None = None
    label: str | None = None

    # ── Métadonnées analyse ─────────────────────────────────────────────
    analyzed_at: datetime = Field(default_factory=datetime.utcnow)
    analyzer_model: str = Field(...)
    analyzer_version: int = Field(default=1, ge=1)

    # ── Facette 1 : params + impact ─────────────────────────────────────
    params_analysis: list[ConfigParamEnrichment] = Field(default_factory=list)
    usage_inferred: str = Field(
        ..., min_length=30, max_length=400,
        description="Use case inféré ('diffuser diagnostic PPRi à collègues CEREMA territoriaux').",
    )

    # ── Facette 2 : quality_checks ──────────────────────────────────────
    quality_checks: list[ConfigQualityCheck] = Field(default_factory=list)
    categories_checked: list[AgentConfigQualityCategory] = Field(default_factory=list)
    overall_score: float = Field(..., ge=0.0, le=1.0)
    score_breakdown: dict[AgentConfigQualityCategory, float] = Field(default_factory=dict)

    # ── Contexte étude (résolu au moment de l'analyse) ──────────────────
    components_in_study: int = Field(
        default=0, description="Nombre de composants visibles par l'agent partagé."
    )
    recipes_in_study: int = Field(default=0)
    assemblies_in_study: int = Field(default=0)
    has_restricted_components: bool = Field(
        default=False,
        description="Si True : alerte audience_safety si audience > restricted.",
    )

    # ── Status (sentinel) ───────────────────────────────────────────────
    status: Literal["pending", "success", "failed"] = "success"
    error_detail: str | None = None
    human_validated: bool = False

    @field_validator("config_hash")
    @classmethod
    def _validate_hash(cls, v: str) -> str:
        if not all(c in "0123456789abcdef" for c in v.lower()):
            raise ValueError("config_hash doit être SHA256 hex (64 chars).")
        return v.lower()

    def short_hash(self) -> str:
        return self.config_hash[:12]


# ── Helpers ──────────────────────────────────────────────────────────────────


def compute_config_hash(
    sid: str, profile: str, audience: str,
    expires_at: int | None, data_scope: str,
    tools_whitelist: list[str] | str,
    project_id: str | None = None,
    label: str | None = None,
) -> str:
    """SHA256 hex canonical de la config proposée. Utilisé pour cache
    invalidation (si même config → cache HIT).
    """
    canonical = json.dumps({
        "sid": sid,
        "profile": profile,
        "audience": audience,
        "expires_at": expires_at,
        "data_scope": data_scope,
        "tools_whitelist": tools_whitelist if isinstance(tools_whitelist, str)
                           else sorted(tools_whitelist),
        "project_id": project_id,
        "label": label,
    }, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()
