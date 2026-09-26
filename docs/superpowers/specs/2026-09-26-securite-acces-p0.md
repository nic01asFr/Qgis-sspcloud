# Sécurité des accès (P0) : constats vérifiés, correctifs, déploiement

Date : 2026-09-26. Équipe T8. Branches (non poussées) :

- `qgis-sspcloud` : `fix/securite-acces-p0` (depuis `origin/main` 4a0c2a3) ;
- `BigQgisMCP` : `fix/auth-workspace` (depuis `origin/main` 2f36a8a).

Sources : inventaire du non-implémenté (top 15 et SEC-1 à SEC-7, AG-14,
LIV-3, EXP-4) et spec agents dédiés (§1.2, lot 1). Chaque constat a été
relu dans le code avant correction. Aucun déploiement, aucune écriture dans
le cluster ; les identifiants `kubectl` étaient expirés, l'item 6 repose donc
sur le code et la documentation du dépôt.

## 1. Synthèse

| # | Constat | Verdict | Correctif |
|---|---|---|---|
| 1 | Clé scopée `qgisk_` = identité complète du propriétaire sur les routes REST inter-pod | Confirmé | Clé scopée cantonnée à `/mcp` et à la lecture des livrables publiés ; 403 explicite ailleurs ; jamais `admin` |
| 2 | Workspace sans authentification (API 8080, MCP 8100, noVNC 6080), CORS `*`, VNC `-nopw` | Confirmé, et plus large : x11vnc écoutait sur toutes les interfaces, flux MJPEG 8081 ouvert | Jeton hub → workspace, mode `permissive` puis `enforce`, noVNC en Basic, x11vnc en boucle locale, CORS local |
| 3 | `allowed: []` = tous les outils ; profil inconnu = `standard` | Confirmé (agent et hub) | Liste vide = aucun outil ; profil inconnu = profil fermé |
| 4 | Clé brute rendue au modèle ; `profile_locked` posé par le client | Confirmé | Coffre côté agent, référence opaque au modèle, remise unique par l'interface ; verrou décidé par le serveur |
| 5 | Secrets S3/Vault en `value:` ; bundles signés par une clé de démo publique | Confirmé | Secret Kubernetes en amorce, fichier sur le PVC comme source ; jeton Vault retiré ; plus de clé de démo |
| 6 | Ingress hérités `*-bridge` | Relevé (non modifié) | Recommandation : supprimer après vérification |

## 2. Constats et correctifs

### 2.1 Clé scopée et routes REST (SEC-1, P0)

**Constat.** `oidc_auth_middleware` (étape 3bis) acceptait une clé scopée sur
tous les préfixes `_OIDC_MIDDLEWARE_INTER_POD` (`/studies`, `/publish`,
`/admin`, `/internal`, `/sessions`, `/agent-context`…). `get_current_user`
rendait alors `{username, role, source: "scoped"}` avec `role = "admin"` si le
propriétaire l'était. Seuls filtres : la liste blanche du proxy `/mcp`, le
refus de minter depuis une clé scopée (`main.py`) et le 403 de
`/diagnostics/isolation`. Les jetons OAuth sont eux aussi des clés scopées
(`mode="supervisor"`) : même trou.

**Correctif** (`hub/hub/auth.py`) :

- `scoped_key_path_allowed()` : une clé scopée n'est recevable que sur `/mcp`,
  `/published` et `/p`. `/published` reste ouvert car sa porte d'audience lit
  `identity["scope"]` (livrables `cerema_internal`) ;
- middleware : hors de ces routes, 403 explicite (« Cette clé déléguée n'est
  valable que sur le connecteur MCP… ») au lieu du passage ;
- `get_current_user` : même refus, en défense en profondeur (une route
  publique qui appellerait la dépendance) ;
- `_validate_scoped_key` : `role` toujours `user` ; `require_admin` refuse
  `source == "scoped"`.

**Tests** : `hub/tests/test_cle_scopee_routes.py` (16 cas : 10 routes refusées,
`/mcp` accepté, jeton OAuth, défense en profondeur, jamais admin, préfixes
sans débordement `/publish` ≠ `/p`).

**Vérifié compatible** : aucun client légitime n'appelle une route REST avec
une clé `qgisk_`. L'agent et le workspace utilisent `HUB_API_KEY` ; claude.ai
et les clients MCP n'appellent que `/mcp`.

