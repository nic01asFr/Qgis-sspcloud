#!/bin/bash
# install.sh - Installe le service QGIS Hub dans ton espace SSPCloud.
#
# A executer depuis un terminal jupyter Onyxia (kubernetes.role: edit requis).
# Sprint Day 5 (2026-08-06) : chart Helm packageur qgis-hub 1.2.0+
# (3 pods : hub + agent + workspace). Zero admin pod requis.
#
# Usage :
#   curl -fsSL https://raw.githubusercontent.com/nic01asFr/Qgis-sspcloud/main/install.sh | bash

set -e

REPO="qgis-sspcloud"
CHART_URL="https://raw.githubusercontent.com/nic01asFr/Qgis-sspcloud/main/helm-repo"
RELEASE="qgis-hub"

# Detection namespace (KUBERNETES_NAMESPACE ou SA token file)
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

# Sprint Day 5 fix (2026-08-06) : detecte la SA du pod jupyter courant
# (qui a le ClusterRole `edit` provisionne par Onyxia). Le hub doit
# utiliser CETTE MEME SA pour pouvoir kubectl get/patch sts (scale
# workspace, patch env agent, create ingress novnc). L'user OIDC
# SSPCloud ne peut pas creer de RoleBinding, donc la SA custom
# qgis-hub reste sans droits -> boucle infinie "Bureau endormi".
SERVICE_ACCOUNT="${KUBERNETES_SERVICE_ACCOUNT:-}"
if [ -z "$SERVICE_ACCOUNT" ]; then
    # Fallback : deduire depuis le pod jupyter courant
    SERVICE_ACCOUNT=$(kubectl get pod "$(hostname)" -n "$NAMESPACE" \
        -o jsonpath='{.spec.serviceAccountName}' 2>/dev/null || echo "")
fi
if [ -z "$SERVICE_ACCOUNT" ]; then
    echo "AVERTISSEMENT : impossible de detecter la SA du pod jupyter."
    echo "  Le hub utilisera 'default' (kubectl scale workspace echouera Forbidden)."
    SERVICE_ACCOUNT="default"
fi

# Domaine SSPCloud standard (peut etre override via env K8S_DOMAIN)
K8S_DOMAIN="${K8S_DOMAIN:-user.lab.sspcloud.fr}"

# Helm config dans /home/onyxia/work (persistent, PVC)
export HELM_CONFIG_HOME="${HELM_CONFIG_HOME:-/home/onyxia/work/.helm-config}"
export HELM_CACHE_HOME="${HELM_CACHE_HOME:-/home/onyxia/work/.helm-cache}"
export HELM_DATA_HOME="${HELM_DATA_HOME:-/home/onyxia/work/.helm-data}"
mkdir -p "$HELM_CONFIG_HOME" "$HELM_CACHE_HOME" "$HELM_DATA_HOME"

echo ""
echo "+==============================================================+"
echo "|  Installation QGIS Hub - $USERNAME"
echo "|  Namespace : $NAMESPACE"
echo "|  Domaine   : $K8S_DOMAIN"
echo "|  SA (herite Onyxia edit) : $SERVICE_ACCOUNT"
echo "+==============================================================+"
echo ""

# Etape 1 : ajouter le repo Helm
echo "[1/5] Ajout du repo Helm qgis-sspcloud"
helm repo add "$REPO" "$CHART_URL" 2>&1 | tail -2
helm repo update "$REPO" 2>&1 | tail -2

# Etape 2 : generer values-user.yaml depuis env vars pod jupyter Onyxia
# (le chart values.schema.json a des x-onyxia.overwriteDefaultWith qui ne sont
# resolus QUE si l'install passe par l'UI Onyxia. En CLI direct, on doit
# les injecter manuellement depuis les env vars du pod jupyter courant.)
VALUES_FILE="/tmp/qgis-hub-values-$USERNAME.yaml"
echo ""
echo "[2/5] Generation values-user.yaml depuis env vars pod jupyter"
cat > "$VALUES_FILE" <<EOF
# Auto-genere par install.sh depuis env vars pod jupyter Onyxia
oidc:
  username: "$USERNAME"

