"""Tests du POC recipes_web (chantier G4-POC).

Couvre :
  - Parsing YAML → Pydantic (recipe valide, invalide, brique_ref hors catégorie).
  - Erreur brique introuvable (RecipeImportError) → fail fast.
  - Erreur render_web manquant / dupliqué / non-terminal.
  - Rendu narrative Jinja2 avec template_context.
  - Idempotence stricte (2 runs → même JSON canonique).
  - Provenance liste bien les briques utilisées.
  - Exemple canonique `diagnostic_parc_bati_temporel.yaml` chargeable et
    exécutable, sortie conforme.
"""

from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

import pytest  # noqa: E402
from pydantic import ValidationError  # noqa: E402

from hub import briques_loader  # noqa: E402
from hub.recipes_web import (  # noqa: E402
    RecipeImport,
    RecipeImportError,
    RecipeStepIncludeBrique,
    RecipeStepRenderWeb,
    RecipeStepRunQgis,
    RecipeWeb,
    execute_recipe_pure,
    load_recipe_from_yaml,
)
from hub.recipes_web.engine import to_json_stable  # noqa: E402


_EXAMPLE_YAML = (
    Path(__file__).resolve().parents[1]
    / "hub" / "recipes_web" / "examples"
    / "diagnostic_parc_bati_temporel.yaml"
)


@pytest.fixture(autouse=True)
def _reset_briques_cache():
    """Reload propre du cache briques avant chaque test."""
    briques_loader.reload_cache()
    yield
    briques_loader.reload_cache()


# --- Fabrique de recipes minimales pour les tests ------------------------


def _make_minimal_recipe(**overrides):
    base = dict(
        manifest_version="0.3.1",
        id="r_test",
        title="Recipe de test",
        version="1.0",
        author="cerema",
        imports=[
            RecipeImport(brique_ref="rules_global/crs_wgs84_obligatoire"),
        ],
        steps=[
            RecipeStepRunQgis(
                kind="run_qgis",
                id="l1",
                algorithm="catalog_wfs",
                outputs={
                    "layer_id": "layer_1",
                    "geometry_type": "polygon",
                    "classification_field": "date_d_apparition",
                    "role": "primary",
                },
            ),
            RecipeStepIncludeBrique(
                kind="include_brique",
                id="disclaimer",
                ref="narrative/disclaimer_rga",
                template_context={"zone_label": "Marseille 4e"},
            ),
            RecipeStepRenderWeb(kind="render_web", id="render"),
        ],
    )
    base.update(overrides)
    return RecipeWeb(**base)


# --- 1. Parsing / validation Pydantic -----------------------------------


def test_recipe_minimale_pydantic_valide():
    recipe = _make_minimal_recipe()
    assert recipe.id == "r_test"
    assert len(recipe.steps) == 3
    # Le discriminator doit reconstruire les bons sous-types.
    assert isinstance(recipe.steps[0], RecipeStepRunQgis)
    assert isinstance(recipe.steps[1], RecipeStepIncludeBrique)
    assert isinstance(recipe.steps[2], RecipeStepRenderWeb)


def test_recipe_extra_champ_refuse():
    with pytest.raises(ValidationError):
        RecipeWeb(
            manifest_version="0.3.1",
            id="r", title="t",
            steps=[RecipeStepRenderWeb(kind="render_web")],
            champ_inconnu="boom",
        )


def test_recipe_import_categorie_invalide_refusee():
    with pytest.raises(ValidationError) as excinfo:
        RecipeImport(brique_ref="nawak/foo")
    assert "catégorie de brique inconnue" in str(excinfo.value)


def test_recipe_import_forme_invalide():
    with pytest.raises(ValidationError):
        RecipeImport(brique_ref="pas_de_slash")


def test_recipe_render_web_absent_refuse():
    with pytest.raises(ValidationError) as excinfo:
        _make_minimal_recipe(steps=[
            RecipeStepRunQgis(
                kind="run_qgis", algorithm="catalog_wfs",
                outputs={"layer_id": "x"},
            ),
        ])
    assert "render_web" in str(excinfo.value)


def test_recipe_render_web_non_terminal_refuse():
    with pytest.raises(ValidationError) as excinfo:
        _make_minimal_recipe(steps=[
            RecipeStepRenderWeb(kind="render_web"),
            RecipeStepRunQgis(
                kind="run_qgis", algorithm="catalog_wfs",
                outputs={"layer_id": "x"},
            ),
        ])
    assert "dernier" in str(excinfo.value)


