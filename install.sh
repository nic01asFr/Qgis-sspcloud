#!/bin/bash
# install.sh — Installe le service QGIS Agent dans ton espace SSPCloud.
# À exécuter depuis un terminal dans un service Onyxia (kubernetes.role: edit requis).
#
# Usage :
#   curl -fsSL https://raw.githubusercontent.com/nic01asFr/Qgis-sspcloud/main/install.sh | bash

set -e

REPO="https://raw.githubusercontent.com/nic01asFr/Qgis-sspcloud/main"
AGENT_IMAGE="ghcr.io/nic01asfr/qgis-agent:latest"
HUB_IMAGE="ghcr.io/nic01asfr/qgis-hub:latest"
HELM_RELEASE_AGENT="qgis-agent"
HELM_RELEASE_HUB="qgis-mcp-bridge"

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
echo "▸ Ajout repo Helm Onyxia..."
helm repo add ide https://nexus.lab.sspcloud.fr/repository/inseefrlab-helm-charts-interactive-services --force-update 2>/dev/null
helm repo update ide 2>/dev/null

# 1. Hub QGIS/MCP (tools, études, publications)
echo "▸ Déploiement hub QGIS ($HELM_RELEASE_HUB)..."
helm upgrade --install "$HELM_RELEASE_HUB" ide/jupyter-python \
    --namespace "$NAMESPACE" \
    --set service.image.custom.enabled=true \
    --set "service.image.custom.version=$HUB_IMAGE" \
    --set "init.personalInit=$REPO/server_init.sh" \
    --set "networking.user.enabled=true" \
    --set "networking.user.ports[0]=8100" \
    --set "persistence.enabled=true" \
    --set "persistence.size=5Gi" \
    --set "global.suspend=false" \
    --set "extraEnvVars[0].name=SERVICE_NAME" \
    --set-string "extraEnvVars[0].value=qgis-mcp" \
    --set "extraEnvVars[1].name=SERVER_MODULE" \
    --set-string "extraEnvVars[1].value=hub.main:app" \
    --set "extraEnvVars[2].name=SERVER_PORT" \
    --set-string "extraEnvVars[2].value=8100" \
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
    --set "networking.user.enabled=true" \
    --set "networking.user.ports[0]=8100" \
    --set "persistence.enabled=true" \
    --set "persistence.size=5Gi" \
    --set "global.suspend=false" \
    --set "extraEnvVars[0].name=SERVICE_NAME" \
    --set-string "extraEnvVars[0].value=qgis-agent" \
    --set "extraEnvVars[1].name=SERVER_MODULE" \
    --set-string "extraEnvVars[1].value=agent.main:app" \
    --set "extraEnvVars[2].name=SERVER_PORT" \
    --set-string "extraEnvVars[2].value=8100" \
    --set "extraEnvVars[3].name=ONYXIA_USER" \
    --set-string "extraEnvVars[3].value=$USERNAME" \
    --set "extraEnvVars[4].name=SSPCLOUD_NAMESPACE" \
    --set-string "extraEnvVars[4].value=$NAMESPACE" \
    2>&1

echo ""
echo "⏳ Attente démarrage des services (~60s)..."
kubectl rollout status statefulset/${HELM_RELEASE_HUB}-jupyter-python \
    -n "$NAMESPACE" --timeout=120s 2>/dev/null || true
kubectl rollout status statefulset/${HELM_RELEASE_AGENT}-jupyter-python \
    -n "$NAMESPACE" --timeout=120s 2>/dev/null || true

# Récupérer l'URL réelle depuis l'ingress créé par le chart
AGENT_USER_HOST=$(kubectl get ingress -n "$NAMESPACE" \
    -l "app.kubernetes.io/instance=${HELM_RELEASE_AGENT}" \
    -o jsonpath='{.items[*].spec.rules[*].host}' 2>/dev/null | tr ' ' '\n' \
    | grep -v "^$" | tail -1)

if [ -n "$AGENT_USER_HOST" ]; then
    DESK_URL="https://${AGENT_USER_HOST}/desk"
else
    # Fallback : convention Onyxia userHostname
    DESK_URL="https://user-${USERNAME}-${HELM_RELEASE_AGENT}-user.user.lab.sspcloud.fr/desk"
fi

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
