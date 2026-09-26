"""Corpus documentaire d'une etude : stockage, indexation, recherche (lot L7).

L'utilisateur depose des documents (PDF, DOCX, ODT, TXT, MD, CSV, XLSX) dans
son etude ; l'agent les consulte par ``consulter_documents`` et cite ses
sources (document, page ou section).

Ou vivent les documents
-----------------------
Sur le volume du HUB (celui de ``studies.db`` et du depot local des
publications), pas sur le PVC du workspace :

    <RACINE>/<sid>/registre.json          metadonnees et statut, par etude
    <RACINE>/<sid>/fichiers/<id>.<ext>    original depose
    <RACINE>/<sid>/segments/<id>.json     texte decoupe (page, section)

Raisons : le hub lit et ecrit ce volume directement (le PVC du workspace
n'est joignable que par ``execute_python`` dans un pod qui peut dormir) ;
l'extraction tourne la ou sont les fichiers ; et un dossier par etude rend
toute fuite inter-etudes structurellement impossible -- la recherche ne lit
que le dossier de l'etude demandee, apres controle du proprietaire par l'API.

Pourquoi un index lexical et pas ``vector_store`` de l'agent
-------------------------------------------------------------
``vector_store`` vit dans ``memory.db`` sur le volume de l'AGENT (RWO,
distinct) et partage une table pour toutes les etudes : le KNN de sqlite-vec
s'applique avant le filtre d'etude. Le hub, lui, n'a pas la cle du modele
d'embedding dans son environnement. On indexe donc ici en BM25 (mots
normalises sans accents, racinisation legere du francais), par etude, sans
dependance ni appel reseau ; l'agent reclasse ensuite les candidats par
similarite semantique (``vector_store.embed_batch``) quand l'API
d'embedding repond. Voir docs/superpowers/specs/2026-09-26-corpus-documentaire.md.

Statuts
-------
``en_attente`` (depose, extraction a venir) -> ``extraction`` -> ``indexe``
ou ``erreur`` (message lisible). Un document reste ``en_attente`` ou
``extraction`` si le service redemarre pendant le traitement : ``lister`` le
signale alors en ``erreur`` (« interrompue, relancez »).
"""
from __future__ import annotations

import hashlib
import json
import logging
import math
import os
import re
import secrets
import shutil
import tempfile
import threading
import time
import unicodedata
from collections import Counter
from pathlib import Path
from typing import Any

from hub import depot_local
from hub import documents_extraction as dx

log = logging.getLogger("hub.documents_etude")

# ── Configuration ────────────────────────────────────────────────────────────

RACINE = Path(os.getenv("DOCUMENTS_RACINE") or (depot_local.RACINE_DONNEES / "documents_etudes"))

FORMATS: dict[str, str] = {
    ".pdf": "pdf",
    ".docx": "docx",
    ".odt": "odt",
    ".txt": "texte",
    ".md": "markdown",
    ".markdown": "markdown",
    ".csv": "csv",
    ".xlsx": "xlsx",
}

LIBELLES_FORMATS = "PDF, DOCX, ODT, TXT, MD, CSV, XLSX"


def _entier_env(nom: str, defaut: int) -> int:
    try:
        return max(1, int(os.getenv(nom, str(defaut))))
    except ValueError:
        return defaut


def limites() -> dict[str, int]:
    """Lues a chaque appel : reglables sans redemarrage de test."""
    return {
        "taille_max_mo": _entier_env("DOCUMENTS_TAILLE_MAX_MO", 25),
        "max_documents": _entier_env("DOCUMENTS_MAX_PAR_ETUDE", 50),
        "volume_max_mo": _entier_env("DOCUMENTS_VOLUME_MAX_MO", 250),
        "caracteres_max": _entier_env("DOCUMENTS_CARACTERES_MAX", 2_000_000),
    }


# Apres ce delai sans tache active, un statut transitoire est un reliquat.
_DELAI_INTERRUPTION_S = 120

_RE_SID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$")
_RE_DOC_ID = re.compile(r"^[a-f0-9]{12}$")

_verrou = threading.RLock()
# Indexations en cours dans CE processus : (sid, doc_id).
EN_COURS: set[tuple[str, str]] = set()


class ErreurDocument(Exception):
    """Refus lisible par l'utilisateur ; ``code`` est le statut HTTP."""

    def __init__(self, code: int, message: str):
        super().__init__(message)
        self.code = code
        self.message = message


