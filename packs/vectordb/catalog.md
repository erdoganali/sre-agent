# VectorDB / Milvus arıza kataloğu

<!-- Bu dosya catalog.yaml'dan üretilir; elle değiştirme. `make catalog` -->

Milvus 2.6 (kurulu sürüm 2.6.25; katalog ilk 2.6.6 için yazıldı) ve VectorDB platformu için ne bozulabilir,
nasıl görünür, bugün kim yakalar. Üç katman: Milvus motoru, VectorDB platformu ve operatörler (tenant yaşam
döngüsü, sırlar, ağ politikası), ortam (GKE; hedef hedef ortamda OpenShift ve Ceph). Her maddenin etki alanı ayrı
yazılır: ortak anahtar ya da operatör arızası bütün tenant'ları birden vurur.

2.6'da Woodpecker WAL metadata'sını etcd'ye, verisini object storage'a yazar: etcd ve object storage artık yazma
yolunun üzerinde; ikisindeki sorun yazmayı doğrudan durdurur.

Eşikler `quotaAndLimits` varsayılanları. Metrik adları v2.6.6 kaynağından ve kümedeki envanterden doğrulandı;
2.6.25 için `make catalog-check` ile yeniden doğrulanmalı. `make catalog-check` her metriğin son 7 günde beyan
edilen yoldan gelip gelmediğine bakar: Milvus birçok metriği ilk istekte oluşturur ve pod yeniden başlayınca bu
seriler kaybolur.

Kaynaklar (2026-10-15 derlemesi): VectorDB belgeleri (`vectordb-platform-helm/docs`, `teslim/KURULUM.md`,
`milvus-tenant-operator` CHANGELOG ve commit geçmişi, `milvus-zilliz-operator/docs/arch`), Milvus 2.6 sürüm
notları ve GitHub issue'ları, GKE spot ve OpenShift belgeleri.

Sinyal yolları: **hatta var** = hedef ortamdaki telemetri yolundan geliyor ve canlıda görüldü; **tanımlı, henüz görülmedi** = ürünün kaynağında var, olay olmadan oluşmuyor; **platform metriği** = Kubernetes/altyapı metriği; **yolda yok** = hiçbir yoldan gelmiyor.

## Özet

