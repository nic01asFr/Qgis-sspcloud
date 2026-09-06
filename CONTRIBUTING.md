# Contribuer

Merci de l'intérêt porté à ce service. Ce document dit le strict nécessaire ;
l'installation d'un environnement de développement est décrite dans
[DEVELOPMENT.md](DEVELOPMENT.md), l'exploitation dans [OPS.md](OPS.md).

## Avant d'ouvrir une pull request

**Lancer la suite.** Elle est rapide et elle tourne sans conteneur :

```bash
cd hub && python -m pytest tests/ -q
```

La CI l'exécute à chaque poussée, avec Python 3.11 et Node 20 — Node sert à
compiler les scripts inline des gabarits HTML, dont la syntaxe n'est vérifiée
par rien d'autre.

**Joindre le test qui aurait attrapé le défaut.** C'est la seule exigence de
fond de ce dépôt, et elle vient de l'expérience : un échappement doublé dans
`desk.html` a rendu tout un panneau inatteignable pendant des semaines sans
qu'aucune alerte ne se déclenche, parce qu'aucun test ne regardait les
gabarits. Réparer la ligne aurait fermé le symptôme ; le test ferme la classe.

Un test qui échoue sur du code juste coûte plus qu'il ne rapporte : préférer
un contrôle précis, quitte à ce qu'il soit plus étroit, à une heuristique qui
produit des faux positifs.

## Messages de commit

Convention `type(portée): sujet`, en français, sujet à l'impératif ou au
constat — `fix(desk): un échappement double tuait tout le panneau`.

Le corps explique **pourquoi**, pas quoi : le diff dit déjà quoi. Ce qui est
utile six mois plus tard, c'est ce qu'on avait constaté, ce qu'on a écarté, et
ce qui reste ouvert.

## Signaler un problème

Les [issues](https://github.com/nic01asFr/Qgis-sspcloud/issues) sont ouvertes.
Un rapport utile donne la version — `GET /version` sur votre instance rend le
commit du hub, la version du chart et les empreintes des images réellement en
cours — et de quoi reproduire.

## Licence des contributions

Ce dépôt est sous [Licence Ouverte 2.0](LICENSE.md). Conformément aux
conditions d'utilisation de GitHub, une contribution proposée par pull request
sur un dépôt public est concédée sous la licence de ce dépôt : il n'y a donc
ni CLA à signer, ni formalité préalable.

Vous pouvez signer vos commits avec `git commit -s`, qui ajoute un
`Signed-off-by` attestant que vous avez le droit de proposer ce code. Ce n'est
pas exigé, mais c'est utile pour une production d'administration — et c'est
gratuit.

## La brique QGIS

Le QGIS Desktop et ses outils MCP vivent dans un dépôt distinct :
[QgisRemoteMCP](https://github.com/nic01asFr/QgisRemoteMCP), miroité sur le
GitLab CEREMA. Une correction qui touche l'exécution PyQGIS, les algorithmes de
traitement ou le pont noVNC va là-bas ; ce dépôt-ci porte le hub, l'agent et le
chart Helm.
