# ADR-015: Telemetri hattı VectorDB'ın hedef ortamdaki Kafka sözleşmesine uyar; Kafka sonrası telemetry-consumer

- **Durum:** Kabul edildi
- **Tarih:** 2026-09-26
- **Etkilediği faz:** 0

## Bağlam

VectorDB'ın OTel gateway'i (`vectordb-otel-collector`) OTLP alıp Kafka'ya yazar. Hedef ortamda (`values-prod.yaml`) broker `kafka.prod.example:9095`, kimlik doğrulama TLS üzerinden SASL PLAIN, format `otlp_json`, topic'ler `vectordb_*`. Şablon TLS'i zorunlu kılar (`insecure_skip_verify: true` sabit). Hedef ortamda Kafka'dan sonrasını hedef ortamın sistemleri (SIEM, log platformu, Instana) tüketir. ADR-014 gereği VectorDB paketine yalnızca values ile dokunulur.

## Karar

- GKE Kafka'sı hedef ortamdaki sözleşmeyi taklit eder: 9095'te TLS + SASL PLAIN dinleyicisi (Strimzi `custom` kimlik doğrulama). PLAIN kullanıcıları (JAAS) repoda değil, elle oluşturulan `kafka-sasl-plain-users` Secret'ında; broker onu `strimzisecrets` config provider'ıyla okur. 9092'deki kimlik doğrulamasız iç dinleyici yalnızca smoke testi içindir.
- Topic adları hedef ortamdakiyle aynı: `vectordb_milvus_metrics`, `vectordb_milvus_accesslog`, `vectordb_auditlog`, `vectordb_tenant_health`, `vectordb_traces`.
- VectorDB tarafında yalnızca `values-gke.yaml` değişir: broker adresi, `exporterMode: kafka`, pipeline'lar açık, topic adları `vectordb_*`. VectorDB kimliği (`user1`) olduğu gibi kullanılır.
- Kafka sonrası GKE'ye özgü `telemetry-consumer` (OpenTelemetry Collector contrib) çalışır: topic'leri `otlp_json` olarak okur; metrikleri Prometheus'a remote write ile, logları Loki'nin OTLP girişine (`log_source` index label), trace'leri Tempo'ya yollar. Tempo metrics-generator servis haritası ve span metriklerini Prometheus'a yazar.

## Sonuçlar

- VectorDB'ın GKE ile hedef ortam arasındaki farkı yalnızca broker adresi, replika sayısı, image registry'si ve kimlik bilgisinin kaynağıdır (values'ta düz metin / secret manager).
- `telemetry-consumer` hedef ortama gitmez; hedef ortamda karşılığı hedef ortamın tüketicileridir.
- VectorDB mesajları anahtarsız yazdığı için aynı serinin örnekleri partition'lara dağılır ve sıra dışı gelebilir; Prometheus'ta `outOfOrderTimeWindow: 2h` açıktır (T1.14'te 10 dakikadan artırıldı: consumer kesintisinden sonra geriden okunan veri kaybolmasın).
- Kafka'da yetkilendirme (ACL) yok; kimliği doğrulanan her kullanıcı her topic'e erişir. Hedef ortama yaklaştırmak gerekirse ayrı karar.
