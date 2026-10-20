#!/usr/bin/env bash
# Modele erişebilen başka bir kümede ölçüm (T2.1 gerçek çağrı, T2.4 profil, T2.6 karar ölçümü).
# Kendi namespace'inde geçici bir pod açar, model kataloğunu ve etiketli veri dosyasını kopyalar,
# ölçümleri koşar, sonuçları eval/results/ altına indirir ve pod'u siler. Hiçbir secret kullanmaz.
#
#   scripts/model-eval-remote.sh <kube-context> <namespace> <image> <veri.json> [model-url-ezmeleri...]
#   ör. ... QWEN3_6_35B_URL=http://vllm.example.internal:8520/v1/chat/completions
set -euo pipefail
CTX=$1 NS=$2 IMAGE=$3 DATA=$4; shift 4
POD=sre-agent-eval-$(date +%H%M%S)
STAMP=$(date -u +%Y%m%dT%H%MZ)
OUT=eval/results
kc() { kubectl --context "$CTX" -n "$NS" "$@"; }
envs=(); for kv in "$@"; do envs+=(--env "$kv"); done

kc run "$POD" --restart=Never --image="$IMAGE" "${envs[@]}" \
  --labels=app.kubernetes.io/part-of=sre-agent,app.kubernetes.io/name=sre-agent-eval \
  --command -- sleep 7200 >/dev/null
trap 'kc delete pod "$POD" --wait=false >/dev/null 2>&1 || true' EXIT
kc wait --for=condition=Ready "pod/$POD" --timeout=180s >/dev/null
kc cp config/models.yaml "$POD:/tmp/models.yaml"
kc cp "$DATA" "$POD:/tmp/data.json"

echo "== 1/3 gerçek çağrı (T2.1)"
kc exec "$POD" -- python -m sre_agent.llm check --catalog /tmp/models.yaml | tee "$OUT/$STAMP-model-check.txt"
echo "== 2/3 karar ölçümü, kurallar ve model (T2.6, olay başına)"
kc exec "$POD" -- python -m sre_agent.decisions evaluate --from-file /tmp/data.json --models /tmp/models.yaml \
  | tee "$OUT/$STAMP-decisions.txt"
echo "== 3/3 profil: düşünme kapalı/açık (T2.4)"
kc exec "$POD" -- python -m sre_agent.decisions profile --from-file /tmp/data.json --models /tmp/models.yaml \
  --n "${PROFILE_N:-30}" --out /tmp/profile.json | tail -12 | tee "$OUT/$STAMP-profile.txt"
kc cp "$POD:/tmp/profile.json" "$OUT/$STAMP-profile.json" >/dev/null
echo "sonuçlar: $OUT/$STAMP-*"