# ── Chemins ──────────────────────────────────────────────────────────────────

def _dossier(sid: str) -> Path:
    if not _RE_SID.match(sid or ""):
        raise ErreurDocument(400, "Identifiant d'étude invalide.")
    return RACINE / sid


def _verifier_doc_id(doc_id: str) -> str:
    if not _RE_DOC_ID.match(doc_id or ""):
        raise ErreurDocument(404, "Document introuvable.")
    return doc_id


def _ecrire_json(chemin: Path, donnees: Any) -> None:
    """Ecriture atomique (fichier temporaire puis remplacement)."""
    chemin.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=chemin.parent, prefix=".tmp-", suffix=".json")
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as f:
            json.dump(donnees, f, ensure_ascii=False)
        os.replace(tmp, chemin)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise


def _lire_registre(sid: str) -> list[dict]:
    chemin = _dossier(sid) / "registre.json"
    if not chemin.is_file():
        return []
    try:
        donnees = json.loads(chemin.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        log.warning("registre illisible pour l'etude %s", sid)
        return []
    return donnees.get("documents", []) if isinstance(donnees, dict) else []


def _ecrire_registre(sid: str, documents: list[dict]) -> None:
    _ecrire_json(_dossier(sid) / "registre.json", {"version": 1, "documents": documents})
    _INDEX.pop(sid, None)  # l'index lexical sera reconstruit a la demande


def _maj_document(sid: str, doc_id: str, **champs: Any) -> dict | None:
    with _verrou:
        docs = _lire_registre(sid)
        for d in docs:
            if d["id"] == doc_id:
                d.update(champs)
                _ecrire_registre(sid, docs)
                return dict(d)
    return None


# ── Metadonnees publiques ────────────────────────────────────────────────────

_CHAMPS_PUBLICS = ("id", "titre", "nom_fichier", "format", "taille", "date_ajout",
                   "auteur", "statut", "message", "n_pages", "n_segments",
                   "n_caracteres", "avertissements", "date_indexation")


def _public(d: dict) -> dict:
    return {k: d.get(k) for k in _CHAMPS_PUBLICS}


def _titre_depuis_nom(nom: str) -> str:
    base = Path(nom).stem
    titre = re.sub(r"[_]+", " ", base).strip()
    return (titre or base or "Document")[:120]


def _nom_sur(nom: str | None) -> str:
    brut = (nom or "").replace("\\", "/").split("/")[-1].strip()
    brut = "".join(c for c in brut if c.isprintable())
    if not brut or brut.startswith(".") or ".." in brut:
        raise ErreurDocument(400, "Nom de fichier invalide.")
    return brut[:200]


# ── Controle du contenu ──────────────────────────────────────────────────────

_MEMBRES_ARCHIVE = {
    "docx": "word/document.xml",
    "odt": "content.xml",
    "xlsx": "xl/workbook.xml",
}


def _verifier_contenu(format_doc: str, contenu: bytes) -> None:
    """Le contenu doit correspondre a l'extension annoncee (pas d'executable
    renomme en .pdf, pas de binaire renomme en .txt)."""
    if not contenu:
        raise ErreurDocument(400, "Le fichier est vide.")
    if format_doc == "pdf":
        if not contenu[:1024].lstrip().startswith(b"%PDF-"):
            raise ErreurDocument(415, "Ce fichier n'est pas un PDF valide.")
        return
    if format_doc in _MEMBRES_ARCHIVE:
        import io
        import zipfile
        if not contenu.startswith(b"PK\x03\x04"):
            raise ErreurDocument(415, f"Ce fichier n'est pas un {format_doc.upper()} valide.")
        try:
            with zipfile.ZipFile(io.BytesIO(contenu)) as z:
                noms = set(z.namelist())
        except zipfile.BadZipFile as exc:
            raise ErreurDocument(415, f"Ce fichier n'est pas un {format_doc.upper()} valide.") from exc
        if _MEMBRES_ARCHIVE[format_doc] not in noms:
            raise ErreurDocument(415, f"Ce fichier n'est pas un {format_doc.upper()} valide.")
        return
    # Texte : pas d'octet nul dans le debut du fichier.
    if b"\x00" in contenu[:8192]:
        raise ErreurDocument(415, "Ce fichier n'est pas un fichier texte.")


# ── Operations ───────────────────────────────────────────────────────────────

def _marquer_interrompus(sid: str, docs: list[dict]) -> bool:
    """Statut transitoire sans tache active depuis trop longtemps -> erreur."""
    change = False
    maintenant = time.time()
    for d in docs:
        if d.get("statut") in ("en_attente", "extraction") \
                and (sid, d["id"]) not in EN_COURS \
                and maintenant - float(d.get("maj_ts") or 0) > _DELAI_INTERRUPTION_S:
            d["statut"] = "erreur"
            d["message"] = "Indexation interrompue (redémarrage du service) : relancez-la."
            change = True
    return change


def lister(sid: str) -> list[dict]:
    """Documents de l'etude, du plus recent au plus ancien."""
    with _verrou:
        docs = _lire_registre(sid)
        if _marquer_interrompus(sid, docs):
            _ecrire_registre(sid, docs)
    return [_public(d) for d in sorted(docs, key=lambda d: d.get("ajout_ts") or 0,
                                       reverse=True)]


def obtenir(sid: str, doc_id: str) -> dict:
    _verifier_doc_id(doc_id)
    for d in lister(sid):
        if d["id"] == doc_id:
            return d
    raise ErreurDocument(404, "Document introuvable.")


def resume(sid: str) -> dict:
    """Compteurs par statut (pour la L2 de l'agent et l'en-tete de l'UI)."""
    docs = lister(sid)
    par_statut = Counter(d["statut"] for d in docs)
    return {"total": len(docs), "indexes": par_statut.get("indexe", 0),
            "en_cours": par_statut.get("en_attente", 0) + par_statut.get("extraction", 0),
            "erreurs": par_statut.get("erreur", 0)}


def ajouter(sid: str, nom_fichier: str | None, contenu: bytes,
            auteur: str | None = None) -> dict:
    """Enregistre un document (statut ``en_attente``). L'indexation est a
    lancer ensuite (``indexer``), en tache de fond."""
    nom = _nom_sur(nom_fichier)
    ext = Path(nom).suffix.lower()
    format_doc = FORMATS.get(ext)
    if not format_doc:
        raise ErreurDocument(
            415, f"Format non pris en charge ({ext or 'sans extension'}). "
                 f"Formats acceptés : {LIBELLES_FORMATS}.")
    lim = limites()
    if len(contenu) > lim["taille_max_mo"] * 1024 * 1024:
        raise ErreurDocument(413, f"Fichier trop volumineux (maximum "
                                  f"{lim['taille_max_mo']} Mo par document).")
    _verifier_contenu(format_doc, contenu)
    empreinte = hashlib.sha256(contenu).hexdigest()
    dossier = _dossier(sid)
    with _verrou:
        docs = _lire_registre(sid)
        if len(docs) >= lim["max_documents"]:
            raise ErreurDocument(409, f"L'étude a déjà {len(docs)} documents "
                                      f"(maximum {lim['max_documents']}). Retirez-en un.")
        volume = sum(int(d.get("taille") or 0) for d in docs) + len(contenu)
        if volume > lim["volume_max_mo"] * 1024 * 1024:
            raise ErreurDocument(413, f"Volume documentaire de l'étude dépassé "
                                      f"(maximum {lim['volume_max_mo']} Mo).")
        for d in docs:
            if d.get("empreinte") == empreinte:
                raise ErreurDocument(409, f"Ce document est déjà dans l'étude "
                                          f"(« {d.get('titre')} »).")
        doc_id = secrets.token_hex(6)
        (dossier / "fichiers").mkdir(parents=True, exist_ok=True)
        (dossier / "fichiers" / f"{doc_id}{ext}").write_bytes(contenu)
        maintenant = time.time()
        doc = {
            "id": doc_id,
            "titre": _titre_depuis_nom(nom),
            "nom_fichier": nom,
            "extension": ext,
            "format": format_doc,
            "taille": len(contenu),
            "empreinte": empreinte,
            "date_ajout": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(maintenant)),
            "ajout_ts": maintenant,
            "maj_ts": maintenant,
            "auteur": auteur,
            "statut": "en_attente",
            "message": None,
            "n_pages": None,
            "n_segments": 0,
            "n_caracteres": 0,
            "avertissements": [],
            "date_indexation": None,
        }
        docs.append(doc)
        _ecrire_registre(sid, docs)
    log.info("document ajoute etude=%s id=%s format=%s taille=%d",
             sid, doc_id, format_doc, len(contenu))
    return _public(doc)


