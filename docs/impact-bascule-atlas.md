# Ce que la bascule vers Atlas change, de bout en bout

> 2026-08-24. Revue d'impact sur la chaîne entière : l'amont qui produit, nos
> composants et l'éditeur, l'aval qui publie. Complète
> [cadrage-composant-atlas.md](cadrage-composant-atlas.md).
>
> Rien ici n'est à faire dans l'urgence. L'objet est de savoir **ce qui bouge,
> ce qui ne bouge pas, et ce qui disparaît** — pour ne pas maintenir ce qui n'a
> plus d'objet, ni casser ce qui sert encore.

## En un coup d'œil

Sur **14 kinds de composants, 4 sont touchés**. Les dix autres ne connaissent
pas Atlas et ne le connaîtront jamais.

| Kind | Devenir |
|---|---|
| `interactive_map` | **bascule** — même kind, `rendering.runtime: "atlas"` |
| `scene_3d` | **absorbé** — Atlas fait déjà la 3D (`model-layer.js`, three.js) |
| `timeline` | **absorbé** — devient `layer.controls[]` dans la scène |
| `legend` | **absorbé** — devient `panelDeclarative` |
| `chart`, `data_table`, `kpi_badge`, `kpi_grid`, `narrative_text`, `heading`, `quote`, `separator`, `media_embed`, `iframe_grist` | **inchangés** |

---

## L'amont — ce qui produit

### QGIS, l'atelier

Ne change pas, et n'a pas à changer : c'est là qu'on compose, qu'on connecte,
qu'on style. Deux constats tout de même.

Les **54 sources externes** du catalogue (25 WFS, 8 WMS, 6 XYZ, 1 WMTS, 7 API)
sont déjà pré-chargées dans le profil par `setup_qgis_connections.py`. Elles
étaient perdues au passage vers le manifest ; elles ne le sont plus.

Le provider `postgres` est accepté par `add_layer` mais **aucune connexion n'est
outillée**. Cohérent avec la classe « atelier seulement » : les bases restent un
usage expert dans QGIS et ne descendent en aval que matérialisées.

### Le producteur de Scene Manifest

Déjà adapté (`19ace29`, `6d82bd6`, `b5566b5`, `d4a12cb`) : il émet `version`,
`order`, `provenance`, l'origine de chaque couche avec sa classe, et son
emprise.

**Ce qu'il lui reste à écrire** : `layer.controls[]`. C'est ce qui remplacera
les composants `timeline` créés à côté. Le contrat porte déjà `dataMin`/
`dataMax` — les bornes s'y déclarent sans que le client parcoure les entités.

**Ce qu'il n'écrit toujours pas** : de `stops`. Le producteur ne fait que
`kind: single`, et les commentaires annoncent encore un « Sprint C-2 »
d'éditeur de symbologie qui n'a jamais eu lieu. Conséquence pratique : les
stops désordonnés que reçoit Atlas ne viennent pas de nous, et nous ne pouvons
pas les trier à la source.

### L'agent

**Rien à changer**, et c'est ce qui rend la bascule bon marché. Il découvre le
contrat par introspection (`describe_entity_schema` → `GET /schema/{entity}`),
valide par `POST /schema/{entity}/validate`, crée par `create_component`. Une
valeur ajoutée au `Literal` `runtime` lui devient visible sans toucher son code.

Un seul point de suivi : ses exemples canoniques et sa documentation citent
`runtime: "maplibre"` et les kinds `timeline`/`legend`. Le jour où ceux-ci
cessent d'être produits, ces exemples devront suivre — sinon l'agent
continuera d'en proposer.

---

## Le milieu — nos composants et l'éditeur

### Ce qui bascule sans rien casser

`interactive_map` **reste `interactive_map`**. Seuls changent
`rendering.runtime` et le gabarit Jinja qui monte l'iframe. L'éditeur, la page
publiée et le widget Grist consomment tous le rendu du hub : **aucun ne change
d'une ligne**.

