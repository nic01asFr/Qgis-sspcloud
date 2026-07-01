"""
hub.actions.types — Types Pydantic pour les actions CEREMA.

Sprint V1.15 (2026-07-01) — contrat stable des retours d'action.

`ActionResult` = format unifie pour toutes les mutations Component/Assembly.
Consomme par :
- endpoints /assist/action (V1.14.1 delegue + V1.15 assembly-scope)
- agent/native_tools_v2.py tools cmp_*/asy_*/stu_*
- workflows internes (create_from_recipe, publish, meta-agent recipe_analyzer)

`Scope` = enforcement des mutations bornes par le token/session.
"""
from __future__ import annotations

from typing import Any, Callable, Literal
from pydantic import BaseModel, ConfigDict, Field


# ============================================================================
# Injectable dependency : execute_python cote workspace (pod QGIS)
# ============================================================================

ExecutePythonFn = Callable[..., Any]
"""Signature attendue : async (username: str, code: str, timeout: float | None) -> str.

Injecte par le consumer :
- Endpoint qgis-sspcloud : `_execute_python_in_workspace` (main.py)
- Cross-projet IISR-Audit / atlas-territorial : leur propre backend PVC
- Tests : mock qui retourne un stdout attendu
"""


# ============================================================================
# Scope enforcement
# ============================================================================

ScopeKind = Literal[
    "study",      # desk chat, ~40 tools workspace + composants
    "assembly",   # panel V1.15 editeur, ~25 tools asy_/cmp_/stu_
    "component",  # drawer V1.14.1 delegue, ~15 tools cmp_
    "carto-mode", # V3 Sprint 3 backlog, ~20 tools carto-specific
    "standalone", # V3+ cross-projet, configurable via profile
]


class Scope(BaseModel):
    """Scope d'un token/session : enforcement des mutations."""
    model_config = ConfigDict(extra="forbid")

    kind: ScopeKind
    sid: str | None = None
    aid: str | None = None
    cid: str | None = None
    block_id: str | None = None
    owner: str  # OIDC username enforced depuis auth, pas depuis payload
    profile_id: str  # component_assist | assembly_assist | ...


# ============================================================================
# Result unifie
# ============================================================================

ActionType = Literal[
    "component_updated",    # cmp_* mutation Component.params
    "component_created",    # asy_insert_block avec kind data-driven
    "component_archived",   # asy_delete_block avec also_archive_component
    "block_inserted",       # asy_insert_block
    "block_deleted",        # asy_delete_block
    "block_moved",          # asy_move_block
    "assembly_updated",     # asy_set_title, asy_set_section_title, etc.
    "assembly_reordered",   # asy_reorder_sections
    "context_read",         # cmp_get_context, asy_get_context, stu_get_context
    "catalog_read",         # list_datasources, list_basemaps, stu_list_*
    "noop",                 # aucune mutation (idempotent no-op)
]


class HistoryEntry(BaseModel):
    """Entree d'historique reversible pour Undo/Redo."""
    model_config = ConfigDict(extra="forbid")

    id: str = Field(..., description="hist_ + 12 hex uuid4")
    aid: str | None = None
    cid: str | None = None
    actor: str
    timestamp: int  # unix ts
    tool: str
    args_json: str
    action_type: ActionType
    label: str  # humain FR "Ajoute bloc kpi_badge"
    reversible: bool = True
    reversal_tool: str | None = None
    reversal_args_json: str | None = None
    assembly_version_before: int | None = None
    assembly_version_after: int | None = None
    component_version_before: int | None = None
    component_version_after: int | None = None


class BlockRef(BaseModel):
    """Reference vers un bloc dans Assembly.layout.sections[].components[]."""
    model_config = ConfigDict(extra="allow")

    block_id: str
    kind: str
    params: dict[str, Any] | None = None
    component_ref: dict[str, Any] | None = None  # {cid, version_num_pinned}
    section_id: str | None = None


class ActionResult(BaseModel):
    """Retour unifie de toute action CEREMA.

    Consomme par :
    - Frontend : dispatch selon `action_type` vers BlockNote API
      (insertBlocks / updateBlock / removeBlocks / moveBlocks / setState)
    - Agent LLM : formatte pour reponse conversationnelle
    - Tests : verifie mutations attendues
    """
    model_config = ConfigDict(extra="forbid")

    success: bool
    tool: str
    action_type: ActionType

    # Contexte de la mutation
    cid: str | None = None
    aid: str | None = None

    # Version numbers post-mutation (pour merge OCC frontend)
    component_version_num_after: int | None = None
    assembly_version_num_after: int | None = None

    # Data payload selon action_type
    block: BlockRef | None = None  # block_inserted, block_moved
    after_block_id: str | None = None  # block_inserted
    component_created_cid: str | None = None  # asy_insert_block avec kind data-driven
    component_archived_cid: str | None = None  # asy_delete_block avec also_archive
    context: dict[str, Any] | None = None  # cmp_get_context, asy_get_context, ...

    # Historique reversible
    history_entry: HistoryEntry | None = None

    # Metadonnees
    warning: str | None = None  # info non-bloquante
    telemetry: dict[str, Any] | None = None  # duration_ms, LLM tokens, etc.
