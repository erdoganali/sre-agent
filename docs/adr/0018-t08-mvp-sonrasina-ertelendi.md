# ADR-018: Artemis OCP taşınabilirlik kontrolü (T0.8) MVP sonrasına ertelendi

- **Durum:** Kabul edildi
- **Tarih:** 2026-09-26
- **Etkilediği faz:** 0, 5

## Bağlam

Faz 0'ın çıkış kriterlerinden biri aynı chart'ların Artemis OCP'de kurulması. GKE tarafı (T0.1–T0.7) tamamlandı; Artemis'e erişim oturum başında yoktu ve MVP'nin değeri GKE'de üretiliyor.

## Karar

T0.8 GCP MVP'si tamamlandıktan sonra yapılır. Faz 0 bu görev dışında kapanmış sayılır ve Faz 1'e geçilir. Görev `faz-0.md`'de `- [-]` (ertelendi) olarak durur; oturum başındaki "ilk açık görev" taramasında atlanır.

## Sonuçlar

- OpenShift farkları (SCC, route, ephemeral exporter ihtiyacı — ADR-016) MVP sonrasına kadar doğrulanmaz; yeni chart'lar yine de farkları yalnızca values'ta tutacak şekilde yazılır.
- Faz 1'deki T1.11 (rapor servisi ve kontrollerin OCP'de kurulması) da aynı gerekçeyle ertelenir ve T0.8 ile birlikte yapılır.
- Hedef ortam fazına (Faz 5) geçmeden önce T0.8 ve T1.11 yapılmış olmalıdır.
