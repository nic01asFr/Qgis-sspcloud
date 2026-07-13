"""hub.recipes_web — Format recipe étendu produisant un scene_manifest V0.3.x.

Chantier G4-POC : POC du pont recipe → scene_manifest publiable directement,
sans passer par l'éditeur BlockNote / assembly manuel. Cf. `SPEC.md`.

API publique :
  - `RecipeWeb`, `RecipeImport`, `RecipeStep`, `RecipeWebOutput` (models.py)
  - `execute_recipe_pure(recipe, context)` (engine.py)
  - `load_recipe_from_yaml(path)` (engine.py)
  - `RecipeImportError`, `RecipeStepError` (engine.py)
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

__all__ = [
    "RecipeImport",
    "RecipeImportError",
    "RecipeStep",
    "RecipeStepError",
    "RecipeStepIncludeBrique",
    "RecipeStepRenderWeb",
    "RecipeStepRunQgis",
    "RecipeWeb",
    "RecipeWebOutput",
    "execute_recipe_pure",
    "load_recipe_from_yaml",
]
