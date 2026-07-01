"""
hub.actions.component_actions — Mutations Component.params reutilisables.

Sprint V1.15 (2026-07-01) — pivot coherence : extraction de la logique
dupliquee entre :
- `component_assist_action_endpoint` (main.py:3985-4180, V1.14.1 hotfix inline)
- `update_component_endpoint` (main.py:4594-4790, pattern INSERT-only OCC)
- `agent/native_tools_v2.py:2163-2400` (5 tools cmp_* HTTP self-call)

Ces 3 chemins avaient ~150 LOC dupliquees. Ce module est la SEULE source
de verite pour "muter Component.params via patch".

Signatures neutres FastAPI/auth : `user: str` en param (pas Depends).
Pas de HTTPException herisee (les erreurs sont ActionError). Le consumer
(endpoint HTTP) convertit.

Dependency injection : `execute_python` (fn signature ExecutePythonFn)
permet de mocker en tests et de brancher un autre backend PVC cross-projet.
"""
from __future__ import annotations

import base64
import hashlib
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
    ExecutePythonFn,
    HistoryEntry,
    Scope,
)

log = logging.getLogger(__name__)


# ============================================================================
# Whitelist tools cmp_* Sprint 2.5 V2.5 + extensibles V1.15
# ============================================================================

CMP_ALLOWED_TOOLS = frozenset({
    # V1.14.1 livre en prod
    "cmp_get_context",
    "cmp_set_tooltip",
    "cmp_set_zone",
    "cmp_set_source_citation",
    "cmp_add_layer",
    # V1.15 nouveaux (backlog etude B agent)
    "cmp_remove_layer",
    "cmp_set_basemap",
    "cmp_set_classification",
    "cmp_set_popup_template",
    "cmp_set_hover_attrs",
    "cmp_reorder_layers",
    "cmp_set_legend",
})


# ============================================================================
# Helper interne : lire manifest Component depuis PVC
# ============================================================================

async def _read_component_manifest(
    sid: str, cid: str, user: str,
    execute_python: ExecutePythonFn,
    comp_mod: Any,  # module hub.components injecte pour eviter cycle
) -> dict[str, Any]:
    """Lit manifest depuis PVC via pod_code. Return dict."""
    stdout = await execute_python(
        user,
        comp_mod.read_component_manifest_pod_code(sid, cid),
    )
    if "COMPONENT_READ_OK" not in stdout:
        raise PersistenceError(f"Lecture manifest {cid} impossible")
    b64 = stdout.split("b64=", 1)[1].split()[0].strip()
    return json.loads(base64.b64decode(b64).decode())


async def _write_component_manifest(
    sid: str, cid: str, user: str, canonical_json: str,
    execute_python: ExecutePythonFn,
    comp_mod: Any,
) -> None:
    """Ecrit manifest sur PVC via pod_code."""
    write_out = await execute_python(
        user,
        comp_mod.write_component_manifest_pod_code(sid, cid, canonical_json),
    )
    if "COMPONENT_WRITE_OK" not in write_out:
        raise PersistenceError(f"Ecriture manifest {cid} : {write_out[:200]}")


# ============================================================================
# Fonction pure : appliquer une mutation params selon le tool
# ============================================================================

