# Changelog qgis-sspcloud

Versions du chart Helm `qgis-hub` (publié via GitHub Actions dans
[`helm-repo/`](helm-repo/)) et jalons majeurs du service.

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
