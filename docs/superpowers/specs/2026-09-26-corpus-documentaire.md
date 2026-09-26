# Corpus documentaire de l'étude (lot L7, MVP)

Date : 2026-09-26. Branche `feat/corpus-documentaire`.
Référence : `2026-09-24-sous-agents-et-contexte-dynamique.md` (§2.3, §2.4, §3.3, L7).

## 1. Ce que fait le MVP

L'utilisateur dépose des documents dans son étude (panneau Ressources >
Sources > « Documents de l'étude »). Le hub les stocke, en extrait le texte,
les découpe et les indexe par étude, en tâche de fond. L'agent les consulte
par l'outil natif `consulter_documents(question, k)` : extraits courts, source
(document, page ou section) et score, résultat borné en jetons.

Ce n'est pas encore le sous-agent de la spec (gemma, 3 recherches, réponse
rédigée) : c'est son outil interne `recherche_documents`, exposé directement à
l'agent principal. Le sous-agent pourra l'appeler tel quel.

## 2. Formats

| Format | Extraction | Localisateur de citation |
|---|---|---|
| PDF | `pypdf` (repli `pdfminer.six` s'il est installé) | page ; titre numéroté de la page si détecté |
| DOCX | bibliothèque standard (zip + XML) | section (styles Titre / Heading, niveau de plan) |
| ODT | bibliothèque standard (zip + XML) | section (`text:h`) |
| TXT | UTF-8, sinon Windows-1252 | aucun (document entier) |
| MD | idem, titres `#` | section |
| CSV | `csv` (séparateur détecté) | « lignes a à b » ; un résumé (colonnes, nombre de lignes) |
| XLSX | bibliothèque standard (zip + XML) | « feuille, lignes a à b » ; un résumé par feuille |

Dégradations assumées : PDF numérisé sans couche texte refusé avec un message
(pas d'OCR) ; PDF protégé par mot de passe refusé ; images, notes de bas de
page, formules (seule la valeur enregistrée est lue) ignorées. Tout autre
format est refusé au dépôt (415) avec la liste des formats acceptés.

Dépendance ajoutée : `pypdf>=4.0` dans `hub/setup.py` (pur Python, sans
bibliothèque native, quelques centaines de Ko dans l'image hub). Aucune
dépendance ajoutée à l'agent. `python-docx`, `odfpy` et `openpyxl` ne sont pas
nécessaires.

## 3. Stockage et indexation

Sur le volume du hub (celui de `studies.db`), pas sur le PVC du workspace :

```
<DATA_DIR>/documents_etudes/<sid>/registre.json
<DATA_DIR>/documents_etudes/<sid>/fichiers/<id>.<ext>
<DATA_DIR>/documents_etudes/<sid>/segments/<id>.json
```

(`DOCUMENTS_RACINE` permet de le déplacer.) Raisons : le hub lit ce volume
directement, alors que le PVC du workspace n'est joignable que par
`execute_python` dans un pod qui peut dormir ; un dossier par étude rend une
fuite inter-études structurellement impossible.

Statuts : `en_attente` -> `extraction` -> `indexe` ou `erreur` (message
lisible). Un statut transitoire sans tâche active depuis plus de 2 minutes
(redémarrage du hub) est affiché en `erreur` « interrompue, relancez ».
Extraction dans un thread, deux à la fois au plus.

Segments : 1 000 caractères au plus, recouvrement d'environ 150, jamais à
cheval sur deux pages ou deux sections.

**Pourquoi pas `vector_store` / `embed_worker`.** Ils vivent dans `memory.db`
sur le volume de l'agent (RWO, distinct du hub), dans une table commune à
toutes les études où le KNN de sqlite-vec s'applique avant le filtre d'étude ;
et le hub n'a pas la clé du modèle d'embedding dans son environnement. Le
MVP indexe donc en BM25 côté hub (mots sans accents ni casse, mots vides
français, racinisation légère), sans réseau ni dépendance. L'agent **reclasse**
ensuite les candidats (3 k, 20 au plus) par similarité sémantique avec
`vector_store.embed_batch` (un appel, 8 s au plus), score final moitié lexical,
moitié cosinus ; si l'API d'embedding échoue, l'ordre lexical est gardé
(`AGENT_DOCUMENTS_RECLASSEMENT=0` coupe le reclassement). Limite connue : une
question sans aucun mot commun avec le document ne remonte rien (rappel
lexical) ; un index vectoriel par étude est l'étape suivante.

## 4. API du hub

Toutes les routes exigent l'authentification habituelle et vérifient que
l'étude appartient à l'utilisateur (`studies.get_study(sid, username)`) :
une étude d'autrui répond 404, comme une étude inexistante.

| Méthode et route | Effet | Réponse |
|---|---|---|
| `GET /studies/{sid}/documents` | liste | `{sid, documents[], resume{total, indexes, en_cours, erreurs}, limites{taille_max_mo, max_documents, volume_max_mo, caracteres_max, formats[]}}` |
| `POST /studies/{sid}/documents` (multipart `file`) | dépôt + indexation en tâche de fond | 202 `{document}` ; 400 nom ou fichier vide ; 413 taille ou volume ; 415 format ou contenu incohérent ; 409 doublon ou nombre max |
| `GET /studies/{sid}/documents/recherche?q=&k=&longueur=` | recherche dans l'étude seule | `{statut: ok \| vide \| aucun_document, documents[], en_cours, resultats[{segment, doc_id, titre, nom_fichier, page, section, score, couverture, extrait}]}` |
| `GET /studies/{sid}/documents/{id}` | fiche et statut | `{document}` |
| `GET /studies/{sid}/documents/{id}/fichier` | original | pièce jointe, `application/octet-stream`, `nosniff` |
| `POST /studies/{sid}/documents/{id}/reindexer` | relance | 202 ; 409 si déjà en cours |
| `DELETE /studies/{sid}/documents/{id}` | retrait (fichier, segments, registre) | 204 |

Document : `id, titre, nom_fichier, format, taille, date_ajout, auteur,
statut, message, n_pages, n_segments, n_caracteres, avertissements,
date_indexation`.

`extrait` est une sous-chaîne exacte du texte indexé (fenêtre de 450
caractères par défaut autour des termes trouvés) : la citation est vérifiable
par le code. `score` est relatif (1 = meilleur résultat), `couverture` la part
des termes de la question présents dans le segment.

## 5. Outil agent `consulter_documents`

Natif, dispatché localement (`agent/agent/documents_etude.py`), sur l'étude
**active** seulement (`/studies/active`, puis la recherche du hub).

Entrée : `question` (obligatoire), `k` (1 à 8, défaut 4).
Sortie (sous-ensemble du contrat §2.4) :

```json
{"statut": "ok | vide | hors_perimetre | erreur",
 "question": "…", "documents_consultables": 3,
 "consigne": "Réponds à partir de ces extraits seulement. Cite la source…",
 "sources": [{"id": "doc:Rapport phase 1#p12", "titre": "Rapport phase 1",
              "page": 12, "section": "2. Zones humides", "score": 0.91,
              "extrait": "…"}],
 "avertissements": [],
 "verification": {"classement": "lexical+semantique", "candidats": 12, "duree_ms": 640}}
```

Bornes : extraits de 450 caractères au plus, résultat sérialisé sous 6 000
caractères (environ 1 500 jetons) ; au-delà, les derniers extraits tombent
avec un avertissement.

La description de l'outil impose de citer la source et de ne pas extrapoler
au-delà des extraits.

Exposition : paquet `documents` (déclencheurs : document, rapport, PDF,
cahier des charges, CCTP, compte rendu, note de…, selon le…, d'après le…,
que dit…), et **retiré tant que l'étude n'a aucun document indexé**. La L2
porte alors une ligne courte : « 3 documents d'étude consultables (« … »,
« … », « … ») : consulter_documents, en citant la source. » Sans déclencheur,
le modèle peut l'obtenir par `demander_outils`, ou l'appeler directement (le
filet l'exécute s'il est dans le profil).

## 6. Limites (variables d'environnement du hub)

| Variable | Défaut |
|---|---|
| `DOCUMENTS_TAILLE_MAX_MO` | 25 Mo par document |
| `DOCUMENTS_MAX_PAR_ETUDE` | 50 documents |
| `DOCUMENTS_VOLUME_MAX_MO` | 250 Mo par étude |
| `DOCUMENTS_CARACTERES_MAX` | 2 000 000 caractères indexés par document |

Autres bornes : 2 000 pages par PDF, 5 000 lignes par tableau, 40 Mo
décompressés par entrée XML d'une archive bureautique.

## 7. Sécurité

- Isolement : contrôle du propriétaire sur chaque route ; un dossier par
  étude ; la recherche ne lit que ce dossier ; l'agent n'interroge que l'étude
  active. Identifiants d'étude et de document validés par expression
  régulière (pas de traversée de chemin) ; le nom de fichier d'origine n'est
  jamais utilisé comme chemin.
- Contenu : extension en liste blanche et contenu vérifié (signature `%PDF-`,
  archive zip contenant la partie attendue, pas d'octet nul pour le texte).
  Lecture du dépôt bornée à la taille maximale.
- Archives : bombe de décompression bornée ; expat (bibliothèque standard)
  protège des entités en cascade.
- Restitution : téléchargement toujours en pièce jointe, en octets bruts,
  `nosniff` ; l'interface pose le texte du serveur par `textContent`.
- Purge d'une étude (`DELETE /studies/{sid}?purge=true`) : son corpus est
  supprimé. L'archivage le conserve.
- Donnée professionnelle : aucun envoi externe à l'indexation ; seul le
  reclassement envoie la question et les extraits candidats à l'API
  d'embedding de la plateforme (la même que la mémoire de l'agent).

## 8. Interface

Bloc autonome `hub/templates/_documents_etude.html` + `hub/hub/static/documents_etude.js`,
inclus dans l'onglet Sources par une seule ligne de `desk.html` (le panneau
est retravaillé par une autre équipe). Liste (titre, format, taille, pages,
statut), ajout par bouton ou glisser-déposer (qui ne déclenche pas le dépôt
de données QGIS du panneau), suivi du statut toutes les 3 s tant qu'un
document est en cours, relance d'une indexation en erreur, téléchargement,
retrait avec confirmation (`qsConfirmer`, bouton « Retirer »).

## 9. À valider en live

1. Persistance du volume hub après redéploiement (le dossier
   `documents_etudes/` doit survivre comme `studies.db`).
2. Extraction de vrais PDF métiers (rapports Cerema, CCTP) : qualité du texte,
   titres de page, temps pour 200 pages.
3. Reclassement sémantique : l'API d'embedding répond avec la clé de l'agent,
   latence ajoutée.
4. Dépôt de 25 Mo via l'ingress (`proxy-body-size: "0"` dans le chart,
   donc sans limite côté ingress ; à confirmer sur le proxy Onyxia).
5. Comportement de qwen3-6-35b-moe : citations effectivement reprises,
   refus d'extrapoler sur une question hors corpus.
6. Coexistence avec la refonte du panneau Ressources (équipe T1).
