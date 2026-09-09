"""Phase 4 desk : polish navbar, Escape, badge MCP."""

from __future__ import annotations

from pathlib import Path

_DESK = (Path(__file__).resolve().parents[1] / "templates" / "desk.html").read_text(
    encoding="utf-8"
)


def test_badge_mcp_dans_status_bar() -> None:
    status = _DESK.split('<footer class="desk-status">')[1].split("</footer>")[0]
    study = _DESK.split('<div class="study-menu">')[1].split("</div>")[0]
    assert 'id="mcp-session-badge"' in status
    assert "mcp-session-badge" not in study
    assert 'id="mcp-session-sep"' in status


def test_escape_ferme_overlays_desk() -> None:
    assert "function fermerOverlaysDesk()" in _DESK
    chunk = _DESK.split("document.addEventListener('keydown'")[1].split("});")[0]
    assert "fermerOverlaysDesk()" in chunk
    assert "layoutState.deckView" in _DESK.split("function fermerOverlaysDesk()")[1].split("document.getElementById('desk-backdrop')")[0]


def test_backdrop_utilise_fermer_overlays() -> None:
    chunk = _DESK.split("getElementById('desk-backdrop')")[1].split("Resize handles")[0]
    assert "fermerOverlaysDesk()" in chunk
