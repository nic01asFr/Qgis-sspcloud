#!/bin/bash
# install.sh — Installe le service QGIS Agent dans ton espace SSPCloud.
# À exécuter depuis un terminal dans un service Onyxia (kubernetes.role: edit requis).
#
# Usage :
#   curl -fsSL https://raw.githubusercontent.com/nic01asFr/Qgis-sspcloud/main/install.sh | bash

set -e

REPO="https://raw.githubusercontent.com/nic01asFr/Qgis-sspcloud/main"
AGENT_IMAGE="ghcr.io/nic01asfr/qgis-agent:latest"       # built: Qgis-sspcloud CI
HUB_IMAGE="ghcr.io/nic01asfr/qgis-hub:latest"          # built: Qgis-sspcloud CI
WORKSPACE_IMAGE="ghcr.io/nic01asfr/qgisremotemcp:latest" # built: BigQgisMCP (manuel)
GPU_IMAGE="ghcr.io/nic01asfr/geoai-gpu:latest"           # built: Passerelle CI
HELM_RELEASE_AGENT="qgis-agent"
HELM_RELEASE_HUB="qgis-mcp-bridge"
HELM_RELEASE_WORKSPACE="qgis-workspace"
HELM_RELEASE_GPU="geoai-gpu"

# Détection automatique du namespace depuis le serviceaccount K8s monté.
# C'est la source la plus fiable dans n'importe quel pod Onyxia.
SA_NS_FILE="/var/run/secrets/kubernetes.io/serviceaccount/namespace"

NAMESPACE="${KUBERNETES_NAMESPACE:-}"
if [ -z "$NAMESPACE" ] && [ -f "$SA_NS_FILE" ]; then
    NAMESPACE=$(cat "$SA_NS_FILE")
fi

USERNAME="${ONYXIA_USER:-}"
if [ -z "$USERNAME" ] && [ -n "$NAMESPACE" ]; then
    # Convention SSPCloud : namespace = user-<login>
    USERNAME="${NAMESPACE#user-}"
fi

if [ -z "$USERNAME" ] || [ -z "$NAMESPACE" ]; then
    echo "ERREUR : impossible de détecter l'utilisateur SSPCloud."
    echo "Lance ce script depuis un terminal dans un service Onyxia."
    exit 1
fi

echo ""
echo "╔══════════════════════════════════════════════════════╗"
echo "║   Installation QGIS Agent — $USERNAME"
echo "╚══════════════════════════════════════════════════════╝"
echo ""

# Vérifier les droits
if ! kubectl auth can-i create secrets -n "$NAMESPACE" 2>/dev/null | grep -q yes; then
    echo "ERREUR : droits insuffisants (create:secrets refusé)."
    echo "Relance le service avec kubernetes.role = edit ou admin."
    exit 1
fi

# Helm repo
echo "▸ Suppression PVCs orphelins sans labels Helm..."
for r in "$HELM_RELEASE_HUB" "$HELM_RELEASE_AGENT" "$HELM_RELEASE_WORKSPACE"; do
    pvc="${r}-jupyter-python"
    owned=$(kubectl get pvc "$pvc" -n "$NAMESPACE" \
        -o jsonpath="{.metadata.annotations['meta\.helm\.sh/release-name']}" 2>/dev/null || true)
    if [ -n "$owned" ] && [ "$owned" != "$r" ]; then
        kubectl delete pvc "$pvc" -n "$NAMESPACE" 2>/dev/null || true
    fi
done

echo "▸ Ajout repo Helm Onyxia..."
helm repo add ide https://nexus.lab.sspcloud.fr/repository/inseefrlab-helm-charts-interactive-services --force-update 2>/dev/null
helm repo update ide 2>/dev/null

# Convention Onyxia : ingress hostname = user-{USERNAME}-{RELEASE}-0.user.lab.sspcloud.fr
# userHostname (port user) = user-{USERNAME}-{RELEASE}-user.user.lab.sspcloud.fr
HUB_HOST="user-${USERNAME}-${HELM_RELEASE_HUB}-0.user.lab.sspcloud.fr"
HUB_USER_HOST="user-${USERNAME}-${HELM_RELEASE_HUB}-user.user.lab.sspcloud.fr"
AGENT_HOST="user-${USERNAME}-${HELM_RELEASE_AGENT}-0.user.lab.sspcloud.fr"
AGENT_USER_HOST="user-${USERNAME}-${HELM_RELEASE_AGENT}-user.user.lab.sspcloud.fr"

# 1. Hub QGIS/MCP (tools, études, publications, /desk)
echo "▸ Déploiement hub QGIS ($HELM_RELEASE_HUB)..."
helm upgrade --install "$HELM_RELEASE_HUB" ide/jupyter-python \
    --namespace "$NAMESPACE" \
    --set service.image.custom.enabled=true \
    --set "service.image.custom.version=$HUB_IMAGE" \
    --set "init.personalInit=$REPO/server_init.sh" \
    --set "persistence.enabled=false" \
    --set "global.suspend=false" \
    --set "extraEnvVars[0].name=SERVICE_NAME" \
    --set-string "extraEnvVars[0].value=qgis-mcp" \
    --set "extraEnvVars[1].name=SERVER_MODULE" \
    --set-string "extraEnvVars[1].value=hub.main:app" \
    --set "extraEnvVars[2].name=SERVER_PORT" \
    --set-string "extraEnvVars[2].value=8888" \
    --set "extraEnvVars[3].name=ONYXIA_USER" \
    --set-string "extraEnvVars[3].value=$USERNAME" \
    --set "extraEnvVars[4].name=SSPCLOUD_NAMESPACE" \
    --set-string "extraEnvVars[4].value=$NAMESPACE" \
    2>&1

