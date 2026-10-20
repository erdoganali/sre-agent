# ADR-011: GKE'de doğrudan Helm, Argo CD yalnızca hedef ortamda

- **Durum:** Kabul edildi
- **Tarih:** 2026-09-25
- **Etkilediği faz:** 0, 5

## Bağlam

ADR-006 her şeyin Helm + Argo CD ile kurulmasını söylüyor. GKE cluster'ında (`example-gke`) Argo CD yok; hedef ortamda chart'lar Argo CD ile deploy ediliyor.

## Karar

GKE'de chart'lar `helm upgrade --install` ile (Makefile hedefleri üzerinden) kurulur. Argo CD yalnızca hedef ortamda, aynı chart'ları ve values dosyalarını deploy etmek için kullanılır. Argo CD `Application` manifest'leri hedef ortam tarafının işidir; chart'lar Argo CD'ye özgü bir şey (hook, sync-wave zorunluluğu) varsaymaz.

## Sonuçlar

- GKE'de ek kontrol düzlemi kurulmaz.
- Chart'lar düz Helm ile kurulabilir kalmalı; CRD'ler ile CR'lar ayrı release'lerde tutulur ki sıralama sorunu Argo CD'ye ihtiyaç duymadan çözülsün.
