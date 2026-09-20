# Changelog qgis-sspcloud

Versions du chart Helm `qgis-hub` (publié via GitHub Actions dans
[`helm-repo/`](helm-repo/)) et jalons majeurs du service.

## 1.5.0 · 2026-09-20

**Un tour ne se fige plus à vie.** Le modèle pouvait cesser de répondre au
milieu d'une réponse sans que rien ne le signale : la lecture du flux se
réarmait au moindre octet, y compris sur les battements de maintien. Deux
tours sur cinq restaient bloqués sur « Analyse en cours… », indéfiniment.
Une garde de silence (`LLM_SILENCE_MAX_S`, 90 s par défaut) les interrompt
désormais en le disant.

**Une conversation longue reste utilisable.** La fenêtre du modèle fait
131 072 jetons ; une session de production en occupait 168 500 — au-delà,
elle était définitivement cassée, sans message. L'historique est maintenant
compacté : les images encodées sont retirées, les messages trop longs
tronqués tête et queue, et le tout borné. La même session retombe à 53 500.

**L'utilisateur voit qu'une mise à jour existe, et l'applique.** Les trois
briques évoluent chacune de leur côté ; rien ne le disait. Un bandeau
compare les empreintes réellement tirées à celles publiées au registre, et
propose le redémarrage — sans perte : `imagePullPolicy: Always` sur un tag
mobile, et le PVC n'est pas touché. Il veille toutes les dix minutes et au
retour sur l'onglet, plus seulement au chargement.

**Les accès au stockage se renouvellent depuis le bureau.** Ils durent sept
jours et personne ne les renouvelait : passé ce délai le catalogue se vide,
les livrables disparaissent, les scènes publiées répondent 503. Le message
d'erreur envoyait relancer `install.sh` — une impasse, le script recopie des
identifiants déjà morts. Le bandeau annonce désormais l'expiration et
accepte ce que la page d'Onyxia affiche : on copie le bloc entier, quel que
soit l'onglet (shell, Python, mc, fichier de configuration), on le colle.

**Une ville entière se charge.** Quatre plafonds de délai se masquaient l'un
l'autre et toute opération longue échouait à 30 s. Marseille tient
maintenant : 300 551 bâtiments en 204 s. Un téléchargement vide ne passe
plus pour un succès.

**L'agent distingue les fichiers des couches.** Chaque couche dit son
origine — fichier de l'étude, service distant, ou **mémoire**. Une couche en
mémoire n'existe sur aucun disque et disparaît au redémarrage de QGIS ; sans
cette distinction, un résultat volatile passait pour un acquis.

**Les liens de livrable s'ouvrent.** Ils pointaient vers `localhost`,
inatteignable depuis le poste de l'utilisatrice qui les recevait. Les
exports sont aussi rangés dans l'étude au lieu de rester au vol.

**Le téléchargement d'une étude emporte ses données.** « Projet + données »
devient l'action principale : un `.qgz` seul ne contient aucune couche, et
s'ouvrait donc vide chez qui le recevait.

