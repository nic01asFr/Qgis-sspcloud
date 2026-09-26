# Mesure de référence du comportement de l'agent (avant déploiement qa/integration)

Date : 2026-09-26, 03:30-04:00 UTC.
Version en ligne : QgisRemoteMCP c276f29 (add_from_catalog borné), agent et hub
alignés sur Qgis-sspcloud a0936e8 (overlays resynchronisés le 2026-09-24).
Modèle : qwen3-6-35b-moe. Profil : standard (89 outils exposés).
Étude : « bac-a-sable banc » (2c00a975de5e), créée pour l'occasion.
Méthode : desk piloté dans Chrome comme un utilisateur ; trajectoire lue dans
les journaux de l'agent ; état réel lu dans QGIS par le bridge
(`/api/command`), jamais d'après la réponse du modèle.

## Synthèse

| Scénario | Verdict | Donnée | Coût | Défauts principaux |
|---|---|---|---|---|
| S1 bâti Aix | réussi | 112 816 (bbox), exact | 3 appels LLM | emoji, « clipper », projet non sauvé |
| S2 périmètre communal | chiffre juste, parcours raté | 54 556 annoncé ; contrôle 54 557 (intersection) / 54 545 (centroïde) | 15 appels, ~3 min, 3 erreurs PyQGIS | affirmation fausse, couche en mémoire, jargon visible |
| S4 « combien à Aix ? » | **échec** | 112 816 annoncé (bbox) au lieu de ~54 550 | 2 appels | mauvaise couche choisie, chiffre trompeur pour un non-spécialiste |
| S3 routes Lavandou | zone juste, zèle coûteux | 5 048 (bbox), exact | 8 appels, boucle d'erreur | style non demandé, **2 plantages de QGIS**, affirmation invérifiée |
| Grand public « c'est quoi une bbox » | ton juste | — | 1 appel | **chiffre inventé** (« écart < 5 % », mesuré +107 %) |

## Fiches