def _apply_cmp_mutation(
    tool: str, args: dict, params: dict[str, Any],
) -> tuple[dict[str, Any], str]:
    """Applique la mutation params en fonction du tool.

    Return (new_params, label_humain_fr).
    Raise ActionValidationError si args manquants/invalides.
    """
    if tool == "cmp_get_context":
        # Read-only : pas de mutation
        return params, "Contexte lu"

    if tool == "cmp_set_source_citation":
        datasource_id = args.get("datasource_id")
        if not datasource_id:
            raise ActionValidationError("cmp_set_source_citation requiert 'datasource_id'")
        params["datasource_id"] = datasource_id
        from hub.catalog_datasources import get_label
        label = get_label(datasource_id)
        if label:
            params["source"] = label
        return params, f"Source citee : {label or datasource_id}"

    if tool == "cmp_set_zone":
        kind = args.get("kind")
        if kind not in {"commune", "manual", "study"}:
            raise ActionValidationError("cmp_set_zone.kind hors {commune, manual, study}")
        zone = {"kind": kind}
        for k in ("insee", "buffer_km", "center_lat", "center_lng", "zoom"):
            if args.get(k) is not None:
                zone[k] = args[k]
        params["zone"] = zone
        if kind == "commune" and args.get("insee"):
            return params, f"Centre sur commune INSEE {args['insee']}"
        return params, "Zone d'etude configuree"

    if tool == "cmp_set_tooltip":
        layer_id_ref = args.get("layer_id_ref")
        field = args.get("field")
        if not layer_id_ref or not field:
            raise ActionValidationError(
                "cmp_set_tooltip requiert 'layer_id_ref' et 'field'"
            )
        overrides = list(params.get("layers_override") or [])
        found = False
        for ov in overrides:
            if ov.get("layer_id_ref") == layer_id_ref:
                ov["tooltip_field"] = field
                found = True
                break
        if not found:
            overrides.append({"layer_id_ref": layer_id_ref, "tooltip_field": field})
        params["layers_override"] = overrides
        return params, f"Bulle au survol sur '{field}'"

    if tool == "cmp_add_layer":
        scene_layer_id = args.get("scene_layer_id")
        if not scene_layer_id:
            raise ActionValidationError("cmp_add_layer requiert 'scene_layer_id'")
        overrides = list(params.get("layers_override") or [])
        ov = None
        for existing_ov in overrides:
            if existing_ov.get("layer_id_ref") == scene_layer_id:
                ov = existing_ov
                break
        if ov is None:
            ov = {"layer_id_ref": scene_layer_id}
            overrides.append(ov)
        ov["visible"] = args.get("visible", True)
        ov["opacity"] = args.get("opacity", 1.0)
        if args.get("z_index") is not None:
            ov["z_index"] = args["z_index"]
        if args.get("tooltip_field"):
            ov["tooltip_field"] = args["tooltip_field"]
        params["layers_override"] = overrides
        return params, f"Couche ajoutee : {scene_layer_id}"

    if tool == "cmp_remove_layer":
        layer_id_ref = args.get("layer_id_ref")
        if not layer_id_ref:
            raise ActionValidationError("cmp_remove_layer requiert 'layer_id_ref'")
        overrides = list(params.get("layers_override") or [])
        new_overrides = [ov for ov in overrides if ov.get("layer_id_ref") != layer_id_ref]
        if len(new_overrides) == len(overrides):
            raise ActionNotFoundError(f"Couche '{layer_id_ref}' introuvable")
        params["layers_override"] = new_overrides
        return params, f"Couche retiree : {layer_id_ref}"

    if tool == "cmp_set_basemap":
        basemap_id = args.get("basemap_id")
        if not basemap_id:
            raise ActionValidationError("cmp_set_basemap requiert 'basemap_id'")
        params["basemap_id"] = basemap_id
        return params, f"Fond de carte : {basemap_id}"

    if tool == "cmp_set_classification":
        layer_id_ref = args.get("layer_id_ref")
        classif = args.get("classification")
        if not layer_id_ref or not classif:
            raise ActionValidationError(
                "cmp_set_classification requiert 'layer_id_ref' et 'classification'"
            )
        overrides = list(params.get("layers_override") or [])
        found = False
        for ov in overrides:
            if ov.get("layer_id_ref") == layer_id_ref:
                ov["classification"] = classif
                found = True
                break
        if not found:
            overrides.append({"layer_id_ref": layer_id_ref, "classification": classif})
        params["layers_override"] = overrides
        return params, f"Classification sur '{classif.get('field', '?')}'"

    if tool == "cmp_set_popup_template":
        layer_id_ref = args.get("layer_id_ref")
        template = args.get("template")
        if not layer_id_ref or not template:
            raise ActionValidationError(
                "cmp_set_popup_template requiert 'layer_id_ref' et 'template'"
            )
        overrides = list(params.get("layers_override") or [])
        found = False
        for ov in overrides:
            if ov.get("layer_id_ref") == layer_id_ref:
                ov["popup_template"] = template
                found = True
                break
        if not found:
            overrides.append({
                "layer_id_ref": layer_id_ref,
                "popup_template": template,
            })
        params["layers_override"] = overrides
        return params, "Bulle au clic configuree"

    if tool == "cmp_set_hover_attrs":
        layer_id_ref = args.get("layer_id_ref")
        attrs = args.get("attributes") or []
        if not layer_id_ref:
            raise ActionValidationError("cmp_set_hover_attrs requiert 'layer_id_ref'")
        overrides = list(params.get("layers_override") or [])
        found = False
        for ov in overrides:
            if ov.get("layer_id_ref") == layer_id_ref:
                ov["hover_attributes"] = attrs
                found = True
                break
        if not found:
            overrides.append({
                "layer_id_ref": layer_id_ref,
                "hover_attributes": attrs,
            })
        params["layers_override"] = overrides
        return params, f"{len(attrs)} attributs au survol"

    if tool == "cmp_reorder_layers":
        layer_ids = args.get("layer_ids") or []
        overrides = list(params.get("layers_override") or [])
        ov_by_id = {ov.get("layer_id_ref"): ov for ov in overrides}
        for i, lid in enumerate(layer_ids):
            if lid in ov_by_id:
                ov_by_id[lid]["z_index"] = i
        params["layers_override"] = list(ov_by_id.values())
        return params, "Ordre des couches mis a jour"

    if tool == "cmp_set_legend":
        legend = args.get("legend")
        if legend is None:
            raise ActionValidationError("cmp_set_legend requiert 'legend'")
        params["legend"] = legend
        return params, "Legende configuree"

    raise ToolNotAllowedError(
        f"Tool '{tool}' non reconnu par _apply_cmp_mutation",
    )


