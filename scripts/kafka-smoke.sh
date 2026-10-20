#!/usr/bin/env bash
# Her telemetri topic'ine benzersiz bir mesaj yazar ve geri okur.
# Kullanım: scripts/kafka-smoke.sh <kube-context> <namespace> <cluster-adı> <topic>...
set -euo pipefail

ctx="$1" ns="$2" cluster="$3"
shift 3
kc=(kubectl --context "$ctx" -n "$ns")

pod=$("${kc[@]}" get pod -l "strimzi.io/cluster=$cluster,strimzi.io/broker-role=true" \
  -o jsonpath='{.items[0].metadata.name}')
echo "broker pod: $pod"

fail=0
for topic in "$@"; do
  token="smoke-$(date +%s)-$RANDOM"
  echo "$token" | "${kc[@]}" exec -i "$pod" -c kafka -- \
    bin/kafka-console-producer.sh --bootstrap-server localhost:9092 --topic "$topic" >/dev/null
  if "${kc[@]}" exec "$pod" -c kafka -- \
    bin/kafka-console-consumer.sh --bootstrap-server localhost:9092 --topic "$topic" \
    --from-beginning --timeout-ms 20000 2>/dev/null | grep -qx "$token"; then
    echo "OK   $topic ($token)"
  else
    echo "FAIL $topic ($token)"
    fail=1
  fi
done
exit "$fail"
