# ADR-005: Port ve adaptör mimarisi; uzmanlar yeteneğe göre tanımlanır; Instana hedef ortam telemetri adaptörüdür

- **Durum:** Kabul edildi
- **Tarih:** 2026-09-25
- **Etkilediği faz:** 3, 5

## Bağlam

Aynı çekirdek GCP'de (Prometheus, Loki, Tempo, GKE) ve hedef ortamda (Instana, OpenShift, ITSM) çalışmalı. Başka servis ekipleri ürünü kendi servislerine uyarlayabilmeli.

## Karar

Çekirdek (graf, token bütçesi, karar sözleşmesi) her kurulumda aynıdır. Ortama göre değişen her şey bir porta takılan adaptördür: tetikleme, telemetri, Kubernetes, model, bilgi tabanı, çıktı. Uzmanlar araca değil yeteneğe göre tanımlanır (metrik, log, trace, Kubernetes). Ortama özel kod yalnızca adaptörlerde bulunur. Hedef ortamda telemetri adaptörü Instana'dır.

Servise özel bilgi, domain paketi olarak taşınır: statik kontroller, uzman prompt'ları, skills dosyaları, kategori eşlemesi ve eval senaryoları. İlk paket VectorDB'dır. Kurulumda hangi adaptör ve paketlerin devreye gireceğini Helm values belirler.

## Sonuçlar

- Ortam değişince yalnızca adaptör değişir; uzman prompt'ları ve graf aynı kalır.
- Başka ekipler çekirdeğe dokunmadan domain paketi yazabilir.
