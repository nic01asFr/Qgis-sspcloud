# KB tips QGIS / PyQGIS — patterns auto-injectés sur erreur

Cette KB est indexée dans le vector_store SQLite-vec (source_type="qgis_tip").
À chaque erreur tool, un hook recherche le tip pertinent par symptom et
l'injecte en suffixe de la réponse renvoyée à l'agent.

Format d'un tip :
- `## tip: <titre court unique>`
- `symptom:` une seule ligne — message d'erreur typique ou intention.
  C'est ce qui est embedé pour le matching.
- `pattern:` bloc de code (peut être multi-lignes via | YAML-style ou ```).
- `note:` une seule ligne ajoutée à la fin (optionnel).

Le parseur (indexer.py) lit jusqu'au prochain `## tip:` ou EOF.

---

## tip: QVariant unwrap pattern
symptom: TypeError int argument must be a string a bytes-like object or a real number not QVariant unsupported operand type for float and QVariant
pattern: |
  # Unwrap safe d'un attribut feature qui retourne QVariant
  v = feat["champ"]
  if v is None:
      continue
  try:
      v = float(v) if not isinstance(v, (int, float)) else v
  except (TypeError, ValueError):
      continue
note: feat[champ] peut retourner QVariant typé Qt. Toujours isinstance ou cast safe avant arithmétique.

## tip: addAttribute vs addAttributes
symptom: AttributeError QgsVectorLayer object has no attribute addAttributes Did you mean addAttribute
pattern: |
  # Layer direct n'a PAS addAttributes. dataProvider() l'a (pluriel, liste).
  prov = layer.dataProvider()
  prov.addAttributes([QgsField("name", QVariant.String), QgsField("count", QVariant.Int)])
  layer.updateFields()
note: dataProvider().addAttributes (pluriel) avec liste de QgsField. Le singulier addAttribute n'existe pas non plus sur layer.

## tip: QgsFields vs QgsField (collection vs item)
symptom: QgsFields object has no attribute add QgsFields object has no attribute addAttribute name QgsFields is not defined Did you mean QgsField
pattern: |
  # QgsFields = COLLECTION de champs. QgsField = UN champ. NE PAS confondre.
  # Ajouter un champ à une collection : .append() (PAS .add ni .addAttribute).
  from qgis.core import QgsFields, QgsField
  from PyQt5.QtCore import QVariant

  fields = QgsFields()
  fields.append(QgsField("code", QVariant.String))
  fields.append(QgsField("count", QVariant.Int))

  # Pour ajouter à un layer existant, passer par dataProvider().addAttributes :
  prov = layer.dataProvider()
  prov.addAttributes([QgsField("code", QVariant.String)])
  layer.updateFields()
note: QgsFields() (pluriel = collection) supporte .append(QgsField(...)). PAS de .add ni .addAttribute sur la collection. Pour modifier un layer, dataProvider().addAttributes([liste de QgsField]) + updateFields().

## tip: Imports PyQGIS explicites
symptom: NameError name Qgs is not defined name QgsProject is not defined
pattern: |
  # Toujours imports explicites, jamais tronqués
  from qgis.core import (
      QgsProject, QgsVectorLayer, QgsFeature, QgsField,
      QgsExpression, QgsFeatureRequest, QgsRectangle,
  )
  from PyQt5.QtCore import QVariant  # si construction QgsField
note: Lister tous les Qgs* utilisés. "from qgis.core import (Qgs," tronqué = NameError au runtime.

## tip: native:countpointsinpolygon (compter ou densité par maille)
symptom: densité par maille compter features par zone agrégat spatial bâtiments par cellule
pattern: |
  result = processing.run("native:countpointsinpolygon", {
      "POLYGONS": grid_layer,     # peut être polygones aussi (count = features qui intersectent)
      "POINTS":   features_layer,  # polygones acceptés malgré le nom
      "WEIGHT":   "",
      "CLASSFIELD": "",
      "FIELD":    "NUMPOINTS",
      "OUTPUT":   "memory:densite",
  })
  density_layer = result["OUTPUT"]
  QgsProject.instance().addMapLayer(density_layer)
note: Évite les pièges QVariant car les counts sortent comme int natifs. Préfère TOUJOURS cet algo à un boucle Python manuelle.

## tip: native:creategrid (maillage rectangulaire ou hexa)
symptom: créer grille maillage cellules carrées hexagonales découpage régulier
pattern: |
  result = processing.run("native:creategrid", {
      "TYPE": 2,  # 0=point, 1=line, 2=rectangle, 3=diamond, 4=hexagon
      "EXTENT": f"{xmin},{xmax},{ymin},{ymax} [EPSG:2154]",
      "HSPACING": 200, "VSPACING": 200,
      "HOVERLAY": 0, "VOVERLAY": 0,
      "CRS": "EPSG:2154",
      "OUTPUT": "memory:grille",
  })
  grid_layer = result["OUTPUT"]
note: EXTENT format = "xmin,xmax,ymin,ymax [EPSG:N]" (xmax avant ymin, surprenant). Utilise layer.extent() puis format f-string.

## tip: native:zonalstatistics (stats raster par polygones)
symptom: statistiques raster par zone moyenne min max sur polygones bâtiments par cellule
pattern: |
  result = processing.run("native:zonalstatistics", {
      "INPUT_RASTER": raster_layer,
      "RASTER_BAND": 1,
      "INPUT_VECTOR": polygons_layer,
      "COLUMN_PREFIX": "stat_",
      "STATISTICS": [2, 5, 6],  # 2=mean, 5=min, 6=max ; cf doc complète
  })
note: Modifie INPUT_VECTOR en place (ajoute colonnes). Pour stats vector→vector, utiliser native:joinbylocationsummary.

## tip: native:joinattributestable (jointure attributaire)
symptom: jointure attribut layer fusion attributs clé commune
pattern: |
  result = processing.run("native:joinattributestable", {
      "INPUT": target_layer,
      "FIELD": "id_target",
      "INPUT_2": source_layer,
      "FIELD_2": "id_source",
      "FIELDS_TO_COPY": [],  # vide = tous
      "METHOD": 1,  # 0=tous matches, 1=premier match
      "DISCARD_NONMATCHING": False,
      "PREFIX": "src_",
      "OUTPUT": "memory:joined",
  })
note: Pour jointure spatiale (par intersection), utiliser native:joinattributesbylocation.

## tip: native:clip (découpe par emprise administrative)
symptom: clip découpe limites administratives commune contour précis
pattern: |
  result = processing.run("native:clip", {
      "INPUT": features_layer,
      "OVERLAY": admin_polygon_layer,  # contour commune par exemple
      "OUTPUT": "memory:clipped",
  })
note: Pour compter "dans la commune X" exact (vs bbox), TOUJOURS clip d'abord contre la couche admin (geo.api.gouv.fr ou IGN).

## tip: native:fieldcalculator (ajouter ou transformer champ)
symptom: calculer champ ajouter colonne expression densité ratio normalisation
pattern: |
  result = processing.run("native:fieldcalculator", {
      "INPUT": layer,
      "FIELD_NAME": "densite_m2",
      "FIELD_TYPE": 0,  # 0=float, 1=int, 2=string
      "FIELD_LENGTH": 12, "FIELD_PRECISION": 4,
      "FORMULA": '"surface_m2" / 10000',
      "OUTPUT": "memory:calculated",
  })
note: Préférer cet algo à une boucle Python qui modifie les attributs (évite QVariant et performance).

## tip: Graduated symbology (choroplèthe)
symptom: choroplèthe symbologie graduée classes quantile couleur ramp legend
pattern: |
  from qgis.core import (
      QgsGraduatedSymbolRenderer, QgsClassificationQuantile,
      QgsSymbol, QgsStyle,
  )
  base = QgsSymbol.defaultSymbol(layer.geometryType())
  ramp = QgsStyle.defaultStyle().colorRamp("YlOrRd")
  renderer = QgsGraduatedSymbolRenderer("NUMPOINTS")
  renderer.setClassificationMethod(QgsClassificationQuantile())
  renderer.updateClasses(layer, 5)
  renderer.updateColorRamp(ramp)
  layer.setRenderer(renderer)
  layer.triggerRepaint()
note: TOUJOURS utiliser la factory pattern (3 étapes). Le constructeur direct QgsGraduatedSymbolRenderer(...) avec plus d'arguments = unexpected type error.

## tip: NULL handling expressions QGIS
symptom: NULL not defined comparer attribut null filter expression QGIS
pattern: |
  # Dans Python : feat.attribute() retourne None
  if feat.attribute("hauteur") is None:
      continue
  # Dans QgsExpression : NULL fonctionne (SQL-like)
  expr = QgsExpression('"hauteur" >= 30 AND "hauteur" IS NOT NULL')
  req = QgsFeatureRequest(expr)
  features = layer.getFeatures(req)
note: NULL n'est PAS un littéral Python (NameError). Utiliser None côté Python, NULL dans expressions QGIS.

## tip: Récupérer layer par name ou id
symptom: Layer not found layer name id mapLayer mapLayersByName get_features layer_id
pattern: |
  project = QgsProject.instance()
  # Par name (humain mais peut renvoyer plusieurs)
  layers = project.mapLayersByName("Bâti BDTOPO - Marseille 4e")
  layer = layers[0] if layers else None
  # Par id (unique, jamais d'ambiguïté) — préférer si l'id est connu
  layer = project.mapLayer("B_ti_BDTOPO___Marseille_4e_61846c4d_fccd_4432_a35e_f3f2c054908c")
note: L'id contient des underscores en remplacement de caractères spéciaux. Si get_features ou autre tool renvoie "Layer not found" + liste available_layers, prendre l'id (pas le name).

## tip: StorymapBuilder usage pour storymap DSFR
symptom: construire storymap DSFR CEREMA narration html livrable publication storymap_creator
pattern: |
  # Bootstrap : récupère le module depuis le hub si absent du pod
  import importlib, sys, os
  sys.path.insert(0, "/data/templates")
  from storymap_dsfr import StorymapBuilder
  
  b = StorymapBuilder(
      title="Densité du bâti — Marseille 4e",
      subtitle="Analyse BDTOPO 2024",
      operator="CEREMA",
  )
  b.add_kpis([
      {"label": "Bâtiments", "value": 50110, "unit": ""},
      {"label": "Surface bâtie", "value": 512.9, "unit": "ha"},
  ])
  b.add_chapter(
      title="Contexte",
      content_html="<p>Le 4e arrondissement de Marseille...</p>",
  )
  # Méthodologie générée AUTOMATIQUEMENT depuis le log audit_trail.jsonl
  import json
  events = [json.loads(l) for l in open("/data/agent/treatments.jsonl")]
  b.add_methodology_from_treatments(events)
  b.add_traceability(study_id="...", creator="...")
  html = b.render()
  
  # Sauve dans /data/exports/storymaps/ pour publication
  os.makedirs("/data/exports/storymaps", exist_ok=True)
  with open("/data/exports/storymaps/bati_marseille4.html", "w") as f:
      f.write(html)
note: JAMAIS construire le HTML DSFR par concaténation de strings — le builder gère structure, palette, chain-badges, audit trail. Le fichier doit être dans /data/exports/storymaps/{slug}.html pour que /publish/storymap/{slug} le trouve.

## tip: Recipes existantes (catalogue partagé)
symptom: recipe densité bâti occupation sol risque inondation pression foncière analyse type standard
pattern: |
  # Liste les recipes accessibles dans ce hub
  result = mcp_call("list_recipes", {})
  # Récupère le détail d'une recipe
  recipe = mcp_call("get_recipe", {"id": "densite_bati"})
  # Exécute avec paramètres
  result = mcp_call("run_recipe", {
      "id": "densite_bati",
      "params": {"zone": "Marseille 4e", "taille_maille": 200}
  })
note: Recipes disponibles : densite_bati, occupation_sol, pression_fonciere_cotiere, risque_inondation, urbanisme_general. TOUJOURS chercher d'abord si une recipe couvre le besoin avant de coder.

## tip: Publication HTML public (storymap publi)
symptom: publish storymap HTML public URL share endpoint /publish slug catalog S3 storymap_bati public
pattern: |
  # Le fichier HTML doit être dans /data/exports/storymaps/{slug}.html
  # Appeler ensuite via mcp_call ou httpx POST sur le hub :
  POST {hub_url}/publish/storymap/{slug}
  # Sans body = fallback /data/exports/storymaps/{slug}.html
  # Réponse JSON :
  #   {"url": "https://minio.lab.sspcloud.fr/.../storymap/{slug}.html",
  #    "key": "...", "kind": "storymap", "slug": "...", "size": ..., "published_at": ...}
note: L'URL retournée est publique (ACL public-read). À donner directement à l'user en fin de réponse. Slug = nom court sans accents ni espaces (ex: bati_marseille4).
