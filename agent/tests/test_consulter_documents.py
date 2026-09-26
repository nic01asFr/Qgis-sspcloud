"""Outil natif ``consulter_documents`` (lot L7) : format, bornage, perimetre.

Le hub est simule : on verifie ce que l'agent lui demande (etude active
seulement) et ce qu'il rend au modele (extraits sources, bornes).
"""
from __future__ import annotations

import asyncio
import json
import os
import sys
import tempfile
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

if "sqlite_vec" not in sys.modules:
    _vec = type(sys)("sqlite_vec")
    _vec.load = lambda *a, **k: None          # type: ignore[attr-defined]
    _vec.loadable_path = lambda: ""           # type: ignore[attr-defined]
    sys.modules["sqlite_vec"] = _vec

os.environ.setdefault("DATA_DIR", tempfile.mkdtemp(prefix="qgis_documents_"))
os.environ.setdefault("HUB_URL", "https://hub.test")
os.environ.setdefault("HUB_API_KEY", "test-key")

from agent import documents_etude as de  # noqa: E402
from agent import paquets_outils as po  # noqa: E402
from agent import qgis_agent as qa  # noqa: E402
from agent import vector_store  # noqa: E402

HUB = "https://hub.test"


def _run(coro):
    return asyncio.new_event_loop().run_until_complete(coro)


def _resultat(i: int, page: int | None = None, section: str | None = None,
              extrait: str | None = None, score: float = 1.0) -> dict:
    return {"segment": f"abcdefabcdef#{i}", "doc_id": "abcdefabcdef",
            "titre": "Rapport phase 1", "nom_fichier": "rapport.pdf",
            "page": page, "section": section, "score": score, "couverture": 1.0,
            "extrait": extrait or f"Les zones humides couvrent {i} hectares."}


class _Reponse:
    def __init__(self, data, status_code=200):
        self._data = data
        self.status_code = status_code

    def json(self):
        return self._data


class _Hub:
    """httpx.AsyncClient simule ; note les requetes recues."""

    requetes: list[tuple[str, dict]] = []
    reponse: _Reponse = _Reponse({})

    def __init__(self, *a, **k):
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False

    async def get(self, url, params=None, headers=None, **k):
        _Hub.requetes.append((url, params or {}))
        if isinstance(_Hub.reponse, Exception):
            raise _Hub.reponse
        return _Hub.reponse


@pytest.fixture
def hub(monkeypatch):
    _Hub.requetes = []
    _Hub.reponse = _Reponse({})
    monkeypatch.setattr(de.httpx, "AsyncClient", _Hub)
    monkeypatch.setenv("AGENT_DOCUMENTS_RECLASSEMENT", "0")
    de.reset_cache()
    yield _Hub
    de.reset_cache()


def _corps(resultats, statut="ok", en_cours=0):
    return {"statut": statut, "sid": "sid42", "en_cours": en_cours,
            "documents": [{"id": "abcdefabcdef", "titre": "Rapport phase 1"}],
            "resultats": resultats}


# ── Format ──────────────────────────────────────────────────────────────────

def test_resultat_source_et_consigne(hub):
    hub.reponse = _Reponse(_corps([_resultat(1, page=12), _resultat(2, section="Délais")]))
    r = _run(de.consulter_documents("zones humides", 2, "sid42", HUB, "cle"))
    assert r["statut"] == "ok"
    assert r["consigne"] == de.CONSIGNE
    s1, s2 = r["sources"]
    assert s1["id"] == "doc:Rapport phase 1#p12" and s1["page"] == 12
    assert s2["id"] == "doc:Rapport phase 1#Délais" and s2["section"] == "Délais"
    assert set(s1) == {"id", "titre", "score", "extrait", "page"}
    assert 0 < s1["score"] <= 1
    assert r["documents_consultables"] == 1
    assert r["verification"]["classement"] == "lexical"


def test_la_recherche_vise_l_etude_active_seulement(hub):
    hub.reponse = _Reponse(_corps([_resultat(1)]))
    _run(de.consulter_documents("digue", 3, "sid42", HUB, "cle"))
    (url, params), = hub.requetes
    assert url == f"{HUB}/studies/sid42/documents/recherche"
    assert params["q"] == "digue" and params["k"] == 9


