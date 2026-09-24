"""Verificateur de chiffres tracables : extraction a la francaise et tracage."""
from __future__ import annotations

import pytest

from evals.verifs.chiffres import (
    DERIVE, IGNORE, NON_TRACE, TRACE, ReglesChiffres, extraire_nombres, nombres_des_sources,
    verifier_chiffres,
)

NBSP = "\N{NO-BREAK SPACE}"
FINE = "\N{NARROW NO-BREAK SPACE}"
SOURCE_S1 = ('{"success": true, "verification": {"feature_count": 68412, "crs": "EPSG:2154", '
             '"emprise_4326": [5.2702, 43.4541, 5.5119, 43.6309]}, "surface_m2": 186080000}')


def _statuts(reponse: str, sources: list[str], regles: ReglesChiffres | None = None) -> dict[str, str]:
    return {n.texte: n.statut for n in verifier_chiffres(reponse, sources, regles).nombres}


# ── Extraction ─────────────────────────────────────────────────────────────

@pytest.mark.parametrize("texte, valeur", [
    ("51 450 444 entités", 51450444),
    (f"51{NBSP}450{NBSP}444 entités", 51450444),
    (f"51{FINE}450{FINE}444 entités", 51450444),
    ("51450444 entités", 51450444),
    ("51_450_444 entités", 51450444),
    ("51,450,444 entities", 51450444),
    ("1.234.567 habitants", 1234567),
    ("une surface de 1 234,5 m²", 1234.5),
    ("longitude 5,27", 5.27),
    ("longitude 5.27", 5.27),
    ("un écart de -12,5", -12.5),
    ("51,4 millions", 51_400_000),
    ("2,5 milliards", 2_500_000_000),
    ("12 k", 12_000),
])
def test_extraction_formats_francais(texte, valeur):
    nombres = extraire_nombres(texte)
    assert len(nombres) == 1, nombres
    assert nombres[0].valeur == pytest.approx(valeur)


def test_extraction_pourcentage_et_unites():
    n = extraire_nombres("soit 12,5 % du total et 3,2 km² et 40 ha et 250 m")
    assert [(x.valeur, x.unite) for x in n] == [(12.5, "%"), (3.2, "km2"), (40, "ha"), (250, "m")]


def test_extraction_ignore_urls_code_et_liens():
    texte = ("Voir [la carte 2](https://hub.example.fr/livrables/123456) "
             "et `bati_2024_v3` puis https://data.geopf.fr/wfs?count=5000 fin.")
    assert extraire_nombres(texte) == []


def test_extraction_ignore_identifiants_de_projection():
    assert extraire_nombres("en EPSG:2154, Lambert 93 ou WGS 84, voire RGF93 et CC44") == []


def test_extraction_ignore_dates_heures_et_puces():
    texte = "1. Charger\n2) Découper\nle 2026-09-24 à 15h30, ou 24/09/2026 10:45"
    assert extraire_nombres(texte) == []


def test_sources_json_et_francais():
    valeurs = {v for v, _ in nombres_des_sources(['{"n": 51450444, "x": -63.28, "e": 1.5e3}',
                                                  "300 551 bâtiments"])}
    assert {51450444.0, -63.28, 1500.0, 300551.0} <= valeurs


# ── Tracage ────────────────────────────────────────────────────────────────

def test_compte_exact_trace():
    v = verifier_chiffres(f"J'ai chargé 68{FINE}412 bâtiments.", [SOURCE_S1])
    assert v.ok
    assert v.nombres[0].statut == TRACE and v.nombres[0].source == 0


def test_compte_invente_non_trace():
    v = verifier_chiffres("J'ai chargé 70 000 bâtiments... non, 69 412.", [SOURCE_S1])
    assert not v.ok
    assert [n.texte for n in v.non_traces] == ["69 412"]


def test_incident_s1_chiffre_du_departement_non_trace():
    v = verifier_chiffres("Il y a 51 450 444 bâtiments à Aix.", ['{"feature_count": 68412}'])
    assert not v.ok


def test_arrondi_des_decimales_affichees():
    assert _statuts("emprise de 5,27 à 5,51", [SOURCE_S1]) == {"5,27": TRACE, "5,51": TRACE}


def test_mauvais_arrondi_refuse():
    # 51 450 444 s'arrondit a 51,5 millions, pas 51,4.
    assert _statuts("51,5 millions", ["51450444"]) == {"51,5 millions": TRACE}
    assert _statuts("51,4 millions", ["51450444"]) == {"51,4 millions": NON_TRACE}


def test_nombre_rond_accepte_comme_arrondi():
    assert _statuts("près de 300 000 bâtiments", ["300551"])["300 000"] == TRACE
    assert _statuts("300 000 bâtiments", ["300551"])["300 000"] == TRACE


def test_arrondi_des_zeros_desactivable():
    regles = ReglesChiffres(arrondi_zeros=False)
    assert _statuts("300 000 bâtiments", ["300551"], regles)["300 000"] == NON_TRACE


