# ADR-028: Karar noktalarının tasarımı — logprob olasılığı, kodda "bilinmiyor", gölge mod

- **Durum:** Kabul edildi
- **Tarih:** 2026-10-09
- **Etkilediği faz:** 2, 3

## Bağlam

T2.5 karar noktaları (ön filtre, grup → kategori) olasılıklı ve tipli cevap verir (ADR-007). Karar modeli ayrı bir servis değildir: katalogdaki `decision` rolü, yani on-prem model (ADR-027). Etiketli veri azdır (Faz 1 golden set + insan etiketleri) ve model kalibre değildir.

## Karar

1. **Olasılık modele yazdırılmaz.** Seçenekler harflerle sorulur, tek token üretilir; olasılık izin verilen harflerin token logprob'larından hesaplanır (yalnız bu seçenekler üzerinde normalize). Sunucu logprob vermiyorsa model kararı "kullanılamaz" sayılır ve kurallar devreye girer; modelin metinle yazdığı güven sayısı karar için kullanılmaz.
2. **"Bilinmiyor" model sınıfı değil, kodun kararıdır:** en yüksek olasılık eşiğin altındaysa ya da ilk iki seçenek arasındaki fark eşikten küçükse. Kontrollerin çoğu veri alamadıysa (kapsama eşiğin altında) model çağrılmaz, sonuç "değerlendirilemedi".
3. **İki aşama:** önce grup, sonra grup içi kategori. Grup kararı "bilinmiyor" ise kategori sorulmaz. Kategorinin nihai olasılığı grup olasılığı × grup içi olasılıktır.
4. **Ön filtre ikiye bölünür.** "Bilinen olay mı?" modele sorulmaz; benzerlikle (aynı alarm adı + anomali kümesi, etiketli geçmiş rapor) deterministik ve açıklanabilir biçimde cevaplanır; ileride gömme benzerliği eklenir. Model yalnız "gürültü mü?" sorusunu cevaplar. Gürültü kararı raporu bastırmaz; yalnız işaretler ve önceliği düşürür. Bastırma ancak ölçümde güvenilir olduğu görülünce ayrı bir kararla açılır.
5. **Her karar kaydedilir** (`reports.decisions`): karar noktası, kaynak (`model` / `rules` / `similarity`), etkili mi (gölgede değil mi), sonuç, olasılık dağılımı, eşikler, model anahtarı, taksonomi sürümü, girdi özetinin hash'i, hata.
6. **Mod:** `off` → `shadow` (kurallar karar verir, model arka planda koşup yalnız kaydedilir) → `model` (model karar verir; hata ya da kullanılamazlıkta kurallar). Model, ölçümde (T2.6: senaryo bazında doğruluk, kapsama, kalibrasyon hatası, kurallarla yan yana) kuralları geçmeden `model` moduna alınmaz.
7. **Model girdisi** ham log değil kontrol raporunun özetidir. Log/veri örnekleri ayrı, "güvenilmez veri" diye işaretli bölümde ve anomali başına en çok birkaç kısa satır olarak verilir.
8. Eşikler başlangıçta geçicidir (values'ta); T2.6 kalibrasyonundan çıkan değerlerle değiştirilir.

## Sonuçlar

- Karar katmanı modelsiz de çalışır (kurallar); model erişimi gelince yalnız mod değişir.
- Karar kayıtları etiketlerle birleşince bir sonraki kalibrasyonun ve eğitimin verisidir.
