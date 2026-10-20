# ADR-021: Domain paketleri yapılandırmayla yüklenir; çekirdek paketsiz çalışır

- **Durum:** Kabul edildi
- **Tarih:** 2026-09-27
- **Etkilediği faz:** 1, 3, 5

## Bağlam

Plan, başka servis ekiplerinin kendi domain paketleriyle kurabileceği modüler bir ürün istiyor; `CLAUDE.md` ortama/ürüne özel kodun çekirdeğe girmemesini şart koşuyor. VectorDB (Milvus) kontrolleri bu tür ilk paket.

## Karar

- Paket bir dizindir: `pack.yaml` (ad, sürüm, modül, alarm → kontrol eşlemesi), `PACK: PackContent` dışa veren bir Python modülü (kontroller, golden signals sinyalleri, okunabilir custom resource türleri) ve isteğe bağlı `rbac-values.yaml`.
- Çekirdek (`sre_agent.packs`) paketleri yalnızca yapılandırmadaki listeden (`config/<ortam>.yaml` → `packs`) dosya yolundan import eder; paketler çekirdeğin wheel'ine girmez.
- `CheckRegistry` çekirdek ve paket kontrollerini birleştirir; aynı ad, bilinmeyen kontrole giden alarm eşlemesi ya da `PACK` dışa vermeyen modül yükleme anında hata verir.
- Paketin RBAC ihtiyacı `rbac-values.yaml` ile sre-agent-rbac chart'ına eklenir (`make rbac-install` yapılandırmadaki paketlerinkileri otomatik ekler); aynı "secrets ve * yok" kısıtı geçerlidir.

## Sonuçlar

- Paket yapılandırmadan çıkarılırsa import edilmez; paket dizini silinse de çekirdek çalışır (testle sınanır).
- Paket kontrolleri çekirdeğin portlarını ve yardımcılarını kullanır; çekirdek API'si paketler için bir sözleşme haline gelir.
- VectorDB paketinde bağlantı sayısı ve segment/compaction kontrolleri Kafka yolunda metrik olmadığı için şimdilik `no_data` döner.
- Paket sözleşmesi dışarıdan gelenler için `docs/packs.md`'de (İngilizce); şablon `packs/example/` (kendi testleriyle). Testlerdeki ikinci paket (`tests/fixtures/packs/demo`) bu şablona taşındı (2026-10-20).
