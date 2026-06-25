"""Tests enforcement scope au proxy MCP (etape 3) — filtrage whitelist tools.

Couvre les helpers purs du proxy : resolution whitelist, parse JSON-RPC, gate
tools/call (erreur JSON-RPC), filtrage tools/list (JSON + SSE). L'injection
sid/pid n'est PAS dans cette etape (coordonnee avec Composants ulterieurement).
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from hub import main  # noqa: E402


def test_whitelist_resolution():
    # None / superviseur / "all" -> pas de filtrage
    assert main._scope_tools_whitelist(None) is None
    assert main._scope_tools_whitelist({"mode": "supervisor", "tools": ["a"]}) is None
    assert main._scope_tools_whitelist({"mode": "scoped", "tools": "all"}) is None
    # liste -> filtrage
    assert main._scope_tools_whitelist(
        {"mode": "scoped", "tools": ["a", "b"]}) == ["a", "b"]


def test_jsonrpc_obj():
    assert main._jsonrpc_obj(b'{"method":"tools/list"}') == {"method": "tools/list"}
    assert main._jsonrpc_obj(b"pas du json") is None
    assert main._jsonrpc_obj(b"[1,2]") is None       # batch -> None (pass-through)


def test_tool_call_gate():
    wl = ["run_recipe"]
    ok = {"jsonrpc": "2.0", "id": 1, "method": "tools/call",
          "params": {"name": "run_recipe", "arguments": {}}}
    assert main._tool_call_denied(ok, wl) is None     # autorise

    ko = {"jsonrpc": "2.0", "id": 2, "method": "tools/call",
          "params": {"name": "delete_layer", "arguments": {}}}
    resp = main._tool_call_denied(ko, wl)             # refuse
    assert resp is not None and resp.status_code == 200
    payload = json.loads(bytes(resp.body))
    assert payload["id"] == 2
    assert payload["error"]["code"] == -32601
    assert "delete_layer" in payload["error"]["message"]

    assert main._tool_call_denied({"params": {}}, wl) is None  # pas de name


def test_filter_tools_list_json():
    wl = ["a", "c"]
    body = json.dumps({
        "jsonrpc": "2.0", "id": 1,
        "result": {"tools": [{"name": "a"}, {"name": "b"}, {"name": "c"}]},
    }).encode()
    out = main._filter_tools_list_payload(body, "application/json", wl)
    names = [t["name"] for t in json.loads(out)["result"]["tools"]]
    assert names == ["a", "c"]


def test_filter_tools_list_sse():
    wl = ["a"]
    inner = json.dumps({
        "jsonrpc": "2.0", "id": 1,
        "result": {"tools": [{"name": "a"}, {"name": "b"}]},
    })
    body = f"event: message\ndata: {inner}\n\n".encode()
    out = main._filter_tools_list_payload(body, "text/event-stream", wl).decode()
    assert "event: message" in out                    # framing SSE preserve
    data_line = next(l for l in out.split("\n") if l.startswith("data:"))
    obj = json.loads(data_line[5:].strip())
    assert [t["name"] for t in obj["result"]["tools"]] == ["a"]


def test_filter_preserves_non_tools_payload():
    # un resultat sans .result.tools n'est pas altere
    wl = ["a"]
    body = json.dumps({"jsonrpc": "2.0", "id": 9, "result": {"ok": True}}).encode()
    out = main._filter_tools_list_payload(body, "application/json", wl)
    assert json.loads(out)["result"] == {"ok": True}