# SA heritee du pod jupyter (ClusterRole edit provisionne Onyxia)
serviceAccount:
  name: "$SERVICE_ACCOUNT"

ingress:
  enabled: true
  hostname: "user-${USERNAME}-qgis.${K8S_DOMAIN}"
  ingressClassName: onyxia

agent:
  enabled: true
  ingress:
    enabled: true
    hostname: "user-${USERNAME}-qgis-agent.${K8S_DOMAIN}"

workspace:
  enabled: true

s3:
  enabled: true
  endpoint: "${AWS_S3_ENDPOINT:-minio.lab.sspcloud.fr}"
  defaultRegion: "${AWS_DEFAULT_REGION:-us-east-1}"
  accessKeyId: "${AWS_ACCESS_KEY_ID:-}"
  secretAccessKey: "${AWS_SECRET_ACCESS_KEY:-}"
  sessionToken: "${AWS_SESSION_TOKEN:-}"
  workingDirectoryPath: "${AWS_BUCKET_NAME:-$USERNAME}/qgis-hub/"

vault:
  enabled: true
  url: "${VAULT_ADDR:-https://vault.lab.sspcloud.fr}"
  token: "${VAULT_TOKEN:-}"
  mount: "${VAULT_MOUNT:-onyxia-kv}"
  directory: "${VAULT_TOP_DIR:-$USERNAME}"

git:
  enabled: true
  name: "${GIT_USER_NAME:-}"
  email: "${GIT_USER_MAIL:-}"
  token: "${GIT_PERSONAL_ACCESS_TOKEN:-}"
EOF
echo "  -> $VALUES_FILE"

# Etape 3 : install ou upgrade (idempotent)
echo ""
echo "[3/5] helm install/upgrade qgis-hub"
if helm list -n "$NAMESPACE" 2>/dev/null | grep -q "^$RELEASE\s"; then
    echo "  Release deja presente -> upgrade"
    helm upgrade "$RELEASE" "$REPO/qgis-hub" -n "$NAMESPACE" -f "$VALUES_FILE" 2>&1 | tail -3
else
    helm install "$RELEASE" "$REPO/qgis-hub" -n "$NAMESPACE" -f "$VALUES_FILE" 2>&1 | tail -3
fi

# Etape 4 : attente rollout
echo ""
echo "[4/5] Attente demarrage pods (~90s)"
for sts in qgis-hub qgis-agent qgis-workspace-"$USERNAME"; do
    kubectl rollout status "statefulset/$sts" -n "$NAMESPACE" --timeout=180s 2>/dev/null \
        && echo "  $sts ready" \
        || echo "  $sts pas encore ready (verifier kubectl get pods -n $NAMESPACE)"
done

# Etape 5 : recup credentials
echo ""
echo "[5/5] Ta cle personnelle HUB_API_KEY"
KEY=$(kubectl get secret qgis-hub-apikey -n "$NAMESPACE" -o jsonpath='{.data.HUB_API_KEY}' | base64 -d 2>/dev/null)
if [ -z "$KEY" ]; then
    echo "  (Secret qgis-hub-apikey pas encore genere. Relance le script dans 30s.)"
    exit 0
fi

HUB_URL="https://user-${USERNAME}-qgis.${K8S_DOMAIN}"
echo ""
echo "+==============================================================+"
echo "|  Installation terminee - tes acces"
echo "+==============================================================+"
echo "|"
echo "|  URL web (bookmark) :"
echo "|    $HUB_URL"
echo "|"
echo "|  Cle API personnelle (a coller dans /login) :"
echo "|    $KEY"
echo "|"
echo "|  Configuration MCP (Claude Desktop, Cursor, Cline) :"
echo "|    $HUB_URL/auth/apikey     (retourne JSON pret a coller)"
echo "|"
echo "|  Cle LLM (a saisir sur /workspace apres 1er login) :"
echo "|    https://llm.lab.sspcloud.fr    (onglet API keys)"
echo "|"
echo "+==============================================================+"
echo ""
