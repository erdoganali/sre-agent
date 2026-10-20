#!/usr/bin/env bash
# MCP sunucularının kimlikleri (ADR-030): k8s ve product ajanın okuma rolüyle aynı sınırlarda
# (rbac-check.sh); metrics, logs, traces Kubernetes'te hiçbir şey okuyamaz.
# Kullanım: scripts/rbac-check-mcp.sh <kube-context> <agent-ns>
set -uo pipefail
ctx="$1" ns="$2"
fail=0
for s in k8s product; do
  echo "== sre-agent-mcp-$s (okuma rolü)"
  scripts/rbac-check.sh "$ctx" "$ns" "sre-agent-mcp-$s" | grep -v "^OK" || true
  scripts/rbac-check.sh "$ctx" "$ns" "sre-agent-mcp-$s" >/dev/null || fail=1
done
for s in metrics logs traces; do
  echo "== sre-agent-mcp-$s (Kubernetes erişimi yok)"
  as="system:serviceaccount:$ns:sre-agent-mcp-$s"
  for r in pods pods/log events nodes configmaps secrets milvuses.milvus.io; do
    for v in get list; do
      got=$(kubectl --context "$ctx" auth can-i "$v" "$r" --as="$as" -A 2>/dev/null)
      [ "$got" = "no" ] || { echo "FAIL  $v $r → $got"; fail=1; }
    done
  done
done
[ "$fail" = 0 ] && echo "MCP kimlikleri: beklenen yetkiler (okuma rolü 2, rolsüz 3)"
exit "$fail"
