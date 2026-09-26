"""Quel code s'execute vraiment -- pas seulement de quelle image il vient.

Constat du 2026-09-24 (strategie qualite, §3.6) : en production, l'agent
importe son paquet depuis un overlay pose sur le PVC
(`PYTHONPATH=/data/qgis-agent-live`), qui a diverge de l'image. Fusionner
dans `main` et reconstruire l'image ne mettait donc rien a jour, et
`/api/version` continuait d'annoncer le commit de l'image : une reponse
exacte a une question qui n'etait plus la bonne.

Ce module releve, une fois au demarrage :

  chemin           le dossier du paquet effectivement importe ;
  empreinte        sha256 court de ce qui est servi : les .py du paquet,
                   ses statiques (`static/`), les gabarits et statiques
                   poses a cote (`../templates`, `../static`) ; chemins
                   relatifs et contenus, tries ;
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

Pourquoi les gabarits et les statiques (mesure du 2026-09-26, apres le lot
qualite 2) : les overlays de production contiennent aussi `templates/`
(chat.html, desk.html, workspace.html) et `hub/hub/static`. Limitee aux
.py, l'empreinte disait « aligne » d'un overlay dont la page servie
differait de l'image : le defaut que ce releve doit montrer, deplace
d'un cran.
"""

from __future__ import annotations

import hashlib
import logging
from pathlib import Path

log = logging.getLogger(__name__)

# 12 caracteres hexadecimaux : assez pour distinguer deux etats du code,
# assez court pour se lire et se comparer a l'oeil dans `/health`.
_LONGUEUR = 12


# Ce qui est servi en plus du code Python, relativement au paquet : les
# statiques montes par le paquet lui-meme (hub : `hub/hub/static`) et, a cote
# du paquet, les gabarits Jinja et les statiques de l'agent -- `main.py` les
# cherche en `Path(__file__).parent.parent / ...`, l'overlay les pose donc au
# meme endroit (`<overlay>/templates`). Un dossier absent ne
# compte pas : l'empreinte d'un paquet sans gabarit reste celle de ses .py.
_DOSSIERS_SERVIS = ("static", "../templates", "../static")


def _fichiers(dossier: Path) -> list[tuple[str, Path]]:
    """(cle, fichier) tries par cle, sans doublon ni cache Python."""
    vus: dict[str, Path] = {}
    for f in dossier.rglob("*.py"):
        if "__pycache__" not in f.parts:
            vus[f.relative_to(dossier).as_posix()] = f
    for sous in _DOSSIERS_SERVIS:
        racine = dossier / sous
        if not racine.is_dir():
            continue
        for f in racine.rglob("*"):
            if not f.is_file() or "__pycache__" in f.parts:
                continue
            # Cle lisible et stable (`static/produit.css`,
            # `../templates/chat.html`). Un .py de `static/` a deja la meme
            # cle cote code : `setdefault` evite de le compter deux fois.
            vus.setdefault(f"{sous}/{f.relative_to(racine).as_posix()}", f)
    return sorted(vus.items())


def empreinte(dossier: Path) -> str:
    """sha256 court de ce que sert un paquet : code, gabarits et statiques.

    Le chemin relatif entre dans le hachage : renommer ou deplacer un module
    change le code importable, meme si les octets sont les memes. Les fins
    de ligne sont neutralisees pour tous les fichiers, binaires compris : la
    transformation est deterministe, deux copies identiques restent donc
    identiques. Cout mesure en local : ~190 Ko de statiques et trois
    gabarits ajoutent quelques millisecondes, une fois au demarrage.
    """
    dossier = Path(dossier)
    h = hashlib.sha256()
    for cle, f in _fichiers(dossier):
        h.update(cle.encode("utf-8"))
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
