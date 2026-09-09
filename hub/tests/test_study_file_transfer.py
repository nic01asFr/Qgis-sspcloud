"""Upload / download fichiers d'étude — chemins autorisés."""

from __future__ import annotations

from hub.main import _safe_study_relpath


def test_safe_study_relpath_accepts_data_exports_projects():
    assert _safe_study_relpath("data/parcelles.gpkg") == "data/parcelles.gpkg"
    assert _safe_study_relpath("exports/carte.pdf") == "exports/carte.pdf"
    assert _safe_study_relpath("notes.md") == "notes.md"
    assert _safe_study_relpath("projects/abc123/project.qgz") == "projects/abc123/project.qgz"


def test_safe_study_relpath_rejette_traversal():
    assert _safe_study_relpath("../etc/passwd") is None
    assert _safe_study_relpath("/data/studies/x/data/a.gpkg") is None
    assert _safe_study_relpath("data/../../secret") is None
    assert _safe_study_relpath("treatments.jsonl") is None
