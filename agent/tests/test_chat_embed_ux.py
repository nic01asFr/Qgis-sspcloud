"""UX chat embed : mémoire dans le chat, nouvelle conv sans alert navigateur."""

from __future__ import annotations

from pathlib import Path

_CHAT = (Path(__file__).resolve().parents[1] / "templates" / "chat.html").read_text(
    encoding="utf-8"
)


def test_bouton_memoire_visible_en_toolbar() -> None:
    assert 'id="btn-memory"' in _CHAT
    assert "toggleMemoryDrawer()" in _CHAT
    assert 'id="memory-drawer"' in _CHAT


def test_drawer_memoire_pas_masque_en_embed() -> None:
    """En embed, le drawer interne remplace l'ancien onglet Mémoire du desk."""
    assert "body.embed #btn-memory" not in _CHAT
    assert "body.embed .memory-drawer" not in _CHAT
    assert "body.embed #memory-drawer" not in _CHAT
    assert "body.embed #btn-memory,\nbody.embed .memory-drawer{display:none" not in _CHAT


def test_embed_masque_footer_et_lien_bureau() -> None:
    chunk = _CHAT.split("Mode embed")[1].split("Sprint UX-1")[0]
    assert "body.embed .qs-pied" in chunk
    assert "display:none !important" in chunk
    assert "body.embed .chat-toolbar a.tool-btn-link" in chunk
    assert "body.embed .chat-toolbar .tool-btn-label{display:none}" in chunk


def test_embed_ne_rend_pas_le_footer_html() -> None:
    """Le pied de page n'est pas envoyé au navigateur en ?embed=1."""
    foot_start = _CHAT.index('class="qs-pied"')
    assert _CHAT.rfind("{% if not embed %}", 0, foot_start) < foot_start
    foot_end = _CHAT.index("{% endif %}", foot_start)
    assert foot_end > foot_start


def test_nouvelle_conversation_sans_confirm() -> None:
    chunk = _CHAT.split("function startNewConversation()")[1].split("function toggleHistoryPopover")[0]
    assert "confirm(" not in chunk
    assert "showChatToast(" in chunk
    assert "embed=1" in chunk


def test_drawer_charge_memoire_et_insights() -> None:
    assert "fetch('/user/memory')" in _CHAT
    assert "fetch('/user/insights')" in _CHAT
    assert "async function loadMemoryPanel()" in _CHAT


def test_drawer_memoire_copy_compact() -> None:
    """Titre produit court ; pas de jargon « agentique » ni conf. % LLM."""
    assert "> Mémoire</span>" in _CHAT or " Mémoire</span>" in _CHAT
    assert "Mémoire agentique" not in _CHAT
    assert "conf. ${conf}" not in _CHAT
    assert "Faits détectés" in _CHAT


def test_bouton_details_techniques() -> None:
    assert 'id="btn-tech-details"' in _CHAT
    assert 'id="tech-show-reasoning"' in _CHAT
    assert 'id="tech-show-tools"' in _CHAT
    assert "chat-hide-tools" in _CHAT
    assert "chat-hide-reasoning" in _CHAT
    assert "function applyTechPrefs()" in _CHAT
    assert "data.reasoning" in _CHAT
    assert 'details class="agent-reasoning"' in _CHAT


def test_embed_densite_conversation() -> None:
    """Colonne chat desk étroite : padding/gap réduits, pas de chrome large."""
    assert "body.embed .chat-messages{padding:10px 10px;gap:10px}" in _CHAT
    assert "body.embed .msg .bubble{padding:8px 10px" in _CHAT
    assert "body.embed .chat-input{padding:8px 10px 10px}" in _CHAT


def test_copy_fr_composer_et_welcome() -> None:
    assert 'placeholder="Écris ton message…"' in _CHAT
    assert "écrit ton message ici" not in _CHAT
    assert ">Assistant prêt</h3>" in _CHAT
    assert "QGIS Agent prêt" not in _CHAT


def test_erreurs_classe_bubble_err() -> None:
    assert "bubble-err" in _CHAT
    assert 'class="bubble-err"' in _CHAT or "class=\\\"bubble-err\\\"" in _CHAT
    # Plus d'inline Marianne/rouge DSFR pour les erreurs streaming
    assert 'style="color:#ce0500"' not in _CHAT


def test_tool_pulse_accent_produit() -> None:
    """Pulse outils = vert produit, pas bleu Marianne."""
    assert "rgba(65,112,31" in _CHAT
    assert "rgba(0,0,145" not in _CHAT
    assert "border-left:3px solid var(--qs-accent)" in _CHAT


def test_toast_aria_live() -> None:
    assert "aria-live" in _CHAT
    chunk = _CHAT.split("function showChatToast")[1].split("function startNewConversation")[0]
    assert "aria-live" in chunk


def test_stop_btn_sans_emoji() -> None:
    assert 'id="stop-btn"' in _CHAT
    assert ">Arrêter</button>" in _CHAT
    assert "⏸" not in _CHAT
