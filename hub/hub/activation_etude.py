"""Activation d'etude atomique et accord hub / QGIS (2026-10-02).

Constat en production : QGIS etant occupe par un long calcul, « Ouvrir » une
etude depuis Mon espace a renvoye `activate_failed`, MAIS le hub avait deja
enregistre l'etude comme active. QGIS gardait l'ancien projet ; redemarre, il
a rouvert l'ancienne etude. Hub et QGIS desaccordes, sans aucun signal.

Cause : le hub ecrivait l'etude active en base AVANT de charger le projet
dans QGIS, et ignorait l'echec du chargement (journalise, puis 200).

Regles :

  A1  Workspace pret : le projet est charge dans QGIS D'ABORD ; l'etude
      active n'est ecrite en base que si QGIS l'a bien chargee (marque
      `STUDY_STAMP sid=… pid=…` sans erreur de chargement).
  A2  QGIS ne repond pas (calcul en cours) ou le chargement echoue : l'etude
      active ne change pas ; une « activation en attente » est notee, que le
      bureau affiche et retente des que QGIS repond.
  A3  Workspace endormi ou en demarrage : comportement inchange (base
      d'abord) ; le reveil recharge l'etude active.
  A4  Au chargement du bureau, verification d'accord : l'etude du projet
      ouvert dans QGIS (variable `hub_sid`) contre l'etude active du hub. En
      desaccord, le projet ouvert est enregistre dans SA propre etude, puis
      l'etude active du hub est rechargee dans QGIS.

Le module est sans dependance au reste du hub (hors `studies` pour le code
pod) : les fonctions de decision sont pures et testees seules.
"""

from __future__ import annotations

import json
import time
from typing import Any, Awaitable, Callable

# Executeur de code Python dans QGIS : (username, code) -> stdout.
Executeur = Callable[[str, str], Awaitable[str]]

# Delai de la sonde « QGIS repond-il ? ». Court : QGIS occupe ne repond pas,
# inutile d'attendre les 30 s du chargement pour le savoir.
DELAI_SONDE_S = 8
# Seconde sonde, quand aucun calcul n'est inscrit au registre des taches :
# une sauvegarde du bureau ou un controle de coherence occupent QGIS 10 a 13 s
# (mesure du 2026-10-03), et la sonde de 8 s concluait a tort « QGIS occupe ».
DELAI_SONDE_PATIENTE_S = 30

_MARQUES_ECHEC = ("PROJECT_LOAD_ERR", "PROJECT_NEW_ERR", "PROJECT_LOAD_OK ok=False",
                  "STUDY_STAMP_ERR")

MESSAGES = {
    "qgis_occupe": "QGIS est occupé par un calcul : l'étude « {nom} » sera ouverte "
                   "dès qu'il sera libre.",
    "echec_chargement": "QGIS n'a pas pu charger le projet de l'étude « {nom} ». "
                        "L'étude active n'a pas changé.",
    "qgis_injoignable": "Le bureau QGIS ne répond pas. L'étude active n'a pas changé.",
}


def classer_erreur(exc: BaseException) -> str:
    """Erreur d'appel a QGIS -> motif. Un delai depasse = QGIS occupe."""
    nom = type(exc).__name__
    if "Timeout" in nom or isinstance(exc, TimeoutError):
        return "qgis_occupe"
    return "qgis_injoignable"


def verdict_activation(
    sid: str, pid: str | None,
    sortie_etude: str | None, sortie_projet: str | None,
    erreur: str | None = None,
) -> dict:
    """QGIS a-t-il charge l'etude `sid` (projet `pid`) ? Fonction pure.

    `sortie_etude` : stdout de `activate_pod_code` (None si l'etude ne
    changeait pas). `sortie_projet` : stdout de `activate_project_pod_code`.
    `erreur` : motif d'echec de l'appel (cf. classer_erreur).
    """
    if erreur:
        return {"ok": False, "motif": erreur}
    if sortie_etude is not None and f"ACTIVE_STUDY={sid}" not in sortie_etude:
        return {"ok": False, "motif": "echec_chargement"}
    sortie = sortie_projet or ""
    if pid and f"STUDY_STAMP sid={sid} pid={pid}" not in sortie:
        return {"ok": False, "motif": "echec_chargement"}
    if any(m in sortie for m in _MARQUES_ECHEC):
        return {"ok": False, "motif": "echec_chargement"}
    return {"ok": True, "motif": "charge"}


def code_lecture_etat_qgis() -> str:
    """Code pod : etude et projet du projet OUVERT dans QGIS (variables)."""
    return """
import json
try:
    from qgis.core import QgsProject, QgsExpressionContextUtils as _ECU
    _p = QgsProject.instance()
    _scope = _ECU.projectScope(_p)
    print("QGIS_ETAT " + json.dumps({
        "sid": str(_scope.variable("hub_sid") or ""),
        "pid": str(_scope.variable("hub_pid") or ""),
        "fichier": _p.fileName() or "",
        "n_couches": len(_p.mapLayers()),
    }))
except Exception as _exc:
    print("QGIS_ETAT_ERR " + str(_exc))
"""


def lire_etat_qgis(sortie: str | None) -> dict | None:
    """stdout de `code_lecture_etat_qgis` -> {sid, pid, fichier, n_couches}."""
    for ligne in (sortie or "").splitlines():
        if ligne.startswith("QGIS_ETAT "):
            try:
                etat = json.loads(ligne[len("QGIS_ETAT "):])
            except ValueError:
                return None
            return etat if isinstance(etat, dict) else None
    return None


