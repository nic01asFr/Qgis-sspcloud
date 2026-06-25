"""
hub.models — Pydantic V0.1 locaux pour la stratification 3 strates
DATA / COMPOSANTS / ASSEMBLAGES (Sprint Composants 2026-06).

Source de vérité du contrat formel Scene Manifest V0.2 = vendorisé depuis
cerema-offre-de-service (`hub/hub/vendor/scene_manifest.py`, policy resync
manuelle D4 anti-drift).

Ces models locaux étendent le contrat avec :
- `Classification` audience CEREMA-internal/public/restricted/confidential
- `AuditChain` transverse obligatoire sur assemblages publiés
- `Component` unités UI paramétrables (interactive_map, scene_3d, kpi_badge, etc.)
- `Assembly` pages HTML composites (storymap_narrative_dsfr, dashboard, sheet_a4)

Capitalisé dans la KB transverse wikichat :
- ~/.wikichat/knowledge/qgis-sspcloud-composants-axis.md
- ~/.wikichat/knowledge/audit-trail-axis.md
- ~/.wikichat/knowledge/maplibre-threejs-pattern-axis.md
"""

from hub.models.classification import Classification
from hub.models.audit_chain import (
    AuditChain,
    LLMProvenance,
    ConfidenceFactors,
    ComponentProvenance,
)
from hub.models.component import (
    Component,
    ComponentKind,
    ComponentSource,
    ComponentRendering,
)
from hub.models.assembly import (
    Assembly,
    AssemblyKind,
    AssemblyLayout,
    AssemblySection,
    AssemblyFooter,
)

__all__ = [
    "Classification",
    "AuditChain",
    "LLMProvenance",
    "ConfidenceFactors",
    "ComponentProvenance",
    "Component",
    "ComponentKind",
    "ComponentSource",
    "ComponentRendering",
    "Assembly",
    "AssemblyKind",
    "AssemblyLayout",
    "AssemblySection",
    "AssemblyFooter",
]