# 2. Agent IA (chat, mémoire, LLM)
echo "▸ Déploiement agent IA ($HELM_RELEASE_AGENT)..."
helm upgrade --install "$HELM_RELEASE_AGENT" ide/jupyter-python \
    --namespace "$NAMESPACE" \
    --set service.image.custom.enabled=true \
    --set "service.image.custom.version=$AGENT_IMAGE" \
    --set "init.personalInit=$REPO/server_init.sh" \
    --set "persistence.enabled=false" \
    --set "global.suspend=false" \
    --set "extraEnvVars[0].name=SERVICE_NAME" \
    --set-string "extraEnvVars[0].value=qgis-agent" \
    --set "extraEnvVars[1].name=SERVER_MODULE" \
    --set-string "extraEnvVars[1].value=agent.main:app" \
    --set "extraEnvVars[2].name=SERVER_PORT" \
    --set-string "extraEnvVars[2].value=8888" \
    --set "extraEnvVars[3].name=ONYXIA_USER" \
    --set-string "extraEnvVars[3].value=$USERNAME" \
    --set "extraEnvVars[4].name=SSPCLOUD_NAMESPACE" \
    --set-string "extraEnvVars[4].value=$NAMESPACE" \
    2>&1

# 3. Workspace QGIS Desktop (noVNC + MCP server)
# Pas de personalInit : l'image qgisremotemcp a son propre entrypoint
echo "▸ Déploiement workspace QGIS Desktop ($HELM_RELEASE_WORKSPACE)..."
helm upgrade --install "$HELM_RELEASE_WORKSPACE" ide/jupyter-python \
    --namespace "$NAMESPACE" \
    --set service.image.custom.enabled=true \
    --set "service.image.custom.version=$WORKSPACE_IMAGE" \
    --set "networking.user.enabled=true" \
    --set "networking.user.ports[0]=8080" \
    --set "ingress.hostname=user-${USERNAME}-${HELM_RELEASE_WORKSPACE}-0.user.lab.sspcloud.fr" \
    --set "ingress.userHostname=user-${USERNAME}-${HELM_RELEASE_WORKSPACE}-user.user.lab.sspcloud.fr" \
    --set "persistence.enabled=false" \
    --set "global.suspend=false" \
    --set "extraEnvVars[0].name=SERVICE_NAME" \
    --set-string "extraEnvVars[0].value=qgis-workspace" \
    --set "extraEnvVars[1].name=ONYXIA_USER" \
    --set-string "extraEnvVars[1].value=$USERNAME" \
    --set "extraEnvVars[2].name=SSPCLOUD_NAMESPACE" \
    --set-string "extraEnvVars[2].value=$NAMESPACE" \
    2>&1

# 4. Pod GPU GeoAI (SAM3 + DeepForest) — scale 0 au démarrage, réveillé à la demande
echo "▸ Déploiement pod GPU GeoAI ($HELM_RELEASE_GPU — suspendu, démarré à la demande)..."
helm repo add inseefrlab https://inseefrlab.github.io/helm-charts-interactive-services --force-update 2>/dev/null
helm repo update inseefrlab 2>/dev/null
helm upgrade --install "$HELM_RELEASE_GPU" inseefrlab/jupyter-pytorch-gpu \
    --namespace "$NAMESPACE" \
    --set service.image.custom.enabled=true \
    --set "service.image.custom.version=$GPU_IMAGE" \
    --set "init.personalInit=$REPO/server_init.sh" \
    --set "networking.user.enabled=true" \
    --set "networking.user.ports[0]=8000" \
    --set "persistence.enabled=true" \
    --set "persistence.size=20Gi" \
    --set "global.suspend=true" \
    --set "nodeSelector.gpu-vram=16GB" \
    --set "extraEnvVars[0].name=SERVICE_NAME" \
    --set-string "extraEnvVars[0].value=geoai-gpu" \
    --set "extraEnvVars[1].name=SERVER_PORT" \
    --set-string "extraEnvVars[1].value=8000" \
    --set "extraEnvVars[2].name=ONYXIA_USER" \
    --set-string "extraEnvVars[2].value=$USERNAME" \
    --set "extraEnvVars[3].name=SSPCLOUD_NAMESPACE" \
    --set-string "extraEnvVars[3].value=$NAMESPACE" \
    --set "extraEnvVars[4].name=GEOAI_GPU_SERVICE_NAME" \
    --set-string "extraEnvVars[4].value=$HELM_RELEASE_GPU" \
    2>&1

echo ""
echo "⏳ Attente démarrage hub + agent (~90s)..."
kubectl rollout status statefulset/${HELM_RELEASE_HUB}-jupyter-python \
    -n "$NAMESPACE" --timeout=120s 2>/dev/null || true
kubectl rollout status statefulset/${HELM_RELEASE_AGENT}-jupyter-python \
    -n "$NAMESPACE" --timeout=120s 2>/dev/null || true

# Le desk est servi par le hub sur l'ingress standard (port 8888 remplacé par uvicorn)
DESK_URL="https://${HUB_HOST}/desk"

echo ""
echo "╔══════════════════════════════════════════════════════╗"
echo "║   ✓ Installation terminée !"
echo "║"
echo "║   Ton bureau de travail QGIS :"
echo "║   $DESK_URL"
echo "║"
echo "║   Bookmarke ce lien — c'est ton espace personnel."
echo "║"
echo "║   Note : le bureau QGIS Desktop (noVNC) se lancera"
echo "║   automatiquement à la demande depuis le bureau."
echo "╚══════════════════════════════════════════════════════╝"
echo ""
