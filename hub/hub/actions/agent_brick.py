"""
hub.actions.agent_brick — Contract AgentBrick CEREMA (V1.15).

Sprint V1.15 Etape 3 (2026-07-01) : contract stable pour la brique agent
reutilisee dans desk / composant / assembly / carto-mode / standalone.

Vision ecosysteme :
- 1 brique agent (contract stable)
- N profils YAML (component_assist, assembly_assist, desk_general, ...)
- N contextes UI (drawer, panel persistent, chat, command palette)
- Extractable V1.16 en package pypi `cerema-agent-brick` pour cross-projet
  CEREMA (IISR-Audit, atlas-territorial, MobSciDat, cerema-offre-de-service)

3 methodes publiques :
- get_suggestions() : liste chip-ready pour UI
- execute_action() : dispatch tool -> hub.actions.apply_*
- (stream_chat) : reporte V2.5 avec SSE + memory L1 cascade
"""
from __future__ import annotations

import logging
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from hub.actions.errors import ToolNotAllowedError
from hub.actions.types import ActionResult, ExecutePythonFn, Scope

log = logging.getLogger(__name__)


# ============================================================================
# Suggestion : chip-ready pour Panel V1.15
# ============================================================================

class Suggestion(BaseModel):
    """Suggestion contextuelle chip-ready pour UI Assistant."""
    model_config = ConfigDict(extra="forbid")

    id: str  # identifiant unique de la suggestion
    label: str  # texte du chip (imperatif court < 50 char)
    prompt: str  # equivalent NL si Marie tape au lieu de cliquer
    tool: str | None = None  # tool a executer (None = escalade agent complet)
    tool_args: dict[str, Any] | None = None
    tool_args_partial: dict[str, Any] | None = None  # partiel, requires_selection
    hint: str | None = None  # note complementaire ("agent complet")
    requires_layer_selection: bool = False
    requires_block_selection: bool = False


# ============================================================================
# AgentBrick — contract stable
# ============================================================================

