#!/usr/bin/env bash
# VectorDB → Kafka → telemetry-consumer → Prometheus/Loki hattını doğrular (yalnızca okuma).
# Milvus metrikleri PodMonitor ile zaten scrape edildiği için Prometheus'ta metrik görmek yetmez;
# consumer'ın gönderdiği kayıt sayılarına bakılır. (Prometheus 3: OTel sayaçlarında _total son eki yok.)
# Kullanım: scripts/telemetry-check.sh <kube-context> <monitoring-ns> <kafka-ns> <kafka-cluster>
set -uo pipefail

ctx="$1" ns="$2" kafka_ns="$3" cluster="$4"
kc() { kubectl --context "$ctx" "$@"; }

kc -n "$ns" port-forward svc/monitoring-kube-prometheus-prometheus 19090:9090 >/dev/null 2>&1 & pf1=$!
kc -n "$ns" port-forward svc/loki 13100:3100 >/dev/null 2>&1 & pf2=$!
trap 'kill $pf1 $pf2 2>/dev/null' EXIT
sleep 3

prom() { curl -s -G localhost:19090/api/v1/query --data-urlencode "query=$1" | jq -r '.data.result[]|"\(.metric|del(.__name__)|to_entries|map("\(.key)=\(.value)")|join(",")) \(.value[1])"'; }
fail=0

echo "== 1. telemetry-consumer'ın Kafka'dan okuduğu ve ilettiği kayıtlar (toplam)"
out=$(prom 'sum by (exporter) ({__name__=~"otelcol_exporter_sent_metric_points(_total)?",job=~".*telemetry-consumer.*"}) or sum by (exporter) ({__name__=~"otelcol_exporter_sent_log_records(_total)?",job=~".*telemetry-consumer.*"})')
echo "${out:-  (sonuç yok)}"
echo "$out" | grep -q 'prometheusremotewrite' || { echo "FAIL metrik iletimi yok"; fail=1; }
echo "$out" | grep -q -E 'otlp_?http/loki' || { echo "FAIL log iletimi yok"; fail=1; }

echo "== 2. Son 10 dakikadaki iletim hataları (boşsa hata yok)"
prom 'sum by (exporter) (increase({__name__=~"otelcol_exporter_send_failed_(metric_points|log_records)(_total)?",job=~".*telemetry-consumer.*"}[10m])) > 0'

echo "== 3. Loki: son 1 saatte log_source'a göre VectorDB log sayısı"
loki_out=$(curl -s -G localhost:13100/loki/api/v1/query \
  --data-urlencode 'query=sum by (log_source) (count_over_time({log_source=~".+"}[1h]))' \
  | jq -r '.data.result[]|"\(.metric.log_source) \(.value[1])"')
echo "${loki_out:-  (sonuç yok)}"
[ -n "$loki_out" ] || { echo "FAIL Loki'de VectorDB logu yok"; fail=1; }

echo "== 4. Kafka yoluyla gelen Milvus metrikleri (job=milvus-metrics, en çok seri, ilk 10)"
curl -s -G localhost:19090/api/v1/query --data-urlencode 'query=topk(10, count by (__name__) ({job="milvus-metrics"}))' \
  | jq -r '.data.result[]|"\(.metric.__name__) \(.value[1])"'

echo "== 5. Kafka consumer group lag (telemetry-consumer)"
pod=$(kc -n "$kafka_ns" get pod -l "strimzi.io/cluster=$cluster,strimzi.io/broker-role=true" -o jsonpath='{.items[0].metadata.name}')
kc -n "$kafka_ns" exec "$pod" -c kafka -- bin/kafka-consumer-groups.sh --bootstrap-server localhost:9092 \
  --describe --group telemetry-consumer 2>/dev/null | awk 'NR==1 || /vectordb_/ {print $2, $3, $4, $5, $6}' | column -t

exit "$fail"
