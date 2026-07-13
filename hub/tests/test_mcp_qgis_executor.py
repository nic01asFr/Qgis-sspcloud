"""Tests du vrai McpQgisExecutor JSON-RPC (chantier G4-b-3a).

Couvre le mode ``live=True`` avec mock strict de ``httpx.AsyncClient``.
Aucun test unitaire ne dépend d'un serveur BigQgisMCP réel.

Tests :
  1. `_mcp_call` OK → parse `body["result"]`.
  2. `_mcp_call` JSON-RPC error → RecipeStepError avec message + step_tag.
  3. `_mcp_call` retry : 500 puis 200 → OK.
  4. `_mcp_call` retry épuisé : 500 x3 → RecipeStepError.
  5. `_mcp_call` timeout → RecipeStepError après retries.
  6. Header Authorization Bearer si mcp_auth défini, absent sinon.
  7. Compteur JSON-RPC incrémental (id 1, 2, 3...).
  8. `execute` live sans zone_hint → pas d'appel set_study_zone.
  9. `execute` live sans datasource → pas d'appel smart_load.
 10. `execute` live avec algo Processing ('native:buffer') → appelle
     run_processing.
 11. `execute` live avec algo catalog ('catalog_wfs') → skip run_processing.
 12. `execute` live retourne bien un layer V0.3.x avec path exporté.
 13. Retrocompat : `execute` avec live=False (défaut) ne fait aucun HTTP.
 14. Marker mcp_live : test optionnel qui skip sans env MCP_LIVE_URL.
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

import httpx  # noqa: E402

from hub.recipes_web import McpQgisExecutor, RecipeStepRunQgis  # noqa: E402
from hub.recipes_web.engine import RecipeStepError  # noqa: E402


# ── Helpers ──────────────────────────────────────────────────────────────────


def _mock_response(
    status_code: int = 200,
    json_body: dict[str, Any] | None = None,
    text: str = "",
) -> MagicMock:
    """Fabrique un mock de httpx.Response (partiel — juste ce qu'on utilise)."""
    resp = MagicMock(spec=httpx.Response)
    resp.status_code = status_code
    resp.text = text or json.dumps(json_body or {})
    if json_body is not None:
        resp.json = MagicMock(return_value=json_body)
    else:
        resp.json = MagicMock(side_effect=ValueError("not json"))
    return resp


class _FakeAsyncClient:
    """Contexte async simulant httpx.AsyncClient — collectionne les appels
    POST et renvoie des réponses scriptées.

    Le constructeur reçoit une liste de réponses ou d'exceptions à lever
    dans l'ordre d'arrivée des appels ``post``.
    """

    def __init__(self, responses: list[Any]):
        self._responses = list(responses)
        self.calls: list[dict[str, Any]] = []

    async def __aenter__(self) -> "_FakeAsyncClient":
        return self

    async def __aexit__(self, exc_type, exc, tb) -> None:
        return None

    async def post(
        self, url: str, json: dict[str, Any] | None = None,
        headers: dict[str, str] | None = None,
        **kwargs: Any,
    ) -> Any:
        self.calls.append({"url": url, "json": json, "headers": headers})
        if not self._responses:
            raise AssertionError("Aucune réponse scriptée restante")
        item = self._responses.pop(0)
        if isinstance(item, Exception):
            raise item
        return item


def _patch_client_factory(fake: _FakeAsyncClient):
    """Remplace httpx.AsyncClient par une factory qui renvoie ``fake``
    quels que soient les kwargs (timeout, headers, ...).
    """
    def _factory(*args: Any, **kwargs: Any) -> _FakeAsyncClient:
        return fake
    return patch("httpx.AsyncClient", _factory)


def _step(
    layer_id: str = "layer_1",
    algorithm: str = "catalog_wfs",
    params: dict[str, Any] | None = None,
    outputs_extra: dict[str, Any] | None = None,
) -> RecipeStepRunQgis:
    outputs = {"layer_id": layer_id, "geometry_type": "polygon"}
    if outputs_extra:
        outputs.update(outputs_extra)
    return RecipeStepRunQgis(
        kind="run_qgis",
        id="s1",
        algorithm=algorithm,
        params=params or {},
        outputs=outputs,
    )


# ── 1. _mcp_call OK ──────────────────────────────────────────────────────────


def test_mcp_call_ok_returns_result():
    exec_ = McpQgisExecutor(mcp_url="http://mcp.test:8090", live=True)
    fake = _FakeAsyncClient([
        _mock_response(200, {"jsonrpc": "2.0", "id": 1, "result": {"ok": 1}}),
    ])
    with _patch_client_factory(fake):
        result = asyncio.run(
            exec_._mcp_call("ping", {"foo": "bar"}, step_tag="tag_a")
        )
    assert result == {"ok": 1}
    assert len(fake.calls) == 1
    body = fake.calls[0]["json"]
    assert body["jsonrpc"] == "2.0"
    assert body["method"] == "ping"
    assert body["params"] == {"foo": "bar"}
    assert body["id"] == 1
    assert fake.calls[0]["url"] == "http://mcp.test:8090/rpc"


# ── 2. _mcp_call JSON-RPC error ──────────────────────────────────────────────


def test_mcp_call_json_rpc_error_raises_recipe_step_error():
    exec_ = McpQgisExecutor(mcp_url="http://mcp.test:8090", live=True)
    fake = _FakeAsyncClient([
        _mock_response(200, {
            "jsonrpc": "2.0", "id": 1,
            "error": {"code": -32000, "message": "boom fantaisie"},
        }),
    ])
    with _patch_client_factory(fake):
        with pytest.raises(RecipeStepError) as exc_info:
            asyncio.run(
                exec_._mcp_call(
                    "smart_load", {"catalog_id": "x"}, step_tag="load_batiments"
                )
            )
    msg = str(exc_info.value)
    assert "load_batiments" in msg
    assert "smart_load" in msg
    assert "boom fantaisie" in msg
    assert "-32000" in msg


# ── 3. Retry : 500 puis 200 → OK ─────────────────────────────────────────────


def test_mcp_call_retries_on_500_then_succeeds(monkeypatch):
    # Patch asyncio.sleep pour ne pas ralentir les tests.
    async def _instant_sleep(_seconds: float) -> None:
        return None

    monkeypatch.setattr(
        "hub.recipes_web.qgis_executor.asyncio.sleep", _instant_sleep
    )

    exec_ = McpQgisExecutor(mcp_url="http://mcp.test:8090", live=True)
    fake = _FakeAsyncClient([
        _mock_response(500, text="upstream down"),
        _mock_response(200, {"jsonrpc": "2.0", "id": 1, "result": "ok"}),
    ])
    with _patch_client_factory(fake):
        result = asyncio.run(
            exec_._mcp_call("ping", {}, step_tag="s")
        )
    assert result == "ok"
    assert len(fake.calls) == 2


# ── 4. Retry épuisé : 500 x3 ─────────────────────────────────────────────────


def test_mcp_call_max_retries_exhausted(monkeypatch):
    async def _instant_sleep(_seconds: float) -> None:
        return None

    monkeypatch.setattr(
        "hub.recipes_web.qgis_executor.asyncio.sleep", _instant_sleep
    )

    exec_ = McpQgisExecutor(mcp_url="http://mcp.test:8090", live=True)
    fake = _FakeAsyncClient([
        _mock_response(502, text="bad gateway"),
        _mock_response(503, text="unavailable"),
        _mock_response(500, text="internal"),
    ])
    with _patch_client_factory(fake):
        with pytest.raises(RecipeStepError) as exc_info:
            asyncio.run(
                exec_._mcp_call("smart_load", {}, step_tag="load_x")
            )
    assert "load_x" in str(exc_info.value)
    assert "smart_load" in str(exc_info.value)
    # 3 tentatives = 1 + 2 retries.
    assert len(fake.calls) == 3


# ── 5. Timeout ───────────────────────────────────────────────────────────────


def test_mcp_call_timeout_raises_after_retries(monkeypatch):
    async def _instant_sleep(_seconds: float) -> None:
        return None

    monkeypatch.setattr(
        "hub.recipes_web.qgis_executor.asyncio.sleep", _instant_sleep
    )

    exec_ = McpQgisExecutor(
        mcp_url="http://mcp.test:8090", live=True, timeout=0.001
    )
    fake = _FakeAsyncClient([
        httpx.TimeoutException("timeout 1"),
        httpx.TimeoutException("timeout 2"),
        httpx.TimeoutException("timeout 3"),
    ])
    with _patch_client_factory(fake):
        with pytest.raises(RecipeStepError) as exc_info:
            asyncio.run(exec_._mcp_call("ping", {}, step_tag="tag_z"))
    msg = str(exc_info.value)
    assert "tag_z" in msg
    assert "timeout" in msg.lower()


# ── 6. Header Bearer présent / absent ────────────────────────────────────────


def test_mcp_call_bearer_header_when_auth_defined():
    exec_ = McpQgisExecutor(
        mcp_url="http://mcp.test:8090", mcp_auth="secret-token", live=True,
    )
    fake = _FakeAsyncClient([
        _mock_response(200, {"jsonrpc": "2.0", "id": 1, "result": None}),
    ])
    with _patch_client_factory(fake):
        asyncio.run(exec_._mcp_call("ping", {}, step_tag="s"))
    headers = fake.calls[0]["headers"] or {}
    assert headers.get("Authorization") == "Bearer secret-token"


def test_mcp_call_no_bearer_header_when_auth_none():
    exec_ = McpQgisExecutor(
        mcp_url="http://mcp.test:8090", mcp_auth=None, live=True,
    )
    fake = _FakeAsyncClient([
        _mock_response(200, {"jsonrpc": "2.0", "id": 1, "result": None}),
    ])
    with _patch_client_factory(fake):
        asyncio.run(exec_._mcp_call("ping", {}, step_tag="s"))
    headers = fake.calls[0]["headers"] or {}
    assert "Authorization" not in headers


# ── 7. Compteur JSON-RPC incrémental ─────────────────────────────────────────


def test_json_rpc_id_incremental():
    exec_ = McpQgisExecutor(live=True)
    ids = [exec_._next_jsonrpc_id() for _ in range(5)]
    assert ids == [1, 2, 3, 4, 5]


def test_mcp_call_id_increases_across_calls():
    exec_ = McpQgisExecutor(mcp_url="http://mcp.test:8090", live=True)
    fake = _FakeAsyncClient([
        _mock_response(200, {"jsonrpc": "2.0", "id": 1, "result": "a"}),
        _mock_response(200, {"jsonrpc": "2.0", "id": 2, "result": "b"}),
    ])
    with _patch_client_factory(fake):
        asyncio.run(exec_._mcp_call("m1", {}, step_tag="s"))
        asyncio.run(exec_._mcp_call("m2", {}, step_tag="s"))
    assert fake.calls[0]["json"]["id"] == 1
    assert fake.calls[1]["json"]["id"] == 2


# ── 8. execute sans zone_hint → pas de set_study_zone ────────────────────────


def test_execute_live_skips_set_study_zone_without_hint():
    exec_ = McpQgisExecutor(mcp_url="http://mcp.test:8090", live=True)
    step = _step(
        algorithm="catalog_wfs",
        outputs_extra={"datasource": "bdtopo_batiments"},
    )
    fake = _FakeAsyncClient([
        # smart_load
        _mock_response(200, {"jsonrpc": "2.0", "id": 1, "result": "loaded"}),
        # export_layer
        _mock_response(200, {
            "jsonrpc": "2.0", "id": 2,
            "result": {"path": "/data/scene_store/layer_1.geojson"},
        }),
    ])
    with _patch_client_factory(fake):
        layer = asyncio.run(exec_.execute(step, {"timestamp": "2026-07-14"}))
    methods = [c["json"]["method"] for c in fake.calls]
    assert "set_study_zone" not in methods
    assert methods == ["smart_load", "export_layer"]
    assert layer["source"]["path"] == "/data/scene_store/layer_1.geojson"


# ── 9. execute sans datasource → pas de smart_load ───────────────────────────


def test_execute_live_skips_smart_load_without_datasource():
    exec_ = McpQgisExecutor(mcp_url="http://mcp.test:8090", live=True)
    step = _step(algorithm="catalog_wfs")  # pas de catalog_id ni datasource
    fake = _FakeAsyncClient([
        # export_layer seul
        _mock_response(200, {"jsonrpc": "2.0", "id": 1, "result": None}),
    ])
    with _patch_client_factory(fake):
        asyncio.run(exec_.execute(step, {"timestamp": "2026-07-14"}))
    methods = [c["json"]["method"] for c in fake.calls]
    assert "smart_load" not in methods
    assert methods == ["export_layer"]


# ── 10. execute avec algo Processing → run_processing ────────────────────────


def test_execute_live_with_processing_algo_calls_run_processing():
    exec_ = McpQgisExecutor(mcp_url="http://mcp.test:8090", live=True)
    step = _step(
        algorithm="native:buffer",
        params={"distance": 10},
        outputs_extra={"datasource": "bdtopo_batiments"},
    )
    fake = _FakeAsyncClient([
        _mock_response(200, {"jsonrpc": "2.0", "id": 1, "result": None}),  # set_study_zone
        _mock_response(200, {"jsonrpc": "2.0", "id": 2, "result": None}),  # smart_load
        _mock_response(200, {"jsonrpc": "2.0", "id": 3, "result": None}),  # run_processing
        _mock_response(200, {"jsonrpc": "2.0", "id": 4, "result": None}),  # export_layer
    ])
    ctx = {"timestamp": "2026-07-14", "study_zone_hint": "Marseille 4e"}
    with _patch_client_factory(fake):
        asyncio.run(exec_.execute(step, ctx))
    methods = [c["json"]["method"] for c in fake.calls]
    assert methods == [
        "set_study_zone", "smart_load", "run_processing", "export_layer",
    ]
    # run_processing params passés correctement.
    rp_params = fake.calls[2]["json"]["params"]
    assert rp_params["algo"] == "native:buffer"
    assert rp_params["params"] == {"distance": 10}


# ── 11. execute avec algo catalog → skip run_processing ──────────────────────


def test_execute_live_with_catalog_algo_skips_run_processing():
    exec_ = McpQgisExecutor(mcp_url="http://mcp.test:8090", live=True)
    step = _step(
        algorithm="catalog_wfs",  # pas de ':' → pas Processing
        outputs_extra={"datasource": "bdtopo_batiments"},
    )
    fake = _FakeAsyncClient([
        _mock_response(200, {"jsonrpc": "2.0", "id": 1, "result": None}),
        _mock_response(200, {"jsonrpc": "2.0", "id": 2, "result": None}),
    ])
    with _patch_client_factory(fake):
        asyncio.run(exec_.execute(step, {"timestamp": "2026-07-14"}))
    methods = [c["json"]["method"] for c in fake.calls]
    assert "run_processing" not in methods


# ── 12. execute retourne layer V0.3.x avec path exporté ──────────────────────


def test_execute_live_returns_layer_with_exported_path():
    exec_ = McpQgisExecutor(mcp_url="http://mcp.test:8090", live=True)
    step = _step(
        outputs_extra={
            "datasource": "bdtopo_batiments",
            "classification_field": "date_d_apparition",
            "layer_name": "Bâtiments",
        },
    )
    fake = _FakeAsyncClient([
        _mock_response(200, {"jsonrpc": "2.0", "id": 1, "result": None}),
        _mock_response(200, {
            "jsonrpc": "2.0", "id": 2,
            "result": {"path": "/data/scene_store/sess42/layer_1.geojson"},
        }),
    ])
    ctx = {
        "timestamp": "2026-07-14T10:00:00Z",
        "scene_store_dir": "/data/scene_store/sess42",
    }
    with _patch_client_factory(fake):
        layer = asyncio.run(exec_.execute(step, ctx))
    assert layer["id"] == "layer_1"
    assert layer["name"] == "Bâtiments"
    assert layer["source"]["type"] == "geojson_path"
    assert layer["source"]["path"] == (
        "/data/scene_store/sess42/layer_1.geojson"
    )
    # Classification graduated conservée.
    assert layer["style"]["classification"]["color"]["mode"] == "graduated"
    assert layer["_mcp_datasource"] == "bdtopo_batiments"


# ── 13. Retrocompat : execute avec live=False ne fait aucun HTTP ─────────────


def test_execute_placeholder_mode_no_http_calls():
    """En mode ``live=False`` (défaut), aucun appel HTTP ne doit être fait.

    Vital pour la retrocompat avec test_qgis_executor.py qui instancie
    McpQgisExecutor() sans mock httpx.
    """
    exec_ = McpQgisExecutor(mcp_url="http://mcp.test:8090")  # live=False
    step = _step(
        outputs_extra={"datasource": "bdtopo_batiments"},
    )
    fake = _FakeAsyncClient([])  # aucune réponse scriptée
    with _patch_client_factory(fake):
        layer = asyncio.run(exec_.execute(step, {"timestamp": "2026-07-14"}))
    assert fake.calls == []
    assert layer["source"]["path"] == "/data/scene_store/layer_1.geojson"


# ── 14. Marker mcp_live : test optionnel skip en CI ──────────────────────────


@pytest.mark.mcp_live
@pytest.mark.skipif(
    not os.environ.get("MCP_LIVE_URL"),
    reason="Test live : nécessite MCP_LIVE_URL vers un BigQgisMCP réel",
)
def test_mcp_live_integration_ping():
    """Test d'intégration contre un BigQgisMCP réel — skip si MCP_LIVE_URL
    n'est pas défini. Utile pour valider un déploiement.
    """
    exec_ = McpQgisExecutor(
        mcp_url=os.environ["MCP_LIVE_URL"],
        mcp_auth=os.environ.get("MCP_LIVE_AUTH"),
        live=True,
    )
    # Ping minimaliste — les tests fonctionnels complets (recipe canonique
    # rejouée) sont dans le prochain chantier G4-b-3a-live.
    result = asyncio.run(
        exec_._mcp_call("ping", {}, step_tag="live_ping")
    )
    # Présence vérifiée par pytest lui-même (assert-libre : la moindre
    # exception fait échouer). On accepte tout résultat sérialisable.
    assert result is None or result is not None


if __name__ == "__main__":
    ns = dict(globals())
    for name, fn in ns.items():
        if name.startswith("test_") and callable(fn):
            print(f"→ {name}")
    print("Utiliser pytest pour executer.")
