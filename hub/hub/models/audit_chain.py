"""
hub.models.audit_chain — Attribut transverse de traçabilité.

Proposition Sprint Composants Phase 3 (2026-06-25) : `audit_chain` est un
attribut OBLIGATOIRE sur tout `Assembly` publié sur S3. Il capture la
provenance complète : sources, recipes, tool_calls, LLM provenance, confidence.

Mapping vers 3 patterns mûrs de l'écosystème (cf.
`~/.wikichat/knowledge/audit-trail-axis.md`) :

1. ZEBRA `audit_trail` (pipeline spatial : `steps[{op, n_in, n_out,
   rejected_ids, elapsed_ms, params}]`) — geoai-kit.js:498-514
2. Strate `Source` Pydantic (citation référentielle : `{corpus, ref_id,
   millesime, authority, licence, statut, url}`) — atlas/model.py:28-36
3. MobSciDat k_claims (assertion multi-source : `prompt_hash`,
   `confidence_score`, `consensus_level`, `has_contradictory_claim`)

Invariant Strate `confidence = min` propagée cross-couches.
"""

from __future__ import annotations

import hashlib
import json
from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field

from hub.models.classification import Classification


class LLMProvenance(BaseModel):
    """Reproductibilité d'un appel LLM ayant produit du contenu signé.

    Améliore le pattern MobSciDat (qui a seulement `prompt_hash VARCHAR(16)`
    avec model_id implicite dans le prompt sérialisé). Pour les livrables
    publics CEREMA reproductibles, on exige l'explicite.
    """
    prompt_hash: str = Field(
        ..., description="SHA256(prompt + model_id + seed + temperature)[:16]"
    )
    model_id: str = Field(
        ..., description="ex. 'qwen3-6-35b-moe@vllm-0.22'"
    )
    seed: int | None = None
    temperature: float | None = None
    tool_calls_count: int = 0


class ConfidenceFactors(BaseModel):
    """Facteurs détaillés de calcul de la confiance (audit cliquable).

    Adapté du pattern MobSciDat (formule multifactorielle 4 signaux) MAIS
    recalibré pour livrables spatiaux : `measurement_ratio` MobSciDat (qui
    distingue MESURE vs ESTIME en données mobilité) remplacé par
    `tool_validation_ratio` (% de tool_calls non-erreur).

    Pondérations à arbitrer Sprint Composants Phase 3 :
    - source_count (palier non-linéaire 0→0.0, 1→0.30, 2→0.65, 3+→0.75+)
    - authority_mean (palier ≥0.90→1.0, ≥0.75→0.95, ≥0.60→0.70)
    - freshness (décroissance temporelle <1an→~1.0, <2ans→~0.5)
    - tool_validation_ratio (% tool_calls success / total)
    """
    source_count: int = 0
    authority_mean: float = 0.0
    freshness: float = 0.0
    tool_validation_ratio: float = 1.0


class ComponentProvenance(BaseModel):
    """Provenance individuelle d'un composant créé (par agent IA ou user).

    Utilisé sur `Component.provenance` ET référencé depuis `AuditChain`
    quand un assemblage agrège plusieurs composants.
    """
    created_by: Literal["agent", "user", "library_template"] = "agent"
    recipe_used: str | None = Field(
        None, description="ex. 'risque_inondation@v3'"
    )
    tool_calls_made: list[dict[str, Any]] = Field(
        default_factory=list,
        description="Trace passive existante des tool_calls. Pattern Phase 11."
    )
    scene_hash_at_creation: str | None = Field(
        None, description="Snapshot du Scene Manifest au moment de la création"
    )
    llm_provenance: LLMProvenance | None = None


class AuditChain(BaseModel):
    """Audit trail OBLIGATOIRE sur tout `Assembly` publié sur S3.

    Sérialisation canonique (sort_keys=True, separators=(",",":"),
    ensure_ascii=True) puis SHA256 = `signed_hash`. Permet ancrage
    tamper-evident.
    """
    # Identité
    aid: str = Field(..., description="Référence Assembly.id (12 hex)")
    sid: str = Field(..., description="Référence Étude.id (12 hex)")
    owner: str = Field(..., description="Username CEREMA OIDC")
    classification: Classification = "cerema_internal"

    # Provenance technique
    scene_hashes: list[str] = Field(
        default_factory=list,
        description="Snapshots Scene Manifest des projets contributeurs"
    )
    components_refs: list[str] = Field(
        default_factory=list,
        description="IDs des composants référencés dans l'assemblage"
    )
    recipes_used: list[str] = Field(
        default_factory=list,
        description="Slugs des recipes ayant produit les composants"
    )
    tool_calls_made: list[dict[str, Any]] = Field(
        default_factory=list,
        description="Trace globale agrégée"
    )

    # Provenance LLM (1 entrée par tour générateur)
    llm_provenance: list[LLMProvenance] = Field(default_factory=list)

    # Sources citées (pattern Strate)
    sources: list[dict[str, str]] = Field(
        default_factory=list,
        description=(
            "Format Source Strate : {corpus, ref_id, millesime, "
            "authority, licence, statut, url}"
        ),
    )

    # Source chunks (pattern MobSciDat — pour cliquabilité humaine)
    source_chunk_ids: list[str] = Field(default_factory=list)
    source_excerpts: list[str] = Field(default_factory=list)

    # Confidence (pattern MobSciDat recalibré spatial)
    confidence_score: float | None = Field(
        None, ge=0.0, le=1.0,
        description="Confidence agrégée. Invariant Strate : MIN propagé."
    )
    confidence_level: Literal[
        "STRONG", "MEDIUM", "WEAK", "UNRELIABLE"
    ] | None = None
    confidence_factors: ConfidenceFactors | None = None

    # Consensus / contradiction (pattern MobSciDat — utile si multi-recipes)
    consensus_level: Literal[
        "UNANIMOUS", "MAJORITY", "SPLIT", "CONTRADICTED", "UNKNOWN"
    ] = "UNKNOWN"
    has_contradictory_claim: bool = False

    # Timestamps
    created_at: datetime = Field(default_factory=datetime.utcnow)

    # Ancrage tamper-evident
    signed_hash: str = Field(
        default="",
        description=(
            "SHA256 canonical de l'audit_chain (calculé via "
            "AuditChain.compute_signed_hash())"
        ),
    )

    def canonical_dict(self) -> dict[str, Any]:
        """Sérialisation canonique pour signed_hash. Exclut signed_hash
        lui-même (sinon référence circulaire)."""
        d = self.model_dump(mode="json", exclude={"signed_hash"})
        return d

    def compute_signed_hash(self) -> str:
        """SHA256 canonique de l'audit_chain."""
        canonical = json.dumps(
            self.canonical_dict(),
            sort_keys=True, separators=(",", ":"), ensure_ascii=True,
        )
        return "sha256:" + hashlib.sha256(canonical.encode("utf-8")).hexdigest()
