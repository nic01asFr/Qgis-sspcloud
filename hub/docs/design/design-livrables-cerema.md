# Design System — Livrables CEREMA DTerMed

> ⚠️ **SPEC DIFFÉRÉE VAGUE E2** (décision D-QGIS-009 2026-06-29)
>
> Recadrage user "DSFR-inspiré sobre moderne, pas conformance stricte" + "storymap
> composition libre Esri-inspired" → Vague E1 cible désormais UX libre composition.
> Le système visuel décrit ici (full DSFR CDN `dsfr.min.css`, layout sec-map sticky,
> fr-tabs chapitres, fr-accordion méthode) **reste pertinent** pour Vague E2 mais
> avec **ajustements** : pas de full DSFR CDN (~280 KB CSS+JS rejetés), garder
> approche DSFR-inspirée sobre avec couleurs France + Marianne web font + classes
> utilitaires custom légères.
>
> Voir `docs/decisions/D-QGIS-009-vague-e1-ux-libre-composition.md` pour le pivot.

> **Auteur** : Design-Lead · **Date** : 2026-06-29
> **Statut** : SPEC PARTIELLE VAGUE E2 (full DSFR strict abandonné, DSFR-inspiré sobre conservé)
> **Référence cible** : `storymap_blancarde_chartreux v12` (1210 lignes, 36 traitements, 9 cartes)

---

## 1. Palette couleurs

### 1.1 Couleurs institutionnelles

| Token                | Hex       | Usage                                      |
|----------------------|-----------|---------------------------------------------|
| `--blue-france`      | `#000091` | Titres chapitres, header, accents primaires |
| `--red-marianne`     | `#e1000f` | Logo RF, badges critiques                   |
| `--text-action`      | `#0063cb` | Liens, boutons tabs actifs                  |

### 1.2 Echelle de gris DSFR

| Token          | Hex       | Usage                                     |
|----------------|-----------|-------------------------------------------|
| `--grey-50`    | `#f6f6f6` | Fond chapitres impairs, fond alt accordeon |
| `--grey-200`   | `#e5e5e5` | Bordures, separateurs                     |
| `--grey-500`   | `#666666` | Texte secondaire, mentions, labels        |
| `--grey-700`   | `#444444` | Texte descriptif hero, sous-titres        |
| `--grey-900`   | `#161616` | Corps de texte principal                  |

### 1.3 Couleurs semantiques

| Token              | Hex       | Usage                                |
|--------------------|-----------|--------------------------------------|
| `--success`        | `#1f8d4d` | Badges "ouvert", indicateurs OK      |
| `--warning`        | `#ff6f00` | Callouts attention, bandeau interne  |
| `--info`           | `#0288d1` | Badges info, liens sources externes  |
| `--error`          | `#d64d00` | Interventions type A (ouvrir)        |
| `--purple-glycine` | `#9c27b0` | Interventions type B, accent 5       |

### 1.4 Palette cartographique (6 couleurs)

Ordre d'attribution automatique pour couches MapLibre et Chart.js :

```css
--map-1: #000091;  /* blue-france */
--map-2: #e1000f;  /* red-marianne */
--map-3: #1f8d4d;  /* success green */
--map-4: #ff6f00;  /* warning orange */
--map-5: #9c27b0;  /* purple glycine */
--map-6: #0288d1;  /* info blue */
```

### 1.5 Alternance fond chapitres

Pattern observe dans la reference : chapitres impairs sur fond `background:var(--background-alt-grey)` (= `--grey-50`), chapitres pairs sur fond `#fff`. Cree un rythme visuel clair sans surcharger.

**Avant (Vague A)** : tous les chapitres sur fond blanc, sections separees par `border-bottom: 1px solid`.
**Apres (Vague E1)** : alternance fond blanc / fond gris-50, separateurs supprimes.

```css
/* Pair */
.story-section:nth-of-type(even) { background: #fff; }
/* Impair */
.story-section:nth-of-type(odd) { background: var(--grey-50); }
```

---

## 2. Typographie

### 2.1 Police principale

```css
font-family: 'Marianne', system-ui, -apple-system, 'Segoe UI', sans-serif;
```

