"""Chat : proposition de bascule, bandeau des calculs, compte rendu de fin.

Le code des traitements en arriere-plan vit dans un bloc isole du chat
(#taches-fond) : une autre equipe fait evoluer la gestion des sessions et de
l'historique dans le meme fichier. Le seul point d'accroche dans le flux du
tour est la remise des evenements `{"tache": ...}` a window.TachesFond.
"""
from __future__ import annotations

import re
from pathlib import Path

_CHAT = (Path(__file__).resolve().parents[1] / "templates" / "chat.html").read_text(
    encoding="utf-8")
_BLOC = _CHAT.split('<script id="taches-fond">')[1].split("</script>")[0]
_STYLE = _CHAT.split('<style id="taches-fond-style">')[1].split("</style>")[0]


def test_le_bloc_est_isole_et_expose_une_seule_entree():
    assert _CHAT.count('<script id="taches-fond">') == 1
    assert "window.TachesFond = {evenement: evenement, rafraichir: rafraichir};" in _BLOC
    assert _BLOC.lstrip().startswith("(function () {")


def test_le_flux_du_tour_remet_les_evenements_tache_au_bloc():
    flux = _CHAT.split("lecture:")[1].split("if (data.phase)")[0]
    assert "if (data.tache) {" in flux
    assert "window.TachesFond.evenement(data, responseDiv)" in flux


def test_le_bloc_ne_touche_ni_aux_sessions_ni_a_l_historique():
    for interdit in ("loadSession(", "session-id').value =", "startNewConversation(",
                     "/sessions/"):
        assert interdit not in _BLOC, interdit


def test_les_trois_choix_sont_des_boutons_en_francais():
    for libelle in ("Continuer en arrière-plan", "Attendre", "Annuler"):
        assert libelle in _BLOC
    assert "b.type = 'button'" in _BLOC
    assert "prend plus de temps que prévu" in _BLOC
    assert "Sans réponse, il continuera en arrière-plan dans" in _BLOC


def test_la_proposition_est_accessible():
    assert "setAttribute('role', 'group')" in _BLOC
    assert "aria-labelledby" in _BLOC
    assert "setAttribute('aria-live', 'polite')" in _BLOC
    assert "annoncer(texte)" in _BLOC
    # Pas de vol du focus pendant que l'utilisateur ecrit.
    assert ".focus(" not in _BLOC
    assert ":focus-visible" in _STYLE


def test_les_decisions_et_actions_passent_par_les_routes_documentees():
    assert "'/chat/taches/' + encodeURIComponent(id) + '/decision'" in _BLOC
    assert "'/taches?session_id=' + encodeURIComponent(sid)" in _BLOC
    assert "'/taches/' + encodeURIComponent(id) + '/' + action" in _BLOC


def test_tous_les_evenements_du_tour_sont_traites():
    for evenement in ("proposition", "progression", "attente", "arriere_plan",
                      "annulee", "fin_attente", "interrompue"):
        assert f"case '{evenement}'" in _BLOC, evenement


def test_le_compte_rendu_de_fin_s_affiche_en_direct_sans_doublon():
    assert "function afficherMessageDeFin(t)" in _BLOC
    # Les comptes rendus deja dans l'historique charge ne sont pas rejoues.
    assert "vues = new Set(taches.filter(t => t.rattachee)" in _BLOC
    # Jamais au milieu d'un tour en cours.
    assert "tourEnCours()" in _BLOC
    assert "rendreTour(bulle, t.message" in _BLOC


def test_interrompue_propose_relancer_ou_abandonner():
    assert "['relancer', 'Relancer'], ['annuler', 'Abandonner']" in _BLOC


def test_le_bureau_est_prevenu():
    assert "type: 'qgis_taches_maj'" in _BLOC
    assert "location.origin" in _BLOC


def test_pas_d_emoji_dans_le_bloc():
    assert not re.search("[\U0001F300-\U0001FAFF☀-➿]", _BLOC + _STYLE)
