"""Traitements en arriere-plan, avec bascule proposee (spec 2026-10-02).

Constat live du 2026-10-02 : un `execute_python` de densite a tenu le tour et
le chat plus de 12 minutes ; puis la reponse illisible a declenche deux
relances automatiques, donc deux nouvelles executions du meme script.

On deroule la vraie `chat_stream` contre un modele simule ET un workspace
simule (execute_async / poll_job / cancel_job), avec un registre du hub en
memoire. Aucun reseau.
"""
from __future__ import annotations

import asyncio
import copy
import json
import sys
from pathlib import Path

import httpx
import pytest

_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

if "sqlite_vec" not in sys.modules:
    _vec = type(sys)("sqlite_vec")
    _vec.load = lambda *a, **k: None          # type: ignore[attr-defined]
    _vec.loadable_path = lambda: ""           # type: ignore[attr-defined]
    sys.modules["sqlite_vec"] = _vec

from agent import arriere_plan as ap   # noqa: E402
from agent import memory               # noqa: E402
from agent import qgis_agent as qa     # noqa: E402

from test_appel_ecrit_relance import (  # noqa: E402
    _Flux, _appel_outil, _reponse_texte, _run, _texte,
)


# ── Reseau simule : modele, workspace (via le hub /mcp) et registre ─────────

class _R:
    def __init__(self, code=200, corps=None, texte=None):
        self.status_code = code
        self._corps = corps
        self.text = texte if texte is not None else json.dumps(corps)
        self.content = self.text.encode()
        self.headers = {"content-type": "application/json"}

    def json(self):
        if self._corps is None:
            return json.loads(self.text)
        return self._corps


def _contenu(etat, suite=None):
    return {"content": [{"type": "text", "text": json.dumps(etat)}] + list(suite or [])}


class _Reseau:
    script: list = []          # reponses du modele
    envois: list = []          # requetes au modele
    taches: dict = {}          # registre du hub
    suivis: list = []          # etats successifs rendus par poll_job
    fin: list = []             # contenu de l'outil, rendu avec l'etat done
    appels: list = []          # (outil, arguments) recus par le workspace
    annulation: dict = {"success": True, "status": "cancelled"}
    soumission: dict | None = None   # reponse forcee a execute_async

    def __init__(self, *a, **k):
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    def stream(self, methode, url, json=None, headers=None):
        _Reseau.envois.append(copy.deepcopy(json))
        if not _Reseau.script:
            raise AssertionError("le modele a ete appele plus souvent que prevu")
        return _Flux(_Reseau.script.pop(0))

    async def post(self, url, json=None, headers=None, **k):
        if url.endswith("/mcp"):
            return self._mcp(json["params"]["name"], json["params"]["arguments"])
        if url.endswith("/taches"):
            _Reseau.taches[json["id"]] = dict(json)
            return _R(201, json)
        return _R(404, {})

    async def patch(self, url, json=None, headers=None, **k):
        tid = url.rsplit("/", 1)[1]
        _Reseau.taches.setdefault(tid, {"id": tid}).update(json)
        return _R(200, _Reseau.taches[tid])

    async def get(self, url, params=None, headers=None, **k):
        if url.endswith("/taches"):
            actives = [t for t in _Reseau.taches.values()
                       if t.get("statut") in ap.ACTIFS]
            return _R(200, {"taches": actives})
        if "/taches/" in url:
            t = _Reseau.taches.get(url.rsplit("/", 1)[1])
            return _R(200, t) if t else _R(404, {})
        if url.endswith("/studies/active"):
            return _R(200, {"id": "etude-1"})
        return _R(404, {})

    def _mcp(self, nom, args):
        _Reseau.appels.append((nom, copy.deepcopy(args)))
        if nom == "execute_async":
            if _Reseau.soumission is not None:
                return _R(200, {"result": _Reseau.soumission})
            return _R(200, {"result": _contenu(
                {"job_id": "t-1", "status": "queued", "tool": args.get("tool")})})
        if nom == "poll_job":
            etat = _Reseau.suivis.pop(0) if len(_Reseau.suivis) > 1 else _Reseau.suivis[0]
            suite = _Reseau.fin if etat.get("status") == "done" else None
            return _R(200, {"result": _contenu(etat, suite)})
        if nom == "cancel_job":
            return _R(200, {"result": _contenu(_Reseau.annulation)})
        return _R(200, {"result": _contenu({"success": True, "outil": nom})})


