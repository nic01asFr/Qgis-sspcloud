# -*- coding: utf-8 -*-
"""Remplace les classes de l'ancienne charte d'Etat par celles du produit.

Usage : python hub/tools/remplace_charte.py <racine_du_depot> [--applique]

Sans `--applique`, le script se contente de rapporter ce qu'il changerait, et
surtout ce qu'il ne SAIT PAS changer -- c'est cette seconde liste qui compte :
une classe oubliee ne provoque aucune erreur, elle laisse juste un element
sans style, ce qui se voit mal sur une capture et pas du tout dans un test.

Les frontieres sont strictes : `(?<![\\w-])` empeche de toucher `dsfr-sobre` ou
`user-nic01asfr-qgis`, ou la sous-chaine `fr-` n'est pas un debut de classe.
Les correspondances sont appliquees de la plus longue a la plus courte pour
que `fr-btn--tertiary-no-outline` soit traite avant `fr-btn--tertiary`, puis
`fr-btn`.
"""

from __future__ import annotations

import io
import re
import sys
from pathlib import Path

# ── Correspondances hors icones ──────────────────────────────────────────
# Les icones sont lues depuis _correspondance_icones.txt, produit par
# genere_icones.py : une seule source pour les deux scripts.

CLASSES = {
    # Boutons
    "fr-btn--tertiary-no-outline": "qs-btn--nu",
    "fr-btn--tertiary":            "qs-btn--tertiaire",
    "fr-btn--secondary":           "qs-btn--secondaire",
    "fr-btn--icon-left":           "qs-btn--icone-gauche",
    "fr-btn--sm":                  "qs-btn--sm",
    "fr-btn--lg":                  "qs-btn--lg",
    "fr-btn":                      "qs-btn",

    # Pastilles (ex-badges)
    "fr-badge--no-icon": "",              # l'icone n'est plus implicite
    "fr-badge--success": "qs-pastille--succes",
    "fr-badge--info":    "qs-pastille--info",
    "fr-badge--new":     "qs-pastille--nouveau",
    "fr-badge--sm":      "qs-pastille--sm",
    "fr-badge":          "qs-pastille",

    # Mise en page
    "fr-container--fluid": "qs-conteneur--large",
    "fr-container":        "qs-conteneur",
    "fr-text--lead":       "qs-texte--lead",
    "fr-text--sm":         "qs-texte--sm",
    "fr-link":             "qs-lien",
    "fr-enlarge-link":     "qs-lien-etendu",
    "fr-input":            "qs-champ",
    "fr-table":            "qs-tableau",
    "fr-collapse":         "qs-repli",

    # Encarts, alertes, avis
    "fr-callout--brown-caramel": "qs-encart--attention",
    "fr-callout__text":          "qs-encart__texte",
    "fr-callout":                "qs-encart",
    "fr-alert--warning":         "qs-alerte--attention",
    "fr-alert__title":           "qs-alerte__titre",
    "fr-alert":                  "qs-alerte",
    "fr-notice--info":           "qs-avis--info",
    "fr-notice__title":          "qs-avis__titre",
    "fr-notice__body":           "qs-avis__corps",
    "fr-notice":                 "qs-avis",

    # Accordeon
    "fr-accordion__title": "qs-accordeon__titre",
    "fr-accordion__btn":   "qs-accordeon__btn",
    "fr-accordion":        "qs-accordeon",

    # En-tete. `__operator` portait la marque de l'exploitant : elle a ete
    # retiree du produit, la classe disparait avec elle.
    "fr-header__service-tagline": "qs-entete__service-accroche",
    "fr-header__service-title":   "qs-entete__service-titre",
    "fr-header__service":         "qs-entete__service",
    "fr-header__brand-top":       "qs-entete__marque-haut",
    "fr-header__brand":           "qs-entete__marque",
    "fr-header__body-row":        "qs-entete__ligne",
    "fr-header__body":            "qs-entete__corps",
    "fr-header__operator":        "qs-entete__marque-haut",
    "fr-header":                  "qs-entete",

    # Pied de page
    "fr-footer__content-list": "qs-pied__liste",
    "fr-footer__content-item": "qs-pied__item",
    "fr-footer__content-link": "qs-pied__lien",
    "fr-footer__content-desc": "qs-pied__desc",
    "fr-footer__content":      "qs-pied__contenu",
    "fr-footer__bottom-list":  "qs-pied__bas-liste",
    "fr-footer__bottom-item":  "qs-pied__bas-item",
    "fr-footer__bottom-link":  "qs-pied__bas-lien",
    "fr-footer__bottom":       "qs-pied__bas",
    "fr-footer__body":         "qs-pied__corps",
    "fr-footer":               "qs-pied",

    # Espacements : meme echelle, 1w = 8px
    "fr-mb-1w": "qs-mb-1w",
    "fr-mb-3w": "qs-mb-3w",
    "fr-mb-4w": "qs-mb-4w",
    "fr-mt-2w": "qs-mt-2w",
    "fr-mt-5w": "qs-mt-5w",
    "fr-py-4w": "qs-py-4w",
    "fr-py-6w": "qs-py-6w",

    # Restes de l'identite d'Etat : le bloc-marque n'a plus d'objet.
    "fr-logo": "",

    # Divers propres a une seule surface
    "fr-card__title":    "qs-carte__titre",
    "fr-content-media":  "qs-media",

    # Modificateurs d'icone
    "fr-icon--sm": "ic--sm",
    "fr-icon--md": "ic--md",
}

