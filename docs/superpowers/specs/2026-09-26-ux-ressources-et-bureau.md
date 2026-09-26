# Audit UX — Ressources, bureau et navigation

Date : 2026-09-26. Équipe UX/UI « Ressources et bureau ». Branche `ux/ressources-bureau`.

Public visé : agents publics non géomaticiens. Référentiels : RGAA 4.1 / WCAG 2.1 AA,
habitudes grand public (panneau repliable, toasts, annulation plutôt que
confirmation seule, vocabulaire courant).

Périmètre : `hub/templates/workspace.html` (« Mon espace »), `hub/templates/desk.html`
(bureau : Ressources, barre d'état, sélecteur étude/projet, menu compte, intégration du
chat), `hub/hub/static/produit.css`, recettes (`recipe_gallery.css`, `recipe_stream.js`),
pages de parcours servies par `hub/hub/main.py` (`/login`, `/onboarding`,
`/published/{owner}/`). Hors périmètre : intérieur du chat (`agent/templates/chat.html`,
équipe Chat), BlockNote, éditeur d'assemblage.

## Méthode

- Lecture intégrale des gabarits, des routes concernées (`/workspace/study/*`,
  `DELETE /publish/{kind}/{slug}`, `/desk/catalog`) et des tests structurels.
- Rendu local des deux gabarits avec Jinja et un contexte représentatif (3 études,
  4 livrables dont un fichier `features`, clé d'assistant manquante), servi en statique
  et inspecté dans Chrome (DevTools MCP) à 1366×768 et 390×800. Le service réel n'a pas
  été utilisé : aucune action sur le live.
- Les appels réseau des panneaux (livrables, couches) ont été simulés dans la page pour
  vérifier le rendu des cartes Livrables.

## Constats priorisés

Légende : **[C]** corrigé sur cette branche, **[R]** reste à faire.

### P0 — bloquant ou risque de perte

1. **[C] « Dépublier » irréversible sans filet** (workspace). `confirm()` natif au libellé
   « OK », suppression S3 immédiate, aucun retour.
   *Impact* : un clic de trop casse un lien déjà diffusé à des collègues ou au public.
   *Correction* : boîte de confirmation du produit (titre explicite, bouton rouge
   « Dépublier », focus initial sur « Annuler », Échap), puis **suppression différée** :
   la ligne disparaît, un toast « « X » dépublié. [Annuler] » reste 8 s (suspendu au
   survol/focus), la requête `DELETE` ne part qu'à l'expiration ; si la page est quittée
   entre-temps, elle part en `keepalive` (l'utilisateur l'a confirmée). Échec serveur :
   la ligne revient et un toast d'erreur le dit. Aucun changement d'API.

2. **[C] Tableau des livrables en jargon et illisible** (workspace). Colonnes « Type /
   Slug / Étude / Taille / Date / Lien / Actions », types bruts (`assembly`, `dataset`,
   `features`), identifiants techniques comme titres, 7 colonnes dans une colonne de
   ~440 px : défilement horizontal dès 1366 px.
   *Correction* : 3 colonnes (Livrable · Publié le · actions), titre = `title` ou slug
   humanisé, type traduit par une table d'affichage côté gabarit (`Page publiée`,
   `Jeu de données`, `Document PDF`, `Récit cartographique`…), actions en icônes
   nommées (Ouvrir — nouvel onglet signalé, **Copier le lien**, Dépublier). Les fichiers
   techniques `features` / `features_pmtiles` sont repliés derrière « Afficher aussi
   N fichiers de données de carte » et leur dépublication avertit que des cartes
   risquent de s'afficher vides.

3. **[C] Barre d'état en jargon et périmée** (bureau). « Récent : native:clip → 54556
   entités » (identifiant d'algorithme, figé au rendu donc faux dès le premier tour),
   « Workspace actif / Endormi », « GPU off / GPU dormant », « MCP: … ».
   *Correction* : « Récent » retiré ; « Bureau QGIS prêt / en veille » ; « Analyse
   d'images : prête / en veille / démarrage… / indisponible » avec info-bulle en clair ;
   « N livrables publiés (T au total) » devenu un bouton qui ouvre l'onglet Livrables ;
   « N notes de l'assistant » ; badge MCP reformulé « Autre session : … ».

4. **[C] Onglets Ressources sans sémantique ni clavier.** Boutons sans `role="tab"`,
   pas d'`aria-selected`, panneaux sans `role="tabpanel"`, aucune navigation aux flèches.
   *Correction* : motif d'onglets ARIA complet (tablist/tab/tabpanel, tabindex roulant,
   flèches, Début/Fin, `hidden` sur le panneau inactif). Le panneau a un titre
   (`<h2>Ressources de l'étude</h2>`) et un bouton Fermer ; Échap le ferme et rend le focus
   au bouton d'ouverture.

