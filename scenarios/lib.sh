# Senaryo yardımcıları (scripts/scenario.sh tarafından yüklenir). CTX, ROOT, SCENARIO_DIR tanımlı.
LAB_NS=sre-lab
LAB_CHART="$ROOT/deploy/helm/sre-lab-app"

kc() { kubectl --context "$CTX" "$@"; }

# Deney uygulamasını chart değerleri + verilen --set'lerle günceller (beklemez: arıza zaten bekleten şey).
lab_set() {
  helm upgrade sre-lab-app "$LAB_CHART" --kube-context "$CTX" -n "$LAB_NS" \
    --reset-values --force-conflicts "$@" >/dev/null
}

# Temel hâl: chart varsayılanları (make lab-install ile aynı).
lab_restore() {
  helm upgrade --install sre-lab-app "$LAB_CHART" --kube-context "$CTX" -n "$LAB_NS" \
    --reset-values --force-conflicts --wait --timeout 3m >/dev/null
}

# Litmus deneyini başlatır: scenarios/<ad>/engine.yaml (ChaosEngine, namespace dosyada).
litmus_start() { kc apply -f "$SCENARIO_DIR/engine.yaml" >/dev/null; }

# Deneyi durdurur (Litmus etkisini geri alır) ve deneyin pod'ları kaybolana kadar bekler.
litmus_stop() {
  local ns name
  ns=$(yq '.metadata.namespace' "$SCENARIO_DIR/engine.yaml")
  name=$(yq '.metadata.name' "$SCENARIO_DIR/engine.yaml")
  kc -n "$ns" delete chaosengine "$name" --ignore-not-found --wait=true --timeout=3m >/dev/null
  for _ in $(seq 1 36); do
    left=$(kc -n "$ns" get pods --no-headers 2>/dev/null | grep -cE "^${name}-runner|-helper-" || true)
    [ "$left" = 0 ] && return 0
    sleep 5
  done
  echo "UYARI deneyin pod'ları 3 dk içinde kalkmadı:"; kc -n "$ns" get pods | grep -E "${name}-runner|-helper-" || true
}

fail() { echo "FAIL $*"; exit 1; }

# Milvus yük üreticisi (deploy/helm/milvus-load; make load-install): tek seferlik eylemi koşar ve bitmesini bekler.
LOAD_NS=loadtest
load_run() {
  local job="milvus-load-$1-$(date +%H%M%S)"
  kc -n "$LOAD_NS" get cronjob "milvus-load-$1" >/dev/null 2>&1 || fail "yük üreticisi kurulu değil (make load-install)"
  kc -n "$LOAD_NS" create job "$job" --from="cronjob/milvus-load-$1" >/dev/null
  kc -n "$LOAD_NS" wait --for=condition=Complete "job/$job" --timeout="${2:-15m}" >/dev/null \
    || { kc -n "$LOAD_NS" logs "job/$job" --tail=5; fail "yük işi bitmedi: $1"; }
  kc -n "$LOAD_NS" logs "job/$job" --tail=1
}
load_traffic() { kc -n "$LOAD_NS" scale deploy/milvus-load-traffic --replicas="$1" >/dev/null; }

# Temel hâle dönüş denetimi
verify() {
  case "$1" in
    lab)
      local want_image got_image msg
      want_image="$(yq '.image.repository' "$LAB_CHART/values.yaml"):$(yq '.image.tag' "$LAB_CHART/values.yaml")"
      got_image=$(kc -n "$LAB_NS" get deploy sre-lab-app -o jsonpath='{.spec.template.spec.containers[0].image}')
      [ "$got_image" = "$want_image" ] || fail "image $got_image (beklenen $want_image)"
      msg=$(kc -n "$LAB_NS" get configmap sre-lab-app -o jsonpath='{.data.message}')
      [ "$msg" = "$(yq '.message' "$LAB_CHART/values.yaml")" ] || fail "ConfigMap message='$msg'"
      kc -n "$LAB_NS" rollout status deploy/sre-lab-app --timeout=2m >/dev/null || fail "uygulama hazır değil"
      [ -z "$(kc -n "$LAB_NS" get chaosengines -o name 2>/dev/null)" ] || fail "ChaosEngine kaldı"
      echo "OK   sre-lab temel hâlde: $got_image, message=$msg, 1/1 hazır, deney yok"
      ;;
    loadtest)
      [ -z "$(kc -n loadtest get chaosengines -o name 2>/dev/null)" ] || fail "ChaosEngine kaldı"
      if kc -n loadtest get deploy milvus-load-traffic >/dev/null 2>&1; then
        [ "$(kc -n loadtest get deploy milvus-load-traffic -o jsonpath='{.spec.replicas}')" = 0 ] || fail "yük trafiği açık"
        load_run status 3m | grep -q "yükleme=\['NotLoad'\]" || fail "büyük collection bellekte (release edilmedi)"
      fi
      kc -n loadtest wait --for=condition=Ready pod -l app.kubernetes.io/name=milvus --timeout=3m >/dev/null \
        || fail "loadtest Milvus pod'ları hazır değil"
      if kc -n loadtest get sts cus-loadtest-etcd >/dev/null 2>&1; then
        [ "$(kc -n loadtest get sts cus-loadtest-etcd -o jsonpath='{.status.readyReplicas}')" = 3 ] \
          || fail "loadtest etcd'nin 3 üyesi hazır değil"
      fi
      echo "OK   loadtest temel hâlde: deney yok, trafik kapalı, veri bellekte değil, Milvus ve etcd hazır"
      ;;
    tenant:*)
      # Herhangi bir Milvus tenant'ı: deney kalmadı, Milvus pod'ları ve etcd'nin bütün üyeleri hazır
      local ns="${1#tenant:}" sts want got
      [ -z "$(kc -n "$ns" get chaosengines -o name 2>/dev/null)" ] || fail "ChaosEngine kaldı"
      kc -n "$ns" wait --for=condition=Ready pod -l app.kubernetes.io/name=milvus --timeout=5m >/dev/null \
        || fail "$ns Milvus pod'ları hazır değil"
      sts=$(kc -n "$ns" get sts -o name | grep -- '-etcd$' | head -1)
      if [ -n "$sts" ]; then
        want=$(kc -n "$ns" get "$sts" -o jsonpath='{.spec.replicas}')
        got=$(kc -n "$ns" get "$sts" -o jsonpath='{.status.readyReplicas}')
        [ "$got" = "$want" ] || fail "$ns etcd: $got/$want üye hazır"
      fi
      echo "OK   $ns temel hâlde: deney yok, Milvus ve etcd hazır"
      ;;
    monitoring)
      kc -n monitoring rollout status deploy/telemetry-consumer-opentelemetry-collector --timeout=3m >/dev/null \
        || fail "telemetry-consumer hazır değil"
      echo "OK   telemetry-consumer 1/1 hazır (lag birkaç dakikada sıfıra iner)"
      ;;
    *) fail "bilinmeyen verify: $1" ;;
  esac
}
