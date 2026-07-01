"""
hub.actions.assembly_actions — Mutations Assembly reutilisables.

Sprint V1.15 Etape 2 (2026-07-01) — Assembly-scope symetrique au pattern
component_actions.py.

Tools asy_* pour muter Assembly.layout.sections[].components[] :
- asy_get_context : read-only
- asy_insert_block : ajoute bloc dans layout (inline ou ref cid si data-driven)
- asy_delete_block : retire bloc (soft-archive Component optionnel)
- asy_move_block : reordonne dans/entre sections
- asy_set_title : mute Assembly.title
- asy_set_section_title : mute Section.title
- asy_reorder_sections : permute sections
- asy_list_available_kinds : lecture read-only (13 ComponentKind)

Le distinguishing point est le format des blocks :
- **inline** kinds (kpi_grid, heading, quote, narrative_text, separator,
  kpi_badge, legend) : {"inline": {...manifest...}} — pas de Component
  persistant, pas de cascade OCC
- **iframe** kinds (interactive_map, chart, scene_3d, data_table,
  media_embed, iframe_grist) : {"ref": cid} — cree Component prealable
  via component_actions

Signatures neutres : execute_python + comp_mod + asm_mod injectes.
"""
from __future__ import annotations

import base64
import json
import logging
import secrets
import time
from typing import Any

from hub.actions.errors import (
    ActionNotFoundError,
    ActionValidationError,
    ConcurrentUpdateError,
    PersistenceError,
    ScopeViolationError,
    ToolNotAllowedError,
)
from hub.actions.types import (
    ActionResult,
    BlockRef,
    ExecutePythonFn,
    HistoryEntry,
    Scope,
)

log = logging.getLogger(__name__)


ASY_ALLOWED_TOOLS = frozenset({
    "asy_get_context",
    "asy_insert_block",
    "asy_delete_block",
    "asy_move_block",
    "asy_set_title",
    "asy_set_section_title",
    "asy_reorder_sections",
    "asy_list_available_kinds",
})


STU_ALLOWED_TOOLS = frozenset({
    "stu_get_context",
    "stu_list_recipes",
    "stu_list_scene_manifests",
})


# Kinds "inline" (bloc DOM sans Component separe)
INLINE_KINDS = frozenset({
    "kpi_grid", "kpi_badge", "heading", "quote", "narrative_text",
    "separator", "legend",
})

# Kinds "iframe" (bloc avec Component ref cid persistant)
IFRAME_KINDS = frozenset({
    "interactive_map", "chart", "scene_3d", "data_table", "media_embed",
    "iframe_grist",
})

# 13 ComponentKind total (V1.13)
ALL_COMPONENT_KINDS = INLINE_KINDS | IFRAME_KINDS


# ============================================================================
# Helpers PVC read/write assembly manifest
# ============================================================================

async def _read_assembly_manifest(
    sid: str, aid: str, user: str,
    execute_python: ExecutePythonFn,
    asm_mod: Any,
) -> dict[str, Any]:
    """Lit manifest Assembly depuis PVC."""
    stdout = await execute_python(
        user,
        asm_mod.read_assembly_manifest_pod_code(sid, aid),
    )
    if "ASSEMBLY_READ_OK" not in stdout:
        raise PersistenceError(f"Lecture manifest assembly {aid} impossible")
    b64 = stdout.split("b64=", 1)[1].split()[0].strip()
    return json.loads(base64.b64decode(b64).decode())


async def _write_assembly_manifest(
    sid: str, aid: str, user: str, canonical_json: str,
    execute_python: ExecutePythonFn,
    asm_mod: Any,
) -> None:
    """Ecrit manifest Assembly sur PVC."""
    write_code = asm_mod.write_assembly_manifest_pod_code(sid, aid, canonical_json)
    write_out = await execute_python(user, write_code)
    if "ASSEMBLY_WRITE_OK" not in write_out:
        raise PersistenceError(f"Ecriture manifest assembly {aid} : {write_out[:200]}")


# ============================================================================
# Helper : trouver le block par block_id dans layout
# ============================================================================

def _find_block_in_layout(
    layout: dict, block_id: str,
) -> tuple[int, int, dict] | None:
    """Retourne (section_idx, block_idx, block_dict) ou None."""
    sections = (layout or {}).get("sections") or []
    for s_idx, section in enumerate(sections):
        blocks = section.get("components") or []
        for b_idx, block in enumerate(blocks):
            # Format : {"ref": cid, "block_id": ...} ou {"inline": {...}}
            bid = block.get("block_id") or block.get("id")
            if not bid and "inline" in block:
                bid = block["inline"].get("block_id") or block["inline"].get("id")
            if bid == block_id:
                return s_idx, b_idx, block
    return None


