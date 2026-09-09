# Spec — UX BlockNote / recettes / mémoire (suite)

Date : 2026-09-10. Équipe UX dédiée (surfaces hors layout grille desk).

## Déjà livré (wins in-place)

- **BlockNote** : chrome allégé (brand `QGIS · Éditeur`, titre plus petit, barre d’état silencieuse en idle, aperçu « Aperçu » sans jargon DSFR/emoji, footer sans ADR). Tooltip hover sans emoji. Panel assistant sans estampe « CEREMA » dans les libellés visibles.
- **Modal recettes** (`#recipe-editor-overlay`) : classes CSS produit, overlay plus léger, fermeture Échap, IDs JS inchangés.
- **Mémoire chat** : titre « Mémoire », footer court, drawer plus étroit, faits sans % confiance / jargon LLM.

## Problèmes restants (risqués — ne pas shipper sans validation)

### BlockNote

1. **Thème `#000091` / Marianne** encore dans `editor-layout.css` et `design/tokens.ts` — verrouillé par tests sprint 3/4 (fidélité aperçu publié). Remplacer par jetons `--qs-*` casserait la prévisualisation « proche du render » et une batterie de tests structurels.
2. **Fond header AgentPanel bleu Marianne plein** : contraste institutionnel fort vs desk vert produit. Migration vers chrome neutre + accent `--qs-accent` = redesign panel + revue a11y.
3. **Dictionary FR BlockNote** (side menu) : déjà noté V1.21 dans le code ; hors scope chrome.
4. **Dead CSS `.mem-*` dans `desk.html`** : styles orphelins de l’ancien panel mémoire desk (plus de markup). Purge sûre mais large diff desk — à faire avec l’équipe layout.

### Recettes

1. **Pas de coloration YAML / lint** : textarea brute. CodeMirror = dépendance + autosave conflict possible.
2. **Pas de diff version** : meta `vN · sha` seulement ; UI historique = nouvelle surface.

### Mémoire

1. **Pas d’action « recopier dans une section »** depuis un insight (footer le mentionnait ; le JS ne le fait pas). Ajouter un bouton = nouveau flux PATCH + UX.
2. **Double source de vérité** sections markdown vs insights : clarifier promote/forget dans un petit plan produit avant UI.

## Hors scope (confirmé)

- Refactor modèle Assembly/Components.
- Layout grille desk (resources/chat columns).
- Nouvelles surfaces (pas de 4ᵉ panneau mémoire desk).
