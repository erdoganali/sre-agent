load_traffic 1
kc -n "$LOAD_NS" rollout status deploy/milvus-load-traffic --timeout=3m >/dev/null
sleep 60   # trafik GCS'ye yazmaya başlasın (flush)
litmus_start
