# ADR-001: Çok ajanlı yapı; akışı deterministik graf yönetir

- **Durum:** Kabul edildi
- **Tarih:** 2026-09-25
- **Etkilediği faz:** 3

## Bağlam

Alarm triage'ı birden çok sinyal kaynağından (metrik, log, trace, Kubernetes, domain) paralel kanıt toplamayı gerektiriyor. Açık modeller akış kontrolünde güvenilmez olabilir. Denetlenebilirlik ve tekrarlanabilirlik şart (BDDK/KVKK).

## Karar

Sistem çok ajanlıdır: koordinatör ajanlar (Gather, Correlate, Evaluate, Synthesize) bağlamı toplar ve bulguları birleştirir; uzman ajanlar paralel kanıt toplar. Akışı LLM değil deterministik bir graf yönetir: graf yönlendirmesi deterministik fonksiyonlardır. Deep dive döngüsü en fazla 2 turdur.

## Sonuçlar

- Aynı girdi aynı yolu izler; akış test edilebilir ve denetlenebilir.
- LLM'ler yalnızca düğüm içinde içerik üretir; bir sonraki adımı seçmez.
- Evaluate yönlendirme kararını kod verir; LLM yalnızca güven değerlendirmesi üretir.
