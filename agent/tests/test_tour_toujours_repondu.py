"""Chaque tour de l'agent se termine par un texte visible.

Constate le 2026-09-11 dans le bureau : a une question en lecture seule,
l'agent a reflechi 24 secondes -- 4 095 paquets de raisonnement pour un plafond
de 4 096 jetons -- puis s'est arrete sans envoyer un mot. L'utilisateur voyait
une bulle vide, puis plus rien.

Deux defauts s'additionnaient :

1. Les jetons de raisonnement comptent dans `max_tokens`. La reflexion a epuise
   le budget, le modele s'est arrete sur `finish_reason == "length"`, et rien ne
   traitait ce cas.
2. Le garde-fou « Je n'ai pas genere de reponse » testait `full_response`, dans
   lequel `_flush_reasoning` venait d'inserer le bloc de raisonnement. La
   reponse n'etait donc jamais vide, et le garde-fou ne se declenchait pas --
   precisement dans le cas pour lequel il existe.

Ces tests font tourner la vraie `chat_stream` contre un modele simule : le
defaut etait dans l'enchainement des branches, qu'une reproduction partielle
de la logique n'aurait pas attrape.
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
import tempfile
from pathlib import Path

import pytest

_RACINE = Path(__file__).resolve().parents[1]
if str(_RACINE) not in sys.path:
    sys.path.insert(0, str(_RACINE))

os.environ.setdefault("DATA_DIR", tempfile.mkdtemp(prefix="agent_tour_"))
os.environ.setdefault("HUB_URL", "https://user-nicolaslaval-qgis.user.lab.sspcloud.fr")
os.environ.setdefault("HUB_API_KEY", "test-key")
os.environ.setdefault("QGIS_API_KEY", "test-key")
os.environ.setdefault("ONYXIA_USER", "test-user")

if "sqlite_vec" not in sys.modules:
    _stub = type(sys)("sqlite_vec")
    _stub.load = lambda *a, **k: None  # type: ignore[attr-defined]
    sys.modules["sqlite_vec"] = _stub

from agent import memory  # noqa: E402
from agent import qgis_agent as qa  # noqa: E402


def _run(coro):
    """Execute une coroutine SANS retirer la boucle d'evenements courante.

    `asyncio.run()` la remet a None en sortant ; les fichiers de test executes
    ensuite, qui appellent `asyncio.get_event_loop()`, echouaient alors sur
    « There is no current event loop ». On reutilise la boucle en place, ou on
    en installe une qui restera disponible.
    """
    try:
        boucle = asyncio.get_event_loop()
        if boucle.is_closed():
            raise RuntimeError("boucle fermee")
    except RuntimeError:
        boucle = asyncio.new_event_loop()
        asyncio.set_event_loop(boucle)
    return boucle.run_until_complete(coro)


# ── Un modele simule, qui repond selon un script ─────────────────────────

def _paquets(reflexion: str = "", texte: str = "", motif: str = "stop",
             motif_final_nul: bool = False) -> list[str]:
    """Les lignes SSE qu'enverrait l'API du modele pour UN appel."""
    evs = []
    if reflexion:
        evs.append({"choices": [{"delta": {"reasoning_content": reflexion},
                                 "finish_reason": None}]})
    if texte:
        evs.append({"choices": [{"delta": {"content": texte},
                                 "finish_reason": None}]})
    evs.append({"choices": [{"delta": {}, "finish_reason": motif}]})
    if motif_final_nul:
        # Certains serveurs ferment par un paquet sans motif.
        evs.append({"choices": [{"delta": {}, "finish_reason": None}]})
    return ["data: " + json.dumps(e) for e in evs] + ["data: [DONE]"]


class _Reponse:
    status_code = 200

    def __init__(self, lignes: list[str]):
        self._lignes = lignes

    async def aiter_lines(self):
        for ligne in self._lignes:
            yield ligne

    async def aread(self) -> bytes:
        return b""


class _Flux:
    def __init__(self, lignes: list[str]):
        self._reponse = _Reponse(lignes)

    async def __aenter__(self):
        return self._reponse

    async def __aexit__(self, *exc):
        return False


class _ClientModele:
    """Remplace httpx.AsyncClient : chaque appel consomme un script."""
    script: list[list[str]] = []
    appels: list[dict] = []

    def __init__(self, *a, **k):
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    def stream(self, methode, url, json=None, headers=None):
        _ClientModele.appels.append(json or {})
        if not _ClientModele.script:
            raise AssertionError("le modele a ete appele plus souvent que prevu")
        return _Flux(_ClientModele.script.pop(0))


@pytest.fixture()
def agent(monkeypatch):
    async def _rien(*a, **k):
        return None

    for nom in ("add_message", "set_session_tag", "add_insight"):
        monkeypatch.setattr(memory, nom, _rien, raising=False)

    async def _modele(profil):
        return "modele-essai"

    async def _prompt(self, user_message=None):
        return "consigne systeme"

    async def _outils(self):
        return []

    monkeypatch.setattr(qa, "_resolve_model", _modele)
    monkeypatch.setattr(qa.QGISAgent, "_build_system_prompt", _prompt)
    monkeypatch.setattr(qa.QGISAgent, "_get_tools", _outils)
    monkeypatch.setattr(qa.httpx, "AsyncClient", _ClientModele)
    _ClientModele.script = []
    _ClientModele.appels = []
    return qa.QGISAgent(username="user", session_id="essai-tour",
                        profile_id="standard")


def _derouler(agent, *script: list[str]) -> list:
    _ClientModele.script = list(script)

    async def _tour():
        return [c async for c in agent.chat_stream("question", history=[])]

    return _run(_tour())


def _visible(morceaux: list) -> str:
    """Ce qui s'affiche : le texte, pas le raisonnement ni les evenements."""
    return "".join(m for m in morceaux if isinstance(m, str))


