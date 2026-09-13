"""Lot C : plus de doublons dans la couche utilisateur, rattachement d'étude fiable.

Défauts d'audit du 2026-09-11 :
- « zone habituelle » injectée jusqu'à quatre fois (section, préférence,
  deux clés d'insight) dans le même prompt ;
- une session restait sans étude pour toujours si le hub était injoignable
  au premier message.
"""
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

from agent import memory  # noqa: E402


def _run(coro):
    try:
        loop = asyncio.get_event_loop()
        if loop.is_closed():
            raise RuntimeError
    except RuntimeError:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
    return loop.run_until_complete(coro)


def _memoire(monkeypatch, tmp_path):
    monkeypatch.setattr(memory, "_DATA_DIR", tmp_path)
    monkeypatch.setattr(memory, "_DB_PATH", tmp_path / "m.db")
    _run(memory.init())


# ── Déduplication de valeur ──────────────────────────────────────────────


def test_le_normalisateur_ecarte_une_valeur_deja_dite() -> None:
    n = memory._NormalisateurValeurs()
    n.ajouter("## Zones\nVar (83), surtout le littoral entre Hyères")
    assert n.contient("Var (83), surtout le littoral")
    assert not n.contient("Gard (30), arrière-pays")
    assert not n.contient("PDF")  # trop court, pas de faux positif


def test_un_fait_deja_dans_le_markdown_n_est_pas_repete(monkeypatch, tmp_path) -> None:
    _memoire(monkeypatch, tmp_path)
    _run(memory.set_memory_section("zones", "Var (83), surtout le littoral", "user"))
    # Même info stockée comme insight auto-détecté.
    _run(memory.add_insight("zone_habituelle", "Var (83), surtout le littoral",
                            source="implicit", username="user"))
    ctx = _run(memory.build_context_summary("user", "sess-x"))
    # La valeur n'apparaît qu'une fois (dans le markdown), pas en doublon.
    assert ctx.count("Var (83), surtout le littoral") == 1


def test_un_fait_nouveau_est_bien_injecte(monkeypatch, tmp_path) -> None:
    _memoire(monkeypatch, tmp_path)
    _run(memory.set_memory_section("zones", "Var (83)", "user"))
    _run(memory.add_insight("methode_recurrente", "scoring de biodispersal",
                            source="implicit", username="user"))
    ctx = _run(memory.build_context_summary("user", "sess-x"))
    assert "scoring de biodispersal" in ctx


# ── Rattachement d'étude fiable ──────────────────────────────────────────


def test_get_session_study(monkeypatch, tmp_path) -> None:
    _memoire(monkeypatch, tmp_path)
    _run(memory.create_session("s1", "user"))
    assert _run(memory.get_session_study("s1")) is None
    _run(memory.set_session_study("s1", "etude-A"))
    assert _run(memory.get_session_study("s1")) == "etude-A"


def test_un_rattachement_existant_n_est_pas_ecrase(monkeypatch, tmp_path) -> None:
    """Changer d'étude en cours ne doit pas reclasser la conversation."""
    _memoire(monkeypatch, tmp_path)
    _run(memory.create_session("s1", "user"))
    _run(memory.set_session_study("s1", "etude-A"))
    # La logique de /chat ne rebinde que si get_session_study est None.
    assert _run(memory.get_session_study("s1")) == "etude-A"


def test_le_chat_rattache_tant_que_l_etude_est_absente() -> None:
    """Le rattachement est tenté à chaque tour tant que la session est orpheline."""
    src = (_ROOT / "agent" / "main.py").read_text(encoding="utf-8")
    assert "if not await memory.get_session_study(session_id):" in src
