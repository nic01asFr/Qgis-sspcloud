# Atlas comme composant — cadrage

> 2026-08-23. Complète [interop-atlas-scene-manifest.md](interop-atlas-scene-manifest.md).
> Ce document ne décide rien pour Widgets-Grist : il propose une forme, et dit
> ce qu'elle coûte de chaque côté.

## Le constat qui rend le cadrage simple

Atlas **est déjà** ce qu'il faut : une page web autonome, embarquable en iframe,
paramétrable par URL (`access`, `mode`, `models`, `readonly`, `vitrine`), avec
trois modes d'hôte déjà distingués — `grist`, `local`, `vitrine`.

Et de notre côté, l'éditeur BlockNote rend déjà ses six blocs riches en
**iframe** vers `/studies/{sid}/components/{cid}/render`, en assumant le choix :
« Réutilise le rendu du hub. Pas de duplication code MapLibre/ChartJS côté
React. »

Les deux moitiés du pont existent. Ce qui manque est au milieu.

## Ce qui manque, exactement

Le Scene Manifest est un contrat **déclaratif et agnostique du transport**. Mais
sa seule implémentation de référence, Atlas, ne sait le lire que **depuis une
table Grist** :

```js
// scene-loader.js
loadLatestSceneManifest(docApi)   →  docApi.fetchTable('SceneManifest')
loadSceneManifestLayers(...)      →  ml.source?.table || ml.id → fetchTable
```

Et sa configuration hors Grist attend une **connexion Grist** — `baseUrl`,
`jeton`, `docId`. Il n'existe aucun paramètre désignant une scène.

Autrement dit : **le contrat est découplé du transport, son implémentation ne
l'est pas.** Tout le chantier tient dans ce découplage — pas dans un portage.

## La forme proposée

**Atlas reste une page, embarquée en iframe, paramétrée par URL.** Rien à porter
en React, rien à publier en npm, rien à changer dans le widget Grist.

```
<iframe src="https://…/w/atlas/?scene=<url>&readonly=1&vitrine=1">
```

| Paramètre | Rôle | État |
|---|---|---|
| `scene` | URL d'un Scene Manifest à charger | **à ajouter** |
| `readonly` | pas d'édition | existe |
| `vitrine` | pas de découverte de compte | existe |
| `access`, `mode`, `models` | — | existent |

Côté hub, deux points de bascule seulement :

- `rendering.runtime: "atlas"` remplace `maplibre` et `maplibre_three` ;
- `_interactive_map_partial.j2` monte l'iframe Atlas au lieu du MapLibre maison.

L'éditeur, la page S3 publiée et le widget Grist en bénéficient **sans changer
une ligne** — ils consomment tous le rendu du hub.

## Ce que cela demande à Atlas

Deux capacités, pas davantage :

**1. Charger un manifest depuis une URL.** Aujourd'hui `loadLatestSceneManifest`
prend un `docApi` et lit une table. Il lui faut un frère qui prend une URL,
`fetch`, et rend le même objet. Le reste du pipeline est inchangé.

**2. Résoudre une couche autrement que par une table.** C'est le vrai travail.
`loadSceneManifestLayers` fait `ml.source?.table || ml.id` puis `fetchTable`.
Quand la couche porte une origine servie, il faut court-circuiter cette
résolution.

Et ce que nous produisons **n'est pas du GeoJSON brut** — c'est ce que j'avais
dit à tort. Le hub externalise déjà les données à la publication, en cascade :

| Ce que porte la couche | Quand |
|---|---|
| `source_type: "pmtiles"` + `tiles_url` + `bbox` + `min_zoom`/`max_zoom` | par défaut — 19 Mo deviennent ~2 Mo, chargement progressif par Range Requests |
| `geojson` = **une URL string** (gzip) | si l'encodage PMTiles échoue |
| `geojson` = dict inline | sous 500 Ko |

MapLibre lit nativement les trois — les tuiles via le plugin `pmtiles`. C'est
donc **moins de travail qu'un chargeur GeoJSON**, et un meilleur résultat sur
les couches lourdes, qui sont justement celles qui posent problème.

## Ce que cela demande au hub

**Rendre le mode de livraison déclaratif.** Aujourd'hui la cascade est décidée
par un seuil de 500 Ko au moment du rendu : le même composant peut basculer de
mode d'un rendu à l'autre, et un consommateur doit supporter les trois sans
savoir lequel arrivera.

Un champ de livraison sur `ComponentSource`, orthogonal à `scope` :

| Mode | Intention | Ce qui est publié |
|---|---|---|
| `inline` | autoportant, figé, marche hors ligne | la donnée, dans le document |
| `url` | livrable léger, donnée remplaçable sans régénérer | un fichier à côté |
| `tuiles` | idem, pour le volume | des tuiles PMTiles |
| `vivant` | jamais de copie — WFS, PostGIS, table Grist | **rien** |
| `auto` | la cascade actuelle | selon la taille |

`auto` par défaut : aucun composant existant ne change de comportement.

Le mode `vivant` est celui qui manque le plus, et celui qui justifie le mieux le
champ : c'est le seul qui ne fabrique **aucune copie** de la donnée. Externaliser
sur S3, c'est publier ; que ce soit décidé par un seuil de taille plutôt que
déclaré est un défaut, pas un détail.

## Les sources externes — le trou le plus béant