_EN_COURS = {"job_id": "t-1", "status": "running", "heartbeat_age_s": 0.5}
_FINI = {"job_id": "t-1", "status": "done", "heartbeat_age_s": 0.1}
_RESULTAT = {"success": True, "result": {"total": 4812},
             "verification": {"count": 4812, "warnings": []}}


@pytest.fixture
def agent(monkeypatch):
    persistes: list = []

    async def _rien(*a, **k):
        return None

    async def _persister(session_id, role, content, tool_calls=None):
        if role == "assistant":
            persistes.append(content)

    for nom in ("set_session_tag", "add_insight"):
        monkeypatch.setattr(memory, nom, _rien, raising=False)
    monkeypatch.setattr(memory, "add_message", _persister)

    async def _modele(profil):
        return "modele-essai"

    async def _prompt(self, user_message=None):
        return "consigne systeme"

    async def _outils(self):
        return [{"type": "function",
                 "function": {"name": nom, "description": "",
                              "parameters": {"type": "object", "properties": {}}}}
                for nom in ("execute_python", "run_recipe", "zoom_to",
                            "get_project_info")]

    directs: list = []

    async def _outil_direct(nom, args, username=None, **k):
        directs.append(nom)
        return '{"success": true, "direct": true}'

    monkeypatch.setenv("AGENT_ARRIERE_PLAN", "1")
    monkeypatch.setenv("AGENT_SEUIL_ATTENTE_S", "0.05")
    monkeypatch.setenv("AGENT_BASCULE_AUTO_S", "0.1")
    monkeypatch.setenv("AGENT_PERIODE_SUIVI_S", "0.01")
    monkeypatch.setattr(qa, "_resolve_model", _modele)
    monkeypatch.setattr(qa.QGISAgent, "_build_system_prompt", _prompt)
    monkeypatch.setattr(qa.QGISAgent, "_get_tools", _outils)
    monkeypatch.setattr(qa, "_call_mcp_tool", _outil_direct)
    monkeypatch.setattr(qa, "_append_history", _rien)
    monkeypatch.setattr(qa.httpx, "AsyncClient", _Reseau)
    monkeypatch.setattr(qa._REGISTRE_TACHES, "hub_url", "https://hub.essai")
    monkeypatch.setattr(qa._REGISTRE_TACHES, "hub_key", "cle")

    _Reseau.script, _Reseau.envois, _Reseau.taches = [], [], {}
    _Reseau.suivis, _Reseau.fin, _Reseau.appels = [_FINI], [], []
    _Reseau.annulation = {"success": True, "status": "cancelled"}
    _Reseau.soumission = None
    ap._ACTIVES_CONNUES.clear()
    ap._ATTENTES.clear()
    ap.oublier_cache()
    a = qa.QGISAgent(username="user", session_id="essai-fond", profile_id="standard")
    a.persistes = persistes
    a.directs = directs
    yield a
    ap._ACTIVES_CONNUES.clear()
    ap._ATTENTES.clear()
    ap.oublier_cache()


def _tour(agent, demande, script, reaction=None, stop=None):
    _Reseau.script = script

    async def _go():
        evenements = []
        async for e in agent.chat_stream(demande, history=[], stop_signal=stop):
            evenements.append(e)
            if reaction is not None:
                reaction(e)
        return evenements

    return _run(_go())


def _taches_evts(evenements):
    return [e for e in evenements if isinstance(e, dict) and "tache" in e]


def _noms(evenements):
    return [e["tache"] for e in _taches_evts(evenements)]


def _appels(nom):
    return [a for a in _Reseau.appels if a[0] == nom]


_CODE = json.dumps({"code": "result['total'] = 4812"})


# ── 1. Fini avant le seuil : meme contrat que l'appel direct ────────────────

