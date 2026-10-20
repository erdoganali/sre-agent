# ADR-007: Karar noktalarında System One sözleşmesi; açık implementasyon, Jev değil

- **Durum:** Kabul edildi
- **Tarih:** 2026-09-25
- **Etkilediği faz:** 2

## Bağlam

Karar noktalarında (ön filtre, tip ve kategori, uzman seçimi, uzman döngüsünde durma, chat intent) metin üreten büyük bir LLM pahalı ve yavaş. Bu sorular olasılıklı, tipli cevaplarla yanıtlanabilir.

## Karar

Karar noktalarında olasılıklı tipli cevap döndüren küçük bir karar modeli kullanılır. Model, System One uyumlu bir API'nin arkasında durur. Jev kapalı ağırlıklı olduğu ve yalnızca bulutta sunulduğu için elendi. Adaylar açık implementasyonlardır: Kev-0.8B, Laya ve jeff. Evaluate'in kalite kapısı LLM'de kalır.

## Sonuçlar

- Karar modelleri zero-shot'ta zayıf. Faz 1'in etiketli verisiyle GCP'de fine-tune ve kalibrasyon yapılmadan canlıya alınmaz.
- Her kapanan olay bir (kontrol raporu → kategori) çifti üretir; bu çiftler eğitim verisidir.
