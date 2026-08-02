# USER_GUIDE — qgis-sspcloud

Guide utilisateur (agent CEREMA) pour tirer parti du service : études,
projets, analyses géospatiales, publications, connecteur MCP externe.

Version 2026-08-02 · Sprint isolation Day 3.1c CLOS.

---

## 1. Concepts clés en 30 secondes

- **Étude** : un workspace géospatial (ex : "PCRS Sorgues", "Diagnostic
  Marseille 4e"). Regroupe **projets QGIS**, **données**, **livrables**,
  **conversations agent**.
- **Projet** : un fichier .qgz QGIS dans une étude (ex : "Projet
  principal", "Scenario A", "Comparaison RGA vs inondation"). Plusieurs
  projets par étude permettent d'isoler des analyses distinctes.
- **Livrable** : un artefact publié et partageable (storymap
  interactive, PDF, dataset GeoJSON). URL publique stable.
- **Session MCP** : quand tu utilises Claude Desktop ou claude.ai avec le
  connecteur QGIS, chaque conversation a son propre contexte
  session-scoped (l'étude active peut différer d'une conversation à
  l'autre sans se marcher dessus).

## 2. Accès au service

- **Desk web** : `https://user-<toi>-qgis.user.lab.sspcloud.fr/desk`
  (auth SSPCloud auto)
- **Connecteur MCP** : à configurer dans Claude Desktop /
  `claude_desktop_config.json` (voir [ONBOARDING.md](ONBOARDING.md) §6)

**Auth persistante (Day 4)** : le portail OIDC te sert une seule fois
au bootstrap (onboarding initial). Ensuite un cookie `hub_api_key` (TTL
90 jours) te maintient authentifié dans le navigateur : plus besoin de
recoller ton token OIDC à chaque expiration cookie Keycloak. Ta clé API
personnelle (`qgis_<user>_...`) est disponible sur `/workspace` ou via
`GET /auth/apikey`. Si tu perds ton cookie (cache navigateur effacé,
autre PC), tu peux le reposer via `https://user-<toi>-qgis.user.lab.sspcloud.fr/login?key=<ta-cle>`.

## 3. Workflow type : créer une étude et publier une carte

### 3.1 Via Claude Desktop (conversation naturelle)

Tape simplement en français :

> "Crée une étude 'Bâtiments Marseille 4e' et charge les bâtiments BD
> TOPO sur cette zone. Exporte une carte web publiable."

Claude va enchaîner :
1. `study_create(name="Bâtiments Marseille 4e")` → crée l'étude + le
   projet default automatiquement, active tout
2. `set_study_zone(target="Marseille 4e")` → géocode et zoome
3. `smart_load(id="bdtopo_batiments")` → télécharge et affiche 300+
   bâtiments
4. `export_web_map(title="...")` → génère un HTML interactif
5. `publish_artifact(kind="storymap", slug="...")` → URL publique stable

Tu reçois : lien vers ta storymap accessible sur Internet.

### 3.2 Via desk web (UI graphique)

1. Ouvre le desk
2. Clic dropdown "Choisir une étude" → "+ Nouvelle étude" (ou choisir
   existante)
3. Le QGIS Desktop s'affiche dans l'iframe centrale
4. Dans le chat agent à droite : demander en français
5. Consulter les livrables produits dans la section "Publications" du
   panneau ressources gauche

## 4. Multi-projets dans une étude

Cas d'usage : comparer 2 scénarios sur la même zone.

Via MCP :
```
study_project_create(label="Scenario haut de gamme")
# ... analyses / smart_load / export
study_project_switch(pid="<autre projet>")
# ... autre analyse en parallele
```

Ou dans le desk : dropdown badge étude affiche les projets, clic pour
switcher. Un projet unique reste actif à la fois côté desk.

**Important** : QGIS Desktop = 1 projet ouvert à la fois. Switcher entre
projets sauve/recharge le .qgz (dual-write persistant).

## 5. Sessions MCP multi-onglets (Day 3+)

Si tu ouvres **2 conversations claude.ai** avec le même connecteur QGIS,
chacune a son propre contexte session-scoped :

- Conversation A : `study_switch(sidX)` → cette conversation opère sur X
- Conversation B : `study_switch(sidY)` → cette conversation opère sur Y
  simultanément
- Le desk web reste sur l'étude par défaut (DB user) → un badge
  `MCP: X, Y` s'affiche pour te signaler la divergence

Tu peux avoir plusieurs contextes de travail en parallèle sans qu'ils se
marchent dessus. Attention : chaque switch entre projets **côté MCP**
déclenche un load/save physique QGIS (~1-2s de latence).

## 6. Publications et partage

### 6.1 Types de livrables

| Kind | Description | Format |
|---|---|---|
| `storymap` | Carte narrative avec sections + visualisations | HTML autoportant |
| `flux` | Storymap éditable via éditeur BlockNote | HTML + JSON |
| `recipe` | Recette exécutable (déterministe) | YAML + doc |
| `dataset` | Export données brutes | GeoJSON / GPKG / CSV |
| `pdf` | Export QGIS Print Layout | PDF |

### 6.2 URL publique

Après `publish_artifact`, tu reçois une `hub_url` publique stable de la
forme :
```
https://user-<toi>-qgis.user.lab.sspcloud.fr/published/<kind>/<slug>
```

Cette URL :
- Est **accessible sans auth** (pas besoin de compte SSPCloud pour la lire)
- Reste stable si tu republies (versioning URL stable + bannière
  obsolescence)
- Peut être partagée par mail / réseau social / intégrée en iframe

### 6.3 Gate RGPD audience (backlog)

Les publications supportent un niveau d'audience `public /
cerema_internal / restricted / confidential`. Actuellement toutes sont
`public`. Extension backlog pour restreindre à certains groupes SSPCloud.

## 7. Gérer les études existantes

### 7.1 Lister mes études

Via MCP : `study_list()` retourne toutes les études actives + celle
active session-scoped ou DB.

Via desk : bouton "← Toutes mes études et publications" dans le dropdown
badge étude.

### 7.2 Archiver / restaurer

Pas encore d'endpoint MCP. Via desk web (section administration futur) :
archive/restore par édition SQLite. Actuellement les études restent
"actives" par défaut.

### 7.3 Télécharger l'étude complète (ZIP)

Depuis le desk : lien `↓ Télécharger l'étude (ZIP)` dans le panneau
ressources. Contient project.qgz + data/ + exports/. Bundle autoportant
(chemins relatifs).

## 8. Bonnes pratiques

### 8.1 Nommer les études

- Utilise un nom explicite : "PCRS Sorgues", pas "test"
- Évite les caractères spéciaux dans les noms (pour URL slugs)

### 8.2 Multi-user via même pod

Non supporté : 1 pod = 1 user Onyxia. Pour partager avec un collègue,
utilise :
- Publications S3 (URL publique)
- Bundle ZIP (envoi manuel)
- (futur) Invitation projet + partage sélectif ACL

### 8.3 Gérer les creds LLM

Ta clé LLM est stockée dans Vault SSPCloud. Refresh via webhook
`/api/reload-llm-key`. Rotation manuelle : voir portail admin.

### 8.4 Sauvegarder ton travail

- Le hub sauvegarde automatiquement le .qgz au switch entre projets ET
  toutes les 30s via autosave (Sprint UX)
- Ton contexte agent est persistant (mémoire long terme dans DB)
- Publications S3 sont éternelles (jusqu'à suppression manuelle)

## 9. Troubleshooting utilisateur

### "L'agent ne trouve pas mes couches"
Ton connecteur MCP est peut-être sur une session-scoped étude ≠ desk.
Vérifie via `study_list` la valeur `active_sid`. Force `study_switch`
vers l'étude désirée.

### "Le badge desk ne matche pas ce que dit l'agent"
Comportement normal Day 3+ : desk = DB user, MCP = session-scoped.
Regarde le petit badge bleu `MCP: X` à droite du badge étude pour voir
la vue MCP.

### "Publish échoue"
Vérifie :
- L'étude est bien active (via `study_list`)
- Le fichier source existe (`export_web_map` retourne un path
  `/data/...`)
- Ton HUB_API_KEY est valide (peut expirer, refresh via portail)

### "QGIS Desktop montre un projet vide alors que je viens de charger"
Bug potentiel divergence session-scoped/desk. Signale-le. Workaround
temporaire : `study_project_switch` explicite depuis MCP vers ton
projet.

## 10. Aller plus loin

- [ARCHITECTURE.md](ARCHITECTURE.md) : détails techniques
- [ONBOARDING.md](ONBOARDING.md) : setup admin/dev
- [OPS.md](OPS.md) : runbook opérationnel
- Recettes disponibles : voir section "RECETTES" du panneau ressources
  desk (ex : "Diagnostic du parc bâti par période de construction")
