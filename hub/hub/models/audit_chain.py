"""
hub.models.audit_chain — Attribut transverse de traçabilité.

Proposition Sprint Composants Phase 3 (2026-06-25) : `audit_chain` est un
attribut OBLIGATOIRE sur tout `Assembly` publié sur S3. Il capture la
provenance complète : sources, recipes, tool_calls, LLM provenance, confidence.

Vague A 2026-06-29 :
- D-FORMAT-008 : rename `signed_hash` → `integrity_hash` (backward-compat
  property `signed_hash` deprecated). Distinction INTÉGRITÉ (SHA256) ≠
  SIGNATURE (Ed25519, futur).
- D-QGIS-006 : `sources: list[Source]` Pydantic strict aligned Strate.

Mapping vers 4 patterns mûrs de l'écosystème (cf.
`~/.wikichat/knowledge/audit-trail-axis.md`) :

1. ZEBRA `audit_trail` (pipeline spatial : `steps[{op, n_in, n_out,
   rejected_ids, elapsed_ms, params}]`) — geoai-kit.js:498-514
2. Strate `Source` Pydantic (citation référentielle : `{corpus, ref_id,
   millesime, authority, licence, statut, url}`) — atlas/model.py:28-36
   ↳ recopié ici cf. hub/hub/models/VENDORED_FROM.md (D-QGIS-006)
3. MobSciDat k_claims (assertion multi-source : `prompt_hash`,
   `confidence_score`, `consensus_level`, `has_contradictory_claim`)
4. qgis-sspcloud `audit_chain` (ce module — synthèse cross-pattern)

Invariant Strate `confidence = min` propagée par CONSOMMATEUR cross-couches.
qgis-sspcloud ne calcule PAS depuis sources[] (Invariant 6 audit-trail-axis).
"""

from __future__ import annotations

import hashlib
import json
import warnings
from datetime import datetime
from enum import Enum
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from hub.models.classification import Classification


# ── Strate Source aligned (D-QGIS-006 - recopié atlas/model.py:Source @ 2026-06-29) ──


class Statut(str, Enum):
    """Statut de vérification d'une Source citée.

    Aligned with atlas/model.py:Source @ 2026-06-29 (Strate).
    Confirmé par Strate-Architect msg wikichat 52e1bcba.
    """
    verifie = "verifie"
    presomption_haute = "presomption_haute"
    a_verifier = "a_verifier"


class Source(BaseModel):
    """Citation d'une source référentielle officielle.

    Aligned with atlas/model.py:Source @ 2026-06-29 (Strate).
    Voir hub/hub/models/VENDORED_FROM.md pour procédure resync.

    Invariants :
    - `extra="forbid"` : strict, pas de champ inconnu
    - `frozen=True` : immutable (cohérent canonicalisation audit_chain)
    - `confidence` ≠ Source (vit sur Validity côté Strate). qgis-sspcloud
      n'auto-calcule PAS depuis sources[] (Invariant 6 audit-trail).
    """
    model_config = ConfigDict(frozen=True, extra="forbid")

    corpus: str = Field(
        ..., description="ex. 'code_environnement', 'BD TOPO IGN', 'SDAGE-RM'"
    )
    ref_id: str | None = Field(
        None, description="Identifiant unitaire dans le corpus (nullable)"
    )
    millesime: str = Field(
        ..., description="Date / version (ex. '2024', 'v2024-Q3')"
    )
    authority: str = Field(
        ..., description="ex. 'IGN', 'DGALN', 'Agence de l'eau RMC'"
    )
    licence: str = Field(
        default="Licence Ouverte 2.0",
        description="Default données publiques françaises",
    )
    url: str | None = Field(
        None, description="URL canonique de la source (optionnel)"
    )
    statut: Statut = Field(
        default=Statut.presomption_haute,
        description="Statut de vérification",
    )


# ── LLM Provenance ────────────────────────────────────────────────────────────


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


# ── Confidence Factors ───────────────────────────────────────────────────────


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


# ── Component Provenance ─────────────────────────────────────────────────────


