"""Tests hub.mcp_hub_tools — Sprint isolation etudes-projets Fix #4 (2026-07-30).

Verifie :
- HUB_TOOLS_SCHEMA contient bien les 6 tools attendus, avec les inputSchema valides
- is_hub_tool discrimine correctement hub-tools vs workspace-tools
- dispatch_hub_tool route bien vers le handler correct + injecte execute_python_fn
- Les handlers construisent bien les args attendus et retournent le shape attendu
- Le merge tools/list injecte les hub-tools sans doublon (idempotence)
"""
from __future__ import annotations

import json
from unittest.mock import AsyncMock, patch

import pytest

from hub.mcp_hub_tools import (
    HUB_TOOL_HANDLERS,
    HUB_TOOLS_SCHEMA,
    dispatch_hub_tool,
    is_hub_tool,
)


# ── Schema ────────────────────────────────────────────────────────────────────

def test_schema_6_tools_present():
    """HUB_TOOLS_SCHEMA doit contenir exactement les 6 tools attendus."""
    names = {t["name"] for t in HUB_TOOLS_SCHEMA}
    assert names == {
        "study_list",
        "study_create",
        "study_switch",
        "study_project_list",
        "study_project_create",
        "study_project_switch",
    }


def test_schema_tools_have_valid_inputschema():
    """Chaque tool MCP doit avoir un inputSchema object serialisable JSON."""
    for tool in HUB_TOOLS_SCHEMA:
        assert "name" in tool
        assert "description" in tool
        assert "inputSchema" in tool
        assert tool["inputSchema"]["type"] == "object"
        # Serialisable
        json.dumps(tool)


def test_schema_study_prefix():
    """Tous les hub-tools doivent porter le prefixe `study_*` pour eviter
    la collision avec les tools workspace (new_project, save_project...)."""
    for tool in HUB_TOOLS_SCHEMA:
        assert tool["name"].startswith("study_"), (
            f"Tool {tool['name']!r} ne respecte pas le namespace study_*"
        )


def test_schema_required_fields_coherent():
    """study_create.name required, study_switch.sid required, etc."""
    by_name = {t["name"]: t for t in HUB_TOOLS_SCHEMA}
    assert by_name["study_create"]["inputSchema"]["required"] == ["name"]
    assert by_name["study_switch"]["inputSchema"]["required"] == ["sid"]
    assert by_name["study_project_create"]["inputSchema"]["required"] == ["label"]
    assert by_name["study_project_switch"]["inputSchema"]["required"] == ["pid"]
    # list-tools : pas de required
    assert "required" not in by_name["study_list"]["inputSchema"]
    assert "required" not in by_name["study_project_list"]["inputSchema"]


# ── Registry + routing ────────────────────────────────────────────────────────

def test_registry_covers_all_schema():
    """HUB_TOOL_HANDLERS doit couvrir 100% des tools declares dans HUB_TOOLS_SCHEMA."""
    schema_names = {t["name"] for t in HUB_TOOLS_SCHEMA}
    registry_names = set(HUB_TOOL_HANDLERS.keys())
    assert schema_names == registry_names


def test_is_hub_tool_positive():
    """Les 6 study_* sont bien hub-tools."""
    for name in ("study_list", "study_create", "study_switch",
                 "study_project_list", "study_project_create", "study_project_switch"):
        assert is_hub_tool(name) is True


def test_is_hub_tool_negative_workspace_tools():
    """Les tools workspace ne doivent pas etre reconnus comme hub-tools."""
    for name in ("add_layer", "execute_python", "new_project", "open_project",
                 "save_project", "upload_file", "export_pdf", "run_recipe"):
        assert is_hub_tool(name) is False


def test_is_hub_tool_negative_unknown():
    """Un nom inconnu -> False (pas d'exception)."""
    assert is_hub_tool("") is False
    assert is_hub_tool("random_tool_name") is False


# ── dispatch_hub_tool ─────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_dispatch_unknown_tool_raises():
    """Tool inconnu -> ValueError explicite."""
    with pytest.raises(ValueError, match="Hub tool inconnu"):
        await dispatch_hub_tool("unknown", {}, "nicolaslaval", None)