C'est le bénéfice du choix fait pour l'éditeur, assumé dans son code : « Réutilise
le rendu du hub. Pas de duplication code MapLibre/ChartJS côté React. » La
question du portage React ne se pose donc pas.

### Ce qui disparaît, et pourquoi c'est un gain

`timeline` et `legend` placent le contrôle **à côté** de la carte ; le contrat
le place **dedans**. Vérification faite, le hub définit trois custom elements —
`geo-map`, `geo-timeline`, `geo-legend` — dont **un seul implémente
`applyBinding`**. Le bus `geo:bind` ne pilote donc que des cartes, jamais un
graphique ni un tableau : ces deux blocs n'existaient que pour piloter une carte
qui ne savait pas se piloter elle-même.

Le bus traverse les iframes par `postMessage`, ce qui a imposé une liste blanche
d'origines pour éviter qu'une iframe hostile pilote la carte d'à côté. Un
contrôle déclaré dans la scène n'a **aucun message à valider** : la surface
d'attaque disparaît avec la mécanique.

`scene_3d` est le cas le plus net — il est décrit comme « futur » dans le code
de l'éditeur, et Atlas fait déjà ce qu'il promettait.

### Ce qui ne bouge pas

Les dix autres kinds n'ont aucun rapport avec la carte. `chart` reste Chart.js,
`data_table` reste un tableau, `narrative_text` reste du Markdown. Atlas ne les
concerne pas, et vouloir les lui confier serait exactement l'erreur inverse de
celle qu'on corrige.

---

## L'aval — ce qui publie

### Le rendu

`_interactive_map_partial.j2` monte l'iframe Atlas au lieu du MapLibre maison.
`_timeline_partial.j2` et `_legend_partial.j2` deviennent des gabarits
d'ancienneté : conservés tant que des livrables publiés en dépendent, plus
alimentés par le producteur.

### L'externalisation des données

**Reste nécessaire, et c'est même sa vraie justification.** Ce n'est pas une
optimisation de poids : c'est le seul moyen qu'une donnée d'atelier — PostGIS,
GeoPackage, fichier PVC — atteigne un navigateur. La cascade PMTiles / GeoJSON
gzip / inline garde tout son sens, désormais pilotée par
`ComponentSource.livraison` plutôt que par un seuil de 500 Ko.

### La publication S3

Inchangée. Les kinds `assembly`, `pdf`, `features`, `features_pmtiles`
continuent comme avant.

### Les deux récits, qui ne sont pas le même objet

Point à ne pas confondre, sous peine de croire à une duplication qui n'existe
pas :

| | Ce que c'est |
|---|---|
| notre `storymap_narrative_dsfr` | un **assemblage** : des sections qui défilent, avec du texte et des composants |
| le `story.js` d'Atlas | des **étapes de caméra** dans une seule scène — position, visibilité, contrôles, symbolisation |

Ils sont **complémentaires** : une section de notre storymap peut contenir une
carte Atlas qui a elle-même son récit interne. Le commentaire de `story.js` le
dit d'ailleurs — « interop interactive_map ».

---

## L'ordre qui s'impose

1. **`layer.controls[]` dans le producteur.** Sans lui, la bascule ferait
   perdre une fonction : les scènes n'auraient plus de contrôles du tout.
2. **`runtime: "atlas"` + le gabarit.** Le point de bascule, une fois qu'il y a
   quelque chose à basculer.
3. **Les exemples de l'agent.** Pour qu'il cesse de proposer ce qui n'est plus
   produit.
4. **Le retrait, un jour.** `timeline`, `legend`, `scene_3d`, le bus `geo:bind`
   et sa liste blanche — le jour où plus aucun livrable publié n'en dépend, et
   pas avant.

Rien de tout cela ne se fait avant qu'Atlas sache lire une scène servie par
URL. C'est le préalable, et il est chez lui.
