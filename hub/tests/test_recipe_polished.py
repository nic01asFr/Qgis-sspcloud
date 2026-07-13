"""Tests du mode `recipe_polished` (chantier G4-b-3b, Sprint V0.3).

Couvre :
  1. `polish_narrative` avec StubLlmClient : modifie bien le content.
  2. Provenance diff contient before + after + polish_llm_provenance.
  3. Pas de narrative_text dans manifest → retour inchange.
  4. LLM timeout → fail-soft (garde original + log).
  5. LLM error → fail-soft (garde original + log).
  6. Max blocs > 10 → skip surplus + log.
  7. `execute_recipe_polished` avec llm_client=None → identique a
     execute_recipe_pure (aux cles mode/polish pres, informatives).
  8. `execute_recipe_polished` avec StubLlmClient → provenance
     ["mode"] = recipe_polished.
  9. Endpoint POST /api/recipes-web/execute?mode=polished retourne un
     output avec polish provenance (sans OPENAI_API_KEY → 400 explicite).
"""

from __future__ import annotations

import asyncio
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
    FailingLlmClient,
    RecipeImport,
    RecipeStepIncludeBrique,
    RecipeStepRenderWeb,
    RecipeWeb,
    StubLlmClient,
    execute_recipe_polished,
    execute_recipe_pure,
    polish_narrative,
)
from hub.recipes_web.polish import MAX_BLOCKS_PER_RUN  # noqa: E402


@pytest.fixture(autouse=True)
def _reset_briques_cache():
    briques_loader.reload_cache()
    yield
    briques_loader.reload_cache()


def _make_manifest_with_narrative(
    n_blocks: int = 1,
    container_key: str = "components",
) -> dict:
    blocks = []
    for i in range(n_blocks):
        blocks.append({
            "id": f"nt_{i}",
            "kind": "narrative_text",
            "content": (
                f"le paragraphe numero {i} decrit la commune "
                f"de Marseille 4e. la population etait de 47000 habitants."
            ),
        })
    return {
        "$schema": "https://cerema.github.io/geo-components/schemas/scene_manifest/0.3.1.json",
        "manifest_version": "0.3.1",
        "title": "manifest test",
        container_key: blocks,
    }


def _make_minimal_recipe() -> RecipeWeb:
    return RecipeWeb(
        manifest_version="0.3.1",
        id="r_polished_test",
        title="Recipe test polished",
        imports=[
            RecipeImport(brique_ref="rules_global/crs_wgs84_obligatoire"),
        ],
        steps=[
            RecipeStepIncludeBrique(
                kind="include_brique",
                id="disclaimer",
                ref="narrative/disclaimer_rga",
                template_context={"zone_label": "Marseille 4e"},
            ),
            RecipeStepRenderWeb(kind="render_web", id="render"),
        ],
    )


# ── 1. StubLlmClient modifie bien le content ────────────────────────────────


def test_polish_narrative_with_stub_changes_content():
    manifest = _make_manifest_with_narrative(n_blocks=1)
    original = manifest["components"][0]["content"]

    polished_manifest, provenance = asyncio.run(
        polish_narrative(manifest, StubLlmClient())
    )
    new_content = polished_manifest["components"][0]["content"]
    assert new_content != original
    assert new_content[0].isupper()
    assert manifest["components"][0]["content"] == original  # non-mute
    assert provenance["polish_llm_provenance"]["blocks_polished"] == 1


# ── 2. Provenance diff contient before + after + polish_llm_provenance ─────


def test_polish_provenance_shape():
    manifest = _make_manifest_with_narrative(n_blocks=2)
    _, provenance = asyncio.run(polish_narrative(manifest, StubLlmClient()))
    assert "before" in provenance
    assert "after" in provenance
    assert "polish_llm_provenance" in provenance
    llm_prov = provenance["polish_llm_provenance"]
    assert llm_prov["blocks_polished"] == 2
    assert llm_prov["blocks_failed"] == 0
    assert llm_prov["blocks_skipped"] == 0
    assert llm_prov["duration_ms_total"] >= 0
    assert len(llm_prov["per_block"]) == 2
    assert set(provenance["before"].keys()) == {"nt_0", "nt_1"}
    assert set(provenance["after"].keys()) == {"nt_0", "nt_1"}


# ── 3. Pas de narrative_text → retour inchange ─────────────────────────────


def test_polish_no_narrative_blocks_returns_unchanged():
    manifest = {
        "manifest_version": "0.3.1",
        "title": "no narrative",
        "layers": [{"layer_id": "x"}],
    }
    polished, provenance = asyncio.run(polish_narrative(manifest, StubLlmClient()))
    assert polished == manifest
    llm_prov = provenance["polish_llm_provenance"]
    assert llm_prov["blocks_polished"] == 0
    assert llm_prov["blocks_failed"] == 0
    assert provenance["before"] == {}
    assert provenance["after"] == {}


# ── 4. LLM timeout → fail-soft ─────────────────────────────────────────────


def test_polish_llm_timeout_falls_back_soft():
    manifest = _make_manifest_with_narrative(n_blocks=1)
    original = manifest["components"][0]["content"]
    client = FailingLlmClient(mode="timeout")

    polished, provenance = asyncio.run(polish_narrative(manifest, client))
    assert polished["components"][0]["content"] == original
    assert provenance["after"]["nt_0"] == original
    llm_prov = provenance["polish_llm_provenance"]
    assert llm_prov["blocks_failed"] == 1
    assert llm_prov["blocks_polished"] == 0
    entry = llm_prov["per_block"][0]
    assert entry["polish_ok"] is False
    assert "reason" in entry


