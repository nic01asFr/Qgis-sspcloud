"""Extraction du texte des documents d'etude (lot L7, corpus documentaire).

Un document devient une liste d'**unites** : un morceau de texte et son
localisateur (page pour un PDF, section pour un DOCX, un ODT ou un Markdown,
feuille et lignes pour un tableau). Le decoupage en segments
(``decouper``) ne franchit jamais la limite d'une unite : une citation porte
donc toujours une page ou une section exacte.

Dependances
-----------
- PDF : ``pypdf`` (pur Python, declare dans ``hub/setup.py``). Repli sur
  ``pdfminer.six`` s'il est installe. Sans l'un ni l'autre, le PDF est refuse
  a l'indexation avec un message clair (statut ``erreur``).
- DOCX, ODT, XLSX : bibliotheque standard (``zipfile`` + ``xml.etree``).
  Pas de ``python-docx``, ``odfpy`` ni ``openpyxl`` : trois dependances de
  moins dans l'image, pour un besoin (le texte et les titres) que le XML
  couvre entierement.
- TXT, MD, CSV : bibliotheque standard.

Degradations assumees
---------------------
- PDF scanne (image seule) : aucun texte, statut ``erreur`` ; pas d'OCR.
- PDF chiffre avec mot de passe : refuse.
- Mise en forme, images, notes de bas de page DOCX, formules XLSX : ignorees
  (seule la valeur calculee enregistree dans le fichier est lue).

Securite
--------
Les archives (DOCX, ODT, XLSX) sont bornees : une entree XML de plus de
``_XML_MAX`` octets decompresses est refusee (bombe de decompression).
L'analyseur XML est celui de la bibliotheque standard, dont expat (>= 2.4)
protege des expansions d'entites en cascade.
"""
from __future__ import annotations

import csv
import io
import re
import zipfile
from dataclasses import dataclass, field
from pathlib import Path
from xml.etree import ElementTree as ET

# Decompresse, par entree XML d'une archive bureautique.
_XML_MAX = 40 * 1024 * 1024
# Lignes lues au plus dans un tableau (CSV, XLSX : toutes feuilles).
_LIGNES_TABLEAU_MAX = 5_000
# Lignes d'un tableau par unite (une unite = un bloc de lignes).
_LIGNES_PAR_UNITE = 25
# Pages lues au plus dans un PDF.
_PAGES_MAX = 2_000


class ErreurExtraction(Exception):
    """Le document ne peut pas etre lu ; le message est destine a l'utilisateur."""


@dataclass
class Unite:
    texte: str
    page: int | None = None
    section: str | None = None


@dataclass
class Extraction:
    unites: list[Unite]
    n_pages: int | None = None
    avertissements: list[str] = field(default_factory=list)

    @property
    def n_caracteres(self) -> int:
        return sum(len(u.texte) for u in self.unites)


# ── Normalisation ────────────────────────────────────────────────────────────

_RE_ESPACES = re.compile(r"[ \t   ]+")
_RE_LIGNES_VIDES = re.compile(r"\n\s*\n+")
_RE_CESURE = re.compile(r"(\w)-\n(\w)")


def nettoyer(texte: str) -> str:
    """Espaces simples, cesures de fin de ligne recollees, paragraphes gardes."""
    t = (texte or "").replace("\r\n", "\n").replace("\r", "\n").replace("\x00", "")
    t = _RE_CESURE.sub(r"\1\2", t)
    t = _RE_ESPACES.sub(" ", t)
    t = "\n".join(ligne.strip() for ligne in t.split("\n"))
    t = _RE_LIGNES_VIDES.sub("\n\n", t)
    return t.strip()


def decoder_texte(contenu: bytes) -> str:
    """UTF-8 (avec ou sans BOM), sinon Windows-1252 (fichiers Excel/Bloc-notes)."""
    try:
        return contenu.decode("utf-8-sig")
    except UnicodeDecodeError:
        return contenu.decode("cp1252", errors="replace")


# ── PDF ──────────────────────────────────────────────────────────────────────

# Titre numerote en debut de ligne (« 2.3 Zones humides », « III. Enjeux »).
_RE_TITRE_NUMEROTE = re.compile(
    r"^((?:\d+(?:\.\d+){0,3}\.?|[IVX]{1,5}\.)\s+[A-ZÀ-Ý][^\n]{2,80})$")


def _titre_de_page(texte: str) -> str | None:
    """Premier titre numerote de la page (heuristique, peut etre None)."""
    for ligne in texte.split("\n")[:60]:
        ligne = ligne.strip()
        if _RE_TITRE_NUMEROTE.match(ligne) and not ligne.rstrip().endswith((".", ",", ";")):
            return ligne[:90]
    return None