def _phases(morceaux: list) -> list[str]:
    return [m["phase"] for m in morceaux if isinstance(m, dict) and "phase" in m]


# ── Le garde-fou ─────────────────────────────────────────────────────────

def test_une_reflexion_sans_reponse_previent_l_utilisateur(agent):
    """Le cas constate : de la reflexion, puis rien. Avant correction, le
    tour se terminait sans un mot."""
    morceaux = _derouler(agent, _paquets(reflexion="Je pense longuement…"))
    assert "Je n'ai pas généré de réponse" in _visible(morceaux), (
        "un tour de pure reflexion se termine sans rien dire a l'utilisateur")


def test_le_raisonnement_ne_compte_pas_comme_une_reponse(agent):
    """Le raisonnement est masque par defaut dans le chat : il ne peut pas
    tenir lieu de reponse."""
    morceaux = _derouler(agent, _paquets(reflexion="x" * 5000))
    assert _visible(morceaux).strip(), "rien de visible n'a ete emis"


# ── La relance quand la reflexion epuise le budget ───────────────────────

def test_un_budget_epuise_declenche_une_seule_relance(agent):
    morceaux = _derouler(
        agent,
        _paquets(reflexion="…", motif="length"),
        _paquets(reflexion="…", motif="length"),
    )
    assert len(_ClientModele.appels) == 2, (
        "attendu un appel initial et UNE relance, vu %d" % len(_ClientModele.appels))
    assert _phases(morceaux).count("relance") == 1


def test_la_relance_demande_une_reponse_directe(agent):
    _derouler(agent,
              _paquets(reflexion="…", motif="length"),
              _paquets(texte="Voici.", motif="stop"))
    derniere = _ClientModele.appels[1]["messages"][-1]
    assert derniere["role"] == "system"
    assert "Réponds" in derniere["content"] and "MAINTENANT" in derniere["content"]


def test_apres_deux_budgets_epuises_l_utilisateur_sait_pourquoi(agent):
    """Un message qui dit ce qui s'est passe et quoi faire, pas le generique."""
    morceaux = _derouler(
        agent,
        _paquets(reflexion="…", motif="length"),
        _paquets(reflexion="…", motif="length"),
    )
    texte = _visible(morceaux)
    assert "réflexion a dépassé" in texte
    assert "Reformule" in texte


def test_la_relance_qui_repond_suffit(agent):
    morceaux = _derouler(
        agent,
        _paquets(reflexion="…", motif="length"),
        _paquets(texte="La surface totale est de 117 ha.", motif="stop"),
    )
    texte = _visible(morceaux)
    assert "117 ha" in texte
    assert "Je n'ai pas" not in texte, "le garde-fou s'est declenche a tort"


def test_un_paquet_final_sans_motif_n_efface_pas_le_budget_epuise(agent):
    """Sans valeur « collante », le dernier paquet ecrasait le « length » et la
    relance ne se declenchait jamais."""
    _derouler(
        agent,
        _paquets(reflexion="…", motif="length", motif_final_nul=True),
        _paquets(texte="Voici.", motif="stop"),
    )
    assert len(_ClientModele.appels) == 2, "le budget epuise n'a pas ete reconnu"


# ── Ce qui ne doit pas changer ───────────────────────────────────────────

def test_une_reponse_normale_ne_declenche_rien(agent):
    morceaux = _derouler(agent, _paquets(texte="Dix-neuf couches.", motif="stop"))
    assert len(_ClientModele.appels) == 1
    assert "relance" not in _phases(morceaux)
    assert "Je n'ai pas" not in _visible(morceaux)


# ── Ce que le chat peut dire pendant que l'agent travaille ───────────────

def test_une_reponse_annonce_la_reflexion_puis_la_redaction(agent):
    """Le chat devinait l'etat de l'agent en analysant le Markdown. Les phases
    le lui disent : il peut afficher « Reflexion… » puis « Redaction… »."""
    morceaux = _derouler(agent, _paquets(reflexion="…", texte="Voici.", motif="stop"))
    assert _phases(morceaux) == ["reflexion", "redaction"]


def test_la_redaction_n_est_annoncee_qu_une_fois_par_iteration(agent):
    lignes = _paquets(texte="Premier morceau. ", motif="stop")
    # Plusieurs paquets de texte dans le meme appel.
    lignes.insert(1, "data: " + json.dumps(
        {"choices": [{"delta": {"content": "Deuxieme. "}, "finish_reason": None}]}))
    morceaux = _derouler(agent, lignes)
    assert _phases(morceaux).count("redaction") == 1


