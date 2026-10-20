#!/usr/bin/env bash
# Postgres şifreleri (rastgele) → Secret'lar; repoya girmez. Varsa dokunmaz.
#   <agent-ns>/postgres-auth: postgres-password, app-password, ro-password, database-url
#   <monitoring-ns>/grafana-postgres-ro: GRAFANA_PG_RO_PASSWORD (Grafana datasource'u)
# Kullanım: scripts/db-secrets.sh <kube-context> <agent-ns> <monitoring-ns>
set -euo pipefail
ctx="$1" ns="$2" mon="$3"
kc() { kubectl --context "$ctx" "$@"; }
kc create namespace "$ns" --dry-run=client -o yaml | kc apply -f - >/dev/null

if kc -n "$ns" get secret postgres-auth >/dev/null 2>&1; then
  echo "$ns/postgres-auth zaten var"
else
  app=$(openssl rand -hex 24)
  kc -n "$ns" create secret generic postgres-auth \
    --from-literal=postgres-password="$(openssl rand -hex 24)" \
    --from-literal=app-password="$app" \
    --from-literal=ro-password="$(openssl rand -hex 24)" \
    --from-literal=database-url="postgresql://sre_agent:${app}@postgres.${ns}.svc:5432/sre_agent"
  echo "$ns/postgres-auth oluşturuldu"
fi

if kc -n "$mon" get secret grafana-postgres-ro >/dev/null 2>&1; then
  echo "$mon/grafana-postgres-ro zaten var"
else
  ro=$(kc -n "$ns" get secret postgres-auth -o jsonpath='{.data.ro-password}' | base64 -d)
  kc -n "$mon" create secret generic grafana-postgres-ro --from-literal=GRAFANA_PG_RO_PASSWORD="$ro"
  echo "$mon/grafana-postgres-ro oluşturuldu"
fi
