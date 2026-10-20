# ADR-029: Ajan ilkeleri ve otonomi seviyeleri

- **Durum:** Kabul edildi
- **Tarih:** 2026-10-13
- **Etkilediği faz:** 3, 5, 6

## Bağlam

Google SRE'nin "AI in SRE" yazısı (2026-05) ajanlar için tasarım ilkeleri ve otonomi seviyeleri tanımlıyor. Hedef ortamda ajanın küme okuma yetkisi ve ileride aksiyon alması güvenlik onayı gerektirecek (plan, "Riskler"). İlkelerin bizde neye karşılık geldiğinin ve hangi otonomi seviyesinde olduğumuzun tek yerde yazılı olması gerekiyor.

## Karar

### İlkeler ve karşılıkları

| İlke (Google SRE) | Bizde |
| --- | --- |
| Klasik otomasyonla çözülen iş AI ile değiştirilmez | Faz 1: 26 kontrol modelsiz; model yalnız yorum gereken adımlarda (ADR-001) |
| Mevcut politika ve prosedürlere uyum | Hedef ortamın teslim ve güvenlik süreci; VectorDB paketine dokunulmaz (ADR-014) |
| Güvenlik, emniyet, gizlilik insanlarla aynı düzeyde | Salt okunur; secret okunmaz; log içeriği güvenilmez veri; PII maskeleme hedef ortamda (Faz 5) |
| Güçlü kimlik (rol ve yetki) | Ayrı ServiceAccount (`sre-agent-readonly`), namespace kapsamı seçilebilir (ADR-025); T3.3'te MCP sunucuları yetenek başına ayrı yetkiyle |
| Güvenilirlik SLO'su ve yedek seçenek | Model yoksa Faz 1 raporu + kural kararı; graph her adımda deterministik yedek |
| Neyi neden yaptığını ve **neyi neden reddettiğini** açıklama | Kanıt zinciri; correlate ve rapor `rejected` alanı (reddedilen hipotez + neden); karar kayıtları (`reports.decisions`) |
| AI arızası için iş sürekliliği | Ajan kapatılabilir (`decisions.mode`, `investigation.mode` = off); alarm yolu ajansız da çalışır |
| Üretim verisine sürekli erişim | Telemetri hattı ve tazelik (kör nokta) |
| Sürekli kalite değerlendirmesi, denetim ve raporlama | Golden set (incident başına), `decisions-eval`, OTel GenAI span'ları, Postgres kayıtları |

### Otonomi seviyeleri

| Seviye | Ajan ne yapar | İnsan ne yapar | Durum |
| --- | --- | --- | --- |
| **L0 Gözlem** | Kanıt toplar, rapor yazar, kategori tahmin eder (shadow) | Her şeye karar verir | **Bugün** |
| **L1 Öneri** | Kök neden ve önerilen adımları **etkili** olarak sunar | Okur, uygular ya da reddeder | Model ölçümde kuralları geçince |
| **L2 Onaylı aksiyon** | Belirli, geri alınabilir aksiyonları **önerir ve insan onayıyla** uygular (ayrı, yetkili ServiceAccount) | Her aksiyonu onaylar | Faz 6 |
| **L3 Sınırlı otonomi** | Önceden onaylı, düşük riskli aksiyonları kendisi uygular, sonra bildirir | Denetler, politikayı belirler | Tanımlanmadı |

Bir seviyeden sonrakine geçiş şartı: ölçüm (golden set, olay türü bazında) ve yazılı onay. L2 ve sonrası her zaman ayrı ServiceAccount ile, okuma kimliğinden ayrıdır; L0–L1'de ajan hiçbir şeyi değiştiremez.

## Sonuçlar

- Hedef ortam güvenlik onayında bu tablo kullanılır; her satırın kanıtı kodda ve kayıtlarda.
- "Neyi neden reddetti" rapor şemasının parçasıdır (T3.2'de eklendi, T3.5'te model doldurur).
- Kaynak: [AI in SRE: Where and how Google is deploying agentic AI](https://cloud.google.com/blog/products/devops-sre/how-google-sre-is-using-agentic-ai-to-improve-operations).
