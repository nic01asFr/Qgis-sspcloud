"""Menus du chat : historique, conversation courante, memoire.

Constats faits en naviguant sur le service, dans la page seule et dans la
colonne de 299 px du bureau.
"""

from __future__ import annotations

import asyncio
import re
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

from agent import insight_extractor, memory  # noqa: E402
from agent import main as agent_main  # noqa: E402

_CHAT = (_ROOT / "templates" / "chat.html").read_text(encoding="utf-8")
_ENTETES = {"Authorization": "Bearer cle-essai-menus", "X-Hub-Proxy-User": "essai"}


def _run(coro):
    return asyncio.get_event_loop().run_until_complete(coro)


def _page(monkeypatch, tmp_path, etude_active: str | None) -> str:
    monkeypatch.setattr(memory, "_DATA_DIR", tmp_path)
    monkeypatch.setattr(memory, "_DB_PATH", tmp_path / "menus.db")
    monkeypatch.setenv("HUB_API_KEY", "cle-essai-menus")
    monkeypatch.setenv("ONYXIA_USER", "essai")

    async def _etude():
        return etude_active

    monkeypatch.setattr(agent_main, "_fetch_active_study_id", _etude)

    async def _preparer():
        await memory.init()
        for sid, etude, titre in (
            ("s-a1", "etude-a", "Surface des zones candidates"),
            ("s-a2", "etude-a", "Couches du projet actif"),
            ("s-b1", "etude-b", "Densite du bati a Montpellier"),
        ):
            await memory.create_session(sid, "user")
            await memory.set_session_summary(sid, titre)
            await memory.set_session_study(sid, etude)

    _run(_preparer())
    r = TestClient(agent_main.app).get("/?new=1", headers=_ENTETES)
    assert r.status_code == 200
    return r.text


# ── Historique ───────────────────────────────────────────────────────────


def test_l_historique_ne_montre_que_les_conversations_de_l_etude(monkeypatch, tmp_path) -> None:
    """Il melangeait les conversations de toutes les etudes."""
    html = _page(monkeypatch, tmp_path, "etude-a")
    assert "Surface des zones candidates" in html
    assert "Couches du projet actif" in html
    assert "Densite du bati a Montpellier" not in html
    assert "Conversations de l&#39;étude" in html or "Conversations de l'étude" in html


def test_sans_etude_connue_l_historique_montre_tout(monkeypatch, tmp_path) -> None:
    html = _page(monkeypatch, tmp_path, None)
    assert "Densite du bati a Montpellier" in html
    assert "Conversations récentes" in html


def test_l_historique_en_propose_plus_de_cinq() -> None:
    """Le menu prevoit 20 entrees ; le serveur n'en envoyait que 5."""
    source = (_ROOT / "agent" / "main.py").read_text(encoding="utf-8")
    appel = source.split("sessions = await memory.get_recent_sessions(")[1][:120]
    assert "limit=20" in appel
    assert "study_id=active_study_id" in appel


def test_les_conversations_se_choisissent_au_clavier() -> None:
    """Des <div> cliquables : inaccessibles a la touche Tab."""
    lateral = _CHAT.split("{% for s in sessions[:8] %}")[1].split("{% endfor %}")[0]
    assert "<button" in lateral and "<div class=\"sidebar-item\"" not in lateral


def test_deux_conversations_au_meme_titre_se_distinguent() -> None:
    assert _CHAT.count('class="quand" data-debut="{{ s.started_at') == 2
    assert "function afficherDatesConversations()" in _CHAT


def test_le_menu_tient_dans_la_colonne_du_bureau() -> None:
    """320 px dans une colonne de 299 : 29 px coupes a gauche."""
    assert re.search(r"\.history-popover\{[^}]*width:min\(320px, calc\(100% - 16px\)\)", _CHAT)


def test_un_seul_bouton_nouvelle_conversation() -> None:
    """« Nouvelle analyse » et « Nouvelle » faisaient deux choses differentes."""
    assert "function newSession()" not in _CHAT
    assert "newSession()" not in _CHAT
    assert _CHAT.count('onclick="startNewConversation()"') == 2


# ── Chargement d'une conversation ────────────────────────────────────────


def _load_session() -> str:
    return _CHAT.split("function loadSession(sid) {")[1].split("\nfunction ")[0]


def test_un_echec_de_chargement_ne_change_pas_de_conversation() -> None:
    """L'ecran gardait l'ancienne conversation, le prochain message partait
    dans la nouvelle."""
    corps = _load_session()
    lecture = corps.index("return r.json();")
    assert corps.index("document.getElementById('session-id').value = sid;") > lecture
    assert "if (!r.ok)" in corps
    assert ".catch(" in corps


def test_une_reponse_vide_enregistree_n_affiche_pas_une_bulle_blanche() -> None:
    assert "Aucune réponse n’a été enregistrée pour ce message." in _load_session()


def test_on_ne_change_pas_de_conversation_pendant_un_tour() -> None:
    assert "Attends la fin de la réponse en cours" in _load_session()


def test_une_seule_definition_d_escape_html() -> None:
    """La seconde ecrasait la premiere et rendait « null » pour une valeur absente."""
    assert _CHAT.count("function escapeHtml(") == 1


# ── Clavier ──────────────────────────────────────────────────────────────


def test_echap_rend_le_focus_et_garde_une_saisie_de_memoire() -> None:
    echap = _CHAT.split("document.addEventListener('keydown', (e) => {\n      if (e.key !== 'Escape') return;")[1][:1600]
    assert "?.focus()" in echap
    assert ".mem-md-actions.dirty" in echap


def test_les_menus_annoncent_leur_etat() -> None:
    for bouton in ("btn-history", "btn-tech-details", "btn-memory"):
        assert "getElementById('%s')?.setAttribute('aria-expanded'" % bouton in _CHAT


# ── Faits memorises ──────────────────────────────────────────────────────


def test_un_fait_propose_peut_etre_garde() -> None:
    """Le tiroir promettait qu'on pouvait les « reprendre », sans action pour le faire."""
    assert "async function confirmInsight(btn)" in _CHAT
    corps = _CHAT.split("async function confirmInsight(btn)")[1][:600]
    assert "fetch('/user/insights'" in corps and "method: 'POST'" in corps


def test_les_faits_ont_des_libelles_lisibles() -> None:
    assert "function _libelleFait(cle)" in _CHAT
    assert "metier: 'métier'" in _CHAT


def test_l_extracteur_n_enregistre_pas_ce_qui_decrit_l_etude() -> None:
    """« zone_etude_actuelle = Saint-Martin » suivait l'utilisateur partout."""
    for cle in ("zone_etude_actuelle", "sujet_etude_actuel", "projet_en_cours", "theme_etude"):
        assert insight_extractor._decrit_l_etude_en_cours(cle), cle
    for cle in ("profil_metier", "zone_habituelle", "format_export", "methode_recurrente"):
        assert not insight_extractor._decrit_l_etude_en_cours(cle), cle
    assert "N'extrais RIEN qui décrive l'étude" in insight_extractor._SYSTEM_PROMPT
