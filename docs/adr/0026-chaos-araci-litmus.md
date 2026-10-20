# ADR-026: Chaos aracı LitmusChaos; ChaosCenter resmi MongoDB ile, ajanın yetkisi namespace'lerle sınırlı

- **Durum:** Kabul edildi
- **Tarih:** 2026-10-04
- **Etkilediği faz:** 1, 5

## Bağlam

Plan ve T1.8 chaos senaryoları için Chaos Mesh öngörüyordu. Kullanıcı LitmusChaos'u ve web arayüzünü (ChaosCenter) öğrenmek istedi. İki engel vardı:

- ChaosCenter MongoDB istiyor. Litmus'un `litmus` chart'ı MongoDB'yi Bitnami alt chart'ıyla kuruyor. Bitnami Ağustos 2025'te ücretsiz image yayınlamayı bıraktı; chart `bitnamilegacy/mongodb` gibi güncellenmeyen image'lar kullanıyor.
- ChaosCenter'dan indirilen küme ajanı (cluster-wide "chaos infrastructure") üç hesabı (`litmus`, `litmus-admin`, `argo-chaos`) küme çapında yazma yetkisiyle kuruyor. Arayüzde yanlış namespace seçilirse gerçek bir tenant'ın (`acme`) pod'ları silinebiliyordu.

## Karar

1. Chaos aracı **LitmusChaos** (3.30/3.31). Kurulum: `docs/litmus-kurulum.md`.
2. GKE'de **ChaosCenter** kurulur (öğrenme ve görselleştirme). MongoDB chart'tan değil, resmi `mongo:8.0` image'ıyla tek kopyalı bir replica set olarak kurulur; chart'ın MongoDB'si kapalıdır (`mongodb.enabled=false`). Parolalar rastgele üretilir, repoya girmez. Arayüz `litmus.example.internal` arkasındadır (ingress, Helm değeriyle); giriş parolalıdır, SSO yoktur.
3. Ajanın yazma yetkisi **`deploy/helm/litmus-scope`** ile listelenen namespace'lere indirilir (GKE: `sre-lab`, `loadtest`). Ajanın kendi ClusterRole'leri aynen kullanılır ama RoleBinding ile yalnızca bu namespace'lerde ve `litmus`'ta bağlanır; ajanın ClusterRoleBinding'leri silinir. Kümede yalnızca okuma kalır (namespace, node, pod, Litmus ve Argo nesneleri; secret, configmap ve log yok). Korunan namespace'ler (`acme`, `vectordb`, `kafka`, `monitoring`, `sre-agent` …) listeye yazılırsa kurulum durur. `make litmus-scope` uygular, `make litmus-check` denetler.
4. Hazır deney paketinin (`kubernetes-chaos`) küme çapında yetkili hesabı kurulmaz; yalnızca seçilen deney tanımları (pod-delete, pod-memory-hog, pod-cpu-hog, pod-network-latency, pod-network-loss) hedef namespace'lere konur ve deneyler o namespace'te yetkili `litmus-admin` hesabıyla çalışır.
5. Hedef ortamda önerilen yol ChaosCenter'sız çekirdektir (litmus-core, MongoDB'siz); kurulum ve yetkiler ayrıca onaya tabidir.

## Sonuçlar

- Ajan ChaosCenter'dan yeniden kurulur ya da güncellenirse küme çapındaki yazma bağları geri gelir; `make litmus-check` bunu yakalar, `make litmus-scope` yeniden uygular.
- Yeni bir namespace'te deney yapmak için `deploy/values/<ortam>/litmus-scope.yaml` listesine eklenip `make litmus-scope` çalıştırılır.
- chaos-operator'ın kendi tasarımı gereği ağ/bellek deneyleri node'da ayrıcalıklı (privileged, hostPID) yardımcı pod açar; Chaos Mesh'te de aynıdır. Hedef ortamda (OpenShift SCC) ayrıca izin gerekir.
- ChaosCenter'ın girişi HTTP ve parolalıdır; şirket ağına açıktır. Kalıcı kullanımda SSO ve TLS konuşulmalı.
- Doğrulama (2026-10-04): `make litmus-check` 34/34; ajanın loglarında yetki hatası yok (Argo `workflows` ve `clusterworkflowtemplates` okuma yetkisi eklenince); `sre-lab`'da pod-delete deneyi Pass.