La reference charge `@gouvfr/dsfr@1.12.1/dist/dsfr.min.css` qui embarque Marianne via `@font-face`. Pour les storymaps standalone, charger le subset woff2 directement ou garder la reference CDN DSFR.

**Decision** : inclure le CDN DSFR complet (`dsfr.min.css` + `utility.min.css`) au lieu de reimplementer les tokens CSS a la main.

### 2.2 Hierarchie typographique

| Element   | Taille  | Poids | Interligne | Usage                             |
|-----------|---------|-------|------------|-----------------------------------|
| h1        | 36px    | 700   | 1.2        | Titre etude hero                  |
| h2        | 20px    | 700   | 1.3        | Sous-titres dans sec-map          |
| chapter-title | 18px | 700  | 1.3        | "Chapitre N -- {theme}" + border-bottom 3px |
| body      | 15px    | 400   | 1.7        | Narratif sections, fr-text--sm    |
| small     | 12px    | 400   | 1.5        | Legendes, labels cartes, sources  |
| tiny      | 11px    | 500   | 1.4        | Eyebrow, tags, audit chain meta   |
| xs        | 10px    | 600   | 1.3        | ML (KPI label), badge interne     |

### 2.3 Regles specifiques

```css
/* Caps pour labels KPI */
.ml {
  font-size: 10px;
  color: var(--text-mention-grey);
  text-transform: uppercase;
  letter-spacing: 0.5px;
}

/* Tabular-nums pour valeurs KPI */
.mv {
  font-size: 22px;
  font-weight: 700;
  color: var(--text-title-blue-france);
  font-variant-numeric: tabular-nums;
}

/* Chapter title avec soulignement blue-france */
.chapter-title {
  font-size: 18px;
  font-weight: 700;
  color: var(--text-title-blue-france);
  border-bottom: 3px solid var(--border-action-high-blue-france);
  padding-bottom: 8px;
  margin-bottom: 16px;
}
```

**Avant (Vague A)** : `story-section-title` a 24px bleu, pas de border-bottom, pas de "Chapitre N".
**Apres (Vague E1)** : `chapter-title` a 18px avec border-bottom 3px, prefixe "Chapitre N -- ".

---

## 3. Layout Grids

### 3.1 Container

```css
.fr-container {
  max-width: 1200px;  /* Reference : 1200px via DSFR */
  margin: 0 auto;
  padding: 0 24px;
}
```

**Avant (Vague A)** : `max-width: 880px` — trop etroit pour le layout 2 colonnes.
**Apres (Vague E1)** : `max-width: 1200px` via `fr-container` DSFR natif.

### 3.2 sec-map — Layout 2 colonnes sticky

Le pattern phare de la reference. Colonne gauche = narrative + legende + filtres. Colonne droite = carte sticky qui suit le scroll.

```css
.sec-map {
  display: grid;
  grid-template-columns: minmax(280px, 360px) 1fr;
  gap: 24px;
  align-items: start;
}

.map-box {
  border-radius: 4px;
  overflow: hidden;
  border: 1px solid var(--border-default-grey);
  height: calc(100vh - 220px);
  min-height: 420px;
  position: sticky;
  top: 90px;
}

/* Responsive : 1 colonne sous 991px */
@media (max-width: 991px) {
  .sec-map { grid-template-columns: 1fr; }
  .map-box { position: relative; top: 0; height: 50vh; }
}
```

**Avant (Vague A)** : cartes en pleine largeur empilees verticalement dans `story-component--full` a 600px fixe.
**Apres (Vague E1)** : layout 2 colonnes avec carte sticky, narrative scrollable a gauche.

### 3.3 KPI Grid — Introduction metriques cles

6 KPI groupes en grid responsive dans la zone hero ou en tete de chapitre.

```css
.mg {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(110px, 1fr));
  gap: 8px;
  margin: 12px 0;
}
```

Chaque KPI :
```html
<div style="text-align:center" class="fr-p-1w">
  <div class="mv">2.89</div>
  <div class="ml">km2</div>
</div>
```

**Avant (Vague A)** : KPI isoles en grands badges gradient pleine largeur (56px valeur, fond bleu degrade). Spectaculaire mais pas dense.
**Apres (Vague E1)** : 6 KPI groupes compacts, 22px valeur + 10px label, fond blanc, alignement tabulaire. Dense et scannable.

