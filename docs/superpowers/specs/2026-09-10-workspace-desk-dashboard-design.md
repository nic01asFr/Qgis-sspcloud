# Design — Workspace dashboard A + Desk chat/cadre

Date : 2026-09-10. Validé utilisateur.

## Workspace
- Supprimer section Paramètres (menu compte suffit).
- Dashboard 2 colonnes (~1100–1200 px) :
  - Gauche ~60 % : études + créer
  - Droite ~40 % : CTA bureau + livrables + mémoire
- Archives repliables sous la liste gauche.
- Navbar inchangée.

## Desk — colonnes
- Chat `panel` : décale la carte (colonne grille).
- Ressources toujours `deck` (calque) : ne décale plus la carte.
- Poignée resize : chat uniquement.
- Confirmé le 2026-09-26 (après le lot 3, qui avait mis les Ressources en
  colonne au-delà de 1 166 px) : le chat ne voile jamais le bureau, à aucune
  largeur (sous 820 px il passe sous la carte) ; les Ressources restent un
  calque avec voile. Détail : `2026-09-26-ux-ressources-et-bureau.md`,
  section « Révision 2026-09-26 ».

## Desk — relief carte
- Chat ouvert : fond trame lignes croisées + iframe dans cadre (radius, ombre, bordure).
- Chat fermé : iframe plein, sans cadre.
- Trame ancrée au conteneur → profondeur au resize.

## Hors scope (équipe dédiée)
- BlockNote, éditeur recettes YAML, contenu mémoire agentique.
