from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Sequence

from news_data_pack import TOOL_NAME, run_news_data_pack
from security import sanitize_error

SCHEMA_VERSION = "cn_a_news_integration_validate.v1"
EXPECTED_VISIBLE_TOOLS = ("news_news_data_pack",)
EXPECTED_SAMPLE = {
    "ticker": "600519",
    "company_name": "贵州茅台",
    "market": "CN_A",
    "start_date": "2026-04-30",
    "end_date": "2026-05-06",
}
EXPECTED_WORKER = "news_analyst"
EXPECTED_STAGE = "frontline"
EXPECTED_PACK_FILES = ("news_data_pack.json", "provider_attempts.json")
FORBIDDEN_DIRECTIONAL_TERMS = (
    "买入",
    "卖出",
    "持有",
    "增持",
    "减持",
    "目标价",
    "看涨",
    "看跌",
    "建仓",
    "平仓",
    "buy",
    "sell",
    "hold",
    "long",
    "short",
)


@dataclass
class ValidationContext:
    evidence_root: Path
    run_id: str
    call_id: str
    second_call_id: str | None
    stage: str
    worker_id: str
    openclaw_evidence_dir: Path | None
    visible_tools_evidence: Path | None
    partial_pack_json: Path | None
    report_path: Path
    sample: dict[str, str]


class _SanitizedArgumentParser(argparse.ArgumentParser):
    def error(self, message: str) -> None:
        raise ValueError(message)


def _build_parser() -> _SanitizedArgumentParser:
    parser = _SanitizedArgumentParser(description="CN_A news integration validation for 600519")
    subparsers = parser.add_subparsers(dest="command", required=True)

    run_tool = subparsers.add_parser("run-tool", help="Run news_news_data_pack then validate evidence")
    _add_common_arguments(run_tool)

    validate_existing = subparsers.add_parser(
        "validate-existing",
        help="Validate existing evidence without calling provider/tool",
    )
    _add_common_arguments(validate_existing)
    return parser


def _add_common_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--evidence-root", required=True)
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--call-id", required=True)
    parser.add_argument("--second-call-id")
    parser.add_argument("--stage", default=EXPECTED_STAGE)
    parser.add_argument("--worker-id", default=EXPECTED_WORKER)
    parser.add_argument("--ticker", default=EXPECTED_SAMPLE["ticker"])
    parser.add_argument("--company-name", default=EXPECTED_SAMPLE["company_name"])
    parser.add_argument("--market", default=EXPECTED_SAMPLE["market"])
    parser.add_argument("--start-date", default=EXPECTED_SAMPLE["start_date"])
    parser.add_argument("--end-date", default=EXPECTED_SAMPLE["end_date"])
    parser.add_argument("--openclaw-evidence-dir")
    parser.add_argument("--visible-tools-evidence")
    parser.add_argument("--partial-pack-json")
    parser.add_argument("--report-path")


def _resolve_context(args: argparse.Namespace) -> ValidationContext:
    evidence_root = Path(args.evidence_root).expanduser().resolve()
    openclaw_dir = (
        Path(args.openclaw_evidence_dir).expanduser().resolve()
        if args.openclaw_evidence_dir
        else None
    )
    visible_tools_evidence = (
        Path(args.visible_tools_evidence).expanduser().resolve()
        if args.visible_tools_evidence
        else None
    )
    partial_pack_json = (
        Path(args.partial_pack_json).expanduser().resolve() if args.partial_pack_json else None
    )
    call_dir = _call_dir(
        evidence_root=evidence_root,
        run_id=args.run_id.strip(),
        stage=args.stage.strip(),
        worker_id=args.worker_id.strip(),
        call_id=args.call_id.strip(),
    )
    report_path = (
        Path(args.report_path).expanduser().resolve()
        if args.report_path
        else call_dir / "integration_validate_report.json"
    )
    return ValidationContext(
        evidence_root=evidence_root,
        run_id=args.run_id.strip(),
        call_id=args.call_id.strip(),
        second_call_id=args.second_call_id.strip() if args.second_call_id else None,
        stage=args.stage.strip(),
        worker_id=args.worker_id.strip(),
        openclaw_evidence_dir=openclaw_dir,
        visible_tools_evidence=visible_tools_evidence,
        partial_pack_json=partial_pack_json,
        report_path=report_path,
        sample={
            "ticker": args.ticker.strip(),
            "company_name": args.company_name.strip(),
            "market": args.market.strip(),
            "start_date": args.start_date.strip(),
            "end_date": args.end_date.strip(),
        },
    )


