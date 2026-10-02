"""Registre des traitements en arriere-plan, et leur surveillance.

Spec : docs/superpowers/specs/2026-10-02-traitements-arriere-plan.md.

L'agent soumet les outils longs (execute_python, run_recipe, smart_load...)
en tache de fond sur le workspace QGIS et les inscrit ici. Le hub :

* garde le registre sur son volume persistant (SQLite), par utilisateur et
  par etude : il survit a un redemarrage du hub ;
* surveille les taches actives en interrogeant le workspace (`poll_job`) :
  battement, fin, perte. Une tache que le workspace ne connait plus, dont le
  battement s'est arrete, ou dont le workspace ne repond plus, passe a
  « interrompue » -- l'utilisateur choisit alors de la relancer ou de
  l'abandonner ;
* signale a l'agent la fin d'une tache passee en arriere-plan, pour qu'il en
  redige le compte rendu dans la conversation d'origine.

Ce module ne connait ni FastAPI ni le workspace : la sonde et l'avis a
l'agent lui sont passes (tests sans reseau).
"""
from __future__ import annotations

import asyncio
import json
import logging
import time
from pathlib import Path
from typing import Any, Awaitable, Callable

import aiosqlite

log = logging.getLogger("hub.taches_fond")

EN_ATTENTE, EN_COURS = "en_attente", "en_cours"
TERMINEE, ECHOUEE, ANNULEE, INTERROMPUE = "terminee", "echouee", "annulee", "interrompue"
ACTIFS = frozenset({EN_ATTENTE, EN_COURS})
FINALS = frozenset({TERMINEE, ECHOUEE, ANNULEE, INTERROMPUE})
STATUTS = ACTIFS | FINALS
MODES = frozenset({"tour", "arriere_plan"})

# Seuils de surveillance (secondes).
BATTEMENT_PERDU_S = 120        # battement du workspace arrete
INJOIGNABLE_PERDU_S = 180      # workspace muet
TOUR_ABANDONNE_S = 180         # le tour qui suivait la tache ne donne plus signe
MAX_AVIS = 360                 # avis a l'agent avant abandon (une passe / 5 s : 30 min)

# Champs que l'agent peut ecrire. Les autres sont tenus par le hub.
CHAMPS_MODIFIABLES = frozenset({
    "statut", "mode", "resultat", "erreur", "raison", "rattachee", "message",
    "rattachee_at", "fini_at", "vu_at", "annulation_demandee", "job_id",
})

_LIMITE_RESULTAT = 20000
_LIMITE_ARGUMENTS = 100000

_STATUTS_PERDUS = frozenset({"dropped", "bridge_unreachable", "client_disconnected"})


# ── Stockage ─────────────────────────────────────────────────────────────────

