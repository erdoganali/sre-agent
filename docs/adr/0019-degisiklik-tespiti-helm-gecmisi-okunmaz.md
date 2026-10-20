# ADR-019: Değişiklik tespitinde Helm geçmişi ve Secret'lar okunmaz

- **Durum:** Kabul edildi
- **Tarih:** 2026-09-26
- **Etkilediği faz:** 1, 5

## Bağlam

T1.3 son N dakikadaki dağıtımları, Helm release geçmişini ve ConfigMap/Secret değişikliklerini istiyor. Helm release geçmişini `sh.helm.release.v1.*` Secret'larında tutar; Secret'ların yalnızca metadata'sını listelemek bile `secrets` üzerinde `list` yetkisi gerektirir. Kural gereği ajan Secret okumaz. GKE audit logları Cloud Logging'dedir (ADR-006) ve Argo CD GKE'de yoktur (ADR-011).

## Karar

Değişiklikler `ChangePort` üzerinden, Secret okumadan çıkarılır:

- **Rollout:** pencerede oluşan ReplicaSet (Deployment) ve ControllerRevision (StatefulSet/DaemonSet); önceki revizyonla image farkı.
- **Kim değiştirdi:** nesnelerin `managedFields` girdileri. `kubectl-*` → elle (human_error), `helm` → Helm, `argocd*` → Argo CD; diğer kontrolcüler ve `status` alt kaynağı sayılmaz.
- **Helm bağlamı:** `meta.helm.sh/release-name` annotation'ı ve `helm.sh/chart` etiketi (güncel değer).
- **Argo CD (hedef ortam):** `Application` kaynaklarının `status.history` ve `status.sync` alanları; ayrı adaptör, RBAC'ta `extraRules` ile açılır.

Secret değişiklikleri izlenmez; ConfigMap değişiklikleri izlenir.

## Sonuçlar

- Helm'in önceki chart sürümü ve değer farkı görülmez; yalnızca güncel chart sürümü ve güncellemenin zamanı görülür.
- `managedFields` her yöneticinin yalnızca son güncellemesini tutar; aynı yöneticinin pencere içindeki birden çok değişikliği tek kayıt olur.
- Secret rotasyonu gibi değişiklikler bu kontrollerle görülmez; güvenlik kontrolleri (T1.5) sertifika süresine ve 401/403 artışına bakar.
