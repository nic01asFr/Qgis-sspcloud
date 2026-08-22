{{- define "qgis-hub.name" -}}
{{- default .Chart.Name .Values.nameOverride | trunc 63 | trimSuffix "-" }}
{{- end }}

{{- define "qgis-hub.fullname" -}}
{{- if .Values.fullnameOverride }}
{{- .Values.fullnameOverride | trunc 63 | trimSuffix "-" }}
{{- else }}
{{- $name := default .Chart.Name .Values.nameOverride }}
{{- if contains $name .Release.Name }}
{{- .Release.Name | trunc 63 | trimSuffix "-" }}
{{- else }}
{{- printf "%s-%s" .Release.Name $name | trunc 63 | trimSuffix "-" }}
{{- end }}
{{- end }}
{{- end }}

{{- define "qgis-hub.labels" -}}
helm.sh/chart: {{ include "qgis-hub.name" . }}-{{ .Chart.Version }}
{{ include "qgis-hub.selectorLabels" . }}
app.kubernetes.io/managed-by: {{ .Release.Service }}
{{- end }}

{{- define "qgis-hub.selectorLabels" -}}
app.kubernetes.io/name: {{ include "qgis-hub.name" . }}
app.kubernetes.io/instance: {{ .Release.Name }}
{{- end }}

{{/*
Nom d'hote public de l'agent (chart 1.3.0, 2026-08-22).

Corrige un repli defectueux. L'expression precedente etait :

  .Values.agent.ingress.hostname | default (printf "%s-agent"
      (.Values.ingress.hostname | replace "-qgis." "-qgis-agent."
       | replace ".user.lab.sspcloud.fr" ".user.lab.sspcloud.fr"))

Trois defauts : le `replace` produisait deja le bon nom, donc le
`printf "%s-agent"` ajoutait un suffixe en trop (resultat observe :
`user-X-qgis-agent.user.lab.sspcloud.fr-agent`, hote invalide) ; le second
`replace` remplacait une chaine par elle-meme ; et l'expression etait
dupliquee entre l'Ingress et les NOTES, avec le risque de diverger.

Le repli ne sert que si `agent.ingress.hostname` n'est pas fourni --
install.sh et l'UI Onyxia le fournissent tous les deux -- mais il doit
rester juste.
*/}}
{{- define "qgis-hub.agentHostname" -}}
{{- $explicit := (.Values.agent.ingress).hostname | default "" | trim -}}
{{- if $explicit -}}
{{- $explicit -}}
{{- else -}}
{{- .Values.ingress.hostname | replace "-qgis." "-qgis-agent." -}}
{{- end -}}
{{- end }}

{{/*
Configuration de l'assistant IA (chart 1.3.0, 2026-08-22).

Onyxia expose DEUX conventions concurrentes pour la config IA de
l'utilisateur, et il faut gerer les deux :

  - `user.profile.aiAssistant.*` (historique) -- c'est celle qui est
    reellement alimentee aujourd'hui : elle correspond au profil range
    dans Vault sous onyxia-kv/{user}/.onyxia/userProfileStr, section
    userProfileValues.aiAssistant.
  - `{{ ai.activeProvider.* }}` (recente) -- prevue pour le nouveau
    systeme de providers. Observee vide sur les services reels tant que
    l'utilisateur ne l'a pas renseignee.

Verification terrain (2026-08-22) : profil utilisateur renseigne et actif
cote `aiAssistant`, alors que le service lance affichait
`ai.activeProvider.apiKey=""` et `ai.enabled=false`. Ne se fier qu'au
format recent revenait donc a n'injecter aucune cle.

On applique la meme strategie que la library-chart Onyxia (_secret_ia.tpl)
qui fusionne les deux : le format historique est prioritaire, le recent
sert de repli. `trim` est indispensable -- les valeurs saisies dans
l'interface de profil peuvent contenir des espaces de bord (constate :
model = "  qwen3-6-35b-moe"), qui feraient rejeter le modele par l'API.
*/}}
{{- define "qgis-hub.llm.apiKey" -}}
{{- $legacy := .Values.llm.apiKey | default "" | trim -}}
{{- $recent := (.Values.llm.provider).apiKey | default "" | trim -}}
{{- default $recent $legacy -}}
{{- end }}

{{- define "qgis-hub.llm.baseUrl" -}}
{{- $legacy := .Values.llm.baseUrl | default "" | trim -}}
{{- $recent := (.Values.llm.provider).apiBase | default "" | trim -}}
{{- default $recent $legacy -}}
{{- end }}

{{- define "qgis-hub.llm.model" -}}
{{- $legacy := .Values.llm.model | default "" | trim -}}
{{- $recent := (.Values.llm.provider).selectedModel | default "" | trim -}}
{{- default $recent $legacy -}}
{{- end }}
