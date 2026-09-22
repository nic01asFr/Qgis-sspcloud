# Agent locate livrables (hub_url) — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** L’agent retrouve les livrables déjà publiés et donne l’URL hub `/published/{owner}/{kind}/{slug}` (200), jamais l’URL MinIO (403), sans republier.

**Architecture:** Enrichir le JSON catalogue au moment du GET (pas dans le fichier S3). Brancher ce même catalogue sur le L2c existant (`GET /studies/{sid}/publications` en 3ᵉ parallèle). Exposer `list_publications` comme `list_catalog_assemblies`. Invalider le cache L2c après `publish_artifact`.

**Tech Stack:** FastAPI hub, `s3_publication.get_catalog`, agent `hub_artifacts` + `native_tools_v2` + `memory.build_context_summary`, pytest.

## Global Constraints

- Ne pas persister `hub_url` dans `catalog/{owner}.json` S3 : `_HUB_URL` est par pod ; calculer à la lecture (comme `/desk/catalog` aujourd’hui).
- Ne pas retirer le champ `url` MinIO du JSON brut (locateur interne) ; ne plus le donner au LLM.
- L2c continue de partir de `active_study.get("id")` (`qgis_agent.py` ~2258), pas du UUID de session.
- `list_publications` est lecture seule : hors `NATIVE_TOOLS_V2_MUTATING`.
- Profil desk `standard.yaml` : `mcp_tools.allowed: all` — le tool V2 sera visible sans YAML. Ajouter le nom seulement dans `storymap_creator_v15.yaml` (whitelist explicite).
- Hors scope : Atlas/rendu Voir, assemblages V1.5 vides, `livrable_journal`, PVC vs MinIO write path.

---

## File map

| File | Role |
|------|------|
| `hub/hub/s3_publication.py` | Helper `hub_url_for` + `enrich_catalog_item` |
| `hub/hub/main.py` | Appeler l’enrichissement sur `/catalog/{owner}`, `/studies/{sid}/publications`, `/desk/catalog`, contexte workspace (~1472) |
| `hub/tests/test_catalog_hub_url.py` | Tests helper + presence dans les 4 call sites |
| `agent/agent/hub_artifacts.py` | 3ᵉ GET publications + summary + hint « ne pas republier » |
| `agent/agent/memory.py` | Bloc L2 « Livrables publiés » avec URL complète ; brief study |
| `agent/tests/test_hub_artifacts_publications.py` | Tests summary / hints / L2 |
| `agent/agent/native_tools_v2.py` | Tool `list_publications` (dispatch + OpenAI schema) |
| `hub/tests/test_catalog_components.py` | Étendre : tool non mutant + schema |
| `hub/hub/profiles/storymap_creator_v15.yaml` | Whitelist le nom |
| `agent/agent/qgis_agent.py` | Prompt 2quater + invalidation cache si `fn_name == "publish_artifact"` |

---

### Task 1: Helper hub_url + brancher les GET catalogue

**Files:**
- Create: `hub/tests/test_catalog_hub_url.py`
- Modify: `hub/hub/s3_publication.py` (après `_KIND_CONTENT_TYPE`)
- Modify: `hub/hub/main.py` — `get_owner_catalog` (~10778), `list_study_publications` (~9336), `desk_catalog` (~13711), workspace catalog (~1472)

**Interfaces:**
- Produces: `s3_publication.hub_url_for(owner, kind, slug, hub_base) -> str`
- Produces: `s3_publication.enrich_catalog_item(item, hub_base) -> dict` (copie superficielle, `setdefault("hub_url", ...)`)
- Consumes: items `get_catalog` inchangés (`url` MinIO reste)

- [ ] **Step 1: Write the failing tests**

