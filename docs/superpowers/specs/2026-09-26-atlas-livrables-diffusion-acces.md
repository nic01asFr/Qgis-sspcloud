# Atlas : livrables, diffusion et accès aux données d'un projet

> 2026-09-26, équipe T4 (supervision : agent principal). Branche
> `docs/atlas-livrables`.
>
> Objet : faire le point sur Atlas, qui a évolué depuis la bascule du hub
> (contrat composant 0.4, 08/09). Le document recense les livrables et les
> modes de diffusion qu'Atlas permet. Il répond à la question « la mise à
> disposition et la gestion des accès aux données d'un projet peuvent-elles
> passer par Atlas ? ». Il met à jour les consignes des livrables à canevas
> web.
>
> Méthode : lecture du dépôt Atlas (sans rien y écrire) et du code du hub.
> Aucun essai en ligne : les écarts du §3 sont **constatés à la lecture du
> code** et restent à confirmer par un essai.
>
> Croisements : les livrables `.grist` relèvent de T6, les agents dédiés de
> T7. Ce document renvoie à leurs travaux sans les reprendre (§8).

---

## 0. En bref

1. **Atlas est en 1.9.0**, publiée le 26/09/2026 sur GitHub Pages. Depuis la
   bascule du hub, il a gagné la fiche d'objet (un formulaire), le relevé de
   terrain avec photos et application Android, et le dessin de géométries
   dans la carte. Le récit peut suivre un trajet, et le rendu couvre le
   relief et l'orthophoto IGN, la nuit et un catalogue d'objets 3D.
2. **Atlas a deux régimes, et ils ne donnent pas les mêmes droits** :
   - *dans un document Grist*, il lit et écrit le document, et les ACL Grist
     font foi ;
   - *hors document* (`?scene=<url>`), il lit une scène publiée, n'écrit
     rien, et ne présente aucune identité à qui que ce soit.
3. **Côté hub, la bascule `runtime: "atlas"` est câblée mais ne peut pas
   s'activer seule**, pour trois raisons :
   - la scène n'est jamais publiée : le kind `scene` est refusé ;
   - son adresse n'est jamais transmise au gabarit ;
   - une scène non `public` est illisible depuis Atlas.

   Aujourd'hui, Atlas ne s'affiche dans un livrable que si l'auteur pose
   `params.scene_url` à la main, vers une scène lisible sans connexion.
4. **Gestion des accès « via Atlas » : non en tant que telle, oui via
   Grist.** Atlas n'a pas et ne veut pas de droits propres (décision
   documentée côté Atlas). La réponse est donc « via un document Grist,
   rendu par Atlas ». Cette voie convient aux données **vivantes,
   collaboratives et à droits fins** : lignes, colonnes, clé de lien par
   destinataire, saisie de terrain. Le hub et S3 restent la voie des
   livrables **figés, versionnés et signés**, et des données lourdes.
5. **Consignes mises à jour** sur la branche, sans changer de contrat d'API :
   - le prompt essentiel : une phrase obsolète remplacée, −17 jetons ;
   - le profil `storymap_creator_v15` : bloc Atlas ajouté, 4 488 jetons
     pour un plafond de 4 500 ;
   - les descriptions de `publish_component` ;
   - `docs/api/components-catalog.md` et `docs/api/blocknote-editor.md`,
     qui présentaient Atlas comme un client du catalogue, ce qui est faux.

---

## 1. Où est Atlas