@pytest.mark.asyncio
async def test_dispatch_study_list_no_execute():
    """study_list ne requiere PAS execute_python_fn (juste API DB)."""
    with patch("hub.studies.list_studies", new=AsyncMock(return_value=[
        {"id": "aaaaaaaaaaaa", "name": "Etude 1", "profile": "standard",
         "status": "active", "last_active": "2026-07-30T10:00:00"},
        {"id": "bbbbbbbbbbbb", "name": "Etude 2", "profile": "cadastre",
         "status": "archived", "last_active": None},
    ])), \
         patch("hub.studies.get_active_study_id",
               new=AsyncMock(return_value="aaaaaaaaaaaa")):
        result = await dispatch_hub_tool(
            "study_list", {}, "nicolaslaval", execute_python_in_workspace_fn=None,
        )
    assert result["active_sid"] == "aaaaaaaaaaaa"
    # Filtrage status=active
    assert len(result["studies"]) == 1
    assert result["studies"][0]["sid"] == "aaaaaaaaaaaa"
    assert result["studies"][0]["is_active"] is True


@pytest.mark.asyncio
async def test_dispatch_study_create_missing_name():
    """study_create sans name -> ValueError."""
    with pytest.raises(ValueError, match="obligatoire"):
        await dispatch_hub_tool(
            "study_create", {"name": "  "}, "nicolaslaval",
            execute_python_in_workspace_fn=AsyncMock(),
        )


@pytest.mark.asyncio
async def test_dispatch_study_switch_missing_sid():
    """study_switch sans sid -> ValueError."""
    with pytest.raises(ValueError, match="'sid' obligatoire"):
        await dispatch_hub_tool(
            "study_switch", {}, "nicolaslaval",
            execute_python_in_workspace_fn=AsyncMock(),
        )


@pytest.mark.asyncio
async def test_dispatch_study_project_list_no_active_no_sid():
    """study_project_list sans sid explicite ni etude active -> erreur explicite."""
    with patch("hub.studies.get_active_study_id",
               new=AsyncMock(return_value=None)):
        with pytest.raises(ValueError, match="Aucune etude active"):
            await dispatch_hub_tool(
                "study_project_list", {}, "nicolaslaval",
                execute_python_in_workspace_fn=None,
            )


@pytest.mark.asyncio
async def test_dispatch_study_project_switch_missing_pid():
    """study_project_switch sans pid -> ValueError."""
    with pytest.raises(ValueError, match="'pid' obligatoire"):
        await dispatch_hub_tool(
            "study_project_switch", {}, "nicolaslaval",
            execute_python_in_workspace_fn=AsyncMock(),
        )


# ── Merge tools/list ──────────────────────────────────────────────────────────

def test_merge_hub_tools_idempotence():
    """Le merge dans _merge_hub_tools_in_tools_list doit etre idempotent :
    si les hub-tools sont deja presents (double appel proxy en amont), on
    ne les duplique pas."""
    from hub.main import _merge_hub_tools_in_tools_list
    # Reponse workspace initiale : 2 tools workspace + 1 hub tool deja injecte
    payload = {
        "jsonrpc": "2.0",
        "id": 1,
        "result": {
            "tools": [
                {"name": "add_layer", "description": "..."},
                {"name": "execute_python", "description": "..."},
                {"name": "study_list", "description": "existing"},
            ]
        }
    }
    raw = json.dumps(payload).encode("utf-8")
    merged = _merge_hub_tools_in_tools_list(raw, "application/json")
    result = json.loads(merged)
    names = [t["name"] for t in result["result"]["tools"]]
    # study_list ne doit apparaitre qu'une fois
    assert names.count("study_list") == 1
    # Les 5 autres hub-tools doivent avoir ete injectes
    for expected in ("study_create", "study_switch", "study_project_list",
                     "study_project_create", "study_project_switch"):
        assert expected in names
    # Total : 2 workspace + 6 hub = 8 (1 hub deja present + 5 injectes)
    assert len(names) == 8


