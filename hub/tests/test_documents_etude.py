"""Corpus documentaire d'etude (lot L7) : extraction, API, recherche bornee.

Les documents sont generes a la volee (``fabrique_documents``) ; le stockage
pointe vers un dossier temporaire ; l'appartenance des etudes est simulee :
``etA`` et ``etB`` appartiennent a l'utilisateur de test, ``etX`` a un autre.
"""
from __future__ import annotations

import sys
import time
import zipfile
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

_ROOT = Path(__file__).resolve().parents[1]
for chemin in (_ROOT, Path(__file__).resolve().parent):
    if str(chemin) not in sys.path:
        sys.path.insert(0, str(chemin))

import fabrique_documents as fab  # noqa: E402
from hub import auth  # noqa: E402
from hub import documents_etude as de  # noqa: E402
from hub import documents_extraction as dx  # noqa: E402
from hub import main as hub_main  # noqa: E402
from hub import studies  # noqa: E402

PROPRIETAIRES = {"etA": "test", "etB": "test", "etX": "autre"}

RAPPORT_PAGES = [
    "1. Introduction\nLe rapport de phase 1 présente le diagnostic du bassin versant.",
    "2. Zones humides\nLes zones humides du secteur nord couvrent 12 hectares.\n"
    "Leur préservation est une priorité du SAGE.",
    "3. Risque inondation\nLa digue de l'Arc protège le quartier des Milles.",
]


# ── Fixtures ────────────────────────────────────────────────────────────────

@pytest.fixture
def racine(tmp_path, monkeypatch):
    monkeypatch.setattr(de, "RACINE", tmp_path / "documents")
    de._INDEX.clear()
    de.EN_COURS.clear()
    yield tmp_path / "documents"
    de._INDEX.clear()
    de.EN_COURS.clear()


@pytest.fixture
def client(racine, monkeypatch):
    async def _utilisateur():
        return {"username": "test", "scope": "user"}

    async def _etude(sid, owner=None):
        proprio = PROPRIETAIRES.get(sid)
        if proprio is None or (owner and owner != proprio):
            return None
        return {"id": sid, "owner": proprio, "name": sid}

    monkeypatch.setattr(studies, "get_study", _etude)
    hub_main.app.dependency_overrides[auth.get_current_user] = _utilisateur
    # Sans « with » : pas de cycle de vie (bootstrap agent, taches de fond).
    yield TestClient(hub_main.app, headers={"user-agent": "kube-probe/1.0"})
    hub_main.app.dependency_overrides.pop(auth.get_current_user, None)


def _deposer(client, sid, nom, contenu):
    return client.post(f"/studies/{sid}/documents", files={"file": (nom, contenu)})


# ── Extraction ──────────────────────────────────────────────────────────────

def test_extraction_pdf_page_par_page_avec_accents(tmp_path):
    chemin = tmp_path / "r.pdf"
    chemin.write_bytes(fab.pdf(RAPPORT_PAGES))
    ext = dx.extraire(chemin, "pdf")
    assert ext.n_pages == 3
    assert [u.page for u in ext.unites] == [1, 2, 3]
    assert "préservation" in ext.unites[1].texte
    assert ext.unites[1].section == "2. Zones humides"


def test_extraction_pdf_sans_texte_est_refusee_clairement(tmp_path):
    chemin = tmp_path / "scan.pdf"
    chemin.write_bytes(fab.pdf(["", ""]))
    with pytest.raises(dx.ErreurExtraction, match="OCR"):
        dx.extraire(chemin, "pdf")


def test_extraction_docx_par_section(tmp_path):
    chemin = tmp_path / "c.docx"
    chemin.write_bytes(fab.docx([("titre", "Objet"), ("texte", "Cartographie des haies."),
                                 ("titre", "Délais"), ("texte", "Livraison sous 3 mois.")]))
    ext = dx.extraire(chemin, "docx")
    assert [u.section for u in ext.unites] == ["Objet", "Délais"]
    assert "Livraison sous 3 mois." in ext.unites[1].texte