def chemin_fichier(sid: str, doc_id: str) -> tuple[Path, dict]:
    _verifier_doc_id(doc_id)
    for d in _lire_registre(sid):
        if d["id"] == doc_id:
            chemin = _dossier(sid) / "fichiers" / f"{doc_id}{d.get('extension', '')}"
            if chemin.is_file():
                return chemin, d
    raise ErreurDocument(404, "Document introuvable.")


def retirer(sid: str, doc_id: str) -> None:
    _verifier_doc_id(doc_id)
    dossier = _dossier(sid)
    with _verrou:
        docs = _lire_registre(sid)
        doc = next((d for d in docs if d["id"] == doc_id), None)
        if doc is None:
            raise ErreurDocument(404, "Document introuvable.")
        docs = [d for d in docs if d["id"] != doc_id]
        _ecrire_registre(sid, docs)
        (dossier / "fichiers" / f"{doc_id}{doc.get('extension', '')}").unlink(missing_ok=True)
        (dossier / "segments" / f"{doc_id}.json").unlink(missing_ok=True)
    _INDEX.pop(sid, None)
    log.info("document retire etude=%s id=%s", sid, doc_id)


def supprimer_etude(sid: str) -> None:
    """Purge d'une etude : tout son corpus disparait."""
    dossier = _dossier(sid)
    with _verrou:
        shutil.rmtree(dossier, ignore_errors=True)
    _INDEX.pop(sid, None)


