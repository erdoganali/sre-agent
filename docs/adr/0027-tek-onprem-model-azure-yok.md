# ADR-027: Faz 2 tek on-prem modelle başlar; Azure OpenAI yok; model erişimi değiştirilebilir

- **Durum:** Kabul edildi
- **Tarih:** 2026-10-09
- **Etkilediği faz:** 2, 3, 5

## Bağlam

Plan küçük/orta/büyük model profilleri ve sınırlı Azure OpenAI (etiketleme ve eval hakemi) öngörüyordu (ADR-002). Şu an erişilebilir tek model kurum içi bir vLLM endpoint'i: `qwen3.6-35b`, OpenAI uyumlu `/v1/chat/completions`, düşünme modu `chat_template_kwargs.enable_thinking` ile açılıp kapanıyor. Azure OpenAI bu aşamada yok. İleride endpoint değişebilir ya da araya kullanıcının kendi model gateway'i girebilir.

## Karar

- Faz 2 tek on-prem modelle başlar. Profiller (küçük/orta/büyük) kalır ama başlangıçta hepsi aynı modele işaret eder; farkı istek ayarları yapar (düşünme açık/kapalı, `max_tokens`, sıcaklık).
- Azure OpenAI kullanılmaz. Etiketleme insan ya da senaryo etiketiyle yapılır; eval hakemi yoktur, puanlama deterministik karşılaştırmayla (koşucu) ve elle yapılır.
- Model erişimi tek bir porttan geçer ve yalnızca OpenAI uyumlu API'yi bilir. Endpoint, model adı, kimlik başlığı ve sağlayıcıya özel ek alanlar (`extra_body`, ör. `chat_template_kwargs`) profil başına Helm values'tan gelir. Kodda model ya da sağlayıcı adı geçmez. Böylece endpoint'i başka bir vLLM'e ya da bir model gateway'ine çevirmek yalnızca values değişikliğidir.
- Katalog biçimi kurum içinde çalışan code-mantis projesinin `configs/models.yaml` dosyasını örnek alır (model anahtarı → name, url, bağlam sınırı, reasoning seviyeleri, sampling parametreleri; `${ENV:varsayılan}` ile ezme; rol → model eşlemesi). Aynı modeli kullanan ekiplerle aynı biçimi paylaşmak, model eklemeyi ve gateway'e geçişi kolaylaştırır.
- Model yeteneklerine (structured output / guided decoding, logprob, araç çağrısı) güvenmeden önce adaptör bunları açılışta yoklar; yoksa yedek yol kullanılır (JSON doğrulama + bir düzeltme denemesi).

## Sonuçlar

- T2.4 "model seçimi" yerine tek modelin profillerinin (düşünme açık/kapalı) ölçülmesine döner; ikinci model geldiğinde aynı ölçüm tekrar koşar.
- ADR-002'deki Azure kullanımı askıya alınır; geri gelirse yine yalnızca çevrimdışı hakem/etiket önerisi için ve values ile açılır.
- Tek model tek arıza noktasıdır: model erişilemezse Faz 1'in LLM'siz raporu yedek olarak üretilmeye devam eder.
