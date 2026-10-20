"""HTTP arayüzü: Alertmanager webhook'u, raporlar ve etiketleme formu."""

from __future__ import annotations

import asyncio
import html
import json
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from typing import Any
from urllib.parse import parse_qs

from fastapi import FastAPI, HTTPException, Request
from fastapi.encoders import jsonable_encoder
from fastapi.responses import HTMLResponse, PlainTextResponse, Response
from prometheus_client import CONTENT_TYPE_LATEST, generate_latest
from pydantic import ValidationError

from sre_agent.decisions import render as decision_render
from sre_agent.graph import render as investigation_render
from sre_agent.report import to_markdown
from sre_agent.service.runner import ReportRunner
from sre_agent.store import Label
from sre_agent.taxonomy import load as load_taxonomy

MAX_ALERTS = 200  # tek webhook'ta işlenecek en fazla alarm
MAX_FORM_BYTES = 8192


def _summary(r: Any) -> dict[str, Any]:
    return {
        "id": r.id,
        "alert": r.alert.name,
        "labels": r.alert.labels,
        "generated_at": r.generated_at.isoformat(),
        "anomalies": [e.check for e in r.anomalies],
        "blind_spots": list(r.blind_spots),
        "packs": list(r.packs),
        "annotation_id": r.annotation_id,
    }


def _row_summary(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": row["id"],
        "alert": row["alertname"],
        "labels": row["labels"],
        "generated_at": row["generated_at"].isoformat(),
        "resolved_at": row["resolved_at"].isoformat() if row["resolved_at"] else None,
        "anomalies": row["anomalies"],
        "blind_spots": row["blind_spots"],
        "packs": row.get("packs") or [],
        "annotation_id": row["annotation_id"],
        "label_category": row["label_category"],
    }


def _label_page(
    report_id: str,
    alert: str,
    current: dict[str, Any] | None,
    message: str = "",
    notes: dict[str, list[tuple[str, str]]] | None = None,
    decisions_html: str = "",
) -> str:
    """Etiketleme formu. Bütün değişken içerik kaçışlanır (alarm etiketleri güvenilmez veri).

    Sistemin kararları yalnız etiket kaydedildikten sonra gösterilir (`decisions_html`):
    önceden görmek etiketi modele doğru çeker ve kalibrasyon verisini bozar (ADR-028).
    """
    e = html.escape
    t = load_taxonomy()
    group_name = {g.id: g.name for g in t.groups}
    selected = (current or {}).get("label_category")

    def option(value: str, text: str) -> str:
        mark = " selected" if value == selected else ""
        return f'<option value="{e(value)}"{mark}>{e(text)}</option>'

    options = "".join(
        option(c.id, f"{group_name[c.group]} / {c.name} — {c.definition[:90]}")
        for c in t.categories
    ) + option("unknown", "Bilinmiyor — kanıt yetersiz / belirlenemedi")
    names = {c.id: c.name for c in t.categories}
    note_items = "".join(
        f"<li><b>{e(names.get(cat, cat))}</b> ({e(pack)}): {e(text)}</li>"
        for cat, items in (notes or {}).items()
        for pack, text in items
    )
    notes_html = f'<ul class="notes">{note_items}</ul>' if note_items else ""
    root = e((current or {}).get("label_root_cause") or "")
    by = e((current or {}).get("labeled_by") or "")
    rid = e(report_id)
    msg = f'<p class="msg">{e(message)}</p>' if message else ""
    return f"""<!doctype html><html lang="tr"><head><meta charset="utf-8">
<title>Etiket — {e(alert)}</title>
<style>
body{{font-family:system-ui,sans-serif;max-width:760px;margin:2rem auto;padding:0 1rem}}
label{{display:block;margin-top:1rem;font-weight:600}}
select,textarea,input{{width:100%;padding:.4rem;font-size:1rem}}
button{{margin-top:1rem;padding:.5rem 1.2rem;font-size:1rem}}
.msg{{padding:.6rem;background:#e7f6e7}}
.notes{{font-size:.9rem;color:#444;padding-left:1.2rem}}
.dec{{border-collapse:collapse;font-size:.9rem;margin-top:.5rem}}
.dec td,.dec th{{border:1px solid #ccc;padding:.25rem .5rem;text-align:left}}
</style></head>
<body><h2>Etiket: {e(alert)}</h2>
<p>Olayın <b>gerçek nedeni</b> neydi? Bu cevap (kanıt → kategori) SRE Agent'ı ölçmek
ve karar modelini eğitmek için kullanılır.
Rapor: <a href="/reports/{rid}.md">/reports/{rid}.md</a></p>
{msg}
<form method="post" action="/reports/{rid}/label">
<label for="category">Kategori</label>
<select id="category" name="category" required>{options}</select>
{notes_html}
<label for="root_cause">Kök neden (bir-iki cümle)</label>
<textarea id="root_cause" name="root_cause" rows="3" required minlength="3"
 maxlength="1000">{root}</textarea>
<label for="labeled_by">Etiketleyen</label>
<input id="labeled_by" name="labeled_by" required maxlength="100" value="{by}">
<button type="submit">Kaydet</button></form>{decisions_html}</body></html>"""


