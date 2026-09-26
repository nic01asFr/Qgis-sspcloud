"""Budget de contexte : estimation, plafonnement de la L3, releve par tour.

Calibrage de l'estimateur (2026-09-24) contre le tokenizer de
Qwen/Qwen3.6-35B-A3B : _QGIS_ESSENTIALS = 6 939 jetons, prompt du profil
standard = 88, schemas des 89 outils du profil standard = 16 612.
"""
from __future__ import annotations

import asyncio
import json
import os
import sys
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

os.environ.setdefault("HUB_URL", "https://user-nicolaslaval-qgis.user.lab.sspcloud.fr")
os.environ.setdefault("HUB_API_KEY", "test-key")

from agent import context_budget as cb  # noqa: E402
from agent import memory  # noqa: E402
from agent import qgis_agent as qa  # noqa: E402


def _run(coro):
    try:
        boucle = asyncio.get_event_loop()
        if boucle.is_closed():
            raise RuntimeError("boucle fermee")
    except RuntimeError:
        boucle = asyncio.new_event_loop()
        asyncio.set_event_loop(boucle)
    return boucle.run_until_complete(coro)


# ── Estimation ───────────────────────────────────────────────────────────────

def test_estimation_calee_sur_le_tokenizer_qwen() -> None:
    """A +-10 % des 6 939 jetons mesures avec le vrai tokenizer."""
    n = cb.estimer_tokens(qa.QGISAgent._QGIS_ESSENTIALS)
    assert 6_939 * 0.9 <= n <= 6_939 * 1.1, n


def test_les_chiffres_comptent_un_jeton_chacun() -> None:
    """Qwen code chaque chiffre a part : une bbox coute bien plus que sa
    longueur divisee par 4."""
    assert cb.estimer_tokens("5.2694, 43.4583") >= 10
    assert cb.estimer_tokens("") == 0
    assert cb.estimer_tokens(None) == 0


def test_les_essentiels_ne_depassent_pas_leur_plafond() -> None:
    """Garde-fou : le bloc fixe ne doit plus grossir (cf. proposition de
    decoupage en regles permanentes / regles contextuelles)."""
    assert cb.estimer_tokens(qa.QGISAgent._QGIS_ESSENTIALS) <= cb.PLAFONDS["essentiels"]


def test_les_schemas_d_outils_sont_comptes_en_json() -> None:
    outils = [{"type": "function", "function": {
        "name": "smart_load", "description": "Charge une source.",
        "parameters": {"type": "object", "properties": {}}}}]
    assert cb.estimer_tokens_outils(outils) == cb.estimer_tokens(
        json.dumps(outils[0], ensure_ascii=False))
    assert cb.estimer_tokens_outils([]) == 0


def test_tokenizer_exact_si_disponible_hors_ligne(monkeypatch) -> None:
    class _Faux:
        def encode(self, texte, add_special_tokens=False):
            return type("E", (), {"ids": list(range(42))})()

    monkeypatch.setattr(cb, "_TOKENIZER", _Faux())
    monkeypatch.setattr(cb, "_TOKENIZER_CHARGE", True)
    assert cb.estimer_tokens("peu importe") == 42


# ── Plafonnement de la L3 ────────────────────────────────────────────────────

def _cout_l3(elements: list[str]) -> int:
    return cb.estimer_tokens(cb.TITRE_L3 + "\n" + "\n".join(f"- {e}" for e in elements))


def test_une_memoire_courte_passe_intacte() -> None:
    elements = ["Urbaniste à la DDTM 13.", "Recettes disponibles : densite_bati"]
    assert cb.borner_l3(elements) == elements


def test_une_memoire_longue_est_coupee_au_plafond_et_le_dit() -> None:
    doc = "--- À PROPOS DE MOI ---\n" + "\n".join(
        f"Ligne de mémoire numéro {i} avec un peu de texte." for i in range(400))
    elements = [doc, "Faits auto-détectés sur l'user :", "  📌 cle = valeur"]
    borne = cb.borner_l3(elements)
    assert borne[-1] == cb.MENTION_L3_TRONQUEE
    assert _cout_l3(borne) <= cb.PLAFONDS["l3"]
    # La priorite est gardee : le debut du document edite par l'utilisateur.
    assert borne[0].startswith("--- À PROPOS DE MOI ---")


def test_les_elements_prioritaires_passent_avant_les_suivants() -> None:
    elements = [f"élément {i} " + "x" * 400 for i in range(20)]
    borne = cb.borner_l3(elements, plafond=300)
    assert borne[0].startswith("élément 0")
    assert "élément 19" not in "\n".join(borne)
    assert _cout_l3(borne) <= 300


# ── Releve ───────────────────────────────────────────────────────────────────

_CTX = (
    "=== Étude en cours ===\n- Zone d'étude active : « Aix »\n\n"
    "=== Suggestions next-action (déterministes, non-LLM) ===\n- publie\n\n"
    f"{cb.TITRE_L3}\n- Urbaniste\n- === pas un titre ===\n"
)


def test_le_contexte_se_decoupe_en_l2_suggestions_l3() -> None:
    parts = cb.decouper_contexte(_CTX)
    assert parts["l2"].startswith("=== Étude en cours ===")
    assert "Suggestions" in parts["suggestions"] and "Aix" not in parts["suggestions"]
    assert parts["l3"].startswith(cb.TITRE_L3)
    # Un « === » ecrit par l'utilisateur reste dans sa memoire.
    assert "pas un titre" in parts["l3"]


