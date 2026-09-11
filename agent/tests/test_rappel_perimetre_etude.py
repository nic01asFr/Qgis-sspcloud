"""Le rappel sémantique est borné à l'étude active.

Défaut du 2026-09-11 : `vector_store.search` cherchait dans toutes les études.
Un message d'une autre étude pouvait donc être injecté dans une conversation.
sqlite-vec n'étant pas installé en test, on vérifie la requête produite (le
filtre d'étude, l'élargissement du KNN) en capturant le SQL exécuté.
"""
from __future__ import annotations

import asyncio
import sys
import types
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

# Stub sqlite_vec (extension absente en test) : la recherche l'invoque pour
# charger l'extension et ne dépend sinon que du SQL, qu'on capture.
_stub = types.ModuleType("sqlite_vec")
_stub.load = lambda *a, **k: None
_stub.loadable_path = lambda: "/dev/null"
sys.modules["sqlite_vec"] = _stub

from agent import vector_store as vs  # noqa: E402

_QGIS = (_ROOT / "agent" / "qgis_agent.py").read_text(encoding="utf-8")
_RECALL = (_ROOT / "agent" / "enrichers" / "memory_recall.py").read_text(encoding="utf-8")
_WORKER = (_ROOT / "agent" / "embed_worker.py").read_text(encoding="utf-8")


def _run(coro):
    # Un autre test de la suite ferme la boucle globale (asyncio.run). On en
    # recrée une si nécessaire plutôt que de dépendre de l'ordre des tests.
    try:
        loop = asyncio.get_event_loop()
        if loop.is_closed():
            raise RuntimeError
    except RuntimeError:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
    return loop.run_until_complete(coro)


class _FakeCursor:
    async def fetchall(self):
        return []


class _FakeConn:
    """Connexion aiosqlite factice qui note tout ce qu'on exécute."""

    def __init__(self, sink):
        self.sink = sink
        self.row_factory = None

    async def enable_load_extension(self, *a):
        pass

    async def execute(self, sql, params=None):
        self.sink.append((sql, params))
        return _FakeCursor()

    async def commit(self):
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False


def _capture_search(monkeypatch, **kwargs):
    sink: list = []
    monkeypatch.setattr(vs.aiosqlite, "connect", lambda *_a, **_k: _FakeConn(sink))
    # Un autre fichier de test peut remplacer sys.modules["sqlite_vec"] par un
    # stub sans loadable_path ; on garantit la référence utilisée par le module.
    monkeypatch.setattr(vs, "sqlite_vec", _stub)

    async def _fake_embed(_text):
        return [0.0] * vs.EMBED_DIM

    monkeypatch.setattr(vs, "embed", _fake_embed)
    _run(vs.search("une requête de test", **kwargs))
    # La requête de recherche est le dernier execute (après load_extension).
    sql, params = sink[-1]
    return sql, params


def test_avec_etude_les_messages_hors_etude_sont_ecartes(monkeypatch) -> None:
    sql, params = _capture_search(monkeypatch, study_id="E1")
    assert "c.study_id = ? OR c.study_id IS NULL" in sql
    assert "E1" in params


def test_include_global_false_exclut_le_transverse(monkeypatch) -> None:
    sql, params = _capture_search(monkeypatch, study_id="E1", include_global=False)
    assert "c.study_id = ?" in sql
    assert "OR c.study_id IS NULL" not in sql


def test_le_knn_est_elargi_quand_on_filtre(monkeypatch) -> None:
    """vec0 filtre APRÈS le KNN : un k étroit renverrait souvent 0 résultat."""
    sql, params = _capture_search(monkeypatch, top_k=5, study_id="E1")
    # params = [qblob, k_knn, ...filtres, top_k_final]
    assert params[1] >= 64
    assert params[-1] == 5


def test_sans_etude_pas_de_filtre_ni_d_elargissement(monkeypatch) -> None:
    sql, params = _capture_search(monkeypatch, top_k=5)
    assert "study_id" not in sql
    assert params[1] == 5  # k_knn == top_k


# ── Câblage : le périmètre est bien transmis de bout en bout ──────────────


def test_le_rappel_passe_l_etude_active() -> None:
    assert "study_id=state.get(\"study_id\")" in _RECALL


def test_l_agent_fournit_l_etude_au_rappel_et_aux_outils() -> None:
    assert '"study_id": _sid_actif' in _QGIS
    # L'outil memory_search borne aussi sa recherche à l'étude active.
    bloc = _QGIS.split('tool_name in ("memory_search"')[1][:900]
    assert "study_id=_sid" in bloc


def test_le_worker_indexe_les_messages_avec_leur_etude() -> None:
    assert "s.study_id" in _WORKER
    assert '"study_id": r[5]' in _WORKER
    # insights / sections / astuces : pas d'étude (transverse).
    assert 'item.get("study_id")' in _WORKER
