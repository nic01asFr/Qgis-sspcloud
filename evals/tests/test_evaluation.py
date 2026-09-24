"""Evaluation de scenarios sur des trajectoires simulees : reussites et echecs."""
from __future__ import annotations

import copy
import re
from pathlib import Path

import pytest

from evals.evaluation import bbox_contenue, evaluer
from evals.modele import OUTILS_MUTATEURS, AppelOutil, Execution, Tour
from evals.rapport import agreger, markdown
from evals.scenario import charger
from evals.tests.aide import tour_fixture

SCENARIOS = Path(__file__).resolve().parents[1] / "scenarios"
HUB = "https://hub.example.fr"
BLANCHE = [HUB, "data.geopf.fr"]


def _scenario(identifiant: str):
    return next(s for s in charger([SCENARIOS]) if s.id == identifiant)


def _execution(scenario_id: str, tours: list[Tour], etat: dict | None, rep: int = 1) -> Execution:
    return Execution(scenario_id=scenario_id, repetition=rep, session_id="sess", tours=tours, etat=etat)


def _echecs(resultat) -> set[str]:
    return {c.nom for c in resultat.criteres if not c.ok}


def _tour(message: str, reponse: str, appels: list[AppelOutil], iterations: int = 2) -> Tour:
    return Tour(message=message, reponse=reponse, appels=appels, iterations=iterations, termine=True)


# ── S1 ─────────────────────────────────────────────────────────────────────

def test_s1_reussi(etat_aix_reussi):
    s = _scenario("S1-bati-aix")
    r = evaluer(s, _execution(s.id, [tour_fixture("s1_reussi.sse", s.messages()[0])], etat_aix_reussi), BLANCHE)
    assert r.valide
    assert r.reussi, [c for c in r.criteres if not c.ok]
    noms = {c.nom for c in r.criteres}
    assert "reponse.chiffres_tracables" in noms
    assert "etat.couche[b[aâ]ti].nombre_entites" in noms
    assert "trajectoire.ordre[set_study_zone < un de (smart_load | add_from_catalog)]" in noms


def test_s1_incident_du_2026_09_24_echoue_sur_tous_les_axes(etat_incident_s1):
    s = _scenario("S1-bati-aix")
    r = evaluer(s, _execution(s.id, [tour_fixture("s1_echec_boucle.sse", s.messages()[0])], etat_incident_s1),
                BLANCHE)
    assert r.valide and not r.reussi
    echecs = _echecs(r)
    attendus = {
        "reponse.chiffres_tracables",
        "reponse.urls_autorisees",
        "cout.arret_auto_interdit",
        "etat.couche[b[aâ]ti].nombre_entites",
        "etat.couche[b[aâ]ti].emprise_dans",
        "etat.nom_interdit[_temp$]",
        "trajectoire.max_erreurs_outils",
    }
    assert attendus <= echecs, echecs
    assert any(n.startswith("trajectoire.interdit[execute_python") for n in echecs)
    # Ce qui a bien ete fait reste credite.
    assert "trajectoire.attendu[set_study_zone]" not in echecs


def test_s1_sans_etat_lu(etat_aix_reussi):
    s = _scenario("S1-bati-aix")
    r = evaluer(s, _execution(s.id, [tour_fixture("s1_reussi.sse")], None), BLANCHE)
    assert "etat.lecture" in _echecs(r)


def test_s1_ordre_inverse_echoue(etat_aix_reussi):
    s = _scenario("S1-bati-aix")
    appels = [AppelOutil("smart_load", {"id": "bdtopo_batiments"}, resultat='{"feature_count": 68412}'),
              AppelOutil("set_study_zone", {"target": "Aix-en-Provence"})]
    tour = _tour(s.messages()[0], "J'ai chargé 68 412 bâtiments.", appels)
    r = evaluer(s, _execution(s.id, [tour], etat_aix_reussi), BLANCHE)
    assert "trajectoire.ordre[set_study_zone < un de (smart_load | add_from_catalog)]" in _echecs(r)


# ── S2 / S4 : clip et chiffre egal au compte ───────────────────────────────

def test_s2_bati_hors_contour_echoue(etat_aix_reussi):
    s = _scenario("S2-bati-aix-perimetre-communal")
    etat = copy.deepcopy(etat_aix_reussi)
    etat["inspections"]["bati_aix_en_provence"]["hors_contour"] = 1532
    appels = [AppelOutil("set_study_zone", {"target": "Aix-en-Provence"}),
              AppelOutil("smart_load", {"id": "bdtopo_batiments"}),
              AppelOutil("run_processing", {"algorithm": "native:clip"})]
    tour = _tour(s.messages()[0], "Le bâti est découpé au périmètre de la commune.", appels)
    r = evaluer(s, _execution(s.id, [tour], etat), BLANCHE)
    assert _echecs(r) == {"etat.couche[b[aâ]ti].hors_zone_max"}
    etat["inspections"]["bati_aix_en_provence"]["hors_contour"] = 0
    assert evaluer(s, _execution(s.id, [tour], etat), BLANCHE).reussi