def test_un_outil_a_un_libelle_lisible():
    assert qa._libelle_outil("get_project_info") == "Lecture du projet QGIS…"
    assert qa._libelle_outil("run_processing") == "Traitement QGIS en cours…"


def test_un_outil_inconnu_garde_un_libelle_generique_plutot_que_son_nom():
    libelle = qa._libelle_outil("outil_jamais_vu")
    assert "outil_jamais_vu" not in libelle
    assert libelle.strip()


# ── Le battement de coeur ────────────────────────────────────────────────

def _relayer(source, periode):
    from agent import main as agent_main

    async def _tout():
        return [x async for x in agent_main._avec_battement(source, periode=periode)]
    return _run(_tout()), agent_main._BATTEMENT


def test_un_silence_produit_des_battements():
    """Sans eux, rien ne partait vers le navigateur pendant un outil long, et
    la passerelle coupait la connexion au bout de 600 s."""
    async def _lent():
        await asyncio.sleep(0.12)
        yield "fin"

    elements, battement = _relayer(_lent(), periode=0.03)
    assert elements.count(battement) >= 2
    assert elements[-1] == "fin"


def test_le_battement_n_interrompt_pas_le_travail_en_cours():
    """Annuler l'element attendu a chaque battement couperait l'appel au
    modele ou l'outil en cours : il doit arriver quand meme, et en entier."""
    etapes = []

    async def _travail():
        etapes.append("debut")
        await asyncio.sleep(0.1)
        etapes.append("fin")
        yield "resultat"

    elements, battement = _relayer(_travail(), periode=0.02)
    assert etapes == ["debut", "fin"]
    assert [e for e in elements if e is not battement] == ["resultat"]


def test_sans_silence_aucun_battement():
    async def _rapide():
        for i in range(3):
            yield i

    elements, battement = _relayer(_rapide(), periode=5)
    assert battement not in elements
    assert elements == [0, 1, 2]


def test_une_reponse_coupee_par_le_budget_n_est_pas_relancee(agent):
    """Si du texte est deja parti, relancer le redoublerait. Le budget epuise
    ne declenche la relance que lorsqu'AUCUN mot n'a ete emis."""
    _derouler(agent, _paquets(texte="Debut de reponse", motif="length"))
    assert len(_ClientModele.appels) == 1


# ── Les conversations passees restent dans leur etude ────────────────────

@pytest.fixture()
def base(monkeypatch, tmp_path):
    """Une base memoire propre, isolee des autres fichiers de test : `memory`
    est un module unique, fixer son chemin a l'import volerait la base des
    autres."""
    monkeypatch.setattr(memory, "_DATA_DIR", tmp_path)
    monkeypatch.setattr(memory, "_DB_PATH", tmp_path / "memory.db")
    _run(memory.init())

    async def _peupler():
        for sid, etude, titre in (
            ("s-sxm", "etude-sxm", "Recul de 500 m autour du bati a Saint-Martin"),
            ("s-mtp", "etude-mtp", "tu peux retravailler la symbologie du projet"),
        ):
            await memory.create_session(sid, "user", "standard")
            await memory.set_session_summary(sid, titre)
            await memory.set_session_study(sid, etude)
    _run(_peupler())
    return tmp_path


def _contexte(etude_active):
    return _run(memory.build_context_summary(
        "user", "nouvelle-conversation", "standard", active_study=etude_active,
    ))


def test_une_conversation_d_une_autre_etude_n_est_pas_injectee(base):
    """Le cas constate : sur Saint-Martin, l'agent a pris pour la demande en
    cours une ancienne conversation d'une autre etude."""
    ctx = _contexte({"id": "etude-sxm", "name": "Saint-Martin"})
    assert "retravailler la symbologie" not in ctx, (
        "une conversation d'une autre etude entre dans le contexte")
    assert "Recul de 500 m" in ctx


def test_les_conversations_passees_sont_dites_closes(base):
    ctx = _contexte({"id": "etude-sxm", "name": "Saint-Martin"})
    assert "ce n'est PAS la demande en cours" in ctx


def test_sans_etude_active_aucune_conversation_n_est_injectee(base):
    ctx = _contexte(None)
    assert "Recul de 500 m" not in ctx
    assert "retravailler la symbologie" not in ctx


def test_la_barre_laterale_garde_toutes_les_conversations(base):
    """Le filtre est optionnel : la liste des conversations n'est pas amputee."""
    toutes = _run(memory.get_recent_sessions("user", limit=10))
    assert {s["id"] for s in toutes} >= {"s-sxm", "s-mtp"}


# ── La consigne ──────────────────────────────────────────────────────────

def test_la_consigne_interdit_les_identifiants_inventes():
    """L'ancienne consigne visait les URL. L'agent l'a respectee a la lettre,
    puis a invente sept identifiants de source absents du catalogue."""
    src = Path(qa.__file__).read_text(encoding="utf-8")
    assert "IDENTIFIANT de source" in src
    assert "absente du catalogue" in src
