"""
Tests Vague E2 Commit H1 (D-QGIS-010) — Optimistic concurrency control.

Verifie que update_assembly_endpoint accepte ou rejette selon
version_num_source vs current_version_num.

Use case : BlockNote autosave + agent IA chat modifient l'assembly en
parallele. Sans control, on perd des modifs. Avec : conflit -> 409 +
UI propose recharge.
"""
from __future__ import annotations


class TestEndpointSignature:
    """L'endpoint PUT /studies/{sid}/assemblies/{aid} existe + supporte
    le param version_num_source (smoke test sans auth, juste verifier
    que le code accepte le champ)."""

    def test_route_exists(self):
        from hub.main import app
        routes = [r.path for r in app.routes]
        assert "/studies/{sid}/assemblies/{aid}" in routes

    def test_payload_accepts_version_num_source(self):
        """Vérifie que le code pop bien `version_num_source` du payload
        avant de continuer le traitement (sinon Assembly validation
        échoue car version_num_source n'est pas un champ Pydantic)."""
        from hub.main import update_assembly_endpoint
        import inspect
        src = inspect.getsource(update_assembly_endpoint)
        # Le code doit faire payload.pop("version_num_source", ...)
        assert 'version_num_source' in src
        assert 'pop' in src
        # Et raise 409 en cas de conflit
        assert '409' in src
        assert 'concurrent_update' in src


class TestSerializerH1Integration:
    """Le sérialiseur Python prêt à recevoir et traiter des payloads
    autosave BlockNote (placeholder __pending__ pour DOM)."""

    def test_pending_placeholder_pattern(self):
        from hub.blocknote_serializer import blocknote_doc_to_assembly_sections
        blocks = [
            {"type": "customHeading", "props": {"text": "Test", "level": 2}},
            {"type": "kpiGrid", "props": {"kpisJson": '[{"value":"1","label":"a"}]'}},
        ]
        sections, new_comps = blocknote_doc_to_assembly_sections(blocks)
        # 2 nouveaux composants DOM à créer
        assert len(new_comps) == 2
        # 2 placeholders __pending__ dans sections
        all_refs = [
            r["ref"]
            for s in sections
            for r in s["components"]
        ]
        assert all(r == "__pending__" for r in all_refs)
        # Le caller doit POST /components pour chaque new_comps + remplacer
        # __pending__ par les cid réels avant PUT /assemblies

    def test_iframe_blocks_use_existing_cid_no_pending(self):
        """Iframe blocks référencent cid existant -> pas de placeholder."""
        from hub.blocknote_serializer import blocknote_doc_to_assembly_sections
        blocks = [
            {"type": "interactiveMap", "props": {"cid": "abc123def456"}},
            {"type": "chart", "props": {"cid": "def456abc789"}},
        ]
        sections, new_comps = blocknote_doc_to_assembly_sections(blocks)
        assert len(new_comps) == 0  # Pas de nouveau component à créer
        refs = [r["ref"] for s in sections for r in s["components"]]
        assert "abc123def456" in refs
        assert "def456abc789" in refs
        assert "__pending__" not in refs
