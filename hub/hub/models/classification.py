"""
hub.models.classification — Niveau de classification audience.

Stub local Sprint Composants Phase 0 (2026-06-25). À fusionner avec le champ
`classification` qui devra être ajouté côté `cerema-offre-de-service/shared/io/
scene_manifest.py` V0.2.3 (Lead #2 — proposition à valider mainteneur).

En attendant le merge upstream, on utilise ce Literal local. Quand
cerema-offre-de-service V0.2.3 est mergé et resyncé dans
`hub/hub/vendor/scene_manifest.py`, ce module devient un alias.

Sémantique CEREMA (à valider mainteneur) :
- `public`            : URL publique, indexable, partageable sans contrainte
- `cerema_internal`   : agents CEREMA authentifiés OIDC (DEFAULT — jamais
                        `public` par défaut, sécurité RGPD)
- `restricted`        : équipe étude + invitations explicites
- `confidential`      : owner étude uniquement (mémo personnel, brouillons)
"""

from __future__ import annotations

from typing import Literal

Classification = Literal[
    "public",
    "cerema_internal",
    "restricted",
    "confidential",
]

DEFAULT_CLASSIFICATION: Classification = "cerema_internal"
"""Default explicite — JAMAIS `public` par défaut (anti-fuite RGPD)."""

CLASSIFICATION_LABELS_FR: dict[str, str] = {
    "public":          "🌍 Public",
    "cerema_internal": "🏛 CEREMA interne",
    "restricted":      "🔒 Restreint (équipe étude)",
    "confidential":    "🤐 Confidentiel (owner)",
}

CLASSIFICATION_DESCRIPTIONS_FR: dict[str, str] = {
    "public":          "URL publique, indexable, partageable sans contrainte",
    "cerema_internal": "Agents CEREMA authentifiés OIDC SSPCloud uniquement",
    "restricted":      "Équipe étude + invitations explicites du owner",
    "confidential":    "Owner étude uniquement (brouillons, mémos)",
}
