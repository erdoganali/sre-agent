"""Graph'ın kontrol akışı: saf, deterministik fonksiyonlar (ADR-001). Model burada karar vermez;
yalnız state'teki tipli çıktılara (Evaluation, bütçe) bakılır."""

from __future__ import annotations

import difflib
from typing import Any

from sre_agent.ports import Capability

MAX_DEEP_DIVE = 2

# Specialist → baktığı yetenekler (araca değil yeteneğe göre; ADR-005)
SPECIALISTS: dict[str, tuple[str, ...]] = {
    "metrics": (Capability.METRICS,),
    "logs": (Capability.LOGS,),
    "traces": (Capability.TRACES,),
    "k8s": (Capability.K8S, Capability.CHANGE),
    "product": (),  # ürün paketi kontrolleri; kanıtı paket kontrollerinden gelir
}

# Model specialist adını serbest yazabilir ("k8s specialist", "log specialist", "kubernetes")
ALIASES = {
    "kubernetes": "k8s",
    "kube": "k8s",
    "cluster": "k8s",
    "metric": "metrics",
    "prometheus": "metrics",
    "log": "logs",
    "loki": "logs",
    "trace": "traces",
    "tempo": "traces",
    "jaeger": "traces",
    "product": "product",
}


def normalize_specialist(name: str, product_aliases: frozenset[str] = frozenset()) -> str | None:
    """Serbest yazılmış specialist adını kanonik ada çevirir; bulunamazsa None.

    Ürün specialist'inin adları (paket adı, ürünün bileşen adları) çekirdekte değil paketten gelir.
    """
    aliases = ALIASES | {a.lower(): "product" for a in product_aliases}
    low = name.lower().replace("-", " ").replace("_", " ")
    for word in low.split():
        if word in SPECIALISTS:
            return word
        if word in aliases:
            return aliases[word]
    head = low.split()[0] if low.split() else low
    close = difflib.get_close_matches(head, list(SPECIALISTS) + list(aliases), n=1, cutoff=0.75)
    if close:
        return close[0] if close[0] in SPECIALISTS else aliases[close[0]]
    return None


def select_specialists(
    evidence: dict[str, list[dict[str, Any]]], node_loss: bool, product: bool = False
) -> list[str]:
    """Anomali kanıtı olan yeteneklerin specialist'leri; node kaybında k8s ve product (ürün
    topology'si: kaybedilen node'daki bileşenler) her zaman açılır. Paket alarmında (product)
    product da açılır: ürün kontrolleri ve bilgi tabanı ikinci bağımsız görüş olur, tek specialist
    kalıp çapraz doğrulama atlanmaz. Hiç kanıt yoksa metrics ve k8s."""
    chosen = [name for name in SPECIALISTS if evidence.get(name)]
    if node_loss:
        chosen += [n for n in ("k8s", "product") if n not in chosen]
    if product and "product" not in chosen:
        chosen.append("product")
    return chosen or ["metrics", "k8s"]


def route_after_evaluate(
    state: dict[str, Any], product_aliases: frozenset[str] = frozenset()
) -> str:
    budget = state.get("budget") or {}
    if budget.get("exhausted") or budget.get("wrap_up"):
        return "synthesize"
    ev = state.get("evaluation") or {}
    if ev.get("confidence") == "high" and ev.get("cross_validated"):
        return "synthesize"
    # deep dive yalnız tanınan bir specialist'e en az bir soru varsa (yoksa graph boşa döner)
    if state.get("deep_dive_round", 0) < MAX_DEEP_DIVE and deep_dive_tasks(state, product_aliases):
        return "deep_dive"
    return "synthesize"


def deep_dive_tasks(
    state: dict[str, Any], product_aliases: frozenset[str] = frozenset()
) -> list[dict[str, str]]:
    """Evaluate'in önerdiği deep dive'lar; adlar normalize, bilinmeyen ve tekrar edenler atılır."""
    seen: set[str] = set()
    out = []
    for dd in (state.get("evaluation") or {}).get("suggested_deep_dives", []):
        name = normalize_specialist(str(dd.get("specialist", "")), product_aliases)
        if name and name not in seen:
            seen.add(name)
            out.append({"specialist": name, "question": str(dd.get("question", ""))[:500]})
    return out