class Registre:
    """Registre SQLite : une ligne par tache, le document entier en JSON."""

    def __init__(self, chemin: Path | str):
        self.chemin = Path(chemin)
        self._pret = False
        self._verrou = asyncio.Lock()

    async def init(self) -> None:
        if self._pret:
            return
        self.chemin.parent.mkdir(parents=True, exist_ok=True)
        async with aiosqlite.connect(self.chemin) as db:
            await db.execute("""
                CREATE TABLE IF NOT EXISTS taches (
                    id        TEXT PRIMARY KEY,
                    username  TEXT NOT NULL,
                    sid       TEXT,
                    session_id TEXT,
                    statut    TEXT NOT NULL,
                    cree_at   REAL NOT NULL,
                    maj_at    REAL NOT NULL,
                    doc       TEXT NOT NULL
                )
            """)
            await db.execute("CREATE INDEX IF NOT EXISTS taches_user_statut "
                             "ON taches(username, statut)")
            await db.commit()
        self._pret = True

    async def _ecrire(self, doc: dict) -> None:
        async with aiosqlite.connect(self.chemin) as db:
            await db.execute(
                "INSERT OR REPLACE INTO taches "
                "(id, username, sid, session_id, statut, cree_at, maj_at, doc) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (doc["id"], doc["username"], doc.get("sid"), doc.get("session_id"),
                 doc["statut"], doc["cree_at"], doc["maj_at"],
                 json.dumps(doc, ensure_ascii=False, default=str)))
            await db.commit()

    async def creer(self, username: str, donnees: dict) -> dict:
        await self.init()
        maintenant = time.time()
        tache_id = str(donnees.get("id") or "").strip()
        if not tache_id or len(tache_id) > 64:
            raise ValueError("id de tache invalide")
        arguments = donnees.get("arguments") or {}
        texte_args = json.dumps(arguments, ensure_ascii=False, default=str)
        if len(texte_args) > _LIMITE_ARGUMENTS:
            # Trop gros pour etre relance a l'identique : on le dit.
            arguments, relancable = {"_tronque": texte_args[:2000]}, False
        else:
            relancable = True
        statut = donnees.get("statut") if donnees.get("statut") in STATUTS else EN_ATTENTE
        doc = {
            "id": tache_id,
            "username": username,
            "job_id": donnees.get("job_id"),
            "client_id": donnees.get("client_id"),
            "outil": str(donnees.get("outil") or ""),
            "arguments": arguments,
            "relancable": relancable,
            "libelle": str(donnees.get("libelle") or "Traitement QGIS")[:200],
            "session_id": donnees.get("session_id"),
            "sid": donnees.get("sid"),
            "statut": statut,
            "mode": donnees.get("mode") if donnees.get("mode") in MODES else "tour",
            "cree_at": float(donnees.get("cree_at") or maintenant),
            "maj_at": maintenant,
            "vu_at": maintenant,
            "battement_at": maintenant,
            "fini_at": None,
            "resultat": None,
            "erreur": None,
            "raison": None,
            "etat_qgis": None,
            "annulation_demandee": False,
            "rattachee": False,
            "message": None,
            "avis_tentatives": 0,
            "tentatives": 1,
        }
        async with self._verrou:
            await self._ecrire(doc)
        return doc

    async def lire(self, tache_id: str, username: str | None = None) -> dict | None:
        await self.init()
        async with aiosqlite.connect(self.chemin) as db:
            ligne = await (await db.execute(
                "SELECT doc, username FROM taches WHERE id = ?", (tache_id,))).fetchone()
        if not ligne:
            return None
        if username is not None and ligne[1] != username:
            return None
        return json.loads(ligne[0])

    async def maj(self, tache_id: str, champs: dict, username: str | None = None,
                  libre: bool = False) -> dict | None:
        """Met a jour une tache. `libre` : le hub lui-meme (tous les champs)."""
        async with self._verrou:
            doc = await self.lire(tache_id, username)
            if doc is None:
                return None
            for cle, valeur in champs.items():
                if not libre and cle not in CHAMPS_MODIFIABLES:
                    continue
                if cle == "statut" and valeur not in STATUTS:
                    continue
                if cle == "mode" and valeur not in MODES:
                    continue
                if cle == "resultat" and isinstance(valeur, str):
                    valeur = valeur[:_LIMITE_RESULTAT]
                doc[cle] = valeur
            if doc["statut"] in FINALS and not doc.get("fini_at"):
                doc["fini_at"] = time.time()
            doc["maj_at"] = time.time()
            await self._ecrire(doc)
            return doc

    async def lister(self, username: str | None = None, *, sid: str | None = None,
                     session_id: str | None = None, actives: bool = False,
                     limite: int = 50) -> list[dict]:
        await self.init()
        clauses, params = [], []
        if username is not None:
            clauses.append("username = ?")
            params.append(username)
        if sid:
            clauses.append("sid = ?")
            params.append(sid)
        if session_id:
            clauses.append("session_id = ?")
            params.append(session_id)
        if actives:
            clauses.append(f"statut IN ({','.join('?' * len(ACTIFS))})")
            params.extend(sorted(ACTIFS))
        where = (" WHERE " + " AND ".join(clauses)) if clauses else ""
        params.append(max(1, min(int(limite), 200)))
        async with aiosqlite.connect(self.chemin) as db:
            lignes = await (await db.execute(
                f"SELECT doc FROM taches{where} ORDER BY cree_at DESC LIMIT ?",
                params)).fetchall()
        return [json.loads(l[0]) for l in lignes]

    async def a_rattacher(self) -> list[dict]:
        """Taches finies, passees en arriere-plan, pas encore rattachees."""
        await self.init()
        async with aiosqlite.connect(self.chemin) as db:
            lignes = await (await db.execute(
                f"SELECT doc FROM taches WHERE statut IN "
                f"({','.join('?' * len(FINALS))})", sorted(FINALS))).fetchall()
        docs = [json.loads(l[0]) for l in lignes]
        return [d for d in docs if d.get("mode") == "arriere_plan"
                and not d.get("rattachee")
                and d.get("avis_tentatives", 0) < MAX_AVIS]


# ── Lecture d'un etat renvoye par poll_job ───────────────────────────────────

def _instant(valeur, defaut: float) -> float:
    """Horodatage enregistre, ou `defaut` s'il manque (0 est un instant valide)."""
    try:
        return float(valeur) if valeur is not None else defaut
    except (TypeError, ValueError):
        return defaut