### 2.2 Authentification hub → workspace (SEC-2, P0)

**Constat.** `src/api_server.py` : aucune authentification, `allow_origins=["*"]`,
`/api/execute` exécute du Python arbitraire. `main_mcp.py` : le Bearer n'est
lu qu'en `MULTI_USER_MODE`, `false` dans le chart et `sessions.py`.
`supervisord.conf` : `x11vnc -nopw` **sans `-localhost`** (port 5900 ouvert
sur l'IP du pod, hors Service mais joignable par IP), websockify 6080 sans
authentification. `stream_server.py` diffuse l'écran sur `0.0.0.0:8081`.
Aucune NetworkPolicy n'est rendue malgré `security.networkPolicy.enabled`.

**Appelants recensés** (tous vérifiés) :

| Appelant | Cible | Authentification après correctif |
|---|---|---|
| Hub, appels directs (`execute_python` des études, `_call_workspace_tool`, publication, audit, maximisation, téléversement) | MCP 8100, API 8080 | `Authorization: Bearer HUB_API_KEY`, **déjà envoyé** |
| Hub, proxy `/mcp` (clients MCP, agent, recettes web) | MCP 8100 | `X-Workspace-Token` ajouté par `_proxy_request` |
| Hub, `/api/files/*`, `/api/upload` | API 8080 | idem (même `_proxy_request`) |
| Hub, `/workspace/vnc/*` et `/workspace/vnc/websockify` | noVNC 6080 | `Authorization: Basic hub:<jeton>` |
| Serveur MCP → API (même pod) | `localhost:8080` | boucle locale, sans jeton |
| Maintenance `kubectl exec … curl localhost:8080/api/command` | boucle locale | sans jeton |
| `kubectl port-forward` (banc `evals`, `PontHttp`) | boucle locale du pod | sans jeton |
| `evals/pont.py` depuis un autre pod | réseau du pod | `WORKSPACE_TOKEN` si fourni |
| Agent | — | n'appelle jamais le workspace directement (il passe par `/mcp` du hub) |
| Sonde de disponibilité (`/health` sur 8100) | kubelet | `/health` reste libre |

**Correctif.**

- Jeton : `HMAC-SHA256(HUB_API_KEY, "qgis-workspace-v1")`, calculé des deux
  côtés à partir du Secret `qgis-hub-apikey`, déjà injecté par `secretKeyRef`
  dans le hub et le workspace. Aucun nouveau secret à créer ni à stocker.
  `WORKSPACE_TOKEN` le remplace s'il est défini des deux côtés. Le vecteur de
  test de référence est le même dans les deux dépôts.
- Workspace (`BigQgisMCP`) : `src/workspace_auth.py` (règle unique),
  intergiciel HTTP dans `api_server.py`, intergiciel ASGI pur dans
  `main_mcp.py` (pas de mise en mémoire des flux SSE), neutre en
  `MULTI_USER_MODE` qui a sa propre authentification.
- Règle : `/health` et boucle locale (`127.0.0.1`, `::1`) libres ; sinon
  `X-Workspace-Token` valide ou `Bearer HUB_API_KEY` ; comparaisons à temps
  constant.
- Modes `WORKSPACE_AUTH_MODE` : `permissive` (défaut : journalise une ligne
  par minute et par couple chemin/hôte, puis sert), `enforce` (401),
  `off`. Valeur inconnue = `enforce`. En `enforce` sans jeton calculable,
  tout appel non local est refusé.
- noVNC : `src/lancer_novnc.py` remplace la commande supervisord ; en
  `enforce`, websockify reçoit `--auth-plugin BasicHTTPAuth --auth-source
  hub:<jeton>` ; sinon commande identique à avant.
- x11vnc : `-localhost` (seul websockify, même conteneur, le joint).
- Flux MJPEG : `STREAM_BIND_HOST` (défaut `0.0.0.0` pour l'usage autonome,
  `127.0.0.1` posé par le chart et par `sessions.py`).
- CORS : origines locales par défaut, `WORKSPACE_CORS_ORIGINS` pour un usage
  autonome. En déploiement hub, aucun navigateur ne joint le workspace
  directement.
- Hub : `hub/hub/workspace_auth.py` ; `_proxy_request` retire tout
  `X-Workspace-Token` fourni par le client et pose le sien ; les deux relais
  noVNC posent l'en-tête Basic (`additional_headers` ou `extra_headers` selon
  la version de `websockets`) ; `sessions.py` recopie `WORKSPACE_AUTH_MODE`
  et `STREAM_BIND_HOST` dans le pod créé et les garantit sur un workspace
  existant (`kubectl set env`, sans effet si la valeur est déjà là) ; le chart
  pose les mêmes variables (`workspace.authMode`).