def _pages_pdf(chemin: Path) -> tuple[list[str], list[str]]:
    avert: list[str] = []
    try:
        import pypdf  # noqa: WPS433 (import tardif : dependance optionnelle)
    except ImportError:
        pypdf = None
    if pypdf is not None:
        try:
            lecteur = pypdf.PdfReader(str(chemin))
        except Exception as exc:
            raise ErreurExtraction(f"PDF illisible ({type(exc).__name__}).") from exc
        if lecteur.is_encrypted:
            try:
                ok = lecteur.decrypt("")
            except Exception:
                ok = 0
            if not ok:
                raise ErreurExtraction("PDF protégé par un mot de passe : "
                                       "déposez une version sans protection.")
        pages = []
        for i, page in enumerate(lecteur.pages):
            if i >= _PAGES_MAX:
                avert.append(f"Seules les {_PAGES_MAX} premières pages sont indexées.")
                break
            try:
                pages.append(page.extract_text() or "")
            except Exception:
                pages.append("")
                avert.append(f"Page {i + 1} illisible, ignorée.")
        return pages, avert
    try:
        from pdfminer.high_level import extract_pages  # noqa: WPS433
        from pdfminer.layout import LTTextContainer  # noqa: WPS433
    except ImportError as exc:
        raise ErreurExtraction("Lecture des PDF indisponible sur ce service "
                               "(bibliothèque pypdf absente).") from exc
    pages = []
    try:
        for i, mise_en_page in enumerate(extract_pages(str(chemin))):
            if i >= _PAGES_MAX:
                avert.append(f"Seules les {_PAGES_MAX} premières pages sont indexées.")
                break
            pages.append("".join(el.get_text() for el in mise_en_page
                                 if isinstance(el, LTTextContainer)))
    except Exception as exc:
        raise ErreurExtraction(f"PDF illisible ({type(exc).__name__}).") from exc
    return pages, avert


def extraire_pdf(chemin: Path) -> Extraction:
    pages, avert = _pages_pdf(chemin)
    unites = []
    for i, brut in enumerate(pages, start=1):
        texte = nettoyer(brut)
        if texte:
            unites.append(Unite(texte, page=i, section=_titre_de_page(texte)))
    if sum(len(u.texte) for u in unites) < 20:
        raise ErreurExtraction(
            "Aucun texte lisible dans ce PDF : c'est probablement un document "
            "numérisé (image). La reconnaissance de caractères (OCR) n'est pas "
            "prise en charge ; déposez une version texte.")
    vides = len(pages) - len(unites)
    if vides and vides >= len(pages) // 2:
        avert.append(f"{vides} page(s) sans texte (images ou pages numérisées).")
    return Extraction(unites, n_pages=len(pages), avertissements=avert)


# ── Archives bureautiques ────────────────────────────────────────────────────

def _lire_xml(archive: zipfile.ZipFile, nom: str) -> ET.Element | None:
    try:
        info = archive.getinfo(nom)
    except KeyError:
        return None
    if info.file_size > _XML_MAX:
        raise ErreurExtraction("Document trop volumineux une fois décompressé.")
    try:
        return ET.fromstring(archive.read(nom))
    except ET.ParseError as exc:
        raise ErreurExtraction("Contenu XML du document invalide.") from exc


def _ouvrir_archive(chemin: Path) -> zipfile.ZipFile:
    try:
        return zipfile.ZipFile(chemin)
    except (zipfile.BadZipFile, OSError) as exc:
        raise ErreurExtraction("Fichier bureautique illisible (archive invalide).") from exc


def _grouper_par_section(blocs: list[tuple[str, bool]]) -> list[Unite]:
    """(texte, est_titre) -> unites, une par section (le titre ouvre la section)."""
    unites: list[Unite] = []
    section: str | None = None
    tampon: list[str] = []

    def _vider() -> None:
        texte = nettoyer("\n\n".join(tampon))
        if texte:
            unites.append(Unite(texte, section=section))
        tampon.clear()

    for texte, est_titre in blocs:
        texte = texte.strip()
        if not texte:
            continue
        if est_titre:
            _vider()
            section = texte[:90]
            tampon.append(texte)
        else:
            tampon.append(texte)
    _vider()
    return unites


_W = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
_RE_STYLE_TITRE = re.compile(r"^(heading|titre|title|berschrift)\s*\d*$", re.I)


