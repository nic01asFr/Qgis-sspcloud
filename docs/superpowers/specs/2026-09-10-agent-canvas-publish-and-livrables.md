# Spec — Canevas agent publiable & surfaces livrables

Date : 2026-09-10. Audit exploratoire (code existant uniquement).  
Hors scope : UX `chat.html` (autre équipe), layout grille desk.

## 1. État actuel

### 1.1 Page agent standalone vs embed

| Surface | Entrée | Template / route |
|--------|--------|------------------|
| Standalone | `/agent/` (proxy hub → pod agent) | `agent/templates/chat.html`, `embed=false` |
| Embed desk | iframe `src="/agent/?embed=1"` dans `hub/templates/desk.html` | même template, `body.embed` |
| Widget « agent partagé » (cible) | URL mintée `/agent-share/{key_short}` | **route absente** (voir §1.2) |

Route chat agent (`agent/agent/main.py`) : query `embed=1` → contexte Jinja `embed`.  
Proxy same-origin : `hub/hub/main.py` (`/agent/{path:path}`, shim fetch/XHR, auth déléguée Bearer).

**Écart embed vs standalone (constaté dans `chat.html` + tests `test_chat_embed_ux.py`) :**

| Élément | Standalone | Embed (`?embed=1`) |
|---------|------------|---------------------|
| Header marque `qs-entete` | oui | masqué (CSS + non rendu) |
| Sidebar (suggestions, projets, sessions) | oui | masquée CSS |
| Footer `qs-pied` (Mon espace, Bureau…) | oui | non rendu HTML |
| Lien toolbar « Bureau QGIS » | oui | masqué |
| Labels toolbar longs | visibles | icônes seules |
| Screenshots tools (`get_screenshot`) | visibles inline | masqués (redondants avec canvas noVNC voisin) |
| Mémoire / historique / technique | drawer + popovers | mêmes contrôles (sidebar remplacée par historique popover) |

Le standalone est déjà le chrome « page agent seule » (CHARTE_AGENT §2 : exploration). Le desk compose **canvas noVNC + chat embed + ressources**.

### 1.2 Publication d’agent (`/agent-share/...`) — gap

`POST /studies/{sid}/scoped-keys/{kid}/publish` (`main.py`) :

- calcule `audit_chain` + `integrity_hash` ;
- écrit `published_url = {_HUB_URL}/agent-share/{key_short}` ;
- commentaire : « servi dynamiquement par `/agent-share/{key_short}` qui appelle `/chat` avec scope » / « sert `/chat-embed` avec Bearer ».

**Constat :** aucune route `@app.get("/agent-share/...")` (ni équivalent) dans le hub. Les liens Widget du desk (`loadLivrables` → `a.published_url`) pointent donc vers une URL **non implémentée**.  
Fondation scoped keys : `docs/agents-scopes-architecture.md` (étapes 1–3 livrées ; data-binding / projection RO S3 = V2).

### 1.3 Surfaces « carte / canevas » déjà existantes

| Mécanisme | Où | Nature | Lecture seule ? |
|-----------|-----|--------|-----------------|
| **noVNC QGIS** | `/workspace/vnc/vnc_lite.html?...` dans `.desk-canvas` | Bureau QGIS live (pod workspace) | Non — session interactive ; chrome noVNC masqué (`_inject_vnc_desk_embed`) mais **pas** de `view_only` dans le code |
| **Scene Manifest** | `GET/POST .../scene_manifest`, build depuis QGIS | Contrat V0.2+ (vendor Atlas / geo-components) | Format données, pas UI |
| **MapLibre / composants** | `hub/hub/maplibre_renderer/*`, `GET .../components/{cid}/render` | Preview HTML iframe-embeddable (auth owner) | Oui côté rendu web ; auth OIDC |
| **Assemblies render / publish** | `.../assemblies/{aid}/render`, S3 via `/published/{owner}/{kind}/{slug}` | Storymap / interactive_map publiés | Oui pour le lecteur (audience gate) |
| **Screenshots chat** | bulles standalone | Image statique tool | Oui, non carto interactive |

Réf. interop : `docs/interop-atlas-scene-manifest.md` (cible Atlas runtime × Scene Manifest × page S3).

### 1.4 Surfaces livrables / publications (trois rôles)

```
/published/{owner}/          → catalogue public (HTML inline dans main.py)
/workspace  « Mes livrables » → teaser owner + dépublier + lien catalogue
/desk onglet Livrables       → production étude (drafts, publi, agents)
```

**`GET /published/{owner}/`** (`list_published_owner`) :

- HTML généré inline (pas de fichier template dédié) ;
- catalogue S3 `s3_publication.get_catalog(owner)` ;
- sans auth pour la liste ; items → `/published/{owner}/{kind}/{slug}` avec gate audience ;
- 404 publication + menus workspace/desk pointent ici ;
- tests : `test_vide_ou_illisible`, `test_compteur_livrables`, `test_dates_du_catalogue`.