def _call_dir(
    *,
    evidence_root: Path,
    run_id: str,
    stage: str,
    worker_id: str,
    call_id: str,
) -> Path:
    return evidence_root / run_id / stage / worker_id / call_id


def _load_json(path: Path, label: str, blocking_reasons: list[str], checked_paths: list[str]) -> dict[str, Any] | None:
    checked_paths.append(str(path))
    if not path.exists() or not path.is_file():
        blocking_reasons.append(f"{label} 不存在: {path}")
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        blocking_reasons.append(f"{label} 读取或解析失败: {sanitize_error(exc)}")
        return None
    if not isinstance(payload, dict):
        blocking_reasons.append(f"{label} 必须是 JSON object")
        return None
    return payload


def _load_json_list(
    path: Path,
    label: str,
    blocking_reasons: list[str],
    checked_paths: list[str],
) -> list[Any] | None:
    checked_paths.append(str(path))
    if not path.exists() or not path.is_file():
        blocking_reasons.append(f"{label} 不存在: {path}")
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        blocking_reasons.append(f"{label} 读取或解析失败: {sanitize_error(exc)}")
        return None
    if not isinstance(payload, list):
        blocking_reasons.append(f"{label} 必须是 JSON array")
        return None
    return payload


def _extract_accepted_news(pack_payload: dict[str, Any]) -> list[dict[str, Any]]:
    data = pack_payload.get("data")
    if not isinstance(data, dict):
        return []
    accepted: list[dict[str, Any]] = []
    for bucket, items in data.items():
        if not isinstance(items, list):
            continue
        for item in items:
            if not isinstance(item, dict):
                continue
            accepted.append(
                {
                    "bucket": bucket,
                    "news_id": item.get("news_id"),
                    "title": item.get("title"),
                    "source": item.get("source"),
                    "publish_time": item.get("publish_time"),
                    "data_source": item.get("data_source"),
                    "match_type": item.get("match_type"),
                    "match_confidence": item.get("match_confidence"),
                    "content_hash": item.get("content_hash"),
                }
            )
    return accepted


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _validate_accepted_news_audit(
    *,
    call_dir: Path,
    sample: dict[str, str],
    accepted_news: list[dict[str, Any]],
    create_if_missing: bool,
    checked_paths: list[str],
    blocking_reasons: list[str],
) -> dict[str, Any]:
    audit_path = call_dir / "accepted_news_list.json"
    checked_paths.append(str(audit_path))
    expected_payload = {
        "schema_version": "cn_a_news_accepted_news_list.v1",
        "sample": sample,
        "accepted_news_count": len(accepted_news),
        "accepted_news": accepted_news,
    }
    if not audit_path.exists():
        if not create_if_missing:
            blocking_reasons.append(f"accepted news list 不存在: {audit_path}")
            return {"ok": False, "path": str(audit_path), "created": False}
        _write_json(audit_path, expected_payload)
        return {"ok": True, "path": str(audit_path), "created": True}

    try:
        existing_payload = json.loads(audit_path.read_text(encoding="utf-8"))
    except Exception as exc:
        blocking_reasons.append(f"accepted news list 读取失败: {sanitize_error(exc)}")
        return {"ok": False, "path": str(audit_path), "created": False}
    if not isinstance(existing_payload, dict):
        blocking_reasons.append("accepted news list 必须是 JSON object")
        return {"ok": False, "path": str(audit_path), "created": False}

    existing_news = existing_payload.get("accepted_news")
    if not isinstance(existing_news, list):
        blocking_reasons.append("accepted news list.accepted_news 必须是数组")
        return {"ok": False, "path": str(audit_path), "created": False}
    if existing_news != accepted_news:
        blocking_reasons.append("accepted news list 与当前 pack.data 汇总结果不一致")
        return {"ok": False, "path": str(audit_path), "created": False}
    return {"ok": True, "path": str(audit_path), "created": False}


