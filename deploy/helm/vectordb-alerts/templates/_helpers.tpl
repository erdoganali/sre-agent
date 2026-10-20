{{- define "alerts.labels" -}}
app.kubernetes.io/managed-by: {{ .Release.Service }}
app.kubernetes.io/part-of: sre-agent
helm.sh/chart: {{ .Chart.Name }}-{{ .Chart.Version }}
{{- end }}

{{/* VectorDB pod'ları etiketle seçilir (namespace listesi yok): yeni tenant açılınca alarmlar
     kendiliğinden kapsar; tenant namespace'indeki ekibin başka pod'ları kapsanmaz.
     kube-state-metrics bu etiketi metriğe taşımalı (metricLabelsAllowlist, kube-prometheus-stack). */}}
{{- define "alerts.svSel" -}}
kube_pod_labels{label_{{ .Values.podLabel.name }}="{{ .Values.podLabel.value }}"}
{{- end }}

{{- define "alerts.svPods" -}}
max by (namespace, pod) ({{ include "alerts.svSel" . }})
{{- end }}

{{- define "alerts.svNamespaces" -}}
max by (namespace) ({{ include "alerts.svSel" . }})
{{- end }}

{{/* ephemeral-storage-exporter pod_namespace / pod_name etiketlerini kullanır */}}
{{- define "alerts.svPodsEph" -}}
label_replace(label_replace({{ include "alerts.svPods" . }}, "pod_namespace", "$1", "namespace", "(.*)"), "pod_name", "$1", "pod", "(.*)")
{{- end }}

{{- define "alerts.infraNamespaces" -}}
max by (namespace) (kube_namespace_labels{namespace=~"{{ .Values.infraNamespaces | join "|" }}"})
{{- end }}

{{- define "alerts.ruleLabels" -}}
{{- range $k, $v := .Values.labels }}
{{ $k }}: {{ $v | quote }}
{{- end }}
{{- end }}
