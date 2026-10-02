# Traitements en arrière-plan, avec bascule proposée

Date : 2026-10-02. Équipe E2. Branches `feat/arriere-plan` (qgis-sspcloud et
QgisRemoteMCP / BigQgisMCP).

## Constat

- Live du 2026-10-02 : un `execute_python` de densité a tenu le tour et le chat
  plus de 12 minutes (« Calcul en cours dans QGIS… 12 min 17 s »).
- `execute_async`, `poll_job` et `cancel_job` existaient, mais seulement dans le
  paquet d'outils « traitement_long » (sur mots-clés). Même quand ils étaient
  utilisés, l'agent restait dans le tour à interroger `poll_job`.
- `execute_async` ne savait soumettre qu'une **action du pont** : `run_recipe`
  (plusieurs actions enchaînées), `smart_load` ou les exports, qui ajoutent leur
  logique autour du pont, n'avaient pas d'équivalent asynchrone.
- Après le calcul de 12 minutes, la réponse MCP n'a pas pu être lue (« MCP JSON
  parse fail -> retry 1/3 », puis 2/3). Les relances automatiques de l'agent
  **réexécutaient** l'outil : un calcul long, ou un script qui modifie des
  données, pouvait s'appliquer trois fois.

## Décisions produit

1. Les outils potentiellement longs partent en tâche de fond **par défaut**,
   sous le capot : le modèle appelle `execute_python` comme avant et reçoit le
   même résultat. L'agent attend jusqu'à un seuil (45 s, réglable).
2. Au-delà du seuil, le chat propose : « Le calcul prend plus de temps que
   prévu (déjà X min). [Continuer en arrière-plan] [Attendre] [Annuler] ». Sans
   réponse, bascule automatique après 2 min (réglable). La bascule termine le
   tour proprement, sur un message fixe ; la conversation est libre.
3. Lancement direct en arrière-plan quand c'est évident : `run_recipe`, ou un
   traitement déclaré lourd (argument `timeout` d'au moins 300 s sur
   `execute_python` / `run_processing`).
4. Le hub tient le registre des tâches (persistant, SQLite sur son volume),
   l'expose (lister, état, annuler, relancer) et surveille les battements.
5. À la fin d'une tâche passée en arrière-plan, l'agent rédige un court compte
   rendu (chiffres issus du résultat seulement), ajouté à la conversation
   d'origine. Chat ouvert : il apparaît en direct ; sinon au prochain affichage.
   Pastille « N calculs en cours » dans la barre d'état du bureau, notification
   discrète à la fin.
6. QGIS fait une chose à la fois : pendant une tâche, toute autre action sur la
   carte est refusée avec une consigne (« je le ferai dès que le calcul en cours
   sera terminé »). Jamais d'appel concurrent.
7. Accessibilité : `aria-live`, boutons au clavier, pas de vol du focus, textes
   en français courant.

## Outils concernés (mesure dans le code)

| Outil | Source de la mesure |
|---|---|
| `execute_python` | QgisRemoteMCP `_LONG_TIMEOUT_ACTIONS` ; agent 900 s |
| `run_processing` | porte native:difference, extractbylocation sur 50 000+ entités |
| `run_recipe` | 1 200 s par étape côté serveur ; **lancement direct** |
| `smart_load`, `add_from_catalog`, `clip_to_study_zone` | `SOCKET_TIMEOUT_LONG` (300 s) |
| `export_flood_map`, `export_web_map`, `export_temporal_map`, `export_qfield`, `export_grist` | `SOCKET_TIMEOUT_LONG` |

`export_layer` et `export_pdf` restent en direct (délai serveur de 60 s).
Liste : `agent/agent/arriere_plan.py`, `OUTILS_LONGS`.

## Architecture

```
chat.html ──SSE /chat──> agent (chat_stream)
   │                      │  execute_async(tool, arguments, client_id) ──> hub /mcp ──> workspace
   │                      │  poll_job(job_id) toutes les 2 s           ──> hub /mcp ──> workspace
   │                      │  POST/PATCH /taches                       ──> hub (registre SQLite)
   │ POST /chat/taches/{id}/decision ──> agent (attente en mémoire)
   │ GET  /taches?session_id=   ──> agent ──> hub /taches
   │
desk.html ── GET /desk/taches, POST /desk/taches/{id}/annuler|relancer ──> hub

hub (boucle toutes les 5 s) : poll_job sur le workspace pour chaque tâche active
   └─ fin d'une tâche « arriere_plan » ──> POST agent /internal/taches/{id}/rattacher
        └─ l'agent rédige le compte rendu, l'ajoute à la conversation, marque « rattachée »
```

