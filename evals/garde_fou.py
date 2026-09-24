"""Garde-fou bac a sable : le banc ne joue JAMAIS sur une etude utilisateur.

Le chat de l'agent travaille dans l'etude ACTIVE du proprietaire du pod ; les
scenarios vident le projet, chargent des couches, restaurent des points de
retour. On exige donc, avant toute action et a chaque repetition :

1. une etude designee explicitement (`--study <sid>`) ;
2. dont le NOM commence par `bac-a-sable` (casse, accents, espaces et
   soulignes ignores : « Bac à sable - banc » passe, « Aix bâti » non) ;
3. qui existe, n'est pas archivee, et appartient bien a l'utilisateur du jeton
   (le hub rend 404 sinon) ;
4. qui est l'etude ACTIVE au moment de jouer (sinon l'agent travaillerait
   ailleurs). Le banc ne l'active lui-meme que sur demande (`--activer-etude`).

Le prefixe n'est pas parametrable : un garde-fou qu'une option desactive n'en
est pas un.
"""
from __future__ import annotations

import re
import unicodedata

PREFIXE_BAC_A_SABLE = "bac-a-sable"


class RefusGardeFou(RuntimeError):
    pass


def normaliser_nom(nom: str) -> str:
    decompose = unicodedata.normalize("NFKD", nom or "")
    sans_accents = "".join(c for c in decompose if not unicodedata.combining(c)).lower().strip()
    return re.sub(r"[\s_]+", "-", sans_accents)


def est_bac_a_sable(nom: str) -> bool:
    return normaliser_nom(nom).startswith(PREFIXE_BAC_A_SABLE)


def verifier_etude(sid: str | None, etude: dict | None) -> str:
    """Controle l'etude designee. Rend un avertissement eventuel, leve sinon."""
    if not sid or not str(sid).strip():
        raise RefusGardeFou("--study est obligatoire : designer une etude bac a sable explicite.")
    if not etude:
        raise RefusGardeFou(f"etude {sid} introuvable pour ce jeton : refus.")
    if str(etude.get("id", sid)) != str(sid):
        raise RefusGardeFou(f"le hub a rendu l'etude {etude.get('id')} au lieu de {sid} : refus.")
    nom = str(etude.get("name") or "")
    if not est_bac_a_sable(nom):
        raise RefusGardeFou(
            f"l'etude {sid} s'appelle « {nom} » : son nom doit commencer par "
            f"« {PREFIXE_BAC_A_SABLE} ». Le banc refuse de toucher une etude utilisateur."
        )
    statut = etude.get("status", "active")
    if statut != "active":
        raise RefusGardeFou(f"l'etude {sid} est au statut « {statut} » : refus.")
    if etude.get("origin", "user") == "user":
        return (f"etude {sid} d'origine « user » : prefere une etude creee avec "
                f"origin=test pour qu'elle reste hors de la liste de l'utilisateur.")
    return ""


def verifier_active(sid: str, active: dict | None) -> None:
    """L'etude active doit etre l'etude bac a sable designee."""
    actif = (active or {}).get("id")
    if str(actif) != str(sid):
        nom = (active or {}).get("name", "aucune")
        raise RefusGardeFou(
            f"l'etude active est {actif} (« {nom} ») et non {sid} : l'agent y "
            f"travaillerait. Active l'etude bac a sable, ou relance avec --activer-etude."
        )
    if not est_bac_a_sable(str((active or {}).get("name") or "")):
        raise RefusGardeFou(f"l'etude active {sid} n'a pas un nom de bac a sable : refus.")