def _texte_paragraphe_docx(p: ET.Element) -> str:
    morceaux = []
    for el in p.iter():
        if el.tag == f"{_W}t" and el.text:
            morceaux.append(el.text)
        elif el.tag == f"{_W}tab":
            morceaux.append("\t")
        elif el.tag in (f"{_W}br", f"{_W}cr"):
            morceaux.append("\n")
    return "".join(morceaux)


def _est_titre_docx(p: ET.Element) -> bool:
    style = p.find(f"{_W}pPr/{_W}pStyle")
    if style is not None:
        val = (style.get(f"{_W}val") or "").replace("-", "").replace("_", "")
        if _RE_STYLE_TITRE.match(val):
            return True
    return p.find(f"{_W}pPr/{_W}outlineLvl") is not None


def extraire_docx(chemin: Path) -> Extraction:
    with _ouvrir_archive(chemin) as archive:
        racine = _lire_xml(archive, "word/document.xml")
    if racine is None:
        raise ErreurExtraction("DOCX invalide : document principal absent.")
    corps = racine.find(f"{_W}body")
    blocs: list[tuple[str, bool]] = []
    for el in (list(corps) if corps is not None else []):
        if el.tag == f"{_W}p":
            blocs.append((_texte_paragraphe_docx(el), _est_titre_docx(el)))
        elif el.tag == f"{_W}tbl":
            for ligne in el.iter(f"{_W}tr"):
                cellules = [" ".join(_texte_paragraphe_docx(p) for p in tc.iter(f"{_W}p")).strip()
                            for tc in ligne.iter(f"{_W}tc")]
                if any(cellules):
                    blocs.append((" | ".join(cellules), False))
    unites = _grouper_par_section(blocs)
    if not unites:
        raise ErreurExtraction("Aucun texte dans ce document.")
    return Extraction(unites)


_TXT = "{urn:oasis:names:tc:opendocument:xmlns:text:1.0}"
_TAB = "{urn:oasis:names:tc:opendocument:xmlns:table:1.0}"
_OFF = "{urn:oasis:names:tc:opendocument:xmlns:office:1.0}"


def _texte_odf(el: ET.Element) -> str:
    """Texte d'un element ODF, espaces (text:s), tabulations et retours compris."""
    morceaux = [el.text or ""]
    for enfant in el:
        if enfant.tag == f"{_TXT}s":
            morceaux.append(" " * int(enfant.get(f"{_TXT}c", "1") or 1))
        elif enfant.tag == f"{_TXT}tab":
            morceaux.append("\t")
        elif enfant.tag == f"{_TXT}line-break":
            morceaux.append("\n")
        elif enfant.tag == f"{_TXT}note":
            pass  # notes de bas de page : ignorees (degradation documentee)
        else:
            morceaux.append(_texte_odf(enfant))
        morceaux.append(enfant.tail or "")
    return "".join(morceaux)


def _blocs_odf(el: ET.Element, blocs: list[tuple[str, bool]]) -> None:
    for enfant in el:
        if enfant.tag == f"{_TXT}h":
            blocs.append((_texte_odf(enfant), True))
        elif enfant.tag == f"{_TXT}p":
            blocs.append((_texte_odf(enfant), False))
        elif enfant.tag == f"{_TAB}table":
            for ligne in enfant.iter(f"{_TAB}table-row"):
                cellules = [" ".join(_texte_odf(p) for p in c.iter(f"{_TXT}p")).strip()
                            for c in ligne.iter(f"{_TAB}table-cell")]
                if any(cellules):
                    blocs.append((" | ".join(c for c in cellules), False))
        else:
            _blocs_odf(enfant, blocs)


def extraire_odt(chemin: Path) -> Extraction:
    with _ouvrir_archive(chemin) as archive:
        racine = _lire_xml(archive, "content.xml")
    if racine is None:
        raise ErreurExtraction("ODT invalide : contenu absent.")
    corps = racine.find(f"{_OFF}body/{_OFF}text")
    blocs: list[tuple[str, bool]] = []
    if corps is not None:
        _blocs_odf(corps, blocs)
    unites = _grouper_par_section(blocs)
    if not unites:
        raise ErreurExtraction("Aucun texte dans ce document.")
    return Extraction(unites)


# ── Texte et Markdown ────────────────────────────────────────────────────────

_RE_TITRE_MD = re.compile(r"^\s{0,3}#{1,6}\s+(.+?)\s*#*\s*$")