| Élément | Emplacement |
|---|---|
| Dépôt | `nic01asFr/Widgets-Grist`, en local `Github Repositories/Widgets Grist` (branche de travail `atlas-formulaire-entite`) |
| Sources | `projects/Atlas/` (`index_v7.html`, `app_v7.js`, `lib/`, `docs/`, `CLAUDE.md` de 2 150 lignes) |
| Version publiée | `published/atlas/`, `package.json` en **1.9.0** (commit `e9571dd`, 26/09/2026), identique sur `origin/main` |
| Page servie | `https://nic01asfr.github.io/Widgets-Grist/atlas/`, qui est la valeur par défaut de `ATLAS_URL` dans `hub/hub/main.py` |
| Vitrine | `https://nic01asfr.github.io/Widgets-Grist/w/atlas/`, générée depuis `published/atlas/vitrine.json` |
| Démos de scènes | `published/atlas/demos/` : `cascade-aygalades-marseille/` (ortho et LiDAR IGN, récit) et `osm-marseille-vieux-port/` |
| Schémas | `published/schemas/scene-manifest-0.2.2.schema.json` et `formdef-1.0.schema.json` |
| Application Android | `packages/atlas-app/` (Capacitor), APK sur `releases/latest/download/atlas.apk` |
| Cadrages utiles | `docs/CADRAGE-PORTABLE-PARTAGE-IFRAME.md`, `CADRAGE-IDENTITE-ACL.md`, `CADRAGE-SCENE-EXTERNE-ET-DECOUPLAGE.md` |

**Homonymes à ne pas confondre.** C'est une source d'erreur pour l'agent.

| Nom | Ce que c'est |
|---|---|
| Atlas (visionneuse) | le widget et runtime décrit ici |
| `rendering.runtime: "atlas"` | le hub monte la visionneuse Atlas en iframe |
| `atlas_immersive` | kind d'assemblage **sans rendu** (`render_assembly` répond 501) |
| `AtlasConfig` (`component_params.py`) | le mode atlas *cartographique* : une carte par entité, inerte |
| Atlas QGIS (`map_composer`, `QgsLayoutAtlas`) | l'export PDF multipage de QGIS |
| `atlas-territorial-briques` | un autre dépôt (GitLab Cerema, Strate) : une usine à fiches territoriales, pas la visionneuse |
| `brocatlas` | un produit distinct, sans lien |

---

## 2. Ce qui a changé

### 2.1 Versions depuis la bascule du hub

| Version | Date | Apports qui comptent pour les livrables |
|---|---|---|
| 1.7.0 | 15/09 | La **fiche d'objet est un formulaire** : la ligne de la table, avec étapes, listes et champs obligatoires, importable d'un projet QField via qgis2grist. Le bas de carte est rangé (légende, récit, attribution). `?navbar=false` retire la barre pour l'intégration en cadre. `?mode=view` garde l'accès `full`. |
| 1.7.1 | 17/09 | Les **contrôles lecteur suivent le type de colonne** : dates, nombres, catégories, listes, texte, et « sans valeur ». Formulaires retirables. Démo sur ortho IGN et relief LiDAR HD. |
| 1.8.0 | 20/09 | **Photo depuis la fiche**, versée en pièce jointe Grist. **Pastille « Relevé »** : les formulaires offerts et « le plus proche de moi ». Feuilles mobiles. **Catalogue d'objets `atlas-objets/0.1`** (`?objets=`). Modèles glTF compressés. |
| 1.9.0 | 26/09 | **Dessin dans la carte** : couche vide, puis point, ligne, surface avec accroche ; édition sommet par sommet, conflit détecté ; création en série. Le **récit s'ancre sur un trajet**. Soleil sur 24 h et éclairage nocturne. La caméra vise la zone visible sur téléphone et tablette. |
| en cours | non commité | `sceneEmbarquee()` (`lib/scene-externe.js`) lit une scène posée dans la page (`window.__ATLAS_SCENE__`). Un livrable **autoportant** devient possible : un seul fichier HTML, ou un dossier « données à côté », hors `?scene=`, qui exige https. |

### 2.2 Contrat d'intégration (état au 26/09)

**Format.** Scene Manifest **0.2.2**. L'autorité est le schéma servi par
Widgets-Grist ; le hub en garde une copie (`hub/hub/schemas/`). Les clés
qu'Atlas exploite sont :

- `layers[]` :
  - source `inline` : GeoJSON objet, la couche est complète ;
  - source par URL : GeoJSON distant ;
  - source `xyz` : raster, avec `min_zoom`/`max_zoom` obligatoires dans les faits ;
  - `wms`, `wmts` et `wfs` sont des échecs déclarés ;
  - `source.table` n'est lue que dans un document ;
- `settings` : `basemap` (dont `ortho-ign`), `terrain3D`, `terrainSource: "ign"`,
  `terrainExaggeration`, `timeOfDay`, `shadows`, `sky`, `labels` ;
