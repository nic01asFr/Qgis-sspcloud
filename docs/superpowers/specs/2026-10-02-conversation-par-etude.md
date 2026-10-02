# La conversation suit l'étude active (2026-10-02)

## Constats

1. Après passage de l'étude « data gp2oa » à « bac-a-sable banc » dans le
   bureau, le chat a rouvert une conversation de l'étude « Diagnostic
   Saint-Privat (Rousset) ». L'historique mélangeait les études : risque
   d'agir sur la mauvaise étude depuis une conversation qui n'est pas la
   sienne.
2. QGIS occupé par un long calcul : « Ouvrir » une étude a renvoyé
   `activate_failed`, mais le hub avait enregistré l'étude comme active.
   QGIS gardait l'ancien projet ; redémarré, il a rouvert l'ancienne étude.
   Hub et QGIS désaccordés, sans signal.

Causes probables : (1) l'ancienne règle « rattacher la conversation tant
qu'elle est orpheline » rattachait une conversation rouverte à l'étude
active *du moment* ; l'historique et la reprise comptaient aussi les
exécutions de recette et les conversations du banc d'évaluation. (2) le hub
écrivait l'étude active en base avant de charger le projet dans QGIS et
ignorait l'échec du chargement ; le formulaire concluait à l'échec à 60 s
pendant que l'activation continuait.

## Règles — conversation (agent, `agent/agent/memory.py`)

- **R1** Une conversation appartient à une étude, fixée à sa création
  (`sessions.study_id`), avec le projet actif d'alors (`sessions.project_id`,
  informatif : pas de cloisonnement par projet). Jamais réécrit, sauf R5.
- **R2** « Non rattachée » (`study_id` NULL) : lisible, pas continuable tant
  qu'une étude est active.
- **R3** `POST /chat` refuse (409, rien n'est écrit) un message dans une
  conversation du chat qui n'appartient pas à l'étude active. Hub injoignable
  ou aucune étude : pas de garde (rien à comparer).
- **R4** Historique et reprise : seulement les conversations du chat (UUID ou
  `study:{sid}`), sans exécutions de recette, tiroirs d'assistance ni banc
  d'évaluation (tag `origine=banc_evaluation`) ; la plus récemment active
  d'abord.
- **R5** Une conversation dont le seul tour a créé ou ouvert une étude
  (`study_create`, `study_switch`) suit cette étude.

Conversation d'une autre étude ouverte depuis l'historique : **lecture
seule** (bandeau, saisie bloquée, bouton « Revenir à l'étude active »).
Choisi plutôt que « basculer l'étude active » : basculer recharge le projet
QGIS (sauvegarde, chargement, possible attente d'un calcul) pour un simple
clic de consultation ; la lecture seule ne change rien et ne peut rien
casser. Pour poursuivre, on ouvre l'étude depuis le bureau.

## Migration douce (au démarrage de l'agent)

1. Une seule fois : une conversation UUID rattachée plus de 5 min après son
   premier message l'a été par l'ancienne règle ; elle redevient « non
   rattachée », l'ancienne valeur gardée dans le tag `rattachement_tardif`
   (réversible).
2. À chaque démarrage : conversation sans étude → étude déduite du tag `sid`
   ou de l'identifiant structuré (`study:{sid}…`, `assist:{sid}…`) ; sinon
   « non rattachée ».

## Bascule et contrat bureau ↔ chat

API agent : `GET /conversations[?toutes=1]` (historique, nom d'étude par
conversation), `GET /conversations/{id}/portee` (étude, lecture seule),
`POST /conversations/suivre` `{session_id, apres_tour, etude_connue}` →
`{decision: inchangee|suit|reprise|nouvelle, session_id, etude}` — jamais une
conversation d'une autre étude que l'étude active.

postMessage, même origine, fenêtre parente uniquement :

| Sens | Message | Quand |
|---|---|---|
| bureau → chat | `{type: 'desk_etude_active', etude: {id, nom}}` | chargement de l'iframe ; étude changée vue par le bureau |
| chat → bureau | `{type: 'chat_etude_suivie', etude, decision, session_id}` | le chat a suivi l'étude ; si elle diffère de celle du bureau, celui-ci propose de se recharger |

Le chat suit aussi l'étude seul : fin de tour (l'assistant a pu changer
d'étude, ou le serveur a refusé l'envoi), retour sur l'onglet (page « Agent
IA seul » comprise). Message discret : « Étude : X — conversation reprise /
nouvelle / la conversation suit l'étude ». Le changement d'étude par le menu
du bureau recharge la page : le chat rouvre la dernière conversation de
l'étude.

## Règles — activation d'étude (hub, `hub/hub/activation_etude.py`)

- **A1** Workspace prêt : QGIS charge le projet d'abord ; l'étude active
  n'est écrite en base que si QGIS l'a chargé (`STUDY_STAMP sid=… pid=…`, sans
  erreur de chargement). S'applique à `/studies/{sid}/activate`,
  `/studies/{sid}/projects/{pid}/activate` et à l'outil `study_switch`.
- **A2** QGIS ne répond pas (sonde de 8 s) : l'étude ne change pas, 409 et
  activation en attente ; le bureau l'affiche (« QGIS est occupé par un
  calcul : l'étude X sera ouverte dès qu'il sera libre », bouton « Annuler
  l'ouverture ») et la retente toutes les 15 s. Chargement en échec : 409
  sans attente.
- **A3** Workspace endormi : flux inchangé (le réveil recharge l'étude).
- **A4** Au chargement du bureau (`POST /desk/coherence-etude`) : étude du
  projet ouvert dans QGIS (variables `hub_sid`/`hub_pid`) contre l'étude
  active du hub. Désaccord : le projet ouvert est enregistré dans sa propre
  étude, puis l'étude active est rechargée dans QGIS. Exceptions : projet non
  marqué, et étude ouverte par une session MCP externe (désaccord voulu).

Hors périmètre : annuler le calcul en cours depuis le bureau (l'utilisateur
peut annuler l'ouverture en attente) ; `_ensure_active_study_for_agent`
(bascule par `X-Session-Id`) garde son flux.

## Tests

`agent/tests/test_conversation_par_etude.py`,
`hub/tests/test_activation_etude_atomique.py`.