def test_extraction_odt(tmp_path):
    chemin = tmp_path / "n.odt"
    chemin.write_bytes(fab.odt([("titre", "Contexte"), ("texte", "La commune compte 4 000 habitants.")]))
    ext = dx.extraire(chemin, "odt")
    assert ext.unites[0].section == "Contexte"
    assert "4 000 habitants" in ext.unites[0].texte


def test_extraction_markdown_et_texte_windows(tmp_path):
    md = tmp_path / "n.md"
    md.write_text("# Titre\nIntro.\n\n## Méthode\nOn compte les bâtiments.\n", encoding="utf-8")
    ext = dx.extraire(md, "markdown")
    assert [u.section for u in ext.unites] == ["Titre", "Méthode"]
    txt = tmp_path / "w.txt"
    txt.write_bytes("Zone d'activité économique".encode("cp1252"))
    assert "activité" in dx.extraire(txt, "texte").unites[0].texte


def test_extraction_tableaux_csv_et_xlsx_decrits(tmp_path):
    c = tmp_path / "t.csv"
    c.write_text("commune;population\nAix;145000\nGardanne;21000\n", encoding="utf-8")
    ext = dx.extraire(c, "csv")
    assert "2 ligne(s) de données" in ext.unites[0].texte
    assert "commune : Gardanne ; population : 21000" in ext.unites[1].texte
    x = tmp_path / "t.xlsx"
    x.write_bytes(fab.xlsx("Communes", [["commune", "population"], ["Aix", 145000]]))
    ext = dx.extraire(x, "xlsx")
    assert ext.unites[0].section == "Communes"
    assert "commune : Aix ; population : 145000" in ext.unites[1].texte


def test_extraction_archive_trop_grosse_decompressee(tmp_path, monkeypatch):
    monkeypatch.setattr(dx, "_XML_MAX", 100)
    chemin = tmp_path / "gros.docx"
    chemin.write_bytes(fab.docx([("texte", "x" * 5000)]))
    with pytest.raises(dx.ErreurExtraction, match="volumineux"):
        dx.extraire(chemin, "docx")


def test_decoupage_borne_sans_franchir_les_pages():
    long_texte = " ".join(f"Phrase numéro {i} du rapport sur les digues." for i in range(200))
    unites = [dx.Unite(long_texte, page=1), dx.Unite("Page deux courte.", page=2)]
    segs = dx.decouper(unites, taille=500, recouvrement=80)
    assert all(len(s["texte"]) <= 500 for s in segs)
    assert segs[-1] == {"texte": "Page deux courte.", "page": 2, "section": None,
                        "i": len(segs) - 1}
    assert {s["page"] for s in segs[:-1]} == {1}
    # Recouvrement : le debut d'un segment reprend la fin du precedent.
    assert segs[1]["texte"].split(" ")[0] in segs[0]["texte"]


# ── API ─────────────────────────────────────────────────────────────────────

def test_depot_indexation_et_liste(client):
    r = _deposer(client, "etA", "Rapport_phase_1.pdf", fab.pdf(RAPPORT_PAGES))
    assert r.status_code == 202, r.text
    doc = r.json()["document"]
    assert doc["titre"] == "Rapport phase 1" and doc["format"] == "pdf"
    # TestClient execute les taches de fond avant de rendre la reponse.
    r = client.get("/studies/etA/documents")
    assert r.status_code == 200
    corps = r.json()
    (d,) = corps["documents"]
    assert d["statut"] == "indexe", d
    assert d["n_pages"] == 3 and d["n_segments"] >= 3
    assert corps["resume"] == {"total": 1, "indexes": 1, "en_cours": 0, "erreurs": 0}
    assert ".pdf" in corps["limites"]["formats"]
    assert client.get(f"/studies/etA/documents/{d['id']}").json()["document"]["id"] == d["id"]


def test_formats_non_pris_en_charge_refuses_clairement(client):
    r = _deposer(client, "etA", "outil.exe", b"MZ\x90\x00")
    assert r.status_code == 415
    assert "Formats acceptés" in r.json()["detail"]
    r = _deposer(client, "etA", "faux.pdf", b"ceci n'est pas un pdf")
    assert r.status_code == 415
    r = _deposer(client, "etA", "faux.docx", b"PK\x03\x04pas une archive")
    assert r.status_code == 415
    assert client.get("/studies/etA/documents").json()["documents"] == []


