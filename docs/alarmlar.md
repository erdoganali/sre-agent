# VectorDB alarm kuralları

Kaynak: `deploy/helm/vectordb-alerts` (eşikler `values.yaml` → `thresholds`). Eşik değişirse bu tablo da güncellenir.
Milvus metrikleri hedef ortamdaki yoldan gelir: VectorDB gateway → Kafka → telemetry-consumer (`job="milvus-metrics"`).
Namespace'ler: platform `vectordb`, tenant `acme|loadtest`; PVC için ek olarak `kafka|monitoring`.

| # | Alarm | Metrik | Eşik | Süre | Önem | Gerekçe |
| --- | --- | --- | --- | --- | --- | --- |
| 1 | MilvusQueryNodeMemoryHigh | querynode working set / bellek limiti | > %80 | 5 dk | warning | Milvus %85'te yazmayı yavaşlatır, %95'te keser; önceden uyarı. Bellek hızlı yükselebildiği için kısa bekleme; kontrol (`milvus_querynode_memory_quota`) da %80'de anomali sayar |
| 2 | MilvusQueryNodeMemoryNearQuota | aynı | > %90 | 2 dk | critical | Yazma kesilmesine (qn_mem_high_water_level 0.95) %5 kaldı |
| 3 | MilvusWriteDenied | `milvus_rootcoord_force_deny_writing_counter_total` artışı | > 0 (5 dk) | anında | critical | Kota nedeniyle yazmalar reddediliyor |
| 4 | MilvusSearchLatencyP99High | `milvus_proxy_req_latency` p99 (Search/HybridSearch/Query) | > 1000 ms | 10 dk | warning | Kullanıcıya görünen arama gecikmesi |
| 5 | MilvusRequestErrorRateHigh | `milvus_proxy_req_count_total` fail / total | > %5 | 10 dk | warning | Hata oranı. Sayaç ilk istekte oluşur; boşta tenant'ta seri yoktur |
| 6 | MilvusTenantTelemetryStale | tenant başına `milvus_rootcoord_timestamp` son 6 sa var, son 10 dk yok | 10 dk | 1 dk | critical | Tenant'ın telemetrisi koptu; raporlar kör noktalı (T1.14) |
| 7 | VectorDBPodRestarting | `kube_pod_container_status_restarts_total` artışı | ≥ 3 / 15 dk | anında | warning | Kararsız pod |
| 8 | VectorDBPodCrashLooping | `kube_pod_container_status_waiting_reason{reason="CrashLoopBackOff"}` | = 1 | 5 dk | critical | Pod ayağa kalkamıyor |
| 9 | PVCUsageHigh | kubelet volume used / capacity | > %80 | 10 dk | warning | Disk büyütme/temizlik zamanı |
| 10 | PVCUsageCritical | aynı | > %90 | 5 dk | critical | Disk dolmak üzere (Milvus etcd, Kafka, Prometheus, Loki, Tempo) |
| 11 | EphemeralStorageNearLimit | `ephemeral_storage_pod_usage` / 1Gi | > %80 | 5 dk | warning | Hedef ortamda pod başı ephemeral sınırı 1Gi; aşılırsa evict |
| 12 | EmptyDirNearSizeLimit | `ephemeral_storage_container_volume_limit_percentage` | > %80 | 5 dk | warning | sizeLimit'li emptyDir (access log 384Mi) dolarsa evict |
| 13 | PodEvicted | `kube_pod_status_reason{reason="Evicted"}` | > 0 | anında | warning | Ephemeral/bellek baskısının sonucu |
| 14 | KafkaConsumerLagHigh | `kafka_consumergroup_lag` (grup+topic toplamı) | > 1 günlük ortalamanın 2 katı **ve** > 100 mesaj | 5 dk | warning | Tüketici geride kalıyor (plandaki "baseline'ın 2 katı") |
| 15 | KafkaTopicNoIncomingData | `kafka_topic_partition_current_offset` hızı (`vectordb_milvus_metrics`) | = 0 | 10 dk | critical | VectorDB gateway Kafka'ya yazamıyor |
| 16 | TelemetryConsumerSendFailures | `otelcol_exporter_send_failed_*` artışı | > 0 / 10 dk | anında | warning | Prometheus/Loki/Tempo'ya iletim düşüyor |
| 17 | MilvusObjectStorageErrors | `internal_storage_op_count_total` fail / (success+fail) | ≥ 5 hata **ve** > %1 (5 dk) | 5 dk | warning | Object storage (GCS/Ceph) hataları; `milvus_object_storage` kontrolüyle aynı eşik |

Log topic'leri olay güdümlüdür (audit, access, health); sessiz kalmaları normal olduğu için 15. kural yalnızca sürekli akan metrik topic'ini izler.

## Alertmanager yönlendirmesi

Kaynak: `deploy/values/gke/kube-prometheus-stack.yaml` → `alertmanager.config`.

- **Route:** `team="vectordb"` → `sre-agent` rapor servisi (`send_resolved: true`). `Watchdog` → `sre-agent` (5 dk'da bir; rapor üretilmez, hat canlılığı metriği olur). `InfoInhibitor` ve diğer her şey → `null`.
- **Gruplama:** `group_by: [alertname, severity]`, `group_wait` 30 sn, `group_interval` 5 dk, `repeat_interval` 4 saat. Aynı alarmın farklı pod/PVC/topic örnekleri tek bildirimde gelir.
- **Inhibition:**

| Kaynak (aktifken) | Bastırılan | Eşit olması gereken etiketler |
| --- | --- | --- |
| MilvusQueryNodeMemoryNearQuota | MilvusQueryNodeMemoryHigh | namespace, pod |
| PVCUsageCritical | PVCUsageHigh | namespace, persistentvolumeclaim |
| KafkaTopicNoIncomingData | MilvusTenantTelemetryStale | — (gateway yazmıyorsa metrik yokluğu sonuçtur) |
| MilvusWriteDenied | MilvusQueryNodeMemoryHigh | — |
| severity=critical | aynı alertname'in warning/info'su | namespace, alertname (chart varsayılanı) |

Doğrulama: `make alert-webhook-test` (sentetik alarmlarla gruplama ve inhibition).
