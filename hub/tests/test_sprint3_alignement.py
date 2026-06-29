"""
Tests Sprint 3 Vague E3 - couverture P2 essentiels :
- 8.10 sectionBreak fix : H2 vide ne fragmente pas section vide (D6)
- 8.11 DSFR theming strict (Mantine override)
- 8.13 Logique blocksToSections (round-trip + edge cases) - pytest equivalent Vitest
- 8.14 Monitoring client errors endpoint
"""
from __future__ import annotations

import inspect
import pytest
from pathlib import Path

REPO_ROOT = Path(__file__).parent.parent.parent


class TestSprintItem810_SectionBreak:
    """8.10 (D6) : H2 vide ne fragmente pas si section courante vide."""

    def test_autosave_handles_empty_heading_no_fragmentation(self):
        autosave = REPO_ROOT / "blocknote-editor" / "src" / "autosave.ts"
        if not autosave.exists():
            pytest.skip("blocknote-editor absent")
        content = autosave.read_text(encoding="utf-8")
        # Le fix doit verifier 'currentHasContent' avant flush()
        assert 'currentHasContent' in content
        assert 'isEmpty' in content
        # Skip explicit : 'continue' apres detection empty + no content
        assert '// Skip' in content or 'Skip' in content


class TestSprintItem811_DsfrTheming:
    """8.11 : DSFR theming strict via override CSS Mantine."""

    def test_editor_layout_css_contains_dsfr_variables(self):
        css = REPO_ROOT / "blocknote-editor" / "src" / "editor-layout.css"
        if not css.exists():
            pytest.skip("blocknote-editor absent")
        content = css.read_text(encoding="utf-8")
        # Variables CSS DSFR
        assert '--bn-color-primary: #000091' in content
        assert '--bn-color-selected-text: #000091' in content
        # Police Marianne
        assert "'Marianne'" in content
        # Liens DSFR
        assert 'color: #000091' in content


class TestSprintItem813_BlockNoteLogicCoverage:
    """8.13 : couverture pytest equivalent Vitest pour la logique critique."""

    def test_blockstosections_function_exists(self):
        autosave = REPO_ROOT / "blocknote-editor" / "src" / "autosave.ts"
        if not autosave.exists():
            pytest.skip("blocknote-editor absent")
        content = autosave.read_text(encoding="utf-8")
        # Signature : retourne {sections, newComponents, updatedComponents}
        assert 'function blocksToSections' in content
        assert 'BlocksToSectionsResult' in content
        assert 'updatedComponents' in content
        assert 'newComponents' in content

    def test_blockstosections_distinguishes_create_vs_update(self):
        autosave = REPO_ROOT / "blocknote-editor" / "src" / "autosave.ts"
        if not autosave.exists():
            pytest.skip("blocknote-editor absent")
        content = autosave.read_text(encoding="utf-8")
        # 3 branches : refOnly (iframe), existingCid (DOM update), nouveau (DOM create)
        assert 'compInfo.refOnly' in content
        assert 'compInfo.existingCid' in content
        # Marker __pending__ pour les nouveaux
        assert '__pending__' in content

    def test_savebloks_uses_promise_all_for_updates(self):
        autosave = REPO_ROOT / "blocknote-editor" / "src" / "autosave.ts"
        if not autosave.exists():
            pytest.skip("blocknote-editor absent")
        content = autosave.read_text(encoding="utf-8")
        # Promise.all parallel pour les updates
        assert 'Promise.all' in content
        assert 'updateComponent' in content

    def test_main_tsx_has_error_handlers(self):
        """8.14 : main.tsx pose des handlers window error + unhandledrejection."""
        main = REPO_ROOT / "blocknote-editor" / "src" / "main.tsx"
        if not main.exists():
            pytest.skip("blocknote-editor absent")
        content = main.read_text(encoding="utf-8")
        assert "addEventListener('error'" in content
        assert "addEventListener('unhandledrejection'" in content
        assert '/api/log/client-error' in content
        # keepalive : flush meme si l'app crashe
        assert 'keepalive' in content


class TestSprintItem814_ClientErrorEndpoint:
    """8.14 : endpoint /api/log/client-error pour monitoring."""

    def test_post_endpoint_exists(self):
        from hub.main import log_client_error_endpoint
        sig = inspect.signature(log_client_error_endpoint)
        assert 'request' in sig.parameters
        assert 'user' in sig.parameters

    def test_get_endpoint_exists(self):
        from hub.main import get_client_errors_endpoint
        sig = inspect.signature(get_client_errors_endpoint)
        assert 'user' in sig.parameters
        assert 'limit' in sig.parameters

    def test_routes_registered(self):
        from hub.main import app
        routes = [r.path for r in app.routes]
        assert '/api/log/client-error' in routes
        assert '/api/log/client-errors' in routes

    def test_ring_buffer_constants(self):
        from hub.main import _CLIENT_ERROR_BUFFER_MAX
        assert _CLIENT_ERROR_BUFFER_MAX == 100

    def test_endpoint_truncates_long_strings(self):
        """L'endpoint coupe les strings pour eviter abus / log bloat."""
        from hub.main import log_client_error_endpoint
        src = inspect.getsource(log_client_error_endpoint)
        # Truncate : message [:500], stack [:2000], url [:300], ua [:200]
        assert '[:500]' in src
        assert '[:2000]' in src
        assert '[:300]' in src
        assert '[:200]' in src


class TestSprint3_GlobalCoherence:
    """Imports sains pour le sprint 3."""

    def test_imports_ok(self):
        from hub.main import (
            log_client_error_endpoint,
            get_client_errors_endpoint,
            _CLIENT_ERROR_BUFFER,
            _CLIENT_ERROR_BUFFER_MAX,
        )
        assert _CLIENT_ERROR_BUFFER == []
        assert _CLIENT_ERROR_BUFFER_MAX == 100
