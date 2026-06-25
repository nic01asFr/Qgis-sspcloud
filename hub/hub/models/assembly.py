"""
hub.models.assembly — Pydantic V0.1 strate ASSEMBLAGES.

Page HTML composite orchestrant des composants en une narration ou un
tableau de bord publishable CEREMA. Un assemblage est rattaché à une étude
(sid), pas à un projet — il peut référencer composants venant de plusieurs
projets de la même étude.

Catalogue MVP (Sprint Composants Phase 3) :
- storymap_narrative_dsfr  : scroll vertical scrollytelling DSFR
- dashboard                : grille fixe DSFR (suivi opérationnel)
- sheet_a4                 : pagination print (rapport officiel PDF)
- modal_embed              : pop-up 1 composant (iframe site tiers)
- atlas_immersive          : plein écran 3D Three.js (visite virtuelle)

Capitalisé dans la KB :
- ~/.wikichat/knowledge/qgis-sspcloud-composants-axis.md §4
- ~/.wikichat/knowledge/audit-trail-axis.md (audit_chain obligatoire à publish)
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field

from hub.models.audit_chain import AuditChain, ComponentProvenance
from hub.models.classification import Classification
from hub.models.component import Component


AssemblyKind = Literal[
    "storymap_narrative_dsfr",
    "dashboard",
    "sheet_a4",
    "modal_embed",
    "atlas_immersive",
]


class AssemblySection(BaseModel):
    """Section d'un assemblage. Une storymap a typiquement intro + sections +
    conclusion + appendix. Une `sheet_a4` a juste des sections paginées."""
    kind: Literal["intro", "section", "conclusion", "appendix"]
    title: str | None = Field(None, max_length=300)
    narrative_md: str | None = Field(
        None, description="Markdown via Marked.js dans le renderer Jinja2"
    )
    components: list[dict[str, Any]] = Field(
        default_factory=list,
        description=(
            "Liste de références ou composants inline. Format : "
            "[{'ref': 'cid'}, {'inline': {...Component manifest...}}]"
        ),
    )


class AssemblyLayout(BaseModel):
    """Layout structurel selon le `kind` d'assemblage."""
    type: Literal[
        "scroll_vertical",  # storymap_narrative_dsfr
        "grid",             # dashboard
        "paginated",        # sheet_a4
        "tab",              # variante dashboard
        "fullscreen",       # atlas_immersive
        "modal",            # modal_embed
    ]
    sections: list[AssemblySection] = Field(default_factory=list)


class AssemblyFooter(BaseModel):
    """Footer DSFR obligatoire sur publications CEREMA (V1.1 livré tag v1.0).

    Sources : Géorisques, IGN, OSM, etc.
    Audit trail ref : pointe vers `exports_index` ou `audit_chain.signed_hash`
    Disclaimer : ex. PPRi non opposable juridiquement
    """
    sources: list[dict[str, str]] = Field(
        default_factory=list,
        description=(
            "Format Source Strate : {corpus, ref_id, millesime, authority, "
            "licence, url} — pour citation directe DSFR"
        ),
    )
    audit_trail_ref: str | None = Field(
        None, description="ex. 'exports_index:1234' ou audit_chain.signed_hash"
    )
    disclaimer: str | None = Field(
        None,
        description=(
            "Mention légale obligatoire (PPRi, ZNIEFF, etc.) — cf. mémoire "
            "livrables_cerema_audit_v1_1_v1_5"
        ),
    )
    cerema_mentions_legales: bool = Field(
        True,
        description="Bloc mentions légales CEREMA inclus par défaut",
    )


class Assembly(BaseModel):
    """Assemblage Sprint Composants V0.1.

    Versionné via `assemblies_index` DB. `content_hash` = SHA256 du manifest
    sérialisé canonique. `audit_chain` est OBLIGATOIRE au moment du publish
    sur S3.
    """
    id: str = Field(
        ..., description="aid (12 hex uuid4)",
        pattern=r"^[0-9a-f]{12}$",
    )
    version: int = Field(1, ge=1)
    assembly_version: str = Field(
        "V0.1", description="Semver du schema Assembly lui-même"
    )

    sid: str = Field(
        ..., description="Étude (scope assemblage = sid, pas pid)",
        pattern=r"^[0-9a-f]{12}$",
    )
    kind: AssemblyKind
    title: str = Field(..., min_length=1, max_length=300)
    description: str | None = Field(None, max_length=5000)
    theme: str = Field("dsfr", description="DSFR ou variante custom")

    audience: Classification = "cerema_internal"
    """Niveau d'audience à publication. JAMAIS public par défaut."""

    layout: AssemblyLayout
    footer: AssemblyFooter = Field(default_factory=AssemblyFooter)

    audit_chain: AuditChain | None = Field(
        None,
        description=(
            "OBLIGATOIRE au publish. None tant que draft. Calculé par "
            "endpoint POST /assemblies/{aid}/publish (Sprint Composants "
            "Phase 3)."
        ),
    )

    provenance: ComponentProvenance = Field(default_factory=ComponentProvenance)