def test_fini_avant_le_seuil_le_modele_recoit_le_resultat_de_l_outil(agent, monkeypatch):
    monkeypatch.setenv("AGENT_SEUIL_ATTENTE_S", "30")
    _Reseau.suivis = [_EN_COURS, _FINI]
    _Reseau.fin = [{"type": "text", "text": json.dumps(_RESULTAT)}]
    evts = _tour(agent, "calcule la densité", [
        _appel_outil("execute_python", _CODE),
        _reponse_texte("La densité est calculée : 4812 bâtiments."),
    ])
    soumission = _appels("execute_async")[0][1]
    assert soumission["tool"] == "execute_python"
    assert soumission["arguments"] == {"code": "result['total'] = 4812"}
    assert soumission["client_id"].startswith("essai-fond:")
    # Le modele recoit exactement le contenu de l'outil.
    outil = [m for m in _Reseau.envois[1]["messages"] if m["role"] == "tool"][0]
    assert json.loads(outil["content"]) == _RESULTAT
    assert "proposition" not in _noms(evts)
    assert agent.directs == []
    (tache,) = _Reseau.taches.values()
    assert tache["statut"] == ap.TERMINEE and tache["rattachee"] is True
    assert tache["session_id"] == "essai-fond" and tache["sid"] == "etude-1"
    assert "La densité est calculée" in _texte(evts)
    assert not ap.peut_etre_occupe()


# ── 2. Seuil depasse, sans reponse : bascule automatique ────────────────────

def test_au_dela_du_seuil_la_bascule_est_proposee_puis_automatique(agent):
    _Reseau.suivis = [_EN_COURS]
    evts = _tour(agent, "calcule la densité", [_appel_outil("execute_python", _CODE)])
    noms = _noms(evts)
    assert noms[0] == "soumise"
    assert noms.index("proposition") < noms.index("arriere_plan")
    proposition = next(e for e in _taches_evts(evts) if e["tache"] == "proposition")
    assert proposition["choix"] == ["arriere_plan", "attendre", "annuler"]
    assert proposition["libelle"] == "Calcul dans QGIS"
    bascule = next(e for e in _taches_evts(evts) if e["tache"] == "arriere_plan")
    assert bascule["automatique"] is True
    # Le tour se termine proprement, sans nouvel appel au modele.
    assert len(_Reseau.envois) == 1
    texte = _texte(evts)
    assert "continue en arrière-plan" in texte
    assert "Vous pouvez continuer à discuter" in texte
    assert "continue en arrière-plan" in agent.persistes[-1]
    (tache,) = _Reseau.taches.values()
    assert tache["mode"] == "arriere_plan" and tache["statut"] in ap.ACTIFS
    # QGIS reste occupe : rien d'autre n'est lance (ni capture, ni sauvegarde).
    assert ap.peut_etre_occupe()
    assert [a[0] for a in _Reseau.appels if a[0] not in ("execute_async", "poll_job")] == []


def test_l_utilisateur_choisit_de_continuer_en_arriere_plan(agent, monkeypatch):
    monkeypatch.setenv("AGENT_BASCULE_AUTO_S", "60")
    _Reseau.suivis = [_EN_COURS]

    def _reaction(e):
        if isinstance(e, dict) and e.get("tache") == "proposition":
            assert ap.poser_decision(e["tache_id"], "arriere_plan")

    evts = _tour(agent, "calcule", [_appel_outil("execute_python", _CODE)], _reaction)
    bascule = next(e for e in _taches_evts(evts) if e["tache"] == "arriere_plan")
    assert bascule["automatique"] is False
    assert "D'accord : « Calcul dans QGIS » continue en arrière-plan." in _texte(evts)


