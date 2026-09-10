# Spec — UX conversation chat agent (suite)

Date : 2026-09-10. Surface : `agent/templates/chat.html` (embed desk + standalone `/agent/`).

## Déjà livré (wins in-place)

- **Densité embed** : padding/gap messages, bulles, composer et welcome resserrés pour colonne ~260–300 px.
- **Copy FR** : placeholder « Écris ton message… », welcome « Assistant prêt », bouton Arrêter sans emoji.
- **Hiérarchie outils** : bordure tool + pulse en accent vert produit (`--qs-accent` / `#41701F`), plus Marianne `#000091`.
- **Liens livrables** : style lien dans bulles (assistant + user) + `word-break`.
- **Erreurs** : classe `.bubble-err` (plus d’inline `#ce0500`), escape HTML sur messages d’erreur.
- **Toast** : `aria-live="polite"`.
- **Composer** : padding bas du formulaire pour ne plus coller au bord iframe.

Tests structurels : `agent/tests/test_chat_embed_ux.py`.

## OK aujourd’hui (ne pas casser)

| Zone | État |
|------|------|
| Chrome embed | Footer / sidebar / lien Bureau masqués ; mémoire drawer accessible |
| Streaming | Spinner bulle + status-bar « Outil : … » (utile quand tools masqués) |
| Tools / reasoning | Masqués par défaut ; popover Technique + prefs `localStorage` |
| Screenshots | Cachés en embed (canvas noVNC voisin) ; visibles standalone |
| Nouvelle conv | Toast + `?new=1` sans `confirm()` navigateur |
| Historique | Popover (embed + standalone) ; sidebar sessions = standalone only |
| Liens markdown | `target=_blank` (évite navigation iframe) |
| Stop | POST `/chat/{sid}/stop` + grace SSE |

## Écarts embed vs standalone (ne pas unifier encore)

| Aspect | Embed (`?embed=1`) | Standalone (`/agent/`) |
|--------|--------------------|------------------------|
| Chrome | Toolbar icônes, pas d’entête/pied | Header QGIS Service + footer + sidebar Suggestions/Sessions |
| Densité | Compacte (cette livraison) | Plus aérée (canvas agent futur) |
| Screenshots tool | Cachés | Visibles inline |
| Rôle produit | Panneau desk à côté du canevas QGIS | Futur « canevas agent » publié (piste lecture seule QGIS séparée) |

Ne pas forcer un seul layout : une autre piste traite le canevas QGIS lecture seule côté standalone.

## Problèmes restants (sévérité)

### Haute — hors scope immédiat / risque API

1. **Pas de bouton « Copier » sur réponse assistant** — demandé souvent ; nécessite UI actions au hover + clipboard API. Pas sur la roadmap courte ; à valider produit.
2. **Rollback via `confirm()` / `alert()` navigateur** — incohérent avec nouvelle conv (toast). Remplacer par modal inline = nouvelle surface.
3. **Historique figé au SSR** — liste sessions du popover n’est pas rafraîchie après nouvelles conv sans reload. Fetch `/sessions` à l’ouverture = petit chantier JS + API.

### Moyenne

4. **Double feedback loading** — status-bar + spinner bulle redondants quand tools visibles ; garder status-bar tant que tools masqués par défaut.
5. **`escapeHtml` dupliqué** dans le script — dette, pas UX.
6. **Welcome exemples** encore longs en embed (3 chips) — OK pour onboarding ; éventuellement 2 max si feedback densité insuffisant.
7. **Chip profil / suggestions sidebar** — dead UI partielle (select profil hidden ; suggestions sidebar invisibles en embed). Cleanup standalone quand canevas agent.

### Basse

8. **CSS orphelin `.context-chip`** (hover `#dde0fe` Marianne) — markup chip absent de la toolbar actuelle.
9. **Title page** encore « QGIS Agent — QGIS Service » — aligner avec brand « QGIS Service » au passage canevas.
10. **Mic** : feedback erreur seulement via `title` — toast serait mieux.

## Hors scope (confirmé)

- `workspace.html`, grille desk resources/chat columns, resize panel.
- Unification totale embed/standalone.
- Nouvelles features (copier message, modal rollback, refresh live historique) sans validation produit.