SURFACES = [
    "hub/templates/desk.html",
    "hub/templates/workspace.html",
    "agent/templates/chat.html",
    "hub/hub/main.py",
    "hub/hub/storymap_dsfr.py",
]


def charge_icones(racine: Path) -> dict[str, str]:
    chemin = racine / "hub" / "hub" / "static" / "_correspondance_icones.txt"
    table = {}
    for ligne in chemin.read_text(encoding="utf-8").splitlines():
        if not ligne.strip():
            continue
        ancien, nouveau = ligne.split("\t")
        table["fr-icon-" + ancien] = "ic-" + nouveau
    return table


def main() -> int:
    racine = Path(sys.argv[1])
    applique = "--applique" in sys.argv

    table = dict(CLASSES)
    table.update(charge_icones(racine))
    # Du plus long au plus court : sinon `fr-btn` mangerait le prefixe de
    # `fr-btn--sm` et laisserait un `--sm` orphelin.
    ordre = sorted(table, key=len, reverse=True)

    total = 0
    for relatif in SURFACES:
        chemin = racine / relatif
        if not chemin.exists():
            print("ABSENT %s" % relatif)
            continue
        texte = chemin.read_text(encoding="utf-8")
        origine = texte
        compte = {}
        for ancien in ordre:
            motif = re.compile(r"(?<![\w-])" + re.escape(ancien) + r"(?![\w-])")
            texte, n = motif.subn(table[ancien], texte)
            if n:
                compte[ancien] = n

        # Ce qui subsiste n'a pas de correspondance : c'est la liste a lire.
        restes = sorted(set(
            m for m in re.findall(r"(?<![\w-])fr-[a-zA-Z0-9_-]+", texte)))

        change = sum(compte.values())
        total += change
        print("== %s" % relatif)
        print("   %d remplacements sur %d classes" % (change, len(compte)))
        if restes:
            print("   NON TRAITE : %s" % ", ".join(restes))

        if applique and texte != origine:
            # Les doubles espaces nes des suppressions (`fr-badge--no-icon`)
            # rendraient les attributs class illisibles.
            texte = re.sub(r'class="([^"]*)"',
                           lambda m: 'class="%s"' % " ".join(m.group(1).split()),
                           texte)
            with io.open(chemin, "w", encoding="utf-8", newline="\n") as f:
                f.write(texte)

    print("\n%d remplacements au total%s"
          % (total, "" if applique else " (simulation, rien ecrit)"))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
