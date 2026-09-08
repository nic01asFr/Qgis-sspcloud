# -*- coding: utf-8 -*-
"""Seconde passe : en-tetes de documents et couleurs en dur.

`remplace_charte.py` traite les classes. Restent les choses qui ne sont pas
des classes et qui, seules, suffiraient a ramener la charte d'Etat : le CSS
charge depuis le CDN, l'attribut de theme, la couleur de barre de navigateur,
la police Marianne, et les couleurs ecrites en dur dans les styles.

Usage : python hub/tools/finit_charte.py <racine> [--applique]
"""

from __future__ import annotations

import io
import re
import sys
from pathlib import Path

# Couleurs de l'ancienne charte -> jetons du produit. Ne couvre QUE les
# surfaces d'interface : les palettes de classification cartographique
# (carto_classification.py) sont un choix de rendu des donnees, pas une
# marque, et ne sont pas touchees ici.
COULEURS = {
    "#000091": "var(--qs-accent)",
    "#e3eaf5": "var(--qs-accent-pale)",
    "#e3e3fd": "var(--qs-accent-pale)",
    "#eef3f9": "var(--qs-accent-pale)",
    "#f0f5fc": "var(--qs-accent-pale)",
    "#c8d4e6": "var(--qs-trait)",
    "#161616": "var(--qs-encre)",
    "#d64d00": "var(--qs-alerte)",
    "#ffe7d6": "var(--qs-alerte-pale)",
    "#18753c": "var(--qs-succes)",
    "#1f8d4d": "var(--qs-succes)",
    "#1f6f3f": "var(--qs-succes)",
}

# Les JETONS de l'ancienne charte, et non ses classes.
#
# C'est la part qu'un inventaire des classes ne voit pas : `background:
# var(--background-action-high-blue-france)` ne contient ni `fr-` ni `#000091`.
# Le CSS du CDN definissait ces variables ; sans lui elles sont indefinies, la
# propriete devient invalide, et l'element perd sa couleur sans que rien ne le
# signale. Le bouton d'envoi du chat etait ainsi devenu transparent.
VARIABLES = {
    "--text-mention-grey":                       "--qs-discret",
    "--text-default-grey":                       "--qs-encre-2",
    "--text-title-grey":                         "--qs-encre",
    "--text-title-blue-france":                  "--qs-accent",
    "--text-action-high-blue-france-hover":      "--qs-accent-fonce",
    "--text-action-high-blue-france":            "--qs-accent",
    "--border-action-high-blue-france":          "--qs-accent",
    "--border-action-low-blue-france":           "--qs-trait",
    "--border-default-grey":                     "--qs-trait",
    "--background-action-high-blue-france-hover": "--qs-accent-fonce",
    "--background-action-high-blue-france":      "--qs-accent",
    "--background-alt-blue-france":              "--qs-accent-pale",
    "--background-alt-grey":                     "--qs-surface-2",
    "--warning-425":                             "--qs-alerte",
}

FEUILLE = '<link rel="stylesheet" href="/static/produit.css">'

# Gabarits servis par un service qui expose /static/produit.css : les
# variables CSS y resolvent. Les pages autonomes de hub/hub/main.py et les
# livrables de storymap_dsfr.py sont traites a part, en valeurs litterales.
SURFACES = [
    "hub/templates/desk.html",
    "hub/templates/workspace.html",
    "hub/hub/main.py",
    "agent/templates/chat.html",
    "hub/hub/storymap_dsfr.py",
]


def traite(texte: str) -> tuple[str, list[str]]:
    faits = []

    # 1. Le CSS de la charte, charge depuis un CDN tiers.
    #
    # La feuille du produit prend la place du PREMIER lien de chaque page :
    # une page en charge deux (le socle et les utilitaires), et un meme
    # fichier Python contient plusieurs pages. Inserer apres <title> n'en
    # aurait servi qu'une.
    motif_lien = re.compile(
        r'[ \t]*<link[^>]*gouvfr/dsfr[^>]*>\n?', re.I)
    liens = motif_lien.findall(texte)
    if liens:
        def _remplace(m):
            return (FEUILLE + "\n") if "dsfr.min.css" in m.group(0) else ""
        texte = motif_lien.sub(_remplace, texte)
        faits.append("%d feuille(s) CDN retiree(s), produit mis a la place"
                     % len(liens))

    # 2. Son script, quand il est present.
    motif_script = re.compile(
        r'[ \t]*<script[^>]*gouvfr/dsfr[^>]*>\s*</script>\n?', re.I)
    n = len(motif_script.findall(texte))
    if n:
        texte = motif_script.sub("", texte)
        faits.append("%d script(s) CDN retire(s)" % n)

    # 3. Rien de plus a inserer : la feuille a pris la place du lien CDN.
    # Une page qui n'en chargeait pas portait deja son style en propre, on
    # ne lui ajoute pas une dependance qu'elle n'avait pas.

    # 4. L'attribut de theme de la charte n'a plus de lecteur.
    n = texte.count(' data-fr-scheme="light"')
    if n:
        texte = texte.replace(' data-fr-scheme="light"', "")
        faits.append("%d attribut(s) de theme retire(s)" % n)

    # 5. La couleur de barre de navigateur.
    n = texte.count('name="theme-color" content="#000091"')
    if n:
        texte = texte.replace('name="theme-color" content="#000091"',
                              'name="theme-color" content="#0F6E8C"')
        faits.append("couleur de barre changee")

    # 6. Marianne est une police d'Etat, sous licence propre.
    motif_police = re.compile(
        r"font-family:\s*Marianne\s*,[^;}\"']*", re.I)
    n = len(motif_police.findall(texte))
    if n:
        texte = motif_police.sub("font-family:var(--qs-police)", texte)
        faits.append("%d declaration(s) de police remplacee(s)" % n)

    # 7. Les couleurs en dur.
    total = 0
    for ancienne, jeton in COULEURS.items():
        for forme in (ancienne, ancienne.upper()):
            n = texte.count(forme)
            if n:
                texte = texte.replace(forme, jeton)
                total += n
    if total:
        faits.append("%d couleur(s) remplacee(s)" % total)

    # 8. Les jetons de l'ancienne charte. Du plus long au plus court, sinon
    # `--text-action-high-blue-france` avalerait le prefixe de sa variante
    # `-hover` et laisserait un suffixe orphelin.
    total = 0
    for ancien in sorted(VARIABLES, key=len, reverse=True):
        motif = "var(%s)" % ancien
        n = texte.count(motif)
        if n:
            texte = texte.replace(motif, "var(%s)" % VARIABLES[ancien])
            total += n
    if total:
        faits.append("%d jeton(s) de l'ancienne charte remplace(s)" % total)

    return texte, faits


def main() -> int:
    racine = Path(sys.argv[1])
    applique = "--applique" in sys.argv

    for relatif in SURFACES:
        chemin = racine / relatif
        if not chemin.exists():
            print("ABSENT %s" % relatif)
            continue
        origine = chemin.read_text(encoding="utf-8")
        texte, faits = traite(origine)
        print("== %s" % relatif)
        for f in faits:
            print("   %s" % f)
        if not faits:
            print("   rien a faire")
        if applique and texte != origine:
            with io.open(chemin, "w", encoding="utf-8", newline="\n") as f:
                f.write(texte)

    if not applique:
        print("\n(simulation, rien ecrit)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
