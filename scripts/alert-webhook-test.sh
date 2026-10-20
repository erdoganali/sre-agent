#!/usr/bin/env bash
# Alertmanager → sre-agent: gruplama ve inhibition'ı sentetik alarmlarla doğrular.
# sre-agent her bildirimi tek satır JSON (event=webhook) olarak loglar.
#  1) Aynı alertname'le 3 alarm → webhook'a TEK bildirimde 3 alarm gelmeli.
#  2) Aynı PVC için PVCUsageCritical + PVCUsageHigh → yalnız Critical gelmeli (High bastırılır).
# Test alarmları test="true" ve run=<id> etiketi taşır, birkaç dakikada kendiliğinden resolved olur.
# Kullanım: scripts/alert-webhook-test.sh <kube-context> <monitoring-ns> <agent-ns>
set -uo pipefail

ctx="$1" ns="$2" agent_ns="$3"
kc() { kubectl --context "$ctx" -n "$ns" "$@"; }
run="t$(date +%s)"
ends=$(date -u -v+3M +%Y-%m-%dT%H:%M:%SZ 2>/dev/null || date -u -d '+3 min' +%Y-%m-%dT%H:%M:%SZ)

kc port-forward svc/monitoring-kube-prometheus-alertmanager 19093:9093 >/dev/null 2>&1 & pf=$!
trap 'kill $pf 2>/dev/null' EXIT
for _ in $(seq 1 20); do curl -sf localhost:19093/-/ready >/dev/null && break; sleep 1; done
curl -sf localhost:19093/-/ready >/dev/null || { echo "FAIL Alertmanager'a port-forward kurulamadı (19093 kullanımda olabilir)"; exit 1; }

alert() { # alertname severity ek-etiketler(json)
  jq -nc --arg n "$1" --arg s "$2" --arg run "$run" --arg ends "$ends" --argjson extra "$3" \
    '{labels: ({alertname: $n, severity: $s, team: "vectordb", test: "true", run: $run} + $extra),
      annotations: {summary: "alert-webhook-test \($run)"}, endsAt: $ends}'
}
payload=$(jq -s . \
  <(alert AlertWebhookGroupTest warning '{"pod":"test-pod-1"}') \
  <(alert AlertWebhookGroupTest warning '{"pod":"test-pod-2"}') \
  <(alert AlertWebhookGroupTest warning '{"pod":"test-pod-3"}') \
  <(alert PVCUsageCritical critical '{"namespace":"sre-test","persistentvolumeclaim":"test-pvc"}') \
  <(alert PVCUsageHigh warning '{"namespace":"sre-test","persistentvolumeclaim":"test-pvc"}'))
resp=$(curl -s -w '\n%{http_code}' -X POST localhost:19093/api/v2/alerts -H 'Content-Type: application/json' -d "$payload")
[ "$(echo "$resp" | tail -1)" = "200" ] || { echo "FAIL Alertmanager'a gönderilemedi: $resp"; exit 1; }
echo "run=$run: 5 test alarmı gönderildi, group_wait (30 sn) bekleniyor..."

lines=""
for _ in $(seq 1 12); do
  sleep 10
  lines=$(kubectl --context "$ctx" -n "$agent_ns" logs deploy/sre-agent --since=5m \
    | grep '"event": "webhook"' | grep "\"run\": \"$run\"" || true)
  echo "$lines" | grep -q AlertWebhookGroupTest && echo "$lines" | grep -q PVCUsageCritical && break
done
fail=0

group=$(echo "$lines" | jq -c 'select(.groupLabels.alertname=="AlertWebhookGroupTest" and .status=="firing")' | head -1)
if [ -n "$group" ] && [ "$(echo "$group" | jq .alertCount)" = "3" ]; then
  echo "OK   gruplama: AlertWebhookGroupTest tek bildirimde 3 alarm ($(echo "$group" | jq -r '[.alerts[].pod]|join(", ")'))"
else
  echo "FAIL gruplama: tek bildirimde 3 alarm görülmedi"; fail=1
fi

if echo "$lines" | jq -e 'select(.groupLabels.alertname=="PVCUsageCritical")' >/dev/null 2>&1; then
  echo "OK   PVCUsageCritical webhook'a ulaştı"
else
  echo "FAIL PVCUsageCritical webhook'ta yok"; fail=1
fi
if echo "$lines" | jq -e 'select(.groupLabels.alertname=="PVCUsageHigh")' >/dev/null 2>&1; then
  echo "FAIL inhibition: PVCUsageHigh webhook'a ulaştı"; fail=1
else
  state=$(curl -s "localhost:19093/api/v2/alerts?filter=run=\"$run\"&filter=alertname=\"PVCUsageHigh\"&inhibited=true" \
    | jq -r '.[0].status | "\(.state) inhibitedBy=\(.inhibitedBy|length)"')
  echo "OK   inhibition: PVCUsageHigh webhook'a gitmedi (Alertmanager: $state)"
fi

echo "== webhook logundaki bildirimler (run=$run)"
echo "$lines" | jq -c '{status, groupLabels, alertCount, alerts: [.alerts[]|.alertname+"/"+(.pod // .persistentvolumeclaim)]}'
exit "$fail"
