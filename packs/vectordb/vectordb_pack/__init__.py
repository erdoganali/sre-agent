"""VectorDB (Milvus) domain paketi."""

from sre_agent.packs import PackContent

from .checks import (
    MILVUS_SIGNALS,
    MilvusConnections,
    MilvusCoordinators,
    MilvusEtcd,
    MilvusHealth,
    MilvusObjectStorage,
    MilvusQueryNodeMemoryQuota,
    MilvusSegmentBacklog,
)

PACK = PackContent(
    checks=(
        MilvusQueryNodeMemoryQuota,
        MilvusHealth,
        MilvusCoordinators,
        MilvusEtcd,
        MilvusObjectStorage,
        MilvusSegmentBacklog,
        MilvusConnections,
    ),
    signals=MILVUS_SIGNALS,
    custom_kinds=(("milvus.io", "v1beta1", "Milvus", "milvuses"),),
)