class AgentBrick:
    """Brique agent CEREMA — 1 infra, N configurations.

    Consumers :
    - qgis-sspcloud : import direct in-process
    - IISR-Audit / atlas-territorial / MobSciDat / cerema-offre-de-service :
      via package extractable `cerema-agent-brick` (pypi interne CEREMA)
      + Web Component `<cerema-agent-panel>` V1.16

    Dependency injection :
    - execute_python : ExecutePythonFn injectee par le consumer (backend PVC)
    - asm_mod, comp_mod : modules de persistence injectes (evite cycles)
    """

    def __init__(
        self,
        *,
        execute_python: ExecutePythonFn,
        asm_mod: Any,
        comp_mod: Any,
    ):
        self._execute_python = execute_python
        self._asm_mod = asm_mod
        self._comp_mod = comp_mod

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def get_suggestions(
        self,
        *,
        scope: Scope,
        profile: str,
        selected_block_id: str | None = None,
    ) -> list[Suggestion]:
        """Retourne suggestions contextuelles chip-ready.

        Pattern V1.14.1 (hardcoded par kind) etendu V1.15 :
        - Assembly scope : suggestions selon Assembly.kind + bloc selectionne
        - Component scope : suggestions selon Component.kind (comme V1.14.1)
        - Study scope : suggestions Assembly + Component + workflow

        Iter 2 (V2.5) : suggestions dynamiques via LLM pre-pass cached.
        """
        # Charge le profile pour connaitre les tools autorises
        from hub import profile_manager
        profile_config = profile_manager.get_profile(profile)
        allowed_native_tools = _get_allowed_native_tools(profile_config)

        # V1.15 iter 1 : suggestions hardcoded par scope kind
        if scope.kind == "component" and scope.cid:
            return _suggestions_for_component_scope(
                scope, selected_block_id, allowed_native_tools,
            )
        if scope.kind == "assembly" and scope.aid:
            return _suggestions_for_assembly_scope(
                scope, selected_block_id, allowed_native_tools,
            )
        return []

    async def execute_action(
        self,
        *,
        tool: str,
        args: dict[str, Any],
        scope: Scope,
        version_num_source: int | None = None,
    ) -> ActionResult:
        """Dispatch tool -> module actions approprie.

        Enforcement whitelist via profile_config.
        Route :
        - cmp_* -> apply_component_patch
        - asy_*, stu_* -> apply_assembly_patch
        """
        from hub.actions.component_actions import (
            CMP_ALLOWED_TOOLS,
            apply_component_patch,
        )
        from hub.actions.assembly_actions import (
            ASY_ALLOWED_TOOLS,
            STU_ALLOWED_TOOLS,
            apply_assembly_patch,
        )

        # Check whitelist du profile
        from hub import profile_manager
        profile_config = profile_manager.get_profile(scope.profile_id)
        allowed = _get_allowed_native_tools(profile_config)
        if allowed != "all" and tool not in allowed:
            raise ToolNotAllowedError(
                f"Tool '{tool}' non autorise pour profil '{scope.profile_id}'",
            )

        # Dispatch
        if tool in CMP_ALLOWED_TOOLS:
            if not scope.cid:
                raise ToolNotAllowedError(
                    f"Tool cmp_* requiert scope.cid (recu scope.kind={scope.kind})"
                )
            return await apply_component_patch(
                sid=scope.sid, cid=scope.cid, tool=tool, args=args,
                user=scope.owner, scope=scope,
                version_num_source=version_num_source,
                execute_python=self._execute_python,
                comp_mod=self._comp_mod,
            )
        if tool in ASY_ALLOWED_TOOLS or tool in STU_ALLOWED_TOOLS:
            if not scope.aid and tool != "stu_get_context":
                raise ToolNotAllowedError(
                    f"Tool asy_*/stu_* requiert scope.aid"
                )
            return await apply_assembly_patch(
                sid=scope.sid, aid=scope.aid, tool=tool, args=args,
                user=scope.owner, scope=scope,
                version_num_source=version_num_source,
                execute_python=self._execute_python,
                asm_mod=self._asm_mod,
                comp_mod=self._comp_mod,
            )
        raise ToolNotAllowedError(f"Tool '{tool}' non implemente")


# ============================================================================
# Helper : extraction whitelist native_tools depuis profile YAML
# ============================================================================

def _get_allowed_native_tools(profile_config: dict) -> list[str] | str:
    """Extrait la whitelist native_tools depuis le profile YAML.

    Support V1.15 : nouveau champ `native_tools.allowed` (list).
    Fallback legacy : `mcp_tools.allowed` (V1.0 profils).
    """
    native = profile_config.get("native_tools", {})
    allowed = native.get("allowed")
    if allowed is not None:
        return allowed if isinstance(allowed, list) else str(allowed)
    # Fallback legacy
    mcp = profile_config.get("mcp_tools", {})
    return mcp.get("allowed", "all")


# ============================================================================
# Suggestions hardcoded V1.15 iter 1 (V2.5 iter 2 = LLM dynamiques cached)
# ============================================================================

def _suggestions_for_component_scope(
    scope: Scope, selected_block_id: str | None,
    allowed_tools: list[str] | str,
) -> list[Suggestion]:
    """Suggestions pour scope composant (drawer V1.14.1)."""
    all_suggestions = [
        Suggestion(
            id="tri_dgpr",
            label="Citer la source TRI DGPR",
            prompt="Cite la source TRI (Territoires Risque Inondation) DGPR",
            tool="cmp_set_source_citation",
            tool_args={"datasource_id": "tri_limites"},
        ),
        Suggestion(
            id="center_marseille_4e",
            label="Centrer sur Marseille 4e arr.",
            prompt="Centre la carte sur Marseille 4e arrondissement",
            tool="cmp_set_zone",
            tool_args={"kind": "commune", "insee": "13204"},
        ),
        Suggestion(
            id="basemap_ign",
            label="Passer au fond Plan IGN v2",
            prompt="Change le fond de carte pour Plan IGN v2",
            tool="cmp_set_basemap",
            tool_args={"basemap_id": "plan-ign-v2"},
        ),
        Suggestion(
            id="tooltip_adresse",
            label="Au survol, afficher l'adresse",
            prompt="Configure la bulle au survol pour l'adresse",
            tool="cmp_set_tooltip",
            tool_args_partial={"field": "adresse"},
            requires_layer_selection=True,
        ),
        Suggestion(
            id="add_tri_perimetre",
            label="Ajouter le perimetre TRI inondation",
            prompt="Ajoute le perimetre TRI Georisques inondation",
            hint="Necessite l'agent IA complet (workspace QGIS)",
        ),
    ]
    return _filter_by_allowed(all_suggestions, allowed_tools)


