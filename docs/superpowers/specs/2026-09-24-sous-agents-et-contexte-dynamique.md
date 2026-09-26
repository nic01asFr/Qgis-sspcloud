# Sous-agents et contexte dynamique de l'agent QGIS

Date : 2026-09-24.
Statut : proposition à arbitrer.
Portée : agent (`agent/agent`), hub (`hub/hub`), workspace MCP (`BigQgisMCP`).
S'appuie sur : `2026-09-24-strategie-qualite-agent.md` (niveaux A à D, budget de
contexte, banc), `docs/CHARTE_AGENT.md` (9 principes), `docs/ARCHITECTURE_AGENT.md`
(agents A à D, Scope), et sur la décision « guidance MCP en 3 couches » déjà validée.

Abréviations des chemins du workspace :
MCP = `BigQgisMCP/main_mcp.py`, BR = `BigQgisMCP/src/qgis_bridge.py`,
HP = `BigQgisMCP/src/qgis_helpers.py`, DS = `BigQgisMCP/datasources.json`.
Côté service : QA = `agent/agent/qgis_agent.py`, MEM = `agent/agent/memory.py`,
HUB = `hub/hub/main.py`.

## 0. Décision proposée en une page

1. **Pas de sous-agent LLM pour les bases de référence.** BD TOPO, Géorisques,
   DVF, INSEE et cadastre ont des schémas fixes et connus. Un LLM qui traduit
   une question en requête y ajoute un second tirage aléatoire sans rien gagner
   en fiabilité. Ces bases deviennent des **outils déterministes typés**
   (`resolve_place`, `load_from_catalog`, `query_reference`, `count_in_zone`),
   qui rendent un résultat vérifié et sa provenance. Remplir des paramètres
   typés, c'est précisément ce que le function calling de qwen fait bien.
