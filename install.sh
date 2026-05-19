#!/bin/bash
# install.sh — Installe le service QGIS Agent dans ton espace SSPCloud.
# À exécuter depuis un terminal dans un service Onyxia (kubernetes.role: edit requis).
#
# Usage :
#   curl -fsSL https://raw.githubusercontent.com/nic01asFr/Qgis-sspcloud/main/install.sh | bash

set -e

REPO="https://raw.githubusercontent.com/nic01asFr/Qgis-sspcloud/main"
HUB_IMAGE="ghcr.io/nic01asfr/qgis-hub:latest"
AGENT_IMAGE="ghcr.io/nic01asfr/qgis-agent:latest"

SA_NS_FILE="/var/run/secrets/kubernetes.io/serviceaccount/namespace"
NAMESPACE="${KUBERNETES_NAMESPACE:-}"
if [ -z "$NAMESPACE" ] && [ -f "$SA_NS_FILE" ]; then
    NAMESPACE=$(cat "$SA_NS_FILE")
fi
USERNAME="${ONYXIA_USER:-${NAMESPACE#user-}}"

if [ -z "$USERNAME" ] || [ -z "$NAMESPACE" ]; then
    echo "ERREUR : impossible de détecter le namespace."
    exit 1
fi

echo ""
echo "╔══════════════════════════════════════════════════════╗"
echo "║   Installation QGIS Agent — $USERNAME"
echo "╚══════════════════════════════════════════════════════╝"
echo ""

_apply() {
    # Écrit un manifest YAML dans un fichier tmp et l'applique
    local name="$1"; shift
    local tmp; tmp=$(mktemp /tmp/manifest-XXXXXX.yaml)
    python3 - "$tmp" "$@" <<'PYEOF'
import sys, os
dest = sys.argv[1]
data = os.environ.get('MANIFEST_YAML', '')
open(dest, 'w').write(data)
PYEOF
    MANIFEST_YAML="$1" python3 -c "
import sys, os
open(sys.argv[1], 'w').write(os.environ['MANIFEST_YAML'])
" "$tmp"
    kubectl apply -f "$tmp" -n "$NAMESPACE" 2>&1
    rm -f "$tmp"
}

# Fonction pour appliquer un manifest YAML passé via stdin (heredoc dans un pipe)
_kubectl_apply() {
    python3 -c "
import sys, subprocess
data = sys.stdin.read()
r = subprocess.run(['kubectl', 'apply', '-f', '-', '-n', '$NAMESPACE'],
    input=data, capture_output=False, text=True)
"
}

echo "▸ Déploiement hub QGIS (qgis-mcp-bridge)..."
python3 - <<PYEOF
import subprocess, os

ns = '$NAMESPACE'
username = '$USERNAME'
hub_image = '$HUB_IMAGE'
repo = '$REPO'
name = 'qgis-mcp-bridge'
host = f'user-{username}-{name}-0.user.lab.sspcloud.fr'

manifest = f"""apiVersion: v1
kind: ServiceAccount
metadata:
  name: {name}
  namespace: {ns}
---
apiVersion: apps/v1
kind: StatefulSet
metadata:
  name: {name}
  namespace: {ns}
  labels:
    app: {name}
spec:
  serviceName: {name}
  replicas: 1
  selector:
    matchLabels:
      app: {name}
  template:
    metadata:
      labels:
        app: {name}
    spec:
      serviceAccountName: {name}
      containers:
      - name: hub
        image: {hub_image}
        imagePullPolicy: Always
        command: ["/bin/bash", "-c"]
        args: ["curl -fsSL {repo}/server_init.sh | bash"]
        ports:
        - containerPort: 8888
        env:
        - name: SERVICE_NAME
          value: qgis-mcp
        - name: SERVER_MODULE
          value: hub.main:app
        - name: SERVER_PORT
          value: "8888"
        - name: ONYXIA_USER
          value: "{username}"
        - name: SSPCLOUD_NAMESPACE
          value: "{ns}"
        readinessProbe:
          httpGet:
            path: /
            port: 8888
          initialDelaySeconds: 30
          periodSeconds: 10
          failureThreshold: 30
---
apiVersion: v1
kind: Service
metadata:
  name: {name}
  namespace: {ns}
  labels:
    app: {name}
spec:
  selector:
    app: {name}
  ports:
  - port: 8888
    targetPort: 8888
    name: http
  clusterIP: None
---
apiVersion: v1
kind: Service
metadata:
  name: {name}-svc
  namespace: {ns}
spec:
  selector:
    app: {name}
  ports:
  - port: 8888
    targetPort: 8888
---
apiVersion: networking.k8s.io/v1
kind: Ingress
metadata:
  name: {name}
  namespace: {ns}
  annotations:
    kubernetes.io/ingress.class: onyxia
spec:
  ingressClassName: onyxia
  rules:
  - host: {host}
    http:
      paths:
      - path: /
        pathType: Prefix
        backend:
          service:
            name: {name}-svc
            port:
              number: 8888
"""

