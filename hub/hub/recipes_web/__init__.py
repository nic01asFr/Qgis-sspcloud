"""hub.recipes_web — Format recipe étendu produisant un scene_manifest V0.3.x.

Chantier G4-POC + G4-b-1 : POC du pont recipe → scene_manifest publiable
directement, avec injection d'un executor QGIS (stub ou MCP). Cf. `SPEC.md`.

API publique :
  - `RecipeWeb`, `RecipeImport`, `RecipeStep`, `RecipeWebOutput` (models.py)
  - `execute_recipe_pure(recipe, context, executor=None)` (engine.py)
  - `load_recipe_from_yaml(path)` (engine.py)
  - `RecipeImportError`, `RecipeStepError` (engine.py)
  - `QgisExecutor` (Protocol), `StubQgisExecutor`, `McpQgisExecutor`
    (qgis_executor.py)
"""

from __future__ import annotations

from hub.recipes_web.engine import (
    RecipeImportError,
    RecipeStepError,
    execute_recipe_pure,
    load_recipe_from_yaml,
)
from hub.recipes_web.models import (
    RecipeImport,
    RecipeStep,
    RecipeStepIncludeBrique,
    RecipeStepRenderWeb,
    RecipeStepRunQgis,
    RecipeWeb,
    RecipeWebOutput,
)
from hub.recipes_web.qgis_executor import (
    McpQgisExecutor,
    QgisExecutor,
    StubQgisExecutor,
)

__all__ = [
    "McpQgisExecutor",
    "QgisExecutor",
    "RecipeImport",
    "RecipeImportError",
    "RecipeStep",
    "RecipeStepError",
    "RecipeStepIncludeBrique",
    "RecipeStepRenderWeb",
    "RecipeStepRunQgis",
    "RecipeWeb",
    "RecipeWebOutput",
    "StubQgisExecutor",
    "execute_recipe_pure",
    "load_recipe_from_yaml",
]
