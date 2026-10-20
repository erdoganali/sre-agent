# ADR-006: GCP'de managed servis yok; GKE'deki Kafka hedef ortam veri yolunun eşleniği

- **Durum:** Kabul edildi
- **Tarih:** 2026-09-25
- **Etkilediği faz:** 0

## Bağlam

Nihai hedef air-gapped OpenShift. GCP'de managed servislere (Managed Prometheus, Cloud Logging, Vertex) dayanan bir sistem hedef ortama taşınırken yeniden yazılmak zorunda kalır.

## Karar

GCP'de managed servis kullanılmaz. Her şey Helm + Argo CD ile kurulur, tek container image ve Helm chart kullanılır, bağımlılıklar Nexus'tan gelir, dışarıya çağrı yapılmaz. Telemetri veri yolu: VectorDB OTel collector → Kafka (Strimzi) → Prometheus, Loki ve Tempo. GKE'deki Kafka, hedef ortamdaki veri yolunun eşleniğidir.

## Sonuçlar

- GCP'de test edilen sistem hedef ortama taşınırken yeniden yazılmaz.
- Aynı chart'lar düzenli olarak Artemis OCP'de kurulup doğrulanır.
- İstisna (ADR-023): GCP'de nesne deposu olarak GCS, yalnızca S3 API'si üzerinden kullanılır (Milvus ve Postgres yedekleri); hedef ortamda karşılığı Ceph'tir.
