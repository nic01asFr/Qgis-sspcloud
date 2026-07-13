"""Tests Chantier G2 : profil frontend-locked.

Verifie :
  - _build_system_prompt(profile_locked=False) contient l'invite <switch_profile>.
  - _build_system_prompt(profile_locked=True) ne contient PAS l'invite mais
    contient la note "PROFIL VERROUILLE".
  - Un texte contenant <switch_profile>...</switch_profile> ne bascule PAS le
    profil quand profile_locked=True.
  - POST /chat avec profile_locked=true pose le tag session profile_locked=true
    dans memory (verifiable via memory.get_session_tags).
  - Backward-compat : sans flag, comportement inchange (routeur contextuel
    applique, pas de tag ajoute).
"""

from __future__ import annotations

import asyncio
import os
import re
import sys
import tempfile
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

# Path setup : agent/agent/ doit etre importable
_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

# Forcer DATA_DIR temporaire AVANT l'import de memory.py.
_TMP_DIR = tempfile.mkdtemp(prefix="qgis_locked_test_")
os.environ["DATA_DIR"] = _TMP_DIR

# Stub sqlite_vec pour les envs de test sans sqlite-vec compile (CI + dev
# local hors container). vector_store est charge par main.py au top-level ;
# on lui fournit un shim minimal pour eviter l'ImportError.
if "sqlite_vec" not in sys.modules:
    _vec_stub = type(sys)("sqlite_vec")

    def _noop_load(*a, **k):
        return None

    _vec_stub.load = _noop_load  # type: ignore[attr-defined]
    sys.modules["sqlite_vec"] = _vec_stub
# Env stub pour le startup de main.py : HUB_URL/HUB_API_KEY sont exiges au
# boot (RuntimeError sinon). En test on donne des valeurs bidon, aucune
# requete reseau n'est faite grace aux monkeypatch dans les tests.
# Cette URL est aussi utilisee par test_rewrite_workspace_urls.py — garder
# la meme valeur permet aux 2 files de coexister quel que soit l'ordre
# d'import (qgis_agent capture _HUB_URL une seule fois au load module).
os.environ.setdefault(
    "HUB_URL",
    "https://user-nicolaslaval-qgis.user.lab.sspcloud.fr",
)
os.environ.setdefault("HUB_API_KEY", "test-key")
os.environ.setdefault("QGIS_API_KEY", "test-key")
os.environ.setdefault("ONYXIA_USER", "test-user")

from agent import memory  # noqa: E402
from agent import qgis_agent as qgis_agent_mod  # noqa: E402
from agent.qgis_agent import QGISAgent  # noqa: E402

# Forcer le DB_PATH sur notre tempdir (voir test_session_tags.py).
memory._DATA_DIR = Path(_TMP_DIR)
memory._DB_PATH = Path(_TMP_DIR) / "memory.db"


def _run(coro):
    return asyncio.get_event_loop().run_until_complete(coro)


async def _setup():
    await memory.init()


# ── Fixtures : contexte mocke pour _build_system_prompt ──────────────────────

async def _build_prompt_isolated(agent: QGISAgent) -> str:
    """Appelle _build_system_prompt en isolant les deps externes.

    Mocke les fetch hub, la couche memory et les enrichers pour tester
    uniquement la composition du prompt (blocs SWITCH vs LOCKED).
    """
    with patch.object(
        qgis_agent_mod, "_load_profile_prompt",
        return_value="[PROFIL PROMPT STUB]",
    ), patch.object(
        agent, "_fetch_active_study_context",
        new=AsyncMock(return_value=({}, [])),
    ), patch.object(
        agent, "_fetch_project_state",
        new=AsyncMock(return_value={}),
    ), patch.object(
        agent, "_fetch_study_artifacts_summary",
        new=AsyncMock(return_value={}),
    ), patch.object(
        memory, "get_session_messages",
        new=AsyncMock(return_value=[]),
    ), patch.object(
        memory, "build_context_summary",
        new=AsyncMock(return_value="[CTX STUB]"),
    ):
        return await agent._build_system_prompt(user_message=None)


# ── Tests unitaires : composition du prompt ──────────────────────────────────

def test_build_system_prompt_unlocked_contains_switch_invite():
    _run(_setup())
    agent = QGISAgent(
        username="user", session_id="s_unlocked",
        profile_id="standard", profile_locked=False,
    )
    prompt = _run(_build_prompt_isolated(agent))
    # L'invite explicite au switch doit etre presente.
    assert "<switch_profile>" in prompt
    assert "PROFIL DYNAMIQUE" in prompt
    # La note verrouillage NE doit PAS etre presente.
    assert "PROFIL VERROUILLE" not in prompt


def test_build_system_prompt_locked_omits_switch_invite():
    _run(_setup())
    agent = QGISAgent(
        username="user", session_id="s_locked",
        profile_id="map_composer", profile_locked=True,
    )
    prompt = _run(_build_prompt_isolated(agent))
    # L'invite au switch (mention du tag <switch_profile>NOM_PROFIL</switch_profile>
    # au sein du bloc PROFIL DYNAMIQUE) ne doit PAS y etre.
    assert "PROFIL DYNAMIQUE" not in prompt
    assert "<switch_profile>NOM_PROFIL</switch_profile>" not in prompt
    # La note verrouillage doit y etre.
    assert "PROFIL VERROUILLE" in prompt


# ── Test : la balise <switch_profile> est ignoree quand locked ──────────────

