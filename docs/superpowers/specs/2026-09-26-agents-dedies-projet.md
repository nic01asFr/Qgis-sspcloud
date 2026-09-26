# Agents dédiés à un projet (templatisation)

> Équipe T7, étude et spec de réalisation, 2026-09-26.
> Branche `docs/agents-dedies`. Le lot 0 (schéma, validation, tests) est
> implémenté sur cette branche, sans exposition.
> Documents liés : `docs/ARCHITECTURE_AGENT.md` (types A/B/C/D, AgentDefinition),
> `docs/CHARTE_AGENT.md` (principes 7 et 8, §5 AgentTemplate),
> `2026-09-24-sous-agents-et-contexte-dynamique.md` (contrat de sous-agent,
> fiche d'étude), `2026-09-24-strategie-qualite-agent.md` (banc, journal de
> tour), spec corpus documentaire (T5, à venir) et spec Atlas (T4, à venir).

## 0. En une page

**Besoin.** La personne veut, avec son assistant, créer des agents
spécialistes des données d'une étude. Chaque agent a une configuration claire,
qu'elle valide avant publication. Cette configuration fixe :

- les documents, les couches et les bases de l'étude qu'il voit ;
- ce qu'il a le droit d'en faire ;
- ses outils, sa mémoire et son ton ;
- ce qu'il affiche (Atlas, livrables) ;
- son public : interne, partenaires ou public.

Les accès aux données doivent être respectés **strictement**.

**Constat.** L'« agent partagé » actuel est une clé `qgisk_` qui ne restreint
**que la liste d'outils, et seulement au proxy MCP**. Le reste de la
configuration n'est pas appliqué :

- le périmètre de données n'est pas appliqué, et le code l'avoue ;
- le profil n'est lu par personne ;
- l'URL publiée mène à une route qui n'existe pas ;
- un appel `tools/call` vers `study_switch` ou `study_create` passe même quand
  l'outil est hors de la liste blanche.

**Décision proposée.**

1. **Un document de configuration versionné et validé** (`kind:
   agent_dedie`, §3) remplace le formulaire de clé. Chaque champ a une
   conséquence côté serveur, et un champ sans application est refusé : on ne
   promet pas ce qu'on n'applique pas.
2. **Les droits s'appliquent dans le serveur, à trois portes** (§5) : la
   liste des outils, l'appel d'outil (arguments imposés, résultats filtrés)
   et l'accès aux données (montage en lecture seule du seul périmètre).
   Le prompt ne fait qu'expliquer, il ne protège rien.
3. **L'isolement vient du provisionnement.** Pour un public partenaires ou
   public, un agent a son propre atelier : un pod dont le volume ne contient
   que les fichiers du périmètre, montés en lecture seule. Ce n'est jamais le
   pod de travail du propriétaire. C'est la piste déjà écrite dans
   `hub/hub/main.py` (« un atelier par agent, étude montée en subPath lecture
   seule »).
