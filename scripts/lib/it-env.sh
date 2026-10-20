# Entegrasyon ortamı (source edilir): Prometheus/Loki/Tempo port-forward'ları ve
# read-only ServiceAccount'un kısa ömürlü token'ı. Çağıran: it_env <ctx> <mon-ns> <agent-ns> <sa>
it_env() {
  local ctx="$1" mon="$2" agent_ns="$3" sa="$4"
  IT_PIDS=()
  # Kapanmış bir port-forward'ı öldürmek hata verir; set -e çıkış kodunu bozmasın.
  trap 'kill "${IT_PIDS[@]}" 2>/dev/null || true' EXIT
  local svc lport rport
  while read -r svc lport rport; do
    kubectl --context "$ctx" -n "$mon" port-forward "svc/$svc" "$lport:$rport" >/dev/null 2>&1 &
    IT_PIDS+=($!)
  done <<PF
monitoring-kube-prometheus-prometheus 19090 9090
loki 13100 3100
tempo 13200 3200
PF
  # Kafka TLS dinleyicisi (sertifika süresi kontrolü için gerçek bir TLS uç noktası)
  kubectl --context "$ctx" -n kafka port-forward svc/telemetry-kafka-bootstrap 19095:9095 >/dev/null 2>&1 &
  IT_PIDS+=($!)
  for port in 19090 13100 13200 19095; do
    for _ in $(seq 1 20); do nc -z localhost "$port" 2>/dev/null && break; sleep 0.5; done
  done
  export SRE_IT_PROMETHEUS_URL=http://localhost:19090
  export SRE_IT_LOKI_URL=http://localhost:13100
  export SRE_IT_TEMPO_URL=http://localhost:13200
  export SRE_IT_TLS_ENDPOINT=localhost:19095
  SRE_IT_K8S_SERVER=$(kubectl --context "$ctx" config view --raw --minify -o jsonpath='{.clusters[0].cluster.server}')
  SRE_IT_K8S_CA_DATA=$(kubectl --context "$ctx" config view --raw --minify -o jsonpath='{.clusters[0].cluster.certificate-authority-data}')
  SRE_IT_K8S_TOKEN=$(kubectl --context "$ctx" -n "$agent_ns" create token "$sa" --duration=10m)
  export SRE_IT_K8S_SERVER SRE_IT_K8S_CA_DATA SRE_IT_K8S_TOKEN
}