### 3.4 fr-tabs DSFR — Navigation sous-sujets chapitres

Chaque chapitre contient des onglets pour explorer differents aspects sans quitter la section.

```html
<div class="fr-tabs">
  <ul class="fr-tabs__list" role="tablist" aria-label="Reseau">
    <li role="presentation">
      <button id="tab-circ" class="fr-tabs__tab" tabindex="0" role="tab"
              aria-selected="true" aria-controls="panel-circ">Circulation</button>
    </li>
    <li role="presentation">
      <button id="tab-cent" class="fr-tabs__tab" tabindex="-1" role="tab"
              aria-selected="false" aria-controls="panel-cent">Centralite</button>
    </li>
    <!-- ... -->
  </ul>
  <div id="panel-circ" class="fr-tabs__panel fr-tabs__panel--selected" role="tabpanel">
    <div class="sec-map">
      <!-- colonne gauche: narrative + legende + stats -->
      <!-- colonne droite: map-box sticky -->
    </div>
  </div>
</div>
```

**Avant (Vague A)** : sections lineaires empilees, pas de tabs.
**Apres (Vague E1)** : fr-tabs DSFR natif dans chaque chapitre. Chaque tab contient un `sec-map`.

### 3.5 fr-accordion DSFR — Sections repliees

Pour sections optionnelles : sources, pipeline traitements, fiabilite.

```html
<div class="fr-accordions-group fr-mt-3w">
  <section class="fr-accordion">
    <h3 class="fr-accordion__title">
      <button class="fr-accordion__btn" aria-expanded="false"
              aria-controls="acc-src">Sources (8)</button>
    </h3>
    <div class="fr-collapse" id="acc-src">
      <!-- contenu masque par defaut -->
    </div>
  </section>
</div>
```

Usage dans la reference : section "Tracabilite" avec 3 accordeons (Sources, Pipeline, Fiabilite).

### 3.6 map-full — Carte pleine largeur (hero ou illustrations)

```css
.map-full {
  border-radius: 4px;
  overflow: hidden;
  border: 1px solid var(--border-default-grey);
  height: 55vh;
  min-height: 380px;
}
```

Utilise dans le hero (section intro) et pour les cartes de synthese (commerces, relief).

---

## 4. Espacement + DSFR-inspire

### 4.1 Padding sections

```css
.fr-py-6w {
  padding-top: 96px;    /* 6 * 16px DSFR base unit */
  padding-bottom: 96px;
}
```

Toute section principale utilise `fr-py-6w`. C'est le rythme vertical de la reference.

**Avant (Vague A)** : `padding: 50px 24px` — trop serre, pas de respiration.
**Apres (Vague E1)** : `fr-py-6w` = 96px vertical, espace ample pro.

### 4.2 Echelle d'espacement

Base DSFR : `1w = 16px`. Espacements harmonieux :

| Token      | Valeur | Usage                                |
|------------|--------|--------------------------------------|
| `fr-p-1w`  | 16px   | Padding interne KPI, petits blocs    |
| `fr-mt-1w` | 16px   | Marge apres sous-titre               |
| `fr-mt-2w` | 32px   | Marge legendes, callouts             |
| `fr-mt-3w` | 48px   | Marge sommaire, KPI grid, accordeons |
| `fr-mt-4w` | 64px   | Marge credits, separations majeures  |
| `fr-py-6w` | 96px   | Padding vertical sections            |

### 4.3 Cards et composants

```css
/* Cards DSFR-inspire */
.story-component {
  background: #fff;
  border-radius: 4px;       /* 4px DSFR strict, pas 8px */
  overflow: hidden;
  border: 1px solid var(--border-default-grey);
  box-shadow: none;          /* La reference n'utilise pas de shadow */
}
```

**Avant (Vague A)** : `border-radius: 8px` + `box-shadow: 0 2px 10px rgba(0,0,0,0.06)`.
**Apres (Vague E1)** : `border-radius: 4px`, pas de shadow (DSFR strict), `border: 1px solid grey-200`.

### 4.4 Callouts DSFR

```html
<div class="fr-callout fr-mt-2w">
  <p class="fr-callout__text">
    <strong>35 voies</strong> impasses voiture mais pas pieton.
  </p>
</div>
```