- `story.steps[].state` : caméra, couches, filtres, symbolisation, heure et
  trajet par étape ;
- `camera` et `provenance` (attribution et emprise).

**Paramètres d'URL posés par l'hôte :**

| Paramètre | Effet |
|---|---|
| `?scene=<url>` | charge la scène et **coupe l'accès au document** : `grist.ready()` n'est pas appelé, rien ne s'écrit |
| `?vitrine=1` | « je ne suis pas dans un document » : transport HTTP |
| `?mode=view` | ne peut que restreindre, jamais octroyer |
| `?navbar=false` | retire la barre du haut, pour une intégration en cadre |
| `?no3d` | coupe les modèles 3D |
| `?models=` | base du catalogue 3D |
| `?objets=` | catalogue d'objets paramétriques |

`readonly` et `access` appartiennent à Grist : le hub ne les pose pas, et il
a raison.

**Règles de sécurité d'une scène externe** (`?scene=`) :

- seules `https:` et `http://localhost` sont admises ;
- la scène est chargée par un `fetch` **sans identifiants**, et doit donc
  être servie avec `Access-Control-Allow-Origin` ;
- `popup_template` est rendu comme du texte ;
- aucune préférence ni aucun récit n'est écrit.

**Droits et identité :**

- Atlas **n'a pas de droits propres**. Il subit les ACL Grist, et une sonde
  d'écriture tranche quand Grist annonce l'écriture à tort.
- Il ne connaît que le `userId` du jeton, pas le nom de la personne.
- Il ne pose **pas** de règles ACL ; c'est une recommandation Atlas, « non
  par défaut ».
- Préférences et récit sont globaux au document : **un récit par document**.
  Plusieurs récits et des préférences par personne sont des lots
  « structurants », non faits.

**Partage selon Atlas :**

- `?style=singlePage` suit la session : un éditeur reste éditeur ;
- `?embed=true` force la lecture ;
- un document public (Public Access = Viewer), avec des Access Rules en
  refus par défaut et des **link keys** (`?UUID_=…` lus par
  `user.LinkKey`), donne une tranche de données par lien ;
