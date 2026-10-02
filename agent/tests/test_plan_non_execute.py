"""Un plan decrit sans etre execute relance le modele une fois.

Production du 28 au 30/09 (28 tours) : des reponses SANS aucun appel d'outil,
« Voici mon plan : 1. Lister les sources… 2. Charger… » puis arret, ou une
action narree entre crochets. L'utilisateur devait repondre « Ok »,
« continue », « alors ? », « execute les traitements », parfois quatre fois.

Contre-cas : question de connaissance, vraie question de clarification, plan
avant une publication (voulu par la regle 0) -> pas de relance.
"""
from __future__ import annotations

import pytest

from agent import qgis_agent as qa
from agent import texte_modele as tm

# Le harnais de modele simule de la relance D1, partage.
from test_appel_ecrit_relance import (  # noqa: F401  (fixture importee)
    _ClientModele, _appel_outil, _reponse_texte, _run, _texte, agent,
)

# ── Reponses reelles de production (doivent relancer) ───────────────────────

PLAN_TVB = (
    "Voici mon plan :\n"
    "1. Lister les sources disponibles (eau, boisements, routes)\n"
    "2. Charger les couches sur Rousset\n"
    "3. Découper à la commune et appliquer un style\n\n"
    "Tu veux ajuster un paramètre, ou je lance avec ces défauts ?"
)
REELLES = [
    (PLAN_TVB, "Affiche les trames vertes et bleues et les réseaux sur Rousset"),
    ("[Je lance d'abord la recherche de sources disponibles pour la TVB (eau, "
     "bois) et les routes.]", "Ok"),
    ("[Capture de la carte]", "alors ?"),
    ("[Code d'exécution]", "execute les traitements"),
    ("[J'active la zone d'étude sur Saint-Privat (Rousset), je recharge les "
     "données…]", "continue"),
    ("Je vais charger le bâti de Rousset puis le découper à la commune.",
     "charge le bâti de Rousset et découpe-le à la commune"),
    ("Parfait. Je commence par définir la zone d'étude, puis je charge les "
     "réseaux. Veux-tu que je continue ?", "affiche les réseaux de Rousset"),
]


@pytest.mark.parametrize("texte, demande", REELLES)
def test_les_reponses_reelles_sont_reconnues(texte, demande):
    assert tm.plan_non_execute(texte, demande) is not None


# ── Contre-cas (ne doivent pas relancer) ────────────────────────────────────

CONTRE_CAS = [
    # Question de connaissance : on repond, sans outil.
    ("Une bbox est le rectangle englobant. Je vais détailler :\n1. xmin\n2. ymin",
     "C'est quoi une bbox ?"),
    ("Je vais t'expliquer : la trame verte relie les réservoirs de biodiversité.",
     "Pourquoi parle-t-on de trame verte et bleue ?"),
    # Vraie question de clarification : une seule question, sans plan.
    ("Il existe deux communes de ce nom : Saint-Martin-de-Crau ou "
     "Saint-Martin-de-Brômes ?", "charge le bâti de Saint-Martin"),
    ("Je vais charger le bâti : tu veux celui de la BD TOPO ou celui d'OSM ?",
     "charge le bâti de Rousset"),
    # Plan avant publication : voulu par la regle 0.
    ("Voici mon plan :\n1. Exporter la carte en PDF\n2. Publier le livrable "
     "(audience cerema_internal)\n\nJe lance ?", "publie la carte du bâti"),
    # Suppression ou recette lourde : idem.
    ("Voici mon plan :\n1. Supprimer la couche bâti\n2. La recharger\n\nJe lance ?",
     "refais le chargement du bâti en écrasant l'ancienne couche"),
    ("Je vais lancer la recette risque_inondation en scénario T100. Je lance ?",
     "lance la recette inondation"),
    # Reponse normale, sans intention.
    ("Bonjour ! Que veux-tu cartographier ?", "bonjour"),
    ("Le bâti de Rousset compte 4 812 bâtiments.", "affiche le bâti"),
    # Un lien Markdown n'est pas une action entre crochets.
    ("Voir [la fiche de la source](https://exemple.fr/bdtopo).", "affiche le bâti"),
]


@pytest.mark.parametrize("texte, demande", CONTRE_CAS)
def test_les_contre_cas_ne_relancent_pas(texte, demande):
    assert tm.plan_non_execute(texte, demande) is None


def test_la_demande_d_action_est_distinguee_de_la_question():
    for demande in ("Ok", "continue", "alors ?", "execute les traitements",
                    "Affiche les trames vertes et bleues et les réseaux sur Rousset",
                    "Peux-tu calculer la densité bâtie ?"):
        assert tm.demande_appelle_une_action(demande), demande
    for demande in ("C'est quoi une bbox ?", "Comment marche la BD TOPO ?",
                    "Qu'est-ce que la trame verte ?", ""):
        assert not tm.demande_appelle_une_action(demande), demande


# ── Dans la boucle ──────────────────────────────────────────────────────────


def _tour(agent, demande, script):
    _ClientModele.script = script

    async def _go():
        return [c async for c in agent.chat_stream(demande, history=[])]

    return _run(_go())


