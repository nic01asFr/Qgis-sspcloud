# ONBOARDING — qgis-sspcloud

Guide pas-à-pas pour un nouveau développeur ou administrateur du service.

Version 2026-08-02 · Sprint isolation Day 3.1c CLOS.

---

## 1. Contexte

Service géospatial CEREMA sur SSPCloud Onyxia :
- QGIS Desktop distant (noVNC) avec agent LLM et mémoire
- Livrables publiés (storymaps, PDF, datasets) accessibles publiquement
- Accès via connecteur MCP (Claude Desktop, claude.ai) ou UI web desk

## 2. Prérequis

- Compte SSPCloud Onyxia actif (auth Keycloak fournie par ADS Sétif ou
  équivalent)
- Rôle admin SSPCloud (`ADMIN` ou similaire) pour redéployer les pods
- Accès GitHub `nic01asfr/Qgis-sspcloud` (lecture minimum, écriture pour
  contributeurs)

## 3. Deux personas types

### Admin (`nic01asfr`)
- Redéploie les pods hub / agent / workspace via `kubectl delete pod`
- Consulte les métriques `/diagnostics/*` (Bearer HUB_API_KEY)
- Rôle CEREMA : maintenance service

### User (`nicolaslaval`, `marie`, ...)
- Utilise le desk web + connecteur MCP
- Ne redéploie pas les pods
- Rôle CEREMA : agent utilisateur (analyste, chargé de mission)

## 4. Premier déploiement (admin)

### 4.1 Setup SSPCloud
1. Se connecter à https://datalab.sspcloud.fr
2. Créer un service `qgis-hub` via catalogue Onyxia (charts custom)
3. Configurer les env vars :
   - `HUB_API_KEY` (Secret K8s `qgis-hub-apikey`, générée par admin)
   - `ONYXIA_USER` (auto)
   - `HUB_URL` (auto)
   - `PORTAL_URL` (auto → portail admin)
4. Lancer le service → pod `jupyter-python-<hash>-0` créé

### 4.2 Setup composants annexes
- `qgis-workspace-<user>` (chart séparé) → QGIS Desktop
- `qgis-agent` (auto-bootstrap par hub au startup)
- `qgis-mcp-portal-bridge` (chart admin partagé)

### 4.3 Vérifier le déploiement
```bash
# Depuis un pod jupyter admin :
kubectl -n user-<u> get pods | grep qgis
# Doit voir : hub (Running), workspace (Running), agent (Running)

# Test HTTP endpoint hub :
curl -H "Authorization: Bearer $HUB_API_KEY" \
  https://user-<u>-qgis.user.lab.sspcloud.fr/version
# Doit retourner {"version":"...","commit":"..."}
```

## 5. Onboarding user (via portail)

1. Utilisateur va sur `https://user-nic01asfr-qgis-mcp-portal-bridge.user.lab.sspcloud.fr`
2. Étape 1/3 : coller le token OIDC SSPCloud (issu de /account/k8sCodeSnippets)
3. Étape 2/3 : configurer sa clé LLM (récupérée via Vault ou saisie
   manuelle)
4. Étape 3/3 : lancer l'espace QGIS personnel
5. Après déploiement, accès :
   - Desk web : `https://user-<u>-qgis.user.lab.sspcloud.fr/desk`
   - Récupérer API key MCP : `GET /auth/apikey` (avec cookie OIDC)

## 6. Configuration client MCP externe (Claude Desktop)

Dans `claude_desktop_config.json` :
```json
{
  "mcpServers": {
    "qgis-<username>": {
      "command": "npx",
      "args": ["-y", "mcp-proxy-cli", "--url", "https://user-<u>-qgis.user.lab.sspcloud.fr/mcp"],
      "env": {
        "MCP_API_KEY": "<clé issue de /auth/apikey>"
      }
    }
  }
}
```

Redémarrer Claude Desktop. Vérifier que les tools apparaissent :
- `study_list`, `study_create`, `study_switch` (hub-tools Day 2)
- `study_project_list`, `study_project_create`, `study_project_switch`
- `add_layer`, `execute_python`, `smart_load`, ... (workspace-tools)

## 7. Workflow user typique

### Créer une nouvelle étude et analyser une zone

