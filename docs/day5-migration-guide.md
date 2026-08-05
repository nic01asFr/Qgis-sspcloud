# Guide de migration Sprint Day 5 (2026-08-05)

Sprint Day 5 supprime le portail admin `nic01asfr` au profit d'une
installation user-autonome via `helm install`. Ce guide couvre :

1. La migration des users existants (installation legacy → chart 1.2.0)
2. Le retrait du portail admin en production (Phase 3-A)

## 1. Users existants — migration vers chart 1.2.0

### Contexte

Avant Sprint Day 5, un user obtenait son service via le portail admin
`nic01asfr` (page web `user-nic01asfr-qgis-mcp-portal-bridge...`). Le portail
provisionnait 3 pods dans le namespace user via l'id-token OIDC.

Après Sprint Day 5, chaque user installe lui-même le service via
`helm install qgis-hub` depuis son terminal Jupyter Onyxia. Zero admin.

### Étapes de migration user

**Cas 1 — user avec pods legacy actifs (déployés par portail nic01asfr)** :

```bash
# 1. Sauvegarde des données persistantes (PVC survit à uninstall si retention=keep,
#    mais mieux vaut sauvegarder au cas où)
kubectl get pvc -n user-<toi>

# 2. Récupération de la clé HUB_API_KEY existante (Secret K8s)
kubectl get secret qgis-hub-apikey -n user-<toi> \
    -o jsonpath='{.data.HUB_API_KEY}' | base64 -d
# -> garde-la de côté, l'auth reste identique après migration

# 3. Détermination du mode d'installation legacy
helm list -n user-<toi> | grep qgis
# Si présent :
#   -> le service a déjà été installé via helm (rare, cas d'early adopter)
#   -> passer directement à `helm upgrade`
# Si absent (cas majoritaire, portail nic01asfr = kubectl raw manifests) :
#   -> les manifests actuels ne sont PAS Helm-managed
#   -> il faut migrer

# 4. Import Helm des ressources kubectl raw (option A : garde les données)
helm install qgis-hub qgis-sspcloud/qgis-hub -n user-<toi> \
    --set image.tag=latest \
    --take-ownership   # !! IMPORTANT : Helm >= 3.15 uniquement
# Si Helm < 3.15 : delete + reinstall (option B)

# 5. Option B (helm < 3.15) : delete + recreate
kubectl delete statefulset qgis-mcp-bridge qgis-agent -n user-<toi> \
    --cascade=orphan   # !! préserve les pods running (0 downtime)
kubectl delete svc qgis-mcp-bridge qgis-mcp-bridge-svc qgis-agent \
    qgis-agent-svc -n user-<toi>
kubectl delete ingress qgis-mcp-bridge qgis-agent -n user-<toi>
# Le PVC reste intact (données préservées)
helm install qgis-hub qgis-sspcloud/qgis-hub -n user-<toi>
# Le chart crée les nouveaux SS/Svc/Ingress. Les pods orphelins sont
# progressivement remplacés par le rollout Helm.
```

**Cas 2 — user vierge (aucun service existant)** :

```bash
# Suivre QUICKSTART.md — one-liner install.sh
curl -fsSL https://raw.githubusercontent.com/nic01asFr/Qgis-sspcloud/main/install.sh | bash
```

### Points d'attention

- **Clé LLM ephemère** : après migration, la clé LLM doit être re-saisie
  sur `/workspace` bloc "Clé LLM (agent IA)". Persistance PVC prévue Phase 2 backlog.
- **Cookie `hub_api_key`** : le cookie déjà posé côté navigateur user reste
  valide (même Secret K8s HUB_API_KEY). Zéro re-login demandé.
- **Cookie `oidc_token` cross-subdomain** : n'est plus nécessaire (proxy
  /agent same-origin Phase 1.7-B). Peut être supprimé du browser sans impact.
- **URL /login-password et /login?key=** : redirect 301 vers /login (compat
  bookmarks users, retirer après 3 mois de coexistence — voir main.py).
- **URL de l'agent standalone** (`user-<u>-qgis-agent.user.lab...`) : reste
  fonctionnelle pour compat, mais le flow canonique passe par `/agent/` proxy
  depuis le hub.