def _validate_visible_tools(
    payload: dict[str, Any],
    blocking_reasons: list[str],
) -> dict[str, Any]:
    source = payload.get("source")
    raw_tools = payload.get("tools")
    if source != "provider_request":
        blocking_reasons.append(f"visible tools source 非法: {source!r}")
    if not isinstance(raw_tools, list):
        blocking_reasons.append("visible tools.tools 必须是数组")
        return {"ok": False, "actual": [], "missing": list(EXPECTED_VISIBLE_TOOLS), "extra": []}

    names: list[str] = []
    for item in raw_tools:
        if isinstance(item, str):
            name = item.strip()
            if name:
                names.append(name)
            continue
        if isinstance(item, dict):
            if isinstance(item.get("name"), str) and item["name"].strip():
                names.append(item["name"].strip())
                continue
            function = item.get("function")
            if isinstance(function, dict) and isinstance(function.get("name"), str) and function["name"].strip():
                names.append(function["name"].strip())

    expected = set(EXPECTED_VISIBLE_TOOLS)
    actual = set(names)
    missing = sorted(expected - actual)
    extra = sorted(actual - expected)
    duplicates = sorted({name for name in names if names.count(name) > 1})
    ok = not missing and not extra and not duplicates and len(names) == len(expected)
    if not ok:
        if missing:
            blocking_reasons.append(f"visible tools 缺少: {missing}")
        if extra:
            blocking_reasons.append(f"visible tools 多出: {extra}")
        if duplicates:
            blocking_reasons.append(f"visible tools 重复: {duplicates}")
        if len(names) != len(expected):
            blocking_reasons.append(
                f"visible tools 数量不匹配: actual={len(names)} expected={len(expected)}"
            )
    return {
        "ok": ok,
        "expected": list(EXPECTED_VISIBLE_TOOLS),
        "actual": names,
        "missing": missing,
        "extra": extra,
        "duplicates": duplicates,
    }


def _validate_concurrency(
    context: ValidationContext,
    checked_paths: list[str],
    blocking_reasons: list[str],
) -> dict[str, Any]:
    if not context.second_call_id:
        return {"ok": False, "checked": False, "reason": "second_call_id 未提供"}

    first_dir = _call_dir(
        evidence_root=context.evidence_root,
        run_id=context.run_id,
        stage=context.stage,
        worker_id=context.worker_id,
        call_id=context.call_id,
    )
    second_dir = _call_dir(
        evidence_root=context.evidence_root,
        run_id=context.run_id,
        stage=context.stage,
        worker_id=context.worker_id,
        call_id=context.second_call_id,
    )
    checked_paths.extend([str(first_dir), str(second_dir)])
    if not first_dir.exists() or not second_dir.exists():
        blocking_reasons.append("并发校验失败：至少一个 call 目录不存在")
        return {"ok": False, "checked": True, "first_dir": str(first_dir), "second_dir": str(second_dir)}
    if first_dir.resolve() == second_dir.resolve():
        blocking_reasons.append("并发校验失败：两个 call_id 指向同一目录")
        return {"ok": False, "checked": True, "first_dir": str(first_dir), "second_dir": str(second_dir)}

    missing_files: list[str] = []
    for folder, call_id in ((first_dir, context.call_id), (second_dir, context.second_call_id)):
        for required in EXPECTED_PACK_FILES:
            required_path = folder / required
            checked_paths.append(str(required_path))
            if not required_path.exists():
                missing_files.append(f"{call_id}:{required}")
    if missing_files:
        blocking_reasons.append(f"并发校验失败：缺少必需文件 {missing_files}")
        return {"ok": False, "checked": True, "missing_files": missing_files}
    return {
        "ok": True,
        "checked": True,
        "first_dir": str(first_dir),
        "second_dir": str(second_dir),
    }


