# Banc d'évaluation comportementale (niveau C)

Outillage du niveau C de la stratégie qualité de l'agent
(`docs/superpowers/specs/2026-09-24-strategie-qualite-agent.md`, par. 4) :
rejouer des conversations contre le vrai modèle, sur une étude **bac à sable**,
N fois chacune, et mesurer un **taux de réussite** à partir de contrôles
déterministes : trajectoire d'outils, état QGIS final lu par le pont, réponse
(chiffres traçables, URL, jargon, clarification), coût.

Le banc ne juge pas encore la qualité de conversation par un modèle juge
(par. 4.2, point 4) : il fournit les trajectoires brutes pour le faire.

```
evals/
  run.py            point d'entrée : python -m evals.run
  clients.py        agent (POST /chat, SSE) et hub (étude, points de retour)
  sse.py            lecture du flux et reconstruction de la trajectoire
  pont.py           pont QGIS : HTTP direct ou commande fournie (kubectl exec)
  sonde.py          lecture de l'état QGIS (lecture seule) et préconditions
  garde_fou.py      refus de toute étude qui n'est pas un bac à sable
  scenario.py       format YAML et validation stricte
  evaluation.py     critères (fonctions pures)
  rapport.py        agrégation, rapport JSON et Markdown
  verifs/           vérificateurs purs, réutilisables en production (par. 5.2)
    chiffres.py     chiffres traçables
    urls.py         liste blanche d'URL
    texte.py        jargon, motifs, question de clarification
  scenarios/        S1 à S6 et six scénarios de conversation
  tests/            tests hors ligne et fixtures SSE fabriquées à la main
```

## 1. Converser avec l'agent

Constaté dans `agent/agent/main.py` et `agent/agent/qgis_agent.py` au
2026-09-24.

### Endpoint

`POST /chat`, corps **formulaire** (`application/x-www-form-urlencoded` ou
multipart), réponse `text/event-stream` :

| Champ | Obligatoire | Rôle |
|---|---|---|
| `message` | oui | le message de l'utilisateur |
| `session_id` | oui | identifiant de conversation ; l'historique (20 derniers messages) est relu à chaque tour |
| `profile_id` | non | profil ; l'étude active et le rendu actif priment, sauf `profile_locked=true` |
| `profile_locked` | non | `true` fige le profil du formulaire |

Le banc prend un UUID neuf par répétition, comme `chat.html`
(`crypto.randomUUID()`), donc un contexte « legacy » sans historique. Les
formes `study:{sid}...` changent le contexte ; `study:{sid}:recipe:{rid}`
court-circuite même le modèle (exécution déterministe de recette).

Autres routes utiles : `POST /chat/{session_id}/stop` (arrêt entre deux
outils), `GET /sessions/{session_id}/messages` (réponse persistée, sans les
résultats d'outils), `POST /sessions/{session_id}/tags` (le banc pose
`origine=banc_evaluation`).

### Authentification