**Tests** : `BigQgisMCP/tests/test_workspace_auth.py` (16 : règle, modes,
API et MCP en 401 sans jeton, CORS, lanceur noVNC, supervisord, flux) ;
`hub/tests/test_workspace_auth.py` (7 : dérivation, en-tête Basic, proxy qui
écrase le jeton du client, modes) ; `evals/tests/test_pont_clients_sonde.py`
(jeton transmis).

### 2.3 Profils ouvrants (AG-14, agents dédiés §1.2 point 4, P1)

**Constat.** Agent : `_get_mcp_tools` testait `isinstance(allowed, list) and
allowed` ; `[]` sautait le filtre et les quatre profils d'assistance
(`component_assist`, `assembly_assist`, `recipe_analyzer`,
`agent_config_analyzer`) recevaient les 49 outils, dont `execute_python` et
`delete_file`. Un profil absent du cache rendait aussi tous les outils, et
`_get_profile_tools_whitelist` rendait `None` (= tout). Hub :
`profile_manager.get_profile` retombait sur `standard` (`allowed: all`) ;
`actions/agent_brick.py` s'en sert pour autoriser les outils natifs.

**Correctif.** Agent (`qgis_agent.py`) : une liste, même vide, est une liste
blanche ; profil inconnu = aucun outil MCP et liste blanche vide ; si le
cache est vide (hub pas prêt au démarrage), un rechargement est tenté une
fois, puis on reste fermé. Hub : identifiant inconnu = profil fermé
`_profil_inconnu` (aucun outil MCP ni natif) ; sans identifiant, le profil
par défaut reste servi ; `/profiles/{id}` répond toujours 404.

**Tests** : `agent/tests/test_profils_fermes.py` (7),
`hub/tests/test_profil_inconnu_ferme.py` (4). Le fixture
`test_instantanes_contexte.py` déclare désormais le profil `all` qu'il
supposait implicitement.

### 2.4 Clé brute et verrou de profil (agents dédiés §1.2 points 5-6, P1)

**Constat.** `create_agent` rendait `{"key": "qgisk_…"}` au modèle (historique
L1, journal de tour) ; `publish_agent` et `revoke_agent` exigeaient la clé
brute en argument ; `publish_agent` rendait une URL `/agent-share/…` qui
répond 404 (HUB-1). `profile_locked` était un champ de formulaire accepté tel
quel ; aucun client du dépôt ne l'envoie.

**Correctif.**

- `agent/agent/cles_deleguees.py` : coffre en mémoire du pod agent. Le modèle
  ne reçoit que `agent_ref` (`agent-<hex>`), `key_masked` et
  `lien_remise_cle` (`{HUB_URL}/agent/api/cles-deleguees/<ref>`).
