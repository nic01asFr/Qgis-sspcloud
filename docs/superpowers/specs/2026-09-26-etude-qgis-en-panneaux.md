# Étude de faisabilité — QGIS « en panneaux » dans le bureau web

Date : 2026-09-26. Équipe T2 « QGIS en panneaux ». Branche `docs/etude-qgis-panneaux`
(qgis-sspcloud) ; prototype sur `proto/qgis-panneaux` (BigQgisMCP, non branché par défaut).

## Question

Peut-on « déstructurer » QGIS Desktop (canevas, arbre des couches, barres d'outils,
boîte à outils Traitements, extensions, tables attributaires, mise en page) en panneaux
distincts, réorganisés dans la page web selon la taille d'écran, en gardant la
fonctionnalité, pour maximiser la surface du canevas et rester lisible sur tout écran ?

## Réponse courte

Oui, mais pas en une fois ni par une seule technique. La voie recommandée est
**incrémentale et hybride** :

1. **Tout de suite (prototype prêt)** : écran X redimensionnable à la taille réelle de
   la zone web + profils d'interface QGIS pilotés par le pont (`standard`, `compact`,
   `carte_max`). Aucun changement de serveur VNC : mesuré, Xvfb 21.1 et x11vnc savent
   déjà suivre un changement de taille RandR. C'est ce qui règle les bandes et la carte
   trop petite.
2. **Ensuite** : panneaux web natifs (couches, traitements, attributs) pilotés par le
   pont PyQGIS, le canevas restant QGIS (via noVNC, en profil `carte_max`). On gagne la
   réorganisation par taille d'écran et l'accessibilité là où elle compte, sans perdre
   l'édition ni les extensions.
3. **En option** : canevas web MapLibre (consultation, mobile), alimenté par QGIS
   Server ou un point de tuiles du pont. QGIS Desktop complet reste à un clic.

Le transport par fenêtre (Xpra « seamless ») est techniquement possible — les docks
flottants de QGIS sont bien des fenêtres X distinctes, vérifié — mais coûte cher
(client HTML5 à forker, paquet Ubuntu obsolète, charge CPU accrue) pour un gain que
les étapes 1 et 2 obtiennent à moindre risque.

## État des lieux (lu dans les sources, puis mesuré)

Sources lues : BigQgisMCP `origin/main` (`Dockerfile`, `entrypoint.sh`,
`supervisord.conf`, `maximize_qgis.sh`, `src/qgis_bridge.py`, `src/api_server.py`,
`src/stream_server.py`, `main_mcp.py`, `qgis_app.html`) ; qgis-sspcloud `origin/main`
(`hub/templates/desk.html`, `hub/hub/main.py` proxy `/workspace/vnc/*`,
`charts/qgis-hub` valeurs `workspace`).

Chaîne actuelle : Xvfb `:99` à `QGIS_RESOLUTION` (1920×1080×24) → fluxbox (fenêtres
maximisées) → QGIS → x11vnc (port 5900) → websockify/noVNC (6080) → proxy hub
`/workspace/vnc/*` → iframe `vnc_lite.html?scale=true` dans `desk.html`. Le bureau
donne au cadre le ratio du bureau distant (`--qgis-ratio`) ; `_inject_vnc_desk_embed`
prévoit déjà `?resize=true` « si un workspace sait un jour redimensionner son écran ».

Expériences locales (Docker Desktop, image `ghcr.io/nic01asfr/qgisremotemcp:latest`
tirée en lecture, aucun service live touché) :

