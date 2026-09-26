# Comportements agent — traitements spatiaux (fidélité territoriale)

Date : 2026-09-24.  
Objectif : **traquer**, classifier et corriger les écarts entre intention user et résultat QGIS — sans empiler des patches ad hoc.

Référence produit : « charge les bâtis sur Aix-en-Provence » doit donner **uniquement** les bâtis d’Aix (filtre à l’import + vérif, **ou** commune + clip + couche nommée).

---

## 1. Classe de défaut (à surveiller en priorité)

| Code | Nom | Symptôme typique |
|------|-----|------------------|
| **T1** | Zone absente / trop large | `smart_load` sans `bbox` utile, ou bbox département / France |
| **T2** | Bbox ≠ contour | Rectangle autour d’Aix inclut Gardanne, Les Milles hors commune, etc. |
| **T3** | Pas de vérif post-chargement | Compte / emprise non contrôlés ; l’agent dit « c’est fait » |
| **T4** | Pas de clip quand le filtre import est insuffisant | BD TOPO / WFS renvoie hors commune ; pas de `clip` / `intersection` |
| **T5** | Couche mal nommée / non conservée | Résultat temporaire, pas de `bati_aix_en_provence` persisté |
| **T6** | Confusion famille URL / livrable | Hors scope ici (déjà traité) — ne pas mélanger |

**Comportement attendu (canon)** pour « données X sur territoire Y » :

1. `set_study_zone(target=Y)` → bbox **et** idéalement contour commune.
2. Charger X avec cette bbox (filtre à l’import si le catalogue le permet).
3. Si la source est polygonale / trop large → charger limite Y + **clip** / sélection par localisation.
4. Vérifier (count, extent vs zone) ; sinon corriger.
5. Renommer / sauver la couche résultat dans l’étude.

Le prompt impose déjà (2ter) : L2 zone = vérité pour `bbox` ; sinon `set_study_zone` d’abord.  
Ce qui **manque** souvent en pratique : clip contour + **preuve** de conformité + nommage.

---

## 2. Matrice de scénarios (à jouer live Chrome / desk)

Jouer **un scénario = une conversation neuve**, noter outils appelés + résultat spatial.

| ID | Demande user | Attendu minimal | Fail si… |
|----|--------------|-----------------|----------|
| S1 | « Charge les bâtis sur Aix-en-Provence » | Zone Aix → bâti filtré ou clipé ; couche dédiée | Bâti hors commune visible ; bbox vide / France |
| S2 | « Uniquement le bâti dans le périmètre communal d’Aix » | Contour commune + clip explicite | Bbox seule sans clip |
| S3 | Zone déjà active Lavandou, user dit « charge le bâti à Aix » | Reset / nouvelle zone Aix (pas réutiliser Lavandou) | Charge dans l’ancienne bbox |
| S4 | « Combien de bâtiments à Aix ? » | Chiffre cohérent avec couche filtrée | Invente un nombre / compte la couche non clipée |
| S5 | « Routes dans le 4e arr. de Marseille » | Sous-zone (IRIS / arr.) pas Marseille entière | Ville entière |
| S6 | Recette / processing sur « ma zone » | Réutilise bbox L2 | String « Marseille » sans coords |

Après chaque run, remplir une fiche (ci-dessous).

---

## 3. Fiche d’observation (copier-coller)

```
Date / étude :
Scénario (S#) :
Demande exacte :
---
Tools (ordre) :
  1.
  2.
---
Zone utilisée (nom + bbox / contour ?) :
Couches créées (noms) :
Vérif faite par l’agent ? (count / extent / clip) : oui/non
---
Code défaut (T1–T5) :
Preuve (capture / count hors zone) :
Correction proposée (prompt / tool / recette) :
```

---

## 4. Où corriger (une fois le défaut classé)

| Cause racine | Levier | Ne pas faire |
|--------------|--------|--------------|
| Prompt trop faible sur « bbox ≠ territoire » | Bloc prompt dédié **fidélité AOI** + exemples Aix/clip | Empiler 10 phrases contradictoires |
| `smart_load` ne clippe jamais au polygone | Tool / helper post-load `clip_to_study_zone` | Laisser l’agent `execute_python` ad hoc à chaque fois |
| Pas de vérif | Hook post-`smart_load` : injecter extent vs zone active | Compter sur le LLM pour se souvenir |
| Catalogue WFS trop large | Param filtre serveur / bbox obligatoire | Télécharger le département puis oublier |
| Sous-commune vs commune | `set_study_zone` + IRIS / arr. (déjà partiellement dans le prompt) | Géocoder au centre-ville seul |

Principe : **outil fiable > prompt**. Si T2/T4 reviennent, un helper `ensure_layer_in_study_zone(layer)` vaut mieux qu’une autre phrase système.

---

## 5. Lien avec le bilan global

