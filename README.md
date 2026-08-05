# QGIS Agent — SSPCloud

Service QGIS géospatial pour agents CEREMA sur SSPCloud Onyxia.  
QGIS Desktop + Agent IA + Mémoire long terme.

**État actuel** : Vagues A + B + E1 + E2 + **E3 sprints 1-3** LIVRÉES
(tag `v1.9.0-sprint-2-3-e3`, 2026-06-29).

Pipeline E2E `load → scene_manifest → component → update_assembly →
publish` + storymap métier avec grammaire narrative
(intro/section/conclusion/appendix) + 6 patterns canoniques + trio
cartographe + cartographie thématique riche (Jenks + ColorBrewer + 6
fonds + interactions + proportional/heatmap + légende riche) + **éditeur
BlockNote block-based** intégré au desk avec 13 custom blocks (DOM +
iframe) + autosave 30s + optimistic concurrency control vs agent IA.
216/216 tests pytest PASSED.

**Tags publiés** (Vague E2 BlockNote + Vague E3 sprints 1-3 alignement) :
- `v1.6.5-vague-e1-composition-libre` (UX libre composition agent IA)
- `v1.6.6-storymap-metier-base` (polish DSFR + 6 patterns métier + trio cartographe)
- `v1.6.7-carto-metier-base` (symbologie + interactions + fonds + viz + légende)
- `v1.7.0-blocknote-editor` (éditeur block-based 13 custom blocks + autosave + bouton desk)
- `v1.7.1-audit-fixes` (5 P0 audit + truncation + code mort -514 LOC)
- `v1.7.2-p1p2-optims` (parallèle + force-overwrite + Cache-Control + whitelist OIDC)
- `v1.7.3-fullwidth` (container 100% + width:100% custom blocks)
- `v1.7.4-roundtrip-section` (frontière sections via heading H2 vide)
- `v1.7.5-consolidation` (audit roadmap + footer dynamique + 8 tests pytest)
- `v1.8.0-sprint1-e3` (alignement reste de l'app : 4 P0 - D2/D3/D4 partiel/D5/D9)
- `v1.9.0-sprint-2-3-e3` ⭐ (sprint 2+3 essentiels : 8 P1+P2 - D1/D6/D8 + DSFR theming + monitoring)

**Marie peut maintenant** (production v1.9.0) :
- Demander à l'agent IA via chat (Vague E1)
- Éditer visuellement via BlockNote — bouton "📝 BlockNote" sur card livrable desk
- Voir le rendu DSFR strict en temps réel via bouton "👁 Aperçu DSFR" dans l'éditeur
- Composer storymaps via 6 patterns métier canoniques (Vague E2 base)
- Visualiser avec carto thématique riche (Vague E2 carto)
- Éditer les métadonnées (titre/audience/sections) via modal "🔧 Métadonnées" expert
- Bénéficier d'un theming DSFR cohérent en édition vs publication (police Marianne)

**Acquis architecturaux** (Vague E3 sprints 1-3) :
- Pas de pollution DB/PVC : BlockNote `update_component` au save (vs create-only) — drift D3
- OCC `version_num_source` bidirectionnel (agent IA + modal E1 + BlockNote) — drifts D2 + D5
- 12/13 ComponentKind rendus (vs 10/13 avant) : `media_embed` + `iframe_grist` livrés — drift D4 partiel
- AssemblyKind filtré au schéma (évite 501 sur dashboard/sheet_a4) — drift D9
- Tests paramétrés `ComponentKind ↔ runtime ↔ helper` (anti-régression future) — drift D8
- Monitoring serveur erreurs JS clients (`/api/log/client-error` + ring buffer 100) — D-QGIS-010 acté
- 273/273 pytest tests PASSED.

**Vague E3 sprint 4 + V2 différés** :
- 8.9 scene_3d Three.js fill-extrusion render (~1j)
- 8.12 Création composant via slash menu BlockNote (~5-8h)
- 8.15 Draft buffer BlockNote + tool agent `get_draft_blocks` (V2)
- 8.16 Block `recipe_output` exécutable live (V2)
- 8.17 CRDT Yjs multi-user collab (V2)
- kinds avancés : audit_chain_narrative + reliability_matrix
- AuditChain enrichi (Phase, VariableReliability, contributors)
- layout sidecar Esri scrollytelling + multi-cartes synchronisées
- `@media print` A4

Cf. [docs/blocks-and-deliverables-model.md §9 backlog](docs/blocks-and-deliverables-model.md).

> **Charte de fonctionnement de l'agent** (vision produit, principes,
> roadmap, invariants) : voir [docs/CHARTE_AGENT.md](docs/CHARTE_AGENT.md).
> Document évolutif à relire avant toute décision technique.

