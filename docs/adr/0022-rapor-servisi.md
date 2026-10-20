# ADR-022: Rapor servisi — Grafana anotasyonu, FastAPI, bellekte raporlar, Artifact Registry

- **Durum:** Kabul edildi
- **Tarih:** 2026-09-27
- **Etkilediği faz:** 1, 3

## Bağlam

T1.7'de T0.7'nin geçici webhook'u, alarma göre kontrolleri seçip paralel koşan ve kanıt raporu üreten bir servise dönüşüyor. Rapor 60 saniye içinde Grafana'da görünmeli. Servisin image'ı, HTTP çatısı ve raporların saklanması belirlenmeli.

## Karar

1. **Grafana anotasyonu:** Grafana'da `sre-agent` servis hesabı, **Editor** rolüyle (anotasyon yazmanın en düşük hazır rolü). Token `sre-agent` namespace'inde `sre-agent-grafana` Secret'ında (`make grafana-token`); repoya girmez. Bu, "ajanlar yazma yetkisi kullanmaz" kuralının bilinçli istisnasıdır: cluster'a değil gözlem aracına yazılır. Cluster erişimi read-only kalır.
2. **HTTP:** FastAPI + uvicorn. `POST /alerts` (Alertmanager), `GET /reports`, `GET /reports/{id}` (JSON), `/reports/{id}.md` (Markdown), `/healthz`. Webhook hemen 202 döner, koşular arka planda (en fazla 2 eşzamanlı, aynı alarm 10 dk içinde tekrar koşulmaz).
3. **Saklama:** Faz 1'de son 200 rapor bellekte; her rapor ve her webhook bildirimi tek satır JSON olarak loglanır. Pod yeniden başlarsa bellekteki raporlar kaybolur; tek replika. Kalıcı saklama Faz 3'te Postgres ile.
4. **Image:** `registry.example.com/sre-agent-apps/sre-agent:<sürüm>-<commit>[-dirty]`, `python:3.12.14-slim` tabanlı, `uv` ile, `linux/amd64`. Root olmayan kullanıcı, salt okunur dosya sistemi. Domain paketleri image'dadır; hangilerinin yükleneceğini yapılandırma belirler. Push kullanıcının `gcloud` yetkisiyle yapılır (`make image-push`).
5. T0.7'nin `alert-webhook` chart'ı kaldırılır; Alertmanager `team=vectordb` alarmlarını `sre-agent`'a yollar. Gruplama doğrulaması (`make alert-webhook-test`) servis logundaki `event=webhook` kayıtlarını okur.

## Sonuçlar

- Kontrol seçimi: paketin alarm eşlemesi, yoksa yapılandırmadaki `default_checks`, o da yoksa tüm çekirdek kontroller.
- Kubernetes bağlantısı kurulamazsa servis yine açılır; k8s/change kontrolleri "adaptör yok" (error) döner.
- Hedef ortamda Grafana yerine başka bir sistem (ITSM/Instana) kullanılacaksa anotasyon bir port arkasına alınır (Faz 5).
