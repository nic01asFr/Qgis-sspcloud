"""Le depot local des publications — ce qui reste quand le stockage tombe.

Les identifiants S3 d'un service Onyxia durent sept jours et personne ne les
renouvelle. Jusqu'ici une publication ne vivait que sur MinIO : passe ce
delai, le catalogue se vidait, les livrables disparaissaient et les scenes
repondaient 503. Rien n'etait perdu, mais tout semblait l'etre -- constate en
production le 2026-08-30, puis de nouveau le 2026-09-20.

Le hub possede pourtant un disque a lui, qui survit aux redeploiements : le
meme volume que `studies.db`. Une publication y est desormais ecrite EN
PREMIER, et S3 n'est plus qu'un second exemplaire -- utile pour partager en
dehors du service, plus indispensable pour lire.

Ce module ne connait pas S3 et n'a aucun identifiant a presenter. C'est tout
son interet : il ne peut pas expirer.

Il reproduit l'arborescence de S3 a l'identique, pour que les deux se lisent
de la meme facon :

    <racine>/publications/{owner}/{kind}/{slug}.{ext}
    <racine>/catalogues/{owner}.json
"""
from __future__ import annotations

import json
import logging
import os
import shutil
import tempfile
from pathlib import Path
from typing import Any

log = logging.getLogger("hub.depot_local")

# Le meme emplacement que `studies.db` : un volume qui survit aux
# redeploiements. Ce bloc existe a l'identique dans `auth.py`, `sessions.py`
# et `studies.py` -- c'est ici qu'il faudra les faire converger le jour ou on
# y touchera. Ne PAS le recopier une cinquieme fois.
RACINE_DONNEES = Path(os.getenv("DATA_DIR") or (
    "/home/onyxia/work/qgis-mcp/server-data"
    if Path("/home/onyxia/work").is_dir()
    else "/tmp/qgis-mcp/server-data"
))

_PUBLICATIONS = RACINE_DONNEES / "publications"
_CATALOGUES = RACINE_DONNEES / "catalogues"

# Au-dela, on n'ecrit pas localement : le volume de travail n'est pas fait
# pour stocker des rasters. Ces objets-la restent sur S3 seul, ou ils ont
# toujours vecu. Un livrable HTML, une scene ou un GeoJSON tiennent tres
# largement en dessous.
TAILLE_MAX = int(os.getenv("DEPOT_LOCAL_TAILLE_MAX", str(64 * 1024 * 1024)))


def _segment(valeur: str) -> str:
    """Un element de chemin sans echappatoire possible.

    `owner` et `kind` viennent de l'URL : sans ce filtre, un `..` bien place
    ferait lire n'importe quel fichier du volume.
    """
    propre = "".join(
        c if c.isalnum() or c in "_-." else "_" for c in str(valeur)
    ).strip("_-.")
    return propre[:120]


def _chemin(owner: str, kind: str, slug: str, ext: str) -> Path:
    return (_PUBLICATIONS / _segment(owner) / _segment(kind)
            / f"{_segment(slug)}.{_segment(ext)}")


def _chemin_catalogue(owner: str) -> Path:
    return _CATALOGUES / f"{_segment(owner)}.json"


def disponible() -> bool:
    """Le disque est-il utilisable ici.

    Faux en test hors conteneur, ou si le volume n'est pas monte. L'appelant
    retombe alors sur S3 seul, comme avant : le depot local ajoute une
    securite, il n'en retire aucune.
    """
    try:
        _PUBLICATIONS.mkdir(parents=True, exist_ok=True)
        return os.access(_PUBLICATIONS, os.W_OK)
    except Exception as exc:
        log.debug("depot local indisponible : %s", exc)
        return False


def _ecrire_atomiquement(cible: Path, contenu: bytes) -> None:
    """Ecrit sans jamais laisser un fichier a moitie ecrit.

    Une publication interrompue en cours d'ecriture serait servie tronquee,
    et le lecteur verrait une scene corrompue plutot qu'une absence.
    """
    cible.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(
        dir=cible.parent, delete=False, suffix=".partiel",
    ) as fichier:
        provisoire = Path(fichier.name)
        fichier.write(contenu)
        fichier.flush()
        os.fsync(fichier.fileno())
    provisoire.replace(cible)


def ecrire(owner: str, kind: str, slug: str, ext: str,
           contenu: bytes) -> bool:
    """Depose une publication sur le disque du hub. Faux si on n'a pas pu.

    Ne leve pas : l'echec du depot local ne doit jamais empecher une
    publication d'aboutir sur S3.
    """
    if len(contenu) > TAILLE_MAX:
        log.info("depot local ignore %s/%s : %d octets > %d",
                 owner, slug, len(contenu), TAILLE_MAX)
        return False
    if not disponible():
        return False
    try:
        _ecrire_atomiquement(_chemin(owner, kind, slug, ext), contenu)
        return True
    except Exception as exc:
        log.warning("depot local : ecriture de %s/%s impossible (%s)",
                    owner, slug, exc)
        return False