def test_attendre_suspend_la_bascule_automatique(agent):
    _Reseau.suivis = [_EN_COURS]
    _Reseau.fin = [{"type": "text", "text": json.dumps(_RESULTAT)}]

    def _reaction(e):
        if isinstance(e, dict) and e.get("tache") == "proposition":
            ap.poser_decision(e["tache_id"], "attendre")
        if isinstance(e, dict) and e.get("tache") == "attente":
            # Bien au-dela du delai de bascule automatique (0,1 s).
            _Reseau.suivis = [_EN_COURS] * 30 + [_FINI]

    evts = _tour(agent, "calcule", [
        _appel_outil("execute_python", _CODE),
        _reponse_texte("Fini : 4812 bâtiments."),
    ], _reaction)
    noms = _noms(evts)
    assert "attente" in noms and "arriere_plan" not in noms
    assert noms[-1] == "fin_attente"
    assert "Fini : 4812 bâtiments." in _texte(evts)


def test_annuler_une_tache_en_attente(agent, monkeypatch):
    monkeypatch.setenv("AGENT_BASCULE_AUTO_S", "60")
    _Reseau.suivis = [{"job_id": "t-1", "status": "queued", "heartbeat_age_s": 0.1}]

    def _reaction(e):
        if isinstance(e, dict) and e.get("tache") == "proposition":
            ap.poser_decision(e["tache_id"], "annuler")

    evts = _tour(agent, "calcule", [
        _appel_outil("execute_python", _CODE),
        _reponse_texte("C'est annulé."),
    ], _reaction)
    assert _appels("cancel_job")[0][1] == {"job_id": "t-1"}
    annulee = next(e for e in _taches_evts(evts) if e["tache"] == "annulee")
    assert annulee["commence"] is False
    outil = [m for m in _Reseau.envois[1]["messages"] if m["role"] == "tool"][0]
    assert json.loads(outil["content"])["annule_par_l_utilisateur"] is True
    (tache,) = _Reseau.taches.values()
    assert tache["statut"] == ap.ANNULEE
    assert not ap.peut_etre_occupe()


def test_annuler_une_tache_commencee_le_dit_et_garde_qgis_occupe(agent, monkeypatch):
    monkeypatch.setenv("AGENT_BASCULE_AUTO_S", "60")
    _Reseau.suivis = [_EN_COURS]
    _Reseau.annulation = {"success": False, "status": "already_dispatched_cannot_cancel"}

    def _reaction(e):
        if isinstance(e, dict) and e.get("tache") == "proposition":
            ap.poser_decision(e["tache_id"], "annuler")

    _tour(agent, "calcule", [
        _appel_outil("execute_python", _CODE),
        _reponse_texte("Annulation demandée."),
    ], _reaction)
    outil = [m for m in _Reseau.envois[1]["messages"] if m["role"] == "tool"][0]
    assert "ne peut pas être interrompu" in json.loads(outil["content"])["note"]
    (tache,) = _Reseau.taches.values()
    assert tache["annulation_demandee"] is True and tache["statut"] in ap.ACTIFS
    assert ap.peut_etre_occupe()


def test_arreter_pendant_l_attente_bascule_en_arriere_plan(agent):
    _Reseau.suivis = [_EN_COURS]
    stop = asyncio.Event()

    def _reaction(e):
        if isinstance(e, dict) and e.get("tache") == "soumise":
            stop.set()

    evts = _tour(agent, "calcule", [_appel_outil("execute_python", _CODE)],
                 _reaction, stop=stop)
    assert "arriere_plan" in _noms(evts)


# ── 3. Lancement direct quand c'est evident ─────────────────────────────────

def test_une_recette_part_directement_en_arriere_plan(agent):
    evts = _tour(agent, "lance la recette", [
        _appel_outil("run_recipe", json.dumps({"id": "densite_bati", "zone": "Rousset"})),
    ])
    assert _appels("poll_job") == []
    bascule = next(e for e in _taches_evts(evts) if e["tache"] == "arriere_plan")
    assert bascule["direct"] is True
    assert "proposition" not in _noms(evts)
    assert "J'ai lancé « Recette (densite_bati sur Rousset) » en arrière-plan" in _texte(evts)


def test_un_delai_declare_long_part_directement(agent):
    evts = _tour(agent, "calcule", [
        _appel_outil("execute_python", json.dumps({"code": "x=1", "timeout": 900})),
    ])
    assert _appels("poll_job") == []
    assert "arriere_plan" in _noms(evts)


