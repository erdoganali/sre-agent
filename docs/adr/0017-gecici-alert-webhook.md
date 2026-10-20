# ADR-017: Faz 0'da Alertmanager alıcısı ConfigMap'li geçici bir webhook

- **Durum:** Yerine geçildi (ADR-022, T1.7)
- **Tarih:** 2026-09-26
- **Etkilediği faz:** 0, 1

## Bağlam

T0.7 alarmların gruplanmış şekilde bir webhook'a düştüğünü görmek istiyor. Plana göre asıl alıcı SRE agent'ın kendisi (Alertmanager webhook tetiklemesi), ancak henüz agent image'ı ve image derleme/registry hattı yok.

## Karar

Alıcı, standart kütüphaneyle yazılmış tek dosyalık bir Python script'i (`deploy/helm/alert-webhook/files/webhook.py`): ConfigMap olarak resmi `python:3.14.7-slim` image'ına bağlanır, her bildirimi tek satır JSON olarak loglar. Root olmayan kullanıcı, salt okunur dosya sistemi, ServiceAccount token'ı bağlanmaz. Alarm içeriği güvenilmez veridir; yalnızca loglanır.

## Sonuçlar

- Image derlemeden çalışır; hedef ortamda yalnızca Python image'ının registry'si Nexus'a çevrilir.
- SRE agent webhook'u hazır olunca Alertmanager alıcısının URL'si değişir, bu chart kaldırılır.
