#!/bin/bash
# start_geoai_image.sh — Boot GeoAI GPU depuis l'image optimisée.
#
# Ce script est déclenché via PERSONAL_INIT_SCRIPT par onyxia-init.sh.
# Il DOIT rendre la main rapidement (exit 0) pour que JupyterLab démarre.
# Le serveur geoai_server tourne en arrière-plan indépendamment.

set -e

GEOAI_MODELS="${GEOAI_MODELS:-sam3,deepforest,omniwater}"
GEOAI_MODELS_DIR="${GEOAI_MODELS_DIR:-/home/onyxia/work/geoai/models}"
GEOAI_SERVER_PORT="${GEOAI_SERVER_PORT:-8000}"
PYTHON="$(which python3)"

echo "[geoai] ═══════════════════════════════════════════════"
echo "[geoai] GeoAI GPU — image ghcr.io/nic01asfr/geoai-gpu"
echo "[geoai] Modèles : $GEOAI_MODELS"
echo "[geoai] ═══════════════════════════════════════════════"

# ── 1. Répertoires PVC ─────────────────────────────────────────────────────────
mkdir -p "$GEOAI_MODELS_DIR/huggingface"
mkdir -p "/home/onyxia/work/geoai/server-data"

# ── 2. Variables d'environnement ───────────────────────────────────────────────
export HF_HOME="$GEOAI_MODELS_DIR/huggingface"
export TORCH_HOME="$GEOAI_MODELS_DIR/torch"
export SAMGEO3_BACKEND="${SAMGEO3_BACKEND:-transformers}"
export GEOAI_MODELS_DIR="$GEOAI_MODELS_DIR"
# DeepForest : poids dans l'image — pas de téléchargement PVC
export DEEPFOREST_CACHE=/opt/geoai/models/deepforest
export SAM2_CONFIG_DIR=/opt/geoai/models/sam2_config

# ── 3. Vérification GPU ────────────────────────────────────────────────────────
GPU=$("$PYTHON" -c "import torch; print(torch.cuda.is_available())" 2>/dev/null || echo "False")
if [ "$GPU" = "True" ]; then
    GPU_NAME=$("$PYTHON" -c "import torch; print(torch.cuda.get_device_name(0))" 2>/dev/null || echo "?")
    echo "[geoai] GPU : $GPU_NAME ✓"
else
    echo "[geoai] AVERTISSEMENT : CUDA non disponible — mode CPU"
fi

# ── 4. Téléchargement SAM2 sur PVC si absent (1er boot ~5min) ─────────────────
if [[ "$GEOAI_MODELS" == *"sam"* ]]; then
    SAM2_MARKER="$GEOAI_MODELS_DIR/huggingface/.sam2_downloaded"
    if [ ! -f "$SAM2_MARKER" ]; then
        echo "[geoai] 1er boot — téléchargement SAM2 (~2.4GB en arrière-plan)..."
        # Lancement en background pour ne pas bloquer JupyterLab
        (
            "$PYTHON" -c "
import os, warnings
warnings.filterwarnings('ignore')
os.environ['HF_HOME'] = '$GEOAI_MODELS_DIR/huggingface'
from transformers import AutoProcessor, AutoModelForMaskGeneration
AutoProcessor.from_pretrained('facebook/sam2-hiera-large')
AutoModelForMaskGeneration.from_pretrained('facebook/sam2-hiera-large')
open('$SAM2_MARKER', 'w').close()
print('[geoai] SAM2 téléchargé sur PVC')
" >> /tmp/geoai_dl.log 2>&1
        ) &
        echo "[geoai] Download SAM2 en cours (PID $!) — log: /tmp/geoai_dl.log"
    else
        echo "[geoai] SAM2 en cache PVC ✓"
    fi

    # SAM3 (facebook/sam3, 0.9B, gated) — uniquement si HF_TOKEN présent
    # SAM3 via transformers.Sam3Model directement (text prompts natifs)
    if [ -n "$HF_TOKEN" ]; then
        SAM3_MARKER="$GEOAI_MODELS_DIR/huggingface/.sam3_downloaded"
        if [ ! -f "$SAM3_MARKER" ]; then
            echo "[geoai] HF_TOKEN présent — téléchargement SAM3 (~3.6GB) en arrière-plan..."
            export HUGGING_FACE_HUB_TOKEN="$HF_TOKEN"
            (
                "$PYTHON" -c "
import os, warnings
warnings.filterwarnings('ignore')
os.environ['HF_HOME'] = '$GEOAI_MODELS_DIR/huggingface'
os.environ['HUGGING_FACE_HUB_TOKEN'] = '$HF_TOKEN'
try:
    from transformers import Sam3Processor, Sam3Model
    print('[geoai] SAM3 processor...')
    Sam3Processor.from_pretrained('facebook/sam3')
    print('[geoai] SAM3 poids (~3.6GB)...')
    Sam3Model.from_pretrained('facebook/sam3')
    open('$SAM3_MARKER', 'w').close()
    print('[geoai] SAM3 téléchargé sur PVC')
except Exception as e:
    print('[geoai] SAM3 erreur:', e)
" >> /tmp/geoai_dl.log 2>&1
            ) &
            echo "[geoai] Download SAM3 en cours (log: /tmp/geoai_dl.log)"
        else
            echo "[geoai] SAM3 en cache PVC ✓"
        fi
    else
        echo "[geoai] SAM3 non activé (HF_TOKEN absent) — SAM2 utilisé"
    fi
fi

# ── 5. Démarrage serveur geoai_server en arrière-plan ─────────────────────────
echo "[geoai] Démarrage geoai_server sur :$GEOAI_SERVER_PORT"

nohup "$PYTHON" -m uvicorn geoai_server.main:app \
    --host 0.0.0.0 \
    --port "$GEOAI_SERVER_PORT" \
    --log-level warning \
    > /tmp/geoai_server.log 2>&1 &

echo "[geoai] Serveur PID : $!"
echo "[geoai] Log serveur : /tmp/geoai_server.log"
echo "[geoai] Initialisation terminée — JupyterLab peut démarrer"

# Rendre la main immédiatement (personalInit ne doit pas bloquer)
exit 0