Utilise dans chaque tab pour l'insight cle — fond `--grey-50`, bordure gauche `blue-france`.

### 4.5 Highlight DSFR

```html
<div class="fr-highlight fr-mt-3w">
  <p>80 metres de denivele entre la Blancarde (26m) et les Chartreux (106m).</p>
</div>
```

Barre verticale bleue a gauche, fond transparent. Pour l'accroche hero.

### 4.6 Segmented controls (filtres carte)

La reference utilise `fr-segmented` pour switcher entre modes (voiture/velo/pieton) :

```html
<div class="fr-segmented fr-segmented--sm fr-mt-2w">
  <div class="fr-segmented__elements">
    <div class="fr-segmented__element">
      <input type="radio" name="mode1" id="m1v" value="voiture" checked>
      <label class="fr-label" for="m1v">Voiture</label>
    </div>
    <!-- ... -->
  </div>
</div>
```

### 4.7 Toggles (couches carte)

```html
<div class="fr-toggle fr-toggle--sm fr-mt-1w">
  <input type="checkbox" class="fr-toggle__input" id="tg-slopes"
         checked data-layer="slopes">
  <label class="fr-toggle__label" for="tg-slopes">Pentes</label>
</div>
```

---

## 5. Header signe

### 5.1 Structure

```html
<header role="banner" class="fr-header">
  <div class="fr-header__body">
    <div class="fr-container">
      <div class="fr-header__body-row">
        <div class="fr-header__brand fr-enlarge-link">
          <div class="fr-header__brand-top">
            <div class="fr-header__logo">
              <p class="fr-logo">Republique<br>Francaise</p>
            </div>
            <div class="fr-header__operator">
              <p style="font-weight:700;font-size:15px;color:#000091">CEREMA</p>
              <p style="font-size:11px;color:#666">DTerMed . DTVB/AU</p>
            </div>
          </div>
          <div class="fr-header__service">
            <p class="fr-header__service-title">{titre etude} . {domaine}</p>
            <p class="fr-header__service-tagline">{localisation} . {n} traitements</p>
          </div>
        </div>
      </div>
    </div>
  </div>
</header>
```

**Avant (Vague A)** : header custom `.dsfr-header` avec `Republique Francaise` en text + `CEREMA . QGIS Service`.
**Apres (Vague E1)** : `fr-header` DSFR natif avec logo RF (`fr-logo`), operateur CEREMA, titre etude + tagline.

### 5.2 Bandeau notice

```html
<div class="fr-notice fr-notice--info">
  <div class="fr-container">
    <div class="fr-notice__body">
      <p class="fr-notice__title">Etude exploratoire -- donnees experimentales (mars 2026)</p>
    </div>
  </div>
</div>
```

Remplace le callout orange custom de Vague A pour les documents internes.

---

## 6. Footer signe

### 6.1 Pattern cible

```
{titre etude} . v{version} DSFR . {n_treatments} traitements . {n_maps} cartes
```

Exemple : `Blancarde-Chartreux . v12 DSFR . 36 traitements . 9 cartes`

### 6.2 Credits humains

```
CEREMA {direction} . {service} . {auteur_1} -> {auteur_2} -> {auteur_n} . {date}
```

Exemple : `CEREMA DTerMed . DTVB/AU . S. Michelon -> X. Durang -> N. Laval . Mars 2026`

Le `->` indique la chaine de production/validation (auteur -> chef equipe -> validation).

### 6.3 Structure HTML complete

