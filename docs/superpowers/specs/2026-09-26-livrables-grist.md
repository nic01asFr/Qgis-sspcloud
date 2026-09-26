# Livrables `.grist` : ce que produit `export_grist`, ce qu'attendent les widgets, les formes à proposer

Équipe T6, 26/09/2026. Lecture du code de `export_grist` (dépôt BigQgisMCP), de
l'appelant hub (`hub/hub/main.py`, `export_grist_endpoint`), du générateur de
Scene Manifest (`hub/hub/studies.py`) et des widgets consommateurs (dépôt
Widgets-Grist : Atlas 1.9.0, qgis2grist v2, TaskFlow, grist_forms). Aucun
essai dans une instance Grist réelle : les points marqués « à vérifier en
Grist réel » restent à confirmer.

Complément : la note T4
`2026-09-26-atlas-livrables-diffusion-acces.md`, qui traite Atlas, la
diffusion et l'accès. Ce document s'y aligne. Voir en particulier son §6.5
(voie hybride : hub et S3 pour le figé, Grist et Atlas pour le vivant, avec une
**fiche de partage**) et son §8 (croisement avec T6).

---

## 0. En bref

- **Le Scene Manifest n'a jamais été lisible dans un `.grist` produit par
  `export_grist`.** Sa déclaration à Grist insérait 8 et 12 valeurs dans des
  tables de 7 et 17 colonnes. SQLite refusait, l'exception était avalée, et
  `scene_manifest_embedded` valait `False`. La table existait en SQL sans
  exister pour Grist. **Corrigé**, avec des tests qui exécutent l'écriture sur
  une vraie base SQLite.
- **Même déclaré, le manifest du hub n'aurait rien affiché dans Atlas.** Ses
  couches désignent leurs données par un fichier du volume (`geojson_path`),
  qu'Atlas classe « d'atelier » et ne charge pas. Elles sont maintenant
  **rattachées aux tables du document** (`source.table`, `geometry_fields`).
- **L'agent appelle `export_grist` sans manifest.** Le point d'entrée du hub,
  qui en fournit un, n'est pas un outil de l'agent. Un **manifest minimal est
  désormais généré** depuis le projet QGIS : couches, géométrie, visibilité,
  champs et style.
- Autres corrections : dates en secondes epoch, widget de saisie reconnu par
  Grist, plantage de la page Carte quand une table de statistiques existe,
  lien de téléchargement, rangement dans l'étude, locale `fr-FR`, colonnes
  écartées signalées, description d'outil réécrite en français et raccourcie.
- Pour la suite, trois formes de livrables sont proposées : **dossier
  territorial**, **inventaire et relevé terrain**, **suivi de projet**. Chacune
  est accompagnée de sa fiche de partage, selon T4 §6.5.

---

## 1. État des lieux : ce que produit `export_grist`

Source : `src/qgis_bridge.py`, `_action_export_grist` (mode projet) et
`_action_export_grist_from_html` (mode HTML). Le fichier est une base SQLite
au format Grist, schéma 46, écrite directement : ni l'API ni le moteur Grist
n'interviennent.

### 1.1 Tables de données (mode projet)

Une table par couche vecteur du projet, visible ou non, **y compris les couches
vides**. Les rasters sont ignorés.

