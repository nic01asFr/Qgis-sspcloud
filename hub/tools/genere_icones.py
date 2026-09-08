# -*- coding: utf-8 -*-
"""Genere la partie icones de produit.css.

Les icones sont posees en masque CSS, comme le faisait la charte precedente :
une classe pose la forme, `background-color: currentColor` lui donne la
couleur du texte environnant. La substitution dans les gabarits reste donc
une classe pour une classe.

Trait de 1.6 sur une grille de 24, extremites arrondies : lisible a 16 px
comme a 24 px.
"""
import io
import sys

RACINE = sys.argv[1]

# nom produit -> contenu du <svg> (sans la balise englobante)
ICONES = {
 "ajout-cercle": '<circle cx="12" cy="12" r="8.5"/><path d="M12 8.5v7M8.5 12h7"/>',
 "ajout": '<path d="M12 5v14M5 12h14"/>',
 "archive": '<rect x="3.5" y="7.5" width="17" height="12" rx="1.5"/><rect x="2.5" y="4" width="19" height="3.5" rx="1"/><path d="M10 11.5h4"/>',
 "article": '<rect x="4.5" y="3.5" width="15" height="17" rx="1.5"/><path d="M8 8h8M8 12h8M8 16h5"/>',
 "livre": '<path d="M5.5 4a1.5 1.5 0 00-1.5 1.5v13A2.5 2.5 0 016.5 16h13V4z"/><path d="M6.5 16h13v4h-13A2.5 2.5 0 014 17.5"/><path d="M8.5 8h7"/>',
 "signet": '<path d="M6.5 3.5h11v17l-5.5-4-5.5 4z"/>',
 "batiment": '<path d="M4.5 20.5V5.5l8-2.5v17.5"/><path d="M12.5 20.5V9.5l7 2v9"/><path d="M2.5 20.5h19"/><path d="M7.5 8.5v.01M7.5 12.5v.01M7.5 16.5v.01M16 14v.01M16 17.5v.01"/>',
 "discussion": '<path d="M20.5 12c0 4.1-3.8 7.4-8.5 7.4-1 0-2-.15-2.9-.42L4 20.5l1.6-3.6A7 7 0 013.5 12C3.5 7.9 7.3 4.6 12 4.6s8.5 3.3 8.5 7.4z"/>',
 "coche": '<path d="M4.5 12.5l5 5 10-11"/>',
 "coche-cercle": '<circle cx="12" cy="12" r="8.5"/><path d="M8 12.2l2.8 2.8L16 9.5"/>',
 "presse-papier": '<path d="M9 4.5H7a1.5 1.5 0 00-1.5 1.5v13A1.5 1.5 0 007 20.5h10a1.5 1.5 0 001.5-1.5V6A1.5 1.5 0 0017 4.5h-2"/><rect x="9" y="2.5" width="6" height="4" rx="1"/>',
 "fermer": '<path d="M6 6l12 12M18 6L6 18"/>',
 "tableau-bord": '<rect x="3.5" y="3.5" width="7" height="7" rx="1"/><rect x="13.5" y="3.5" width="7" height="7" rx="1"/><rect x="3.5" y="13.5" width="7" height="7" rx="1"/><rect x="13.5" y="13.5" width="7" height="7" rx="1"/>',
 "base-donnees": '<ellipse cx="12" cy="6" rx="7.5" ry="3"/><path d="M4.5 6v12c0 1.7 3.4 3 7.5 3s7.5-1.3 7.5-3V6"/><path d="M4.5 12c0 1.7 3.4 3 7.5 3s7.5-1.3 7.5-3"/>',
 "supprimer": '<path d="M4.5 6.5h15M9.5 6.5V4h5v2.5"/><path d="M6.5 6.5l1 13a1.5 1.5 0 001.5 1.4h6a1.5 1.5 0 001.5-1.4l1-13"/><path d="M10.5 10v7M13.5 10v7"/>',
 "telecharger": '<path d="M12 3.5v11M7.5 10.5l4.5 4.5 4.5-4.5"/><path d="M4.5 18.5v2h15v-2"/>',
 "brouillon": '<path d="M13.5 3.5H6.5A1.5 1.5 0 005 5v14a1.5 1.5 0 001.5 1.5h11A1.5 1.5 0 0019 19V9z"/><path d="M13.5 3.5V9H19"/>',
 "globe": '<circle cx="12" cy="12" r="8.5"/><path d="M3.5 12h17"/><path d="M12 3.5c2.2 2.4 3.4 5.4 3.4 8.5S14.2 18.1 12 20.5c-2.2-2.4-3.4-5.4-3.4-8.5S9.8 5.9 12 3.5z"/>',
 "editer-bloc": '<path d="M19.5 12.5V19a1.5 1.5 0 01-1.5 1.5H6A1.5 1.5 0 014.5 19V7A1.5 1.5 0 016 5.5h6.5"/><path d="M15.5 4.5l4 4-6 6h-4v-4z"/>',
 "editer": '<path d="M4.5 19.5h4l11-11-4-4-11 11z"/><path d="M13.5 5.5l4 4"/>',
 "lien-externe": '<path d="M18.5 13.5V19a1.5 1.5 0 01-1.5 1.5H5.5A1.5 1.5 0 014 19V7.5A1.5 1.5 0 015.5 6H11"/><path d="M14.5 3.5h6v6M20.5 3.5l-9 9"/>',
 "oeil": '<path d="M2.5 12S6 5.5 12 5.5 21.5 12 21.5 12 18 18.5 12 18.5 2.5 12 2.5 12z"/><circle cx="12" cy="12" r="3"/>',
 "oeil-barre": '<path d="M9.5 6c.8-.3 1.6-.5 2.5-.5 6 0 9.5 6.5 9.5 6.5s-1 1.9-2.9 3.6"/><path d="M6.2 8C4 9.8 2.5 12 2.5 12S6 18.5 12 18.5c1.5 0 2.9-.4 4-1"/><path d="M9.9 9.9a3 3 0 004.2 4.2"/><path d="M3.5 3.5l17 17"/>',
 "fichier": '<path d="M13.5 3.5H6.5A1.5 1.5 0 005 5v14a1.5 1.5 0 001.5 1.5h11A1.5 1.5 0 0019 19V9z"/><path d="M13.5 3.5V9H19"/><path d="M8.5 13h7M8.5 16.5h5"/>',
 "dossier": '<path d="M3.5 6.5A1.5 1.5 0 015 5h4l2 2.5h8A1.5 1.5 0 0120.5 9v9a1.5 1.5 0 01-1.5 1.5H5A1.5 1.5 0 013.5 18z"/>',
 "diese": '<path d="M9 3.5L7 20.5M17 3.5l-2 17M3.5 8.5h17M2.5 15.5h17"/>',
 "image": '<rect x="3.5" y="4.5" width="17" height="15" rx="1.5"/><circle cx="8.5" cy="9.5" r="1.8"/><path d="M3.5 16.5l4.5-4 3.5 3 4-4.5 5 5.5"/>',
 "grille": '<rect x="3.5" y="3.5" width="7" height="7" rx="1"/><rect x="13.5" y="3.5" width="7" height="7" rx="1"/><rect x="3.5" y="13.5" width="7" height="7" rx="1"/><rect x="13.5" y="13.5" width="7" height="7" rx="1"/>',
 "ampoule": '<path d="M9 17.5h6"/><path d="M10 20.5h4"/><path d="M12 2.5a6.5 6.5 0 00-3.7 11.8c.5.4.7.9.7 1.5v.2h6v-.2c0-.6.2-1.1.7-1.5A6.5 6.5 0 0012 2.5z"/>',
 "courbe": '<path d="M3.5 3.5v17h17"/><path d="M7 15.5l3.5-4.5 3 2.5 5-6.5"/>',
 "liste": '<path d="M9 6.5h11M9 12h11M9 17.5h11"/><path d="M4.5 6.5v.01M4.5 12v.01M4.5 17.5v.01"/>',
 "cadenas": '<rect x="4.5" y="10" width="15" height="10.5" rx="1.5"/><path d="M8 10V7a4 4 0 018 0v3"/>',
 "epingle": '<path d="M12 21.5s7-6.3 7-11.5a7 7 0 10-14 0c0 5.2 7 11.5 7 11.5z"/><circle cx="12" cy="10" r="2.8"/>',
 "carte": '<path d="M9 4.5L3.5 6.5v13L9 17.5m0-13l6 2m-6-2v13m6-11l5.5-2v13L15 19.5m0-13v13m-6-2l6 2"/>',
 "enregistrer": '<path d="M4.5 5.5A1 1 0 015.5 4.5h11l4 4v11a1 1 0 01-1 1h-14a1 1 0 01-1-1z"/><path d="M7.5 4.5v6h8v-6"/><rect x="7.5" y="13.5" width="9" height="6"/>',
 "formes": '<circle cx="7.5" cy="7.5" r="4"/><rect x="13" y="13" width="7.5" height="7.5" rx="1"/><path d="M13 4h7.5v7.5"/>',
 "etoile": '<path d="M12 3.5l2.7 5.6 6 .9-4.4 4.2 1.1 6-5.4-2.9-5.4 2.9 1.1-6-4.4-4.2 6-.9z"/>',
 "tableau": '<rect x="3.5" y="4.5" width="17" height="15" rx="1.5"/><path d="M3.5 9.5h17M3.5 14.5h17M9.5 4.5v15M15 4.5v15"/>',
 "equipe": '<circle cx="9" cy="8" r="3.5"/><path d="M2.5 20c0-3.6 2.9-6 6.5-6s6.5 2.4 6.5 6"/><path d="M16 5.2a3.5 3.5 0 010 5.6"/><path d="M18 14.6c2.1.7 3.5 2.5 3.5 5.4"/>',
 "horloge": '<circle cx="12" cy="12" r="8.5"/><path d="M12 7v5.3l3.4 2"/>',
 "televerser": '<path d="M12 15.5v-11M7.5 8.5L12 4l4.5 4.5"/><path d="M4.5 18.5v2h15v-2"/>',
 "fenetre": '<rect x="3.5" y="4.5" width="17" height="15" rx="1.5"/><path d="M3.5 9h17"/><path d="M6.5 6.7v.01M9 6.7v.01"/>',
 # Le chevron ne vient d'aucune classe de l'ancienne charte : il remplace
 # un pseudo-element que celle-ci fournissait pour l'accordeon.
 "reglages": '<circle cx="12" cy="12" r="3"/><path d="M19.4 14.5a1.6 1.6 0 00.3 1.8l.1.1a2 2 0 11-2.8 2.8l-.1-.1a1.6 1.6 0 00-1.8-.3 1.6 1.6 0 00-1 1.5v.2a2 2 0 11-4 0v-.1a1.6 1.6 0 00-1-1.5 1.6 1.6 0 00-1.8.3l-.1.1a2 2 0 11-2.8-2.8l.1-.1a1.6 1.6 0 00.3-1.8 1.6 1.6 0 00-1.5-1h-.2a2 2 0 110-4h.1a1.6 1.6 0 001.5-1 1.6 1.6 0 00-.3-1.8l-.1-.1a2 2 0 112.8-2.8l.1.1a1.6 1.6 0 001.8.3h.1a1.6 1.6 0 001-1.5v-.2a2 2 0 114 0v.1a1.6 1.6 0 001 1.5 1.6 1.6 0 001.8-.3l.1-.1a2 2 0 112.8 2.8l-.1.1a1.6 1.6 0 00-.3 1.8v.1a1.6 1.6 0 001.5 1h.2a2 2 0 110 4h-.1a1.6 1.6 0 00-1.5 1z"/>',
 "deconnexion": '<path d="M9.5 20.5H5.5A1.5 1.5 0 014 19V5a1.5 1.5 0 011.5-1.5h4"/><path d="M15 16.5l4.5-4.5L15 7.5"/><path d="M19.5 12h-11"/>',
 "copier": '<rect x="8.5" y="8.5" width="12" height="12" rx="1.5"/><path d="M15.5 8.5V5A1.5 1.5 0 0014 3.5H5A1.5 1.5 0 003.5 5v9A1.5 1.5 0 005 15.5h3.5"/>',
 "alerte": '<path d="M12 3.5L21.5 20H2.5z"/><path d="M12 9.5v5M12 17.5v.01"/>',
 "memoire": '<path d="M12 4.5a4 4 0 00-4 4v.4a3.2 3.2 0 00-1.5 2.7 3.2 3.2 0 001.7 2.8A3 3 0 0011 19.5V4.9a3.9 3.9 0 011-.4z"/><path d="M12 4.5a4 4 0 014 4v.4a3.2 3.2 0 011.5 2.7 3.2 3.2 0 01-1.7 2.8A3 3 0 0113 19.5V4.9a3.9 3.9 0 00-1-.4z"/>',
 "chevron-bas": '<path d="M6 9.5l6 6 6-6"/>',
}

