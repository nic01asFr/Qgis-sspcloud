"""Un item que l'API refuse ne doit plus barrer toute la file d'indexation.

Mesure du 2026-09-17 en production : « embed_batch fail (8 items): 400 Bad
Request » toutes les 90 s, sans fin. Un message de 80 896 caracteres -- une
reponse contenant une capture en base64 -- faisait echouer son lot ; l'echec
rendait 0 sans rien marquer, donc le tick suivant reprenait le MEME lot.
139 items attendaient derriere lui, et la memoire semantique ne s'enrichissait
plus du tout.

Deux causes : le texte etait tronque a 8000 caracteres a l'archivage mais
expedie ENTIER a l'API, et rien n'ecartait un item fautif.
"""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

if "sqlite_vec" not in sys.modules:
    _vec = type(sys)("sqlite_vec")
    _vec.load = lambda *a, **k: None          # type: ignore[attr-defined]
    _vec.loadable_path = lambda: ""           # type: ignore[attr-defined]
    sys.modules["sqlite_vec"] = _vec

from agent import embed_worker as ew  # noqa: E402


def _run(coro):
    try:
        boucle = asyncio.get_event_loop()
        if boucle.is_closed():
            raise RuntimeError("boucle fermee")
    except RuntimeError:
        boucle = asyncio.new_event_loop()
        asyncio.set_event_loop(boucle)
    return boucle.run_until_complete(coro)


def _item(identifiant: str, texte: str) -> dict:
    return {
        "source_type": "message",
        "source_id": identifiant,
        "text": texte,
        "username": "user",
        "study_id": None,
        "metadata": None,
    }


@pytest.fixture()
def journal(monkeypatch):
    """Capture ce qui est insere, sans base de donnees."""
    inseres: list[dict] = []

    async def _inserer(db, items, vecteurs):
        for it, v in zip(items, vecteurs):
            inseres.append({"id": it["source_id"], "vecteur": v})

    monkeypatch.setattr(ew, "_insert_batch", _inserer)
    monkeypatch.setattr(ew.vs, "EMBED_DIM", 4, raising=False)
    return inseres


def _api_qui_refuse(motif: str):
    """Simule l'API : elle refuse tout lot contenant l'item empoisonne."""
    appels: list[list[str]] = []

    async def _embed_batch(textes):
        appels.append(list(textes))
        if any(motif in t for t in textes):
            raise RuntimeError("400 Bad Request")
        return [[1.0, 0.0, 0.0, 0.0] for _ in textes]

    return _embed_batch, appels


# ── La file avance malgre l'item fautif ──────────────────────────────────


def test_les_bons_items_sont_indexes_malgre_un_fautif(journal, monkeypatch):
    embed, _ = _api_qui_refuse("POISON")
    monkeypatch.setattr(ew.vs, "embed_batch", embed)

    items = [_item(str(i), f"texte {i}") for i in range(8)]
    items[3]["text"] = "POISON"

    traites = _run(ew._indexer(None, items))

    assert traites == 8, "tous les items doivent quitter la file"
    assert {e["id"] for e in journal} == {str(i) for i in range(8)}


def test_l_item_fautif_est_ecarte_avec_un_vecteur_nul(journal, monkeypatch):
    embed, _ = _api_qui_refuse("POISON")
    monkeypatch.setattr(ew.vs, "embed_batch", embed)

    items = [_item(str(i), f"texte {i}") for i in range(4)]
    items[1]["text"] = "POISON"

    _run(ew._indexer(None, items))

    fautif = next(e for e in journal if e["id"] == "1")
    assert fautif["vecteur"] == [0.0] * 4, "ecarte, donc loin de toute recherche"
    sain = next(e for e in journal if e["id"] == "0")
    assert sain["vecteur"] != [0.0] * 4


def test_la_dichotomie_reste_bornee(journal, monkeypatch):
    """Huit items, un fautif : quelques appels, pas un par item."""
    embed, appels = _api_qui_refuse("POISON")
    monkeypatch.setattr(ew.vs, "embed_batch", embed)

    items = [_item(str(i), f"texte {i}") for i in range(8)]
    items[5]["text"] = "POISON"

    _run(ew._indexer(None, items))

    assert len(appels) <= 8, f"{len(appels)} appels pour isoler un item"


def test_un_lot_entierement_sain_tient_en_un_appel(journal, monkeypatch):
    embed, appels = _api_qui_refuse("POISON")
    monkeypatch.setattr(ew.vs, "embed_batch", embed)

    items = [_item(str(i), f"texte {i}") for i in range(8)]
    traites = _run(ew._indexer(None, items))

    assert traites == 8
    assert len(appels) == 1


# ── Le texte envoye est borne ────────────────────────────────────────────


def test_le_texte_envoye_a_l_api_est_tronque(journal, monkeypatch):
    envoyes: list[str] = []

    async def _embed_batch(textes):
        envoyes.extend(textes)
        return [[1.0, 0.0, 0.0, 0.0] for _ in textes]

    monkeypatch.setattr(ew.vs, "embed_batch", _embed_batch)
    monkeypatch.setattr(ew, "_TEXTE_MAX", 100)

    _run(ew._indexer(None, [_item("1", "x" * 80_896)]))

    assert len(envoyes[0]) == 100