def _contains_directional_judgment(text: str) -> list[str]:
    lowered = text.lower()
    hits: list[str] = []
    for keyword in FORBIDDEN_DIRECTIONAL_TERMS:
        if keyword.lower() in lowered:
            hits.append(keyword)
    return hits


def _validate_p0_failure(pack_payload: dict[str, Any], blocking_reasons: list[str]) -> dict[str, Any]:
    quality = pack_payload.get("quality")
    if not isinstance(quality, dict):
        blocking_reasons.append("P0 失败校验失败：quality 缺失")
        return {"ok": False, "checked": False, "reason": "quality 缺失"}
    status = quality.get("status")
    if status != "failed":
        return {"ok": False, "checked": False, "reason": "当前 pack 不是 failed，无法覆盖 P0 全失败验收"}

    ok_field = pack_payload.get("ok")
    if ok_field is not False:
        blocking_reasons.append("P0 失败校验失败：pack.ok 必须是 false")

    reader_brief = pack_payload.get("reader_brief")
    if not isinstance(reader_brief, str) or not reader_brief.strip():
        blocking_reasons.append("P0 失败校验失败：reader_brief 缺失")
        return {"ok": False, "checked": True, "directional_terms": []}

    directional_hits = _contains_directional_judgment(reader_brief)
    if directional_hits:
        blocking_reasons.append(
            f"P0 失败校验失败：reader_brief 包含方向性判断词 {sorted(set(directional_hits))}"
        )
    return {
        "ok": not directional_hits and ok_field is False,
        "checked": True,
        "directional_terms": sorted(set(directional_hits)),
        "quality_status": status,
    }


def _validate_partial_case(
    *,
    primary_pack_payload: dict[str, Any],
    partial_pack_path: Path | None,
    checked_paths: list[str],
    blocking_reasons: list[str],
) -> dict[str, Any]:
    candidate_payload: dict[str, Any] | None = None
    source = "primary_pack"
    if partial_pack_path is not None:
        candidate_payload = _load_json(
            partial_pack_path,
            "partial_pack_json",
            blocking_reasons,
            checked_paths,
        )
        source = "controlled_input_file"
    else:
        candidate_payload = primary_pack_payload

    if candidate_payload is None:
        return {"ok": False, "checked": True, "source": source, "reason": "输入不可读"}

    quality = candidate_payload.get("quality")
    if not isinstance(quality, dict):
        blocking_reasons.append("partial 校验失败：quality 缺失")
        return {"ok": False, "checked": True, "source": source, "reason": "quality 缺失"}

    status = quality.get("status")
    directional_allowed = quality.get("directional_judgment_allowed")
    company_direct_count = quality.get("company_direct_news_count")
    industry_count = quality.get("industry_background_count")
    macro_count = quality.get("policy_macro_count")
    has_background = isinstance(industry_count, int) and isinstance(macro_count, int) and (industry_count + macro_count > 0)

    if status != "partial":
        blocking_reasons.append("partial 校验失败：quality.status 必须是 partial")
    if directional_allowed is not False:
        blocking_reasons.append("partial 校验失败：directional_judgment_allowed 必须是 false")
    if company_direct_count != 0:
        blocking_reasons.append("partial 校验失败：company_direct_news_count 必须是 0")
    if not has_background:
        blocking_reasons.append("partial 校验失败：背景资料计数必须大于 0")

    return {
        "ok": (
            status == "partial"
            and directional_allowed is False
            and company_direct_count == 0
            and has_background
        ),
        "checked": True,
        "source": source,
        "quality_status": status,
        "directional_judgment_allowed": directional_allowed,
        "company_direct_news_count": company_direct_count,
        "industry_background_count": industry_count,
        "policy_macro_count": macro_count,
    }


