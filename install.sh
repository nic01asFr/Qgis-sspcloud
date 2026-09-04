#!/bin/bash
# install.sh - Installe le service QGIS Hub dans ton espace SSPCloud.
#
# A executer depuis un terminal jupyter Onyxia (kubernetes.role: edit requis).
# Sprint Day 5 (2026-08-06) : chart Helm packageur qgis-hub 1.2.0+
# (3 pods : hub + agent + workspace). Zero admin pod requis.
#
# Usage :
#   curl -fsSL https://raw.githubusercontent.com/nic01asFr/Qgis-sspcloud/main/install.sh | bash

set -e

REPO="qgis-sspcloud"
CHART_URL="https://raw.githubusercontent.com/nic01asFr/Qgis-sspcloud/main/helm-repo"
RELEASE="qgis-hub"

# Detection namespace (KUBERNETES_NAMESPACE ou SA token file)
SA_NS_FILE="/var/run/secrets/kubernetes.io/serviceaccount/namespace"
NAMESPACE="${KUBERNETES_NAMESPACE:-}"
if [ -z "$NAMESPACE" ] && [ -f "$SA_NS_FILE" ]; then
    NAMESPACE=$(cat "$SA_NS_FILE")
fi
USERNAME="${ONYXIA_USER:-${NAMESPACE#user-}}"
if [ -z "$USERNAME" ] || [ -z "$NAMESPACE" ]; then
    echo "ERREUR : impossible de detecter le namespace SSPCloud."
    echo "Lance ce script depuis un terminal jupyter Onyxia."
    exit 1
fi

# Sprint Day 5 fix (2026-08-06) : detecte la SA du pod jupyter courant
# (qui a le ClusterRole `edit` provisionne par Onyxia). Le hub doit
# utiliser CETTE MEME SA pour pouvoir kubectl get/patch sts (scale
# workspace, patch env agent, create ingress novnc). L'user OIDC
# SSPCloud ne peut pas creer de RoleBinding, donc la SA custom
# qgis-hub reste sans droits -> boucle infinie "Bureau endormi".
SERVICE_ACCOUNT="${KUBERNETES_SERVICE_ACCOUNT:-}"
if [ -z "$SERVICE_ACCOUNT" ]; then
    # Fallback : deduire depuis le pod jupyter courant
    SERVICE_ACCOUNT=$(kubectl get pod "$(hostname)" -n "$NAMESPACE" \
        -o jsonpath='{.spec.serviceAccountName}' 2>/dev/null || echo "")
fi
if [ -z "$SERVICE_ACCOUNT" ]; then
    echo "AVERTISSEMENT : impossible de detecter la SA du pod jupyter."
    echo "  Le hub utilisera 'default' (kubectl scale workspace echouera Forbidden)."
    SERVICE_ACCOUNT="default"
fi

# Domaine SSPCloud standard (peut etre override via env K8S_DOMAIN)
K8S_DOMAIN="${K8S_DOMAIN:-user.lab.sspcloud.fr}"

# ---------------------------------------------------------------------------
# Cle LLM : recuperation depuis le profil Onyxia stocke dans Vault.
#
# Le chart declare `{{ai.activeProvider.apiKey}}` dans values.schema.json,
# mais ce placeholder n'est resolu QUE par l'UI Onyxia. En install CLI il
# reste lettre morte -- c'est pourquoi l'agent demarrait sans cle.
#
# Onyxia range les preferences utilisateur dans Vault sous
#   onyxia-kv/data/{user}/.onyxia/userProfileStr
# dont le champ `value` contient un JSON :
#   {"userProfileValues": {"aiAssistant": {"apiKey": ..., "apiBase": ...,
#                                          "model": ..., "enabled": ...}}}
#
# Necessite que le service Jupyter ait ete lance avec l'option Vault
# activee (VAULT_TOKEN present). Sinon on laisse vide : l'utilisateur
# saisira sa cle dans le formulaire /workspace, qui la persistera aussi.
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# Controle des droits, avant toute action.
#
# L'echec est deja rattrape apres le helm, mais tardivement : l'utilisateur
# attend une minute pour apprendre qu'il devait cocher une case. Surtout, la
# lecture de la cle existante juste en dessous echoue silencieusement sans
# droits `get secrets` -- on genererait alors une nouvelle cle, invalidant
# les cookies de 90 jours deja poses dans les navigateurs.
#
# Le role par defaut d'un service Jupyter Onyxia est `view`, qui ne permet
# ni de lire un Secret ni de creer quoi que ce soit.
# ---------------------------------------------------------------------------
if ! kubectl auth can-i get secrets -n "$NAMESPACE" >/dev/null 2>&1; then
    echo ""
    echo "ERREUR : ton service Jupyter n'a pas les droits Kubernetes requis."
    echo ""
    echo "  Relance-le en reglant, dans ses parametres :"
    echo "    Kubernetes > Enable access from within the service : oui"
    echo "    Kubernetes > Kubernetes role                       : edit"
    echo ""
    echo "  Le role propose par defaut est 'view' : il ne permet pas"
    echo "  d'installer un service."
    echo ""
    exit 1
