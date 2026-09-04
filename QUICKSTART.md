# Installer QGIS Service sur SSPCloud

QGIS Desktop, un assistant IA et un connecteur MCP dans ton espace SSPCloud,
en une commande. Aucun administrateur n'intervient.

Chart Helm `qgis-hub` **1.3.0**.

---

## Avant de commencer

Un compte SSPCloud actif suffit : <https://datalab.sspcloud.fr>.

---

## 1. Lance un service Jupyter avec les droits Kubernetes

Sur `datalab.sspcloud.fr` : **Nouveau service** → **Jupyter-python**.

Avant de valider, déplie **Kubernetes** et règle :

```
Kubernetes  >  Enable access from within the service : oui
            >  Kubernetes role                       : edit
```

> **C'est le seul point d'attention de toute l'installation.** Le rôle par
> défaut est `view`, qui ne permet pas de créer des ressources : l'installation
> échouerait. Le script le vérifie et s'arrête avec un message explicite si le
> compte n'a pas les droits.
>
> Active aussi **Vault** (coché par défaut) : c'est ce qui permet de reprendre
> automatiquement la clé de ton assistant IA depuis ton profil SSPCloud.

Lance le service, puis ouvre-le.

## 2. Ouvre un terminal

Dans JupyterLab : **File** → **New** → **Terminal**.

## 3. Lance l'installation

```bash
curl -fsSL https://raw.githubusercontent.com/nic01asFr/Qgis-sspcloud/main/install.sh | bash
```

Compte deux à trois minutes. Le script :

1. reprend la configuration de ton assistant IA depuis ton profil SSPCloud ;
2. déploie trois composants — le hub, l'assistant, et QGIS Desktop ;
3. enregistre le service dans ton interface Onyxia ;
4. affiche ton adresse et ta clé d'accès.

```
+==============================================================+
|  Installation terminee - tes acces
+==============================================================+
|
|  URL web (bookmark) :
|    https://user-<toi>-qgis.user.lab.sspcloud.fr
|
|  Cle API personnelle (a coller dans /login) :
|    qgis_<toi>_XXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXX
|
+==============================================================+
```

## 4. Connecte-toi

Ouvre l'adresse affichée, colle la clé, valide.

Un cookie de 90 jours est posé : tu ne repasseras plus par cette étape pendant
trois mois, y compris après avoir fermé ton navigateur. La clé n'apparaît
jamais dans l'adresse.

**C'est terminé.** Ton bureau QGIS et ton assistant sont opérationnels.

---

## Retrouver ta clé plus tard

Elle reste affichée dans Onyxia : **Mes services** → **QGIS Hub** → notes
d'installation. Relancer `install.sh` la réaffiche également, sans jamais la
changer — tes accès en cours restent valables.

En dernier recours, `/onboarding` accepte une connexion par ton compte SSPCloud.

---

## L'assistant IA

Il utilise la clé configurée dans ton profil SSPCloud
(**Mon compte** → **Assistant IA**), reprise automatiquement à l'installation.
Elle est conservée : ni un redémarrage ni une mise à jour ne l'effacent.

Si tu n'en as pas encore, ou pour en changer : bloc **Clé LLM (agent IA)** sur
ta page d'espace de travail. Une clé se récupère sur
<https://llm.lab.sspcloud.fr> (onglet **API keys**).

---

## Connecter Claude Desktop, Cursor ou Cline

```bash
curl -s https://user-<toi>-qgis.user.lab.sspcloud.fr/auth/apikey \
  -X POST -b "hub_api_key=<ta-cle>" | python -m json.tool
```

Le bloc `claude_config` retourné se colle tel quel dans la configuration MCP de
ton client. Transport Streamable HTTP, aucun proxy local à installer.

---

## Mettre à jour, vérifier, retirer

Relancer la même commande met le service à jour sans toucher à tes données ni
à ta clé :

```bash
curl -fsSL https://raw.githubusercontent.com/nic01asFr/Qgis-sspcloud/main/install.sh | bash
```

Vérifier l'état des trois composants :

```bash
kubectl get statefulset -n user-<toi> \
    qgis-hub qgis-agent qgis-workspace-<toi> \
    -o custom-columns=NOM:.metadata.name,PRETS:.status.readyReplicas,VOULUS:.spec.replicas
```

Attendu : trois lignes, `PRETS` égal à `VOULUS` sur chacune.

```
NOM                        PRETS   VOULUS
qgis-hub                   1       1
qgis-agent                 1       1
qgis-workspace-<toi>       1       1
```

Si un composant manque, la commande le **nomme** :
`Error from server (NotFound): statefulsets.apps "qgis-workspace-<toi>" not
found`. C'est voulu : une commande qui se contenterait de filtrer afficherait
simplement moins de lignes, et une absence passerait pour un affichage normal.

Retirer le service en conservant les données (les volumes sont préservés) :

```bash
helm uninstall qgis-hub -n user-<toi>
```

Tout supprimer, y compris les études :

```bash
helm uninstall qgis-hub -n user-<toi>
kubectl delete pvc -n user-<toi> \
    data-qgis-agent-0 data-qgis-workspace-<toi>-0 qgis-hub
kubectl delete secret -n user-<toi> qgis-hub-apikey qgis-llm-apikey
```

---

## En cas de problème

| Message | Cause et correction |
|---|---|
| `droits Kubernetes insuffisants` | Le service Jupyter est en rôle `view`. Relance-le en `edit` (étape 1). |
| `le secret … n'appartient pas encore à la release` | Le script affiche les deux commandes de rattachement à copier, puis relance-le. |
| L'assistant ne répond pas | Sa clé n'est pas configurée : un bandeau l'indique dans le bureau, avec la marche à suivre. |
| Publication de livrables en échec | Les accès au stockage S3 expirent au bout de 7 jours. Relance `install.sh`. |

---

## Pour aller plus loin

- [README.md](README.md) — ce que le service permet
- [docs/day5-user-guide-visuel.md](docs/day5-user-guide-visuel.md) — guide illustré
- [ARCHITECTURE.md](ARCHITECTURE.md) — architecture technique
- [OPS.md](OPS.md) — exploitation
- [CHANGELOG.md](CHANGELOG.md) — historique des versions
