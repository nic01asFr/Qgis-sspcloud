"""Petits documents generes pour les tests du corpus documentaire.

Aucune dependance : PDF ecrit a la main (police standard Helvetica,
WinAnsiEncoding), DOCX, ODT et XLSX assembles avec ``zipfile``.
"""
from __future__ import annotations

import io
import zipfile
from xml.sax.saxutils import escape


def _echapper_pdf(texte: str) -> bytes:
    t = texte.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")
    return t.encode("cp1252")


def pdf(pages: list[str]) -> bytes:
    """PDF texte d'une page par element (lignes separees par « \\n »)."""
    objets: dict[int, bytes] = {}
    n = len(pages)
    kids = " ".join(f"{4 + 2 * i} 0 R" for i in range(n))
    objets[1] = b"<< /Type /Catalog /Pages 2 0 R >>"
    objets[2] = f"<< /Type /Pages /Kids [{kids}] /Count {n} >>".encode()
    objets[3] = (b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica "
                 b"/Encoding /WinAnsiEncoding >>")
    for i, texte in enumerate(pages):
        lignes = texte.split("\n") if texte else []
        flux = b"BT /F1 11 Tf 50 780 Td 14 TL " + b" ".join(
            b"(" + _echapper_pdf(ligne) + b") Tj T*" for ligne in lignes) + b" ET"
        objets[4 + 2 * i] = (
            f"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 595 842] "
            f"/Resources << /Font << /F1 3 0 R >> >> /Contents {5 + 2 * i} 0 R >>"
        ).encode()
        objets[5 + 2 * i] = (b"<< /Length %d >>\nstream\n" % len(flux)) + flux + b"\nendstream"
    sortie = b"%PDF-1.4\n"
    positions = {}
    for num in sorted(objets):
        positions[num] = len(sortie)
        sortie += b"%d 0 obj\n" % num + objets[num] + b"\nendobj\n"
    xref = len(sortie)
    total = max(objets) + 1
    sortie += b"xref\n0 %d\n0000000000 65535 f \n" % total
    for num in range(1, total):
        sortie += b"%010d 00000 n \n" % positions[num]
    sortie += b"trailer\n<< /Size %d /Root 1 0 R >>\nstartxref\n%d\n%%%%EOF\n" % (total, xref)
    return sortie


def _zip(membres: dict[str, str]) -> bytes:
    tampon = io.BytesIO()
    with zipfile.ZipFile(tampon, "w", zipfile.ZIP_DEFLATED) as z:
        for nom, contenu in membres.items():
            z.writestr(nom, contenu)
    return tampon.getvalue()


_W = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"


def docx(blocs: list[tuple[str, str]]) -> bytes:
    """blocs : (« titre » | « texte », contenu)."""
    corps = []
    for genre, texte in blocs:
        style = '<w:pPr><w:pStyle w:val="Heading1"/></w:pPr>' if genre == "titre" else ""
        corps.append(f"<w:p>{style}<w:r><w:t xml:space=\"preserve\">{escape(texte)}"
                     "</w:t></w:r></w:p>")
    document = (f'<?xml version="1.0" encoding="UTF-8"?><w:document xmlns:w="{_W}">'
                f"<w:body>{''.join(corps)}</w:body></w:document>")
    return _zip({
        "[Content_Types].xml": '<?xml version="1.0"?><Types xmlns="http://schemas.'
                               'openxmlformats.org/package/2006/content-types"/>',
        "word/document.xml": document,
    })


def odt(blocs: list[tuple[str, str]]) -> bytes:
    corps = []
    for genre, texte in blocs:
        balise = "text:h" if genre == "titre" else "text:p"
        corps.append(f"<{balise}>{escape(texte)}</{balise}>")
    contenu = (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<office:document-content '
        'xmlns:office="urn:oasis:names:tc:opendocument:xmlns:office:1.0" '
        'xmlns:text="urn:oasis:names:tc:opendocument:xmlns:text:1.0">'
        f"<office:body><office:text>{''.join(corps)}</office:text></office:body>"
        "</office:document-content>")
    return _zip({"mimetype": "application/vnd.oasis.opendocument.text",
                 "content.xml": contenu})


_X = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"
_R = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"


def xlsx(feuille: str, lignes: list[list[str]]) -> bytes:
    partagees: list[str] = []
    rangs = []
    for r, ligne in enumerate(lignes, start=1):
        cellules = []
        for c, valeur in enumerate(ligne):
            ref = f"{chr(65 + c)}{r}"
            if isinstance(valeur, (int, float)):
                cellules.append(f'<c r="{ref}"><v>{valeur}</v></c>')
            else:
                partagees.append(valeur)
                cellules.append(f'<c r="{ref}" t="s"><v>{len(partagees) - 1}</v></c>')
        rangs.append(f'<row r="{r}">{"".join(cellules)}</row>')
    return _zip({
        "xl/workbook.xml": (f'<workbook xmlns="{_X}" xmlns:r="{_R}"><sheets>'
                            f'<sheet name="{escape(feuille)}" sheetId="1" r:id="rId1"/>'
                            "</sheets></workbook>"),
        "xl/_rels/workbook.xml.rels": (
            '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/'
            'relationships"><Relationship Id="rId1" Type="worksheet" '
            'Target="worksheets/sheet1.xml"/></Relationships>'),
        "xl/sharedStrings.xml": (f'<sst xmlns="{_X}">'
                                 + "".join(f"<si><t>{escape(s)}</t></si>" for s in partagees)
                                 + "</sst>"),
        "xl/worksheets/sheet1.xml": (f'<worksheet xmlns="{_X}"><sheetData>'
                                     f"{''.join(rangs)}</sheetData></worksheet>"),
    })
