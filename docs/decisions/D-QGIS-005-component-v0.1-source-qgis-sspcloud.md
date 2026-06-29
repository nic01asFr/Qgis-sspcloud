# D-QGIS-005 — Component V0.1 source de vérité = qgis-sspcloud

**Date** : 2026-06-29  
**Statut** : ACTÉ  
**Auteur** : Composants-Architect  
**Validateurs** : Lead-cerema (msg wikichat #cerema-platform f861efc5)  
**Décisions liées** : D4 (Scene Manifest V0.2 = cerema-offre-de-service)

## Conclusion

qgis-sspcloud (`hub/hub/models/component.py`) est l'autorité Pydantic du
contrat `Component V0.1` cross-écosystème CEREMA.

## Rationale

Component est un livrable **UI paramétrable** (interactive_map, chart,
kpi_badge, narrative_text, ...). Scene Manifest est un pivot **DATA**
(style.declarative). Deux abstractions, deux autorités.

Frontière préservée :
- COMPOSANTS (Component) → autorité qgis-sspcloud
- DONNÉES (Scene Manifest V0.2) → autorité cerema-offre-de-service (D4)

## Conséquences

- Tout projet de l'écosystème qui utilise `Component` Pydantic doit
  vendoriser depuis qgis-sspcloud + ajouter `VENDORED_FROM.md`
- Évolutions de Component se font dans qgis-sspcloud, communiquées dans
  `#cerema-templates` wikichat
- qgis-sspcloud n'impose PAS Component comme standard écosystème
  (invariant qgis-sspcloud-composants-axis §8)

## Référence wikichat

- Axe : `qgis-sspcloud-composants-axis`
- Canal : `#qgis-sspcloud-sprint-co`
- #decisions msg : `f8dda810` (2026-06-29)
