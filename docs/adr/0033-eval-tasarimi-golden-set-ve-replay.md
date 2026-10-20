# ADR-033: Eval tasarımı — dondurulmuş golden set, kayıt ve tekrar oynatma, karşılaştırılabilir koşular

- **Durum:** Kabul edildi
- **Tarih:** 2026-10-15
- **Etkilediği faz:** 3, 4, 5

## Bağlam

T3.4'ün kabul eval'i (9 olay, 5'i doğru) araçları canlı kümeye gönderdi: olay anının yerleşimi Prometheus'tan okundu. Prometheus 10 gün, Loki 7 gün, Tempo 3 gün tutuyor; bir olay bu süreden sonra aynı veriyle tekrar oynatılamıyor. MVP'den sonra prompt, model ya da şema değiştiğinde aynı olaylarda önce/sonra karşılaştırması gerekecek. Ayrıca T3.4 eval'i küçük ve dengesizdi (50 olayın 39'u donanım), tekrar edilmedi, tek bir vekil ölçüte (kanıtta desen eşleşmesi) dayandı ve etiketi tartışmalı olaylar (aynı saatte iki neden) ayrılmadı. T3.5'in kabulü "golden set'te kuralları ve T2.5 tek çağrılık model kararını geçiyor" diyor; bunun için sistemlerin aynı olaylarda, aynı veriyle karşılaştırılması gerekir.

## Karar

1. **Dondurulmuş golden set:** `eval/golden/v<N>.yaml` git'te. Olay başına kimlik, etiket, kaynak (gerçek / lab), not ve gerekirse kabul edilen kategoriler (`accept: [a, b]`, aynı saatte iki neden). Katmanlı seçim; eksik kategoriler chaos senaryolarıyla doldurulur ve set yeni sürüm olur. Sonuçlar yalnız aynı sürümle karşılaştırılır.
2. **İki mod:**
   - `live`: araçlar gerçek kümeye gider; her araç çağrısı ve sonucu olay başına kaydedilir (`eval/cassettes/v<N>/<olay>.jsonl`, git'te; yalnız araç çıktısı, gizli değer yok — MCP sunucuları zaten döndürmez, ADR-030).
   - `replay`: araçlar kayıttan cevap verir, kümeye gidilmez. Kayıtta olmayan çağrı "kayıt yok" döner ve oranı raporlanır; oran yüksekse veri saklama süresi içinde yeni bir `live` koşu gerekir.
3. **Karşılaştırılan sistemler (aynı olaylarda):** A Faz 1 kuralları, B T2.5 tek çağrılık model kararı, C graph araçsız (Faz 1 kanıtı), D graph araçlı. C–D farkı araçların katkısıdır (ablasyon). Kabul: D, A ve B'yi geçer.
4. **Metrikler:** doğruluk (kabul edilen kategorilerden biri); makro-F1 (dengesizlik); "bilinmiyor" oranı ve susmadığı olaylarda doğruluk; "kesin" dediği raporların doğruluğu; kanıt — desen eşleşmesi ve hakem model puanı (1–5, yazılı ölçüt; hakem Azure modeli, ADR-002'nin izin verdiği kullanım); maliyet ve güvenilirlik — olay başına token, süre, araç çağrısı, yedeğe düşme oranı.
5. **İstatistik:** modelli sistemler (B, D) olay başına 3 kez koşulur, ortalama ve aralık verilir; oranlar Wilson güven aralığıyla; iki sistem aynı olaylarda eşleştirilmiş testle (McNemar) karşılaştırılır. Modelsiz A ve ucuz C bir kez.
6. **Koşu kimliği:** her sonuç dosyasında commit, imaj etiketi, rol başına model, model kataloğu ve prompt'ların hash'i, taksonomi sürümü, golden set sürümü, mod, çağrı sınırı, tarih.
7. **Karşılaştırma:** `make eval-compare A=<sonuç> B=<sonuç>` olay olay farkı ve metrik farklarını verir. T3.4 kabul sonucu (`eval/results/20261014T2159Z-specialists.json`) ilk referanstır.
8. **Bütçe:** eval başına token tavanı (varsayılan 1M; sistem başına ayrı sayılır); tavan dolunca eval durur, o ana kadarki sonuç yazılmış olur.

## Sonuçlar

- MVP sonrasında prompt/model/şema değişikliği, aynı olaylarda aynı araç verisiyle (replay) ölçülebilir; veri saklama süresi engel olmaz.
- Kayıt dosyaları repo boyutunu büyütür; olay başına araç çıktısı ~8K token ile sınırlı olduğundan set başına birkaç MB beklenir.
- `replay` modelin keşif davranışını kısıtlar: kayıtta olmayan çağrı veri döndürmez. Bu yüzden asıl ölçüm `live` ile yapılır, `replay` regresyon ve önce/sonra karşılaştırması içindir.
- Hakem model Azure'da; canlı pipeline'a girmez (ADR-002, ADR-032).
- Etiket kalitesi (tek etiketleyici) sonuçların üst sınırıdır; tartışmalı olaylar `accept` ile işaretlenir, ikinci etiketleyici Faz 4'te değerlendirilir.

## Ek (2026-10-15): golden set'in kompozisyonu — Milvus ve VectorDB odaklı

GKE'deki etiketli olayların ~%90'ı spot node kaybı (günde 10–15). Bu ortama özgü; hedef ortamda spot yok. Golden set bu dağılımı yansıtırsa ölçüm ve ajan GKE'ye uyar. Kırılganlık araştırması (VectorDB belgeleri ve operatör geçmişi, Milvus 2.6 sürüm notları ve issue'ları, GKE/OpenShift/Ceph) üç katman gösterdi: Milvus motoru, VectorDB platformu ve operatörler, ortam. Ayrıntı: `packs/vectordb/catalog.md`.

Karar:

- **Ana gövde kurgudan etiketli senaryolar.** Etiket elle verilmez; senaryonun kendisi etikettir. Gerçek Milvus olayları (ör. 8 Ekim datanode HNSW OOM) eklenir.
- **Spot node kaybı ayrı alt küme** (2–3 olay); sonucu ayrı raporlanır, ana metriğe girmez.
- **Ana metrik kategori bazında** (makro-F1 ve kategori başına doğruluk); toplam doğruluk ortamın olay dağılımına bağlı olduğu için tek başına raporlanmaz.
- **Senaryo listesi (v1):**

| # | Senaryo | Katman | Kategori |
|---|---|---|---|
| 1 | etcd çoğunluk kaybı (3 üyeden 2'si) | Milvus/ortam | database |
| 2 | etcd yavaş disk | Milvus | database |
| 3 | etcd alan kotası (NOSPACE) | Milvus | database |
| 4 | object storage gecikmesi | Milvus/ortam | vendor_external |
| 5 | object storage kimlik ya da CA hatası | platform/ortam | human_error |
| 6 | querynode bellek kotası, yazma reddi (06) | Milvus | database |
| 7 | datanode index OOM (HNSW) | Milvus | capacity_performance |
| 8 | streamingnode kill, WAL takılması | Milvus | database |
| 9 | mixcoord failover | Milvus | database |
| 10 | L0 delete fırtınası | Milvus | database |
| 11 | izleme listesinde olmayan namespace | operatör | infrastructure_system |
| 12 | `milvus-bucket-key` boş ya da yanlış adlı | platform | human_error |
| 13 | NetworkPolicy 2379 ya da 19530'u kesiyor | platform/ortam | network |
| 14 | tenant-operator çalışmıyor (bekleyen tenant işi ilerlemez) | operatör | infrastructure_system |
| 15 | milvus-operator çalışmıyor (silinen bileşen geri gelmez) | operatör | infrastructure_system |
| 16 | access-log sidecar emptyDir dolu, proxy evict | proxy pod'u | capacity_performance |
| 17 | sidecar çöküyor, proxy hazır değil | proxy pod'u | application_service_bug |
| 18 | telemetri hattının kesilmesi (11) | platform | infrastructure_system |
| 19 | spot node kaybı (ayrı alt küme) | ortam | hardware_problem |

- **Önkoşul yetenekler:** ajan `MilvusTenant` CR durumunu, operatörlerin sağlığını ve etcd'yi göremedikçe 1–3, 11 ve 14–15 teşhis edilemez. Operatör logları ve metrikleri ile etcd metrikleri bugün toplanmıyor; bunun için VectorDB'a öneri gerekir (ADR-014: paket değişikliği önceden bildirilir).
- **Sıra:** önce 1, sonra 4, 5, 11, 12, 14, 15 (hedef ortamda en kritik ve bugün en kör olunan yerler).

## Ek 2 (2026-10-15): kayıt arka uç düzeyinde

`live` modda kaydedilen şey MCP aracının metin cevabı değil, adaptörün arka uçtan aldığı ham cevaptır (Prometheus, Loki, Tempo, Kubernetes). `replay` modda MCP aracı bu ham veriyle yeniden çalışır ve metni yeniden üretir. Böylece araç cevabının biçimindeki değişiklikler (öne çıkanlar, karşılaştırma, boşluğun sebebi) aynı olayda eski ve yeni hâliyle karşılaştırılabilir; metin kaydedilseydi eski biçim oynatılırdı.