r = subprocess.run(['kubectl', 'apply', '-f', '-', '-n', ns],
    input=manifest, capture_output=True, text=True)
print(r.stdout or r.stderr)
PYEOF

echo "▸ Déploiement agent IA (qgis-agent)..."
python3 - <<PYEOF
import subprocess, os

ns = '$NAMESPACE'
username = '$USERNAME'
agent_image = '$AGENT_IMAGE'
repo = '$REPO'
name = 'qgis-agent'
host = f'user-{username}-{name}-0.user.lab.sspcloud.fr'

manifest = f"""apiVersion: v1
kind: ServiceAccount
metadata:
  name: {name}
  namespace: {ns}
---
apiVersion: apps/v1
kind: StatefulSet
metadata:
  name: {name}
  namespace: {ns}
  labels:
    app: {name}
spec:
  serviceName: {name}
  replicas: 1
  selector:
    matchLabels:
      app: {name}
  template:
    metadata:
      labels:
        app: {name}
    spec:
      serviceAccountName: {name}
      containers:
      - name: agent
        image: {agent_image}
        imagePullPolicy: Always
        command: ["/bin/bash", "-c"]
        args: ["curl -fsSL {repo}/server_init.sh | bash"]
        ports:
        - containerPort: 8888
        env:
        - name: SERVICE_NAME
          value: qgis-agent
        - name: SERVER_MODULE
          value: agent.main:app
        - name: SERVER_PORT
          value: "8888"
        - name: ONYXIA_USER
          value: "{username}"
        - name: SSPCLOUD_NAMESPACE
          value: "{ns}"
        readinessProbe:
          httpGet:
            path: /
            port: 8888
          initialDelaySeconds: 30
          periodSeconds: 10
          failureThreshold: 30
---
apiVersion: v1
kind: Service
metadata:
  name: {name}
  namespace: {ns}
spec:
  selector:
    app: {name}
  ports:
  - port: 8888
    targetPort: 8888
    name: http
  clusterIP: None
---
apiVersion: v1
kind: Service
metadata:
  name: {name}-svc
  namespace: {ns}
spec:
  selector:
    app: {name}
  ports:
  - port: 8888
    targetPort: 8888
---
apiVersion: networking.k8s.io/v1
kind: Ingress
metadata:
  name: {name}
  namespace: {ns}
spec:
  ingressClassName: onyxia
  rules:
  - host: {host}
    http:
      paths:
      - path: /
        pathType: Prefix
        backend:
          service:
            name: {name}-svc
            port:
              number: 8888
"""

r = subprocess.run(['kubectl', 'apply', '-f', '-', '-n', ns],
    input=manifest, capture_output=True, text=True)
print(r.stdout or r.stderr)
PYEOF

echo ""
echo "⏳ Attente démarrage hub + agent (~90s)..."
kubectl rollout status statefulset/qgis-mcp-bridge -n "$NAMESPACE" --timeout=120s 2>/dev/null || true
kubectl rollout status statefulset/qgis-agent -n "$NAMESPACE" --timeout=120s 2>/dev/null || true

DESK_URL="https://user-${USERNAME}-qgis-mcp-bridge-0.user.lab.sspcloud.fr/desk"

echo ""
echo "╔══════════════════════════════════════════════════════╗"
echo "║   ✓ Installation terminée !"
echo "║"
echo "║   Ton bureau de travail QGIS :"
echo "║   $DESK_URL"
echo "║"
echo "║   Bookmarke ce lien — c'est ton espace personnel."
echo "╚══════════════════════════════════════════════════════╝"
echo ""
