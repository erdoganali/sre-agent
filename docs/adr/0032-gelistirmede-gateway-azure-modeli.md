# ADR-032: Geliştirme ve ölçüm ortamında gateway üzerinden Azure OpenAI modeli

- **Durum:** Kabul edildi
- **Tarih:** 2026-10-14
- **Etkilediği faz:** 2, 3
- **İlgili:** ADR-002, ADR-027 (bu ADR onları hedef ortam için değiştirmez)

## Bağlam

Kural: canlı pipeline Azure OpenAI'a bağımlı olamaz; Azure yalnız etiketleme ve eval hakemi içindir (ADR-002, ADR-027). On-prem modeller (qwen3.6-35b, gpt-oss-20b) akşam saatlerinde kapalı; Faz 2 ölçümleri ve Faz 3 geliştirmesi (specialist araç döngüsü) model olmadan ilerleyemiyor. Model gateway'i (llm-gateway.example.internal, OpenAI uyumlu) hem on-prem modelleri hem de Azure OpenAI modellerini aynı arayüzle sunuyor. Gateway'den 2026-10-14'te ölçülen:

| Model | Logprob | Araç çağırma | Tipli JSON | Not |
|---|---|---|---|---|
| gpt-4o-mini | var | var | var | Tek harflik soruya harfle başlamadı |
| gpt-4.1-mini | var | var | var | Doğru harf, ~0,6–1 sn |
| gpt-5.4-nano | var | yok (gateway 404) | var | Araç döngüsüne uygun değil |

## Karar

- **Kapsam:** yalnız geliştirme ve ölçüm ortamı (staging `example-gke`). Hedef (air-gapped) kurulumda kural aynen geçerli: roller on-prem modellerde.
- **Model:** `gpt-4.1-mini` (araç çağırma, logprob ve tipli çıktının üçünü de veren en küçük model); üç rol (decision, specialist, synthesize) buna bağlı. On-prem modeller açıkken aynı katalogla onlara dönülebilir.
- **Erişim:** her istek gateway'den; anahtar yalnız kümedeki `sre-agent-models` Secret'ında (`EAG_AUTH`; qwen ve gpt-oss için de aynı anahtar). Anahtar repoya, values dosyasına ya da loga yazılmaz.
- **Ayar:** `config/models.yaml`'da gateway modelleri (`${EAG_URL}`, `${EAG_AUTH}`); staging'nin `deploy/values/staging/sre-agent.yaml` dosyası rolleri `SRE_*_MODEL` ile seçer. GKE ve hedef ortam values'ı değişmez.
- **Ölçüm notu:** Azure modeliyle alınan sonuçlar ayrı etiketlenir (model adı koşu kaydında); on-prem model sonucu yerine geçmez.

## Sonuçlar

- Geliştirme ve ölçüm on-prem modellerin açık olduğu saatlere bağlı değil.
- Token harcaması gateway'de de muhasebeleşir; sre-agent kendi sayacını ayrıca tutar (`sre_agent_model_tokens_total`).
- Hedef ortam/air-gapped kurulumu etkilenmez: orada gateway ve Azure yok, katalog yalnız on-prem modelleri gösterir.
- Anahtar bir kez mesajda düz metin geçti; kalıcı kullanımdan önce yenilenmeli.