fi

# ---------------------------------------------------------------------------
# Cle d'acces au service (HUB_API_KEY).
#
# Elle est generee ICI, pas par le chart, pour deux raisons :
#   - passee en value Helm, elle devient visible dans Onyxia > Mes services
#     et dans les notes d'installation : l'utilisateur la retrouve sans
#     jamais taper de commande ni recuperer de jeton Kubernetes ;
#   - le chart n'a plus besoin de `lookup` sur les Secrets, ce qui evitait
#     l'echec d'installation depuis un Jupyter au role par defaut.
#
# Idempotence : on relit toujours la cle existante avant d'en generer une.
# Sans ca, une reinstallation changerait la cle et invaliderait les cookies
# de 90 jours deja poses dans les navigateurs de l'utilisateur.
# ---------------------------------------------------------------------------
HUB_KEY_VALUE=$(kubectl get secret qgis-hub-apikey -n "$NAMESPACE" \
    -o jsonpath='{.data.HUB_API_KEY}' 2>/dev/null | base64 -d 2>/dev/null || echo "")
if [ -z "$HUB_KEY_VALUE" ]; then
    _rand=$(head -c 24 /dev/urandom | od -An -tx1 | tr -d ' \n' | cut -c1-32)
    HUB_KEY_VALUE="qgis_${USERNAME}_${_rand}"
    echo "Cle d'acces : nouvelle cle generee"
else
    echo "Cle d'acces : cle existante conservee (tes acces restent valables)"
fi

