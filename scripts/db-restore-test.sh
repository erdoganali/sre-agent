#!/usr/bin/env bash
# Yedekten geri dönüş denemesi: nesne deposundaki son yedeği geçici bir veritabanına yükler, rapor
# sayısını canlı veritabanıyla karşılaştırır, geçici veritabanını siler. Canlı veriye yazmaz.
# Kullanım: scripts/db-restore-test.sh <kube-context> <agent-ns> <bucket> [provider] [endpoint]
set -uo pipefail
ctx="$1" ns="$2" bucket="$3" provider="${4:-GCS}" endpoint="${5:-https://storage.googleapis.com}"
kc() { kubectl --context "$ctx" -n "$ns" "$@"; }
job="postgres-restore-test-$(date +%s)"
cat <<YAML | kc apply -f - >/dev/null
apiVersion: batch/v1
kind: Job
metadata: {name: $job, labels: {app.kubernetes.io/part-of: sre-agent}}
spec:
  backoffLimit: 0
  ttlSecondsAfterFinished: 3600
  template:
    metadata: {labels: {app.kubernetes.io/name: postgres-backup, app.kubernetes.io/part-of: sre-agent}}
    spec:
      restartPolicy: Never
      automountServiceAccountToken: false
      securityContext: {runAsNonRoot: true, runAsUser: 999, runAsGroup: 999, seccompProfile: {type: RuntimeDefault}}
      initContainers:
        - name: fetch
          image: docker.io/rclone/rclone:1.75.1
          command: [sh, -c]
          args:
            - |
              latest=\$(rclone lsf --files-only ":s3:$bucket/postgres/" | sort | tail -1) &&
              echo "son yedek: \$latest" && rclone copyto ":s3:$bucket/postgres/\$latest" /backup/latest.dump
          env:
            - {name: RCLONE_S3_PROVIDER, value: "$provider"}
            - {name: RCLONE_S3_ENDPOINT, value: "$endpoint"}
            - {name: RCLONE_CONFIG, value: /tmp/rclone.conf}
            - {name: RCLONE_S3_ACCESS_KEY_ID, valueFrom: {secretKeyRef: {name: postgres-backup-s3, key: access_key_id}}}
            - {name: RCLONE_S3_SECRET_ACCESS_KEY, valueFrom: {secretKeyRef: {name: postgres-backup-s3, key: secret_access_key}}}
          volumeMounts: [{name: backup, mountPath: /backup}, {name: tmp, mountPath: /tmp}]
      containers:
        - name: restore
          image: docker.io/library/postgres:18.6
          command: [sh, -c]
          args:
            - |
              set -e;
              db=restore_test_\$(date +%s);
              createdb -h postgres "\$db";
              pg_restore -h postgres -d "\$db" --no-owner --no-privileges /backup/latest.dump;
              r=\$(psql -h postgres -d "\$db" -tAc "select count(*) from reports.reports");
              l=\$(psql -h postgres -d sre_agent -tAc "select count(*) from reports.reports");
              dropdb -h postgres "\$db";
              echo "yedekteki rapor=\$r canlı rapor=\$l";
              [ "\$r" -gt 0 ] && echo OK || { echo "FAIL yedek boş"; exit 1; }
          env:
            - {name: PGUSER, value: postgres}
            - {name: PGPASSWORD, valueFrom: {secretKeyRef: {name: postgres-auth, key: postgres-password}}}
          volumeMounts: [{name: backup, mountPath: /backup}]
      volumes: [{name: backup, emptyDir: {sizeLimit: 2Gi}}, {name: tmp, emptyDir: {}}]
YAML
kc wait --for=condition=complete --timeout=5m "job/$job" >/dev/null 2>&1 || kc wait --for=condition=failed --timeout=1s "job/$job" >/dev/null 2>&1
kc logs "job/$job" -c fetch 2>/dev/null | tail -2
kc logs "job/$job" -c restore 2>/dev/null | tail -3
kc get "job/$job" -o jsonpath='{.status.succeeded}' | grep -q 1
