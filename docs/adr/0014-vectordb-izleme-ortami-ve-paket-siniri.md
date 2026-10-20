# ADR-014: Repo VectorDB'a hedef ortama yakın izleme ortamı da sağlar; VectorDB paketine yalnızca values ile dokunulur

- **Durum:** Kabul edildi
- **Tarih:** 2026-09-26
- **Etkilediği faz:** Tümü

## Bağlam

Plan bu repoyu SRE agent projesi olarak tanımlıyor. Oysa kurulan altyapı (Kafka, Prometheus, Grafana, Alertmanager, Loki, Tempo) VectorDB projesinin de izleme ortamıdır ve VectorDB hedef ortama teslim edilen bir pakettir (`vectordb-platform-helm`). İzleme ortamı pakete uymalı; paket izleme ortamına uydurulmamalı.

## Karar

1. Repo iki amaca hizmet eder: SRE agent ve VectorDB için hedef ortamdakine yakın bir izleme ortamı. Altyapı kararlarında ölçüt hedef ortam ortamına yaklaşmaktır.
2. VectorDB paketi bozulmaz. `vectordb-platform-helm` içinde yalnızca values yaml dosyaları değiştirilebilir; şablonlara dokunulmaz. VectorDB'ı etkileyecek her değişiklik yapılmadan önce bildirilir.
3. Uyumsuzluk olduğunda bu repodaki taraf (Kafka listener'ları, topic adları, kimlik doğrulama, gateway) VectorDB'ın mevcut yapılandırmasına uyar. Referans hedef ortamdaki davranıştır: `values-prod.yaml`.
4. İzleme chart'ları ileride `vectordb-platform-helm`'e taşınabilir; ancak hedef ortam teslim paketine girmeden ve ayrı bir kararla.

## Sonuçlar

- Topic adları, Kafka kimlik doğrulaması ve mesaj formatı VectorDB'ın values'ından alınır.
- GKE'de VectorDB tarafında gereken ayar değişiklikleri yalnızca `values-gke.yaml` üzerinden yapılır.
