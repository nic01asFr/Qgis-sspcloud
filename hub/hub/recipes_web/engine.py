"""hub.recipes_web.engine — Moteur d'exécution POC des recipes web.

Chantier G4-POC. Mode `recipe_pure` uniquement : déterministe strict,
aucun appel LLM. Le mode `recipe_polished` est documenté dans SPEC.md
comme extension future.

Points d'attention :
  - `produced_at` provient toujours de `context["timestamp"]` — jamais
    de `datetime.now()` ou `time.time()`, sinon l'idempotence saute.
  - `run_qgis` est simulé : produit un layer stub. L'intégration MCP
    réelle sera l'objet de G4-b.
  - Toute erreur d'import ou de step est fatale (levée immédiate).

API :
  - `load_recipe_from_yaml(path) -> RecipeWeb`
  - `execute_recipe_pure(recipe, context) -> RecipeWebOutput`
  - `RecipeImportError`, `RecipeStepError`
"""

from __future__ import annotations

import copy
import json
import logging
from pathlib import Path
from typing import Any

from hub import briques_loader
from hub.recipes_web.models import (
    RecipeStepIncludeBrique,
    RecipeStepRenderWeb,
    RecipeStepRunQgis,
    RecipeWeb,
    RecipeWebOutput,
)

log = logging.getLogger("hub.recipes_web.engine")


class RecipeImportError(RuntimeError):
    """Une brique référencée par `imports` ou `include_brique` est
    introuvable dans la bibliothèque."""


class RecipeStepError(RuntimeError):
    """Erreur d'exécution d'un step (paramètres invalides, kind inconnu,
    Jinja2 en échec…)."""


# --- Chargement YAML -----------------------------------------------------


def load_recipe_from_yaml(path: str | Path) -> RecipeWeb:
    """Charge un fichier YAML de recipe et valide via Pydantic.

    Le YAML doit produire un dict à sa racine. Toute erreur de parsing
    ou de validation est propagée telle quelle (YAMLError, ValidationError).
    """
    try:
        import yaml
    except ImportError as exc:  # pragma: no cover
        raise RuntimeError(
            "PyYAML est requis pour charger une recipe web YAML"
        ) from exc

    src = Path(path)
    with src.open("r", encoding="utf-8") as fh:
        raw = yaml.safe_load(fh)

    if not isinstance(raw, dict):
        raise ValueError(
            f"Recipe web mal formée (racine non dict) : {src}"
        )

    return RecipeWeb(**raw)


# --- Résolution des briques ---------------------------------------------


def _resolve_brique(ref: str) -> dict[str, Any]:
    """Récupère une brique de la bibliothèque, lève RecipeImportError si absente."""
    category, _, brique_id = ref.partition("/")
    brique = briques_loader.get_brique(category, brique_id)
    if brique is None:
        raise RecipeImportError(
            f"Brique introuvable : {ref!r} (catégorie {category!r}, "
            f"id {brique_id!r})"
        )
    return brique


def _render_narrative(
    brique: dict[str, Any], template_context: dict[str, Any]
) -> str:
    """Rendu Jinja2 d'une brique narrative avec `template_context`.

    En cas d'échec du rendu, on lève RecipeStepError (bloquant).
    """
    try:
        from jinja2 import Environment, StrictUndefined, TemplateError
    except ImportError as exc:  # pragma: no cover
        raise RuntimeError(
            "Jinja2 est requis pour rendre une brique narrative"
        ) from exc

    source = brique.get("source_content", "")
    env = Environment(
        autoescape=False,
        undefined=StrictUndefined,
        keep_trailing_newline=True,
    )
    try:
        template = env.from_string(source)
        return template.render(**template_context)
    except TemplateError as exc:
        raise RecipeStepError(
            f"Rendu Jinja2 en échec pour brique "
            f"{brique.get('category')}/{brique.get('id')} : {exc}"
        ) from exc


# --- Moteur principal ---------------------------------------------------


def _deep_merge_defaults(
    target: dict[str, Any], defaults: dict[str, Any]
) -> None:
    """Fusionne `defaults` dans `target` en place. Les clés déjà présentes
    dans `target` gagnent (defaults n'écrase pas).

    Récursif sur les sous-dicts pour éviter d'écraser une clé imbriquée.
    """
    for key, value in defaults.items():
        if key not in target:
            target[key] = copy.deepcopy(value)
            continue
        if isinstance(target[key], dict) and isinstance(value, dict):
            _deep_merge_defaults(target[key], value)
        # sinon : target garde sa valeur (les defaults n'écrasent pas).