def test_un_execute_async_emis_par_le_modele_passe_par_le_registre(agent):
    """Live du 2026-10-02 : `execute_async(code=…)` appele par le modele
    tournait hors du registre (ni suivi, ni message de fin)."""
    evts = _tour(agent, "exécute ce script en arrière-plan", [
        _appel_outil("execute_async", json.dumps(
            {"code": "import time; time.sleep(90)", "timeout": 120})),
    ])
    (soumission,) = [a[1] for a in _appels("execute_async")]
    assert soumission["tool"] == "execute_python"
    assert soumission["arguments"] == {"code": "import time; time.sleep(90)",
                                       "timeout": 300}
    assert "client_id" in soumission
    (tache,) = _Reseau.taches.values()
    assert tache["statut"] in ap.ACTIFS
    assert next(e for e in _taches_evts(evts) if e["tache"] == "arriere_plan")["direct"]


@pytest.mark.parametrize("arguments, attendu", [
    ({"tool": "run_processing", "arguments": {"algorithm": "native:buffer"}},
     ("run_processing", {"algorithm": "native:buffer"})),
    ({"action": "run_recipe", "params": {"id": "densite"}},
     ("run_recipe", {"id": "densite"})),
    ({"code": "x=1", "timeout": 900}, ("execute_python", {"code": "x=1", "timeout": 900})),
    ({"code": "x=1"}, ("execute_python", {"code": "x=1", "timeout": 300})),
])
def test_soumission_du_modele_rend_l_outil_reel(arguments, attendu):
    assert ap.soumission_du_modele("execute_async", arguments) == attendu


def test_soumission_du_modele_laisse_les_autres_outils():
    assert ap.soumission_du_modele("zoom_to", {"layer": "a"}) == ("zoom_to", {"layer": "a"})


# ── 4. Tache perdue ─────────────────────────────────────────────────────────

def test_une_tache_que_le_workspace_ne_connait_plus_est_interrompue(agent):
    _Reseau.suivis = [{"error": "Unknown job_id: t-1"}]
    evts = _tour(agent, "calcule", [
        _appel_outil("execute_python", _CODE),
        _reponse_texte("Le calcul s'est interrompu."),
    ])
    assert "interrompue" in _noms(evts)
    outil = [m for m in _Reseau.envois[1]["messages"] if m["role"] == "tool"][0]
    assert json.loads(outil["content"])["tache_interrompue"] is True
    (tache,) = _Reseau.taches.values()
    assert tache["statut"] == ap.INTERROMPUE


def test_un_battement_arrete_interrompt_la_tache(agent):
    _Reseau.suivis = [{"job_id": "t-1", "status": "running", "heartbeat_age_s": 999}]
    evts = _tour(agent, "calcule", [
        _appel_outil("execute_python", _CODE), _reponse_texte("Interrompu."),
    ])
    assert "interrompue" in _noms(evts)


# ── 5. QGIS occupe : jamais d'appel concurrent ──────────────────────────────

def test_une_action_carte_pendant_un_calcul_n_est_pas_lancee(agent):
    _Reseau.taches["tf-occupe"] = {"id": "tf-occupe", "statut": ap.EN_COURS,
                                   "libelle": "Calcul dans QGIS", "cree_at": 0}
    ap.marquer_active("tf-occupe")
    evts = _tour(agent, "zoome sur Rousset", [
        _appel_outil("zoom_to", json.dumps({"extent": "Rousset"})),
        _reponse_texte("Je le ferai dès que le calcul en cours sera terminé."),
    ])
    assert agent.directs == [] and _Reseau.appels == []
    outil = [m for m in _Reseau.envois[1]["messages"] if m["role"] == "tool"][0]
    contenu = json.loads(outil["content"])
    assert contenu["qgis_occupe"] is True and contenu["outil_non_lance"] == "zoom_to"
    # Le modele le sait des le debut du tour.
    premier = _Reseau.envois[0]["messages"]
    assert any("Un calcul tourne en arrière-plan" in (m.get("content") or "")
               for m in premier)
    assert "dès que le calcul en cours sera terminé" in _texte(evts)


