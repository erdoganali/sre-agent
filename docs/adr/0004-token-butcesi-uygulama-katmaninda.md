# ADR-004: Token bütçesi uygulama katmanında uygulanır

- **Durum:** Kabul edildi
- **Tarih:** 2026-09-25
- **Etkilediği faz:** 2, 3

## Bağlam

GPU kıt ve GCP kullanımı ücretlendiriliyor. vLLM ayarları bizim kontrolümüzde değil, dolayısıyla limitler sunucu tarafında uygulanamaz.

## Karar

Token bütçesi tamamen uygulama katmanında uygulanır:

| Seviye | Limit | Limit dolunca |
| --- | --- | --- |
| Koşu | Yumuşak 600K, sert 1M token | Yumuşakta erken sentez; sertte eldeki kanıtla "kesin değil" işaretli rapor |
| Uzman | Kümülatif 120K token | Tool'suz son cevap |
| Tek tool sonucu | Yaklaşık 8K token | Kırpma + özet satırı |
| Eşzamanlılık | Aynı anda 2 koşu (başlangıç) | Öncelik kuyruğu |

Destekleyici önlemler: MCP yanıtları JSON yerine Markdown döner, sık birlikte yapılan çağrılar composite araçlarda birleşir, tekrar eden çağrılar yakalanır, uzmanlar ortak bir brief önekiyle başlar.

## Sonuçlar

- Koşu başına token ve süre Prometheus'a yazılır, Grafana'da izlenir.
- Bir story, token limiti geçilmeden "done" sayılmaz.
