"""Tests fix H5 revue adversariale Sprint V0.4.1 : polish_validator.

Verifie que le validator refuse tout polish qui modifie chiffres, URLs
ou sigles ALL CAPS. C'est le garde-fou programmatique de l'anti-hallucination
promise par le mode recipe_polished (G4-b-3b).
"""
from hub.recipes_web.polish_validator import validate_polish


def test_polish_ok_reformulation_stylistique():
    """Reformulation legitime : ordre des mots, ponctuation -> OK."""
    original = "Le PPRi de Marseille a ete approuve en 2019 par la prefecture."
    polished = "En 2019, la prefecture a approuve le PPRi de Marseille."
    ok, violations = validate_polish(original, polished)
    assert ok, f"violations inattendues : {violations}"


def test_polish_refuse_chiffre_modifie():
    """LLM change 2019 -> 2024 : refuse."""
    original = "Le PPRi a ete approuve en 2019."
    polished = "Le PPRi a ete approuve en 2024."
    ok, violations = validate_polish(original, polished)
    assert not ok
    assert any("numbers_removed" in v and "2019" in v for v in violations)
    assert any("numbers_added" in v and "2024" in v for v in violations)


def test_polish_refuse_pourcent_modifie():
    """LLM change 45% -> 90% : refuse (chiffre avec unite)."""
    original = "45% de la commune est en zone rouge."
    polished = "90% de la commune est en zone rouge."
    ok, violations = validate_polish(original, polished)
    assert not ok


def test_polish_refuse_url_modifie():
    """LLM change une URL : refuse."""
    original = "Consulter https://georisques.gouv.fr pour verifier."
    polished = "Consulter https://evil.example.com pour verifier."
    ok, violations = validate_polish(original, polished)
    assert not ok
    assert any("urls_removed" in v for v in violations)
    assert any("urls_added" in v for v in violations)


def test_polish_refuse_acronym_supprime():
    """LLM supprime un sigle CEREMA : refuse."""
    original = "Le CEREMA et l'INSEE collaborent."
    polished = "L'organisation travaille avec l'INSEE."
    ok, violations = validate_polish(original, polished)
    assert not ok
    assert any("acronyms_removed" in v and "CEREMA" in v for v in violations)


def test_polish_refuse_acronym_ajoute():
    """LLM hallucine un sigle inexistant : refuse."""
    original = "Consulter la mairie pour verifier."
    polished = "Consulter la mairie et le CEREMA pour verifier."
    ok, violations = validate_polish(original, polished)
    assert not ok
    assert any("acronyms_added" in v and "CEREMA" in v for v in violations)


def test_polish_accepte_reformulation_avec_meme_faits():
    """Tous les faits preserves + reformulation stylistique -> OK."""
    original = (
        "En 2019, le PPRi a ete approuve. Consulter https://georisques.gouv.fr. "
        "Contactez la DDRM 13."
    )
    polished = (
        "Le PPRi a ete approuve en 2019. Contactez la DDRM 13 ou consultez "
        "https://georisques.gouv.fr."
    )
    ok, violations = validate_polish(original, polished)
    assert ok, f"violations : {violations}"


def test_polish_texte_sans_faits():
    """Texte purement narratif sans chiffres/URLs/sigles -> OK."""
    original = "La commune presente des enjeux."
    polished = "Les enjeux de la commune sont importants."
    ok, violations = validate_polish(original, polished)
    assert ok


def test_polish_empty_strings():
    """Textes vides ou None -> OK (aucun fait a comparer)."""
    ok, _ = validate_polish("", "")
    assert ok
    ok, _ = validate_polish(None, None)  # type: ignore[arg-type]
    assert ok


# Sprint V0.4.2 Chantier E (MED#5) : noms propres


def test_polish_refuse_nom_propre_modifie():
    """Fix MED#5 : LLM change Marseille -> Marseilles : refuse."""
    original = "La commune de Marseille est concernee."
    polished = "La commune de Marseilles est concernee."
    ok, violations = validate_polish(original, polished)
    assert not ok
    assert any("proper_nouns_removed" in v and "Marseille" in v for v in violations)
    assert any("proper_nouns_added" in v and "Marseilles" in v for v in violations)


def test_polish_refuse_nom_propre_supprime():
    """Fix MED#5 : LLM supprime un nom propre : refuse."""
    original = "La commune de Marseille et de Bezier sont concernees."
    polished = "La commune concerne les zones etudiees."
    ok, violations = validate_polish(original, polished)
    assert not ok
    assert any("proper_nouns_removed" in v for v in violations)


def test_polish_accepte_nom_propre_conserve():
    """Fix MED#5 : Marseille reste Marseille apres reformulation."""
    original = "Le PPRi a ete approuve en 2019 pour la commune de Marseille."
    polished = "En 2019, la commune de Marseille a vu son PPRi approuve."
    ok, violations = validate_polish(original, polished)
    assert ok, f"violations : {violations}"


def test_polish_accepte_departement_compose():
    """Fix MED#5 : departement compose Bouches-du-Rhone preserve."""
    original = "Consulter la DDRM des Bouches-du-Rhone."
    polished = "Consulter la DDRM du departement des Bouches-du-Rhone."
    ok, violations = validate_polish(original, polished)
    assert ok, f"violations : {violations}"


def test_polish_multiplicite_chiffres_ok():
    """Chiffre repete plusieurs fois dans original mais une seule fois
    dans polished -> OK (set-based comparison)."""
    original = "En 2019, 2019 a ete une annee cle."
    polished = "L'annee 2019 a ete une annee cle."
    ok, _ = validate_polish(original, polished)
    assert ok
