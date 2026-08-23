# Atlas comme runtime carto — cadrage d'interopérabilité

> Décision du 2026-08-23. Engage cinq dépôts : `qgis-sspcloud`,
> `Widgets-Grist` (Atlas, qgis2grist, grist_forms), `BigQgisMCP`,
> `cerema-offre-de-service`, `Passerelle` (geoai-kit).

## La cible en une phrase

**Un format** — Scene Manifest — **un runtime** — Atlas — **trois surfaces** :
le widget Grist, le bloc carto de l'éditeur BlockNote, la page publiée sur S3.

Aujourd'hui chaque surface a son moteur. Atlas rend les scènes dans Grist,
`geo-components.js` (85 Ko, figé au 17/07) les rend dans les pages publiées, et
l'éditeur n'a pas de bloc carto pilotable. Trois moteurs pour un seul objet.

La cible est celle de Leaflet : une librairie qu'on installe, à qui on donne une
scène, et qui la rend — quel que soit l'endroit où elle tourne.

```
        QGIS  ─┐
    BigQgisMCP ─┼──► Scene Manifest ──► Atlas ──┬──► widget Grist
     qgis2grist ─┤     (le format)   (le runtime)├──► bloc BlockNote
      assistant ─┘                               └──► page S3 publiée
```

## Le diagnostic, mesuré

### Le module Python n'a pas dérivé

Le vendor du hub est **identique** à l'autorité `cerema-offre-de-service`
(diff vide, V0.2.2). Le drift redouté par `hub/hub/vendor/VENDORED_FROM.md`
n'existe pas.

### Mais deux contrats différents portent le même numéro

C'est le vrai obstacle, et il n'est pas dans le hub.

| | Modèle Pydantic `cerema-offre-de-service` | JSON Schema publié (lu par Atlas) |
|---|---|---|
| requis racine | `format`, `version`, `scene_id`, `scene_hash`, `crs`, `bbox`, `source`, `layers` | `version`, `layers` |
| requis couche | `id`, `name`, `order`, `geomType`, `data_url` | `name` |
| géométrie | `geomType` littéral `Polygon` | `geometry_type` **ou** `geomType`, libre |
| origine donnée | `data_url` | `source`, chaîne ou objet |
| absents de l'autre | — | `classification`, `meta`, `terrain` |

Les deux s'appellent « Scene Manifest 0.2.2 ». L'un est strict et fermé, l'autre
tolérant et ouvert. **Aucun producteur ne peut satisfaire les deux sans savoir
lequel fait autorité.** C'est la décision à prendre en premier, et elle dépasse
qgis-sspcloud.

### Le hub est à une ligne du schéma d'Atlas

Vérifié par validation :

```
manifest du hub, tel quel      → refusé : 'version' is a required property
manifest du hub + version:"0.2.2" → VALIDE
```

Contre le modèle Pydantic en revanche, il manque `bbox`, `crs`, `scene_hash`,
`scene_id`, `version`, et un mapping de couche (`geometry_type`→`geomType`,
`geojson_path`→`data_url`, `order`). Le hub ne valide d'ailleurs jamais : il
n'appelle le vendor que pour hasher (`main.py:3985`, `main.py:4048`, où le
commentaire annonce un « Sprint C-2 » qui n'a jamais eu lieu).

### Et valider ne suffirait pas : Atlas ne sait pas charger nos couches

Le point décisif, qu'un contrôle de schéma ne révèle pas.
`lib/scene-loader.js` l'annonce dès sa première ligne — *« Chargement doc Grist
qgis2grist via Scene Manifest V0.2 »*. Atlas résout chaque couche vers une
**table Grist** (`ml.source.table` → `fetchTableToRows` → `rowsToGeoJSON`). Il
n'existe aucun chemin « charger un GeoJSON depuis une URL ».

Or le hub produit des couches qui pointent vers des **fichiers GeoJSON sur PVC**.
Un manifest du hub peut donc être parfaitement valide et rester vide à l'écran.

