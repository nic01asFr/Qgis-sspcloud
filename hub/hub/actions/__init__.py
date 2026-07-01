"""
hub.actions — Module actions CEREMA (Sprint V1.15).

PIVOT COHÉRENCE : 1 source de vérité pour toutes les mutations Component
et Assembly. Consommé par :
- endpoints /assist/action (V1.14.1 déléguée + V1.15 assembly-scope)
- agent/native_tools_v2.py tools cmp_*/asy_*/stu_*
- workflows internes (create_from_recipe, publish)
- cross-projet CEREMA (IISR-Audit, atlas-territorial, MobSciDat) via
  package `cerema-agent-brick` extractable

Signatures NEUTRES FastAPI/auth : execute_python injecté, user: str en param.
Erreurs typées ActionError (le consumer HTTP convertit).
"""
from hub.actions.errors import (
    ActionError,
    ActionNotFoundError,
    ActionValidationError,
    ConcurrentUpdateError,
    PersistenceError,
    ScopeViolationError,
    ToolNotAllowedError,
)
from hub.actions.types import (
    ActionResult,
    ActionType,
    BlockRef,
    ExecutePythonFn,
    HistoryEntry,
    Scope,
    ScopeKind,
)
from hub.actions.component_actions import (
    CMP_ALLOWED_TOOLS,
    apply_component_patch,
)
from hub.actions.assembly_actions import (
    ASY_ALLOWED_TOOLS,
    STU_ALLOWED_TOOLS,
    INLINE_KINDS,
    IFRAME_KINDS,
    ALL_COMPONENT_KINDS,
    apply_assembly_patch,
)
from hub.actions.agent_brick import (
    AgentBrick,
    Suggestion,
)

__all__ = [
    # Errors
    "ActionError",
    "ActionNotFoundError",
    "ActionValidationError",
    "ConcurrentUpdateError",
    "PersistenceError",
    "ScopeViolationError",
    "ToolNotAllowedError",
    # Types
    "ActionResult",
    "ActionType",
    "BlockRef",
    "ExecutePythonFn",
    "HistoryEntry",
    "Scope",
    "ScopeKind",
    # Component actions
    "CMP_ALLOWED_TOOLS",
    "apply_component_patch",
    # Assembly actions
    "ASY_ALLOWED_TOOLS",
    "STU_ALLOWED_TOOLS",
    "INLINE_KINDS",
    "IFRAME_KINDS",
    "ALL_COMPONENT_KINDS",
    "apply_assembly_patch",
    # AgentBrick
    "AgentBrick",
    "Suggestion",
]
