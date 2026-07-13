"""Tests Chantier G6 : 3 nouveaux enrichers (bbox, layer_id, brique).

Chaque enricher suit le pattern G7 : signature async
`(user_message: str, state: dict) -> EnrichmentResult | None`, fail-soft
sur erreurs reseau, retourne None si non pertinent.

Verifie :
  - bbox_enricher : detection regex, validation coords, surface km2,
    reverse geocoding BAN, timeout fail-soft
  - layer_id_enricher : detection des layer_ids connus, injection CRS+hint,
    multi-detection, alias inconnu
  - briques_enricher : score keywords, hub down, aucun match
  - Integration : run_all appelle bien les 3 nouveaux enrichers
"""

from __future__ import annotations

import asyncio
import os
import sys
import tempfile
from pathlib import Path
from unittest.mock import AsyncMock, patch

# Path setup : agent/agent/ doit etre importable
_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

# DATA_DIR temporaire AVANT l'import de memory.py.
_TMP_DIR = tempfile.mkdtemp(prefix="qgis_g6_test_")
os.environ["DATA_DIR"] = _TMP_DIR

# Stub sqlite_vec pour les envs sans binding compile.
if "sqlite_vec" not in sys.modules:
    _vec_stub = type(sys)("sqlite_vec")

    def _noop_load(*a, **k):
        return None

    _vec_stub.load = _noop_load  # type: ignore[attr-defined]
    sys.modules["sqlite_vec"] = _vec_stub

os.environ.setdefault(
    "HUB_URL",
    "https://user-nicolaslaval-qgis.user.lab.sspcloud.fr",
)
os.environ.setdefault("HUB_API_KEY", "test-key")
os.environ.setdefault("QGIS_API_KEY", "test-key")
os.environ.setdefault("ONYXIA_USER", "test-user")

from agent import briques_client  # noqa: E402
from agent.enrichers import (  # noqa: E402
    bbox_enricher,
    briques_enricher,
    layer_id_enricher,
)
from agent.enrichers.base import EnrichmentResult  # noqa: E402


def _run(coro):
    return asyncio.get_event_loop().run_until_complete(coro)


# ── bbox_enricher ────────────────────────────────────────────────────────────


def test_bbox_enricher_marseille_returns_city():
    """Bbox Marseille valide -> retour non-None + surface + ville proche."""
    # Mock la reponse BAN pour eviter dependance reseau
    fake_resp = type("R", (), {
        "status_code": 200,
        "json": lambda self: {
            "features": [{"properties": {"city": "Marseille", "label": "Marseille"}}]
        },
    })()

    async def fake_get(self, url, params=None, **kw):
        return fake_resp

    with patch("httpx.AsyncClient.get", new=fake_get):
        result = _run(bbox_enricher.enrich(
            "Analyse cette zone : 5.38,43.29,5.43,43.33 pour PPRi",
            {},
        ))

    assert isinstance(result, EnrichmentResult)
    assert result.type == "bbox_context"
    assert "Marseille" in result.summary
    assert "km²" in result.summary
    # Surface : ~5x5 km approx a cette latitude
    assert 5 < result.data["surface_km2"] < 40
    assert result.data["city"] == "Marseille"


def test_bbox_enricher_rejects_invalid_lng():
    """Longitude > 180 -> bbox rejetee, None."""
    result = _run(bbox_enricher.enrich(
        "bbox test : 200.5,43.29,201.5,44.0",
        {},
    ))
    assert result is None


def test_bbox_enricher_ban_timeout_still_useful():
    """BAN timeout / down -> retour utile (surface km2 sans ville)."""
    async def fake_get(self, url, params=None, **kw):
        raise TimeoutError("BAN down")

    with patch("httpx.AsyncClient.get", new=fake_get):
        result = _run(bbox_enricher.enrich(
            "zone : 5.38,43.29,5.43,43.33",
            {},
        ))

    assert isinstance(result, EnrichmentResult)
    assert result.type == "bbox_context"
    assert "km²" in result.summary
    assert result.data["city"] is None
    # Confiance reduite quand pas de ville
    assert result.confidence < 1.0


def test_bbox_enricher_no_bbox_in_message():
    """Aucune bbox dans le message -> None (pas d'appel reseau)."""
    result = _run(bbox_enricher.enrich(
        "Fais moi une analyse de Marseille",
        {},
    ))
    assert result is None


def test_bbox_enricher_rejects_min_gte_max():
    """min >= max (bbox inversee) -> None."""
    result = _run(bbox_enricher.enrich(
        "bbox : 5.5,43.5,5.4,43.3",  # lng_min > lng_max
        {},
    ))
    assert result is None


# ── layer_id_enricher ────────────────────────────────────────────────────────


def test_layer_id_enricher_bdtopo_batiments():
    """Detection bdtopo_batiments -> CRS EPSG:2154 + hint G3."""
    result = _run(layer_id_enricher.enrich(
        "Charge bdtopo_batiments et calcule la surface",
        {},
    ))
    assert isinstance(result, EnrichmentResult)
    assert result.type == "layer_id_context"
    assert "EPSG:2154" in result.summary
    assert "bdtopo_batiments" in result.summary
    # Hint G3 (reprojection) doit etre present
    assert "4326" in result.summary or "reprojet" in result.summary.lower()


def test_layer_id_enricher_multiple_layers():
    """2 layer_ids -> 2 lignes distinctes."""
    result = _run(layer_id_enricher.enrich(
        "Combine bdtopo_voies avec rge_alti_5m pour l'analyse",
        {},
    ))
    assert isinstance(result, EnrichmentResult)
    assert "bdtopo_voies" in result.summary
    assert "rge_alti_5m" in result.summary
    assert len(result.data["layer_ids"]) == 2


