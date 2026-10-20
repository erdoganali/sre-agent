# ADR-016: Pod başına ephemeral storage kullanımı için k8s-ephemeral-storage-metrics

- **Durum:** Kabul edildi
- **Tarih:** 2026-09-26
- **Etkilediği faz:** 0

## Bağlam

T0.6 ephemeral storage için bir alarm istiyor (hedef ortamda pod başı sınır 1Gi; VectorDB access log emptyDir'i 384Mi). GKE/containerd'de cAdvisor `container_fs_usage_bytes` vermiyor, kube-state-metrics kullanım bilgisi taşımıyor; tenant pod'larında ephemeral limiti de tanımlı değil. Yalnızca eviction'a bakmak tepkisel kalır.

## Karar

`k8s-ephemeral-storage-metrics` (1.21.3) `monitoring` namespace'ine kurulur. Kubelet `/stats/summary`'yi okuyup pod ve emptyDir başına kullanım metriği üretir. Chart'ın hazır kuralları kapalı; kurallar `vectordb-alerts`'te.

## Sonuçlar

- RBAC yalnızca okuma (`get/list/watch`: nodes, nodes/proxy, nodes/stats, pods). `nodes/proxy` kubelet API'sine erişim verdiği için hassastır; hedef ortama taşınacaksa güvenlik onayı gerekir. OpenShift'te cAdvisor fs metrikleri varsa exporter gerekmeyebilir (T0.8'de bakılır).
- EphemeralStorageNearLimit 1Gi'yi sabit sınır kabul eder; pod'lara limit tanımlanırsa kural limite göre yazılabilir.
