"""VectorDB / Milvus dashboard'unu `queries.yaml`'dan üretir (T1.13).

Paket kontrolleri ile paneller aynı PromQL'i kullanır: sorgu şablonu `{ns}` → `$tenant`,
`{range}` → `$__rate_interval`, `{offset}` → "" ile açılır. Her kontrol panelinin açıklaması
kontrol adını içerir (test bununla eşler).

Çalıştırma: make dashboards  (packs/vectordb/dashboards/milvus.json yazılır)
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

import yaml

HERE = Path(__file__).parent
QUERIES = yaml.safe_load((HERE.parent / "queries.yaml").read_text())
OUT = HERE / "milvus.json"

DS = {"type": "prometheus", "uid": "prometheus"}
TENANT = '{job="milvus-metrics",k8s_namespace_name="$tenant"}'
ROLES = ("proxy", "querynode", "streamingnode", "datanode", "mixcoord")


def expr(template: str) -> str:
    """Kontrol sorgusunu panel sorgusuna çevirir (kontrolün `render`'ı ile aynı biçimlendirme)."""
    return template.format(ns="$tenant", range="$__rate_interval", offset="")


def q(check: str, key: str) -> str:
    return expr(QUERIES["checks"][check][key])


def desc(what: str, normal: str, action: str, check: str | None = None) -> str:
    text = f"**Ne gösterir:** {what}\n\n**Normalde:** {normal}\n\n**Kırmızı/turuncuysa:** {action}"
    if check:
        text += f"\n\n**Kontrol:** `{check}` (aynı sorgu, packs/vectordb/queries.yaml)"
    return text


class Layout:
    def __init__(self) -> None:
        self.panels: list[dict[str, Any]] = []
        self.y = 0
        self.x = 0
        self.row_h = 0

    def row(self, title: str) -> None:
        self._newline()
        self.panels.append(
            {
                "type": "row",
                "title": title,
                "collapsed": False,
                "gridPos": {"x": 0, "y": self.y, "w": 24, "h": 1},
                "panels": [],
            }
        )
        self.y += 1

    def add(self, panel: dict[str, Any], w: int, h: int) -> None:
        if self.x + w > 24:
            self._newline()
        panel["gridPos"] = {"x": self.x, "y": self.y, "w": w, "h": h}
        panel["datasource"] = DS
        self.panels.append(panel)
        self.x += w
        self.row_h = max(self.row_h, h)

    def _newline(self) -> None:
        if self.x:
            self.y += self.row_h
        self.x = 0
        self.row_h = 0


def target(e: str, legend: str = "", ref: str = "A", instant: bool = False) -> dict[str, Any]:
    t: dict[str, Any] = {"refId": ref, "datasource": DS, "expr": e, "legendFormat": legend}
    if instant:
        t["instant"] = True
    return t


def steps(*pairs: tuple[str, float | None]) -> dict[str, Any]:
    return {"mode": "absolute", "steps": [{"color": c, "value": v} for c, v in pairs]}


def stat(
    title: str,
    description: str,
    targets: list[dict[str, Any]],
    thresholds: dict[str, Any],
    unit: str = "short",
    no_value: str = "veri yok",
) -> dict[str, Any]:
    return {
        "type": "stat",
        "title": title,
        "description": description,
        "targets": targets,
        "fieldConfig": {
            "defaults": {"unit": unit, "thresholds": thresholds, "noValue": no_value},
            "overrides": [],
        },
        "options": {
            "colorMode": "background",
            "graphMode": "none",
            "textMode": "value_and_name",
            "reduceOptions": {"calcs": ["lastNotNull"]},
        },
    }


def series(
    title: str,
    description: str,
    targets: list[dict[str, Any]],
    unit: str = "short",
    thresholds: dict[str, Any] | None = None,
    overrides: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    defaults: dict[str, Any] = {
        "unit": unit,
        "noValue": "veri yok",
        "custom": {"lineWidth": 2, "fillOpacity": 10, "showPoints": "never"},
    }
    if thresholds:
        defaults["thresholds"] = thresholds
        defaults["custom"]["thresholdsStyle"] = {"mode": "dashed"}
    return {
        "type": "timeseries",
        "title": title,
        "description": description,
        "targets": targets,
        "fieldConfig": {"defaults": defaults, "overrides": overrides or []},
        "options": {"legend": {"displayMode": "list", "placement": "bottom"}},
    }


def build() -> dict[str, Any]:
    L = Layout()

    # ------------------------------------------------------------------ durum
    L.row("Durum: tenant ayakta mı?")
    for role in ROLES:
        L.add(
            stat(
                f"{role} hazır",
                desc(
                    f"`{role}` rolündeki hazır pod sayısı (Kubernetes).",
                    "1 ya da daha fazla.",
                    "0 ise rol çalışmıyor: pod'un olaylarına ve loglarına bak.",
                ),
                [
                    target(
                        "sum(kube_pod_status_ready"
                        f'{{namespace="$tenant",condition="true",pod=~".*-milvus-{role}-.*"}})'
                    )
                ],
                steps(("red", None), ("green", 1)),
                no_value="yok",
            ),
            w=4,
            h=4,
        )
    L.add(
        stat(
            "Milvus toplanıyor mu?",
            desc(
                "Tenant ajanının Milvus bileşenlerinden metrik toplayabildiği hedef sayısı (up), "
                "hedef ortamdaki yoldan. `milvus_health` kontrolü Milvus nesnesinin durumunu "
                "Kubernetes'ten okur; Prometheus'ta karşılığı olmadığı için en yakın "
                "gösterge budur.",
                "Rol sayısı kadar (5).",
                "Düşükse bir bileşenin /metrics'i okunamıyor ya da pod yok.",
                check="milvus_health",
            ),
            [target(f"sum(up{TENANT})")],
            steps(("red", None), ("orange", 1), ("green", 5)),
        ),
        w=4,
        h=4,
    )
    L.add(
        stat(
            "Yüklü collection",
            desc(
                "Querycoord'un belleğe yüklediği collection sayısı / toplam collection.",
                "Kullanılan collection'lar yüklü.",
                "Yüklü sayı düşerse arama 'collection not loaded' hatası verir.",
            ),
            [
                target(f"max(milvus_querycoord_collection_num{TENANT})", "yüklü"),
                target(f"max(milvus_rootcoord_collection_num{TENANT})", "toplam", "B"),
            ],
            steps(("blue", None)),
        ),
        w=6,
        h=4,
    )
    L.add(
        stat(
            "Veri yaşı",
            desc(
                "Tenant'ın Milvus metriklerinin hedef ortamdaki yoldan gelen son örneğinin yaşı.",
                "1 dakikanın altında.",
                "10 dakikayı geçerse hat kopuk: SRE Platform / Telemetri hattı dashboard'una bak.",
            ),
            [
                target(
                    "time() - max(max_over_time(timestamp(milvus_rootcoord_timestamp"
                    f"{TENANT})[6h:1m]))"
                )
            ],
            steps(("green", None), ("orange", 300), ("red", 600)),
            unit="s",
        ),
        w=6,
        h=4,
    )

    # ------------------------------------------------------------------ bellek
    check = "milvus_querynode_memory_quota"
    L.row("Querynode belleği ve yazma kesme eşiği")
    L.add(
        series(
            "Querynode belleği / limit ve yazma kesme eşiği",
            desc(
                "Her querynode'un bellek kullanımının limite oranı ve Milvus'un yazmaları "
                "reddettiği eşik (qn_mem_high_water_level, sabit değil metrikten).",
                "Oran eşiğin epey altında; eşik 0.95.",
                "Oran eşiğe yaklaşırsa Milvus önce yazmayı yavaşlatır (0.85), sonra reddeder.",
                check=check,
            ),
            [
                target(q(check, "ratio"), "{{pod}}"),
                target(q(check, "hwl"), "yazma kesme eşiği", "B"),
            ],
            unit="percentunit",
            overrides=[
                {
                    "matcher": {"id": "byName", "options": "yazma kesme eşiği"},
                    "properties": [
                        {"id": "color", "value": {"mode": "fixed", "fixedColor": "red"}},
                        {"id": "custom.lineStyle", "value": {"fill": "dash", "dash": [10, 10]}},
                    ],
                }
            ],
        ),
        w=16,
        h=8,
    )
    L.add(
        series(
            "Reddedilen yazmalar",
            desc(
                "Milvus'un kota nedeniyle yazmayı reddettiği olay sayısı (force-deny-writing).",
                "0.",
                "0'dan büyükse yazmalar reddediliyor: bellek ya da disk kotası dolmuş.",
                check=check,
            ),
            [target(q(check, "deny"), "reddedilen")],
            thresholds=steps(("green", None), ("red", 1)),
        ),
        w=8,
        h=8,
    )

    # ------------------------------------------------------------------ koordinatör
    check = "milvus_coordinators"
    L.row("Koordinatörler")
    L.add(
        stat(
            "Koordinatörün gördüğü düğümler",
            desc(
                "Querycoord'un gördüğü querynode, datacoord'un gördüğü datanode sayısı.",
                "Her biri en az 1.",
                "0 ise arama ya da yazma yapılamaz.",
                check=check,
            ),
            [
                target(q(check, "querynodes"), "querynode"),
                target(q(check, "datanodes"), "datanode", "B"),
            ],
            steps(("red", None), ("green", 1)),
        ),
        w=8,
        h=6,
    )
    L.add(
        series(
            "Querynode heartbeat yaşı",
            desc(
                "Her querynode'un koordinatöre son heartbeat'inden bu yana geçen süre.",
                "Birkaç saniye.",
                "60 saniyeyi geçerse querynode koordinatörle konuşamıyor.",
                check=check,
            ),
            [target(q(check, "stale"), "node {{node_id}}")],
            unit="s",
            thresholds=steps(("green", None), ("red", 60)),
        ),
        w=16,
        h=6,
    )

    # ------------------------------------------------------------------ etcd
    check = "milvus_etcd"
    L.row("etcd (meta veri)")
    L.add(
        stat(
            "etcd pod'u",
            desc(
                "Tenant'taki etcd pod sayısı ve hazır olmayanlar.",
                "3 pod, hazır olmayan yok.",
                "Hazır olmayan varsa Milvus meta veri okuyup yazamayabilir.",
                check=check,
            ),
            [
                target(q(check, "exists"), "pod"),
                target(q(check, "not_ready"), "hazır değil: {{pod}}", "B"),
            ],
            steps(("green", None)),
        ),
        w=8,
        h=6,
    )
    L.add(
        series(
            "etcd yeniden başlama",
            desc(
                "Aralıkta yeniden başlayan etcd pod'ları.",
                "Boş.",
                "Yeniden başlama etcd'nin disk, bellek ya da alan kotası sorunu olabilir.",
                check=check,
            ),
            [target(q(check, "restarts"), "{{pod}}")],
        ),
        w=8,
        h=6,
    )
    L.add(
        series(
            "Meta işlem hataları",
            desc(
                "Milvus'un etcd'ye başarısız meta işlemleri.",
                "0.",
                "Artıyorsa etcd erişilemiyor ya da alan kotası dolmuş olabilir (etcd metrikleri "
                "toplanmıyor; bkz. arıza kataloğu).",
                check=check,
            ),
            [target(q(check, "meta_fail"), "hata")],
            thresholds=steps(("green", None), ("red", 1)),
        ),
        w=8,
        h=6,
    )

    # ------------------------------------------------------------------ object storage
    check = "milvus_object_storage"
    L.row("Object storage")
    L.add(
        series(
            "Object storage işlemleri ve hatalar",
            desc(
                "Milvus'un object storage (GKE'de GCS, hedef ortamda Ceph) işlemleri ve başarısız "
                "olanlar.",
                "Hata 0; işlem sayısı yükle birlikte değişir.",
                "Hata oranı %1'i geçerse dış depolama sorunlu.",
                check=check,
            ),
            [
                target(q(check, "total"), "işlem"),
                target(q(check, "fails"), "hata", "B"),
            ],
            overrides=[
                {
                    "matcher": {"id": "byName", "options": "hata"},
                    "properties": [
                        {"id": "color", "value": {"mode": "fixed", "fixedColor": "red"}}
                    ],
                }
            ],
        ),
        w=12,
        h=7,
    )
    L.add(
        series(
            "Object storage p99 gecikme",
            desc(
                "Object storage isteklerinin yüzde 99'unun altında kaldığı süre.",
                "Yüzlerce milisaniye.",
                "2 saniyeyi geçerse sorgulara yansır.",
                check=check,
            ),
            [target(q(check, "p99"), "p99")],
            unit="ms",
            thresholds=steps(("green", None), ("red", 2000)),
        ),
        w=12,
        h=7,
    )

    # ------------------------------------------------------------------ segment
    check = "milvus_segment_backlog"
    L.row("Segment ve compaction birikimi")
    L.add(
        series(
            "Growing / flushing segment",
            desc(
                "Henüz kalıcı hâle gelmemiş (growing, flushing) segment sayısı.",
                "Yükle dalgalanır, sürekli artmaz. Collection yoksa veri yok.",
                "200'ü geçip artıyorsa flush yazma hızına yetişemiyor.",
                check=check,
            ),
            [target(q(check, "growing"), "segment")],
            thresholds=steps(("green", None), ("red", 200)),
        ),
        w=12,
        h=7,
    )
    L.add(
        series(
            "Bekleyen compaction",
            desc(
                "Bekleyen ve çalışan compaction görevleri.",
                "Az ve geçici.",
                "50'yi geçip artıyorsa compaction gecikiyor; L0 silme kayıtları da birikir.",
                check=check,
            ),
            [target(q(check, "compaction"), "görev")],
            thresholds=steps(("green", None), ("red", 50)),
        ),
        w=12,
        h=7,
    )

    # ------------------------------------------------------------------ proxy
    L.row("Proxy: istekler, gecikme, bağlantı")
    signals = QUERIES["signals"]
    L.add(
        series(
            "İstek hızı",
            desc(
                "Proxy'ye saniyede gelen istek. golden_signals_baseline kontrolü bunu "
                "geçen haftayla "
                "karşılaştırır.",
                "Kullanıma göre; boşta 0.",
                "Ani düşüş istemci sorununu, ani artış yük sıçramasını gösterir.",
                check="golden_signals_baseline",
            ),
            [target(expr(signals["milvus_requests_per_s"]), "istek/sn")],
            unit="reqps",
        ),
        w=8,
        h=7,
    )
    L.add(
        series(
            "Arama p99 gecikmesi",
            desc(
                "Search, HybridSearch ve Query isteklerinin p99 gecikmesi.",
                "1000 ms altında.",
                "1000 ms'yi 10 dakika aşarsa MilvusSearchLatencyP99High alarmı çalar.",
                check="golden_signals_baseline",
            ),
            [target(expr(signals["milvus_search_p99_ms"]), "p99")],
            unit="ms",
            thresholds=steps(("green", None), ("red", 1000)),
        ),
        w=8,
        h=7,
    )
    check = "milvus_connections"
    L.add(
        series(
            "Proxy bağlantı sayısı",
            desc(
                "Proxy başına açık istemci bağlantısı. **Bilinen sorun:** sorgulanan metrik "
                "(milvus_proxy_connection_num) Milvus 2.6.6'da yok; panel ve kontrol "
                "veri göstermez "
                "(arıza kataloğu, bilinen sorunlar).",
                "Limitin (10000) %80'inin altında.",
                "Limite yaklaşırsa yeni istemciler reddedilir.",
                check=check,
            ),
            [target(q(check, "connections"), "{{pod}}")],
        ),
        w=8,
        h=7,
    )

    # ------------------------------------------------------------------ pod ve disk
    L.row("Pod'lar ve diskler")
    L.add(
        series(
            "Yeniden başlama",
            desc(
                "Aralıkta yeniden başlayan konteynerler.",
                "Boş.",
                "Sürekli yeniden başlayan pod için rapora ve pod loglarına bak.",
                check="pod_restarts",
            ),
            [
                target(
                    "sum by (pod) (increase(kube_pod_container_status_restarts_total"
                    '{namespace="$tenant"}[$__rate_interval])) > 0',
                    "{{pod}}",
                )
            ],
        ),
        w=8,
        h=7,
    )
    L.add(
        series(
            "PVC doluluğu",
            desc(
                "Tenant'ın kalıcı disklerinin (etcd dahil) doluluk oranı.",
                "%80 altında.",
                "%80'i geçerse PVCUsageHigh, %90'ı geçerse PVCUsageCritical çalar.",
                check="pvc_usage",
            ),
            [
                target(
                    'kubelet_volume_stats_used_bytes{namespace="$tenant"} / '
                    'kubelet_volume_stats_capacity_bytes{namespace="$tenant"}',
                    "{{persistentvolumeclaim}}",
                )
            ],
            unit="percentunit",
            thresholds=steps(("green", None), ("orange", 0.8), ("red", 0.9)),
        ),
        w=8,
        h=7,
    )
    L.add(
        series(
            "Ephemeral disk / 1 GiB",
            desc(
                "Pod başına geçici disk kullanımının hedef ortamdaki 1 GiB sınırına oranı.",
                "%80 altında.",
                "Sınır aşılırsa pod evict edilir.",
                check="ephemeral_storage_usage",
            ),
            [
                target(
                    'max by (pod_name) (ephemeral_storage_pod_usage{pod_namespace="$tenant"}) '
                    "/ 1073741824",
                    "{{pod_name}}",
                )
            ],
            unit="percentunit",
            thresholds=steps(("green", None), ("orange", 0.8)),
        ),
        w=8,
        h=7,
    )

    for i, p in enumerate(L.panels, start=1):
        p["id"] = i

    return {
        "uid": "vectordb-milvus",
        "title": "VectorDB / Milvus",
        "tags": ["sre-agent", "vectordb", "milvus"],
        "timezone": "browser",
        "schemaVersion": 39,
        "version": 1,
        "refresh": "30s",
        "time": {"from": "now-6h", "to": "now"},
        "description": (
            "Üretildi: packs/vectordb/dashboards/build.py (make dashboards); elle değiştirme."
        ),
        "templating": {
            "list": [
                {
                    "name": "tenant",
                    "label": "Tenant",
                    "type": "query",
                    "datasource": DS,
                    "query": {
                        "query": 'label_values(milvus_rootcoord_timestamp{job="milvus-metrics"}, '
                        "k8s_namespace_name)",
                        "refId": "tenant",
                    },
                    "definition": 'label_values(milvus_rootcoord_timestamp{job="milvus-metrics"}, '
                    "k8s_namespace_name)",
                    "refresh": 2,
                    "sort": 1,
                    "includeAll": False,
                    "multi": False,
                }
            ]
        },
        "annotations": {
            "list": [
                {
                    "name": "SRE raporları",
                    "datasource": {"type": "grafana", "uid": "-- Grafana --"},
                    "enable": True,
                    "iconColor": "red",
                    "target": {
                        "type": "tags",
                        "tags": ["sre-agent", "namespace=$tenant"],
                        "matchAny": False,
                        "limit": 200,
                    },
                },
                {
                    "name": "Alarmlar",
                    "datasource": DS,
                    "enable": True,
                    "iconColor": "orange",
                    "expr": 'ALERTS{alertstate="firing",team="vectordb",namespace="$tenant"} or '
                    'ALERTS{alertstate="firing",team="vectordb",k8s_namespace_name="$tenant"}',
                    "titleFormat": "{{alertname}}",
                    "textFormat": "{{severity}}",
                    "step": "60s",
                },
                {
                    # Litmus deneyi sürerken grafikte mor alan (T1.8)
                    "name": "Chaos deneyleri",
                    "datasource": DS,
                    "enable": True,
                    "iconColor": "purple",
                    "expr": "max by (chaosresult_namespace, chaosresult_name, fault_name) "
                    "(litmuschaos_awaited_experiments) > 0",
                    "titleFormat": "Chaos: {{fault_name}}",
                    "textFormat": "{{chaosresult_namespace}} / {{chaosresult_name}} (Litmus)",
                    "step": "30s",
                },
            ]
        },
        "panels": L.panels,
    }


def render() -> str:
    return json.dumps(build(), ensure_ascii=False, indent=1) + "\n"


if __name__ == "__main__":
    check_only = "--check" in sys.argv
    text = render()
    if check_only:
        if not OUT.exists() or OUT.read_text() != text:
            print(f"{OUT} güncel değil: make dashboards", file=sys.stderr)
            sys.exit(1)
        sys.exit(0)
    OUT.write_text(text)
    print(f"{OUT} yazıldı")