def test_merge_hub_tools_json_response():
    """Reponse JSON workspace -> merge propre avec les 6 hub-tools."""
    from hub.main import _merge_hub_tools_in_tools_list
    payload = {
        "jsonrpc": "2.0",
        "id": 1,
        "result": {
            "tools": [
                {"name": "add_layer", "description": "..."},
            ]
        }
    }
    raw = json.dumps(payload).encode("utf-8")
    merged = _merge_hub_tools_in_tools_list(raw, "application/json")
    result = json.loads(merged)
    names = [t["name"] for t in result["result"]["tools"]]
    assert "add_layer" in names
    # Les 6 hub-tools injectes
    for expected in ("study_list", "study_create", "study_switch",
                     "study_project_list", "study_project_create", "study_project_switch"):
        assert expected in names
    assert len(names) == 7


def test_merge_hub_tools_sse_response():
    """Reponse SSE (text/event-stream) -> parse chaque data: line et injecte."""
    from hub.main import _merge_hub_tools_in_tools_list
    payload = {
        "jsonrpc": "2.0",
        "id": 1,
        "result": {"tools": [{"name": "add_layer", "description": "..."}]}
    }
    raw = f"event: message\ndata: {json.dumps(payload)}\n\n".encode("utf-8")
    merged = _merge_hub_tools_in_tools_list(raw, "text/event-stream")
    text = merged.decode("utf-8")
    # La ligne event: doit etre preservee
    assert "event: message" in text
    # La ligne data: doit contenir les hub-tools
    for expected in ("study_list", "study_create", "study_switch"):
        assert expected in text


def test_merge_hub_tools_malformed_json_returns_raw():
    """Payload non parseable -> retourne raw inchange (fail-safe)."""
    from hub.main import _merge_hub_tools_in_tools_list
    raw = b"this is not json"
    merged = _merge_hub_tools_in_tools_list(raw, "application/json")
    assert merged == raw


def test_merge_hub_tools_no_result_tools_key():
    """Payload sans result.tools -> retourne inchange (aucun crash)."""
    from hub.main import _merge_hub_tools_in_tools_list
    payload = {"jsonrpc": "2.0", "id": 1, "error": {"code": -1, "message": "..."}}
    raw = json.dumps(payload).encode("utf-8")
    merged = _merge_hub_tools_in_tools_list(raw, "application/json")
    result = json.loads(merged)
    # Aucun tools key -> merge no-op
    assert "tools" not in result.get("result", {})
    assert result == payload


# ── Day 3 session-scoped tests ───────────────────────────────────────────────


@pytest.mark.asyncio
async def test_day3_dispatch_propagates_mcp_session_id():
    """dispatch_hub_tool passe mcp_session_id aux handlers."""
    from hub import session_active_state as sas
    await sas._reset_for_tests()
    with patch("hub.studies.list_studies",
               new=AsyncMock(return_value=[
                   {"id": "sidA", "name": "A", "status": "active",
                    "last_active": None, "profile": "standard"},
               ])):
        # Pre-set session-scoped active
        await sas.set_active("mcp-XYZ", "sidA", "pidA")
        result = await dispatch_hub_tool(
            "study_list", {}, "user", execute_python_in_workspace_fn=None,
            mcp_session_id="mcp-XYZ",
        )
    assert result["active_sid"] == "sidA"
    assert result["session_scoped"] is True
    await sas._reset_for_tests()


@pytest.mark.asyncio
async def test_day3_study_list_sans_session_fallback_db():
    """Sans mcp_session_id -> fallback DB (session_scoped=False)."""
    from hub import session_active_state as sas
    await sas._reset_for_tests()
    with patch("hub.studies.list_studies",
               new=AsyncMock(return_value=[
                   {"id": "sidDB", "name": "DB", "status": "active",
                    "last_active": None, "profile": "standard"},
               ])), \
         patch("hub.studies.get_active_study_id",
               new=AsyncMock(return_value="sidDB")), \
         patch("hub.studies.get_active_project_id",
               new=AsyncMock(return_value=None)):
        result = await dispatch_hub_tool(
            "study_list", {}, "user", execute_python_in_workspace_fn=None,
        )
    assert result["active_sid"] == "sidDB"
    assert result["session_scoped"] is False


