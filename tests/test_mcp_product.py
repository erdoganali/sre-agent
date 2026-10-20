"""MCP product sunucusu ve topology (T3.3): paket haritası, izinli alanlar, olay anındaki etki."""

from __future__ import annotations

import builtins
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
from mcp import Client
from pydantic import ValidationError

from sre_agent.mcp import product
from sre_agent.packs import CheckRegistry, load_pack
from sre_agent.ports import Capability, Sample
from sre_agent.topology import Topology, pick

PACK = load_pack(Path("packs/vectordb"))
T0 = datetime(2026, 10, 9, 14, 34, tzinfo=UTC)
PASSWORD = "Milvus-r00t-parola"

MILVUS_CR = {
    "metadata": {"name": "cus-acme", "namespace": "acme"},
    "spec": {
        "components": {
            "image": "milvus:v2.6",
            "proxy": {"replicas": 1},
            "dataNode": {"replicas": 1},
        },
        "config": {
            "common": {"security": {"rootPassword": PASSWORD}},
            "minio": {
                "bucketName": "vectordb-data-gke-acme",
                "rootPath": "data",
                "secretAccessKey": PASSWORD,
            },
        },
        "dependencies": {
            "etcd": {
                "endpoints": ["cus-acme-etcd-0.cus-acme-etcd-headless:2379"],
                "external": True,
            },
            "storage": {
                "type": "S3",
                "endpoint": "storage.googleapis.com",
                "secretRef": "milvus-bucket-key",
            },
        },
    },
    "status": {"status": "Healthy", "conditions": [{"type": "MilvusReady", "status": "True"}]},
}

PODS = {
    "node-ugj2": [
        "cus-acme-etcd-0",
        "cus-acme-milvus-datanode-5868b75464-v8dmt",
        "cus-acme-milvus-querynode-0-6947bcb477-s98s4",
    ],
    "node-l05r": ["cus-acme-etcd-1", "cus-acme-etcd-2"],
    "node-d65v": [
        "cus-acme-milvus-proxy-6c99755b44-h9zrs",
        "cus-acme-milvus-mixcoord-6bbbf99d59-b4fzh",
    ],
}


class FakeHistory:
    """ugj2 olay anında NotReady ve 107 dk sonra yeniden oluşturuldu; d65v sağlam."""

    async def query(self, promql: str, at: datetime) -> list[Sample]:
        if "kube_pod_info" in promql:
            return [Sample({"pod": p, "node": n}, 1.0) for n, ps in PODS.items() for p in ps]
        if "kube_node_status_condition" in promql:
            return [Sample({"node": n}, 0.0 if n == "node-ugj2" else 1.0) for n in PODS]
        if "kube_node_created" in promql:
            base = {n: T0 - timedelta(days=3) for n in PODS}
            if at > T0:
                base["node-ugj2"] = T0 + timedelta(minutes=107)
            return [Sample({"node": n}, t.timestamp()) for n, t in base.items()]
        return []

    async def query_range(self, *a: Any) -> list[Any]:
        return []


class FakeK8s:
    async def list(self, *a: Any, **k: Any) -> list[Any]:
        return []

    async def list_custom(
        self, group: str, kind: str, namespace: str | None = None
    ) -> builtins.list[Any]:
        return [MILVUS_CR] if (group, kind) == ("milvus.io", "Milvus") else []

    async def pod_logs(self, *a: Any, **k: Any) -> builtins.list[str]:
        return []


def server() -> Any:
    reg = CheckRegistry([PACK])
    checks = {n: reg.checks[n]() for n in sorted(reg.product_checks)}
    ports = {Capability.METRICS: FakeHistory(), Capability.K8S: FakeK8s()}
    assert PACK.topology is not None
    return product.build(checks, ports, {PACK.manifest.name: PACK.topology})


async def call(srv: Any, tool: str, **args: Any) -> tuple[bool, str]:
    async with Client(srv) as c:
        r = await c.call_tool(tool, args)
        return bool(r.is_error), "\n".join(getattr(x, "text", "") for x in r.content)


# ---- paket haritası


