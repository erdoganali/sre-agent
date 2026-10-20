#!/usr/bin/env bash
# Chaos senaryosu (T1.8): scripts/scenario.sh inject|cleanup <senaryo> <kube-context>
# Her senaryo scenarios/<ad>/ altında: inject.sh, cleanup.sh, expected.yaml (+ Litmus deneyi için engine.yaml).
# cleanup sonunda ortamın temel hâline döndüğü senaryonun `verify` alanına göre denetlenir.
set -euo pipefail
action="${1:?inject|cleanup}" name="${2:?senaryo}" ctx="${3:?kube-context}"
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
SCENARIO_DIR="$ROOT/scenarios/$name"
[ -f "$SCENARIO_DIR/expected.yaml" ] || { echo "senaryo yok: $name (make scenarios)"; exit 1; }
export CTX="$ctx" ROOT SCENARIO_DIR
# shellcheck source=../scenarios/lib.sh
source "$ROOT/scenarios/lib.sh"

echo "== $name: $(yq '.title' "$SCENARIO_DIR/expected.yaml")"
case "$action" in
  inject)
    # shellcheck disable=SC1091
    source "$SCENARIO_DIR/inject.sh"
    echo "OK   enjekte edildi $(date -u +%H:%M:%SZ). Beklenen alarm: $(yq -o=csv '.expected.alerts' "$SCENARIO_DIR/expected.yaml")," \
      "yaklaşık $(yq '.expected.alert_within_min' "$SCENARIO_DIR/expected.yaml") dk içinde."
    echo "     temizlik: make scenario-cleanup S=$name"
    ;;
  cleanup)
    # shellcheck disable=SC1091
    source "$SCENARIO_DIR/cleanup.sh"
    verify "$(yq '.verify' "$SCENARIO_DIR/expected.yaml")"
    ;;
  *) echo "bilinmeyen eylem: $action"; exit 1 ;;
esac