# nom de l'ancienne charte -> nom produit (sert a la substitution)
CORRESPONDANCE = {
 "add-circle-line": "ajout-cercle", "add-line": "ajout",
 "archive-line": "archive", "article-line": "article",
 "book-2-line": "livre", "bookmark-line": "signet",
 "building-line": "batiment", "chat-3-line": "discussion",
 "check-line": "coche", "checkbox-circle-line": "coche-cercle",
 "clipboard-line": "presse-papier", "close-line": "fermer",
 "dashboard-line": "tableau-bord", "database-line": "base-donnees",
 "delete-line": "supprimer", "download-line": "telecharger",
 "draft-line": "brouillon", "earth-line": "globe",
 "edit-box-line": "editer-bloc", "edit-line": "editer",
 "external-link-line": "lien-externe", "eye-line": "oeil",
 "eye-off-line": "oeil-barre", "file-line": "fichier",
 "folder-2-line": "dossier", "hashtag": "diese",
 "image-line": "image", "layout-grid-line": "grille",
 "lightbulb-line": "ampoule", "line-chart-line": "courbe",
 "list-unordered": "liste", "lock-line": "cadenas",
 "map-pin-2-line": "epingle", "road-map-line": "carte",
 "save-line": "enregistrer", "shapes-line": "formes",
 "star-line": "etoile", "table-line": "tableau",
 "team-line": "equipe", "time-line": "horloge",
 "upload-line": "televerser", "window-line": "fenetre",
}