## 2. Retrait du portail admin nic01asfr (Phase 3-A)

### Actions côté environnement K8s admin

Le pod portail `nic01asfr` tourne dans son propre namespace `user-nic01asfr`.
Une fois tous les users migrés (voir §1) :

```bash
# 1. Stopper le service portail (garde le PVC pour audit historique)
helm uninstall qgis-mcp-portal-bridge -n user-nic01asfr
# ou si déployé kubectl raw :
kubectl delete statefulset qgis-mcp-portal-bridge -n user-nic01asfr
kubectl delete svc,ingress -l app=qgis-mcp-portal-bridge -n user-nic01asfr

# 2. Vérifier qu'aucun user n'appelle plus l'URL portail
kubectl logs -f qgis-mcp-portal-bridge-0 -n user-nic01asfr | grep -v "kube-probe"
# Après 24h sans trafic user, safe à supprimer définitivement.

# 3. Suppression finale (une fois audit terminé)
kubectl delete namespace user-nic01asfr
# !! IRRÉVERSIBLE — supprime tout le namespace admin.
```

### Actions côté repo GitHub

Le repo [Passerelle](https://github.com/nic01asfr/Passerelle) contient
historiquement le code du portail. À archiver :

```bash
# Sur GitHub UI (repo Settings > Archive) :
# 1. Passerelle : archive avec note "Legacy admin portail — remplacé
#    par chart Helm qgis-hub 1.2.0+ dans qgis-sspcloud repo."
# 2. Ajouter à Passerelle README un bandeau :
#    "> ARCHIVÉ 2026-08-05. Le service qgis-hub est maintenant installé
#    via helm chart. Voir https://github.com/nic01asFr/Qgis-sspcloud"
```

### Nettoyage code résiduel (optionnel, faible priorité)

Le code du hub qgis-sspcloud contient encore des endpoints admin liés
au portail (utilisés en debug/legacy) :

- `hub/main.py::_bootstrap_agent` — patch StatefulSet agent, appelé par
  portail. Peut être conservé (utilisé aussi lors de rotate clé LLM).
- `hub/main.py::/api/hub-status` — polling portail. Peut être conservé
  (utilisé aussi par healthchecks internes).
- `hub/main.py::/admin/agent-config` — endpoint admin, restrict à
  ADMIN_TOKEN. Peut être conservé pour debug ops.

Aucun retrait urgent nécessaire. Ces endpoints ne bloquent pas les users
et servent en support debug.

## 3. PR catalogue Onyxia officiel InseeFrLab (Phase 3-C)

Une fois chart 1.2.0 stabilisé (~1-2 semaines de production sans regression),
soumettre PR sur https://github.com/InseeFrLab/helm-charts-datascience
(ou catalogue Onyxia officiel équivalent) :

```yaml
# catalogue-onyxia/qgis-hub/qgis-hub.md
name: QGIS Hub
description: |
  QGIS Desktop noVNC + Agent LLM + Hub API géospatial pour agents CEREMA.
  Publications storymap DSFR, connecteur MCP (Claude Desktop, Cursor).
category: geospatial
image: <URL screenshot desk.html>
chart:
  url: https://raw.githubusercontent.com/nic01asFr/Qgis-sspcloud/main/helm-repo
  name: qgis-hub
  version: ">= 1.2.0"
```

Attente review 1-8 semaines selon disponibilité mainteneurs InseeFrLab.

## Timeline

| Phase | Date | Statut |
|---|---|---|
| Chart 1.0.x | 2026-08-04 | Chart initial |
| Chart 1.1.x | 2026-08-05 | Fix RBAC + proxy /agent + UI LLM (Phase 1.7) |
| Chart 1.2.0 | 2026-08-05 | Single credential + /login + PORTAL_URL cleanup (Phase 2) |
| Migration users existants | 2026-08-06+ | Guide ci-dessus (§1) |
| Retrait portail admin | 2026-08-15+ | Après migration confirmée (§2) |
| PR catalogue Onyxia | 2026-09+ | Après 2-3 semaines stabilité (§3) |