def test_releve_complet_et_ligne_de_journal() -> None:
    prompt = "# IDENTITY\n\nTu es...\n\n---\n\n# CONTEXT\n\n" + _CTX
    sys_ = cb.releve_systeme(prompt, identite="Tu es...", contexte=_CTX)
    assert sum(v for k, v in sys_.items() if k != "systeme") == sys_["systeme"]
    tour = cb.releve_tour(sys_, [{"type": "function", "function": {"name": "a"}}],
                          [{"role": "user", "content": "bonjour"}], "et ensuite ?")
    assert tour["total"] == tour["systeme"] + tour["outils"] + tour["historique"] + tour["message"]
    ligne = cb.ligne_journal(tour)
    assert ligne.startswith("total=")
    assert "outils=" in ligne and "(1)" in ligne and "\n" not in ligne


def test_un_depassement_est_signale_dans_la_ligne() -> None:
    ligne = cb.ligne_journal({"total": 30_000, "l3": 5_000, "systeme_et_outils": 24_000})
    assert "[depasse: l3>1200, systeme_et_outils>14000(cible)]" in ligne


# ── Le releve est pose a chaque tour ─────────────────────────────────────────

class _Flux:
    def __init__(self):
        fin = {"choices": [{"delta": {"content": "Voilà."}, "finish_reason": "stop"}]}
        self._lignes = ["data: " + json.dumps(fin), "data: [DONE]"]
        self.status_code = 200

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def aiter_lines(self):
        for ligne in self._lignes:
            yield ligne


class _ClientModele:
    def __init__(self, *a, **k):
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    def stream(self, *a, **k):
        return _Flux()


def test_chaque_tour_journalise_son_budget(monkeypatch, caplog) -> None:
    async def _rien(*a, **k):
        return None

    monkeypatch.setattr(memory, "add_message", _rien)
    monkeypatch.setattr(qa, "_resolve_model", AsyncMock(return_value="m"))
    monkeypatch.setattr(qa.httpx, "AsyncClient", _ClientModele)

    async def _prompt(self, user_message=None):
        self._releve_systeme = cb.releve_systeme("consigne systeme")
        return "consigne systeme"

    async def _outils(self):
        return [{"type": "function", "function": {"name": "smart_load"}}]

    monkeypatch.setattr(qa.QGISAgent, "_build_system_prompt", _prompt)
    monkeypatch.setattr(qa.QGISAgent, "_get_tools", _outils)
    agent = qa.QGISAgent(username="user", session_id="tour-budget")

    async def _tour():
        return [c async for c in agent.chat_stream(
            "question", history=[{"role": "assistant", "content": "avant"}])]

    with caplog.at_level("INFO", logger=qa.log.name):
        _run(_tour())

    r = agent.dernier_releve_contexte
    assert r["n_outils"] == 1 and r["n_historique"] == 1
    assert r["systeme"] > 0 and r["message"] > 0
    assert any("budget contexte session=tour-budget total=" in m for m in caplog.messages)


# ── G9 : la portee part bien vers la L2 ──────────────────────────────────────

@pytest.mark.parametrize("session_id, attendu", [
    ("assist:sid42:cid:c1", "assist_component"),
    ("study:sid42:recipe:densite_bati", "recipe_run"),
    ("study:sid42", "desk"),
    ("5d0c7b1e-uuid-historique", None),   # legacy -> vue desk, sans alerte
])
def test_la_portee_est_transmise_au_constructeur_de_contexte(session_id, attendu) -> None:
    agent = qa.QGISAgent(username="user", session_id=session_id)
    capture = AsyncMock(return_value="")
    with patch.object(qa, "_load_profile_prompt", return_value="P"), \
         patch.object(qa, "_resolve_active_sid", new=AsyncMock(return_value=None)), \
         patch.object(qa.briques_client, "fetch_briques_rules",
                      new=AsyncMock(return_value=([], []))), \
         patch.object(agent, "_fetch_active_study_context",
                      new=AsyncMock(return_value=(None, None))), \
         patch.object(agent, "_fetch_project_state", new=AsyncMock(return_value=None)), \
         patch.object(agent, "_fetch_study_artifacts_summary",
                      new=AsyncMock(return_value=None)), \
         patch.object(memory, "get_session_messages", new=AsyncMock(return_value=[])), \
         patch.object(memory, "get_session_tags",
                      new=AsyncMock(return_value={"cid": "ancien", "note": "x"})), \
         patch.object(memory, "build_context_summary", new=capture):
        _run(agent._build_system_prompt(user_message=None))

    kwargs = capture.await_args.kwargs
    assert kwargs["context_kind"] == attendu
    if attendu:
        # Le session_id prime sur un tag perime ; les autres tags passent.
        assert kwargs["scope_ids"]["sid"] == "sid42"
        assert kwargs["scope_ids"].get("note") == "x"
        if attendu == "assist_component":
            assert kwargs["scope_ids"]["cid"] == "c1"
        assert kwargs["hub_url"] == qa._HUB_URL
    else:
        assert kwargs["scope_ids"] == {}
