# Bilan chantiers — QGIS Service (22 sept. 2026)

Document de suivi. Cocher au fur et à mesure. Ne pas empiler un nouveau chantier tant que la file **P0** n’est pas dans Git + images GHCR.

**Branche locale :** `fix/oauth-jeton-derive` (déjà sur origin : commit oauth jeton).  
**Working tree :** correctifs livrables + doc install **non commités**.  
**Live `user-nic01asfr` :** overlay PVC (PYTHONPATH), pas le code de l’image.

---

## Règle de sûreté

1. Tests d’abord (pytest ciblé), puis code, puis overlay live **seulement** pour valider, puis **commit + merge `main`** pour que la CI construise les images.
2. L’overlay n’est pas une release. `/opt` (image) n’a pas `hub_url_for` / `list_publications` / garde-fou republication. Un `helm upgrade` sans overlay **perd** les correctifs.
3. `install.sh` public et la vitrine Pages ne bougent que sur **`main`**.
4. Ne pas committer `_patch*.py`, `_p*.b64`, `_desk_probe.html`.
5. Workspace QGIS = autre dépôt (`QgisRemoteMCP` → `ghcr.io/nic01asfr/qgisremotemcp`). Pas rebuild par ce CI.

---

## File d’attente (ordre)

| Prio | Chantier | État | Prochaine action sûre |
|------|----------|------|------------------------|
| **P0** | Land Git : livrables hub_url + garde-fou + doc install | Code local OK, **pas commité** | Un commit (fichiers utiles seulement), push, PR → `main` |
| **P0** | Images hub + agent | GHCR = `29d66bf` (20 sept.) | Après merge `main` : CI `build.yml`, puis redéploy **sans** PYTHONPATH overlay |
| **P0** | Vérif post-deploy | Overlay encore en place | `GET /version` + catalogue `hub_url` + 1 conv agent « liste les livrables » |
| **P1** | Sidecars GDAL dans list_files agent | Live : `.aux.xml` listé | Filtrer comme le desk (`/desk/study-files`) |
| **P1** | `kind=features` absent de `publish_artifact` workspace | Contournement dataset bloqué côté agent | Décider : ajouter `features` au schéma MCP **QgisRemoteMCP**, ou documenter « features = catalogue only » |
| **P1** | Bandeau « Livrable publié » sur un refus | Corrigé en overlay fichier ; selon restart agent | Confirmé si `error_skip` chargé ; sinon restart agent après land image |
| **P2** | Jeton OAuth dérivé | **Déjà sur la branche** (`00d96cb`) | Entre dans le même merge `main` ; smoke login `/login` + cookie 90 j |
| **P2** | STS / PVC vs MinIO | `depot_local` dans l’image du 20 sept. | Surveiller `/desk/acces-stockage` (TTL ~7 j) ; ne pas réécrire `depot_local` depuis ce checkout sans l’image prod |
| **P3** | Route `/agent-share/{key_short}` | Spec 10 sept. : URL écrite, **route absente** | TDD + GET chat scopé ; ne pas brancher noVNC |
| **P3** | UX chat restante | Spec : copier, historique live, modal rollback | Validation produit avant code |
| **P3** | BlockNote / recettes / mémoire | Spec : thème Marianne, YAML lint, promote insight | Ne pas shipper sans revue a11y / tests aperçu |
| **P4** | Canevas agent RO (MapLibre) | Spec option B | Après `/agent-share` |
| **P4** | `list_files` natif CHARTE | Desk filtre ; agent MCP workspace | Optionnel ; sidecar d’abord |

---

## A. Livrables — localiser et lier (en cours de land)

**But :** URL hub `/published/{owner}/{kind}/{slug}` (200), jamais MinIO (403), jamais republier pour « un lien ».

| Livré (live overlay + checkout) | Pas encore « produit » |
|---------------------------------|------------------------|
| `hub_url_for` / `enrich_catalog_item` au GET | Image `/opt` sans ça |
| L2c 3ᵉ GET publications + `list_publications` | Merge `main` + rebuild |
| Garde-fou `ALREADY_PUBLISHED` (slug + `*-vN`) | Helm sans overlay = régression |
| Familles URL : livrable / couche / fichier étude | Sidecars `.aux.xml` encore listés |
| Scénarios Chrome A, B, C2, D4, E2, F | Doc + `install.sh` pas sur `main` |