- un jeton `getAccessToken` (JWT d'environ 15 min) ne sert pas à une
  intégration permanente.

**Application Android.** Sur `grist.numerique.gouv.fr`, l'en-tête
`Authorization` est refusé au contrôle préalable CORS. Un navigateur ne peut
donc pas présenter de clé API. L'APK émet ses requêtes hors du moteur web :
c'est sa raison d'être, pour le terrain et la photo.

---

## 3. Ce que le hub en fait aujourd'hui, et les écarts

### 3.1 Chemin prévu

Le composant `interactive_map` porte `rendering.runtime: "atlas"`. Tous les
exemples canoniques le font, et `test_bascule_atlas.py` y veille.

1. `_pre_render_component_html` voit `runtime == "atlas"` et une `scene_url`.
2. Il rend `_interactive_map_atlas.j2` : une iframe
   `ATLAS_URL?scene=…&mode=view&vitrine=1[&no3d=1][&models=…]` avec
   `sandbox="allow-scripts allow-same-origin allow-popups"`.
3. La scène est censée être publiée par `_publier_scene_rendue` : une scène
   0.2.2 dont les couches sont externalisées (PMTiles ou GeoJSON gzip), sous
   `/published/{owner}/scene/{cid}-scene`.

### 3.2 Écarts constatés à la lecture du code (à confirmer par essai)

| # | Écart | Où | Effet |
|---|---|---|---|
| E1 | `"scene"` n'est pas dans `s3_publication._KINDS`. `s3_key()` lève `ValueError`, que `_publier_scene_rendue` avale. | `s3_publication.py:44-73, 374-376` ; `main.py:3658-3704` | **aucune scène n'est publiée**, un avertissement au journal seulement |
| E2 | L'URL renvoyée par `_publier_scene_rendue` est ignorée, et `ctx["scene_url"]` n'est posé nulle part. | `main.py:7686`, `7880` | la bascule ne s'active que si `params.scene_url` est posé à la main |
| E3 | La scène prend l'audience du composant (`cerema_internal` par défaut). Une publication non `public` exige une identité hub et ne porte pas `Access-Control-Allow-Origin`. Atlas, sur github.io, la lit sans identifiants. | `main.py:10530-10604`, `10830` ; `scene-externe.js:199` | même corrigés E1 et E2, **Atlas ne peut rendre que des livrables `public`**. L'iframe afficherait « scène refusée : HTTP 401 ». |
| E4 | Le commentaire `ATLAS_URL` dit « pas de valeur par défaut devinée », alors qu'une valeur par défaut est codée. | `main.py:1371-1385` | le commentaire trompe, sans effet sur le fonctionnement |
| E5 | La doc et les descriptions d'outils présentaient Atlas comme un client de `/catalog/components` et un intégrateur d'iframes `/published`. | `components-catalog.md`, `native_tools_v2.py` | l'agent promettait une intégration qui n'existe pas. **Corrigé** (§7). |
| E6 | Le profil `storymap_creator_v15` déclare `publication.public_url` vers `minio.lab.sspcloud.fr`, que le prompt interdit (403). | `storymap_creator_v15.yaml:108-110` | c'est de la configuration, à vérifier avant de retirer : lue par du code ? |
| E7 | Le bandeau « Livrable public — généré par l'agent QGIS » est injecté sur tout `storymap`/`flux` HTML, quelle que soit l'audience. | `main.py:10755-10785` | le libellé est faux pour un livrable `cerema_internal` |

**Conséquence pour les consignes.** Tant que E1 à E3 ne sont pas traités,
l'agent ne doit pas promettre une carte Atlas dans un livrable non public.
C'est ce que disent désormais le prompt et le profil.

### 3.3 Correctifs proposés (code, non appliqués ici)

Ils touchent au comportement de publication. Ils relèvent d'un lot de code
avec essai réel, pas de ce lot de consignes.

1. **E1** : ajouter `"scene"` à `_KINDS`, avec `_KIND_EXT["scene"] = "json"`,
   et un test qui publie réellement via `s3_key`.
2. **E2** : poser `ctx["scene_url"]` avec le retour de
   `_publier_scene_rendue`, **seulement si** l'audience est `public`.
3. **E3** : garder la règle, pour ne rien publier en `public` par défaut.
   Pour un livrable non public, **ne pas** basculer sur Atlas : garder le
   rendu MapLibre, qui est servi dans la même origine avec cookie. Une
   alternative serait une URL signée à durée courte, mais les jetons qui
   expirent sont un problème déjà vécu (CADRAGE-SCENE-EXTERNE, « la
   révocation »). Elle est à écarter sans demande explicite.
4. **E4** : corriger le commentaire.
5. **E6** : retirer `public_url` si aucun code ne le lit.
6. **E7** : conditionner le libellé du bandeau à l'audience.

---

## 4. Livrables envisageables via Atlas

| Livrable | Voie | Ce qu'Atlas apporte | Contraintes | Statut |
|---|---|---|---|---|
| **Carte ou scène explorable publique** | `interactive_map` en `runtime: "atlas"`, scène publique | Relief et ortho IGN, 3D, contrôles lecteur typés, soleil | Audience `public` confirmée. Couches https avec CORS ; `xyz` avec bornes de zoom. | **bloqué par E1 à E3**, sauf `scene_url` manuel |
| **Récit ou storymap 3D** | `story` dans la scène (hors document) ou `Atlas_Story` (dans Grist) | Chaque étape est un état complet et citable ; trajet suivi | Un seul récit par document Grist. Récit en lecture seule hors document. | disponible dans Grist ; côté hub, c'est l'assemblage `storymap_narrative_dsfr` qui porte le récit |
| **Carte vivante d'un projet** (données tenues à jour) | document Grist avec widget Atlas et Scene Manifest (via `export_grist` et qgis2grist) | « La carte est une vue, pas un export » : zéro republication | Droits = ACL Grist. Volume limité par Grist (pas de raster lourd). | disponible. **Livrable `.grist` : voir T6.** |
| **Campagne de relevé terrain** | le même document, formulaires offerts hors édition, APK Android | Fiche-formulaire (FormDef, import QField), photo, « le plus proche », dessin de géométries (1.9) | Compte Grist et clé API sur l'appareil (APK). La localisation ne marche pas dans l'iframe widget. | disponible (1.8/1.9) |
| **Page Grist intégrée dans un livrable hub** | composant `iframe_grist` (`widget_url` = page du document, `embed=true` ou `singlePage`) | Atlas vivant au milieu d'une storymap | Lisible par un tiers seulement si le document est public ou si une clé de lien est posée : les cookies Grist en iframe tierce ne sont pas fiables. | disponible (le gabarit existe) |
| **Pack autoportant ou vitrine** | `scene.json` et GeoJSON sur un hébergement https statique ; bientôt un fichier unique (`__ATLAS_SCENE__`) | Démo hors connexion au hub | Uniquement des couches `inline`, URL ou `xyz`. Données forcément publiques. | disponible (démos) ; fichier unique en cours |
| **Tableau de bord d'indicateurs** | ce n'est pas Atlas : les graphiques de Grist, ou l'assemblage `dashboard` (501) | — | — | hors Atlas |
| **Fiche territoriale A4, atlas multipage** | ce n'est pas Atlas : PDF via `map_composer` (atlas QGIS), `sheet_a4` (501) | — | Pièce figée, citable | hors Atlas |

Règle de choix, dans la lignée de `docs/reflexion-livrables-atlas.md` :

- **Figé, opposable, signé** : assemblage hub ou PDF.
- **Explorable et public** : scène Atlas.
- **Vivant, collaboratif, restreint ou saisi** : Atlas dans Grist.

---

## 5. Modes de diffusion

| Mode | Qui peut lire | Identité | Révocation | Usage recommandé |
|---|---|---|---|---|
| Hub `/published/{owner}/{kind}/{slug}` | selon l'audience : `public` anonyme ; `cerema_internal` toute identité hub ; `restricted` et `confidential` le propriétaire ou un superviseur | OIDC du hub (Keycloak SSPCloud) ou clé API | republier ou retirer ; `/p/{slug}` stable et versionné | livrable figé, signé (`audit_chain`) |
| iframe d'un composant `/published/.../component/...` | idem ; `frame-ancestors *` | idem, **mais pas de cookie en iframe tierce** | idem | intégration dans un site : en pratique **`public` seulement** |
| Atlas `?scene=` | quiconque peut lire l'URL de la scène | aucune | retirer la scène (le cache navigateur peut subsister) | scène publique, démo |
| Lien de document Grist | selon le partage et les ACL | compte de l'instance Grist | retirer le partage, changer une clé de lien | carte vivante, collaboration |
| `singlePage` / `embed=true` | idem ; `embed` force la lecture | idem | idem | intégration d'une page de document |
| Public Access et clés de lien | anonyme, avec une tranche par clé | clé portée dans l'URL | changer la clé, ou la valeur `UUID` de la ligne | diffusion ciblée sans compte |
| APK Atlas | titulaire d'une clé API | clé API sur l'appareil | révoquer la clé | terrain |
| Pack statique | quiconque | aucune | retirer l'hébergement | vitrine, archive |

**Deux domaines d'identité distincts.** Le hub s'authentifie par le
Keycloak SSPCloud ; Grist par le compte de son instance. Aucun pont n'existe
entre eux. Un livrable qui mêle les deux (un assemblage hub contenant une
`iframe_grist` privée) demande **deux connexions**, et la seconde échoue
souvent en iframe tierce. Il faut le dire à l'utilisateur plutôt que de
promettre.

---

## 6. La question : gérer l'accès aux données d'un projet « via Atlas » ?

### 6.1 Réponse

**Pas via Atlas, via Grist, et Atlas en est la vue.** Atlas refuse
explicitement d'avoir un système de droits : « 0 système de droits en plus
de celui de Grist ». Il ne pose pas d'ACL et ne connaît que le `userId`.
Hors document, il n'a aucune identité. Lui confier la gestion des accès
serait donc à contre-courant du produit.

En revanche, **le couple Grist et Atlas est pertinent** pour une partie des
besoins :

| Besoin | Grist et Atlas | Hub et S3 |
|---|---|---|
| Données à jour sans republier | **oui** (la carte suit la table) | non (instantané) |
| Droits fins : table, colonne, ligne | **oui** (Access Rules) | non (audience par objet) |
| Une tranche par destinataire sans compte | **oui** (clés de lien) | non |
| Saisie et relevé terrain, photos | **oui** | non |
| Livrable figé, versionné, signé et citable | faible | **oui** (`audit_chain`, `/p/{slug}/v{n}`) |
| Données lourdes : rasters, PMTiles, grands GeoPackage | **non** | **oui** |
| Accès réservé aux comptes SSPCloud | non (autre domaine d'identité) | **oui** (OIDC) |
| Traçabilité de qui a lu | limitée | refus journalisés par le hub |

### 6.2 Modèle d'accès recommandé (voie Grist)

Il reprend CADRAGE-PORTABLE-PARTAGE-IFRAME §4.3.

1. Le **document de projet** appartient au chargé d'étude (propriétaire),
   avec des éditeurs nommés.
2. Les **Access Rules sont en refus par défaut**. Chaque table, colonne ou
   ligne à diffuser est exposée explicitement. Les tables système d'Atlas
   (`Atlas_LayerPrefs`, `Atlas_Story`, `Maquette_Layers`, `SceneManifest`)
   sont en lecture pour les lecteurs, sans écriture.
3. Le **Public Access** reste désactivé, sauf besoin anonyme. Dans ce cas,
   il est en Viewer et **combiné aux clés de lien** : une clé par
   destinataire ou par périmètre.
4. Les **couches sensibles se protègent par leur table**. Une couche dont
   la table est refusée n'a simplement pas de données. Point connu : un
   récit partagé montre alors des étapes vides, ce que le lot 4 d'Atlas
   reste à détecter.

### 6.3 Qui gère quoi

| Acteur | Responsabilité |
|---|---|
| Chargé d'étude (propriétaire du document) | partage, Access Rules, clés de lien, révocation |
| Agent QGIS | produire le document et la scène (T6), **proposer** les règles et le texte de partage, **jamais** ouvrir un accès public ni poser des ACL sans confirmation explicite |
| Hub | publier les livrables figés, tenir le catalogue des liens (L2 « Livrables publiés »), ne pas relayer de lien Grist privé comme s'il était public |
| Atlas | afficher ce que Grist laisse passer ; basculer en lecture sur refus |
| Administrateur de l'instance Grist | politique CORS, comptes, quotas (hors de notre portée) |

### 6.4 Sécurité

- **Une clé de lien est un secret au porteur.** Elle voyage dans l'URL et
  peut fuiter par transfert ou par historique. Il faut une clé par
  destinataire, et la changer en cas de doute. Le hub pose
  `referrerpolicy="no-referrer"` sur ses iframes : garder ce réglage.
- **Une ACL mal écrite expose en silence.** L'agent ne doit pas écrire les
  règles lui-même dans `_grist_ACLRules`. Les poser recharge la page et
  change la gouvernance du document : c'est un geste du propriétaire.
- **Une scène externe n'est pas de confiance.** Atlas applique déjà cette
  règle. Côté hub, ne jamais publier en `public` une scène dont les couches
  sont restreintes : l'audience de la scène et celle des couches doivent
  concorder.
- **Le RGPD s'applique** : la règle actuelle reste valable,
  `cerema_internal` par défaut et `public` seulement sur confirmation
  explicite. Elle vaut aussi pour « rendre le document Grist public ».

### 6.5 Recommandation

Retenir une **voie hybride**, sans nouveau développement de droits :

- **Hub et S3** pour les livrables figés et les données lourdes, avec les
  audiences actuelles.
- **Grist et Atlas** pour la mise à disposition *vivante* des données d'un
  projet, avec les droits de Grist. C'est le livrable `.grist` de T6, que
  l'agent accompagne d'une **fiche de partage** : qui, quelle tranche,
  quelle clé, comment révoquer.
- **Ne pas** construire de gestion d'accès « Atlas » ni de pont
  d'identité SSPCloud et Grist dans ce lot.

---

## 7. Consignes : textes avant et après

Budget. Les essentiels (`_QGIS_ESSENTIALS`) passent de 6 919 à 6 902
jetons, pour un plafond de 7 000 (`context_budget.PLAFONDS["essentiels"]`,
tests `test_context_budget.py`). L'ajout porte sur le profil et les
descriptions d'outils, comme demandé.

### 7.1 Appliqué : prompt essentiel, section 2quinquies (`agent/agent/qgis_agent.py`)

Avant :

```
   `describe_entity_schema(..., use_case='<pattern>')`. Le composant
   `passerelle-geo-components@dev` de la lib carto commune ne rend
   correctement que les manifests V0.3.1 riches (contract SceneManifest
   V0.3.1 publie sur npm 2026-07-10).
```

Après :

```
   `describe_entity_schema(..., use_case='<pattern>')`. `runtime: 'atlas'`
   n'affiche Atlas que si `params.scene_url` est une scène https lisible
   sans connexion (livrable `public`) ; sinon le hub rend MapLibre.
```

Motif. La référence à geo-components 0.3.1 est périmée depuis l'arbitrage
du 23/08 : le contrat est celui d'Atlas, 0.2.2. La phrase nouvelle empêche
de promettre Atlas dans un livrable interne (E3).

### 7.2 Appliqué : descriptions de `publish_component` (`agent/agent/native_tools_v2.py`)

Registre `NATIVE_TOOLS`, avant :

```
"Use case : composant embarquable iframe par sites tiers "
"(Atlas widget Grist, sites CEREMA externes). Retourne URL "
"hub /published/{owner}/component/component-{cid}. "
"Audience cerema_internal default (anti-fuite RGPD)."
```

Après :

```
"Use case : composant embarquable en iframe (page tierce, "
"document Grist). Retourne URL "
"hub /published/{owner}/component/component-{cid}. "
"Audience cerema_internal default (anti-fuite RGPD) : "
"illisible hors session hub, seule `public` l'est."
```

Schéma d'outil, avant :

```
"(Atlas widget Grist, sites CEREMA, etc.). Retourne URL hub "
"/published/{owner}/component/component-{cid}. Audience "
"cerema_internal default - JAMAIS public par défaut (anti-RGPD)."
```

Après :

```
"Use case : composant embarquable en iframe (page tierce, "
"document Grist). Retourne URL hub "
"/published/{owner}/component/component-{cid}. Audience "
"cerema_internal default - JAMAIS public par défaut (anti-RGPD) ; "
"hors session hub, seule `public` s'affiche."
```

Les docstrings de `publish_component` et de `create_component` sont
alignées : le runtime `atlas` et sa condition sont ajoutés.

### 7.3 Appliqué : profil `hub/hub/profiles/storymap_creator_v15.yaml` (4 427 à 4 488 jetons, plafond 4 500)

- Dans la liste des runtimes valides (piège n° 2), `'atlas'` est ajouté.
  Il manquait, alors que tous les exemples canoniques l'emploient.
- Dans le catalogue cross-étude, « Atlas widget » est retiré de la liste
  des producteurs de composants : Atlas n'en publie pas.
- Un anti-pattern redondant est condensé. Avant :
  « **`publish_artifact(...)` ou `export_pdf(...)`** : ces tools legacy
  n'appartiennent PAS au workflow V1.5 (…). Utilise `publish_assembly` à la
  place. » Après : « `publish_artifact` / `export_pdf` : legacy, hors
  workflow V1.5. » L'interdit détaillé reste dans le bloc « PUBLICATION
  OBLIGATOIRE ».
- Un bloc est ajouté en fin de prompt :

```
  ─── SI L'USER VEUT ATLAS ───

  `runtime: 'atlas'` n'affiche Atlas que si `params.scene_url` est une
  scène https lisible sans connexion (livrable `public`). Sinon (données
  non publiques, saisie) : Atlas dans un document Grist, droits = ACL
  Grist ; donne le lien Grist (`iframe_grist` si accès public ou clé de
  lien). ≠ atlas QGIS (`map_composer`).
```

### 7.4 Appliqué : `docs/api/components-catalog.md` et `docs/api/blocknote-editor.md`

- **En-tête.** Atlas est retiré des consommateurs du catalogue. Un encadré
  est ajouté : « Atlas n'est pas un client de ce catalogue » (format,
  régimes, absence d'identité, sens de l'intégration).
- **« Use case 2 ».** L'ancien titre, « ZEBRA publie un composant pour
  Atlas widget Grist », décrivait les étapes « Atlas widget Grist GET
  /catalog/components » et « Atlas embarque iframe ». Il est réécrit en
  « ZEBRA publie un composant, réutilisé dans un livrable » : un auteur
  authentifié découvre le composant, puis le référence ou l'intègre en
  iframe, lisible hors session seulement en `public`. Une note précise
  qu'on donne à Atlas une scène, pas un composant.
- **Sécurité.** La ligne `frame-ancestors *` précise désormais que l'iframe
  n'ouvre pas les droits, et que seule l'audience `public` porte
  `Access-Control-Allow-Origin`.
- **`blocknote-editor.md`.** Ligne « Atlas widget Grist » : « (iframe
  `/published/...` inchangé) » devient « (Atlas lit des Scene Manifest, pas
  l'éditeur) ».

### 7.5 Proposé, non appliqué

Ces changements touchent du code ou des énoncés que des tests figent. Ils
sont à faire avec le lot E1 à E3.

1. **`schema_introspect.py:162`**, description de `atlas_immersive` :
   « Plein écran 3D Three.js — visite virtuelle » deviendrait « Plein écran
   — **non rendu (501)** ; pour une scène 3D plein cadre, publier un
   `interactive_map` runtime `atlas` en `public` ». Ce texte remonte à
   l'agent par `list_entity_kinds` et `describe_entity_schema`.
2. **Description OpenAI de `create_component`.** Une fois E1 et E2
   corrigés, ajouter : « `runtime: 'atlas'` : le hub publie la scène et
   monte Atlas si l'audience est `public` ». Aujourd'hui, ce serait faux.
3. **Commentaire `ATLAS_URL`** (E4) : « Valeur par défaut : la page publiée
   par Widgets-Grist. `ATLAS_URL=""` désactive la bascule. »
4. **Nouveau cas d'usage `describe_entity_schema`** `use_case='scene_publique_atlas'` :
   un exemple avec `classification: "public"`, `params.scene_url` et
   `params.visionneuse` (`no3d`, `models_base`). À faire après E1 à E3,
   pour ne pas enseigner un chemin qui échoue.
5. **Prompt de T6 (livrable `.grist`).** Ajouter la « fiche de partage »
   décrite au §6.5 à la réponse de l'agent. À arbitrer avec T6.

---

## 8. Croisements avec T6 et T7

- **T6 (livrables `.grist`).** La voie « Atlas dans Grist » (§4, lignes 3
  et 4) est *le* livrable `.grist` vu du côté de la carte. Ce document
  n'en décrit ni la production (`export_grist`, qgis2grist, table
  `SceneManifest`) ni le format. Il apporte à T6 :
  - le modèle d'accès (§6.2) ;
  - la répartition des rôles (§6.3) ;
  - la règle « pas d'ACL posée par l'agent ».
- **T7 (agents dédiés).** Un agent dédié à la « diffusion » ou au
  « partage » serait le bon porteur des consignes du §6, plutôt que le
  prompt global. À défaut, le bloc du profil `storymap_creator_v15` suffit.
  Il faut éviter de dupliquer ce bloc dans plusieurs profils : le mettre
  dans un seul, référencé.

---

## 9. Questions ouvertes

1. **E3 est-il un choix ?** Faut-il qu'un livrable `cerema_internal` puisse
   montrer Atlas ? Si oui, deux voies existent : servir Atlas depuis
   l'origine du hub (copie de `published/atlas/`, `ATLAS_URL` local), ou des
   URL signées. La première est plus simple. Elle est cohérente avec le
   fichier unique en cours côté Atlas (`__ATLAS_SCENE__`).
2. **Plusieurs récits par document** (lot 3 d'Atlas). Tant qu'il n'est pas
   fait, un document de projet ne porte qu'un récit : prévoir un document
   par récit diffusé, ou un récit dans la scène publiée.
3. **Liste d'origines admises pour `?scene=`.** C'est une question
   d'exploitant, posée par Atlas « le jour d'une mise en ligne ». Si le hub
   devient producteur régulier, il faut la trancher ensemble.
