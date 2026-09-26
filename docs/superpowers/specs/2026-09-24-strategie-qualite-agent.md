# Stratégie qualité de l'agent QGIS

Date : 2026-09-24.
Portée : agent (`agent/`), hub (`hub/`), workspace MCP (`BigQgisMCP`).
Modèle cible : `qwen3-6-35b-moe`.

## 0. Ce qu'on garantit

On traite de la donnée dans un cadre professionnel, pour des utilisateurs
presque grand public. L'ordre de priorité ne se négocie pas :

1. **Aucune donnée fausse présentée comme vraie.** Un chiffre, une emprise, une
   couche livrée doivent être exacts, ou explicitement qualifiés.
2. **Aucune action non tracée.** Tout ce que l'agent fait doit pouvoir être
   relu et rejoué.
3. **Une conversation juste.** L'agent comprend la demande, pose une question
   quand c'est ambigu, parle simplement, dit ce qu'il a fait et ce qu'il ne
   sait pas.
4. **L'efficacité.** Peu d'itérations, peu de tokens, pas de boucle d'erreurs.

Principe directeur, repris du plan « comportements spatiaux » :
**outil fiable > harness cadré > prompt**. Un défaut qui revient se corrige dans
l'outil ou le harness. Ajouter une phrase au prompt est le dernier recours.

## 1. Quatre niveaux de contrôle

| Niveau | Ce qu'il vérifie | Déterministe | Où il tourne |
|---|---|---|---|
| A. Outils | Chaque outil respecte son contrat de donnée | oui | CI + conteneur QGIS |
| B. Harness | Le contexte donné au LLM est juste, borné et à jour | oui | CI |
| C. Comportement | Le vrai LLM, en scénario, fait ce qu'il faut | non (taux) | nightly + avant release |
| D. Production | Ce qui se passe réellement chez les utilisateurs | observation | continu |

Aujourd'hui, seul le niveau A existe, en partie, et seul `hub/tests` tourne en
CI. Les niveaux B à D sont à construire.

## 2. Niveau A — Outils : des scripts éprouvés, des postconditions

### 2.1 Contrat de donnée de chaque outil qui modifie l'état

Tout outil qui crée ou modifie une couche renvoie un bloc `verification`
calculé par l'outil lui-même, jamais par le LLM :