def _list_all_blocks(layout: dict) -> list[dict]:
    """Retourne tous les blocs a plat avec section_id + block_id."""
    result = []
    sections = (layout or {}).get("sections") or []
    for section in sections:
        s_id = section.get("id") or section.get("section_id")
        for block in (section.get("components") or []):
            if "inline" in block:
                inl = block["inline"]
                result.append({
                    "block_id": inl.get("block_id") or inl.get("id"),
                    "kind": inl.get("kind"),
                    "section_id": s_id,
                    "is_inline": True,
                })
            else:
                result.append({
                    "block_id": block.get("block_id"),
                    "kind": None,  # kind depuis Component ref
                    "cid": block.get("ref"),
                    "section_id": s_id,
                    "is_inline": False,
                })
    return result


# ============================================================================
# apply_assembly_patch — API publique
# ============================================================================

async def apply_assembly_patch(
    *,
    sid: str,
    aid: str,
    tool: str,
    args: dict,
    user: str,
    scope: Scope | None = None,
    version_num_source: int | None = None,
    execute_python: ExecutePythonFn,
    asm_mod: Any,
    comp_mod: Any,
) -> ActionResult:
    """Dispatcher pour tools asy_* et stu_*.

    Pattern INSERT-only + OCC identique component_actions.
    Certains tools (asy_insert_block avec kind iframe) cascadent vers
    apply_component_patch prealable.
    """
    # 1. Whitelist
    if tool not in ASY_ALLOWED_TOOLS and tool not in STU_ALLOWED_TOOLS:
        raise ToolNotAllowedError(
            f"Tool '{tool}' hors whitelist asy_/stu_. "
            f"Tools : {sorted(ASY_ALLOWED_TOOLS | STU_ALLOWED_TOOLS)}",
        )

    # 2. Scope enforcement
    if scope is not None:
        if scope.kind == "assembly" and scope.aid != aid:
            raise ScopeViolationError(
                f"Scope aid={scope.aid} != requested aid={aid}",
            )

    # 3. Study-level tools (read-only)
    if tool in STU_ALLOWED_TOOLS:
        return await _dispatch_stu(sid, tool, args, user, execute_python,
                                    asm_mod, comp_mod)

    # 4. Assembly-level : lecture manifest + OCC
    latest = await asm_mod.get_assembly_latest(aid)
    if not latest:
        raise ActionNotFoundError(f"Assembly {aid} introuvable")
    if latest["sid"] != sid or latest["owner"] != user:
        raise ScopeViolationError("Pas owner ou hors scope etude")

    current_version = int(latest.get("version_num", 1))
    if version_num_source is not None:
        try:
            src_v = int(version_num_source)
        except (TypeError, ValueError):
            raise ActionValidationError("version_num_source doit etre un entier")
        if src_v != current_version:
            raise ConcurrentUpdateError(
                "Assembly modifie par un autre processus",
                current=current_version, source=src_v,
            )

    # 5. asy_get_context : read-only manifest
    if tool == "asy_get_context":
        manifest = await _read_assembly_manifest(sid, aid, user, execute_python, asm_mod)
        blocks_summary = _list_all_blocks(manifest.get("layout", {}))
        return ActionResult(
            success=True,
            tool=tool,
            action_type="context_read",
            aid=aid,
            assembly_version_num_after=current_version,
            context={
                "kind": manifest.get("kind"),
                "title": manifest.get("title"),
                "sections_count": len((manifest.get("layout") or {}).get("sections") or []),
                "blocks": blocks_summary,
                "version_num": current_version,
            },
        )

    if tool == "asy_list_available_kinds":
        return ActionResult(
            success=True,
            tool=tool,
            action_type="catalog_read",
            aid=aid,
            context={
                "inline_kinds": sorted(INLINE_KINDS),
                "iframe_kinds": sorted(IFRAME_KINDS),
                "all_kinds": sorted(ALL_COMPONENT_KINDS),
            },
        )

    # 6. Tools mutants Assembly : lecture manifest + mutation + write + insert
    manifest = await _read_assembly_manifest(sid, aid, user, execute_python, asm_mod)
    label = ""
    action_type = "assembly_updated"
    inserted_block: BlockRef | None = None
    after_block_id: str | None = None
    component_created_cid: str | None = None

    if tool == "asy_set_title":
        title = args.get("title")
        if not title or len(title) > 300:
            raise ActionValidationError("asy_set_title requiert 'title' (max 300 chars)")
        manifest["title"] = title
        label = f"Titre : {title[:50]}"

    elif tool == "asy_set_section_title":
        section_id = args.get("section_id")
        title = args.get("title")
        if not section_id or not title:
            raise ActionValidationError(
                "asy_set_section_title requiert 'section_id' et 'title'"
            )
        found = False
        for s in ((manifest.get("layout") or {}).get("sections") or []):
            if s.get("id") == section_id or s.get("section_id") == section_id:
                s["title"] = title
                found = True
                break
        if not found:
            raise ActionNotFoundError(f"Section {section_id} introuvable")
        label = f"Section : {title[:50]}"

    elif tool == "asy_reorder_sections":
        order = args.get("section_ids_order") or []
        sections = (manifest.get("layout") or {}).get("sections") or []
        if len(order) != len(sections):
            raise ActionValidationError(
                f"asy_reorder_sections : {len(order)} ids fournis vs {len(sections)} sections"
            )
        id_to_section = {}
        for s in sections:
            sid_key = s.get("id") or s.get("section_id")
            id_to_section[sid_key] = s
        try:
            new_sections = [id_to_section[s_id] for s_id in order]
        except KeyError as k:
            raise ActionValidationError(f"Section id {k} inconnu")
        manifest.setdefault("layout", {})["sections"] = new_sections
        action_type = "assembly_reordered"
        label = "Sections reordonnees"

    elif tool == "asy_insert_block":
        kind = args.get("kind")
        params = args.get("params") or {}
        section_id = args.get("section_id")
        after_block_id = args.get("after_block_id")

        if not kind or kind not in ALL_COMPONENT_KINDS:
            raise ActionValidationError(
                f"asy_insert_block : kind '{kind}' inconnu. "
                f"Valides : {sorted(ALL_COMPONENT_KINDS)}"
            )

        # Trouver section cible
        sections = (manifest.get("layout") or {}).get("sections") or []
        if not sections:
            raise ActionValidationError("Assembly sans sections")
        target_section = None
        if section_id:
            for s in sections:
                if s.get("id") == section_id or s.get("section_id") == section_id:
                    target_section = s
                    break
            if target_section is None:
                raise ActionNotFoundError(f"Section {section_id} introuvable")
        else:
            target_section = sections[0]

        block_id = f"blk_{secrets.token_hex(6)}"
        new_block: dict[str, Any] = {"block_id": block_id}

        if kind in INLINE_KINDS:
            new_block["inline"] = {
                "block_id": block_id,
                "kind": kind,
                "params": params,
            }
        else:
            # iframe kind : create Component prealable
            from hub.models import Component, ComponentRendering
            new_cid = secrets.token_hex(6)
            try:
                new_component = Component(
                    id=new_cid,
                    kind=kind,
                    title=params.get("title") or f"{kind} block",
                    params=params,
                    rendering=ComponentRendering(runtime="maplibre"),
                )
            except Exception as exc:
                raise ActionValidationError(f"Component invalide : {exc}")

            content_json = json.dumps(
                new_component.model_dump(mode="json"), ensure_ascii=False, indent=2,
            )
            # Ecrire PVC + insert DB
            write_code = comp_mod.write_component_manifest_pod_code(sid, new_cid, content_json)
            write_out = await execute_python(user, write_code)
            if "COMPONENT_WRITE_OK" not in write_out:
                raise PersistenceError(f"Ecriture Component {new_cid} : {write_out[:200]}")
            try:
                await comp_mod.insert_component(
                    component=new_component, owner=user, sid=sid,
                    file_path=comp_mod.component_manifest_path(sid, new_cid),
                    size_bytes=len(content_json.encode("utf-8")),
                    previous_hash="",
                )
            except Exception as exc:
                raise PersistenceError(f"Insert Component DB : {exc}")

            new_block["ref"] = new_cid
            component_created_cid = new_cid

        # Ajouter dans section a l'index approprie
        blocks = target_section.setdefault("components", [])
        insert_idx = len(blocks)
        if after_block_id:
            for i, b in enumerate(blocks):
                bid = b.get("block_id") or (b.get("inline") or {}).get("block_id")
                if bid == after_block_id:
                    insert_idx = i + 1
                    break
        blocks.insert(insert_idx, new_block)

        action_type = "block_inserted"
        inserted_block = BlockRef(
            block_id=block_id,
            kind=kind,
            params=params if kind in INLINE_KINDS else None,
            component_ref=(
                {"cid": new_block["ref"], "version_num_pinned": 1}
                if "ref" in new_block else None
            ),
            section_id=target_section.get("id") or target_section.get("section_id"),
        )
        label = f"Ajoute bloc {kind}"

    elif tool == "asy_delete_block":
        block_id = args.get("block_id")
        if not block_id:
            raise ActionValidationError("asy_delete_block requiert 'block_id'")
        loc = _find_block_in_layout(manifest.get("layout", {}), block_id)
        if not loc:
            raise ActionNotFoundError(f"Block {block_id} introuvable")
        s_idx, b_idx, block = loc
        del manifest["layout"]["sections"][s_idx]["components"][b_idx]
        action_type = "block_deleted"
        label = f"Retire bloc {block.get('inline', {}).get('kind') or 'component'}"

    elif tool == "asy_move_block":
        block_id = args.get("block_id")
        target_section_id = args.get("target_section_id")
        after_block_id = args.get("after_block_id")
        if not block_id:
            raise ActionValidationError("asy_move_block requiert 'block_id'")
        loc = _find_block_in_layout(manifest.get("layout", {}), block_id)
        if not loc:
            raise ActionNotFoundError(f"Block {block_id} introuvable")
        s_idx, b_idx, block = loc
        # Retirer de la section actuelle
        del manifest["layout"]["sections"][s_idx]["components"][b_idx]
        # Trouver section cible
        sections = manifest["layout"]["sections"]
        target_s_idx = s_idx  # meme section par defaut
        if target_section_id:
            for i, s in enumerate(sections):
                if s.get("id") == target_section_id or s.get("section_id") == target_section_id:
                    target_s_idx = i
                    break
        target_blocks = sections[target_s_idx].setdefault("components", [])
        # Position
        insert_idx = len(target_blocks)
        if after_block_id:
            for i, b in enumerate(target_blocks):
                bid = b.get("block_id") or (b.get("inline") or {}).get("block_id")
                if bid == after_block_id:
                    insert_idx = i + 1
                    break
        target_blocks.insert(insert_idx, block)
        action_type = "block_moved"
        label = f"Deplace bloc {block_id[:8]}"

    # 7. Validate + write + insert (INSERT-only)
    from hub.models import Assembly
    manifest["sid"] = sid
    manifest["id"] = aid
    try:
        assembly = Assembly.model_validate(manifest)
    except Exception as exc:
        raise ActionValidationError(f"Assembly invalide apres mutation : {exc}")

    assembly.version = current_version + 1
    content_json = json.dumps(
        assembly.model_dump(mode="json"), ensure_ascii=False, indent=2,
    )
    await _write_assembly_manifest(sid, aid, user, content_json, execute_python, asm_mod)

    try:
        await asm_mod.insert_assembly(
            assembly=assembly,
            owner=user,
            file_path=asm_mod.assembly_manifest_path(sid, aid),
            previous_hash=latest.get("content_hash", ""),
        )
    except Exception as exc:
        raise PersistenceError(f"Insert assembly DB : {exc}")

    # 8. History entry
    hist_id = f"hist_{secrets.token_hex(6)}"
    history_entry = HistoryEntry(
        id=hist_id,
        aid=aid,
        actor=user,
        timestamp=int(time.time()),
        tool=tool,
        args_json=json.dumps(args, ensure_ascii=False),
        action_type=action_type,
        label=label,
        reversible=True,
        assembly_version_before=current_version,
        assembly_version_after=assembly.version,
    )

    return ActionResult(
        success=True,
        tool=tool,
        action_type=action_type,
        aid=aid,
        assembly_version_num_after=assembly.version,
        block=inserted_block,
        after_block_id=after_block_id,
        component_created_cid=component_created_cid,
        history_entry=history_entry,
    )