LLM_API_KEY_VALUE=""
LLM_BASE_URL_VALUE=""
LLM_MODEL_VALUE=""
if [ -n "${VAULT_TOKEN:-}" ] && [ -n "${VAULT_ADDR:-}" ]; then
    echo "[0/5] Lecture de ta configuration IA depuis ton profil SSPCloud"
    _vault_mount="${VAULT_MOUNT:-onyxia-kv}"
    _profile_json=$(curl -s --max-time 15 \
        -H "X-Vault-Token: $VAULT_TOKEN" \
        "$VAULT_ADDR/v1/${_vault_mount}/data/${USERNAME}/.onyxia/userProfileStr" \
        2>/dev/null || echo "")
    if [ -n "$_profile_json" ]; then
        # Deux niveaux de JSON : l'enveloppe Vault, puis la chaine `value`.
        _parsed=$(printf '%s' "$_profile_json" | python3 -c '
import json, sys
try:
    outer = json.load(sys.stdin)
    raw = outer["data"]["data"]["value"]
    ai = json.loads(raw)["userProfileValues"]["aiAssistant"]
    # strip() obligatoire : les valeurs saisies dans l'\''interface de profil
    # Onyxia peuvent porter des espaces de bord (constate en production :
    # model = "  qwen3-6-35b-moe"), qui feraient rejeter le modele par
    # l'\''API LLM. Une valeur vide vaut absence : pas de valeur factice.
    print("\t".join([
        (ai.get("apiKey") or "").strip(),
        (ai.get("apiBase") or "").strip(),
        (ai.get("model") or "").strip(),
    ]))
except Exception:
    print("\t\t")
' 2>/dev/null || printf '\t\t')
        LLM_API_KEY_VALUE=$(printf '%s' "$_parsed" | cut -f1)
        LLM_BASE_URL_VALUE=$(printf '%s' "$_parsed" | cut -f2)
        LLM_MODEL_VALUE=$(printf '%s' "$_parsed" | cut -f3)
    fi
    if [ -n "$LLM_API_KEY_VALUE" ]; then
        echo "  -> cle IA trouvee dans ton profil${LLM_MODEL_VALUE:+ (modele : $LLM_MODEL_VALUE)}"
    else
        echo "  -> aucune cle IA dans ton profil (tu pourras la saisir sur /workspace)"
    fi
else
    echo "[0/5] Profil IA non consultable (option Vault non activee sur ce Jupyter)"
    echo "  -> tu pourras saisir ta cle IA sur /workspace apres installation"
fi

# Helm config dans /home/onyxia/work (persistent, PVC)
export HELM_CONFIG_HOME="${HELM_CONFIG_HOME:-/home/onyxia/work/.helm-config}"
export HELM_CACHE_HOME="${HELM_CACHE_HOME:-/home/onyxia/work/.helm-cache}"
export HELM_DATA_HOME="${HELM_DATA_HOME:-/home/onyxia/work/.helm-data}"
mkdir -p "$HELM_CONFIG_HOME" "$HELM_CACHE_HOME" "$HELM_DATA_HOME"

echo ""
echo "+==============================================================+"
echo "|  Installation QGIS Hub - $USERNAME"
echo "|  Namespace : $NAMESPACE"
echo "|  Domaine   : $K8S_DOMAIN"
echo "|  SA (herite Onyxia edit) : $SERVICE_ACCOUNT"
echo "+==============================================================+"
echo ""

# Etape 1 : ajouter le repo Helm
echo "[1/5] Ajout du repo Helm qgis-sspcloud"
helm repo add "$REPO" "$CHART_URL" 2>&1 | tail -2
helm repo update "$REPO" 2>&1 | tail -2

# Etape 2 : generer values-user.yaml depuis env vars pod jupyter Onyxia
# (le chart values.schema.json a des x-onyxia.overwriteDefaultWith qui ne sont
# resolus QUE si l'install passe par l'UI Onyxia. En CLI direct, on doit
# les injecter manuellement depuis les env vars du pod jupyter courant.)
VALUES_FILE="/tmp/qgis-hub-values-$USERNAME.yaml"
echo ""
echo "[2/5] Generation values-user.yaml depuis env vars pod jupyter"
cat > "$VALUES_FILE" <<EOF
# Auto-genere par install.sh depuis env vars pod jupyter Onyxia
oidc:
  username: "$USERNAME"

# SA heritee du pod jupyter (ClusterRole edit provisionne Onyxia)
serviceAccount:
  name: "$SERVICE_ACCOUNT"

# Cle d'acces : passee en value pour rester visible dans Onyxia et dans
# les notes d'installation. Relue a chaque execution, donc stable.
security:
  apiKey: "$HUB_KEY_VALUE"

ingress:
  enabled: true
  hostname: "user-${USERNAME}-qgis.${K8S_DOMAIN}"
  ingressClassName: onyxia

agent:
  enabled: true
  ingress:
    enabled: true
    hostname: "user-${USERNAME}-qgis-agent.${K8S_DOMAIN}"

workspace:
  enabled: true

# Assistant IA : repris du profil Onyxia (Vault) quand il est lisible.
# Vide = le chart ne cree pas le Secret et ne touche pas a une cle deja
# saisie via le formulaire /workspace.
llm:
  apiKey: "${LLM_API_KEY_VALUE}"
  baseUrl: "${LLM_BASE_URL_VALUE:-https://llm.lab.sspcloud.fr/api}"
  model: "${LLM_MODEL_VALUE}"

s3:
  enabled: true
  endpoint: "${AWS_S3_ENDPOINT:-minio.lab.sspcloud.fr}"
  defaultRegion: "${AWS_DEFAULT_REGION:-us-east-1}"
  accessKeyId: "${AWS_ACCESS_KEY_ID:-}"
  secretAccessKey: "${AWS_SECRET_ACCESS_KEY:-}"
  sessionToken: "${AWS_SESSION_TOKEN:-}"
  workingDirectoryPath: "${AWS_BUCKET_NAME:-$USERNAME}/qgis-hub/"

vault:
  enabled: true
  url: "${VAULT_ADDR:-https://vault.lab.sspcloud.fr}"
  token: "${VAULT_TOKEN:-}"
  mount: "${VAULT_MOUNT:-onyxia-kv}"
  directory: "${VAULT_TOP_DIR:-$USERNAME}"

git:
  enabled: true
  name: "${GIT_USER_NAME:-}"
  email: "${GIT_USER_MAIL:-}"
  token: "${GIT_PERSONAL_ACCESS_TOKEN:-}"
EOF
echo "  -> $VALUES_FILE"

# Etape 3 : install ou upgrade (idempotent)
echo ""
# Rattachement des ressources orphelines
#
# Une instance installee autrement -- a la main, ou par une version anterieure
# du chart -- porte des ressources que Helm ne reconnait pas comme siennes. Il
# refuse alors de les gerer, et l'upgrade echoue tout entier :
#
#   Error: ... "qgis-workspace-<user>" ... cannot be imported into the current
#   release: invalid ownership metadata
#
# Constate le 2026-09-04 sur une instance ou le workspace tournait hors
# release. Les rattacher est exactement ce que le script recommandait deja
# pour un Secret ; on le fait ici pour tout ce que le chart rend, et avant
# l'echec plutot qu'apres.
_adopte=""
_adopter() {
    _kind="$1"; _nom="$2"
    kubectl get "$_kind" "$_nom" -n "$NAMESPACE" >/dev/null 2>&1 || return 0
    _proprio=$(kubectl get "$_kind" "$_nom" -n "$NAMESPACE" \
        -o jsonpath='{.metadata.annotations.meta\.helm\.sh/release-name}' \
        2>/dev/null || echo "")
    [ "$_proprio" = "$RELEASE" ] && return 0
    if [ -n "$_proprio" ]; then
        echo "  ATTENTION : $_kind/$_nom appartient a la release '$_proprio'."
        echo "  Rien n'est touche. Desinstalle cette release, ou renomme le service."
        return 0
    fi
    kubectl label "$_kind" "$_nom" -n "$NAMESPACE" \
        app.kubernetes.io/managed-by=Helm --overwrite >/dev/null 2>&1 || return 0
    kubectl annotate "$_kind" "$_nom" -n "$NAMESPACE" \
        meta.helm.sh/release-name="$RELEASE" \
        meta.helm.sh/release-namespace="$NAMESPACE" --overwrite >/dev/null 2>&1 \
        || return 0
    _adopte="$_adopte $_kind/$_nom"
}

echo ""
echo "[2b/5] Rattachement des ressources deja presentes"
for _r in \
    "serviceaccount qgis-hub" \
    "pvc qgis-hub" \
    "secret qgis-hub-apikey" \
    "secret qgis-llm-apikey" \
    "service qgis-hub" \
    "service qgis-agent" \
    "service qgis-agent-svc" \
    "statefulset qgis-hub" \
    "statefulset qgis-agent" \
    "ingress qgis-hub" \
    "ingress qgis-agent" \
    "statefulset qgis-workspace-$USERNAME" \
    "service qgis-workspace-$USERNAME" ; do
    _adopter $_r
done
if [ -n "$_adopte" ]; then
    echo "  rattache :$_adopte"
else
    echo "  rien a rattacher"
fi

echo "[3/5] helm install/upgrade qgis-hub"
# Le resultat de helm est capture AVANT tout pipe : `cmd | tail` renvoie le
# code de sortie de `tail`, donc un echec d'installation passait inapercu et
# le script affichait quand meme "Installation terminee" (constate en
# conditions reelles le 2026-08-22, deux fois : droits insuffisants, puis
# Secret non adoptable). On sort desormais en erreur, avec le diagnostic.
# `|| _helm_rc=$?` est indispensable : avec `set -e`, une commande qui echoue
# arreterait le script avant meme qu'on puisse lire son code de sortie.
_helm_log="/tmp/qgis-hub-helm-$USERNAME.log"
_helm_rc=0
if helm list -n "$NAMESPACE" 2>/dev/null | grep -q "^$RELEASE[[:space:]]"; then
    echo "  Release deja presente -> upgrade"
    _action="upgrade"
    helm upgrade "$RELEASE" "$REPO/qgis-hub" -n "$NAMESPACE" \
        -f "$VALUES_FILE" >"$_helm_log" 2>&1 || _helm_rc=$?
else
    _action="install"
    helm install "$RELEASE" "$REPO/qgis-hub" -n "$NAMESPACE" \
        -f "$VALUES_FILE" >"$_helm_log" 2>&1 || _helm_rc=$?
fi
tail -3 "$_helm_log"

if [ "$_helm_rc" -ne 0 ]; then
    echo ""
    echo "ERREUR : le helm $_action a echoue. Le service n'est PAS a jour."
    echo ""
    if grep -q "cannot be imported into the current release" "$_helm_log"; then
        # Le message nomme l'espece et le nom : « Secret "x" », mais aussi
        # « StatefulSet "y" » ou « Service "z" ». Ne chercher que Secret
        # laissait sans remede tous les autres cas -- celui du workspace
        # notamment, rencontre le 2026-09-04.
        _kind=$(grep -oE '[A-Za-z]+ "[^"]+" in namespace' "$_helm_log" \
                | head -1 | awk '{print $1}' | tr 'A-Z' 'a-z')
        _orphan=$(grep -oE '[A-Za-z]+ "[^"]+" in namespace' "$_helm_log" \
                  | head -1 | sed 's/.*"\(.*\)".*/\1/')
        _kind="${_kind:-secret}"
        echo "  Cause : $_kind '$_orphan' existe mais n'appartient pas encore"
        echo "  a la release Helm. Rattache-le puis relance ce script :"
        echo ""
        echo "    kubectl label $_kind $_orphan -n $NAMESPACE \\"
        echo "        app.kubernetes.io/managed-by=Helm --overwrite"
        echo "    kubectl annotate $_kind $_orphan -n $NAMESPACE \\"
        echo "        meta.helm.sh/release-name=$RELEASE \\"
        echo "        meta.helm.sh/release-namespace=$NAMESPACE --overwrite"
    elif grep -qE "Apply failed with [0-9]+ conflict|conflict with \"kubectl" "$_helm_log"; then
        # Rencontre en conditions reelles le 2026-09-05. Rattacher l'objet
        # (etiquettes + annotations) le fait accepter par la release, mais
        # Helm applique cote serveur et reclame aussi la propriete des
        # CHAMPS. Une ressource creee jadis par `kubectl` reste revendiquee
        # par le gestionnaire `kubectl-client-side-apply` :
        #
        #   Service      .spec.ports[6080].name
        #   StatefulSet  .spec.volumeClaimTemplates
        #                .spec.template.spec.containers[qgis].resources.limits.cpu
        #
        # A noter : `--dry-run=server` ne le detecte PAS. Il valide la
        # propriete de l'objet, pas celle des champs. Une simulation verte ne
        # garantit donc pas que l'upgrade passera.
        echo "  Cause : conflit de propriete de champs. Ces ressources ont ete"
        echo "  creees par kubectl, qui en revendique encore certains champs."
        echo ""
        echo "  Supprime les objets en gardant leurs pods et leurs volumes,"
        echo "  puis relance ce script. Les donnees ne sont pas touchees --"
        echo "  verifie en conditions reelles : 168 Mo d'etudes intacts, meme"
        echo "  volume avant et apres."
        echo ""
        echo "    kubectl delete statefulset qgis-workspace-$USERNAME -n $NAMESPACE \\"
        echo "        --cascade=orphan"
        echo "    kubectl delete service qgis-workspace-$USERNAME -n $NAMESPACE"
        echo ""
        echo "  ATTENTION : le pod est recree, pas seulement adopte. Le projet"
        echo "  QGIS en cours en memoire est perdu (il reste dans le .qgz)."
    elif grep -qE "updates to statefulset spec for fields other than|field is immutable" "$_helm_log"; then
        _sts=$(grep -oE 'statefulset[s]? "[^"]+"' "$_helm_log" | head -1 \
               | sed 's/.*"\(.*\)".*/\1/')
        echo "  Cause : le StatefulSet '$_sts' tourne avec un gabarit dont"
        echo "  certains champs ne peuvent pas etre modifies en place."
        echo ""
        echo "  Supprime l'objet en gardant ses pods et ses volumes, puis"
        echo "  relance ce script -- les donnees ne sont pas touchees :"
        echo ""
        echo "    kubectl delete statefulset $_sts -n $NAMESPACE --cascade=orphan"
    elif grep -qE "forbidden|Forbidden" "$_helm_log"; then
        echo "  Cause : droits Kubernetes insuffisants."
        echo "  Relance ton service Jupyter avec, dans ses parametres :"
        echo "    Kubernetes > Enable access > role = edit"
        echo "  (le role par defaut, 'view', ne permet pas d'installer.)"
    else
        echo "  Detail complet : $_helm_log"
    fi
    echo ""
    exit 1
fi

# ---------------------------------------------------------------------------
# Enregistrement du service aupres d'Onyxia.
#
# Onyxia n'affiche dans "Mes services" que les releases pour lesquelles il
# trouve un Secret de metadonnees. Sans lui, une installation faite en CLI
# reste invisible dans l'interface, meme si `helm list` la voit : constate
# sur qgis-hub, n8n et grist-coder, tous absents de l'UI.
#
# Convention lue dans le code d'onyxia-api (HelmAppsService.java) :
#   Secret `sh.onyxia.release.v1.<release>` portant les cles
#   owner / friendlyName / catalog / share.
#
# Creer ce Secret suffit donc a faire apparaitre le service dans l'UI avec
# son lien d'acces et ses notes d'installation (la cle IA, l'URL du bureau).
# Non bloquant : si l'ecriture echoue, le service fonctionne quand meme,
# il reste simplement absent de la liste Onyxia.
# ---------------------------------------------------------------------------
echo ""
echo "  Enregistrement du service dans l'interface Onyxia"
if kubectl create secret generic "sh.onyxia.release.v1.${RELEASE}" \
        -n "$NAMESPACE" \
        --from-literal=owner="$USERNAME" \
        --from-literal=friendlyName="QGIS Hub" \
        --from-literal=catalog="divers" \
        --from-literal=share=false \
        --dry-run=client -o yaml 2>/dev/null | kubectl apply -f - >/dev/null 2>&1; then
    echo "  -> visible dans datalab.sspcloud.fr > Mes services"
else
    echo "  -> non enregistre (le service reste accessible par son URL)"
fi

# Etape 4 : attente rollout
echo ""
echo "[4/5] Attente demarrage pods (~90s)"
for sts in qgis-hub qgis-agent qgis-workspace-"$USERNAME"; do
    kubectl rollout status "statefulset/$sts" -n "$NAMESPACE" --timeout=180s 2>/dev/null \
        && echo "  $sts ready" \
        || echo "  $sts pas encore ready (verifier kubectl get pods -n $NAMESPACE)"
done

# Etape 5 : recup credentials
echo ""
echo "[5/5] Ta cle personnelle HUB_API_KEY"
KEY=$(kubectl get secret qgis-hub-apikey -n "$NAMESPACE" -o jsonpath='{.data.HUB_API_KEY}' | base64 -d 2>/dev/null)
if [ -z "$KEY" ]; then
    echo "  (Secret qgis-hub-apikey pas encore genere. Relance le script dans 30s.)"
    exit 0
fi

HUB_URL="https://user-${USERNAME}-qgis.${K8S_DOMAIN}"
echo ""
echo "+==============================================================+"
echo "|  Installation terminee - tes acces"
echo "+==============================================================+"
echo "|"
echo "|  URL web (bookmark) :"
echo "|    $HUB_URL"
echo "|"
echo "|  Cle API personnelle (a coller dans /login) :"
echo "|    $KEY"
echo "|"
echo "|  Configuration MCP (Claude Desktop, Cursor, Cline) :"
echo "|    $HUB_URL/auth/apikey     (retourne JSON pret a coller)"
echo "|"
echo "|  Cle LLM (a saisir sur /workspace apres 1er login) :"
echo "|    https://llm.lab.sspcloud.fr    (onglet API keys)"
echo "|"
echo "+==============================================================+"
echo ""
