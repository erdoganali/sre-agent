# ADR-008: VectorDB ajanın bilgi tabanı

- **Durum:** Kabul edildi
- **Tarih:** 2026-09-25
- **Etkilediği faz:** 4

## Bağlam

Ön filtre bilinen olayları geçmiş RCA'larla yanıtlamalı; chat ve uzmanlar runbook'lara erişmeli. Kurumda VectorDB (Milvus) vektör platformu hem GCP'de hem hedef ortamda mevcut.

## Karar

Ajanın RCA ve runbook bilgi tabanı VectorDB'da tutulur: GCP'de GKE üzerindeki, hedef ortamda OpenShift üzerindeki VectorDB. Bilgi tabanı bir port olarak soyutlanır.

## Sonuçlar

- Ek bir vektör veritabanı işletilmez.
- VectorDB aynı zamanda ilk hedef sistemdir; VectorDB olaylarında bilgi tabanının da etkilenebileceği ayrıca ele alınmalıdır.
