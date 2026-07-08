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
    "public":          "Diffusion publique",
    "cerema_internal": "Diffusion interne CEREMA",
    "restricted":      "Diffusion restreinte (équipe étude)",
    "confidential":    "Confidentiel (moi uniquement)",
}
"""V1.20.4 : labels humains pour livrable COPIL — fin des emojis + slugs
techniques (`cerema_internal`) exposés au lecteur."""

CLASSIFICATION_DESCRIPTIONS_FR: dict[str, str] = {
    "public":          "URL publique, indexable, partageable sans contrainte",
    "cerema_internal": "Agents CEREMA authentifiés OIDC SSPCloud uniquement",
    "restricted":      "Équipe étude + invitations explicites du owner",
    "confidential":    "Owner étude uniquement (brouillons, mémos)",
}


ASSEMBLY_KIND_LABELS_FR: dict[str, str] = {
    "storymap_narrative_dsfr":  "Note narrative CEREMA",
    "atlas_kpi_dashboard":      "Tableau de bord d'indicateurs",
    "carto_pdf":                "Carte imprimable",
    "briefing_note":            "Note de synthèse",
}
"""V1.20.4 : mapping kind technique -> libelle humain affiche au user.
Utilise dans le template storymap DSFR pour remplacer le jargon
`STORYMAP NARRATIVE DSFR` exposé au COPIL."""


def format_datetime_fr(value) -> str:
    """Formate un timestamp (str ISO ou datetime) en FR : '8 juillet 2026 à 08:27'.

    V1.20.4 : fin du timestamp ISO brut '2026-07-08T08:27:56.597274' dans
    le footer livrable publique.
    """
    from datetime import datetime as _dt

    if value is None:
        return ""
    if isinstance(value, str):
        try:
            # Accepte '2026-07-08T08:27:56.597274' ou avec Z / offset
            v = value.replace("Z", "+00:00")
            dt = _dt.fromisoformat(v)
        except ValueError:
            return value  # renvoie brut si non parsable, mieux que crasher
    else:
        dt = value

    mois_fr = [
        "janvier", "février", "mars", "avril", "mai", "juin",
        "juillet", "août", "septembre", "octobre", "novembre", "décembre",
    ]
    return f"{dt.day} {mois_fr[dt.month - 1]} {dt.year} à {dt.hour:02d}:{dt.minute:02d}"