> **Bilan session courante** : [BILAN_SESSION_2026_06_29.md](BILAN_SESSION_2026_06_29.md)
>
> **Décisions architecturales** : [docs/decisions/](docs/decisions/) (ADR)
>
> **Pipeline publication** : axe wikichat
> [qgis-sspcloud-publication-flow-axis](~/.wikichat/knowledge/qgis-sspcloud-publication-flow-axis.md)
> + [docs/scene-manifest-v0.2-contract.md](docs/scene-manifest-v0.2-contract.md)
> (livrable pour Passerelle-Archi Lead #6 geoai-kit `applyManifestToMap`)

## Installation

**Sprint Day 5 (2026-08-05)** : chart Helm autonome, zero admin requis.

**Quickstart** : voir [QUICKSTART.md](QUICKSTART.md) (3 étapes ~3 min).

Résumé express :

1. Connecte-toi sur [datalab.sspcloud.fr](https://datalab.sspcloud.fr)
2. Lance un service Jupyter avec **`kubernetes.role: edit`**
3. Dans le terminal, colle :

```bash
curl -fsSL https://raw.githubusercontent.com/nic01asFr/Qgis-sspcloud/main/install.sh | bash
```

Le script fait `helm install qgis-hub qgis-sspcloud/qgis-hub` (chart 1.2.0+),
déploie les 3 pods (~90s), et affiche ta clé HUB_API_KEY personnelle + URL.

**Premier accès web** : va sur `<URL>/login`, colle ta clé HUB_API_KEY.
Cookie 90j auto-set → workspace accessible.

## Architecture

```
qgis-hub       → Hub API + desk web (tools MCP, études, publications, proxy /agent)
qgis-agent     → Agent IA (chat conversationnel, mémoire LLM, tool-runner)
qgis-workspace → QGIS Desktop noVNC (démarré à la demande)
```

Détails : [ARCHITECTURE.md](ARCHITECTURE.md) — plan Sprint Day 5 :
[docs/spec-day5-plan-complet.md](docs/spec-day5-plan-complet.md).

## Images Docker

- `ghcr.io/nic01asfr/qgis-agent:latest`
- `ghcr.io/nic01asfr/qgis-hub:latest`

Buildées automatiquement par GitHub Actions à chaque push sur `main`.

## Invariants d'architecture

Règles dures à ne jamais violer, sous peine de casser l'engagement
d'explicabilité du service CEREMA. Tout nouveau code doit les respecter.

### 1. Audit trail = source de vérité

Les chain-badges DSFR affichés dans une storymap publiée DOIVENT correspondre
à des traitements réellement exécutés et tracés dans `treatments.jsonl` par
`hub.audit_trail`. L'agent ne fabrique jamais les steps. Toujours préférer
`StorymapBuilder.add_methodology_from_treatments(events)` à
`add_methodology(steps=...)` — le premier marque `source="audit"`, le second
émet un warning et marque `source="manual"`.

Tests qui verrouillent cet invariant : `hub/tests/test_storymap_audit.py`,
`hub/tests/test_audit_trail.py`. À lancer avant tout commit qui touche
`storymap_dsfr.py` ou `audit_trail.py`.

### 2. Anti-hallucination géographique

L'agent ne devine jamais une URL de service externe. Toujours passer par
le catalogue déclaré dans `_QGIS_ESSENTIALS` (`agent/qgis_agent.py`) :
- `overpass-api.de`, `overpass.openstreetmap.fr`
- `geo.api.gouv.fr`
- `data.geopf.fr`

La directive est en tête du prompt, jamais en bas. Si un endpoint manque,
on l'ajoute au catalogue — on ne laisse pas le LLM improviser.

### 3. Mémoire à 3 couches (4 à terme)

- **L1** conversation (RAM, turn courant)
- **L2** étude active (`project_state` + `treatments.jsonl` + insights)
- **L3** user permanent (sections markdown éditables)
- **L4** documents étude (RAG, à venir — Phase 13+)

Aucun de ces niveaux ne doit être contourné. Voir
`agent/agent/qgis_agent.py:_HUB_URL` (et non `os.getenv`) pour L2 — bug
historique corrigé.

### 4. Persistance pod

Le PVC `/home/onyxia/work` survit aux restarts ; `~/.local pip`, env vars
shell, patches `/app/src` **non**. Toujours documenter pip+env dans
`setup.py` + `kubectl set env` plutôt que dans le shell de session.

### 5. Tests d'invariant

À chaque modification, exécuter :

```bash
cd hub && python tests/test_storymap_audit.py && python tests/test_audit_trail.py
```
