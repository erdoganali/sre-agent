"""Specialist ölçümü (T3.4): doğru kanıt eşleşmesi, tablo ve özet."""

from __future__ import annotations

from pathlib import Path

from sre_agent.graph.specialist_eval import label_of, load_expectations, render, rows_for

EXP = load_expectations(Path("eval/specialist-expectations.yaml"))


def finding(
    evidence: list[tuple[str, str]], tools: list[str], error: str | None = None
) -> dict[str, object]:
    return {
        "evidence": [{"source": s, "summary": t} for s, t in evidence],
        "tools_used": tools,
        "suspects": ["hardware_problem"],
        "error": error,
    }


def test_expectations_cover_every_taxonomy_category_except_security() -> None:
    from sre_agent.taxonomy import load

    cats = {c.id for c in load().categories} - {"security", "planned_infrastructure_change"}
    assert cats <= set(EXP)


def test_rows_mark_found_missing_and_fallback() -> None:
    result = {
        "findings": {
            "k8s": finding(
                [("k8s.placement(...)", "ugj2 | NotReady oldu | +107 dk")], ["k8s.placement"]
            ),
            "metrics": finding([("metrics.query(...)", "restart 4")], ["metrics.query"]),
            "product": finding([], [], error="ModelError: 503"),
        }
    }
    rows = {r.specialist: r for r in rows_for("i1", "hardware_problem", result, EXP)}
    assert rows["k8s"].ok is True and rows["metrics"].ok is False and rows["product"].ok is None
    table = render(list(rows.values()), {"i1": {"category": "hardware_problem"}})
    assert "| i1 | hardware_problem | k8s | 1 | placement | ✓ |" in table
    assert "| product |" in table and "yedek" in table
    assert "| k8s | 1 | 1 |" in table and "Rapor kategorisi etiketle aynı: 1/1" in table


def test_label_is_majority_of_member_reports() -> None:
    recs = [{"label_category": "hardware_problem"}] * 2 + [{"label_category": "network"}]
    assert label_of(recs) == "hardware_problem"
