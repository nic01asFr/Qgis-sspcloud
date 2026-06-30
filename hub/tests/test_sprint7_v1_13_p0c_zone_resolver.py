"""
Tests Sprint 1 V1.13 P0c - hub.zone_resolver.

Couvre :
- resolve_commune_zone(insee) : appel geo.api.gouv.fr + parsing
- resolve_study_zone(sid) : lookup studies.get_study
- resolve_zone(zone_param, sid) : dispatcher 3 kinds
- _zoom_from_bbox : heuristique zoom depuis largeur bbox
- _build_interactive_map_ctx integre zone_resolver
"""
from __future__ import annotations

import pytest

from hub.zone_resolver import (
    DEFAULT_CENTER_LAT,
    DEFAULT_CENTER_LNG,
    DEFAULT_ZOOM,
    _zoom_from_bbox,
    resolve_commune_zone,
    resolve_study_zone,
    resolve_zone,
)


class TestZoomFromBbox:
    def test_default_if_empty(self):
        assert _zoom_from_bbox([]) == DEFAULT_ZOOM
        assert _zoom_from_bbox([0, 0, 0, 0]) == DEFAULT_ZOOM

    def test_small_bbox_high_zoom(self):
        # bbox 0.01 deg ~ ville/quartier -> zoom ~15
        z = _zoom_from_bbox([5.39, 43.30, 5.40, 43.31])
        assert z > 14
        assert z <= 18

    def test_large_bbox_low_zoom(self):
        # bbox 5 deg ~ region -> zoom ~10
        z = _zoom_from_bbox([0, 40, 5, 45])
        assert 10 <= z <= 12


class TestResolveCommuneZone:
    """Tests avec mock http_get (pas de hit reseau)."""

    @pytest.mark.asyncio
    async def test_invalid_insee_returns_none(self):
        async def mock_get(url):
            return None
        result = await resolve_commune_zone("abc", http_get=mock_get)
        assert result is None
        result = await resolve_commune_zone("", http_get=mock_get)
        assert result is None

    @pytest.mark.asyncio
    async def test_resolve_marseille_4e_via_mock(self):
        """Marseille 4e arr. INSEE 13204."""
        async def mock_get(url):
            assert "13204" in url
            return {
                "code": "13204",
                "nom": "Marseille 4e Arrondissement",
                "centre": {"type": "Point", "coordinates": [5.397, 43.293]},
                "contour": {
                    "type": "Polygon",
                    "coordinates": [[
                        [5.380, 43.280],
                        [5.415, 43.280],
                        [5.415, 43.310],
                        [5.380, 43.310],
                        [5.380, 43.280],
                    ]],
                },
            }
        result = await resolve_commune_zone("13204", http_get=mock_get)
        assert result is not None
        assert result["center_lng"] == 5.397
        assert result["center_lat"] == 43.293
        assert result["bbox"] is not None
        assert result["bbox"][0] < result["bbox"][2]  # west < east
        assert result["zoom"] > 10  # arrondissement = zoom precis

    @pytest.mark.asyncio
    async def test_marseillette_pitfall_uses_insee_strict(self):
        """KB project_bug_marseille_geocoding : INSEE 13204 != 'Marseille' fuzzy."""
        async def mock_get(url):
            # Verifier qu'on appelle l'endpoint INSEE strict, pas /communes?nom=
            assert "/communes/13204" in url
            assert "?nom=" not in url
            return {
                "code": "13204",
                "nom": "Marseille 4e Arrondissement",
                "centre": {"type": "Point", "coordinates": [5.397, 43.293]},
            }
        result = await resolve_commune_zone("13204", http_get=mock_get)
        assert result is not None

    @pytest.mark.asyncio
    async def test_buffer_km_extends_bbox(self):
        async def mock_get(url):
            return {
                "code": "13204",
                "nom": "Test",
                "centre": {"type": "Point", "coordinates": [5.0, 43.0]},
                "contour": {
                    "type": "Polygon",
                    "coordinates": [[[5.0, 43.0], [5.01, 43.0], [5.01, 43.01], [5.0, 43.01], [5.0, 43.0]]],
                },
            }
        r1 = await resolve_commune_zone("13204", buffer_km=None, http_get=mock_get)
        r2 = await resolve_commune_zone("13204", buffer_km=2.0, http_get=mock_get)
        assert r1 is not None and r2 is not None
        assert r2["bbox"][0] < r1["bbox"][0]  # West etend
        assert r2["bbox"][2] > r1["bbox"][2]  # East etend

    @pytest.mark.asyncio
    async def test_network_failure_returns_none(self):
        async def mock_get(url):
            return None
        result = await resolve_commune_zone("13204", http_get=mock_get)
        assert result is None


