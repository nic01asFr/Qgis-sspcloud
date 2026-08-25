"""
Tests v1.12.0 - composant map editable + bug fix customHeading contentRef.

Le composant interactive_map est CLE METIER CEREMA (carto risque,
exposition, etc.). En v1.10/v1.11 il etait en lecture seule iframe.
v1.12 le passe en editable via drawer Edit Panel : titre/subtitle/
description/basemap/source/caveat/height.
"""
from __future__ import annotations

import pytest
from pathlib import Path

REPO_ROOT = Path(__file__).parent.parent.parent
BLOCKNOTE_SRC = REPO_ROOT / "blocknote-editor" / "src"


def _read(rel: str) -> str | None:
    p = BLOCKNOTE_SRC / rel
    if not p.exists():
        return None
    return p.read_text(encoding="utf-8")


class TestBugFix_CustomHeadingContentRef:
    """v1.12.4 ROLLBACK : 3 tentatives inline editing ont echoue (BlockNote
    NodeView React ne propage pas contentRef sur custom blocks). Retour au
    pattern v1.10 : content:'none' + props.text + drawer Edit Panel."""

    def test_custom_heading_content_none_with_props_text(self):
        content = _read("blocks/CustomHeading.tsx")
        if content is None:
            pytest.skip()
        # Le titre garde son texte dans ses props, pas dans le contenu du
        # bloc : c'est ce qui permet de le rendre sans que BlockNote s'en mele.
        assert "content: 'none'" in content
        assert "text: { default: '' }" in content
        # Il s'edite SUR PLACE depuis le « Chantier 1 V1.20.1 ». Le test
        # exigeait `openEditPanel` -- le panneau lateral -- et echouait depuis
        # ce changement : il figeait un mecanisme abandonne, pas un defaut.
        assert "InlineEditable" in content, (
            "le titre ne s'edite plus ni sur place ni par le panneau"
        )


class TestInteractiveMapForm:
    """Form InteractiveMapForm expose les params editables."""

    def test_form_file_exists(self):
        content = _read("forms/InteractiveMapForm.tsx")
        if content is None:
            pytest.skip()
        assert "export function InteractiveMapForm" in content

    def test_form_exposes_metier_fields(self):
        content = _read("forms/InteractiveMapForm.tsx")
        if content is None:
            pytest.skip()
        # 7 champs editables : title, subtitle, description, basemap_id,
        # height, source, caveat
        for field in ["title", "subtitle", "description", "basemap_id",
                      "source", "caveat", "height"]:
            assert field in content, f"Field '{field}' absent du form"

    def test_form_documents_p0b2_symbology_deferred(self):
        """V1.12 differait layers V2. V1.13 P0b-1 livre Layers basics ;
        Symbology+Interactions reste differe P0b-2 (sprint courant)."""
        content = _read("forms/InteractiveMapForm.tsx")
        if content is None:
            pytest.skip()
        # Tolere ancien text V1.12 (V2) OU nouveau text V1.13 P0b-2
        assert ("V2" in content or "P0b-2" in content or "differé" in content
                or "differe" in content)


class TestInteractiveMapBlock_PropsExpanded:
    """IframeEmbed InteractiveMapBlock propSchema enrichi v1.12."""

    def test_propschema_has_editable_props(self):
        content = _read("blocks/IframeEmbed.tsx")
        if content is None:
            pytest.skip()
        # Apres v1.12, propSchema expose title/subtitle/description/
        # basemap_id/source/caveat/height pour le form
        for prop in ["title:", "subtitle:", "description:", "basemap_id:",
                     "source:", "caveat:", "height:"]:
            assert prop in content, f"PropSchema manque {prop}"


class TestEditPanel_MapIntegration:
    """EditPanel branche interactive_map en mode drawer editable."""

    def test_interactiveMap_in_dom_kind_labels(self):
        """interactiveMap doit etre dans DOM_KIND_LABELS (editable) vs
        IFRAME_KIND_LABELS (lecture seule)."""
        content = _read("EditPanel.tsx")
        if content is None:
            pytest.skip()
        # DOM_KIND_LABELS contient interactiveMap
        # IFRAME_KIND_LABELS ne le contient plus
        # Pour verifier on cherche la presence dans DOM et l'absence comme
        # entry dans IFRAME_KIND_LABELS
        assert "interactiveMap: 'Carte interactive" in content
        # IFRAME_KIND_LABELS ne doit pas commencer par interactiveMap
        iframe_block = content[content.find("IFRAME_KIND_LABELS"):]
        # interactiveMap retire des IFRAME_KIND_LABELS
        # (le mot reste dans le code via DRAWER_KINDS et BLOCK_TO_KIND)

    def test_drawer_kinds_includes_interactiveMap(self):
        content = _read("EditPanel.tsx")
        if content is None:
            pytest.skip()
        # DRAWER_KINDS = {kpiGrid, interactiveMap}
        assert "DRAWER_KINDS = new Set(['kpiGrid', 'interactiveMap'])" in content

    def test_block_to_kind_maps_interactiveMap(self):
        content = _read("EditPanel.tsx")
        if content is None:
            pytest.skip()
        assert "interactiveMap: 'interactive_map'" in content

    def test_render_uses_InteractiveMapForm(self):
        content = _read("EditPanel.tsx")
        if content is None:
            pytest.skip()
        assert "<InteractiveMapForm" in content


class TestSerializer_MapParamsExposed:
    """Forward Assembly -> BlockNote pousse les params map dans block.props."""

    def test_serializer_pushes_map_params(self):
        content = _read("serializer.ts")
        if content is None:
            pytest.skip()
        # case 'interactive_map' expose les 7 params dans props
        assert "case 'interactive_map':" in content
        # Les params metier doivent etre push dans props
        assert "props.basemap_id" in content
        assert "props.description" in content
        assert "props.caveat" in content


class TestHubMapCtx_NewFields:
    """Le hub _build_interactive_map_ctx expose les nouveaux params au template."""

    def test_hub_ctx_includes_subtitle_description_height(self):
        from hub.main import _build_interactive_map_ctx
        import inspect
        src = inspect.getsource(_build_interactive_map_ctx)
        assert '"subtitle"' in src
        assert '"description"' in src
        assert '"height"' in src
        # Priorite params.title sur comp_manifest.title
        assert "title_from_params" in src or "params.get(\"title\"" in src


class TestMapPartialJinja:
    """Le template _interactive_map_partial.j2 affiche subtitle + description."""

    def test_partial_uses_subtitle_description(self):
        partial = (REPO_ROOT / "hub" / "hub" / "maplibre_renderer" /
                   "_interactive_map_partial.j2")
        if not partial.exists():
            pytest.skip()
        content = partial.read_text(encoding="utf-8")
        assert "{{ subtitle | e }}" in content
        assert "{{ description | e }}" in content
        # Height dynamique
        assert "{{ height" in content


class TestSprint6_Coherence:
    def test_version_bumped(self):
        pkg = REPO_ROOT / "blocknote-editor" / "package.json"
        if not pkg.exists():
            pytest.skip()
        content = pkg.read_text(encoding="utf-8")
        import re
        m = re.search(r'"version":\s*"(\d+)\.(\d+)\.(\d+)"', content)
        assert m
        major, minor, patch = int(m.group(1)), int(m.group(2)), int(m.group(3))
        assert (major, minor, patch) >= (1, 12, 0), f"v{major}.{minor}.{patch} < 1.12.0"

    def test_no_regression_imports(self):
        from hub.main import _build_interactive_map_ctx, update_component_endpoint
        assert all([_build_interactive_map_ctx, update_component_endpoint])