def test_recipe_render_web_duplique_refuse():
    with pytest.raises(ValidationError) as excinfo:
        _make_minimal_recipe(steps=[
            RecipeStepRenderWeb(kind="render_web"),
            RecipeStepRenderWeb(kind="render_web"),
        ])
    assert "un seul step" in str(excinfo.value)


def test_run_qgis_sans_layer_id_refuse():
    with pytest.raises(ValidationError) as excinfo:
        RecipeStepRunQgis(
            kind="run_qgis", algorithm="catalog_wfs", outputs={},
        )
    assert "layer_id" in str(excinfo.value)


# --- 2. Exécution du moteur ---------------------------------------------


def test_execute_recipe_pure_output_shape():
    recipe = _make_minimal_recipe()
    out = asyncio.run(execute_recipe_pure(
        recipe, {"timestamp": "2026-07-13T10:00:00+02:00"},
    ))
    assert out.scene_manifest["manifest_version"] == "0.3.1"
    assert out.scene_manifest["title"] == "Recipe de test"
    assert out.scene_manifest["produced_at"] == "2026-07-13T10:00:00+02:00"
    # 1 layer stub produit par le step run_qgis.
    assert len(out.scene_manifest["layers"]) == 1
    layer = out.scene_manifest["layers"][0]
    assert layer["id"] == "layer_1"
    assert layer["source"]["type"] == "geojson_path"
    assert layer["source"]["crs"] == "EPSG:4326"
    # Description contient bien le rendu Jinja2 du disclaimer.
    assert "Marseille 4e" in out.scene_manifest["description"]
    assert "RGA" in out.scene_manifest["description"]


def test_provenance_liste_briques_utilisees():
    recipe = _make_minimal_recipe()
    out = asyncio.run(execute_recipe_pure(
        recipe, {"timestamp": "2026-07-13T10:00:00+02:00"},
    ))
    # 1 import global (crs) + 1 include (disclaimer) = 2 refs distinctes.
    assert set(out.briques_used) == {
        "rules_global/crs_wgs84_obligatoire",
        "narrative/disclaimer_rga",
    }
    assert out.provenance["mode"] == "recipe_pure"
    assert out.provenance["recipe_id"] == "r_test"
    # rules_enforced trace la règle globale.
    refs = {r["ref"] for r in out.provenance["rules_enforced"]}
    assert "rules_global/crs_wgs84_obligatoire" in refs


def test_brique_ref_inconnue_leve_recipe_import_error():
    recipe = _make_minimal_recipe(
        imports=[RecipeImport(
            brique_ref="rules_global/n_existe_absolument_pas",
        )],
    )
    with pytest.raises(RecipeImportError) as excinfo:
        asyncio.run(execute_recipe_pure(recipe, {}))
    assert "n_existe_absolument_pas" in str(excinfo.value)


def test_narrative_jinja_rendu_avec_template_context():
    """Le rendu Jinja2 doit interpoler zone_label dans le disclaimer."""
    recipe = _make_minimal_recipe(imports=[])  # pas d'imports globaux
    out = asyncio.run(execute_recipe_pure(
        recipe, {"timestamp": "2026-07-13T10:00:00+02:00"},
    ))
    description = out.scene_manifest["description"]
    assert "Marseille 4e" in description
    # Le pct_ancien_avant_1948 n'est pas dans le context → le bloc {% if %}
    # doit être absent (pas d'exception, juste pas de rendu du pct).
    assert "%" not in description or "12.4" not in description


def test_narrative_jinja_pct_rendu_quand_present():
    recipe = _make_minimal_recipe(
        imports=[],
        steps=[
            RecipeStepIncludeBrique(
                kind="include_brique",
                ref="narrative/disclaimer_rga",
                template_context={
                    "zone_label": "Sete",
                    "pct_ancien_avant_1948": 42.5,
                },
            ),
            RecipeStepRenderWeb(kind="render_web"),
        ],
    )
    out = asyncio.run(execute_recipe_pure(
        recipe, {"timestamp": "2026-07-13T10:00:00+02:00"},
    ))
    description = out.scene_manifest["description"]
    assert "Sete" in description
    assert "42.5" in description


# --- 3. Idempotence stricte du mode recipe_pure --------------------------


