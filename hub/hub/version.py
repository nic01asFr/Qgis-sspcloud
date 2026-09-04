"""Ce qui tourne, exactement — pour que « suis-je a jour ? » ait une reponse.

Le service est fait de trois images construites par deux depots. Jusqu'ici
rien ne disait laquelle de leurs versions s'executait : la procedure de mise
a jour demandait de « verifier que /version retourne le nouveau commit »,
alors que cette route n'existait pas. L'etape etait irrealisable depuis
toujours, et personne ne pouvait s'en apercevoir -- une verification qu'on ne
peut pas faire ne rate jamais.

Trois choses, et elles ne viennent pas du meme endroit :

  commit   le SHA d'ou l'image du hub a ete construite, injecte au build
           (`ARG GIT_SHA` -> `HUB_GIT_SHA`). Absent si l'image a ete
           construite a la main sans cet argument : on le dit, on ne devine
           pas.
  chart    la version du chart Helm qui a pose ce deploiement, injectee par
           le chart lui-meme. C'est elle qui identifiera le produit quand les
           empreintes d'images y seront figees.
  images   les empreintes REELLEMENT en cours, relevees aupres de Kubernetes
           -- pas celles que les valeurs demandent. C'est la distinction qui
           compte : un tag mobile peut designer autre chose que ce que le
           noeud a en cache, et c'est precisement ce que les deux CI
           contournent en poussant `:main` a cote de `:latest`.

Public a dessein : une empreinte d'image publique ne revele rien, et un
etat de version qui demande une authentification n'est pas consultable par
ce qui en aurait besoin -- une supervision, un collegue qui doute, soi-meme
depuis un autre poste.
"""

from __future__ import annotations

import json
import logging
import os
import subprocess
import time
from typing import Any

log = logging.getLogger(__name__)

# Le releve interroge l'API Kubernetes ; la route est publique. Sans cache, la
# rafraichir en boucle ferait porter cette charge au cluster.
_TTL_S = 60
_cache: dict[str, Any] = {"t": 0.0, "data": None}

# Nom du pod -> composant. Le workspace porte le nom de l'utilisateur, on le
# reconnait par prefixe.
_COMPOSANTS = (
    ("qgis-hub-0", "hub"),
    ("qgis-agent-0", "agent"),
)


def _namespace() -> str:
    """Le namespace du pod, ou vide si on ne tourne pas dans un cluster."""
    depuis_env = os.getenv("KUBERNETES_NAMESPACE", "")
    if depuis_env:
        return depuis_env
    try:
        with open("/var/run/secrets/kubernetes.io/serviceaccount/namespace") as f:
            return f.read().strip()
    except OSError:
        return ""


def _empreintes_en_cours(namespace: str) -> dict[str, str]:
    """Les empreintes des images des pods du service, telles que le kubelet
    les rapporte.

    `imageID` porte l'empreinte du manifeste effectivement tire, la ou
    `image` ne porte que le tag demande. Rendre le tag serait rendre une
    intention ; on rend ce qui s'execute.
    """
    if not namespace:
        return {}
    try:
        r = subprocess.run(
            ["kubectl", "get", "pods", "-n", namespace, "-o", "json"],
            capture_output=True, text=True, timeout=15,
        )
        if r.returncode != 0:
            log.debug("version : kubectl get pods a echoue : %s", r.stderr[:200])
            return {}
        pods = json.loads(r.stdout).get("items", [])
    except Exception as exc:
        log.debug("version : releve des empreintes impossible : %s", exc)
        return {}

    trouve: dict[str, str] = {}
    for pod in pods:
        nom = pod.get("metadata", {}).get("name", "")
        composant = next((c for p, c in _COMPOSANTS if nom == p), None)
        if composant is None and nom.startswith("qgis-workspace-"):
            composant = "workspace"
        if composant is None:
            continue
        for etat in pod.get("status", {}).get("containerStatuses") or []:
            ref = etat.get("imageID", "")
            if "sha256:" in ref:
                trouve[composant] = "sha256:" + ref.split("sha256:")[-1]
                break
    return trouve


def etat() -> dict[str, Any]:
    """Version du hub, du chart, et empreintes des images en cours.

    Ne leve jamais : un service qui refuse de dire sa version parce qu'il n'a
    pas pu joindre l'API Kubernetes est moins utile qu'un service qui dit ce
    qu'il sait et signale ce qu'il ignore.
    """
    maintenant = time.time()
    if _cache["data"] is not None and (maintenant - _cache["t"]) < _TTL_S:
        return _cache["data"]

    ns = _namespace()
    images = _empreintes_en_cours(ns)

    # Le workspace dort apres deux heures sans usage : son pod disparait, et
    # son empreinte avec. L'absence ici ne veut donc pas dire « pas
    # deploye » -- on distingue les deux plutot que de laisser conclure.
    if "workspace" not in images:
        images["workspace"] = None

    data = {
        "commit": os.getenv("HUB_GIT_SHA") or None,
        "chart": os.getenv("HUB_CHART_VERSION") or None,
        "namespace": ns or None,
        "images": images,
        "notes": {
            "workspace": ("en veille ou absent — le pod est mis a zero replique "
                          "apres inactivite, son empreinte n'est alors pas lisible")
            if images.get("workspace") is None else None,
            "commit": ("inconnu — image construite sans l'argument GIT_SHA")
            if not os.getenv("HUB_GIT_SHA") else None,
            "chart": ("inconnu — deploiement pose autrement que par le chart")
            if not os.getenv("HUB_CHART_VERSION") else None,
        },
    }
    data["notes"] = {k: v for k, v in data["notes"].items() if v}
    if not data["notes"]:
        data.pop("notes")

    _cache["data"], _cache["t"] = data, maintenant
    return data