### Workspace (QgisRemoteMCP)

`execute_async` accepte désormais `tool` (nom d'un outil MCP), `arguments` et
`client_id` :

- l'outil s'exécute en arrière-plan **tel quel** ; à la fin, `poll_job` rend le
  bloc d'état puis le contenu exact de l'outil (texte, capture) ;
- une seule tâche à la fois (fil principal de QGIS) ; les suivantes attendent
  dans l'ordre (`queued`, `queue_position`) ;
- **soumission idempotente** : un `client_id` déjà vu rend la tâche existante ;
- battement réel : `heartbeat_age_s` reste proche de 0 tant que le fil vit ;
- annulation : une tâche en attente est annulée ; une tâche commencée répond
  `already_dispatched_cannot_cancel` (QGIS ne sait pas interrompre un script) ;
- identifiants préfixés `t-` ; les anciens appels (`code`, `action`) gardent
  leur contrat. Outils refusés : `execute_async`, `poll_job`, `cancel_job`,
  `qgis_desktop_ui`, `restart_qgis_engine`.

Code : `src/taches_outils.py`, branchement dans `main_mcp.py`.

### Relance automatique (correctif du 2026-10-02)

`qgis_agent._appel_mcp_jsonrpc` ne rejoue une réponse ambiguë (HTTP 502/503/504,
corps vide, JSON illisible) que si `arriere_plan.relance_permise(outil, args)` :

- lecture seule (`get_*`, `list_*`, `search_*`, `poll_job`…) ou idempotent
  (`set_study_zone`, `zoom_to`, `set_layer_style`, `set_layer_visibility`,
  `cancel_job`) ;
- `execute_async` portant un `client_id` (idempotent côté serveur).

Tout autre outil (calcul, chargement, export, publication, ajout ou retrait de
couche…) n'est **jamais** rejoué : le modèle reçoit « L'outil a pu s'exécuter
quand même : vérifie l'état du projet avant de le relancer ». Une connexion
refusée (`ConnectError`) se rejoue toujours : rien n'est parti.

Avec l'exécution asynchrone, une coupure pendant le suivi se traduit par une
nouvelle interrogation de `poll_job`, jamais par une nouvelle soumission
(test `test_une_coupure_du_suivi_reprend_le_suivi_sans_resoumettre`).

## États d'une tâche (registre du hub)

| État | Sens | Suite |
|---|---|---|
| `en_attente` | soumise, attend son tour dans QGIS | `en_cours`, `annulee` |
| `en_cours` | QGIS calcule | `terminee`, `echouee`, `interrompue`, `annulee` |
| `terminee` | fin normale, résultat disponible | compte rendu rattaché |
| `echouee` | l'outil a rendu une erreur | message fixe rattaché |
| `annulee` | annulée en attente, ou annulée commencée puis finie (résultat ignoré) | — |
| `interrompue` | workspace ne connaît plus la tâche, battement arrêté > 120 s, statut `dropped`/`bridge_unreachable`, ou workspace muet > 180 s | relancer ou abandonner |

`qt_frozen` n'interrompt pas : c'est l'état normal pendant un script long, qui
tient le fil principal. Seul un battement arrêté trahit une perte.

Mode : `tour` (un tour du chat suit la tâche et consommera son résultat) ou
`arriere_plan` (le hub rattachera la fin). Une tâche en mode `tour` dont le tour
ne donne plus signe de vie depuis 180 s (`vu_at`, rafraîchi toutes les 15 s)
passe en `arriere_plan`.

Document : `id` (`tf-…`), `username`, `job_id`, `outil`, `arguments`,
`relancable`, `libelle`, `session_id` (conversation d'origine), `sid` (étude),
`statut`, `mode`, `cree_at`, `maj_at`, `vu_at`, `battement_at`, `fini_at`,
`resultat` (texte sans images, 20 000 caractères au plus), `erreur`, `raison`,
`etat_qgis`, `annulation_demandee`, `rattachee`, `rattachee_at`, `message`,
`tentatives`, `avis_tentatives`.

## Événements SSE nouveaux (POST /chat)

Tous portent `tache` (type), `tache_id` et `libelle`.

| `tache` | Champs | Quand |
|---|---|---|
| `soumise` | — | outil long soumis au workspace |
| `progression` | `ecoule_s`, `statut` | à chaque suivi (2 s) |
| `proposition` | `ecoule_s`, `bascule_auto_s`, `choix: ["arriere_plan","attendre","annuler"]` | seuil dépassé |
| `attente` | — | l'utilisateur a choisi « Attendre » (plus de bascule automatique) |
| `arriere_plan` | `automatique`, `direct` | bascule ; le tour se termine juste après |
| `annulee` | `commence` | annulation (commencée : QGIS finira, résultat ignoré) |
| `fin_attente` | — | fini après une proposition |
| `interrompue` | `raison` | tâche perdue pendant le tour |

Les événements existants (`phase`, `text`, `battement`, `done`…) sont inchangés.

## API

### Agent

| Méthode | Route | Corps / réponse |
|---|---|---|
| POST | `/chat/taches/{id}/decision` | `{"choix": "arriere_plan"\|"attendre"\|"annuler"}` → 200 ; 404 si aucun tour n'attend ; 400 choix inconnu |
| GET | `/taches` | relais de `GET /taches` du hub (`session_id`, `etude`, `actives`, `limite`) |
| POST | `/taches/{id}/annuler`, `/taches/{id}/relancer` | relais du hub |
| POST | `/internal/taches/{id}/rattacher` | inter-pod (Bearer `HUB_API_KEY`) ; idempotent ; `{"ok", "message"}` |

### Hub

| Méthode | Route | Remarque |
|---|---|---|
| POST | `/taches` | inscription par l'agent (201) |
| GET | `/taches` | `?etude=&session_id=&actives=1&limite=` → `{"taches", "actives", "maintenant"}` |
| GET | `/taches/{id}` | document complet (résultat compris) |
| PATCH | `/taches/{id}` | champs permis : `statut, mode, resultat, erreur, raison, rattachee, message, rattachee_at, fini_at, vu_at, annulation_demandee, job_id` |
| POST | `/taches/{id}/annuler` | en attente → `annulee` ; commencée → `annulation_demandee` (reste active) ; interrompue → `annulee` |
| POST | `/taches/{id}/relancer` | interrompue ou échouée → nouvelle soumission (`client_id` dérivé), `en_attente`, 409 sinon |
| GET | `/desk/taches` ; POST `/desk/taches/{id}/annuler`, `/relancer` | bureau (propriétaire du pod) |

`/taches` est joignable en inter-pod (`_OIDC_MIDDLEWARE_INTER_POD`). Une clé
scopée (agent partagé) reçoit 403. Au proxy `/mcp`, `execute_async(tool=X)` est
refusé si `X` est hors du périmètre de la clé.

## Séquences

### Fini avant le seuil

1. Le modèle appelle `execute_python`.
2. L'agent soumet `execute_async(tool, arguments, client_id)`, inscrit la tâche
   (`mode=tour`), émet `soumise`.
3. `poll_job` toutes les 2 s ; `done` avant 45 s.
4. Le registre passe à `terminee`, `rattachee=true` ; le modèle reçoit le
   contenu de l'outil, identique à l'appel direct ; le tour continue.

### Seuil dépassé

1. Comme ci-dessus jusqu'à 45 s ; l'agent émet `proposition`.
2. Le chat affiche la question et trois boutons ; l'annonce passe par la
   région `role=status`.
3. Choix :
   - **Continuer en arrière-plan**, ou rien pendant 2 min, ou « Arrêter » :
     `mode=arriere_plan`, événement `arriere_plan`, message fixe (« … continue en
     arrière-plan. Vous pouvez continuer à discuter… Les actions sur la carte
     attendront la fin du calcul »), fin du tour sans nouvel appel au modèle,
     sans capture ni sauvegarde (QGIS occupé) ;
   - **Attendre** : plus de bascule automatique ; le tour attend la fin ;
   - **Annuler** : `cancel_job`. En attente : `annulee`. Commencée : le calcul se
     termine dans QGIS (il occupe toujours le moteur), son résultat sera ignoré
     et un court message le dira.

### Fin en arrière-plan

1. Le hub interroge `poll_job` toutes les 5 s ; `done` → `terminee`, résultat
   enregistré (texte, sans images).
2. Le hub appelle l'agent `/internal/taches/{id}/rattacher` (retenté à chaque passe,
   pendant 30 minutes, si l'agent ne répond pas).
3. L'agent rédige 2 à 4 phrases (modèle sans raisonnement). Si un nombre du
   texte n'apparaît pas dans le résultat, le texte est écarté au profit d'un
   message fixe construit à partir du résultat (`verification.count`,
   `feature_count`, couche…). Échec, annulation, interruption : message fixe.
4. Le message (préfixé « **Calcul en arrière-plan** · ») est ajouté à la
   conversation d'origine avec un mémo d'action ; le registre passe à
   `rattachee=true`. L'agent complète l'historique du projet et sauvegarde
   l'étude (ce que le tour n'a pas pu faire pendant le calcul).
5. Chat ouvert sur cette conversation : le bandeau interroge `/taches` (8 s si
   une tâche est active, 30 s sinon) et affiche le message en direct, jamais au
   milieu d'un tour. Sinon il apparaît au prochain affichage (il est dans
   l'historique). Le bureau notifie « Calcul terminé : … ».

### QGIS occupé

1. Une tâche est active. Au début du tour, le modèle reçoit une consigne : un
   calcul tourne, n'appelle aucun outil sur la carte.
2. La lecture de l'état du projet (`get_project_info`) est sautée pour ne pas
   attendre QGIS.
3. Si le modèle appelle malgré tout un outil QGIS, il n'est pas lancé : le
   résultat `{"qgis_occupe": true, "consigne": "… dis que tu le feras dès que
   le calcul en cours sera terminé"}` lui est rendu. Les outils hors QGIS
   (mémoire, documents, recettes de l'étude, `poll_job`, `cancel_job`) restent
   permis.

### Tâche perdue

Workspace redémarré (`Unknown job_id`), battement arrêté, workspace muet : le
hub passe la tâche à `interrompue` avec une raison en clair, l'agent rattache
un message fixe, le chat et le bureau proposent **Relancer** (même outil, mêmes
arguments, nouveau `client_id`) ou **Abandonner**.

## Réglages (agent)

| Variable | Défaut | Rôle |
|---|---|---|
| `AGENT_ARRIERE_PLAN` | `1` | `0` : tout en direct (retour arrière) |
| `AGENT_SEUIL_ATTENTE_S` | `45` | seuil avant la proposition |
| `AGENT_BASCULE_AUTO_S` | `120` | délai de bascule automatique |
| `AGENT_PERIODE_SUIVI_S` | `2` | période de `poll_job` dans le tour |
| `AGENT_BATTEMENT_PERDU_S` | `120` | battement au-delà duquel la tâche est perdue |

Côté hub : `taches_fond.BATTEMENT_PERDU_S` (120), `INJOIGNABLE_PERDU_S` (180),
`TOUR_ABANDONNE_S` (180), `MAX_AVIS` (360 passes, soit 30 min).

## Coordination

- E3 (conversation par étude) : le code du chat est isolé dans
  `<script id="taches-fond">` et `<style id="taches-fond-style">`. Seul point
  d'accroche dans le flux du tour : `if (data.tache) { window.TachesFond.evenement(…) }`.
  Il lit `#session-id`, ne touche ni à la session ni à l'historique. Le compte
  rendu est écrit dans la conversation **d'origine** (`session_id` de la tâche).
- E1 (prompt, `paquets_outils.py`) : aucun changement. La consigne « QGIS
  occupé » est un message de consigne ajouté au tour (pas le prompt système),
  seulement quand une tâche est active.
- Suites de tests existantes : `agent/tests/conftest.py` pose
  `AGENT_ARRIERE_PLAN=0` par défaut (elles simulent les outils par l'appel
  direct) ; `test_arriere_plan.py` l'active.

## Risques et points à valider en live

- Changement d'étude pendant un calcul : la bascule d'étude du hub exécute du
  Python sur le workspace, qui attendra la fin du calcul. À observer ; un
  avertissement dans le sélecteur d'étude serait utile.
- `poll_job` passe par le proxy `/mcp` du hub (résolution de l'étude active à
  chaque appel) : coût à mesurer sur un calcul de 10 minutes (300 appels).
- Le délai de chaque outil reste celui du workspace (`timeout` de
  `execute_python`, 60 s par défaut) : un script long doit toujours demander
  un délai suffisant.
- La capture automatique de `execute_python` est prise à la fin de la tâche ;
  elle ne figure pas dans le compte rendu (texte seulement).
- Un workspace ancien (sans `tool` dans `execute_async`) est détecté : l'agent
  repasse en appel direct, sans relance.