def _stub_layer_from_run_qgis(step: RecipeStepRunQgis) -> dict[str, Any]:
    """Produit un layer stub V0.3.1 depuis un step run_qgis (POC)."""
    outputs = step.outputs
    layer_id = outputs["layer_id"]
    layer: dict[str, Any] = {
        "id": layer_id,
        "name": outputs.get("layer_name", layer_id),
        "role": outputs.get("role", "primary"),
        "geometry_type": outputs.get("geometry_type", "polygon"),
        "source": {
            "type": "geojson_path",
            "path": f"/data/scene_store/stub/{layer_id}.geojson",
            "crs": "EPSG:4326",
        },
        "style": {
            "visible": True,
            "z_index": 10,
            "classification": {
                "color": {"mode": "single", "value": "#000091"},
            },
        },
    }
    classification_field = outputs.get("classification_field")
    if classification_field:
        layer["style"]["classification"]["color"] = {
            "mode": "graduated",
            "field": classification_field,
            "method": "quantile",
        }
        layer["_stub_classification_field"] = classification_field
    return layer


def _default_slot_for(category: str) -> str:
    """Slot par défaut selon la catégorie de la brique."""
    if category == "narrative":
        return "description"
    if category in ("rules_global", "rules_forbidden"):
        return "rules_enforced"
    if category == "compositions":
        return "_merge_root"
    if category == "palettes":
        return "context.palettes"
    if category == "use_cases":
        return "provenance.use_case_ref"
    return "context"


def _inject_into_manifest(
    manifest: dict[str, Any],
    slot: str,
    payload: Any,
) -> None:
    """Injecte `payload` dans le manifest à l'emplacement désigné par `slot`.

    Slots reconnus :
      - "_merge_root" : merge shallow à la racine (payload doit être dict).
      - "rules_enforced" : append dans provenance.rules_enforced (liste).
      - "description" : concaténation dans le champ description (str).
      - "provenance.<key>" : set provenance[key] = payload.
      - "context.<key>" : set manifest["context"][key] = payload.
      - "context" : merge dans manifest["context"] (payload doit être dict).
      - autre : set manifest[slot] = payload (écrase).
    """
    if slot == "_merge_root":
        if not isinstance(payload, dict):
            raise RecipeStepError(
                "slot '_merge_root' attend un payload dict, reçu "
                f"{type(payload).__name__}"
            )
        for k, v in payload.items():
            manifest.setdefault(k, copy.deepcopy(v))
        return

    if slot == "rules_enforced":
        prov = manifest.setdefault("provenance", {})
        rules = prov.setdefault("rules_enforced", [])
        rules.append(payload)
        return

    if slot == "description":
        current = manifest.get("description", "") or ""
        sep = "\n\n" if current else ""
        manifest["description"] = f"{current}{sep}{payload}".rstrip() + "\n"
        return

    if slot.startswith("provenance."):
        key = slot.split(".", 1)[1]
        prov = manifest.setdefault("provenance", {})
        prov[key] = payload
        return

    if slot.startswith("context."):
        key = slot.split(".", 1)[1]
        ctx = manifest.setdefault("context", {})
        ctx[key] = payload
        return

    if slot == "context":
        if not isinstance(payload, dict):
            raise RecipeStepError(
                "slot 'context' attend un payload dict, reçu "
                f"{type(payload).__name__}"
            )
        ctx = manifest.setdefault("context", {})
        ctx.update(payload)
        return

    manifest[slot] = payload


# --- Exécution ----------------------------------------------------------


