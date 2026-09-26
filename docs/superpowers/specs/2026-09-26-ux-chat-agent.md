# Audit UX du chat de l'agent -- 2026-09-26

Perimetre : `agent/templates/chat.html`, servi seul (`/agent/`, page « Assistant
IA ») et incruste dans le bureau (`/agent/?embed=1`, colonne d'environ 300 px).
Public : agents publics non geomaticiens. Reference : assistants grand public
(ChatGPT, Claude, Le Chat), RGAA / WCAG 2.1 AA.

Methode : navigation dans Chrome sur le service (etude « bac-a-sable banc »),
page seule a 1280 px et colonne a 320 px ; deux messages courts (« Explique-moi
ce que tu peux faire ») ; lecture du DOM ; Lighthouse accessibilite (91/100) ;
lecture du protocole SSE (`agent/agent/main.py`, `qgis_agent.py`). Les
corrections ont ete verifiees dans Chrome sur le gabarit rendu localement, avec
un flux SSE simule (phases, texte, blocs d'outil, `retirer_texte`, flux coupe,
tour vide).

## Constats et corrections

Format : constat -> impact utilisateur -> correction. [fait] = corrige dans la
branche `ux/chat-agent` ; [reste] = non traite ici, avec la raison.

### P0 -- bloquant ou trompeur

1. **Statut affiche deux fois, et fige apres la fin du tour.** La bulle
   (`#current-response`) et la barre au-dessus de la saisie (`#status-bar`)
   repetaient « Reflexion… · 5 s ». Le repere `.bubble-thinking` insere quand
   tout le rendu est masque n'etait pas retire en fin de tour : « Redaction de
   la reponse… · 170 s » restait affiche, un tour vide laissait « Redaction…
   5 s » sans message. -> L'utilisateur croit que l'agent travaille encore, ou
   qu'il a plante. -> [fait] Un seul etat visible : une ligne `.tour-statut`
   dans le tour, hors du contenu rendu (chaque morceau recu ne l'efface plus).
   `terminerTour()` retire `.tour-statut` et `.bubble-thinking` quoi qu'il
   arrive (bloc `finally`). La barre ne sert plus qu'aux lecteurs d'ecran
   (`role="status"`, annonce seulement quand l'etape change). Tour sans texte
   lisible : message clair et bouton Relancer (le serveur envoie desormais un
   texte de repli ; le front garde un repli s'il manque).

2. **Flux ferme sans fin annoncee, ou muet.** -> Reponse tronquee presentee
   comme complete, ou attente sans fin. -> [fait] « Reponse interrompue »
   deja present, conserve. Ajout d'un garde-fou : sans aucune nouvelle du
   serveur pendant 75 s (il bat toutes les 15 s), le flux est coupe et la
   bulle le dit.

3. **La reponse disparait apres rechargement du bureau.** Cause : l'agent
   n'enregistre la reponse qu'a la fin du tour (`memory.add_message` en fin de
   `chat_stream`) ; recharger la page (le bureau le fait lui-meme quand le
   poste de travail devient pret, `desk.html`, `location.reload()`) coupe le
   flux, le tour est annule, seul le message de l'utilisateur reste. ->
   L'utilisateur perd sa reponse sans explication. -> [fait, cote front] le
   dernier message sans reponse affiche « La reponse a ce message n'a pas ete
   conservee… » avec Relancer. [reste, serveur] enregistrer la reponse
   partielle a l'annulation, ou laisser finir le tour detache du client
   (equipe agent). [reste, bureau] eviter `location.reload()` pendant un tour
   (equipe bureau : `qgis_agent_turn_done` dit quand le tour finit).

4. **Messages intermediaires en jargon dans la reponse.** « Je vais appeler
   get_project_info… » restait visible meme « Details techniques » masques :
   seuls les blocs d'outil etaient caches, pas le texte du modele entre deux
   actions. -> Bruit, jargon, la reponse utile est noyee. -> [fait] Tout ce
   qui precede la fin du dernier bloc d'outil est range dans « Voir les
   etapes (n) », replie ; la reponse est ce qui suit. Les etapes sont nommees
   en langage courant (« Lecture du projet QGIS ») : en direct par les phases
   du serveur, au rechargement par la meme table `_LIBELLES_OUTILS`, passee au
   gabarit. Le nom technique d'un outil n'est plus jamais un libelle d'etat.

5. **Texte a la suite d'un bloc d'outil avale par la citation.** Le serveur
   ecrit `> **\`outil\`** — args\n` ; si le texte suivant arrive sans ligne
   vide, Markdown le rattache au bloc cite, masque avec les details
   techniques. -> Une partie de la reponse disparait. -> [fait] ligne vide
   inseree apres chaque en-tete d'outil avant le rendu.

6. **Evenement `retirer_texte`** (nouveau, equipe serveur : faux appel
   d'outil ecrit en texte, puis relance). -> Sans traitement, le faux appel
   reste affiche. -> [fait] retire de `fullText` seulement s'il en est la fin
   (espaces finaux ignores), sinon ignore ; nouveau rendu.

### P1 -- confort, comprehension, accessibilite

7. **« Nouvelle conversation » recharge l'iframe** : l'ancienne conversation
   reste visible un instant, puis l'accueil clignote. -> [fait] Conversation
   neuve sur place : nouvel identifiant (le serveur cree la session au premier
   message), accueil depuis un `<template>`, focus dans la saisie. Refusee
   pendant un tour, comme le changement de conversation.

8. **Conversation reprise : l'accueil s'affiche puis est remplace.** -> Flash
   trompeur (« Assistant pret » puis l'historique). -> [fait] « Chargement de
   la conversation… » tant que l'historique n'est pas lu.

9. **Bouton « Technique » : etat illisible.** Il fallait ouvrir le menu pour
   savoir si les details etaient affiches. -> [fait] Libelle « Details
   masques / affiches », style actif, `aria-label` a jour, libelles des cases
   en langage courant.

10. **Defilement force.** Chaque morceau recu ramenait en bas, meme si
    l'utilisateur remontait relire. -> [fait] Suivi seulement si l'utilisateur
    est deja en bas ; sinon bouton « Aller au dernier message ».

11. **Pas d'action sur une reponse.** -> [fait] Pied discret : heure (aussi
    sous chaque message de l'utilisateur, date complete au survol), Copier,
    Relancer (derniere reponse seulement).

12. **Accessibilite** (Lighthouse 91) : tiroir memoire ferme mais focusable
    (`aria-hidden` + champs) ; saisie sans nom accessible ; micro sans nom ;
    pas de titre de niveau 1 ; conversation sans region nommee ; menus sans
    `aria-expanded` initial ; cibles de 16-20 px (resume des resultats,
    suppression d'un fait). -> [fait] `inert` sur le tiroir ferme et focus sur
    sa fermeture a l'ouverture ; `aria-label` saisie, micro ; `h1` masque ;
    region « Conversation » ; `aria-haspopup/expanded/controls` ; cibles
    >= 24 px ; Entree n'envoie pas pendant une saisie accentuee (IME) ;
    animations coupees sous `prefers-reduced-motion`.

13. **Rendu Markdown** : titres `h2/h3` a la taille de la page dans une bulle
    de 300 px ; tableaux sans bordures ; un bloc de code de la reponse replie
    en « Resultat outil » et masque avec les details techniques. -> [fait]
    titres a l'echelle de la bulle, tableaux bordes et defilants, seul un bloc
    qui suit un bloc d'outil est replie.

14. **Suggestions de depart en jargon** (« T100 », « SAM3 », « DVF côte
    2021-2024 ») et gestionnaire en ligne casse par une apostrophe. -> [fait]
    Trois demandes en langage courant, identiques dans l'accueil et la barre
    laterale ; boutons construits par le DOM.

15. **Page seule (« Assistant IA »)** : pas de repere de l'etude, pas de
    retour vers le service hors du pied de page, pied de page qui reduit la
    zone de conversation, en-tete aux balises mal fermees, doublons
    « Nouvelle conversation » et « Bureau ». -> [fait] En-tete : « Etude
    active : <nom> » (lu au passage sur le hub, sans second appel), navigation
    « Mon espace » / « Bureau de travail » (libelles du pied et du bureau) ;
    la conversation occupe la fenetre sous l'en-tete, le pied vient apres ;
    balises equilibrees ; doublons masques. Titre de l'onglet « Assistant IA
    — QGIS Service ».

### P2 -- a planifier

16. [reste] Emojis et noms d'outils dans les reponses : consigne du modele
    (equipe prompt). Le front rend proprement mais ne filtre pas le texte.
17. [reste] Pas de theme sombre : la feuille `produit.css` n'en definit pas ;
    en ajouter un au seul chat creerait une incoherence. A faire dans la
    feuille commune.
18. [reste] « Arreter » attend jusqu'a 60 s la fin de l'action en cours
    (periode de grace serveur). Le libelle dit desormais « Arret demande : je
    termine l'action en cours… » ; une annulation plus franche est cote
    serveur.
19. [reste] L'historique (barre laterale et menu) n'est pas mis a jour sans
    rechargement apres une nouvelle conversation ; il faudrait une route
    listant les sessions de l'etude en JSON (existe sans filtre d'etude :
    `/sessions`).
20. [reste] Resumes des resultats d'outil (« ✓ résultat tool ») et bouton
    « ↶ Revenir avant » : vocabulaire technique, visibles seulement avec les
    details affiches. A reformuler avec l'equipe agent (le retour arriere
    tronque la conversation : consequence a expliquer).
21. [reste] Lecteurs d'ecran : la reponse n'est pas lue automatiquement
    (seul « Reponse terminee » est annonce) ; choix delibere, le rendu
    complet a chaque morceau ferait relire tout le texte. A tester avec NVDA.
22. [reste] Chargement de l'historique limite a 50 messages
    (`get_session_messages`), sans indication.

## Coordination

- Serveur (agent) : texte de repli et `done` garantis, `retirer_texte` --
  pris en charge ici. Demande : ne pas perdre la reponse d'un tour dont le
  client se deconnecte (constat 3).
- Bureau (`hub/templates/desk.html`, non modifie) : `location.reload()` au
  reveil du poste de travail coupe un tour en cours (constat 3). Libelles
  alignes : « Mon espace », « Bureau de travail », « Nouvelle conversation »,
  « Historique », « Memoire ».

## A verifier visuellement apres deploiement

- Colonne du bureau (300 px) : ligne d'etat, « Voir les etapes », pied de
  reponse, bouton « Aller au dernier message ».
- Tour reel avec plusieurs actions : texte intermediaire bien range dans les
  etapes, reponse seule visible ; captures visibles en page seule.
- Rechargement du bureau pendant un tour : message « reponse non conservee »
  et Relancer.
- Page seule : en-tete (nom de l'etude, navigation) a 1280 et 600 px.
- Clavier : Tab jusqu'a « Voir les etapes », Copier, Relancer ; Echap sur les
  menus ; tiroir memoire.
