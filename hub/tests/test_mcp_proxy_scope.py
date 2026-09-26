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


# ── Outils hub (study_*) : le scope s'applique aussi ─────────────────────
#
# Constate le 2026-09-26 : le gate n'etait pose que sur les outils du
# workspace. `study_switch` & co partaient au dispatch local sans controle ;
# une cle scopee (agent partage) pouvait changer l'etude de son proprietaire.

class _Requete:
    def __init__(self, corps: bytes):
        self._corps = corps
        self.headers = {"content-type": "application/json"}
        self.query_params = {}

    async def body(self):
        return self._corps


def _appel(nom: str) -> bytes:
    return json.dumps({"jsonrpc": "2.0", "id": 7, "method": "tools/call",
                       "params": {"name": nom, "arguments": {"sid": "x"}}}).encode()


def test_outil_hub_hors_scope_refuse_sans_dispatch(monkeypatch):
    import asyncio
    from hub import mcp_hub_tools

    appele = []

    async def _dispatch(*a, **k):
        appele.append(a)
        return "ne doit pas etre appele"

    monkeypatch.setattr(mcp_hub_tools, "dispatch_hub_tool", _dispatch)
    assert mcp_hub_tools.is_hub_tool("study_switch")
    scope = {"mode": "scoped", "tools": ["get_project_info"]}
    resp = asyncio.run(main._proxy_request(
        _Requete(_appel("study_switch")), "http://pod.invalid/mcp", "sess", scope=scope))
    payload = json.loads(bytes(resp.body))
    assert payload["error"]["code"] == -32601
    assert "study_switch" in payload["error"]["message"]
    assert appele == [], "un outil hub hors scope ne doit jamais etre execute"


def test_gate_pose_avant_la_detection_des_outils_hub():
    """Verrou de source : le gate precede la bifurcation hub / workspace."""
    source = (_ROOT / "hub" / "main.py").read_text(encoding="utf-8")
    bloc = source.split("async def _proxy_request")[1].split("\nasync def ")[0]
    gate = bloc.index("denied = _tool_call_denied(obj, whitelist)")
    detection = bloc.index("is_hub_tool(_tool_name)")
    assert gate < detection
    assert bloc.count("_tool_call_denied(") == 1