def test_s2_couche_non_inspectee_echoue(etat_aix_reussi):
    s = _scenario("S2-bati-aix-perimetre-communal")
    etat = copy.deepcopy(etat_aix_reussi)
    etat["inspections"] = {}
    appels = [AppelOutil("set_study_zone", {"target": "Aix"}), AppelOutil("smart_load", {"id": "x"}),
              AppelOutil("run_processing", {"algorithm": "native:clip"})]
    r = evaluer(s, _execution(s.id, [_tour(s.messages()[0], "Découpé à la commune.", appels)], etat), BLANCHE)
    assert "etat.couche[b[aâ]ti].hors_zone_max" in _echecs(r)


def test_s2_sans_clip_dans_la_trajectoire(etat_aix_reussi):
    s = _scenario("S2-bati-aix-perimetre-communal")
    appels = [AppelOutil("set_study_zone", {"target": "Aix"}), AppelOutil("smart_load", {"id": "x"})]
    r = evaluer(s, _execution(s.id, [_tour(s.messages()[0], "Chargé dans la commune.", appels)], etat_aix_reussi),
                BLANCHE)
    assert any(n.startswith("trajectoire.attendu[un de (clip_to_study_zone") for n in _echecs(r))


def _tour_s4(reponse: str) -> Tour:
    appels = [AppelOutil("set_study_zone", {"target": "Aix-en-Provence"}, resultat='{"success": true}'),
              AppelOutil("smart_load", {"id": "bdtopo_batiments"},
                         resultat='{"verification": {"feature_count": 68412}}')]
    return _tour("Combien de bâtiments y a-t-il à Aix-en-Provence ?", reponse, appels)


def test_s4_chiffre_egal_au_compte(etat_aix_reussi):
    s = _scenario("S4-combien-de-batiments-aix")
    r = evaluer(s, _execution(s.id, [_tour_s4("Il y a 68 412 bâtiments dans la commune.")], etat_aix_reussi), BLANCHE)
    assert r.reussi, _echecs(r)


def test_s4_chiffre_different_du_compte_reel(etat_aix_reussi):
    s = _scenario("S4-combien-de-batiments-aix")
    etat = copy.deepcopy(etat_aix_reussi)
    etat["couches"][1]["nombre_entites"] = 70101
    r = evaluer(s, _execution(s.id, [_tour_s4("Il y a 68 412 bâtiments dans la commune.")], etat), BLANCHE)
    assert "reponse.chiffre_egal_compte" in _echecs(r)


def test_s4_question_au_lieu_de_repondre(etat_aix_reussi):
    s = _scenario("S4-combien-de-batiments-aix")
    r = evaluer(s, _execution(s.id, [_tour_s4("Tu veux la commune ou l'agglomération ?")], etat_aix_reussi),
                BLANCHE)
    assert {"reponse.clarification_attendue", "reponse.chiffre_egal_compte"} <= _echecs(r)


# ── S6 : multi-tours ───────────────────────────────────────────────────────

def _etat_s6(etat_aix_reussi: dict) -> dict:
    etat = copy.deepcopy(etat_aix_reussi)
    etat["couches"] = [
        {"nom": "routes_aix", "type": "vecteur", "nombre_entites": 9120, "valide": True,
         "emprise_4326": [5.28, 43.46, 5.50, 43.62], "crs": "EPSG:2154"},
        {"nom": "tampon_routes_20m", "type": "vecteur", "nombre_entites": 9120, "valide": True,
         "emprise_4326": [5.2797, 43.4598, 5.5003, 43.6202], "crs": "EPSG:2154"},
    ]
    return etat


def _tours_s6(second_outil: AppelOutil) -> list[Tour]:
    t1 = _tour("Charge les routes de ma zone.", "J'ai chargé 9 120 tronçons de route.",
               [AppelOutil("smart_load", {"id": "bdtopo_troncons_routes"},
                           resultat='{"verification": {"feature_count": 9120}}')])
    t2 = _tour("Fais une zone tampon de 20 mètres autour de ces routes.",
               "Voilà : un tampon de 20 mètres autour des 9 120 tronçons.", [second_outil])
    return [t1, t2]