```html
<footer class="fr-footer" role="contentinfo">
  <div class="fr-container">
    <div class="fr-footer__body">
      <div class="fr-footer__brand fr-enlarge-link">
        <a class="fr-footer__brand-link" href="#">
          <p class="fr-logo">Republique<br>Francaise</p>
        </a>
        <a class="fr-footer__brand-link" href="#">
          <p style="font-weight:700;color:#000091">CEREMA</p>
        </a>
      </div>
      <div class="fr-footer__content">
        <p class="fr-footer__content-desc">
          {titre} . v{version} DSFR . {n_treatments} traitements . {n_maps} cartes
        </p>
        <ul class="fr-footer__content-list">
          <li class="fr-footer__content-item">
            <a class="fr-footer__content-link" href="https://cerema.fr">cerema.fr</a>
          </li>
          <li class="fr-footer__content-item">
            <a class="fr-footer__content-link" href="https://panoramax.xyz">panoramax.xyz</a>
          </li>
        </ul>
      </div>
    </div>
    <div class="fr-footer__bottom">
      <ul class="fr-footer__bottom-list">
        <li class="fr-footer__bottom-item">
          <span class="fr-footer__bottom-link">
            BD TOPO v3 . PCI . OSM . RGE ALTI . Panoramax
          </span>
        </li>
        <li class="fr-footer__bottom-item">
          <span class="fr-footer__bottom-link">Licence ouverte 2.0 / ODbL</span>
        </li>
      </ul>
    </div>
  </div>
</footer>
```

### 6.4 Credits humains (avant footer)

Place en fin de section Tracabilite, avant le `<footer>` :

```html
<p class="fr-text--xs fr-mt-4w" style="color:var(--text-mention-grey)">
  CEREMA DTerMed . DTVB/AU . S. Michelon -> X. Durang -> N. Laval . Mars 2026
</p>
```

**Avant (Vague A)** : footer custom avec grille 3 colonnes (Sources / Methodologie / CEREMA), pas de branding RF, pas de credits humains.
**Apres (Vague E1)** : `fr-footer` DSFR natif, logo RF + CEREMA, ligne de description formatee, sources en bottom list, credits humains en fin de tracabilite.

---

## 7. Section Tracabilite (pattern cle)

### 7.1 Structure

La reference dedidie une section entiere a la tracabilite avec :
- Titre "Tracabilite" + nombre de traitements/sources/niveaux de fiabilite
- Chaines de traitement visuelles (SVG flowcharts collapsibles)
- Accordeons DSFR pour Sources / Pipeline / Fiabilite
- Credits humains en closing

### 7.2 Chaines de traitement (chain)

```css
.chain {
  margin: 24px 0;
  background: #fff;
  border: 1px solid var(--border-default-grey);
  border-radius: 4px;
  overflow: hidden;
}

.chain-header {
  padding: 14px 18px;
  background: var(--background-alt-grey);
  border-bottom: 1px solid var(--border-default-grey);
  cursor: pointer;
  display: flex;
  align-items: center;
  gap: 10px;
}

.chain-header h4 {
  font-size: 14px;
  font-weight: 700;
  color: var(--text-title-blue-france);
  margin: 0;
  flex: 1;
}

.chain-body { display: none; padding: 18px; }
.chain-body.open { display: block; }

.chain-flow {
  margin: 0 0 16px;
  padding: 12px;
  background: #fafafa;
  border-radius: 4px;
  overflow-x: auto;
}
```

Chaque chaine contient un SVG flowchart (source -> processing -> output) avec miniatures cliquables.

### 7.3 Badges sources

```html
<span class="fr-badge fr-badge--info fr-badge--sm fr-badge--no-icon">
  BD TOPO . WFS
</span>
```

---

## 8. Composants DSFR supplementaires

### 8.1 fr-summary (sommaire)

```html
<nav class="fr-summary fr-mt-3w" role="navigation" aria-label="Sommaire">
  <p class="fr-summary__title">Sommaire</p>
  <ol class="fr-summary__list">
    <li><a class="fr-summary__link" href="#ch1">Le reseau</a></li>
    <li><a class="fr-summary__link" href="#ch2">Le foncier</a></li>
    <li><a class="fr-summary__link" href="#ch3">Le cadre de vie</a></li>
    <li><a class="fr-summary__link" href="#methodo">Construction</a></li>
    <li><a class="fr-summary__link" href="#s7">Tracabilite</a></li>
  </ol>
</nav>
```

Place dans la zone hero, colonne gauche.

### 8.2 fr-tile (interventions)

```html
<div class="fr-grid-row fr-grid-row--gutters fr-mt-2w">
  <div class="fr-col-6">
    <div class="fr-tile fr-tile--sm">
      <div class="fr-tile__body">
        <h3 class="fr-tile__title" style="color:#d64d00">A . Ouvrir (25)</h3>
        <p class="fr-tile__detail fr-text--xs">3 modes connectes</p>
      </div>
    </div>
  </div>
</div>
```