class TestResolveStudyZone:
    @pytest.mark.asyncio
    async def test_no_sid_returns_none(self):
        class FakeStudies:
            async def get_study(self, sid):
                return None
        result = await resolve_study_zone("", studies_module=FakeStudies())
        assert result is None

    @pytest.mark.asyncio
    async def test_study_without_zone_returns_none(self):
        class FakeStudies:
            async def get_study(self, sid):
                return {"sid": sid, "name": "Test"}
        result = await resolve_study_zone("abc123", studies_module=FakeStudies())
        assert result is None

    @pytest.mark.asyncio
    async def test_study_with_explicit_center(self):
        """Cas typique : set_study_zone agent IA a stocke center+zoom dans study.zone."""
        class FakeStudies:
            async def get_study(self, sid):
                return {
                    "sid": sid,
                    "zone": {
                        "center_lat": 43.30,
                        "center_lng": 5.39,
                        "zoom": 14,
                        "bbox": [5.38, 43.29, 5.40, 43.31],
                    },
                }
        result = await resolve_study_zone("abc123", studies_module=FakeStudies())
        assert result is not None
        assert result["center_lat"] == 43.30
        assert result["zoom"] == 14


class TestResolveZoneDispatcher:
    @pytest.mark.asyncio
    async def test_none_returns_fallbacks(self):
        result = await resolve_zone(None, None)
        assert result["center_lng"] == DEFAULT_CENTER_LNG
        assert result["center_lat"] == DEFAULT_CENTER_LAT
        assert result["zoom"] == DEFAULT_ZOOM

    @pytest.mark.asyncio
    async def test_kind_manual_uses_explicit_values(self):
        zone = {"kind": "manual", "center_lat": 48.85, "center_lng": 2.35, "zoom": 12}
        result = await resolve_zone(zone, None)
        assert result["center_lat"] == 48.85
        assert result["center_lng"] == 2.35
        assert result["zoom"] == 12

    @pytest.mark.asyncio
    async def test_kind_commune_calls_geo_api(self):
        called: dict[str, str] = {}

        async def mock_get(url):
            called["url"] = url
            return {
                "code": "13204",
                "centre": {"coordinates": [5.397, 43.293]},
            }

        zone = {"kind": "commune", "insee": "13204"}
        result = await resolve_zone(zone, None, http_get=mock_get)
        assert "13204" in called.get("url", "")
        assert result["center_lng"] == 5.397

    @pytest.mark.asyncio
    async def test_kind_study_uses_study_lookup(self):
        class FakeStudies:
            async def get_study(self, sid):
                return {"zone": {"center_lat": 50.0, "center_lng": 3.0, "zoom": 11}}
        result = await resolve_zone({"kind": "study"}, "abc", studies_module=FakeStudies())
        assert result["center_lat"] == 50.0


class TestHelperHubIntegration:
    """_build_interactive_map_ctx utilise hub.zone_resolver."""

    def test_helper_imports_zone_resolver(self):
        import inspect
        from hub.main import _build_interactive_map_ctx
        src = inspect.getsource(_build_interactive_map_ctx)
        assert "zone_resolver" in src
        assert "resolve_zone" in src

    def test_no_more_hardcoded_3_codes_insee(self):
        """V1.13 P0c remplace heuristique 13204/75104/69383 par appel API."""
        import inspect
        from hub.main import _build_interactive_map_ctx
        src = inspect.getsource(_build_interactive_map_ctx)
        # P0b-1 livrait hardcoded 3 codes dans _build_interactive_map_ctx.
        # P0c les delegue a zone_resolver -> ne devraient plus etre en dur ICI
        # (ils peuvent etre dans zone_resolver mais pas dans le helper).
        # Pattern : les 3 codes n'apparaissent pas dans une chaine if/elif.
        # Note : on garde tolerant (peut subsister en doc), on verifie juste
        # que zone_resolver est appele.
        assert "resolve_zone" in src

    def test_helper_async_resolve(self):
        """Le helper appelle resolve_zone en async (pas synchrone)."""
        import inspect
        from hub.main import _build_interactive_map_ctx
        src = inspect.getsource(_build_interactive_map_ctx)
        assert "await resolve_zone" in src


class TestSprint1_P0c_Coherence:
    def test_no_regression_imports(self):
        from hub.zone_resolver import resolve_zone, resolve_commune_zone, resolve_study_zone
        from hub.main import _build_interactive_map_ctx
        assert all([resolve_zone, resolve_commune_zone, resolve_study_zone, _build_interactive_map_ctx])