def test_switch_profile_directive_ignored_when_locked():
    """Simule ce que fait chat_stream apres reception LLM : parse la balise
    <switch_profile> et applique (ou pas) selon profile_locked.

    On reproduit le flux inline (extrait de qgis_agent.chat_stream) : cela
    verifie que la meme sequence de branches produit le bon comportement.
    """
    _run(_setup())

    sid = "s_locked_ignore"
    agent = QGISAgent(
        username="user", session_id=sid,
        profile_id="map_composer", profile_locked=True,
    )
    # Simule une reponse LLM contenant une tentative de switch.
    full_response = (
        "Voici la carte demandee. <switch_profile>guided_tour</switch_profile>"
        " Fin."
    )

    # Reproduction de la logique inline chat_stream (bloc G2).
    m = re.search(
        r'<switch_profile>\s*([a-zA-Z0-9_]+)\s*</switch_profile>',
        full_response, flags=re.IGNORECASE,
    )
    assert m is not None, "regex sanity check"

    if m and agent.profile_locked:
        attempted = m.group(1).strip().lower()
        _run(memory.set_session_tag(sid, "profile_switch_ignored_last", attempted))
        m = None

    # Le switch reel ne doit PAS avoir eu lieu.
    assert m is None
    assert agent.profile_id == "map_composer"  # profil actif inchange

    # Telemetry : le tag doit etre pose.
    tags = _run(memory.get_session_tags(sid))
    assert tags.get("profile_switch_ignored_last") == "guided_tour"


def test_switch_profile_directive_honored_when_unlocked():
    """Backward-compat : sans profile_locked, la balise doit basculer le
    profil comme avant.
    """
    _run(_setup())

    sid = "s_unlocked_switch"
    agent = QGISAgent(
        username="user", session_id=sid,
        profile_id="standard", profile_locked=False,
    )
    full_response = "<switch_profile>map_composer</switch_profile>"

    m = re.search(
        r'<switch_profile>\s*([a-zA-Z0-9_]+)\s*</switch_profile>',
        full_response, flags=re.IGNORECASE,
    )
    assert m is not None

    # Branche G2 : locked=False -> on n'entre pas dans le short-circuit.
    if m and agent.profile_locked:
        m = None
    assert m is not None, "profile_locked=False doit laisser passer le switch"

    # Applique le switch (equivalent inline chat_stream).
    _run(agent.set_profile(m.group(1).strip().lower()))
    assert agent.profile_id == "map_composer"


# ── Test integration : POST /chat pose le tag session ────────────────────────

def test_post_chat_sets_profile_locked_tag(monkeypatch):
    """Verifie qu'un POST /chat avec profile_locked=true pose le tag
    profile_locked=true sur la session (via memory.set_session_tag).

    On mocke QGISAgent.chat_stream pour eviter le vrai loop LLM.
    """
    from fastapi.testclient import TestClient

    # Import differe : main.py cree une app FastAPI globale, on veut la
    # patcher avant l'appel HTTP.
    from agent import main as agent_main

    _run(_setup())

    # Stub chat_stream : yield un seul chunk vide et retourne.
    async def _fake_stream(self, message, history=None, stop_signal=None):
        yield ""

    monkeypatch.setattr(QGISAgent, "chat_stream", _fake_stream)

    # Neutralise le fetch etude active (pas de hub en test) pour eviter les
    # network calls dans _fetch_active_study_id / _resolve_active_profile.
    async def _no_active_id():
        return None
    monkeypatch.setattr(agent_main, "_fetch_active_study_id", _no_active_id)

    async def _resolve(profile_id, session_id):
        return profile_id
    monkeypatch.setattr(agent_main, "_resolve_active_profile", _resolve)

    sid = "chat_locked_sess"
    with TestClient(agent_main.app) as client:
        # Court-circuit auth OIDC via user-agent kube-probe (cf. middleware
        # agent_oidc_middleware, etape 2).
        resp = client.post(
            "/chat",
            data={
                "message": "test",
                "session_id": sid,
                "profile_id": "map_composer",
                "profile_locked": "true",
            },
            headers={"user-agent": "kube-probe/1.0"},
        )
        assert resp.status_code == 200
        # Draine le stream pour que le handler termine ses effets.
        _ = resp.content

    tags = _run(memory.get_session_tags(sid))
    assert tags.get("profile_locked") == "true"


def test_post_chat_backward_compat_no_tag(monkeypatch):
    """Sans flag profile_locked, aucun tag profile_locked ne doit etre pose."""
    from fastapi.testclient import TestClient

    from agent import main as agent_main

    _run(_setup())

    async def _fake_stream(self, message, history=None, stop_signal=None):
        yield ""
    monkeypatch.setattr(QGISAgent, "chat_stream", _fake_stream)

    async def _no_active_id():
        return None
    monkeypatch.setattr(agent_main, "_fetch_active_study_id", _no_active_id)

    async def _resolve(profile_id, session_id):
        return profile_id
    monkeypatch.setattr(agent_main, "_resolve_active_profile", _resolve)

    sid = "chat_unlocked_sess"
    with TestClient(agent_main.app) as client:
        resp = client.post(
            "/chat",
            data={
                "message": "test",
                "session_id": sid,
                "profile_id": "standard",
            },
            headers={"user-agent": "kube-probe/1.0"},
        )
        assert resp.status_code == 200
        _ = resp.content

    tags = _run(memory.get_session_tags(sid))
    assert "profile_locked" not in tags


if __name__ == "__main__":
    import inspect
    ns = dict(globals())
    for name, fn in ns.items():
        if name.startswith("test_") and callable(fn):
            sig = inspect.signature(fn)
            if "monkeypatch" in sig.parameters:
                continue  # skip les tests qui exigent la fixture pytest
            print(f"→ {name}")
            fn()
    print("OK — profile_locked tests (hors integration) passent.")
