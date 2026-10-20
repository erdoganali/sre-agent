"""Entegrasyon testleri için ortak yardımcılar (scripts/lib/it-env.sh ortamı)."""

from __future__ import annotations

import os

import pytest
from lightkube import AsyncClient, KubeConfig

ENV = ("SRE_IT_PROMETHEUS_URL", "SRE_IT_LOKI_URL", "SRE_IT_TEMPO_URL", "SRE_IT_K8S_SERVER")


def require_env(*extra: str) -> None:
    if not all(os.environ.get(k) for k in (*ENV, *extra)):
        pytest.skip("entegrasyon ortamı yok (make test-integration)", allow_module_level=True)


def k8s_client() -> AsyncClient:
    """Read-only ServiceAccount'un kısa ömürlü token'ıyla Kubernetes istemcisi."""
    config = KubeConfig.from_dict(
        {
            "clusters": [
                {
                    "name": "gke",
                    "cluster": {
                        "server": os.environ["SRE_IT_K8S_SERVER"],
                        "certificate-authority-data": os.environ["SRE_IT_K8S_CA_DATA"],
                    },
                }
            ],
            "users": [{"name": "sa", "user": {"token": os.environ["SRE_IT_K8S_TOKEN"]}}],
            "contexts": [{"name": "sa", "context": {"cluster": "gke", "user": "sa"}}],
            "current-context": "sa",
        }
    )
    return AsyncClient(config)