```
User (dans Claude Desktop, connecteur qgis-nicolaslaval) :
  "Crée une étude 'PCRS Sorgues' et charge les bâtiments BD TOPO"

Claude appelle :
1. study_create(name="PCRS Sorgues")
   → sid, default_pid, session_scoped=True
2. set_study_zone(target="Sorgues")
   → bbox_4326
3. smart_load(id="bdtopo_batiments", bbox=[...])
   → 300 features
4. execute_python(code="...")   # analyses éventuelles
5. export_web_map(title="...")
   → path
6. publish_artifact(kind="storymap", slug="...", source=path)
   → hub_url public
```

Résultat : URL stable partageable, livrable en DB `publications`.

### Consulter le desk

Ouvrir `https://user-<u>-qgis.user.lab.sspcloud.fr/desk`

- Badge affiche `ÉTUDE : <name> › PROJET : <name>` (Fix #6 Day 2)
- Si divergence session MCP : badge `MCP: <study_names>` (Day 3.1c)
- Dropdown projets à droite du badge : ✓ actif, 📄 autres, + Nouveau projet
- Panneau ressources (couches, publications, fichiers, recettes)
- Iframe QGIS Desktop (noVNC) au centre
- Iframe agent chat à droite

## 8. Cycle de développement

### 8.1 Setup local
```bash
git clone https://github.com/nic01asfr/qgis-sspcloud
cd qgis-sspcloud/hub
python -m venv .venv && source .venv/bin/activate
pip install -e .
pytest tests/
```

### 8.2 Build local
Le CI GitHub Actions build automatiquement `qgis-hub:latest` et
`qgis-hub:main` sur push main. Voir `.github/workflows/`.

Build manuel :
```bash
docker build -t ghcr.io/nic01asfr/qgis-hub:local hub/
```

### 8.3 Redéployer sur pod SSPCloud
Depuis un pod jupyter admin (kubectl-enabled) :
```bash
kubectl -n user-<u> delete pod jupyter-python-<hash>-0
# statefulset repull image et recrée pod avec imagePullPolicy: Always
```

Attente ready :
```bash
# Poll HTTP :
until curl -s https://user-<u>-qgis.user.lab.sspcloud.fr/mcp -o /dev/null -w "%{http_code}" | grep -q "^401$"; do sleep 10; done
```

### 8.4 Tests d'intégration E2E
Depuis Claude Desktop ou via curl avec Bearer :
```bash
curl -X POST https://user-<u>-qgis.user.lab.sspcloud.fr/mcp \
  -H "Authorization: Bearer $HUB_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{"jsonrpc":"2.0","id":1,"method":"tools/call","params":{"name":"study_list","arguments":{}}}'
```

## 9. Ressources documentation

- [ARCHITECTURE.md](ARCHITECTURE.md) — vue technique complète
- [OPS.md](OPS.md) — runbook opérationnel (backups, incidents, monitoring)
- [docs/spec-day3-session-scoped-active-study.md](docs/spec-day3-session-scoped-active-study.md)
  — spec design Day 3
- [docs/CHARTE_AGENT.md](docs/CHARTE_AGENT.md) — charte prompt agent LLM
- [docs/ARCHITECTURE_AGENT.md](docs/ARCHITECTURE_AGENT.md) — architecture
  agent LLM + tools natifs

## 10. Troubleshooting

### Le desk affiche "Choisir une étude…"
DB user vide. Créer une étude via MCP `study_create` ou via UI desk
(bouton "+ Nouvelle étude" dans page /workspace).

### Badge desk `MCP: X` visible mais desk affiche Y
Normal (design piste 1 Day 3). Une session MCP externe a un contexte
session-scoped ≠ DB. Ne pas se soucier ; les tools MCP continuent
d'opérer sur leur session, le desk sur DB.

### `publish_artifact` retourne 401
Vérifier commit `a1e10de` déployé (Day 3.1c). Sinon `/publish` n'est pas
whitelist inter-pod → mise à jour requise.

### Layers RAM QGIS perdues entre 2 calls MCP
Vérifier commit `98b0964` déployé (Day 3.1b). Le fix `_ensure` lit
`active_actual` depuis `session_active_state` en priorité pour éviter
les faux positifs "divergence" qui rechargeaient le .qgz.

### Publish échoue mais l'URL semble OK
Vérifier les creds STS MinIO (expirent après 7j sans refresh). Voir
[OPS.md](OPS.md) §rotation credentials.