- **Via le hub (recommandé)** : `https://<hub>/agent/chat`. Le hub valide la
  clé `qgis_...` de l'utilisateur (cookie `hub_api_key` ou `Authorization:
  Bearer`) puis relaie vers l'agent avec `Authorization: Bearer HUB_API_KEY`
  et `X-Hub-Proxy-User` (`hub/hub/main.py`, `hub_proxy_agent`). Le relais
  coupe à 600 s.
- **Direct sur l'agent** : `Authorization: Bearer <HUB_API_KEY>` et
  `X-Hub-Proxy-User: <ONYXIA_USER du pod>` (bloc « 3bis » du middleware).
  Option `--utilisateur`.

Le banc envoie le jeton dans les deux en-têtes (cookie et Bearer).

### Étude

L'agent travaille toujours dans l'**étude active** du propriétaire du pod
(`GET /studies/active` côté hub) ; la session y est rattachée au premier
message. Il n'y a pas de paramètre d'étude sur `/chat` : d'où le garde-fou
(par. 3).

### Événements SSE

Une ligne `data: <json>` par événement, suivie d'une ligne vide. Pas de
champ `event:`.

| Objet | Sens |
|---|---|
| `{"phase": "reflexion"}` | un appel au modèle commence (= une itération ; plafond 20) |
| `{"reasoning": "..."}` | réflexion du modèle (masquée par défaut dans le chat) |
| `{"phase": "redaction"}` | le modèle commence à écrire du texte visible |
| `{"text": "..."}` | morceau de Markdown : réponse **et** blocs d'outils |
| `{"phase": "outil", "outil": nom, "label": ...}` | un outil démarre |
| `{"phase": "relance", "label": ...}` | relance après un budget épuisé par la réflexion |
| `{"battement": true}` | toutes les 15 s de silence (outil long, modèle muet) |
| `{"error": ..., "error_class": ...}` | exception ; fin du flux |
| `{"done": true}` | fin normale |

Les appels d'outils **ne sont pas structurés** : après `phase: outil`,
l'agent émet du texte :

````
<!--ckpt:<id>-->                         (émis juste avant, si point de retour)
> **`smart_load`** — `id=bdtopo_batiments`, `name=bati_aix`
```
<300 premiers caractères du résultat>...
```
![capture](data:image/jpeg;base64,...)
(trombone) **Livrable publie** : [Voir le pdf](<hub_url>)   (publish_artifact seulement)
````

Chaque argument est coupé à 40 caractères, le résultat à 300. L'arrêt
automatique arrive dans ce même segment (« (pictogramme) **Boucle d'erreur
détectée** ... »). `evals/sse.py` reconstruit la trajectoire à partir de ces
segments ; sans aucun événement de phase (agent plus ancien), il découpe le
texte seul.

## 2. Lancer une évaluation

À fournir :

1. **Une étude bac à sable** dont le nom commence par `bac-a-sable`, de
   préférence créée hors de la liste de l'utilisateur :
   `POST /studies {"name": "bac-a-sable banc", "origin": "test"}` sur le hub.
2. **L'URL du hub** et **un jeton** `qgis_...` du propriétaire du pod,
   passé par la variable `EVALS_JETON` (il n'apparaît ni dans le rapport ni
   dans l'historique du shell).
3. **Un accès au pont QGIS** du workspace :
   - `--bridge http://localhost:8080` après un
     `kubectl port-forward <pod-workspace> 8080:8080` ;
   - ou `--bridge-cmd "<commande>"`, qui lit la requête JSON sur l'entrée
     standard et rend la réponse, par exemple
     `kubectl exec -i -n <ns> <pod-workspace> -- curl -s -X POST -H "Content-Type: application/json" --data-binary @- http://localhost:8080/api/command`
     (si `curl` manque dans le pod, un `python3 -c` équivalent convient).

```
export EVALS_JETON=qgis_...
python -m evals.run --scenarios evals/scenarios \
    --agent-url https://<hub>/agent --hub-url https://<hub> \
    --bridge http://localhost:8080 --study <sid> --repeat 3
```

Options : `--filtre '^S[1-4]-'`, `--activer-etude` (active le bac à sable
s'il ne l'est pas), `--duree-max-tour 900`, `--url-autorisee <entrée>`,
`--sortie <dossier>`, `--dry-run` (valide et liste les scénarios, sans
réseau).

Codes de sortie : `0` tous les scénarios au seuil, `1` au moins un sous son
seuil, `2` refus (garde-fou, option manquante, scénario invalide, hub
injoignable).

Déroulé : garde-fou ; point de retour initial ; pour chaque scénario et
chaque répétition : nouvelle vérification de l'étude active, restauration du
point de retour, préconditions, tours de conversation, lecture de l'état,
évaluation ; restauration finale.

## 3. Garde-fou bac à sable