manquantes = set(CORRESPONDANCE.values()) - set(ICONES)
assert not manquantes, "icones non dessinees : %s" % sorted(manquantes)
inutiles = set(ICONES) - set(CORRESPONDANCE.values()) - {"chevron-bas", "reglages", "deconnexion",
                                            "copier", "alerte", "memoire"}
assert not inutiles, "icones dessinees sans usage : %s" % sorted(inutiles)

GABARIT = ('<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" '
           'fill="none" stroke="#000" stroke-width="1.6" '
           'stroke-linecap="round" stroke-linejoin="round">%s</svg>')


def encode(svg):
    """Encodage minimal pour un data-URI CSS : guillemets simples suffisent."""
    return (svg.replace('"', "'")
               .replace("#", "%23")
               .replace("<", "%3C")
               .replace(">", "%3E")
               .replace("\n", ""))


lignes = []
for nom in sorted(ICONES):
    data = encode(GABARIT % ICONES[nom])
    lignes.append('.ic-%s{--ic:url("data:image/svg+xml,%s")}' % (nom, data))
# La feuille porte deux marqueurs : on remplace ce qu'il y a entre eux.
# Un fichier CSS separe imposerait une requete de plus et laisserait la porte
# ouverte a une desynchronisation entre les deux.

NL = chr(10)