2. **Deux sous-agents LLM seulement, là où l'entrée n'est pas structurée** :
   `consulter_documents` (RAG sur les documents d'étude, avec citations) et
   `interroger_base` (SQL en lecture seule sur une base déclarée dans QGIS dont
   le schéma est inconnu à l'avance). Un troisième, `resumer_projets`, est
   différé : un résumé calculé et mis en cache suffit au départ.
3. **La façade dynamique est le vrai levier.** `tools/list` dépend de la zone,
   de la phase et de l'étude. Les ids du catalogue y sont des enums. Les
   sous-agents n'y figurent que si leur objet existe : des documents pour l'un,
   une connexion de base pour l'autre. Le cache d'outils de l'agent est
   invalidé par une `etat_version`.
4. **La fiche d'étude est tenue par le harness, pas par le LLM.** C'est une
   mémoire de travail structurée, alimentée par les blocs `verification` et les
   provenances, qui remplace une partie des vidages L2 actuels.
5. **L'utilisateur retrouve ses repères d'assistant grand public.** Des étapes
   typées s'affichent, imbriquées pour les sous-agents. Un pied « Sources » est
   calculé à partir des provenances. Le bouton Arrêter se propage aux
   sous-agents. La réponse est courte.

Critère d'arbitrage, dans cet ordre : **fiabilité de la donnée > traçabilité >
coût de contexte > latence**.

## 1. Constat : ce que l'agent principal paie aujourd'hui

### 1.1 Contexte fixe payé à chaque appel LLM

Mesures du 2026-09-24 sur le code. Les tokens sont estimés à environ
3,5 caractères par token ; le lot L0 remplace ces estimations par un comptage
réel.

| Poste | Où | Taille | Tokens estimés |
|---|---|---|---|
| `_QGIS_ESSENTIALS` (antisèche PyQGIS et règles) | QA:1888-2370 | 23 400 car. | ~6 700 |
| Consignes de bascule de profil et mémoire | QA:1642-1693 | 2 100 car. | ~600 |
| Prompt de profil, L2, L3, enrichers | QA:2372-2480, MEM:1220 | 5 à 20 000 car. | 1 500 à 6 000 |
| 48 schémas MCP du workspace | MCP:277-888 | ~30 000 car. | ~8 500 |
| 28 outils natifs V1.5 (livrables) | `native_tools_v2.py` | 21 700 car. | ~6 200 |
| Outils natifs mémoire et recettes | QA:1100-1290 | 3 800 car. | ~1 100 |
| Outils hub `study_*` fusionnés | HUB:12496 | non mesuré | ~800 |
| **Total avant historique** | | **~90 à 105 000 car.** | **~25 à 30 000** |

Conséquences :

- Ce socle est **renvoyé à chaque itération** de la boucle (20 au plus,
  QA:2547). Le scénario S1 du 2026-09-24 s'est arrêté vers l'itération 7, soit
  environ 200 000 tokens d'entrée pour une seule demande. Le cache de préfixe
  du serveur de modèle, s'il est actif, réduit la latence mais pas la
  dilution de l'attention.
- **Les schémas d'outils représentent 60 % du socle.** Le filtrage par profil
  existe (QA:339-385), mais `standard` expose tout, y compris
  `mouse_click`, `key_press`, `export_grist` (2 400 car.) et les 28 outils de
  livrables, qu'il s'agisse de cartographie ou non.
- **Les résultats d'outils ne sont pas tronqués** (QA:3182-3204). Seules les
  images sont retirées. Une sortie `get_features` de 100 entités ou une trace
  Python entre entière dans le contexte, et y reste pour les itérations
  suivantes.
- **La vue L2 par portée ne sert jamais** : `build_context_summary` est appelé
  sans `context_kind` (QA:2432-2438), alors que MEM:1245-1260 sait le traiter.

### 1.2 Tâches coûteuses en contexte et en erreurs

| Tâche | Comment l'agent la fait aujourd'hui | Coût observé ou estimé | Erreurs typiques |
|---|---|---|---|
| **Recherche catalogue** | `list_datasources(search)` puis `add_from_catalog` ou `smart_load` (S1, fiche du 2026-09-24) | 1 à 2 appels, 500 à 3 000 car. de liste, plus deux schémas redondants (`add_from_catalog` 761 car., `smart_load` 1 564 car.) | mauvais outil pour le vecteur ; id deviné ; WFS en direct (51 450 444 entités, emprise France, incident d'Aix) |
| **Géocodage et homonymes** | `set_study_zone(target)` → `search_commune` : `geo.api.gouv.fr?nom=…&limit=1`, premier résultat retenu (HP:490-536) ; `geocode` BAN `limit=1` (HP:234) ; enricher `reference_data` avec `limit=1` et `boost=population` (`enrichers/reference_data.py:40-47`) | 1 appel, mais **choix silencieux** | « Saint-Martin » : une commune prise au hasard parmi des dizaines ; `Marseille` seul vers Marseillette, corrigé au cas par cas (HP:402, QA:1806 `_AMBIGUOUS_MAJOR_CITIES`) |
| **Zone vs contour** | `set_study_zone` télécharge le contour mais ne garde que sa bbox (HP:452-487) ; le clip n'est que suggéré (`verification.suite`, BR:5675-5712) | 2 à 6 appels `execute_python` pour charger un contour et découper | S1 : 5 couches `commune_aix*` en double, pas de clip, auto-stop après 3 erreurs identiques (QA:3245) |
| **BD TOPO** (11 sources WFS) | `smart_load` → GeoPackage filtré par bbox et mis en cache 24 h (HP:1079-1245) | correct depuis QgisRemoteMCP#4 | compte sur la bbox présenté comme compte communal (défaut T4 du plan spatial) |
| **Cadastre** | `ign_cadastre` en WMS seulement (DS:70) | — | pas de parcelle vectorielle : l'agent écrit du WFS à la main ou renonce |
| **INSEE / IRIS** | aucune source ; l'enricher `reference_data` donne population et surface d'une commune | — | chiffres infra-communaux inventés ou calculés sur la mauvaise maille |
| **DVF, Géorisques (API)** | `dvf_api`, `georisques_api` sont de type `api` sans outil dédié ; `add_from_catalog` répond « use execute_python » (BR:~5629) | 1 000 car. de schéma `execute_python`, 1 à 3 000 car. de code écrit par le LLM, sortie non tronquée ; 2 à 4 itérations | pagination oubliée, mauvais code INSEE, pas de provenance, réponse brute non vérifiée |
| **Écriture PyQGIS** | `execute_python` et l'antisèche de 23 400 car. (QA:1888) | c'est le premier poste de contexte fixe | boucles d'erreurs d'attribut (S1), NULL, fabrique de style (QA:2264-2318) |
| **Livrables** | L2c : compteurs et récents (`hub_artifacts.py:103`, `:246`), 28 outils natifs | 6 200 tokens de schémas à chaque tour | recréation, republication (garde `guard_publish_artifact`, QA:765) |
| **Documents d'étude** | **inexistant** : l'index vectoriel ne couvre que messages, insights et `memory_doc` (`embed_worker.py:1-15`) | — | — |
| **Projets annexes** | table `study_projects` sans résumé ni focus (`hub/hub/studies.py:205-217`) | — | l'agent ignore ce qui a été fait dans un projet voisin |

Deux constats orientent la suite :

- **Les erreurs coûteuses viennent de l'identification** (quel lieu, quelle
  source, quel périmètre) **et du code écrit à la main**, pas du raisonnement.
  Ces deux causes se corrigent de façon déterministe.
- **Le contexte est surtout pris par des schémas d'outils hors sujet et par
  l'antisèche PyQGIS.** Moins d'outils visibles et moins de code à écrire
  réduisent le contexte fixe du même coup.

## 2. Options évaluées

### 2.1 Grille

Notes de 1 (mauvais) à 3 (bon), pour un modèle unique `qwen3-6-35b-moe`.

| Option | Fiabilité | Traçabilité | Coût contexte | Latence | Verdict |
|---|---|---|---|---|---|
| (a) Sous-agents LLM spécialisés appelés comme un outil | 2 (deuxième tirage aléatoire, sauf sortie vérifiée) | 2 (bonne si le sous-agent rend ses étapes) | 3 (le contexte spécialisé ne pollue pas l'agent principal) | 1 (+5 à 30 s par appel) | **Oui, seulement pour l'entrée non structurée** |
| (b) Outils déterministes de haut niveau | 3 (testables en conteneur, postconditions) | 3 (provenance calculée) | 3 (un schéma court remplace du code) | 3 | **Oui, cas par défaut** |
| (c) Chargement dynamique des outils et règles par phase et intention | 2 (risque d'outil absent au mauvais moment) | 3 | 3 (−40 à −60 % du socle) | 3 | **Oui, avec un noyau toujours présent** |
| (d) Fiche d'étude structurée | 3 (faits issus des `verification`) | 3 | 2 (remplace du L2 plus long) | 3 | **Oui, tenue par le harness** |
| (e1) Routeur LLM d'intention en amont | 1 (erreur de routage silencieuse) | 2 | 2 | 1 | Non : un classifieur à règles suffit (les enrichers existent) |
| (e2) Plan explicite en tête de tâche longue | 2 | 3 | 2 | 2 | Oui, léger : une liste d'étapes montrée, pas un planificateur |
| (e3) Compactage des résultats d'outils avec pointeur | 3 | 3 (le brut reste au journal) | 3 | 3 | **Oui, prérequis (P0 de la stratégie)** |
| (e4) Recettes exposées comme outils | 3 | 3 | 2 | 3 | Oui, dans la façade (paquet `recettes`) |
| (e5) Vérificateur de chiffres et de sources en sortie | 3 | 3 | 3 | 2 | Oui (stratégie §5.2), il s'appuie sur la provenance |

### 2.2 Quand un sous-agent LLM vaut mieux qu'un outil déterministe

Un sous-agent LLM se justifie si **les trois** conditions sont réunies :

1. **L'entrée n'est pas réductible à des paramètres typés**, parce que le
   texte est libre, le schéma inconnu à l'avance ou le corpus trop gros.
2. **La tâche demande plusieurs lectures successives dont le détail n'a pas
   à rester dans le contexte principal**, comme parcourir un schéma SQL ou
   lire dix extraits pour en garder trois.
3. **La sortie se vérifie mécaniquement** : une citation existe dans le
   document, une requête SQL s'exécute et renvoie N lignes, et chaque chiffre
   rendu provient d'un résultat de requête.

Dans le cas inverse, l'outil déterministe s'impose : schéma connu, paramètres
énumérables, et besoin d'un résultat reproductible et testable en CI. C'est le
cas de toutes les bases de référence listées plus haut.

| Besoin | Réponse | Pourquoi |
|---|---|---|
| « Combien de bâtiments à Aix ? » | déterministe : `count_in_zone(load_from_catalog(bdtopo_batiments))` | schéma fixe ; le compte doit être exact et rejouable |
| « Risques sur ma commune » | déterministe : `query_reference(base=georisques, insee)` | API connue, sortie à normaliser |
| « Prix au m² des ventes 2023 » | déterministe : `query_reference(base=dvf, insee, annee, type_local)` | idem |
| « Saint-Martin » | déterministe : `resolve_place` rend des candidats ; **l'utilisateur** tranche | ce n'est ni au LLM ni à un sous-agent de choisir |
| « Que dit le rapport de phase 1 sur les zones humides ? » | **sous-agent `consulter_documents`** | corpus libre, citations vérifiables |
| « Dans ma base PostGIS, quelles parcelles ont un permis en cours ? » | **sous-agent `interroger_base`** | schéma inconnu, introspection en plusieurs étapes, SQL vérifiable |
| « Où en est le projet voisin ? » | déterministe au départ (fiche du projet mise en cache), puis sous-agent si besoin | les faits sont structurés : couches, livrables, dates |

### 2.3 Modèle et budget de chaque sous-agent

| Sous-agent | Modèle | Contexte max | Itérations | Délai | Outils internes |
|---|---|---|---|---|---|
| `consulter_documents` | `gemma4-26b-moe` (déjà utilisé en extraction, `insight_extractor.py:37`), repli qwen | 8 000 tokens | 3 recherches au plus | 45 s | `recherche_documents(requete, k)` (sqlite-vec, `vector_store.py`), `lire_extrait(doc, page)` |
| `interroger_base` | `qwen3-6-35b-moe` (function calling strict, déjà choisi pour `recipe_analyzer`, QA:115) | 10 000 tokens | 5 | 60 s | `list_database_connections`, `decrire_table`, `executer_sql_lecture` (LIMIT imposé, transaction en lecture seule) |
| `resumer_projets` (différé) | `gemma4-26b-moe` | 6 000 tokens | 1 | asynchrone, en cache | aucun ; entrée = fiche structurée des projets |

Les modèles passent par `_MODEL_FALLBACKS` (QA:109-118), avec une entrée par
sous-agent, et par `_resolve_model`. Aucun modèle n'est codé en dur dans le
sous-agent. Le précédent existe : les méta-agents `analyze_recipe`
(`native_tools_v2.py:1145`) et `analyze_agent_config` (`:993`) appellent déjà
`/chat/completions` en direct avec une liste de modèles (`:732`). Les
sous-agents suivent ce modèle, sous forme d'**outils natifs de l'agent**,
dispatchés localement comme `NATIVE_TOOLS_V2` (QA:881).

### 2.4 Contrat commun d'un sous-agent

Entrée, validée en Pydantic, sur le modèle de `hub/hub/models/recipe_analysis.py` :

```json
{
  "question": "texte de la demande, reformulé par l'agent principal",
  "etude": {"sid": "…", "zone": {"insee": "13001", "nom": "Aix-en-Provence", "bbox_4326": […]}},
  "contraintes": {"lecture_seule": true, "max_lignes": 200},
  "budget": {"tokens": 8000, "iterations": 3, "delai_s": 45}
}
```

Sortie, **la seule chose que voit l'agent principal**, plafonnée à 1 500 tokens :

```json
{
  "statut": "ok | ambigu | vide | hors_perimetre | erreur",
  "reponse": "2 à 5 phrases factuelles",
  "resultat": {"lignes": [...], "compte": 42, "tronque": false},
  "sources": [{"id": "doc:rapport_phase1.pdf#p12", "titre": "…", "extrait": "…", "date": "…"}],
  "verification": {"requete": "SELECT …", "lignes_renvoyees": 42, "citations_verifiees": 3},
  "etapes": [{"libelle": "Lecture du schéma de la base", "statut": "ok", "duree_ms": 820}],
  "avertissements": ["Table 'permis' sans date de mise à jour"],
  "cout": {"tokens_entree": 6100, "tokens_sortie": 410, "duree_ms": 9400},
  "questions": ["Parlez-vous de la table permis_2024 ou permis_archive ?"]
}
```

Règles :

- **Vérification par le code, pas par le sous-agent.** Pour
  `consulter_documents`, chaque `extrait` doit figurer mot pour mot dans le
  document indexé, sinon la source est retirée et un avertissement ajouté.
  Pour `interroger_base`, le SQL est rejoué par le code, qui fournit
  `lignes_renvoyees` ; tout nombre de `reponse` absent de `resultat` fait
  passer le statut à `erreur`.
- **Gestion d'erreur** :
  - `ambigu` : l'agent principal pose `questions` à l'utilisateur, jamais un
    choix silencieux ;
  - `vide` : il le dit ;
  - `erreur` : au plus une relance, sans repli automatique vers
    `execute_python` ;
  - dépassement du budget : `statut=erreur` et les étapes faites sont rendues.
- **Traçabilité** : l'entrée, la sortie complète et les appels internes vont
  au journal de tour rejouable (stratégie §5.1), sous l'`id` de l'appel
  parent.
- **Arrêter** : le `stop_signal` de `chat_stream` (QA:2485) est transmis au
  sous-agent, qui s'arrête entre deux étapes et rend `statut=erreur,
  avertissements=["arrêté par l'utilisateur"]`. Les sous-agents sont en
  lecture seule, donc sans rollback à prévoir.
- **Isolation** : le sous-agent ne voit ni l'historique de conversation, ni
  la mémoire L3, ni les outils qui modifient le projet.

### 2.5 Ce que voit l'utilisateur

Aujourd'hui, le flux SSE (`agent/agent/main.py:1201-1232`) porte
`{"phase": "reflexion" | "redaction" | "relance" | "outil"}` (QA:2588, 2714,
2773, 2957). L'appel et le résultat d'un outil passent en markdown dans `text`
(blockquote, QA:2958-2974). Il n'existe pas d'événement d'étape typé.

Cible, conforme aux habitudes des assistants grand public :

1. **Étapes typées** :
   - `{"etape": {"id", "parent", "libelle", "statut": "en_cours|ok|avertissement|erreur", "detail"}}` ;
   - le libellé est en langage courant (table `_LIBELLES_OUTILS`, QA:1515) ;
   - les étapes d'un sous-agent sont imbriquées sous leur parent, repliées par
     défaut (« Recherche dans les documents de l'étude · 3 étapes ») ;
   - le blockquote markdown reste pour l'historique et le rollback
     (`attachRollbackButtons`), mais le chat affiche les étapes.
2. **Pied « Sources »**, calculé à partir des `provenance` et `sources` du tour,
   jamais rédigé par le LLM :
   - « BD TOPO Bâtiments, IGN, chargé le 24/09, filtré sur la commune d'Aix » ;
   - « Rapport phase 1, p. 12 ».
3. **Question plutôt que choix** : un statut `ambigu` produit des boutons de
   choix (candidats de `resolve_place`, par exemple), sur le modèle des menus
   du chat existants.
4. **Arrêter** reste visible pendant un sous-agent et agit en moins de
   2 secondes entre deux étapes.
5. **Réponse courte** : le chiffre, son périmètre, sa source, et la suite
   proposée.

## 3. Façade dynamique

### 3.1 Principe : une seule vérité, calculée des deux côtés (option C)

- **Le workspace calcule ce qui dépend de l'état QGIS** : la zone courante, les
  enums du catalogue, la phase (`_build_context`, BR:309-348), le texte des
  descriptions et le `_context`.
- **L'agent calcule ce qui dépend de la conversation** : le profil, l'intention
  détectée par les enrichers, la présence de documents, de connexions et de
  livrables, et le budget.
- **Le hub** continue de fusionner les outils `study_*` et d'appliquer la liste
  blanche (HUB:12496-12578). Il ne décide rien d'autre.
- **Le lien entre les trois** est une `etat_version`, une empreinte courte de
  `(sid, projet, zone_id, phase, catalogue_version)`. Le workspace la renvoie
  dans chaque `_context` et dans `tools/list`.

### 3.2 Ce que le workspace doit changer

Aujourd'hui, `TOOLS` est une liste statique (MCP:277-888), `tools/list` la
renvoie telle quelle (MCP:2259-2264), et `listChanged` vaut `False`
(MCP:2236).

1. `tools/list` est **construit par une fonction** `construire_outils(etat)`.
   Ses tests unitaires comparent la sortie à des instantanés.
2. **Façade de chargement** `load_from_catalog` :
   - `id` : enum des sources catalogue, avec un libellé court en français par
     id dans la description, soit environ 2 000 caractères pour 47 sources ;
   - `purpose` : `map | analyze | count`. `map` sur un fond ouvre un flux ;
     `analyze` et `count` sur du vecteur matérialisent un GeoPackage filtré,
     puis le découpent au contour si la zone est une commune ;
   - les sources `api` sont **exclues de l'enum** et relèvent de
     `query_reference` ;
   - `add_from_catalog` devient un alias déprécié : absent de `tools/list`,
     il reste dispatché ;
   - `smart_load` reste dispatché et est retiré de la liste quand la façade
     est active.
3. **`set_study_zone`** :
   - sa description porte la zone courante (« Zone active : Aix-en-Provence
     (13001), contour communal ») ;
   - le contour est **conservé** (couche `zone_etude` et WKT en variable de
     projet), et plus seulement sa bbox (HP:452-487) ;
   - s'il y a plus d'un candidat, l'outil renvoie `statut=ambigu` et la liste
     des candidats (nom, département, INSEE, population), **sans rien
     appliquer**.
4. **`execute_python`** : sa description renvoie en une ligne vers les outils
   qui couvrent le besoin (clip, compte, chargement, requêtes de référence).
5. **`_context` en français**, calculé à partir de l'état réel. Il porte
   `etat_version`, la zone, le compte réel et la suite (stratégie §3.3). Il
   remplace le hint anglais générique de `_phase_hint` (BR:351-362).
6. `listChanged: true` et émission de `notifications/tools/list_changed` pour
   les clients MCP externes (Claude Desktop, Cursor). C'est facultatif pour
   l'agent interne, qui s'appuie sur `etat_version`.

### 3.3 Ce que l'agent doit changer

Aujourd'hui, `_tools_cache` n'est invalidé qu'au changement de profil
(QA:1581, 1593-1630, 1640).

1. **Invalidation** :
   - l'agent garde `self._etat_version` ;
   - après chaque résultat d'outil, il lit `_context.etat_version` ; si elle a
     changé, il vide `_tools_cache` **et reconstruit `tools` pour l'itération
     suivante du même tour** (aujourd'hui `tools` est calculé une seule fois,
     en QA:2501) ;
   - `set_study_zone`, `study_switch` et `study_project_switch` provoquent
     toujours une invalidation.
2. **Paquets d'outils**. Un paquet est une liste de noms déclarée en YAML,
   dans le profil (`mcp_tools.paquets`) ou dans un fichier commun :

   | Paquet | Contenu | Présent quand |
   |---|---|---|
   | `noyau` (toujours) | `set_study_zone`, `get_study_zone`, `load_from_catalog`, `count_in_zone`, `clip_to_study_zone`, `get_project_info`, `get_features`, `set_layer_style`, `set_layer_visibility`, `zoom_to`, `get_screenshot`, `save_project`, `memory_search` | toujours |
   | `reference` | `resolve_place`, `query_reference` | toujours pour `standard`, `risk_analyst` et `db_analyst` |
   | `traitement` | `run_processing`, `search_algorithms`, `execute_python`, `execute_async`, `poll_job` | phase `analysis`, ou intention « calcul » |
   | `cartographie` | `apply_layout_template`, `list_layout_templates`, `export_pdf`, `export_web_map`, `export_layer` | phase `cartography` ou `export`, ou intention « carte » |
   | `livrables` | les 28 outils natifs V1.5, `publish_artifact` | intention « livrable », profil `storymap_creator`/`map_composer`, ou livrable en cours |
   | `recettes` | `list_recipes`, `run_recipe`, outils recettes natifs | recette pertinente trouvée par `recipe_matcher`, ou demande explicite |
   | `bases` | `interroger_base` | `list_database_connections` non vide (résultat mis en cache pour la session) |
   | `documents` | `consulter_documents` | l'étude a au moins un document indexé |
   | `interface` | `mouse_*`, `key_press`, `qgis_desktop_ui` | jamais pour l'agent interne (réservé aux clients externes) |

   L'intention vient des enrichers (regex, déjà en parallèle, QA:2413), sans
   routeur LLM. Un **filet** évite le blocage par un outil absent : si le LLM
   appelle un outil connu mais absent de la liste, l'agent l'exécute s'il
   figure dans la liste blanche du profil, puis ajoute son paquet pour la
   suite du tour. L'événement est journalisé ; c'est l'indicateur de réglage
   des paquets.
3. **Règles chargées avec les paquets.** L'antisèche `_QGIS_ESSENTIALS`
   (23 400 car.) est découpée par paquet : les règles de zone et de bbox vont
   au noyau, NULL, fabrique de style et extent Processing vont au paquet
   `traitement`, la mise en page au paquet `cartographie`. Une règle ne
   voyage qu'avec les outils qu'elle encadre.
4. **G9** : passer `context_kind` et `scope_ids` à `build_context_summary`
   (QA:2432).
5. **Budget cible**, contrôlé par les tests d'instantané :
   - socle système et outils ≤ 12 000 tokens en cartographie simple ;
   - ≤ 16 000 tokens pour le pire paquet ;
   - soit une baisse de 45 à 60 % par rapport à aujourd'hui.

### 3.4 Articulation façade et sous-agents

- Un sous-agent est **un outil comme un autre dans `tools/list`**, soumis aux
  paquets. Il n'apparaît que si son objet existe : l'agent ne peut pas appeler
  `consulter_documents` dans une étude sans documents.
- Ses outils internes **ne sont jamais** dans la liste de l'agent principal.
- Il reçoit la zone et l'`etat_version` courantes ; sa sortie porte
  l'`etat_version` qu'il a lue, et l'agent rejette un résultat obsolète.
- Ses `sources` alimentent le même pied « Sources » et la fiche d'étude.

### 3.5 Fiche d'étude (mémoire de travail structurée)

C'est un document JSON par étude, stocké par le hub à côté de `studies` et
mis à jour **par le code** après chaque outil qui renvoie un bloc
`verification` ou une provenance. Contenu :

- zone résolue : INSEE, nom, contour et méthode ;
- couches produites : nom, source, compte, clip oui/non, date ;
- sources consultées ;
- livrables : slug, URL hub, statut ;
- décisions confirmées par l'utilisateur, par exemple le choix de la commune
  parmi les homonymes ;
- questions ouvertes.

Règles :

- **Rendu en L2 sous 800 tokens au plus.** La fiche remplace la liste brute
  des couches et une partie des traitements (MEM:1382).
- Un résumé narratif de 3 lignes est produit de façon asynchrone par
  `gemma4-26b-moe`. Il n'est jamais source d'un chiffre.
- Pour les projets annexes, chacun a sa fiche. L'agent reçoit la fiche
  complète du projet en focus et une ligne par autre projet. Il faut ajouter
  un champ `focus` à `study_projects` (`hub/hub/studies.py:205`).

## 4. Plan de livraison incrémental

Chaque lot se livre en une PR par dépôt, avec ses tests en CI. Les tests
« banc » sont des scénarios du niveau C de la stratégie, joués N = 3 fois.

| Lot | Durée | Contenu | Tests | Critères de succès mesurables |
|---|---|---|---|---|
| **L0 Mesure** | 1 j | Comptage de tokens par section (système, chaque paquet, chaque résultat d'outil), journalisé à chaque tour ; troncature des résultats d'outils avec pointeur (P0 de la stratégie) | instantanés de contexte pour les 8 situations de la stratégie §3.2 ; test de plafond par résultat (≤ 2 000 tokens) | ligne de base publiée ; aucun résultat d'outil > 2 000 tokens envoyé au LLM |
| **L1 Lieu sans homonymie** | 2 j | `resolve_place` dans le workspace : candidats, INSEE, contour ; `set_study_zone` renvoie `ambigu` sans appliquer ; enricher `reference_data` corrigé (plus de `limit=1` silencieux) ; boutons de choix dans le chat | contrats en conteneur : Saint-Martin (> 1 candidat), Marseille 4e (13204), Aix (13001, polygone) ; banc « Ambiguïté » | 100 % des homonymes donnent une question ; 0 choix silencieux sur 5 cas de référence × 3 |
| **L2 Façade de chargement** | 2-3 j | `load_from_catalog(id enum, purpose)`, contour conservé, `clip_to_study_zone`, `count_in_zone`, `verification` complète ; `add_from_catalog` et `smart_load` masqués | contrats S1, S2, S4 en conteneur (GeoPackage figé) ; test d'enum synchronisé avec DS | S1, S2, S4 : 100 % zone et chiffre corrects ; `execute_python` absent de la trajectoire S1 dans ≥ 90 % des runs |
| **L3 tools/list dynamique** | 2 j | `construire_outils(etat)` côté workspace ; `etat_version` dans `_context` ; paquets et invalidation dans le même tour côté agent ; découpage de `_QGIS_ESSENTIALS` ; G9 | instantanés : changement de zone en cours de tour (nouvelle zone seule dans les descriptions) ; profil restreint ; budget par paquet ; filet d'outil absent | socle ≤ 12 000 tokens en cartographie simple ; S3 (changement de zone) ≥ 90 % ; filet déclenché dans < 5 % des tours du banc |
| **L4 Données de référence** | 2-3 j | `query_reference(base, insee, …)` pour `georisques` et `dvf` (API catalogue), `insee_commune` ; provenance normalisée ; pied « Sources » déterministe | contrats sur réponses API figées (fixtures) ; banc « Risques de ma commune », « Prix DVF » | 100 % des chiffres traçables (vérificateur §5.2) ; 0 `execute_python` pour ces demandes ; médiane ≤ 4 itérations |
| **L5 Fiche d'étude** | 2 j | schéma et stockage hub ; mise à jour par le code depuis `verification` et provenance ; rendu L2 ≤ 800 tokens ; `focus` projet | instantanés « projet annexe en focus », « changement de zone » ; test d'isolation entre études | banc « Contexte d'étude » (« comme la dernière fois ») ≥ 90 % ; L2 total ≤ 1 500 tokens |
| **L6 Protocole de sous-agent et étapes** | 2 j | classe `SousAgent` (contrat Pydantic, budget, arrêt, journal) ; événements `etape` imbriqués ; rendu dans `agent/templates/chat.html` (:1857-1897) | LLM simulé : budget dépassé, arrêt pendant une étape, citation inventée retirée, nombre non traçable qui donne `erreur` | Arrêter effectif < 2 s ; 100 % des étapes d'un sous-agent visibles et rejouables depuis le journal |
| **L7 `consulter_documents`** | 3 j | indexation des documents d'étude (extension de `embed_worker.py`) ; sous-agent gemma, repli qwen ; citations vérifiées | corpus de référence (3 PDF) et 15 questions avec réponse attendue ; question hors corpus qui donne `vide` | ≥ 85 % de réponses justes ; 0 citation non vérifiable ; p95 ≤ 30 s |
| **L8 `interroger_base`** | 3 j | sous-agent qwen, SQL en lecture seule (transaction `READ ONLY`, `LIMIT`, délai), introspection de schéma | base PostGIS de test en conteneur ; tentatives d'écriture refusées ; banc « base métier » | 0 écriture possible ; ≥ 80 % de réponses justes sur 10 questions ; chaque chiffre issu du SQL rejoué |

Ordre et dépendances :

- L0 conditionne tout, car sans mesure on ne peut pas prouver de gain.
- L1 et L2 portent la fiabilité de la donnée et passent en premier.
- L3 et L4 peuvent se faire en parallèle.
- Les sous-agents LLM (L6 à L8) viennent en dernier : ils ne sont utiles que
  si le socle déterministe est sain.

## 5. Risques et parades

| Risque | Parade |
|---|---|
| Un outil utile est absent de la liste au mauvais moment (paquets) | filet d'exécution par liste blanche, journal, réglage des paquets par le banc |
| L'enum du catalogue dérive de `datasources.json` | enum générée depuis DS au démarrage ; test de synchronisation |
| Latence cumulée des sous-agents | réservés à deux cas ; délai par sous-agent ; étapes visibles pendant l'attente |
| Un sous-agent affirme un chiffre faux | vérification par le code (SQL rejoué, citation exacte) ; sinon `erreur` |
| La fiche d'étude diverge de l'état QGIS | alimentée seulement par les `verification` ; recalage sur `get_project_info` en début de tour |
| Deux modèles à maintenir | un seul point de choix (`_MODEL_FALLBACKS`) ; bancs joués sur les deux |
| Les clients MCP externes perdent des outils | la façade dynamique ne s'applique qu'aux sessions de l'agent interne ; les clients externes gardent la liste complète, moins l'alias déprécié |

## 6. Décisions à arbitrer

1. **Données de référence en déterministe, pas en sous-agent LLM** (§2.2).
   Proposition : oui.
2. **Où vit la façade** : dans le workspace (`construire_outils`), avec
   `etat_version` lue par l'agent ; le hub reste un simple filtre.
   Alternative : tout calculer dans le hub proxy (HUB:12578), qui voit toutes
   les sessions mais pas l'état QGIS. Proposition : le workspace.
3. **Modèles** : gemma4-26b-moe pour `consulter_documents`, qwen pour
   `interroger_base`. À confirmer au premier passage du banc.
4. **Sources à ajouter au catalogue** : IRIS (contours et statistiques INSEE)
   et parcelles cadastrales en vecteur, qui manquent aujourd'hui (DS : cadastre
   en WMS seul, aucune source INSEE ou IRIS). Source et licence à valider.
5. **Masquer les 28 outils de livrables hors paquet `livrables`** : c'est le
   plus gros gain de contexte, mais le changement de comportement est visible
   pour les profils de livrables. Proposition : oui, avec le filet.
6. **Clients MCP externes** : faut-il leur appliquer aussi la façade
   (`listChanged`) ou leur garder la liste complète ? Proposition : la liste
   complète en v1.
7. **Priorité du document d'étude (L7)** : elle dépend de la date d'arrivée du
   « contexte documentaire ». S'il arrive tôt, L7 passe devant L5.
