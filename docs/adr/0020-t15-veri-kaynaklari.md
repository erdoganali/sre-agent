# ADR-020: Uygulama, ağ ve güvenlik kontrollerinin veri kaynakları

- **Durum:** Kabul edildi
- **Tarih:** 2026-09-26
- **Etkilediği faz:** 1, 5

## Bağlam

T1.5 hata imzaları, çöken container'ın exception'ı, DNS/bağlantı hataları, ingress 5xx, object storage hataları, 401/403 ve sertifika süresi istiyor. GKE'de Loki'ye yalnızca VectorDB'ın OTel hattından gelen loglar akıyor; pod logları gelmiyor. ingress-nginx metrikleri toplanmıyor ve ingress-nginx paylaşılan, bize ait olmayan bir kurulum. TLS sertifikaları Secret'ta ve Secret okunmuyor. Object storage metrikleri Milvus'a özgü.

## Karar

1. **Pod logları** için `K8sPort.pod_logs` (RBAC'ta zaten `pods/log` okuma var). Çöken container'ın önceki çalışmasının logu (`previous=true`) buradan okunur. Tüm pod loglarını Loki'ye taşıyan bir DaemonSet kurulmaz (hedef ortamda logların Kafka sonrası hedefi hâlâ açık soru).
2. **ingress 5xx** kontrolü yazılır, sorgusu hazır bekler; ingress-nginx'e dokunulmaz, metrik yokken `no_data` döner.
3. **Sertifika süresi** için yeni `tls` yeteneği: Ingress `spec.tls` host'larına ve yapılandırmadaki uç noktalara TLS el sıkışması yapılır, sertifikanın bitiş tarihi okunur. Doğrulama kapalıdır (amaç güven değil, tarih); Secret okunmaz.
4. **Object storage** kontrolü Milvus metriklerine dayandığı için çekirdekte değil, VectorDB domain paketinde (T1.6) yazılır.

## Sonuçlar

- `lightkube.AsyncClient.log()` `previous` parametresini açmadığı için adaptör lightkube'un iç istek kurucusunu kullanır; birim testi URL'de `previous=true` olduğunu sınar, kütüphane yükseltmesinde bozulursa yakalanır.
- 401/403 yalnızca Kubernetes API için ölçülür; uygulama katmanı için metrik olursa sorgu değiştirilir.
- TLS el sıkışması ajanın ağ erişimi gerektirir (salt okunur ama dışa bağlantı); hedef ortamda hedef uç noktalar açıkça yapılandırılır.
- Sertifika ayrıştırma için `cryptography` bağımlılığı eklendi.
