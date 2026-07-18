"""Sprint isolation multi-agent A1 (2026-07-18) — extraction expected_sid.

Verifie que le helper `_extract_expected_sid` parse correctement les 6 patterns
session_id de la convention Sprint V0.3 G1 + le nouveau pattern agent-scoped
`agent:{agent_id}:sid:{sid}` de B1, avec fallback safe sur legacy/UUID/None.

La logique de switch elle-meme (_ensure_active_study_for_agent) est
integration-testee via le proxy /mcp end-to-end plutot qu'unit-teste (elle
touche studies.set_active_study + activate_pod_code qui ont besoin d'un
workspace pod up).
"""
from __future__ import annotations

import pytest

from hub.main import _extract_expected_sid


@pytest.mark.parametrize(
    "session_id, expected",
    [
        # 6 patterns convention Sprint V0.3 G1
        ("study:abc123", "abc123"),
        ("study:abc123:draft:d1", "abc123"),
        ("study:abc123:recipe:r1", "abc123"),
        ("study:abc123:recipe_edit:my-slug", "abc123"),
        ("assist:abc123:cid:c1", "abc123"),
        ("assist:abc123:aid:a1", "abc123"),
        # Pattern nouveau B1 agent-scoped
        ("agent:worker42:sid:xyz789", "xyz789"),
        ("agent:abc-def-ghi:sid:study_1", "study_1"),
        # Legacy / non-reconnus / vides -> None (fallback safe)
        (None, None),
        ("", None),
        ("standalone-uuid-1234", None),
        ("random:no:sid:convention", None),
        ("study:", None),  # sid vide -> None
        ("assist:", None),
        # Robustesse types
        (123, None),
        (["study", "sid1"], None),
    ],
)
def test_extract_expected_sid(session_id, expected):
    """6 patterns G1 + agent-scoped B1 + fallback legacy safe."""
    assert _extract_expected_sid(session_id) == expected


def test_extract_expected_sid_agent_pattern_needs_full_shape():
    """agent:{id}:sid:{sid} exige 4 parts non-vides."""
    # OK
    assert _extract_expected_sid("agent:w1:sid:s1") == "s1"
    # Pas assez de parts -> None
    assert _extract_expected_sid("agent:w1:sid") is None
    assert _extract_expected_sid("agent:w1") is None
    # sid vide -> None
    assert _extract_expected_sid("agent:w1:sid:") is None
    # keyword sid manquant -> None
    assert _extract_expected_sid("agent:w1:not_sid:s1") is None


# ── B1 : contract session_id retourne par POST /agent-context/new ──────────

def test_extract_expected_sid_roundtrip_with_b1_pattern():
    """L'agent qui recoit session_id de POST /agent-context/new doit pouvoir
    le passer tel quel dans X-Session-Id du call MCP suivant : le hub extrait
    le meme sid via _extract_expected_sid. Non-regression cross-endpoint."""
    # Simule la construction de session_id cote endpoint create_agent_context
    for agent_id in ["auto-20260718T101500", "myworker", "recipe-runner.42",
                     "test_agent_v1"]:
        for sid in ["abc123", "study_1", "long-sid-with-dashes"]:
            session_id = f"agent:{agent_id}:sid:{sid}"
            assert _extract_expected_sid(session_id) == sid


def test_extract_expected_sid_b1_pattern_rejects_bad_agent_id():
    """agent_id avec caracteres qui cassent le split ':' produit un sid
    mal extrait. La sanitize cote endpoint B1 (regex [a-zA-Z0-9._-]+) protege
    contre ca, mais on verifie ici le comportement pour un cas volontairement
    mal forme (defensive parsing du parser)."""
    # Un agent_id contenant ":" produit un session_id qui a plus de 4 parts.
    # Le parser voit "agent:my", "id", "sid", "xyz" - le keyword "sid" est
    # a la position 2 (correcte) donc extraction OK si len exact = 4.
    # Cas avec ":" en trop -> le parser attend len==4 exactement, donc None
    session_id = "agent:my:id:sid:xyz"  # 5 parts
    assert _extract_expected_sid(session_id) is None