```python
# hub/tests/test_catalog_hub_url.py
from hub import s3_publication as s3

def test_hub_url_for_strips_slash_and_extension_passthrough():
    url = s3.hub_url_for(
        "nic01asfr", "features", "scene-sxm-eolien",
        "https://user-nic01asfr-qgis.user.lab.sspcloud.fr/",
    )
    assert url == (
        "https://user-nic01asfr-qgis.user.lab.sspcloud.fr"
        "/published/nic01asfr/features/scene-sxm-eolien"
    )

def test_enrich_adds_hub_url_keeps_minio_locator():
    item = {
        "owner": "nic01asfr", "kind": "dataset", "slug": "test-chrome-20260922",
        "url": "https://minio.lab.sspcloud.fr/nic01asfr/qgis-workspace/published/"
               "nic01asfr/dataset/test-chrome-20260922.gpkg",
    }
    out = s3.enrich_catalog_item(
        item, "https://user-nic01asfr-qgis.user.lab.sspcloud.fr",
    )
    assert out["url"].startswith("https://minio.")
    assert out["hub_url"].endswith("/published/nic01asfr/dataset/test-chrome-20260922")
    assert "hub_url" not in item  # pas de mutation in-place du dict source

def test_enrich_skips_incomplete_item():
    out = s3.enrich_catalog_item({"slug": "x"}, "https://hub.example")
    assert "hub_url" not in out

def test_catalog_endpoints_call_enrich():
    from pathlib import Path
    src = Path("hub/hub/main.py").read_text(encoding="utf-8")
    assert src.count("enrich_catalog_item") >= 4
    assert "enrich_catalog_item" in src.split("async def get_owner_catalog")[1][:2500]
    assert "enrich_catalog_item" in src.split("async def list_study_publications")[1][:2000]
    assert "enrich_catalog_item" in src.split("async def desk_catalog")[1][:2500]
```

- [ ] **Step 2: Run tests — expect FAIL**

```
pytest hub/tests/test_catalog_hub_url.py -v
```

- [ ] **Step 3: Implement helper**

```python
def hub_url_for(owner: str, kind: str, slug: str, hub_base: str) -> str:
    base = (hub_base or "").rstrip("/")
    slug = (slug or "").split("?")[0]
    for ext in (".html", ".pdf", ".yaml", ".json", ".gpkg", ".qgz",
                ".zip", ".pmtiles", ".geojson"):
        if slug.endswith(ext):
            slug = slug[: -len(ext)]
            break
    return f"{base}/published/{owner}/{kind}/{slug}"


def enrich_catalog_item(item: dict, hub_base: str) -> dict:
    out = dict(item or {})
    owner = out.get("owner") or ""
    kind = out.get("kind") or ""
    slug = out.get("slug") or ""
    if not (owner and kind and slug and hub_base):
        return out
    out.setdefault("hub_url", hub_url_for(owner, kind, slug, hub_base))
    return out
```

- [ ] **Step 4: Wire the 4 call sites**

`get_owner_catalog` : après `items = ...`,  
`items = [s3_publication.enrich_catalog_item(i, _HUB_URL) for i in items]`

`list_study_publications` : même map sur `matched` avant le return.

`desk_catalog` et workspace (~1472) : remplacer le `for it in all_items: if not hub_url: it["hub_url"] = ...` par `enrich_catalog_item`. Conserver le calcul `size_kb` local au desk/workspace.

Owner manquant sur un vieil item : fallback `_ONYXIA_USER` / `user["username"]` **avant** enrich (`item.setdefault("owner", ...)`).

- [ ] **Step 5: Run tests — expect PASS**

```
pytest hub/tests/test_catalog_hub_url.py hub/tests/test_vide_ou_illisible.py hub/tests/test_compteur_livrables.py -v
```

- [ ] **Step 6: Commit** `hub: enrich catalog items with hub_url at read time`

---

### Task 2: L2c — publications de l’étude + URL complète

**Files:**
- Create: `agent/tests/test_hub_artifacts_publications.py`
- Modify: `agent/agent/hub_artifacts.py`
- Modify: `agent/agent/memory.py` (`build_context_summary` ~1329, `_brief_study_summary` ~1426)

**Interfaces:**
- Consumes: `GET {hub}/studies/{sid}/publications` → `{publications: [...]}` (déjà là)
- Produces: `summarize_artifacts` gagne `"publications": {"total", "recent": [{slug, kind, hub_url, audience}]}`
- `fetch_study_artifacts` : 3ᵉ `asyncio.gather` (fail-soft : `[]` si 4xx)

- [ ] **Step 1: Write the failing tests**

