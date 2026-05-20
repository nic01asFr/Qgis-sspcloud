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