def lire_suivi(contenu: list | None) -> tuple[dict, list]:
    """Bloc d'etat (premier texte JSON) et contenu de l'outil qui le suit."""
    elements = list(contenu or [])
    if not elements:
        return {}, []
    try:
        etat = json.loads(elements[0].get("text") or "")
    except (AttributeError, TypeError, ValueError):
        return {"error": str(elements[0])[:300]}, elements[1:]
    return (etat if isinstance(etat, dict) else {}), elements[1:]


def texte_du_contenu(contenu: list) -> str:
    """Le texte de l'outil (images retirees : elles ne servent pas au compte rendu)."""
    return "\n".join(str(c.get("text") or "") for c in contenu
                     if isinstance(c, dict) and c.get("type") == "text").strip()


def classer(sonde: dict, maintenant: float, tache: dict) -> tuple[str, str | None]:
    """(statut, raison) d'une tache active, selon la sonde du workspace.

    `sonde` : {"injoignable": True} | {"etat": {...}, "contenu": [...]}.
    """
    if sonde.get("injoignable"):
        if maintenant - _instant(tache.get("battement_at"), maintenant) > INJOIGNABLE_PERDU_S:
            return INTERROMPUE, "le bureau QGIS ne répond plus"
        return tache.get("statut") or EN_COURS, None
    etat = sonde.get("etat") or {}
    erreur = str(etat.get("error") or "")
    statut = etat.get("status")
    if not statut and "Unknown job_id" in erreur:
        return INTERROMPUE, "le service QGIS ne connaît plus ce calcul (redémarrage probable)"
    if statut == "queued":
        return EN_ATTENTE, None
    if statut in ("running", "qt_frozen"):
        # qt_frozen est normal pendant un script long (il tient le fil
        # principal). Seul un battement arrete trahit une tache perdue.
        age = etat.get("heartbeat_age_s")
        if isinstance(age, (int, float)) and age > BATTEMENT_PERDU_S:
            return INTERROMPUE, "QGIS ne donne plus signe de vie"
        return EN_COURS, None
    if statut == "done":
        return (ECHOUEE if etat.get("is_error") else TERMINEE), None
    if statut == "error":
        return ECHOUEE, None
    if statut == "cancelled":
        return ANNULEE, None
    if statut in _STATUTS_PERDUS:
        return INTERROMPUE, "QGIS ne répond plus"
    return tache.get("statut") or EN_COURS, None


# ── Surveillance ─────────────────────────────────────────────────────────────

Sonde = Callable[[dict], Awaitable[dict]]
Avis = Callable[[dict], Awaitable[bool]]


async def tour_de_surveillance(registre: Registre, sonder: Sonde, aviser: Avis,
                               maintenant: float | None = None) -> dict:
    """Une passe : met a jour les taches actives, puis avise l'agent des fins.

    Rend un bilan {"suivies", "finies", "avisees"} (journal et tests).
    """
    maintenant = time.time() if maintenant is None else maintenant
    bilan = {"suivies": 0, "finies": 0, "avisees": 0}
    for tache in await registre.lister(None, actives=True, limite=200):
        bilan["suivies"] += 1
        try:
            sonde = await sonder(tache)
        except Exception as exc:  # une sonde qui casse ne casse pas la boucle
            log.warning("sonde de la tache %s : %s", tache["id"], exc)
            sonde = {"injoignable": True}
        statut, raison = classer(sonde, maintenant, tache)
        champs: dict[str, Any] = {"statut": statut}
        if not sonde.get("injoignable"):
            etat = sonde.get("etat") or {}
            champs["battement_at"] = maintenant
            champs["etat_qgis"] = {k: etat.get(k) for k in
                                   ("status", "stage", "heartbeat_age_s", "qt_lag_ms",
                                    "queue_position", "age_s") if k in etat}
        if raison:
            champs["raison"] = raison
        if statut in FINALS:
            bilan["finies"] += 1
            champs["fini_at"] = maintenant
            contenu = sonde.get("contenu") or []
            if contenu:
                champs["resultat"] = texte_du_contenu(contenu)
            etat = sonde.get("etat") or {}
            if etat.get("error"):
                champs["erreur"] = str(etat["error"])[:2000]
            if tache.get("annulation_demandee") and statut in (TERMINEE, ECHOUEE):
                # L'utilisateur avait annule un calcul deja commence : il a
                # fini dans QGIS, mais son resultat est ignore.
                champs["statut"] = ANNULEE
            if tache.get("mode") == "tour":
                # Le tour suit cette tache et en consommera le resultat ; si
                # le tour a disparu, la regle ci-dessous la basculera.
                if maintenant - _instant(tache.get("vu_at"), 0.0) > TOUR_ABANDONNE_S:
                    champs["mode"] = "arriere_plan"
                else:
                    # On laisse le tour conclure ; pas de fin ecrite ici.
                    champs = {k: v for k, v in champs.items()
                              if k in ("battement_at", "etat_qgis")}
        elif tache.get("mode") == "tour" and \
                maintenant - _instant(tache.get("vu_at"), maintenant) > TOUR_ABANDONNE_S:
            # Le tour qui suivait la tache a disparu (agent redemarre, onglet
            # ferme) : elle continue en arriere-plan, son resultat sera rattache.
            champs["mode"] = "arriere_plan"
        await registre.maj(tache["id"], champs, libre=True)

    for tache in await registre.a_rattacher():
        try:
            ok = await aviser(tache)
        except Exception as exc:
            log.warning("avis de fin de la tache %s : %s", tache["id"], exc)
            ok = False
        if ok:
            bilan["avisees"] += 1
        else:
            await registre.maj(tache["id"], {
                "avis_tentatives": int(tache.get("avis_tentatives") or 0) + 1,
            }, libre=True)
    return bilan


