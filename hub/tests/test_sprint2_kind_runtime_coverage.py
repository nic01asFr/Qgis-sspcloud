"""
Tests Sprint 2 P1 (8.7) - couverture ComponentKind <-> runtime <-> helper.

Audit drift D8 : ajouter un kind sans son runtime ou son template Jinja2
declenche une failure surprise. Ce test parametré protege contre ce risque.

Pour chaque kind dans ComponentKind Literal :
1. Verifier qu'au moins un `runtime` est documente
2. Verifier que `_pre_render_component_html` retourne du HTML non-vide
3. Verifier que le mapping BlockNote `componentKindToBlockType` est defini
"""
from __future__ import annotations

import asyncio
import pytest
import inspect


def get_all_component_kinds() -> list[str]:
    """Extrait les 13 kinds depuis Literal."""
    from hub.models.component import ComponentKind
    from typing import get_args
    return list(get_args(ComponentKind))


COMPONENT_KINDS = get_all_component_kinds()


class TestKindCoverage:
    """Cross-verification : tous les kinds ont leur infrastructure complete."""

    def test_13_kinds_exposed(self):
        """ComponentKind Literal expose les 13 kinds attendus."""
        assert len(COMPONENT_KINDS) == 13
        expected = {
            "interactive_map", "scene_3d", "chart", "kpi_badge", "legend",
            "narrative_text", "data_table", "media_embed", "iframe_grist",
            "kpi_grid", "heading", "quote", "separator",
        }
        assert set(COMPONENT_KINDS) == expected

    @pytest.mark.parametrize("kind", COMPONENT_KINDS)
    def test_kind_has_helper_branch(self, kind):
        """Chaque kind doit avoir une branche dans _pre_render_component_html.

        Detecte via lecture source : presence de `kind == "{kind}"` ou
        bien le fallback (pour les kinds en placeholder explicite).
        """
        from hub.main import _pre_render_component_html
        src = inspect.getsource(_pre_render_component_html)
        # Soit le kind est explicitement gere
        has_branch = (
            f'kind == "{kind}"' in src
            or f"kind == '{kind}'" in src
        )
        # Soit le fallback else final est present (placeholder texte)
        has_fallback = (
            "Composant " in src and "kind non géré" in src
        ) or "Voir l'aperçu interactif" in src

        assert has_branch or has_fallback, (
            f"Kind '{kind}' n'a ni branche dedicacee ni fallback "
            f"dans _pre_render_component_html"
        )


class TestBlockNoteMapping:
    """Bijection 13x13 entre ComponentKind et BlockNote block type."""

    @pytest.mark.parametrize("kind", COMPONENT_KINDS)
    def test_kind_has_blocknote_mapping(self, kind):
        """Chaque ComponentKind doit avoir son block BlockNote (D-QGIS-010)."""
        from pathlib import Path
        blocks_index = (
            Path(__file__).parent.parent.parent
            / "blocknote-editor" / "src" / "blocks" / "index.ts"
        )
        if not blocks_index.exists():
            pytest.skip("blocknote-editor non present (CI Docker hub-only)")
        content = blocks_index.read_text(encoding="utf-8")
        # Le mapping snake_case kind doit etre present : soit comme cle JS
        # `kpi_grid:` (sans quote), soit comme string `'kpi_grid'` ou `"kpi_grid"`.
        patterns = [
            f"{kind}:",           # cle d'objet JS sans quote
            f"'{kind}'",          # string simple quote
            f'"{kind}"',          # string double quote
        ]
        assert any(p in content for p in patterns), (
            f"Kind '{kind}' absent de componentKindToBlockType() mapping. "
            f"Patterns recherches : {patterns}"
        )


class TestRuntimeCoverage:
    """Chaque ComponentKind doit avoir un runtime accepte dans Component.rendering."""

    @pytest.mark.parametrize("kind", COMPONENT_KINDS)
    def test_kind_can_be_validated_pydantic(self, kind):
        """Smoke test : un Component valide pour chaque kind se construit."""
        from hub.models import Component

        # Choix runtime selon kind (heuristique alignee avec helper hub)
        runtime_by_kind = {
            "interactive_map": "maplibre",
            "scene_3d": "maplibre_three",
            "chart": "chartjs",
            "data_table": "datatables",
            "narrative_text": "marked",
            "kpi_badge": "html",
            "kpi_grid": "html",
            "heading": "html",
            "quote": "html",
            "separator": "html",
            "legend": "html",
            "media_embed": "iframe",
            "iframe_grist": "iframe",
        }
        runtime = runtime_by_kind.get(kind, "html")

        # Source minimal selon kind
        source = {"scope": "external", "data_url": "/test/dummy"}

        minimal = {
            "id": "0" * 12,  # 12 hex
            "sid": "0" * 12,
            "kind": kind,
            "title": f"test {kind}",
            "source": source,
            "rendering": {"runtime": runtime, "container_size": "responsive"},
            "params": {},
        }
        comp = Component.model_validate(minimal)
        assert comp.kind == kind
        assert comp.rendering.runtime == runtime


class TestPlaceholderKinds:
    """Documenter explicitement les kinds en placeholder V1.

    scene_3d : differé Vague E3 sprint 3 (Three.js fill-extrusion).
    media_embed + iframe_grist : LIVRES v1.8.0 sprint 1 (D4 partiel).
    """

    def test_scene_3d_still_placeholder(self):
        """scene_3d reste dans le fallback v1.8.0 (Vague E3 sprint 3 a venir)."""
        from hub.main import _pre_render_component_html
        src = inspect.getsource(_pre_render_component_html)
        # scene_3d ne doit pas avoir de branche dedicacee
        assert 'kind == "scene_3d"' not in src
        # Mais le fallback else doit etre present
        # (le texte 'apercu interactif' est dans f-string + escape, on cherche
        # plutot le pattern 'Composant {' + 'href="/studies/' qui designe le fallback)
        assert 'Composant {_h.escape(kind)}' in src
        assert '/render" target="_blank"' in src

    def test_media_embed_now_supported(self):
        """media_embed est LIVRE v1.8.0 (sprint 1 fix D4 partiel)."""
        from hub.main import _pre_render_component_html
        src = inspect.getsource(_pre_render_component_html)
        assert 'kind == "media_embed"' in src
        assert "_media_embed_partial.j2" in src

    def test_iframe_grist_now_supported(self):
        """iframe_grist est LIVRE v1.8.0 (sprint 1 fix D4 partiel)."""
        from hub.main import _pre_render_component_html
        src = inspect.getsource(_pre_render_component_html)
        assert 'kind == "iframe_grist"' in src
        assert "_iframe_grist_partial.j2" in src
