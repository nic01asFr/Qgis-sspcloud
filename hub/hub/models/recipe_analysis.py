"""
hub.models.recipe_analysis — Schema Pydantic V0.1 pour l'analyse meta-agent
des recipes (Sprint Composants Phase 3c).

Pattern : un agent profile=recipe_analyzer analyse une recipe (YAML + steps
Python) et produit une RecipeAnalysis à 2 facettes :

1. params_analysis : description + impact métier de chaque param
   → consommé par le LLM principal (agent Marie persona) pour la
     discipline dry-run "plan-puis-execute"

2. quality_checks : 5 catégories de checks (qgis_data, external_services,
   qgis_modules, gpu, best_practices)
   → encadrer les bonnes pratiques sans cadrer la créativité
   → sévérité dominante : warning/info, error rare

Cache : (slug, source, content_hash[:12]) hybride DB + PVC JSON
- DB : recipe_analyses_index (queryable, audit)
- PVC user : /data/studies/{sid}/recipes/{slug}/analysis.json
- PVC système : /data/system_recipes_enrichments/{slug}_{hash[:12]}.json

Capitalisation : KB axe qgis-sspcloud-composants §3c + meta-agent-enrichment-pattern
"""
from __future__ import annotations

import hashlib
import json
from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator


# ── Facette 1 : description params + impact métier ──────────────────────────


class ParamEnrichment(BaseModel):
    """Description enrichie d'un paramètre de recipe pour UX LLM principal.

    Permet à Marie persona de comprendre l'impact d'un param AVANT de lancer
    la recipe. Le LLM principal lit ce dict, présente à l'user, et propose
    d'ajuster si pertinent (discipline plan-puis-execute).
    """

    name: str = Field(..., description="Nom du paramètre tel qu'il apparaît dans la recipe.")
    type: Literal["string", "int", "float", "bool", "enum", "list", "dict", "any"]
    default: Any = Field(None, description="Valeur par défaut si l'user n'override pas.")
    description_short: str = Field(
        ..., min_length=10, max_length=200,
        description="Description en 1 ligne, lisible pour Marie."
    )
    impact_description: str = Field(
        ..., min_length=50, max_length=500,
        description=(
            "Impact métier 2-3 lignes : comment cette valeur change le résultat "
            "final (narratif, juridique, alarmiste, etc.). DOIT mentionner un "
            "mot métier (pas générique). Pas d'extrapolation."
        ),
    )
    impact_level: Literal["low", "medium", "high", "critical"] = Field(
        ...,
        description=(
            "low: technique sans impact narratif. medium: ajuste précision. "
            "high: change le scénario/audience visée. critical: RGPD, juridique, "
            "publication."
        ),
    )
    alternatives: list[str] = Field(
        default_factory=list,
        description="Valeurs alternatives pertinentes (ex: ['T10', 'T50', 'T100', 'T1000']).",
    )


# ── Facette 2 : quality_checks 5 catégories ──────────────────────────────────


QualityCategory = Literal[
    "qgis_data",          # CRS, géométries, QVariant traps, attributs
    "external_services",  # WFS/WCS, APIs, timeouts, fallbacks
    "qgis_modules",       # imports, side-effects, pluriel/singulier
    "gpu",                # torch.cuda, empty_cache, batch adaptatif
    "best_practices",     # logs print(), idempotence, validation
]


class QualityCheck(BaseModel):
    """Un check qualité détecté par le meta-agent analyseur.

    Esprit : encadrer sans cadrer. severity dominante = info/warning,
    error rare (et grave : QVariant trap qui crash garanti, side-effect
    QgsProject global qui pollue d'autres recipes, etc.).
    """

    category: QualityCategory
    severity: Literal["info", "warning", "error"] = Field(
        ...,
        description=(
            "info: suggestion d'amélioration. warning: bonne pratique recommandée. "
            "error: bug garanti ou anti-pattern dangereux."
        ),
    )
    title: str = Field(..., min_length=10, max_length=120)
    description: str = Field(..., min_length=30, max_length=600)
    location: str = Field(
        ..., description="Localisation dans le code (ex: 'step 3 / lines 42-44')."
    )
    fix_hint: str = Field(
        ..., min_length=20, max_length=400,
        description="Comment corriger (instruction actionnable, pas vague)."
    )
    code_snippet_suggested: str | None = Field(
        None, max_length=2000,
        description="Patch optionnel (Python) — si l'agent peut proposer un fix concret.",
    )


# ── Schema principal RecipeAnalysis ──────────────────────────────────────────