**C'est là qu'est le travail réel** : donner à Atlas une seconde origine de
données. Le reste — `rowsToGeoJSON`, la symbolisation, les contrôles, le récit —
est déjà en place et indifférent à la provenance.

## Les trois chantiers, dans l'ordre des dépendances

### 0. Trancher le contrat

Décider lequel des deux « 0.2.2 » fait autorité, ou les réconcilier
explicitement (le strict comme profil de validation, le tolérant comme surface
de lecture). Sans cela, chaque projet continue de viser une cible différente.
Décision inter-projets — à porter en axe transverse.

### 1. Socle — le producteur devient conforme

- Le hub émet `version` (une ligne) et déclare l'origine de ses couches sous une
  forme qu'un runtime peut résoudre.
- La validation stricte est activée au build — le « Sprint C-2 ».
- `BigQgisMCP.export_grist` produit la même chose.

Les **41 sites de lecture** internes (`geojson_path` ×22, `geometry_type` ×19)
passent par un lecteur unique qui accepte les deux graphies, pour que les
manifests déjà persistés sur PVC continuent d'être lus sans migration.

### 2. Runtime — Atlas devient un composant

Atlas sait **déjà** tourner hors Grist : `lib/hote.js` distingue les modes
(`grist`, `local`, `vitrine`) et le noyau de rendu n'importe rien de Grist. Les
69 appels à l'API Grist sont concentrés dans `app_v7.js`, plus 8 dans
`grist-adapter.js`. Le découpage suit cette frontière existante :

| Couche | Modules | Lignes | Grist |
|---|---|---|---|
| **Noyau** — rend une scène | `declarative-style`, `controls`, `layer-order`, `terrain-base`, `viewport`, `basemap-layers`, `point-fallback`, `model-layer`, `scene-prefs`, `feuille-mobile`, `edge-scroll`, `viewer-controls` | ~1870 | aucun |
| **Binding** — lit un manifest | `scene-loader`, `manifest-binding`, `story` | ~630 | 3 imports à couper |
| **Hôte** — d'où viennent les données | `grist-*`, `hote*`, `data-client`, `view-mode`, `geo-tables`, `decouverte` | ~1890 | par nature |

Les trois imports à couper sont connus : `defaultLayerVisible` et
`applyAtlas3dFromRows` (`grist-sync.js`), `parseGristBool` (`grist-bool.js`,
12 lignes — un parseur de booléen, à remonter au noyau).

C'est dans cette couche « hôte » que s'ajoute l'origine GeoJSON par URL, à côté
de l'origine table Grist.

Le paquet publié aujourd'hui est un **manifeste de widget Grist**
(`package.json` → `grist.widgetId`), pas un package consommable. Il lui faut un
point d'entrée et des `exports`.

Une fois le noyau isolé, il remplace `geo-components.js` dans les pages publiées
et devient le bloc carto de l'éditeur — même code, trois hôtes.

### 3. Formulaires — FormDef

Le pendant du Scene Manifest pour la saisie. Cadré le 2026-07-29
(`grist-forms-blocknote-binding-axis`), six phases assignées à qgis-sspcloud :
aucune n'a démarré, le hub ne connaît ni `survey_*` ni `iframe_grist_form`.

Le socle existe côté Widgets-Grist : `formdef.schema.json`, moteur, publication,
pont QGIS (`qgis-form-to-formdef.js`), 89+ tests. qgis2grist traduit déjà les
widgets QGIS d'un paquet QField en champs typés — c'est l'amont naturel.

## L'invariant à tenir

Celui que pose `QFIELD-COMPLET-GRIST-IDEAL.md` : **un seul contrat par
domaine**. Scene Manifest pour la carto, FormDef pour les formulaires. Pas de
second moteur, pas de format parallèle « en attendant ».

C'est ce qui permet à l'assistant de produire une carte : il écrit une scène
conforme, et n'a rien d'autre à savoir.
