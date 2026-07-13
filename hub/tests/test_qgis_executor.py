"""Tests du protocol QgisExecutor et de ses implémentations (chantier G4-b-1).

Couvre :
  1. `StubQgisExecutor.execute` sur un step valide → layer avec path stub://
  2. `McpQgisExecutor.execute` sur un step valide → layer avec path
     /data/scene_store/...
  3. Backward-compat : `execute_recipe_pure` sans executor param → stub par
     défaut, comportement identique au POC.
  4. Injection : `execute_recipe_pure(..., executor=McpQgisExecutor())` →
     utilise bien le MCP executor (path scene_store présent).
  5. Idempotence préservée : deux runs avec même executor + même context →
     même sortie JSON canonique.
  6. Protocol conformity : `isinstance(executor, QgisExecutor)` via
     `runtime_checkable` — les deux executors doivent l'implémenter.
  7. Endpoint POST /api/recipes-web/execute?executor=stub → 200.
  8. Endpoint POST /api/recipes-web/execute?executor=mcp → 200 (placeholder).
  9. Endpoint POST /api/recipes-web/execute?executor=invalid → 400.
 10. Régression : le comportement default (sans executor) est bit-à-bit
     identique à un run avec `StubQgisExecutor` explicite.
"""

from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from hub import auth  # noqa: E402
from hub import briques_loader  # noqa: E402
from hub import main as hub_main  # noqa: E402
from hub.recipes_web import (  # noqa: E402
    McpQgisExecutor,
    QgisExecutor,
    RecipeImport,
    RecipeStepIncludeBrique,
    RecipeStepRenderWeb,
    RecipeStepRunQgis,
    RecipeWeb,
    StubQgisExecutor,
    execute_recipe_pure,
)
from hub.recipes_web.engine import to_json_stable  # noqa: E402


# ── Fixtures ─────────────────────────────────────────────────────────────────


@pytest.fixture(autouse=True)
def _reset_briques_cache():
    briques_loader.reload_cache()
    yield
    briques_loader.reload_cache()