def test_layer_id_enricher_unknown_layer():
    """Layer inconnu -> None."""
    result = _run(layer_id_enricher.enrich(
        "Charge foobar_random_layer sur l'atlas",
        {},
    ))
    assert result is None


def test_layer_id_enricher_case_insensitive():
    """Match case-insensitive : BDTOPO_batiments -> detecte."""
    result = _run(layer_id_enricher.enrich(
        "Voir la couche BDTOPO_Batiments dans la zone.",
        {},
    ))
    assert isinstance(result, EnrichmentResult)
    assert "bdtopo_batiments" in result.data["layer_ids"]


# ── briques_enricher ─────────────────────────────────────────────────────────


_BRIQUE_A = {
    "id": "crs_wgs84_obligatoire",
    "severity": "error",
    "title": "CRS EPSG:4326 obligatoire pour rendu web",
    "rule_text": "Toute donnee servant un composant interactive_map doit etre en EPSG:4326.",
    "llm_hint": "Impose la reprojection avant inline pour eviter les bugs cartographiques.",
}

_BRIQUE_B = {
    "id": "no_hallucination_sources",
    "severity": "block",
    "title": "Ne jamais inventer une source",
    "rule_text": "Interdiction absolue d'inventer une couche PPR ou PLU inexistante.",
    "llm_hint": "Refuse si la source n'est pas au catalog.",
}


def test_briques_enricher_keywords_match_first_brique():
    """Message avec 'reprojection' + 'inline' -> brique CRS matche."""
    # Reset cache pour repartir sur un fetch
    briques_client.reset_cache()

    async def fake_fetch(hub_url, api_key, timeout=3.0):
        return ([_BRIQUE_A], [_BRIQUE_B])

    with patch("agent.briques_client.fetch_briques_rules", new=fake_fetch):
        result = _run(briques_enricher.enrich(
            "Je veux reprojeter la couche pour l'inline dans un composant map",
            {},
        ))

    assert isinstance(result, EnrichmentResult)
    assert result.type == "brique_match"
    assert "crs_wgs84_obligatoire" in result.data["id"]
    assert "BRIQUE PERTINENTE" in result.summary
    assert "error" in result.summary


def test_briques_enricher_hub_down_returns_none():
    """Hub down -> briques_client renvoie ([], []) -> enricher renvoie None."""
    briques_client.reset_cache()

    async def fake_fetch(hub_url, api_key, timeout=3.0):
        return ([], [])

    with patch("agent.briques_client.fetch_briques_rules", new=fake_fetch):
        result = _run(briques_enricher.enrich(
            "Reprojeter la couche pour inline",
            {},
        ))
    assert result is None


def test_briques_enricher_no_keyword_match():
    """Message dont aucun mot ne matche les briques -> None."""
    briques_client.reset_cache()

    async def fake_fetch(hub_url, api_key, timeout=3.0):
        return ([_BRIQUE_A], [_BRIQUE_B])

    with patch("agent.briques_client.fetch_briques_rules", new=fake_fetch):
        result = _run(briques_enricher.enrich(
            "bonjour comment ca va aujourd hui matin",
            {},
        ))
    assert result is None


def test_briques_enricher_fetch_exception_fails_soft():
    """Si fetch_briques_rules leve, l'enricher renvoie None (pas d'erreur)."""
    briques_client.reset_cache()

    async def fake_fetch(hub_url, api_key, timeout=3.0):
        raise RuntimeError("boom")

    with patch("agent.briques_client.fetch_briques_rules", new=fake_fetch):
        result = _run(briques_enricher.enrich(
            "reprojection inline layer",
            {},
        ))
    assert result is None


# ── Integration : run_all pipeline ───────────────────────────────────────────


def test_run_all_includes_g6_enrichers():
    """Le pipeline enrichers.run_all appelle bien les 3 nouveaux enrichers."""
    from agent import enrichers as pkg

    # On verifie que la liste _ENRICHERS reference bien les 3 nouveaux.
    fn_names = [f.__module__ + "." + f.__name__ for f in pkg._ENRICHERS]
    assert any("bbox_enricher" in n for n in fn_names)
    assert any("layer_id_enricher" in n for n in fn_names)
    assert any("briques_enricher" in n for n in fn_names)


def test_run_all_returns_multiple_enrichment_results_for_composite_message():
    """Message contenant bbox + layer_id -> au moins 2 EnrichmentResult."""
    from agent import enrichers as pkg

    briques_client.reset_cache()

    # Mock BAN + memory.list_recipes + fetch_briques_rules pour isoler le test
    fake_resp = type("R", (), {
        "status_code": 200,
        "json": lambda self: {"features": [{"properties": {"city": "Marseille"}}]},
    })()

    async def fake_get(self, url, params=None, **kw):
        return fake_resp

    async def fake_fetch(hub_url, api_key, timeout=3.0):
        return ([], [])

    async def fake_recipes():
        return []

    with patch("httpx.AsyncClient.get", new=fake_get), \
         patch("agent.briques_client.fetch_briques_rules", new=fake_fetch), \
         patch("agent.memory.list_recipes", new=fake_recipes):
        results = _run(pkg.run_all(
            "Charge bdtopo_batiments sur la bbox 5.38,43.29,5.43,43.33",
            {},
        ))

    types = {r.type for r in results}
    assert "bbox_context" in types
    assert "layer_id_context" in types
