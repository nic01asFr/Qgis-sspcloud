#!/bin/bash
# server_init.sh — init générique pour les pods QGIS Agent et Hub.
# Le code est dans l'image Docker — aucun téléchargement de packages.
# Variables requises (via helm extraEnvVars) :
#   SERVICE_NAME    : qgis-agent | qgis-mcp
#   SERVER_MODULE   : agent.main:app | hub.main:app
#   SERVER_PORT     : 8100 (défaut)

set -e

SERVICE_NAME="${SERVICE_NAME:-service}"
SERVER_MODULE="${SERVER_MODULE:-}"
SERVER_PORT="${SERVER_PORT:-8100}"
ONYXIA_USER="${ONYXIA_USER:-}"

echo "[init] Service: $SERVICE_NAME | Module: $SERVER_MODULE | Port: $SERVER_PORT"

# Persistance sur PVC si disponible
PERSIST_ROOT="/home/onyxia/work/${SERVICE_NAME}"
if [ -d "/home/onyxia/work" ] && [ -w "/home/onyxia/work" ]; then
    mkdir -p "$PERSIST_ROOT/server-data"
    ln -sfn "$PERSIST_ROOT" "/tmp/${SERVICE_NAME}"
    echo "[init] PVC : /tmp/${SERVICE_NAME} → $PERSIST_ROOT"
else
    mkdir -p "/tmp/${SERVICE_NAME}/server-data"
fi

# Indexer les tips KB si agent
if [ "$SERVICE_NAME" = "qgis-agent" ]; then
    python -m agent.tips.indexer 2>/dev/null &
fi

# Démarrer le serveur
# Si SERVER_MODULE est vide, l'image a son propre entrypoint (ex: qgis-workspace)
if [ -z "$SERVER_MODULE" ]; then
    echo "[init] SERVER_MODULE vide — l'image gère son propre démarrage."
    exit 0
fi

echo "[init] Démarrage uvicorn $SERVER_MODULE sur :$SERVER_PORT"
exec uvicorn "$SERVER_MODULE" \
    --host 0.0.0.0 \
    --port "$SERVER_PORT" \
    --log-level info
