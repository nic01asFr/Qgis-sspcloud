"""hub.recipes_web.models — Modèles Pydantic du format recipe web (G4-POC).

Voir `SPEC.md` pour la description narrative. Ce module fournit :

- `RecipeImport` : pointeur vers une brique de la bibliothèque.
- `RecipeStepRunQgis`, `RecipeStepIncludeBrique`, `RecipeStepRenderWeb` :
  variantes d'un step, discriminées par le champ `kind`.
- `RecipeStep` : alias union discriminée.
- `RecipeWeb` : recipe complète, strictement typée.
- `RecipeWebOutput` : sortie normalisée du moteur d'exécution.

Toutes les validations restent locales aux modèles. Les erreurs runtime
(brique introuvable, step inconnu) sont levées côté `engine.py`.
"""

from __future__ import annotations

from typing import Annotated, Any, Literal, Union

from pydantic import BaseModel, ConfigDict, Field, field_validator

# Import du loader pour connaître les catégories valides.
# Import unique en tête pour éviter les cycles ultérieurs.
from hub.briques_loader import VALID_CATEGORIES

# --- Imports (briques référencées globalement) ---------------------------

_ALLOWED_CATEGORIES: frozenset[str] = frozenset(VALID_CATEGORIES)


class RecipeImport(BaseModel):
    """Référence à une brique de la bibliothèque, format `category/id`."""
    model_config = ConfigDict(extra="forbid")

    brique_ref: str = Field(
        ...,
        description="Référence 'category/id'. category ∈ VALID_CATEGORIES.",
    )

    @field_validator("brique_ref")
    @classmethod
    def _check_ref_shape(cls, value: str) -> str:
        if "/" not in value:
            raise ValueError(
                "brique_ref doit avoir la forme 'category/id' "
                f"(reçu : {value!r})"
            )
        category, _, brique_id = value.partition("/")
        if not category or not brique_id:
            raise ValueError(
                "brique_ref doit avoir 'category' et 'id' non vides "
                f"(reçu : {value!r})"
            )
        if category not in _ALLOWED_CATEGORIES:
            raise ValueError(
                f"catégorie de brique inconnue : {category!r} "
                f"(valides : {sorted(_ALLOWED_CATEGORIES)})"
            )
        return value

    @property
    def category(self) -> str:
        return self.brique_ref.split("/", 1)[0]

    @property
    def brique_id(self) -> str:
        return self.brique_ref.split("/", 1)[1]


# --- Steps (union discriminée sur `kind`) --------------------------------


class _RecipeStepBase(BaseModel):
    """Base commune aux steps. `id` optionnel — sert au tracing provenance."""
    model_config = ConfigDict(extra="forbid")

    id: str | None = Field(
        None, description="Identifiant optionnel du step (pour la provenance)"
    )


class RecipeStepRunQgis(_RecipeStepBase):
    """Step d'exécution QGIS.

    Dans le POC G4, l'exécution est simulée : le moteur produit un layer stub
    dans le scene_manifest. L'intégration MCP réelle interviendra en G4-b.
    """
    kind: Literal["run_qgis"]
    algorithm: str = Field(
        ...,
        description=(
            "Identifiant de l'algo QGIS ou d'un catalog datasource "
            "(ex. 'catalog_wfs', 'native:buffer')"
        ),
    )
    params: dict[str, Any] = Field(default_factory=dict)
    outputs: dict[str, Any] = Field(
        ...,
        description=(
            "Descripteur du layer produit. Doit contenir au minimum "
            "'layer_id'. Champs recommandés : layer_name, geometry_type, "
            "classification_field, role."
        ),
    )

    @field_validator("outputs")
    @classmethod
    def _check_layer_id(cls, value: dict[str, Any]) -> dict[str, Any]:
        if not value.get("layer_id"):
            raise ValueError("outputs.layer_id est requis pour run_qgis")
        return value


