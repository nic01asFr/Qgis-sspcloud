"""Bloc « Documents de l'étude » du panneau Ressources (lot L7)."""
from __future__ import annotations

from pathlib import Path

from jinja2 import Environment, FileSystemLoader

_HUB = Path(__file__).resolve().parents[1]
_TEMPLATES = _HUB / "templates"
_JS = (_HUB / "hub" / "static" / "documents_etude.js").read_text(encoding="utf-8")


def _rendu(**ctx) -> str:
    env = Environment(loader=FileSystemLoader(str(_TEMPLATES)), autoescape=True)
    return env.get_template("_documents_etude.html").render(**ctx)


def test_desk_inclut_le_bloc_dans_l_onglet_sources() -> None:
    desk = (_TEMPLATES / "desk.html").read_text(encoding="utf-8")
    sources = desk.split("<!-- ═══ Onglet 1/3 : SOURCES")[1].split("<!-- ═══ Onglet 2/3")[0]
    assert '{% include "_documents_etude.html" %}' in sources


def test_bloc_accessible_avec_etude_active() -> None:
    html = _rendu(active_study_id="sid42")
    assert 'data-sid="sid42"' in html
    assert 'aria-controls="docs-etude-contenu"' in html
    assert 'aria-expanded="false"' in html
    assert 'role="status" aria-live="polite"' in html
    assert 'aria-label="Ajouter des documents à l\'étude"' in html
    assert 'accept=".pdf,.docx,.odt,.txt,.md,.markdown,.csv,.xlsx"' in html
    assert '<script src="/static/documents_etude.js" defer></script>' in html


def test_bloc_sans_etude_active() -> None:
    html = _rendu(active_study_id=None)
    assert 'data-sid=""' in html
    assert "docs-etude-input" not in html
    assert "Choisis une étude" in html


def test_js_confirme_le_retrait_et_n_injecte_pas_de_html() -> None:
    assert "innerHTML" not in _JS
    assert "qsConfirmer" in _JS and "danger: true" in _JS
    assert 'method: "DELETE"' in _JS
    # Le depot du panneau Sources (donnees QGIS) ne doit pas recevoir aussi
    # les documents deposes ici.
    assert _JS.count("e.stopPropagation()") >= 3
    # Formats acceptes identiques a ceux du hub.
    from hub import documents_etude as de
    for ext in de.FORMATS:
        assert f'"{ext}"' in _JS