def test_s6_multi_tours_reussi(etat_aix_reussi):
    s = _scenario("S6-ma-zone-traitement")
    tours = _tours_s6(AppelOutil("run_processing", {"algorithm": "native:buffer", "DISTANCE": "20"}))
    r = evaluer(s, _execution(s.id, tours, _etat_s6(etat_aix_reussi)), BLANCHE)
    assert r.reussi, [c for c in r.criteres if not c.ok]
    # « 20 mètres » vient du message de l'utilisateur, « 9 120 » du premier tour.
    assert any(c.nom == "tour2.reponse.chiffres_tracables" and c.ok for c in r.criteres)


def test_s6_redefinir_la_zone_au_second_tour_echoue(etat_aix_reussi):
    s = _scenario("S6-ma-zone-traitement")
    tours = _tours_s6(AppelOutil("set_study_zone", {"target": "Marseille"}))
    r = evaluer(s, _execution(s.id, tours, _etat_s6(etat_aix_reussi)), BLANCHE)
    echecs = _echecs(r)
    assert "tour2.trajectoire.interdit[set_study_zone]" in echecs
    assert any(n.startswith("tour2.trajectoire.attendu[") for n in echecs)
    assert not any(n.startswith("tour1.") for n in echecs)


def test_tours_manquants_signales(etat_aix_reussi):
    s = _scenario("S6-ma-zone-traitement")
    tours = _tours_s6(AppelOutil("run_processing", {"algorithm": "native:buffer"}))[:1]
    r = evaluer(s, _execution(s.id, tours, _etat_s6(etat_aix_reussi)), BLANCHE)
    assert "banc.tours_joues" in _echecs(r)


# ── Conversation, ambiguite, livrable ──────────────────────────────────────

def test_c4_clarification_reussie():
    s = _scenario("C4-homonymie-saint-martin")
    etat = {"zone": None, "couches": [], "inspections": {}}
    r = evaluer(s, _execution(s.id, [tour_fixture("clarification.sse", s.messages()[0])], etat), BLANCHE)
    assert r.reussi, [c for c in r.criteres if not c.ok]


def test_c4_choix_silencieux_echoue(etat_aix_reussi):
    s = _scenario("C4-homonymie-saint-martin")
    r = evaluer(s, _execution(s.id, [tour_fixture("s1_reussi.sse", s.messages()[0])], etat_aix_reussi), BLANCHE)
    echecs = _echecs(r)
    assert {"reponse.clarification_attendue", "trajectoire.interdit[set_study_zone]",
            "trajectoire.outils_mutateurs_interdits", "etat.zone.definie",
            "etat.couche[b[aâ]ti].absente"} <= echecs


def test_c1_salutation_jargon_et_longueur():
    s = _scenario("C1-salutation")
    ok = _tour("Bonjour !", "Bonjour ! Je peux charger des données sur un territoire et en faire des cartes.", [], 1)
    assert evaluer(s, _execution(s.id, [ok], None), BLANCHE).reussi
    jargon = _tour("Bonjour !", "Bonjour ! J'appelle smart_load avec une bbox en EPSG:2154.", [], 1)
    assert "reponse.mots_interdits" in _echecs(evaluer(s, _execution(s.id, [jargon], None), BLANCHE))


def test_c5_hors_sujet_recette_inventee():
    s = _scenario("C5-hors-sujet")
    recette = _tour(s.messages()[0], "Bien sûr : 250 g de farine, 125 g de beurre, 4 pommes. "
                                     "Préchauffe le four à 180 degrés.", [], 1)
    echecs = _echecs(evaluer(s, _execution(s.id, [recette], None), BLANCHE))
    assert {"reponse.chiffres_tracables", "reponse.ne_doit_pas_contenir", "reponse.doit_contenir"} <= echecs


def test_c6_livrable_lien_trace(etat_aix_reussi):
    s = _scenario("C6-livrable-carte-pdf")
    r = evaluer(s, _execution(s.id, [tour_fixture("livrable.sse", s.messages()[0])], etat_aix_reussi), BLANCHE)
    assert r.reussi, [c for c in r.criteres if not c.ok]


def test_c6_hub_hors_liste_blanche(etat_aix_reussi):
    s = _scenario("C6-livrable-carte-pdf")
    r = evaluer(s, _execution(s.id, [tour_fixture("livrable.sse")], etat_aix_reussi), ["data.geopf.fr"])
    assert {"reponse.urls_autorisees", "reponse.urls_min"} <= _echecs(r)


