# ADR-013: Loki ve Tempo grafana-community chart'larıyla, tek binary ve dosya sistemi depolamayla

- **Durum:** Kabul edildi
- **Tarih:** 2026-09-26
- **Etkilediği faz:** 0

## Bağlam

`grafana/tempo` chart'ı kullanımdan kaldırılmış (`deprecated: true`) ve Tempo 2.9.0'da kalmış; `grafana/loki` Loki 3.6.12'de. Chart'ların bakımı `grafana-community` reposuna geçmiş: Loki 3.7.8 ve Tempo 3.0.3 orada. Yeni kurulumlar en güncel sürümle yapılır. Nesne deposu yok: GCS yönetilen servis (ADR-006), cluster'daki MinIO çalışmıyor.

## Karar

- Loki `grafana-community/loki` 18.13.5 (Loki 3.7.8), Tempo `grafana-community/tempo` 3.0.0 (Tempo 3.0.3).
- İkisi de tek pod (Loki SingleBinary, Tempo monolitik), kalıcı diskte dosya sistemi depolama; Loki 7 gün, Tempo 72 saat saklar. Tek kiracı.
- Kullanım istatistiği ve güncelleme kontrolleri (Loki, Tempo, Grafana) kapalı.
- Chart'lar `helm repo add` gerektirmeden `--repo` URL'siyle kurulur; hedef ortamda URL Nexus'a çevrilir.

## Sonuçlar

- Hedef ortamda S3 uyumlu nesne deposu varsa geçiş yalnızca values'ta (`loki.storage`, `tempo.storage`).
- Tek pod ölçeklenmez; yük artarsa Loki SimpleScalable ve `tempo-distributed`'a geçilir (ayrı karar).
- Tempo 3.0'da ingester ve compactor yok; ileride eklenecek ayarlar 3.x yapılandırmasına göre yazılır.