def test_k_borne(hub):
    hub.reponse = _Reponse(_corps([_resultat(i) for i in range(20)]))
    r = _run(de.consulter_documents("zones", 50, "sid42", HUB, "cle"))
    assert len(r["sources"]) == de.K_MAX
    assert hub.requetes[-1][1]["k"] == 20
    r = _run(de.consulter_documents("zones", "n'importe", "sid42", HUB, "cle"))
    assert len(r["sources"]) == de.K_DEFAUT


def test_extraits_et_resultat_bornes_en_jetons(hub, monkeypatch):
    long = "zones humides " * 200
    hub.reponse = _Reponse(_corps([_resultat(i, page=i, extrait=long) for i in range(8)]))
    r = _run(de.consulter_documents("zones", 8, "sid42", HUB, "cle"))
    assert all(len(s["extrait"]) <= de.EXTRAIT_MAX for s in r["sources"])
    assert len(json.dumps(r, ensure_ascii=False)) <= de.RESULTAT_MAX_CARACTERES
    # Budget plus serre : les derniers extraits tombent, avec un avertissement.
    monkeypatch.setattr(de, "RESULTAT_MAX_CARACTERES", 1_500)
    r = _run(de.consulter_documents("zones", 8, "sid42", HUB, "cle"))
    assert 1 <= len(r["sources"]) < 8
    assert "Extraits tronqués au budget de contexte." in r["avertissements"]


def test_sans_etude_active(hub):
    r = _run(de.consulter_documents("zones", 3, None, HUB, "cle"))
    assert r["statut"] == "hors_perimetre" and not hub.requetes


def test_etude_sans_document_et_question_hors_corpus(hub):
    hub.reponse = _Reponse({**_corps([], statut="aucun_document"), "documents": []})
    r = _run(de.consulter_documents("zones", 3, "sid42", HUB, "cle"))
    assert r["statut"] == "vide" and r["documents_consultables"] == 0
    hub.reponse = _Reponse(_corps([], statut="vide", en_cours=1))
    r = _run(de.consulter_documents("cantines", 3, "sid42", HUB, "cle"))
    assert r["statut"] == "vide" and r["sources"] == []
    assert "sans inventer" in r["consigne"]
    assert any("en cours d'indexation" in a for a in r["avertissements"])


def test_erreurs_du_hub(hub):
    hub.reponse = _Reponse({"detail": "Étude introuvable"}, status_code=404)
    r = _run(de.consulter_documents("zones", 3, "sid42", HUB, "cle"))
    assert r["statut"] == "erreur" and "HTTP 404" in r["avertissements"][0]
    hub.reponse = ConnectionError("hub injoignable")
    r = _run(de.consulter_documents("zones", 3, "sid42", HUB, "cle"))
    assert r["statut"] == "erreur"
    r = _run(de.consulter_documents("   ", 3, "sid42", HUB, "cle"))
    assert r["statut"] == "erreur" and r["avertissements"] == ["Question vide."]


def test_reclassement_semantique_puis_repli_lexical(hub, monkeypatch):
    monkeypatch.setenv("AGENT_DOCUMENTS_RECLASSEMENT", "1")
    hub.reponse = _Reponse(_corps([_resultat(1, page=1, score=1.0),
                                   _resultat(2, page=2, score=0.9)]))
    # La page 2 est semantiquement bien plus proche de la question.
    vecteurs = [[1.0, 0.0], [0.0, 1.0], [1.0, 0.0]]
    with patch.object(vector_store, "embed_batch", new=AsyncMock(return_value=vecteurs)):
        r = _run(de.consulter_documents("zones", 2, "sid42", HUB, "cle"))
    assert [s["page"] for s in r["sources"]] == [2, 1]
    assert r["verification"]["classement"] == "lexical+semantique"
    with patch.object(vector_store, "embed_batch",
                      new=AsyncMock(side_effect=RuntimeError("API embedding muette"))):
        r = _run(de.consulter_documents("zones", 2, "sid42", HUB, "cle"))
    assert [s["page"] for s in r["sources"]] == [1, 2]
    assert r["verification"]["classement"] == "lexical"