async def _read_label(request: Request) -> Label:
    body = await request.body()
    if len(body) > MAX_FORM_BYTES:
        raise HTTPException(413)
    form = {k: v[0].strip() for k, v in parse_qs(body.decode("utf-8")).items()}
    valid = {c.id for c in load_taxonomy().categories} | {"unknown"}
    if form.get("category") not in valid:
        raise HTTPException(422, "geçersiz kategori")
    try:
        return Label(
            category=form["category"],
            root_cause=form.get("root_cause", ""),
            labeled_by=form.get("labeled_by", ""),
        )
    except ValidationError as exc:
        raise HTTPException(422, "eksik ya da hatalı alan") from exc


def _incident_page(
    inc: dict[str, Any], message: str = "", notes: dict[str, list[tuple[str, str]]] | None = None
) -> str:
    """Olay etiket sayfası: üye raporlar listelenir, etiket hepsine uygulanır."""
    e = html.escape
    page = _label_page(inc["id"], f"olay {inc['id']}", inc, message, notes)
    page = page.replace(
        f'action="/reports/{e(inc["id"])}/label"', f'action="/incidents/{e(inc["id"])}/label"'
    )
    members = "".join(
        f'<li><a href="/reports/{e(m["id"])}.md">{e(m["id"])}</a> '
        f"{e(str(m['generated_at'])[:16])} {e(m['alertname'])} {e(m.get('namespace') or '')}</li>"
        for m in inc.get("reports", [])
    )
    nss = ", ".join(inc.get("namespaces") or [])
    info = (
        f"<p><b>{inc['report_count']} rapor</b>, namespace: {e(nss)}"
        f"; kayıp node: {e(', '.join(inc.get('lost_nodes') or []) or 'yok')}"
        f"; ortak kanıt: {e(', '.join(inc.get('common_anomalies') or []))}. "
        "Etiket bütün raporlara uygulanır.</p>"
        f"<details><summary>Raporlar</summary><ul>{members}</ul></details>"
    )
    return page.replace("<form ", info + "<form ", 1)