def _validate_openclaw_turn_evidence(
    *,
    context: ValidationContext,
    checked_paths: list[str],
    blocking_reasons: list[str],
) -> dict[str, Any]:
    start_blocking_count = len(blocking_reasons)
    if context.openclaw_evidence_dir is None:
        blocking_reasons.append("openclaw turn evidence 缺失：未提供 --openclaw-evidence-dir")
        return {"ok": False, "checked": False, "reason": "missing_argument"}

    evidence_dir = context.openclaw_evidence_dir
    checked_paths.append(str(evidence_dir))
    if not evidence_dir.exists() or not evidence_dir.is_dir():
        blocking_reasons.append(f"openclaw evidence 目录不存在: {evidence_dir}")
        return {"ok": False, "checked": True, "reason": "missing_dir"}

    openclaw_result_path = evidence_dir / "openclaw-result.json"
    openclaw_result = _load_json(openclaw_result_path, "openclaw_result", blocking_reasons, checked_paths)
    if openclaw_result is None:
        return {"ok": False, "checked": True, "reason": "openclaw_result_unreadable"}

    status = openclaw_result.get("status")
    if status not in {"succeeded", "failed"}:
        blocking_reasons.append(f"openclaw_result.status 非法: {status!r}")
    if status == "failed":
        failure_reason = openclaw_result.get("failure_reason")
        if not isinstance(failure_reason, str) or not failure_reason.strip():
            blocking_reasons.append("openclaw_result.status=failed 但 failure_reason 缺失")

    required_path_fields = (
        ("provider_request_path", "final_prompt"),
        ("first_response_path", "llm_back"),
        ("raw_output_path", "report"),
        ("tool_calls_path", "tool_calls"),
        ("visible_tools_path", "visible_tools"),
        ("openviking_receipt_path", "openviking_receipt"),
        ("workspace_evidence_path", "workspace_evidence"),
    )
    resolved_paths: dict[str, Path] = {}
    for field_name, label in required_path_fields:
        raw = openclaw_result.get(field_name)
        if not isinstance(raw, str) or not raw.strip():
            blocking_reasons.append(f"openclaw_result.{field_name} 缺失")
            continue
        candidate = Path(raw.strip()).expanduser()
        if not candidate.is_absolute():
            candidate = (evidence_dir / candidate).resolve()
        else:
            candidate = candidate.resolve()
        checked_paths.append(str(candidate))
        if not candidate.exists() or not candidate.is_file():
            blocking_reasons.append(f"openclaw {label} 文件不存在: {candidate}")
            continue
        resolved_paths[field_name] = candidate

    provider_request = _load_json(
        resolved_paths["provider_request_path"],
        "provider_request",
        blocking_reasons,
        checked_paths,
    ) if "provider_request_path" in resolved_paths else None
    workspace_evidence = _load_json(
        resolved_paths["workspace_evidence_path"],
        "workspace_evidence",
        blocking_reasons,
        checked_paths,
    ) if "workspace_evidence_path" in resolved_paths else None
    tool_calls = _load_json(
        resolved_paths["tool_calls_path"],
        "tool_calls",
        blocking_reasons,
        checked_paths,
    ) if "tool_calls_path" in resolved_paths else None
    visible_tools = _load_json(
        resolved_paths["visible_tools_path"],
        "visible_tools",
        blocking_reasons,
        checked_paths,
    ) if "visible_tools_path" in resolved_paths else None
    receipt = _load_json(
        resolved_paths["openviking_receipt_path"],
        "openviking_receipt",
        blocking_reasons,
        checked_paths,
    ) if "openviking_receipt_path" in resolved_paths else None

    if provider_request is not None:
        payload = provider_request.get("payload")
        if not isinstance(payload, dict):
            blocking_reasons.append("provider_request.payload 缺失")
        else:
            messages = payload.get("messages")
            if not isinstance(messages, list) or not messages:
                blocking_reasons.append("provider_request.payload.messages 缺失或为空")

    if visible_tools is not None:
        visible_status = _validate_visible_tools(visible_tools, blocking_reasons)
    else:
        visible_status = {"ok": False, "expected": list(EXPECTED_VISIBLE_TOOLS), "actual": []}

    if tool_calls is not None:
        if tool_calls.get("source") != "model_tool_events":
            blocking_reasons.append(f"tool_calls.source 非法: {tool_calls.get('source')!r}")
        if tool_calls.get("status") not in {"none", "recorded"}:
            blocking_reasons.append(f"tool_calls.status 非法: {tool_calls.get('status')!r}")

    report_text_path = resolved_paths.get("raw_output_path")
    if report_text_path is not None:
        try:
            content = report_text_path.read_text(encoding="utf-8")
            if not content.strip():
                blocking_reasons.append("report 文本为空")
        except Exception as exc:
            blocking_reasons.append(f"report 文本读取失败: {sanitize_error(exc)}")

    for payload, label in (
        (workspace_evidence, "workspace_evidence"),
        (tool_calls, "tool_calls"),
        (visible_tools, "visible_tools"),
        (receipt, "openviking_receipt"),
    ):
        if payload is None:
            continue
        _validate_runtime_markers(
            payload=payload,
            label=label,
            run_id=context.run_id,
            call_id=context.call_id,
            stage=context.stage,
            worker_id=context.worker_id,
            blocking_reasons=blocking_reasons,
        )

    if receipt is not None:
        if receipt.get("source") != "openviking_adapter_verified_receipt":
            blocking_reasons.append("openviking_receipt.source 非法")
        verification = receipt.get("verification")
        if not isinstance(verification, dict) or verification.get("verified") is not True:
            blocking_reasons.append("openviking_receipt.verification.verified 不是 true")

    return {
        "ok": len(blocking_reasons) == start_blocking_count,
        "checked": True,
        "openclaw_result_status": status,
        "visible_tools": visible_status,
    }