# La feuille porte deux marqueurs : on remplace ce qu'il y a entre eux. Un
# fichier CSS separe imposerait une requete de plus et laisserait la porte
# ouverte a une desynchronisation entre les deux.
DEBUT = '/* ICONES-DEBUT - genere par tools/genere_icones.py, ne pas editer */'
FIN = '/* ICONES-FIN */'

# L'accordeon a besoin du chevron sous un nom fixe.
CHEVRON = ':root{--ic-chevron:var(--ic)}'

# Chaque trace est AUSSI expose sous un nom de variable.
#
# Une classe ne sert que si l'element peut la porter. Des qu'une icone doit
# vivre dans un pseudo-element deja ecrit -- le chevron d'un accordeon, le
# marqueur d'un appel d'outil dans le fil de discussion -- il faut le data-URI
# sous un nom. Les deux formes coexistent donc, depuis la meme source.
noms = NL.join(
    '  --ic-%s: url("data:image/svg+xml,%s");' % (n, encode(GABARIT % ICONES[n]))
    for n in sorted(ICONES))
racine = ':root{' + NL + noms + NL + '  --ic-chevron: var(--ic-chevron-bas);' + NL + '}'
bloc = NL.join([DEBUT, racine] + lignes + [FIN])

chemin = RACINE + '/hub/hub/static/produit.css'
css = io.open(chemin, encoding='utf-8').read()
d = css.index(DEBUT[:14])
f = css.index(FIN) + len(FIN)
with io.open(chemin, 'w', encoding='utf-8', newline=NL) as sortie:
    sortie.write(css[:d] + bloc + css[f:])

with io.open(RACINE + '/hub/hub/static/_correspondance_icones.txt', 'w',
             encoding='utf-8', newline=NL) as sortie:
    sortie.write(NL.join('%s	%s' % (k, v)
                         for k, v in sorted(CORRESPONDANCE.items())) + NL)

print('%d icones injectees dans produit.css' % len(ICONES))
