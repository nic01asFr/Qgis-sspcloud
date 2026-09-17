"""L'agent doit pouvoir dire de quel commit il a ete construit.

`/api/version` figurait dans la liste des chemins publics de l'agent depuis
longtemps -- mais la route n'existait pas. La verification « l'agent est-il a
jour ? » etait donc irrealisable, et une verification qu'on ne peut pas faire
ne rate jamais. Meme defaut, et meme correctif, que cote hub.
"""
from __future__ import annotations

import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

if "sqlite_vec" not in sys.modules:
    _vec = type(sys)("sqlite_vec")
    _vec.load = lambda *a, **k: None          # type: ignore[attr-defined]
    _vec.loadable_path = lambda: ""           # type: ignore[attr-defined]
    sys.modules["sqlite_vec"] = _vec

from agent import main as agent_main  # noqa: E402

_DEPOT = _ROOT.parent


def _chemins() -> set[str]:
    return {r.path for r in agent_main.app.routes if hasattr(r, "path")}


def test_la_route_declaree_publique_existe_vraiment():
    assert "/api/version" in _chemins()
    assert "/api/version" in agent_main._AGENT_PUBLIC_ROUTES


def test_l_absence_de_commit_est_dite_pas_deduite(monkeypatch):
    import asyncio
    monkeypatch.delenv("AGENT_GIT_SHA", raising=False)
    etat = asyncio.new_event_loop().run_until_complete(agent_main.api_version())
    assert etat["commit"] is None
    assert "note" in etat


def test_le_commit_est_rendu_quand_il_est_connu(monkeypatch):
    import asyncio
    monkeypatch.setenv("AGENT_GIT_SHA", "abc1234")
    etat = asyncio.new_event_loop().run_until_complete(agent_main.api_version())
    assert etat["commit"] == "abc1234"
    assert "note" not in etat


def test_l_image_recoit_le_commit_au_build():
    contenu = (_DEPOT / "Dockerfile.agent").read_text(encoding="utf-8")
    assert "ARG GIT_SHA" in contenu
    assert "AGENT_GIT_SHA=${GIT_SHA}" in contenu


def test_la_ci_passe_l_argument():
    ci = (_DEPOT / ".github" / "workflows" / "build.yml").read_text(encoding="utf-8")
    bloc = ci.split("file: Dockerfile.agent")[1].split("Smoke test")[0]
    assert "GIT_SHA=${{ github.sha }}" in bloc
