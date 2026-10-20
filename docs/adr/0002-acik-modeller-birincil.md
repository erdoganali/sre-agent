# ADR-002: Açık modeller birincil; Azure OpenAI sınırlı, opsiyonel ve canlı pipeline dışında

- **Durum:** Kabul edildi
- **Tarih:** 2026-09-25
- **Etkilediği faz:** 2

## Bağlam

Nihai hedef air-gapped OpenShift; orada yalnızca kurum içi açık modeller erişilebilir. GCP'de Azure OpenAI sınırlı kotayla mevcut.

## Karar

Canlı pipeline'da açık modeller (vLLM endpoint'leri) kullanılır. Azure OpenAI yalnızca etiketleme ve eval hakemi için kullanılır; canlı pipeline Azure'a bağımlı olamaz. Golden set, Azure OpenAI kapalıyken de geçmelidir.

## Sonuçlar

- GCP'de test edilen davranış hedef ortamda aynı kalır.
- Correlate gibi zor adımlarda açık modellerin zayıf kalma riski var; statik kontroller ve deterministik yönlendirme riski azaltır, golden set erken gösterir.
- Fark ettirmeden Azure'a bağımlı hale gelmemek için golden set Azure kapalıyken de koşar.