- P0 images/redéploy : **prérequis** pour tester sur code à jour (sinon on corrige l’overlay).
- Ce chantier = **P1 comportemental** après redéploy (ou en parallèle sur overlay si on accepte le biais).
- Ne pas ouvrir `/agent-share` / BlockNote tant que S1–S3 ne sont pas stables.

---

## 6. UX conversation — progression / chargement (à traiter avec les T*)

Constat live S1 + vérif (2026-09-24) : le fil utile est **dupliqué**.

| Élément | Où aujourd’hui | Problème |
|---------|----------------|----------|
| « Analyse / Réflexion / Calcul… » | Bulle `#current-response` **et** `#status-bar` (même texte, `display:flex`) | Double affichage ; le bandeau au-dessus de l’input reste le plus visible |
| « Outil : … » / durée | Idem (bulle + status-bar) | Pendant `execute_python`, status-bar peut dire « Rédaction… » alors qu’un outil tourne |
| Arrêter | À côté de l’input | OK ; orphelin du statut une fois qu’on regarde la bulle |
| Tools (`blockquote`) | Dans le flux messages | Bien si Technique ON |
| Persistance SSE | Historique session | Rechargement desk / perte focus → **réponse assistant disparaît** (reste le message user) ; messages API auth côté pod |

**Direction produit (MLI)** : un seul endroit — la bulle / le tour en cours. Masquer ou vider `#status-bar` quand `#current-response` porte déjà le statut.

Pistes :
1. Statut + Arrêter **dans** `#current-response` uniquement.
2. Ne plus afficher « Rédaction » si un tool est en vol.
3. Persister le tour assistant (checkpoints / messages) pour survivre à un reload desk.

---

## 7. Observation S1 live (2026-09-24) — **ÉCHEC / fiche complète**

```
Date / étude : Saint-Martin — potentiel éolien (étude active) ; zone study → Aix-en-Provence
             OVERLAY live, images GHCR stale ; modèle qwen3-6-35b-moe
Scénario : S1 (+ message VÉRIFICATION S1 après perte SSE du 1er run)
Demande : bâtis Aix uniquement + filtre/clip + vérif count/emprise + nom bati_aix_en_provence ; ne pas publier
---
Tools (ordre 1er run) :
  1. set_study_zone(target=Aix-en-Provence)     OK (_context.study_zone=Aix)
  2. list_datasources(search≈bati)
  3. add_from_catalog(bdtopo_batiments, bbox Aix, name=batiments_bdtopo_aix_emprise)
  4. add_layer / execute_python (commune_aix*, geo.api.gouv, WFS IGN, GeoJSON…)
  5. execute_python ×N — boucles d’erreurs (StopIteration, attr manquant)
Vérif run : list layers → retry contour geo.api.gouv → Auto-stop boucle d’erreur
            (×3 « X object has no attribute X » sur execute_python, iter≈7)
---
Zone utilisée : set_study_zone Aix OK (label) ; pas de clip final
Couches créées (preuve bridge 2026-09-24, /api/command execute_python) :
  - batiments_bdtopo_aix_emprise : count=51_450_444, CRS=EPSG:4326,
    extent=[-63.28, -21.77, 55.9, 51.97]  ← FRANCE / outre-mer, PAS Aix
  - commune_aix_en_provence (×2) : count=1, extent≈[5.27, 43.45, 5.51, 43.63] OK contour
  - commune_aix (×3) : count=1, extent POINT [5.39, 43.54] (pas un polygone utile)
  - bati_temp / communes_temp : invalid, count=0
  - bati_aix_en_provence : ABSENT
Vérif faite par l’agent ? non (auto-stop avant clip ; pas de bilan count/emprise)
---
Codes défaut :
  T1 — bbox catalogue/WFS ignorée (51M features, emprise nationale)
  T4 — pas de clip malgré contours commune présents
  T3 — pas de vérif post-chargement
  T5 — pas de couche résultat nommée ; litter de couches temp / doublons commune_*
  UX — statut double (bulle + #status-bar) ; perte réponse après reload desk
Preuve : kubectl exec workspace → POST /api/command ; logs agent
         « Auto-stop boucle d'erreur » ; DOM statusBar+currentResponse
Correction proposée (priorité) :
  1. P0 outil : add_from_catalog / WFS doit appliquer bbox (ou refuser si count aberrant)
  2. helper clip_to_study_zone(layer) après load polygone + rename
  3. garde-fou : si featureCount > seuil vs zone → forcer clip ou fail explicite
  4. UX : statut uniquement dans la bulle (§6)
```

**Verdict S1** : intention correcte (zone + catalogue + contour), **résultat spatial faux** (bâti national). Ne pas rejouer S2 avant correctif T1.

---

## 8. Prochaine session concrète

1. ~~Finir / stopper S1~~ → auto-stop + fiche §7 complète.
2. **P0** : root cause `add_from_catalog` / WFS bbox → 51M (test unitaire + live).
3. Helper `clip_to_study_zone` + nommage résultat (T4/T5).
4. Spec UX courte : un seul statut dans la bulle (pas `#status-bar` dupliqué).
