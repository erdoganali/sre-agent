#!/usr/bin/env bash
# T1.7 kabulü: Alertmanager'a sentetik bir alarm gönderir (test="true"), sre-agent raporunu ve
# Grafana anotasyonunu bekler; alarm gönderiminden anotasyona kadar geçen süreyi ölçer (≤ 60 sn).
# Kullanım: scripts/report-demo.sh <kube-context> <monitoring-ns> <agent-ns>
set -uo pipefail
ctx="$1" mon="$2" agent_ns="$3"
kc() { kubectl --context "$ctx" "$@"; }

pids=()
trap 'kill "${pids[@]}" 2>/dev/null || true' EXIT
kc -n "$mon" port-forward svc/monitoring-kube-prometheus-alertmanager 19093:9093 >/dev/null 2>&1 & pids+=($!)
kc -n "$agent_ns" port-forward svc/sre-agent 18080:8080 >/dev/null 2>&1 & pids+=($!)
kc -n "$mon" port-forward svc/monitoring-grafana 13000:80 >/dev/null 2>&1 & pids+=($!)
for p in 19093 18080 13000; do for _ in $(seq 1 20); do nc -z localhost $p 2>/dev/null && break; sleep 0.5; done; done
# Grafana admin şifresini yalnızca ilk kurulumda Secret'tan alır; arayüzden değiştirildiyse
# GRAFANA_ADMIN_PASSWORD (ve gerekirse GRAFANA_ADMIN_USER) ortam değişkeniyle verin.
user=${GRAFANA_ADMIN_USER:-$(kc -n "$mon" get secret grafana-admin -o jsonpath='{.data.admin-user}' | base64 -d)}
pass=${GRAFANA_ADMIN_PASSWORD:-$(kc -n "$mon" get secret grafana-admin -o jsonpath='{.data.admin-password}' | base64 -d)}
curl -sf -o /dev/null -u "$user:$pass" localhost:13000/api/user \
  || { echo "FAIL Grafana admin girişi reddedildi (şifre değiştiyse GRAFANA_ADMIN_PASSWORD verin)"; exit 1; }

run="r$(date +%s)"
now=$(date -u +%Y-%m-%dT%H:%M:%SZ)
ends=$(date -u -v+5M +%Y-%m-%dT%H:%M:%SZ 2>/dev/null || date -u -d '+5 min' +%Y-%m-%dT%H:%M:%SZ)
alert=$(jq -nc --arg run "$run" --arg now "$now" --arg ends "$ends" '[{labels: {alertname: "MilvusQueryNodeMemoryHigh",
  severity: "warning", team: "vectordb", namespace: "acme", pod: "report-demo", test: "true", run: $run},
  annotations: {summary: "report-demo \($run)"}, startsAt: $now, endsAt: $ends}]')
start=$(date +%s)
curl -sf -X POST localhost:19093/api/v2/alerts -H 'Content-Type: application/json' -d "$alert" \
  || { echo "FAIL Alertmanager'a gönderilemedi"; exit 1; }
echo "run=$run alarm gönderildi; rapor ve anotasyon bekleniyor (group_wait 30 sn)..."

report="" ann=""
for _ in $(seq 1 45); do
  sleep 2
  report=$(curl -s "localhost:18080/reports?include_test=true" | jq -c --arg run "$run" '[.[]|select(.labels.run==$run)][0] // empty')
  ann=$(echo "$report" | jq -r '.annotation_id // empty' 2>/dev/null)
  [ -n "$ann" ] && break
done
elapsed=$(( $(date +%s) - start ))
[ -n "$report" ] || { echo "FAIL rapor gelmedi (${elapsed} sn)"; exit 1; }
id=$(echo "$report" | jq -r .id)
echo "== rapor $id (${elapsed} sn)"
curl -s "localhost:18080/reports/$id.md"
[ -n "$ann" ] || { echo "FAIL Grafana anotasyonu yok"; exit 1; }
echo "== Grafana anotasyonu $ann"
curl -s -u "$user:$pass" "localhost:13000/api/annotations?tags=sre-agent&limit=20" \
  | jq -r --argjson id "$ann" '.[]|select(.id==$id)|"tags=\(.tags|join(",")) zaman=\(.time)\n\(.text)"'
[ "$elapsed" -le 60 ] && echo "OK   alarmdan Grafana anotasyonuna ${elapsed} sn (≤ 60)" \
  || { echo "FAIL ${elapsed} sn > 60"; exit 1; }
