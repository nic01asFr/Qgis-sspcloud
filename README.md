# QGIS Agent — SSPCloud

Service QGIS géospatial pour agents CEREMA sur SSPCloud Onyxia.  
QGIS Desktop + Agent IA + Mémoire long terme.

## Installation

1. Connecte-toi sur [datalab.sspcloud.fr](https://datalab.sspcloud.fr)
2. Lance un service (Jupyter ou VSCode) avec **`kubernetes.role: edit`**
3. Dans le terminal du service, colle :

```bash
curl -fsSL https://raw.githubusercontent.com/nic01asFr/Qgis-sspcloud/main/install.sh | bash
```

4. Le script déploie l'agent et le hub (~60s), puis affiche l'URL de ton bureau personnel.

## Architecture

```
qgis-agent    → Agent IA (chat, mémoire LLM, /desk)
qgis-mcp-bridge → Hub QGIS (tools MCP, études, publications)
qgis-workspace  → QGIS Desktop noVNC (démarré à la demande)
```

## Images Docker

- `ghcr.io/nic01asfr/qgis-agent:latest`
- `ghcr.io/nic01asfr/qgis-hub:latest`

Buildées automatiquement par GitHub Actions à chaque push sur `main`.