def _make_minimal_recipe(**overrides) -> RecipeWeb:
    """Recette minimale valide utilisée par plusieurs tests."""
    base = dict(
        manifest_version="0.3.1",
        id="r_test_executor",
        title="Recipe test executor",
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
                params={"catalog_id": "bdtopo_batiments"},
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


@pytest.fixture
def client():
    async def _fake_user():
        return {"username": "test", "scope": "user"}

    hub_main.app.dependency_overrides[auth.get_current_user] = _fake_user
    with TestClient(hub_main.app) as c:
        yield c
    hub_main.app.dependency_overrides.pop(auth.get_current_user, None)


def _post_execute(client: TestClient, payload: dict, params: dict | None = None):
    return client.post(
        "/api/recipes-web/execute",
        json=payload,
        params=params or {},
        headers={"user-agent": "kube-probe/1.0"},
    )


# ── 1. StubQgisExecutor : layer avec path stub:// ────────────────────────────


def test_stub_executor_produit_path_stub_prefixe():
    step = RecipeStepRunQgis(
        kind="run_qgis",
        algorithm="catalog_wfs",
        outputs={
            "layer_id": "batiments",
            "layer_name": "Bâtiments",
            "classification_field": "date_d_apparition",
        },
    )
    stub = StubQgisExecutor()
    layer = asyncio.run(stub.execute(step, {}))
    assert layer["id"] == "batiments"
    assert layer["name"] == "Bâtiments"
    assert layer["source"]["type"] == "geojson_path"
    assert layer["source"]["path"] == "stub://batiments"
    assert layer["source"]["crs"] == "EPSG:4326"
    # Trace stub explicite du champ de classification.
    assert layer["_stub_classification_field"] == "date_d_apparition"


# ── 2. McpQgisExecutor : layer avec path scene_store ─────────────────────────


def test_mcp_executor_produit_path_scene_store():
    step = RecipeStepRunQgis(
        kind="run_qgis",
        algorithm="catalog_wfs",
        outputs={
            "layer_id": "batiments",
            "layer_name": "Bâtiments",
        },
    )
    mcp = McpQgisExecutor(mcp_url="http://mcp.test:8090")
    layer = asyncio.run(mcp.execute(step, {}))
    assert layer["source"]["type"] == "geojson_path"
    # Path par défaut = /data/scene_store, pas stub://.
    assert layer["source"]["path"] == "/data/scene_store/batiments.geojson"
    assert not layer["source"]["path"].startswith("stub://")


def test_mcp_executor_respecte_scene_store_dir_du_context():
    step = RecipeStepRunQgis(
        kind="run_qgis",
        algorithm="catalog_wfs",
        outputs={"layer_id": "l"},
    )
    mcp = McpQgisExecutor()
    layer = asyncio.run(mcp.execute(step, {
        "scene_store_dir": "/data/scene_store/session_42",
        "study_zone_hint": "Marseille 4e",
    }))
    assert layer["source"]["path"] == (
        "/data/scene_store/session_42/l.geojson"
    )
    assert layer["_mcp_zone_hint"] == "Marseille 4e"


# ── 3. Backward-compat : execute_recipe_pure sans executor ───────────────────


def test_execute_recipe_pure_backward_compat_default_stub():
    """Sans executor injecté → StubQgisExecutor par défaut, layer stub://."""
    recipe = _make_minimal_recipe()
    out = asyncio.run(execute_recipe_pure(
        recipe, {"timestamp": "2026-07-13T10:00:00+02:00"},
    ))
    assert len(out.scene_manifest["layers"]) == 1
    layer = out.scene_manifest["layers"][0]
    assert layer["source"]["path"] == "stub://layer_1"


# ── 4. Injection d'un executor MCP ───────────────────────────────────────────


def test_execute_recipe_pure_avec_mcp_executor_injecte():
    recipe = _make_minimal_recipe()
    ctx = {"timestamp": "2026-07-13T10:00:00+02:00"}
    out = asyncio.run(execute_recipe_pure(
        recipe, ctx, executor=McpQgisExecutor(),
    ))
    layer = out.scene_manifest["layers"][0]
    assert layer["source"]["path"] == "/data/scene_store/layer_1.geojson"
    # Traces MCP présentes (via params.catalog_id → _mcp_datasource).
    assert layer.get("_mcp_datasource") == "bdtopo_batiments"


# ── 5. Idempotence stricte préservée avec injection ──────────────────────────


def test_idempotence_preservee_avec_executor_injecte():
    recipe = _make_minimal_recipe()
    ctx = {"timestamp": "2026-07-13T10:00:00+02:00"}
    out1 = asyncio.run(execute_recipe_pure(
        recipe, ctx, executor=McpQgisExecutor(),
    ))
    out2 = asyncio.run(execute_recipe_pure(
        recipe, ctx, executor=McpQgisExecutor(),
    ))
    assert to_json_stable(out1.scene_manifest) == to_json_stable(
        out2.scene_manifest
    )
    assert to_json_stable(out1.provenance) == to_json_stable(out2.provenance)


# ── 6. Protocol conformity (runtime_checkable) ───────────────────────────────


def test_les_deux_executors_implementent_le_protocol():
    stub = StubQgisExecutor()
    mcp = McpQgisExecutor()
    # QgisExecutor est décoré @runtime_checkable → isinstance(vrai) attendu.
    assert isinstance(stub, QgisExecutor)
    assert isinstance(mcp, QgisExecutor)
    # Un objet sans execute() doit être rejeté.
    class _NotAnExecutor:
        pass
    assert not isinstance(_NotAnExecutor(), QgisExecutor)


def test_executors_ont_une_methode_execute_async():
    """execute doit être une coroutine sur les deux implémentations."""
    step = RecipeStepRunQgis(
        kind="run_qgis",
        algorithm="catalog_wfs",
        outputs={"layer_id": "x"},
    )
    for executor in (StubQgisExecutor(), McpQgisExecutor()):
        coro = executor.execute(step, {})
        assert asyncio.iscoroutine(coro)
        result = asyncio.run(coro)
        assert isinstance(result, dict)
        assert "source" in result


# ── 7. Endpoint : ?executor=stub → 200 ───────────────────────────────────────


def test_endpoint_executor_stub_returns_200(client):
    resp = _post_execute(
        client,
        {"recipe_id": "diagnostic_parc_bati_temporel"},
        params={"executor": "stub"},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    # Layers stubs : path préfixé stub://.
    for layer in body["scene_manifest"]["layers"]:
        assert layer["source"]["path"].startswith("stub://"), layer


# ── 8. Endpoint : ?executor=mcp → 200 (placeholder) ──────────────────────────


def test_endpoint_executor_mcp_returns_200(client):
    resp = _post_execute(
        client,
        {"recipe_id": "diagnostic_parc_bati_temporel"},
        params={"executor": "mcp"},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    # Layers MCP : path scene_store, pas stub://.
    for layer in body["scene_manifest"]["layers"]:
        path = layer["source"]["path"]
        assert not path.startswith("stub://"), path
        assert path.startswith("/data/scene_store/"), path


# ── 9. Endpoint : ?executor=invalid → 400 ────────────────────────────────────


def test_endpoint_executor_invalid_returns_400(client):
    resp = _post_execute(
        client,
        {"recipe_id": "diagnostic_parc_bati_temporel"},
        params={"executor": "gibberish"},
    )
    assert resp.status_code == 400, resp.text
    detail = resp.json().get("detail", "").lower()
    assert "executor" in detail and "gibberish" in detail


# ── 10. Régression : default == StubQgisExecutor explicite ───────────────────


def test_default_executor_identique_a_stub_explicite():
    """Un run sans executor doit être bit-à-bit identique à un run avec
    StubQgisExecutor injecté explicitement (backward-compat totale)."""
    recipe = _make_minimal_recipe()
    ctx = {"timestamp": "2026-07-13T10:00:00+02:00"}
    out_default = asyncio.run(execute_recipe_pure(recipe, ctx))
    out_explicit = asyncio.run(execute_recipe_pure(
        recipe, ctx, executor=StubQgisExecutor(),
    ))
    assert to_json_stable(out_default.scene_manifest) == to_json_stable(
        out_explicit.scene_manifest
    )
    assert to_json_stable(out_default.provenance) == to_json_stable(
        out_explicit.provenance
    )


if __name__ == "__main__":
    ns = dict(globals())
    for name, fn in ns.items():
        if name.startswith("test_") and callable(fn):
            print(f"→ {name}")
    print("Utiliser pytest pour executer.")
