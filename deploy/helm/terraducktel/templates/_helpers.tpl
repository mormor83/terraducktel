{{/*
Common labels applied to every object. Kept deliberately small so selectors
stay stable across upgrades (selectors use only the per-component `app` label).
*/}}
{{- define "tdt.labels" -}}
helm.sh/chart: {{ .Chart.Name }}-{{ .Chart.Version | replace "+" "_" }}
app.kubernetes.io/part-of: terraducktel
app.kubernetes.io/instance: {{ .Release.Name }}
app.kubernetes.io/managed-by: {{ .Release.Service }}
{{- if .Chart.AppVersion }}
app.kubernetes.io/version: {{ .Chart.AppVersion | quote }}
{{- end }}
{{- end -}}

{{/*
The name of the shared Secret holding app credentials + tokens.
*/}}
{{- define "tdt.secretName" -}}
{{- default (printf "%s-secrets" .Release.Name) .Values.existingSecret -}}
{{- end -}}

{{/*
Image reference helper: (dict "img" .Values.api.image "root" $)
Renders "<repository>:<tag>", defaulting the tag to the chart's global tag.
*/}}
{{- define "tdt.image" -}}
{{- $img := .img -}}
{{- $tag := default .root.Values.global.imageTag $img.tag -}}
{{- printf "%s:%s" $img.repository $tag -}}
{{- end -}}