| Arıza | Etki | Alarm | Kontrol | Senaryo | Panel | Kategori |
| --- | --- | --- | --- | --- | --- | --- |
| [Disk kotası nedeniyle yazma kesilmesi](#yazma-disk-kotasi) | tek tenant | `MilvusWriteDenied` | — | — | Reddedilen yazmalar | Database |
| [L0 delete birikimi](#yazma-l0-delete) | tek tenant | — | — | — | — | Database |
| [Querynode belleği nedeniyle yazma kesilmesi](#yazma-querynode-bellek) | tek tenant | `MilvusQueryNodeMemoryHigh`, `MilvusQueryNodeMemoryNearQuota`, `MilvusWriteDenied` | `milvus_querynode_memory_quota`, `memory_near_limit`, `golden_signals_baseline` | 06-milvus-memory | Querynode belleği / limit ve yazma kesme eşiği | Database |
| [Datanode/streamingnode belleği nedeniyle yazma kesilmesi](#yazma-streamingnode-bellek) | tek tenant | `MilvusWriteDenied` | `memory_near_limit` | — | Reddedilen yazmalar | Database |
| [Time tick gecikmesi](#yazma-time-tick) | tek tenant | — | — | — | — | Database |
| [etcd alan kotasının dolması](#etcd-alan-kotasi) | tek tenant | `PVCUsageHigh`, `PVCUsageCritical`, `VectorDBPodCrashLooping` | `milvus_etcd` | — | Meta işlem hataları | Database |
| [etcd'ye erişilemiyor](#etcd-erisilemez) | tek tenant | `MilvusEtcdQuorumLost`, `VectorDBTenantMassRestart`, `VectorDBPodRestarting` | `milvus_etcd`, `probe_timeouts`, `pod_restarts` | 12-etcd-quorum-loss | etcd pod'u | Database |
| [etcd diski yavaş (fsync), lease süresi doluyor](#etcd-yavas-disk) | tek tenant | `VectorDBPodRestarting` | `milvus_etcd`, `pod_restarts` | — | — | Database |
| [Collection yüklenemiyor](#yukleme-basarisiz) | tek tenant | `MilvusRequestErrorRateHigh` | `milvus_health`, `memory_near_limit` | — | Yüklü collection | Database |
| [Querynode eksik ya da salt okunur](#yukleme-querynode-eksik) | tek tenant | `VectorDBPodCrashLooping`, `PodEvicted` | `milvus_coordinators`, `pod_restarts`, `node_conditions` | node-loss (planlı) | Koordinatörün gördüğü düğümler | Infrastructure / System |
| [Compaction gecikmesi ve segment birikimi](#arkaplan-compaction) | tek tenant | — | `milvus_segment_backlog` | — | Bekleyen compaction | Database |
| [Index oluşturma gecikmesi ya da başarısızlığı](#arkaplan-index) | tek tenant | — | `golden_signals_baseline`, `pod_restarts`, `memory_near_limit` | — | — | Database |
| [Object storage hataları ve gecikmesi](#depolama-hata) | tek tenant | `MilvusObjectStorageErrors`, `MilvusRequestErrorRateHigh` | `milvus_object_storage` | 10-object-storage | Object storage işlemleri ve hatalar | Vendor/External Problem |
| [Object storage kimlik, CA ya da adresleme hatası](#depolama-kimlik-ca) | **bütün tenant'lar** | `MilvusObjectStorageErrors` | `milvus_object_storage` | — | — | Human Error |
| [1 GiB ephemeral sınırı ve access log emptyDir'i](#disk-ephemeral) | tek tenant | `EphemeralStorageNearLimit`, `EmptyDirNearSizeLimit`, `PodEvicted` | `ephemeral_storage_usage` | — | Ephemeral disk / 1 GiB | Capacity/Performance |
| [Querynode lokal diskinin dolması](#disk-querynode-lokal) | tek tenant | `PVCUsageHigh`, `PVCUsageCritical`, `EphemeralStorageNearLimit` | `pvc_usage`, `ephemeral_storage_usage` | — | PVC doluluğu | Capacity/Performance |
| [Bağlantı sayısının tükenmesi](#proxy-baglanti) | tek tenant | — | `milvus_connections` | milvus-connection-limit (planlı) | Proxy bağlantı sayısı | Database |
| [Arama gecikmesi ve zaman aşımı](#proxy-gecikme) | tek tenant | `MilvusSearchLatencyP99High` | `golden_signals_baseline`, `cpu_throttling`, `milvus_coordinators` | — | Arama p99 gecikmesi | Capacity/Performance |
| [İstek hataları (not loaded, timeout, diğer)](#proxy-hata) | tek tenant | `MilvusRequestErrorRateHigh` | `milvus_health`, `new_error_signatures` | — | — | Database |
| [Hız sınırı (rate limited)](#proxy-rate-limit) | tek tenant | `MilvusRequestErrorRateHigh` | — | — | — | Capacity/Performance |
| [WAL'a (woodpecker) yazılamıyor ya da yavaş](#wal-yazma) | tek tenant | — | — | — | — | Database |
| [Woodpecker durdu, etcd şişti, zincirleme çökme](#woodpecker-truncation-etcd) | tek tenant | — | — | — | — | Vendor/External Problem |
| [Mixcoord ya da düğümler çalışmıyor](#koordinator-dusuk) | tek tenant | `VectorDBPodCrashLooping`, `VectorDBPodRestarting`, `MilvusTenantTelemetryStale` | `milvus_coordinators`, `milvus_health`, `pod_restarts` | — | Querynode heartbeat yaşı | Database |
| [Geri yüklemeden sonra index yok](#geri-yukleme-index-yok) | tek tenant | — | — | — | — | Application/Service Bug |
| [Tenant yedeği başarısız](#yedek-basarisiz) | tek tenant | — | — | — | — | Infrastructure / System |
| [Geri yükleme (MilvusRestore) başarısız](#yedek-geri-yukleme) | tek tenant | — | — | — | — | Infrastructure / System |
| [VectorDB platform işi başarısız](#yedek-platform-isi) | tek tenant | — | — | — | — | Application/Service Bug |
| [Telemetri toplayıcısı kayıt düşürüyor ya da gönderemiyor](#hat-collector-drop) | **bütün tenant'lar** | `KafkaTopicNoIncomingData`, `MilvusTenantTelemetryStale` | — | — | — | Infrastructure / System |
| [Tenant'ın telemetrisi gelmiyor](#hat-tenant-verisi) | tek tenant | `MilvusTenantTelemetryStale`, `KafkaConsumerLagHigh`, `KafkaTopicNoIncomingData`, `TelemetryConsumerSendFailures` | `kafka_consumer_lag` | 11-kafka-lag | Veri yaşı | Infrastructure / System |
| [milvus-operator bağımlılıklar hazır olmadığı için reconcile etmiyor](#op-bagimlilik-hazir-degil) | tek tenant | — | `milvus_health`, `milvus_etcd` | — | — | Database |
| [CRD ile operatör sürümü uyumsuz](#op-crd-surum-uyumsuz) | **bütün tenant'lar** | — | — | — | — | Human Error |
| [milvus-operator çalışmıyor](#op-milvus-operator-calismiyor) | **bütün tenant'lar** | — | `pod_restarts` | — | — | Infrastructure / System |
| [Root parolası rotasyonu uygulanmadı ya da password-reset Job takıldı](#op-parola-rotasyonu) | **bütün tenant'lar** | — | `auth_failures` | — | — | Security |
| [Reconcile hata döngüsünde (yetki, eksik CRD, çakışma)](#op-reconcile-hatasi) | tek tenant | — | — | — | — | Infrastructure / System |
| [Tek tenant'ın bozuk durumu bütün reconcile'ı kilitliyor](#op-tek-tenant-hepsini-kilitler) | **bütün tenant'lar** | — | — | — | — | Application/Service Bug |
| [milvus-tenant-operator çalışmıyor (crash, OOM, leader yok)](#op-tenant-operator-calismiyor) | **bütün tenant'lar** | — | `pod_restarts` | — | — | Infrastructure / System |
| [metrics-agent veri toplayamıyor ya da tenant kotasını tüketiyor](#metrics-agent-sorunlari) | tek tenant | `MilvusTenantTelemetryStale` | — | — | — | Infrastructure / System |
| [Access-log sidecar'ının emptyDir'i doldu](#sidecar-emptydir-dolu) | tek tenant | `EmptyDirNearSizeLimit`, `EphemeralStorageNearLimit`, `PodEvicted` | `ephemeral_storage_usage` | — | — | Capacity/Performance |
| [Sidecar çöktü, proxy pod'u hazır değil](#sidecar-proxy-hazir-degil) | tek tenant | `VectorDBPodRestarting` | `pod_restarts` | — | — | Application/Service Bug |
| [NetworkPolicy Milvus ya da etcd erişimini kesiyor](#ag-politikasi) | tek tenant | — | `networkpolicy_changes` | — | — | Network |
| [milvus-bucket-key boş ya da yanlış adla verilmiş](#bucket-key-bos) | **bütün tenant'lar** | — | — | — | — | Human Error |
| [İmaj çekilemiyor (registry)](#imaj-cekilemiyor) | tek tenant | — | `pod_restarts` | — | — | Human Error |
| [Tenant'ın milvusConfig'i reddedildi](#milvusconfig-reddi) | tek tenant | — | — | — | — | Human Error |
| [Namespace milvus-operator'ın izleme listesinde değil](#operator-izleme-listesi) | tek tenant | — | — | — | — | Infrastructure / System |
| [milvus-operator yeniden başlıyor (tenant açma, helm upgrade, ArgoCD döngüsü)](#operator-yeniden-baslama) | **bütün tenant'lar** | — | — | — | — | Infrastructure / System |
| [Tenant Creating'de takılı](#tenant-olusturma-takili) | tek tenant | — | `pending_pods` | — | — | Infrastructure / System |
| [Tenant silinemiyor, kaynaklar yetim kalıyor](#tenant-silme-takili) | tek tenant | — | — | — | — | Infrastructure / System |

## Yazmanın engellenmesi (quotaAndLimits)

### <a id="yazma-disk-kotasi"></a>Disk kotası nedeniyle yazma kesilmesi

**Belirti:** Object storage'daki toplam veri kotayı aşarsa bütün DML istekleri reddedilir.

| Sinyal | Yol | Not |
| --- | --- | --- |
| `milvus_rootcoord_disk_quota` | hatta var | Kotaya sayılan kullanım |
| `milvus_datacoord_stored_binlog_size` | tanımlı, henüz görülmedi | Collection başına saklanan veri |
| `milvus_rootcoord_force_deny_writing_counter` | hatta var |  |

- **Eşik:** Koruma açık ama kota tanımsız (diskQuota -1): varsayılan kurulumda tetiklenmez. (quotaAndLimits.limitWriting.diskProtection)
- **Etki:** tek tenant
- **Alarm:** `MilvusWriteDenied`
- **Kontrol:** —
- **Senaryo:** —
- **Panel:** Reddedilen yazmalar
- **Kategori:** Database
- **Not:** Hedef ortamda tenant başına kota tanımlanırsa bu satır önem kazanır; eşik o zaman values'tan okunmalı.

### <a id="yazma-l0-delete"></a>L0 delete birikimi

**Belirti:** Çok sayıda delete sonrası L0 segmentlerde silme kayıtları birikir; aramalar yavaşlar, bellek büyür. Koruma açıksa yazma reddedilir.

| Sinyal | Yol | Not |
| --- | --- | --- |
| `milvus_datacoord_l0_delete_entries_num` | tanımlı, henüz görülmedi | L0'daki silme kaydı sayısı |
| `milvus_datacoord_store_level0_segment_size` | tanımlı, henüz görülmedi |  |
| `milvus_querynode_delete_buffer_row_num` | tanımlı, henüz görülmedi | Querynode silme tamponu |
| `milvus_querynode_delete_buffer_size` | tanımlı, henüz görülmedi |  |
| `milvus_wal_delete_rows` | hatta var | WAL'a yazılan silme satırları (akış hızı) |

- **Eşik:** Korumalar varsayılan olarak kapalı. Açılırsa L0: 30M / 50M satır; silme tamponu: 32768 / 65536 satır. (quotaAndLimits.limitWriting.{l0SegmentsRowCountProtection,deleteBufferRowCountProtection,deleteBufferSizeProtection})
- **Etki:** tek tenant
- **Alarm:** —
- **Kontrol:** —
- **Senaryo:** —
- **Panel:** —
- **Kategori:** Database
- **Not:** L0 birikimini compaction eritir; compaction gecikirse ikisi birlikte görülür (arkaplan-compaction).

### <a id="yazma-querynode-bellek"></a>Querynode belleği nedeniyle yazma kesilmesi

**Belirti:** Insert/upsert/delete istekleri "deny to write, reason: memory quota exhausted" hatasıyla reddedilir. Öncesinde bellek düşük su seviyesini geçince yazma hızı kısılır. Arama çalışmaya devam eder.

| Sinyal | Yol | Not |
| --- | --- | --- |
| `milvus_rootcoord_force_deny_writing_counter` | hatta var | Artışı = yazma reddedildi. MilvusWriteDenied bunu izler |
| `milvus_rootcoord_qn_mem_high_water_level` | hatta var | Yapılandırılmış yüksek su seviyesi (0.95); eşik sabit yazılmaz, buradan okunur |
| `container_memory_working_set_bytes` | platform metriği | querynode konteynerinin kullanımı; limite oranı alarmın temeli |
| `kube_pod_container_resource_limits` | platform metriği | querynode bellek limiti |
| `milvus_rootcoord_quota_states` | tanımlı, henüz görülmedi | Kota durumu (ör. MemoryQuotaExceeded); yalnızca bir kota devredeyken oluşur |
| `milvus_rootcoord_rate_limit_ratio` | tanımlı, henüz görülmedi | Düşük su seviyesinden sonra yazma hızının kısılma oranı |

- **Eşik:** Düşük su seviyesi 0.85 (hız kısılır), yüksek su seviyesi 0.95 (yazma reddedilir). Alarmlarımız 0.80 (warning, 5 dk) ve 0.90 (critical, 2 dk); kontrol 0.80'de anomali sayar. (quotaAndLimits.limitWriting.memProtection.queryNodeMemory{Low,High}WaterLevel)
- **Etki:** tek tenant
- **Alarm:** `MilvusQueryNodeMemoryHigh`, `MilvusQueryNodeMemoryNearQuota`, `MilvusWriteDenied`
- **Kontrol:** `milvus_querynode_memory_quota`, `memory_near_limit`, `golden_signals_baseline`
- **Senaryo:** 06-milvus-memory
- **Panel:** Querynode belleği / limit ve yazma kesme eşiği
- **Kategori:** Database
- **Not:** Milvus kendi bellek ölçümünü kullanır (işlemin kullandığı / konteyner limiti); konteyner metriğiyle birkaç puan fark olabilir. Querynode'lar arasında dengesiz yük tek bir node'u eşiğe taşıyabilir.
- **Kaynak:** [#33913](https://github.com/milvus-io/milvus/issues/33913) · [#25836](https://github.com/milvus-io/milvus/issues/25836)

### <a id="yazma-streamingnode-bellek"></a>Datanode/streamingnode belleği nedeniyle yazma kesilmesi

**Belirti:** Aynı "memory quota exhausted" hatası, bu kez yazma yolundaki düğümün (2.6'da streamingnode) belleği nedeniyle. Querynode belleği normal görünürken yazma reddedilir.

| Sinyal | Yol | Not |
| --- | --- | --- |
| `milvus_rootcoord_force_deny_writing_counter` | hatta var | Hangi düğümün tetiklediğini söylemez |
| `container_memory_working_set_bytes` | platform metriği | streamingnode ve datanode konteynerleri |
| `milvus_wal_growing_segment_hwm_bytes` | hatta var | Büyüyen segment belleği yüksek su seviyesi |
| `milvus_wal_growing_segment_lwm_bytes` | hatta var | Düşük su seviyesi |

- **Eşik:** 0.85 / 0.95 (quotaAndLimits.limitWriting.memProtection.dataNodeMemory{Low,High}WaterLevel)
- **Etki:** tek tenant
- **Alarm:** `MilvusWriteDenied`
- **Kontrol:** `memory_near_limit`
- **Senaryo:** —
- **Panel:** Reddedilen yazmalar
- **Kategori:** Database
- **Not:** Bellek alarmımız yalnızca querynode'a bakıyor; streamingnode ve datanode için karşılığı yok. MilvusWriteDenied sonucu yakalar ama nedeni ayırt etmez.
- **Kaynak:** [#50877](https://github.com/milvus-io/milvus/issues/50877)

### <a id="yazma-time-tick"></a>Time tick gecikmesi

**Belirti:** Aramalar en yeni veriyi görmez ya da tutarlılık beklerken zaman aşımına düşer. Koruma açıksa gecikme eşiği aşılınca yazma da reddedilir.

| Sinyal | Yol | Not |
| --- | --- | --- |
| `milvus_rootcoord_time_tick_delay` | tanımlı, henüz görülmedi | Düğüm başına time tick gecikmesi |
| `milvus_rootcoord_produce_tt_lag_ms` | tanımlı, henüz görülmedi |  |
| `milvus_wal_last_confirmed_time_tick` | hatta var | WAL'da onaylanan son time tick |
| `milvus_wal_last_allocated_time_tick` | hatta var | Ayrılan son time tick; ikisi arasındaki fark birikim |

- **Eşik:** Koruma varsayılan olarak kapalı (maxTimeTickDelay 1200 sn). (quotaAndLimits.limitWriting.ttProtection)
- **Etki:** tek tenant
- **Alarm:** —
- **Kontrol:** —
- **Senaryo:** —
- **Panel:** —
- **Kategori:** Database
- **Not:** Koruma kapalı olduğu için yazma kesilmez; belirti arama tarafında görülür.

## etcd (meta veri)

### <a id="etcd-alan-kotasi"></a>etcd alan kotasının dolması

**Belirti:** etcd "mvcc: database space exceeded" (NOSPACE) alarmına girer, meta veri yazılamaz. DDL ve yükleme işlemleri hata verir; koordinatörler yeniden başlarken açılamaz, CrashLoopBackOff.

| Sinyal | Yol | Not |
| --- | --- | --- |
| `etcd_mvcc_db_total_size_in_bytes` | **yolda yok** | etcd toplanmıyor |
| `etcd_server_quota_backend_bytes` | **yolda yok** | etcd toplanmıyor |
| `milvus_meta_op_count` | hatta var | status="fail" artışı: meta işlemleri başarısız |
| `milvus_meta_kv_size` | hatta var | Meta işlem boyutları |
| `kubelet_volume_stats_used_bytes` | platform metriği | etcd diskinin doluluğu (kota değil, disk) |

- **Eşik:** etcd varsayılan kotası 2 GiB (--quota-backend-bytes). (etcd başlatma parametresi (VectorDB şablonu))
- **Etki:** tek tenant
- **Alarm:** `PVCUsageHigh`, `PVCUsageCritical`, `VectorDBPodCrashLooping`
- **Kontrol:** `milvus_etcd`
- **Senaryo:** —
- **Panel:** Meta işlem hataları
- **Kategori:** Database
- **Not:** PVC alarmları diskin dolmasını görür, etcd kotasını görmez: kota diskten önce dolabilir. etcd metrikleri için öneri: tenant ajanının etcd'yi de toplaması (VectorDB'a bildirilecek).
- **Kaynak:** [#43582](https://github.com/milvus-io/milvus/issues/43582) · [#44614](https://github.com/milvus-io/milvus/issues/44614)

### <a id="etcd-erisilemez"></a>etcd'ye erişilemiyor

**Belirti:** etcd pod'ları hazır değil ya da lider seçilemiyor; Milvus meta işlemleri zaman aşımına düşer.

| Sinyal | Yol | Not |
| --- | --- | --- |
| `kube_pod_status_ready` | platform metriği | etcd pod'ları |
| `kube_pod_container_status_restarts_total` | platform metriği |  |
| `milvus_meta_op_count` | hatta var | status="fail" |
| `milvus_meta_request_latency` | hatta var |  |

- **Etki:** tek tenant
- **Alarm:** `MilvusEtcdQuorumLost`, `VectorDBTenantMassRestart`, `VectorDBPodRestarting`
- **Kontrol:** `milvus_etcd`, `probe_timeouts`, `pod_restarts`
- **Senaryo:** 12-etcd-quorum-loss
- **Panel:** etcd pod'u
- **Kategori:** Database

### <a id="etcd-yavas-disk"></a>etcd diski yavaş (fsync), lease süresi doluyor

**Belirti:** etcd'nin diski yavaşlarsa yazmalar gecikir, Milvus bileşenlerinin lease'leri dolar ve süreçler kendini kapatır (çıkış kodu 80, "the session is expired"). Restartlar etcd çalışırken de görülür.

| Sinyal | Yol | Not |
| --- | --- | --- |
| `etcd_disk_wal_fsync_duration_seconds` | **yolda yok** | etcd toplanmıyor |
| `etcd_server_has_leader` | **yolda yok** | etcd toplanmıyor |
| `milvus_meta_request_latency` | hatta var |  |
| `kube_pod_container_status_restarts_total` | platform metriği |  |

- **Etki:** tek tenant
- **Alarm:** `VectorDBPodRestarting`
- **Kontrol:** `milvus_etcd`, `pod_restarts`
- **Senaryo:** —
- **Panel:** —
- **Kategori:** Database
- **Not:** Lease kaybı log'u Milvus pod'larında; etcd metrikleri olmadan kök neden (disk) görülemez.
- **Kaynak:** [#49530](https://github.com/milvus-io/milvus/issues/49530) · [#45303](https://github.com/milvus-io/milvus/issues/45303)

## Collection yükleme ve sorgu düğümleri

### <a id="yukleme-basarisiz"></a>Collection yüklenemiyor

**Belirti:** Arama "collection not loaded" hatası döner ya da yükleme bitmez. Genellikle querynode belleği ya da diski yetmez, index dosyaları okunamaz.

| Sinyal | Yol | Not |
| --- | --- | --- |
| `milvus_querycoord_load_req_count` | tanımlı, henüz görülmedi | status="fail" |
| `milvus_querycoord_load_latency` | tanımlı, henüz görülmedi |  |
| `milvus_querycoord_task_num` | tanımlı, henüz görülmedi |  |
| `milvus_querycoord_collection_num` | hatta var | Yüklü collection sayısı |
| `{log_source="milvus_access"} | json | status != "Successful"` | log | Erişim logunda başarısız istekler ve hata kodu |

- **Etki:** tek tenant
- **Alarm:** `MilvusRequestErrorRateHigh`
- **Kontrol:** `milvus_health`, `memory_near_limit`
- **Senaryo:** —
- **Panel:** Yüklü collection
- **Kategori:** Database
- **Not:** Hata oranı alarmı sonucu görür; yükleme başarısızlığına özel bir alarm ya da kontrol yok.

### <a id="yukleme-querynode-eksik"></a>Querynode eksik ya da salt okunur

**Belirti:** Querynode sayısı beklenenden az; replikalar salt okunur düğümlerde kalır, aramalar yavaşlar ya da hata verir.

| Sinyal | Yol | Not |
| --- | --- | --- |
| `milvus_querycoord_querynode_num` | hatta var |  |
| `milvus_querycoord_replica_ro_node` | hatta var | Salt okunur düğümdeki replikalar |
| `kube_pod_status_ready` | platform metriği |  |

- **Etki:** tek tenant
- **Alarm:** `VectorDBPodCrashLooping`, `PodEvicted`
- **Kontrol:** `milvus_coordinators`, `pod_restarts`, `node_conditions`
- **Senaryo:** node-loss
- **Panel:** Koordinatörün gördüğü düğümler
- **Kategori:** Infrastructure / System

## Compaction ve index

### <a id="arkaplan-compaction"></a>Compaction gecikmesi ve segment birikimi

**Belirti:** Küçük ve silinmiş satırlı segmentler birikir; aramalar yavaşlar, bellek ve disk büyür, L0 birikir.

| Sinyal | Yol | Not |
| --- | --- | --- |
| `milvus_datacoord_compaction_task_num` | tanımlı, henüz görülmedi |  |
| `milvus_datacoord_segment_num` | tanımlı, henüz görülmedi | segment_state başına (Growing, Flushed …) |
| `milvus_datanode_compaction_latency` | tanımlı, henüz görülmedi |  |
| `milvus_datanode_compaction_latency_in_queue` | tanımlı, henüz görülmedi |  |

- **Etki:** tek tenant
- **Alarm:** —
- **Kontrol:** `milvus_segment_backlog`
- **Senaryo:** —
- **Panel:** Bekleyen compaction
- **Kategori:** Database

### <a id="arkaplan-index"></a>Index oluşturma gecikmesi ya da başarısızlığı

**Belirti:** Yeni veri index'lenmez; aramalar index'siz (brute-force) yolla yavaşlar. Index görevleri "unissued" ya da "failed" kalır.

| Sinyal | Yol | Not |
| --- | --- | --- |
| `milvus_datacoord_index_task_count` | tanımlı, henüz görülmedi | index_task_status: unissued, in-progress, failed |
| `milvus_indexnode_build_index_latency` | tanımlı, henüz görülmedi |  |
| `milvus_rootcoord_indexed_entity_num` | tanımlı, henüz görülmedi | entity_num ile karşılaştırılır |
| `milvus_rootcoord_entity_num` | tanımlı, henüz görülmedi |  |

- **Etki:** tek tenant
- **Alarm:** —
- **Kontrol:** `golden_signals_baseline`, `pod_restarts`, `memory_near_limit`
- **Senaryo:** —
- **Panel:** —
- **Kategori:** Database
- **Not:** Index'e özel kontrol ya da alarm yok; gecikme dolaylı olarak arama gecikmesinde görülür. Gözlendi (loadtest, 2026-10-08): compaction segmentleri ~1 GB'a birleştirince HNSW derlemesi datanode'u (limit 2 GiB) döngüsel olarak OOMKilled etti (9+ yeniden başlama), index bekleyen satırlar azalmadı. İz: datanode OOMKilled + pod_restarts; collection silinince durdu. Büyük HNSW collection'larında datanode belleği, segment boyutundan (dataCoord.segment.maxSize) büyük olmalı.

## Object storage

### <a id="depolama-hata"></a>Object storage hataları ve gecikmesi

**Belirti:** Segment yazma/okuma, index ve yükleme hata verir ya da yavaşlar. WAL (woodpecker) object storage'a yazamazsa yazma yolu da durur.

| Sinyal | Yol | Not |
| --- | --- | --- |
| `milvus_storage_op_count` | hatta var | status="fail" |
| `internal_storage_op_count` | hatta var | Çekirdek (C++) katmanın depolama işlemleri; kontrol bunu kullanır |
| `milvus_storage_request_latency` | tanımlı, henüz görülmedi |  |
| `woodpecker_server_object_storage_operations` | hatta var | WAL'ın object storage işlemleri |
| `woodpecker_server_object_storage_operation_latency` | hatta var |  |

- **Etki:** tek tenant
- **Alarm:** `MilvusObjectStorageErrors`, `MilvusRequestErrorRateHigh`
- **Kontrol:** `milvus_object_storage`
- **Senaryo:** 10-object-storage
- **Panel:** Object storage işlemleri ve hatalar
- **Kategori:** Vendor/External Problem
- **Not:** GKE'de GCS, hedef ortamda Ceph. MilvusObjectStorageErrors kontrolle aynı eşiği kullanır (≥5 hata ve %1).

### <a id="depolama-kimlik-ca"></a>Object storage kimlik, CA ya da adresleme hatası

**Belirti:** Anahtar yanlış ya da boşsa AccessDenied; kurum CA'sı yoksa x509; Ceph RGW'de virtual-host adresleme kullanılırsa SignatureDoesNotMatch (403); endpoint portsuz ve useSSL açıksa :80'e TLS. Bucket açılamaz, streamingnode ve querynode başlamaz ya da yazamaz.

| Sinyal | Yol | Not |
| --- | --- | --- |
| `milvus_storage_op_count` | tanımlı, henüz görülmedi | status="fail" |
| `MilvusTenant.status.message` | Kubernetes nesnesi | auto-create bucket failed: AccessDenied / x509 / timeout |

- **Etki:** **bütün tenant'lar**
- **Alarm:** `MilvusObjectStorageErrors`
- **Kontrol:** `milvus_object_storage`
- **Senaryo:** —
- **Panel:** —
- **Kategori:** Human Error
- **Not:** Anahtar ve CA bütün tenant'larda ortak: biri bozulunca hepsi etkilenir. Hedef ortamda Ceph path-style ve kurum CA'sı zorunlu.
- **Kaynak:** tenant-operator v0.37.1, v0.44.20 · [Ceph #37616](https://tracker.ceph.com/issues/37616)

## Lokal disk ve ephemeral

### <a id="disk-ephemeral"></a>1 GiB ephemeral sınırı ve access log emptyDir'i

**Belirti:** Pod'un geçici diski hedef ortamdaki 1 GiB sınırını ya da access log emptyDir'inin 384 MiB sınırını aşar; pod evict edilir, yeniden planlanır.

| Sinyal | Yol | Not |
| --- | --- | --- |
| `ephemeral_storage_pod_usage` | platform metriği |  |
| `ephemeral_storage_container_volume_limit_percentage` | platform metriği |  |
| `kube_pod_status_reason` | platform metriği | reason="Evicted" |

- **Eşik:** %80 (1 GiB ve emptyDir sizeLimit için) (deploy/helm/vectordb-alerts/values.yaml)
- **Etki:** tek tenant
- **Alarm:** `EphemeralStorageNearLimit`, `EmptyDirNearSizeLimit`, `PodEvicted`
- **Kontrol:** `ephemeral_storage_usage`
- **Senaryo:** —
- **Panel:** Ephemeral disk / 1 GiB
- **Kategori:** Capacity/Performance

### <a id="disk-querynode-lokal"></a>Querynode lokal diskinin dolması

**Belirti:** Disk tabanlı index ve önbellek için lokal disk dolar; yükleme başarısız olur, pod evict edilebilir.

| Sinyal | Yol | Not |
| --- | --- | --- |
| `milvus_querynode_disk_used_size` | hatta var |  |
| `milvus_querynode_disk_cache_evict_bytes` | tanımlı, henüz görülmedi |  |
| `kubelet_volume_stats_used_bytes` | platform metriği |  |
| `ephemeral_storage_pod_usage` | platform metriği |  |

- **Etki:** tek tenant
- **Alarm:** `PVCUsageHigh`, `PVCUsageCritical`, `EphemeralStorageNearLimit`
- **Kontrol:** `pvc_usage`, `ephemeral_storage_usage`
- **Senaryo:** —
- **Panel:** PVC doluluğu
- **Kategori:** Capacity/Performance

## Proxy: istek hataları, gecikme, bağlantı

### <a id="proxy-baglanti"></a>Bağlantı sayısının tükenmesi

**Belirti:** Yeni istemci bağlantıları reddedilir; mevcut istemciler çalışmaya devam eder.

| Sinyal | Yol | Not |
| --- | --- | --- |
| `milvus_proxy_connection_num` | **yolda yok** | 2.6.6'da yok; kontrol bunu sorguluyor |
| `milvus_proxy_user_rpc_count` | hatta var | Kullanıcı başına istek; bağlantı sayısının yerini tutmaz |

- **Etki:** tek tenant
- **Alarm:** —
- **Kontrol:** `milvus_connections`
- **Senaryo:** milvus-connection-limit
- **Panel:** Proxy bağlantı sayısı
- **Kategori:** Database
- **Not:** Bkz. bilinen sorunlar: kontrol çalışmıyor.

### <a id="proxy-gecikme"></a>Arama gecikmesi ve zaman aşımı

**Belirti:** Arama/sorgu p99 gecikmesi yükselir, istemciler zaman aşımına düşer.

| Sinyal | Yol | Not |
| --- | --- | --- |
| `milvus_proxy_req_latency` | hatta var | function_name: Search, HybridSearch, Query |
| `milvus_proxy_req_in_queue_latency` | hatta var | Proxy kuyruğunda bekleme |
| `milvus_proxy_queue_task_num` | hatta var |  |
| `milvus_querynode_read_task_ready_len` | hatta var | Querynode'da bekleyen okuma görevleri |
| `milvus_querynode_sq_segment_latency` | tanımlı, henüz görülmedi |  |

- **Eşik:** p99 > 1000 ms, 10 dk (deploy/helm/vectordb-alerts/values.yaml)
- **Etki:** tek tenant
- **Alarm:** `MilvusSearchLatencyP99High`
- **Kontrol:** `golden_signals_baseline`, `cpu_throttling`, `milvus_coordinators`
- **Senaryo:** —
- **Panel:** Arama p99 gecikmesi
- **Kategori:** Capacity/Performance

### <a id="proxy-hata"></a>İstek hataları (not loaded, timeout, diğer)

**Belirti:** Proxy isteklerinin bir kısmı hata döner; erişim logunda hata kodu ve mesajı görülür.

| Sinyal | Yol | Not |
| --- | --- | --- |
| `milvus_proxy_req_count` | hatta var | status="fail" / status="total", function_name başına |
| `{log_source="milvus_access"} | json | status != "Successful"` | log | error_code ile nedeni ayırt edilir |

- **Eşik:** Hata oranı > %5, 10 dk (deploy/helm/vectordb-alerts/values.yaml)
- **Etki:** tek tenant
- **Alarm:** `MilvusRequestErrorRateHigh`
- **Kontrol:** `milvus_health`, `new_error_signatures`
- **Senaryo:** —
- **Panel:** —
- **Kategori:** Database
- **Not:** Hata kodlarına göre ayrım (not loaded / timeout / rate limit) bugün yalnızca erişim logundan yapılabilir.

### <a id="proxy-rate-limit"></a>Hız sınırı (rate limited)

**Belirti:** İstekler "rate limit exceeded" hatasıyla reddedilir.

| Sinyal | Yol | Not |
| --- | --- | --- |
| `milvus_proxy_rate_limit_req_count` | hatta var | status="fail" = sınıra takılan |
| `{log_source="milvus_access"} | json | error_code != "0"` | log |  |

- **Eşik:** DML/DQL/DDL hız sınırları varsayılan olarak kapalı; açık olan yalnızca collection başına flush (0.1/sn). (quotaAndLimits.{dml,dql,ddl,flushRate})
- **Etki:** tek tenant
- **Alarm:** `MilvusRequestErrorRateHigh`
- **Kontrol:** —
- **Senaryo:** —
- **Panel:** —
- **Kategori:** Capacity/Performance
- **Not:** Hız sınırına özel kontrol yok.

## Streaming ve WAL (2.6)

### <a id="wal-yazma"></a>WAL'a (woodpecker) yazılamıyor ya da yavaş

**Belirti:** 2.6'da yazma yolu streamingnode ve WAL üzerinden geçer. WAL yazması yavaşlarsa ya da tekrar denerse insert gecikir, zaman aşımına düşer.

| Sinyal | Yol | Not |
| --- | --- | --- |
| `milvus_wal_append_message_duration_seconds` | hatta var |  |
| `milvus_wal_impls_append_message_retry` | hatta var | Tekrar denemeler |
| `milvus_wal_recovery_inconsistent_event` | hatta var | Kurtarmada tutarsızlık |
| `woodpecker_client_append_latency` | hatta var |  |

- **Etki:** tek tenant
- **Alarm:** —
- **Kontrol:** —
- **Senaryo:** —
- **Panel:** —
- **Kategori:** Database
- **Not:** 2.6 mimarisine özgü, yeni bir alan; ne alarm ne kontrol var. Eşikler için veri gerekiyor.

### <a id="woodpecker-truncation-etcd"></a>Woodpecker durdu, etcd şişti, zincirleme çökme

**Belirti:** Object storage bir segment yazımını onaylayıp kalıcı yapmazsa (kayıp yazma) Woodpecker'ın kırpma işi durur; tüketim noktaları donar, etcd'de anahtar birikir ("apply request took too long"), bileşenler lease kaybedip crash-loop'a girer. Önce bir kanalın checkpoint'i donar, sonra bütün tenant çöker.

| Sinyal | Yol | Not |
| --- | --- | --- |
| `milvus_wal_recovery_inconsistent_event` | hatta var |  |
| `woodpecker_client_append_latency` | hatta var |  |
| `kube_pod_container_status_restarts_total` | platform metriği | bütün bileşenlerde artış |
| `etcd_mvcc_db_total_size_in_bytes` | **yolda yok** | etcd toplanmıyor |

- **Etki:** tek tenant
- **Alarm:** —
- **Kontrol:** —
- **Senaryo:** —
- **Panel:** —
- **Kategori:** Vendor/External Problem
- **Not:** Kök neden object storage'ın dayanıklılığı (Ceph min_size); hedef ortamın Ceph ortamı için doğrudan geçerli. Belirti veritabanı gibi görünür, başlatan olay depolamadır.
- **Kaynak:** [#54049](https://github.com/milvus-io/milvus/issues/54049) · [#48730](https://github.com/milvus-io/milvus/issues/48730)

## Koordinatörler

### <a id="koordinator-dusuk"></a>Mixcoord ya da düğümler çalışmıyor

**Belirti:** Koordinatör düşerse yeni yükleme, DDL ve zaman damgası üretimi durur; tenant'ın telemetrisi de kesilir.

| Sinyal | Yol | Not |
| --- | --- | --- |
| `milvus_num_node` | hatta var | Rol başına düğüm sayısı |
| `milvus_querycoord_last_heartbeat_timestamp` | hatta var |  |
| `milvus_rootcoord_timestamp` | hatta var | İlerlemiyorsa koordinatör takılmış |
| `kube_pod_container_status_waiting_reason` | platform metriği | reason="CrashLoopBackOff" |

- **Etki:** tek tenant
- **Alarm:** `VectorDBPodCrashLooping`, `VectorDBPodRestarting`, `MilvusTenantTelemetryStale`
- **Kontrol:** `milvus_coordinators`, `milvus_health`, `pod_restarts`
- **Senaryo:** —
- **Panel:** Querynode heartbeat yaşı
- **Kategori:** Database

## Yedek, geri yükleme ve platform işleri

### <a id="geri-yukleme-index-yok"></a>Geri yüklemeden sonra index yok

**Belirti:** Restore index'leri geri getirmez; collection yüklenirken "index not found" hatası alınır.

| Sinyal | Yol | Not |
| --- | --- | --- |
| `MilvusRestore.status` | Kubernetes nesnesi |  |
| `milvus_querycoord_load_req_count` | tanımlı, henüz görülmedi | status="fail" |

- **Etki:** tek tenant
- **Alarm:** —
- **Kontrol:** —
- **Senaryo:** —
- **Panel:** —
- **Kategori:** Application/Service Bug
- **Kaynak:** 06-ACIK-ISLER.md D9

### <a id="yedek-basarisiz"></a>Tenant yedeği başarısız

**Belirti:** Gece yedeği (tenant başına CronJob, 02:00) başarısız biter ya da MilvusBackup "Failed" olur; felakette dönülecek yedek yoktur.

| Sinyal | Yol | Not |
| --- | --- | --- |
| `kube_job_status_failed` | platform metriği | job_name=~"cus-.*-milvus-backup-.*" |
| `kube_cronjob_status_last_successful_time` | platform metriği | Son başarılı yedeğin yaşı |
| `MilvusBackup.status.phase` | Kubernetes nesnesi | sre-agent'ın okuma yetkisinde değil |

- **Etki:** tek tenant
- **Alarm:** —
- **Kontrol:** —
- **Senaryo:** —
- **Panel:** —
- **Kategori:** Infrastructure / System
- **Not:** Hiç alarm ya da kontrol yok. Genel KubeJobFailed alarmı sre-agent'a gitmiyor.

### <a id="yedek-geri-yukleme"></a>Geri yükleme (MilvusRestore) başarısız

**Belirti:** Geri yükleme "Failed" olur ya da bitmez; hedef tenant eksik veriyle kalır.

| Sinyal | Yol | Not |
| --- | --- | --- |
| `MilvusRestore.status.phase` | Kubernetes nesnesi | sre-agent'ın okuma yetkisinde değil |
| `kube_job_status_failed` | platform metriği |  |

- **Etki:** tek tenant
- **Alarm:** —
- **Kontrol:** —
- **Senaryo:** —
- **Panel:** —
- **Kategori:** Infrastructure / System
- **Not:** Elle başlatılan bir işlem; alarmdan çok işlem sonucunun raporlanması gerekir.

### <a id="yedek-platform-isi"></a>VectorDB platform işi başarısız

**Belirti:** VectorDB'ın tenant işlemleri için açtığı Job'lar (ör. parola sıfırlama) başarısız kalır. Katalog yazılırken `vectordb` namespace'inde böyle bir Job vardı.

| Sinyal | Yol | Not |
| --- | --- | --- |
| `kube_job_status_failed` | platform metriği |  |

- **Etki:** tek tenant
- **Alarm:** —
- **Kontrol:** —
- **Senaryo:** —
- **Panel:** —
- **Kategori:** Application/Service Bug
- **Not:** Bizim kurallarımız Job başarısızlığını izlemiyor.

## Telemetri hattı

### <a id="hat-collector-drop"></a>Telemetri toplayıcısı kayıt düşürüyor ya da gönderemiyor

**Belirti:** Toplayıcı tek nokta; kuyruk dolunca kayıt düşer (retry yok). Gateway'de TLS açılır da ajanlar düz göndermeye devam ederse veri sessizce kesilir. Milvus etkilenmez ama alarmlar kör kalır.

| Sinyal | Yol | Not |
| --- | --- | --- |
| `otelcol_exporter_send_failed_log_records` | platform metriği |  |
| `otelcol_exporter_queue_size` | platform metriği |  |

- **Etki:** **bütün tenant'lar**
- **Alarm:** `KafkaTopicNoIncomingData`, `MilvusTenantTelemetryStale`
- **Kontrol:** —
- **Senaryo:** —
- **Panel:** —
- **Kategori:** Infrastructure / System
- **Kaynak:** 06-ACIK-ISLER.md B6 · ADR-0023 (VectorDB)

### <a id="hat-tenant-verisi"></a>Tenant'ın telemetrisi gelmiyor

**Belirti:** Tenant'ın Milvus metrikleri Prometheus'a ulaşmıyor; alarmlar sessizleşir, raporlar kör noktalı olur.

| Sinyal | Yol | Not |
| --- | --- | --- |
| `milvus_rootcoord_timestamp` | hatta var | Son örneğin yaşı = tazelik |
| `kafka_consumergroup_lag` | platform metriği |  |

- **Eşik:** Son veri > 10 dk (deploy/helm/vectordb-alerts/values.yaml, config/gke.yaml (freshness))
- **Etki:** tek tenant
- **Alarm:** `MilvusTenantTelemetryStale`, `KafkaConsumerLagHigh`, `KafkaTopicNoIncomingData`, `TelemetryConsumerSendFailures`
- **Kontrol:** `kafka_consumer_lag`
- **Senaryo:** 11-kafka-lag
- **Panel:** Veri yaşı
- **Kategori:** Infrastructure / System
- **Not:** Kitap Bölüm 3.9. `make pipeline-demo` bunun provası.

## Operatörler (tenant-operator, milvus-operator)

### <a id="op-bagimlilik-hazir-degil"></a>milvus-operator bağımlılıklar hazır olmadığı için reconcile etmiyor

**Belirti:** milvus-operator önce etcd'yi (2379) ve depolamayı sınar; hazır değillerse Milvus bileşenlerini hiç reconcile etmez. Milvus CR Unhealthy ya da Pending'de kalır, pod'lar oluşmaz ya da güncellenmez.

| Sinyal | Yol | Not |
| --- | --- | --- |
| `Milvus.status.conditions` | Kubernetes nesnesi | EtcdReady / StorageReady False |

- **Etki:** tek tenant
- **Alarm:** —
- **Kontrol:** `milvus_health`, `milvus_etcd`
- **Senaryo:** —
- **Panel:** —
- **Kategori:** Database
- **Kaynak:** milvus-zilliz-operator/docs/arch/reconcile.md

### <a id="op-crd-surum-uyumsuz"></a>CRD ile operatör sürümü uyumsuz

**Belirti:** Operatör yeni bir alan bekler ama CRD eskiyse alan sessizce düşer, ayar uygulanmaz ve hata çıkmaz. Hedef ortamda CRD elle uygulanmazsa operatör "no matches for kind" ile yeniden başlar.

| Sinyal | Yol | Not |
| --- | --- | --- |
| `CustomResourceDefinition.spec.versions` | Kubernetes nesnesi |  |
| `kube_pod_container_status_restarts_total` | platform metriği | operatör |

- **Etki:** **bütün tenant'lar**
- **Alarm:** —
- **Kontrol:** —
- **Senaryo:** —
- **Panel:** —
- **Kategori:** Human Error
- **Kaynak:** teslim/KURULUM.md §7–8

### <a id="op-milvus-operator-calismiyor"></a>milvus-operator çalışmıyor

**Belirti:** Milvus bileşenlerinin kendini onarması (silinen Deployment'ın geri gelmesi, yükseltme, ölçek değişikliği) durur; tenant-operator'ın yazdığı Milvus CR'ları uygulanmaz. Çalışan pod'lar ayakta kalır.

| Sinyal | Yol | Not |
| --- | --- | --- |
| `kube_deployment_status_replicas_available` | platform metriği | milvus-operator |
| `kube_pod_container_status_restarts_total` | platform metriği | milvus-operator |
| `Milvus.status.conditions` | Kubernetes nesnesi |  |

- **Etki:** **bütün tenant'lar**
- **Alarm:** —
- **Kontrol:** `pod_restarts`
- **Senaryo:** —
- **Panel:** —
- **Kategori:** Infrastructure / System

### <a id="op-parola-rotasyonu"></a>Root parolası rotasyonu uygulanmadı ya da password-reset Job takıldı

**Belirti:** Yeni parola tenant reconcile edilene kadar uygulanmaz; password-reset Job proxy'ye ulaşamazsa takılır. Eski Job kalırsa yeni tenant varsayılan parolayla açık kalabiliyordu. milvusadmin yoksa backend "auth check failure" alır.

| Sinyal | Yol | Not |
| --- | --- | --- |
| `kube_job_status_failed` | platform metriği | password-reset |
| `MilvusTenant.status.message` | Kubernetes nesnesi |  |

- **Etki:** **bütün tenant'lar**
- **Alarm:** —
- **Kontrol:** `auth_failures`
- **Senaryo:** —
- **Panel:** —
- **Kategori:** Security
- **Kaynak:** 06-ACIK-ISLER.md B3, D10 · tenant-operator 7ea3d3e

### <a id="op-reconcile-hatasi"></a>Reconcile hata döngüsünde (yetki, eksik CRD, çakışma)

**Belirti:** Operatör aynı tenant'ı tekrar tekrar dener ve başaramaz: eksik yetki (ör. deletecollection), CRD yok ("no matches for kind"), çakışan güncelleme. Tenant status.message'da hata, operatör logunda tekrar eden satır.

| Sinyal | Yol | Not |
| --- | --- | --- |
| `MilvusTenant.status.message` | Kubernetes nesnesi |  |
| `MilvusTenant.status.phase` | Kubernetes nesnesi |  |
| `controller_runtime_reconcile_errors_total` | **yolda yok** | toplanmıyor |

- **Etki:** tek tenant
- **Alarm:** —
- **Kontrol:** —
- **Senaryo:** —
- **Panel:** —
- **Kategori:** Infrastructure / System
- **Kaynak:** tenant-operator v0.30.2 · teslim/KURULUM.md §8

### <a id="op-tek-tenant-hepsini-kilitler"></a>Tek tenant'ın bozuk durumu bütün reconcile'ı kilitliyor

**Belirti:** Bir tenant'ta beklenmeyen durum (cus-sevim olayında etcd persistence nil) operatörün koruma kontrolüne takılır ve operatör bütün tenant'ların reconcile'ını durdurur.

| Sinyal | Yol | Not |
| --- | --- | --- |
| `MilvusTenant.status.message` | Kubernetes nesnesi |  |
| `kube_pod_container_status_restarts_total` | platform metriği | operatör |

- **Etki:** **bütün tenant'lar**
- **Alarm:** —
- **Kontrol:** —
- **Senaryo:** —
- **Panel:** —
- **Kategori:** Application/Service Bug
- **Not:** tenant-operator v0.30.3 ve milvus-operator fork rc2 ile kapatıldı; sınıfı (tek tenant → hepsi) hâlâ risk.
- **Kaynak:** tenant-operator v0.30.3 · milvus-zilliz-operator CHANGELOG (rc2)

### <a id="op-tenant-operator-calismiyor"></a>milvus-tenant-operator çalışmıyor (crash, OOM, leader yok)

**Belirti:** Çalışan Milvus'lar etkilenmez ama hiçbir tenant işi ilerlemez: yeni tenant açılmaz, silinmez, parola rotasyonu ve yedek CronJob'ları güncellenmez, izleme listesi güncellenmez, sırlar aynalanmaz. Sorun ancak bir değişiklik istendiğinde fark edilir.

| Sinyal | Yol | Not |
| --- | --- | --- |
| `kube_deployment_status_replicas_available` | platform metriği | milvus-tenant-operator |
| `kube_pod_container_status_restarts_total` | platform metriği | milvus-tenant-operator |
| `controller_runtime_reconcile_errors_total` | **yolda yok** | operatör metrikleri toplanmıyor |

- **Etki:** **bütün tenant'lar**
- **Alarm:** —
- **Kontrol:** `pod_restarts`
- **Senaryo:** —
- **Panel:** —
- **Kategori:** Infrastructure / System
- **Not:** Operatör logları ve metrikleri bugün hiçbir yoldan toplanmıyor: ajan operatörü yalnız pod durumundan görebilir.

## Proxy pod'u ve tenant ajanları (sidecar, metrics-agent)

### <a id="metrics-agent-sorunlari"></a>metrics-agent veri toplayamıyor ya da tenant kotasını tüketiyor

**Belirti:** Ajanın kendi Role'ü eksikse ya da hedefi kaçırırsa (metricsEnabled sonradan açılınca bir kez) metrik gelmez; alarmlar kör kalır. Ajan ve sidecar tenant ResourceQuota'sından yer: kota dolarsa Milvus pod'u oluşturulamaz ("exceeded quota"). Gateway'e düz OTLP gönderir; gateway TLS'e geçerse veri sessizce kesilir.

| Sinyal | Yol | Not |
| --- | --- | --- |
| `kube_resourcequota` | platform metriği | used / hard |
| `up` | platform metriği |  |

- **Etki:** tek tenant
- **Alarm:** `MilvusTenantTelemetryStale`
- **Kontrol:** —
- **Senaryo:** —
- **Panel:** —
- **Kategori:** Infrastructure / System
- **Not:** Ajan bütün Milvus pod'larını okur ve gateway'e bağlıdır (topology'de düzeltildi).
- **Kaynak:** 06-ACIK-ISLER.md B4, B6

### <a id="sidecar-emptydir-dolu"></a>Access-log sidecar'ının emptyDir'i doldu

**Belirti:** Proxy pod'undaki access-log sidecar'ı logları 384Mi'lik emptyDir'e yazar; dolarsa pod ephemeral sınırını aşar ve evict edilir. Proxy giderse tenant'ın bütün istemci istekleri düşer: yardımcı bir bileşen veri yolunu keser.

| Sinyal | Yol | Not |
| --- | --- | --- |
| `kubelet_volume_stats_used_bytes` | platform metriği | emptyDir |
| `kube_pod_status_reason` | platform metriği | Evicted |

- **Etki:** tek tenant
- **Alarm:** `EmptyDirNearSizeLimit`, `EphemeralStorageNearLimit`, `PodEvicted`
- **Kontrol:** `ephemeral_storage_usage`
- **Senaryo:** —
- **Panel:** —
- **Kategori:** Capacity/Performance
- **Not:** Hedef ortamda pod başına 1 GiB ephemeral; querynode önbelleği ve sidecar aynı bütçeden yer (ölçülmedi).

### <a id="sidecar-proxy-hazir-degil"></a>Sidecar çöktü, proxy pod'u hazır değil

**Belirti:** Sidecar container'ı çöker ya da yeniden başlarsa pod Ready olmaz; Service proxy'yi uç noktalardan çıkarır. Proxy sağlıklı olsa da istemciler bağlanamaz.

| Sinyal | Yol | Not |
| --- | --- | --- |
| `kube_pod_container_status_restarts_total` | platform metriği | container=sidecar |
| `kube_pod_status_ready` | platform metriği | proxy pod'u |
| `milvus_proxy_req_count` | hatta var | istek düşüşü |

- **Etki:** tek tenant
- **Alarm:** `VectorDBPodRestarting`
- **Kontrol:** `pod_restarts`
- **Senaryo:** —
- **Panel:** —
- **Kategori:** Application/Service Bug

## Platform ve tenant yaşam döngüsü

### <a id="ag-politikasi"></a>NetworkPolicy Milvus ya da etcd erişimini kesiyor

**Belirti:** Tenant politikası proxy ve etcd pod'larını izole eder; yalnız backend, password-reset, milvus-operator ve namespace içi trafik açıktır. 2379 izni eksikse operatör etcd'ye ulaşamaz ve Milvus pod'ları kurulmaz; başka namespace'teki istemciler 19530'a ulaşamaz. Etiketler tutmazsa kural beklenen pod'u seçmez.

| Sinyal | Yol | Not |
| --- | --- | --- |
| `NetworkPolicy.spec` | Kubernetes nesnesi |  |
| `milvus_proxy_req_count` | hatta var | istek gelmiyor |

- **Etki:** tek tenant
- **Alarm:** —
- **Kontrol:** `networkpolicy_changes`
- **Senaryo:** —
- **Panel:** —
- **Kategori:** Network
- **Kaynak:** teslim/cluster-objeleri/tenant/networkpolicy-tenant.yaml · teslim/KURULUM.md §1.6

### <a id="bucket-key-bos"></a>milvus-bucket-key boş ya da yanlış adla verilmiş

**Belirti:** Ortak depolama anahtarı boşsa aynalama reddedilir ve tenant Creating'de kalır; secretRef farklı adla verilirse kopya hiç oluşmaz ve hiçbir yer hata vermez. 0.44.12 öncesinde boş değer kopyanın üstüne yazılıyordu: tenant Running görünürken Milvus depolamaya yazamıyordu.

| Sinyal | Yol | Not |
| --- | --- | --- |
| `MilvusTenant.status.phase` | Kubernetes nesnesi | Creating'de takılı |
| `MilvusTenant.status.message` | Kubernetes nesnesi |  |

- **Etki:** **bütün tenant'lar**
- **Alarm:** —
- **Kontrol:** —
- **Senaryo:** —
- **Panel:** —
- **Kategori:** Human Error
- **Kaynak:** tenant-operator v0.44.12 · teslim/KURULUM.md §7

### <a id="imaj-cekilemiyor"></a>İmaj çekilemiyor (registry)

**Belirti:** Registry boş verilirse chart docker.io önekini ekler; air-gap ortamda ya da yetki yoksa ImagePullBackOff.

| Sinyal | Yol | Not |
| --- | --- | --- |
| `kube_pod_container_status_waiting_reason` | platform metriği | reason="ImagePullBackOff" |

- **Etki:** tek tenant
- **Alarm:** —
- **Kontrol:** `pod_restarts`
- **Senaryo:** —
- **Panel:** —
- **Kategori:** Human Error
- **Kaynak:** milvus-tenant-operator/docs/TROUBLESHOOTING-IMAGE-REGISTRY.md

### <a id="milvusconfig-reddi"></a>Tenant'ın milvusConfig'i reddedildi

**Belirti:** Kara listedeki ya da bozuk bir ayar reddedilir; tenant Running kalır, ayar uygulanmaz, sebep yalnız status.message'da görünür.

| Sinyal | Yol | Not |
| --- | --- | --- |
| `MilvusTenant.status.message` | Kubernetes nesnesi |  |

- **Etki:** tek tenant
- **Alarm:** —
- **Kontrol:** —
- **Senaryo:** —
- **Panel:** —
- **Kategori:** Human Error
- **Kaynak:** tenant-operator v0.44.16–18

### <a id="operator-izleme-listesi"></a>Namespace milvus-operator'ın izleme listesinde değil

**Belirti:** Milvus CR yazılır ama pod gelmez; status boş, log sessiz. Tenant Running görünebilir. Sebep: namespace --watch-namespace listesinde değil ya da milvus-operator-manager RoleBinding'i yok.

| Sinyal | Yol | Not |
| --- | --- | --- |
| `Milvus.status.status` | Kubernetes nesnesi | boş |
| `MilvusTenant.status.phase` | Kubernetes nesnesi |  |
| `kube_pod_info` | platform metriği | tenant namespace'inde Milvus pod'u yok |

- **Etki:** tek tenant
- **Alarm:** —
- **Kontrol:** —
- **Senaryo:** —
- **Panel:** —
- **Kategori:** Infrastructure / System
- **Not:** Tenant fazı Milvus sağlığını göstermez; gerçek sağlık Milvus CR'ında ve pod'larda.
- **Kaynak:** vectordb-platform-helm/docs/04-TENANT-YASAM-DONGUSU.md (K1)

### <a id="operator-yeniden-baslama"></a>milvus-operator yeniden başlıyor (tenant açma, helm upgrade, ArgoCD döngüsü)

**Belirti:** Her yeni tenant ve her helm upgrade'de milvus-operator 1–2 kez yeniden başlar; ~75 sn boyunca hiçbir tenant reconcile edilmez. ArgoCD selfHeal ile ignoreDifferences eksikse sonsuz döngüye girer.

| Sinyal | Yol | Not |
| --- | --- | --- |
| `kube_pod_container_status_restarts_total` | platform metriği | milvus-operator |
| `kube_deployment_status_observed_generation` | platform metriği | generation artışı |

- **Etki:** **bütün tenant'lar**
- **Alarm:** —
- **Kontrol:** —
- **Senaryo:** —
- **Panel:** —
- **Kategori:** Infrastructure / System
- **Kaynak:** vectordb-platform-helm/docs/01-ISLETME.md §4

### <a id="tenant-olusturma-takili"></a>Tenant Creating'de takılı

**Belirti:** Sıra bozulursa (metrics ConfigMap yok) proxy init'te bekler, password-reset Job bitmez ("Waiting for password reset"); varsayılan StorageClass yoksa etcd PVC Pending kalır; bucket açılamazsa "auto-create bucket failed".

| Sinyal | Yol | Not |
| --- | --- | --- |
| `MilvusTenant.status.message` | Kubernetes nesnesi |  |
| `kube_persistentvolumeclaim_status_phase` | platform metriği | phase="Pending" |
| `kube_job_status_failed` | platform metriği | password-reset Job |

- **Etki:** tek tenant
- **Alarm:** —
- **Kontrol:** `pending_pods`
- **Senaryo:** —
- **Panel:** —
- **Kategori:** Infrastructure / System
- **Kaynak:** 04-TENANT-YASAM-DONGUSU.md (K2) · tenant-operator v0.44.1

### <a id="tenant-silme-takili"></a>Tenant silinemiyor, kaynaklar yetim kalıyor

**Belirti:** Silme sırası bozulursa Milvus CR Deleting'de, tenant Terminating'de takılır; etcd StatefulSet ve PVC'ler ya da bucket'taki veri yetim kalır ("bucket is not empty").

| Sinyal | Yol | Not |
| --- | --- | --- |
| `MilvusTenant.metadata.deletionTimestamp` | Kubernetes nesnesi |  |
| `Milvus.status.status` | Kubernetes nesnesi | Deleting |

- **Etki:** tek tenant
- **Alarm:** —
- **Kontrol:** —
- **Senaryo:** —
- **Panel:** —
- **Kategori:** Infrastructure / System
- **Kaynak:** 04-TENANT-YASAM-DONGUSU.md (S1, S3) · tenant-operator v0.44.7

## Eksikler

Bu bölüm otomatik üretilir: bağlantısı olmayan satırlar.

- **Alarmı olmayan (26):** [L0 delete birikimi](#yazma-l0-delete), [Time tick gecikmesi](#yazma-time-tick), [Compaction gecikmesi ve segment birikimi](#arkaplan-compaction), [Index oluşturma gecikmesi ya da başarısızlığı](#arkaplan-index), [Bağlantı sayısının tükenmesi](#proxy-baglanti), [WAL'a (woodpecker) yazılamıyor ya da yavaş](#wal-yazma), [Woodpecker durdu, etcd şişti, zincirleme çökme](#woodpecker-truncation-etcd), [Geri yüklemeden sonra index yok](#geri-yukleme-index-yok), [Tenant yedeği başarısız](#yedek-basarisiz), [Geri yükleme (MilvusRestore) başarısız](#yedek-geri-yukleme), [VectorDB platform işi başarısız](#yedek-platform-isi), [milvus-operator bağımlılıklar hazır olmadığı için reconcile etmiyor](#op-bagimlilik-hazir-degil), [CRD ile operatör sürümü uyumsuz](#op-crd-surum-uyumsuz), [milvus-operator çalışmıyor](#op-milvus-operator-calismiyor), [Root parolası rotasyonu uygulanmadı ya da password-reset Job takıldı](#op-parola-rotasyonu), [Reconcile hata döngüsünde (yetki, eksik CRD, çakışma)](#op-reconcile-hatasi), [Tek tenant'ın bozuk durumu bütün reconcile'ı kilitliyor](#op-tek-tenant-hepsini-kilitler), [milvus-tenant-operator çalışmıyor (crash, OOM, leader yok)](#op-tenant-operator-calismiyor), [NetworkPolicy Milvus ya da etcd erişimini kesiyor](#ag-politikasi), [milvus-bucket-key boş ya da yanlış adla verilmiş](#bucket-key-bos), [İmaj çekilemiyor (registry)](#imaj-cekilemiyor), [Tenant'ın milvusConfig'i reddedildi](#milvusconfig-reddi), [Namespace milvus-operator'ın izleme listesinde değil](#operator-izleme-listesi), [milvus-operator yeniden başlıyor (tenant açma, helm upgrade, ArgoCD döngüsü)](#operator-yeniden-baslama), [Tenant Creating'de takılı](#tenant-olusturma-takili), [Tenant silinemiyor, kaynaklar yetim kalıyor](#tenant-silme-takili)
- **Kontrolü olmayan (20):** [Disk kotası nedeniyle yazma kesilmesi](#yazma-disk-kotasi), [L0 delete birikimi](#yazma-l0-delete), [Time tick gecikmesi](#yazma-time-tick), [Hız sınırı (rate limited)](#proxy-rate-limit), [WAL'a (woodpecker) yazılamıyor ya da yavaş](#wal-yazma), [Woodpecker durdu, etcd şişti, zincirleme çökme](#woodpecker-truncation-etcd), [Geri yüklemeden sonra index yok](#geri-yukleme-index-yok), [Tenant yedeği başarısız](#yedek-basarisiz), [Geri yükleme (MilvusRestore) başarısız](#yedek-geri-yukleme), [VectorDB platform işi başarısız](#yedek-platform-isi), [Telemetri toplayıcısı kayıt düşürüyor ya da gönderemiyor](#hat-collector-drop), [CRD ile operatör sürümü uyumsuz](#op-crd-surum-uyumsuz), [Reconcile hata döngüsünde (yetki, eksik CRD, çakışma)](#op-reconcile-hatasi), [Tek tenant'ın bozuk durumu bütün reconcile'ı kilitliyor](#op-tek-tenant-hepsini-kilitler), [metrics-agent veri toplayamıyor ya da tenant kotasını tüketiyor](#metrics-agent-sorunlari), [milvus-bucket-key boş ya da yanlış adla verilmiş](#bucket-key-bos), [Tenant'ın milvusConfig'i reddedildi](#milvusconfig-reddi), [Namespace milvus-operator'ın izleme listesinde değil](#operator-izleme-listesi), [milvus-operator yeniden başlıyor (tenant açma, helm upgrade, ArgoCD döngüsü)](#operator-yeniden-baslama), [Tenant silinemiyor, kaynaklar yetim kalıyor](#tenant-silme-takili)
- **Senaryosu olmayan (41):** [Disk kotası nedeniyle yazma kesilmesi](#yazma-disk-kotasi), [L0 delete birikimi](#yazma-l0-delete), [Datanode/streamingnode belleği nedeniyle yazma kesilmesi](#yazma-streamingnode-bellek), [Time tick gecikmesi](#yazma-time-tick), [etcd alan kotasının dolması](#etcd-alan-kotasi), [etcd diski yavaş (fsync), lease süresi doluyor](#etcd-yavas-disk), [Collection yüklenemiyor](#yukleme-basarisiz), [Compaction gecikmesi ve segment birikimi](#arkaplan-compaction), [Index oluşturma gecikmesi ya da başarısızlığı](#arkaplan-index), [Object storage kimlik, CA ya da adresleme hatası](#depolama-kimlik-ca), [1 GiB ephemeral sınırı ve access log emptyDir'i](#disk-ephemeral), [Querynode lokal diskinin dolması](#disk-querynode-lokal), [Arama gecikmesi ve zaman aşımı](#proxy-gecikme), [İstek hataları (not loaded, timeout, diğer)](#proxy-hata), [Hız sınırı (rate limited)](#proxy-rate-limit), [WAL'a (woodpecker) yazılamıyor ya da yavaş](#wal-yazma), [Woodpecker durdu, etcd şişti, zincirleme çökme](#woodpecker-truncation-etcd), [Mixcoord ya da düğümler çalışmıyor](#koordinator-dusuk), [Geri yüklemeden sonra index yok](#geri-yukleme-index-yok), [Tenant yedeği başarısız](#yedek-basarisiz), [Geri yükleme (MilvusRestore) başarısız](#yedek-geri-yukleme), [VectorDB platform işi başarısız](#yedek-platform-isi), [Telemetri toplayıcısı kayıt düşürüyor ya da gönderemiyor](#hat-collector-drop), [milvus-operator bağımlılıklar hazır olmadığı için reconcile etmiyor](#op-bagimlilik-hazir-degil), [CRD ile operatör sürümü uyumsuz](#op-crd-surum-uyumsuz), [milvus-operator çalışmıyor](#op-milvus-operator-calismiyor), [Root parolası rotasyonu uygulanmadı ya da password-reset Job takıldı](#op-parola-rotasyonu), [Reconcile hata döngüsünde (yetki, eksik CRD, çakışma)](#op-reconcile-hatasi), [Tek tenant'ın bozuk durumu bütün reconcile'ı kilitliyor](#op-tek-tenant-hepsini-kilitler), [milvus-tenant-operator çalışmıyor (crash, OOM, leader yok)](#op-tenant-operator-calismiyor), [metrics-agent veri toplayamıyor ya da tenant kotasını tüketiyor](#metrics-agent-sorunlari), [Access-log sidecar'ının emptyDir'i doldu](#sidecar-emptydir-dolu), [Sidecar çöktü, proxy pod'u hazır değil](#sidecar-proxy-hazir-degil), [NetworkPolicy Milvus ya da etcd erişimini kesiyor](#ag-politikasi), [milvus-bucket-key boş ya da yanlış adla verilmiş](#bucket-key-bos), [İmaj çekilemiyor (registry)](#imaj-cekilemiyor), [Tenant'ın milvusConfig'i reddedildi](#milvusconfig-reddi), [Namespace milvus-operator'ın izleme listesinde değil](#operator-izleme-listesi), [milvus-operator yeniden başlıyor (tenant açma, helm upgrade, ArgoCD döngüsü)](#operator-yeniden-baslama), [Tenant Creating'de takılı](#tenant-olusturma-takili), [Tenant silinemiyor, kaynaklar yetim kalıyor](#tenant-silme-takili)
- **Paneli olmayan (32):** [L0 delete birikimi](#yazma-l0-delete), [Time tick gecikmesi](#yazma-time-tick), [etcd diski yavaş (fsync), lease süresi doluyor](#etcd-yavas-disk), [Index oluşturma gecikmesi ya da başarısızlığı](#arkaplan-index), [Object storage kimlik, CA ya da adresleme hatası](#depolama-kimlik-ca), [İstek hataları (not loaded, timeout, diğer)](#proxy-hata), [Hız sınırı (rate limited)](#proxy-rate-limit), [WAL'a (woodpecker) yazılamıyor ya da yavaş](#wal-yazma), [Woodpecker durdu, etcd şişti, zincirleme çökme](#woodpecker-truncation-etcd), [Geri yüklemeden sonra index yok](#geri-yukleme-index-yok), [Tenant yedeği başarısız](#yedek-basarisiz), [Geri yükleme (MilvusRestore) başarısız](#yedek-geri-yukleme), [VectorDB platform işi başarısız](#yedek-platform-isi), [Telemetri toplayıcısı kayıt düşürüyor ya da gönderemiyor](#hat-collector-drop), [milvus-operator bağımlılıklar hazır olmadığı için reconcile etmiyor](#op-bagimlilik-hazir-degil), [CRD ile operatör sürümü uyumsuz](#op-crd-surum-uyumsuz), [milvus-operator çalışmıyor](#op-milvus-operator-calismiyor), [Root parolası rotasyonu uygulanmadı ya da password-reset Job takıldı](#op-parola-rotasyonu), [Reconcile hata döngüsünde (yetki, eksik CRD, çakışma)](#op-reconcile-hatasi), [Tek tenant'ın bozuk durumu bütün reconcile'ı kilitliyor](#op-tek-tenant-hepsini-kilitler), [milvus-tenant-operator çalışmıyor (crash, OOM, leader yok)](#op-tenant-operator-calismiyor), [metrics-agent veri toplayamıyor ya da tenant kotasını tüketiyor](#metrics-agent-sorunlari), [Access-log sidecar'ının emptyDir'i doldu](#sidecar-emptydir-dolu), [Sidecar çöktü, proxy pod'u hazır değil](#sidecar-proxy-hazir-degil), [NetworkPolicy Milvus ya da etcd erişimini kesiyor](#ag-politikasi), [milvus-bucket-key boş ya da yanlış adla verilmiş](#bucket-key-bos), [İmaj çekilemiyor (registry)](#imaj-cekilemiyor), [Tenant'ın milvusConfig'i reddedildi](#milvusconfig-reddi), [Namespace milvus-operator'ın izleme listesinde değil](#operator-izleme-listesi), [milvus-operator yeniden başlıyor (tenant açma, helm upgrade, ArgoCD döngüsü)](#operator-yeniden-baslama), [Tenant Creating'de takılı](#tenant-olusturma-takili), [Tenant silinemiyor, kaynaklar yetim kalıyor](#tenant-silme-takili)
- **Yolda olmayan sinyaller (8):** `etcd_mvcc_db_total_size_in_bytes` ([etcd alan kotasının dolması](#etcd-alan-kotasi)), `etcd_server_quota_backend_bytes` ([etcd alan kotasının dolması](#etcd-alan-kotasi)), `etcd_disk_wal_fsync_duration_seconds` ([etcd diski yavaş (fsync), lease süresi doluyor](#etcd-yavas-disk)), `etcd_server_has_leader` ([etcd diski yavaş (fsync), lease süresi doluyor](#etcd-yavas-disk)), `milvus_proxy_connection_num` ([Bağlantı sayısının tükenmesi](#proxy-baglanti)), `etcd_mvcc_db_total_size_in_bytes` ([Woodpecker durdu, etcd şişti, zincirleme çökme](#woodpecker-truncation-etcd)), `controller_runtime_reconcile_errors_total` ([Reconcile hata döngüsünde (yetki, eksik CRD, çakışma)](#op-reconcile-hatasi)), `controller_runtime_reconcile_errors_total` ([milvus-tenant-operator çalışmıyor (crash, OOM, leader yok)](#op-tenant-operator-calismiyor))

## Bilinen sorunlar

- `milvus_connections` kontrolü `milvus_proxy_connection_num` metriğini sorguluyor; bu metrik Milvus 2.6.6'da yok (kaynakta tanımlı değil). Kontrol bu yüzden hep "veri yok" döner. Düzeltme: bağlantı sayısının 2.6'daki karşılığını bulmak ya da kontrolü kaldırmak (ayrı iş).
- etcd'nin kendi metrikleri (`etcd_mvcc_db_total_size_in_bytes`, `etcd_server_quota_backend_bytes`) hiçbir yoldan toplanmıyor. Tenant metrics-agent yalnızca Milvus pod'larının 9091 portunu topluyor. Operatörün açtığı PodMonitor'da etcd'nin `client` (2379) portu tanımlı ama selector'ı yalnız `app.kubernetes.io/name=milvus` pod'larını seçiyor, etcd pod'ları (`name=etcd`) seçilmiyor (2026-10-19, staging ve GKE). Kümede görülen `etcd_*` metrikleri `job=apiserver`'dan, Kubernetes'in kendi etcd'sinden gelir. etcd alan kotası bu yüzden önceden görülemez; ancak sonucu (meta işlem hataları, çöken koordinatörler) görülür. Öneri VectorDB'a bildirilmeden değişiklik yapılmaz (ADR-014).
- Alarm kuralları pod'ları `app.kubernetes.io/part-of=vectordb` etiketiyle seçer (2026-10-18, eskiden elle tutulan tenant listesi); etiketi olmayan VectorDB pod'u (ör. `vectordb-docs`) kapsanmaz.
- Milvus'un yedek ve geri yükleme nesnelerinin (MilvusBackup, MilvusRestore) durumu sre-agent'ın okuma yetkisinde değil (yalnızca `milvuses` ekli). Yedek başarısızlığını görmek için paketin `rbac-values.yaml`'ına eklenmeleri gerekir.