# ============================================================================
# API publique du module
# ============================================================================

async def apply_component_patch(
    *,
    sid: str,
    cid: str,
    tool: str,
    args: dict,
    user: str,
    scope: Scope | None = None,
    version_num_source: int | None = None,
    execute_python: ExecutePythonFn,
    comp_mod: Any,
) -> ActionResult:
    """Applique une mutation Component.params via un tool cmp_*.

    Pattern V1.14.1 refactorise :
    1. Enforce scope (si scope fourni)
    2. Whitelist check du tool
    3. Lire manifest actuel depuis PVC
    4. Verifier OCC version_num_source
    5. Appliquer mutation via _apply_cmp_mutation (fonction pure)
    6. Valider Component Pydantic (leve ActionValidationError si echec)
    7. Bump version + write PVC + insert DB
    8. Return ActionResult typed
    """
    # 1. Whitelist tool
    if tool not in CMP_ALLOWED_TOOLS:
        raise ToolNotAllowedError(
            f"Tool '{tool}' hors whitelist cmp_*. Tools : {sorted(CMP_ALLOWED_TOOLS)}",
        )

    # 2. Scope enforcement (si fourni)
    if scope is not None:
        if scope.kind == "component" and scope.cid != cid:
            raise ScopeViolationError(
                f"Scope cid={scope.cid} != requested cid={cid}",
            )
        if scope.sid and scope.sid != sid:
            raise ScopeViolationError(
                f"Scope sid={scope.sid} != requested sid={sid}",
            )

    # 3. Lire manifest actuel
    latest = await comp_mod.get_component_latest(cid)
    if not latest or latest["sid"] != sid:
        raise ActionNotFoundError(f"Composant {cid} introuvable dans etude {sid}")
    if latest["owner"] != user:
        raise ScopeViolationError("Pas owner du composant")

    # 4. OCC version_num_source
    current_version = int(latest.get("version_num", 1))
    if version_num_source is not None:
        try:
            src_version = int(version_num_source)
        except (TypeError, ValueError):
            raise ActionValidationError("version_num_source doit etre un entier")
        if src_version != current_version:
            raise ConcurrentUpdateError(
                "Le composant a ete modifie par un autre processus",
                current=current_version, source=src_version,
            )

    # Read-only cmp_get_context : short-circuit
    if tool == "cmp_get_context":
        manifest = await _read_component_manifest(
            sid, cid, user, execute_python, comp_mod,
        )
        return ActionResult(
            success=True,
            tool=tool,
            action_type="context_read",
            cid=cid,
            component_version_num_after=current_version,
            context={
                "kind": manifest.get("kind"),
                "title": manifest.get("title"),
                "params": manifest.get("params"),
                "version_num": current_version,
            },
        )

    # 5. Lire manifest + appliquer mutation
    manifest = await _read_component_manifest(
        sid, cid, user, execute_python, comp_mod,
    )
    params = dict(manifest.get("params") or {})
    new_params, label = _apply_cmp_mutation(tool, args, params)

    # 6. Valider Component Pydantic (force sid/id depuis URL)
    new_manifest = {**manifest, "params": new_params, "sid": sid, "id": cid}
    from hub.models import Component
    try:
        component = Component.model_validate(new_manifest)
    except Exception as exc:
        raise ActionValidationError(f"Validation Pydantic : {exc}")

    # 7. Bump version + write PVC + insert DB
    component.version = current_version + 1
    content_json = json.dumps(
        component.model_dump(mode="json"), ensure_ascii=False, indent=2,
    )
    await _write_component_manifest(
        sid, cid, user, content_json, execute_python, comp_mod,
    )
    try:
        await comp_mod.insert_component(
            component=component,
            owner=user,
            sid=sid,
            file_path=comp_mod.component_manifest_path(sid, cid),
            size_bytes=len(content_json.encode("utf-8")),
            previous_hash=latest.get("content_hash", ""),
        )
    except Exception as exc:
        raise PersistenceError(f"Insert DB : {exc}")

    # 8. History entry pour Undo/Redo (V1.15)
    hist_id = f"hist_{secrets.token_hex(6)}"
    history_entry = HistoryEntry(
        id=hist_id,
        cid=cid,
        actor=user,
        timestamp=int(time.time()),
        tool=tool,
        args_json=json.dumps(args, ensure_ascii=False),
        action_type="component_updated",
        label=label,
        reversible=(tool != "cmp_get_context"),
        component_version_before=current_version,
        component_version_after=component.version,
    )

    return ActionResult(
        success=True,
        tool=tool,
        action_type="component_updated",
        cid=cid,
        component_version_num_after=component.version,
        history_entry=history_entry,
    )