- Route `GET /api/cles-deleguees/{ref}` (derrière le middleware de l'agent) :
  remise **unique**, dans l'heure, `Cache-Control: no-store`.
- `publish_agent` / `revoke_agent` résolvent la référence (une clé déjà en
  clair, collée par l'utilisateur, reste acceptée) ; une référence inconnue
  est refusée sans appel au hub ; `published_url` n'est plus rendue au modèle
  tant que la route n'existe pas.
- Verrou : `_verrou_de_profil` l'accorde seulement si le client le demande
  **et** que la session est d'un contexte à persona fixée (`editor_freeform`,
  `recipe_create`, `assist_component`, `assist_assembly`), lu dans
  l'identifiant de session. Pour les deux contextes d'assistance, le profil
  est imposé par le serveur.

**Tests** : `agent/tests/test_cles_et_verrou_serveur.py` (12) ;
`test_profile_locked.py` utilise désormais une session d'éditeur.

### 2.5 Secrets du chart et signature (SEC-4, LIV-3, P0)

**Contrainte d'exploitation** (reçue en cours de lot) : pas de dépendance à
Vault (renouvellement manuel) ; les secrets du service vivent sur son PVC.

**Constats.**

- `templates/statefulset.yaml` posait `AWS_ACCESS_KEY_ID`,
  `AWS_SECRET_ACCESS_KEY`, `AWS_SESSION_TOKEN` et `VAULT_TOKEN` en `value:` :
  lisibles par quiconque lit le StatefulSet (rôle `view`).
- Aucun code d'exécution ne lit Vault (recherche dans `hub/` et `agent/`).
  Seul `install.sh` lit Vault **à l'installation**, en option, pour la clé
  LLM, et recopiait `VAULT_TOKEN` dans les values.
- Les identifiants S3 étaient lus, dans l'ordre, dans le Secret
  `passerelle-s3-creds` puis dans l'environnement du pod.
- `hub/hub/publish/bundle.py` signait avec `bytes(range(32))` à défaut de
  `CEREMA_ED25519_PRIVATE_KEY`, que ni le chart ni `deploy/` ne définissent.

**Correctif.**

- `hub/hub/secrets_pvc.py` : fichier `DATA_DIR/secrets/<nom>` (répertoire
  0700, fichier 0600, écriture atomique, valeurs multilignes refusées), inerte
  hors déploiement (`DATA_DIR` absent).
- `s3_publication` : ordre de lecture **fichier du PVC**, puis Secret
  passerelle, puis environnement ; amorçage automatique du fichier au premier
  identifiant valide trouvé ; le renouvellement depuis l'interface écrit
  d'abord le fichier, puis le Secret (retour arrière possible) et réussit
  même sans droit d'écriture sur les Secrets.
- Chart : `templates/secret-s3.yaml` (Secret `<release>-qgis-hub-s3`, ou
  `s3.existingSecret`) lu par `secretKeyRef` ; ce Secret ne sert plus que
  d'amorce. `VAULT_TOKEN` n'est plus rendu (la valeur reste acceptée).
  `install.sh` n'écrit plus le jeton Vault dans les values et rattache le
  nouveau Secret.
- Signature : plus aucune clé de démo. Sans clé valide, le bundle est produit
  **non signé** et le dit (`SIGNED: false`, `SIGNATURE_ALGO: none`,
  avertissement dans `README.txt`) ; `sign_integrity` lève
  `SignatureIndisponible`. La clé est **fournie** par l'installation :
  `publication.signingKey.existingSecret` (le chart ne la génère pas).

**Tests** : `hub/tests/test_secrets_sur_pvc.py` (7),
`test_sprint10_wave1_publish.py` (non signé, clé invalide sans repli, clé
fournie vérifiable), `test_install_adoption.py` (accord chart/`install.sh`,
exécuté ici car `helm` est présent). `helm lint` et `helm template` passent ;
une valeur `workspace.authMode` hors énumération est refusée par le schéma.

**Migration, sans rupture.**

1. `helm upgrade` : le chart crée `qgis-hub-s3` depuis les mêmes values
   qu'avant ; le pod lit les mêmes valeurs, par `secretKeyRef`.
2. Au premier accès S3, le hub recopie les identifiants valides (Secret
   passerelle ou environnement) dans `secrets/s3.env` du PVC. Rien à faire.
3. Plus tard, facultatif : vider `s3.accessKeyId/secretAccessKey/sessionToken`
   des values ; le fichier du PVC fait foi.
4. Retour arrière : l'ancien hub relit le Secret passerelle, toujours tenu à
   jour.
5. Clé LLM : déjà dans le Secret `qgis-llm-apikey` (pas de Vault à
   l'exécution). Proposition, non faite dans ce lot : la ranger aussi dans
   `secrets/` du PVC et retirer la lecture Vault d'`install.sh`.

## 3. Séquence de déploiement exacte

Préalable : relecture et fusion des deux branches ; incrément de version du
chart (`Chart.yaml`, 1.4.0 → 1.5.0) et publication `helm-repo` selon
`DEVELOPMENT.md`, non faits ici.

1. **Workspace** : fusionner `BigQgisMCP` `fix/auth-workspace` ; la CI publie
   `qgisremotemcp:latest`. Sans variable, le mode est `permissive` : comportement
   inchangé sauf x11vnc en boucle locale et CORS local. Le pod ne tire la
   nouvelle image qu'à son redémarrage.
2. **Hub et agent** : fusionner `qgis-sspcloud` `fix/securite-acces-p0` ; CI des
   images ; `install.sh` (ou `helm upgrade`) avec le chart 1.5.0,
   `workspace.authMode` laissé à `permissive`. Le hub redémarre et envoie le
   jeton ; le workspace redémarre (gabarit modifié) et tire l'image de
   l'étape 1. L'ordre 1/2 est indifférent en `permissive`.
3. **Vérifier** (24 h d'usage normal) :
   - `kubectl exec qgis-workspace-<u>-0 -- grep -h "workspace_auth PERMISSIF" /var/log/supervisor/api_err.log /var/log/supervisor/mcp_err.log`
     ne doit rien montrer d'autre que des appels attendus ;
   - `kubectl exec qgis-workspace-<u>-0 -- python3 -c "from websockify.auth_plugins import BasicHTTPAuth"`
     doit réussir (plugin présent dans l'image) ;
   - bureau noVNC du desk, chat avec un outil QGIS, téléversement et
     téléchargement de fichier, publication d'un livrable.
4. **Durcir** : `helm upgrade … --reuse-values --set workspace.authMode=enforce`
   (et reporter la valeur dans les values d'`install.sh` pour qu'une
   réinstallation ne revienne pas à `permissive`). Hub et workspace redémarrent ;
   websockify exige le Basic.
5. **Contrôler** : depuis un autre pod du namespace,
   `curl -s -o /dev/null -w "%{http_code}" http://qgis-workspace-<u>:8080/api/project`
   → `401` ; `kubectl exec qgis-workspace-<u>-0 -- curl -s localhost:8080/api/project`
   → `200` ; bureau et chat fonctionnels.
6. **Retour arrière** : `--set workspace.authMode=permissive` (ou `off`).
7. **Signature** (quand une clé existe) : `kubectl create secret generic
   qgis-bundle-signing --from-literal=CEREMA_ED25519_PRIVATE_KEY=<hex>` puis
   `--set publication.signingKey.existingSecret=qgis-bundle-signing`.
8. Release suivante : passer le défaut du chart à `enforce`.

## 4. Ingress hérités (item 6, non modifiés)

`qgis-agent-bridge-ingress`, `qgis-mcp-bridge-ingress`,
`qgis-mcp-portal-bridge-ingress` (134 à 136 jours, soit mi-mai 2026) et les
services `*-bridge-jupyter-python` datent de l'architecture antérieure au
chart (Sprint Day 5, août 2026). D'après `OPS.md` §3.2,
`docs/day5-migration-guide.md`, `docs/history/ONBOARDING-legacy.md` et
`deploy/rbac/hub-secret-reader.yaml` :

- ce sont des services Onyxia « jupyter-python » détournés : le pod
  `qgis-mcp-bridge-jupyter-python-0` sert **sa propre copie figée du hub**
  (`/opt/qgis-hub`, port 8888, celui qu'expose l'ingress Jupyter) ;
  `qgis-agent-bridge` fait de même pour l'agent ; `qgis-mcp-portal-bridge`
  est l'ancien portail d'onboarding (il recevait des jetons OIDC et des clés
  LLM et déployait des manifestes) ;
- ils ne sont pas gérés par Helm et ne sont plus mis à jour : leur
  `PERSONAL_INIT_SCRIPT` (`server_init.sh`) répond 404 ;
- leur authentification est donc celle du code figé : les correctifs
  d'accès postérieurs (au moins le jeton OAuth dérivé de septembre 2026 et le
  présent lot) ne s'y appliquent pas, alors qu'ils sont publics ;
- la décision Q2 (`STRUCTURE_ET_PROCESS.md`) réserve `/desk` et `/workspace`
  au hub du chart. `OPS.md` dit « à retirer, pas à maintenir ».

**Recommandation : supprimer**, après vérification en lecture :
`kubectl get ingress,svc,sts -n user-<u> | grep bridge`,
`kubectl logs <pod>-0 --since=72h | grep -v kube-probe` (aucun trafic
utilisateur attendu), puis sauvegarde du PVC s'il porte des données, et
suppression (`helm uninstall` si ce sont des releases Onyxia, sinon
`kubectl delete sts,svc,ingress`). Aucun de ces services n'est appelé par le
code actuel (les URL de repli `qgis-mcp-bridge` ont été retirées de l'agent).

## 5. Risques résiduels

- **Contournement `kube-probe` intra-cluster.** Le hub et l'agent laissent
  passer toute requête `User-Agent: kube-probe` sans `X-Forwarded-For`. Côté
  hub, les routes restent protégées par `get_current_user` ; côté agent, la
  plupart des routes n'ont pas d'autre contrôle (dont `/chat` et la remise de
  clé, protégée seulement par une référence aléatoire de 64 bits à usage
  unique). À traiter avec EXP-4.
- **Pas de NetworkPolicy** (EXP-4) : le jeton du workspace est la seule
  barrière réseau. Ajouter une NetworkPolicy « workspace joignable par le
  seul hub » reste la mesure de fond (à tester : sondes kubelet, droits de
  création sur SSPCloud).
- Le workspace détient `HUB_API_KEY` (pour `publish_artifact`) : sa
  compromission donne la clé maître. Un jeton propre au workspace, puis le
  retrait de `HUB_API_KEY` du pod, est la suite logique.
- Rotation de `HUB_API_KEY` : le workspace garde l'ancienne valeur jusqu'à
  son redémarrage (comme `publish_artifact` aujourd'hui) ; en `enforce`, il
  faut le redémarrer après une rotation.
- En `permissive`, rien n'est bloqué : c'est un état de transition.
- Le plugin `BasicHTTPAuth` de websockify n'a pas pu être essayé hors
  conteneur (tests `container` non joués) : étape 3 de la séquence.
- Clés scopées toujours stockées en clair avec `parent_key` (SEC-5) ;
  périmètre de données non appliqué (SEC-3) ; un jeton OAuth garde tous les
  outils à `/mcp` (par conception, `mode="supervisor"`).
- Une clé scopée lit les livrables `restricted`/`confidential` de son
  propriétaire (`/published` compare le nom d'utilisateur).
- Coffre des clés en mémoire : après un redémarrage de l'agent, la référence
  n'est plus résolue (publication ou révocation par l'interface du hub).
- Les values Helm portent encore les identifiants S3 posés par Onyxia (secret
  de release Helm, lisible avec le droit `get secrets`).

## 6. Ce qui reste

- Passer `workspace.authMode` à `enforce` par défaut (release suivante).
- NetworkPolicy du workspace et suppression du contournement `kube-probe`
  côté agent (EXP-4, SEC-6).
- Route `/agent-share` ou retrait définitif de l'URL côté hub (HUB-1) ;
  document d'agent dédié et validation par geste d'interface (lot 2).
- Hachage des clés scopées et révocation en cascade (SEC-5).
- Clé LLM sur le PVC et fin de la lecture Vault dans `install.sh`.
- Suppression des ingress `*-bridge` (section 4).
- `securityContext` du workspace (SEC-7).

## 7. Commits et tests

`qgis-sspcloud` (`fix/securite-acces-p0`) :

| Commit | Objet |
|---|---|
| `cd38f3c` | fix(auth): cantonner les cles scopees au proxy mcp et aux livrables publies |
| `34a1a32` | fix(profils): fermer le filtre d'outils sur liste vide et profil inconnu |
| `e6e5c9f` | fix(agent): garder la cle deleguee hors du modele et decider du verrou de profil cote serveur |
| `a37e565` | fix(publication): ne plus signer les bundles avec une cle de demo publique |
| `2b4b635` | fix(chart): lire les secrets s3 par secretkeyref et retirer le jeton vault du pod |
| `5bcec53` | fix(stockage): conserver les identifiants s3 sur le pvc du hub sans dependance a vault |
| `fce3637` | fix(workspace): authentifier le hub aupres du workspace et de novnc |
| `7637c67` | fix(evals): transmettre le jeton du workspace au pont http quand il est fourni |

`BigQgisMCP` (`fix/auth-workspace`) : `e073041` fix(auth): authentifier les
appels non locaux de l'api, du serveur mcp et de novnc.

Suites : hub 1 393 réussis, 4 ignorés, 5 xfail (1 357 sur `main` ; +36) ;
agent 517 (498 ; +19) ; evals 166 (165 ; +1) ; BigQgisMCP
`-m "not container"` 225 réussis, 6 ignorés (209 ; +16).