def lire(owner: str, kind: str, slug: str, ext: str) -> bytes | None:
    """Le contenu, ou None s'il n'est pas ici."""
    chemin = _chemin(owner, kind, slug, ext)
    try:
        if not chemin.is_file():
            return None
        return chemin.read_bytes()
    except Exception as exc:
        log.warning("depot local : lecture de %s/%s impossible (%s)",
                    owner, slug, exc)
        return None


def entete(owner: str, kind: str, slug: str, ext: str) -> dict | None:
    """Taille et date, sans lire le contenu. None si absent."""
    chemin = _chemin(owner, kind, slug, ext)
    try:
        if not chemin.is_file():
            return None
        etat = chemin.stat()
        return {"size": etat.st_size, "last_modified": int(etat.st_mtime)}
    except Exception:
        return None


def _bornes(entete_range: str, taille: int) -> tuple[int, int] | None:
    """Traduit un en-tete `Range` en bornes inclusives, ou None s'il est fou.

    Seule la forme a une plage est traitee : c'est la seule que PMTiles
    emette. Une forme exotique rend None, et l'appelant sert l'objet entier
    plutot que de repondre faux.
    """
    entete_range = (entete_range or "").strip().lower()
    if not entete_range.startswith("bytes=") or "," in entete_range:
        return None
    plage = entete_range[len("bytes="):].strip()
    try:
        if plage.startswith("-"):                  # les N derniers octets
            longueur = int(plage[1:])
            if longueur <= 0:
                return None
            debut, fin = max(0, taille - longueur), taille - 1
        else:
            morceaux = plage.split("-", 1)
            debut = int(morceaux[0])
            fin = int(morceaux[1]) if len(morceaux) > 1 and morceaux[1] else taille - 1
    except ValueError:
        return None
    fin = min(fin, taille - 1)
    if debut > fin or debut < 0:
        return None
    return debut, fin


def lire_intervalle(owner: str, kind: str, slug: str, ext: str,
                    entete_range: str, content_type: str) -> dict | None:
    """Lit une plage d'octets, comme le fait S3 pour les tuiles PMTiles.

    Rend le meme dictionnaire que `s3_publication.read_range`, pour que la
    route qui sert les publications n'ait pas a savoir d'ou vient l'octet.
    """
    chemin = _chemin(owner, kind, slug, ext)
    try:
        if not chemin.is_file():
            return None
        taille = chemin.stat().st_size
        bornes = _bornes(entete_range, taille)
        if bornes is None:
            return None
        debut, fin = bornes
        with chemin.open("rb") as fichier:
            fichier.seek(debut)
            corps = fichier.read(fin - debut + 1)
        return {
            "body": corps,
            "content_range": f"bytes {debut}-{fin}/{taille}",
            "content_length": len(corps),
            "content_type": content_type,
            "total_size": taille,
        }
    except Exception as exc:
        log.warning("depot local : plage %s de %s/%s illisible (%s)",
                    entete_range, owner, slug, exc)
        return None


def supprimer(owner: str, kind: str, slug: str, ext: str) -> bool:
    """Retire une publication du disque. Vrai si elle y etait."""
    chemin = _chemin(owner, kind, slug, ext)
    try:
        if chemin.is_file():
            chemin.unlink()
            return True
    except Exception as exc:
        log.warning("depot local : suppression de %s/%s impossible (%s)",
                    owner, slug, exc)
    return False


def catalogue(owner: str) -> list[dict] | None:
    """L'index local, ou None s'il n'a jamais ete ecrit.

    None et liste vide ne disent pas la meme chose : l'un veut dire « je ne
    sais pas », l'autre « il n'y a rien ». Les confondre ferait annoncer
    « aucun livrable » a quelqu'un qui en a.
    """
    chemin = _chemin_catalogue(owner)
    try:
        if not chemin.is_file():
            return None
        items = json.loads(chemin.read_text(encoding="utf-8"))
        return items if isinstance(items, list) else None
    except Exception as exc:
        log.warning("depot local : catalogue de %s illisible (%s)", owner, exc)
        return None


def ecrire_catalogue(owner: str, items: list[dict]) -> bool:
    """Enregistre l'index local. Faux si on n'a pas pu."""
    try:
        _CATALOGUES.mkdir(parents=True, exist_ok=True)
        _ecrire_atomiquement(
            _chemin_catalogue(owner),
            json.dumps(items, ensure_ascii=False, indent=2).encode("utf-8"),
        )
        return True
    except Exception as exc:
        log.warning("depot local : catalogue de %s non enregistre (%s)",
                    owner, exc)
        return False


def etat() -> dict[str, Any]:
    """Ce que le depot contient, pour le dire a l'utilisateur."""
    infos: dict[str, Any] = {
        "disponible": disponible(),
        "racine": str(_PUBLICATIONS),
        "publications": 0,
        "octets": 0,
    }
    try:
        for fichier in _PUBLICATIONS.rglob("*"):
            if fichier.is_file() and not fichier.name.endswith(".partiel"):
                infos["publications"] += 1
                infos["octets"] += fichier.stat().st_size
    except Exception:
        pass
    try:
        usage = shutil.disk_usage(_PUBLICATIONS)
        infos["disque_libre_octets"] = usage.free
    except Exception:
        pass
    return infos
