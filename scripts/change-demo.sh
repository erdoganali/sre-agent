#!/usr/bin/env bash
# T1.3 kabulü: sre-lab-app'e kasıtlı bir helm upgrade (image tag) ve kubectl edit (ConfigMap)
# uygular, ardından değişiklik kontrollerini read-only SA ile koşup raporu yazdırır.
# YAZMA YAPAR (sre-lab namespace'i). Kullanım:
#   scripts/change-demo.sh <kube-context> <monitoring-ns> <agent-ns> <serviceaccount> <lab-ns>
set -euo pipefail
ctx="$1" lab="$5"
kc() { kubectl --context "$ctx" -n "$lab" "$@"; }
chart="$(dirname "$0")/../deploy/helm/sre-lab-app"

helm upgrade --install sre-lab-app "$chart" --kube-context "$ctx" -n "$lab" --create-namespace --wait
start=$(( $(date -u +%s) - 5 ))

current=$(kc get deploy sre-lab-app -o jsonpath='{.spec.template.spec.containers[0].image}')
case "$current" in *3.14.7-slim) tag=3.14.6-slim ;; *) tag=3.14.7-slim ;; esac
echo "== helm upgrade: image tag → $tag"
helm upgrade sre-lab-app "$chart" --kube-context "$ctx" -n "$lab" --reuse-values --set image.tag="$tag" --wait

msg="edited-$(date +%s)"
echo "== kubectl edit: configmap/sre-lab-app message → $msg"
KUBE_EDITOR="perl -pi -e 's/^  message: .*/  message: $msg/'" kc edit configmap sre-lab-app

sleep 2
source "$(dirname "$0")/lib/it-env.sh"
it_env "$1" "$2" "$3" "$4"
export SRE_IT_CHANGE_SINCE="$start" SRE_IT_LAB_NS="$lab" SRE_IT_EXPECT_TAG="$tag" SRE_IT_EXPECT_MSG="$msg"
uv run pytest -m integration -o addopts="" -q -s tests/integration/test_change_detection.py