def test_approximation_annoncee_tolerance_relative():
    assert _statuts("environ 52 000 bâtiments", ["51450"])["52 000"] == TRACE
    assert _statuts("52 123 bâtiments", ["51450"])["52 123"] == NON_TRACE


def test_approximation_trop_loin_refusee():
    assert _statuts("environ 60 000 bâtiments", ["51450"])["60 000"] == NON_TRACE


def test_conversion_d_unites_de_surface():
    statuts = _statuts("La commune couvre 186,08 km², soit 18 608 ha.", [SOURCE_S1])
    assert statuts == {"186,08 km²": TRACE, "18 608 ha": TRACE}


def test_conversion_de_longueur():
    assert _statuts("un réseau de 12,4 km", ['{"longueur_m": 12403.2}'])["12,4 km"] == TRACE


def test_pourcentage_direct_ou_fraction():
    assert _statuts("12,5 % des bâtiments", ['{"part": 12.5}'])["12,5 %"] == TRACE
    assert _statuts("12,5 % des bâtiments", ['{"part": 0.125}'])["12,5 %"] == TRACE


def test_pourcentage_derive_d_un_rapport():
    v = verifier_chiffres("soit 25 % des bâtiments", ['{"clip": 17103, "total": 68412}'])
    assert v.ok
    assert v.nombres[0].statut == DERIVE


def test_pourcentage_sans_rapport_non_trace():
    assert _statuts("soit 12,5 % du département", ['{"n": 68412}'])["12,5 %"] == NON_TRACE


def test_petit_entier_de_discours_ignore():
    v = verifier_chiffres("J'ai fait 3 étapes et créé 2 couches.", [])
    assert v.ok
    assert {n.statut for n in v.nombres} == {IGNORE}


def test_seuil_des_petits_entiers_parametrable():
    regles = ReglesChiffres(seuil_petits_entiers=0)
    assert _statuts("3 couches", [], regles)["3"] == NON_TRACE


def test_petit_pourcentage_non_ignore():
    assert _statuts("5 % des bâtiments", [])["5 %"] == NON_TRACE


def test_annee_ignoree_sauf_separateur():
    statuts = _statuts("Données BD TOPO 2024, mises à jour en 2023.", [])
    assert statuts == {"2024": IGNORE, "2023": IGNORE}
    assert _statuts("2 024 bâtiments", [])["2 024"] == NON_TRACE


def test_annee_presente_dans_les_sources_reste_tracee():
    v = verifier_chiffres("2024 bâtiments", ['{"n": 2024}'])
    assert v.nombres[0].statut == TRACE


def test_arrondissement_et_ordinaux_ignores():
    statuts = _statuts("dans le 4e arrondissement, le 1er et le 13ème, 2nde couche", [])
    assert set(statuts.values()) == {IGNORE}


def test_numero_arrondissement_sans_suffixe():
    assert _statuts("le 13 arrondissement", [])["13"] == IGNORE


def test_code_postal_et_insee_ignores():
    statuts = _statuts("13100 Aix-en-Provence, code INSEE 13001, code postal 83980", [])
    assert set(statuts.values()) == {IGNORE}


def test_identifiants_colles_ignores():
    assert extraire_nombres("scénario S1, niveau L2, modèle qwen3") == []


def test_identifiant_colle_ignore():
    # « 3D » : lettre collee apres ; « A4 » : lettre collee avant, jamais extrait.
    assert _statuts("une vue 3D et un format A4", []) == {"3": IGNORE}


def test_signe_ignore_si_la_source_est_negative():
    assert _statuts("une baisse de 12,5 %", ['{"evolution": -12.5}'])["12,5 %"] == TRACE


def test_coordonnees_negatives():
    assert _statuts("emprise de -63,28 à 55,9", ['{"extent": [-63.28, -21.77, 55.9, 51.97]}']) == {
        "-63,28": TRACE, "55,9": TRACE,
    }


def test_resume_liste_les_nombres_non_traces():
    v = verifier_chiffres("52 123 bâtiments et 68 412 routes", [SOURCE_S1])
    assert "52 123" in v.resume()
    assert "68 412" not in v.resume()


def test_reponse_sans_nombre():
    v = verifier_chiffres("C'est fait, la couche est chargée.", [SOURCE_S1])
    assert v.ok and v.nombres == []


def test_sources_vides():
    v = verifier_chiffres("68 412 bâtiments", [])
    assert not v.ok


def test_nombre_repris_dans_une_source_textuelle_francaise():
    assert _statuts("300 551 bâtiments", ["Marseille : 300 551 bâtiments mesurés"])["300 551"] == TRACE


def test_plusieurs_nombres_groupes_distincts():
    statuts = _statuts("entre 5 000 et 10 000 bâtiments, dont 1 234 tracés", ["5000 10000 1234"])
    assert statuts == {"5 000": TRACE, "10 000": TRACE, "1 234": TRACE}


def test_annee_suivie_d_un_compte_non_fusionnee():
    nombres = extraire_nombres("en 2024 150 bâtiments")
    assert [n.valeur for n in nombres] == [2024, 150]