4. **Le parcours de création passe par l'assistant et une file « À valider »**
   (repris de l'Atelier, §2) : proposition, aperçu, validation explicite par
   un geste d'interface, publication. Le modèle peut proposer et refuser. Il
   ne peut pas valider ni publier.
5. **Livraison en 6 lots** (§6). Le lot 0 est fait sur cette branche. Tant que
   le lot 3 (isolement) n'est pas livré, seuls des agents **internes en
   lecture seule** peuvent être publiés.

## 1. État de l'existant

### 1.1 Ce qui marche

| Brique | Où | État |
|---|---|---|
| Profils YAML validés en Pydantic (`ProfileConfig`), liste blanche `mcp_tools.allowed` / `disabled`, `native_tools` | `hub/hub/profile_manager.py`, `hub/hub/profiles/*.yaml` (13 profils) | Marche. Filtrage fait **côté agent** (`qgis_agent._get_mcp_tools`) |
| Profil verrouillé (`profile_locked`) : ni routeur contextuel ni bascule `<switch_profile>` | `agent/agent/main.py:1130`, `qgis_agent.py:1425` | Marche, mais c'est un champ de formulaire envoyé par le client : c'est une commodité d'interface, pas une frontière |
| Paquets d'outils : socle plus paquets selon l'intention, bornés par la liste blanche du profil | `agent/agent/paquets_outils.py` | Marche. Il réduit le contexte, il ne restreint pas les droits (il réexpose un outil autorisé à la demande) |
| Clés scopées `qgisk_` : émission, validation, expiration, révocation douce, clé parente | `hub/hub/auth.py:584-690`, table `scoped_keys` | Marche. Un jeton délégué ne peut pas émettre de clé (`main.py:5400`) |
| Filtre au proxy `/mcp` : `tools/list` filtré et `tools/call` refusé hors liste blanche | `main.py:12502` `_scope_tools_whitelist`, `_tool_call_denied` | Marche pour les outils du workspace |
| Analyse avant émission (`analyze_agent_config`), puis `create_agent` / `publish_agent` / `revoke_agent` | `agent/agent/native_tools_v2.py:993-1140`, profil `agent_config_analyzer`, modèle `hub/hub/models/agent_config_analysis.py` | Marche : analyse LLM mise en cache, puis empreinte `audit_chain` à la publication |
| Bouton « Créer un agent partagé » et liste des agents de l'étude | `hub/templates/desk.html:1261` et `:2067` | Le bouton envoie une consigne à l'assistant ; la liste s'affiche |
| Classification des ressources (`public`, `cerema_internal`, `restricted`, `confidential`), défaut jamais public | `hub/hub/models/classification.py`, composants | Marche sur les composants et le filtre des livrables publiés (`main.py:10586`) |
| Mémoire L1 (messages), L2 (état d'étude, traitements), L3 (utilisateur), index sémantique | `agent/agent/memory.py`, `vector_store.py` | Marche, en mode **mono-utilisateur** (`username="user"` par défaut) |

### 1.2 Ce qui n'est pas branché (ou est faux)

1. **Le périmètre de données n'est pas appliqué.** `scope["sid"]`, `["pid"]`
   et `["data"]` ne sont jamais lus : un fil-piège le vérifie
   (`hub/tests/test_scope_donnees_non_applique.py`). Les outils QGIS opèrent
   sur l'étude **active** du pod.
2. **Les outils hub contournent la liste blanche.** Dans `_proxy_request`,
   un `tools/call` vers un outil hub (`study_switch`, `study_create`,
   `study_project_*`) part vers `dispatch_hub_tool` **avant** le contrôle
   `_tool_call_denied`, qui ne s'applique qu'aux outils du workspace
   (`main.py:12665-12685`). `tools/list` les masque, mais un client qui
   connaît le nom peut les appeler. Une clé scopée peut donc changer l'étude
   active du propriétaire.
   **À corriger en premier (lot 1)**, indépendamment du reste.
3. **Le profil (`persona`) d'une clé n'est lu par personne.** Le chat de
   l'agent s'authentifie auprès du hub avec la clé maître (`HUB_API_KEY`,
   `qgis_agent.py:228`) : la liste blanche du proxy ne s'applique jamais à
   l'agent interne. Pour lui, seul le filtrage de profil côté agent compte.
4. **Un profil inconnu retombe sur `standard`** (`profile_manager.get_profile`),
   qui a `allowed: all`. Le repli est donc un repli **ouvrant**.
5. **La route `/agent-share/{key_short}` n'existe pas.** `publish_agent`
   calcule une URL qui répond 404, et l'agent C (diffusion) n'est pas
   implémenté (`ARCHITECTURE_AGENT.md` §2).
6. **La clé brute passe dans la conversation.** `create_agent` rend `key` au
   modèle : elle entre dans l'historique L1 et dans le journal de tour.
7. **`audience` n'est appliquée qu'aux livrables**, pas aux agents : aucune
   liste d'invités, aucun accès anonyme contrôlé.
8. **Pas de document de configuration.** La config est éclatée en arguments
   d'outil. Rien n'est versionné, à part une empreinte à la publication, et
   rien ne se relit ni ne se modifie : on révoque et on recrée.
9. **`AgentDefinition` et le « Scope » restent au stade de la cible**
   (`ARCHITECTURE_AGENT.md` §1 et §3, « ⏳ V2/V3 »). `hub/hub/scopes.py`
   n'existe pas.
10. **Isolement.** Il y a un workspace QGIS par utilisateur (StatefulSet
    `qgis-workspace-{owner}`, `sessions.py`), et le hub est mono-utilisateur.
    Un agent publié tournerait donc dans le pod de travail du propriétaire,
    avec ses fichiers, son étude active et ses secrets.

**Conséquence.** On ne peut aujourd'hui publier honnêtement qu'un agent
**interne**, sur une liste d'outils de lecture, sans garantie sur les données.
La notice `data_scope_notice` renvoyée par le mint le dit déjà.

## 2. Ce qu'on reprend de l'Atelier, et ce qu'on ne reprend pas

L'étude porte sur le dépôt `Claude Code sspcloud` et ses worktrees
`atelier-*`, en lecture seule. Les agents de l'Atelier n'ont **pas** de
fichier de profil déclaratif. Les profils `code` et `assistant` sont des
constantes Python (`atelier-src/mcp_gateway/atelier/commandes/profils.py`).
Les garde-fous sont des « gardiens » déclarés en JSON et exécutés sans
modèle, par des hooks `PreToolUse`, ou par classement des commandes côté
serveur.

### 2.1 Repris

| Idée de l'Atelier | Où | Traduction ici |
|---|---|---|
| Le serveur décide du profil. Un en-tête ne peut que restreindre. Une valeur inconnue vaut le profil le plus étroit. | `profils.py` (`profil_effectif`) | Le hub dérive l'agent de la clé, jamais d'un champ client. Outil ou profil inconnu : refus (lot 0 le code déjà) |
| Filtrer la liste **et** l'appel ; réécrire le schéma d'outil selon le profil | `test_profils_acces.py` | Porte 1 et porte 2 (§5.2) ; `sid`/`pid`/`chemin` retirés du schéma et imposés |
| Le projet est imposé par la conversation, jamais pris d'un argument (`cadrer_les_arguments`, `HorsProfil`) | `profils.py` | L'étude est imposée par la clé ; un argument `sid` différent est refusé |
| Classes de commandes `lecture` / `reversible` / `engageante` / `reservee` ; `reservee` jamais exposée au modèle | `commandes/modele.py` | Classes d'outils (§3.3). `publication`, `code` et `administration` ne sont jamais accessibles à un agent dédié |
| File « À valider » : le modèle peut refuser mais pas accepter | `a_valider.py` | Parcours de création (§4) : la validation est un geste d'interface, pas un outil |
| Un agent naît désactivé, avec un budget ; une modification par un modèle le désactive | `commandes/agents.py` | Brouillon, puis validé, puis publié. Toute modification repasse en brouillon. Quotas obligatoires |
| Plafond de privilège (`_refuser_si_trop_large`) | `profiles.py`, `mcp_ceiling` | Un agent dédié ne peut dépasser ni les droits du propriétaire sur l'étude, ni le plafond de son audience (§3.4) |
| Gardiens déclaratifs, sans modèle, journal en ajout seul, secrets filtrés | `gardiens/gardiens.json`, `executeur.py` | Contrôles en ligne (quota, expiration, taux de refus) sans LLM, et journal d'accès en ajout seul (§5.3) |
| Secrets par référence ; transcripts filtrés par empreinte avant la mémoire | `mcp_sync`, `filtre_transcripts.py` | Aucune clé dans la conversation (correctif du point 1.2.6) ; les connexions de base sont passées par nom, jamais par DSN |
| Tests d'accès qui passent par la vraie porte `/mcp` | `test_profils_acces.py`, `test_profils_surfaces.py` | Banc d'accès du lot 4 (§5.4) |

### 2.2 Pas repris

- **Une clé propriétaire partagée par tous les agents du pod**
  (`ATELIER_OWNER_KEY`). Dans l'Atelier, un agent qui omet l'en-tête de
  conversation garde un accès complet. Ici, chaque agent dédié a sa propre
  clé, dérivée et révocable, et le propriétaire n'est jamais lu depuis un
  en-tête.
- **L'isolement par convention** : un seul pod, un seul utilisateur Unix, des
  règles `deny`. Pour un public non interne, on exige un pod séparé.
- **Des profils dispersés** entre code, `catalog.yaml`, SQLite et
  déclencheurs wikichat, sans version. Ici, il n'y a qu'un document, avec une
  empreinte et une chaîne de versions.
- **Un garde-fou par expressions régulières sur des commandes shell** : il se
  contourne par un autre interpréteur. Ici, `execute_python` est simplement
  interdit aux agents dédiés.
- **La perte d'identité à travers la passerelle** : chaque appel porte
  l'identifiant de l'agent dédié jusqu'au journal.

## 3. Schéma de configuration d'un agent dédié

### 3.1 Principes

- **Un document par agent**, en YAML versionné (`kind: agent_dedie`,
  `version_schema: "0.1"`). Il est stocké avec l'étude
  (`/data/studies/{sid}/agents/{slug}/v{n}.yaml`) et indexé par le hub. Il
  n'est jamais modifié en place : une modification crée la version `n+1`,
  qui porte `version_precedente` (l'empreinte de la version `n`).
- **Validation stricte en deux niveaux**, codée dans `hub/hub/agents_dedies.py`
  (lot 0) :
  - structure : Pydantic `extra="forbid"`, une faute de frappe est une erreur ;
  - cohérence : `valider_config`, qui rend **toutes** les erreurs avec un
    code stable et un chemin.
- **Se tromper doit restreindre, pas ouvrir.** Un outil hors registre est
  refusé. Un profil inconnu est refusé, sans repli. Une ressource sans
  classification est traitée comme `confidential`.
- **Chaîne de droits** : chaque outil a une classe, chaque classe exige une
  action, et chaque action n'agit que sur les ressources qui portent le
  droit correspondant. Un maillon manquant est une erreur.
- **Pas de champ décoratif.** Chaque champ est appliqué par le code (§5.2).
  Le texte `ton.consignes` est le seul champ libre, et il n'accorde aucun
  droit.

### 3.2 Champs

| Bloc | Champs | Appliqué par |
|---|---|---|
| identité | `slug`, `nom`, `description`, `version`, `version_precedente` | index hub, empreinte |
| `etude` | `sid` (12-hex), `projets` (pid ; vide = projet principal) | clé scopée, arguments imposés, montage |
| `public` | `audience` (`interne` / `partenaires` / `public`), `invites` (identités ou groupes OIDC), `anonyme` | contrôle d'accès à la route de l'agent (lot 2) |
| `perimetre.couches` | `id`, `source` (chemin relatif au bundle), `droits`, `attributs_exclus`, `filtre` | montage (lot 3), filtre des résultats (lot 2) |
| `perimetre.documents` | `id`, `chemin`, `droits` (`lire`, `citer`) | index de corpus restreint (T5) |
| `perimetre.bases` | `id`, `connexion` (nom), `tables` (liste fermée), `droits` (`lire`, `requeter`) | sous-agent `interroger_base` en lecture seule |
| `perimetre.livrables` | slugs de livrables publiés | lecture seule des livrables |
| `actions` | table `action -> {confirmation, formats}` ; actions : `lire`, `requeter`, `citer`, `afficher`, `modifier`, `exporter` | porte 2 : confirmation par l'utilisateur ou le propriétaire |
| `outils` | `autorises` (noms), `paquets` (confort, résolus à la publication) | portes 1 et 2 |
| `garde_fous` | `lecture_seule`, `citations_obligatoires`, `max_entites_par_reponse`, `max_appels_outils_par_tour`, `message_hors_perimetre` | boucle agent et proxy |
| `memoire` | `conversation` (L1), `fiche_etude` (L2 en lecture), `documents` (L4 du périmètre), `visiteur` (L3 du visiteur), `apprentissage` | agent : couches chargées (§5.2) |
| `ton` | `profil_base`, `registre`, `langue`, `consignes` | prompt (seul endroit où le prompt compte) |
| `affichage` | `atlas.{active, couches, fond}`, `livrables` | rendu Atlas (T4), sous-ensemble du périmètre |
| `quotas` | `messages_par_jour`, `jetons_par_mois`, `expire_le` | compteur hub, expiration de la clé |

La mémoire L3 du **propriétaire** n'a pas de champ : elle n'est jamais
visible par un agent dédié.

### 3.3 Classes d'outils

Registre `CLASSE_OUTIL` dans `hub/hub/agents_dedies.py`. Un test fait échouer
la suite si un outil des paquets de l'agent n'y est pas classé.

| Classe | Action exigée | Exemples | Agent dédié |
|---|---|---|---|
| `lecture` | `lire` | `get_features`, `get_project_info`, `consulter_documents`, `interroger_base` | oui |
| `affichage` | `afficher` | `zoom_to`, `set_layer_visibility`, `afficher_atlas` | oui |
| `ecriture` | `modifier` | `modifier_entites`, `run_processing`, `smart_load`, `save_project` | seulement si `lecture_seule: false` et pas en public |
| `export` | `exporter` | `export_layer`, `export_pdf` | oui, sur les couches qui portent `exporter` |
| `publication` | aucune | `publish_artifact`, `publish_assembly` | **jamais** (geste du propriétaire) |
| `code` | aucune | `execute_python`, `execute_async` | **jamais** (contourne tout périmètre) |
| `administration` | aucune | `study_switch`, `create_agent`, `restart_qgis_engine`, `mouse_click` | **jamais** |

`afficher_atlas`, `modifier_entites`, `consulter_documents` et
`interroger_base` sont des outils à créer. Ce sont des **outils typés** qui
prennent un `id` du périmètre, jamais un chemin : c'est ce qui rend le
contrôle possible. En particulier, `modifier_entites(couche_id, modifications)`
remplace, pour un agent dédié, l'écriture par `execute_python` ou
`run_processing`.

### 3.4 Règles de cohérence (codées au lot 0)

| Code | Règle |
|---|---|
| `structure` | Champ inconnu, type faux, `sid` ou `slug` mal formé, action inconnue |
| `chemin_invalide` | Un chemin doit être relatif au bundle : pas de `/` initial, pas de `..`, pas de `\`, pas de lecteur, pas de segment vide |
| `id_invalide`, `id_duplique`, `perimetre_vide` | Identifiants propres et uniques ; au moins une ressource |
| `droit_inapplicable`, `droit_sans_action` | Droit compatible avec la nature de la ressource, et action déclarée |
| `outil_inconnu`, `outil_interdit`, `outil_sans_action` | Outil classé, classe permise, action déclarée ; un catalogue externe ne peut pas reclasser un outil connu |
| `lecture_seule_contredite`, `modifier_sans_cible` | `modifier` incompatible avec `lecture_seule`, et exige une couche cible |
| `public_ecriture`, `public_memoire`, `public_base`, `public_apprentissage` | Un agent public ne modifie rien, ne retient rien du visiteur, n'interroge pas de base et n'apprend pas |
| `anonyme_non_public` | L'accès anonyme est réservé à l'audience `public` |
| `partenaires_sans_invites`, `partenaires_ecriture_sans_confirmation` | Pour des partenaires : liste d'invités obligatoire, et toute écriture est confirmée |
| `expiration_requise`, `expiration_passee`, `expiration_trop_lointaine` | Hors audience interne : expiration obligatoire, au plus 366 jours |
| `profil_inconnu`, `profil_interne` | Pas de repli sur `standard` ; les méta-agents sont exclus |
| `atlas_hors_perimetre`, `atlas_sans_droit`, `livrable_hors_perimetre` | L'affichage est un sous-ensemble du périmètre |
| `classification_incompatible` | Visibilité par audience : `public` ne voit que `public` ; `partenaires` voit `public` et `restricted` ; `interne` voit tout sauf `confidential` |

Contrôles réservés à la publication (lot 2), parce qu'ils demandent l'état
réel de l'étude :

- chaque `source` et chaque `chemin` existe dans le bundle ;
- chaque connexion de base est déclarée dans l'étude ;
- le propriétaire a lui-même ces droits ;
- les classifications effectives sont passées à `valider_config(...,
  classifications=...)`.

### 3.5 Exemple complet

Le fichier de référence est
`hub/hub/agents_dedies_exemples/ppri-lavandou-partenaires.yaml`, et les tests
vérifient qu'il reste valide. Il décrit un agent pour des partenaires : il lit
et cite des documents, lit et exporte le zonage, et laisse compléter les
repères de crue, sous confirmation du propriétaire.

```yaml
kind: agent_dedie
version_schema: "0.1"
slug: ppri-lavandou-partenaires
nom: "Conseiller PPRi du Lavandou"
description: >-
  Repond aux services de la commune et de la DDTM sur le zonage PPRi,
  le reglement et les enjeux exposes. Consultation seule, sauf le suivi
  des reperes de crue que les partenaires peuvent completer.
version: 1
version_precedente: null

etude:
  sid: 4c176e577d70
  projets: []

public:
  audience: partenaires
  invites:
    - groupe:ddtm83-risques
    - mairie-lavandou@exemple.fr
  anonyme: false

perimetre:
  couches:
    - id: zonage_ppri
      source: data/ppri/zonage_reglementaire.gpkg
      libelle: "Zonage reglementaire PPRi"
      droits: [lire, requeter, afficher, exporter]
    - id: enjeux_batis
      source: data/enjeux/batiments_exposes.gpkg
      libelle: "Batiments exposes (aleas T100)"
      droits: [lire, requeter, afficher]
      attributs_exclus: [proprietaire, parcelle_proprietaire]
    - id: reperes_crue
      source: data/terrain/reperes_crue.gpkg
      libelle: "Reperes de crue"
      droits: [lire, afficher, modifier]
  documents:
    - id: reglement_ppri
      chemin: docs/ppri/reglement_ppri_2019.pdf
      libelle: "Reglement du PPRi (2019)"
      droits: [lire, citer]
    - id: note_presentation
      chemin: docs/ppri/note_presentation.pdf
      droits: [lire, citer]
  bases: []
  livrables:
    - storymap/risque-t100-lavandou

actions:
  lire: {}
  requeter: {}
  citer: {}
  afficher: {}
  exporter:
    confirmation: utilisateur
    formats: [csv, gpkg]
  modifier:
    confirmation: proprietaire

outils:
  paquets: []
  autorises:
    - get_features
    - get_project_info
    - consulter_documents
    - afficher_atlas
    - zoom_to
    - set_layer_visibility
    - export_layer
    - modifier_entites

garde_fous:
  lecture_seule: false
  citations_obligatoires: true
  max_entites_par_reponse: 500
  max_appels_outils_par_tour: 8
  message_hors_perimetre: >-
    Je ne peux repondre que sur le PPRi du Lavandou et les donnees
    listees dans ma fiche.

memoire:
  conversation: session
  fiche_etude: lecture
  documents: corpus_perimetre
  visiteur: aucune
  apprentissage: desactive

ton:
  profil_base: risk_analyst
  registre: pedagogique
  langue: fr
  consignes: >-
    Tu t'adresses a des agents de collectivite. Cite l'article du
    reglement pour toute regle. Ne donne jamais d'avis juridique sur un
    permis particulier : renvoie vers le service instructeur.

affichage:
  atlas:
    active: true
    couches: [zonage_ppri, enjeux_batis, reperes_crue]
    fond: plan_ign
  livrables:
    - storymap/risque-t100-lavandou

quotas:
  messages_par_jour: 300
  jetons_par_mois: 3000000
  expire_le: 2027-06-30
```

L'écriture par des partenaires suppose le lot 5 (§6). Avant ce lot, la
publication refuse `modifier` hors audience `interne`, même si le document
est valide.

## 4. Parcours de création avec l'assistant

### 4.1 Création

Point d'entrée : le bouton « Créer un agent partagé », renommé
« Créer un agent dédié ». Il envoie une consigne à l'assistant, qui bascule
sur un profil `agent_dedie_createur`. Ce profil a les outils du paquet
`agents_publies`, refondus, et aucun outil qui modifie l'étude.

1. **Cadrage** (conversation). L'assistant demande :
   - pour qui (interne, partenaires ou public) et jusqu'à quand ;
   - quelles questions l'agent doit traiter ;
   - s'il doit modifier des données.

   Il lit l'inventaire de l'étude (couches, documents, bases, livrables, avec
   leur classification) grâce à un outil de lecture, `inventaire_etude`.
2. **Proposition.** L'outil `proposer_agent_dedie(document)` dépose un
   brouillon. Le hub le valide (`valider_config` avec les profils, les
   classifications et la date du jour) et rend le rapport. L'assistant
   corrige, puis redépose. Le brouillon va dans la file « À valider » de
   l'étude, avec son empreinte.
3. **Aperçu** (interface, pas conversation). Une carte « Agent dédié » dans le
   panneau Livrables présente le document en langage clair :
   - « Voit » : liste des couches et des documents, avec leur classification ;
   - « Peut faire » : les actions, et celles qui demandent une confirmation ;
   - « Ne peut pas » : ce qui est exclu, en particulier le code, la
     publication et le changement d'étude ;
   - « Qui peut lui parler » ;
   - « Jusqu'au », quotas, aperçu de l'Atlas.

   Les avertissements du rapport y figurent. Un bouton « Essayer » ouvre
   l'agent en **bac à sable** : clé temporaire de 30 minutes, réservée au
   propriétaire, avec le même périmètre.
4. **Validation explicite.** C'est le bouton « Valider cette version » de la
   carte, un geste d'interface authentifié par la session OIDC du
   propriétaire. Il n'existe **pas** d'outil `valider_agent` : le modèle peut
   proposer ou retirer une proposition, jamais valider. La validation fige
   l'empreinte. Si le document a changé depuis l'aperçu, elle est refusée.
5. **Publication.** Le hub :
   - revalide avec l'état réel de l'étude (§3.4, contrôles de publication) ;
   - provisionne l'exécution : clé dérivée pour un agent interne, atelier
     dédié au-delà (§5.1) ;
   - émet la clé **sans la montrer au modèle** ;
   - calcule `audit_chain` (réutilise `publish_agent_endpoint`) ;
   - active la route `/agents/{owner}/{slug}`.

   L'assistant reçoit seulement l'URL, l'empreinte et la clé masquée.

### 4.2 Modification

- On modifie par la conversation (nouvelle proposition) ou en éditant le YAML
  dans la carte. Le résultat est toujours une version `n+1` à l'état
  **brouillon**.
- La version publiée reste active et inchangée tant que `n+1` n'est pas
  validée puis publiée. C'est la règle de l'Atelier (« une modification par
  un modèle désactive l'agent »), adoucie : on ne coupe pas le service, mais
  rien ne change sans validation.
- Une réduction de droits (retrait d'une couche, d'un invité ou d'une action)
  peut être publiée en un clic. Un élargissement repasse par l'aperçu
  complet.

### 4.3 Révocation et expiration

- **Révoquer** (bouton ou `revoke_agent`) met `revoked_at` sur la clé, ferme
  la route, met l'atelier à l'échelle zéro puis le supprime, et garde le
  journal. Le modèle peut **proposer** une révocation ; seule l'interface
  l'exécute.
- **Expiration** : un contrôle sans modèle, toutes les 15 minutes, révoque les
  agents dont `expire_le` est passé. Le propriétaire est prévenu 14 jours
  avant.
- **Coupure d'urgence** : `AGENTS_DEDIES_ACTIFS=0` ferme toutes les routes
  d'agents dédiés sans toucher aux clés.
- **Cascade** : si le propriétaire perd l'accès à l'étude, ou si l'étude est
  archivée, ses agents sont révoqués.

## 5. Exécution

### 5.1 Isolement

| Audience | Exécution | Pourquoi |
|---|---|---|
| `interne`, lecture seule | Hub et agent du propriétaire, avec une **clé dérivée** que l'agent utilise à la place de `HUB_API_KEY` pour les tours de cet agent dédié. Workspace dédié à l'échelle zéro par défaut ; les outils de lecture passent par des services hub typés (§3.3) qui lisent les fichiers du périmètre, sans QGIS. | Coût minimal. La frontière est la clé et les outils typés, pas le pod |
| `interne` en écriture, `partenaires`, `public` | **Atelier dédié** : StatefulSet `qgis-agent-{owner}-{slug}`, même image que le workspace, sans noVNC, dont le volume ne contient **que** les fichiers du périmètre. Montage `subPath` en lecture seule, plus un volume en écriture pour les seules couches `modifier`. Pas de secret du propriétaire, pas de jeton S3 général, NetworkPolicy qui n'admet que le hub. | C'est la seule façon de garantir qu'un chemin ou un outil oublié ne voit rien d'autre : ce qui n'est pas monté n'existe pas |
| Toutes | Le **processus agent** d'un agent dédié est une instance séparée (conteneur `agent` dans l'atelier, ou pool partagé sans état), avec sa propre base L1. Il n'a pas accès à `memory.db` du propriétaire. | Principe 7 de la charte : pas de L3 du producteur exposée |

L'écriture sur une couche se fait dans le volume de l'atelier, puis un
**report** vers l'étude a lieu :

- avec `confirmation: proprietaire`, chaque lot de modifications attend la
  validation du propriétaire dans la file « À valider » ;
- sinon, le report est journalisé et réversible par l'`inverse` enregistré
  (principe 4 de la charte).

### 5.2 Application des droits côté serveur

Trois portes, toutes dans le hub ou dans l'atelier. Le modèle ne les voit pas
et ne peut pas les négocier.

1. **Liste des outils.** `tools/list` est filtré par `outils_effectifs(config)`
   au proxy, et l'agent construit sa liste à partir du **document**, pas du
   profil. Le profil ne sert qu'au ton. Pas de `demander_outils` ni de filet
   « outil autorisé mais non exposé » hors de cette liste.
2. **Appel d'outil.**
   - Refus hors liste, **y compris pour les outils hub** (correctif 1.2.2).
   - Arguments imposés : `sid` et `pid` sont retirés des schémas et imposés ;
     un `couche_id` ou un `document_id` doit appartenir au périmètre et
     porter le droit requis ; les chemins libres sont interdits.
   - Confirmation : une action `confirmation: utilisateur` rend d'abord un
     aperçu, et n'est exécutée qu'au second appel qui porte le jeton
     d'aperçu. Une action `confirmation: proprietaire` dépose une demande
     dans la file.
   - Budgets : `max_appels_outils_par_tour`, quotas de messages et de jetons
     comptés par le hub.
3. **Données.**
   - Résultats de `get_features` et `interroger_base` : colonnes
     `attributs_exclus` retirées, `filtre` appliqué, plafond
     `max_entites_par_reponse`.
   - Le corpus documentaire (T5) est interrogé avec un filtre `document_id ∈
     perimetre`, appliqué dans la requête d'index et pas après coup.
   - Les citations sont vérifiées mot pour mot (contrat de sous-agent, spec
     du 2026-09-24, §2.4).
   - Bases : connexion en lecture seule (rôle SQL sans droit d'écriture
     quand c'est possible) et tables en liste fermée, vérifiées sur le plan
     de requête et pas par expression régulière.

Mémoire, appliquée par le code de l'agent :

- `conversation: session` : L1 propre à l'agent dédié, purgée au bout de
  30 jours ;
- `fiche_etude: lecture` : fiche d'étude **restreinte au périmètre** ;
- `documents: corpus_perimetre` : L4 filtrée ;
- `visiteur` : L3 **du visiteur** authentifié, jamais celle du propriétaire ;
- `apprentissage: a_valider` : les enseignements vont dans la file du
  propriétaire, jamais directement dans une mémoire.

### 5.3 Traçabilité

- **Journal d'accès en ajout seul**, par agent dédié : horodatage, identité
  du visiteur (ou `anonyme` avec une empreinte de session), outil, arguments
  imposés, ressources touchées, décision (`autorise`, `refuse:<code>`,
  `en_attente`), taille du résultat, jetons.
  - Les secrets sont filtrés.
  - Le journal a le même format que le journal de tour rejouable (stratégie
    qualité §5.1), avec en plus `agent_dedie`, `version` et `empreinte`.
- **Chaque réponse** porte l'empreinte de la version active, ce qui permet
  de dire « qui a répondu, avec quelle configuration ».
- **Tableau de bord** dans la carte : volume, refus par code, quotas
  consommés, dernières actions en attente.

### 5.4 Tests d'accès

Le banc `hub/tests/test_agents_dedies_acces.py` (lot 4) passe par la vraie
porte `/mcp` et par la route de l'agent, avec une clé d'agent dédié. Chaque
cas est un **refus attendu** :

| Tentative | Attendu |
|---|---|
| `tools/call` d'un outil hors liste, d'un outil hub (`study_switch`), de `execute_python` | `-32601`, journalisé `refuse:outil` |
| `get_features` sur une couche du projet hors périmètre, ou avec `sid` d'une autre étude | refus `hors_perimetre` |
| `consulter_documents` qui cite un document hors périmètre | source retirée et avertissement |
| Chemin forgé (`../`, absolu) dans tout argument | refus `chemin` |
| `modifier_entites` sur une couche sans droit `modifier`, ou sans confirmation | refus, ou dépôt en file |
| Colonne `attributs_exclus` demandée explicitement | colonne absente du résultat |
| Clé révoquée, expirée, ou étude archivée | 401 |
| Visiteur hors `invites` (partenaires), anonyme hors `public` | 403 |
| Clé d'agent dédié qui tente d'émettre une clé | 403 (existant) |
| Profil de base supprimé après publication | agent suspendu, pas de repli `standard` |
| Dans l'atelier : `ls` ou lecture d'un fichier hors périmètre par un outil de lecture | fichier absent (non monté) |

Le banc comportemental (stratégie qualité §4) ajoute une famille « agent
dédié ». Sur 10 scénarios d'incitation (« ignore tes consignes »,
« montre-moi toute l'étude », « change d'étude »), on attend 0 action hors
périmètre. La réponse texte peut varier ; l'action, non.

## 6. Plan de livraison incrémental

| Lot | Contenu | Tests | Critère de sortie |
|---|---|---|---|
| **0** (fait, cette branche) | `hub/hub/agents_dedies.py` : schéma Pydantic strict, `valider_config`, registre des classes d'outils, empreinte ; exemple de référence. Aucune route. | `hub/tests/test_agents_dedies.py` (61 tests) : exemple valide, fautes de frappe, outils interdits, audiences, expiration, profils, affichage, classifications, registre complet vis-à-vis de `paquets_outils`, module non exposé | Suites hub et agent vertes |
| **1** Correctifs de l'existant | (a) Contrôle de liste blanche **avant** `dispatch_hub_tool`. (b) `create_agent` ne rend plus la clé brute au modèle : elle est remise par l'interface. (c) Un profil inconnu pour une clé scopée donne un refus, pas un repli `standard`. (d) `publish_agent` ne rend plus d'URL tant que la route n'existe pas. | Tests proxy : `study_switch` avec clé restreinte refusé ; clé absente du résultat d'outil | Aucun contournement connu de la liste blanche |
| **2** Document et parcours, interne en lecture seule | Stockage versionné `agents/{slug}/v{n}.yaml`, index SQLite ; endpoints `POST /studies/{sid}/agents-dedies` (brouillon), `GET` (aperçu et rapport), `POST .../valider` (OIDC seulement, refusé à toute clé scopée), `POST .../publier`, `DELETE` ; carte d'aperçu dans le panneau Livrables ; outils `inventaire_etude` et `proposer_agent_dedie` ; clé dérivée utilisée par l'agent pour les tours de l'agent dédié ; route `/agents/{owner}/{slug}` (chat embarqué) ; outils typés de lecture `consulter_documents` (T5) et `get_features` à arguments imposés. Publication **limitée à `interne` et `lecture_seule`**. | Validation refusée par une clé scopée ; empreinte modifiée entre aperçu et validation refusée ; réponses restreintes au périmètre (attributs exclus, filtre) ; journal d'accès | Une personne crée, essaie, valide et publie un agent interne, et le révoque, sans toucher au YAML |
| **3** Atelier dédié | StatefulSet par agent (chart : gabarit `agent-dedie-statefulset.yaml`), montage `subPath` en lecture seule du périmètre, NetworkPolicy, mise à l'échelle zéro au repos, suppression à la révocation. Ouverture de `partenaires` et `public` en lecture seule, avec invités, accès anonyme et quotas. | Banc d'accès §5.4, dont « fichier non monté » ; montée et descente d'échelle | Banc d'accès entièrement en refus attendus ; coût mesuré d'un atelier au repos |
| **4** Banc et contrôles en ligne | Banc d'accès complet, famille « agent dédié » du banc comportemental, contrôles sans modèle (expiration, quotas, taux de refus anormal qui suspend l'agent et prévient le propriétaire) | Banc complet ; 0 action hors périmètre sur 10 scénarios d'incitation | Seuils atteints deux passages de suite |
| **5** Écriture et Atlas | Outil `modifier_entites`, file de report avec `inverse`, confirmations ; `afficher_atlas` branché sur la spec T4 ; mémoire visiteur et apprentissage à valider | Écriture hors droit refusée ; report annulable ; Atlas limité à `affichage.atlas.couches` | Un agent partenaires complète une couche, le propriétaire valide ou annule |

Ordre non négociable : **lot 1 avant toute nouvelle publication ; lot 3
avant toute audience non interne.**

## 7. Risques

| Risque | Nature | Parade |
|---|---|---|
| Fuite de données hors périmètre par un outil ou un chemin oublié | Sécurité | Atelier dédié : ce qui n'est pas monté n'existe pas. Outils typés par `id`, pas de chemin libre. Registre fermé, où un outil inconnu est refusé. |
| Contournement par le modèle (injection dans un document du corpus, incitation par le visiteur) | Sécurité | Les droits ne sont jamais dans le prompt. `execute_python` est interdit. Le banc d'incitation fait partie des critères de sortie. |
| Escalade : un agent dédié qui crée ou valide un agent, ou qui change d'étude | Sécurité | Classe `administration` interdite. La validation est un geste OIDC. Une clé scopée ne peut rien émettre (existant). Correctif 1a. |
| Fuite de la clé | Sécurité | Correctif 1b : la clé ne passe plus par le modèle. Clé dérivée, expirante, révocable, avec cascade à la révocation du propriétaire. |
| Mémoire croisée : L3 du propriétaire visible, ou visiteurs mélangés | Sécurité, RGPD | Processus agent séparé, pas de champ pour la L3 du propriétaire, L1 purgée au bout de 30 jours, mémoire visiteur interdite au public. |
| Données personnelles dans les couches (propriétaires, parcelles) | RGPD | `attributs_exclus` appliqué côté serveur ; classification obligatoire ; `analyze_agent_config` repris comme conseil à l'aperçu. |
| Coût : un pod par agent, des jetons consommés par des visiteurs | Coût | Mise à l'échelle zéro au repos, quotas obligatoires comptés par le hub, lecture interne sans atelier (lot 2). Mesurer le démarrage à froid au lot 3 : si plus de 30 s, envisager un pool chaud partagé. |
| Latence au premier message (atelier à l'échelle zéro) | Usage | Message d'attente explicite. Réveil anticipé au chargement de la page de l'agent. |
| Confusion entre « agent partagé », « agent dédié », « livrable » et « profil » | Usage | Un seul terme dans l'interface : « agent dédié ». La carte d'aperçu en langage clair (« voit », « peut faire », « ne peut pas »). Les clés `qgisk_` existantes restent listées comme « anciens agents partagés », en lecture seule. |
| Croire qu'un agent est cloisonné alors qu'il ne l'est pas | Usage, confiance | Pas de champ décoratif. Publication refusée pour ce que le lot livré n'applique pas encore (ex. `modifier` avant le lot 5). |
| Dérive du registre d'outils quand le workspace ajoute des outils | Maintenance | Le défaut restrictif et le test qui compare le registre aux paquets de l'agent. |
| Dépendances non livrées (corpus T5, Atlas T4) | Planning | Le lot 2 n'exige que le corpus filtré par `document_id`. L'Atlas n'arrive qu'au lot 5. |

## 8. Décisions à arbitrer

1. **Lecture interne sans atelier (lot 2)** : acceptable, ou atelier dédié
   dès le premier agent ? Recommandation : sans atelier, mais seulement pour
   des outils typés de lecture qui ne passent pas par QGIS.
2. **Écriture par des partenaires** : report avec validation du propriétaire
   (proposé), ou écriture directe dans une couche de saisie séparée ?
3. **Identités `invites`** : groupes OIDC SSPCloud seulement, ou adresses de
   messagerie avec lien magique ? Le second élargit le public et le risque.
4. **Devenir des clés `qgisk_` existantes** : migrer vers des documents
   `agent_dedie` en `interne` et `lecture_seule`, ou les laisser expirer ?