# ── L2 et resume ────────────────────────────────────────────────────────────

def test_ligne_l2():
    assert de.ligne_l2(None) is None
    assert de.ligne_l2({"indexes": 0, "en_cours": 0, "titres": []}) is None
    assert "en cours d'indexation" in de.ligne_l2({"indexes": 0, "en_cours": 2, "titres": []})
    ligne = de.ligne_l2({"indexes": 1, "en_cours": 0, "titres": ["Rapport"]})
    assert ligne == ("1 document d'étude consultable (« Rapport ») : "
                     "consulter_documents, en citant la source.")


def test_resume_documents_et_cache(hub):
    hub.reponse = _Reponse({"documents": [
        {"titre": "A", "statut": "indexe"}, {"titre": "B", "statut": "extraction"},
        {"titre": "C", "statut": "erreur"}]})
    r = _run(de.resume_documents(HUB, "cle", "sid42"))
    assert r == {"indexes": 1, "en_cours": 1, "titres": ["A"]}
    _run(de.resume_documents(HUB, "cle", "sid42"))
    assert len(hub.requetes) == 1  # cache 30 s
    assert _run(de.resume_documents(HUB, "cle", None)) is None


# ── Paquet et exposition ────────────────────────────────────────────────────

@pytest.mark.parametrize("message", [
    "Que dit le cahier des charges sur les délais ?",
    "Selon le rapport, combien d'hectares de zones humides ?",
    "Résume le document que j'ai déposé",
    "D'après la note de cadrage, quel est le périmètre ?",
])
def test_declencheurs_du_paquet_documents(message):
    assert "documents" in po.paquets_par_intention(message)


def test_paquet_documents_absent_d_une_question_cartographique():
    assert "documents" not in po.paquets_par_intention("Combien de bâtiments dans ma zone ?")


def _agent_hors_ligne(documents: dict | None) -> qa.QGISAgent:
    agent = qa.QGISAgent(username="user", session_id="study:sid42", profile_id="standard")
    agent._tools_cache = [
        {"type": "function", "function": {"name": "get_project_info"}},
        de.SCHEMA,
        {"type": "function", "function": {"name": "export_pdf"}},
    ]
    agent._documents_etude = documents
    return agent


def _noms(outils):
    return {po.nom_outil(o) for o in outils}


def test_outil_expose_seulement_si_l_etude_a_des_documents(monkeypatch):
    monkeypatch.setitem(qa._PROFILES_CACHE, "standard", {})
    sans = _agent_hors_ligne({"indexes": 0, "en_cours": 1, "titres": []})
    assert "consulter_documents" not in _noms(_run(sans._outils_exposes({"documents"})))
    avec = _agent_hors_ligne({"indexes": 2, "en_cours": 0, "titres": ["A", "B"]})
    assert "consulter_documents" in _noms(_run(avec._outils_exposes({"documents"})))
    # Sans l'intention, le paquet reste replie (le filet demander_outils le rouvre).
    assert "consulter_documents" not in _noms(_run(avec._outils_exposes(set())))


def test_dispatch_natif_sur_l_etude_active():
    appel = AsyncMock(return_value={"statut": "ok", "sources": []})
    with patch.object(qa, "_resolve_active_sid", new=AsyncMock(return_value="sid42")), \
         patch.object(de, "consulter_documents", new=appel):
        sortie = _run(qa._call_mcp_tool_raw(
            "consulter_documents", {"question": "digue", "k": 3}, "user"))
    assert json.loads(sortie)["statut"] == "ok"
    args = appel.await_args.args
    assert args[:3] == ("digue", 3, "sid42")


def test_schema_consigne_citer_et_ne_pas_extrapoler():
    desc = de.SCHEMA["function"]["description"]
    assert "cite la source" in desc and "n'extrapole pas" in desc
    assert de.SCHEMA["function"]["parameters"]["required"] == ["question"]
    assert po.paquet_de("consulter_documents") == "documents"