# ── 5. LLM error → fail-soft ───────────────────────────────────────────────


def test_polish_llm_error_falls_back_soft():
    manifest = _make_manifest_with_narrative(n_blocks=1)
    original = manifest["components"][0]["content"]
    client = FailingLlmClient(mode="error")

    polished, provenance = asyncio.run(polish_narrative(manifest, client))
    assert polished["components"][0]["content"] == original
    assert provenance["after"]["nt_0"] == original
    llm_prov = provenance["polish_llm_provenance"]
    assert llm_prov["blocks_failed"] == 1
    entry = llm_prov["per_block"][0]
    assert entry["polish_ok"] is False
    assert "RuntimeError" in entry["reason"]


# ── 6. Max blocs > 10 → skip surplus + log ─────────────────────────────────


def test_polish_skips_excess_blocks_beyond_max():
    n = MAX_BLOCKS_PER_RUN + 3
    manifest = _make_manifest_with_narrative(n_blocks=n)
    polished, provenance = asyncio.run(polish_narrative(manifest, StubLlmClient()))
    llm_prov = provenance["polish_llm_provenance"]
    assert llm_prov["blocks_polished"] == MAX_BLOCKS_PER_RUN
    assert llm_prov["blocks_skipped"] == 3
    for i in range(MAX_BLOCKS_PER_RUN, n):
        assert (
            polished["components"][i]["content"]
            == manifest["components"][i]["content"]
        )
        entry = next(
            e for e in llm_prov["per_block"]
            if e["block_id"] == f"nt_{i}"
        )
        assert entry["polish_ok"] is False
        assert "max_blocks_per_run" in entry.get("reason", "")


# ── 7. execute_recipe_polished(llm_client=None) ≡ execute_recipe_pure ──────


def test_execute_polished_without_llm_matches_pure_data():
    recipe = _make_minimal_recipe()
    ctx = {"timestamp": "2026-07-13T10:00:00+00:00"}

    out_pure = asyncio.run(execute_recipe_pure(recipe, ctx))
    out_polished = asyncio.run(execute_recipe_polished(recipe, ctx, llm_client=None))

    assert out_pure.scene_manifest == out_polished.scene_manifest
    assert out_pure.briques_used == out_polished.briques_used
    assert out_polished.provenance["mode"] == "recipe_polished"
    assert out_pure.provenance["mode"] == "recipe_pure"
    polish_prov = out_polished.provenance.get("polish") or {}
    llm_prov = polish_prov.get("polish_llm_provenance", {})
    assert llm_prov.get("reason") == "no_llm_client_provided"


# ── 8. execute_recipe_polished(StubLlmClient) → mode=recipe_polished ──────


def test_execute_polished_with_stub_marks_mode_and_polish_provenance():
    recipe = _make_minimal_recipe()
    ctx = {"timestamp": "2026-07-13T10:00:00+00:00"}
    out = asyncio.run(
        execute_recipe_polished(recipe, ctx, llm_client=StubLlmClient())
    )
    assert out.provenance["mode"] == "recipe_polished"
    assert "polish" in out.provenance
    polish_prov = out.provenance["polish"]
    assert "before" in polish_prov
    assert "after" in polish_prov
    assert "polish_llm_provenance" in polish_prov


def test_execute_polished_actually_polishes_when_narrative_blocks_present():
    recipe = _make_minimal_recipe()
    ctx = {"timestamp": "2026-07-13T10:00:00+00:00"}
    out = asyncio.run(execute_recipe_pure(recipe, ctx))

    out.scene_manifest["components"] = [
        {"id": "b1", "kind": "narrative_text",
         "content": "le paragraphe simule."},
    ]
    original = out.scene_manifest["components"][0]["content"]

    polished_manifest, prov = asyncio.run(
        polish_narrative(out.scene_manifest, StubLlmClient())
    )
    assert polished_manifest["components"][0]["content"] != original
    assert prov["polish_llm_provenance"]["blocks_polished"] == 1


# ── 9. Endpoint HTTP mode=polished ──────────────────────────────────────────


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


def test_endpoint_mode_polished_without_openai_env_returns_400(client, monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("OPENAI_BASE_URL", raising=False)
    resp = _post_execute(
        client,
        {"recipe_id": "diagnostic_parc_bati_temporel"},
        params={"mode": "polished"},
    )
    assert resp.status_code == 400, resp.text
    detail = resp.json().get("detail", "").lower()
    assert "polished" in detail
    assert "openai_api_key" in detail or "llm" in detail


def test_endpoint_mode_pure_still_default_backward_compat(client):
    resp = _post_execute(
        client,
        {"recipe_id": "diagnostic_parc_bati_temporel"},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["provenance"]["mode"] == "recipe_pure"
    assert "polish" not in body["provenance"]


def test_endpoint_mode_unknown_returns_400(client):
    resp = _post_execute(
        client,
        {"recipe_id": "diagnostic_parc_bati_temporel"},
        params={"mode": "gpt5"},
    )
    assert resp.status_code == 400, resp.text
    detail = resp.json().get("detail", "").lower()
    assert "mode" in detail