Avant toute action, et **avant chaque répétition** (l'utilisateur peut
changer d'étude pendant le banc) :

- `--study` est obligatoire ;
- l'étude existe pour ce jeton, n'est pas archivée ;
- son nom normalisé (casse, accents, espaces et soulignés ignorés) commence
  par `bac-a-sable` ; ce préfixe n'est pas paramétrable ;
- c'est l'étude active ; le banc ne l'active que sur `--activer-etude`.

En cas de refus en cours de route, le banc s'arrête sans restaurer quoi que
ce soit sur l'étude devenue active. Une étude d'origine `user` passe, avec un
avertissement.

## 4. Réinitialisation entre répétitions

Le hub sait photographier et restaurer le projet QGIS de l'étude active
(`POST /sessions/{id}/checkpoint`, puis `/restore-checkpoint`) : le banc
pose un point de retour au début et le restaure avant chaque répétition et à
la fin. Les préconditions `vider_projet`, `zone_initiale`, `couches` et
`script` s'appliquent ensuite par le pont.

Ce que la restauration **ne remet pas à zéro** :

- les fichiers écrits dans l'étude (GeoPackage, exports PDF) et les
  livrables publiés ;
- la **mémoire utilisateur** (L3) : l'agent peut mémoriser des « insights »
  tirés des conversations du banc. Préférer un compte ou un pod dédié ;
- les sessions de chat, étiquetées `origine=banc_evaluation` pour pouvoir
  les retrouver et les purger.

Si le point de retour est refusé (QGIS occupé), le banc le dit et enchaîne
les répétitions sur l'état laissé par la précédente ; `vider_projet` limite
alors les dégâts.

## 5. Format de scénario

Un fichier YAML par scénario (ou `scenarios: [...]`). Validation stricte :
une clé inconnue est une erreur.

```yaml
id: S1-bati-aix                     # lettres, chiffres, - _ .
famille: fidelite_territoriale      # voir FAMILLES dans scenario.py (par. 4.3)
titre: ...
description: ...
source: incident, fiche d'observation...
repetitions: 5                      # sinon --repeat, sinon 3
seuil_reussite: 1.0                 # défaut 1.0 en fidélité territoriale, 0.9 sinon
preconditions:
  vider_projet: true                # retire couches et zone d'étude
  zone_initiale: {cible: Le Lavandou}   # action set_study_zone du pont
  couches: [{nom: ..., uri: ..., fournisseur: ogr}]
  script: "..."                     # execute_python libre, relu
tours:
  - message: Charge les bâtis sur Aix-en-Provence
    attentes:                       # facultatif : trajectoire, reponse, cout de CE tour
      trajectoire: {...}
attentes:
  trajectoire:                      # sur l'ensemble des tours
    outils_attendus:                # chaque élément doit être appelé au moins une fois
      - set_study_zone
      - un_de: [smart_load, add_from_catalog]
      - {outil: run_processing, si_arguments: "(?i)clip"}   # regex sur l'aperçu des arguments
    outils_interdits: [...]         # mêmes motifs
    ordre:                          # première occurrence de A avant celle de B
      - [set_study_zone, {un_de: [smart_load, add_from_catalog]}]
    aucun_outil: false
    outils_mutateurs_interdits: false   # voir OUTILS_MUTATEURS (modele.py)
    max_appels: 10
    max_erreurs_outils: 2
    max_execute_python: 3
  etat:                             # lu par la sonde, jamais par le modèle
    zone:
      definie: true
      nom_contient: Aix             # ou liste
      nom_ne_contient_pas: Lavandou
      bbox_dans: [xmin, ymin, xmax, ymax]   # EPSG:4326
      bbox_contient_point: [5.4474, 43.5298]
      tolerance_km: 0.5
    couches:
      - motif: "b[aâ]ti"            # regex sur le nom ; la meilleure candidate est retenue
        presente: true              # false : aucune couche ne doit correspondre
        nombre_entites: {min: 5000, max: 250000}
        emprise_dans: [..] | zone   # emprise contenue, avec tolerance_km (défaut 0.5)
        crs: [EPSG:2154]
        hors_zone_max: 0            # entités hors zone comptées par la sonde
        reference_hors_zone: contour   # ou bbox (zone d'étude)
        contour_motif: "commune|contour"
        persistante: true           # pas une couche en mémoire
        valide: true
        type: vecteur
    nombre_couches_max: 10
    noms_interdits: ["_temp$"]
  reponse:                          # dernier tour ; les trois premières clés valent pour tous
    chiffres_tracables: true        # défaut true
    urls_autorisees: true           # défaut true
    urls_tracees: false             # l'URL doit figurer dans un résultat d'outil
    urls_min: 1
    mots_interdits: [smart_load, bbox]
    doit_contenir: ["(?i)commune"]
    ne_doit_pas_contenir: ["(?i)minio"]
    clarification_attendue: true    # question en fin de réponse, aucun outil modifiant ; false : pas de question
    longueur_min: 0
    longueur_max: 1200
    chiffre_egal_compte: {motif: "b[aâ]ti", tolerance_rel: 0}   # la réponse cite le compte réel
  cout:
    iterations_max: 8               # par tour
    duree_max_s: 600                # total
    arret_auto_interdit: true       # défauts true
    erreur_flux_interdite: true
    fin_de_flux_requise: true
```

Une exécution réussit si **tous** ses critères passent. Une exécution que le
banc n'a pas pu jouer (pont ou hub en panne) est **invalide** : comptée à
part, hors taux.

## 6. Vérificateurs

### Chiffres traçables (`verifs/chiffres.py`)

Chaque nombre de la réponse doit se retrouver dans une source : résultats
d'outils de la conversation jusqu'au tour, messages de l'utilisateur, et,
pour le dernier tour, l'état lu par la sonde.

1. Tracé d'abord : un nombre tracé est accepté, même s'il ressemble à une
   année.
2. Formats français : espaces, insécables et fines comme séparateurs de
   milliers (`51 450 444`), virgule décimale, `1.234.567`, `51_450_444`,
   suffixes `k`, `millions`, `milliards`.
3. Arrondi : la précision affichée fixe la tolérance (`5,27` trace
   5.2734 ; `51,4 millions` ne trace **pas** 51 450 444, qui s'arrondit à
   51,5). Un nombre rond d'au moins 1 000 vaut arrondi (`300 000` trace
   300 551).
