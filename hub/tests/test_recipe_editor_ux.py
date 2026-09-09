"""Structure UX modal éditeur recettes (desk Sources)."""

from __future__ import annotations

from pathlib import Path

_DESK = (Path(__file__).resolve().parents[1] / "templates" / "desk.html").read_text(
    encoding="utf-8"
)


def test_recipe_editor_overlay_ids_stables() -> None:
    for sel in (
        'id="recipe-editor-overlay"',
        'id="recipe-editor-title"',
        'id="recipe-editor-meta"',
        'id="recipe-editor-slug"',
        'id="recipe-editor-name"',
        'id="recipe-editor-format"',
        'id="recipe-editor-content"',
        'id="recipe-editor-status"',
        'id="recipe-editor-save"',
    ):
        assert sel in _DESK


def test_recipe_editor_ouvre_via_classe_is_open() -> None:
    assert "overlay.classList.add('is-open')" in _DESK
    assert "overlay.classList.remove('is-open')" in _DESK
    assert "#recipe-editor-overlay.is-open{display:flex}" in _DESK
    # Plus d'ouverture uniquement via style.display inline
    assert "overlay.style.display = 'flex'" not in _DESK.split("async function openRecipeEditor")[1].split(
        "function closeRecipeEditor"
    )[0]


def test_recipe_editor_echap_ferme() -> None:
    chunk = _DESK.split("document.addEventListener('keydown'")[1].split("async function saveRecipeFromEditor")[0]
    assert "Escape" in chunk
    assert "closeRecipeEditor()" in chunk