def _validate_runtime_markers(
    *,
    payload: dict[str, Any],
    label: str,
    run_id: str,
    call_id: str,
    stage: str,
    worker_id: str,
    blocking_reasons: list[str],
) -> None:
    runtime_markers = payload.get("runtime_markers")
    if isinstance(runtime_markers, dict):
        for field, expected in (
            ("run_id", run_id),
            ("call_id", call_id),
            ("worker_id", worker_id),
            ("stage", stage),
        ):
            actual = runtime_markers.get(field)
            if actual is not None and actual != expected:
                blocking_reasons.append(
                    f"{label}.runtime_markers.{field} 不一致: {actual!r} != {expected!r}"
                )
        return

    # 收据没有 runtime_markers，顶层字段必须对齐。
    for field, expected in (
        ("run_id", run_id),
        ("call_id", call_id),
        ("worker_id", worker_id),
        ("stage", stage),
    ):
        actual = payload.get(field)
        if actual is not None and actual != expected:
            blocking_reasons.append(f"{label}.{field} 不一致: {actual!r} != {expected!r}")


def _summarize_provider_attempts(provider_attempts: list[Any]) -> dict[str, Any]:
    total = len(provider_attempts)
    ok_count = 0
    failed_count = 0
    reasons: dict[str, int] = {}
    for item in provider_attempts:
        if not isinstance(item, dict):
            continue
        if bool(item.get("ok")):
            ok_count += 1
        else:
            failed_count += 1
            reason = item.get("empty_reason")
            if isinstance(reason, str) and reason.strip():
                reasons[reason] = reasons.get(reason, 0) + 1
    return {
        "total": total,
        "ok_count": ok_count,
        "failed_count": failed_count,
        "empty_reason_counts": dict(sorted(reasons.items())),
    }


