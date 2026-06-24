# Vendored modules — registry

Modules copiés depuis des projets sources externes. Resynchroniser
manuellement quand la source évolue (les projets sources ne sont pas
encore en release pip stable, donc pas de `pip install` possible).

## scene_manifest.py

- **Source** : `cerema-offre-de-service/shared/io/scene_manifest.py`
- **Repo source** : https://github.com/nic01asFr/cerema-offre-de-service
  (ou GitLab CEREMA équivalent)
- **Spec** : `cerema-offre-de-service/docs/scene-manifest-spec.md`
- **Version** : V0.2 (convergence GPIF, 2026-05-29)
- **License** : EUPL-1.2
- **Date sync initiale** : 2026-06-24
- **Décision** : D4 #decisions 2026-05-29 — anti-drift, ne PAS forker
  comme source autoritaire ; rester aligné sur la spec V0.2.

### Quand resynchroniser

- À chaque changement de version dans le repo source (V0.2.x → V0.3.x)
- Si extension nécessaire (ex: `rotation_field` sur marker 2D — point
  ouvert documenté section "Point ouvert connu" de l'axe wikichat)

### Procédure de resync

```bash
# Depuis le repo qgis-sspcloud :
cp ../cerema-offre-de-service/shared/io/scene_manifest.py \
   hub/hub/vendor/scene_manifest.py

# Verifier qu'il n'y a pas de breaking change qui casse les imports
# locaux. Si oui, adapter les call-sites avant de commit.

# Mettre a jour la date de sync ci-dessous + version :
# - Date sync : YYYY-MM-DD
# - Version : V0.X.Y
```

## Modules vendorises envisages (futurs Sprints)

- `shared/io/gpkg.py` (Sprint Composants-3) : helpers GPKG étendu V0.2
  pour produire le pivot binaire (features + tables scene_manifest /
  scene_relations / scene_provenance).