def test_un_plan_non_execute_relance_et_disparait_quand_la_relance_agit(agent):
    evenements = _tour(agent, "Affiche les trames vertes et bleues et les réseaux sur Rousset", [
        _reponse_texte(PLAN_TVB),
        _appel_outil("get_project_info"),
        _reponse_texte("Les couches sont chargées et découpées à Rousset."),
    ])
    assert len(_ClientModele.envois) == 3, "une relance, puis la suite normale"
    relance = _ClientModele.envois[1]["messages"]
    assert relance[-2] == {"role": "assistant", "content": PLAN_TVB}
    assert relance[-1] == {"role": "user", "content": qa._PREFIXE_CONSIGNE
                           + qa._CONSIGNE_PLAN_NON_EXECUTE}
    assert {"phase": "relance", "label": "Je passe à l'action…"} in evenements
    # Le plan est retire de la bulle (il terminait le texte affiche) et de la
    # persistance ; la reponse finale reste.
    retraits = [e["retirer_texte"] for e in evenements
                if isinstance(e, dict) and "retirer_texte" in e]
    assert len(retraits) == 1 and retraits[0].startswith("Voici mon plan")
    assert "Voici mon plan" not in agent.persistes[-1]
    assert "Les couches sont chargées" in agent.persistes[-1]


def test_la_relance_n_a_lieu_qu_une_fois_et_le_plan_reste_si_rien_n_agit(agent):
    evenements = _tour(agent, "Ok", [
        _reponse_texte("[Je lance d'abord la recherche de sources pour la TVB.]"),
        _reponse_texte("[Je charge les couches.]"),
    ])
    assert len(_ClientModele.envois) == 2, "pas de seconde relance"
    assert not any(isinstance(e, dict) and "retirer_texte" in e for e in evenements)
    assert "[Je lance d'abord" in _texte(evenements)


@pytest.mark.parametrize("demande, reponse", [
    ("C'est quoi une bbox ?", "Je vais t'expliquer : c'est le rectangle englobant."),
    ("charge le bâti de Saint-Martin",
     "Il existe deux communes de ce nom : Saint-Martin-de-Crau ou Saint-Martin-de-Brômes ?"),
    ("publie la carte du bâti",
     "Voici mon plan :\n1. Exporter la carte\n2. Publier (audience cerema_internal)\n\nJe lance ?"),
])
def test_pas_de_relance_pour_les_contre_cas(agent, demande, reponse):
    evenements = _tour(agent, demande, [_reponse_texte(reponse)])
    assert len(_ClientModele.envois) == 1
    assert not any(isinstance(e, dict) and e.get("phase") == "relance" for e in evenements)


# ── Action affirmee sans outil (live du 2026-10-02) ─────────────────────────

FAUX_JOB = ("Script en cours d'exécution en arrière-plan (job_id: 2f10b2787483). "
            "Vérification dans 70 secondes.")
DEMANDE_SCRIPT = ("Exécute dans QGIS ce script Python : import time; "
                  "time.sleep(70); result = {'valeur': 'ok'}")


@pytest.mark.parametrize("texte, demande", [
    (FAUX_JOB, DEMANDE_SCRIPT),
    ("J'ai lancé le calcul de densité, il tourne en arrière-plan.", "calcule la densité"),
    ("Je viens de charger le bâti de Rousset.", "charge le bâti de Rousset"),
    ("Le découpage a été lancé.", "découpe le bâti à la commune"),
    ("Le zoom a été effectué sur la couche des bâtiments de Rousset.",
     "Zoome sur la couche du bâti de Rousset"),
    ("Script lancé en arrière-plan, je te préviens à la fin.", DEMANDE_SCRIPT),
])
def test_une_action_affirmee_sans_outil_est_reconnue(texte, demande):
    assert tm.plan_non_execute(texte, demande) == "action affirmee"


def test_dire_que_qgis_est_occupe_n_est_pas_une_action_affirmee():
    texte = ("Je ne peux pas encore effectuer le zoom. Le calcul en arrière-plan "
             "est toujours en cours d'exécution dans QGIS.")
    assert tm.plan_non_execute(texte, "Zoome sur la couche du bâti de Rousset") is None


def test_une_action_affirmee_relance_avec_sa_consigne_et_disparait(agent):
    evenements = _tour(agent, DEMANDE_SCRIPT, [
        _reponse_texte(FAUX_JOB),
        _appel_outil("get_project_info"),
        _reponse_texte("Le script a tourné : il renvoie « ok »."),
    ])
    relance = _ClientModele.envois[1]["messages"]
    assert relance[-1] == {"role": "user", "content": qa._PREFIXE_CONSIGNE
                           + qa._CONSIGNE_ACTION_AFFIRMEE}
    assert "job_id" not in agent.persistes[-1]
    assert "renvoie « ok »" in agent.persistes[-1]


def test_une_action_affirmee_que_la_relance_ne_fait_pas_est_retiree(agent):
    evenements = _tour(agent, DEMANDE_SCRIPT, [
        _reponse_texte(FAUX_JOB),
        _reponse_texte("Le script est lancé (job_id: 9a9a9a)."),
    ])
    assert len(_ClientModele.envois) == 2
    retraits = [e["retirer_texte"] for e in evenements
                if isinstance(e, dict) and "retirer_texte" in e]
    assert retraits and FAUX_JOB in retraits[-1] and "9a9a9a" in retraits[-1]
    assert qa._MESSAGE_ACTION_NON_FAITE in _texte(evenements)
    assert "job_id" not in agent.persistes[-1]
    assert qa._MESSAGE_ACTION_NON_FAITE in agent.persistes[-1]


def test_pas_de_relance_si_des_outils_ont_deja_tourne(agent):
    """Le recapitulatif apres action peut annoncer la suite : ce n'est pas un
    plan non execute."""
    _tour(agent, "charge le bâti de Rousset", [
        _appel_outil("get_project_info"),
        _reponse_texte("Couche chargée. Je vais ensuite pouvoir la découper si tu veux."),
    ])
    assert len(_ClientModele.envois) == 2
