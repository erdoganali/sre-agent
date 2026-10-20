# ADR-003: Model sunumu kapsam dışı; hazır vLLM endpoint'leri tüketilir

- **Durum:** Kabul edildi
- **Tarih:** 2026-09-25
- **Etkilediği faz:** 2

## Bağlam

Platformda hazır vLLM endpoint'leri var ve başka bir ekip tarafından işletiliyor.

## Karar

Model sunumu bu projenin işi değildir. Modeller OpenAI uyumlu endpoint'lerden tüketilir; kod model sağlayıcısını bilmez.

## Sonuçlar

- vLLM ayarları (prefix caching, batch) bizim kontrolümüzde değildir; tasarım bunlara güvenmez.
- Model değişikliği konfigürasyonla (model profilleri) yapılır, kod değişikliği gerektirmez.