### S1 — « Charge les bâtis sur Aix-en-Provence »
- Outils : set_study_zone(Aix) → list_datasources(batiment) → smart_load(bdtopo_batiments).
- État : GeoPackage d'étude `batiment_f4f6a6da.gpkg`, 112 816 entités, EPSG:2154,
  emprise [5.265, 43.441, 5.514, 43.631] (bbox d'Aix, léger débord attendu).
- Réponse : chiffre exact, nuance bbox / contour dite, découpage proposé.
- Défauts : « ⚠️ », « clipper » ; projet `isDirty` après le tour ; rappel mémoire :
  4 messages injectés (sim. 0,77), vraisemblablement tirés des S1 ratés de
  l'étude Saint-Martin — fuite possible entre études.

### S2 — « Oui, uniquement le bâti dans le périmètre communal d'Aix, et combien »
- Outils : execute_python ×2, execute_async, poll_job, get_project_info,
  execute_python ×2, list_datasources ×2, smart_load(bdtopo_communes),
  execute_python ×3 (native:clip écrit à la main, nettoyage).
- Erreurs PyQGIS : type de champ (`QgsField.String`), nom de couche, inversion
  lon/lat du contour geo.api.gouv.
- État : `buildings_aix_exact`, **couche mémoire**, 54 556 entités ;
  la couche bbox est **toujours présente**.
- Contrôle indépendant (contour INSEE 13001, 187,6 km²) : 54 557 bâtiments
  intersectent, 54 545 ont leur centroïde dedans. Chiffre juste à 0,02 %.
- Défauts : « La couche large bbox (112k) a été remplacée » est **faux** ;
  messages intermédiaires en jargon visibles même « Détails techniques »
  masqué ; le bandeau « Rédaction de la réponse… · 170 s » reste affiché
  après la fin du tour.
- Ce que qa/integration change : clip_to_study_zone (contour mémorisé,
  GeoPackage d'étude, verification avant/après).

### S4 — « Combien de bâtiments y a-t-il à Aix-en-Provence ? » (conversation neuve)
- Outils : execute_python (featureCount de la première couche nommée
  « Bâtiments (BD TOPO) »).
- Réponse : « 112 816 bâtiments dans l'emprise d'Aix-en-Provence ».
- Échec : la couche découpée existait ; rien dans le contexte ne dit quelle
  couche porte le chiffre communal validé. Le mot « emprise » ne protège pas
  un utilisateur grand public, qui retient un chiffre deux fois trop grand.
- Correctifs attendus : fiche d'étude (couches validées, chiffres vérifiés et
  leur portée) ; règle « pour une commune, compter la couche découpée, sinon
  proposer le découpage » ; vérificateur de chiffres.

### S3 — « Charge les routes au Lavandou » (conversation neuve, zone Aix active)
- Outils : set_study_zone(Le Lavandou) → list_datasources(route) →
  smart_load(bdtopo_routes) → set_layer_style (échec) → execute_python ×4
  (style catégorisé ; détection de boucle d'erreur « cannot import name »).
- Zone : correctement redéfinie (critère S3 rempli).
- **QGIS a planté deux fois (signal 11) à 03:46 et 03:47**, relancé par
  supervisord ; le projet a été rechargé depuis son `.qgz`.
- Conséquence : `buildings_aix_exact` est revenue **vide (0 entité)** — couche
  au nom trompeur, donnée perdue (T5 réalisé).
- Réponse : affirme « chargées et stylisées » alors que le style a échoué puis
  QGIS a planté ; expose la mécanique (« 2 échecs d'import… set_layer_style ») ;
  emojis de couleur.
- Correctifs attendus : ne pas agir hors demande (style non demandé) ;
  set_layer_style à réparer (le modèle retombe sur du PyQGIS qui fait planter
  QGIS) ; résultats toujours persistés ; une couche mémoire vide après
  rechargement doit être signalée ou retirée.

### Grand public — « Bonjour, je débute. C'est quoi une bbox… ? »
- Aucun outil. Explication claire, adaptée, propose une démonstration.
- **Chiffre inventé** : « l'écart est souvent négligeable, < 5 % » (mesuré à
  Aix : +107 %). Cas type du vérificateur de chiffres traçables.

## Défauts d'infrastructure relevés pendant la mesure
1. Bridge : un résultat contenant `NaN` (emprise d'une couche vide) fait
   planter la sérialisation JSON de l'API (`ValueError: Out of range float
   values are not JSON compliant: nan`) → HTTP 500 au lieu d'une réponse.
   Le bloc `verification` calcule des emprises : à neutraliser (NaN → null).
2. QGIS segfault (signal 11) pendant du code de style écrit par le modèle.
3. Le chiffre d'itérations et la durée ne sont visibles que dans les journaux.

## À rejouer après déploiement de qa/integration
Mêmes messages, même étude remise à zéro, 3 répétitions chacun (banc `evals/`),
plus S5, S6 et les scénarios grand public du banc. Critères : S4 doit donner le
chiffre communal ou proposer le découpage ; S2 en ≤ 5 appels via
clip_to_study_zone avec un GeoPackage persistant ; aucun chiffre non traçable.

## Après déploiement du lot qualité 1 (2026-09-26, 05:05-05:30 UTC)

Version : Qgis-sspcloud 9161da1 (agent et hub, overlays alignés : `/health`
`aligne: true`), QgisRemoteMCP 1434783. Même étude remise à zéro, mêmes
messages, un passage chacun (le banc répété reste à faire).

| Scénario | Avant | Après |
|---|---|---|
| S1 bâti Aix | réussi, 3 appels | réussi, 3 appels ; contour mémorisé (WKT 34 488 car.) ; plus d'emoji |
| S2 périmètre communal | 15 appels, 3 erreurs PyQGIS, couche mémoire, affirmation fausse | **3 appels** (get_project_info → clip_to_study_zone), 54 557 = contrôle indépendant, GeoPackage d'étude persistant — mais **1 tour perdu** avant (voir D1) |
| S4 « combien à Aix ? » | **échec** (112 816) | **réussi** : 54 557 et explication bbox/commune |
| S3 routes Lavandou | style non demandé, boucle, 2 plantages QGIS | 3 appels, rien d'hors demande, aucun plantage |
| Grand public bbox | chiffre inventé (« < 5 % ») | clair, aucun chiffre inventé |

Budget mesuré en production (nouvelle ligne de journal) : ~29 000 jetons par
appel au modèle, dont **19 253 pour les 90 schémas d'outils**.

### Défauts restants, par priorité
- **D1 — appel d'outil écrit en texte.** Au premier message de S2, le modèle a
  rédigé `> **\`get_project_info\`** — …` au lieu d'émettre un appel : tour
  terminé sans action, l'utilisateur voit « Rédaction de la réponse… 5 s »
  figé, sans message. Cause : l'historique renvoyé au modèle contient les
  tours précédents tels que l'interface les rend (appels d'outils en
  Markdown) ; le modèle imite ce format. Correctifs : historique propre pour
  le modèle (texte final seul, ou vrais messages tool) ; détecter un appel
  écrit en texte et relancer une fois ; message clair si le tour finit vide.
- **D2 — champ `rapport_emprise_zone` mal lu.** Deux fois interprété comme
  « rectangle / commune » (« rapport 1.1 », « 19× plus grande que la
  commune »), alors qu'il compare l'emprise chargée au rectangle de la zone.
  Correctif : renommer, et ajouter le rapport à la surface du contour.
- **D3 — L2 sans identifiant de couche** : clip_to_study_zone exige
  `layer_id`, d'où un get_project_info systématique. Donner l'id dans L2, ou
  accepter le nom de couche.
- **D4 — schémas d'outils = 66 % du contexte.** Filtrage par phase (lot L3
  de la spec sous-agents).
- **D5 — surface utilisateur** : noms d'outils cités (`clip_to_study_zone`),
  « clip/clippe », emoji ⚠️ résiduel ; bandeau de statut qui reste affiché.
- **D6 — rappel mémoire** : 4 messages injectés à chaque tour (sim. 0,68 à
  0,82), y compris d'anciennes conversations : à borner par étude.
- **D7 — vérificateur de chiffres** absent en production (le « < 5 % »
  inventé a disparu ici par chance, pas par construction).
