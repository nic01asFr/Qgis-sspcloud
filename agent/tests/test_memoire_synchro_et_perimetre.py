"""Cohérence de la mémoire : l'index suit les suppressions, pas de fuite d'étude.

Défauts constatés à l'audit du 2026-09-11 :
- oublier un fait ou réécrire une section ne retirait rien de l'index
  sémantique, qui resservait ce que l'utilisateur croyait supprimé ;
- la balise <remember> n'écartait pas les faits qui décrivent l'étude ;
- le filtre anti-doublon du rappel était inopérant (id absent) ;
- une conversation restait bloquée en mode recette.
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

_QGIS_SRC = (_ROOT / "agent" / "qgis_agent.py").read_text(encoding="utf-8")
_CHAT = (_ROOT / "templates" / "chat.html").read_text(encoding="utf-8")


def _run(coro):
    return asyncio.get_event_loop().run_until_complete(coro)


def _memoire(monkeypatch, tmp_path):
    monkeypatch.setattr(memory, "_DATA_DIR", tmp_path)
    monkeypatch.setattr(memory, "_DB_PATH", tmp_path / "m.db")
    _run(memory.init())
    purges: list[dict] = []

    async def _faux_purge(**kwargs):
        purges.append(kwargs)

    # Interception de l'appel best-effort vers l'index vectoriel.
    import agent.vector_store as vs
    monkeypatch.setattr(vs, "purge", _faux_purge)
    return purges


# ── L'index suit les suppressions et modifications ───────────────────────


def test_oublier_un_fait_le_retire_de_l_index(monkeypatch, tmp_path) -> None:
    purges = _memoire(monkeypatch, tmp_path)
    iid = _run(memory.add_insight("format_export", "PDF A3", username="user"))
    _run(memory.delete_insight(iid))
    assert {"source_type": "insight", "source_id": str(iid)} in purges


def test_reecrire_un_fait_force_la_reindexation(monkeypatch, tmp_path) -> None:
    purges = _memoire(monkeypatch, tmp_path)
    iid = _run(memory.add_insight("format_export", "PDF A3", username="user"))
    purges.clear()
    _run(memory.add_insight("format_export", "PNG", username="user"))  # update
    assert {"source_type": "insight", "source_id": str(iid)} in purges


def test_effacer_une_section_la_retire_de_l_index(monkeypatch, tmp_path) -> None:
    purges = _memoire(monkeypatch, tmp_path)
    _run(memory.set_memory_section("zones", "Var (83)", username="user"))
    assert any(p.get("source_type") == "memory_doc" for p in purges)


def test_vider_les_faits_purge_l_index(monkeypatch, tmp_path) -> None:
    purges = _memoire(monkeypatch, tmp_path)
    _run(memory.add_insight("a", "1"))
    purges.clear()
    _run(memory.clear_insights("user"))
    assert {"source_type": "insight"} in purges


# ── purge accepte une cible précise ──────────────────────────────────────


def test_purge_accepte_source_id() -> None:
    import inspect
    import agent.vector_store as vs
    assert "source_id" in inspect.signature(vs.purge).parameters


# ── Filtre anti-doublon du rappel : l'id circule ─────────────────────────


def test_les_messages_portent_leur_id(monkeypatch, tmp_path) -> None:
    _memoire(monkeypatch, tmp_path)
    _run(memory.create_session("s1", "user"))
    _run(memory.add_message("s1", "user", "Bonjour, une longue phrase de test."))
    msgs = _run(memory.get_session_messages("s1"))
    assert msgs and msgs[0].get("id") is not None


# ── <remember> écarte les faits d'étude ──────────────────────────────────


def test_remember_ecarte_les_faits_d_etude() -> None:
    """Le tour applique la même garde que l'extracteur automatique."""
    assert "from agent.insight_extractor import _decrit_l_etude_en_cours" in _QGIS_SRC
    # La garde precede l'enregistrement de l'insight <remember>.
    bloc = _QGIS_SRC.split("if _decrit_l_etude_en_cours(key_clean):")[1][:200]
    assert "continue" in bloc


def test_la_consigne_remember_interdit_les_faits_d_etude() -> None:
    assert "NE MÉMORISE JAMAIS ce qui décrit l'ÉTUDE" in _QGIS_SRC
    # L'exemple trompeur d'une zone d'étude ponctuelle a disparu.
    assert "preferred_zone:Le Lavandou" not in _QGIS_SRC


# ── La conversation sort du mode recette ─────────────────────────────────


def test_la_conversation_sort_du_mode_recette() -> None:
    envoi = _CHAT.split("chat-form').addEventListener('submit'")[1]
    assert "etaitUneRecette" in envoi
    assert "/:recipe:/.test(sessionAuSubmit)" in envoi
    fin = envoi.split("} finally {")[1]
    assert "if (etaitUneRecette" in fin
    assert "randomUUID" in fin