def extraire_texte(chemin: Path, markdown: bool) -> Extraction:
    texte = decoder_texte(chemin.read_bytes())
    if markdown:
        blocs = []
        for ligne in texte.splitlines():
            m = _RE_TITRE_MD.match(ligne)
            blocs.append((m.group(1), True) if m else (ligne, False))
        # Les lignes d'un meme paragraphe restent ensemble : on recompose.
        unites: list[Unite] = []
        section: str | None = None
        tampon: list[str] = []
        for contenu, est_titre in blocs:
            if est_titre:
                t = nettoyer("\n".join(tampon))
                if t:
                    unites.append(Unite(t, section=section))
                tampon = [contenu]
                section = contenu[:90]
            else:
                tampon.append(contenu)
        t = nettoyer("\n".join(tampon))
        if t:
            unites.append(Unite(t, section=section))
    else:
        t = nettoyer(texte)
        unites = [Unite(t)] if t else []
    if not unites:
        raise ErreurExtraction("Le fichier est vide.")
    return Extraction(unites)


# ── Tableaux (CSV, XLSX) ─────────────────────────────────────────────────────

def _unites_tableau(lignes: list[list[str]], nom: str, feuille: str | None,
                    avert: list[str]) -> list[Unite]:
    """Un tableau decrit : un resume (colonnes, nombre de lignes), puis des blocs
    de lignes « colonne : valeur ; ... », en-tete repetee a chaque bloc."""
    lignes = [[(c or "").strip() for c in ligne] for ligne in lignes]
    lignes = [ligne for ligne in lignes if any(ligne)]
    if not lignes:
        return []
    entete = [c or f"colonne {i + 1}" for i, c in enumerate(lignes[0])]
    corps = lignes[1:]
    prefixe = f"Feuille « {feuille} »" if feuille else f"Tableau « {nom} »"
    resume = (f"{prefixe} : {len(corps)} ligne(s) de données, "
              f"{len(entete)} colonne(s) : {', '.join(entete)}.")
    unites = [Unite(resume, section=feuille or "Description")]
    for debut in range(0, len(corps), _LIGNES_PAR_UNITE):
        bloc = corps[debut:debut + _LIGNES_PAR_UNITE]
        rendu = []
        for ligne in bloc:
            paires = [f"{entete[i] if i < len(entete) else f'colonne {i + 1}'} : {v}"
                      for i, v in enumerate(ligne) if v]
            rendu.append(" ; ".join(paires))
        # Numeros de ligne du fichier (en-tete = ligne 1).
        etiquette = f"lignes {debut + 2} à {debut + 1 + len(bloc)}"
        section = f"{feuille}, {etiquette}" if feuille else etiquette
        unites.append(Unite(nettoyer("\n".join(rendu)), section=section))
    return unites


def extraire_csv(chemin: Path) -> Extraction:
    texte = decoder_texte(chemin.read_bytes())
    echantillon = texte[:8192]
    try:
        dialecte = csv.Sniffer().sniff(echantillon, delimiters=";,\t|")
    except csv.Error:
        separateur = ";" if echantillon.count(";") > echantillon.count(",") else ","

        class dialecte(csv.excel):  # noqa: N801 (ne pas modifier csv.excel)
            delimiter = separateur
    avert: list[str] = []
    lignes = []
    for i, ligne in enumerate(csv.reader(io.StringIO(texte), dialecte)):
        if i > _LIGNES_TABLEAU_MAX:
            avert.append(f"Seules les {_LIGNES_TABLEAU_MAX} premières lignes sont indexées.")
            break
        lignes.append(ligne)
    unites = _unites_tableau(lignes, chemin.stem, None, avert)
    if not unites:
        raise ErreurExtraction("Le tableau est vide.")
    return Extraction(unites, avertissements=avert)


_X = "{http://schemas.openxmlformats.org/spreadsheetml/2006/main}"
_R_ID = "{http://schemas.openxmlformats.org/officeDocument/2006/relationships}id"
_REL = "{http://schemas.openxmlformats.org/package/2006/relationships}"
_RE_COLONNE = re.compile(r"^([A-Z]+)")


def _indice_colonne(ref: str) -> int:
    m = _RE_COLONNE.match(ref or "")
    if not m:
        return -1
    n = 0
    for c in m.group(1):
        n = n * 26 + (ord(c) - 64)
    return n - 1