def preparer_reindexation(sid: str, doc_id: str) -> dict:
    _verifier_doc_id(doc_id)
    chemin_fichier(sid, doc_id)
    doc = _maj_document(sid, doc_id, statut="en_attente", message=None, maj_ts=time.time())
    if doc is None:
        raise ErreurDocument(404, "Document introuvable.")
    return _public(doc)


def indexer(sid: str, doc_id: str) -> dict | None:
    """Extraction + decoupage, synchrone (a lancer dans un thread).

    Ne leve jamais : toute erreur finit en statut ``erreur`` avec un message
    destine a l'utilisateur.
    """
    try:
        chemin, doc = chemin_fichier(sid, doc_id)
    except ErreurDocument:
        return None  # retire entre-temps
    _maj_document(sid, doc_id, statut="extraction", message=None, maj_ts=time.time())
    try:
        extraction = dx.extraire(chemin, doc["format"])
        avert = list(extraction.avertissements)
        lim = limites()
        unites = extraction.unites
        if extraction.n_caracteres > lim["caracteres_max"]:
            garde, total = [], 0
            for u in unites:
                if total + len(u.texte) > lim["caracteres_max"]:
                    break
                garde.append(u)
                total += len(u.texte)
            unites = garde
            avert.append(f"Document très long : seuls les {lim['caracteres_max']:,} "
                         "premiers caractères sont indexés.".replace(",", " "))
        segments = dx.decouper(unites)
        if not segments:
            raise dx.ErreurExtraction("Aucun texte exploitable dans ce document.")
        _ecrire_json(_dossier(sid) / "segments" / f"{doc_id}.json",
                     {"doc_id": doc_id, "empreinte": doc.get("empreinte"),
                      "segments": segments})
        maj = _maj_document(
            sid, doc_id, statut="indexe", message=None, maj_ts=time.time(),
            n_pages=extraction.n_pages, n_segments=len(segments),
            n_caracteres=sum(len(u.texte) for u in unites),
            avertissements=avert[:5],
            date_indexation=time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        )
        if maj is None:  # retire pendant l'extraction : pas de segments orphelins
            (_dossier(sid) / "segments" / f"{doc_id}.json").unlink(missing_ok=True)
            return None
        log.info("document indexe etude=%s id=%s segments=%d", sid, doc_id, len(segments))
    except dx.ErreurExtraction as exc:
        maj = _maj_document(sid, doc_id, statut="erreur", message=str(exc),
                            maj_ts=time.time(), n_segments=0)
    except Exception as exc:  # jamais de statut bloque
        log.exception("indexation document etude=%s id=%s", sid, doc_id)
        maj = _maj_document(sid, doc_id, statut="erreur", maj_ts=time.time(), n_segments=0,
                            message=f"Lecture impossible ({type(exc).__name__}).")
    _INDEX.pop(sid, None)
    return _public(maj) if maj else None


