# Schema examples — canonical scene manifests

Vendorises depuis Passerelle `sdk/js/geo-components/examples/` (source de verite).
Utilises par `hub.schema_introspect.describe_entity_schema(use_case=...)` pour
retourner un exemple canonique SceneManifest V0.3.1 riche a l'agent MCP.

## Contenu

- `interactive_map/*.json` : 9 exemples canoniques (minimal, diagnostic-temporel,
  corpus-documentaire, choropleth-demographique, heatmap-rag, timeline-overlay-simple,
  multi-layers-narrative, maquette-3d, validation-terrain-zebra).
- `interactive_map/README.md` : documentation des patterns.

## Synchronisation

Ces fichiers doivent etre **strictement** en phase avec le contract V0.3.1
publie sur npm (passerelle-geo-components@dev). En cas de bump contract :

```bash
# A executer depuis le repo qgis-sspcloud
rm hub/hub/schema_examples/interactive_map/*.json
cp ../Passerelle/sdk/js/geo-components/examples/*.json \
   hub/hub/schema_examples/interactive_map/
```

CI a mettre en place : verifier que le hash des fichiers vendorises correspond
a celui du package npm passerelle-geo-components@dev.

## References

- [Passerelle contract V0.3.1](../../../../Passerelle/sdk/js/geo-components/CONTRACT-V0.3.1.md)
- [Passerelle JSON Schema](../../../../Passerelle/sdk/js/geo-components/schemas/scene_manifest.schema.json)
- [Passerelle spec integration](../../../../Passerelle/sdk/js/geo-components/docs/AGENT-CONTRACT-BOOK.md)
