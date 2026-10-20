#!/usr/bin/env bash
# Yedekleme servis hesabı için GCS HMAC anahtarı üretir ve doğrudan Secret'a yazar (ekrana basmaz).
# Kullanım: scripts/db-backup-secret.sh <kube-context> <agent-ns> <gcp-project> <service-account-email>
set -euo pipefail
ctx="$1" ns="$2" project="$3" sa="$4"
kc() { kubectl --context "$ctx" "$@"; }
if kc -n "$ns" get secret postgres-backup-s3 >/dev/null 2>&1; then
  echo "$ns/postgres-backup-s3 zaten var"; exit 0
fi
read -r access secret < <(gcloud storage hmac create "$sa" --project="$project" \
  --format='value(metadata.accessId,secret)')
[ -n "$access" ] && [ -n "$secret" ] || { echo "HMAC anahtarı üretilemedi"; exit 1; }
kc -n "$ns" create secret generic postgres-backup-s3 \
  --from-literal=access_key_id="$access" --from-literal=secret_access_key="$secret" >/dev/null
echo "$ns/postgres-backup-s3 oluşturuldu (HMAC erişim kimliği: ${access:0:8}…)"