```python
from agent.hub_artifacts import summarize_artifacts, build_next_action_hints

def test_summarize_keeps_full_hub_url():
    raw = {
        "components": [], "assemblies": [],
        "publications": [{
            "slug": "scene-sxm-eolien", "kind": "features",
            "hub_url": "https://hub.example/published/u/features/scene-sxm-eolien",
            "audience": "public", "published_at": 1,
        }],
    }
    s = summarize_artifacts(raw)
    rec = s["publications"]["recent"][0]
    assert rec["hub_url"].endswith("/scene-sxm-eolien")
    assert rec["slug"] == "scene-sxm-eolien"
    assert "aid" not in rec

def test_hint_says_do_not_republish_when_publications_exist():
    art = summarize_artifacts({
        "components": [], "assemblies": [],
        "publications": [{"slug": "a", "kind": "storymap",
                          "hub_url": "https://hub.example/published/u/storymap/a"}],
    })
    hints = build_next_action_hints(art)
    blob = " ".join(hints).lower()
    assert "hub_url" in blob or "/published/" in blob
    assert "minio" not in blob
```

Test L2 : `build_context_summary` avec `study_artifacts` contenant publications → le texte contient l’URL entière (pas `[:8]`).

- [ ] **Step 2: Run — expect FAIL**

```
pytest agent/tests/test_hub_artifacts_publications.py -v
```

- [ ] **Step 3: `fetch_study_artifacts` — 3ᵉ GET**

Dans le `gather` existant, ajouter  
`c.get(f"{hub_url}/studies/{sid}/publications", headers=headers)`.  
Parser `rp.json().get("publications")` si 200, sinon `[]`.  
Retourner `{"components", "assemblies", "publications"}`.

Timeout `_HTTP_TIMEOUT` inchangé (4s). Fail-soft identique.

- [ ] **Step 4: `summarize_artifacts`**

```python
pubs = raw.get("publications") or []
recent_p = []
for p in pubs[:_MAX_ITEMS_PER_KIND]:
    recent_p.append({
        "slug": p.get("slug") or "",
        "kind": p.get("kind") or "?",
        "hub_url": p.get("hub_url") or "",
        "audience": p.get("audience") or "cerema_internal",
    })
# dans le return :
"publications": {"total": len(pubs), "recent": recent_p},
```

Ne pas tronquer `hub_url`. Si `hub_url` vide (hub pas encore déployé avec Task 1), ne pas inventer d’URL MinIO.

- [ ] **Step 5: `build_next_action_hints`**

Si `publications.total > 0`, prepend :

`"Livrables déjà publiés sur cette étude (URL = champ hub_url). Ne republie pas pour les retrouver. N'utilise jamais minio.lab.sspcloud.fr."`

Garder les hints V1.5 existants.

- [ ] **Step 6: `memory.py` L2c**

Après le bloc assemblages :

```
if p.get("total", 0) > 0:
    layer2.append(f"Livrables publiés (catalogue) : {p['total']}")
    for it in (p.get("recent") or [])[:5]:
        url = it.get("hub_url") or "(url hub absente)"
        layer2.append(f"  • {it.get('kind')} `{it.get('slug')}` → {url}")
```

`_brief_study_summary` : ajouter `N livrables publiés` dans `totals` (aujourd’hui « Livrables existants » ne compte que composants/assemblages — faux en prod).

- [ ] **Step 7: Tests PASS + commit** `agent: inject catalog publications into L2c with hub_url`

```
pytest agent/tests/test_hub_artifacts_publications.py agent/tests/test_context_kind_l2.py -v
```

---

### Task 3: Tool `list_publications`

**Files:**
- Modify: `agent/agent/native_tools_v2.py` (fn + `NATIVE_TOOLS_V2` + `NATIVE_TOOLS_V2_OPENAI`)
- Modify: `hub/tests/test_catalog_components.py` (étendre la classe `TestNativeToolsCatalog`)
- Modify: `hub/hub/profiles/storymap_creator_v15.yaml`

**Interfaces:**
- Consumes: `GET /catalog/{ONYXIA_USER}` (query `kind` déjà supportée) ; filtre optionnel `study_id`
- Produces: `{count, items: [{slug, kind, hub_url, audience, study_id}]}` — **sans** `url`

