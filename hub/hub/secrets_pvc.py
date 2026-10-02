"""Fichier de secrets du service, sur le volume persistant (PVC) du hub.

Decision d'exploitation (2026-09-26) : les identifiants du service sont
conserves sur le PVC du hub, avec le reste de ses donnees, et non dans Vault
(renouvellement manuel des identifiants Vault : on n'en depend pas). Le
fichier vit sous `DATA_DIR/secrets/`, repertoire en 0700 et fichier en 0600,
au format `CLE=valeur` une ligne par cle.

Ce module ne fait que lire et ecrire ce fichier. Les appelants (aujourd'hui
`s3_publication`) decident de l'ordre des sources. Hors deploiement
(`DATA_DIR` non defini : postes de developpement, tests), il est inerte :
rien n'est lu ni ecrit, pour ne jamais deposer de secret dans un repertoire
temporaire partage.
"""

from __future__ import annotations

import logging
import os
from pathlib import Path

log = logging.getLogger("hub.secrets_pvc")


def chemin(nom: str) -> Path | None:
    """Chemin du fichier `nom` sur le PVC, ou None hors deploiement."""
    base = os.getenv("DATA_DIR", "").strip()
    if not base:
        return None
    return Path(base) / "secrets" / nom


def lire(nom: str) -> dict[str, str]:
    """Contenu du fichier (dictionnaire vide s'il manque ou est illisible)."""
    fichier = chemin(nom)
    if fichier is None or not fichier.is_file():
        return {}
    try:
        texte = fichier.read_text(encoding="utf-8")
    except OSError as exc:
        log.warning("secrets %s illisible : %s", fichier, exc)
        return {}
    valeurs: dict[str, str] = {}
    for ligne in texte.splitlines():
        ligne = ligne.strip()
        if not ligne or ligne.startswith("#") or "=" not in ligne:
            continue
        cle, valeur = ligne.split("=", 1)
        valeurs[cle.strip()] = valeur.strip()
    return valeurs


def fusionner(nom: str, valeurs: dict[str, str | None]) -> bool:
    """Fusionne des valeurs dans le fichier, avec des droits restreints.

    Memes conventions que le Secret passerelle : une valeur est ecrite, une
    chaine vide laisse la cle telle quelle, `None` la retire. L'ecriture
    passe par un fichier temporaire renomme : pas de fichier a moitie ecrit.
    """
    fichier = chemin(nom)
    if fichier is None:
        return False
    actuelles = lire(nom)
    for cle, valeur in valeurs.items():
        if valeur is None:
            actuelles.pop(cle, None)
        elif valeur:
            if "\n" in valeur or "\r" in valeur:
                log.warning("secrets %s : valeur multiligne refusee (%s)", nom, cle)
                return False
            actuelles[cle] = valeur
    try:
        fichier.parent.mkdir(parents=True, exist_ok=True)
        os.chmod(fichier.parent, 0o700)
        provisoire = fichier.with_suffix(fichier.suffix + ".tmp")
        descripteur = os.open(
            provisoire, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600,
        )
        with os.fdopen(descripteur, "w", encoding="utf-8") as sortie:
            for cle in sorted(actuelles):
                sortie.write(f"{cle}={actuelles[cle]}\n")
        os.chmod(provisoire, 0o600)
        os.replace(provisoire, fichier)
    except OSError as exc:
        log.warning("secrets %s : ecriture impossible : %s", fichier, exc)
        return False
    return True