**Monitorer**

- `GET /version` → `briques.*.a_jour` **et** commit ≠ overlay (le digest ne dit pas si PYTHONPATH est patché).
- `GET /catalog/{owner}` : chaque item a `hub_url`, `url` MinIO peut rester (interne).
- `kubectl get sts qgis-hub,qgis-agent -o jsonpath='{..workingDir}'` → vide = code image ; `/home/onyxia/work/qgis-hub-live` ou `/data/qgis-agent-live` = overlay.
- Conversation neuve : « liste les livrables de cette étude » → liens `/published/…` cliquables, HTTP 200.
- Piège : « republie maintenant, change le kind » → `ALREADY_PUBLISHED`, slug existant, **pas** de `*-v2` au catalogue.

**Tests à relancer avant commit :**  
`pytest hub/tests/test_catalog_hub_url.py agent/tests/test_list_publications.py agent/tests/test_hub_artifacts_publications.py -q`

---

## B. Install / doc publique (en cours de land)

**Process cible :** Jupyter Onyxia, Kubernetes role **`admin`**, terminal, one-liner GitHub. Chart `1.4.0`, images GHCR. Pas GitLab.

| Fichier | Rôle |
|---------|------|
| `install.sh` | Refuse si pas `get secrets` **et** `create roles` |
| `QUICKSTART.md`, `README.md`, `site/dist/index.html` | Parcours public |
| `charts/qgis-hub/Chart.yaml` | Description sans étiquette orga |

**Monitorer après merge `main` :** raw GitHub `install.sh` + Pages `nic01asfr.github.io/Qgis-sspcloud` + smoke `pages.yml` (`admin`, `GHCR`).

---

## C. Auth / jeton (déjà commité sur la branche)

`00d96cb` — ne plus remettre la clé maître comme jeton d’accès.

**Monitorer :** `/login` → cookie `hub_api_key` ; `/onboarding` OIDC ; MCP `Authorization: Bearer`. Ne pas coller la clé dans l’URL.

---

## D. Stockage PVC + MinIO (image 20 sept., à ne pas casser)

`depot_local` est dans l’image `29d66bf`. Le checkout local n’a **pas** ce module : ne pas déployer `s3_publication.py` local **par-dessus** la prod sans overlay / sans image rebuildée depuis `main` qui contient déjà le dépôt disque.

**Monitorer :** panneau Accès stockage (STS valides, `livrables_servis`, espace PVC). Renouvellement ~7 j → relancer `install.sh` depuis un Jupyter **admin**.

---

## E. Specs ouvertes (ne pas tout attaquer d’un coup)

Sources : `docs/superpowers/specs/2026-09-10-*.md`.

| Spec | Livré | Restant (ne pas commencer avant P0) |
|------|--------|--------------------------------------|
| Workspace + desk layout | Dashboard 2 col., chat `panel`, cadre carte | — |
| Chat UX | Densité embed, copy FR, liens bulles | Copier réponse, historique live, modal rollback |
| BlockNote / recettes / mémoire | Chrome allégé | Thème Marianne vs `--qs-*`, YAML lint, promote insight |
| Canevas + livrables | Desk Livrables + `/published/` | **`GET /agent-share/{key_short}` manquant** ; canevas RO MapLibre |

---

## F. Hygiène live (cette instance)

À faire **après** land images, pas avant :

- [ ] Retirer PYTHONPATH / workingDir overlay des STS hub + agent
- [ ] Confirmer import `hub.s3_publication` depuis `/opt/...` **avec** `hub_url_for`
- [ ] Nettoyer `/home/onyxia/work/qgis-hub-live` et `/data/qgis-agent-live` seulement alors
- [ ] Catalogue : pas de slug test `scene-sxm-eolien-v2` (déjà dépublié)

---

## Checklist session type

1. `git status` — rien de secret / `_patch*`  
2. Pytest ciblé (ci-dessus)  
3. Commit + PR `main`  
4. CI tests + build hub/agent green  
5. `GET /version` commit = SHA du merge  
6. Jupyter admin + `install.sh` (update) **ou** rollout STS **sans** overlay  
7. Chrome : liste livrables + piège republish + 1 fichier `/studies/{sid}/file/...`
