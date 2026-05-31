# qgis-sspcloud V1.0 — Publication CEREMA

**Date** : 2026-05-31
**Tag git** : `v1.0` sur commit `a55cff6`
**Statut** : prêt pour test avec collègues CEREMA

## Critères de succès atteints

Un agent CEREMA novice peut :
1. Se connecter via le portail nic01asfr-qgis-mcp-portal-bridge
2. Voir son espace QGIS déployé automatiquement en <2 min
3. Créer une étude avec profil dédié (Standard, Risques naturels, Storymap, etc.)
4. Lancer une analyse géospatiale via chat naturel (« crée une storymap d'analyse du risque inondation T100 sur le 4ème arrondissement de Marseille »)
5. Obtenir 2 livrables (storymap interactive + PDF A3) en ~5 min
6. Cliquer les liens « Voir le storymap / Voir le pdf » directement depuis le chat
7. Voir ses livrables listés dans le drawer Ressources + page /workspace
8. Dépublier un livrable obsolète depuis /workspace

E2E validé avec compte test `nicolaslaval` : recipe `risque_inondation` Marseille 4e = 17/17 steps OK, 665 bâtiments exposés T100 = 4,7 %, storymap 7,9 Mo + PDF 38 Mo publiés sur S3 MinIO.

## Commits inclus dans V1.0

| # | Commit | Fix |
|---|---|---|
| 1 | `0f4449f` | httpx K8s (au lieu de kubectl absent dans image hub) |
| 2 | `5660189` | Loader chat masqué par catch cross-origin |
| 3 | `28bea39` | **Bug #17 readiness probe** : chart Onyxia jupyter-python hardcode `path:/` timeout 2s → court-circuit User-Agent `kube-probe` dans `hub_home` |
| 4 | `e8eb7a1` | **Liens livrables `[undefined](undefined)`** : marked.js v9 API positional (vs object destructure utilisée) |
| 5 | `48b82be` | 5 tips PyQGIS dans `qgis_tips.md` (layer tree, count, WMS, graduated, atlas) — anti-hallucinations sous-agent |
| 6 | `c8b13c4` | 4 fixes UX critiques : drawer timeout + retry, compteur clair, bouton Dépublier, Nouvelle conv chat |
| 7 | `a55cff6` | Hotfix drawer (toujours visible, pas dépendant de `show-publi`) |

## Audits livrés (mémoire)

- `project_bug_17_mcp_cold_start_502.md` — diagnostic complet readiness probe
- `reference_desk_loader_chat_fix.md` — règle « pas de catch cross-origin comme source de vérité »
- `reference_ux_audit_2026_05_31_complete.md` — 17 items UX/UI priorisés
- `project_agent_harness_v1_5_audit.md` — F1/F2/F3/F6 harness backlog
- `project_recipes_crud_v1_5.md` — plan 10-12h débloquer CRUD recettes
- `project_recipes_macros_partage_v1_5_unified.md` — plan V1.5 unifié 4 sprints 38h
- `reference_bigqgismcp_recipes_architecture.md` — architecture recipes BigQgisMCP + RCE Python identifié

## Acceptable pour V1 (non-bloquant)

- Slugs inconsistants entre livrables (« analyse-… » vs « risque-inondation-t100-… »)
- Drawer Mémoire affiche « Chargement… » sans timeout (équivalent #1 sur autre endpoint)
- Bouton « Archiver » sans tooltip
- Libellés UI non unifiés (« Publi » / « Publications » / « Mes livrables »)
- Colonne « Date » dans table /workspace non alimentée
- DisclosureTriangle « Plus d'options ▾ » chevron statique
- Bug recipe `risque_inondation` upstream : raster basemap rendu au-dessus + bâti incomplet sur PDF (corrigible côté BigQgisMCP)
- Recipes non éditables / non partageables (V1.5 — plan unifié 44h cadré)

## Roadmap post-V1

**V1.1 — Polish UX rapide (~10h, après feedback collègue)**
- 6 quick wins (tooltips, libellés, PROFILE_LABELS, date, breadcrumb, chevron)
- 2 critiques résiduels (slugs unifiés, drawer Mémoire timeout)
- Patch recipe `risque_inondation` BigQgisMCP (raster en bas + bâti complet)

**V1.5 — Recipes éditables + partage (~44h, plan unifié cadré)**
- Sprint S0 : BigQgisMCP `RECIPES_USER_DIR` fallback (6-9h)
- Sprint S1 : Recipes CRUD hub-side + seed à création étude (8h)
- Sprint S2 : Macros A+B+D (record + replay direct, 10h)
- Sprint S3 : Partage selectif (share-link + clone, 8h)
- Sprint S4 : Macros B'+C (rétroactif + analyzer LLM trace→recipe paramétrée, 12h)

**V1.6+ — Harness robustesse (~12h)**
- F1 ErrorCategory triage (INFRA/RECOVERABLE_APP/FATAL_APP avec alternatives)
- F2 Profil-adaptive cheatsheet
- F3 Checkpoint enforcement (lever exception au lieu de log warning)
- F6 Recovery sur polluted history

## Déploiement V1

Pour déployer cette version chez un collègue CEREMA :
1. Le collègue va sur https://user-nic01asfr-qgis-mcp-portal-bridge.user.lab.sspcloud.fr/
2. Il colle son id-token SSPCloud (récupérable via Mon compte → Connexion Kubernetes)
3. Il clique « Créer mon espace QGIS »
4. Onyxia auto-launch déploie `ghcr.io/nic01asfr/qgis-hub:latest` (= a55cff6) + `ghcr.io/nic01asfr/qgis-agent:latest`
5. Redirection auto vers `/desk` quand le hub répond `/health` 200

Images `:latest` pointent sur les builds CI a55cff6 (success vérifié).

## Contact

Mainteneur : Nicolas LAVAL (CEREMA) — nicolas.laval@cerema.fr

Issues : github.com/nic01asfr/Qgis-sspcloud/issues