def diagnostic(
    hub_sid: str | None, hub_pid: str | None,
    etat_qgis: dict | None, attente: dict | None,
    sids_sessions_mcp: tuple | list | set = (),
) -> dict:
    """Accord hub / QGIS et action a mener. Fonction pure (regles A2, A4).

    `sids_sessions_mcp` : etudes ouvertes par des sessions MCP externes
    (etat session-scoped, qui ne touche pas l'etude active en base). Un QGIS
    sur l'une d'elles est un desaccord VOULU : pas de resynchronisation.

    Rend {"etat", "action"} :
      etat   : accord | non_marque | inconnu | desaccord | desaccord_session_mcp
               | en_attente | accord_tardif
      action : aucune | patienter | retenter | valider_attente | resynchroniser
    """
    if attente:
        if etat_qgis is None:
            return {"etat": "en_attente", "action": "patienter"}
        if (etat_qgis.get("sid") == attente.get("sid")
                and (not attente.get("pid") or etat_qgis.get("pid") == attente.get("pid"))):
            # Le chargement demande pendant le calcul a fini par passer.
            return {"etat": "accord_tardif", "action": "valider_attente"}
        return {"etat": "en_attente", "action": "retenter"}
    if etat_qgis is None:
        return {"etat": "inconnu", "action": "aucune"}
    sid_qgis = etat_qgis.get("sid") or ""
    if not sid_qgis or not hub_sid:
        # Projet non marque (anterieur, ou ouvert hors du flux) : on ne
        # conclut rien, pour ne rien casser.
        return {"etat": "non_marque", "action": "aucune"}
    pid_qgis = etat_qgis.get("pid") or ""
    # Un projet QGIS sans `hub_pid` n'est PAS en accord avec un projet designe
    # par la base (2026-10-03) : c'est le projet d'etude (legacy), charge par
    # une activation d'etude. Le tenir pour accord laissait la base sur un
    # nouveau projet et QGIS sur le principal, sans resynchronisation.
    if sid_qgis == hub_sid and (not hub_pid or pid_qgis == hub_pid):
        return {"etat": "accord", "action": "aucune"}
    if sid_qgis in set(sids_sessions_mcp or ()):
        return {"etat": "desaccord_session_mcp", "action": "aucune"}
    return {"etat": "desaccord", "action": "resynchroniser"}


# ── Activations en attente (memoire du processus, par utilisateur) ───────────
# Volontairement en memoire : une attente ne survit pas a un redemarrage du
# hub, et c'est voulu -- apres redemarrage, la verification d'accord (A4)
# repart de l'etude active en base, qui n'a pas change.

_EN_ATTENTE: dict[str, dict] = {}


def noter_en_attente(username: str, sid: str, pid: str | None, motif: str,
                     nom: str = "") -> dict:
    attente = {"sid": sid, "pid": pid, "motif": motif, "nom": nom,
               "depuis": int(time.time())}
    _EN_ATTENTE[username] = attente
    return dict(attente)


def en_attente(username: str) -> dict | None:
    attente = _EN_ATTENTE.get(username)
    return dict(attente) if attente else None


def effacer_attente(username: str) -> None:
    _EN_ATTENTE.pop(username, None)


def message(motif: str, nom: str) -> str:
    return MESSAGES.get(motif, MESSAGES["echec_chargement"]).format(nom=nom or "?")


async def sonder_qgis(executer: Executeur, username: str) -> dict | None:
    """Etat du projet ouvert dans QGIS, ou None si QGIS ne repond pas."""
    try:
        return lire_etat_qgis(await executer(username, code_lecture_etat_qgis()))
    except Exception:
        return None


async def charger_dans_qgis(
    executer: Executeur, username: str, sid: str, pid: str | None,
    changer_etude: bool, migrer_ancien: bool = True,
) -> dict:
    """Charge l'etude/projet dans QGIS et rend le verdict (sans toucher la base).

    `migrer_ancien=False` pour un projet secondaire : il ne recoit jamais le
    projet d'etude legacy (cf. studies.activate_project_pod_code).
    """
    from hub import studies

    sortie_etude: str | None = None
    try:
        if changer_etude:
            sortie_etude = await executer(username, studies.activate_pod_code(sid))
        sortie_projet = (await executer(
            username, studies.activate_project_pod_code(sid, pid, migrer_ancien=migrer_ancien))
            if pid else "")
    except Exception as exc:
        return {**verdict_activation(sid, pid, sortie_etude, None,
                                     erreur=classer_erreur(exc)),
                "detail": f"{type(exc).__name__}: {exc}"[:300]}
    return verdict_activation(sid, pid, sortie_etude, sortie_projet)


async def workspace_pret(username: str) -> bool:
    """Le pod QGIS est-il deja demarre (regle A3) ?"""
    try:
        from hub import sessions
        lignes = await sessions.list_sessions(username)
    except Exception:
        return False
    return bool(lignes) and lignes[0].get("status") == "ready"


def resume(attente: dict | None) -> dict[str, Any] | None:
    """Forme publique d'une attente (pour le bureau)."""
    if not attente:
        return None
    return {"sid": attente.get("sid"), "pid": attente.get("pid"),
            "nom": attente.get("nom", ""), "motif": attente.get("motif"),
            "message": message(attente.get("motif", ""), attente.get("nom", "")),
            "depuis": attente.get("depuis")}