# ── Recherche lexicale (BM25) ────────────────────────────────────────────────

_MOTS_VIDES = frozenset("""
a au aux avec ce ces cet cette ceci cela ça dans de des du elle elles en et
eux il ils je la le les leur leurs lui ma mais me meme mes moi mon ne nos
notre nous on ou par pas pour qu que qui sa se ses son sur ta te tes toi ton
tu un une vos votre vous y d l j m n s t c qu est sont ete etre avoir ont a
as avons avez etait etaient sera seront fait faire plus moins tres tout tous
toute toutes aussi comme donc alors si dont quel quelle quels quelles quoi
comment pourquoi combien quand selon dit dis disent dire parle parlent
indique indiquent mentionne mentionnent prevoit precise precisent explique
document documents fichier fichiers pdf the of and to in is for on
""".split())

_SUFFIXES = ("issements", "issement", "atrices", "atrice", "ateurs", "ateur",
             "ations", "ation", "ements", "ement", "ments", "ment", "ites",
             "ite", "iques", "ique", "ables", "able", "istes", "iste",
             "euses", "euse", "eurs", "eur", "ives", "ive", "ifs", "if",
             "ees", "ee", "es", "e", "s", "x")


def normaliser(texte: str) -> str:
    t = unicodedata.normalize("NFKD", texte or "")
    return "".join(c for c in t if not unicodedata.combining(c)).lower()


def _racine(mot: str) -> str:
    if mot.isdigit() or len(mot) <= 3:
        return mot
    if mot.endswith("eaux"):
        return mot[:-1]            # reseaux -> reseau
    if mot.endswith("aux") and len(mot) > 5:
        return mot[:-3] + "al"     # canaux -> canal
    for suf in _SUFFIXES:
        if mot.endswith(suf) and len(mot) - len(suf) >= 3:
            return mot[:-len(suf)]
    return mot


_RE_MOT = re.compile(r"[a-z0-9]+")


def termes(texte: str) -> list[str]:
    """Mots normalises (sans accents ni casse), sans mots vides, racinises."""
    out = []
    for m in _RE_MOT.findall(normaliser(texte)):
        if m in _MOTS_VIDES or (len(m) < 2 and not m.isdigit()):
            continue
        out.append(_racine(m))
    return out


class _Index:
    """Index BM25 d'une etude, reconstruit quand le registre change."""

    K1 = 1.2
    B = 0.75

    def __init__(self, sid: str):
        self.segments: list[dict] = []
        self.tf: list[Counter] = []
        self.longueurs: list[int] = []
        df: Counter = Counter()
        for d in _lire_registre(sid):
            if d.get("statut") != "indexe":
                continue
            chemin = _dossier(sid) / "segments" / f"{d['id']}.json"
            try:
                segs = json.loads(chemin.read_text(encoding="utf-8")).get("segments", [])
            except (OSError, ValueError):
                continue
            titre_termes = termes(d.get("titre") or "")
            for s in segs:
                entete = termes(s.get("section") or "") + titre_termes
                t = Counter(termes(s.get("texte") or "") + entete)
                self.segments.append({**s, "doc_id": d["id"], "titre": d.get("titre"),
                                      "nom_fichier": d.get("nom_fichier"),
                                      "format": d.get("format")})
                self.tf.append(t)
                self.longueurs.append(sum(t.values()))
                df.update(t.keys())
        n = len(self.segments)
        self.moyenne = (sum(self.longueurs) / n) if n else 0.0
        self.idf = {m: math.log(1 + (n - f + 0.5) / (f + 0.5)) for m, f in df.items()}

    def scores(self, requete: list[str]) -> list[tuple[float, float, int]]:
        """(score BM25, couverture des termes de la requete, indice segment)."""
        uniques = list(dict.fromkeys(requete))
        if not uniques or not self.segments:
            return []
        out = []
        for i, tf in enumerate(self.tf):
            s, trouves = 0.0, 0
            norm = self.K1 * (1 - self.B + self.B * self.longueurs[i] / (self.moyenne or 1))
            for m in uniques:
                f = tf.get(m)
                if not f:
                    continue
                trouves += 1
                s += self.idf.get(m, 0.0) * f * (self.K1 + 1) / (f + norm)
            if s > 0:
                out.append((s, trouves / len(uniques), i))
        out.sort(key=lambda x: (-x[0], x[2]))
        return out


