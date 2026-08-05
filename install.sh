#!/bin/bash
# install.sh — Installe le service QGIS Hub dans ton espace SSPCloud.
#
# A executer depuis un terminal jupyter Onyxia (kubernetes.role: edit requis).
# Nouveau flow Day 5 (2026-08-05) : chart Helm packageur qgis-hub (3 pods).
#
# Usage :
#   curl -fsSL https://raw.githubusercontent.com/nic01asFr/Qgis-sspcloud/main/install.sh | bash
#
# Zero admin pod requis. Le user est autonome de bout en bout.

set -e

REPO="qgis-sspcloud"
CHART_URL="https://raw.githubusercontent.com/nic01asFr/Qgis-sspcloud/main/helm-repo"
RELEASE="qgis-hub"

# Detection namespace (POD_NAMESPACE ou SA token)
SA_NS_FILE="/var/run/secrets/kubernetes.io/serviceaccount/namespace"
NAMESPACE="${KUBERNETES_NAMESPACE:-}"
if [ -z "$NAMESPACE" ] && [ -f "$SA_NS_FILE" ]; then
    NAMESPACE=$(cat "$SA_NS_FILE")
fi
USERNAME="${ONYXIA_USER:-${NAMESPACE#user-}}"
if [ -z "$USERNAME" ] || [ -z "$NAMESPACE" ]; then
    echo "ERREUR : impossible de detecter le namespace SSPCloud."
    echo "Lance ce script depuis un terminal jupyter Onyxia."
    exit 1
fi

# Helm config dans /home/onyxia/work (persistent, PVC)
export HELM_CONFIG_HOME="${HELM_CONFIG_HOME:-/home/onyxia/work/.helm-config}"
export HELM_CACHE_HOME="${HELM_CACHE_HOME:-/home/onyxia/work/.helm-cache}"
export HELM_DATA_HOME="${HELM_DATA_HOME:-/home/onyxia/work/.helm-data}"
mkdir -p "$HELM_CONFIG_HOME" "$HELM_CACHE_HOME" "$HELM_DATA_HOME"

echo ""
echo "╔══════════════════════════════════════════════════════════════╗"
echo "║  Installation QGIS Hub — $USERNAME"
echo "║  Namespace : $NAMESPACE"
echo "╚══════════════════════════════════════════════════════════════╝"
echo ""

# Etape 1 : ajouter le repo Helm
echo "▸ Etape 1/4 : ajout du repo Helm qgis-sspcloud"
helm repo add "$REPO" "$CHART_URL" 2>&1 | tail -2
helm repo update "$REPO" 2>&1 | tail -2

# Etape 2 : install ou upgrade (idempotent)
echo ""
echo "▸ Etape 2/4 : helm install qgis-hub (chart derniere version)"
if helm list -n "$NAMESPACE" | grep -q "^$RELEASE\s"; then
    echo "  Release deja presente -> upgrade"
    helm upgrade "$RELEASE" "$REPO/qgis-hub" -n "$NAMESPACE" --reuse-values 2>&1 | tail -3
else
    helm install "$RELEASE" "$REPO/qgis-hub" -n "$NAMESPACE" 2>&1 | tail -3
fi

# Etape 3 : attente rollout
echo ""
echo "▸ Etape 3/4 : attente demarrage pods (~90s)"
for sts in qgis-hub qgis-agent qgis-workspace-"$USERNAME"; do
    kubectl rollout status "statefulset/$sts" -n "$NAMESPACE" --timeout=180s 2>/dev/null \
        && echo "  $sts ready" \
        || echo "  $sts pas encore ready (verifier kubectl get pods -n $NAMESPACE)"
done

# Etape 4 : recup credentials
echo ""
echo "▸ Etape 4/4 : ta cle personnelle HUB_API_KEY"
KEY=$(kubectl get secret qgis-hub-apikey -n "$NAMESPACE" -o jsonpath='{.data.HUB_API_KEY}' | base64 -d 2>/dev/null)
if [ -z "$KEY" ]; then
    echo "  (Secret qgis-hub-apikey pas encore genere. Relance le script dans 30s.)"
else
    HUB_URL="https://user-${USERNAME}-qgis.user.lab.sspcloud.fr"
    echo ""
    echo "╔══════════════════════════════════════════════════════════════╗"
    echo "║  Installation terminee — tes acces"
    echo "╠══════════════════════════════════════════════════════════════╣"
    echo "║  URL web (bookmark) :"
    echo "║    $HUB_URL"
    echo "║"
    echo "║  Cle API personnelle (MCP + auth cookie) :"
    echo "║    $KEY"
    echo "║"
    echo "║  Configuration MCP (Claude Desktop, Cursor, Cline) :"
    echo "║    $HUB_URL/auth/apikey  (retourne JSON pret a coller)"
    echo "║"
    echo "║  Cle LLM (a saisir sur /workspace apres login) :"
    echo "║    https://llm.lab.sspcloud.fr > API keys"
    echo "╚══════════════════════════════════════════════════════════════╝"
    echo ""
fi
