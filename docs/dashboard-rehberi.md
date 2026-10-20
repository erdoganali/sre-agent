# Dashboard okuma rehberi

Grafana'da üç dashboard var. İkisi de repodan otomatik yüklenir; elle değiştirilen şey bir sonraki kurulumda geri gelir.

| Dashboard | Sorduğu soru |
| --- | --- |
| **SRE Platform / Telemetri hattı** | VectorDB'ın telemetrisi bize ulaşıyor mu, SRE Agent çalışıyor mu? |
| **SRE Agent / Raporlar** | SRE Agent hangi alarmlara ne rapor üretti? |
| **VectorDB / Milvus** | Seçili tenant'ın Milvus'u ne durumda? (paneller sre-agent'ın Milvus kontrolleriyle aynı sorguyu kullanır) |

Her kutunun sağ üstündeki **ⓘ** simgesinde üç şey yazar: *ne gösterir*, *normalde kaç olur*, *kırmızıysa ne yapmalı*.
Renkler her yerde aynı: 🟢 normal · 🟠 dikkat · 🔴 müdahale gerekiyor.

## Veri nereden geliyor?

```
VectorDB (Milvus, backend)
   │  OTLP
   ▼
VectorDB gateway ──► Kafka (vectordb_* topic'leri) ──► telemetry-consumer ──► Prometheus (metrik)
   (hedef ortamda da aynı)        (hedef ortamda da aynı)        (yalnızca GKE)      ├─► Loki (log)
                                                                           └─► Tempo (trace)
                                                                                  │
Alertmanager ──(alarm)──► SRE Agent ──► rapor ──► Grafana (kırmızı işaretler, "Raporlar")
```

Bir halka koparsa ondan sonraki her şey "sessiz" görünür. Sessizlik "sorun yok" demek değildir; bu yüzden en üstte hattın kendisinin sağlığı durur.

## SRE Platform / Telemetri hattı: nasıl okunur?

1. **En üstteki "Hat sağlıklı mı?"** kutusuna bak.
   - **✅ Her şey yolunda (0):** gerisine bakmana gerek yok.
   - **🔴 sayı:** o kadar koşul bozuk; aynı satırdaki kırmızı kutuyu bul.
2. **Kırmızı kutuya göre nereye bakılır:**

| Kırmızı kutu | Anlamı | Bakma sırası |
| --- | --- | --- |
| **Alarm sistemi (Watchdog yaşı)** > 15 dk | Prometheus → Alertmanager → SRE Agent zinciri kopmuş; yeni alarmlar rapora dönüşmüyor | `monitoring` namespace'inde Prometheus ve Alertmanager pod'ları; sonra SRE Agent |
| **SRE Agent ayakta mı?** = HAYIR | Rapor servisi çalışmıyor | `sre-agent` namespace'indeki pod'un durumu ve logu |
| **Tenant verisi ne kadar eski?** > 10 dk | O tenant'ın Milvus metrikleri gelmiyor; raporları "kör noktalı" | **Kafka** bölümü: "VectorDB'dan gelen mesaj" 0 ise sorun **VectorDB gateway**'inde; mesaj geliyor ama "bekleyen mesaj" artıyorsa sorun **telemetry-consumer**'da |
| **Kafka'da bekleyen mesaj** > 1000 | telemetry-consumer okumuyor ya da yavaş | `monitoring` namespace'inde telemetry-consumer pod'u. 2 saatten uzun sürerse o dönemin metrikleri kaybolur (aşağıya bak) |
| **Düşen kayıt** > 0 | Okunan veri Prometheus/Loki'ye yazılamıyor | "telemetry-consumer" bölümündeki "düşen" çizgisi; Prometheus/Loki pod'ları |

3. **VectorDB alarmları** bölümü, bizim yazdığımız 16 kuraldan açık olanları gösterir (kurallar: `docs/alarmlar.md`). Her alarmın raporu "SRE Agent / Raporlar" dashboard'undadır.
4. **SRE Agent** bölümü ajanın kendisini ölçer:
   - **"Kayıp alarm" 0 olmalı.** Değilse bazı alarmlar için rapor üretilemedi.
   - **"Kör noktalı rapor"** eksik veriyle üretilmiş rapor sayısıdır. Bu raporlardaki "anomali yok" sonucuna tek başına güvenme.
5. **Genel Kubernetes alarmları** bölümü kapalı durur. Cluster'daki bütün alarmları (bizim olmayanlar dahil) gösterir; açmak için başlığa tıkla.

## SRE Agent / Raporlar: nasıl okunur?

- Üstteki liste, her alarm için üretilen raporun özetidir. Satırda alarm adı, bulunan anomali sayısı ve etiketler (namespace, `test`) yer alır.
- Satır **"⚠ kör nokta"** ile başlıyorsa rapor eksik veriyle üretilmiştir; önce hattı düzelt.
- Özetin sonundaki bağlantı tam raporu açar (`sre-agent.example.internal`, giriş gerekmez).
- Tam raporda kontroller "Değişiklik → Uygulama → Altyapı → Dış → Güvenlik" sırasıyla, önce anomaliler olacak şekilde dizilir. Her anomalinin altında onu bulan sorgu yazar.
- `test` etiketli raporlar deneme amaçlı üretilmiş sentetik alarmlardır.

## Bilinen sınırlar

- **Consumer 2 saatten uzun durursa veri kaybolur.** Consumer geri açıldığında Kafka'da biriken metrikleri Prometheus'a yazar; Prometheus 2 saatten eski örnekleri kabul etmez (sıra dışı kabul penceresi). Kaybolan dönemin grafiklerinde boşluk kalır ve "Düşen kayıt" kutusu 15 dakika kırmızı görünür. Kafka verileri 24 saat tuttuğu için sınır Prometheus tarafındadır.
- **VectorDB gateway'inin kendi metrikleri yok.** Gateway'in çalıştığı yalnızca Kafka'ya gelen mesajlardan anlaşılır.
- **Alertmanager ya da Prometheus'un kendisi düşerse** bunu bildirecek bir alarm yoktur; yalnızca Watchdog kutusu kırmızıya döner.
