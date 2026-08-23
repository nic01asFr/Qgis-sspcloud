# Ce qu'Atlas change à ce qu'on peut produire

> 2026-08-24. Note de réflexion, pas un plan. Elle pose une question de
> conception ouverte par nic01asFr : si Atlas apporte le récit, les contrôles
> et l'exploration, que devient un livrable ?

## Le registre qu'on gagne

Atlas n'est pas seulement un moteur de rendu. Ses modules disent ce qu'il sait
faire, et plusieurs ne relèvent pas de l'affichage :

| Module | Ce qu'il apporte |
|---|---|
| `story` | un récit : étapes de caméra **et** état de scène |
| `controls` | filtres et **animation** — temps, plage, catégorie |
| `viewer-controls` | l'environnement : relief, soleil, ombres |
| `terrain-base` | les surfaces posées en volume sur le relief |
| `model-layer` | des modèles 3D placés, orientés, mis à l'échelle |
| `view-mode` | un **mode lecture**, avec garde en écriture |
| `feuille-mobile` | des panneaux qui glissent, sur téléphone |

Deux méritent d'être relevés parce qu'ils ne concernent pas la carte mais le
**lecteur** :

`view-mode` distingue déjà celui qui explore de celui qui compose. C'est
précisément ce qu'un livrable demande — on donne à voir sans donner à modifier.

`feuille-mobile` suppose qu'on consulte sur un téléphone. Pour une restitution
de terrain, ce n'est pas un détail d'ergonomie : c'est la condition d'usage.

## Ce que nos livrables savent faire aujourd'hui

Un livrable est un **document** : des sections qui défilent, du texte, des
composants juxtaposés. L'auteur choisit les vues, le lecteur suit l'ordre.

Les cartes y sont interactives au sens faible — on zoome, on déplace. Mais
**le lecteur ne peut rien interroger**. Il ne peut pas filtrer sur une plage de
hauteurs, remonter à 1990, comparer deux scénarios, basculer en volume. Ce que
l'auteur n'a pas mis dans la vue n'existe pas pour lui.

## Le déplacement

Avec les contrôles dans la scène, un livrable peut cesser d'être un **rapport**
pour devenir un **instrument** : quelque chose avec quoi on travaille, pas
seulement quelque chose qu'on lit.

Le changement porte moins sur la technique que sur le métier de production :

> Aujourd'hui l'auteur choisit **la** bonne vue.
> Demain il prépare **l'espace des vues possibles**, et un chemin recommandé.

Ce chemin recommandé, c'est le récit. Il ne contraint pas le lecteur, il
l'oriente — et lui laisse la liberté de s'en écarter, ce qu'un document ne
permet pas.

Conséquence pour l'assistance : la commande change de forme. « Fais-moi une
carte des bâtiments de plus de 20 m » devient « prépare une scène où l'on peut
explorer la hauteur du bâti, avec un récit qui montre les trois secteurs
concernés ». L'agent ne produit plus une image, il prépare un terrain.

## Mais tous les livrables n'ont pas à changer

C'est le point où il ne faut pas se laisser emporter. Une pièce réglementaire
opposable doit rester **figée, datée, citable** — l'explorer n'aurait aucun
sens, et pouvoir la déplacer serait un défaut. Un PDF d'annexe non plus.

La finalité commande le registre :

| Finalité | Ce que le lecteur doit pouvoir faire | Registre |
|---|---|---|
| **Pièce réglementaire** | rien — lire ce qui fait foi | document figé, PDF |
| **Note de synthèse** | suivre un raisonnement | document, cartes fixes |
| **Diagnostic territorial** | vérifier sur son secteur | document **+ scènes explorables** |
| **Observatoire** | suivre une évolution, filtrer | scène, récit secondaire |
| **Aide à la décision** | comparer des variantes | scène + scénarios |
| **Restitution de terrain** | retrouver un lieu, une photo | scène mobile |

Aujourd'hui, **nous n'avons qu'un registre** — le document — et nous l'employons
pour les six. C'est cela que la bascule permet de corriger : non pas remplacer
le document, mais cesser d'en faire la seule réponse.

## Ce que cela demande, si on va dans ce sens

Rien d'immédiat, et rien qui précède la bascule technique. Mais trois choses
seraient à penser :

**Déclarer la finalité.** Un composant sait dire son `kind`, un assemblage sa
mise en page ; aucun ne dit ce qu'il est censé permettre. Sans cela, ni l'agent
ni l'auteur ne peuvent choisir le registre — et le choix se fera par défaut,
donc toujours en faveur du document.

**Assumer deux modes de lecture.** Un livrable exploratoire n'a pas de « bonne »
capture : ce qu'on imprime n'est plus ce qu'on consulte. Il faudra soit
accepter qu'un PDF soit une vue parmi d'autres, soit prévoir la vue de
référence — celle que le récit désigne.

**Ne pas perdre la citabilité.** C'est le risque réel de l'instrument : une
scène qu'on explore n'a pas d'état stable, donc rien à citer. Le récit y répond
en partie — une étape est un état nommé, donc référençable. C'est peut-être sa
fonction la plus importante, et elle n'a rien à voir avec la narration.

## Ce que je ne recommanderais pas

Basculer tous les livrables en scènes. Le document reste le bon registre pour
la majorité des usages, et l'exploration a un coût pour le lecteur : il faut
savoir quoi chercher. Un diagnostic qu'on doit explorer pour comprendre est un
diagnostic raté.

L'instrument ne remplace pas le rapport ; il s'ajoute là où le rapport ne
suffisait pas.
