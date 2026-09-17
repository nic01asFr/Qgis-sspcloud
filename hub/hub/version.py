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
import urllib.request
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


def _empreintes_en_cours(namespace: str) -> dict[str, dict]:
    """Ce que chaque pod du service execute : empreinte et reference demandee.

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

    trouve: dict[str, dict] = {}
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
                trouve[composant] = {
                    "digest": "sha256:" + ref.split("sha256:")[-1],
                    # `image` porte le tag demande : c'est lui qui dit quel
                    # depot et quel tag interroger au registre.
                    "reference": etat.get("image", ""),
                }
                break
    return trouve


# ── Ce qui est publie, face a ce qui tourne ─────────────────────────────────
#
# Savoir ce qui s'execute ne suffit pas a repondre « suis-je a jour ? » : il
# faut aussi ce que le registre publie sous le tag demande. Les deux valeurs
# se comparent directement -- verifie le 2026-09-17 sur les trois briques :
# l'`imageID` rapporte par le kubelet est exactement le `Docker-Content-Digest`
# rendu par le registre.
#
# C'est ce qui permettra a l'application de signaler une mise a jour, et a
# l'utilisateur de la declencher : les trois briques sont en
# `imagePullPolicy: Always` sur un tag mobile, donc un simple redemarrage tire
# la nouvelle image, et les donnees vivent sur le PVC.

# Interroger le registre est plus lent et plus fragile que lire l'etat du
# cluster : on le garde plus longtemps, et un echec n'empeche jamais de rendre
# ce qu'on sait deja.
_TTL_REGISTRE_S = 300
_cache_publie: dict[str, tuple[float, str | None]] = {}


def _digest_publie(depot: str, tag: str) -> str | None:
    """Empreinte publiee sous ce tag, ou None si le registre n'a pas repondu.

    `depot` est de la forme « proprietaire/image ». Seul ghcr.io est
    interroge : c'est le registre des trois images du service, et il delivre
    un jeton anonyme pour une image publique.
    """
    clef = f"{depot}:{tag}"
    maintenant = time.time()
    fige = _cache_publie.get(clef)
    if fige and (maintenant - fige[0]) < _TTL_REGISTRE_S:
        return fige[1]

    digest = None
    try:
        jeton_url = (f"https://ghcr.io/token?scope=repository:{depot}:pull"
                     f"&service=ghcr.io")
        with urllib.request.urlopen(jeton_url, timeout=8) as r:
            jeton = json.loads(r.read()).get("token", "")
        requete = urllib.request.Request(
            f"https://ghcr.io/v2/{depot}/manifests/{tag}",
            method="HEAD",
            headers={
                "Authorization": f"Bearer {jeton}",
                "Accept": ", ".join((
                    "application/vnd.oci.image.index.v1+json",
                    "application/vnd.docker.distribution.manifest.list.v2+json",
                    "application/vnd.oci.image.manifest.v1+json",
                    "application/vnd.docker.distribution.manifest.v2+json",
                )),
            },
        )
        with urllib.request.urlopen(requete, timeout=8) as r:
            digest = r.headers.get("Docker-Content-Digest")
    except Exception as exc:
        log.debug("version : empreinte publiee de %s indisponible : %s", clef, exc)

    _cache_publie[clef] = (maintenant, digest)
    return digest


def _depot_et_tag(reference: str) -> tuple[str, str] | None:
    """« ghcr.io/proprio/image:tag » -> (« proprio/image », « tag »).

    Rend None pour tout autre registre : on ne sait interroger que celui-la,
    et deviner serait pire que se taire.
    """
    if not reference or not reference.startswith("ghcr.io/"):
        return None
    reste = reference[len("ghcr.io/"):]
    if "@" in reste:                      # reference deja par empreinte
        reste = reste.split("@", 1)[0]
    depot, _, tag = reste.partition(":")
    if not depot or "/" not in depot:
        return None
    return depot, (tag or "latest")


def _etat_des_briques(pods: dict[str, dict]) -> dict[str, Any]:
    """Pour chaque brique : ce qui tourne, ce qui est publie, et l'ecart.

    `a_jour` vaut None quand la question n'a pas de reponse -- brique en
    veille, registre injoignable, image hors ghcr.io. Un inconnu n'est pas un
    « a jour » : c'est precisement la confusion qu'on veut eviter.
    """
    briques: dict[str, Any] = {}
    for composant, etat_pod in pods.items():
        en_cours = etat_pod.get("digest")
        reference = etat_pod.get("reference") or ""
        coordonnees = _depot_et_tag(reference)
        publie = _digest_publie(*coordonnees) if coordonnees else None

        brique: dict[str, Any] = {
            "en_cours": en_cours,
            "publie": publie,
            "a_jour": (en_cours == publie) if (en_cours and publie) else None,
        }
        if reference:
            brique["image"] = reference
        if en_cours is None:
            brique["note"] = ("en veille ou absente — le pod est mis a zero "
                              "replique apres inactivite")
        elif publie is None:
            brique["note"] = "registre injoignable — l'ecart n'est pas mesurable"
        briques[composant] = brique
    return briques


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
    pods = _empreintes_en_cours(ns)

    # Le workspace dort apres deux heures sans usage : son pod disparait, et
    # son empreinte avec. L'absence ici ne veut donc pas dire « pas
    # deploye » -- on distingue les deux plutot que de laisser conclure.
    for composant in ("hub", "agent", "workspace"):
        pods.setdefault(composant, {"digest": None, "reference": ""})

    # `images` reste ce qu'il a toujours ete -- une empreinte par brique --
    # pour ne rien casser chez ses lecteurs. `briques` y ajoute la comparaison
    # avec ce que le registre publie.
    images = {c: e.get("digest") for c, e in pods.items()}
    briques = _etat_des_briques(pods)

    data = {
        "commit": os.getenv("HUB_GIT_SHA") or None,
        "chart": os.getenv("HUB_CHART_VERSION") or None,
        "namespace": ns or None,
        "images": images,
        "briques": briques,
        # Ce que l'application a besoin de savoir en un coup d'oeil pour
        # decider d'afficher, ou non, une proposition de mise a jour.
        "mise_a_jour_disponible": sorted(
            c for c, b in briques.items() if b.get("a_jour") is False
        ),
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
