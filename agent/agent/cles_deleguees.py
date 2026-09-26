"""Coffre des cles deleguees : la cle brute ne passe jamais par le modele.

Constate le 2026-09-26 (audit securite des acces, agents dedies §1.2 point 6) :
l'outil `create_agent` rendait au modele la cle `qgisk_` en clair. Elle
entrait dans l'historique L1, dans le journal de tour, et pouvait etre
recopiee par le modele dans n'importe quelle reponse. `publish_agent` et
`revoke_agent` exigeaient d'ailleurs cette cle brute en argument.

Desormais :
  - la cle brute reste dans ce coffre, en memoire du processus agent ;
  - le modele ne voit qu'une reference opaque (`agent-<hex>`), inutilisable
    comme jeton, qu'il passe a `publish_agent` / `revoke_agent` ;
  - l'utilisateur recupere la cle UNE fois, par l'interface, via la route
    authentifiee `GET /api/cles-deleguees/{ref}` du pod agent. La lecture est
    unique : la cle n'est plus remise ensuite.

Limites assumees (documentees dans la spec securite des acces) : le coffre
est en memoire. Apres un redemarrage du pod, la reference n'est plus
resolue ; l'utilisateur publie ou revoque alors depuis l'interface du hub,
ou recree l'agent. Le lot 2 des agents dedies remplacera ce mecanisme par un
document versionne et une validation par geste d'interface.
"""

from __future__ import annotations

import secrets
import threading
import time

_PREFIXE_REF = "agent-"
# Delai pendant lequel l'utilisateur peut recuperer la cle par l'interface.
_DELAI_REMISE_S = 3600

_verrou = threading.Lock()
# ref -> {"cle": str, "cree": float, "remise": bool}
_coffre: dict[str, dict] = {}


def deposer(cle: str) -> str:
    """Range une cle brute et rend sa reference opaque."""
    ref = _PREFIXE_REF + secrets.token_hex(8)
    with _verrou:
        _coffre[ref] = {"cle": cle, "cree": time.time(), "remise": False}
    return ref


def resoudre(ref_ou_cle: str) -> str | None:
    """Rend la cle brute pour une reference connue, sinon None.

    Une cle `qgisk_` deja en clair (collee par l'utilisateur lui-meme) est
    rendue telle quelle : elle est deja dans la conversation, la refuser ne
    protegerait rien et casserait le parcours historique.
    """
    if not ref_ou_cle:
        return None
    if ref_ou_cle.startswith("qgisk_"):
        return ref_ou_cle
    with _verrou:
        entree = _coffre.get(ref_ou_cle)
        return entree["cle"] if entree else None


def remettre(ref: str) -> str | None:
    """Remise unique a l'utilisateur. None si inconnue, deja remise ou expiree."""
    with _verrou:
        entree = _coffre.get(ref)
        if not entree or entree["remise"]:
            return None
        if time.time() - entree["cree"] > _DELAI_REMISE_S:
            return None
        entree["remise"] = True
        return entree["cle"]


def masquer(cle: str) -> str:
    """Forme affichable d'une cle (meme convention que le hub)."""
    return (cle or "")[:14] + "…"


def purger_tout() -> None:
    """Vide le coffre (tests)."""
    with _verrou:
        _coffre.clear()
