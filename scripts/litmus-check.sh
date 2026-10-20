#!/usr/bin/env bash
# Litmus ajanının yetkileri litmus-scope ile sınırlı mı? (yalnızca okuma; ADR-026)
#  - izinli namespace'lerde deney hesapları pod silebilir
#  - korunan namespace'lerde silemez, hiçbir hedef namespace'te secret okuyamaz
#  - ajanın küme çapındaki yazma bağları geri gelmemiş (ajan yeniden kurulunca gelir)
# Kullanım: scripts/litmus-check.sh <kube-context> <litmus-ns> <litmus-scope-values>
set -uo pipefail
ctx="$1" lns="$2" values="$3"
allowed=$(yq '.namespaces[]' "$values")
forbidden=$(yq '.forbiddenNamespaces[]' "$values")
sas="litmus-admin argo-chaos litmus"
fail=0
kubectl --context "$ctx" get namespace "$lns" >/dev/null || { echo "FAIL kümeye bağlanılamadı ya da $lns yok"; exit 1; }
check() {  # beklenen fiil kaynak ns hesap
  local got
  got=$(kubectl --context "$ctx" auth can-i "$2" "$3" -n "$4" --as="system:serviceaccount:$lns:$5" 2>/dev/null)
  if [ "$got" = "$1" ]; then st=OK; else st=FAIL; fail=1; fi
  printf '%-5s %-6s %-7s %-13s %-12s beklenen=%-3s sonuç=%s\n' "$st" "$2" "$3" "$4" "$5" "$1" "$got"
}
for sa in $sas; do
  for ns in $allowed; do check yes delete pods "$ns" "$sa"; check no get secrets "$ns" "$sa"; done
  for ns in $forbidden; do check no delete pods "$ns" "$sa"; done
done
crb=$(kubectl --context "$ctx" get clusterrolebinding -o json) || { echo "FAIL ClusterRoleBinding'ler okunamadı"; exit 1; }
extra=$(echo "$crb" | jq -r --arg ns "$lns" \
  '.items[] | select(any(.subjects[]?; .namespace==$ns)) | select(.roleRef.name != "litmus-scope-read") | .metadata.name')
if [ -n "$extra" ]; then
  echo "FAIL ajanın küme çapındaki yazma bağları geri gelmiş: $extra  →  make litmus-scope"; fail=1
else
  echo "OK    küme çapında yalnızca litmus-scope-read bağı var"
fi
exit "$fail"
