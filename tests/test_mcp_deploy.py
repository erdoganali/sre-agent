"""MCP kurulumu (T3.3, ADR-030): sunucu başına kimlik, token yalnız k8s/product'ta; başlatma."""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path
from typing import Any

import pytest
import yaml

from sre_agent.config import AppConfig
from sre_agent.mcp.__main__ import build_server, security

needs_helm = pytest.mark.skipif(shutil.which("helm") is None, reason="helm yok")


def render(chart: str, *args: str) -> list[dict[str, Any]]:
    out = subprocess.run(
        ["helm", "template", "sre-agent", chart, "-n", "sre-agent", *args],
        check=True,
        capture_output=True,
        text=True,
    ).stdout
    return [d for d in yaml.safe_load_all(out) if d]


@needs_helm
def test_each_server_has_own_identity_and_only_k8s_product_get_token() -> None:
    docs = render(
        "deploy/helm/sre-agent",
        "--set=image.tag=t",
        "--set-file=config=config/gke.yaml",
        "--set=mcp.enabled=true",
    )
    deps = {
        d["metadata"]["name"]: d["spec"]["template"]["spec"]
        for d in docs
        if d["kind"] == "Deployment" and "-mcp-" in d["metadata"]["name"]
    }
    assert sorted(deps) == [
        f"sre-agent-mcp-{s}" for s in sorted(["metrics", "logs", "traces", "k8s", "product"])
    ]
    for name, spec in deps.items():
        server = name.removeprefix("sre-agent-mcp-")
        assert spec["serviceAccountName"] == f"sre-agent-mcp-{server}"
        assert spec["automountServiceAccountToken"] is (server in ("k8s", "product"))
        c = spec["containers"][0]
        assert c["command"] == ["python", "-m", "sre_agent.mcp"] and c["args"][0] == server
        assert f"--allow-host={name}.sre-agent.svc" in c["args"]
        assert c["securityContext"]["readOnlyRootFilesystem"] is True
    own_sa = {d["metadata"]["name"] for d in docs if d["kind"] == "ServiceAccount"}
    assert own_sa == {"sre-agent-mcp-metrics", "sre-agent-mcp-logs", "sre-agent-mcp-traces"}


@needs_helm
def test_mcp_off_by_default() -> None:
    docs = render("deploy/helm/sre-agent", "--set=image.tag=t", "--set-file=config=config/gke.yaml")
    assert not [d for d in docs if "-mcp-" in d["metadata"]["name"]]


@needs_helm
def test_rbac_binds_read_role_only_to_agent_k8s_and_product() -> None:
    docs = render("deploy/helm/sre-agent-rbac")
    binding = next(d for d in docs if d["kind"] == "ClusterRoleBinding")
    assert [s["name"] for s in binding["subjects"]] == [
        "sre-agent-readonly",
        "sre-agent-mcp-k8s",
        "sre-agent-mcp-product",
    ]
    role = next(d for d in docs if d["kind"] == "ClusterRole")
    assert all("secrets" not in r["resources"] for r in role["rules"])
    assert all(set(r["verbs"]) == {"get", "list", "watch"} for r in role["rules"])


def test_telemetry_servers_build_without_kubernetes(tmp_path: Path) -> None:
    cfg = AppConfig(prometheus_url="http://p", loki_url="http://l", tempo_url="http://t")
    for name in ("metrics", "logs", "traces"):
        assert build_server(name, cfg, tmp_path).name == name


def test_missing_backend_address_stops_startup(tmp_path: Path) -> None:
    with pytest.raises(SystemExit, match="loki_url"):
        build_server("logs", AppConfig(), tmp_path)


def test_host_header_allow_list() -> None:
    s = security(["sre-agent-mcp-logs.sre-agent.svc"])
    assert s.enable_dns_rebinding_protection
    assert (
        "sre-agent-mcp-logs.sre-agent.svc:*" in s.allowed_hosts and "localhost:*" in s.allowed_hosts
    )
