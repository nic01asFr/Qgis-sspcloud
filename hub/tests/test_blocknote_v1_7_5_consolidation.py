"""
Tests v1.7.5 consolidation (audit roadmap) — 3 patterns critiques BlockNote.

Couvre les bugs corrigés v1.7.1 + v1.7.4 sans surface pytest dédiée :
1. Optimistic concurrency 409 : payload.pop('version_num_source') + comparaison
   int + raise HTTP 409 structuré (covered en source seulement avant v1.7.5).
2. PUT merge skip None (P0 #5) : applique aux 2 endpoints update_assembly
   ET update_component.
3. Round-trip section count : forward serializer.ts génère heading H2 vide
   par section sauf la 1ère pour préserver la structure au backward.

Pattern : introspection source code (vs TestClient FastAPI), aligné sur
test_blocknote_editor_endpoint.py qui fait pareil. Les tests TestClient
réels nécessitent une stack complète OIDC + studies + assemblies trop
lourde pour CI unitaire.
"""
from __future__ import annotations

import inspect


class TestConcurrency409:
    """Audit v1.7.1 P0 #1 + v1.7.2 P1 #2 : optimistic concurrency control."""

    def test_endpoint_pops_version_num_source(self):
        """L'endpoint PUT pop bien `version_num_source` avant validation Pydantic."""
        from hub.main import update_assembly_endpoint
        src = inspect.getsource(update_assembly_endpoint)
        # Le pop est obligatoire car version_num_source n'est PAS un champ Assembly Pydantic
        assert 'payload.pop("version_num_source"' in src or "payload.pop('version_num_source'" in src
        # Compare avec current version + raise 409 structuré
        assert "409" in src
        assert "concurrent_update" in src
        # Bypass possible si versionNumSource is None (utilisé par 'Forcer écrasement')
        assert "version_num_source is not None" in src

    def test_409_payload_structure_documented(self):
        """Le 409 retourne un detail JSON structuré avec current_version_num + source_version_num."""
        from hub.main import update_assembly_endpoint
        src = inspect.getsource(update_assembly_endpoint)
        assert "current_version_num" in src
        assert "source_version_num" in src


class TestPutMergeSkipNone:
    """Audit v1.7.1 P0 #5 : merge top-level skip None.

    Sans ce fix, un payload {layout: null} écrasait les sections existantes
    du manifest -> perte de contenu silencieuse.
    """

    def test_update_assembly_skip_none_merge(self):
        from hub.main import update_assembly_endpoint
        src = inspect.getsource(update_assembly_endpoint)
        # Le merge doit explicitement skip les valeurs None
        assert "if v is None" in src
        assert "continue" in src

    def test_update_component_skip_none_merge(self):
        """Le même fix doit être appliqué côté update_component."""
        from hub.main import update_component_endpoint
        src = inspect.getsource(update_component_endpoint)
        assert "if v is None" in src
        assert "continue" in src


class TestSerializerRoundTrip:
    """Audit v1.7.4 : forward serializer.ts génère heading H2 vide pour préserver
    les frontières de section au round-trip.

    Test sur le fichier TS via lecture texte (pas d'exécution JS côté pytest).
    """

    def test_serializer_inserts_section_marker(self):
        """serializer.ts insère un heading H2 même si section.title vide (sauf 1ère)."""
        from pathlib import Path
        serializer = Path(__file__).parent.parent.parent / "blocknote-editor" / "src" / "serializer.ts"
        if not serializer.exists():
            # En CI Docker hub seul, le repo TS n'est pas présent
            return
        content = serializer.read_text(encoding="utf-8")
        # Le fix v1.7.4 ajoute un commentaire 'fix : TOUJOURS insérer un heading'
        assert "v1.7.4" in content or "frontière" in content.lower() or "frontiere" in content.lower()
        # La logique : isFirstSection skip, sinon génère heading avec section.title || ''
        assert "isFirstSection" in content
        assert "section.title || ''" in content or 'section.title || ""' in content

    def test_autosave_blocks_to_sections_handles_empty_heading(self):
        """blocksToSections groupe les blocks par heading H2 (même vide)."""
        from pathlib import Path
        autosave = Path(__file__).parent.parent.parent / "blocknote-editor" / "src" / "autosave.ts"
        if not autosave.exists():
            return
        content = autosave.read_text(encoding="utf-8")
        # La logique : heading natif H1/H2 -> flush() + nouveau current.title
        assert "btype === 'heading'" in content
        assert "level <= 2" in content
        assert "flush()" in content


class TestSerializerParallelization:
    """Audit v1.7.2 P1 #1 : Promise.allSettled fetch composants parallèle."""

    def test_serializer_uses_promise_allsettled(self):
        """Le serializer.ts doit utiliser Promise.allSettled (pas await en série)."""
        from pathlib import Path
        serializer = Path(__file__).parent.parent.parent / "blocknote-editor" / "src" / "serializer.ts"
        if not serializer.exists():
            return
        content = serializer.read_text(encoding="utf-8")
        assert "Promise.allSettled" in content
        # Tolérant aux fetch ratés (404, timeout) : un composant raté ne casse pas le rendu
        assert "componentByCid" in content


class TestStaticBlockNoteWhitelist:
    """Audit v1.7.2 P1 #3 : /static/blocknote-editor whitelisté middleware OIDC."""

    def test_static_blocknote_in_oidc_whitelist(self):
        from hub.auth import _OIDC_MIDDLEWARE_PUBLIC
        assert "/static/blocknote-editor" in _OIDC_MIDDLEWARE_PUBLIC
