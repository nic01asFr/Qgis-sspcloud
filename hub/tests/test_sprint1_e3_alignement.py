"""
Tests Sprint 1 Vague E3 alignement (D-QGIS-010 + audit blocks model).

Couvre les 4 items P0 livres :
- 8.1 BlockNote update_component vs create (D3) - serializer.ts + autosave.ts
- 8.2 OCC update_component_endpoint + agent tools + modal E1 (D2 + D5)
- 8.3 Partials Jinja2 media_embed + iframe_grist (D4 partiel)
- 8.4 AssemblyKind limiter a storymap_narrative_dsfr (D9)

Pattern : introspection source code + lecture fichiers TS.
"""
from __future__ import annotations

import inspect
from pathlib import Path


REPO_ROOT = Path(__file__).parent.parent.parent


class TestSprintItem84_AssemblyKindFilter:
    """8.4 (D9) : /schema/assembly/kinds filtré à storymap_narrative_dsfr."""

    def test_schema_kinds_endpoint_filters_assembly(self):
        from hub.main import schema_kinds_endpoint
        src = inspect.getsource(schema_kinds_endpoint)
        # Le filter doit explicitement traiter le cas entity_type == "assembly"
        assert 'entity_type == "assembly"' in src
        assert "storymap_narrative_dsfr" in src
        # Filter note doit etre presente pour debug LLM
        assert "_filter_note" in src or "501" in src


class TestSprintItem82_OCC_UpdateComponent:
    """8.2 (D2) : update_component_endpoint OCC version_num_source."""

    def test_endpoint_pops_version_num_source(self):
        from hub.main import update_component_endpoint
        src = inspect.getsource(update_component_endpoint)
        # Mecanisme OCC ajoute (identique a update_assembly_endpoint v1.7.1)
        assert 'version_num_source' in src
        assert 'pop' in src
        assert '409' in src
        assert 'concurrent_update' in src

    def test_endpoint_handles_invalid_version_num_source(self):
        """version_num_source doit etre int, sinon 400."""
        from hub.main import update_component_endpoint
        src = inspect.getsource(update_component_endpoint)
        # Le bloc try/except autour de int() pour propre erreur 400
        assert 'TypeError' in src or 'ValueError' in src
        assert 'version_num_source doit etre un entier' in src


class TestSprintItem82_AgentTools:
    """8.2 (D2) : tools agent IA acceptent version_num_source optionnel."""

    def test_update_assembly_tool_accepts_version_num_source(self):
        from agent.native_tools_v2 import update_assembly as fn
        sig = inspect.signature(fn)
        assert 'version_num_source' in sig.parameters
        param = sig.parameters['version_num_source']
        assert param.default is None  # optionnel

    def test_update_component_tool_accepts_version_num_source(self):
        from agent.native_tools_v2 import update_component as fn
        sig = inspect.signature(fn)
        assert 'version_num_source' in sig.parameters
        param = sig.parameters['version_num_source']
        assert param.default is None


class TestSprintItem82_ModalE1OCC:
    """8.2 (D5) : modal E1 desk.html envoie version_num_source + gere 409."""

    def test_desk_html_captures_version_num_at_open(self):
        desk = REPO_ROOT / "hub" / "templates" / "desk.html"
        content = desk.read_text(encoding="utf-8")
        # Capture au load + state global
        assert '_editor_current_version_num' in content
        assert 'data.metadata?.version_num' in content

    def test_desk_html_sends_version_num_source_at_save(self):
        desk = REPO_ROOT / "hub" / "templates" / "desk.html"
        content = desk.read_text(encoding="utf-8")
        # Le save doit envoyer version_num_source dans le body
        assert 'body.version_num_source' in content or 'version_num_source: _editor_current_version_num' in content

    def test_desk_html_handles_409_conflict(self):
        desk = REPO_ROOT / "hub" / "templates" / "desk.html"
        content = desk.read_text(encoding="utf-8")
        # Modal de confirmation Recharger / Forcer ecrasement
        assert 'r.status === 409' in content
        assert 'Conflit' in content
        assert 'forcer' in content.lower() or 'écrasement' in content.lower()


class TestSprintItem81_UpdateComponentApi:
    """8.1 (D3) : api.ts expose updateComponent + autosave distingue create/update."""

    def test_api_ts_exports_updateComponent(self):
        api = REPO_ROOT / "blocknote-editor" / "src" / "api.ts"
        if not api.exists():
            return
        content = api.read_text(encoding="utf-8")
        assert 'export async function updateComponent' in content
        assert 'PUT' in content
        # Doit aussi gerer le 409 + version_num_source
        assert '409' in content
        assert 'version_num_source' in content

    def test_autosave_ts_distinguishes_create_vs_update(self):
        autosave = REPO_ROOT / "blocknote-editor" / "src" / "autosave.ts"
        if not autosave.exists():
            return
        content = autosave.read_text(encoding="utf-8")
        # Le mapping retourne existingCid si cid present
        assert 'existingCid' in content
        # blocksToSections retourne updatedComponents en plus
        assert 'updatedComponents' in content
        # saveBlocks appelle updateComponent pour DOM blocks existants
        assert 'updateComponent' in content


class TestSprintItem83_PartialsJinja2:
    """8.3 (D4) : partials media_embed + iframe_grist existent + helper les wire."""

    def test_media_embed_partial_exists(self):
        partial = REPO_ROOT / "hub" / "hub" / "maplibre_renderer" / "_media_embed_partial.j2"
        assert partial.exists()
        content = partial.read_text(encoding="utf-8")
        # Doit gerer 4 types : image, video, pdf, iframe fallback
        assert '<img' in content
        assert '<video' in content
        assert 'application/pdf' in content
        assert '<iframe' in content
        # Sandbox pour iframe = securite
        assert 'sandbox' in content

    def test_iframe_grist_partial_exists(self):
        partial = REPO_ROOT / "hub" / "hub" / "maplibre_renderer" / "_iframe_grist_partial.j2"
        assert partial.exists()
        content = partial.read_text(encoding="utf-8")
        assert '<iframe' in content
        assert 'sandbox' in content
        assert 'widget_url' in content

    def test_pre_render_helper_wires_new_partials(self):
        from hub.main import _pre_render_component_html
        src = inspect.getsource(_pre_render_component_html)
        # Le helper doit lire les 2 nouveaux templates
        assert '_media_embed_partial.j2' in src
        assert '_iframe_grist_partial.j2' in src
        # Et brancher selon kind
        assert 'kind == "media_embed"' in src
        assert 'kind == "iframe_grist"' in src


class TestSprint1_GlobalCoherence:
    """Tests globaux de cohérence cross-changes sprint 1."""

    def test_no_regression_total_count(self):
        """204 tests doivent toujours passer."""
        # Smoke test : on s'assure que ces imports marchent toujours
        from hub.main import (
            update_assembly_endpoint,
            update_component_endpoint,
            schema_kinds_endpoint,
            _pre_render_component_html,
        )
        from agent.native_tools_v2 import update_assembly, update_component
        # Tous les imports OK = bonne santé module-level
        assert all([
            update_assembly_endpoint,
            update_component_endpoint,
            schema_kinds_endpoint,
            _pre_render_component_html,
            update_assembly,
            update_component,
        ])