def _suggestions_for_assembly_scope(
    scope: Scope, selected_block_id: str | None,
    allowed_tools: list[str] | str,
) -> list[Suggestion]:
    """Suggestions pour scope assembly (Panel V1.15 latéral).

    Marie a un bloc selectionne : suggestions ciblees.
    Marie n'a rien selectionne : suggestions assembly-level (+ Section, titre).
    """
    if selected_block_id:
        # Contexte : bloc selectionne (typiquement une carte)
        # Suggestions cmp_* pour ce bloc + suggestions insert relative
        return _filter_by_allowed([
            Suggestion(
                id="insert_kpi_after",
                label="Ajouter des chiffres cles apres",
                prompt="Ajoute un bloc chiffres cles apres celui-ci",
                tool="asy_insert_block",
                tool_args={
                    "kind": "kpi_grid",
                    "params": {"kpis": []},
                    "after_block_id": selected_block_id,
                },
            ),
            Suggestion(
                id="insert_heading_after",
                label="Ajouter un titre de section apres",
                prompt="Ajoute un titre H2 apres ce bloc",
                tool="asy_insert_block",
                tool_args={
                    "kind": "heading",
                    "params": {"text": "Nouveau titre", "level": 2},
                    "after_block_id": selected_block_id,
                },
            ),
            Suggestion(
                id="delete_block",
                label="Retirer ce bloc",
                prompt="Retire ce bloc du document",
                tool="asy_delete_block",
                tool_args={"block_id": selected_block_id},
            ),
            Suggestion(
                id="insert_narrative_after",
                label="Ajouter un paragraphe apres",
                prompt="Ajoute un paragraphe narratif apres",
                tool="asy_insert_block",
                tool_args={
                    "kind": "narrative_text",
                    "params": {"markdown": "Nouveau paragraphe..."},
                    "after_block_id": selected_block_id,
                },
            ),
        ], allowed_tools)
    else:
        # Contexte : rien selectionne — suggestions assembly-level
        return _filter_by_allowed([
            Suggestion(
                id="add_intro",
                label="Ajouter une section introduction",
                prompt="Ajoute une section d'introduction en haut",
                tool="asy_insert_block",
                tool_args={
                    "kind": "heading",
                    "params": {"text": "Introduction", "level": 2},
                },
            ),
            Suggestion(
                id="add_map",
                label="Ajouter une carte interactive",
                prompt="Ajoute un bloc carte interactive",
                tool="asy_insert_block",
                tool_args={
                    "kind": "interactive_map",
                    "params": {"title": "Nouvelle carte"},
                },
            ),
            Suggestion(
                id="add_kpi_grid",
                label="Ajouter un bandeau de chiffres cles",
                prompt="Ajoute un bandeau KPI",
                tool="asy_insert_block",
                tool_args={
                    "kind": "kpi_grid",
                    "params": {"kpis": []},
                },
            ),
            Suggestion(
                id="asy_get_context",
                label="Voir la structure du document",
                prompt="Montre-moi la structure",
                tool="asy_get_context",
                tool_args={},
            ),
        ], allowed_tools)


def _filter_by_allowed(
    suggestions: list[Suggestion], allowed: list[str] | str,
) -> list[Suggestion]:
    """Filtre les suggestions selon la whitelist native_tools du profil."""
    if allowed == "all" or not isinstance(allowed, list):
        return suggestions
    return [
        s for s in suggestions
        if not s.tool or s.tool in allowed
    ]
