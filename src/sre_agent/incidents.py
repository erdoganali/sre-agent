"""Olay gruplama (T3.1): aynı nedenden gelen raporlar tek olayda toplanır.

Bir alarm tek bir kök nedenin yalnız bir belirtisidir: spot node kaybında tenant'ın her
bileşeni ayrı alarm verir (2026-10: 214 rapor ≈ 29 olay). Investigation, etiket ve ölçüm olay
düzeyinde yapılır. Kurallar deterministiktir (ADR-001), model kullanılmaz:

  1. Ortak node kaybı: rapor ile olayın bir üyesi, aralarında en çok WINDOW olan iki pencerede
     aynı node kaybını görmüş (node_recreated). Kayıp "node + yeniden oluşma anı" ile tanınır:
     aynı node'un 19:53 ve 20:14'teki kayıpları ayrı olaylardır. Namespace fark etmez (aynı
     dalga tek olay), ama namespace'i olmayan alarm bu yolla bağlanmaz: onun node kanıtı
     kümenin tamamına bakar, bağ kurmak için zayıftır.
  2. Aynı namespace ve benzer kanıt: WINDOW içinde, aynı namespace'te, anomali kümesi Jaccard
     benzerliği >= SIMILARITY.

Olaylar alarmın sahibine (`team` etiketi) göre ayrılır: aynı anda node kaybı yaşayan bir lab
deneyi (team=sre-lab) ile bir ürün olayı aynı olay değildir; sahipleri ve
etiketleri farklıdır (2026-10-08 19:50: nx71 bir saatte 9 kez yeniden oluşurken lab
senaryoları da o node'daydı). İkisi de yoksa yeni olay açılır. WINDOW boyunca yeni üyesi
gelmeyen olay kapanmış sayılır.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from datetime import datetime, timedelta

from sre_agent.checks import Status
from sre_agent.report import Report

WINDOW = timedelta(minutes=30)
SIMILARITY = 0.6
NODE_CHECK = "node_recreated"


@dataclass(frozen=True)
class Signature:
    """Gruplama için raporun özeti (rapor, DB satırı ya da geriye dönük hesaptan)."""

    report_id: str
    at: datetime  # raporun yazıldığı an
    namespace: str | None
    anomalies: frozenset[str]
    lost_nodes: frozenset[str]
    team: str | None = None  # alarmın sahibi; farklı sahipli raporlar aynı olaya girmez

    @classmethod
    def of(cls, report: Report) -> Signature:
        lost: set[str] = set()
        for e in report.evidence:
            if e.check == NODE_CHECK and e.status is Status.ANOMALY and isinstance(e.data, list):
                lost |= {
                    f"{r['node']}@{r['created_at']}" if r.get("created_at") else str(r["node"])
                    for r in e.data
                    if isinstance(r, dict) and "node" in r
                }
        return cls(
            report_id=report.id,
            at=report.generated_at,
            namespace=report.alert.labels.get("namespace"),
            anomalies=frozenset(e.check for e in report.anomalies),
            lost_nodes=frozenset(lost),
            team=report.alert.labels.get("team"),
        )


@dataclass
class Incident:
    id: str
    members: list[Signature] = field(default_factory=list)

    @property
    def opened_at(self) -> datetime:
        return self.members[0].at

    @property
    def last_seen(self) -> datetime:
        return max(m.at for m in self.members)

    @property
    def namespaces(self) -> list[str]:
        return sorted({m.namespace for m in self.members if m.namespace})

    @property
    def lost_nodes(self) -> list[str]:
        return sorted(set().union(*(m.lost_nodes for m in self.members)))

    @property
    def common_anomalies(self) -> list[str]:
        """Üyelerin en az yarısında görülen anomaliler (olayın ortak kanıtı)."""
        counts: dict[str, int] = {}
        for m in self.members:
            for a in m.anomalies:
                counts[a] = counts.get(a, 0) + 1
        return sorted(a for a, n in counts.items() if n * 2 >= len(self.members))


def _jaccard(a: frozenset[str], b: frozenset[str]) -> float:
    union = a | b
    return len(a & b) / len(union) if union else 0.0


def reason(sig: Signature, incident: Incident) -> str | None:
    """Rapor bu olaya katılır mı; katılırsa nedeni (yoksa None)."""
    if sig.at - incident.last_seen > WINDOW:
        return None
    for m in reversed(incident.members):
        if abs(sig.at - m.at) > WINDOW or sig.team != m.team:
            continue
        common = sig.lost_nodes & m.lost_nodes if sig.namespace and m.namespace else set()
        if common:
            return f"ortak node kaybı: {', '.join(sorted(common))}"
        if sig.namespace and sig.namespace == m.namespace:
            sim = _jaccard(sig.anomalies, m.anomalies)
            if sim >= SIMILARITY:
                return f"aynı namespace ({sig.namespace}), kanıt benzerliği {sim:.2f}"
    return None


def new_id(sig: Signature) -> str:
    return hashlib.sha1(f"incident:{sig.report_id}".encode()).hexdigest()[:12]


def assign(sig: Signature, open_incidents: list[Incident]) -> tuple[Incident, str]:
    """Raporu uygun açık olaya ekler ya da yeni olay açar: (olay, neden)."""
    for inc in sorted(open_incidents, key=lambda i: i.last_seen, reverse=True):
        why = reason(sig, inc)
        if why:
            inc.members.append(sig)
            return inc, why
    inc = Incident(id=new_id(sig), members=[sig])
    open_incidents.append(inc)
    return inc, "yeni olay"


def group(signatures: list[Signature]) -> list[Incident]:
    """Geçmiş raporları zaman sırasıyla gruplar (geriye dönük doldurma ve testler için)."""
    incidents: list[Incident] = []
    for sig in sorted(signatures, key=lambda s: s.at):
        live = [i for i in incidents if sig.at - i.last_seen <= WINDOW]
        inc, _ = assign(sig, live)
        if inc not in incidents:
            incidents.append(inc)
    return incidents
