# D-QGIS-006 — audit_chain.Source aligné Strate, recopié

**Date** : 2026-06-29  
**Statut** : ACTÉ  
**Auteur** : Composants-Architect  
**Validateurs** : Strate-Architect (msg #cerema-templates 52e1bcba), Lead-cerema (msg #cerema-platform f861efc5)  
**Décisions liées** : D-QGIS-005, D-FORMAT-008 (rename integrity_hash)

## Conclusion

qgis-sspcloud `audit_chain.sources: list[Source]` adopte le schema Strate
`atlas/model.py:Source` Pydantic `frozen=True, extra="forbid"`.

**Stratégie de portage** : recopier (PAS importer) avec test contrat
anti-drift.

## Schema canonique adopté

```python
class Statut(str, Enum):
    verifie = "verifie"
    presomption_haute = "presomption_haute"
    a_verifier = "a_verifier"

class Source(BaseModel):
    """Aligned with atlas/model.py:Source @ 2026-06-29 (Strate)."""
    model_config = ConfigDict(frozen=True, extra="forbid")
    corpus: str
    ref_id: str | None = None
    millesime: str
    authority: str
    licence: str = "Licence Ouverte 2.0"
    url: str | None = None
    statut: Statut = Statut.presomption_haute
```

## Rationale

Alignement avec Invariant 3 de l'axe audit-trail-axis : provenance citée
source officielle avec `{corpus, ref_id, millesime, authority, licence,
statut, url}`.

**Pourquoi recopier (pas importer)** (validé Strate-Architect) :
1. `atlas-territorial-briques` est un proto recherche, pas un package
   pypi publié — import direct = dépendance fragile
2. Schema petit (7 champs + 1 enum), coût copie négligeable
3. Anti-drift via test contrat suffit
4. qgis-sspcloud peut avoir des defaults différents

## Invariant `confidence = min`

**Confirmé par Strate-Architect** : `confidence` ≠ Source. Vit sur
`Validity` (atlas/model.py:39-43).

- `audit_chain.confidence_score: float | None` posé EXPLICITEMENT par
  agent IA côté qgis-sspcloud
- Le `min()` est appliqué EN AVAL par consommateur (Strate/Geomind) lors
  du croisement multi-couches
- qgis-sspcloud ne fait PAS d'auto-calcul depuis sources[]

## Implémentation

Vague A Vague A commit 1 (fondations).

Fichiers impactés :
- `hub/hub/models/audit_chain.py` : ajout `class Statut` + `class Source`,
  refactor `sources: list[Source]`
- `hub/hub/models/VENDORED_FROM.md` : NEW — documentation alignement
- `hub/tests/test_audit_chain_strate_alignment.py` : NEW — test contrat
  anti-drift

## Conséquences

- Resync annuel ou sur signal test contrat KO
- Annonce dans `#cerema-templates` lors de toute évolution
- Pattern réutilisable : autres modules CEREMA peuvent s'aligner sur
  Strate `Validity`, `Layer`, etc.

## Référence wikichat

- Axe : `audit-trail-axis` §2 (Source Strate canonique)
- Canal : `#cerema-templates`
- #decisions msg : `f8dda810` (2026-06-29)
