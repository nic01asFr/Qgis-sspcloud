"""API mémoire user : sections markdown + insights."""

from __future__ import annotations

import asyncio
import os
import sys
import tempfile
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

_TMP_DIR = tempfile.mkdtemp(prefix="qgis_mem_api_")
os.environ["DATA_DIR"] = _TMP_DIR
os.environ["HUB_API_KEY"] = "test-hub-key-mem"
os.environ.setdefault("ONYXIA_USER", "test-user")

if "sqlite_vec" not in sys.modules:
    _vec_stub = type(sys)("sqlite_vec")
    _vec_stub.load = lambda *a, **k: None  # type: ignore[attr-defined]
    sys.modules["sqlite_vec"] = _vec_stub

from fastapi.testclient import TestClient  # noqa: E402

from agent import memory  # noqa: E402
from agent.main import _AGENT_INTER_POD_ROUTES, app  # noqa: E402

memory._DATA_DIR = Path(_TMP_DIR)
memory._DB_PATH = Path(_TMP_DIR) / "memory_api.db"

_AUTH = {"Authorization": f"Bearer {os.environ['HUB_API_KEY']}"}


def _run(coro):
    return asyncio.get_event_loop().run_until_complete(coro)


def _client() -> TestClient:
    _run(memory.init())
    return TestClient(app)


def test_user_routes_whitelist_inter_pod() -> None:
    assert "/user" in _AGENT_INTER_POD_ROUTES


def test_get_memory_schema_et_sections_vides() -> None:
    client = _client()
    r = client.get("/user/memory", headers=_AUTH)
    assert r.status_code == 200
    data = r.json()
    keys = [s["key"] for s in data["schema"]]
    assert keys == ["identity", "zones", "data", "carto", "methods", "notes"]
    assert data["sections"]["identity"] == ""
    assert data["markdown"] == ""


def test_patch_memory_section_persiste_et_injecte_contexte() -> None:
    client = _client()
    r = client.patch(
        "/user/memory",
        json={"key": "identity", "content": "Chargé d'études CEREMA"},
        headers=_AUTH,
    )
    assert r.status_code == 200
    assert r.json()["saved"] is True

    r2 = client.get("/user/memory", headers=_AUTH)
    assert r2.json()["sections"]["identity"] == "Chargé d'études CEREMA"
    assert "Chargé d'études CEREMA" in r2.json()["markdown"]

    ctx = _run(memory.build_context_summary("user", "s1", "standard"))
    assert "Chargé d'études CEREMA" in ctx


def test_patch_memory_section_invalide_400() -> None:
    client = _client()
    r = client.patch(
        "/user/memory",
        json={"key": "inexistant", "content": "x"},
        headers=_AUTH,
    )
    assert r.status_code == 400


def test_insights_crud() -> None:
    client = _client()
    r = client.post(
        "/user/insights",
        json={"key": "zone_pref", "value": "Var littoral"},
        headers=_AUTH,
    )
    assert r.status_code == 200
    iid = r.json()["id"]

    listed = client.get("/user/insights", headers=_AUTH).json()
    assert any(i["id"] == iid for i in listed)

    r_del = client.delete(f"/user/insights/{iid}", headers=_AUTH)
    assert r_del.status_code == 204

    listed2 = client.get("/user/insights", headers=_AUTH).json()
    assert not any(i["id"] == iid for i in listed2)
