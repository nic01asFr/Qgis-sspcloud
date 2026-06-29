# Modèles alignés — registry

Modèles Pydantic recopiés depuis d'autres projets de l'écosystème CEREMA.
Stratégie : **recopie + test contrat anti-drift**, pas import direct
(évite cross-repo coupling).

Distinct de `hub/hub/vendor/VENDORED_FROM.md` qui documente les modules
vendorisés avec leur autorité d'origine.

## Source (audit_chain.Source)

- **Source** : `atlas-territorial-briques/atlas/model.py:28-36` (Strate)
- **Repo source** : `C:/Users/Omen/Desktop/LAVAL/Github Repositories/atlas-territorial-briques`
- **Version** : Strate Source @ 2026-06-29 (confirmé par Strate-Architect)
- **Décision** : D-QGIS-006 (#decisions wikichat 2026-06-29) — alignement
  Invariant 3 audit-trail (provenance citée source officielle avec
  `{corpus, ref_id, millesime, authority, licence, statut, url}`)
- **License source** : à confirmer (atlas-territorial-briques = proto recherche)

### Schema canonique aligné

```python
class Statut(str, Enum):
    verifie = "verifie"
    presomption_haute = "presomption_haute"
    a_verifier = "a_verifier"

class Source(BaseModel):
    """Aligned with atlas/model.py:Source @ 2026-06-29 (Strate)."""
    model_config = ConfigDict(frozen=True, extra="forbid")
    corpus: str
    ref_id: str | None = None              # nullable
    millesime: str
    authority: str
    licence: str = "Licence Ouverte 2.0"   # default données publiques
    url: str | None = None
    statut: Statut = Statut.presomption_haute
```

### Invariants à respecter

1. **`confidence = min`** : qgis-sspcloud NE calcule PAS confidence depuis
   `sources[]`. L'agent IA pose explicitement `audit_chain.confidence_score`.
   Le `min()` est appliqué EN AVAL par consommateur (Strate/Geomind) lors
   du croisement multi-couches. Validé par Strate-Architect.

2. **`extra="forbid"`** : strict — pas de champ inconnu ajouté côté
   qgis-sspcloud sans alignement Strate.

3. **`frozen=True`** : immutable une fois construit (cohérent canonicalisation
   pour audit_chain.integrity_hash).

### Quand resynchroniser

- À chaque évolution du schema Strate (`atlas/model.py:Source`)
- Test contrat `tests/test_audit_chain_strate_alignment.py` casse → signal
- Notifier dans `#cerema-templates` wikichat avant resync

### Procédure de resync

```bash
# Depuis le repo qgis-sspcloud :
# 1. Lire le model Strate à jour
cat ../atlas-territorial-briques/atlas/model.py | grep -A 20 "^class Source"

# 2. Si évolution :
#    - Mettre à jour hub/hub/models/audit_chain.py:Source
#    - Mettre à jour la date du commentaire `# aligned with ... @ DATE`
#    - Mettre à jour `tests/test_audit_chain_strate_alignment.py`
#    - Mettre à jour ce fichier (version + date)
#    - Publier dans #cerema-templates wikichat

# 3. Si pas d'évolution : juste rafraîchir la date du commentaire (annuel)
```

### Test contrat anti-drift

Localisation : `hub/tests/test_audit_chain_strate_alignment.py`

```python
def test_source_schema_matches_strate():
    """Vérifie que hub.models.audit_chain.Source a EXACTEMENT les 7 champs
    Strate avec les bons types et defaults. Casse si Strate évolue (signal
    de resync) ou si qgis-sspcloud dérive."""
    from hub.models.audit_chain import Source, Statut
    fields = Source.model_fields
    assert set(fields.keys()) == {
        "corpus", "ref_id", "millesime", "authority",
        "licence", "url", "statut",
    }
    assert fields["corpus"].annotation == str
    assert fields["ref_id"].annotation == (str | None)
    assert fields["millesime"].annotation == str
    assert fields["authority"].annotation == str
    assert fields["licence"].annotation == str
    assert fields["licence"].default == "Licence Ouverte 2.0"
    assert fields["url"].annotation == (str | None)
    assert fields["statut"].annotation == Statut
    assert fields["statut"].default == Statut.presomption_haute
    assert Source.model_config.get("frozen") is True
    assert Source.model_config.get("extra") == "forbid"
```

---

## Future entrées

À ajouter quand d'autres modèles seront alignés :
- `LLMProvenance` (potentielle convergence avec MobSciDat k_claims)
- `Citation` (extension possible cross-projet)
