#!/usr/bin/env bash
# T1.14 kabulü: telemetry-consumer durdurulur → hat kopukluğu dashboard'da görünür, tenant için
# MilvusTenantTelemetryStale tetiklenir, o sırada üretilen rapor "kör nokta" yazar. Çıkışta consumer
# her durumda geri açılır. YAZMA YAPAR (consumer replika sayısı; VectorDB etkilenmez).
# Kullanım: scripts/pipeline-demo.sh <kube-context> <monitoring-ns> <agent-ns> [tenant-ns]
set -uo pipefail
ctx="$1" mon="$2" agent_ns="$3" tenant="${4:-acme}"
kc() { kubectl --context "$ctx" "$@"; }
consumer=deploy/telemetry-consumer-opentelemetry-collector

pids=()
cleanup() {
  echo "== consumer geri açılıyor"
  kc -n "$mon" scale "$consumer" --replicas=1 >/dev/null
  kill "${pids[@]}" 2>/dev/null || true
}
trap cleanup EXIT
kc -n "$mon" port-forward svc/monitoring-kube-prometheus-prometheus 19090:9090 >/dev/null 2>&1 & pids+=($!)
kc -n "$mon" port-forward svc/monitoring-kube-prometheus-alertmanager 19093:9093 >/dev/null 2>&1 & pids+=($!)
kc -n "$agent_ns" port-forward svc/sre-agent 18080:8080 >/dev/null 2>&1 & pids+=($!)
for p in 19090 19093 18080; do for _ in $(seq 1 20); do nc -z localhost $p 2>/dev/null && break; sleep 0.5; done; done
prom() { curl -s -G localhost:19090/api/v1/query --data-urlencode "query=$1" | jq -r '.data.result[0].value[1] // empty'; }

start=$(date +%s)
echo "== $(date +%T) telemetry-consumer durduruluyor"
kc -n "$mon" scale "$consumer" --replicas=0 >/dev/null

echo "== MilvusTenantTelemetryStale ($tenant) bekleniyor (~11 dk; dashboard'da lag artışını izleyebilirsin)"
fired=""
for _ in $(seq 1 80); do
  sleep 15
  lag=$(prom 'sum(kafka_consumergroup_lag{consumergroup="telemetry-consumer"})')
  age=$(prom "time() - max(max_over_time(timestamp(milvus_rootcoord_timestamp{job=\"milvus-metrics\",k8s_namespace_name=\"$tenant\"})[6h:1m]))")
  printf "   %s  lag=%s  %s veri yaşı=%.0f sn\n" "$(date +%T)" "${lag:-?}" "$tenant" "${age:-0}"
  fired=$(prom "ALERTS{alertname=\"MilvusTenantTelemetryStale\",namespace=\"$tenant\",alertstate=\"firing\"}")
  [ -n "$fired" ] && break
done
[ -n "$fired" ] || { echo "FAIL MilvusTenantTelemetryStale tetiklenmedi"; exit 1; }
echo "OK   $(date +%T) MilvusTenantTelemetryStale firing ($(( $(date +%s) - start )) sn)"

run="p$(date +%s)"
now=$(date -u +%Y-%m-%dT%H:%M:%SZ)
ends=$(date -u -v+5M +%Y-%m-%dT%H:%M:%SZ 2>/dev/null || date -u -d '+5 min' +%Y-%m-%dT%H:%M:%SZ)
alert=$(jq -nc --arg run "$run" --arg now "$now" --arg ends "$ends" --arg ns "$tenant" '[{labels:
  {alertname: "MilvusQueryNodeMemoryHigh", severity: "warning", team: "vectordb", namespace: $ns,
   pod: "pipeline-demo", test: "true", run: $run}, startsAt: $now, endsAt: $ends}]')
curl -sf -X POST localhost:19093/api/v2/alerts -H 'Content-Type: application/json' -d "$alert" >/dev/null
echo "== hat kopukken sentetik alarm gönderildi (run=$run); rapor bekleniyor"
report=""
for _ in $(seq 1 45); do
  sleep 2
  report=$(curl -s "localhost:18080/reports?include_test=true" | jq -c --arg run "$run" '[.[]|select(.labels.run==$run)][0] // empty')
  [ -n "$report" ] && break
done
[ -n "$report" ] || { echo "FAIL rapor gelmedi"; exit 1; }
id=$(echo "$report" | jq -r .id)
curl -s "localhost:18080/reports/$id" | jq -r '.blind_spots[]?' > /tmp/sre-blind.txt
curl -s "localhost:18080/reports/$id.md" | head -12
[ -s /tmp/sre-blind.txt ] && echo "OK   rapor kör noktayı yazdı: $(paste -sd ';' /tmp/sre-blind.txt)" \
  || { echo "FAIL raporda kör nokta yok"; exit 1; }
echo "== SRE Agent metrikleri (son 1 sa): gelen alarm=$(prom 'sum(increase(sre_agent_alerts_received_total{status="firing"}[1h]))') rapor=$(prom 'sum(increase(sre_agent_reports_total[1h]))') kör noktalı=$(prom 'sum(increase(sre_agent_reports_total{blind_spot="true"}[1h]))')"
