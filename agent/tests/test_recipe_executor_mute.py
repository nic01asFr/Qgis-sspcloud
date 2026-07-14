"""Tests Chantier G8 : agent.recipe_executor_mute.

Verifie :
  1. stream_recipe_execution → yield 4 events dans l'ordre attendu
     (recipe_start, recipe_step*, recipe_output, recipe_done).
  2. recipe_id inexistant (hub 404) → yield recipe_error puis stop.
  3. Le narrative final ne contient PAS de balise LLM (pas de function_call,
     pas de generation stream, pas de <tool_call>).
  4. Le tag session execution_mode=recipe_pure est set apres exécution.
  5. Une session avec context_kind=recipe_run court-circuite bien le LLM
     (mock QGISAgent.chat_stream — il ne doit PAS etre appele).
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
import tempfile
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

# DATA_DIR isole avant import memory (patron des autres tests agent).
_TMP_DIR = tempfile.mkdtemp(prefix="qgis_g8_test_")
os.environ["DATA_DIR"] = _TMP_DIR

# Stub sqlite_vec au besoin (cf. test_profile_locked.py).
if "sqlite_vec" not in sys.modules:
    _vec_stub = type(sys)("sqlite_vec")
    def _noop_load(*a, **k):
        return None
    _vec_stub.load = _noop_load  # type: ignore[attr-defined]
    sys.modules["sqlite_vec"] = _vec_stub

os.environ.setdefault(
    "HUB_URL",
    "https://user-nicolaslaval-qgis.user.lab.sspcloud.fr",
)
os.environ.setdefault("HUB_API_KEY", "test-key")
os.environ.setdefault("QGIS_API_KEY", "test-key")
os.environ.setdefault("ONYXIA_USER", "test-user")

from agent import memory  # noqa: E402
from agent import recipe_executor_mute as rem  # noqa: E402

memory._DATA_DIR = Path(_TMP_DIR)
memory._DB_PATH = Path(_TMP_DIR) / "memory.db"


def _run(coro):
    return asyncio.get_event_loop().run_until_complete(coro)


async def _setup():
    await memory.init()


# ── Fixture : reponse hub simulee (RecipeWebOutput valide) ───────────────────


def _fake_hub_output() -> dict:
    """RecipeWebOutput minimal qui satisfait _summarize_scene_manifest et
    _build_final_narrative sans dependre du moteur reel."""
    return {
        "scene_manifest": {
            "manifest_version": "0.3.1",
            "produced_at": "2026-07-13T10:00:00Z",
            "title": "Recipe demo",
            "layers": [
                {"id": "L1", "name": "Layer 1"},
                {"id": "L2", "name": "Layer 2"},
            ],
            "provenance": {"output_kind": "component"},
        },
        "provenance": {
            "recipe_id": "demo",
            "recipe_version": "1.0",
            "mode": "recipe_pure",
            "produced_at": "2026-07-13T10:00:00Z",
            "steps_order": ["step_a", "step_b", "render"],
            "briques_used": ["rules_global/foo"],
            "rules_enforced": [],
            "output_kind": "component",
        },
        "briques_used": ["rules_global/foo"],
    }


def _parse_sse_stream(events: list[str]) -> list[tuple[str, dict]]:
    """Parse une liste de chunks SSE renvoyes par le stream en (event, payload)."""
    parsed: list[tuple[str, dict]] = []
    for chunk in events:
        # Un chunk peut contenir "event: X\ndata: {...}\n\n"
        lines = chunk.strip("\n").split("\n")
        event_name = ""
        data_line = ""
        for line in lines:
            if line.startswith("event: "):
                event_name = line[len("event: "):]
            elif line.startswith("data: "):
                data_line = line[len("data: "):]
        if event_name and data_line:
            parsed.append((event_name, json.loads(data_line)))
    return parsed


# ── Tests ───────────────────────────────────────────────────────────────────


def test_stream_yields_events_in_order_on_success():
    """Recipe qui reussit : recipe_start, recipe_step*, recipe_output, recipe_done."""
    _run(_setup())

    async def _fake_hub_call(hub_url, api_key, recipe_id, user_message, sid=None):
        return 200, _fake_hub_output()

    with patch.object(rem, "_call_hub_execute", new=AsyncMock(
        side_effect=_fake_hub_call,
    )):
        async def _collect():
            events = []
            async for chunk in rem.stream_recipe_execution(
                session_id="study:s1:recipe:demo",
                recipe_id="demo",
                hub_url="http://hub",
                api_key="k",
                user_message="test",
            ):
                events.append(chunk)
            return events

        events = _run(_collect())
    parsed = _parse_sse_stream(events)
    names = [e for e, _ in parsed]
    # Ordre attendu : start, step(x3), output, done
    assert names[0] == "recipe_start"
    assert names[-1] == "recipe_done"
    assert "recipe_output" in names
    assert names.count("recipe_step") == 3
    # Index du step doit preceder recipe_output puis recipe_done
    idx_start = names.index("recipe_start")
    idx_first_step = names.index("recipe_step")
    idx_output = names.index("recipe_output")
    idx_done = names.index("recipe_done")
    assert idx_start < idx_first_step < idx_output < idx_done


def test_stream_yields_recipe_error_when_hub_returns_404():
    """recipe_id inexistant : hub 404 → yield recipe_error puis stop."""
    _run(_setup())

    async def _fake_hub_call(hub_url, api_key, recipe_id, user_message, sid=None):
        return 404, {"detail": "Recipe 'ghost' introuvable"}

    with patch.object(rem, "_call_hub_execute", new=AsyncMock(
        side_effect=_fake_hub_call,
    )):
        async def _collect():
            events = []
            async for chunk in rem.stream_recipe_execution(
                session_id="study:s1:recipe:ghost",
                recipe_id="ghost",
                hub_url="http://hub",
                api_key="k",
                user_message="test",
            ):
                events.append(chunk)
            return events

        events = _run(_collect())
    parsed = _parse_sse_stream(events)
    names = [e for e, _ in parsed]
    # start + error, pas de done
    assert names == ["recipe_start", "recipe_error"], names
    # Le payload d'erreur contient le http_status et le recipe_id
    err_payload = parsed[-1][1]
    assert err_payload["http_status"] == 404
    assert err_payload["recipe_id"] == "ghost"

    # Le tag recipe_run_failed doit avoir ete pose.
    tags = _run(memory.get_session_tags("study:s1:recipe:ghost"))
    assert tags.get("recipe_run_failed") == "ghost"


def test_narrative_contains_no_llm_markers():
    """Le narratif final est un template pur : aucune balise LLM."""
    output = _fake_hub_output()
    narr = rem._build_final_narrative("demo", output, "message user")
    assert isinstance(narr, str) and narr
    forbidden = [
        "<function_call>",
        "<tool_call>",
        "<generation>",
        "```tool",
        "assistant:",
        "<think>",
        "<|",
    ]
    lowered = narr.lower()
    for tag in forbidden:
        assert tag.lower() not in lowered, f"balise LLM interdite trouvee : {tag}"
    # Verifications positives : le narratif nomme la recipe et les briques.
    assert "recipe" in lowered
    assert "publier" in lowered


def test_execution_mode_tag_set_after_success():
    """Apres un run reussi, le tag session execution_mode=recipe_pure est pose."""
    _run(_setup())

    sid = "study:s2:recipe:demo"

    async def _fake_hub_call(hub_url, api_key, recipe_id, user_message, sid=None):
        return 200, _fake_hub_output()

    with patch.object(rem, "_call_hub_execute", new=AsyncMock(
        side_effect=_fake_hub_call,
    )):
        async def _drain():
            async for _ in rem.stream_recipe_execution(
                session_id=sid,
                recipe_id="demo",
                hub_url="http://hub",
                api_key="k",
                user_message="hello",
            ):
                pass
        _run(_drain())

    tags = _run(memory.get_session_tags(sid))
    assert tags.get("execution_mode") == "recipe_pure"
    assert tags.get("recipe_run_ok") == "demo"


def test_chat_recipe_run_shortcircuits_llm(monkeypatch):
    """POST /chat avec session_id study:{sid}:recipe:{rid} ne doit PAS
    appeler QGISAgent.chat_stream (mock) : le mode mute prend la main.
    """
    from fastapi.testclient import TestClient
    from agent import main as agent_main
    from agent.qgis_agent import QGISAgent

    _run(_setup())

    # Compteur d'appels : chat_stream ne doit JAMAIS etre appele en mode mute.
    calls = {"n": 0}

    async def _forbidden_stream(self, message, history=None, stop_signal=None):
        calls["n"] += 1
        yield "SHOULD NOT BE CALLED"

    monkeypatch.setattr(QGISAgent, "chat_stream", _forbidden_stream)

    async def _no_active_id():
        return None
    monkeypatch.setattr(agent_main, "_fetch_active_study_id", _no_active_id)

    async def _resolve(profile_id, session_id):
        return profile_id
    monkeypatch.setattr(agent_main, "_resolve_active_profile", _resolve)

    # Mock l'appel hub → retourne notre fake output.
    from agent import recipe_executor_mute as _rem

    async def _fake_hub_call(hub_url, api_key, recipe_id, user_message, sid=None):
        return 200, _fake_hub_output()

    monkeypatch.setattr(
        _rem, "_call_hub_execute", AsyncMock(side_effect=_fake_hub_call),
    )

    sid = "study:g8sess:recipe:demo"
    with TestClient(agent_main.app) as client:
        resp = client.post(
            "/chat",
            data={
                "message": "lance la recipe",
                "session_id": sid,
                "profile_id": "standard",
            },
            headers={"user-agent": "kube-probe/1.0"},
        )
        assert resp.status_code == 200
        body = resp.content.decode("utf-8")

    # Le LLM ne doit pas avoir ete consulte.
    assert calls["n"] == 0, "chat_stream ne doit pas etre appele en mode mute"
    # Le stream contient les events recipe_*
    assert "event: recipe_start" in body
    assert "event: recipe_done" in body
    # Et l'output est bien serialise dedans (marker deterministe).
    assert "recipe_pure" in body


if __name__ == "__main__":
    import inspect
    ns = dict(globals())
    for name, fn in ns.items():
        if name.startswith("test_") and callable(fn):
            sig = inspect.signature(fn)
            if "monkeypatch" in sig.parameters:
                continue
            print(f"→ {name}")
            fn()
    print("OK — recipe_executor_mute tests (hors integration) passent.")