5. **[C] Archiver une étude : message trompeur, pas d'annulation.** « Vous pourrez toujours
   en créer une nouvelle » laisse croire à une perte ; aucune annulation, il fallait
   trouver « Études archivées ».
   *Correction* : confirmation claire (« rien n'est supprimé, restaurable à tout moment »),
   puis, au retour sur la page, toast « « X » archivée. [Annuler] » qui soumet le
   formulaire de restauration existant. Le POST classique reste le chemin nominal (marche
   sans JavaScript) ; l'état transite par `sessionStorage`.

### P1 — gêne forte, correction sûre

6. **[C] Cartes Livrables du bureau en jargon.** « draft », « Preview », « BlockNote »,
   « Métadonnées », « ⎘ Cloner », « Widget », « Storymap DSFR », « Dataset », « Flux WMS »,
   « Assistant V1.5 », titres = slug. *Correction* : brouillon / Aperçu / Modifier /
   Réglages / Dupliquer / Ouvrir ; types traduits (y compris `atlas_kpi_dashboard`,
   `carto_pdf`, `features`…) ; titre lisible ; **Copier le lien** sur tout livrable publié
   et tout assistant partagé ; liens nouvel onglet signalés (icône + texte masqué) ;
   « Voir les éléments » devient un vrai bouton `aria-expanded` ; titre de carte en `<h3>`.

7. **[C] Section Sources en jargon.** « Recettes user (YAML) », « Recettes web »,
   badge « EXAMPLE », « Executer », « .qgz seul », « → data/ », « Recette deterministe
   web V0.3.1 », « Workspace endormi », « Slug invalide », `✎` / `×` sans nom accessible,
   « Supprimer » alors que le serveur archive. *Correction* : « Mes recettes » (avec
   phrase d'aide), « Recettes prêtes à l'emploi », « Exemple / Personnelle », « Lancer avec
   l'assistant », « Projet seul (.qgz) », formats nommés en clair ; boutons Modifier /
   Archiver à icône nommés, confirmation « Archiver » cohérente + toast ; messages
   d'erreur d'envoi en clair ; nombre d'objets formaté (« 1 234 objets ») et type de
   géométrie traduit (points, lignes, surfaces).

8. **[C] Doublon de téléchargement.** « Télécharger (projet + données) » en tête et
   « ↓ Télécharger l'étude complète (ZIP) » en bas visaient la même URL
   `/studies/{sid}/export`. Le second est retiré.

9. **[C] Densité et cibles trop petites.** Textes de 9,5 à 11 px dans tout le panneau,
   boutons de 18–20 px de haut. *Correction* : textes 11,5–13 px, onglets 40 px, actions
   30–32 px, lignes de liste 30 px (WCAG 2.5.8 : 24 px minimum), panneau porté à 300 px
   minimum (`max(300px, var(--publi-w,240px))`).

10. **[C] Navigation incohérente entre pages.** Menus de compte différents (le workspace
    n'avait ni « Mon espace » ni « Documentation », le bureau pas de « Bureau »), page
    courante non signalée, liens nouvel onglet non annoncés, `role="menu"` sans flèches.
    *Correction* : mêmes entrées dans le même ordre sur les deux pages, `aria-current="page"`,
    « Assistant en pleine page » ajouté au menu du bureau, « (nouvel onglet) » en texte
    masqué partout, flèches/Début/Fin dans les menus (`produit.js`), focus dans le menu
    quand il est ouvert au clavier, retour du focus à la fermeture (menu, modale compte).

11. **[C] Sélecteur étude › projet.** Pas d'`aria-expanded`/`aria-haspopup`, champs de
    création sans libellé, « Vue d'ensemble… » vague, Échap ne rendait pas le focus ; sur
    téléphone le menu débordait à gauche de l'écran (noms coupés).
    *Correction* : attributs ARIA synchronisés, `aria-label` sur les champs, « Toutes mes
    études (Mon espace) », Échap ferme et rend le focus, menu ancré à l'écran sous 560 px.

12. **[C] Canvas en veille en jargon** (« workspace… scale 0 », « pod K8s + Xvfb »,
    « node froid », « logs du pod »). *Correction* : « Bureau QGIS en veille », phrases
    courantes, progression annoncée (`role="status"`).

13. **[C] Premier usage (Mon espace).** Bandeau « clé LLM » sans action ; état vide
    « Démarrez votre première étude » (vouvoiement isolé) sans dire ce qu'est une étude ;
    « Insights agentiques », « Préférences explicites » ; badge « ACTIVE » en capitales.
    *Correction* : bouton « Renseigner la clé de l'assistant » qui ouvre la modale au bon
    champ ; état vide explicatif au tutoiement ; « Tes préférences », « Ce que l'agent a
    remarqué » ; « Active » ; boutons « Ouvrir / Archiver » nommés avec l'étude
    (`aria-label`) ; « Créer l'étude ».

14. **[C] Couleurs de la galerie de recettes** héritées du bleu Marianne (#000091) dans un
    produit vert ; CTA « Publier » et toasts de `recipe_stream.js` idem, sans accents.
    *Correction* : jetons `--qs-*` avec repli, tailles relevées, animation coupée sous
    `prefers-reduced-motion`, textes accentués et en clair.

15. **[C] Pages de parcours.** `/login` : survol du bouton en bleu vif `#1212ff` (reste de
    l'ancienne charte), pas de focus visible ; `/published/{owner}/` : **pas de balise
    viewport** (illisible sur téléphone). Corrigés a minima.

16. **[C] Mise en page téléphone de « Mon espace ».** Les blocs gardaient la largeur de leur
    contenu (`align-items:start` hérité de la grille) : colonnes de largeurs différentes.

### P2 — à planifier

17. **[R] Recherche, filtre et tri des livrables** (workspace et bureau) : utiles au-delà
    d'une dizaine d'éléments ; nécessitent de décider l'ordre par défaut (date) et la
    persistance. Aujourd'hui la liste est courte (tronquée côté serveur).
18. **[R] Relancer une recette depuis « Mes recettes »** : seule l'édition YAML est
    proposée. Il faudrait un bouton « Relancer » envoyant `recipe_run_request` au chat
    (contrat postMessage de l'équipe Chat) — à concevoir avec elle.
19. **[R] Demandes « à copier-coller » vers le chat** (`quick-action-btn`) : l'utilisateur
    doit coller lui-même. Un envoi direct par `postMessage` (type à définir avec l'équipe
    Chat) supprimerait l'étape.
20. **[R] Fil d'Ariane explicite** « Mon espace › Étude › Projet » : aujourd'hui la marque
    sert de lien retour et le sélecteur porte étude › projet. Un premier segment
    « Mon espace » visible réduirait l'ambiguïté mais prend de la place dans une barre
    déjà contrainte à 1366 px avec le chat ouvert. À maquetter.
21. **[R] Pages `/login`, `/onboarding`, `/published/{owner}/`** : styles propres en ligne
    dans `main.py`, en-têtes différents de Mon espace et du bureau, classes inexistantes
    sur `/onboarding` (`qs-marque__titre`, `qs-texte-discret`, `qs-btn--primaire`), lien
    « Retour à mon espace » affiché aux visiteurs anonymes du catalogue public. Les
    rapprocher de `produit.css` demande de sortir ces pages en gabarits — hors correctif sûr.
22. **[R] Modale « Mon compte » de Mon espace** : styles en ligne et contrastes codés en dur
    (#666 sur blanc = 5,7:1, conforme, mais hors jetons) ; pas de piège à focus (le bureau
    non plus). À factoriser avec celle du bureau.
23. **[R] Code mort** : `.mem-*` dans `desk.html`, panneaux cachés (composants,
    assemblages, exports, publi, agents) encore rendus avec du jargon (« audit_chain »,
    « feature flag », `alert()`), invisibles mais lus par certains outils. Purge à faire
    avec l'équipe layout (les loaders JS y sont encore câblés).
24. **[R] Compteur de livrables** : `catalog_count` inclut les fichiers techniques repliés ;
    le badge peut annoncer 4 quand 3 lignes sont visibles. Le compteur est figé par
    `test_compteur_livrables` ; à trancher côté produit.
25. **[R] Contraste des pastilles d'étude** et du texte `--qs-discret` à 11 px : conformes
    AA (4,6:1) mais limites ; à surveiller si les tailles redescendent.

## Composants ajoutés

`hub/hub/static/produit.js` (sans dépendance) : `qsToast(message, {type, action,
surAction, surFin, duree})` en région `role="status"`, suspendu au survol/focus ;
`qsConfirmer({titre, message, confirmer, annuler, danger})` → `Promise<boolean>`
(`role="alertdialog"`, piège à focus, Échap) ; `qsCopier(texte)` avec repli ; navigation
aux flèches dans tout `role="menu"`. Styles `.qs-toast*`, `.qs-dialogue*`,
`.qs-btn--danger`, `.qs-sr` dans `produit.css` (animations neutralisées par la règle
`prefers-reduced-motion` existante).

## À valider visuellement après déploiement

- Mon espace à 1366×768 : tableau des livrables sans défilement horizontal, toast de
  dépublication puis « Annuler » (le lien doit rester actif), puis dépublication réelle
  sur un livrable de test uniquement.
- Archiver une étude de test → toast « Annuler » → l'étude revient dans la liste.
- Bureau : onglets au clavier (Tab, flèches, Échap), bouton « N livrables publiés » de la
  barre d'état, « Copier le lien » sur une carte Livrables, puce « Analyse d'images ».
- Téléphone (≤ 560 px) : menu étude/projet entièrement visible, barre d'état sans
  défilement.
- Vérifier avec les données réelles que `item.title` est renseigné dans le catalogue
  (sinon le slug humanisé s'affiche).