def test_taille_maximale_et_doublon(client, monkeypatch):
    monkeypatch.setenv("DOCUMENTS_TAILLE_MAX_MO", "1")
    r = _deposer(client, "etA", "gros.txt", b"a" * (1024 * 1024 + 10))
    assert r.status_code == 413
    assert "1 Mo" in r.json()["detail"]
    assert _deposer(client, "etA", "note.txt", b"Une note courte.").status_code == 202
    r = _deposer(client, "etA", "note-copie.txt", b"Une note courte.")
    assert r.status_code == 409 and "déjà" in r.json()["detail"]


def test_nombre_maximal_de_documents(client, monkeypatch):
    monkeypatch.setenv("DOCUMENTS_MAX_PAR_ETUDE", "2")
    for i in range(2):
        assert _deposer(client, "etA", f"n{i}.txt", f"note {i}".encode()).status_code == 202
    r = _deposer(client, "etA", "n3.txt", b"note 3")
    assert r.status_code == 409 and "maximum 2" in r.json()["detail"]


def test_document_en_erreur_puis_reindexation(client):
    r = _deposer(client, "etA", "scan.pdf", fab.pdf(["", ""]))
    doc_id = r.json()["document"]["id"]
    d = client.get(f"/studies/etA/documents/{doc_id}").json()["document"]
    assert d["statut"] == "erreur" and "OCR" in d["message"]
    r = client.post(f"/studies/etA/documents/{doc_id}/reindexer")
    assert r.status_code == 202
    assert client.get(f"/studies/etA/documents/{doc_id}").json()["document"]["statut"] == "erreur"


def test_retrait_supprime_fichier_et_segments(client, racine):
    doc_id = _deposer(client, "etA", "r.pdf", fab.pdf(RAPPORT_PAGES)).json()["document"]["id"]
    assert (racine / "etA" / "segments" / f"{doc_id}.json").is_file()
    assert client.delete(f"/studies/etA/documents/{doc_id}").status_code == 204
    assert client.get("/studies/etA/documents").json()["documents"] == []
    assert not (racine / "etA" / "fichiers" / f"{doc_id}.pdf").exists()
    assert not (racine / "etA" / "segments" / f"{doc_id}.json").exists()
    assert client.delete(f"/studies/etA/documents/{doc_id}").status_code == 404
    r = client.get("/studies/etA/documents/recherche", params={"q": "zones humides"})
    assert r.json()["statut"] == "aucun_document"


def test_telechargement_en_piece_jointe(client):
    contenu = fab.pdf(RAPPORT_PAGES)
    doc_id = _deposer(client, "etA", "r.pdf", contenu).json()["document"]["id"]
    r = client.get(f"/studies/etA/documents/{doc_id}/fichier")
    assert r.status_code == 200 and r.content == contenu
    assert "attachment" in r.headers["content-disposition"]
    assert r.headers["content-type"] == "application/octet-stream"


def test_etude_d_un_autre_utilisateur_repond_404(client):
    assert client.get("/studies/etX/documents").status_code == 404
    assert _deposer(client, "etX", "r.txt", b"texte").status_code == 404
    assert client.get("/studies/etX/documents/recherche", params={"q": "x"}).status_code == 404
    assert client.delete("/studies/etX/documents/abcdefabcdef").status_code == 404
    assert client.get("/studies/inconnue/documents").status_code == 404


def test_identifiants_invalides(client):
    assert client.get("/studies/etA/documents/..%2F..%2Fregistre").status_code == 404
    assert client.delete("/studies/etA/documents/pas-un-id").status_code == 404


# ── Recherche ───────────────────────────────────────────────────────────────