@pytest.mark.asyncio
async def test_day3_study_list_reinject_active_archived():
    """Fix edge case : etude active archivee -> toujours dans la liste."""
    from hub import session_active_state as sas
    await sas._reset_for_tests()
    with patch("hub.studies.list_studies",
               new=AsyncMock(return_value=[
                   {"id": "sidA", "name": "A-active", "status": "active",
                    "last_active": None, "profile": "standard"},
                   {"id": "sidB", "name": "B-archived", "status": "archived",
                    "last_active": None, "profile": "standard"},
               ])):
        # Active-sid pointe sur l'etude archived
        await sas.set_active("mcp-XYZ", "sidB", None)
        result = await dispatch_hub_tool(
            "study_list", {}, "user", execute_python_in_workspace_fn=None,
            mcp_session_id="mcp-XYZ",
        )
    # sidB doit apparaitre malgre status=archived
    names = [s["sid"] for s in result["studies"]]
    assert "sidA" in names
    assert "sidB" in names  # <-- fix edge case
    assert result["active_sid"] == "sidB"
    # is_active True sur sidB
    assert next(s for s in result["studies"] if s["sid"] == "sidB")["is_active"] is True
    await sas._reset_for_tests()


@pytest.mark.asyncio
async def test_day3_switch_isolation_2_sessions():
    """2 mcp_session_id distincts -> 2 active_sid isoles, DB user inchangee."""
    from hub import session_active_state as sas
    await sas._reset_for_tests()

    execute_mock = AsyncMock()
    with patch("hub.studies.get_study", new=AsyncMock(side_effect=lambda sid, u: {
                    "id": sid, "name": f"study-{sid}", "status": "active",
                })), \
         patch("hub.studies.get_active_study_id",
               new=AsyncMock(return_value="sidDB")), \
         patch("hub.studies.get_active_project_id",
               new=AsyncMock(return_value=None)), \
         patch("hub.studies.get_default_project", new=AsyncMock(return_value=None)), \
         patch("hub.studies.set_active_study",
               new=AsyncMock()) as mock_set_db, \
         patch("hub.studies.touch_study", new=AsyncMock()), \
         patch("hub.studies.save_active_project_pod_code",
               return_value="# save code"), \
         patch("hub.studies.activate_pod_code", return_value="# activate code"):

        # Session A switch vers sidA
        await dispatch_hub_tool(
            "study_switch", {"sid": "sidAAAAAAAAAA"}, "user",
            execute_python_in_workspace_fn=execute_mock,
            mcp_session_id="mcp-A",
        )
        # Session B switch vers sidB
        await dispatch_hub_tool(
            "study_switch", {"sid": "sidBBBBBBBBBB"}, "user",
            execute_python_in_workspace_fn=execute_mock,
            mcp_session_id="mcp-B",
        )

        # Verif isolation
        sidA, _ = await sas.get_active("mcp-A")
        sidB, _ = await sas.get_active("mcp-B")
        assert sidA == "sidAAAAAAAAAA"
        assert sidB == "sidBBBBBBBBBB"
        # DB user JAMAIS mutee (session-scoped)
        mock_set_db.assert_not_called()

    await sas._reset_for_tests()


@pytest.mark.asyncio
async def test_day3_switch_sans_session_mute_db():
    """Sans mcp_session_id -> comportement Day 2 (mute DB user)."""
    from hub import session_active_state as sas
    await sas._reset_for_tests()

    execute_mock = AsyncMock()
    with patch("hub.studies.get_study", new=AsyncMock(return_value={
                    "id": "sidX", "name": "X", "status": "active",
                })), \
         patch("hub.studies.get_active_study_id",
               new=AsyncMock(return_value="sidPrev")), \
         patch("hub.studies.get_active_project_id",
               new=AsyncMock(return_value=None)), \
         patch("hub.studies.get_default_project", new=AsyncMock(return_value=None)), \
         patch("hub.studies.set_active_study",
               new=AsyncMock()) as mock_set_db, \
         patch("hub.studies.touch_study", new=AsyncMock()), \
         patch("hub.studies.save_active_project_pod_code",
               return_value="# save code"), \
         patch("hub.studies.activate_pod_code", return_value="# activate code"):

        await dispatch_hub_tool(
            "study_switch", {"sid": "sidX"}, "user",
            execute_python_in_workspace_fn=execute_mock,
            # mcp_session_id absent
        )
        # DB user bien mutee
        mock_set_db.assert_called_once_with("user", "sidX")
