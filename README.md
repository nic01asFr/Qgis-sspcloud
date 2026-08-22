# QGIS Hub — SSPCloud

Service géospatial CEREMA sur SSPCloud Onyxia :
**QGIS Desktop + Agent IA + connecteur MCP** installable en **3 minutes** par
l'utilisateur, sans intervention admin.

**Chart Helm stable** : `qgis-hub 1.3.0` (2026-08-22).

---

## Démarrage rapide

Lance un service **Jupyter-python** sur [datalab.sspcloud.fr](https://datalab.sspcloud.fr)
en réglant `Kubernetes > Enable access > role = edit` — **ce n'est pas le
réglage par défaut**, et c'est le seul point d'attention de l'installation.
Puis, dans un terminal du service :

```bash
curl -fsSL https://raw.githubusercontent.com/nic01asFr/Qgis-sspcloud/main/install.sh | bash
```

Le script déploie les 3 composants (hub + assistant + QGIS Desktop), reprend la
clé de ton assistant IA depuis ton profil SSPCloud, enregistre le service dans
ton interface Onyxia, et affiche ton adresse et ta clé d'accès. Colle-la sur
`<URL>/login` : un cookie de 90 jours te dispense ensuite de cette étape.

La clé reste consultable dans **Onyxia > Mes services > QGIS Hub**, sans aucune
commande.

**Guides** :
- [QUICKSTART.md](QUICKSTART.md) — installation pas à pas
- [docs/day5-user-guide-visuel.md](docs/day5-user-guide-visuel.md) — guide illustré
- [docs/day5-migration-guide.md](docs/day5-migration-guide.md) — migration depuis l'ancien portail

## Architecture

```
qgis-hub       → Hub API + desk web (tools MCP, études, publications, proxy /agent)
qgis-agent     → Agent IA (chat SSE, mémoire, tool-runner LLM)
qgis-workspace → QGIS Desktop noVNC (démarré à la demande)
```

**Un seul credential** = `HUB_API_KEY` (Secret K8s, cookie 90j, Bearer MCP).
**Proxy same-origin** = iframe agent + noVNC servis via hub, zéro cookie
cross-subdomain.

Détails : [ARCHITECTURE.md](ARCHITECTURE.md) · Plan Sprint Day 5 :
[docs/spec-day5-plan-complet.md](docs/spec-day5-plan-complet.md) · Runbook :
[OPS.md](OPS.md) · Setup dev : [DEVELOPMENT.md](DEVELOPMENT.md).

## Images Docker

- `ghcr.io/nic01asfr/qgis-hub:latest` (Dockerfile.hub)
- `ghcr.io/nic01asfr/qgis-agent:latest` (Dockerfile.agent)
- `ghcr.io/nic01asfr/qgisremotemcp:latest` (workspace, rebuild manuel)

Build automatique par GitHub Actions sur push `main`
([`build.yml`](.github/workflows/build.yml)). Publish chart Helm auto sur
[`helm-repo/`](helm-repo/) → consommé par `install.sh`.

## Ce que le service permet

- **Analyse géospatiale** : QGIS Desktop complet + BigQgisMCP tools + accès
  IGN, Géorisques, OSM, DVF, INSEE
- **Assistant IA** : chat conversationnel LLM SSPCloud (`qwen3-6-35b-moe`),
  tool-calling QGIS, mémoire 3 couches (session, étude, user)
- **Publications** : storymaps DSFR interactives, PDF A4, datasets GeoJSON —
  URL publique stable
- **Éditeur block-based** : BlockNote avec 13 custom blocks + autosave +
  DSFR strict + optimistic concurrency control vs agent IA
- **Connecteur MCP** : Claude Desktop, Cursor, Cline, claude.ai (transport
  Streamable HTTP, `Bearer HUB_API_KEY`)

## Récupérer la config MCP

```bash
curl -H "Authorization: Bearer $(kubectl get secret qgis-hub-apikey \
  -o jsonpath='{.data.HUB_API_KEY}' | base64 -d)" \
  -X POST https://user-<toi>-qgis.user.lab.sspcloud.fr/auth/apikey | python -m json.tool
```

Retourne un JSON `claude_config` prêt à copier dans
`claude_desktop_config.json` (ou équivalent Cursor/Cline).

## Historique (Vagues antérieures)

Le service a suivi plusieurs vagues avant Sprint Day 5 (chart 1.2.5) :

- **Vague A + B** — MCP tools + persistance études
- **Vague E1** — composition libre agent IA
- **Vague E2** — storymap métier (6 patterns) + carto thématique + éditeur
  BlockNote (13 blocks, autosave, OCC, DSFR)
- **Vague E3 sprints 1-3** (tag `v1.9.0-sprint-2-3-e3`, 2026-06-29) —
  alignement pipeline `load → scene_manifest → component → update_assembly
  → publish` + monitoring serveur erreurs JS
- **Sprints isolation Day 3/4** (2026-08-02) — session-scoped active_sid +
  cookie hub_api_key 90j auto-set + cross-tenant defense in depth
- **Sprint Day 5** (2026-08-05→06) — chart Helm autonome + suppression
  portail admin + single credential + proxy same-origin (chart 1.2.5)

Bilans détaillés : [docs/history/](docs/history/) · ADR : [docs/decisions/](docs/decisions/).

## Charte agent (vision produit)

Voir [docs/CHARTE_AGENT.md](docs/CHARTE_AGENT.md) — vision, principes,
invariants d'explicabilité. À relire avant toute décision technique
affectant l'agent.

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