def test_pack_topology_matches_architecture() -> None:
    t = PACK.topology
    assert t is not None
    milvus = sorted(set(t.components) - {"etcd", "metrics-agent"})
    assert t.dependents("etcd") == milvus  # bütün Milvus bileşenleri etcd'ye bağlı
    assert t.dependents("telemetry_gateway") == ["metrics-agent"]  # ajan Milvus'a değil hatta bağlı
    assert "etcd" in t.dependents("milvus_operator")
    assert {"streamingnode", "datanode", "querynode"} <= set(t.dependents("object_storage"))
    assert "querynode" in t.components["proxy"].depends
    assert t.component_of("cus-acme-etcd-0") == "etcd"
    assert t.component_of("cus-acme-milvus-streamingnode-7bd9d76f9c-qzds4") == "streamingnode"


def test_topology_rejects_unknown_dependency() -> None:
    with pytest.raises(ValidationError, match="bilinmeyen bağımlılık"):
        Topology.model_validate({"components": {"a": {"pods": "a", "depends": ["yok"]}}})


def test_pick_returns_only_allowed_fields() -> None:
    assert PACK.topology and PACK.topology.live
    got = pick(MILVUS_CR, PACK.topology.live.fields)
    flat = repr(got)
    assert PASSWORD not in flat and "secretRef" not in flat and "rootPassword" not in flat
    assert got["spec.config.minio.bucketName"] == "vectordb-data-gke-acme"
    assert got["spec.dependencies.etcd.endpoints"] == [
        "cus-acme-etcd-0.cus-acme-etcd-headless:2379"
    ]
    assert got["spec.components.proxy.replicas"] == 1 and got["status.status"] == "Healthy"


# ---- product sunucusu


async def test_tool_list_and_read_only() -> None:
    async with Client(server()) as c:
        got = {t.name: t for t in (await c.list_tools()).tools}
    assert {n: t.input_schema.get("required", []) for n, t in got.items()} == {
        "list_checks": [],
        "run_check": ["name", "namespace"],
        "topology": ["namespace"],
    }
    assert all(t.annotations and t.annotations.read_only_hint for t in got.values())


async def test_topology_links_lost_node_to_etcd_and_dependents() -> None:
    err, text = await call(server(), "topology", namespace="acme", at="2026-10-09T14:34:00Z")
    assert not err and PASSWORD not in text and "secretRef" not in text
    lost = text[text.index("Kaybedilen node'lar") :]
    assert "**node-ugj2** (NotReady oldu; olaydan +107 dk sonra yeniden oluşturuldu)" in lost
    assert "etcd (cus-acme-etcd-0)" in lost
    assert (
        "bağlı bileşenler (etcd): attu, datanode, mixcoord, proxy, querynode, streamingnode" in lost
    )
    assert "node-d65v" not in lost and "node-l05r" not in lost
    etcd_row = next(r for r in text.splitlines() if r.startswith("| etcd |"))
    assert "| 3 |" in etcd_row and "evet: node-ugj2" in etcd_row
    assert "vectordb-data-gke-acme" in text  # canlı kaynaktan izinli alan


async def test_run_check_runs_pack_check_and_rejects_others() -> None:
    err, text = await call(server(), "run_check", name="milvus_health", namespace="acme")
    assert not err and "milvus_health [ok]" in text and "1 Milvus Healthy" in text
    err, text = await call(server(), "run_check", name="pod_restarts", namespace="acme")
    assert err and "bilinmeyen kontrol" in text


async def test_list_checks_shows_pack_checks() -> None:
    err, text = await call(server(), "list_checks")
    assert not err and "milvus_health" in text and "pod_restarts" not in text


async def test_topology_highlights_share_on_lost_nodes() -> None:
    err, text = await call(server(), "topology", namespace="acme", at="2026-10-09T14:34:00Z")
    assert not err
    head = text.split("| bileşen |")[0]
    assert "etcd: 1/3 pod kaybedilen node'larda;" in head
    assert "datanode: 1/1 pod kaybedilen node'larda (yarıdan fazlası)" in head