def test_sans_tache_connue_aucun_appel_au_registre(agent):
    _Reseau.suivis = [_FINI]
    _Reseau.fin = [{"type": "text", "text": "{}"}]
    _tour(agent, "zoome", [_appel_outil("zoom_to"), _reponse_texte("Fait.")])
    assert agent.directs == ["zoom_to"]


# ── 6. Workspace ancien : appel direct ──────────────────────────────────────

def test_un_workspace_sans_taches_d_outils_repasse_en_direct(agent):
    _Reseau.soumission = {"content": [{"type": "text", "text":
                          "execute_async: 'code' is required when action is 'execute_python'"}],
                          "isError": True}
    _tour(agent, "calcule", [_appel_outil("execute_python", _CODE), _reponse_texte("Ok.")])
    assert agent.directs == ["execute_python"]
    assert _Reseau.taches == {}


# ── 7. Pas de relance automatique d'un outil qui calcule ou modifie ─────────

class _Illisible:
    """Hub qui rend une reponse illisible (ou une exception) a chaque POST."""
    posts: list = []
    mode = "json"

    def __init__(self, *a, **k):
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def post(self, url, json=None, headers=None, **k):
        _Illisible.posts.append(json["params"]["name"])
        if _Illisible.mode == "connexion":
            raise httpx.ConnectError("refuse")
        if _Illisible.mode == "502":
            return _R(502, texte="<html>Bad gateway</html>")
        if _Illisible.mode == "vide":
            return _R(200, texte="")
        return _R(200, texte="<html>coupure apres 12 min</html>")


@pytest.fixture
def hub_illisible(monkeypatch):
    async def _sans_attente(_):
        return None

    monkeypatch.setattr(qa.httpx, "AsyncClient", _Illisible)
    monkeypatch.setattr(qa.asyncio, "sleep", _sans_attente)
    _Illisible.posts = []
    _Illisible.mode = "json"
    return _Illisible


@pytest.mark.parametrize("outil", [
    "execute_python", "run_processing", "run_recipe", "smart_load",
    "export_layer", "publish_artifact", "add_layer", "remove_layer",
])
@pytest.mark.parametrize("mode", ["json", "502", "vide"])
def test_un_outil_mutant_ou_long_n_est_jamais_rejoue(hub_illisible, outil, mode):
    hub_illisible.mode = mode
    sortie = _run(qa._call_mcp_tool_raw(outil, {"code": "x"}))
    assert hub_illisible.posts == [outil], "une seule execution, jamais de relance"
    assert "a pu s'executer" in json.loads(sortie)["error"]


@pytest.mark.parametrize("outil", ["get_project_info", "get_features", "list_files",
                                   "poll_job", "zoom_to"])
def test_une_lecture_ou_un_outil_idempotent_est_rejoue(hub_illisible, outil):
    _run(qa._call_mcp_tool_raw(outil, {}))
    assert hub_illisible.posts == [outil] * 4


def test_une_soumission_avec_client_id_est_rejouee_sans_risque(hub_illisible):
    _run(qa._call_mcp_tool_raw("execute_async", {"tool": "execute_python",
                                                 "client_id": "s:1:a"}))
    assert len(hub_illisible.posts) == 4


def test_une_soumission_sans_client_id_n_est_pas_rejouee(hub_illisible):
    _run(qa._call_mcp_tool_raw("execute_async", {"code": "x"}))
    assert hub_illisible.posts == ["execute_async"]


def test_une_connexion_refusee_est_rejouee_rien_n_est_parti(hub_illisible):
    hub_illisible.mode = "connexion"
    sortie = _run(qa._call_mcp_tool_raw("execute_python", {"code": "x"}))
    assert hub_illisible.posts == ["execute_python"] * 4
    assert "refuse" in json.loads(sortie)["error"]


