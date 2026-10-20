{{- define "kafka.labels" -}}
app.kubernetes.io/managed-by: {{ .Release.Service }}
app.kubernetes.io/part-of: sre-agent
helm.sh/chart: {{ .Chart.Name }}-{{ .Chart.Version }}
{{- end }}
