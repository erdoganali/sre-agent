# ADR-030: MCP sunucularının yerleşimi — yetenek başına ayrı sunucu ve kimlik

- **Durum:** Kabul edildi
- **Tarih:** 2026-10-14
- **Etkilediği faz:** 3, 5

## Bağlam

Faz 3'te specialist'ler (T3.4) kanıtı kendileri toplayacak: Prometheus, Loki, Tempo, Kubernetes ve ürün paketi (Milvus) araç olarak sunulmalı. Plan, araçların MCP üzerinden ve "her MCP sunucusu için ayrı ServiceAccount" ile çalışmasını istiyor (plan, "Güvenlik"; ADR-029 "güçlü kimlik"). Hedef ortamda telemetri Instana'dan gelecek; specialist'in gördüğü araç arayüzü değişmemeli. Seçenekler:

- A: Yetenek başına ayrı sunucu, her biri kendi Deployment'ı ve ServiceAccount'uyla; tek imaj.
- B: Bütün araçlar tek MCP sunucusunda, tek kimlikle.
- C: MCP yok; araçlar sre-agent sürecinde doğrudan Python fonksiyonu.

## Karar

A.

- **Sunucular:** `metrics` (Prometheus), `logs` (Loki), `traces` (Tempo), `k8s` (Kubernetes API) ve `product` (domain paketinin kontrolleri, sorguları ve topology'si). Kod `src/sre_agent/mcp/`; sunucu `python -m sre_agent.mcp <ad>` ile başlar.
- **Aktarım:** resmi MCP Python SDK, küme içinde streamable HTTP; her sunucu bir Service. Testlerde ve yerelde aynı süreçte (bellek içi istemci).
- **Kimlik:** her sunucunun ayrı ServiceAccount'u var. `metrics`, `logs`, `traces` Kubernetes'e erişmez (token bağlanmaz). `k8s` ve `product` mevcut salt okunur ClusterRole'ü (`sre-agent-readonly`, ADR-025 kapsamı) kendi ServiceAccount'larıyla kullanır. Secret hiçbirinde okunmaz; `rbac-check` her ServiceAccount için koşar.
- **Kurulum:** sre-agent chart'ında `mcp.servers` listesi; açılan her sunucu için Deployment, Service ve ServiceAccount.
- **Araç ilkeleri:** az sayıda araç; sık birlikte yapılan sorgular tek araçta (composite); cevap Markdown, ~8K'da kırpılır ve kırpma bütçeye sayılır; aynı argümanla tekrar eden çağrı yeniden sorgulanmaz; zaman penceresi ve satır sayısı sınırlı; çıktı güvenilmez veri olarak işaretlenir.
- **Çekirdek ve paket:** çekirdek sunucularda yalnız genel araçlar (ör. `placement`: pod → node, olay anında). Ürüne özgü bilgi (bileşenler, bağımlılıklar, izinli ConfigMap alanları) paketten gelir (`packs/<ürün>/topology.yaml`); `product` sunucusu paketi yükler.
- **Hedef ortam:** araç adları ve şemaları aynı kalır; `metrics`/`logs`/`traces` sunucularının arkasındaki adaptör Instana'ya çevrilir (ADR-005, port ve adaptör).

## Sonuçlar

- Bir sunucunun yetkisi ötekilerden bağımsız daraltılabilir; telemetri sunucusu ele geçse bile Kubernetes'i okuyamaz.
- 5 ek pod (her biri ~100 MiB). Sunuculardan biri düşerse yalnız o yetenek eksik kalır; specialist "veri yok" bulgusu döner, graph durmaz.
- Yeni bağımlılık: `mcp` (resmi SDK); air-gapped kurulumda imajın içinde gelir.
- B'nin tek kimliği planla ve ADR-029 ile çelişirdi; C araç arayüzünü süreç içine kilitler, başka ajanların (A2A, Faz 4) aynı araçları kullanmasını zorlaştırırdı.