def test_une_coupure_du_suivi_reprend_le_suivi_sans_resoumettre(agent, monkeypatch):
    """Une reponse illisible pendant le suivi : on reinterroge poll_job, on ne
    soumet jamais une seconde fois le calcul."""
    monkeypatch.setenv("AGENT_SEUIL_ATTENTE_S", "30")
    reel = _Reseau._mcp
    coupures = {"n": 0}

    def _mcp(self, nom, args):
        if nom == "poll_job" and coupures["n"] < 2:
            coupures["n"] += 1
            _Reseau.appels.append((nom, args))
            return _R(200, texte="<html>coupure</html>")
        return reel(self, nom, args)

    async def _sans_attente(_):
        return None

    monkeypatch.setattr(_Reseau, "_mcp", _mcp)
    monkeypatch.setattr(qa.asyncio, "sleep", _sans_attente)
    _Reseau.suivis = [_FINI]
    _Reseau.fin = [{"type": "text", "text": json.dumps(_RESULTAT)}]
    _tour(agent, "calcule", [_appel_outil("execute_python", _CODE), _reponse_texte("Ok.")])
    assert len(_appels("execute_async")) == 1
    assert len(_appels("poll_job")) >= 3


# ── 8. Briques du module ────────────────────────────────────────────────────

def test_les_outils_longs_sont_ceux_mesures_dans_le_code():
    for outil in ("execute_python", "run_processing", "run_recipe", "smart_load",
                  "clip_to_study_zone", "add_from_catalog", "export_flood_map",
                  "export_web_map", "export_temporal_map", "export_qfield", "export_grist",
                  "densite_par_maille", "compter_par_zone"):
        assert ap.est_long(outil), outil
    for outil in ("zoom_to", "get_project_info", "export_layer", "export_pdf"):
        assert not ap.est_long(outil), outil


@pytest.mark.parametrize("etat, attendu", [
    ({"status": "queued"}, ap.EN_ATTENTE),
    ({"status": "running", "heartbeat_age_s": 1}, ap.EN_COURS),
    ({"status": "qt_frozen", "heartbeat_age_s": 1}, ap.EN_COURS),
    ({"status": "running", "heartbeat_age_s": 500}, ap.INTERROMPUE),
    ({"status": "done"}, ap.TERMINEE),
    ({"status": "done", "is_error": True}, ap.ECHOUEE),
    ({"status": "error"}, ap.ECHOUEE),
    ({"status": "cancelled"}, ap.ANNULEE),
    ({"status": "dropped"}, ap.INTERROMPUE),
    ({"error": "Unknown job_id: t-1"}, ap.INTERROMPUE),
    ({"error": "Poll error: timeout"}, ap.EN_COURS),
])
def test_classement_des_etats(etat, attendu):
    assert ap.classer(etat) == attendu


def test_les_chiffres_doivent_venir_du_resultat():
    source = json.dumps({"verification": {"count": 4812}, "result": {"densite": 12.5}})
    assert ap.chiffres_coherents("4 812 bâtiments, densité 12,5.", source)
    assert not ap.chiffres_coherents("5 000 bâtiments environ.", source)


def test_la_duree_se_lit_en_francais():
    assert ap.duree_lisible(45) == "45 s"
    assert ap.duree_lisible(737) == "12 min 17 s"
    assert ap.duree_lisible(3720) == "1 h 02 min"


# ── 9. Message de fin rattache a la conversation ────────────────────────────

class _RegistreMemoire:
    def __init__(self, tache):
        self.tache = tache
        self.majs = []

    async def lire(self, tid):
        return dict(self.tache) if self.tache.get("id") == tid else None

    async def maj(self, tid, **champs):
        self.majs.append(champs)
        self.tache.update(champs)
        return True


def _tache_finie(**extra):
    base = {"id": "tf-1", "session_id": "conv-1", "outil": "execute_python",
            "libelle": "Calcul dans QGIS", "statut": ap.TERMINEE,
            "resultat": json.dumps(_RESULTAT), "rattachee": False}
    base.update(extra)
    return base