| Élément | Règle |
|---|---|
| Nom de table | nom de couche sans accents, `[^A-Za-z0-9_]` → `_` ; préfixe `T` si le nom commence par un chiffre ; suffixe `_1`, `_2` en cas de doublon. **Désormais avec une majuscule initiale**, comme Grist et qgis2grist |
| Colonnes | un champ QGIS = une colonne ; libellé = nom QGIS ; `id` et `manualSort` suffixés `_col` |
| Types | `string` → Text, entiers → Int, réels → Numeric, `bool` → Bool, `date` → Date, `datetime`/`timestamp` → `DateTime:<fuseau>`, sinon Text |
| Widgets QGIS | `ValueMap` → Choice (valeurs seules, sans libellés), `DateTime` → DateTime, `CheckBox` → Bool, `ExternalResource` → **Attachments** |
| Géométrie : point | `latitude`, `longitude` (Numeric, WGS84, 6 décimales) |
| Géométrie : ligne ou surface | `centroid_lat`, `centroid_lon`, `_geojson` (GeoJSON Text, WGS84, avec Z et M s'ils existent) |
| Couleur | `_color` : couleur du rendu QGIS par entité (unique, catégorisé ou gradué) |
| Plafond | 30 colonnes. Au-delà, on garde la géométrie, puis les champs dont le nom contient `nature`, `nom`, `type`, `date`, `surface`, etc., puis les premiers. **Désormais signalé** dans `colonnes_ecartees` |
| Volume | 50 000 entités par couche (`max_features_per_layer`) |

Tables calculées :

- **`<Couche>_Stats`** : créée si une couche porte un champ `year` ou `annee`.
  Elle contient une ligne par année et par groupe (champ contenant `band` ou
  `bande`), avec compte, médiane, moyenne, min et max du **premier** champ
  Numeric.
- **`SceneManifest`** : voir 1.3.

### 1.2 Pages et widgets préconfigurés

| Page | Contenu | Condition |
|---|---|---|
| une page par table | grille brute | toujours |
| **Carte** | widget personnalisé « Custom widget builder » (@berhalak, gristlabs.github.io) dans lequel est **inclus** le HTML Leaflet `templates/web/leaflet_grist_widget.html`, avec la configuration injectée (centre, tables, rendus). Sous le widget, une grille liée | une couche de points non vide, sinon la première table non vide |
| **Statistiques** | graphique Grist natif en barres (année, groupe, médiane, moyenne) et grille | table `_Stats` présente |
| **Saisie terrain** | formulaire Grist natif, widget « Map » de Grist Labs (colonnes associées : Name, Latitude, Longitude) et grille liée | couche avec widgets `ValueMap`, `DateTime` ou `ExternalResource`, ou dont le nom contient « observation » |

Rien d'autre : **aucune** colonne `Ref`, **aucune** formule, **aucune** table
de synthèse, **aucune** règle d'accès hormis la règle par défaut des
propriétaires. Aucune pièce jointe n'est incluse : `_grist_Attachments` est
créée vide.

### 1.3 Scene Manifest

- **Avant ce lot.** La table était créée seulement si l'appelant fournissait
  `scene_manifest_json`, c'est-à-dire par le point d'entrée hub
  `POST /studies/{sid}/projects/{pid}/export_grist`. Sa déclaration échouait
  toujours (voir §0) : la table restait orpheline en SQL, invisible pour Grist
  et pour `docApi.fetchTable`.
- **Après ce lot.**
  - Le manifest fourni est relu. Chaque couche est reconnue par son nom QGIS,
    son `displayName` ou son `id`, puis rattachée à sa table : `source =
    {type: "grist", table, geometry_fields}`. On retire `geojson_path`,
    `geojson` et `data_url`. Une couche sans table, raster ou flux, est
    laissée telle quelle.
  - Sans manifest fourni, et sauf `scene_manifest: false`, le manifest est
    **généré** : `format`, `version: "0.2.2"`, `title`, `crs`, puis une couche
    par table (`id`, `name`, `order`, `geometry_type`, `featureCount`,
    `visibility.defaultVisible` d'après l'arbre des couches, `fields` avec
    `name`, `label` et `gType`, et `style.declarative` tiré du rendu QGIS).
    S'y ajoute `provenance`.
  - La table suit le schéma canonique lu par Atlas et écrit par qgis2grist :
    `manifest_json`, `scene_hash`, `source_file`, `created_at` (DateTime, en
    secondes epoch), plus `n_layers`. Elle est déclarée comme les autres
    tables (`manualSort`, section brute) et **sans page**, visible dans
    « Données brutes ».

### 1.4 Métadonnées et retour de l'outil

- `_grist_DocInfo` : fuseau `timezone` (Europe/Paris par défaut), locale
  **`fr-FR`** (auparavant `en-US` : nombres et dates à l'américaine).
- Règles d'accès : les quatre groupes standard, et une seule règle par défaut.
- L'outil renvoie : `path`, `download_url`, `size_bytes`, `tables`,
  `total_records`, `pages`, `layers` (entités par table),
  `scene_manifest_embedded`, `scene_manifest` (`origine` fourni ou généré,
  `scene_hash`, `version`, `n_layers`, erreurs éventuelles),
  **`ouvrir_dans_grist`** (la consigne d'import) et **`colonnes_ecartees`**.

### 1.5 Mode HTML (`html_path`)

Chaque FeatureCollection d'une page Leaflet devient une table. Les colonnes
`Choice` et `Date` sont détectées, les tables de formulaire aussi, ainsi que
les références `Ref:` entre tables (détectées **toujours**, quel que soit
`detect_relationships`). La carte d'origine est réécrite pour lire les tables.
Ce mode n'embarque **pas** de Scene Manifest. `output_path` y est désormais
pris en compte (il était annoncé mais ignoré).

### 1.6 Où va le fichier, et comment l'utilisateur l'ouvre

- **Appel par l'agent** (outil MCP) : le fichier va dans
  `/data/studies/<étude active>/exports/grist/<nom>.grist`, sinon à la racine
  de `/data`. Auparavant, il allait toujours à la racine, hors de l'étude et de
  son archive.
- **Appel par le hub** : `/data/studies/{sid}/projects/{pid}/exports/<nom>.grist`,
  indexé dans `exports_index`.
- `download_url` : il était construit sur le **seul nom** du fichier, ce qui
  donnait `/files/<nom>.grist`, lien faux pour un export d'étude. Il l'est
  désormais sur le chemin complet (`/studies/<sid>/file/exports/grist/...`).
- Aucun `kind` de `publish_artifact` ne couvre `.grist` (`s3_publication._KINDS`).
  Le livrable se transmet par son lien d'étude, puis l'utilisateur l'importe
  dans son instance Grist : **Ajouter > Importer un document**.

---

## 2. Ce qu'attendent les widgets (versions du 26/09/2026)

### 2.1 Atlas 1.9.0 (`Widgets-Grist/projects/Atlas`)

Contrat de données, sans rien dupliquer de T4 :

| Clé | Attente d'Atlas | `export_grist` avant | après |
|---|---|---|---|
| Table `SceneManifest` | `fetchTable('SceneManifest')`, dernière ligne par `created_at`, `JSON.parse(manifest_json)` | absente pour Grist | **conforme** |
| `layers[].source.table` | nom de table du document ; sinon `geojson_path` → « atelier », non chargée | `geojson_path` (manifest hub) ou rien | **`source.table`** |
| Colonne géométrie ligne/surface | `geometry_json`, ou ce que déclare `source.geometry_fields.geojson` ; la détection sans manifest cherche `geometry_json`, `geometry`, `geom`, `wkt` | `_geojson`, non déclaré : **lignes et surfaces invisibles** | déclaré par `geometry_fields` |
| Colonnes point | `latitude`/`longitude` (et variantes) | conformes | conformes |
| `style.declarative` | `single` (`color`), `categorized` ou `graduated` (`field` = **nom de colonne**, `stops` avec `value`, `color`, `label`, plus `lower`/`upper` en gradué) | absent | généré depuis le rendu QGIS |
| `fields` | `name`, `label`, `gType` : alimentent la fiche d'objet | absents | présents |
| Types de colonnes | la fiche d'objet (1.7 et suivantes, moteur `grist_forms`) s'appuie sur les vrais types Grist : Choice, Bool, Date, Attachments, Ref | Date en **chaîne ISO**, donc invalide | **secondes epoch** |
| `_color` | ignorée (colonne en `_`), la couleur vient du style déclaratif | — | — |
| Accès | `grist.ready({requiredAccess: 'full'})` | — | — |

Widget publié : `https://nic01asfr.github.io/Widgets-Grist/atlas/`
(`widgetId: atlas`, `accessLevel: full`). Tables propres à Atlas, créées par
lui à la première écriture : `Atlas_LayerPrefs`, `Atlas_Story` (un récit par
document), `Maquette_Layers`. Les paramètres d'URL (`?navbar=false`, etc.) et
la règle « pas de droits propres » sont décrits dans T4 §2.2.

### 2.2 qgis2grist v2

C'est le producteur de référence du même contrat : colonne `geometry_json`,
table `QgisWidgets` (`config_json`) et `SceneManifest`. Les couches y ont
`id = name = table`. Un `.grist` d'`export_grist` reste lisible par Atlas sans
`QgisWidgets`, grâce aux `fields` du manifest.

### 2.3 TaskFlow et grist_forms

- **TaskFlow** (Kanban, Gantt, Calendar, Dashboard, Plan, Feuille de temps) :
  tables `Tasks`, `Team` et `Projects`, avec des noms logiques fixes
  (`titre`, `statut` Choice, `dateDebut` et `dateEcheance` Date, `projet`
  Ref:Projects, `assignees` RefList:Team…). Voir
  `projects/tasks_app/COLUMNS_SPEC.md`. Le Kanban crée le schéma s'il manque.
- **grist_forms** : la table `Formulaires` (statuts `brouillon`, `publie`,
  `terrain`) décrit les questionnaires. Atlas monte ce moteur pour sa fiche
  d'objet.

### 2.4 Écarts restants (non corrigés ici)

1. **`_geojson` au lieu de `geometry_json`.** L'écart est couvert par
   `geometry_fields` quand le manifest est présent. Sans manifest
   (`scene_manifest: false`), Atlas ne voit ni lignes ni surfaces. À
   aligner : renommer en `geometry_json`, adapter
   `leaflet_grist_widget.html` (qui lit `_geojson` à plusieurs endroits) et garder la
   lecture de l'ancien nom.
2. **`ExternalResource` → Attachments** avec, pour valeur, un chemin texte.
   Une couche QField déjà relevée donne des cellules en erreur. Proposition :
   Attachments seulement si la couche est vide (formulaire), Text sinon.
3. **Page Carte** : widget Leaflet inclus dans un widget tiers (Custom widget
   builder). Aucune page Atlas n'est préconfigurée. Proposition au §3.4.
4. **Page Saisie terrain** : la clé `customDef` est corrigée en `customView`.
   **À vérifier en Grist réel**, comme toute l'écriture directe en SQLite.
5. **`ValueMap`** : seules les valeurs passent, pas les libellés.
6. **Aucune relation.** `detect_relationships` était sans effet en mode projet.
   Il est retiré du schéma de l'outil ; l'appel du hub, qui le passe encore,
   reste sans effet et ne casse rien.
7. **Le Scene Manifest n'a pas de `settings`** (fond de carte, relief) ni de
   `camera`. Atlas prend ses défauts.

---

## 3. Formes de livrables proposées

Principe, selon T4 §6.5. Un `.grist` est un livrable **vivant** : la carte suit
la table, les droits sont ceux de Grist. Le livrable figé et citable reste
celui du hub (PDF, storymap, assemblage). Chaque forme ci-dessous est
accompagnée d'une **fiche de partage** (§3.5). L'agent la propose, sans jamais
poser lui-même une règle d'accès.

Conventions communes à toutes les formes :

- tables géographiques au contrat du §2.1 : Scene Manifest, `source.table`,
  `geometry_fields` ;
- une table `Sources` : `couche` (Text), `table` (Text), `origine`
  (Text : flux, fichier, calcul), `producteur`, `millesime`, `licence`,
  `url` et `n_entites` (Int). Elle est tirée de ce que le hub sait déjà dire
  de l'origine des couches (`studies.py`, `_origine`) ;
- libellés de colonnes en français lisible, identifiants sans accents.

### 3.1 Dossier territorial (grand public, élus, partenaires)

Usage : présenter un territoire, avec sa carte, ses chiffres clés et des
fiches par lieu ou par thème.

| Table | Colonnes | Remarques |
|---|---|---|
| `Territoire` (1 ligne) | `nom`, `code_insee`, `emprise` (Text, bbox), `date_edition` (Date), `resume` (Text Markdown) | fiche d'accueil |
| `Indicateurs` | `theme` (Choice), `indicateur`, `valeur` (Numeric), `unite`, `millesime`, `source` (Ref:Sources), `commentaire` | alimentée par l'agent (comptes, surfaces, densités) |
| une table par couche | contrat §2.1 | |
| `Fiches` | `titre`, `lieu` (Ref vers la table de couche principale), `texte` (Markdown), `photo` (Attachments), `ordre` (Int) | une fiche par lieu remarquable |
| `Sources` | voir plus haut | |
| `SceneManifest`, `Atlas_Story` | | le récit Atlas tient lieu de visite guidée |

Formules : `Indicateurs.valeur_affichee = f"{$valeur:,.0f} {$unite}"`.
Si l'indicateur est un compte, `Territoire.n_<couche> = len(<Table>.all)`.

Pages : **Accueil** (fiche `Territoire`, grille `Indicateurs`, graphique par
thème), **Carte** (Atlas en pleine page ; la fiche d'objet suffit comme
panneau), **Fiches** (liste et fiche), **Sources**.

### 3.2 Inventaire et relevé terrain (métiers : voirie, patrimoine, environnement)

Usage : recenser des objets, les visiter et les mettre à jour sur le terrain
(téléphone, application Android d'Atlas, ou formulaires Grist).

| Table | Colonnes | Remarques |
|---|---|---|
| `Objets` (géographique) | `type` (Choice), `etat` (Choice : bon, moyen, dégradé, à vérifier), `nom`, `photo` (Attachments), `commentaire`, géométrie | les libellés de `ValueMap` deviennent les choix |
| `Visites` | `objet` (Ref:Objets), `date` (Date), `agent` (Ref:Equipe), `constat` (Choice), `photo` (Attachments), `note` | une ligne par passage |
| `Equipe` | `nom`, `email`, `role` (Choice) | sert aussi aux règles d'accès |
| `Formulaires` | une ligne `statut = terrain` | questionnaire de visite, pour la fiche d'objet d'Atlas |
| `Sources`, `SceneManifest` | | |

Formules :

- `Objets.derniere_visite = max(Visites.lookupRecords(objet=$id).date or [None])` ;
- `Objets.etat_courant` = constat de la dernière visite ;
- `Objets.a_revoir = not $derniere_visite or (TODAY() - $derniere_visite).days > 365`.

Pages : **Carte** (Atlas, catégorisé sur `etat_courant`), **Saisie** (formulaire
Grist sur `Visites`), **Tableau de bord** (table de synthèse par `type` et
`etat_courant`, graphique), **Objets** (grille et fiche).

C'est l'extension naturelle de la page « Saisie terrain » actuelle, et le
pendant Grist d'`export_qfield`.

### 3.3 Suivi de projet ou d'opération (maîtrise d'ouvrage, chargés d'étude)

Usage : suivre un projet qui a des sites ou des emprises : tâches, jalons,
avancement par site.

| Table | Colonnes | Remarques |
|---|---|---|
| `Projects`, `Tasks`, `Team` | **contrat TaskFlow** (`COLUMNS_SPEC.md`) sans écart de nom | Kanban, Gantt et Calendar fonctionnent sans configuration |
| `Sites` (géographique) | `nom`, `projet` (Ref:Projects), `phase` (Choice), `avancement` (Int 0-100), géométrie | |
| `Tasks.site` | Ref:Sites | colonne additionnelle, ignorée par TaskFlow |
| `Sources`, `SceneManifest` | | style gradué sur `avancement` |

Formule : `Sites.taches_ouvertes = len(Tasks.lookupRecords(site=$id, statut="En cours"))`.

Pages : **Planning** (TaskFlow Gantt), **Tableau** (TaskFlow Kanban), **Sites**
(Atlas et grille liée), **Équipe**.

### 3.4 Configuration attendue du widget Atlas (pour une page préconfigurée)

À écrire dans `_grist_Views_section.options` d'une section `custom`, sous la
même forme que la page Carte actuelle (`customView` en chaîne JSON) :

```json
{"customView": "{\"mode\":\"url\",\"url\":\"https://nic01asfr.github.io/Widgets-Grist/atlas/\",\"widgetId\":\"atlas\",\"access\":\"full\",\"renderAfterReady\":true,\"pluginId\":\"\",\"sectionId\":\"\",\"columnsMapping\":null,\"widgetDef\":{\"widgetId\":\"atlas\",\"name\":\"Atlas — Maquette 3D\",\"url\":\"https://nic01asfr.github.io/Widgets-Grist/atlas/\",\"accessLevel\":\"full\",\"renderAfterReady\":true,\"published\":true}}"}
```

- La section porte la table de la couche principale (`tableRef`). Atlas lit
  toutes les tables par `docApi`, il n'a donc pas besoin de correspondance de
  colonnes.
- `access: full` est indispensable : Atlas appelle `fetchTable` et, pour ses
  préférences et son récit, écrit.
- `?navbar=false` seulement pour une intégration en cadre (T4 §2.2).
- **À vérifier en Grist réel** avant d'en faire la page par défaut. Tant que
  ce n'est pas fait, la consigne `ouvrir_dans_grist` indique le geste manuel :
  Ajouter une vue > Personnalisé > Atlas, accès complet.

### 3.5 Fiche de partage (d'après T4 §6.2 à §6.5)

Pour chaque forme, l'agent rédige, **sans rien appliquer** :

| Rubrique | Contenu proposé |
|---|---|
| Propriétaire | le chargé d'étude, qui importe le `.grist` dans son espace |
| Éditeurs | nommés, par exemple `Equipe` pour l'inventaire |
| Règles d'accès | refus par défaut. Lecture pour les lecteurs sur les tables à diffuser. Lecture seule sur `SceneManifest`, `Atlas_LayerPrefs`, `Atlas_Story` et `Maquette_Layers`. Colonnes sensibles (noms de personnes, photos) exclues. Pour l'inventaire, les lignes de `Visites` ne sont éditables que par leur `agent` |
| Accès public | désactivé par défaut. Si le besoin est anonyme : Viewer, et **une clé de lien par destinataire ou par périmètre** |
| Révocation | changer la clé de lien, retirer le partage |
| Rappel RGPD | `cerema_internal` par défaut. « Public » seulement sur confirmation explicite |

Ces règles se posent dans l'interface Grist (Règles d'accès) par le
propriétaire. L'agent **n'écrit jamais** dans `_grist_ACLRules` d'un livrable
destiné à être partagé (T4 §6.4).

### 3.6 Mise en œuvre suggérée

1. **Fait dans ce lot** : un contrat Atlas conforme par défaut (manifest
   généré ou rattaché, dates, locale).
2. **Court terme (BigQgisMCP)** : écarts 1 et 2 du §2.4 ; une page « Atlas »
   préconfigurée (§3.4), après essai en Grist réel ; une table `Sources`
   générée.
3. **Moyen terme** : un paramètre `forme` (`dossier_territorial`,
   `inventaire`, `suivi_projet`) qui ajoute les tables, formules et pages des
   §3.1 à 3.3 autour des tables de couches. Écrire ces gabarits par l'API Grist
   (`grist-mcp-server-v2`) plutôt qu'en SQLite brut est à étudier : les
   formules et les tables de synthèse y sont calculées par le moteur, ce que
   l'écriture directe ne permet pas.
4. **Côté hub** : un `kind` de publication `grist`, si l'on veut que le
   `.grist` figure au catalogue des livrables. Aujourd'hui, il n'y figure pas.

---

## 4. Consignes : textes avant et après

Budget. La consigne tient en deux lignes dans les essentiels. Le reste est dans
la description de l'outil, qui n'est exposée que lorsque le paquet
`exports_metier` est actif (mots « grist », « tableur », etc.).

### 4.1 Appliqué : description de `export_grist` (BigQgisMCP, `main_mcp.py`)

Avant (anglais, environ 2 400 caractères avec le schéma) :

```
Export as a .grist file (SQLite). Two modes: (1) From QGIS project layers
(default) — creates tables, typed columns, map widget, stats, form. (2) From
HTML file (html_path) — takes any HTML containing GeoJSON (...), extracts data
into Grist tables, and transforms the original map into a Grist custom widget
reading from those tables. Same interactive map, but data lives in Grist.
Optional: pass output_path to customize the .grist destination (default
/data/{doc_name}.grist) and/or scene_manifest_json to embed a Scene Manifest
V0.2 as an extra SceneManifest table (cross-runtime style bridge for atlas
widgets).
```

Après :

```
Livrable Grist : un fichier .grist a importer dans Grist (Ajouter > Importer
un document). Mode projet (defaut) : une table par couche vecteur (colonnes
typees, geometrie en WGS84 : latitude/longitude pour les points,
centroid_lat/centroid_lon + _geojson pour lignes et surfaces, couleur QGIS
dans _color), une table SceneManifest que lit le widget Atlas (couches,
geometrie, style), et des pages Carte, Statistiques (si un champ annee/year)
et Saisie terrain (couche 'observation' ou a formulaire). Mode HTML
(html_path) : les GeoJSON d'une page export_web_map/flood/temporal deviennent
des tables. Rendu dans l'etude active ; donne a l'utilisateur `download_url`
et la consigne `ouvrir_dans_grist` (pas de publish_artifact : aucun kind ne
couvre .grist). Signale `colonnes_ecartees` : au-dela de 30 colonnes, les
moins utiles sont omises. Avant l'export : ne garder visibles que les couches
du livrable, renommer les couches comme le lecteur doit les lire, symboliser
dans QGIS.
```

Paramètres : `detect_relationships` est retiré (sans effet) ; `scene_manifest`
(booléen) est ajouté ; les autres descriptions sont traduites et raccourcies.
Un test borne le bloc sous 2 600 caractères.

### 4.2 Appliqué : essentiels, section 2quater (`agent/agent/qgis_agent.py`)

Avant : la règle « LIVRABLES PARTAGEABLES = `publish_artifact` OBLIGATOIRE »
ne prévoyait aucune exception. Pour un `.grist`, l'agent était donc poussé vers
un `publish_artifact` impossible, ou vers un `kind=dataset` détourné.

Après, ajout à la fin de la liste des étapes :

```
   Exception `.grist` (`export_grist`) : aucun kind ne le couvre, donc pas
   de `publish_artifact` ; donne `download_url` et `ouvrir_dans_grist`.
```

Test : cas `livrable_grist` dans `agent/tests/test_instantanes_contexte.py`
(la consigne est présente, `export_grist` est exposé, budget d'outils ≤ 7 000).

### 4.3 Proposé, non appliqué

- Un **profil** « livrable Grist », ou le bloc de diffusion du profil
  `storymap_creator_v15` cité par T4 §8 : choix de la forme (§3), préparation
  dans QGIS (couches visibles, noms, symbologie), puis `export_grist` et fiche
  de partage (§3.5). Il vaut mieux attendre le paramètre `forme` pour ne pas
  décrire dans le prompt des tables que l'outil ne crée pas encore.
- Dans `paquets_outils.py`, le paquet `exports_metier` pourrait aussi
  s'ouvrir sur « dossier territorial », « inventaire » et « suivi de projet ».
  Ce n'est utile qu'avec le paramètre `forme`.

---

## 5. Branches, commits, tests

| Dépôt | Branche | Commits |
|---|---|---|
| BigQgisMCP | `fix/export-grist-formes` (depuis `origin/main` 2f36a8a) | `fix(export_grist): la table scenemanifest existe enfin pour grist et atlas` ; `fix(export_grist): document en francais et tables a majuscule initiale` |
| qgis-sspcloud | `docs/livrables-grist` (depuis `origin/main` 5a621f2) | consigne 2quater, cas d'instantané, cette spec |

Tests :

- BigQgisMCP : `python -m pytest -q -m "not container" tests` → 232 réussis
  (209 avant, plus 23 dans `tests/test_export_grist_formes.py`, qui
  exécutent l'écriture SQLite, le rattachement du manifest, le style et les
  dates).
- qgis-sspcloud agent : 499 réussis (498 et le cas `livrable_grist`). Hub
  inchangé.
- Aucun essai en Grist réel : écriture directe en SQLite, `customView` de la
  page Saisie, section brute sans page. Ces points sont à confirmer à
  l'import dans une instance Grist avant déploiement.

La fixture `agent/tests/fixtures/outils_hub_2f36a8a.json` photographie les
outils du hub à un commit donné. Elle garde l'ancienne description : il faudra
la régénérer au prochain déploiement de BigQgisMCP.