def test_c6_url_inventee(etat_aix_reussi):
    s = _scenario("C6-livrable-carte-pdf")
    tour = tour_fixture("livrable.sse")
    tour.reponse = tour.reponse.replace("carte-bati-aix", "carte-bati-aix-v2")
    r = evaluer(s, _execution(s.id, [tour], etat_aix_reussi), BLANCHE)
    assert "reponse.urls_autorisees" in _echecs(r)


# ── Cout, banc ─────────────────────────────────────────────────────────────

def test_erreur_de_flux_et_coupure(etat_aix_reussi):
    s = _scenario("C1-salutation")
    tour = tour_fixture("erreur_llm.sse", "Bonjour !")
    echecs = _echecs(evaluer(s, _execution(s.id, [tour], None), BLANCHE))
    assert {"cout.erreur_flux_interdite", "cout.fin_de_flux_requise"} <= echecs
    coupe = _tour("Bonjour !", "Bonjour", [], 1)
    coupe.coupe_par_le_banc = True
    assert "cout.fin_de_flux_requise" in _echecs(evaluer(s, _execution(s.id, [coupe], None), BLANCHE))


def test_plafonds_d_iterations_et_de_duree():
    s = _scenario("C1-salutation")
    tour = _tour("Bonjour !", "Bonjour", [], iterations=5)
    tour.duree_s = 400
    echecs = _echecs(evaluer(s, _execution(s.id, [tour], None), BLANCHE))
    assert {"cout.iterations_max", "cout.duree_totale"} <= echecs


def test_execution_invalide_hors_taux():
    s = _scenario("C1-salutation")
    ex = _execution(s.id, [], None)
    ex.erreur_banc = "ErreurPont: connexion refusee"
    r = evaluer(s, ex, BLANCHE)
    assert not r.valide and not r.reussi
    assert [c.categorie for c in r.criteres] == ["banc"]


def test_bbox_contenue_avec_tolerance():
    assert bbox_contenue([5.3, 43.5, 5.4, 43.6], [5.27, 43.45, 5.51, 43.63])
    assert not bbox_contenue([5.25, 43.5, 5.4, 43.6], [5.27, 43.45, 5.51, 43.63])
    assert bbox_contenue([5.25, 43.5, 5.4, 43.6], [5.27, 43.45, 5.51, 43.63], tolerance_km=2)


# ── Rapport ────────────────────────────────────────────────────────────────

def test_agregation_et_markdown(etat_aix_reussi, etat_incident_s1):
    s = _scenario("S1-bati-aix")
    r1 = evaluer(s, _execution(s.id, [tour_fixture("s1_reussi.sse")], etat_aix_reussi, 1), BLANCHE)
    r2 = evaluer(s, _execution(s.id, [tour_fixture("s1_echec_boucle.sse")], etat_incident_s1, 2), BLANCHE)
    ex3 = _execution(s.id, [], None, 3)
    ex3.erreur_banc = "hub injoignable"
    r3 = evaluer(s, ex3, BLANCHE)
    rapport = agreger([s], [r1, r2, r3], {"date": "2026-09-24", "etude": "sid", "etude_nom": "bac-a-sable"})
    fiche = rapport["scenarios"][0]
    assert (fiche["valides"], fiche["reussites"], fiche["taux_reussite"]) == (2, 1, 0.5)
    assert fiche["seuil_atteint"] is False
    assert fiche["criteres"]["reponse.chiffres_tracables"]["taux"] == 0.5
    assert fiche["cout"]["arrets_auto"] == 1
    assert fiche["cout"]["execute_python"] == 3
    assert rapport["synthese"]["par_famille"]["fidelite_territoriale"]["taux"] == 0.5
    md = markdown(rapport)
    assert "S1-bati-aix" in md and "NON atteint" in md and "hub injoignable" in md
    assert "`reponse.chiffres_tracables`" in md


# ── Coherence avec l'agent ─────────────────────────────────────────────────

def test_outils_mutateurs_couvrent_l_agent():
    source = Path(__file__).resolve().parents[2] / "agent" / "agent" / "qgis_agent.py"
    if not source.exists():
        pytest.skip("source de l'agent absente")
    texte = source.read_text(encoding="utf-8")
    bloc = re.search(r"_MUTATING_TOOLS: frozenset\[str\] = frozenset\(\{(.*?)\}\)", texte, re.S)
    assert bloc, "definition de _MUTATING_TOOLS introuvable"
    outils_agent = set(re.findall(r'"([a-z_]+)"', bloc.group(1)))
    assert outils_agent and outils_agent <= OUTILS_MUTATEURS, outils_agent - OUTILS_MUTATEURS
