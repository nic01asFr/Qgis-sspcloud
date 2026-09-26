# Inventaire du non implémenté, non branché, cassé ou sous-optimal — 2026-09-26

Équipe T3, sous supervision de l'agent principal. Document de constat : aucune
modification de code n'accompagne cet inventaire.

**Base examinée.** `qgis-sspcloud` origin/main `5a621f2` (fusion PR #9, lot
qualité 3) et `BigQgisMCP` (workspace MCP, noté « WS ») origin/main `2f36a8a`.
Les chemins sont relatifs à la racine de chaque dépôt ; le préfixe `WS:` désigne
le workspace.

**Méthode.** Lecture des docs (ARCHITECTURE*, CHARTE_AGENT, RELEASE_NOTES_V1,
OPS, specs `docs/superpowers/specs/*`, plans) et des marqueurs « ⏳ », V2/V3,
TODO, backlog, puis croisement par grep et lecture du code :

- routes FastAPI face aux appels de l'UI et de l'agent ;
- outils MCP déclarés, dispatchés et implémentés dans le pont ;
- profils YAML face aux outils réels ;
- variables d'environnement lues face aux valeurs posées par le chart ;
- `skip` et `xfail` des tests ;
- chart Helm, Dockerfiles et CI.

Chaque affirmation cite un `fichier:ligne` lu sur origin/main. Ce qui dépend du
live (overlay posé à la main, isolation réseau de SSPCloud, bibliothèques de
l'image de base) est marqué « à vérifier ».

**Légende.**

- État : **absent** (prévu, aucun code) ; **non branché** (code présent, jamais
  appelé ni déclenché) ; **cassé** (branché, mais le résultat est faux ou
  l'appel échoue) ; **sous-optimal** (fonctionne, avec un défaut mesurable) ;
  **doc périmée**.
- Effort : S ≤ 1 j ; M de 2 à 4 j ; L ≥ 1 semaine.
- Priorité : P0 = fiabilité des données, sécurité ou release ; P1 = défaut
  visible ou dette qui fausse la mesure ; P2 = confort, nettoyage.
- Chantiers en cours : T1 panneaux du bureau, T2 QGIS en panneaux, T4 Atlas,
  T5 corpus documentaire, T6 livrables Grist, T7 agents dédiés. Un item marqué
  « → Tn » relève de ce chantier : il n'est pas redécrit, seules ses dépendances
  sont données.

## Les 15 priorités

| # | Item | Où | État | Prio | Effort | Équipe |
|---|------|----|------|------|--------|--------|
| 1 | Une clé scopée (`qgisk_`) donne l'identité complète du propriétaire sur toutes les routes REST inter-pod : `/publish`, `/studies`, `/admin`… Seul `/mcp` filtre les outils. (SEC-1) | `hub/hub/auth.py:1381-1390`, `:1067-1069` ; seul refus : `hub/hub/main.py:5400` | cassé | P0 | M | sécurité |
| 2 | Workspace : `/api/execute` (Python arbitraire) sans authentification, CORS `*`, VNC `-nopw`, aucune NetworkPolicy alors que `security.networkPolicy.enabled: true` (SEC-2, EXP-4) | `WS:src/api_server.py:57-62`, `:596-604` ; `WS:supervisord.conf:55` ; `charts/qgis-hub/values.yaml:82-92` | non branché | P0 | M | exploitation + T2 |
| 3 | Recette lancée depuis la galerie : l'exécuteur `stub` produit des couches `stub://`, et le chat n'affiche aucun des événements SSE nommés (REC-1) | `agent/agent/recipe_executor_mute.py:51-58`, `:146-153` ; `hub/hub/main.py:2452` ; `agent/templates/chat.html:2392` | cassé | P0 | S-M | agent + hub |
| 4 | Le live sert des overlays PYTHONPATH qui divergent de l'image ; l'écart n'est visible que dans `/health` (EXP-1) | `hub/hub/empreinte_code.py:3-8`, `:137-157` ; `hub/hub/version.py` ; plan bilan-chantiers l.113 | sous-optimal | P0 | S / M | exploitation |
| 5 | Secrets S3 et Vault en `value:` dans le spec du pod ; les variables Vault ne sont lues par aucun code (SEC-4) | `charts/qgis-hub/templates/statefulset.yaml:95-116` | sous-optimal | P0 | M | exploitation |
| 6 | Signature d'intégrité des bundles publiés faite avec une clé de démo publique (`bytes(range(32))`), faute de secret dans le chart (LIV-3) | `hub/hub/publish/bundle.py:48-50`, `:53-87` ; `CEREMA_ED25519_PRIVATE_KEY` absent de `charts/` et `deploy/` | cassé | P0 | S | hub + exploitation |
| 7 | La CI pousse `:latest` et `:main` avant le smoke test d'import (EXP-5) | `.github/workflows/build.yml:83-111`, `:129-151` | sous-optimal | P0 | S | exploitation |
| 8 | Profil à `allowed: []` : le filtre est sauté, le chat reçoit les 49 outils MCP (dont `execute_python`, `delete_file`) et aucun de ses outils natifs (AG-14) | `agent/agent/qgis_agent.py:350-354` face à `:396` ; `hub/hub/profiles/component_assist.yaml:35-36` + 3 profils | cassé | P1 | S | agent |
| 9 | Périmètre de données des clés scopées (`sid`, `pid`, `data`) stocké mais jamais appliqué (SEC-3) | `hub/hub/main.py:5367` ; `hub/tests/test_scope_donnees_non_applique.py:1-20` | absent | P1 | L | sécurité → T7 |
| 10 | `/agent-share/{key_short}` : URL générée et publiée, aucune route ne la sert (HUB-1) | `hub/hub/main.py:5505-5576` | cassé | P1 | M | hub → T7 |
| 11 | Le retour arrière supprime les messages en base mais pas dans l'index sémantique : l'agent se « souvient » d'actions annulées (MEM-2) | `agent/agent/memory.py:674-697` ; `agent/agent/vector_store.py:290` | cassé | P1 | S | agent |
| 12 | `produit.css` copié hors du dossier servi : le chat en accès direct n'a aucun style (EXP-2) | `Dockerfile.agent:13` face à `agent/agent/main.py:135` | cassé | P1 | S | exploitation |
| 13 | Lieu homonyme choisi en silence (`limit: 1`), sans `resolve_place` ni retour `ambigu` (AG-1, AG-2) | `agent/agent/enrichers/reference_data.py:44` ; spec sous-agents L1 l.392 | absent | P1 | M | agent + workspace |
| 14 | Réponse perdue si le client se déconnecte ; le bureau recharge la page pendant un tour (AG-9, UI-2) | `agent/agent/qgis_agent.py:3897` ; `agent/agent/main.py:1244-1264` ; `hub/templates/desk.html:4252`, `:4308` | cassé | P1 | M | agent + T1 |
| 15 | Prompts de 11 profils sur 13 ignorés : `PROFILS_AVEC_PROMPT` vaut `standard,guided_tour` et aucun chart ne le définit (AG-15) | `agent/agent/qgis_agent.py:318-335` | non branché | P1 | S / M | agent → T7 |

Juste après ce tableau viennent :

- AG-7 : pas de vérificateur de chiffres en ligne ;
- AG-4 : `_QGIS_ESSENTIALS` monolithique ;
- EXP-3 : bundle blocknote non reproductible ;
- HUB-5 : `ADMIN_TOKEN` jamais défini ;
- REC-2 : `cadastre_solaire` cassée ;
- MEM-5 : tips jamais indexés.

## Nombre d'items par domaine

| Domaine | Items | Dont P0 | Dont P1 |
|---------|-------|---------|---------|
| Agent | 18 | 0 | 12 |
| Hub / API | 7 | 1 | 2 |
| Bureau / UI | 11 | 0 | 2 |
| Workspace / outils | 8 | 0 | 5 |
| Livrables / publication | 9 | 1 | 2 |
| Mémoire | 6 | 0 | 3 |
| Recettes | 6 | 1 | 2 |
| Sécurité / accès | 7 | 3 | 2 |
| Exploitation / déploiement | 11 | 3 | 6 |
| Documentation périmée | 5 | 0 | 1 |
| **Total** | **88** | **9** | **37** |

Le décompte des P0 compte chaque ID séparément : dans le top 15, l'item 2 regroupe
SEC-2 et EXP-4, et l'item 4 regroupe EXP-1 et HUB-7.

---

## 1. Agent

État des lots de la spec `2026-09-24-sous-agents-et-contexte-dynamique.md`
(l.391-399) :

| Lot | État | Preuve |
|-----|------|--------|
| L0 Mesure | livré en grande partie ; le journal de tour rejouable manque (AG-8) | `agent/agent/context_budget.py:58-90` ; `agent/agent/tool_result_budget.py:8`, `:44` ; appel en `agent/agent/qgis_agent.py:3561` |
| L1 Lieu sans homonymie | absent | AG-1, AG-2 |
| L2 Façade de chargement | partiel | `clip_to_study_zone` existe (`WS:main_mcp.py:681`) ; `load_from_catalog` et `count_in_zone` n'existent dans aucun des deux dépôts ; `smart_load` et `add_from_catalog` restent exposés (`agent/agent/paquets_outils.py:118`) |
| L3 tools/list dynamique | partiel | paquets, filet `demander_outils`, invalidation dans le tour (`agent/agent/qgis_agent.py:3180-3207`, `:3430-3440`) ; `construire_outils` et `listChanged` absents du workspace ; AG-4 |
| L4 Données de référence | absent | `query_reference` introuvable ; pas de pied « Sources » (AG-7) |
| L5 Fiche d'étude | absent | aucune occurrence de `fiche_etude` ; pas de colonne `focus` (`hub/hub/studies.py:205-216`) |
| L6 Protocole de sous-agent | absent → T7 | aucune occurrence de `SousAgent` ; le flux n'émet que des `phase` (`agent/agent/qgis_agent.py:2822`, `:2856`) |
| L7 `consulter_documents` | absent → T5 | `agent/agent/embed_worker.py:6`, `:263` n'indexe que messages, insights et `memory_doc` |
| L8 `interroger_base` | absent → T7 | aucune occurrence ; pas de transaction `READ ONLY` |

| ID | Item | Où | État | Impact | Effort | Prio | Dépendances / équipe |
|----|------|----|------|--------|--------|------|----------------------|
| AG-1 | L'enricher de référence garde le premier résultat (`limit: 1`, `boost: population`) sans signaler d'homonymie | `agent/agent/enrichers/reference_data.py:44` | cassé | une commune homonyme entre dans le contexte comme vérité | S | P1 | aucune ; agent |
| AG-2 | `set_study_zone` qui renvoie `ambigu` avec des candidats, et boutons de choix dans le chat | spec sous-agents l.243-245, l.292-294 ; seule une liste fixe existe, `_AMBIGUOUS_MAJOR_CITIES` (`agent/agent/qgis_agent.py:1962`) | absent | choix silencieux (Saint-Martin) | M | P1 | AG-1 ; workspace (`resolve_place`) |
| AG-3 | Façade `load_from_catalog(id, purpose)` et `count_in_zone` | spec L2 l.393 | absent | un compte calculé sur la bbox est présenté comme communal (défaut T4 du plan spatial) | L | P1 | workspace ; AG-2 ; WS-6 |
| AG-4 | `_QGIS_ESSENTIALS` monolithique, jamais découpé en paquets ; `etat_version` lu par l'agent mais produit par personne (`construire_outils` absent) | `agent/agent/qgis_agent.py:2043-2529`, injecté en `:2648` ; plafond « qui fige l'existant » en `agent/agent/context_budget.py:69-71` ; `agent/agent/qgis_agent.py:1571-1575` | sous-optimal | environ 7 000 jetons fixes à chaque itération | M | P1 | `paquets_outils` (livré) ; workspace pour `etat_version` |
| AG-5 | Paquet `interface` (souris, clavier) déclenché par « bouton », « menu », « panneau », alors que la spec l'exclut pour l'agent interne | `agent/agent/paquets_outils.py:202-206` ; spec sous-agents l.330 | sous-optimal | clics ou frappes sur une demande banale | S | P1 | → T2 |
| AG-6 | `_VISUAL_TOOLS` (capture d'écran en fin de tour) liste 14 noms inexistants (`load_layer`, `set_style`, `zoom_to_layer`, `geoai_detect`…) et omet `add_layer`, `add_from_catalog`, `set_layer_style` | `agent/agent/qgis_agent.py:3830-3846` | cassé | pas de capture après un changement de style ou un ajout de couche | S | P2 | aucune |
| AG-7 | Vérificateur de chiffres et d'URL absent en ligne, pas de pied « Sources » : ces contrôles n'existent que dans le banc | `evals/verifs/chiffres.py`, `evals/verifs/urls.py` ; `agent/agent/texte_modele.py:26` (« ne touche jamais aux chiffres ») ; spec L4 l.395 | absent | un chiffre inventé (« < 5 % ») peut revenir | M | P1 | provenance normalisée (AG-3) |
| AG-8 | Pas de journal de tour rejouable : ni arguments, ni résultats complets, ni empreinte du prompt | stratégie qualité §5.1 ; `agent/agent/context_budget.py:27` ; résultats tronqués à 200 caractères (`agent/agent/texte_modele.py:93`) | absent | un tour faux ne peut pas être diagnostiqué | M | P1 | aucune |
| AG-9 | La réponse n'est enregistrée qu'en fin de `chat_stream` ; aucun `finally` ne sauve le texte partiel | `agent/agent/qgis_agent.py:3897` ; `agent/agent/main.py:1244-1264` | cassé | une déconnexion perd la réponse | M | P1 | UI-2 |
| AG-10 | « Arrêter » attend la fin de l'outil (grâce `STOP_TOOL_GRACE_SEC`, 30 s par défaut) sans annulation franche | `agent/agent/qgis_agent.py:3335` ; spec ux-chat P2 point 18 (qui dit 60 s) ; cible < 2 s dans la spec sous-agents §2.5 | sous-optimal | jusqu'à 30 s d'attente | M | P2 | workspace (`cancel_job`) |
| AG-11 | Banc C : 12 scénarios sur les ~40 visés, pas de modèle juge, pas d'exécution planifiée | `evals/scenarios/` (12 YAML) ; `evals/README.md:10` ; aucun `schedule` dans `.github/workflows/*.yml` | sous-optimal | la régression de comportement n'est mesurée qu'à la main | M-L | P1 | clé LLM en CI |
| AG-12 | Lots L5 à L8 : fiche d'étude et `focus`, `SousAgent`, événements `etape`, `consulter_documents`, `interroger_base`, avec leurs instantanés de contexte | tableau des lots ci-dessus ; `agent/templates/chat.html:1006` ; `agent/tests/test_instantanes_contexte.py:196-304` ; route orpheline `POST /agent-context/new` (`hub/hub/main.py:11508`) | absent | — | L | P2 | → T5 (L7), → T7 (L6, L8) ; dépend de L0 à L4 |
| AG-13 | Fichiers annexes GDAL (`.aux.xml`, `.gpkg-shm`…) filtrés par une consigne du prompt, pas par l'outil | `agent/agent/qgis_agent.py:2271-2272` ; plan bilan-chantiers ligne P1 | sous-optimal | liste de fichiers bruitée quand le modèle oublie la consigne | S | P2 | `list_files` du workspace |
| AG-14 | `allowed: []` vaut « tous les outils MCP » pour le filtre MCP, mais « aucun » pour le filtre natif | `agent/agent/qgis_agent.py:350-354`, `:396` ; profils `component_assist.yaml:35-36`, `assembly_assist.yaml:34-35`, `recipe_analyzer.yaml:39-40`, `agent_config_analyzer.yaml:32-33` | cassé | persona d'assistance avec `execute_python` et `delete_file`, sans ses outils métier | S | P1 | AG-17 |
| AG-15 | `PROFILS_AVEC_PROMPT` (défaut `standard,guided_tour`) n'est défini ni dans le chart ni dans les Dockerfiles | `agent/agent/qgis_agent.py:318-335` | non branché | storymap_creator, risk_analyst, db_analyst, map_composer… ne suivent pas leur YAML | S (valeur) / M (essais par profil) | P1 | → T7 |
| AG-16 | Deux outils `get_recipe` dans la même liste : le natif (`slug`) s'ajoute au MCP (`id`) sans dédoublonnage, et le dispatch devine d'après la présence de `slug` | `agent/agent/qgis_agent.py:1168`, `:1650-1660`, `:805-807` ; `WS:main_mcp.py:844-854` | cassé | ambiguïté pour le modèle ; rejet possible par un serveur de modèles strict | S | P1 | aucune |
| AG-17 | Exposition des outils incohérente avec les profils : les 5 outils recettes sont exposés en bloc dès que `save_recipe` est autorisé (`delete_recipe` compris), les outils mémoire sans condition, et `_PAQUETS_PAR_PROFIL` reste inerte dès qu'une liste blanche existe | `agent/agent/qgis_agent.py:1653`, `:1659-1660`, `:1698-1699` ; `hub/hub/profiles/storymap_creator_v15.yaml:88-90` ; `agent/agent/paquets_outils.py:235-239` | sous-optimal | outil destructif hors liste blanche | S | P2 | AG-14 |
| AG-18 | Outils `cmp_*`, `asy_*` et `stu_*` jamais exposés au modèle : ils ne sont joignables que par les puces d'interface. Cinq fonctions `cmp_*` de l'agent (~240 lignes) n'ont aucun appelant. Des profils citent des outils qui n'existent pas (`cmp_list_available_layers`, `cmp_suggest_classification`, `cmp_propose_layers_for_topic`, `list_basemaps`) | `agent/agent/native_tools_v2.py:2340-2585`, `:2611-2614` ; `hub/hub/actions/agent_brick.py:17` (« reporté V2.5 ») ; `hub/hub/profiles/component_assist.yaml:46-67` | absent / non branché | les profils `component_assist` et `assembly_assist` ne peuvent rien faire en chat | M | P2 | → T7 |

## 2. Hub / API

Le hub compte 192 décorateurs de route dans `hub/hub/main.py` (158 chemins
distincts), sans aucun `APIRouter`. L'agent en compte 44 dans
`agent/agent/main.py`. Croisement avec les appelants (UI, agent, hub vers
lui-même, infra, tests) :

- hub : 28 routes sans aucun appelant, 17 appelées seulement par des tests ou
  les evals ;
- agent : 16 sans appelant, 3 appelées seulement par des tests ;
- aucun appel de l'UI vers une route absente, aucun `onclick` vers un handler
  inexistant.

Une partie des routes orphelines est légitimement externe (OAuth, `robots.txt`,
`/.well-known/*`). Les cas significatifs sont listés ci-dessous ; les routes
mémoire sont en MEM-1.

| ID | Item | Où | État | Impact | Effort | Prio | Dépendances / équipe |
|----|------|----|------|--------|--------|------|----------------------|
| HUB-1 | `/agent-share/{key_short}` : URL générée par la publication d'agent, sans route. `key_short` correspond aux 12 premiers caractères après `qgisk_`, donc surtout au nom d'utilisateur : il n'est pas unique | `hub/hub/main.py:5505-5509`, `:5573-5576` ; `hub/hub/auth.py:608` | cassé | lien « Widget » en 404 | M | P1 | SEC-1, SEC-5 ; → T7 |
| HUB-2 | `/internal/compact-deltas` : la compaction des deltas d'assemblage n'est déclenchée par rien (aucun CronJob dans le chart, aucun appelant) | `hub/hub/main.py:12022` ; `hub/hub/studies.py:443-449` | non branché | la table d'historique des assemblages grossit sans borne | S | P2 | exploitation |
| HUB-3 | Erreurs du bundle blocknote envoyées dans un tampon RAM de 100 entrées que ni UI ni alerte ne lit ; pas de `/metrics` (Prometheus « backlog ») | `hub/hub/main.py:5177`, `:5218` ; `blocknote-editor/src/main.tsx:38` ; `ARCHITECTURE.md:317` ; `hub/hub/main.py:168` | sous-optimal | erreurs perdues au redémarrage, aucune observation de charge | M | P2 | exploitation |
| HUB-4 | API en double ou morte. Aucun appelant pour `GET/PATCH/DELETE /studies/{sid}/projects/{pid}` et `.../activate` (l'UI passe par `/workspace/project/...`), `components/{cid}/render_legacy` (~120 lignes), `/sessions/{id}/novnc_url`, `/schemas/*`, `scene_manifest/history`. `POST .../scene_manifest/build` n'est cité que dans le prompt du profil v15, et aucun outil ne l'appelle. `/api/recipes-web/list` et `/gallery`, `assist/action-stream`, `export-bundle` et `publish-pdf` ne sont appelés que par les tests. Côté agent, 16 routes n'ont aucun appelant (`/projects`, `/recipes`, `/profiles`, `/memory/context`…) | `hub/hub/main.py:4491-4559`, `:6621`, `:11092`, `:5108-5118`, `:4711`, `:4884`, `:2700`, `:2756`, `:6253`, `:9272`, `:9406` ; `hub/hub/profiles/storymap_creator_v15.yaml:377` ; `agent/agent/main.py:1539-1701` | non branché | surface d'API à maintenir et à sécuriser pour rien ; le prompt décrit un endpoint inatteignable | M | P2 | REC-4 ; SEC-1 (surface exposée aux clés scopées) |
| HUB-5 | `ADMIN_TOKEN` et `ADMIN_USERS` jamais définis dans le chart : `/admin/agent-config`, `/admin/workspace-info` et `/admin/workspace-fix-image` répondent 500, personne n'a le rôle admin. `/studies/reconciliation` et `/admin/recipe-analyses/*` n'ont pas d'UI | `hub/hub/main.py:12108-12115`, `:12234`, `:12321` ; `hub/hub/auth.py:43-44` ; `hub/hub/main.py:4212`, `:5942` | cassé | outils d'exploitation inutilisables | S | P1 | secret Helm |
| HUB-6 | `hub/hub/main.py` fait 14 264 lignes ; le découpage annoncé n'est pas fait | `docs/ARCHITECTURE_AGENT.md:697` (qui annonce « 3473 L ») | sous-optimal | revues et fusions coûteuses | L | P2 | tous chantiers |
| HUB-7 | `/version` ignore l'alignement image/overlay calculé par `empreinte_code` ; `/desk/mise-a-jour` propose un redémarrage qui recharge le même overlay | `hub/hub/version.py` ; `hub/hub/main.py:2185-2251`, `:2259-2273` | sous-optimal | l'utilisateur se croit à jour | S | P0 | EXP-1 |

## 3. Bureau / UI

| ID | Item | Où | État | Impact | Effort | Prio | Dépendances / équipe |
|----|------|----|------|--------|--------|------|----------------------|
| UI-1 | Code mort : CSS `.mem-*` (71 lignes) et environ 760 lignes de panneaux jamais affichés (composants, assemblages, exports, agents), encore rendus avec leur jargon (« feature flag », « audit_chain », `alert()`). `exportGristDoc`, seul appelant UI de `POST .../export_grist`, vit dans le panneau `exports` caché : l'export Grist est inatteignable depuis l'interface | `hub/templates/desk.html:419-489`, `:926-929`, `:1312-1554`, `:1555-1727`, `:1728-1953` (`exportGristDoc` en `:1854-1867`), `:2016-2132` | non branché | gabarit alourdi ; textes lus par les lecteurs d'écran ; fonction Grist perdue | M | P2 | → T1 (non redécrit) ; → T6 pour l'export Grist |
| UI-2 | Deux `location.reload()` sans condition sur un tour en cours | `hub/templates/desk.html:4252`, `:4308` ; `qgis_agent_turn_done` n'est écouté que pour le catalogue (`:4459-4465`) | cassé | le tour en cours est perdu | S | P1 | AG-9 ; → T1 |
| UI-3 | Demandes rapides copiées dans le presse-papier au lieu d'être envoyées au chat | `hub/templates/desk.html:1529`, `:1257-1335` ; un contrat `postMessage` existe déjà (`:2953`) | absent | collage manuel | S | P2 | → T1 |
| UI-4 | Pages `/login`, `/onboarding` et `/published` générées en ligne, avec des classes absentes de `produit.css` (`qs-texte-discret`, `qs-btn--primaire` : 0 occurrence) | `hub/hub/main.py:1831`, `:1939-1944` | cassé | styles non appliqués sur les pages d'entrée | M | P2 | UI-9 |
| UI-5 | Historique du chat : non filtré par étude, non rafraîchi sans rechargement, limité à 50 messages sans l'indiquer | `agent/agent/main.py:1330-1337` ; `agent/agent/memory.py:591` | sous-optimal | conversations mélangées ; début de conversation invisible | S | P2 | agent |
| UI-6 | Vocabulaire technique « ✓ résultat tool » et « ↶ Revenir avant » | `agent/templates/chat.html:1739`, `:1752`, `:1900`, `:1917` | sous-optimal | incompréhensible pour le public visé | S | P2 | décision de l'équipe agent |
| UI-7 | Compteur de livrables qui inclut les fichiers techniques repliés | `hub/templates/desk.html:942`, `:2234` ; `hub/templates/workspace.html:418` | sous-optimal | badge « 4 » pour 3 lignes visibles | S | P2 | décision produit ; → T1 |
| UI-8 | Recherche, filtre et tri des livrables | spec ux-ressources P2 point 17 ; aucun champ de recherche dans `hub/templates/workspace.html` | absent | liste inutilisable au-delà d'une dizaine d'éléments | M | P2 | → T6 |
| UI-10 | Deux boutons inatteignables. « Recadrer la carte » est dans le panneau `layers` et « ＋ Nouvelle recette » dans le panneau `recipes`, mais `_activerOnglet` n'active que les deux onglets restants (`sources`, `livrables`). Quatre écouteurs visent des onglets supprimés | `hub/templates/desk.html:1285-1292`, `:2004-2006`, `:931`, `:936`, `:3050-3063`, `:1413`, `:1679`, `:2128`, `:4183` ; route `POST /desk/recadrer` (`hub/hub/main.py:14175`) | cassé | plus de filet contre la carte blanche ; impossible de créer une recette depuis l'UI | S | P1 | → T1 |
| UI-11 | Chat, signaux cassés. (1) Le bureau poste `/agent/context/render/{sid}` avec un identifiant `desk_xxxx` que le chat n'utilise pas : une requête inutile par clic, alors que la voie qui fonctionne est le `postMessage` `qgis_set_render`. (2) `updateContextChip` cherche `#ctx-label` et `#ctx-icon`, absents du DOM. (3) Le lien « /workspace » est codé sans `hub_url` et donne 404 en accès direct. (4) `produit.js` n'est pas chargé par le chat, qui réimplémente son toast | `hub/templates/desk.html:3017-3035`, `:4324-4328`, `:2912` ; `agent/agent/main.py:1292` ; `agent/templates/chat.html:1362-1375`, `:989`, `:780` | cassé | faible pour l'utilisateur ; dette et bruit réseau | S | P2 | → T1 |
| UI-9 | Finitions : pas de thème sombre (`produit.css` sans `prefers-color-scheme`), modale « Mon compte » sans piège à focus, fil d'Ariane sans segment « Mon espace » | spec ux-chat P2 point 17 ; spec ux-ressources P2 points 20 et 22 ; `hub/templates/desk.html:197`, `:811` | absent | accessibilité, cohérence | M | P2 | → T1 |

## 4. Workspace / outils

Vérifications croisées dans WS :

- les 52 noms déclarés dans `WS:main_mcp.py` se répartissent en 49 outils,
  tous dispatchés (table en `WS:main_mcp.py:2120-2169`), et 3 prompts ;
- les 47 commandes envoyées au pont ont chacune un handler `_action_*` dans
  `WS:src/qgis_bridge.py` ;
- les 28 outils natifs de l'agent appellent des routes hub qui existent toutes ;
- les noms de `agent/agent/paquets_outils.py` existent tous. Les écarts de
  profils sont traités en AG-18.

| ID | Item | Où | État | Impact | Effort | Prio | Dépendances / équipe |
|----|------|----|------|--------|--------|------|----------------------|
| WS-1 | La ressource `skill://file-exchange` prescrit `bridge_put` / `bridge_get` (« pending implementation »), deux outils qui n'existent pas | `WS:main_mcp.py:914` ; `WS:skills/file_exchange.md` ; `WS:README.md:301` | absent | le modèle est orienté vers une API inexistante | S (retirer) / L (implémenter) | P1 | workspace |
| WS-2 | `publish_artifact` : l'enum `kind` n'a pas `features`, et l'outil ne transmet pas l'audience (toujours `cerema_internal`) alors que sa description promet un lien public | `WS:main_mcp.py:883`, `:887`, `:1793-1812` ; plan bilan-chantiers l.29 | absent | publication de couche seule impossible ; lien « public » annoncé à tort | S | P1 | → T6 |
| WS-3 | Code non exposé ou en double : handlers `apply_style` (pourtant listé dans `_MUTATING_ACTIONS`), `export_grist_from_html`, `export_image`, `list_layers` (REST seul) ; `_tool_restart_qgis_engine` défini deux fois | `WS:src/qgis_bridge.py:247-255` ; `WS:src/api_server.py:571-572` ; `WS:main_mcp.py:1694`, `:1732` | non branché | code mort, ou fonctions utiles non exposées | S | P2 | → T6 pour `export_grist_from_html` |
| WS-4 | Rendu GPU neutralisé : `entrypoint.sh` retire `LIBGL_ALWAYS_SOFTWARE` quand un GPU est présent, mais supervisord le force à 1 | `WS:entrypoint.sh:48` face à `WS:supervisord.conf:32` | cassé (sans effet aujourd'hui) | GPU inutilisé le jour où il est fourni | S | P2 | → T2 |
| WS-5 | Tests conteneur (PyQGIS, GRASS) exclus de la CI (`-m "not container"`) | `WS:tests/test_clip_zone_etude.py:360-406` ; `WS:tests/test_processing_providers.py:170-175` | non branché | `clip_to_study_zone` jamais testé sur un vrai QGIS | M | P1 | image de test QGIS en CI |
| WS-6 | Bloc `verification` limité à `clip_to_study_zone` et `smart_load`, pas étendu à `run_processing` ni `execute_python` | stratégie qualité P0 point 3 ; `WS:src/qgis_bridge.py:6446`, `:6501` | partiel | un traitement Processing n'est pas contrôlé | M | P1 | AG-3 |
| WS-7 | CI du workspace dépendante de `secrets.GHCR_PAT` ; le jeton par défaut échoue | `WS:.github/workflows/build.yml:131-145` | sous-optimal | images non publiées si le PAT expire | S | P1 | exploitation |
| WS-8 | `Dockerfile.workspace` (QGIS 3.34, « publiée manuellement ») en doublon, divergent de `WS:Dockerfile` ; job CI commenté | `Dockerfile.workspace:5-16` ; `.github/workflows/build.yml:153-160` | doc périmée / doublon | confusion sur l'image réelle | S | P2 | exploitation |

## 5. Livrables / publication

| ID | Item | Où | État | Impact | Effort | Prio | Dépendances / équipe |
|----|------|----|------|--------|--------|------|----------------------|
| LIV-1 | D4 et D9 : `scene_3d` et `media_embed` tombent en placeholder. Un seul AssemblyKind sur cinq est rendu : `dashboard`, `sheet_a4`, `modal_embed` et `atlas_immersive` renvoient 501 | `hub/tests/test_drifts_modele_blocks.py:95`, `:135` (xfail) ; `hub/hub/models/assembly.py:32-38` ; `hub/hub/main.py:8093`, `:8146-8155`, `:5244-5261` | absent | — | L | P1 | → T4 (non redécrit) |
| LIV-2 | D7 : pas de kind `recipe_output`, donc une storymap ne se rejoue pas sur une scène plus récente. D6 : deux H2 voisins fragmentent une section | `hub/tests/test_drifts_modele_blocks.py:110`, `:125` (xfail) ; `docs/blocks-and-deliverables-model.md:547` | absent / cassé | republication obligatoire via l'agent ; structure du livrable altérée | L | P2 | REC-4 ; → T6 |
| LIV-3 | Signature d'intégrité des bundles avec la clé de démo `bytes(range(32))` dès que `CEREMA_ED25519_PRIVATE_KEY` manque ; le chart ne la définit pas | `hub/hub/publish/bundle.py:48-50`, `:53-87` | cassé | signatures falsifiables : l'auditabilité est nulle | S | P0 | secret Helm |
| LIV-4 | Pas d'expiration des publications ; le script de re-ACL des anciens objets en `public-read` est annoncé mais n'existe pas | `hub/hub/s3_publication.py:771`, `:784-787` | absent | des livrables historiques restent lisibles directement sur MinIO | M | P1 | SEC |
| LIV-5 | `POST /publish/{kind}/{slug}` : `KeyError` (500) pour 4 kinds acceptés (`assembly`, `features`, `features_pmtiles`, `component`) sans `source`. `DELETE` ignore le résultat de `s3_publication.delete`, ne passe pas par `_safe_slug` et ne notifie pas le journal de l'agent | `hub/hub/main.py:10929-10949`, `:11020-11031` ; `hub/hub/s3_publication.py:44-50` ; `agent/agent/memory.py:1019` | cassé | erreur 500 ; dépublication annoncée réussie à tort ; contexte agent faux | S | P2 | aucune |
| LIV-6 | Le garde-fou `ALREADY_PUBLISHED` n'existe que côté agent : l'API du hub écrase en silence un slug existant, audience et ACL comprises | `agent/agent/native_tools_v2.py:446` ; `agent/agent/qgis_agent.py:793` ; `hub/hub/s3_publication.py:768-800` | sous-optimal | le contenu d'un lien partagé change | S-M | P2 | aucune |
| LIV-7 | Export PDF : `weasyprint` exige Pango et Cairo, que `Dockerfile.hub` n'installe pas ; seule `ImportError` est attrapée, donc une `OSError` donne 500. Les cartes restent des placeholders en PDF | `hub/setup.py:26` ; `hub/hub/pdf.py:12-16` ; `hub/hub/main.py:9422-9428` | à vérifier (image de base) | PDF en échec ou sans carte | S | P2 | exploitation |
| LIV-8 | CORS `*` posé aussi sur des publications `cerema_internal` protégées par cookie, sans `Allow-Credentials` | `hub/hub/main.py:10620`, `:10731`, `:10830`, `:10685-10692` | sous-optimal (incohérent, pas une fuite) | accès cross-origin authentifié impossible | S | P2 | LIV-4 |
| LIV-9 | Les cinq xfail de dérive sont en `strict=False` : un correctif passerait en XPASS sans que personne le voie | `hub/tests/test_drifts_modele_blocks.py:95-151` | sous-optimal | écart refermé non détecté, doc non mise à jour | S | P2 | → T4 à la livraison |

## 6. Mémoire

| ID | Item | Où | État | Impact | Effort | Prio | Dépendances / équipe |
|----|------|----|------|--------|--------|------|----------------------|
| MEM-1 | Six routes `/desk/memory*` du hub sans appelant (le tiroir mémoire du chat parle directement à l'agent). `DELETE /desk/memory/preferences/{key}` écrit une chaîne vide au lieu de supprimer ; les deux `DELETE` renvoient toujours 204. L'UI de la spec est incomplète : pas de « recopier dans une section », pas de reset, pas de préférences | `hub/hub/main.py:14122-14172` ; `agent/templates/chat.html:1502-1622` ; spec `2026-09-10-blocknote-recipes-memory-ux.md:18-28` | non branché / cassé | surface d'API morte ; suppression annoncée à tort | S (purge) / M (UI) | P2 | UI-1 ; → T1 |
| MEM-2 | Le retour arrière (`truncate_messages_after`) supprime les messages sans purger `embed_chunks` ; `vector_store.purge` ne sait pas filtrer par `source_id` | `agent/agent/memory.py:674-697` ; `agent/agent/vector_store.py:290` ; rappel par `agent/agent/enrichers/memory_recall.py:146` | cassé | l'agent se « souvient » d'actions annulées | S | P1 | aucune |
| MEM-3 | Aucune rétention : ni purge des messages, sessions et embeddings, ni suppression de session ; `close_session` n'a aucun appelant | `agent/agent/main.py:439-448` (seuls les checkpoints sont purgés) ; `agent/agent/memory.py:556` | absent | la base grossit sans limite ; pas de droit à l'oubli par conversation | M | P1 | MEM-2 |
| MEM-4 | Embeddings sans repli : un seul appel HTTP à `LLM_BASE_URL/embeddings`, et le worker journalise l'exception toutes les 30 s sans backoff | `agent/agent/vector_store.py:40`, `:109-133` ; `agent/agent/embed_worker.py:236-240` | sous-optimal | le rappel sémantique meurt en silence si l'API tombe | M | P2 | aucune |
| MEM-5 | La base de tips `qgis_tip` n'est jamais indexée en production : `reindex_all` ne se lance qu'à la main (`python -m`) | `agent/agent/tips/indexer.py:105`, `:140` ; consommateur `agent/agent/qgis_agent.py:564-575` | non branché | l'aide aux erreurs d'outil ne se déclenche jamais sur un pod neuf | S | P1 | MEM-4 |
| MEM-6 | Extraction d'insights seulement aux multiples exacts de 6 messages utilisateur, jamais à la clôture | `agent/agent/qgis_agent.py:3905-3913` | sous-optimal | les sessions courtes ne produisent aucun insight | S | P2 | MEM-3 |

## 7. Recettes

| ID | Item | Où | État | Impact | Effort | Prio | Dépendances / équipe |
|----|------|----|------|--------|--------|------|----------------------|
| REC-1 | Chaîne « Lancer avec l'assistant » cassée de bout en bout. (1) L'agent appelle `/api/recipes-web/execute` sans `executor` ; le défaut `stub` fabrique des couches `stub://` (et même `executor=mcp` reste factice sans `USE_REAL_MCP`, jamais défini). (2) L'agent émet des événements SSE nommés (`event:`) que le parseur du chat ignore : narratif, erreur et fin de tour sont perdus. (3) `onRecipeDone` n'est jamais appelé, donc le bouton « Publier » ne s'affiche pas. (4) `publish_hint.endpoint` vaut `/publish/scene_manifest`, qui ne correspond à aucune route | `agent/agent/recipe_executor_mute.py:51-58`, `:146-153` ; `hub/hub/main.py:2452`, `:2527-2546` ; `hub/hub/recipes_web/qgis_executor.py:79-104`, `:421-424` ; `agent/templates/chat.html:2392` ; `hub/hub/static/recipe_stream.js:46`, `:163` ; `agent/agent/recipe_executor_mute.py:293-297` | cassé | un livrable bâti sur des données fictives, dans un chat qui semble muet | S-M | P0 | agent + hub |
| REC-2 | `cadastre_solaire` charge `ign_ortho`, absent du catalogue (qui n'a que `ign_ortho_wmts`, `ign_ortho_wms`, `ign_ortho_irc`) ; `smart_load` fait une correspondance exacte | `WS:recipes/cadastre_solaire.json:43` ; `WS:datasources.json:43`, `:52`, `:61` ; `WS:src/qgis_bridge.py:6468-6473` | cassé | recette en échec | S | P1 | REC-3 |
| REC-3 | Aucune des six recettes du workspace n'a de test d'exécution ni de validation statique ; les tests du pipeline solaire dépendent de `/data/solar/manifest.json` et sont ignorés en CI | `WS:recipes/*.json` ; `WS:tests/` ; `WS:tests/test_solar_pipeline.py:185-268` | absent | une recette cassée passe inaperçue (REC-2 en est la preuve) | S | P1 | WS-5 |
| REC-4 | Deux systèmes de recettes disjoints : 6 recettes MCP en JSON et un registre web YAML qui ne contient qu'un exemple canonique | `hub/hub/recipes_web/examples/` (1 fichier) ; `hub/hub/recipes_web/registry.py:45` | sous-optimal | galerie pauvre ; deux formats à maintenir | M | P2 | REC-3 |
| REC-5 | Bouton « Relancer » depuis « Mes recettes » | spec ux-ressources P2 point 18 ; `recipe_run_request` n'est émis que par la galerie (`hub/templates/desk.html:2921-2953`) | absent | l'utilisateur ne peut qu'éditer le YAML | S | P2 | REC-1 ; → T1 |
| REC-6 | Deux profils storymap coexistent. Le routage « storymap » et « pdf », les consignes `<switch_profile>` et l'UI pointent vers la v1 (ancien flux MinIO) ; la v15 (composants et assemblages) n'est atteinte que par `create_agent` | `agent/agent/main.py:64`, `:68`, `:70` ; `agent/agent/qgis_agent.py:1805`, `:1817-1818` ; `agent/templates/chat.html:1295`, `:1356` ; `hub/hub/profile_manager.py:37`, `:83-84` | sous-optimal | le flux moderne de livrables n'est pas proposé pour une storymap | M | P2 | AG-15 ; → T7 |

## 8. Sécurité / accès

| ID | Item | Où | État | Impact | Effort | Prio | Dépendances / équipe |
|----|------|----|------|--------|--------|------|----------------------|
| SEC-1 | Une clé scopée passe le middleware sur tous les préfixes inter-pod (`/studies`, `/publish`, `/admin`, `/internal`…), puis `get_current_user` renvoie l'utilisateur complet, avec le rôle admin s'il l'a. Seuls filtres : la liste blanche d'outils au proxy `/mcp` et le refus de minter une clé depuis une clé scopée. Le commentaire « chemin inerte en prod » est périmé depuis la livraison du mint | `hub/hub/auth.py:1381-1390`, `:1067-1069`, `:1258`, `:1290`, `:665` ; `hub/hub/main.py:5400` | cassé | une clé déléguée peut dépublier, révoquer d'autres clés, lire la mémoire, appeler `/admin` | M | P0 | aucune |
| SEC-2 | Workspace : `/api/execute` (Python arbitraire), `/api/files*` et `/api/upload` sans authentification, CORS `allow_origins=["*"]` ; le bearer MCP n'est vérifié qu'en `MULTI_USER_MODE`, à `false` dans le chart ; x11vnc `-nopw` | `WS:src/api_server.py:57-62`, `:596-604` ; `WS:main_mcp.py:2548-2558` ; `charts/qgis-hub/templates/workspace-statefulset.yaml:56-57` ; `WS:supervisord.conf:55` | non branché | tout pod du namespace peut exécuter du code dans QGIS (à vérifier : isolation réseau de SSPCloud) | M | P0 | EXP-4 ; → T2 |
| SEC-3 | Périmètre de données des clés scopées non appliqué (`sid`, `pid`, `data` stockés, jamais lus). Le data-binding était prévu avant le mint, qui a été livré en premier. Le « scope orthogonal » V2 (`AgentScope`, `derive_scope`) n'existe pas | `hub/hub/main.py:5367`, `:5418-5428` ; `hub/tests/test_scope_donnees_non_applique.py` ; `docs/agents-scopes-architecture.md:73-104` ; `docs/ARCHITECTURE_AGENT.md:78`, `:297` | absent (assumé, documenté) | un agent partagé agit sur l'étude active du pod | L | P1 | SEC-1 ; `activate_study` du workspace ; → T7 |
| SEC-4 | Secrets S3 et Vault en `value:` (et non en `secretKeyRef`) ; `VAULT_*`, `AWS_WORKING_DIRECTORY_PATH` et `KUBERNETES_POD_NAME` ne sont lus par aucun code | `charts/qgis-hub/templates/statefulset.yaml:54`, `:95-116` | sous-optimal | secrets lisibles par `kubectl get sts -o yaml` ; jeton Vault injecté pour rien | M | P0 | exploitation |
| SEC-5 | Clés scopées stockées en clair (le bearer sert de clé primaire), avec la clé superviseur recopiée dans chaque ligne (`parent_key`). La révocation de la clé parente ne révoque pas ses clés scopées. Pas d'échange jeton → clé (V2 n°4) | `hub/hub/auth.py:613-622`, `:632-695`, `:499-513` ; `hub/hub/main.py:5467` (« ne sera plus affichée en clair ») | sous-optimal | une fuite de la base expose la clé maître | M | P1 | HUB-1 |
| SEC-6 | Durcissement : comparaison de clé inter-pod non constante (`==`) côté hub et agent ; côté workspace multi-utilisateur, `JWT_SECRET` par défaut `dev-secret-change-in-prod`, mots de passe en `sha256` sans KDF, bearer invalide traité comme `anonymous` | `hub/hub/auth.py:1323` ; `agent/agent/main.py:232`, `:254` ; `WS:src/auth.py:28`, `:186`, `:195` ; `WS:main_mcp.py:2549-2558` | sous-optimal | faible en déploiement hub (`MULTI_USER_MODE=false`), réel en mode autonome | S | P2 | aucune |
| SEC-7 | Aucun `securityContext` sur le StatefulSet du workspace | `charts/qgis-hub/templates/workspace-statefulset.yaml` | absent | conteneur QGIS avec les droits par défaut | S | P2 | exploitation |

## 9. Exploitation / déploiement

| ID | Item | Où | État | Impact | Effort | Prio | Dépendances / équipe |
|----|------|----|------|--------|--------|------|----------------------|
| EXP-1 | Overlays PYTHONPATH (`/home/onyxia/work/qgis-hub-live`, `/data/qgis-agent-live`) posés à la main sur les StatefulSets, hors chart ; un `helm upgrade` les perd. `empreinte_code` hache le paquet importé et celui de l'image, calcule `aligne` et le journalise, mais ne l'expose que dans `/health` | `hub/hub/empreinte_code.py:3-8`, `:86-103`, `:137-157` ; `agent/agent/empreinte_code.py:5` ; plan bilan-chantiers l.7, l.14, l.113 (case non cochée) ; stratégie qualité l.164-167 | sous-optimal | le code servi n'est pas celui de `main` ; correctifs perdus ou fantômes (état du live à vérifier) | S (exposer) / M (retirer) | P0 | HUB-7 |
| EXP-2 | `agent/static/produit.css` orphelin. `Dockerfile.agent` le copie vers `/opt/qgis-agent/agent/static/`, alors que `main.py` monte `Path(__file__).parent.parent / "static"`, c'est-à-dire `/opt/qgis-agent/static`. `empreinte_code` hache pourtant le fichier orphelin (clé `static/`) | `Dockerfile.agent:13` ; `agent/agent/main.py:135-142` ; `agent/agent/empreinte_code.py:59-75` | cassé | chat sans style en accès direct (le démarrage journalise « Feuille du produit absente ») | S | P1 | vérification de `/static/produit.css` à ajouter au smoke test CI |
| EXP-3 | Bundle blocknote-editor. Build : pas de `package-lock.json` (`npm install`, dépendances en `^`), `tsc -b` sauté, `vitest` (2 fichiers de test) jamais lancé en CI, `sourcemap: true` qui publie les sources en production (`blocknote-editor/vite.config.ts:32`). Types de l'API : `src/gen/hub-api.d.ts` est un placeholder écrit à la main, et `gen:types` n'a jamais tourné. Service : le bundle est cherché à `Path(__file__).parent / "static" / "blocknote-editor"`, donc absent quand le code vient d'un overlay | `Dockerfile.hub:9-20`, `:38` ; `hub/hub/main.py:1023`, `:1075` ; `blocknote-editor/package.json:12` | sous-optimal | build non reproductible, erreurs de type non vues, éditeur absent en live overlay | M | P1 | EXP-1 |
| EXP-4 | `security.networkPolicy.*` sans aucun template ; `git.*` également sans effet | `charts/qgis-hub/values.yaml:64-68`, `:82-92` ; `charts/qgis-hub/values.schema.json:268-285`, `:301` | non branché | fausse assurance d'isolement réseau | M | P0 | SEC-2 |
| EXP-5 | CI : `:latest` et `:main` poussés avant le smoke test (le workspace fait l'inverse) ; chart ni linté ni rendu (`helm package` seul) ; `TestAccordAvecLeChart` ignoré faute de helm | `.github/workflows/build.yml:83-111`, `:129-151` ; `.github/workflows/helm-publish.yml:22-26` ; `hub/tests/test_install_adoption.py:83` | sous-optimal | une image cassée devient `:latest` ; un template cassé part au dépôt Helm | S | P0 | — |
| EXP-6 | Images en `tag: latest` avec `pullPolicy: Always` et `appVersion: "latest"`, alors que la CI publie `:sha` | `charts/qgis-hub/values.yaml:8`, `:108`, `:129` ; `charts/qgis-hub/Chart.yaml` | sous-optimal | rollback impossible, déploiement non reproductible | S-M | P1 | EXP-5 |
| EXP-7 | Aucune `livenessProbe` (seulement des `readinessProbe`, et une `startupProbe` côté hub) ; le `HEALTHCHECK` du Dockerfile du workspace est ignoré par Kubernetes | `charts/qgis-hub/templates/*` ; `WS:Dockerfile:174` | absent | un QGIS ou un uvicorn gelé n'est jamais redémarré | S | P1 | — |
| EXP-8 | Variables lues par le code mais posées par aucun chart : `USE_REAL_MCP`, `ADMIN_TOKEN`, `ADMIN_USERS`, `PROFILS_AVEC_PROMPT`, `CEREMA_ED25519_PRIVATE_KEY`, `JWT_SECRET`, `STOP_TOOL_GRACE_SEC`, `TOOL_RESULT_BUDGET_CHARS`… Les drapeaux `COMPONENTS_ENABLED`, `ASSEMBLIES_ENABLED` et `PMTILES_ENABLED` valent `true` par défaut, sans interrupteur d'exploitation | `hub/hub/main.py:103`, `:2542-2546`, `:5085`, `:6911` ; `agent/agent/qgis_agent.py:320-323` ; conséquences en REC-1, HUB-5, AG-15, LIV-3 | non branché | comportement réglé par les défauts du code, invisible dans le chart | S | P1 | — |
| EXP-9 | Ressources : workspace limité à 2 CPU et 4 Gi pour un rendu llvmpipe avec GRASS ; `storageClassName: rook-ceph-block` codé en dur | `charts/qgis-hub/values.yaml:133-139` ; `charts/qgis-hub/templates/agent-statefulset.yaml:177` ; `charts/qgis-hub/templates/workspace-statefulset.yaml:37` | sous-optimal | risque d'OOM ; chart non portable | S | P2 | — |
| EXP-10 | Restes inutiles : réécriture de `onyxia-init.sh` que la commande uvicorn du chart court-circuite ; Role RBAC `get secrets` rendu inutile par le `secretKeyRef` de `HUB_API_KEY` ; `_correspondance_icones.txt` (outil de build) servi publiquement par le montage `/static` ; journal « BlockNote editor bundle ABSENT » émis à tort quand `hub/hub/static` manque | `hub/hub/main.py:1066-1076` ; `Dockerfile.hub:57-58` ; `charts/qgis-hub/templates/statefulset.yaml:40`, `:83-93` ; `deploy/rbac/hub-secret-reader.yaml:1-19` | sous-optimal | droit superflu s'il est appliqué | S | P2 | — |
| EXP-11 | Environ 90 `pytest.skip()` sans raison, déclenchés par un fichier `.tsx` absent : un renommage désactive le test en silence. Un skip conditionnel masque une régression possible (« le format d'en-tête legacy attendu a changé ») | `hub/tests/test_sprint4_edit_panel.py:93-263` ; `hub/tests/test_sprint5_v1_11_inline_popup.py:46-162` ; `hub/tests/test_sprint6_v1_12_map_editable.py:33-177` ; `hub/tests/test_sprint7_v1_13_*.py` ; `hub/tests/test_resolution_etude_projet.py:91` | sous-optimal | la couverture de l'éditeur est illusoire | S | P1 | EXP-3 |

Tests `skip` / `xfail` recensés (pour mémoire) :

- xfail D4, D6, D7, D9, D10 : `hub/tests/test_drifts_modele_blocks.py:95-151`, tous en `strict=False` (LIV-1, LIV-2, LIV-9 ; D10 = l'agent ne lit pas les blocs en cours d'édition, → T7).
- Skips d'environnement légitimes : `weasyprint` (`hub/tests/test_sprint10_wave1_publish.py:286`, `:338`), `jsonschema` et `yaml` (`importorskip`), serveur MCP live (`hub/tests/test_mcp_qgis_executor.py:502`), Node (`hub/tests/test_templates_js.py:122`, qui tourne bien en CI).
- `agent/tests` : aucun skip.
- `evals/tests/test_evaluation.py:312` : source de l'agent absente.

## 10. Documentation périmée

Il n'existe de `ROADMAP.md` dans aucun des deux dépôts. La feuille de route est
dispersée entre `docs/ARCHITECTURE_AGENT.md` §7 (l.640-700),
`docs/blocks-and-deliverables-model.md` §9 (l.525-555) et les xfail de
`hub/tests/test_drifts_modele_blocks.py`. Les marqueurs ⏳ de
`ARCHITECTURE_AGENT.md` et `CHARTE_AGENT.md` datent du dernier commit de ces
fichiers (30 mai 2026). Leur règle de mise à jour (`:709` et `:516`) n'est pas
appliquée.

| ID | Item | Où | Réalité dans le code | Effort | Prio |
|----|------|----|----------------------|--------|------|
| DOC-1 | Marqueurs ⏳ déjà livrés : CRUD recettes versionné, bouton Copier, persistance de la clé LLM | `docs/ARCHITECTURE_AGENT.md:100`, `:564`, `:652` ; `docs/CHARTE_AGENT.md:139` ; `docs/day5-migration-guide.md:72` ; `docs/day5-user-guide-visuel.md:138-139` | livré : `hub/hub/main.py:10227-10366` ; `agent/templates/chat.html:440`, `:2173` ; Secret `qgis-llm-apikey` (`charts/qgis-hub/templates/agent-statefulset.yaml:211-216`) | S | P2 |
| DOC-2 | Chiffres faux : « 8 profils », « hub/main.py 3473 L », workspace « ~2300 lines » et « No automated tests yet » | `docs/ARCHITECTURE_AGENT.md:75`, `:697` ; `docs/CHARTE_AGENT.md:65`, `:181` ; `WS:README.md:604-605` | 13 profils ; 14 264 lignes ; 2 805 lignes ; 12 fichiers de test et un job CI | S | P2 |
| DOC-3 | « Tous les livrables publics » | `USER_GUIDE.md:136-140` ; `docs/ARCHITECTURE_AGENT.md:484` | défaut `audience="cerema_internal"` avec ACL privée (`hub/hub/s3_publication.py:771`) ; le filtrage par groupe SSPCloud manque (LIV-4) | S | P1 |
| DOC-4 | Macros, Agent C/D, `AgentTemplate`, `/embed/agent`, `/studies/{sid}/agents`, RAG des documents d'étude « à venir » | `docs/ARCHITECTURE_AGENT.md:134`, `:154`, `:255`, `:420` ; `docs/CHARTE_AGENT.md:157`, `:205`, `:214`, `:460` ; `README.md:164` | toujours absents : les reclasser explicitement en V2 | S (doc) / L (code) | P2 → T5, T7 |
| DOC-5 | OPS : « bbox de `set_study_zone` perdue, fix backlog côté QgisRemoteMCP » | `OPS.md:249` | la description de l'outil affirme désormais la persistance (`WS:main_mcp.py:665`) ; à vérifier dans le pont | S | P2 |

---

## Chevauchements avec les chantiers en cours

| Chantier | Items qui en relèvent (non redécrits) | Dépendances signalées par cet inventaire |
|----------|---------------------------------------|------------------------------------------|
| T1 panneaux du bureau | UI-1, UI-2, UI-3, UI-7, UI-9, UI-10, UI-11, REC-5, MEM-1 | AG-9 (sauver le partiel) avant de rendre UI-2 sûr ; purger MEM-1 en même temps que `.mem-*` ; REC-1 avant REC-5 |
| T2 QGIS en panneaux | AG-5, WS-4, SEC-2 | EXP-4 (NetworkPolicy) et authentification de l'API du workspace avant toute exposition supplémentaire de noVNC ou de l'API |
| T4 Atlas | LIV-1, LIV-9 | basculer les xfail D4 et D9 en `strict=True` à la livraison |
| T5 corpus documentaire | AG-12 (L7), DOC-4 (RAG) | L0 livré ; `vector_store` a déjà `study_id` ; MEM-2 et MEM-3 (purge par source) à régler avant d'indexer des documents ; L6 pour le protocole de sous-agent |
| T6 livrables Grist | WS-2, WS-3 (`export_grist_from_html`), UI-8, LIV-2, UI-1 (export Grist) | l'export Grist n'a plus de point d'entrée UI (`exportGristDoc` dans un panneau caché) ; décision `kind=features` ; audience transmise par `publish_artifact` ; LIV-4 (ACL) pour les tables publiées |
| T7 agents dédiés | HUB-1, SEC-3, AG-12 (L6, L8, `/agent-context/new`), AG-15, AG-18, REC-6, DOC-4 | SEC-1 et SEC-5 avant tout partage d'agent ; SEC-3 (data-binding) avant d'annoncer un périmètre ; AG-14 à corriger avant d'ouvrir les profils d'assistance |