def extraire_xlsx(chemin: Path) -> Extraction:
    avert: list[str] = []
    unites: list[Unite] = []
    with _ouvrir_archive(chemin) as archive:
        classeur = _lire_xml(archive, "xl/workbook.xml")
        if classeur is None:
            raise ErreurExtraction("XLSX invalide : classeur absent.")
        liens = _lire_xml(archive, "xl/_rels/workbook.xml.rels")
        cibles = {}
        if liens is not None:
            for rel in liens.iter(f"{_REL}Relationship"):
                cible = rel.get("Target", "")
                cible = cible.lstrip("/")
                if not cible.startswith("xl/"):
                    cible = "xl/" + cible
                cibles[rel.get("Id")] = cible
        partagees: list[str] = []
        sst = _lire_xml(archive, "xl/sharedStrings.xml")
        if sst is not None:
            for si in sst.iter(f"{_X}si"):
                partagees.append("".join(t.text or "" for t in si.iter(f"{_X}t")))
        total = 0
        for feuille in classeur.iter(f"{_X}sheet"):
            nom = feuille.get("name") or "Feuille"
            cible = cibles.get(feuille.get(_R_ID))
            racine = _lire_xml(archive, cible) if cible else None
            if racine is None:
                continue
            lignes: list[list[str]] = []
            for row in racine.iter(f"{_X}row"):
                if total >= _LIGNES_TABLEAU_MAX:
                    avert.append(f"Seules les {_LIGNES_TABLEAU_MAX} premières lignes "
                                 "sont indexées.")
                    break
                valeurs: dict[int, str] = {}
                for c in row.iter(f"{_X}c"):
                    t = c.get("t")
                    if t == "inlineStr":
                        v = "".join(x.text or "" for x in c.iter(f"{_X}t"))
                    else:
                        el = c.find(f"{_X}v")
                        v = el.text if el is not None and el.text else ""
                        if t == "s" and v.isdigit() and int(v) < len(partagees):
                            v = partagees[int(v)]
                    col = _indice_colonne(c.get("r", ""))
                    valeurs[col if col >= 0 else len(valeurs)] = v
                if valeurs:
                    largeur = max(valeurs) + 1
                    lignes.append([valeurs.get(i, "") for i in range(largeur)])
                    total += 1
            unites.extend(_unites_tableau(lignes, chemin.stem, nom, avert))
    if not unites:
        raise ErreurExtraction("Le classeur est vide.")
    return Extraction(unites, avertissements=sorted(set(avert)))


# ── Point d'entree ───────────────────────────────────────────────────────────

def extraire(chemin: Path, format_doc: str) -> Extraction:
    """Extrait les unites d'un document selon son format (cf. FORMATS)."""
    if format_doc == "pdf":
        return extraire_pdf(chemin)
    if format_doc == "docx":
        return extraire_docx(chemin)
    if format_doc == "odt":
        return extraire_odt(chemin)
    if format_doc in ("texte", "markdown"):
        return extraire_texte(chemin, markdown=format_doc == "markdown")
    if format_doc == "csv":
        return extraire_csv(chemin)
    if format_doc == "xlsx":
        return extraire_xlsx(chemin)
    raise ErreurExtraction(f"Format non pris en charge : {format_doc}.")


# ── Decoupage en segments ────────────────────────────────────────────────────

_RE_PHRASES = re.compile(r"(?<=[.!?;:])\s+")


def _morceaux(texte: str, taille: int) -> list[str]:
    """Paragraphes, puis phrases, puis mots : jamais plus long que ``taille``."""
    out: list[str] = []
    for para in texte.split("\n\n"):
        if len(para) <= taille:
            out.append(para)
            continue
        for phrase in _RE_PHRASES.split(para):
            while len(phrase) > taille:
                coupe = phrase.rfind(" ", 0, taille)
                coupe = coupe if coupe > taille // 2 else taille
                out.append(phrase[:coupe])
                phrase = phrase[coupe:].lstrip()
            if phrase:
                out.append(phrase)
    return [m for m in out if m.strip()]


def decouper(unites: list[Unite], taille: int = 1000,
             recouvrement: int = 150) -> list[dict]:
    """Segments d'au plus ``taille`` caracteres, sans franchir une unite.

    Chaque segment reprend la fin du precedent (``recouvrement`` caracteres
    environ, coupes sur un mot) pour qu'une phrase a cheval reste trouvable.
    """
    segments: list[dict] = []
    for u in unites:
        courant = ""
        for m in _morceaux(u.texte, taille):
            sep = "\n\n" if courant else ""
            if len(courant) + len(sep) + len(m) <= taille:
                courant += sep + m
                continue
            if courant:
                segments.append({"texte": courant, "page": u.page, "section": u.section})
                queue = courant[-recouvrement:]
                espace = queue.find(" ")
                queue = queue[espace + 1:] if 0 <= espace < len(queue) - 1 else ""
                courant = (queue + " " + m) if queue and len(queue) + 1 + len(m) <= taille else m
            else:
                courant = m
        if courant.strip():
            segments.append({"texte": courant, "page": u.page, "section": u.section})
    for i, s in enumerate(segments):
        s["i"] = i
    return segments