- `feature_count` : compte réel de la couche locale (pas le `numberMatched`
  d'un service distant) ;
- `emprise_4326` : l'emprise réelle, avec son rapport à la zone d'étude ;
- `crs`, `geometries_invalides` (compte), `provenance` (source catalogue,
  date, filtre appliqué) ;
- `origine` : fichier, mémoire ou service distant ;
- `avertissement` : présent si une postcondition échoue (emprise aberrante,
  zéro entité, couche en mémoire non sauvée, CRS inattendu).

`smart_load` et `add_from_catalog` le font depuis le 2026-09-24
(QgisRemoteMCP#4). Il faut l'étendre à `run_processing`, `run_recipe`,
`execute_python` (couches créées pendant le script), `export_layer` et
`publish_artifact`.

### 2.2 Tests

- **Tests de contrat en conteneur** (marqueur `container`, déjà prévu dans
  `pytest.ini` de BigQgisMCP) : pour chaque outil, un jeu d'entrées de
  référence et des assertions sur la donnée produite. Exemples : bâti d'Aix,
  compte dans une fourchette et emprise dans la zone ; clip au contour, plus
  aucune entité hors commune ; reprojection, CRS attendu.
- **Données de référence figées** : un petit GeoPackage par cas (commune,
  quelques centaines d'entités), pour ne pas dépendre du réseau IGN en CI.
  Tests réseau réels en nightly seulement.
- **Non-régression par incident** : chaque incident mesuré en production
  devient un test (modèle : `test_catalogue_wfs_borne.py`, qui fige l'incident
  des 51 450 444 entités).
- **Recettes** : un schéma JSON versionné, validé en CI, pour les 6 recettes
  du workspace (aujourd'hui une seule porte `recipe_schema_version`). Chaque
  recette est exécutée en conteneur sur une zone de référence, avec des
  postconditions sur ses sorties.

### 2.3 Outils de haut niveau plutôt que du code écrit par le LLM

Chaque `execute_python` écrit par le LLM est une source d'erreurs et de
boucles. Les besoins récurrents deviennent des outils testés, par exemple :
`clip_to_study_zone(layer, nom)`, `count_in_zone(layer)`,
`load_commune_contour(nom)`. Mesure de suivi : la part des tours où l'agent a
recours à `execute_python` pour une tâche qu'un outil couvre (voir §5).

## 3. Niveau B — Harness : le contexte idéal, mesuré

Pour un modèle de 35B MoE, chaque token de contexte inutile coûte en qualité.
Le harness se teste comme du code.

### 3.1 Budget de contexte mesuré et plafonné

Constat : le prompt statique fait 25 à 45 000 caractères (environ 7 à 12 000
tokens) avant les schémas d'outils. Aucun budget n'existe. Les résultats
d'outils ne sont pas tronqués.

À mettre en place :

- **Comptage par section** à chaque tour (IDENTITY, GLOBAL_RULES, CONTEXT L2,
  mémoire L3, schémas d'outils, historique, résultats d'outils), journalisé
  (§5.1).
- **Plafonds par section**, avec un test qui échoue au dépassement. Exemple de
  cible, à calibrer : système et outils ≤ 14 000 tokens ; L2 ≤ 1 500 ; mémoire
  L3 ≤ 1 200 ; chaque résultat d'outil ≤ 2 000.
- **Troncature des résultats d'outils** avec un pointeur : garder le bloc
  `verification` et les champs utiles, remplacer le reste par « N lignes
  omises, relire avec get_features(…) ». C'est la lacune la plus urgente du
  harness.
- **Schémas d'outils filtrés par phase et par profil** : un utilisateur en
  cartographie n'a pas besoin des 46 outils. Chaque schéma exposé coûte du
  contexte et ajoute une occasion de se tromper d'outil.

### 3.2 Tests « instantané de contexte »

Pour une dizaine de situations types, on assemble le contexte complet sans
appeler le LLM, puis on vérifie son contenu et sa taille :

| Situation | Doit contenir | Ne doit pas contenir |
|---|---|---|
| Étude vide, pas de zone | invitation à définir la zone | liste de couches fantôme |
| Zone Aix active, 3 couches | zone, bbox, couches avec compte | l'ancienne zone |
| Changement de zone en cours de conversation | la nouvelle zone seule | l'ancienne bbox dans les hints |
| Étude avec livrables publiés | 5 livrables récents, URL hub | URL MinIO |
| Projet annexe en focus | son résumé et son état | le détail des autres projets |
| Documents d'étude attachés | extraits pertinents seulement | documents entiers |
| Mémoire utilisateur longue | la mémoire bornée | plus que le plafond L3 |
| Profil restreint | les outils de la liste blanche | les autres outils |

Ces tests protègent aussi contre la dérive : aujourd'hui, le paramètre de
contexte G9 n'est pas passé (`qgis_agent.py:2322`), si bien que la vue L2
propre à chaque portée ne sert jamais. Un instantané l'aurait montré.

### 3.3 Hints dynamiques : un contrat, pas du texte libre

Le `_context` renvoyé par les outils est aujourd'hui une phrase générique en
anglais. Il devient un contrat testé :

- en français ;
- **calculé à partir de l'état réel** : zone active, compte réel, rapport
  emprise/zone, prochaine étape (« découper avant de compter », « couche en
  mémoire : exporter pour la garder ») ;
- **court** : une ligne d'état, une ligne de suite ;
- la liste des couches est plafonnée, comme la L2 (15 couches).

Tests : pour chaque état de projet type, le hint attendu.

### 3.4 Garde-fous de la boucle

Ils existent : 20 itérations au plus, arrêt automatique à la troisième erreur
identique, alerte si le pont est tombé, retour au checkpoint sur Arrêter. Il
manque des tests qui les déclenchent avec un LLM simulé : erreur répétée,
panne du pont, arrêt pendant un outil qui modifie l'état.

### 3.5 Mémoire structurante et bornée

- L3 plafonnée en tokens, triée par pertinence pour la demande en cours.
- L'extracteur d'insights (aujourd'hui sur `gemma4-26b-moe`) est testé sur
  des conversations de référence : qu'extrait-il, avec quelle confiance, et
  refuse-t-il bien les faits non stables.
- Test d'isolation : rien de la mémoire d'un utilisateur ou d'une étude ne
  fuit dans une autre.

### 3.6 Alignement du code déployé

Constat du 2026-09-24 : l'agent et le hub importent leur code depuis des
overlays posés sur le PVC (`PYTHONPATH`). Fusionner dans `main` et
reconstruire l'image ne met donc rien à jour. Tant que ce mode existe, un
contrôle compare au démarrage l'empreinte de l'overlay à celle de l'image, et
l'affiche dans `/health`. Cible : supprimer les overlays et revenir à l'image
seule.

## 4. Niveau C — Comportement : un banc d'évaluation avec le vrai modèle

### 4.1 Principe

Un **banc de scénarios** rejoue des conversations contre le vrai
`qwen3-6-35b-moe`, sur un workspace bac à sable (étude dédiée, jamais une
étude utilisateur). Chaque scénario est joué N fois (3 en nightly, 5 avant
release), car le modèle n'est pas déterministe. On mesure un **taux de
réussite**, pas un succès unique.

### 4.2 Ce que chaque scénario vérifie

1. **Trajectoire** : outils attendus, outils interdits (par exemple WFS écrit
   à la main, `add_from_catalog` pour du vecteur), ordre (zone avant
   chargement).
2. **État final QGIS**, lu par le bridge et non par le LLM : couches créées,
   compte, emprise dans la zone, clip effectué, nommage, sauvegarde.
3. **Réponse** :
   - **chiffres traçables** : chaque nombre de la réponse figure dans un
     résultat d'outil du tour. C'est un contrôle déterministe, qui peut aussi
     tourner en production (§5.2) ;
   - pas d'URL inventée, pas d'URL MinIO ;
   - la réponse dit ce qui a été fait et ses limites (bbox au lieu du contour,
     par exemple).
4. **Qualité de conversation**, notée par un modèle juge plus fort, hors
   ligne, sur une grille fixe : compréhension, clarté pour un non-spécialiste,
   question de clarification si c'est ambigu, honnêteté, concision.
5. **Coût** : itérations, tokens, durée, nombre d'erreurs d'outils.

### 4.3 Familles de scénarios (à constituer, environ 40 au départ)

| Famille | Exemples | Critère d'échec typique |
|---|---|---|
| Fidélité territoriale | S1 à S6 du plan « comportements spatiaux » | données hors zone, chiffre sur la bbox présenté comme communal |
| Conversation grand public | « bonjour », « c'est quoi une bbox ? », demande floue, hors sujet | jargon, action lancée sans comprendre, pas de question de clarification |
| Ambiguïté | « Saint-Martin » (plusieurs communes), « Marseille » avec un arrondissement actif | choix silencieux de la mauvaise commune |
| Contexte d'étude | « comme la dernière fois », « le projet voisin », « le livrable d'hier » | ignore le contexte, recharge tout |
| Livrables | carte PDF, storymap, republication | URL non hub, republication en double, livrable non publié |
| Mémoire | « retiens que je travaille en Lambert 93 », rappel au tour suivant | oubli, ou mémorisation d'un fait non stable |
| Reprise d'erreur | service IGN lent, zéro entité, script en échec | boucle, contournement par du code à la main, « c'est fait » alors que non |
| Arrêt et rollback | l'utilisateur arrête pendant un chargement | état incohérent, couche orpheline |
| Multi-tours | référence à « cette couche », changement d'avis | mauvaise couche visée |
| Refus justifié | données personnelles, action destructive sans confirmation | exécution sans demander |

Chaque incident observé en production devient un scénario (§5.3).

### 4.4 Seuils de passage (à calibrer au premier passage)

- Fidélité territoriale et chiffres traçables : **100 %** sur les cas de
  référence. Une donnée fausse bloque la release.
- Trajectoire correcte : ≥ 90 %.
- Grille de conversation : moyenne ≥ 4 sur 5, aucun scénario sous 3.
- Coût : médiane des itérations ≤ 6, aucun arrêt automatique sur les cas
  nominaux.

## 5. Niveau D — Production : tracer, mesurer, boucler

### 5.1 Journal de tour rejouable

Constat : on ne peut pas rejouer une conversation. Les résultats d'outils sont
coupés à 200 caractères, et le prompt système n'est pas conservé.

Pour chaque tour, conserver :
- l'empreinte du prompt système et les tailles par section ;
- le modèle et ses paramètres ;
- les tokens entrée et sortie par appel ;
- les arguments et résultats **complets** de chaque outil (stockage à part,
  la troncature ne s'appliquant qu'à ce qui est envoyé au LLM) ;
- les durées, les erreurs, les déclenchements de garde-fous ;
- l'état QGIS avant et après (les checkpoints existent déjà).

Ce journal permet de rejouer un tour réel dans le banc (§4) en changeant une
seule variable : prompt, outil ou modèle.

### 5.2 Contrôles en ligne

Avant d'afficher une réponse :
- **vérificateur de chiffres** : si un nombre n'est pas traçable, la réponse
  est annotée, ou l'agent est relancé une fois ;
- **vérificateur d'URL** : seules les URL du hub et du catalogue passent.

### 5.3 Indicateurs et boucle de retour

Tableau de bord hebdomadaire :
- taux d'arrêt automatique, de boucles d'erreur, d'itérations maximales ;
- erreurs par outil ;
- part d'`execute_python` écrit à la main ;
- taux d'avertissements de donnée ignorés (un avertissement suivi d'un
  chiffre dans la réponse) ;
- tokens par tour (médiane et p95) ;
- signalements des utilisateurs.

Boucle : incident → fiche d'observation → correction dans l'outil ou le
harness → test de non-régression (A ou B) → scénario (C).

## 6. Où tourne quoi

| Moment | Contenu | Bloquant |
|---|---|---|
| Chaque PR | tests unitaires et de source des trois dépôts, instantanés de contexte, budget de tokens, schéma des recettes | oui |
| Nightly | tests de contrat en conteneur, réseau IGN réel, banc C réduit (N=3) | alerte |
| Avant release | banc C complet (N=5), seuils §4.4 | oui |
| Au déploiement | empreinte du code chargé = image publiée (§3.6), `/health` | oui |

Aujourd'hui, la CI de `qgis-sspcloud` ne lance que `hub/tests`, et celle de
BigQgisMCP ne lance pas son pytest. C'est le premier point à corriger, et il
ne coûte presque rien.

## 7. Priorités

**P0 (fiabilité de la donnée, cette semaine)**
1. `agent/tests` et `BigQgisMCP/tests` en CI.
2. Troncature des résultats d'outils avec pointeur (harness).
3. Bloc `verification` étendu à `run_processing`, `run_recipe` et aux couches
   créées par `execute_python`.
4. Outil `clip_to_study_zone`, avec son test de contrat.
5. Valider S1 à S4 en live sur le code à jour (fiches du plan).

**P1 (contexte et traçabilité)**
6. Comptage de tokens par section, plafonds, tests d'instantané de contexte.
7. Journal de tour rejouable.
8. Hints `_context` en français, calculés, testés ; `tools/list` dynamique
   selon la zone (la façade prévue).
9. Vérificateur de chiffres et d'URL en ligne.

**P2 (banc et pilotage)**
10. Banc de scénarios C : les 10 premiers scénarios, puis 40.
11. Grille de conversation et modèle juge.
12. Tableau de bord hebdomadaire, suppression des overlays.
13. Intégration du contexte documentaire et des projets annexes, avec leurs
    instantanés dès l'introduction.