def _validate_existing(
    context: ValidationContext,
    *,
    allow_create_accepted_audit: bool = False,
) -> tuple[int, dict[str, Any]]:
    checked_paths: list[str] = []
    blocking_reasons: list[str] = []

    call_dir = _call_dir(
        evidence_root=context.evidence_root,
        run_id=context.run_id,
        stage=context.stage,
        worker_id=context.worker_id,
        call_id=context.call_id,
    )
    checked_paths.append(str(call_dir))
    if not call_dir.exists() or not call_dir.is_dir():
        blocking_reasons.append(f"call evidence 目录不存在: {call_dir}")
        report = _build_report(
            context=context,
            checked_paths=checked_paths,
            provider_attempt_summary={},
            accepted_news_count=0,
            visible_tools_status={"ok": False, "checked": False, "reason": "call_dir_missing"},
            concurrency_status={"ok": False, "checked": False, "reason": "call_dir_missing"},
            openclaw_turn_evidence_status={"ok": False, "checked": False, "reason": "call_dir_missing"},
            p0_failure_status={"ok": False, "checked": False, "reason": "call_dir_missing"},
            partial_case_status={"ok": False, "checked": False, "reason": "call_dir_missing"},
            blocking_reasons=blocking_reasons,
        )
        return 1, report

    pack_path = call_dir / "news_data_pack.json"
    attempts_path = call_dir / "provider_attempts.json"
    pack_payload = _load_json(pack_path, "news_data_pack", blocking_reasons, checked_paths)
    provider_attempts = _load_json_list(
        attempts_path,
        "provider_attempts",
        blocking_reasons,
        checked_paths,
    )
    if pack_payload is None or provider_attempts is None:
        report = _build_report(
            context=context,
            checked_paths=checked_paths,
            provider_attempt_summary={},
            accepted_news_count=0,
            visible_tools_status={"ok": False, "checked": False, "reason": "pack_or_attempts_missing"},
            concurrency_status=_validate_concurrency(context, checked_paths, blocking_reasons),
            openclaw_turn_evidence_status={"ok": False, "checked": False, "reason": "pack_or_attempts_missing"},
            p0_failure_status={"ok": False, "checked": False, "reason": "pack_or_attempts_missing"},
            partial_case_status={"ok": False, "checked": False, "reason": "pack_or_attempts_missing"},
            blocking_reasons=blocking_reasons,
        )
        return 1, report

    pack_attempts = pack_payload.get("provider_attempts")
    if not isinstance(pack_attempts, list):
        blocking_reasons.append("news_data_pack.provider_attempts 缺失或非数组")
    elif pack_attempts != provider_attempts:
        blocking_reasons.append("news_data_pack.provider_attempts 与 provider_attempts.json 不一致")

    accepted_news = _extract_accepted_news(pack_payload)
    accepted_audit_status = _validate_accepted_news_audit(
        call_dir=call_dir,
        sample=context.sample,
        accepted_news=accepted_news,
        create_if_missing=allow_create_accepted_audit,
        checked_paths=checked_paths,
        blocking_reasons=blocking_reasons,
    )
    if not accepted_audit_status.get("ok"):
        blocking_reasons.append("accepted news list 校验失败")

    visible_tools_payload: dict[str, Any] | None = None
    visible_tools_path: Path | None = None
    if context.visible_tools_evidence is not None:
        visible_tools_path = context.visible_tools_evidence
    elif context.openclaw_evidence_dir is not None:
        visible_tools_path = context.openclaw_evidence_dir / "visible-tools.json"
    if visible_tools_path is not None:
        visible_tools_payload = _load_json(
            visible_tools_path,
            "visible_tools_evidence",
            blocking_reasons,
            checked_paths,
        )
    else:
        blocking_reasons.append("visible tools 证据缺失：未提供 --visible-tools-evidence 或 --openclaw-evidence-dir")

    visible_tools_status = (
        _validate_visible_tools(visible_tools_payload, blocking_reasons)
        if visible_tools_payload is not None
        else {"ok": False, "checked": False, "reason": "missing_evidence"}
    )

    concurrency_status = _validate_concurrency(context, checked_paths, blocking_reasons)
    p0_failure_status = _validate_p0_failure(pack_payload, blocking_reasons)
    partial_case_status = _validate_partial_case(
        primary_pack_payload=pack_payload,
        partial_pack_path=context.partial_pack_json,
        checked_paths=checked_paths,
        blocking_reasons=blocking_reasons,
    )
    openclaw_status = _validate_openclaw_turn_evidence(
        context=context,
        checked_paths=checked_paths,
        blocking_reasons=blocking_reasons,
    )
    provider_attempt_summary = _summarize_provider_attempts(provider_attempts)
    report = _build_report(
        context=context,
        checked_paths=checked_paths,
        provider_attempt_summary=provider_attempt_summary,
        accepted_news_count=len(accepted_news),
        visible_tools_status=visible_tools_status,
        concurrency_status=concurrency_status,
        openclaw_turn_evidence_status=openclaw_status,
        p0_failure_status=p0_failure_status,
        partial_case_status=partial_case_status,
        blocking_reasons=blocking_reasons,
    )
    exit_code = 0 if not blocking_reasons else 1
    report["exit_code"] = exit_code
    return exit_code, report