def test_le_message_de_fin_est_rattache_une_seule_fois():
    registre = _RegistreMemoire(_tache_finie())
    ajoutes = []

    async def _ajouter(sid, role, texte, appels):
        ajoutes.append((sid, role, texte, appels))

    async def _modele(messages):
        assert "N'utilise AUCUN chiffre absent" in messages[0]["content"]
        return "Les 4812 bâtiments ont été vérifiés."

    r1 = _run(ap.rattacher("tf-1", registre, _ajouter, _modele))
    r2 = _run(ap.rattacher("tf-1", registre, _ajouter, _modele))
    assert r1["ok"] and r2.get("deja")
    assert len(ajoutes) == 1
    sid, role, texte, appels = ajoutes[0]
    assert (sid, role) == ("conv-1", "assistant")
    assert texte.startswith(ap.ENTETE_MESSAGE_FIN)
    assert "Les 4812 bâtiments ont été vérifiés." in texte
    assert appels[0]["tool"] == "execute_python"
    assert registre.tache["rattachee"] is True and registre.tache["message"] == texte


def test_un_chiffre_invente_par_le_modele_est_ecarte():
    registre = _RegistreMemoire(_tache_finie())
    ajoutes = []

    async def _ajouter(sid, role, texte, appels):
        ajoutes.append(texte)

    async def _modele(messages):
        return "Environ 5000 bâtiments ont été traités en 3 minutes."

    _run(ap.rattacher("tf-1", registre, _ajouter, _modele))
    assert "5000" not in ajoutes[0]
    assert "4812 entités vérifiées" in ajoutes[0]


def test_une_tache_interrompue_donne_un_message_fixe_avec_le_choix():
    registre = _RegistreMemoire(_tache_finie(statut=ap.INTERROMPUE,
                                             raison="QGIS ne répond plus",
                                             resultat=None))
    ajoutes = []

    async def _ajouter(sid, role, texte, appels):
        ajoutes.append(texte)

    async def _modele(messages):
        raise AssertionError("pas de modele pour une interruption")

    _run(ap.rattacher("tf-1", registre, _ajouter, _modele))
    assert "s'est interrompu : QGIS ne répond plus" in ajoutes[0]
    assert "relancer" in ajoutes[0]


def test_une_tache_encore_active_n_est_pas_rattachee():
    registre = _RegistreMemoire(_tache_finie(statut=ap.EN_COURS))

    async def _ajouter(*a):
        raise AssertionError("rien a ajouter")

    assert _run(ap.rattacher("tf-1", registre, _ajouter, None))["ok"] is False


# ── 10. Points d'entree de l'agent ──────────────────────────────────────────

@pytest.fixture
def client_agent(monkeypatch):
    from fastapi.testclient import TestClient
    from agent import main as agent_main
    monkeypatch.delenv("ONYXIA_USER", raising=False)
    # Le middleware relit la cle a chaque requete ; d'autres suites la changent.
    monkeypatch.setenv("HUB_API_KEY", "test-key")
    ap._ATTENTES.clear()
    return TestClient(agent_main.app, headers={
        "Authorization": "Bearer test-key", "X-Hub-Proxy-User": "nicolas"})


def test_la_decision_est_transmise_au_tour_qui_attend(client_agent):
    ap.ouvrir_attente("tf-9")
    r = client_agent.post("/chat/taches/tf-9/decision", json={"choix": "attendre"})
    assert r.status_code == 200 and r.json()["choix"] == "attendre"
    assert ap.prendre_decision("tf-9") == "attendre"


def test_une_decision_sans_tour_en_attente_rend_404(client_agent):
    assert client_agent.post("/chat/taches/tf-0/decision",
                             json={"choix": "annuler"}).status_code == 404


def test_un_choix_inconnu_est_refuse(client_agent):
    ap.ouvrir_attente("tf-8")
    assert client_agent.post("/chat/taches/tf-8/decision",
                             json={"choix": "tout_casser"}).status_code == 400


def test_le_rattachement_est_reserve_au_hub(monkeypatch):
    from fastapi.testclient import TestClient
    from agent import main as agent_main
    monkeypatch.setenv("ONYXIA_USER", "nicolas")
    monkeypatch.setenv("HUB_API_KEY", "test-key")
    client = TestClient(agent_main.app)
    r = client.post("/internal/taches/tf-1/rattacher", headers={"accept": "application/json"})
    assert r.status_code == 401
