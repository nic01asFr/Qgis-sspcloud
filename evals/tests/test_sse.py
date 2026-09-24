"""Lecture du flux SSE et reconstruction de la trajectoire, sur fixtures fabriquees."""
from __future__ import annotations

from evals.sse import analyser_bloc_outil, construire_tour, lire_evenements
from evals.tests.aide import evenements_fixture, tour_fixture


def test_lire_evenements_decoupe_les_lignes_data():
    lignes = ['data: {"phase": "reflexion"}', "", 'data: {"text": "a"}', "", 'data: {"done": true}', ""]
    assert list(lire_evenements(lignes)) == [{"phase": "reflexion"}, {"text": "a"}, {"done": True}]


def test_lire_evenements_tolere_crlf_commentaires_et_absence_de_ligne_finale():
    lignes = [": commentaire\r\n", 'data: {"text": "x"}\r\n', "\r\n", 'data: {"done": true}']
    assert list(lire_evenements(lignes)) == [{"text": "x"}, {"done": True}]


def test_lire_evenements_concatene_un_evenement_sur_plusieurs_lignes():
    lignes = ['data: {"text":', 'data:  "multi"}', ""]
    assert list(lire_evenements(lignes)) == [{"text": "multi"}]


def test_lire_evenements_garde_une_charge_non_json():
    assert list(lire_evenements(["data: pas du json", ""])) == [{"_brut": "pas du json"}]


def test_trajectoire_reussie_outils_arguments_resultats():
    tour = tour_fixture("s1_reussi.sse", "Charge les bâtis sur Aix-en-Provence")
    assert tour.outils() == ["set_study_zone", "smart_load"]
    zone, chargement = tour.appels
    assert zone.arguments == {"target": "Aix-en-Provence"}
    assert '"bbox": [5.2694' in zone.resultat
    assert zone.images == 1
    assert chargement.arguments == {"id": "bdtopo_batiments", "name": "bati_aix_en_provence"}
    assert chargement.resultat_tronque is True
    assert '"feature_count": 68412' in chargement.resultat
    assert not zone.erreur and not chargement.erreur
    assert tour.iterations == 3
    assert tour.battements == 1
    assert tour.checkpoints == ["ckpt-0001", "ckpt-0002"]
    assert tour.caracteres_raisonnement > 0
    assert tour.termine and not tour.erreur_flux and not tour.arret_auto


def test_reponse_ne_contient_ni_bloc_outil_ni_resultat_ni_marqueur():
    tour = tour_fixture("s1_reussi.sse")
    assert tour.reponse.startswith("C'est fait")
    assert "```" not in tour.reponse
    assert "set_study_zone" not in tour.reponse
    assert "ckpt" not in tour.reponse
    assert "data:image" not in tour.reponse


def test_arret_automatique_et_erreurs_outils_detectes():
    tour = tour_fixture("s1_echec_boucle.sse")
    assert tour.arret_auto is True
    assert tour.outils().count("execute_python") == 3
    assert tour.erreurs_outils() == 3
    assert any("Boucle d'erreur" in m for m in tour.messages_systeme)
    assert "Boucle d'erreur" not in tour.reponse
    code = tour.appels[2]
    assert code.arguments_tronques is True


def test_lien_de_livrable_capte_et_ligne_de_memorisation_retiree_de_la_reponse():
    tour = tour_fixture("livrable.sse")
    publication = tour.appels[-1]
    assert publication.nom == "publish_artifact"
    assert publication.lien_livrable == "https://hub.example.fr/livrables/carte-bati-aix"
    assert "https://hub.example.fr/livrables/carte-bati-aix" in tour.sources()
    assert "Mémorisé" not in tour.reponse
    assert any("Mémorisé" in m for m in tour.messages_systeme)
    assert "**" not in "".join(tour.messages_systeme).replace("*\N{BRAIN}", "")


def test_erreur_de_flux():
    tour = tour_fixture("erreur_llm.sse")
    assert "HTTP 503" in tour.erreur_flux
    assert not tour.termine
    assert tour.reponse == ""


def test_repli_sans_evenements_de_phase():
    tour = tour_fixture("ancien_format.sse")
    assert tour.outils() == ["get_project_info"]
    assert '"feature_count": 1234' in tour.appels[0].resultat
    assert tour.reponse.startswith("Je regarde le projet.")
    assert "tronçons" in tour.reponse
    assert "```" not in tour.reponse


def test_outil_sans_resultat_ni_arguments():
    appel, reste = analyser_bloc_outil("\n\n> **`get_screenshot`**\n", "get_screenshot")
    assert appel.nom == "get_screenshot"
    assert appel.arguments == {}
    assert appel.resultat == ""
    assert reste == ""


def test_evenements_comptes():
    evenements = evenements_fixture("s1_reussi.sse")
    tour = construire_tour("m", evenements)
    assert tour.evenements == len(evenements)