class ComponentProvenance(BaseModel):
    """Provenance individuelle d'un composant créé (par agent IA ou user).

    Utilisé sur `Component.provenance` ET sur `Assembly.provenance` (les 2
    réutilisent ce model — option C D-QGIS-009 Vague E1 2026-06-29).
    Référencé depuis `AuditChain` quand un assemblage agrège plusieurs
    composants.
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
    # Vague E1 D-QGIS-009 (2026-06-29) : trace clone pour reusabilite catalogue.
    # Reference cid si Component cloné, ou aid si Assembly cloné (sémantique
    # selon contexte d'usage). Default None : entité créée from scratch.
    cloned_from: str | None = Field(
        None,
        description=(
            "Référence vers l'entité source si clonée (cid pour Component, "
            "aid pour Assembly). None = créé from scratch."
        ),
    )


# ── AuditChain ────────────────────────────────────────────────────────────────


class AuditChain(BaseModel):
    """Audit trail OBLIGATOIRE sur tout `Assembly` publié sur S3.

    Sérialisation canonique (sort_keys=True, separators=(",",":"),
    ensure_ascii=True) puis SHA256 = `integrity_hash`. Permet ancrage
    tamper-evident.

    D-FORMAT-008 (2026-06-29) : rename `signed_hash` → `integrity_hash`.
    Distinction INTÉGRITÉ (SHA256, ce champ) ≠ SIGNATURE (Ed25519,
    cohérent Scene Manifest V0.2.2 sidecar `.sig` futur). Le legacy
    `signed_hash` reste accessible via property avec DeprecationWarning
    pendant 1 release de grâce (suppression v1.7).
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

    # Sources citées (D-QGIS-006 — Pydantic strict Strate-aligned)
    sources: list[Source] = Field(
        default_factory=list,
        description=(
            "Sources citées (Strate Source aligned). "
            "Cf. hub/hub/models/VENDORED_FROM.md procédure resync."
        ),
    )

    # Source chunks (pattern MobSciDat — pour cliquabilité humaine)
    source_chunk_ids: list[str] = Field(default_factory=list)
    source_excerpts: list[str] = Field(default_factory=list)

    # Confidence (Invariant 6 audit-trail : explicite par producteur,
    # min appliqué EN AVAL par consommateur — qgis-sspcloud n'auto-calcule pas)
    confidence_score: float | None = Field(
        None, ge=0.0, le=1.0,
        description=(
            "Confidence posée explicitement par producteur. "
            "Le min() Strate est appliqué EN AVAL par consommateur."
        )
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

    # Ancrage tamper-evident (D-FORMAT-008 rename)
    integrity_hash: str = Field(
        default="",
        description=(
            "SHA256 canonical de l'audit_chain. INTÉGRITÉ tamper-evident "
            "(pas signature crypto). Calculé via compute_integrity_hash()."
        ),
    )

    # ── Backward-compat property (1 release de grâce, suppression v1.7) ──

    @property
    def signed_hash(self) -> str:
        """DEPRECATED — utiliser `integrity_hash` (D-FORMAT-008 2026-06-29).

        Property legacy qui retourne `integrity_hash` avec
        DeprecationWarning. Sera supprimée en v1.7.
        """
        warnings.warn(
            "AuditChain.signed_hash is deprecated since v1.6.4, use "
            "integrity_hash instead (D-FORMAT-008). Will be removed in v1.7.",
            DeprecationWarning,
            stacklevel=2,
        )
        return self.integrity_hash

    # ── Méthodes ──────────────────────────────────────────────────────────────

    def canonical_dict(self) -> dict[str, Any]:
        """Sérialisation canonique pour integrity_hash.

        Exclut `integrity_hash` lui-même — référence circulaire — et
        `created_at`, qui est renseigné à l'instanciation.

        Sans cette seconde exclusion, deux chaînes au contenu identique
        produisaient des empreintes différentes : l'empreinte attestait
        l'instant du calcul, pas ce qu'on prétend sceller. Un contrôle
        d'intégrité devenait impossible, puisque recalculer donnait toujours
        autre chose. Le défaut ne se voyait pas sur une horloge grossière —
        deux créations tombaient dans la même graduation — et sortait sur une
        machine plus rapide.

        Même principe que le `scene_hash`, qui écarte déjà ses parties
        volatiles : une empreinte porte sur ce qui est atteste, et la date de
        création n'en fait pas partie. Elle reste dans l'objet, simplement hors
        du calcul.
        """
        return self.model_dump(
            mode="json", exclude={"integrity_hash", "created_at"},
        )

    def compute_integrity_hash(self) -> str:
        """SHA256 canonique de l'audit_chain (D-FORMAT-008 rename)."""
        canonical = json.dumps(
            self.canonical_dict(),
            sort_keys=True, separators=(",", ":"), ensure_ascii=True,
        )
        return "sha256:" + hashlib.sha256(canonical.encode("utf-8")).hexdigest()

    def compute_signed_hash(self) -> str:
        """DEPRECATED — utiliser `compute_integrity_hash()` (D-FORMAT-008).

        Wrapper legacy avec DeprecationWarning. Sera supprimé en v1.7.
        """
        warnings.warn(
            "AuditChain.compute_signed_hash() is deprecated since v1.6.4, "
            "use compute_integrity_hash() instead (D-FORMAT-008). Will be "
            "removed in v1.7.",
            DeprecationWarning,
            stacklevel=2,
        )
        return self.compute_integrity_hash()
