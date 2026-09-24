"""Verificateur d'URL et controles textuels."""
from __future__ import annotations

from evals.verifs.texte import motifs_absents, motifs_presents, mots_presents, normaliser, pose_une_question
from evals.verifs.urls import (
    AUTORISEE, CATALOGUE_PAR_DEFAUT, INTERDITE, INVENTEE, extraire_urls, verifier_urls,
)

HUB = "https://user-x-hub.user.lab.sspcloud.fr"
BLANCHE = [HUB, *CATALOGUE_PAR_DEFAUT]


def _statuts(reponse: str, **options) -> dict[str, str]:
    return {u.url: u.statut for u in verifier_urls(reponse, options.pop("blanche", BLANCHE), **options).urls}


def test_extraction_liens_markdown_et_urls_nues_sans_doublon():
    texte = (f"[carte]({HUB}/livrables/a) et {HUB}/livrables/a. "
             "Source : https://data.geopf.fr/wfs, voir aussi <https://www.insee.fr/fr>.")
    assert extraire_urls(texte) == [f"{HUB}/livrables/a", "https://data.geopf.fr/wfs", "https://www.insee.fr/fr"]


def test_images_data_ignorees():
    assert extraire_urls("![capture](data:image/jpeg;base64,AAAA)") == []


def test_url_du_hub_autorisee():
    assert _statuts(f"Ta carte : [voir]({HUB}/livrables/carte-bati)") == {f"{HUB}/livrables/carte-bati": AUTORISEE}


def test_hote_du_catalogue_et_sous_domaine():
    statuts = _statuts("https://data.geopf.fr/wms-r et https://wxs.data.geopf.fr/x")
    assert set(statuts.values()) == {AUTORISEE}


def test_hote_inconnu_interdit():
    assert _statuts("https://example.com/donnees") == {"https://example.com/donnees": INTERDITE}


def test_hote_ressemblant_interdit():
    assert _statuts("https://data.geopf.fr.evil.com/x")["https://data.geopf.fr.evil.com/x"] == INTERDITE


def test_minio_et_s3_signes_interdits_meme_sur_hote_autorise():
    statuts = _statuts(
        "[a](https://minio.lab.sspcloud.fr/u/b.gpkg) "
        f"[b]({HUB}/f.gpkg?X-Amz-Signature=abc)",
        blanche=[*BLANCHE, "minio.lab.sspcloud.fr"],
    )
    assert set(statuts.values()) == {INTERDITE}


def test_lien_undefined_et_relatif_interdits():
    statuts = _statuts("[Voir](undefined) et [là](/studies/s1/file/x.pdf)")
    assert statuts == {"undefined": INTERDITE, "/studies/s1/file/x.pdf": INTERDITE}


def test_lien_relatif_admis_sur_option():
    assert _statuts("[là](/studies/s1/file/x.pdf)", relatifs_autorises=True) == {
        "/studies/s1/file/x.pdf": AUTORISEE}


def test_prefixe_de_chemin_dans_la_liste_blanche():
    blanche = ["https://hub.example.fr/livrables"]
    assert _statuts("https://hub.example.fr/livrables/x", blanche=blanche) == {
        "https://hub.example.fr/livrables/x": AUTORISEE}
    assert _statuts("https://hub.example.fr/admin", blanche=blanche) == {
        "https://hub.example.fr/admin": INTERDITE}
    assert _statuts("https://hub.example.fr/livrablesX", blanche=blanche) == {
        "https://hub.example.fr/livrablesX": INTERDITE}


def test_schema_http_different_refuse_pour_une_entree_https():
    assert _statuts(f"{HUB.replace('https', 'http')}/x") == {f"{HUB.replace('https', 'http')}/x": INTERDITE}


def test_url_inventee_quand_la_trace_est_exigee():
    sources = [f'{{"hub_url": "{HUB}/livrables/carte-bati"}}']
    statuts = _statuts(f"[a]({HUB}/livrables/carte-bati) [b]({HUB}/livrables/carte-bat1)",
                       sources=sources, exiger_trace=True)
    assert statuts == {f"{HUB}/livrables/carte-bati": AUTORISEE, f"{HUB}/livrables/carte-bat1": INVENTEE}


def test_ponctuation_finale_retiree():
    assert extraire_urls("Voir https://www.insee.fr/fr.") == ["https://www.insee.fr/fr"]


def test_resume():
    v = verifier_urls("https://example.com", BLANCHE)
    assert not v.ok and "example.com" in v.resume()


# ── Texte ──────────────────────────────────────────────────────────────────

def test_normaliser():
    assert normaliser("Épreuve Bâti") == "epreuve bati"


def test_mots_presents_mot_entier_casse_et_accents():
    texte = "J'ai utilisé smart_load puis une BBox, en Lambert."
    assert mots_presents(texte, ["smart_load", "bbox", "wfs", "lambert"]) == ["smart_load", "bbox", "lambert"]
    assert mots_presents("les données JSONées", ["json"]) == []


def test_motifs():
    assert motifs_absents("un rectangle", ["rectangle", "emprise"]) == ["emprise"]
    assert motifs_presents("minio ici", ["(?i)MINIO", "s3"]) == ["(?i)MINIO"]


def test_question_en_fin_de_reponse():
    assert pose_une_question("Plusieurs communes existent.\n\nLaquelle veux-tu ?")
    assert pose_une_question("Voici les choix :\n\n- A\n- B\n\nLaquelle ?\n")
    assert pose_une_question("**Laquelle veux-tu ?**")


def test_pas_de_question_ou_question_enterree():
    assert not pose_une_question("C'est fait.")
    assert not pose_une_question("")
    texte = "Tu veux Aix ?\n\nJ'ai chargé.\n\nPuis découpé.\n\nPuis nommé.\n\nFini."
    assert not pose_une_question(texte)
