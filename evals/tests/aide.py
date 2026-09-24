"""Chargement des fixtures SSE pour les tests."""
from __future__ import annotations

from pathlib import Path

from evals.modele import Tour
from evals.sse import construire_tour, lire_evenements

FIXTURES = Path(__file__).parent / "fixtures"


def evenements_fixture(nom: str) -> list[dict]:
    return list(lire_evenements((FIXTURES / nom).read_text(encoding="utf-8").splitlines()))


def tour_fixture(nom: str, message: str = "message") -> Tour:
    return construire_tour(message, evenements_fixture(nom))