async def boucle_de_surveillance(registre: Registre, sonder: Sonde, aviser: Avis,
                                 periode: float = 5.0) -> None:
    """Tourne tant que le hub vit. Sans tache active, une passe ne coute
    qu'une lecture SQLite."""
    while True:
        try:
            await tour_de_surveillance(registre, sonder, aviser)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            log.warning("surveillance des taches : %s", exc)
        await asyncio.sleep(periode)


# ── Actions de l'utilisateur ─────────────────────────────────────────────────

Annuleur = Callable[[dict], Awaitable[dict]]
Soumetteur = Callable[[dict, str], Awaitable[dict]]


async def annuler(registre: Registre, tache: dict, annuler_job: Annuleur) -> dict:
    """Annule une tache. Une tache deja commencee dans QGIS ne peut pas etre
    interrompue : elle reste active (QGIS est occupe) et sera notee
    « annulee » a sa fin, son resultat ignore."""
    if tache["statut"] == INTERROMPUE:
        return await registre.maj(tache["id"], {"statut": ANNULEE, "rattachee": True},
                                  libre=True)
    if tache["statut"] not in ACTIFS:
        return tache
    reponse = {}
    if tache.get("job_id"):
        try:
            reponse = await annuler_job(tache) or {}
        except Exception as exc:
            reponse = {"error": str(exc)}
    if reponse.get("success") and reponse.get("status") in ("cancelled", "cancel_pending"):
        return await registre.maj(tache["id"], {
            "statut": ANNULEE, "annulation_demandee": True, "rattachee": True,
        }, libre=True)
    return await registre.maj(tache["id"], {
        "annulation_demandee": True, "mode": "arriere_plan",
    }, libre=True)


async def relancer(registre: Registre, tache: dict, soumettre: Soumetteur) -> dict:
    """Relance une tache interrompue (ou echouee) : nouvelle soumission, meme
    outil, memes arguments ; le resultat sera rattache a la conversation."""
    if tache["statut"] not in (INTERROMPUE, ECHOUEE):
        raise ValueError("seule une tache interrompue ou en echec se relance")
    if not tache.get("relancable", True):
        raise ValueError("arguments trop volumineux pour etre relances a l'identique")
    tentative = int(tache.get("tentatives") or 1) + 1
    client_id = f"{tache.get('client_id') or tache['id']}:relance-{tentative}"
    reponse = await soumettre(tache, client_id)
    job_id = (reponse or {}).get("job_id")
    if not job_id:
        raise RuntimeError((reponse or {}).get("error") or "soumission refusee")
    maintenant = time.time()
    return await registre.maj(tache["id"], {
        "job_id": job_id, "client_id": client_id, "statut": EN_ATTENTE,
        "mode": "arriere_plan", "tentatives": tentative, "rattachee": False,
        "message": None, "resultat": None, "erreur": None, "raison": None,
        "fini_at": None, "battement_at": maintenant, "vu_at": maintenant,
        "annulation_demandee": False, "avis_tentatives": 0,
    }, libre=True)


def vue_publique(tache: dict) -> dict:
    """Ce que voient le chat et le bureau (sans les arguments complets)."""
    vue = {k: v for k, v in tache.items() if k not in ("arguments", "client_id")}
    resultat = vue.get("resultat")
    if isinstance(resultat, str) and len(resultat) > 2000:
        vue["resultat"] = resultat[:2000] + "…"
    return vue
