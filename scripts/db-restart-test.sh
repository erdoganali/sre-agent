#!/usr/bin/env bash
# Kabul: raporlar pod yeniden başlasa da duruyor mu? sre-agent ve Postgres pod'ları silinir
# (StatefulSet/Deployment yeniden açar), rapor listesi öncesi ve sonrası karşılaştırılır.
# Kullanım: scripts/db-restart-test.sh <kube-context> <agent-ns>
set -uo pipefail
ctx="$1" ns="$2"
kc() { kubectl --context "$ctx" -n "$ns" "$@"; }
list() {  # servis yanıt verene kadar (en fazla ~2 dk) dener
  local out=""
  for _ in $(seq 1 12); do
    kc port-forward svc/sre-agent 28081:8080 >/dev/null 2>&1 & local pf=$!
    for _ in $(seq 1 20); do nc -z localhost 28081 2>/dev/null && break; sleep 0.5; done
    out=$(curl -sf "localhost:28081/reports?include_test=true&limit=500" | jq -r '.[].id' | sort)
    kill $pf 2>/dev/null; wait $pf 2>/dev/null
    [ -n "$out" ] && break
    sleep 10
  done
  echo "$out"
}
before=$(list); n=$(echo "$before" | grep -c .)
[ "$n" -gt 0 ] || { echo "FAIL önce rapor yok (make report-demo ile üretin)"; exit 1; }
echo "önce: $n rapor; pod'lar yeniden başlatılıyor"
kc delete pod -l app.kubernetes.io/name=postgres --wait=true >/dev/null
kc delete pod -l app.kubernetes.io/name=sre-agent --wait=true >/dev/null
kc rollout status statefulset/postgres --timeout=3m >/dev/null
kc rollout status deploy/sre-agent --timeout=3m >/dev/null
after=$(list); m=$(echo "$after" | grep -c .)
echo "sonra: $m rapor"
if [ "$before" = "$after" ]; then echo "OK   raporlar yeniden başlatmadan sonra aynı"; else
  echo "FAIL rapor listesi değişti"; diff <(echo "$before") <(echo "$after"); exit 1; fi
