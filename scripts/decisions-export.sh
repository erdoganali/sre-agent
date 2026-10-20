#!/usr/bin/env bash
# Etiketli raporları dışa aktarır (decisions export). Pod içinde arka planda dosyaya yazılır ve
# hazır olunca kopyalanır: uzun exec akışı kesiliyor (çıktı ~4 MB, bağlantı sıfırlanıyordu).
#   scripts/decisions-export.sh <kube-context> <namespace> <hedef.json>
set -euo pipefail
ctx="$1" ns="$2" out="$3"
kc() { kubectl --context "$ctx" -n "$ns" "$@"; }
pod=$(kc get pod -l app.kubernetes.io/name=sre-agent -o jsonpath='{.items[0].metadata.name}')
kc exec "$pod" -- rm -f /tmp/export.json /tmp/export.done /tmp/export.err
kc exec "$pod" -- sh -c 'nohup sh -c "python -m sre_agent.decisions export > /tmp/export.json 2>/tmp/export.err; echo \$? > /tmp/export.done" </dev/null >/dev/null 2>&1 &'
for _ in $(seq 1 120); do
  kc exec "$pod" -- test -f /tmp/export.done 2>/dev/null && break
  sleep 2
done
rc=$(kc exec "$pod" -- cat /tmp/export.done)
[ "$rc" = 0 ] || { kc exec "$pod" -- tail -5 /tmp/export.err; exit 1; }
mkdir -p "$(dirname "$out")"
kc cp "$pod:/tmp/export.json" "$out" >/dev/null
kc exec "$pod" -- rm -f /tmp/export.json /tmp/export.done /tmp/export.err
python3 -c "import json,sys; print(len(json.load(open(sys.argv[1]))), 'rapor →', sys.argv[1])" "$out"
