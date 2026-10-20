#!/usr/bin/env bash
# Read-only ServiceAccount'un yetkilerini kubectl auth can-i ile tablolar (yalnızca okuma).
# Beklenen: okuma evet; yazma ve secrets hayır. Beklenmeyen bir sonuç varsa çıkış kodu 1.
# Kullanım: scripts/rbac-check.sh <kube-context> <agent-ns> <serviceaccount>
set -uo pipefail
ctx="$1" ns="$2" sa="$3"
as="system:serviceaccount:$ns:$sa"
fail=0
check() { # beklenen fiil kaynak [namespace]
  local want="$1" verb="$2" res="$3" target="${4:-monitoring}"
  got=$(kubectl --context "$ctx" auth can-i "$verb" "$res" --as="$as" -n "$target" 2>/dev/null)
  mark=OK; [ "$got" = "$want" ] || { mark=FAIL; fail=1; }
  printf "%-5s %-7s %-32s %-12s beklenen=%-3s sonuç=%s\n" "$mark" "$verb" "$res" "$target" "$want" "$got"
}
for r in pods pods/log events nodes configmaps persistentvolumeclaims deployments.apps \
         replicasets.apps controllerrevisions.apps statefulsets.apps jobs.batch networkpolicies.networking.k8s.io; do
  check yes list "$r"
done
check yes get pods vectordb
check yes list milvuses.milvus.io acme
check yes watch events acme
for v in create update patch delete; do
  for r in pods deployments.apps configmaps; do check no "$v" "$r"; done
done
check no create pods/exec
check no create pods/portforward
for v in get list watch; do check no "$v" secrets; check no "$v" secrets kafka; done
check no create serviceaccounts/token
check no "*" "*"
exit "$fail"