def test_recherche_sourcee_page_et_extrait_mot_pour_mot(client, racine):
    doc_id = _deposer(client, "etA", "Rapport_phase_1.pdf",
                      fab.pdf(RAPPORT_PAGES)).json()["document"]["id"]
    r = client.get("/studies/etA/documents/recherche",
                   params={"q": "Que dit le rapport sur les zones humides ?", "k": 2})
    assert r.status_code == 200
    corps = r.json()
    assert corps["statut"] == "ok"
    premier = corps["resultats"][0]
    assert premier["doc_id"] == doc_id and premier["page"] == 2
    assert premier["titre"] == "Rapport phase 1"
    assert premier["score"] == 1.0
    assert "12 hectares" in premier["extrait"]
    # L'extrait figure mot pour mot dans le texte indexe.
    import json
    segments = json.loads((racine / "etA" / "segments" / f"{doc_id}.json")
                          .read_text(encoding="utf-8"))["segments"]
    assert any(premier["extrait"] in s["texte"] for s in segments)
    assert len(corps["resultats"]) <= 2


def test_recherche_insensible_aux_accents_et_aux_pluriels(client):
    _deposer(client, "etA", "r.pdf", fab.pdf(RAPPORT_PAGES))
    r = client.get("/studies/etA/documents/recherche", params={"q": "preserver zone humide"})
    assert r.json()["resultats"][0]["page"] == 2


def test_recherche_hors_corpus_rend_vide(client):
    _deposer(client, "etA", "r.pdf", fab.pdf(RAPPORT_PAGES))
    r = client.get("/studies/etA/documents/recherche",
                   params={"q": "tarif des cantines scolaires en 2024"})
    assert r.json()["statut"] == "vide" and r.json()["resultats"] == []


def test_recherche_bornee_a_l_etude_aucune_fuite(client):
    """Un terme present dans etA seulement ne sort jamais d'une recherche sur etB."""
    id_a = _deposer(client, "etA", "rapport_A.pdf", fab.pdf(RAPPORT_PAGES)).json()["document"]["id"]
    id_b = _deposer(client, "etB", "note_B.md",
                    "# Voirie\nLa chaussée de la RD7 sera refaite en 2027.\n"
                    .encode()).json()["document"]["id"]
    rb = client.get("/studies/etB/documents/recherche", params={"q": "zones humides digue"}).json()
    assert rb["statut"] == "vide"
    assert [d["id"] for d in rb["documents"]] == [id_b]
    rb = client.get("/studies/etB/documents/recherche", params={"q": "chaussée RD7"}).json()
    assert {x["doc_id"] for x in rb["resultats"]} == {id_b}
    ra = client.get("/studies/etA/documents/recherche", params={"q": "chaussée RD7 zones"}).json()
    assert {x["doc_id"] for x in ra["resultats"]} <= {id_a}


def test_recherche_ignore_les_documents_non_indexes(racine):
    de.ajouter("etA", "attente.txt", b"Les zones humides en attente.")
    r = de.rechercher("etA", "zones humides")
    assert r["statut"] == "aucun_document" and r["en_cours"] == 1


def test_statut_transitoire_orphelin_signale_interrompu(racine, monkeypatch):
    doc = de.ajouter("etA", "n.txt", b"Une note.")
    # Plus de tache active, et le dernier changement date de 10 minutes.
    de._maj_document("etA", doc["id"], maj_ts=time.time() - 600)
    (d,) = de.lister("etA")
    assert d["statut"] == "erreur" and "relancez" in d["message"]


def test_purge_de_l_etude_supprime_son_corpus(racine):
    de.ajouter("etA", "n.txt", b"Une note.")
    assert (racine / "etA").is_dir()
    de.supprimer_etude("etA")
    assert not (racine / "etA").exists()
    assert de.lister("etA") == []


def test_termes_normalises():
    assert de.termes("Les Zones Humides") == de.termes("zone humide")
    assert de.termes("réseaux") == de.termes("réseau")
    assert "que" not in de.termes("Que dit le document ?")


def test_archive_docx_valide_exige_le_document_principal(racine):
    import io
    tampon = io.BytesIO()
    with zipfile.ZipFile(tampon, "w") as z:
        z.writestr("autre.xml", "<a/>")
    with pytest.raises(de.ErreurDocument) as exc:
        de.ajouter("etA", "x.docx", tampon.getvalue())
    assert exc.value.code == 415