| Mesure | Résultat |
|---|---|
| Version réelle de QGIS dans l'image | **3.44.14** (le dépôt `ubuntu-ltr` sert désormais la 3.44 LTR, pas la 3.34), Qt 5.15.13 |
| Xvfb 21.1.12 : `xrandr` vers une taille plus petite ou égale à la taille de lancement | OK (1280×800, 1437×913, 1600×1000…) ; **agrandir au-delà échoue** (`BadMatch`) : la taille de lancement est le maximum |
| x11vnc 0.9.16 sans option pendant ces changements | détecte l'événement RandR, recrée son tampon (`rfbNewFramebuffer`) et annonce la taille aux clients |
| QGIS maximisé par fluxbox après changement de taille | suit la nouvelle taille (fenêtre et canevas) |
| TigerVNC Xvnc 1.13.1 (paquet noble) | redimensionnable **au-delà** de la taille de lancement (3000×1800 depuis 1920×1080), GLX présent, accepte `SetDesktopSize` du client |
| Flux MJPEG `:8081/frame` après réduction de l'écran | **500** : `-video_size` figé au lancement, x11grab refuse une zone plus grande que l'écran |
| Capture de secours ffmpeg du pont | même défaut (`1920x1080` en dur) |
| Échelle Qt de QGIS sous Xvfb | **1,04** (politique d'arrondi `PassThrough` de QGIS, DPI déduit par Xvfb) : écran logique 1844×1037 pour 1920×1080 physiques. Avec `Xvfb -dpi 96` : 1,0 |
| Profil « carte seule » (`mActionToggleMapOnly`) | canevas = 100 % de la fenêtre ; `saveState()` = 2,6 ko, `restoreState()` rend l'état exact |
| Dock passé en flottant (`setFloating(True)`) | devient une fenêtre X de premier niveau distincte (vue par `xdotool search`, listée par fluxbox) |
| Charge au repos (projet d'une couche) | ~520–620 Mio (qgis.bin ~610 Mio RSS, Xvfb ~95 Mio à 2560×1440), CPU < 1 % |
| Paquets disponibles (noble + dépôt QGIS) | `xpra` **3.1.5** seulement (obsolète, 6.x via le dépôt xpra.org) ; `tigervnc-standalone-server` 1.13.1 ; `qgis-server` 3.44.14 |

Deux constats hors question mais utiles :

- `supervisord.conf` exige `QGIS_RESOLUTION` (`%(ENV_QGIS_RESOLUTION)s`) : sans la
  variable, supervisord refuse de démarrer. Le chart la pose, un `docker run` nu non.
- `get_screenshot` rend **la carte seule** (méthode 1 : `QgsMapRendererSequentialJob`
  aux dimensions demandées) dès qu'une couche est chargée, et le bureau entier sinon.
  Or `mouse_click` attend des pixels du bureau 1920×1080. Un agent qui vise un clic
  d'après la capture vise déjà faux aujourd'hui. À corriger quel que soit le choix
  (voir « Impacts MCP »).

## Options étudiées

### (a) Écran redimensionnable + mise en page QGIS pilotée

Principe : l'écran X prend la taille de la zone web ; le pont applique un profil
d'interface adapté (docks, barres, carte seule).

Deux variantes de serveur d'affichage :

- **a1 — garder Xvfb + x11vnc**, lancer Xvfb à une taille maximale (ex. 3840×2160) et
  le redimensionner par `xrandr` sur demande du bureau (`POST /api/display`). x11vnc
  ignore toujours la demande du client (`SetDesktopSize`), mais ce n'est plus
  nécessaire : c'est le hub qui transmet la taille. **Mesuré, fonctionne.** Coût
  mémoire du maximum : tampon 3840×2160×4 = 33 Mo.
- **a2 — remplacer par TigerVNC Xvnc** (un seul processus serveur X + VNC) : noVNC
  `resize=remote` fonctionne nativement, pas de maximum, pas de scrutation du tampon
  par x11vnc (Xvnc connaît les zones modifiées : CPU plus bas en mouvement de carte).
  Variante KasmVNC (fork de TigerVNC) : client web intégré, redimensionnement et
  « multi-écran » en fenêtres de navigateur séparées, meilleurs codecs ; mais il
  remplace noVNC et le proxy hub devrait suivre.

Mise en page pilotée (dans le pont, `iface.mainWindow()`) : les 19 docks de QGIS 3.44
ont des `objectName` stables (`Layers`, `Browser`, `ProcessingToolbox`,
`LayerStyling`, `MessageLog`, `ResultsViewer`, `Temporal Controller`…) ainsi que les
20 barres (`mMapNavToolBar`, `mAttributesToolBar`…). `saveState`/`restoreState`,
`show`/`hide`, `setFloating`, `resizeDocks` et l'action `mActionToggleMapOnly` couvrent
tout le besoin. La table attributaire peut s'ouvrir en dock (réglage QGIS
`qgis/dockAttributeTable`), la mise en page reste une fenêtre séparée (QMainWindow du
composeur).

| Critère | Évaluation |
|---|---|
| Fidélité fonctionnelle | **Totale** : c'est QGIS lui-même (extensions, traitements, édition, composeur) |
| Latence / bande passante | Inchangées (VNC). Un écran plus petit = moins de pixels à envoyer ; plus grand = un peu plus |
| Charge serveur | a1 : identique (+33 Mo de tampon max). a2 : légèrement inférieure (plus de x11vnc) |
| Outils MCP | Compatibles si les coordonnées suivent l'écran courant (taille exposée) ; flux MJPEG et capture de secours à corriger (fait dans le prototype) |
| Accessibilité | Inchangée : un canvas VNC reste opaque aux lecteurs d'écran |
| Risques | Redimensionnement pendant une suite de clics d'agent (course) ; fenêtres de dialogue QGIS positionnées pour l'ancienne taille ; téléphone (< 800 px) toujours en mise à l'échelle |
| Effort | a1 : prototype fait (½ j), intégration bureau 2–3 j. a2 : 2–3 j + validation 3D/GLX |

### (b) Transport par fenêtre (une fenêtre X = un élément web)

- **Xpra + client HTML5** : en mode « seamless », chaque fenêtre X devient un élément
  (canvas) positionné dans la page, avec son propre décor. Vérifié côté QGIS : un dock
  rendu flottant est une fenêtre X de premier niveau, il deviendrait donc un panneau
  web distinct ; idem table attributaire flottante, composeur, dialogues.
  Limites : le client xpra-html5 gère lui-même la position des fenêtres (bureau
  flottant) ; pour les ranger dans une grille responsive il faut le forker ou piloter
  son API JS. Le paquet noble est la 3.1 (obsolète) ; il faut le dépôt xpra.org
  (6.x). Chaque fenêtre est encodée séparément, serveur Python : CPU et mémoire
  supérieurs à x11vnc (ordre de +100 à +300 Mo, à mesurer). Menus déroulants, bulles et
  dialogues modaux sont des fenêtres transitoires qu'il faut placer correctement. Les
  docks flottants perdent l'ancrage automatique : leur taille ne suit plus la fenêtre
  principale, c'est le client web qui doit la dicter.
- **noVNC multi-vues sur des régions** : plusieurs x11vnc `-clip LxH+X+Y` sur un grand
  écran où le pont place les docks flottants dans des régions disjointes ; chaque
  région = une iframe noVNC. Peu de code, mais fragile : un menu ou un dialogue qui
  s'ouvre hors région est invisible (il faut garder une vue « bureau entier »), chaque
  x11vnc scrute le tampon (CPU × n), et chaque redimensionnement de panneau impose de
  déplacer/redimensionner la fenêtre correspondante.
- **Broadway** : GTK seulement, QGIS est Qt — écarté. **waypipe / Wayland** : pas de
  client navigateur — écarté. **KasmVNC multi-écran** : ouvre des fenêtres de
  navigateur, pas des panneaux dans une page — hors cible.

| Critère | Évaluation |
|---|---|
| Fidélité fonctionnelle | Totale en théorie ; en pratique dépend du placement des fenêtres transitoires |
| Latence / bande passante | Xpra : bonne (encodage par fenêtre, mise à l'échelle client) ; multi-vues : n flux |
| Charge serveur | En hausse (Xpra Python + encodeurs, ou n × x11vnc) |
| Outils MCP | xdotool agit toujours sur le même écran X : les clics restent valides en coordonnées X, mais la capture du « bureau entier » ne correspond plus à ce que voit l'utilisateur |
| Accessibilité | Inchangée (canvas) |
| Risques | Élevés : fork d'un client tiers, dialogues perdus, focus clavier entre fenêtres, maintenance de deux piles (noVNC + Xpra) |
| Effort | 3 à 6 semaines pour un résultat stable, puis maintenance |

### (c) Hybride : panneaux web natifs + QGIS moteur

Principe : les panneaux deviennent du HTML (DSFR) alimentés par le pont PyQGIS/MCP,
QGIS reste le moteur et le canevas.

- **Panneaux web** : le pont expose déjà l'essentiel — `list_layers`,
  `get_project_info`, `set_layer_visibility`, `set_layer_style`, `get_features`,
  `list_algorithms`/`search_algorithms`, `run_processing`, tâches asynchrones
  (`/api/submit`). Un arbre des couches web, une recherche de traitements avec
  formulaire généré depuis les définitions de paramètres, une table attributaire
  paginée sont réalistes. Ils sont réorganisables par CSS (grille, onglets, tiroir sur
  téléphone) et **accessibles** (DOM, clavier, RGAA).
- **Canevas** : deux choix.
  - **c1 — canevas QGIS** (noVNC, profil `carte_max`) : édition, accrochage, outils
    d'extensions, étiquettes, rendu exact. Recommandé.
  - **c2 — canevas web** (MapLibre/OpenLayers) : QGIS Server (`qgis-server` 3.44
    disponible dans le même dépôt) servant WMS/WMTS/OGC API sur le projet, ou un point
    de tuiles XYZ dans le pont (`QgsMapRendererParallelJob`, hors fil principal), ou
    export vectoriel via `maplibre_style_mapper.py` + `pmtiles_encoder.py` déjà
    présents dans le hub. Fluide sur mobile, mais consultation seulement :
    numérisation, outils cartographiques des extensions et rendu fidèle des styles
    complexes (règles, étiquettes, géométries générées) sont perdus ou dégradés. QGIS
    Server lit le fichier projet : il faut une synchronisation (l'autosave
    `/data/.autosave.qgz` existe) et +200 à +400 Mo par utilisateur.

| Critère | Évaluation |
|---|---|
| Fidélité fonctionnelle | c1 : totale (le panneau web peut toujours renvoyer vers le dock QGIS). c2 : partielle, consultation |
| Latence / bande passante | Panneaux : requêtes JSON légères. c2 : tuiles, cache navigateur |
| Charge serveur | c1 : quasi nulle (pont). c2 : + QGIS Server ou rendu de tuiles |
| Outils MCP | Excellente : panneaux et agent parlent le même langage (actions du pont) ; aucune coordonnée |
| Accessibilité | **La seule option qui l'améliore réellement** |
| Risques | Double source de vérité si l'utilisateur modifie aussi dans QGIS (signaux du projet à écouter) ; fonctions QGIS non couvertes par les panneaux |
| Effort | Arbre des couches ~1 sem. ; traitements ~2 sem. ; table ~1–2 sem. ; c2 2–4 sem. |

### (d) Autres pistes

- **Qt WebGL streaming** (`-platform webgl`, Qt 5.12–5.15) : pensé pour QtQuick, rendu
  des widgets médiocre, un seul client, absent de Qt 6 — écarté.
- **Qt VNC platform plugin** (`-platform vnc`) : un écran fixe, pas de gestionnaire de
  fenêtres, pas mieux que x11vnc — écarté.
- **Guacamole + xrdp** : RDP gère le redimensionnement dynamique, mais guacd + Java +
  nouveau proxy/authentification : lourd pour SSPCloud — écarté.
- **Selkies (WebRTC/GStreamer)** : très fluide, redimensionne par xrandr, mais encodage
  vidéo CPU (≈ 1 cœur par utilisateur sans GPU) — à garder en réserve si un pool GPU
  devient disponible.
- **Gain simple de surface** : fluxbox sans décor ni barre des tâches pour QGIS
  (`[Deco] {NONE}` dans `apps`, `session.screen0.toolbar.visible: false`) : environ
  45 px de hauteur rendus au canevas (mesuré : canevas à y = 44 en `carte_max`).
- **Échelle Qt à 1,0** (`Xvfb -dpi 96`) : pixels logiques = pixels X, interface plus
  nette, coordonnées de docks directement exploitables (repris dans le prototype sous
  le drapeau).

## Comparatif résumé

| | a1 Xvfb+xrandr | a2 Xvnc | b Xpra | b' multi-vues | c1 panneaux web + canevas QGIS | c2 canevas web |
|---|---|---|---|---|---|---|
| Fidélité | totale | totale | totale* | partielle | totale | consultation |
| Surface canevas | ++ | ++ | +++ | ++ | +++ | +++ |
| Lisible sur petit écran | + | + | ++ | + | +++ | +++ |
| Charge serveur | = | − | ++ | + | = | + |
| Outils MCP | adapter taille | adapter taille | captures à revoir | fragile | natif | natif |
| Accessibilité | = | = | = | = | ++ | ++ |
| Effort | ½ j + 3 j | 3–5 j | 3–6 sem. | 1–2 sem. | 4–5 sem. | 2–4 sem. |
| Risque | faible | faible-moyen | élevé | élevé | moyen | moyen |

\* sous réserve du placement des fenêtres transitoires.

## Recommandation et architecture cible

```
navigateur (desk.html)
 ├─ zone canevas : iframe noVNC (scale + écran à la taille de la zone)
 │     └─ QGIS Desktop, profil choisi selon la largeur (carte_max / compact / standard)
 ├─ panneaux web (DOM, DSFR) : Couches · Traitements · Attributs · (Styles)
 │     └─ hub → API workspace :8080 → pont PyQGIS (actions existantes + ui_layout)
 └─ bouton « Interface QGIS complète » → profil standard (tous les docks QGIS)
```

Règle de mise en page proposée (largeur de la zone canevas) :
< 1100 px → `carte_max` + panneaux web en tiroir ; 1100–1600 px → `compact` (couches
QGIS ou web à gauche) ; > 1600 px → `standard`. Toujours surchargeable par
l'utilisateur, mémorisé par étude.

## Étapes incrémentales

1. **Écran à la taille de la zone** (prototype prêt, BigQgisMCP `proto/qgis-panneaux`,
   commit `8ced1bf`) :
   - `src/ecran.py` : lecture de la taille X réelle, redimensionnement RandR borné
     (800×600 → maximum), calculs purs testés ;
   - `entrypoint.sh` : drapeau `QGIS_ECRAN_MAX` (ex. `3840x2160`) → Xvfb lancé au
     maximum avec `-dpi 96` puis ramené à `QGIS_RESOLUTION` avant QGIS. Sans drapeau :
     lancement inchangé ;
   - `src/api_server.py` : `GET /api/display` (taille, maximum, redimensionnable ?) et
     `POST /api/display {width,height}` (409 sans drapeau) ; bornage des clics sur la
     taille réelle ;
   - `src/stream_server.py` et capture de secours du pont : taille lue à chaque capture
     (corrige le 500 mesuré) ;
   - pont : action `ui_layout` (`etat`, `standard`, `compact`, `carte_max`), réversible
     (`saveState` mémorisé une fois, profils non empilés), renvoie le rectangle du
     canevas en pixels X.
   Vérifié de bout en bout dans une image dérivée locale : 1920×1080 → 1366×768 →
   2560×1300, flux MJPEG 200, échelle Qt 1,0, clic hors écran borné à 2559×1299,
   `carte_max` à 1366×768 : canevas 1366×699 (contre 1066×590 en `compact`).
   Tests : `tests/test_ecran.py` (10), suite hors conteneur 219 OK.
2. **Branchement bureau (qgis-sspcloud)**, derrière le même drapeau :
   - chart : variable `QGIS_ECRAN_MAX` dans `workspace-statefulset.yaml` et
     `sessions.py` (vide par défaut) ;
   - hub : `POST /workspace/display` relayé vers `:8080/api/display` (même contrôle
     propriétaire que `/workspace/vnc/*`), plus `POST /workspace/ui-layout` vers le pont ;
   - `desk.html` : si le workspace se déclare redimensionnable, le cadre remplit la zone
     (plus de `--qgis-ratio`), un `ResizeObserver` envoie la taille (anti-rebond
     400 ms, arrondi pair, ignoré en dessous de 800×600 : on garde alors la mise à
     l'échelle) et choisit le profil ; sélecteur de profil dans la barre du canevas ;
   - fluxbox sans décor ni barre pour QGIS (+45 px).
3. **Outils MCP** (BigQgisMCP) : exposer `ui_layout` et la taille d'écran ; descriptions
   de `mouse_click`/`mouse_drag`/`mouse_scroll` en « pixels de l'écran courant »
   (taille renvoyée par `ui_layout etat`) ; `get_screenshot` doit dire ce qu'il montre
   (carte seule ou bureau) et offrir un mode `bureau` pour viser un clic ; verrou
   court côté hub : pas de redimensionnement pendant une suite d'actions GUI de
   l'agent ; `qgis_app.html` : remplacer `DISPLAY_W = 1920` par la taille lue ;
   flux MJPEG : `scale=1280:-2` pour ne pas déformer un écran non 16:9.
4. **Premiers panneaux web** : Couches (liste, visibilité, ordre, zoom), puis
   Traitements (recherche + formulaire + suivi de tâche), puis table attributaire
   paginée. Synchronisation par sondage léger puis par signaux du projet
   (`layersAdded`, `layerRemoved`, `visibilityChanged`) poussés par le pont.
5. **Option** : canevas web MapLibre en lecture pour téléphone/tablette et partage ;
   **option** : bascule Xvnc (a2) si la charge x11vnc devient visible.

## Impacts

- **Outils MCP** : les actions du pont (majorité des outils) ne changent pas. Les
  outils GUI (`mouse_*`, `key_press`) restent en pixels X, mais la taille n'est plus
  constante : l'agent doit la lire (`ui_layout etat` → `canevas_px`) plutôt que
  supposer 1920×1080. Le défaut existant (capture « carte seule » contre clics en
  pixels bureau) est à traiter à l'étape 3.
- **Bureau** : fin des bandes et du cadre au ratio figé quand le drapeau est actif ;
  le code `suivreRatioBureauQgis` reste le repli si le workspace n'est pas
  redimensionnable. Tests hub concernés : `test_desk_panel_modes.py`,
  `test_workspace_ux_compact.py`, `test_desk_layout_defaults.py`.
- **Charge SSPCloud** : étapes 1–4 sans hausse notable (mesuré ~0,6 Gio au repos,
  limites du chart 4 Gio / 2 CPU inchangées) ; QGIS Server (option c2) +0,2 à 0,4 Gio.

## Critères de validation

1. Aucune bande : pour 1366×768, 1536×864, 1920×1080, 2560×1440 et une zone
   non 16:9, l'écran X égale la zone à 2 px près et le canevas noVNC la remplit.
2. Surface canevas à 1366×768 : ≥ 85 % de la zone en `carte_max` (mesuré 91 % avant
   retrait du décor fluxbox), ≥ 55 % en `compact`.
3. Réversibilité : `standard` après n'importe quelle suite de profils restitue docks
   et barres à l'identique (comparaison `saveState`).
4. Outils MCP : `mouse_click` au centre de `canevas_px` touche le canevas à toute
   taille ; flux `/frame` en 200 après chaque changement ; `get_screenshot` sans
   erreur.
5. Sans drapeau : comportement actuel strictement inchangé (Xvfb à
   `QGIS_RESOLUTION`, pas de `-dpi`, `POST /api/display` en 409).
6. Redimensionnement : < 1 s entre l'arrêt du glissement de la poignée et l'image
   recadrée ; aucun plantage QGIS sur 50 changements successifs.
7. Téléphone (< 800 px) : repli en mise à l'échelle, panneaux web en tiroir, carte
   utilisable.

## Risques et parades

- **Course agent / utilisateur** : un redimensionnement entre deux clics décale la
  cible → verrou court côté hub, et l'agent relit la taille avant une suite de clics.
- **Dialogues QGIS** centrés sur l'ancienne taille : fluxbox les replace à
  l'ouverture ; à vérifier sur la mise en page et les extensions.
- **Maximum d'Xvfb** : impossible de dépasser `QGIS_ECRAN_MAX` sans redémarrer ;
  3840×2160 couvre les écrans courants (le bureau borne au maximum annoncé).
- **Échelle Qt 1,0 sous drapeau** : interface environ 4 % plus petite qu'aujourd'hui ;
  à faire valider par l'utilisateur (ou garder `-dpi` par défaut hors drapeau).
- **Panneaux web vs QGIS** : deux interfaces sur le même projet → le pont est la
  source de vérité, les panneaux se rafraîchissent sur ses signaux.

## Sources

- [TigerVNC / Xvnc : seul serveur VNC gérant multi-écran et redimensionnement dynamique (wiki xrdp)](https://github.com/neutrinolabs/xrdp/wiki/Xvnc-backend-:-Multi-monitor-and-resize-support)
- [KasmVNC — fonctions côté client (redimensionnement distant, multi-écran)](https://kasmweb.com/kasmvnc/docs/master/clientside.html)
- [xpra-html5 — client HTML5, sessions seamless](https://github.com/Xpra-org/xpra-html5)
- [Xpra — documentation des clients](https://xpra-org.github.io/xpra/Usage/Clients.html)
- [Ajout de RandR à Xvfb (xorg-devel, 2015)](https://lists.x.org/archives/xorg-devel/2015-June/046670.html)
- [x11vnc (Wikipédia)](https://en.wikipedia.org/wiki/X11vnc)
