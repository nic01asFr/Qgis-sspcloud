# QGIS Agent — SSPCloud

Service QGIS géospatial pour agents CEREMA sur SSPCloud Onyxia.  
QGIS Desktop + Agent IA + Mémoire long terme.

**État actuel** : Vagues A + B + E1 + E2 base + E2 carto LIVRÉES
(tag `v1.6.7-carto-metier-base`, 2026-06-29) — pipeline E2E
`load → scene_manifest → component → update_assembly → publish` validé +
storymap métier avec grammaire narrative (intro/section/conclusion/appendix) +
6 patterns canoniques (hero_constat, zoom_territoire, croisement_enjeu,
fiche_indicateur, reliability_summary, conclusion_actionnable) +
cartographie thématique riche (choroplèthes graduated + ColorBrewer +
Jenks/quantile + interactions tooltip/popup/toggle + 6 fonds +
proportional symbols + heatmap + légende auto gradient_bar/proportional).
189/189 tests pytest PASSED.

**Tags publiés** :
- `v1.6.5-vague-e1-composition-libre` (UX libre composition agent IA)
- `v1.6.6-storymap-metier-base` (polish DSFR + 6 patterns métier + trio cartographe)
- `v1.6.7-carto-metier-base` ⭐ (symbologie + interactions + fonds + viz + légende)

**Prochaine vague** : E2 pivot UI éditeur BlockNote (D-QGIS-010 acté
2026-06-29) — éditeur block-based dans le desk pour permettre à Marie
d'éditer visuellement les Assembly après création agent IA. Custom
blocks DOM (heading, kpi_grid, quote, separator, narrative_text) +
iframe (interactive_map, chart, data_table). Tag attendu :
`v1.7.0-blocknote-editor`.

**Vague E3 différée** : kinds avancés (audit_chain_narrative,
reliability_matrix) + AuditChain enrichi (Phase, VariableReliability,
contributors) + layout sidecar Esri scrollytelling + scene_3d MapLibre
fill-extrusion + multi-cartes synchronisées + `@media print` A4.

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

1. Connecte-toi sur [datalab.sspcloud.fr](https://datalab.sspcloud.fr)
2. Lance un service (Jupyter ou VSCode) avec **`kubernetes.role: edit`**
3. Dans le terminal du service, colle :

```bash
curl -fsSL https://raw.githubusercontent.com/nic01asFr/Qgis-sspcloud/main/install.sh | bash
```

4. Le script déploie l'agent et le hub (~60s), puis affiche l'URL de ton bureau personnel.

## Architecture

```
qgis-agent    → Agent IA (chat, mémoire LLM, /desk)
qgis-mcp-bridge → Hub QGIS (tools MCP, études, publications)
qgis-workspace  → QGIS Desktop noVNC (démarré à la demande)
```

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
