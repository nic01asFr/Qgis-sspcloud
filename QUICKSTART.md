# QUICKSTART — QGIS Hub sur SSPCloud

**Sprint Day 5 (2026-08-06) — chart 1.2.5 — installation user autonome sans admin.**

Ce guide t'installe le service QGIS Hub (Hub + Agent IA + Workspace QGIS Desktop)
dans ton espace SSPCloud en **3 minutes** depuis un terminal Jupyter Onyxia.

Zero pod admin requis. Une fois installe, tu es autonome pour l'auth
web et le MCP (Claude Desktop, Cursor, Cline).

---

## Prerequis

- Compte SSPCloud actif (https://datalab.sspcloud.fr)
- Un service Jupyter (image `python`, `pyspark`, ou tout template Onyxia)
  demarre dans ton espace personnel
- Terminal du Jupyter ouvert (`Launcher > Other > Terminal`)

Ton pod Jupyter doit avoir `kubernetes.role: edit` (defaut Onyxia depuis 2023).

---

## Installation en 3 etapes

### 1. Lance le one-liner

Dans le terminal Jupyter :

```bash
curl -fsSL https://raw.githubusercontent.com/nic01asFr/Qgis-sspcloud/main/install.sh | bash
```

Le script :
1. Configure Helm (repo cache dans `/home/onyxia/work/.helm-*`)
2. Ajoute le repo `qgis-sspcloud`
3. Lance `helm install qgis-hub qgis-sspcloud/qgis-hub`
4. Attend le rollout des 3 pods (~90s)
5. Extrait ta cle personnelle HUB_API_KEY depuis le Secret K8s

Sortie finale :

```
URL web :   https://user-<toi>-qgis.user.lab.sspcloud.fr
Cle API  :  qgis_<toi>_<32-chars>
MCP JSON :  <URL>/auth/apikey
```

### 2. Premier acces web

Ouvre l'URL web imprimee ci-dessus. Deux options d'auth au 1er acces :

**Option A** (recommandee) — Password saisi via form POST :
- Va sur `<URL>/login-password`
- Ton password est visible dans Onyxia UI :
  Mes services > qgis-hub > "Values de Helm" > `security.password`
- Cookie `hub_api_key` 90j auto-set apres validation

**Option B** — Token OIDC SSPCloud :
- Va sur `<URL>/onboarding`
- Colle ton id-token depuis https://datalab.sspcloud.fr/account/k8sCodeSnippets

Une fois connecte, sur la page `/workspace`, ouvre le bloc
**"Ma cle d'acces personnelle"** pour bookmarquer ton lien magique.
Aux acces suivants, le cookie te reconnait automatiquement.

### 3. Configure la cle LLM

Sur `/workspace`, ouvre le bloc **"Cle LLM (agent IA)"** :
- Recupere une cle sur https://llm.lab.sspcloud.fr (onglet "API keys")
- Colle-la, clique Enregistrer
- L'agent recharge en RAM (zero downtime, aucun restart pod)

**Note ephemere** : cette cle survit tant que le pod agent tourne. Sur
redeploy du chart, retape-la. Persistance PVC prevue Phase 2.

---

## Configuration MCP (optionnel)

Pour connecter ton service depuis Claude Desktop, Cursor ou Cline :

```bash
curl -s https://user-<toi>-qgis.user.lab.sspcloud.fr/auth/apikey
```

Retourne un JSON pret a coller dans ton fichier de config MCP.

---

## Verifier l'etat de l'installation

Depuis le terminal Jupyter :

```bash
kubectl get pods -n user-<toi> -l app.kubernetes.io/managed-by=Helm
```

Attendus :
- `qgis-hub-0` — Hub API + desk web (port 8888)
- `qgis-agent-0` — Agent LLM (port 8888)
- `qgis-workspace-<toi>-0` — QGIS Desktop noVNC (port 8888)

Retirer :
```bash
helm uninstall qgis-hub -n user-<toi>
```

---

## Reference

- README complet : [README.md](README.md)
- Architecture : [ARCHITECTURE.md](ARCHITECTURE.md)
- Ops (runbook) : [OPS.md](OPS.md)
- SPEC Sprint Day 5 : [docs/spec-day5-plan-complet.md](docs/spec-day5-plan-complet.md)

---

## Zero admin ?

Historiquement, l'installation passait par un portail admin (`nic01asfr`)
qui provisionnait les pods d'un user via son id-token OIDC. Depuis
Sprint Day 5, chaque user installe lui-meme via `helm install` depuis
son propre terminal Onyxia. Le chart provisionne tout (SA, Ingress,
Secret K8s, StatefulSets pour Hub + Agent + Workspace). Le portail
admin est en cours de retrait (Phase 3-A).
