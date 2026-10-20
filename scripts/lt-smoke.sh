#!/usr/bin/env bash
# Loki'ye örnek log, Tempo'ya örnek trace yollar ve ikisini geri okur.
# Log satırı trace_id içerir: Grafana'da log → trace geçişi de denenebilir.
# Kullanım: scripts/lt-smoke.sh <kube-context> <namespace> [curl-image]
set -euo pipefail

ctx="$1" ns="$2" image="${3:-docker.io/curlimages/curl:8.16.0}"
loki="http://loki.$ns.svc:3100"
tempo_otlp="http://tempo.$ns.svc:4318"
tempo="http://tempo.$ns.svc:3200"

trace_id=$(openssl rand -hex 16)
span_id=$(openssl rand -hex 8)
now_s=$(date +%s)
start_ns="${now_s}000000000"
end_ns="$((now_s + 1))000000000"
echo "trace_id: $trace_id"

kubectl --context "$ctx" -n "$ns" run "lt-smoke-$RANDOM" --rm -i --restart=Never --quiet \
  --image="$image" --command -- sh -eu -c "
curl -sf -X POST -H 'Content-Type: application/json' $tempo_otlp/v1/traces -d '{
  \"resourceSpans\": [{
    \"resource\": {\"attributes\": [{\"key\": \"service.name\", \"value\": {\"stringValue\": \"lt-smoke\"}}]},
    \"scopeSpans\": [{\"spans\": [{
      \"traceId\": \"$trace_id\", \"spanId\": \"$span_id\", \"name\": \"lt-smoke-span\", \"kind\": 1,
      \"startTimeUnixNano\": \"$start_ns\", \"endTimeUnixNano\": \"$end_ns\"
    }]}]
  }]
}' >/dev/null
echo 'trace gönderildi'

curl -sf -X POST -H 'Content-Type: application/json' $loki/loki/api/v1/push -d '{
  \"streams\": [{
    \"stream\": {\"job\": \"lt-smoke\", \"service_name\": \"lt-smoke\"},
    \"values\": [[\"$start_ns\", \"lt-smoke örnek log trace_id=$trace_id\"]]
  }]
}'
echo 'log gönderildi'

ok_log=0; ok_trace=0
for i in \$(seq 1 30); do
  if [ \$ok_log = 0 ] && curl -sf -G $loki/loki/api/v1/query_range \
      --data-urlencode 'query={job=\"lt-smoke\"} |= \"$trace_id\"' \
      --data-urlencode 'start=$((now_s - 300))000000000' | grep -q '$trace_id'; then
    echo 'OK   loki: log bulundu'; ok_log=1
  fi
  if [ \$ok_trace = 0 ] && curl -sf $tempo/api/v2/traces/$trace_id | grep -q 'lt-smoke-span'; then
    echo 'OK   tempo: trace bulundu'; ok_trace=1
  fi
  [ \$ok_log = 1 ] && [ \$ok_trace = 1 ] && exit 0
  sleep 2
done
[ \$ok_log = 1 ] || echo 'FAIL loki: log bulunamadı'
[ \$ok_trace = 1 ] || echo 'FAIL tempo: trace bulunamadı'
exit 1
"
