#!/usr/bin/env bash
# Specialist ölçümü (T3.4): veri GKE'de, model gateway'de. GKE'deki MCP sunucularına port-forward
# açılır, ölçüm yerelde koşar, port-forward'lar kapatılır. Hiçbir şey kaydedilmez.
# Anahtar ortam değişkeninden: EAG_KEY (dosyaya ya da komut satırına yazılmaz).
#   scripts/specialists-eval.sh <kube-context> <veri.json> [eval-specialists argümanları...]
set -euo pipefail
ctx="$1" data="$2"; shift 2
: "${EAG_KEY:?EAG_KEY ortam değişkeni gerekli (read -s EAG_KEY; export EAG_KEY)}"
servers=(metrics logs traces k8s product)
pids=() args=()
for i in "${!servers[@]}"; do
  port=$((18181 + i))
  # port-forward bir bağlantı hatasında kapanıyor; döngü onu yeniden açar
  ( while true; do
      kubectl --context "$ctx" -n sre-agent port-forward "svc/sre-agent-mcp-${servers[$i]}" "$port:8080" >/dev/null 2>&1
      sleep 1
    done ) &
  pids+=($!)
  args+=(--mcp "${servers[$i]}=http://localhost:$port/mcp")
done
trap 'kill "${pids[@]}" 2>/dev/null; pkill -f "port-forward svc/sre-agent-mcp-" 2>/dev/null || true' EXIT
for _ in $(seq 1 30); do
  [ "$(lsof -nP -iTCP -sTCP:LISTEN 2>/dev/null | grep -cE ':1818[1-5] ')" -ge 5 ] && break
  sleep 1
done
EAG_AUTH="Bearer $EAG_KEY" \
EAG_URL="${EAG_URL:-http://llm-gateway.example.internal/v1/chat/completions}" \
SRE_DECISION_MODEL="${MODEL:-gpt-4.1-mini}" SRE_SPECIALIST_MODEL="${MODEL:-gpt-4.1-mini}" \
SRE_SYNTHESIZE_MODEL="${MODEL:-gpt-4.1-mini}" \
  uv run python -m sre_agent.graph eval-specialists --from-file "$data" "${args[@]}" "$@"