### 8.3 fr-card (details interactifs)

Pour Panoramax et details contextuels a cote des cartes.

---

## 9. Migration : ce qui change pour le template Jinja2

### 9.1 Dependance CDN DSFR

```html
<!-- Remplacer le bloc <style> custom par : -->
<link rel="stylesheet" href="https://unpkg.com/@gouvfr/dsfr@1.12.1/dist/dsfr.min.css">
<link rel="stylesheet" href="https://unpkg.com/@gouvfr/dsfr@1.12.1/dist/utility/utility.min.css">
<!-- + script DSFR pour tabs/accordion interactifs -->
<script type="module" src="https://unpkg.com/@gouvfr/dsfr@1.12.1/dist/dsfr.module.min.js"></script>
<script nomodule src="https://unpkg.com/@gouvfr/dsfr@1.12.1/dist/dsfr.nomodule.min.js"></script>
```

### 9.2 Tableau comparatif avant/apres

| Aspect                  | Vague A (actuel)                       | Vague E1 (cible)                              |
|-------------------------|----------------------------------------|-----------------------------------------------|
| CSS                     | Custom `:root` vars, 130 lignes       | `dsfr.min.css` + overrides 30 lignes          |
| Container               | `max-width: 880px`                    | `fr-container` 1200px                         |
| Header                  | `.dsfr-header` custom                 | `fr-header` DSFR natif                        |
| Hero                    | Gradient background, 1 colonne        | 2 colonnes (texte+KPI / carte)                |
| KPI                     | Badges gradient isoles 56px           | Grid `.mg` 6 KPI groupes 22px                 |
| Sections                | Lineaires, 50px padding               | `fr-py-6w` (96px), fond alterne               |
| Chapitres               | h2 24px bleu simple                   | `chapter-title` 18px + border-bottom 3px      |
| Cartes                  | Empilees 600px hauteur fixe           | `sec-map` 2 colonnes, carte sticky            |
| Navigation              | Scroll vertical continu               | `fr-tabs` par chapitre                        |
| Sections optionnelles   | Tout visible                          | `fr-accordion` (sources, pipeline, fiabilite) |
| Callouts                | Callout orange custom                 | `fr-callout` + `fr-notice` DSFR               |
| Border-radius           | 8px                                   | 4px DSFR                                      |
| Box-shadow              | `0 2px 10px rgba(0,0,0,0.06)`        | Aucun (DSFR strict)                           |
| Footer                  | Grille 3 colonnes custom              | `fr-footer` DSFR natif + branding RF          |
| Credits humains         | Absents                               | Chaine auteur -> validateur -> approbateur     |
| Tracabilite             | Audit chain basique                   | Section dediee + chain flowcharts + accordeons |

### 9.3 Donnees supplementaires requises dans assembly

Pour implementer le footer et la tracabilite Vague E1, l'objet `assembly` devra exposer :

```python
assembly.version        # str "v12"
assembly.n_treatments   # int 36
assembly.n_maps         # int 9
assembly.credits        # list[str] ["S. Michelon", "X. Durang", "N. Laval"]
assembly.direction      # str "DTerMed"
assembly.service        # str "DTVB/AU"
assembly.date_label     # str "Mars 2026"
```

---

## 10. Priorites d'implementation

| Priorite | Element                         | Impact                              |
|----------|---------------------------------|-------------------------------------|
| P0       | CDN DSFR (dsfr.min.css)        | Debloque tout le reste              |
| P0       | fr-container 1200px            | Layout global                       |
| P1       | sec-map 2 colonnes sticky      | Pattern phare de la reference       |
| P1       | fr-tabs par chapitre           | Navigation non-lineaire             |
| P1       | KPI grid `.mg`                 | Hero dense et scannable             |
| P2       | fr-header / fr-footer natifs   | Branding institutionnel             |
| P2       | Alternance fond chapitres      | Rythme visuel                       |
| P2       | fr-callout / fr-highlight      | Insights cles mis en evidence       |
| P3       | Section tracabilite + chains   | Transparence methodologique         |
| P3       | fr-accordion sources/pipeline  | Sections repliees                   |
| P3       | Credits humains                | Signature professionnelle           |