Le service gère déjà **54 sources externes** au catalogue : 25 WFS, 8 WMS,
6 XYZ, 1 WMTS, 7 API. `setup_qgis_connections.py` les pré-charge dans le profil
QGIS pour qu'elles apparaissent au démarrage dans le panneau Explorateur.

Mais elles ne survivent pas au passage vers le manifest. Le producteur écrit :

```python
layer_entry = {id, name, order, geometry_type, visible, style{…}}
# puis, pour les couches vecteur uniquement : geojson_path, n_features, crs
```

Pour une couche WMS, il n'y a **aucune origine** — le manifest déclare « une
couche raster nommée X, en bleu » sans dire où elle est. Pour une couche WFS,
les entités sont converties en GeoJSON local : le flux devient une copie figée,
et le lien avec la source vivante est perdu.

Aucune externalisation ne rattrape ce cas : il n'y a rien à externaliser.

C'est le trou qui empêche « afficher quel que soit le contexte ». Et c'est aussi
ce qui donne son vrai sens au mode `vivant` : ce n'est pas un mode exotique
réservé aux bases de données, c'est **le mode naturel de la moitié du
catalogue**. Un WMS n'a jamais besoin d'être copié — MapLibre l'affiche
directement, comme QGIS.

Ce qu'il faut porter dans la couche, sous une union discriminée par `type` —
celle du 0.3.2 convient telle quelle :

| `type` | Ce qu'il faut | Qui sait l'afficher |
|---|---|---|
| `wms` / `wmts` | `url`, `layers`, `format`, `crs` | MapLibre nativement (source `raster`) |
| `xyz` | `url` avec `{z}/{x}/{y}` | MapLibre nativement |
| `wfs` | `url`, `typename`, `bbox` | à convertir, ou tuiler |
| `pmtiles` | `tiles_url`, `bbox`, zooms | plugin `pmtiles` |
| `geojson_url` | `url` | MapLibre nativement |
| `geojson` | inline | MapLibre nativement |

Quatre des six sont déjà natifs à MapLibre. Le travail n'est pas de les
implémenter, c'est de **ne plus les perdre**.

## Trois classes de source, selon ce qui peut traverser

Toutes les sources de l'atelier ne sont pas transposables en aval, et ce n'est
pas affaire de prudence : certaines ne le peuvent **physiquement pas**.

`postgres-cerema-postgresql` est un service **ClusterIP**, sans IP externe.
QGIS, qui tourne dans le cluster, s'y connecte. Atlas, qui tourne dans le
navigateur de l'utilisateur, ne le peut pas — même avec des identifiants
valides. La base ne traverse pas.

D'où une distinction à porter dans le contrat, parce que le producteur et le
consommateur doivent en tirer des conséquences opposées :

| Classe | Exemples | QGIS | Atlas | Ce que le producteur doit faire |
|---|---|---|---|---|
| **Atelier seulement** | PostGIS, GeoPackage local, fichier PVC | oui | **non** | matérialiser avant publication — tuiles, GeoJSON publié |
| **Transposable telle quelle** | WMS, WMTS, XYZ, WFS publics | oui | oui | porter l'URL, ne rien copier |
| **Transposable par délégation** | table Grist, service à auth client | oui | oui | porter la référence ; l'hôte fournit l'accès |

La première classe justifie à elle seule la cascade d'externalisation existante :
elle n'est pas une optimisation de poids, c'est **le seul moyen qu'une donnée
d'atelier atteigne un navigateur**.

La deuxième est celle qu'on perd aujourd'hui pour rien : un WMS public n'a
aucune raison d'être copié, et il l'est — ou plutôt il est simplement oublié.

Conséquence pratique : le manifest doit dire **de quelle classe** relève chaque
couche. Sans cela, Atlas ne peut que tenter et échouer — ce qu'il fait déjà, en
silence, en repliant `ml.source?.table || ml.id` sur un nom de table imaginaire.
Un consommateur doit pouvoir distinguer « je ne sais pas lire cette source » de
« cette source n'était pas censée m'arriver ».

## L'esprit qu'on préserve

- **Un seul contrat par domaine.** Scene Manifest pour la carte, FormDef pour
  la saisie. On ne redéfinit pas, on référence — l'index de contrats du hub
  renvoie vers Widgets-Grist pour les deux.
- **Pas de code partagé, des contrats partagés.** Atlas ne devient pas une
  dépendance de qgis-sspcloud, ni l'inverse. Une iframe et une URL suffisent.
- **Chacun reste maître de son runtime.** Le widget Grist ne change pas ; notre
  éditeur ne change pas ; c'est le gabarit du hub qui bascule.

## Ce qui reste ouvert

- **L'adresse.** Nos contrats sont servis par une instance SSPCloud
  (`user-nic01asfr-qgis…`), pas un domaine stable comme les GitHub Pages de
  Widgets-Grist. Pour un contrat durable de l'écosystème, il faudra trancher.
- **Le volume, même tuilé.** `DEFER_FEATURE_THRESHOLD` (8000 entités) ne
  s'applique chez Atlas que si la couche est **déjà masquée** — une couche
  visible et lourde est chargée intégralement. Les tuiles atténuent, elles ne
  suppriment pas la question.
- **`vivant` avec une base.** Cela suppose des identifiants quelque part : soit
  un proxy côté hub, soit des sources qui portent leur propre authentification
  (WFS IGN, table Grist avec ses droits). Commencer par les secondes.
