#!/usr/bin/env bash
# Grafana'da "sre-agent" servis hesabı (Editor: anotasyon yazma, ADR-022) ve token'ı;
# token sre-agent namespace'inde Secret olarak saklanır, repoya girmez. Tekrar çalıştırılabilir.
# Kullanım: scripts/grafana-token.sh <kube-context> <monitoring-ns> <agent-ns> <secret-adı>
set -euo pipefail
ctx="$1" mon="$2" agent_ns="$3" secret="$4"
kc() { kubectl --context "$ctx" "$@"; }

if kc -n "$agent_ns" get secret "$secret" >/dev/null 2>&1; then
  echo "Secret $agent_ns/$secret zaten var; yenilemek için önce silin."
  exit 0
fi

kc -n "$mon" port-forward svc/monitoring-grafana 13000:80 >/dev/null 2>&1 & pf=$!
trap 'kill $pf 2>/dev/null || true' EXIT
for _ in $(seq 1 20); do nc -z localhost 13000 2>/dev/null && break; sleep 0.5; done
# Grafana admin şifresini yalnızca ilk kurulumda Secret'tan alır; arayüzden değiştirildiyse
# GRAFANA_ADMIN_PASSWORD (ve gerekirse GRAFANA_ADMIN_USER) ortam değişkeniyle verin.
user=${GRAFANA_ADMIN_USER:-$(kc -n "$mon" get secret grafana-admin -o jsonpath='{.data.admin-user}' | base64 -d)}
pass=${GRAFANA_ADMIN_PASSWORD:-$(kc -n "$mon" get secret grafana-admin -o jsonpath='{.data.admin-password}' | base64 -d)}
curl -sf -o /dev/null -u "$user:$pass" localhost:13000/api/user \
  || { echo "FAIL Grafana admin girişi reddedildi (şifre değiştiyse GRAFANA_ADMIN_PASSWORD verin)"; exit 1; }
api() { curl -sf -u "$user:$pass" -H 'Content-Type: application/json' "localhost:13000$1" "${@:2}"; }

id=$(api "/api/serviceaccounts/search?query=sre-agent" | jq -r '.serviceAccounts[] | select(.name=="sre-agent") | .id')
if [ -z "$id" ]; then
  id=$(api /api/serviceaccounts -X POST -d '{"name":"sre-agent","role":"Editor"}' | jq -r .id)
  echo "Grafana servis hesabı oluşturuldu (id=$id, Editor)"
fi
token=$(api "/api/serviceaccounts/$id/tokens" -X POST -d "{\"name\":\"sre-agent-$(date +%s)\"}" | jq -r .key)
kc create namespace "$agent_ns" --dry-run=client -o yaml | kc apply -f - >/dev/null
kc -n "$agent_ns" create secret generic "$secret" --from-literal=token="$token"
echo "Secret $agent_ns/$secret oluşturuldu"
