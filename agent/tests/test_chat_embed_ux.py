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


def test_nouvelle_conversation_sans_confirm() -> None:
    chunk = _CHAT.split("function startNewConversation()")[1].split("function toggleHistoryPopover")[0]
    assert "confirm(" not in chunk
    assert "showChatToast(" in chunk
    assert "embed=1" in chunk


def test_drawer_charge_memoire_et_insights() -> None:
    assert "fetch('/user/memory')" in _CHAT
    assert "fetch('/user/insights')" in _CHAT
    assert "async function loadMemoryPanel()" in _CHAT


def test_bouton_details_techniques() -> None:
    assert 'id="btn-tech-details"' in _CHAT
    assert 'id="tech-show-reasoning"' in _CHAT
    assert 'id="tech-show-tools"' in _CHAT
    assert "chat-hide-tools" in _CHAT
    assert "chat-hide-reasoning" in _CHAT
    assert "function applyTechPrefs()" in _CHAT
    assert "data.reasoning" in _CHAT
    assert 'details class="agent-reasoning"' in _CHAT
