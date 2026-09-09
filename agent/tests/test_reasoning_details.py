"""Encapsulation du raisonnement LLM pour l'UI chat."""

from agent.qgis_agent import reasoning_details_html


def test_reasoning_details_html_vide() -> None:
    assert reasoning_details_html("") == ""
    assert reasoning_details_html("   ") == ""


def test_reasoning_details_html_encapsule() -> None:
    out = reasoning_details_html("Je vais d'abord charger la couche.")
    assert 'class="agent-reasoning"' in out
    assert "<summary>Raisonnement</summary>" in out
    assert "Je vais d'abord charger la couche." in out