class RecipeAnalysis(BaseModel):
    """Analyse complète d'une recipe par le meta-agent recipe_analyzer.

    Format pivot Pydantic V0.1, persisté en DB index + JSON PVC.

    Versioning : si profile recipe_analyzer évolue, on bump analyzer_version
    → re-trigger analyse automatiquement (mécanisme à venir Phase 3c-2).
    """

    # ── Identité ────────────────────────────────────────────────────────
    slug: str = Field(..., min_length=1, max_length=128)
    source: Literal["user", "system"] = Field(
        ..., description="user = recipe PVC user-scoped, system = BigQgisMCP /app/recipes."
    )
    content_hash: str = Field(
        ..., min_length=64, max_length=64,
        description="SHA256 du YAML brut de la recipe — invalidation cache."
    )

    # ── Métadonnées analyse ─────────────────────────────────────────────
    analyzed_at: datetime = Field(default_factory=datetime.utcnow)
    analyzer_model: str = Field(
        ..., description="Modèle LLM utilisé (ex: 'qwen3-6-35b-moe')."
    )
    analyzer_version: int = Field(default=1, ge=1)

    # ── Facette 1 : UX LLM principal (params + usage) ───────────────────
    params_analysis: list[ParamEnrichment] = Field(default_factory=list)
    usage_typical: str = Field(
        ..., min_length=30, max_length=400,
        description="2-3 lignes 'cette recipe sert à...' + cas d'usage typique CEREMA.",
    )
    cost_estimate: str = Field(
        ..., min_length=10, max_length=200,
        description="Estimation temps + ordre de grandeur features (ex: '~30s sur 1000 features, ~5min sur 100k features').",
    )
    cost_level: Literal["light", "medium", "heavy"] = Field(
        ...,
        description=(
            "light: <30s typique. medium: 30s-5min (recipes spatiales courantes). "
            "heavy: >5min (jointures lourdes, GeoAI, processing batch)."
        ),
    )

    # ── Facette 2 : quality_checks ──────────────────────────────────────
    quality_checks: list[QualityCheck] = Field(default_factory=list)
    categories_checked: list[QualityCategory] = Field(
        default_factory=list,
        description=(
            "Liste des catégories effectivement analysées (peut omettre celles "
            "non pertinentes — ex: 'gpu' si pas de torch import dans la recipe)."
        ),
    )
    overall_score: float = Field(
        ..., ge=0.0, le=1.0,
        description=(
            "Score global qualité [0.0-1.0]. Pondération : "
            "error -0.3, warning -0.05, info -0.01. Plancher 0.0."
        ),
    )
    score_breakdown: dict[QualityCategory, float] = Field(
        default_factory=dict,
        description="Score par catégorie [0.0-1.0]. Catégories non analysées : 1.0 (neutre).",
    )

    # ── Validation humaine (optionnel V2 UI desk) ───────────────────────
    human_validated: bool = False
    human_validator: str | None = None
    human_validated_at: datetime | None = None
    human_notes: str | None = Field(
        None, max_length=2000,
        description="Notes admin (ex: 'fix_hint #2 appliqué manuellement le 2026-06-30').",
    )

    # ── Status (sentinel pour pending/failed) ───────────────────────────
    status: Literal["pending", "success", "failed"] = "success"
    error_detail: str | None = Field(
        None, max_length=600,
        description="Si status=failed : raison brève (LLM down, JSON invalide, etc.).",
    )

    @field_validator("content_hash")
    @classmethod
    def _validate_hash(cls, v: str) -> str:
        if not all(c in "0123456789abcdef" for c in v.lower()):
            raise ValueError("content_hash doit être SHA256 hex (64 chars).")
        return v.lower()

    def short_hash(self) -> str:
        """Helper : retourne les 12 premiers chars du content_hash (cache filename)."""
        return self.content_hash[:12]


# ── Helpers utilitaires ──────────────────────────────────────────────────────


def compute_content_hash(content: str) -> str:
    """SHA256 hex du contenu textuel (YAML brut de la recipe).

    Utilisé pour :
    - Cache invalidation : si recipe change → nouveau hash → re-analyse
    - Cache lookup : check (slug, source, hash) en DB
    """
    return hashlib.sha256(content.encode("utf-8")).hexdigest()


def empty_analysis(
    slug: str, source: Literal["user", "system"], content: str,
    analyzer_model: str = "unknown",
    status: Literal["pending", "failed"] = "pending",
    error_detail: str | None = None,
) -> RecipeAnalysis:
    """Crée un sentinel analysis (pending ou failed) — utilisé par le tool
    natif analyze_recipe pour signaler un état transitoire ou échec LLM.
    """
    return RecipeAnalysis(
        slug=slug,
        source=source,
        content_hash=compute_content_hash(content),
        analyzer_model=analyzer_model,
        params_analysis=[],
        usage_typical=("(en attente d'analyse)" if status == "pending"
                       else "(analyse échouée, recipe utilisable sans)"),
        cost_estimate="(non analysé)",
        cost_level="medium",
        quality_checks=[],
        categories_checked=[],
        overall_score=0.5 if status == "pending" else 0.3,
        score_breakdown={},
        status=status,
        error_detail=error_detail,
    )
