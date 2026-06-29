# Pattern editorial narratif — Storymaps CEREMA DTerMed

> ✅ **VALIDE CROSS-VAGUE** (Vague E1 + Vague E2)
>
> Ce document décrit le **pattern éditorial CEREMA** (chapitres-questions, phrases
> pivots, KPI inline, ton DTerMed) et reste **directement applicable** :
> - Vague E1 : pour la discipline de composition libre via agent IA (system_prompt v15)
> - Vague E2 : pour les nouveaux kinds (kpi_grid, audit_chain_narrative) une fois livrés
>
> Voir D-QGIS-009 pour pivot stratégique Vague E1 UX libre.

> Reference : Blancarde-Chartreux (4e arrondissement Marseille, mars 2026).
> Destinataires : agents IA storymap_creator_v15, redacteurs humains, reviewers.

---

## 1. Structure narrative

### 1.1 Intro : bandeau KPI + phrase pivot

L'intro tient en un ecran. Pas de paragraphe explicatif. Trois blocs :

1. **Titre sec** : nom du territoire, pas de sous-titre descriptif.
2. **Bandeau KPI** : 4 a 6 chiffres-cles en grille compacte (surface, population, lineaire voirie, troncons, points de contact, potentiel d'action).
3. **Phrase pivot** dans un `fr-highlight` : 1-2 phrases qui ancrent la tension du livrable.

**Exemple reference :**

```
Blancarde Chartreux
Analyse morphologique du 4e arrondissement.

| 2.89 km2 | 49,744 hab. | 74.7 km voirie | 1,261 troncons |

80 metres de denivele entre la Blancarde (26m) et les Chartreux (106m).
212 ponts critiques sur le reseau voiture.
```

**Anti-pattern :**
```
Cette etude porte sur le 4e arrondissement de Marseille, situe entre
les quartiers de la Blancarde et des Chartreux. Le territoire couvre
une superficie de 2.89 km2 et accueille environ 49 744 habitants...
```

Regle : le lecteur doit saisir l'enjeu en 5 secondes.

### 1.2 Chapitres = questions

Chaque chapitre est formule comme une **question** ou une **affirmation provocante**, jamais un titre descriptif neutre.

| Bon (question/affirmation)       | Mauvais (descriptif)                    |
|----------------------------------|-----------------------------------------|
| Comment circule-t-on ?           | Analyse de la circulation               |
| Quels axes sont indispensables ? | Centralite du reseau viaire             |
| Le velo, parent pauvre           | Etat des amenagements cyclables         |
| Qui possede la rue ?             | Diagnostic foncier de la voirie         |
| Ouvrir ou creer ?                | Propositions d'interventions foncieres  |

Patterns de formulation :
- **Comment...?** (processus, flux)
- **Quels...?** (inventaire critique)
- **Qui...?** (acteurs, propriete, responsabilite)
- **Affirmation-choc** sans point d'interrogation (constat provocant : "Le velo, parent pauvre")

### 1.3 Sous-sujets en onglets DSFR

Chaque chapitre se decompose en **onglets** (`fr-tabs`) qui organisent les sous-angles. Chaque onglet contient :

1. Un **h2 question** (le sous-angle)
2. Un **paragraphe phrase pivot** (1 ligne, KPI + constat)
3. Une **carte interactive** (layout `sec-map` : texte a gauche, carte sticky a droite)
4. Un **fr-callout** en bas du texte : la phrase a retenir

Exemple structure chapitre "Le reseau" :
```
Chapitre 1 -- Le reseau
  [Onglet Circulation]   → h2: "Comment circule-t-on ?"
  [Onglet Centralite]    → h2: "Quels axes sont indispensables ?"
  [Onglet Velo & pentes] → h2: "Le velo, parent pauvre"
```

### 1.4 Section methodologie : "Comment ces cartes sont construites"

Derniere section narrative, avant le pied de page. Structuree en **accordeons** (un par analyse), chacun montrant :

1. **Titre-question** : "Comment on mesure l'effet canyon des rues", pas "Methodologie canyon urbain"
2. **Diagramme pipeline** visuel (SVG inline) : sources → traitements → resultat
3. **Etapes cliquables** avec captures d'ecran des traitements intermediaires
4. **Resume chiffre** : "36 traitements, 8 sources, 3 niveaux de fiabilite"

Sous-accordeons complementaires :
- **Sources** (liste avec millesime, licence, URL)
- **Pipeline** (liste ordonnee T01 → T32)
- **Fiabilite** (niveaux de confiance par couche)

---

## 2. Phrases pivots

### 2.1 Pattern obligatoire

```
[Chiffre choc] + [constat synthetique en une proposition]
```

Chaque phrase pivot est une **assertion factuelle autonome**. Elle se suffit a elle-meme. Pas de verbe modal, pas de conditionnel.

### 2.2 Exemples reference (Blancarde-Chartreux)

| Phrase pivot                                                | KPI   | Constat                                   |
|-------------------------------------------------------------|-------|-------------------------------------------|
| 35 voies impasses voiture avec passage pieton en bout.      | 35    | permeabilite pietonne cachee              |
| Rue Pierre Roche = corridor n1. 212 ponts critiques.        | 212   | vulnerabilite reseau                      |
| 97/100 convertibles sans impact connectivite voiture.       | 97    | potentiel de conversion velo              |
| 14% de couverture cyclable. Carte des pentes RGE ALTI.      | 14%   | deficit cyclable + source                 |
| 15% potentiellement prive. 9 niveaux de confiance.          | 15%   | incertitude fonciere quantifiee           |
| Ouvrir = plus d'impact : 1 ouverture → 3 modes.            | 1→3   | effet de levier multimodal                |
| 63 points de contact.                                       | 63    | gisement d'interventions                  |

### 2.3 Anti-patterns

| Interdit                                                              | Pourquoi                            |
|-----------------------------------------------------------------------|-------------------------------------|
| "Les voies impasses sont au nombre de 35, ce qui represente..."       | Periphrase, dilue le chiffre        |
| "Il serait possible de convertir environ 97 voies..."                 | Conditionnel = doute = perte impact |
| "L'analyse montre que la couverture cyclable est de 14%"              | "L'analyse montre" = filler         |
| "On notera que 212 ponts critiques ont ete identifies"                | Passif + "on notera" = academique   |

### 2.4 Discipline

- **1 phrase = 1 KPI + 1 affirmation**. Pas de phrase avec 3 chiffres.
- **Point final**, pas de point d'exclamation.
- **Source inline** si pertinent : "Carte des pentes RGE ALTI." (nomme la source dans la phrase).
- Les chiffres sont **en gras** dans le rendu HTML (`<strong>35</strong>`).

---

## 3. KPI inline narratif

### 3.1 Syntaxe markdown enrichie

Dans les composants `narrative_text`, les KPI s'integrent dans le flux du paragraphe via cette syntaxe :

```markdown
{{ kpi:35 | voies impasses voiture }} avec passage pieton en bout.
```

Rendu HTML attendu :

```html
<span class="kpi-inline">
  <span class="kpi-value">35</span>
  <span class="kpi-label">voies impasses voiture</span>
</span> avec passage pieton en bout.
```

Rendu visuel : le chiffre est en gros (22px, bold, bleu France `#000091`), le label en petit (10px, uppercase, gris mention).

### 3.2 Multi-KPI dans une phrase

```markdown
{{ kpi:14% | couverture cyclable }}. Carte des pentes {{ kpi:RGE ALTI | source }}.
```

Regle : maximum 2 KPI inline par phrase. Au-dela, utiliser un bandeau KPI (`mg` grid) au-dessus du paragraphe.

### 3.3 Bandeau KPI (intro ou debut de section)

Pour les series de 4+ indicateurs, utiliser la grille compacte :

```markdown
:::kpi-grid
2.89 | km2
49,744 | hab.
74.7 | km voirie
1,261 | troncons
:::
```

Rendu : grille `mg` avec `.mv` (valeur) + `.ml` (label) par cellule.

### 3.4 Callout (phrase a retenir)

Chaque onglet se termine par un `fr-callout` qui contient LA phrase pivot du sous-sujet :

```markdown
:::callout
**35 voies** impasses voiture mais pas pieton.
:::
```

Le callout n'est PAS un resume. C'est la phrase la plus impactante du sous-sujet, celle qui reste si le lecteur ne lit rien d'autre.

---

## 4. Ton CEREMA DTerMed

### 4.1 Principes

| Principe                    | Explication                                                    |
|-----------------------------|----------------------------------------------------------------|
| **Constats > recommandations** | On montre ce qui est, pas ce qu'il faudrait faire           |
| **Factuel > modal**         | Indicatif present, pas de "pourrait", "permettrait de"         |
| **Precis > general**        | "Rue Pierre Roche" pas "un axe majeur"                        |
| **Sources nommees**         | "BD TOPO v3", "RGE ALTI 5m", pas "les donnees disponibles"    |
| **Court > long**            | Si ca tient en 8 mots, ne pas en ecrire 20                    |
| **Chiffre en tete**         | Le KPI ouvre la phrase, le contexte suit                       |

### 4.2 Registre de langue

- **Vocabulaire technique accepte** si le contexte est pose par la carte visible a cote : "ponts critiques", "centralite d'intermediation", "effet canyon H/W".
- **Pas de jargon sans carte** : si le lecteur ne voit pas la carte en meme temps, expliciter.
- **Pas d'anglicismes** sauf termes consacres (storymap, pipeline).
- **Tutoiement interdit**. Pas de "nous" academique. Phrases impersonnelles ou sujet = le territoire/le reseau/la rue.

### 4.3 Avant / Apres

| Avant (generique)                                                                                    | Apres (DTerMed)                                             |
|-------------------------------------------------------------------------------------------------------|-------------------------------------------------------------|
| "L'analyse de la centralite d'intermediation permet d'identifier les axes structurants du reseau."    | "Rue Pierre Roche = corridor n1. 212 ponts critiques."     |
| "Le territoire presente un denivele significatif entre les parties basses et hautes."                 | "80m de denivele entre la Blancarde (26m) et les Chartreux (106m)." |
| "Les amenagements cyclables couvrent une part limitee du reseau viaire."                              | "14% de couverture cyclable."                               |
| "Plusieurs voies en impasse pour la circulation automobile offrent neanmoins une continuite pietonne." | "35 voies impasses voiture avec passage pieton en bout."    |
| "Il pourrait etre envisage de convertir certaines voies au profit des mobilites actives."             | "97/100 convertibles sans impact connectivite voiture."     |

### 4.4 Citations sources

Chaque section nomme ses sources dans le flux narratif ou dans le callout :

```
Carte des pentes RGE ALTI.
Foncier : cadastre PCI + BD TOPO v3.
Centralite : graphe BD TOPO reseau routier + algorithme Brandes.
```

La section "Comment ces cartes sont construites" fournit le detail complet (pipeline, fiabilite, millesimes).

---

## 5. Discipline system_prompt v15

### 5.1 Instructions agent IA pour le ton editorial

Ajouter au `agent_system_prompt` du profil `storymap_creator_v15` :

```
--- TON EDITORIAL CEREMA DTerMed ---

Quand tu rediges un narrative_text, applique ces regles :

TITRES CHAPITRES = QUESTIONS
- Formule chaque h2 comme une question ou une affirmation provocante.
- Patterns : "Comment...?", "Quels...?", "Qui...?", ou affirmation-choc.
- JAMAIS : "Analyse de...", "Etude de...", "Diagnostic...".

PHRASES PIVOTS (obligatoire dans chaque section)
- Format : [Chiffre gras] + [constat en 1 proposition].
- Exemples : "35 voies impasses voiture avec passage pieton en bout."
- INTERDIT : periphrases ("Les voies impasses sont au nombre de..."),
  conditionnel ("pourrait permettre"), filler ("L'analyse montre que").
- 1 phrase = 1 KPI + 1 affirmation. Point final.

KPI INLINE
- Utilise {{ kpi:VALEUR | LABEL }} dans le markdown narrative_text.
- Maximum 2 KPI inline par phrase.
- Les series 4+ KPI vont dans un :::kpi-grid au-dessus.

CALLOUT = phrase a retenir
- Chaque onglet se termine par :::callout avec LA phrase pivot.
- Pas un resume. La phrase la plus impactante.

TON GENERAL
- Constats, pas recommandations. Indicatif present.
- Nomme les sources dans le texte : "BD TOPO v3", "RGE ALTI 5m".
- Nomme les lieux : "Rue Pierre Roche", pas "un axe important".
- Court. Si 8 mots suffisent, pas 20.
```

### 5.2 Patterns de questions chapitres

Grille de formulation selon le domaine d'analyse :

| Domaine         | Pattern question                      | Exemple                          |
|-----------------|---------------------------------------|----------------------------------|
| Mobilite        | Comment [verbe mouvement] ?           | Comment circule-t-on ?           |
| Reseau          | Quels [elements] sont [adj critique] ?| Quels axes sont indispensables ? |
| Equipement      | [Sujet], [constat provocant]          | Le velo, parent pauvre           |
| Foncier/acteurs | Qui [verbe possession/responsabilite]?| Qui possede la rue ?             |
| Intervention    | [Verbe action] ou [alternative] ?     | Ouvrir ou creer ?                |
| Risque          | Ou [verbe menace] ?                   | Ou l'eau monte-t-elle ?          |
| Cadre de vie    | Comment [verbe perception] ?          | Comment respire la rue ?         |

### 5.3 Checklist avant publication

L'agent (ou le reviewer humain) verifie avant `publish_assembly` :

- [ ] Chaque chapitre a un titre-question (pas descriptif)
- [ ] Chaque onglet contient au moins 1 phrase pivot (KPI + constat)
- [ ] Chaque onglet se termine par un `fr-callout`
- [ ] L'intro contient un bandeau KPI (4-6 indicateurs) + 1-2 phrases pivot
- [ ] Aucun conditionnel ("pourrait", "permettrait") dans les phrases pivots
- [ ] Les sources sont nommees inline (BD TOPO v3, RGE ALTI, etc.)
- [ ] La section "Comment ces cartes sont construites" est presente
- [ ] `audience` est explicitement confirme par l'utilisateur

---

## Annexe : Mapping composants V1.5

| Element editorial          | Composant V1.5       | Notes                                     |
|----------------------------|----------------------|--------------------------------------------|
| Bandeau KPI intro          | `kpi_badge` (x4-6)  | Grille `kpi-grid` dans assemblage layout   |
| Phrase pivot / paragraphe  | `narrative_text`     | Markdown avec `{{ kpi:V \| L }}` inline    |
| Callout                    | `narrative_text`     | Bloc `:::callout` dans le markdown         |
| Carte interactive          | `interactive_map`    | Layout `sec-map` (texte + carte sticky)    |
| Legende                    | `legend`             | Generee depuis datasources couche          |
| Onglets sous-sujet         | Layout assemblage    | `fr-tabs` dans `storymap_narrative_dsfr`   |
| Pipeline methodologie      | `narrative_text`     | Section audit_chain auto + accordeons      |
| Sources / fiabilite        | Footer assemblage    | `footer.sources` obligatoire               |
