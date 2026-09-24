"""Quel code s'execute vraiment -- pas seulement de quelle image il vient.

Constat du 2026-09-24 (strategie qualite, §3.6) : en production, le hub
importe son paquet depuis un overlay pose sur le PVC
(`PYTHONPATH=/home/onyxia/work/qgis-hub-live`), qui a diverge de l'image.
Fusionner dans `main` et reconstruire l'image ne mettait donc rien a jour,
et `/version` continuait d'annoncer le commit et l'empreinte de l'image :
une reponse exacte a une question qui n'etait plus la bonne.

Meme module que `agent/agent/empreinte_code.py`, a dessein : les deux
images ne partagent aucun paquet, et l'empreinte doit se calculer de la
meme facon des deux cotes pour se lire de la meme facon.

Ce module releve, une fois au demarrage :

  chemin           le dossier du paquet effectivement importe ;
  empreinte        sha256 court du contenu de ses .py (chemins relatifs et
                   contenus, tries) ;
  commit_image     le commit d'ou l'image a ete construite (`GIT_SHA` au
                   build) -- celui de l'image, pas forcement celui du code
                   charge, d'ou le nom ;
  overlay          vrai si le paquet importe n'est pas celui installe dans
                   l'image. Inconnu (None) hors image : on le dit plutot
                   que de repondre « non » par defaut ;
  aligne           vrai si le code qui s'execute est celui de l'image :
                   pas d'overlay, ou un overlay identique a l'image (alors
                   `empreinte_image` est rendue aussi). Faux est exactement
                   le defaut constate. None hors image.

Les fins de ligne sont normalisees avant hachage : un fichier depose en
CRLF depuis un poste Windows reste le meme code, et ne doit pas faire
crier a la divergence.
"""

from __future__ import annotations

import hashlib
import logging
from pathlib import Path

log = logging.getLogger(__name__)

# 12 caracteres hexadecimaux : assez pour distinguer deux etats du code,
# assez court pour se lire et se comparer a l'oeil dans `/health`.
_LONGUEUR = 12


def empreinte(dossier: Path) -> str:
    """sha256 court du code Python d'un paquet : chemins relatifs et contenus.

    Le chemin relatif entre dans le hachage : renommer ou deplacer un module
    change le code importable, meme si les octets sont les memes.
    """
    dossier = Path(dossier)
    fichiers = sorted(
        (f for f in dossier.rglob("*.py") if "__pycache__" not in f.parts),
        key=lambda f: f.relative_to(dossier).as_posix(),
    )
    h = hashlib.sha256()
    for f in fichiers:
        h.update(f.relative_to(dossier).as_posix().encode("utf-8"))
        h.update(b"\0")
        h.update(f.read_bytes().replace(b"\r\n", b"\n"))
        h.update(b"\0")
    return h.hexdigest()[:_LONGUEUR]


def _meme_dossier(a: Path, b: Path) -> bool:
    try:
        return a.resolve() == b.resolve()
    except OSError:
        return False


def releve(paquet: Path, dossier_image: str | None, commit: str | None) -> dict:
    """Etat du code charge. Ne leve jamais : `/health` doit toujours repondre.

    `dossier_image` est le chemin du paquet tel qu'installe dans l'image
    (pose par le Dockerfile) ; vide ou absent hors image.
    """
    paquet = Path(paquet)
    etat: dict = {"chemin": str(paquet), "commit_image": commit or None}
    try:
        etat["empreinte"] = empreinte(paquet)
    except Exception as exc:
        # Un /health qui plante pour une empreinte ferait tomber la sonde de
        # disponibilite, donc le pod : on rend l'erreur, pas une exception.
        log.warning("Empreinte du code impossible : %s", exc)
        etat["empreinte"] = None
        etat["erreur"] = f"{type(exc).__name__}: {exc}"

    if not dossier_image:
        etat["overlay"] = None
        etat["aligne"] = None
        etat["note"] = "chemin d'installation de l'image inconnu (hors image ?)"
        return etat

    image = Path(dossier_image)
    etat["overlay"] = not _meme_dossier(paquet, image)
    # `aligne` est LE booleen a verifier au deploiement (§6) : vrai si le
    # code qui s'execute est celui de l'image, quelle que soit la raison.
    etat["aligne"] = True
    if etat["overlay"]:
        try:
            etat["empreinte_image"] = empreinte(image) if image.is_dir() else None
        except Exception as exc:
            log.warning("Empreinte du code de l'image impossible : %s", exc)
            etat["empreinte_image"] = None
        etat["aligne"] = (
            etat["empreinte_image"] is not None
            and etat["empreinte_image"] == etat.get("empreinte")
        )
        if not etat["aligne"]:
            log.warning(
                "Code charge depuis un overlay (%s, empreinte %s) different de "
                "l'image (%s, empreinte %s) : le commit annonce ne decrit pas "
                "le code qui s'execute.",
                paquet, etat.get("empreinte"), image, etat["empreinte_image"],
            )
    return etat