async def execute_recipe_pure(
    recipe: RecipeWeb, context: dict[str, Any]
) -> RecipeWebOutput:
    """Exécute une recipe web en mode `recipe_pure` (déterministe, no LLM).

    `context` doit fournir au minimum :
      - `timestamp` (str, ex. "2026-07-13T10:00:00+02:00") — utilisé comme
        `produced_at` du manifest. Sans lui : "1970-01-01T00:00:00+00:00".

    Retourne un `RecipeWebOutput` contenant :
      - `scene_manifest` prêt à publier ;
      - `provenance` complet (steps, briques, rules enforced) ;
      - `briques_used` list[str].
    """
    ctx = context or {}
    timestamp = ctx.get("timestamp") or "1970-01-01T00:00:00+00:00"

    # 1. Pré-validation des imports globaux — fail fast.
    briques_used: list[str] = []
    rules_enforced: list[dict[str, Any]] = []
    for imp in recipe.imports:
        brique = _resolve_brique(imp.brique_ref)
        briques_used.append(imp.brique_ref)
        if imp.category in ("rules_global", "rules_forbidden"):
            rules_enforced.append(
                {
                    "ref": imp.brique_ref,
                    "severity": brique.get("severity", "info"),
                    "title": brique.get("title"),
                }
            )

    # 2. Squelette du manifest.
    manifest: dict[str, Any] = {
        "$schema": (
            "https://cerema.github.io/geo-components/schemas/"
            f"scene_manifest/{recipe.manifest_version}.json"
        ),
        "manifest_version": recipe.manifest_version,
        "produced_at": timestamp,
        "title": recipe.title,
        "provenance": {
            "producer": "qgis-sspcloud/recipes_web",
            "producer_version": "G4-POC",
            "recipe_used": {
                "slug": recipe.id,
                "version": recipe.version,
                "author": recipe.author,
            },
            "rules_enforced": copy.deepcopy(rules_enforced),
        },
        "layers": [],
    }

    # 3. Exécution séquentielle des steps.
    steps_order: list[str] = []
    render_step: RecipeStepRenderWeb | None = None

    for idx, step in enumerate(recipe.steps):
        step_tag = step.id or f"{step.kind}_{idx}"

        if isinstance(step, RecipeStepRunQgis):
            log.info("[recipe %s] run_qgis stub → %s", recipe.id, step_tag)
            layer = _stub_layer_from_run_qgis(step)
            manifest["layers"].append(layer)
            steps_order.append(step_tag)
            continue

        if isinstance(step, RecipeStepIncludeBrique):
            log.info(
                "[recipe %s] include_brique %s (slot=%s)",
                recipe.id, step.ref, step.slot,
            )
            brique = _resolve_brique(step.ref)
            if step.ref not in briques_used:
                briques_used.append(step.ref)

            slot = step.slot or _default_slot_for(step.category)

            if step.category == "narrative":
                rendered = _render_narrative(brique, step.template_context)
                _inject_into_manifest(manifest, slot, rendered)
            elif step.category in ("rules_global", "rules_forbidden"):
                payload = {
                    "ref": step.ref,
                    "severity": brique.get("severity", "info"),
                    "title": brique.get("title"),
                    "rule_text": brique.get("rule_text"),
                }
                _inject_into_manifest(manifest, slot, payload)
                # Aussi dans provenance.rules_enforced (idempotent : on
                # évite d'ajouter deux fois la même ref).
                already = {
                    r.get("ref")
                    for r in manifest["provenance"].get("rules_enforced", [])
                }
                if step.ref not in already:
                    manifest["provenance"]["rules_enforced"].append(
                        {
                            "ref": step.ref,
                            "severity": brique.get("severity", "info"),
                            "title": brique.get("title"),
                        }
                    )
            elif step.category == "compositions":
                # Injecte les clés du JSON de composition à la racine du
                # manifest (les clés existantes gagnent).
                payload = {
                    k: v for k, v in brique.items()
                    if k not in ("id", "category", "path", "source_content")
                }
                _inject_into_manifest(manifest, slot, payload)
            elif step.category == "palettes":
                payload = {
                    k: v for k, v in brique.items()
                    if k not in ("path", "source_content")
                }
                target_slot = slot
                if slot == "context.palettes":
                    target_slot = f"context.palettes_{brique['id']}"
                _inject_into_manifest(manifest, target_slot, payload)
            elif step.category == "use_cases":
                _inject_into_manifest(
                    manifest, slot,
                    {"ref": step.ref, "title": brique.get("title")},
                )
            else:
                raise RecipeStepError(
                    f"Catégorie de brique non gérée : {step.category!r}"
                )
            steps_order.append(step_tag)
            continue

        if isinstance(step, RecipeStepRenderWeb):
            render_step = step
            steps_order.append(step_tag)
            continue

        raise RecipeStepError(
            f"Kind de step inconnu : {type(step).__name__}"
        )

    if render_step is None:  # pragma: no cover (validation Pydantic)
        raise RecipeStepError("Aucun step 'render_web' terminal")

    # 4. Application des manifest_defaults et target d'output.
    _deep_merge_defaults(manifest, render_step.manifest_defaults)
    manifest["provenance"]["output_kind"] = render_step.target

    if render_step.target == "assembly":
        # POC : on emballe le manifest composant dans un mini-assembly.
        component_manifest = copy.deepcopy(manifest)
        assembly_manifest: dict[str, Any] = {
            "$schema": (
                "https://cerema.github.io/geo-components/schemas/"
                "assembly_manifest/0.3.1.json"
            ),
            "manifest_version": recipe.manifest_version,
            "produced_at": timestamp,
            "title": recipe.title,
            "kind": "storymap_narrative_dsfr",
            "provenance": component_manifest["provenance"],
            "components": [
                {"inline": component_manifest},
            ],
        }
        final_manifest = assembly_manifest
    else:
        final_manifest = manifest

    # 5. Provenance normalisée + shortcut.
    provenance = {
        "recipe_id": recipe.id,
        "recipe_version": recipe.version,
        "mode": "recipe_pure",
        "produced_at": timestamp,
        "steps_order": steps_order,
        "briques_used": list(briques_used),
        "rules_enforced": copy.deepcopy(
            manifest["provenance"].get("rules_enforced", [])
        ),
        "output_kind": render_step.target,
    }

    return RecipeWebOutput(
        scene_manifest=final_manifest,
        provenance=provenance,
        briques_used=list(briques_used),
    )


# --- Utilitaires de debug -----------------------------------------------


def to_json_stable(payload: Any) -> str:
    """Sérialisation JSON stable (clés triées) — utile pour tests
    d'idempotence."""
    return json.dumps(payload, sort_keys=True, ensure_ascii=False, indent=2)
