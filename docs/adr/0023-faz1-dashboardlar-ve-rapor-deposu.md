# ADR-023: Faz 1'e dashboard'lar ve kalıcı rapor deposu eklenir; Postgres operator'süz kurulur

- **Durum:** Kabul edildi
- **Tarih:** 2026-09-27
- **Etkilediği faz:** 1, 3, 5

## Bağlam

Plan Faz 1'de LLM'siz rapor ve etiketli veri istiyor ama raporların ve etiketlerin nerede tutulacağını ve insanların ajanın bulduklarını nereden izleyeceğini tanımlamıyor; Postgres Faz 3'te geliyordu. Rapor servisinin (T1.7) bellekteki raporları pod yeniden başlayınca kayboluyor. Hat sessizce koparsa kontroller `no_data` döner ve rapor "normal" gibi okunabilir.

## Karar

1. Faz 1'e dört görev eklenir, yürütme sırasıyla: T1.14 telemetri hattı dashboard'u ve veri tazeliği; T1.12 kalıcı rapor deposu ve SRE Agent dashboard'u; T1.15 Milvus arıza kataloğu; T1.13 VectorDB/Milvus dashboard'u. T1.16 sentetik prob Milvus'a yazdığı için ayrı karar bekler.
2. **Postgres operator'süz:** resmi image, kendi chart'ımızda tek replikalı StatefulSet + PVC; CRD ya da cluster seviyesinde yetki gerektirmez (hedef ortamda merkezi cluster'larda operator kurdurmak zor). Uygulama veritabanını yalnızca `DATABASE_URL` ile tanır; hedef ortamda merkezi Postgres/DBaaS'a geçiş yalnızca adres değişikliğidir. Otomatik failover ve point-in-time recovery yok (MVP için kabul); yedek günlük `pg_dump`, geri dönüş en az bir kez denenir. HA gerekirse CloudNativePG yeniden değerlendirilir.
3. Şemalar `reports` (Faz 1) ve `agent` (Faz 3); Alembic ile migration. Raporlar insan etiketi, fingerprint ve taksonomi/kontrol sürümleriyle saklanır: karar modelinin eğitim verisi bu tablodan çıkar.
4. **Dashboard'lar kod olarak:** Grafana sidecar'ı ConfigMap'lerden yükler. SRE Agent dashboard'u (Postgres) çekirdektir ve hedef ortama aynen gider; Milvus (pakete ait) ve telemetri hattı dashboard'ları Prometheus'a bağlı GCP adaptörleridir.
5. Paket kontrolleri ile Milvus dashboard panelleri aynı sorgu dosyasını kullanır ("rapor anomali diyor, grafik normal" çelişkisi olmasın).
6. Rapor sayfaları ingress'le dışarı açılır (`/reports`). Basic auth önce açıktı; kullanıcı kararıyla (2026-09-27) kaldırıldı: raporlar ve etiket formu ingress'e erişen herkese açık. Gerekirse basic auth ya da IP kısıtı geri açılır.

## Sonuçlar

- Faz 1 uzar; buna karşılık chaos senaryoları (T1.8) koşulurken ajanın bulguları ve hat durumu izlenebilir, golden set sonuçları kalıcı olur.
- **Yedek hedefi (T1.12'de karar verildi):** S3 uyumlu nesne deposu, `rclone` ile. GCP'de GCS'nin S3 API'si (HMAC anahtarı; bucket `example-sre-agent-backups`, 30 gün saklama), hedef ortamda Ceph. Bu, ADR-006'ya bilinçli bir istisnadır: VectorDB'ın Milvus'u da GCS'yi aynı şekilde kullanır ve uygulama yalnızca S3 API'sini bilir; hedef ortamda yalnızca uç nokta ve anahtar değişir. Yedekleme hesabı yazıp okuyabilir, silemez.
