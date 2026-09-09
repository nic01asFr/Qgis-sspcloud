"""Panneau Sources du desk : import, listes compactes, sections repliees."""

from __future__ import annotations

from pathlib import Path

_DESK = (Path(__file__).resolve().parents[1] / "templates" / "desk.html").read_text(
    encoding="utf-8"
)


def test_sources_upload_zone_et_glisser_deposer_panneau() -> None:
    assert 'id="src-upload-zone"' in _DESK
    assert 'id="src-upload-browse-btn"' in _DESK
    assert "_bindSrcDropTarget(srcPane, zone)" in _DESK
    assert "Glisser-déposer des couches ou fichiers" in _DESK
    assert "function _setUploadStatus(" in _DESK
    assert "deck-tab" not in _DESK


def test_sections_fichiers_et_couches_repliees_par_defaut() -> None:
    assert 'data-src-section="files"' in _DESK
    assert 'data-src-section="layers"' in _DESK
    assert 'aria-expanded="false"' in _DESK
    assert "src-section-content collapsed" in _DESK
    assert "_SRC_SECTIONS_KEY = 'desk.src.sections'" in _DESK


def test_listes_compactes_une_ligne() -> None:
    assert "class=\"src-row\"" in _DESK
    assert "class=\"src-list\"" in _DESK
    assert "panneau Couches QGIS" in _DESK


def test_resume_compteurs_sans_liste_complete() -> None:
    assert "id=\"src-files-summary\"" in _DESK
    assert "id=\"src-layers-summary\"" in _DESK
    assert "id=\"src-recipes-user-summary\"" in _DESK
    assert "refreshSourcesSummaries" in _DESK
