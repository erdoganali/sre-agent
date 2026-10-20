# ADR-025: sre-agent'ın okuma yetkisi küme ya da namespace kapsamlı olabilir

- **Durum:** Kabul edildi
- **Tarih:** 2026-09-27
- **Etkilediği faz:** 1, 3, 5

## Bağlam

sre-agent salt okunur bir kimlikle (`sre-agent-readonly`) çalışır; yetkisi bugüne kadar küme çapındaydı (ClusterRole): secret dışında her namespace'i okuyabiliyordu. Tek ekip için sorun değil. Ancak aynı sre-agent birden çok ürünü izlediğinde (T1.18, kitap Bölüm 13) ya da hedef ortamda güvenlik ekibi en az yetki istediğinde, "bu ürünün namespace'lerini başka ürünün ajanı görmesin" gereksinimi doğabilir. Seçenekler:

- A: Chart'a kapsam seçeneği: küme ya da yalnızca listelenen namespace'ler.
- B: Yalnızca ClusterRole; karar hedef ortama bırakılır.
- C: Her ürüne ayrı sre-agent kurulumu.

## Karar

A. `sre-agent-rbac` chart'ında `scope` değeri:

- `cluster` (varsayılan, GKE): ClusterRole + ClusterRoleBinding; bugünkü davranış.
- `namespaces`: `namespaces` listesindeki her namespace'te Role + RoleBinding. Küme kapsamlı kaynaklar (nodes, namespaces, persistentvolumes, storageclasses) bu kipte yazılmaz.

Ortamın seçimi `deploy/values/<ortam>/sre-agent-rbac.yaml` dosyasından gelir (`make rbac-install` varsa ekler). Hedef ortamda hangi kipin kullanılacağına güvenlik ekibi karar verir.

## Sonuçlar

- Namespace kipinde node kontrolleri (`pending_pods`'un node kısmı, `node_conditions`, `node_events`) "veri yok" döner; donanım kaynaklı olaylar (Hardware Problem) daha zor ayırt edilir.
- Veri tazeliği denetimi Kubernetes erişilebilirliğini namespace listesi yerine alarmın namespace'indeki pod'larla sınar; iki kipte de çalışır.
- Yeni bir ürün eklendiğinde namespace kipinde onun namespace'leri de listeye yazılmalıdır.
- C (ürün başına ayrı kurulum) gerekirse aynı chart'larla yapılabilir; bu karar onu engellemez.