def _run_tool(context: ValidationContext) -> tuple[int, dict[str, Any]]:
    checked_paths: list[str] = []
    blocking_reasons: list[str] = []
    try:
        run_news_data_pack(
            tool_input={
                "ticker": context.sample["ticker"],
                "market": context.sample["market"],
                "start_date": context.sample["start_date"],
                "end_date": context.sample["end_date"],
            },
            context={
                "run_id": context.run_id,
                "stage": context.stage,
                "worker_id": context.worker_id,
                "call_id": context.call_id,
                "tool_name": TOOL_NAME,
                "evidence_root": str(context.evidence_root),
            },
        )
    except Exception as exc:
        blocking_reasons.append(f"run-tool 执行失败: {sanitize_error(exc)}")
        report = _build_report(
            context=context,
            checked_paths=checked_paths,
            provider_attempt_summary={},
            accepted_news_count=0,
            visible_tools_status={"ok": False, "checked": False, "reason": "run_tool_failed"},
            concurrency_status={"ok": False, "checked": False, "reason": "run_tool_failed"},
            openclaw_turn_evidence_status={"ok": False, "checked": False, "reason": "run_tool_failed"},
            p0_failure_status={"ok": False, "checked": False, "reason": "run_tool_failed"},
            partial_case_status={"ok": False, "checked": False, "reason": "run_tool_failed"},
            blocking_reasons=blocking_reasons,
        )
        report["exit_code"] = 1
        return 1, report
    return _validate_existing(context, allow_create_accepted_audit=True)


def _build_report(
    *,
    context: ValidationContext,
    checked_paths: list[str],
    provider_attempt_summary: dict[str, Any],
    accepted_news_count: int,
    visible_tools_status: dict[str, Any],
    concurrency_status: dict[str, Any],
    openclaw_turn_evidence_status: dict[str, Any],
    p0_failure_status: dict[str, Any],
    partial_case_status: dict[str, Any],
    blocking_reasons: list[str],
) -> dict[str, Any]:
    return {
        "schema_version": SCHEMA_VERSION,
        "generated_at": datetime.now().astimezone().isoformat(),
        "sample": context.sample,
        "checked_paths": sorted(set(checked_paths)),
        "provider_attempt_summary": provider_attempt_summary,
        "accepted_news_count": accepted_news_count,
        "visible_tools_status": visible_tools_status,
        "concurrency_status": concurrency_status,
        "openclaw_turn_evidence_status": openclaw_turn_evidence_status,
        "p0_failure_status": p0_failure_status,
        "partial_case_status": partial_case_status,
        "blocking_reasons": sorted(set(blocking_reasons)),
        "exit_code": 1 if blocking_reasons else 0,
    }


def main(argv: Sequence[str] | None = None) -> int:
    parser = _build_parser()
    try:
        args = parser.parse_args(argv)
        context = _resolve_context(args)
        if args.command == "run-tool":
            exit_code, report = _run_tool(context)
        elif args.command == "validate-existing":
            exit_code, report = _validate_existing(context)
        else:
            raise ValueError(f"unknown command: {args.command}")
        _write_json(context.report_path, report)
        sys.stdout.write(json.dumps(report, ensure_ascii=False, indent=2) + "\n")
        return exit_code
    except Exception as exc:
        sys.stderr.write(f"integration_validate_600519 failed: {sanitize_error(exc)}\n")
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