- [ ] **Step 1: Extending `test_catalog_components.py` — FAIL**

Même assertions que `list_catalog_assemblies` : présent dans `NATIVE_TOOLS_V2`, absent de `MUTATING`, présent dans `NATIVE_TOOLS_V2_OPENAI`, présent dans `storymap_creator_v15.yaml`.

- [ ] **Step 2: Implement next to `list_catalog_assemblies`**

```python
async def list_publications(
    kind: str | None = None,
    study_id: str | None = None,
) -> dict[str, Any]:
    owner = os.getenv("ONYXIA_USER", "").strip()
    if not owner:
        return {"error": "ONYXIA_USER absent, impossible de lister le catalogue"}
    params = {}
    if kind:
        params["kind"] = kind
    data = await _hub_call("GET", f"/catalog/{owner}", params=params or None)
    if data.get("error"):
        return data
    items = []
    for it in (data.get("items") or []):
        if study_id and it.get("study_id") != study_id:
            continue
        hub = it.get("hub_url") or ""
        if not hub:
            continue  # ne jamais retomber sur it["url"] MinIO
        items.append({
            "slug": it.get("slug"),
            "kind": it.get("kind"),
            "hub_url": hub,
            "audience": it.get("audience"),
            "study_id": it.get("study_id"),
        })
    return {"count": len(items), "items": items}
```

Description OpenAI (à coller près de `list_catalog_assemblies`) :

`LISTE les livrables déjà publiés de cet utilisateur. Retourne hub_url (https://…/published/owner/kind/slug) à coller tel quel. N'utilise PAS list_catalog_assemblies pour ça (marketplace V1.5). Ne republie pas pour retrouver une URL. Interdit : minio.lab.sspcloud.fr.`

- [ ] **Step 3: YAML v15** — ajouter `- list_publications` à côté de `list_catalog_assemblies`.

- [ ] **Step 4: Tests PASS + commit** `agent: add list_publications native tool (hub_url only)`

```
pytest hub/tests/test_catalog_components.py -v
```

---

### Task 4: Prompt + invalidation cache après publish_artifact

**Files:**
- Modify: `agent/agent/qgis_agent.py` — bloc `2quater` (~1884) et hook L2c (~2619)
- Optional smoke : `agent/tests/test_prompt_structure.py` si le fichier assert déjà des sous-chaînes du system prompt

- [ ] **Step 1: Invalidation**

Juste après `if fn_name in _ARTIFACT_MUTATING_TOOLS:` :

```python
if fn_name == "publish_artifact":
    self._artifacts_force_refresh = True
```

(`publish_artifact` est un tool MCP workspace, pas dans `NATIVE_TOOLS_V2_MUTATING` — ne pas l’y ajouter, ce frozenset est V1.5.)

- [ ] **Step 2: Prompt 2quater — ajouter après le point 4 `hub_url`**

```
Pour RETROUVER un livrable déjà publié : lis la section L2 « Livrables publiés »
(URL complète) ou appelle `list_publications`. Ne republie JAMAIS pour
obtenir un lien. Le champ `url` MinIO est interdit (403). Seul `hub_url` /published/...
```

Corriger la phrase actuelle « URL publique stable sans auth » si l’audience n’est pas `public` : le hub gâte encore cookie/OIDC.

- [ ] **Step 3: Tests existants prompt + commit** `agent: locate livrables via L2/list_publications, not republish`

```
pytest agent/tests/test_prompt_structure.py agent/tests/test_hub_artifacts_publications.py hub/tests/test_catalog_hub_url.py hub/tests/test_catalog_components.py -v
```

---

## Vérif live (après déploiement, pas dans le commit)

Sur le desk Saint-Martin, même question qu’en session :  
« Sans inventer d’URL : liste les livrables de cette étude, notamment scene-sxm-eolien. »

Attendu : lien `https://user-…/published/nic01asfr/features/scene-sxm-eolien`, pas MinIO, pas de proposition de republish.

---

## Hors plan (ne pas glisser)

- Réécrire `serve_published` / depot PVC
- Outil `list_artifacts` (nom halluciné live) — le nom CHARTE est `list_publications`
- Afficher Atlas au clic Voir
- Brancher `livrable_journal` SQLite