4. Approximation annoncée (« environ », « près de », « plus de », « ~ ») :
   5 % de tolérance.
5. Unités converties : km², ha contre m² ; km contre m ; pourcentage contre
   fraction. Un pourcentage égal à 100 × a / b de deux sources est `derive`.
6. Ignorés s'ils ne sont pas tracés : petits entiers ≤ 10 (hors %), années
   1900-2100 sans séparateur, ordinaux et arrondissements (`4e`, `1er`),
   codes postaux et INSEE, numéros annoncés (« étape », « n° »),
   identifiants collés (`S1`, `qwen3`, `3D`), puces, dates, heures,
   `EPSG:2154`, `Lambert 93`, `WGS 84`, contenu des liens, URL et code.

Limites : pas de reconstitution des sommes ou différences ; `2024` sans
séparateur et absent des sources passe pour une année.

Le module est pur (bibliothèque standard) : en production (par. 5.2),
`verifier_chiffres(reponse, resultats_outils_du_tour)` peut tourner avant
l'affichage, sur les résultats **complets**.

### URL (`verifs/urls.py`)

Autorisées : l'URL du hub (`--hub-url`), les hôtes du catalogue
(`CATALOGUE_PAR_DEFAUT` : data.geopf.fr, geoservices.ign.fr, geo.api.gouv.fr,
...) et `--url-autorisee`. Une entrée est un hôte (sous-domaines compris) ou
une URL avec préfixe de chemin. Refusées : MinIO/S3 (même sur un hôte
autorisé), `undefined`, liens relatifs, schémas non web. Avec
`urls_tracees`, une URL autorisée mais absente des résultats d'outils est
`inventee`.

## 7. Rapport

Dans `--sortie` (défaut `evals/resultats/<horodatage>/`, ignoré par git) :

- `rapport.md` : synthèse, taux par catégorie de critère, tableau par
  scénario (taux, seuil, itérations et durée médianes, erreurs d'outils,
  arrêts automatiques), critères en échec avec le premier détail ;
- `rapport.json` : même contenu, plus chaque exécution et chaque critère ;
- `brut/<scenario>/r<rep>-t<tour>.jsonl` : les événements SSE bruts, pour
  rejouer l'analyse ou alimenter un modèle juge.

## 8. Limites connues

- **Résultats d'outils tronqués à 300 caractères** dans le flux : un chiffre
  juste peut sortir « non tracé » s'il se trouve plus loin dans le
  résultat. L'état lu par la sonde, ajouté comme source au dernier tour,
  couvre les comptes et emprises. Le journal de tour rejouable (par. 5.1)
  lèverait la limite.
- **Arguments tronqués à 40 caractères** : `si_arguments` ne voit que ce
  début (suffisant pour `target`, `id`, `algorithm`).
- Pas de tokens mesurés : le flux ne les transporte pas. Coût = itérations,
  durée, erreurs d'outils.
- `OUTILS_MUTATEURS` est une copie de `_MUTATING_TOOLS` de l'agent, élargie
  aux exports ; un test signale toute divergence.
- La sonde lit au plus 300 000 entités par couche inspectée (`tronque`
  dans le détail) et ne voit que le projet QGIS ouvert.
- La réinitialisation ne couvre ni les fichiers, ni les livrables, ni la
  mémoire utilisateur (par. 4).
- Bornes géographiques des scénarios (Aix, 4e arrondissement de Marseille)
  posées avec une marge ; à resserrer après un premier passage réel, comme
  les seuils (par. 4.4 : « à calibrer au premier passage »).

## 9. Tests

```
python -m pytest -q evals
```

Hors ligne : lecture SSE sur fixtures fabriquées à la main (dont l'incident
S1 du 2026-09-24), vérificateurs, évaluation de trajectoires simulées en
réussite et en échec, garde-fou (aucun appel à l'agent ni au pont quand il
refuse), clients et pont avec doublures. Dépendances : `pyyaml`, `pytest`
(`evals/requirements.txt`).