def test_idempotence_stricte_deux_runs_meme_output():
    """Deux exécutions successives sur mêmes entrées doivent donner un
    scene_manifest bit-à-bit identique (JSON canonique)."""
    recipe = _make_minimal_recipe()
    ctx = {"timestamp": "2026-07-13T10:00:00+02:00"}
    out1 = asyncio.run(execute_recipe_pure(recipe, ctx))
    out2 = asyncio.run(execute_recipe_pure(recipe, ctx))
    assert to_json_stable(out1.scene_manifest) == to_json_stable(
        out2.scene_manifest
    )
    assert out1.briques_used == out2.briques_used
    assert to_json_stable(out1.provenance) == to_json_stable(out2.provenance)


def test_timestamps_differents_produisent_manifests_differents():
    """Sécurité : sans context.timestamp identique, les runs divergent
    uniquement sur produced_at (jamais sur le reste du manifest)."""
    recipe = _make_minimal_recipe()
    out1 = asyncio.run(execute_recipe_pure(
        recipe, {"timestamp": "2026-07-13T10:00:00+02:00"},
    ))
    out2 = asyncio.run(execute_recipe_pure(
        recipe, {"timestamp": "2026-07-14T11:11:11+02:00"},
    ))
    assert (
        out1.scene_manifest["produced_at"]
        != out2.scene_manifest["produced_at"]
    )
    # Le reste (title, layers, description) reste égal.
    m1 = dict(out1.scene_manifest)
    m2 = dict(out2.scene_manifest)
    m1.pop("produced_at")
    m2.pop("produced_at")
    m1["provenance"] = dict(m1["provenance"])  # copies pour ne pas muter
    m2["provenance"] = dict(m2["provenance"])
    assert m1["title"] == m2["title"]
    assert m1["layers"] == m2["layers"]
    assert m1.get("description") == m2.get("description")


# --- 4. Chargement YAML + exemple canonique ------------------------------


def test_load_recipe_from_yaml_exemple():
    """L'exemple canonique doit se charger sans erreur."""
    assert _EXAMPLE_YAML.exists(), f"Exemple absent : {_EXAMPLE_YAML}"
    recipe = load_recipe_from_yaml(_EXAMPLE_YAML)
    assert recipe.id == "diagnostic_parc_bati_temporel"
    assert recipe.output_kind == "component"
    assert len(recipe.steps) == 4
    # Le premier step est un run_qgis, le dernier un render_web.
    assert isinstance(recipe.steps[0], RecipeStepRunQgis)
    assert isinstance(recipe.steps[-1], RecipeStepRenderWeb)


def test_execute_recipe_pure_sur_exemple_canonique():
    recipe = load_recipe_from_yaml(_EXAMPLE_YAML)
    out = asyncio.run(execute_recipe_pure(
        recipe, {"timestamp": "2026-07-13T10:00:00+02:00"},
    ))
    sm = out.scene_manifest
    # 2 layers stubs (batiments + contour arm).
    assert len(sm["layers"]) == 2
    ids = {layer["id"] for layer in sm["layers"]}
    assert ids == {"batiments_bd_topo", "arrondissement_13204"}
    # Description contient le disclaimer avec Marseille 4e.
    assert "Marseille 4e" in sm["description"]
    # manifest_defaults appliqués (basemap, projection, legend…).
    assert sm["basemap"]["id"] == "plan-ign-v2-gris"
    assert sm["projection"] == "mercator"
    assert sm["zone"]["insee"] == "13204"
    assert sm["legend"]["from_layer"] == "batiments_bd_topo"
    # 3 briques distinctes utilisées.
    assert set(out.briques_used) == {
        "rules_global/crs_wgs84_obligatoire",
        "rules_forbidden/no_hallucination_sources",
        "narrative/disclaimer_rga",
    }


def test_output_kind_assembly_emballe_composant():
    """target=assembly doit produire un manifest d'assembly qui inline
    le composant produit."""
    recipe = _make_minimal_recipe(
        output_kind="assembly",
        steps=[
            RecipeStepRunQgis(
                kind="run_qgis", algorithm="catalog_wfs",
                outputs={"layer_id": "l"},
            ),
            RecipeStepRenderWeb(kind="render_web", target="assembly"),
        ],
    )
    out = asyncio.run(execute_recipe_pure(
        recipe, {"timestamp": "2026-07-13T10:00:00+02:00"},
    ))
    sm = out.scene_manifest
    assert "components" in sm
    assert len(sm["components"]) == 1
    inline = sm["components"][0]["inline"]
    # Le composant inline conserve son manifest_version et ses layers.
    assert inline["manifest_version"] == "0.3.1"
    assert len(inline["layers"]) == 1
    assert out.provenance["output_kind"] == "assembly"
