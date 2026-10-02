"""Traitements en arriere-plan, avec bascule proposee a l'utilisateur.

Constat live du 2026-10-02 : un `execute_python` de densite a tenu le tour et
le chat plus de 12 minutes (« Calcul en cours dans QGIS… 12 min 17 s »). Les
outils asynchrones existaient (`execute_async`, `poll_job`) mais l'agent ne
s'en servait que sur mots-cles, et restait de toute facon dans le tour a
attendre.

Desormais (spec docs/superpowers/specs/2026-10-02-traitements-arriere-plan.md) :

1. les outils potentiellement longs partent en tache de fond PAR DEFAUT,
   sous le capot : le modele appelle `execute_python` comme avant et recoit
   le meme resultat ;
2. l'agent attend jusqu'a un seuil (45 s par defaut). Au-dela, le chat
   propose : continuer en arriere-plan, attendre, annuler. Sans reponse, la
   bascule se fait seule (2 min par defaut) ;
3. une recette complete, ou un traitement declare lourd (delai demande d'au
   moins 5 min), part directement en arriere-plan ;
4. le hub tient le registre des taches et surveille leur battement ;
5. a la fin, l'agent redige un court message rattache a la conversation ;
6. tant qu'une tache occupe QGIS, toute autre action sur la carte est
   refusee avec une consigne claire, jamais lancee en concurrence.

Ce module ne fait aucun appel au modele ni a QGIS par lui-meme : les appels
reseau lui sont passes (tests sans reseau).
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import time
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable

import httpx

log = logging.getLogger("agent.arriere_plan")


# ── Ce qui part en tache de fond ─────────────────────────────────────────────
#
# Mesure dans le code (2026-10-02), pas a l'intuition :
#   - QgisRemoteMCP `_LONG_TIMEOUT_ACTIONS` (delai 300 s) : smart_load,
#     add_from_catalog, clip_to_study_zone, export_flood_map, export_web_map,
#     export_temporal_map, export_qfield, export_grist, execute_python ;
#   - `run_recipe` : delai de 1 200 s par etape cote serveur ;
#   - `run_processing` : delai court par defaut, mais c'est lui qui porte
#     native:difference ou extractbylocation sur 50 000 entites et plus.
# export_layer et export_pdf restent en direct : delai de 60 s cote serveur.
OUTILS_LONGS: frozenset[str] = frozenset({
    "execute_python", "run_processing", "run_recipe",
    "smart_load", "add_from_catalog", "clip_to_study_zone",
    "export_flood_map", "export_web_map", "export_temporal_map",
    "export_qfield", "export_grist",
    # Vague E (2026-10-02) : 51 s mesures pour une maille de 50 m sur les
    # 112 816 batiments d'Aix.
    "densite_par_maille", "compter_par_zone",
})

# Lancees directement en arriere-plan, sans attendre le seuil : une recette
# complete enchaine des dizaines d'etapes.
OUTILS_DIRECTS: frozenset[str] = frozenset({"run_recipe"})

# Un delai demande d'au moins cette valeur (argument `timeout`) declare un
# traitement lourd : il part directement en arriere-plan.
_DELAI_DECLARE_LOURD_S = 300

# Outils qui ne touchent pas QGIS : permis pendant qu'une tache l'occupe.
OUTILS_HORS_QGIS: frozenset[str] = frozenset({
    "poll_job", "cancel_job",
    "study_list", "study_project_list",
    "memory_search", "memory_similar", "consulter_documents",
    "save_recipe", "list_recipes_for_study", "delete_recipe",
    "get_recipe_history", "demander_outils",
})

# Relance automatique (cf. qgis_agent._call_mcp_tool_raw) : permise seulement
# quand executer deux fois ne change rien. Constat du 2026-10-02 : apres un
# `execute_python` de 12 min, la reponse illisible a declenche deux relances,
# donc deux nouvelles executions du meme script.
OUTILS_SANS_EFFET: frozenset[str] = frozenset({
    "get_project_info", "get_features", "get_screenshot", "get_study_zone",
    "get_recipe", "list_recipes", "list_datasources", "list_files",
    "list_layout_templates", "list_database_connections", "search_algorithms",
    "poll_job", "qgis_desktop_ui", "study_list", "study_project_list",
    # Idempotents : les rejouer laisse le meme etat.
    "set_study_zone", "zoom_to", "set_layer_visibility", "set_layer_style",
    "cancel_job",
})


def relance_permise(outil: str, arguments: dict | None = None) -> bool:
    """Peut-on rejouer cet appel apres une reponse ambigue (5xx, corps vide,
    JSON illisible) ? Non pour tout outil qui modifie ou calcule : la requete
    a pu etre executee. Une soumission `execute_async` portant un `client_id`
    est idempotente cote serveur : la rejouer rend la meme tache."""
    if outil in OUTILS_SANS_EFFET:
        return True
    if outil.startswith(("get_", "list_", "search_")):
        return True
    if outil == "execute_async" and (arguments or {}).get("client_id"):
        return True
    return False


# ── Reglages ─────────────────────────────────────────────────────────────────

def _flottant(nom: str, defaut: float) -> float:
    try:
        return float(os.getenv(nom, "") or defaut)
    except ValueError:
        return defaut


def actif() -> bool:
    """Interrupteur general (AGENT_ARRIERE_PLAN=0 pour revenir au direct)."""
    return os.getenv("AGENT_ARRIERE_PLAN", "1").strip() not in ("0", "false", "non")


def seuil_attente_s() -> float:
    return _flottant("AGENT_SEUIL_ATTENTE_S", 45.0)


def delai_bascule_auto_s() -> float:
    return _flottant("AGENT_BASCULE_AUTO_S", 120.0)


def periode_suivi_s() -> float:
    return _flottant("AGENT_PERIODE_SUIVI_S", 2.0)


def battement_perdu_s() -> float:
    return _flottant("AGENT_BATTEMENT_PERDU_S", 120.0)


def est_long(outil: str) -> bool:
    return outil in OUTILS_LONGS


def lancement_direct(outil: str, arguments: dict | None) -> bool:
    if outil in OUTILS_DIRECTS:
        return True
    try:
        delai = float((arguments or {}).get("timeout") or 0)
    except (TypeError, ValueError):
        delai = 0
    return outil in ("execute_python", "run_processing") and delai >= _DELAI_DECLARE_LOURD_S


def soumission_du_modele(outil: str, arguments: dict | None) -> tuple[str, dict]:
    """Un `execute_async` emis par le modele, ramene a l'outil qu'il porte.

    Constat live du 2026-10-02 : sur « traitement en arriere-plan », le modele
    a appele `execute_async(code=…)` lui-meme. La tache a tourne, mais hors du
    registre du hub : ni suivi, ni message de fin, ni garde « QGIS occupe ».
    La tache de fond est l'affaire de l'agent : on rend l'outil reel, declare
    lourd (delai d'au moins 5 min), qui part donc directement en
    arriere-plan par le circuit commun. Tout autre outil est rendu tel quel.
    """
    if outil != "execute_async":
        return outil, arguments or {}
    args = dict(arguments or {})
    if args.get("tool"):
        return str(args["tool"]), dict(args.get("arguments") or {})
    action = str(args.get("action") or "execute_python")
    if action != "execute_python":
        return action, dict(args.get("params") or {})
    reel = {"code": args.get("code") or (args.get("params") or {}).get("code") or ""}
    try:
        delai = float(args.get("timeout") or 0)
    except (TypeError, ValueError):
        delai = 0
    reel["timeout"] = max(delai, _DELAI_DECLARE_LOURD_S)
    return "execute_python", reel


# ── Libelles (francais courant, sans nom de fonction) ────────────────────────

_LIBELLES = {
    "execute_python":      "Calcul dans QGIS",
    "run_processing":      "Traitement QGIS",
    "run_recipe":          "Recette",
    "smart_load":          "Chargement des données",
    "add_from_catalog":    "Chargement d'une source du catalogue",
    "clip_to_study_zone":  "Découpage à la zone d'étude",
    "export_flood_map":    "Export de la carte inondation",
    "export_web_map":      "Export de la carte web",
    "export_temporal_map": "Export de la carte temporelle",
    "export_qfield":       "Export pour QField",
    "export_grist":        "Export vers Grist",
    "densite_par_maille":  "Calcul de la densité par maille",
    "compter_par_zone":    "Comptage par zone",
}


def libelle(outil: str, arguments: dict | None = None) -> str:
    base = _LIBELLES.get(outil, "Traitement QGIS")
    args = arguments or {}
    precision = ""
    if outil == "run_recipe":
        precision = str(args.get("id") or "")
        if args.get("zone"):
            precision += f" sur {args['zone']}"
    elif outil in ("smart_load", "add_from_catalog"):
        precision = str(args.get("id") or "")
    elif outil == "run_processing":
        precision = str(args.get("algorithm") or "")
    precision = precision.strip()[:60]
    return f"{base} ({precision})" if precision else base


def duree_lisible(secondes: float) -> str:
    s = max(0, int(round(secondes)))
    if s < 60:
        return f"{s} s"
    minutes, reste = divmod(s, 60)
    if minutes < 60:
        return f"{minutes} min {reste:02d} s" if reste else f"{minutes} min"
    heures, minutes = divmod(minutes, 60)
    return f"{heures} h {minutes:02d} min"


# ── Lecture d'un suivi ───────────────────────────────────────────────────────

# Etats du registre (hub) ; les memes de bout en bout.
EN_ATTENTE, EN_COURS = "en_attente", "en_cours"
TERMINEE, ECHOUEE, ANNULEE, INTERROMPUE = "terminee", "echouee", "annulee", "interrompue"
ACTIFS = frozenset({EN_ATTENTE, EN_COURS})
FINALS = frozenset({TERMINEE, ECHOUEE, ANNULEE, INTERROMPUE})

_STATUTS_PERDUS = frozenset({"dropped", "bridge_unreachable", "client_disconnected"})


def lire_suivi(contenu: list | None) -> tuple[dict, list]:
    """Separe le bloc d'etat (premier texte JSON) du contenu de l'outil."""
    elements = list(contenu or [])
    if not elements:
        return {}, []
    premier = elements[0]
    try:
        etat = json.loads(premier.get("text") or "")
    except (AttributeError, ValueError, TypeError):
        return {"error": str(premier)[:300]}, elements[1:]
    return (etat if isinstance(etat, dict) else {}), elements[1:]


def classer(etat: dict, battement_max_s: float | None = None) -> str:
    """Etat du registre pour un bloc renvoye par `poll_job`."""
    if not etat:
        return EN_COURS
    erreur = str(etat.get("error") or "")
    statut = etat.get("status")
    if not statut and "Unknown job_id" in erreur:
        return INTERROMPUE
    if statut == "queued":
        return EN_ATTENTE
    if statut in ("running", "qt_frozen"):
        # qt_frozen est normal pendant un script long : il tient le fil
        # principal. Seul un battement arrete trahit une tache perdue.
        age = etat.get("heartbeat_age_s")
        limite = battement_perdu_s() if battement_max_s is None else battement_max_s
        if isinstance(age, (int, float)) and age > limite:
            return INTERROMPUE
        return EN_COURS
    if statut == "done":
        return ECHOUEE if etat.get("is_error") else TERMINEE
    if statut == "error":
        return ECHOUEE
    if statut == "cancelled":
        return ANNULEE
    if statut in _STATUTS_PERDUS:
        return INTERROMPUE
    if erreur and not statut:
        # Erreur de suivi (reseau...) : on ne conclut rien.
        return EN_COURS
    return EN_COURS


# ── Decisions de l'utilisateur pendant l'attente ─────────────────────────────

CHOIX = ("arriere_plan", "attendre", "annuler")


@dataclass
class _Attente:
    evenement: asyncio.Event = field(default_factory=asyncio.Event)
    choix: str | None = None


_ATTENTES: dict[str, _Attente] = {}


def ouvrir_attente(tache_id: str) -> None:
    _ATTENTES[tache_id] = _Attente()


def fermer_attente(tache_id: str) -> None:
    _ATTENTES.pop(tache_id, None)


def poser_decision(tache_id: str, choix: str) -> bool:
    """Depose le choix de l'utilisateur. Faux si aucun tour n'attend."""
    if choix not in CHOIX:
        return False
    attente = _ATTENTES.get(tache_id)
    if attente is None:
        return False
    attente.choix = choix
    attente.evenement.set()
    return True


def prendre_decision(tache_id: str) -> str | None:
    attente = _ATTENTES.get(tache_id)
    if attente is None or attente.choix is None:
        return None
    choix, attente.choix = attente.choix, None
    attente.evenement.clear()
    return choix


async def patienter(tache_id: str, secondes: float) -> None:
    """Dort `secondes`, ou moins si une decision arrive."""
    attente = _ATTENTES.get(tache_id)
    if attente is None:
        await asyncio.sleep(secondes)
        return
    try:
        await asyncio.wait_for(attente.evenement.wait(), timeout=secondes)
    except asyncio.TimeoutError:
        pass


# ── Registre du hub ──────────────────────────────────────────────────────────

class Registre:
    """Client du registre des taches du hub (`/taches`)."""

    _cache_actives: tuple[float, list] = (0.0, [])

    def __init__(self, hub_url: str, hub_key: str):
        self.hub_url = (hub_url or "").rstrip("/")
        self.hub_key = hub_key or ""

    def _entetes(self) -> dict:
        return {"Authorization": f"Bearer {self.hub_key}"}

    async def creer(self, tache: dict) -> bool:
        if not (self.hub_url and self.hub_key):
            return False
        try:
            async with httpx.AsyncClient(timeout=10) as c:
                r = await c.post(f"{self.hub_url}/taches", json=tache,
                                 headers=self._entetes())
            Registre._cache_actives = (0.0, [])
            return r.status_code < 300
        except Exception as exc:
            log.warning("registre des taches : creation impossible (%s)", exc)
            return False

    async def maj(self, tache_id: str, **champs) -> bool:
        if not (self.hub_url and self.hub_key):
            return False
        try:
            async with httpx.AsyncClient(timeout=10) as c:
                r = await c.patch(f"{self.hub_url}/taches/{tache_id}", json=champs,
                                  headers=self._entetes())
            Registre._cache_actives = (0.0, [])
            return r.status_code < 300
        except Exception as exc:
            log.warning("registre des taches : mise a jour impossible (%s)", exc)
            return False

    async def lire(self, tache_id: str) -> dict | None:
        if not (self.hub_url and self.hub_key):
            return None
        try:
            async with httpx.AsyncClient(timeout=10) as c:
                r = await c.get(f"{self.hub_url}/taches/{tache_id}",
                                headers=self._entetes())
            return r.json() if r.status_code == 200 else None
        except Exception:
            return None

    async def actives(self, fraicheur_s: float = 3.0) -> list | None:
        """Taches qui occupent QGIS (cache court). None si le hub ne repond pas."""
        quand, valeur = Registre._cache_actives
        if time.monotonic() - quand < fraicheur_s:
            return valeur
        if not (self.hub_url and self.hub_key):
            return None
        try:
            async with httpx.AsyncClient(timeout=5) as c:
                r = await c.get(f"{self.hub_url}/taches", params={"actives": "1"},
                                headers=self._entetes())
            if r.status_code != 200:
                return None
            donnees = r.json()
            taches = donnees.get("taches", []) if isinstance(donnees, dict) else []
        except Exception:
            return None
        Registre._cache_actives = (time.monotonic(), taches)
        return taches


def oublier_cache() -> None:
    Registre._cache_actives = (0.0, [])


# ── QGIS est-il occupe ? ─────────────────────────────────────────────────────
#
# Le registre du hub fait foi, mais l'interroger avant CHAQUE outil couterait
# un aller-retour reseau a chaque tour, meme sans aucune tache. L'agent garde
# donc la liste des taches qu'il sait actives (les siennes, plus celles que la
# veille periodique de main.py rapporte du hub) et n'interroge le hub que si
# elle n'est pas vide.

_ACTIVES_CONNUES: set[str] = set()


def marquer_active(tache_id: str) -> None:
    _ACTIVES_CONNUES.add(tache_id)


def marquer_finie(tache_id: str) -> None:
    _ACTIVES_CONNUES.discard(tache_id)
    oublier_cache()


def peut_etre_occupe() -> bool:
    return bool(_ACTIVES_CONNUES)


def synchroniser(taches: list) -> None:
    """Aligne la liste locale sur les taches actives rapportees par le hub."""
    _ACTIVES_CONNUES.clear()
    _ACTIVES_CONNUES.update(str(t.get("id")) for t in taches or [] if t.get("id"))


async def taches_qui_occupent(registre: "Registre") -> list:
    """Taches actives qui occupent QGIS (vide sans appel reseau si aucune)."""
    if not peut_etre_occupe():
        return []
    taches = await registre.actives()
    if taches is None:
        # Hub muet : dans le doute, QGIS reste occupe par ce qu'on sait.
        return [{"id": t, "libelle": "un calcul"} for t in sorted(_ACTIVES_CONNUES)]
    synchroniser(taches)
    return taches


# ── Ce que lit le modele ─────────────────────────────────────────────────────

def resultat_occupe(tache: dict, outil: str) -> str:
    """Resultat rendu au modele a la place d'un appel qui casserait QGIS."""
    ecoule = time.time() - float(tache.get("cree_at") or time.time())
    return json.dumps({
        "success": False,
        "qgis_occupe": True,
        "outil_non_lance": outil,
        "calcul_en_cours": tache.get("libelle") or "un calcul",
        "depuis": duree_lisible(ecoule),
        "consigne": (
            "QGIS est occupé par un calcul en arrière-plan. Ne relance pas cet "
            "outil et n'en essaie pas un autre sur la carte. Dis simplement à "
            "l'utilisateur que tu le feras dès que le calcul en cours sera "
            "terminé ; le résultat du calcul s'affichera dans la conversation."
        ),
    }, ensure_ascii=False)


def note_occupe(taches: list) -> str:
    """Note de debut de tour quand une tache occupe QGIS."""
    t = taches[0]
    ecoule = time.time() - float(t.get("cree_at") or time.time())
    return (
        f"Un calcul tourne en arrière-plan dans QGIS : « {t.get('libelle') or 'calcul'} », "
        f"depuis {duree_lisible(ecoule)}. QGIS est occupé : n'appelle aucun outil "
        "sur la carte avant la fin de ce calcul. Si l'utilisateur demande une "
        "action sur la carte, dis-lui que tu la feras dès que le calcul en cours "
        "sera terminé. Le résultat sera ajouté à la conversation automatiquement."
    )


def resultat_bascule(tache: dict) -> str:
    return json.dumps({
        "en_arriere_plan": True,
        "tache_id": tache.get("id"),
        "calcul": tache.get("libelle"),
        "consigne": ("Le calcul continue en arrière-plan. N'appelle plus "
                     "d'outil QGIS dans ce tour ; le résultat sera ajouté "
                     "à la conversation à la fin du calcul."),
    }, ensure_ascii=False)


def resultat_annule(tache: dict, commence: bool) -> str:
    note = ("Le calcul avait déjà commencé dans QGIS : il ne peut pas être "
            "interrompu, il se terminera mais son résultat sera ignoré."
            if commence else "Le calcul n'avait pas commencé : rien n'a été fait.")
    return json.dumps({"success": False, "annule_par_l_utilisateur": True,
                       "calcul": tache.get("libelle"), "note": note},
                      ensure_ascii=False)


def resultat_interrompu(tache: dict, raison: str) -> str:
    return json.dumps({"success": False, "tache_interrompue": True,
                       "calcul": tache.get("libelle"), "raison": raison,
                       "consigne": ("Explique simplement à l'utilisateur que le "
                                    "calcul s'est interrompu et propose de le relancer.")},
                      ensure_ascii=False)


def texte_bascule(tache: dict, automatique: bool, direct: bool = False) -> str:
    """Ce que lit l'utilisateur quand le tour se termine sur une bascule."""
    nom = tache.get("libelle") or "Le calcul"
    if direct:
        debut = f"J'ai lancé « {nom} » en arrière-plan : c'est un traitement long."
    elif automatique:
        debut = (f"Sans réponse de votre part, « {nom} » continue en arrière-plan.")
    else:
        debut = f"D'accord : « {nom} » continue en arrière-plan."
    return (
        f"\n\n{debut} Vous pouvez continuer à discuter. Le résultat "
        "s'affichera ici dès qu'il sera prêt, et la pastille « calculs en "
        "cours » du bureau indique où il en est. Les actions sur la carte "
        "attendront la fin du calcul, car QGIS ne fait qu'une chose à la fois."
    )


# ── Message de fin, rattache a la conversation ──────────────────────────────

_RE_NOMBRE = re.compile(r"\d[\d\s  .,]*\d|\d")


def _nombres(texte: str) -> set[str]:
    """Nombres d'un texte, normalises (espaces fines et separateurs retires)."""
    vus = set()
    for brut in _RE_NOMBRE.findall(texte or ""):
        propre = re.sub(r"[\s  ]", "", brut)
        # 4 812 / 4812 / 4.812 / 4,812 : on garde les chiffres, plus la
        # partie entiere seule pour tolerer un arrondi d'affichage.
        chiffres = re.sub(r"[.,]", "", propre)
        vus.add(chiffres)
        vus.add(re.split(r"[.,]", propre)[0])
    return {v for v in vus if v}


def chiffres_coherents(texte: str, source: str) -> bool:
    """Tous les nombres du texte figurent-ils dans le resultat ?"""
    autorises = _nombres(source)
    return all(n in autorises for n in _nombres(texte))


def _faits(resultat: str) -> list[str]:
    """Faits lisibles extraits du resultat (repli sans modele)."""
    faits: list[str] = []
    try:
        donnees = json.loads(resultat)
    except (TypeError, ValueError):
        m = re.search(r"\{.*\}", resultat or "", flags=re.S)
        try:
            donnees = json.loads(m.group(0)) if m else {}
        except ValueError:
            donnees = {}
    if not isinstance(donnees, dict):
        return faits
    verif = donnees.get("verification")
    if isinstance(verif, dict):
        n = verif.get("count") if verif.get("count") is not None else verif.get("feature_count")
        if n is not None:
            faits.append(f"{n} entités vérifiées")
        for avert in (verif.get("warnings") or [])[:2]:
            faits.append(f"point d'attention : {str(avert)[:120]}")
    for cle in ("layer_name", "name"):
        if isinstance(donnees.get(cle), str) and donnees[cle]:
            faits.append(f"couche « {donnees[cle][:80]} »")
            break
    if donnees.get("feature_count") is not None:
        faits.append(f"{donnees['feature_count']} entités")
    if isinstance(donnees.get("succeeded"), int):
        faits.append(f"{donnees['succeeded']} étapes réussies")
    if isinstance(donnees.get("failed"), int) and donnees["failed"]:
        faits.append(f"{donnees['failed']} étapes en échec")
    if isinstance(donnees.get("result"), dict):
        for cle, val in list(donnees["result"].items())[:4]:
            if isinstance(val, (int, float, str)) and len(str(val)) <= 60:
                faits.append(f"{cle} : {val}")
    if donnees.get("error"):
        faits.append(f"erreur : {str(donnees['error'])[:160]}")
    return faits


def message_fin_deterministe(tache: dict) -> str:
    nom = tache.get("libelle") or "Le calcul"
    statut = tache.get("statut")
    if statut == TERMINEE:
        faits = _faits(tache.get("resultat") or "")
        corps = f"« {nom} » est terminé."
        if faits:
            corps += " " + " ; ".join(faits[:4]) + "."
        return corps + " Vous pouvez reprendre la suite quand vous voulez."
    if statut == ECHOUEE:
        faits = [f for f in _faits(tache.get("resultat") or tache.get("erreur") or "")
                 if f.startswith("erreur")]
        detail = f" ({faits[0]})" if faits else (
            f" ({str(tache.get('erreur'))[:160]})" if tache.get("erreur") else "")
        return (f"« {nom} » s'est terminé en erreur{detail}. Dites-moi si je "
                "dois corriger et relancer.")
    if statut == ANNULEE:
        return (f"« {nom} », que vous aviez annulé, est maintenant terminé dans "
                "QGIS. Son résultat a été ignoré ; vérifiez la liste des couches "
                "si besoin.")
    if statut == INTERROMPUE:
        raison = tache.get("raison") or "QGIS ne répond plus"
        return (f"« {nom} » s'est interrompu : {raison}. Vous pouvez le relancer "
                "ou l'abandonner depuis la pastille « calculs » du bureau, ou me "
                "le demander ici.")
    return f"« {nom} » : {statut}."


_CONSIGNE_REDACTION = (
    "Tu rédiges, en français courant, le compte rendu d'un calcul QGIS qui "
    "vient de se terminer en arrière-plan. 2 à 4 phrases, sans titre, sans "
    "liste, sans code, sans nom de fonction ni jargon technique. Dis ce qui a "
    "été produit (couches, nombre d'entités, contrôle de vérification) et un "
    "éventuel point d'attention. N'utilise AUCUN chiffre absent du résultat "
    "fourni. Ne propose pas d'autre action que de poursuivre."
)


async def rediger_message_fin(
    tache: dict, appel_modele: Callable[[list[dict]], Awaitable[str]] | None,
) -> str:
    """Message de fin court, chiffres issus du resultat seulement.

    Le modele redige seulement les fins reussies ; s'il echoue, ou s'il cite
    un chiffre absent du resultat, on se rabat sur un message fixe.
    """
    if tache.get("statut") != TERMINEE or appel_modele is None:
        return message_fin_deterministe(tache)
    resultat = (tache.get("resultat") or "")[:6000]
    messages = [
        {"role": "system", "content": _CONSIGNE_REDACTION},
        {"role": "user", "content": (
            f"Calcul : {tache.get('libelle')}\n"
            f"Demande d'origine : outil {tache.get('outil')}\n"
            f"Résultat brut :\n{resultat}")},
    ]
    try:
        texte = (await appel_modele(messages) or "").strip()
    except Exception as exc:
        log.warning("redaction du message de fin impossible : %s", exc)
        texte = ""
    if not texte or len(texte) > 900 or not chiffres_coherents(texte, resultat):
        if texte:
            log.info("message de fin du modele ecarte (chiffres hors resultat)")
        return message_fin_deterministe(tache)
    return f"« {tache.get('libelle') or 'Le calcul'} » est terminé. {texte}"


# Debut du message rattache : on sait d'ou il vient, a l'ecran comme au
# rechargement de la conversation.
ENTETE_MESSAGE_FIN = "**Calcul en arrière-plan** · "


@dataclass
class Issue:
    """Fin de l'execution d'un outil long dans le tour."""
    resultat: str
    bascule: bool = False
    tache: dict | None = None
    automatique: bool = False
    direct: bool = False


async def rattacher(
    tache_id: str,
    registre: Registre,
    ajouter_message: Callable[[str, str, str, list | None], Awaitable[None]],
    appel_modele: Callable[[list[dict]], Awaitable[str]] | None,
    apres: Callable[[dict], Awaitable[Any]] | None = None,
) -> dict:
    """Rattache la fin d'une tache a sa conversation (idempotent).

    `ajouter_message(session_id, role, texte, tool_calls)` persiste le message
    dans la conversation d'origine.
    """
    verrou = _VERROUS.setdefault(tache_id, asyncio.Lock())
    async with verrou:
        tache = await registre.lire(tache_id)
        if not tache:
            return {"ok": False, "raison": "tache inconnue"}
        if tache.get("rattachee"):
            return {"ok": True, "deja": True, "message": tache.get("message")}
        if tache.get("statut") not in FINALS:
            return {"ok": False, "raison": "tache non terminee"}
        texte = await rediger_message_fin(tache, appel_modele)
        message = ENTETE_MESSAGE_FIN + texte
        session_id = tache.get("session_id") or ""
        memo = [{"tool": tache.get("outil"), "args": {"tache": tache_id},
                 "result": (tache.get("resultat") or tache.get("erreur") or "")[:200]}]
        if session_id:
            await ajouter_message(session_id, "assistant", message, memo)
        await registre.maj(tache_id, rattachee=True, message=message,
                           rattachee_at=time.time())
        if apres is not None:
            try:
                await apres(tache)
            except Exception as exc:
                log.warning("apres rattachement %s : %s", tache_id, exc)
        return {"ok": True, "message": message}


_VERROUS: dict[str, asyncio.Lock] = {}
