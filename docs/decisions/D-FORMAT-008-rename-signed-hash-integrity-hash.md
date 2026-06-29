# D-FORMAT-008 — Rename `signed_hash` → `integrity_hash`

**Date** : 2026-06-29  
**Statut** : ACTÉ (avec backward-compat 1 release)  
**Auteur** : Composants-Architect (suite réserve Lead-cerema)  
**Validateurs** : Lead-cerema (msg #cerema-platform f861efc5)  
**Décisions liées** : D-QGIS-006

## Conclusion

Renommer `audit_chain.signed_hash` en `audit_chain.integrity_hash`.

Sémantique inchangée : SHA256 canonique sur JSON sérialisé canonique
(`sort_keys=True, separators=(",",":"), ensure_ascii=True`).

## Rationale (Lead-cerema)

Scene Manifest V0.2.2 prévoit **Ed25519 sidecar `.sig`** en V2 pour
publication officielle CEREMA → tiers (non-répudiation).

Le mot "signed" implique une signature cryptographique (clé privée) que
SHA256 seul ne fournit pas.

**Confusion garantie** quand le vrai signing arrivera. Distinguer dès
maintenant :
- `integrity_hash` = INTÉGRITÉ (tamper-evident, SHA256)
- `signature` = SIGNATURE (non-répudiation, Ed25519, futur)

Pattern aligné avec Scene Manifest V0.2 qui parle de `hash`, pas
"signed_hash".

## Préfixage registry

`D-FORMAT-008` car impact contrat transverse : `audit_chain` est produit
par qgis-sspcloud mais lisible par Strate / ZEBRA / MobSciDat (audit-
trail-axis §4.3 mapping).

## Implémentation (backward-compat 1 release)

```python
class AuditChain(BaseModel):
    integrity_hash: str = Field(..., description="SHA256 canonique...")
    
    @property
    def signed_hash(self) -> str:
        """DEPRECATED — utiliser `integrity_hash`. Sera supprimé v1.7."""
        import warnings
        warnings.warn(
            "AuditChain.signed_hash is deprecated, use integrity_hash",
            DeprecationWarning, stacklevel=2,
        )
        return self.integrity_hash
```

Templates DSFR storymap : `{{ audit_chain.integrity_hash | truncate(16) }}`
avec fallback Jinja2 si ancien manifest a `signed_hash`.

## Conséquences cross-projet

- Consommateurs lisant `audit_chain.signed_hash` doivent migrer vers
  `integrity_hash` avant v1.7
- Avertissement dans `#cerema-templates` à publier après commit
- Documenter dans `audit-trail-axis.md` (Invariant 4)

## Migration

Phase | Action
---|---
v1.6.4 (Vague A) | `integrity_hash` ajouté, `signed_hash` deprecated property
v1.7 | `signed_hash` property supprimée
v2.0 (futur) | `signature: Ed25519` ajouté distinct de `integrity_hash`

## Référence wikichat

- Axe : `audit-trail-axis` Invariant 4 (proposition → 🟢 fixé avec rename)
- Canal : `#cerema-platform`, `#cerema-templates`
- #decisions msg : `f8dda810` (2026-06-29)
