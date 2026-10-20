# Mimari karar kayıtları (ADR)

| # | Karar | Etkilediği faz |
| --- | --- | --- |
| [ADR-001](0001-cok-ajanli-deterministik-graf.md) | Çok ajanlı yapı; akışı LLM değil deterministik graf yönetir | 3 |
| [ADR-002](0002-acik-modeller-birincil.md) | Açık modeller birincil; Azure OpenAI sınırlı, opsiyonel ve canlı pipeline dışında | 2 |
| [ADR-003](0003-model-sunumu-kapsam-disi.md) | Model sunumu kapsam dışı; hazır vLLM endpoint'leri tüketilir | 2 |
| [ADR-004](0004-token-butcesi-uygulama-katmaninda.md) | Token bütçesi uygulama katmanında uygulanır | 2, 3 |
| [ADR-005](0005-port-adaptor-mimarisi.md) | Port ve adaptör mimarisi; uzmanlar yeteneğe göre tanımlanır; Instana hedef ortam telemetri adaptörüdür | 3, 5 |
| [ADR-006](0006-gcpde-managed-servis-yok.md) | GCP'de managed servis yok; GKE'deki Kafka hedef ortam veri yolunun eşleniği | 0 |
| [ADR-007](0007-karar-noktalarinda-system-one.md) | Karar noktalarında System One sözleşmesi; açık implementasyon, Jev değil | 2 |
| [ADR-008](0008-vectordb-bilgi-tabani.md) | VectorDB ajanın bilgi tabanı | 4 |
| [ADR-009](0009-bmad-ile-yurutme.md) | Proje BMAD ile yürütülür | Tümü |
| [ADR-010](0010-python-langgraph.md) | Dil Python; orkestrasyon LangGraph (ADK alternatif olarak açık) | 3 |
| [ADR-011](0011-gkede-helm-hedef ortamda-argocd.md) | GKE'de doğrudan Helm, Argo CD yalnızca hedef ortamda | 0, 5 |
| [ADR-012](0012-kube-prometheus-stack-sifirdan-kurulum.md) | GKE'deki sahipsiz kube-prometheus-stack kaldırılıp sıfırdan kurulur | 0 |
| [ADR-013](0013-loki-tempo-grafana-community.md) | Loki ve Tempo grafana-community chart'larıyla, tek binary ve dosya sistemi depolamayla | 0 |
| [ADR-014](0014-vectordb-izleme-ortami-ve-paket-siniri.md) | Repo VectorDB'a hedef ortama yakın izleme ortamı da sağlar; VectorDB paketine yalnızca values ile dokunulur | Tümü |
| [ADR-015](0015-telemetri-hatti-telemetry-consumer.md) | Telemetri hattı VectorDB'ın hedef ortamdaki Kafka sözleşmesine uyar; Kafka sonrası telemetry-consumer | 0 |
| [ADR-016](0016-ephemeral-storage-exporter.md) | Pod başına ephemeral storage kullanımı için k8s-ephemeral-storage-metrics | 0 |
| [ADR-017](0017-gecici-alert-webhook.md) | Faz 0'da Alertmanager alıcısı ConfigMap'li geçici bir webhook | 0, 1 |
| [ADR-018](0018-t08-mvp-sonrasina-ertelendi.md) | Artemis OCP taşınabilirlik kontrolü (T0.8) MVP sonrasına ertelendi | 0, 5 |
| [ADR-019](0019-degisiklik-tespiti-helm-gecmisi-okunmaz.md) | Değişiklik tespitinde Helm geçmişi ve Secret'lar okunmaz | 1, 5 |
| [ADR-020](0020-t15-veri-kaynaklari.md) | Uygulama, ağ ve güvenlik kontrollerinin veri kaynakları | 1, 5 |
| [ADR-021](0021-domain-paketleri.md) | Domain paketleri yapılandırmayla yüklenir; çekirdek paketsiz çalışır | 1, 3, 5 |
| [ADR-022](0022-rapor-servisi.md) | Rapor servisi — Grafana anotasyonu, FastAPI, bellekte raporlar, Artifact Registry | 1, 3 |
| [ADR-023](0023-faz1-dashboardlar-ve-rapor-deposu.md) | Faz 1'e dashboard'lar ve kalıcı rapor deposu eklenir; Postgres operator'süz kurulur | 1, 3, 5 |
| [ADR-024](0024-t116-sentetik-prob-ertelendi.md) | Sentetik prob (T1.16) test tenant'ı hazır olana kadar ertelendi | 1 |
| [ADR-025](0025-okuma-yetkisinin-kapsami.md) | sre-agent'ın okuma yetkisi küme ya da namespace kapsamlı olabilir | 1, 3, 5 |
| [ADR-026](0026-chaos-araci-litmus.md) | Chaos aracı LitmusChaos; ChaosCenter resmi MongoDB ile, ajanın yetkisi namespace'lerle sınırlı | 1, 5 |

Yeni ADR: sıradaki numarayla `NNNN-kisa-baslik.md`; Durum, Tarih, Etkilediği faz, Bağlam, Karar, Sonuçlar.
| [ADR-027](0027-tek-onprem-model-azure-yok.md) | Faz 2 tek on-prem modelle başlar; Azure OpenAI yok; model erişimi values ile değiştirilebilir (gateway dahil) | 2, 3, 5 |
| [ADR-028](0028-karar-noktalari-tasarimi.md) | Karar noktaları: logprob olasılığı, "bilinmiyor" kodda, gölge mod, gürültü bastırmaz, her karar kaydedilir | 2, 3 |
| [ADR-029](0029-ilkeler-ve-otonomi-seviyeleri.md) | Ajan ilkeleri (Google SRE AI) ve otonomi seviyeleri L0–L3; bugün L0 | 3, 5, 6 |
| [ADR-030](0030-mcp-sunuculari-yerlesimi.md) | MCP sunucuları: yetenek başına ayrı sunucu, Deployment ve ServiceAccount; telemetri sunucuları Kubernetes okumaz; ürün bilgisi paketten | 3, 5 |
| [ADR-031](0031-specialist-arac-dongusu.md) | Specialist araç döngüsü: native tool calling + tipli JSON yedeği; 8 çağrı / 120K sınırı kodda; kanıt ToolClient kayıtlarından | 3 |
| [ADR-032](0032-gelistirmede-gateway-azure-modeli.md) | Geliştirme/ölçüm ortamında (staging) gateway üzerinden gpt-4.1-mini; hedef air-gapped kurulumda kural aynen | 2, 3 |
| [ADR-033](0033-eval-tasarimi-golden-set-ve-replay.md) | Eval: dondurulmuş golden set, live/replay (araç kayıtları), A–D sistemleri aynı olaylarda, 3 tekrar, koşu kimliği ve eval-compare | 3, 4, 5 |
| [ADR-034](0034-bootcamp-mvp-kapsami-rag-ve-demo.md) | MVP kapsamı (bootcamp teslimi): etkili soruşturma raporda, RAG (Milvus BM25, graph brief'i + `knowledge_search`), demo kipi; ölçüm işleri ertelendi | 3, 4 |