**Autres** — recherche du catalogue insensible aux accents (47 sources
annoncées = 47 réelles) ; une erreur publique reste lisible depuis une autre
origine (en-tête CORS sur les réponses d'erreur) ; l'indexation sémantique ne
se bloque plus sur un item refusé ; le bureau QGIS noVNC n'est plus exposé
sans authentification ; les prompts de profil atteignent enfin le modèle.

## 1.4.0 · 2026-09-05

**Le service dit ce qui tourne.** `GET /version` — public, sans
authentification — rend le commit d'où vient l'image du hub, la version du
chart qui a posé le déploiement, et les **empreintes réellement en cours**
des trois images, relevées auprès de Kubernetes.

Jusqu'ici rien ne le disait. `OPS.md` demandait pourtant, à l'étape 4 d'une
mise à jour, de « vérifier que /version retourne le nouveau commit » : la
route n'existait pas, et deux autres endpoints annoncés publics non plus.
Une vérification qu'on ne peut pas faire ne rate jamais — personne ne s'en
était aperçu.

L'endpoint rend l'empreinte **tirée** (`imageID`), pas le tag demandé : un
tag mobile peut désigner autre chose que ce que le nœud a en cache. Et ce
qu'il ignore, il le nomme — commit absent si l'image a été construite sans
`GIT_SHA`, workspace `null` avec la mention « en veille » quand le pod est à
zéro réplique.

**Installer sur une instance préexistante ne bloque plus.** `install.sh`
rattache à la release les ressources qui existent sans propriétaire, et
diagnostique les deux échecs rencontrés en conditions réelles : conflit de
propriété d'objet, et conflit de propriété de **champs** — celui-là n'est
pas détecté par `--dry-run=server`, qui valide l'objet et non ses champs.

**Une image non vérifiée n'atteint plus les pods.** Côté `BigQgisMCP`, le
catalogue est contrôlé avant la construction, l'image publiée sur le seul
tag immuable, contrôlée à nouveau, et les tags mobiles posés seulement
ensuite. Auparavant les trois tags partaient d'un coup et le contrôle venait
après : le garde-fou constatait sans empêcher.

**Contract `component` 0.3** — `auto` ne veut plus dire « le hub décide
selon la taille » mais « la meilleure forme disponible ». Le code faisait
déjà cela ; c'est le contrat qui décrivait autre chose, et deux tests le
certifiaient conforme parce que le stockage était en panne. 0.1 et 0.2
restent servis.

**Tuiles reproductibles** — PMTiles compressait ses métadonnées avec
l'horodatage courant, donc `sha256(pmtiles_bytes)` nommait du contenu-plus-
heure : republier une couche inchangée créait une adresse neuve et un objet
orphelin. Deux octets, corrigés.

**Aussi** — la page des publications distingue « aucune » de « illisible » ;
le compteur de livrables dénombre ce qu'il affiche ; les dates du catalogue,
qui sont des epochs, ne font plus tomber la page en 500.

## 1.3.0 · 2026-08-22

**Une commande, plus aucun jeton à retrouver.** Le parcours d'installation
ne dépend plus de `kubectl` ni d'un token OIDC.

**Clé d'accès visible** — `install.sh` génère (ou relit) la clé et la passe
en value `security.apiKey`. Elle apparaît dans Onyxia > Mes services et
dans les notes d'installation, comme le mot de passe du chart Jupyter
officiel. Le `lookup` sur les Secrets disparaît du chemin nominal : il
faisait échouer l'installation depuis un Jupyter au rôle par défaut.

**Service visible dans Onyxia** — `install.sh` crée le Secret de
métadonnées `sh.onyxia.release.v1.qgis-hub` (clés `owner`, `friendlyName`,
`catalog`, `share`). Sans lui, une release installée en ligne de commande
restait absente de l'interface, même si `helm list` la voyait.

**Clé LLM reprise du profil et conservée** — trois défauts corrigés :
1. le placeholder `{{userProfileValues.aiAssistant.apiKey}}` n'existe pas ;
   les formats réels sont `user.profile.aiAssistant.*` (alimenté) et
   `{{ai.activeProvider.*}}` (repli). Les deux sont gérés ;
2. `install.sh` lit le profil dans Vault
   (`onyxia-kv/{user}/.onyxia/userProfileStr`), que la ligne de commande
   n'interrogeait pas ;
3. la clé n'était écrite qu'en RAM du pod agent : nouveau Secret
   `qgis-llm-apikey` + `secretKeyRef optional`, elle survit aux
   redémarrages et aux `helm upgrade`.

Les valeurs du profil sont désormais nettoyées (`trim`) — un modèle saisi
avec des espaces de bord était rejeté par l'API LLM.

**Config MCP réparée** — `POST /auth/apikey` renvoyait `hub_url: ""` et un
`claude_config` avec `"url": "/mcp"`, inutilisable par Claude Desktop.
L'endpoint lisait `os.getenv("HUB_URL")` alors que le chart n'injectait
cette variable que dans le pod agent (récidive du travers interdit par
l'invariant n°3). Le chart l'injecte maintenant dans les deux pods.

**Lien « Ouvrir » d'Onyxia** — il pointait vers le chat de l'agent.
Onyxia prend le premier Ingress du manifeste sans le trier, et Helm suit
l'ordre alphabétique des templates. Renommés en `ingress-1-hub.yaml` et
`ingress-2-agent.yaml`. Au passage, le repli de hostname de l'agent
produisait un hôte invalide (`…sspcloud.fr-agent`) : centralisé dans le
helper `qgis-hub.agentHostname`.

**Alerte clé LLM sur `/workspace`** — le bureau la signalait déjà, mais la
page d'atterrissage restait muette alors que le bloc est replié.

## 1.2.5 · 2026-08-06

**Fix WebSocket noVNC** — endpoint `/workspace/vnc/websockify` accepte
cookie `hub_api_key` (Phase 2-1 canonique) au lieu d'exiger `oidc_token`.
Sans ce fix, iframe QGIS Desktop restait "Something went wrong,
connection is closed".

Commits : `44be082`, `da37a6a`.

## 1.2.4 · 2026-08-06

**Fix RBAC pod hub** — la SA custom `qgis-hub` n'avait pas les droits
kubectl (user OIDC SSPCloud ne peut pas créer de RoleBinding).
`install.sh` détecte maintenant `KUBERNETES_SERVICE_ACCOUNT` du pod
jupyter courant (qui a `edit` provisionné par Onyxia) et l'injecte dans
`values.serviceAccount.name`. Sans ce fix : hub `kubectl get sts`
échouait Forbidden → status "sleeping" permanent → bouton "Réveiller le
bureau" inopérant.

Commits : `76b1291`, `6801f0e`.

## 1.2.3 · 2026-08-06

Tentative erronée (SA `default` sans droits `edit`). Voir 1.2.4 pour fix
correct.

## 1.2.2 · 2026-08-05

**Fix UX bandeau LLM** — bandeau agent "clé LLM manquante" dans iframe
chat pointait vers `datalab.sspcloud.fr/account > AI Assistant`
(procédure obsolète, portail retiré). Maintenant pointe vers
`/workspace` bloc "🤖 Clé LLM (agent IA)" (form Phase 1.7-C).

Commits : `19a1a27`, `8e0b64c`.

## 1.2.1 · 2026-08-05

**Fix redirect racine** — `/` sans cookie redirect vers `/onboarding`
(token OIDC obscur) au lieu de `/login` (form clé API que l'user vient
de recevoir de `install.sh`). Fix `_portal_login_redirect_url` → `/login`.

Commits : `0b6fa41`.

## 1.2.0 · 2026-08-05

**Sprint Day 5 Phase 2** — retrait portail admin + single credential.

- **/login unifié** : form POST HUB_API_KEY (au lieu de `login-password`
  chart-généré + magic URL `/login?key=` obsolètes)
- **SECURITY_PASSWORD retiré** du chart + env vars
- **PORTAL_URL cleanup** hub + agent + chat.html (fallback popup retiré)
- **Doc refonte** : README, ARCHITECTURE, ONBOARDING (bandeau obsolescence)

Breaking : users doivent réinstaller sans `--set security.password`.

Commits : `9781383`, `8ae9afb`.

## 1.1.8 · 2026-08-05

**Fix NameError `_NAMESPACE`** dans `workspace_set_llm_key` (endpoint
Phase 1.7-C). Utilise `ONYXIA_USER` module-level main.py au lieu de
`_NAMESPACE` défini uniquement dans auth.py.

Commits : `887232f`.

## 1.1.7 · 2026-08-05

**Fix `_validate_api_key`** utilise directement `ONYXIA_USER` (env var
standard SSPCloud) au lieu de `_NAMESPACE.removeprefix("user-")`
fragile en contexte async middleware.

Commits : `ca7c14b`.

## 1.1.6 · 2026-08-05

**Sprint Day 5 Phases 1.7-C + 1.7-D**

- UI clé LLM : bloc form dans `/workspace` + endpoint POST
  `/workspace/llm-key` + webhook agent `/api/reload-llm-key` (zéro
  downtime).
- `install.sh` refonte helm CLI (5 étapes) + `QUICKSTART.md` (3 étapes
  user) + `NOTES.txt` chart update.

Commits : `8f0dec1`.

## 1.1.5 · 2026-08-05

**Phase 1.7-B — Proxy /agent same-origin**

Endpoint `/agent/{path:path}` proxifie streaming SSE vers `qgis-agent`
en injectant `Authorization: Bearer HUB_API_KEY` + `X-Hub-Proxy-User`.
Iframe agent dans `desk.html` devient same-origin. Élimine dépendance
cookie `oidc_token` cross-subdomain. Shim JS injecté dans HTML pour
préfixer fetch/XHR/EventSource par `/agent/`.

Commits : `dfd6030`.

## 1.1.4 · 2026-08-05

**Phase 1.7-A — Fix RBAC racine hub via secretKeyRef**

Le chart injecte `HUB_API_KEY` via `secretKeyRef` du Secret K8s
`qgis-hub-apikey` au lieu de dépendre de l'API K8s (get secrets).
`_validate_api_key` fast-path env var → source unique de vérité.

Commits : `c885f04`.

## Jalons antérieurs

- **v1.9.0-sprint-2-3-e3** (2026-06-29) — Vague E3 sprints 2+3 :
  alignement pipeline + monitoring erreurs JS
- **v1.7.0-blocknote-editor** (2026-06) — Vague E2 : éditeur block-based
  BlockNote intégré au desk
- **v1.6.5-vague-e1-composition-libre** (2026-05) — Vague E1 : UX
  composition libre agent IA
- **Vagues A + B** — MCP tools + persistance études

Voir [docs/history/](docs/history/) pour les bilans détaillés
(BILAN_SESSION_2026_06_27.md, BILAN_SESSION_2026_06_29.md).

## Format

Ce fichier suit approximativement [Keep a Changelog](https://keepachangelog.com/)
et le semantic versioning du chart Helm (major.minor.patch).