**Workspace** (`workspace.html`) : tableau filtré (étude / tronqué) + `DELETE /publish/{kind}/{slug}` (dépublier) + CTA « Voir toutes mes publications ».

**Desk** (`desk.html`, `loadLivrables`) : fusion assemblies + catalog legacy + scoped-keys agents ; actions Preview / Publier / BlockNote / Widget.

---

## 2. Recommandation page livrables

| Surface | Verdict | Pourquoi (code / câblage) |
|---------|---------|---------------------------|
| **`/published/{owner}/`** | **Garder** | URL canonique partageable ; CTAs 404 ; menus compte ; robots/`X-Robots-Tag` ; seule vue compte-entier publique |
| **Desk onglet Livrables** | **Garder** | Seule surface de **production** (brouillons + publish + agents) ; scope étude |
| **Workspace « Mes livrables »** | **Alléger (fusion partielle)** | Liste redondante avec `/published/` ; valeur unique = **dépublier** + contexte étude active. Ne pas retirer le bloc : garder compteur + 3–5 lignes + lien catalogue + action dépublier ; éviter un 3ᵉ inventaire complet |

**Ne pas fusionner** `/published/` dans le desk : audiences et scopes différents (anonyme / compte vs OIDC / étude).  
**Ne pas retirer** `/published/` : casserait les liens mintés et les tests de catalogue.

---

## 3. Canevas QGIS lecture seule dans la page agent / widget

Objectif produit : sur standalone ou `/agent-share/...`, montrer **uniquement la carte** (pas chrome QGIS, pas drawer Ressources, pas desk).

### Options (effort / risques) — basées sur l’existant

| Option | Effort | Risques | Réalisme |
|--------|--------|---------|----------|
| **A. Embed noVNC « carte seule »** | Moyen–élevé | Pas de `view_only` ; expose le desktop QGIS (barres, édition) ; exige pod workspace owner allumé ; inadapté à un délégué `qgisk_` / URL publique ; chrome noVNC déjà partiellement masqué mais framebuffer = UI QGIS entière | **Non recommandé** pour widget publié |
| **B. Iframe MapLibre / composant `interactive_map`** (preview ou URL `/published/...`) | Faible–moyen | Preview `.../components/{cid}/render` = auth owner aujourd’hui ; pour délégué il faut URL publiée S3 ou endpoint RO scopé (V2 scopes) ; pas le « live QGIS » | **Option réaliste n°1** pour widget + standalone enrichi |
| **C. Scene Manifest → Atlas / geo-components** | Moyen (aligné roadmap) | Contrat encore en convergence (interop doc) ; runtime Atlas hors repo | **Cible moyen terme**, pas unblock immédiat |
| **D. Screenshots / captures dans le chat** | Déjà là (standalone) | Statique, pas navigation carte | Complément UX seulement |

**Recommandation technique :** pour un canevas RO sur page agent publiable → **B** (carte MapLibre issue d’un composant / assembly déjà publiable), branchée sur le même HTML que `chat.html` standalone (split pane chat | carte), **sans** noVNC. Implémenter d’abord la route manquante `/agent-share/{key_short}` (chat scopé) ; ajouter la carte en second temps seulement si un `published_url` composant/assembly ou un `scene_manifest` publié est résolvable depuis le scope de la clé.

---

## 4. Prochaines étapes courtes

1. **Implémenter `GET /agent-share/{key_short}`** — résoudre clé courte → scoped key, servir chat (réutiliser proxy `/agent/` + scope Bearer) ; tests smoke Widget desk.  
2. **Clarifier produit livrables** — alléger le tableau workspace (teaser + dépublier) ; garder `/published/` et desk inchangés fonctionnellement.  
3. **Spike canevas RO (option B)** — iframe `interactive_map` publié à côté du chat standalone / share ; critères : pas de dépendance workspace live, audience respectée.  
4. **Ne pas** brancher noVNC sur `/agent-share` ni sur standalone « publication ».

---

## 5. Fichiers de référence (audit)

- `agent/templates/chat.html` — modes embed / standalone  
- `agent/agent/main.py` — `embed` query, journal livrables  
- `hub/hub/main.py` — proxy `/agent`, VNC, `list_published_owner`, `serve_published`, `publish_agent_endpoint`, render composants  
- `hub/templates/desk.html` — canvas noVNC, iframe agent, onglet Livrables  
- `hub/templates/workspace.html` — bloc Mes livrables  
- `docs/agents-scopes-architecture.md`, `docs/interop-atlas-scene-manifest.md`, `docs/CHARTE_AGENT.md`
