"""La page du chat ne s'incruste que dans une page de meme origine."""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

if "sqlite_vec" not in sys.modules:
    _vec_stub = type(sys)("sqlite_vec")
    _vec_stub.load = lambda *a, **k: None  # type: ignore[attr-defined]
    sys.modules["sqlite_vec"] = _vec_stub

from fastapi.testclient import TestClient  # noqa: E402

from agent import memory  # noqa: E402
from agent.main import app  # noqa: E402


def _run(coro):
    return asyncio.get_event_loop().run_until_complete(coro)


def test_la_page_du_chat_interdit_l_incrustation_par_un_tiers(monkeypatch, tmp_path) -> None:
    """Incruste ailleurs que dans le bureau, le chat agissait au nom de l'utilisateur."""
    monkeypatch.setattr(memory, "_DATA_DIR", tmp_path)
    monkeypatch.setattr(memory, "_DB_PATH", tmp_path / "incrustation.db")
    monkeypatch.setenv("HUB_API_KEY", "cle-essai-incrustation")
    monkeypatch.setenv("ONYXIA_USER", "essai")
    _run(memory.init())

    # Requete telle que le proxy /agent/ du hub la transmet ; `new=1` evite
    # d'interroger le hub sur l'etude active.
    r = TestClient(app).get("/?new=1&embed=1", headers={
        "Authorization": "Bearer cle-essai-incrustation",
        "X-Hub-Proxy-User": "essai",
    })

    assert r.status_code == 200
    assert r.headers["content-security-policy"] == "frame-ancestors 'self'"
