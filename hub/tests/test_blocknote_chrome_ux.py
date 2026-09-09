"""Structure UX chrome éditeur BlockNote (allègement 2026-09-10)."""

from __future__ import annotations

from pathlib import Path

_APP = (
    Path(__file__).resolve().parents[2] / "blocknote-editor" / "src" / "App.tsx"
).read_text(encoding="utf-8")
_CSS = (
    Path(__file__).resolve().parents[2]
    / "blocknote-editor"
    / "src"
    / "editor-layout.css"
).read_text(encoding="utf-8")
_PANEL = (
    Path(__file__).resolve().parents[2]
    / "blocknote-editor"
    / "src"
    / "AgentPanel.tsx"
).read_text(encoding="utf-8")


def test_chrome_sans_estampe_cerema() -> None:
    assert "QGIS · Éditeur" in _APP
    assert "CEREMA · QGIS · Éditeur" not in _APP
    # Footer produit : plus d'ADR dans le chrome visible
    assert "D-QGIS-010 · BlockNote" not in _APP
    assert "Aperçu DSFR" not in _APP


def test_tooltip_hover_sans_emoji() -> None:
    assert "Cliquer pour modifier" in _CSS
    assert "✏️" not in _CSS


def test_assistant_panel_label_neutre() -> None:
    assert "Assistant rédaction" in _PANEL
    # Libellés UI (pas le commentaire d'en-tête fichier)
    assert 'aria-label="Assistant redaction CEREMA"' not in _PANEL
    assert "Assistant redaction CEREMA\n" not in _PANEL
    assert ">Assistant rédaction CEREMA<" not in _PANEL.replace(" ", "")
    assert "          Assistant rédaction\n" in _PANEL or "Assistant rédaction\n        </div>" in _PANEL
