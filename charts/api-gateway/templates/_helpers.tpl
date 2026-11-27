{{- define "api-gateway.labels" -}}
app.kubernetes.io/part-of: api-gateway
app.kubernetes.io/managed-by: {{ .Release.Service }}
helm.sh/chart: {{ .Chart.Name }}-{{ .Chart.Version }}
{{- end -}}

{{/* NGINX upstream name for a service, e.g. service-a -> service_a_upstream */}}
{{- define "api-gateway.upstream" -}}
{{ . | replace "-" "_" }}_upstream
{{- end -}}
