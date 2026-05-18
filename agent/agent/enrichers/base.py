"""Contrat commun aux enrichers."""

from __future__ import annotations
from dataclasses import dataclass, field


@dataclass
class EnrichmentResult:
    """Sortie structurée d'un enricher, injectée dans la L2 du contexte.

    Le champ `summary` est ce qui apparaît dans le prompt système — il doit
    être court et factuel. Le champ `data` est disponible aux turns suivants
    via insight ou via lookup ultérieur si nécessaire.
    """

    type: str               # ex "geo_validation", "reference_data"
    summary: str            # 1-3 lignes, lisible humain, injecté dans prompt
    data: dict = field(default_factory=dict)  # structuré pour usage ultérieur
    confidence: float = 1.0 # 0.0 à 1.0 — l'agent doit savoir nuancer
    warnings: str | None = None  # avertissements à transmettre à l'user