class RecipeStepIncludeBrique(_RecipeStepBase):
    """Step d'inclusion d'une brique dans la sortie.

    - `ref` référence la brique (même format que RecipeImport.brique_ref).
    - `template_context` alimente Jinja2 pour les briques narrative.
    - `slot` désigne l'emplacement dans le manifest final (par défaut
      "description" pour les narrative, "context" pour les autres).
    """
    kind: Literal["include_brique"]
    ref: str = Field(..., description="Référence 'category/id' de la brique")
    template_context: dict[str, Any] = Field(default_factory=dict)
    slot: str | None = Field(
        None,
        description=(
            "Slot du scene_manifest où injecter le contenu. Défaut : "
            "'description' pour narrative, 'context' sinon."
        ),
    )

    @field_validator("ref")
    @classmethod
    def _check_ref_shape(cls, value: str) -> str:
        # Réutilise la même règle que RecipeImport.
        RecipeImport(brique_ref=value)
        return value

    @property
    def category(self) -> str:
        return self.ref.split("/", 1)[0]

    @property
    def brique_id(self) -> str:
        return self.ref.split("/", 1)[1]


class RecipeStepRenderWeb(_RecipeStepBase):
    """Step terminal — agrège le scene_manifest final.

    Une seule occurrence autorisée par recipe (validation dans RecipeWeb).
    """
    kind: Literal["render_web"]
    target: Literal["component", "assembly"] = "component"
    manifest_defaults: dict[str, Any] = Field(
        default_factory=dict,
        description="Valeurs par défaut fusionnées dans le manifest de sortie",
    )


RecipeStep = Annotated[
    Union[RecipeStepRunQgis, RecipeStepIncludeBrique, RecipeStepRenderWeb],
    Field(discriminator="kind"),
]


# --- Recipe racine -------------------------------------------------------


class RecipeWeb(BaseModel):
    """Recipe web complète — voir SPEC.md.

    Contrat strict : au moins 8 champs typés, pas de champ inconnu accepté.
    """
    model_config = ConfigDict(extra="forbid")

    manifest_version: str = Field(
        ..., description="Version du SceneManifest cible (ex. '0.3.1')"
    )
    id: str = Field(..., min_length=1, max_length=200)
    title: str = Field(..., min_length=1, max_length=500)
    version: str = Field("1.0", description="Version de la recipe")
    author: str = Field("anonymous")
    use_cases: list[str] = Field(default_factory=list)
    depends_on_datasources: list[str] = Field(default_factory=list)
    output_kind: Literal["component", "assembly"] = "component"
    imports: list[RecipeImport] = Field(default_factory=list)
    steps: list[RecipeStep] = Field(..., min_length=1)

    @field_validator("steps")
    @classmethod
    def _check_render_web_unique_and_last(
        cls, value: list[RecipeStep]
    ) -> list[RecipeStep]:
        render_positions = [
            idx for idx, step in enumerate(value)
            if isinstance(step, RecipeStepRenderWeb)
        ]
        if len(render_positions) == 0:
            raise ValueError(
                "au moins un step 'render_web' est requis (terminal)"
            )
        if len(render_positions) > 1:
            raise ValueError(
                "un seul step 'render_web' autorisé par recipe "
                f"(trouvé aux positions {render_positions})"
            )
        if render_positions[0] != len(value) - 1:
            raise ValueError(
                "le step 'render_web' doit être le dernier de la liste "
                f"(position {render_positions[0]} / {len(value) - 1})"
            )
        return value


# --- Sortie du moteur ----------------------------------------------------


class RecipeWebOutput(BaseModel):
    """Sortie normalisée d'`execute_recipe_pure`.

    - `scene_manifest` : dict directement publishable (V0.3.x).
    - `provenance` : traçabilité complète (mode, briques, ordre des steps).
    - `briques_used` : shortcut pratique = provenance['briques_used'].
    """
    model_config = ConfigDict(extra="forbid")

    scene_manifest: dict[str, Any]
    provenance: dict[str, Any]
    briques_used: list[str] = Field(default_factory=list)
