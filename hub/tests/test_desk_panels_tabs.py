"""Câblage onglets / panneaux du desk QGIS."""

from __future__ import annotations

import re
from pathlib import Path

_DESK = (Path(__file__).resolve().parents[1] / "templates" / "desk.html").read_text(
    encoding="utf-8"
)


def _rtabs() -> list[str]:
    return re.findall(r'data-rtab="([^"]+)"', _DESK.split("<!-- ═══ Onglet 2/3 : LIVRABLES")[0])


def _rpane_meta() -> list[str]:
    chunk = _DESK.split("<!-- ═══ Onglet 1/3 : SOURCES")[1].split(
        "Anciens panes (caches"
    )[0]
    return re.findall(r'data-rpane="([^"]+)"', chunk)


def test_ressources_deux_onglets_visibles() -> None:
    visible = _rtabs()
    assert visible == ["sources", "livrables"]
    assert "partages" not in visible


def test_chaque_onglet_ressources_a_son_rpane() -> None:
    panes = _rpane_meta()
    for tab in ("sources", "livrables"):
        assert tab in panes
    assert "partages" not in panes


def test_agents_fusionnes_dans_livrables() -> None:
    assert "scoped-keys?only_published=false" in _DESK.split("async function loadLivrables")[1].split("async function")[0]
    assert "kind: 'agent_partage'" in _DESK
    assert "state: isShared ? 'shared' : 'draft'" in _DESK
    assert "Créer un agent partagé" in _DESK
    assert "data-rpane=\"partages\"" not in _DESK


def test_loaders_ressources() -> None:
    assert "async function loadSources()" in _DESK
    assert "async function loadLivrables()" in _DESK
    assert "async function loadPartages()" not in _DESK


def test_panneau_droit_chat_sans_onglet_memoire() -> None:
    """La mémoire vit dans le drawer du chat (iframe), pas dans la navbar desk."""
    assert 'class="desk-right"' in _DESK
    assert 'src="/agent/?embed=1"' in _DESK
    assert 'data-tab="memory"' not in _DESK
    assert 'data-pane="memory"' not in _DESK
    assert "loadMemorySections" not in _DESK


def test_reclic_onglets_rafraichit_donnees() -> None:
    assert "if (target === 'sources')" in _DESK
    assert "refreshSourcesSummaries();" in _DESK
    assert "} else if (target === 'livrables') {" in _DESK
    assert "loadLivrables();" in _DESK
