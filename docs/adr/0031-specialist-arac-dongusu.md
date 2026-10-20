# ADR-031: Specialist araç döngüsü — native tool calling, tipli JSON yedeği, kodda sınırlar

- **Durum:** Kabul edildi
- **Tarih:** 2026-10-14
- **Etkilediği faz:** 3

## Bağlam

T3.4'te specialist'ler (metrics, logs, traces, k8s, product) kanıtı MCP araçlarıyla (ADR-030) kendileri toplayacak. Model bir döngüde araç seçer, sonucu okur, gerekirse başka bir araç çağırır, sonunda bulgusunu yazar. Modeller OpenAI uyumlu uç noktalardan gelir (ADR-027) ve sunucudan sunucuya yetenek değişir: bazıları `tools` parametresini (native tool calling) destekler, bazıları desteklemez ya da düşünme modellerinde kararsız çalışır. Döngünün süresi, maliyeti ve bulgudaki kanıtın doğruluğu kodda güvence altında olmalı. Seçenekler:

- A: Native tool calling; sunucu desteklemiyorsa tipli JSON ("sıradaki adım") yedeği.
- B: Yalnız tipli JSON döngüsü (her model aynı yoldan).
- C: Yalnız native tool calling; desteklemeyen model specialist olamaz.

## Karar

A.

- **Yetenek yoklaması:** native tool calling ilk kullanımda yoklanır ve model başına hatırlanır (logprob yoklamasıyla aynı yol, ADR-028). Desteklenmiyorsa ya da model geçersiz araç çağrısı üretiyorsa tipli JSON yedeği kullanılır: `{"action": "call", "tool": ..., "arguments": ...}` ya da `{"action": "done"}`.
- **Araç kümesi:** her specialist yalnız kendi MCP sunucusunun araçlarını görür (metrics → metrics, …, product → product). Araç listesi ve şeması sunucudan okunur.
- **Sınırlar kodda:** specialist başına en çok 8 araç çağrısı (ilk karar 15; 2026-10-15'te ölçümde olay başına ~35–40K token görülünce 8'e indirildi — doğru kanıt bulunan olaylarda 1–9 çağrı yetiyordu); bütçe scope'u specialist başına 120K token, koşu 600K yumuşak / 1M sert; tek araç sonucu ~8K token (`ToolClient`, `Budget`). Sınır dolunca döngü durur ve eldekiyle bulgu yazılır.
- **Bulgu ikinci çağrıda:** döngü bitince birikmiş analiz tipli `Finding`'e çevrilir (gözlem, aday kategoriler, güven). Bulgunun **kanıt satırları modelden değil `ToolClient.calls`'tan** gelir (hangi araç, hangi argüman, dönen verinin özeti); model kanıt uyduramaz, yalnız hangi çağrının neyi gösterdiğini söyler. Aday kategoriler taksonomiye bağlı (T3.2'deki kategori doğrulaması).
- **Ortak önek:** bütün specialist'ler aynı sistem metni ve olay brief'iyle başlar; sunucu prefix caching yapıyorsa kazanç `usage`'daki önbellek alanından ölçülür.
- **Ürün bilgisi pakette:** specialist yönergeleri `packs/<ürün>/specialists/<ad>.md`; çekirdek ürün adı bilmez.
- **Yedek:** model, MCP sunucusu ya da döngü hata verirse o specialist için T3.2'deki `EvidenceSpecialist` (Faz 1 kanıtı) kullanılır; graph durmaz.
- **Güvenilmez veri:** araç sonuçları `<guvenilmez_veri>` içinde gider; sistem metni içindeki talimatlara uyulmayacağını söyler (T3.6'da testlenir).

## Sonuçlar

- Aynı specialist kodu tool calling destekleyen ve desteklemeyen modellerle çalışır; model değişimi yalnız katalog satırıdır.
- Kanıt `ToolClient.calls`'tan geldiği için rapordaki her kanıt satırı gerçek bir araç çağrısına izlenebilir (ADR-029, açıklanabilirlik).
- Tipli JSON yedeği her turda bir yapılandırılmış çıktı ister; native yola göre biraz daha fazla token harcar.
- B her modelde aynı davranırdı ama native desteği olan modellerin daha güvenilir araç seçimini kullanmazdı; C katalogdaki modelleri gereksiz yere daraltırdı.
