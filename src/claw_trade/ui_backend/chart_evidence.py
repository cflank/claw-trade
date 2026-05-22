from __future__ import annotations

import re
from datetime import UTC, datetime
from typing import Any
from uuid import uuid4


def _now_iso() -> str:
    return datetime.now(UTC).isoformat()


def get_report_chart_evidence(
    report_id: str,
    *,
    chart_assets: list[dict[str, Any]] | None = None,
    data_gaps: list[dict[str, Any]] | None = None,
    export_result: dict[str, Any] | None = None,
    markdown: str = "",
) -> dict[str, Any]:
    merged: dict[str, dict[str, Any]] = {}

    for title in _extract_markdown_image_titles(markdown):
        key = _normalize_key(title)
        merged[key] = {
            "title": title,
            "chartType": "other",
            "status": "missing",
            "reason": "本次报告未生成该图表。",
            "capturedAt": _now_iso(),
        }

    all_assets = list(chart_assets or [])
    if export_result and isinstance(export_result.get("chart_assets"), list):
        all_assets.extend(export_result["chart_assets"])

    for asset in all_assets:
        title = str(asset.get("title") or asset.get("chart_title") or "图表").strip()
        key = _normalize_key(title)
        status = str(asset.get("status") or "").lower()
        mapped_status = "ready" if status == "ready" else "failed" if status == "failed" else "missing"
        merged[key] = {
            "title": title,
            "chartType": str(asset.get("chartType") or asset.get("chart_type") or "other"),
            "status": mapped_status,
            "reason": str(asset.get("reason") or ""),
            "capturedAt": str(asset.get("capturedAt") or asset.get("captured_at") or _now_iso()),
        }

    for gap in data_gaps or ():
        title = str(gap.get("title") or gap.get("chart_title") or gap.get("chartType") or "图表").strip()
        key = _normalize_key(title)
        reason = str(gap.get("userMessage") or gap.get("reason") or "图表缺失，请检查数据源。")
        current = merged.get(key, {})
        merged[key] = {
            "title": title,
            "chartType": str(current.get("chartType") or gap.get("chartType") or "other"),
            "status": "missing" if current.get("status") != "failed" else "failed",
            "reason": reason,
            "capturedAt": str(gap.get("occurredAt") or gap.get("capturedAt") or _now_iso()),
        }

    items = []
    statuses: list[str] = []
    for value in merged.values():
        status = str(value["status"])
        statuses.append(status)
        user_message = _chart_status_message(status, str(value.get("reason") or ""))
        items.append(
            {
                "id": f"chart_{uuid4().hex}",
                "reportId": report_id,
                "chartType": value["chartType"],
                "title": value["title"],
                "status": status,
                "userMessage": user_message,
                "capturedAt": value["capturedAt"],
            }
        )

    if statuses and all(status == "ready" for status in statuses):
        summary = "ready"
    elif any(status == "ready" for status in statuses):
        summary = "partial"
    else:
        summary = "missing"
    return {"reportId": report_id, "items": items, "summary": summary}


def _extract_markdown_image_titles(markdown: str) -> list[str]:
    if not markdown:
        return []
    out: list[str] = []
    for alt, _url in re.findall(r"!\[(.*?)\]\((.*?)\)", markdown):
        title = alt.strip()
        if title:
            out.append(title)
    return out


def _normalize_key(title: str) -> str:
    return re.sub(r"\s+", "", title.lower())


def _chart_status_message(status: str, reason: str) -> str:
    if status == "ready":
        return "图表已生成，可查看。"
    if status == "failed":
        return reason or "图表生成失败，请稍后重试。"
    return reason or "本次报告未生成该图表。"