_INDEX: dict[str, tuple[tuple, _Index]] = {}


def _signature(sid: str) -> tuple:
    chemin = _dossier(sid) / "registre.json"
    try:
        st = chemin.stat()
        return (st.st_mtime_ns, st.st_size)
    except OSError:
        return (0, 0)


def _index(sid: str) -> _Index:
    sig = _signature(sid)
    cache = _INDEX.get(sid)
    if cache and cache[0] == sig:
        return cache[1]
    idx = _Index(sid)
    _INDEX[sid] = (sig, idx)
    return idx


def _extrait(texte: str, requete: set[str], longueur: int) -> str:
    """Fenetre de ``longueur`` caracteres, prise telle quelle dans le segment,
    qui contient le plus de termes de la requete. Coupee sur des mots."""
    if len(texte) <= longueur:
        return texte
    positions = [m.start() for m in re.finditer(r"\S+", texte)
                 if any(t in requete for t in termes(m.group()))]
    if not positions:
        debut = 0
    else:
        meilleur, debut = -1, positions[0]
        for p in positions:
            n = sum(1 for q in positions if p <= q < p + longueur - 40)
            if n > meilleur:
                meilleur, debut = n, p
        # Un peu de contexte avant le premier terme.
        debut = max(0, debut - 60)
    fin = min(len(texte), debut + longueur)
    debut = max(0, fin - longueur) if fin == len(texte) else debut
    if debut > 0:
        espace = texte.find(" ", debut)
        debut = espace + 1 if 0 <= espace < debut + 30 else debut
    if fin < len(texte):
        espace = texte.rfind(" ", debut, fin)
        fin = espace if espace > debut + longueur // 2 else fin
    return texte[debut:fin].strip()


def rechercher(sid: str, question: str, k: int = 5, longueur_extrait: int = 450) -> dict:
    """Recherche dans le corpus de l'etude ``sid`` SEULEMENT.

    Renvoie ``statut`` (``ok``, ``vide``, ``aucun_document``), les documents
    consultables et au plus ``k`` resultats : identifiant de segment,
    document, page ou section, extrait pris mot pour mot dans le texte
    indexe, score relatif (1 = meilleur) et couverture des termes.
    """
    k = max(1, min(int(k or 5), 20))
    longueur_extrait = max(120, min(int(longueur_extrait or 450), 1200))
    docs = lister(sid)
    consultables = [{"id": d["id"], "titre": d["titre"], "format": d["format"],
                     "n_pages": d["n_pages"]} for d in docs if d["statut"] == "indexe"]
    base = {"sid": sid, "question": question, "documents": consultables,
            "en_cours": sum(1 for d in docs if d["statut"] in ("en_attente", "extraction"))}
    if not consultables:
        return {**base, "statut": "aucun_document", "resultats": []}
    requete = termes(question)
    if not requete:
        return {**base, "statut": "vide", "resultats": [],
                "message": "Question sans mot significatif."}
    idx = _index(sid)
    classes = idx.scores(requete)
    # Bruit : une longue question qui ne partage qu'un mot avec le segment.
    if len(set(requete)) >= 4:
        classes = [c for c in classes if c[1] >= 0.25]
    if not classes:
        return {**base, "statut": "vide", "resultats": []}
    meilleur = classes[0][0]
    ensemble = set(requete)
    resultats, vus = [], set()
    for s, couverture, i in classes:
        seg = idx.segments[i]
        extrait = _extrait(seg["texte"], ensemble, longueur_extrait)
        if extrait in vus:
            continue
        vus.add(extrait)
        resultats.append({
            "segment": f"{seg['doc_id']}#{seg['i']}",
            "doc_id": seg["doc_id"],
            "titre": seg["titre"],
            "nom_fichier": seg["nom_fichier"],
            "page": seg.get("page"),
            "section": seg.get("section"),
            "score": round(s / meilleur, 3),
            "couverture": round(couverture, 3),
            "extrait": extrait,
        })
        if len(resultats) >= k:
            break
    return {**base, "statut": "ok", "resultats": resultats}
