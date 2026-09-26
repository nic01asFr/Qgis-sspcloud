"""Rappel sémantique borné à l'étude active, hors conversation courante.

Défaut D6 (mesure live du 2026-09-26) : 2 à 4 anciens messages injectés à
chaque tour, dont un S1 raté de l'étude « Saint-Martin » dans l'étude
« bac-a-sable banc ». Cause : l'étude d'un message est figée dans l'index à
l'indexation ; un message indexé sans étude passait pour transverse.

La base des messages est une vraie SQLite temporaire ; l'index vectoriel est
simulé (sqlite-vec absent en test) : il renvoie ce qu'une recherche non
filtrée renverrait, y compris un chunk hérité sans étude.
"""
from __future__ import annotations

import asyncio
import sys
import types
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

if "sqlite_vec" not in sys.modules:
    _vec = types.ModuleType("sqlite_vec")
    _vec.load = lambda *a, **k: None          # type: ignore[attr-defined]
    _vec.loadable_path = lambda: ""           # type: ignore[attr-defined]
    sys.modules["sqlite_vec"] = _vec

from agent import memory  # noqa: E402
from agent import vector_store  # noqa: E402
from agent.enrichers import memory_recall as mr  # noqa: E402

_QUESTION = "Combien de bâtiments dans la commune ?"


def _run(coro):
    try:
        boucle = asyncio.get_event_loop()
        if boucle.is_closed():
            raise RuntimeError("boucle fermee")
    except RuntimeError:
        boucle = asyncio.new_event_loop()
        asyncio.set_event_loop(boucle)
    return boucle.run_until_complete(coro)


@pytest.fixture
def base(tmp_path, monkeypatch):
    """Deux études, trois conversations :
    - « banc » (E_BANC) : conversation courante + une conversation antérieure ;
    - « saint-martin » (E_SM) : un S1 raté, indexé SANS étude (hérité).
    """
    monkeypatch.setattr(memory, "_DATA_DIR", tmp_path)
    monkeypatch.setattr(memory, "_DB_PATH", tmp_path / "rappel.db")

    async def _preparer():
        await memory.init()
        ids = {}
        for sess, etude in (("conv-courante", "E_BANC"),
                            ("conv-ancienne", "E_BANC"),
                            ("conv-saint-martin", "E_SM"),
                            ("conv-sans-etude", None)):
            await memory.create_session(sess, "user")
            if etude:
                await memory.set_session_study(sess, etude)
        textes = {
            "courant": ("conv-courante", "assistant",
                        "La couche bâti compte 54 557 bâtiments dans la commune."),
            "ancien": ("conv-ancienne", "assistant",
                       "Dans la commune on compte 112 816 bâtiments (emprise bbox)."),
            "ancien_sans_chiffre": ("conv-ancienne", "user",
                                    "Peux-tu découper les bâtiments à la commune ?"),
            "saint_martin": ("conv-saint-martin", "assistant",
                             "Saint-Martin : 8 412 bâtiments dans la commune."),
            "sans_etude": ("conv-sans-etude", "assistant",
                           "Exploration libre : 3 021 bâtiments dans la commune."),
        }
        for cle, (sess, role, texte) in textes.items():
            await memory.add_message(sess, role, texte)
            msgs = await memory.get_session_messages(sess)
            ids[cle] = str(msgs[-1]["id"])
        return ids, textes

    return _run(_preparer())


def _hits(ids, textes, sim=0.8, avec_fait=True):
    """Ce que renverrait l'index : tout, y compris le chunk hérité (NULL)."""
    hits = [
        {"source_type": "message", "source_id": ids[k], "text": textes[k][2],
         "similarity": sim, "created_at": 1_758_800_000,
         "metadata": {"session_id": textes[k][0], "role": textes[k][1]}}
        for k in ("courant", "ancien", "saint_martin", "sans_etude",
                  "ancien_sans_chiffre")
    ]
    if avec_fait:
        hits.append({"source_type": "insight", "source_id": "1",
                     "text": "palette: YlOrRd", "similarity": sim,
                     "created_at": 1_758_800_000,
                     "metadata": {"key": "palette"}})
    return hits


def _enrichir(monkeypatch, hits, state):
    async def _recherche(*a, **k):
        return hits
    monkeypatch.setattr(vector_store, "search", _recherche)
    return _run(mr.enrich(_QUESTION, state))


def test_ni_autre_etude_ni_conversation_courante(base, monkeypatch) -> None:
    ids, textes = base
    r = _enrichir(monkeypatch, _hits(ids, textes),
                  {"study_id": "E_BANC", "recent_message_ids": []})
    assert r is not None
    # Autre étude, même indexée sans étude : jamais.
    assert "Saint-Martin" not in r.summary
    assert "Exploration libre" not in r.summary
    # Conversation courante inconnue ici : son exclusion a son propre test.
    assert "112 816" in r.summary


def test_la_conversation_courante_est_exclue(base, monkeypatch) -> None:
    ids, textes = base
    # L'historique ne contient pas le message « courant » (hors fenêtre de
    # 20), mais le dernier message visible désigne la conversation.
    r = _enrichir(monkeypatch, _hits(ids, textes),
                  {"study_id": "E_BANC", "recent_message_ids": [ids["courant"]]})
    assert "54 557" not in r.summary
    # Même règle quand l'appelant fournit la conversation explicitement.
    r = _enrichir(monkeypatch, _hits(ids, textes),
                  {"study_id": "E_BANC", "recent_message_ids": [],
                   "session_id": "conv-courante"})
    assert "54 557" not in r.summary
    assert "112 816" in r.summary


def test_un_chiffre_rappele_porte_sa_date_et_sa_portee(base, monkeypatch) -> None:
    ids, textes = base
    r = _enrichir(monkeypatch, _hits(ids, textes),
                  {"study_id": "E_BANC", "session_id": "conv-courante"})
    ligne = next(l for l in r.summary.splitlines() if "112 816" in l)
    assert "autre conversation de cette étude" in ligne
    assert mr.MENTION_CHIFFRES in ligne
    assert "20" in ligne.split("·")[1]  # date AAAA-MM-JJ
    sans = next(l for l in r.summary.splitlines() if "découper les bâtiments" in l)
    assert mr.MENTION_CHIFFRES not in sans


def test_sans_etude_active_aucun_message(base, monkeypatch) -> None:
    ids, textes = base
    r = _enrichir(monkeypatch, _hits(ids, textes), {"recent_message_ids": []})
    assert r is not None
    assert all("message" not in l for l in r.summary.splitlines()[1:])
    assert "palette: YlOrRd" in r.summary


def test_seuil_et_plafond(base, monkeypatch) -> None:
    ids, textes = base
    # 0,60 : sous le seuil relevé (0,55 auparavant) : rien.
    r = _enrichir(monkeypatch, _hits(ids, textes, sim=0.60),
                  {"study_id": "E_BANC", "session_id": "conv-courante"})
    assert r is None
    r = _enrichir(monkeypatch, _hits(ids, textes, sim=0.9),
                  {"study_id": "E_BANC", "session_id": "conv-courante"})
    assert len(r.data["hits"]) <= mr._MAX_HITS


def test_message_supprime_non_rappele(base, monkeypatch) -> None:
    ids, textes = base
    hits = _hits(ids, textes, avec_fait=False)
    for h in hits:
        h["source_id"] = "999999"
    r = _enrichir(monkeypatch, hits, {"study_id": "E_BANC"})
    assert r is None