# ============================================================================
# stu_* tools (study-level read-only)
# ============================================================================

async def _dispatch_stu(
    sid: str, tool: str, args: dict, user: str,
    execute_python: ExecutePythonFn,
    asm_mod: Any, comp_mod: Any,
) -> ActionResult:
    """Dispatcher tools stu_* (read-only study-level)."""
    from hub import studies

    if tool == "stu_get_context":
        study = await studies.get_study(sid, user)
        if not study:
            raise ActionNotFoundError(f"Etude {sid} introuvable")
        projects = await studies.list_projects(sid)
        assemblies = await asm_mod.list_assemblies(sid=sid, owner=user)
        components = await comp_mod.list_components(sid=sid, owner=user)
        return ActionResult(
            success=True,
            tool=tool,
            action_type="context_read",
            context={
                "study": {
                    "sid": study.get("sid") or study.get("id"),
                    "name": study.get("name"),
                    "zone": study.get("zone"),
                },
                "projects_count": len(projects) if projects else 0,
                "assemblies_count": len(assemblies) if assemblies else 0,
                "components_count": len(components) if components else 0,
            },
        )

    if tool == "stu_list_recipes":
        try:
            from hub import recipes_index
            recipes = await recipes_index.list_recipes(owner=user)
        except Exception:
            recipes = []
        return ActionResult(
            success=True,
            tool=tool,
            action_type="catalog_read",
            context={"recipes": recipes[:50]},
        )

    if tool == "stu_list_scene_manifests":
        try:
            manifests = await studies.list_scene_manifests(sid)
        except Exception:
            manifests = []
        return ActionResult(
            success=True,
            tool=tool,
            action_type="catalog_read",
            context={"scene_manifests": manifests[:50]},
        )

    raise ToolNotAllowedError(f"Tool stu_* {tool} non implemente")
