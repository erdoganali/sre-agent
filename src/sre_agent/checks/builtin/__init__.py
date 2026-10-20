"""Çekirdekle gelen, ortamdan bağımsız kontroller."""

from sre_agent.checks import Check
from sre_agent.checks.builtin.application import CrashLogException, ErrorSpans, NewErrorSignatures
from sre_agent.checks.builtin.capacity import (
    CpuThrottling,
    EphemeralStorageUsage,
    GoldenSignalsBaseline,
    HpaSaturation,
    MemoryNearLimit,
    PvcUsage,
)
from sre_agent.checks.builtin.changes import (
    ArgoCdSync,
    HelmReleaseChanges,
    ManualChanges,
    RecentRollouts,
)
from sre_agent.checks.builtin.kafka import KafkaConsumerLag
from sre_agent.checks.builtin.network import (
    ConnectionErrors,
    DnsErrors,
    Ingress5xx,
    NetworkPolicyChanges,
    ProbeTimeouts,
)
from sre_agent.checks.builtin.nodes import NodeConditions, NodeEvents, NodeRecreated, PendingPods
from sre_agent.checks.builtin.pods import PodRestarts
from sre_agent.checks.builtin.security import AuthFailures, TlsCertificateExpiry

__all__ = [
    "BUILTIN_CHECKS",
    "ArgoCdSync",
    "AuthFailures",
    "ConnectionErrors",
    "CpuThrottling",
    "CrashLogException",
    "DnsErrors",
    "EphemeralStorageUsage",
    "ErrorSpans",
    "GoldenSignalsBaseline",
    "HelmReleaseChanges",
    "HpaSaturation",
    "Ingress5xx",
    "KafkaConsumerLag",
    "ManualChanges",
    "MemoryNearLimit",
    "NetworkPolicyChanges",
    "NewErrorSignatures",
    "NodeConditions",
    "NodeEvents",
    "NodeRecreated",
    "PendingPods",
    "PodRestarts",
    "ProbeTimeouts",
    "PvcUsage",
    "RecentRollouts",
    "TlsCertificateExpiry",
]

# Çekirdekle gelen kontroller (kayıt: sre_agent.packs.CheckRegistry).
BUILTIN_CHECKS: tuple[type[Check], ...] = (
    AuthFailures,
    ConnectionErrors,
    CpuThrottling,
    CrashLogException,
    DnsErrors,
    EphemeralStorageUsage,
    ErrorSpans,
    GoldenSignalsBaseline,
    HelmReleaseChanges,
    HpaSaturation,
    Ingress5xx,
    KafkaConsumerLag,
    ManualChanges,
    MemoryNearLimit,
    NetworkPolicyChanges,
    NewErrorSignatures,
    NodeConditions,
    NodeEvents,
    NodeRecreated,
    PendingPods,
    PodRestarts,
    ProbeTimeouts,
    PvcUsage,
    RecentRollouts,
    ArgoCdSync,
    TlsCertificateExpiry,
)
