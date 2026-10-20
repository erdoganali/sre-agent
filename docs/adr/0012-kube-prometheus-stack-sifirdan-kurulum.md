# ADR-012: GKE'deki sahipsiz kube-prometheus-stack kaldırılıp sıfırdan kurulur

- **Durum:** Kabul edildi
- **Tarih:** 2026-09-25
- **Etkilediği faz:** 0

## Bağlam

`example-gke` cluster'ının `monitoring` namespace'inde kube-prometheus-stack 67.8.0 çalışıyordu. Bir zamanlar Argo CD ile kurulmuş, ancak cluster'da artık Argo CD ve Helm release'i yok; kurulum sahipsizdi ve bazı nesneleri elle değiştirilmişti. Kontrol edildi: başka namespace'lerden dashboard/datasource, Prometheus'u sorgulayan iş yükü veya APIService yok; Grafana'da yalnızca admin kullanıcısı var. Tek dış bağımlılık `acme` namespace'indeki bir PodMonitor (CRD'ye bağlı, stack'e değil). Sahiplenme (`--take-ownership`) değerlendirildi; eski sürüme ve elle yapılmış değişikliklere bağlı kalmak yerine temiz kurulum seçildi.

## Karar

Eski stack'in namespace'i, cluster seviyesindeki RBAC ve webhook kayıtları ve `kube-system` exporter servisleri silinir. Prometheus Operator CRD'leri silinmez (başka namespace'lerdeki monitor'lar korunur), yeni sürümle server-side apply ile güncellenir. Güncel kube-prometheus-stack (91.5.2, sonra 91.5.3) `monitoring` release adıyla, `monitoring` namespace'ine kurulur:

- Prometheus `release` etiketi aramaz; tüm namespace'lerdeki ServiceMonitor/PodMonitor/PrometheusRule okunur.
- GKE'de scrape edilemeyen kontrol düzlemi bileşenleri ve kube-proxy kapalı; CoreDNS yerine kube-dns.
- Grafana admin bilgisi repoda değil, elle oluşturulan `grafana-admin` Secret'ından okunur.

## Sonuçlar

- Eski Prometheus verisi (10 gün) ve Grafana'ya UI'dan eklenmiş dashboard'lar kaybolur (kabul edildi).
- Yapılandırmanın tamamı repoda; aynı chart OCP'ye values farkıyla taşınır.
- Helm ≥ 3.18.1 gerekir (3.18.0 sıfır değerli koşulları yanlış render ediyor; Makefile kontrol ediyor).
- Helm CRD'leri güncellemediği için sürüm yükseltmelerinde önce `make monitoring-crds` çalıştırılır.