def create_app(runner: ReportRunner) -> FastAPI:
    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncIterator[None]:
        task = asyncio.create_task(runner.maintain()) if runner.store else None
        yield
        if task:
            task.cancel()

    app = FastAPI(title="sre-agent", docs_url=None, redoc_url=None, lifespan=lifespan)

    @app.get("/healthz")
    async def healthz() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/metrics")
    async def metrics() -> Response:
        return Response(generate_latest(), media_type=CONTENT_TYPE_LATEST)

    @app.post("/alerts", status_code=202)
    async def alerts(payload: dict[str, Any]) -> dict[str, Any]:
        alerts = payload.get("alerts") or []
        if len(alerts) > MAX_ALERTS:
            raise HTTPException(413, "çok fazla alarm")
        accepted = runner.submit(payload)
        # Alertmanager bildirimi (gruplama görünür olsun diye): tek satır JSON.
        print(
            json.dumps(
                {
                    "event": "webhook",
                    "groupKey": payload.get("groupKey"),
                    "status": payload.get("status"),
                    "groupLabels": payload.get("groupLabels") or {},
                    "alertCount": len(alerts),
                    "alerts": [(a.get("labels") or {}) for a in alerts],
                    "accepted": len(accepted),
                },
                ensure_ascii=False,
            ),
            flush=True,
        )
        return {"accepted": accepted}

    @app.get("/reports")
    async def reports(include_test: bool = False, limit: int = 50) -> list[dict[str, Any]]:
        limit = max(1, min(limit, 500))
        if runner.store is not None:
            # Depo varsa sessizce boş bellek listesine düşülmez: eksik liste yanıltır.
            try:
                return [_row_summary(r) for r in await runner.store.recent(limit, include_test)]
            except Exception as exc:
                raise HTTPException(503, "rapor deposuna erişilemiyor") from exc
        rows = [
            _summary(r)
            for r in reversed(runner.reports.values())
            if include_test or r.alert.labels.get("test") != "true"
        ]
        return rows[:limit]

    @app.get("/reports/{report_id}.md", response_class=PlainTextResponse)
    async def report_md(report_id: str) -> str:
        report = await runner.find(report_id)
        if report is None:
            raise HTTPException(404)
        md = to_markdown(report) + decision_render.markdown(await runner.find_decisions(report_id))
        if runner.store is not None and report.incident_id:
            inc = await runner.store.get_incident(report.incident_id)
            mode = (inc or {}).get("investigation_mode")
            md = investigation_render.attach(md, (inc or {}).get("investigation"), mode)
        if runner.store is not None:
            row = await runner.store.get(report_id)
            if row and row["label_category"]:
                md += (
                    f"\n## Etiket\n\n- **Kategori:** {row['label_category']}\n"
                    f"- **Kök neden:** {row['label_root_cause']}\n"
                    f"- **Etiketleyen:** {row['labeled_by']} ({row['labeled_at']:%Y-%m-%d %H:%M})\n"
                )
            md += f"\nEtiketlemek için: /reports/{report_id}/label\n"
        return md

    @app.get("/reports/{report_id}/label", response_class=HTMLResponse)
    async def label_form(report_id: str) -> str:
        if runner.store is None:
            raise HTTPException(503, "kalıcı depo yok")
        row = await runner.store.get(report_id)
        if row is None:
            raise HTTPException(404)
        return _label_page(report_id, row["alertname"], row, notes=runner.registry.category_notes)

    @app.post("/reports/{report_id}/label", response_class=HTMLResponse)
    async def label_submit(report_id: str, request: Request) -> str:
        if runner.store is None:
            raise HTTPException(503, "kalıcı depo yok")
        label = await _read_label(request)
        if not await runner.store.set_label(report_id, label, datetime.now(UTC)):
            raise HTTPException(404)
        row = await runner.store.get(report_id)
        decisions = await runner.find_decisions(report_id)
        return _label_page(
            report_id,
            row["alertname"] if row else "",
            row,
            "Etiket kaydedildi.",
            notes=runner.registry.category_notes,
            decisions_html=decision_render.html_table(decisions, label.category),
        )

    @app.get("/incidents/{incident_id}")
    async def incident_json(incident_id: str) -> dict[str, Any]:
        if runner.store is None:
            raise HTTPException(503, "kalıcı depo yok")
        inc = await runner.store.get_incident(incident_id)
        if inc is None:
            raise HTTPException(404)
        out: dict[str, Any] = jsonable_encoder(inc)
        return out

    @app.get("/incidents/{incident_id}/label", response_class=HTMLResponse)
    async def incident_label_form(incident_id: str) -> str:
        if runner.store is None:
            raise HTTPException(503, "kalıcı depo yok")
        inc = await runner.store.get_incident(incident_id)
        if inc is None:
            raise HTTPException(404)
        return _incident_page(inc, notes=runner.registry.category_notes)

    @app.post("/incidents/{incident_id}/label", response_class=HTMLResponse)
    async def incident_label_submit(incident_id: str, request: Request) -> str:
        if runner.store is None:
            raise HTTPException(503, "kalıcı depo yok")
        label = await _read_label(request)
        if await runner.store.get_incident(incident_id) is None:
            raise HTTPException(404)
        n = await runner.store.label_incident(incident_id, label, datetime.now(UTC))
        inc = await runner.store.get_incident(incident_id)
        assert inc is not None
        return _incident_page(
            inc, f"Olay etiketlendi ({n} rapor).", notes=runner.registry.category_notes
        )

    @app.get("/reports/{report_id}")
    async def report_json(report_id: str) -> dict[str, Any]:
        report = await runner.find(report_id)
        if report is None:
            raise HTTPException(404)
        out = report.model_dump(mode="json")
        out["decisions"] = await runner.find_decisions(report_id)
        return out

    return app
