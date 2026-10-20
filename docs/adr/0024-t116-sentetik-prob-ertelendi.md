# ADR-024: Sentetik prob (T1.16) test tenant'ı hazır olana kadar ertelendi

- **Durum:** Kabul edildi
- **Tarih:** 2026-09-27
- **Etkilediği faz:** 1

## Bağlam

T1.16, tenant başına küçük bir kanarya collection'a dakikada bir insert + search yapıp başarı ve gecikmeyi ölçen bir prob istiyor: pod'lar hazır görünürken yazmaların reddedildiği durumu yakalamak için. Prob Milvus'a **yazar**; sre-agent'ın salt okunur kuralının dışında kalır. Üç açık konu var:

- Hangi tenant'ta çalışacak? Müşteri tenant'ına (`acme`, hedef ortamdakiler) yazmak ayrı onay ister. Test tenant'ı `loadtest` şu an boş (içinde Milvus yok).
- Probun Milvus kullanıcısını kim oluşturacak? Milvus'ta kimlik doğrulama açık; kullanıcı açmak tenant'ın Milvus'una yazmak demek.
- Hedef ortamda açık mı olacak?

## Karar

T1.16 ertelenir (`faz-1.md`'de `- [-]`). `loadtest`'te VectorDB üzerinden bir Milvus açıldığında ve kullanıcı konusu netleştiğinde yeniden ele alınır.

O zaman için konuşulan tasarım (bağlayıcı değil):

- sre-agent'tan ayrı bileşen: kendi chart'ı (`deploy/helm/milvus-canary`), kendi namespace'i, Kubernetes yetkisi yok.
- Varsayılan kapalı: `enabled: false`, `tenants: []`; tenant başına listeye yazılarak açılır. Hızlı kapatma: `make canary-off` (kaldırır), `make canary-pause` (durdurur).
- Tenant'ta yalnızca `sre_canary` collection'ına yetkili ayrı bir Milvus kullanıcısı; root parolası kullanılmaz.
- Hedef ortamda varsayılan kapalı; açılması hedef ortamın onayına bağlı.

## Sonuçlar

- "Pod'lar hazır ama yazma reddediliyor" durumu şimdilik yalnızca `MilvusWriteDenied` (force-deny-writing sayacı) ile görülür; istemci tarafından ölçülen başarı ve gecikme yok.
- T1.8'in Milvus senaryoları da `loadtest`'e ihtiyaç duyuyor; test tenant'ının açılması iki görevin ortak ön koşulu.
