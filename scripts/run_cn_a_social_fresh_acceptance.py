#!/usr/bin/env python3
from __future__ import annotations

import argparse
from dataclasses import asdict, dataclass
from datetime import date, timedelta
import importlib
import importlib.util
import json
import os
import re
import subprocess
import sys
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = REPO_ROOT / "src"
SOCIAL_SCRIPTS_ROOT = REPO_ROOT / "agents" / "social_analyst" / "skills" / "cn-a-social-data" / "scripts"
EXPECTED_VISIBLE_TOOLS = ("openviking_write_material", "social_social_sentiment_pack")
EXPECTED_WORKER = "social_analyst"
EXPECTED_STAGE = "frontline"
RUN_ID_RE = re.compile(r"run_id=([A-Za-z0-9._-]+)")


@dataclass(frozen=True)
class FreshRunResult:
    ok: bool
    run_id: str | None
    command: list[str]
    exit_code: int
    stdout: str
    stderr: str


def _ensure_import_paths() -> None:
    for item in (SRC_ROOT, SOCIAL_SCRIPTS_ROOT):
        text = str(item)
        if text not in sys.path:
            sys.path.insert(0, text)


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="CN_A social_analyst 600519 fresh acceptance runner")
    sub = parser.add_subparsers(dest="command", required=True)

    run_fresh = sub.add_parser("run-fresh", help="Run a real OpenClaw social_analyst fresh run, then validate")
    _add_common_validate_args(run_fresh)
    run_fresh.add_argument("--ticker", default="600519")
    run_fresh.add_argument("--company-name", default="贵州茅台")
    run_fresh.add_argument("--market", default="CN_A")
    run_fresh.add_argument("--profile", default="CN_A")
    run_fresh.add_argument("--currency", default="CNY")
    run_fresh.add_argument("--currency-symbol", default="¥")
    run_fresh.add_argument("--current-date", default=date.today().isoformat())
    run_fresh.add_argument("--start-date")
    run_fresh.add_argument("--end-date")
    run_fresh.add_argument("--run-dir", default="runs")

    validate = sub.add_parser("validate-existing", help="Validate an existing fresh run evidence set")
    _add_common_validate_args(validate)
    validate.add_argument("--run-id", required=True)
    validate.add_argument("--call-id")
    validate.add_argument("--run-dir", default="runs")
    return parser


def _add_common_validate_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--acceptance-root", default="docs/evidence/cn_a_social/fresh_acceptance")
    parser.add_argument("--openviking-backend", help="module:attr override, defaults to CLAW_TRADE_OPENVIKING_BACKEND or create_default_backend")


def _default_start_end(current_date_text: str) -> tuple[str, str]:
    current = date.fromisoformat(current_date_text)
    return (current - timedelta(days=30)).isoformat(), current.isoformat()


def _run_fresh(args: argparse.Namespace) -> FreshRunResult:
    start_date, end_date = _default_start_end(args.current_date)
    if args.start_date:
        start_date = args.start_date
    if args.end_date:
        end_date = args.end_date

    cmd = [
        sys.executable,
        "-m",
        "claw_trade.cli.run_control",
        "--ticker",
        args.ticker,
        "--company-name",
        args.company_name,
        "--market",
        args.market,
        "--profile",
        args.profile,
        "--currency",
        args.currency,
        "--currency-symbol",
        args.currency_symbol,
        "--current-date",
        args.current_date,
        "--start-date",
        start_date,
        "--end-date",
        end_date,
        "--stop-point",
        "single_worker_complete",
        "--target-worker-id",
        EXPECTED_WORKER,
        "--target-stage",
        EXPECTED_STAGE,
        "--run-dir",
        args.run_dir,
    ]
    completed = subprocess.run(cmd, capture_output=True, text=True, cwd=str(REPO_ROOT), check=False)
    merged_text = f"{completed.stdout}\n{completed.stderr}"
    matched = RUN_ID_RE.search(merged_text)
    run_id = matched.group(1) if matched else None
    return FreshRunResult(
        ok=completed.returncode == 0 and run_id is not None,
        run_id=run_id,
        command=cmd,
        exit_code=completed.returncode,
        stdout=completed.stdout,
        stderr=completed.stderr,
    )


def _load_json(path: Path, label: str, blocking: list[str]) -> dict[str, Any] | None:
    if not path.exists() or not path.is_file():
        blocking.append(f"{label} missing: {path}")
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        blocking.append(f"{label} read failed: {exc}")
        return None
    if not isinstance(payload, dict):
        blocking.append(f"{label} must be a JSON object")
        return None
    return payload


def _copy_json(src: Path, dst: Path) -> None:
    dst.parent.mkdir(parents=True, exist_ok=True)
    payload = json.loads(src.read_text(encoding="utf-8"))
    dst.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _load_guard_module():
    spec = importlib.util.spec_from_file_location("cn_a_social_guard", SOCIAL_SCRIPTS_ROOT / "guard.py")
    if spec is None or spec.loader is None:
        raise RuntimeError("cannot load social guard module")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _load_runtime_object(spec: str, *, env_key: str) -> object:
    module_name, separator, attr_name = spec.partition(":")
    if not module_name or separator != ":" or not attr_name:
        raise ValueError(f"{env_key} must be module:attr")
    module = importlib.import_module(module_name)
    if not hasattr(module, attr_name):
        raise ValueError(f"{env_key} attr not found: {spec}")
    symbol = getattr(module, attr_name)
    if isinstance(symbol, type):
        return symbol()
    if callable(symbol):
        try:
            return symbol()
        except TypeError:
            return symbol
    return symbol


def _resolve_backend(spec_override: str | None):
    spec = (spec_override or "").strip()
    if not spec:
        spec = ""
    if not spec:
        spec = (os.environ.get("CLAW_TRADE_OPENVIKING_BACKEND", "") or "").strip()
    if spec:
        backend = _load_runtime_object(spec, env_key="CLAW_TRADE_OPENVIKING_BACKEND")
    else:
        module = importlib.import_module("claw_trade.artifacts.openviking_backend_http")
        backend = module.create_default_backend()
    required_methods = ("fetch_content_by_uri", "fetch_l2_index_by_uri")
    for name in required_methods:
        if not callable(getattr(backend, name, None)):
            raise RuntimeError(f"openviking backend missing method: {name}")
    return backend


def _find_social_call_dir(run_dir: Path, run_id: str, call_id: str | None, blocking: list[str]) -> tuple[str | None, Path | None]:
    calls_root = run_dir / run_id / "calls"
    if not calls_root.exists() or not calls_root.is_dir():
        blocking.append(f"calls directory missing: {calls_root}")
        return None, None
    if call_id:
        exact = calls_root / call_id
        if not exact.exists():
            blocking.append(f"call_id not found: {call_id}")
            return None, None
        return call_id, exact

    matched: list[tuple[str, Path]] = []
    for call_json in sorted(calls_root.glob("*/call.json")):
        payload = _load_json(call_json, f"call_json:{call_json}", blocking)
        if payload is None:
            continue
        if payload.get("worker_id") == EXPECTED_WORKER and payload.get("stage") == EXPECTED_STAGE:
            matched.append((call_json.parent.name, call_json.parent))
    if not matched:
        blocking.append("no social_analyst frontline call found")
        return None, None
    return matched[-1]


def _extract_visible_tool_names(visible_tools_payload: dict[str, Any]) -> list[str]:
    raw_tools = visible_tools_payload.get("tools")
    if not isinstance(raw_tools, list):
        return []
    names: list[str] = []
    for item in raw_tools:
        if isinstance(item, str):
            stripped = item.strip()
            if stripped:
                names.append(stripped)
            continue
        if not isinstance(item, dict):
            continue
        name = item.get("name")
        if isinstance(name, str) and name.strip():
            names.append(name.strip())
            continue
        function = item.get("function")
        if isinstance(function, dict):
            name = function.get("name")
            if isinstance(name, str) and name.strip():
                names.append(name.strip())
    return names


def _extract_final_prompt_text(provider_request_payload: dict[str, Any]) -> str:
    payload = provider_request_payload.get("payload")
    if not isinstance(payload, dict):
        return ""
    messages = payload.get("messages")
    if not isinstance(messages, list):
        return ""
    parts: list[str] = []
    for message in messages:
        if not isinstance(message, dict):
            continue
        content = message.get("content")
        if isinstance(content, str):
            parts.append(content)
            continue
        if isinstance(content, list):
            for item in content:
                if isinstance(item, dict) and isinstance(item.get("text"), str):
                    parts.append(item["text"])
    return "\n\n".join(parts).strip()


def _manifest_has_call(run_dir: Path, run_id: str, call_id: str) -> bool:
    manifest_path = run_dir / run_id / "openviking" / "approved-manifest.json"
    if not manifest_path.exists():
        return False
    try:
        payload = json.loads(manifest_path.read_text(encoding="utf-8"))
    except Exception:
        return False
    materials = payload.get("materials")
    if not isinstance(materials, list):
        return False
    for item in materials:
        if isinstance(item, dict) and item.get("call_id") == call_id:
            return True
    return False


def _safe_evidence_filename(uri: str) -> str:
    digest = re.sub(r"[^A-Za-z0-9._-]", "_", uri)
    digest = digest[-120:]
    return digest or "evidence.json"


def _validate_existing(args: argparse.Namespace, *, run_id: str, call_id: str | None) -> tuple[int, dict[str, Any]]:
    run_dir = (REPO_ROOT / args.run_dir).resolve()
    blocking: list[str] = []
    checked_paths: list[str] = []
    guard_module = _load_guard_module()
    backend = _resolve_backend(args.openviking_backend)
    selected_call_id, call_dir = _find_social_call_dir(run_dir, run_id, call_id, blocking)
    if selected_call_id is None or call_dir is None:
        return 1, {
            "ok": False,
            "run_id": run_id,
            "call_id": selected_call_id,
            "blocking_reasons": blocking,
            "checked_paths": checked_paths,
        }

    acceptance_root = (REPO_ROOT / args.acceptance_root).resolve()
    acceptance_dir = acceptance_root / run_id / selected_call_id
    acceptance_dir.mkdir(parents=True, exist_ok=True)

    openclaw_result_path = call_dir / "openclaw-result.json"
    call_path = call_dir / "call.json"
    checked_paths.extend([str(call_path), str(openclaw_result_path)])
    call_payload = _load_json(call_path, "call.json", blocking)
    openclaw_result = _load_json(openclaw_result_path, "openclaw-result.json", blocking)
    if call_payload is None or openclaw_result is None:
        return 1, {
            "ok": False,
            "run_id": run_id,
            "call_id": selected_call_id,
            "blocking_reasons": blocking,
            "checked_paths": checked_paths,
        }

    if openclaw_result.get("status") != "succeeded":
        blocking.append(f"openclaw result status is not succeeded: {openclaw_result.get('status')!r}")

    required_evidence_fields = {
        "provider_request_path": "provider-request.json",
        "visible_tools_path": "visible-tools.json",
        "tool_calls_path": "tool-calls.json",
        "openviking_receipt_path": "openviking-write-receipt.json",
        "raw_output_path": "raw-output.md",
    }
    resolved_paths: dict[str, Path] = {}
    for field in required_evidence_fields:
        raw = openclaw_result.get(field)
        if not isinstance(raw, str) or not raw.strip():
            blocking.append(f"openclaw-result missing field: {field}")
            continue
        path = Path(raw.strip()).expanduser().resolve()
        checked_paths.append(str(path))
        if not path.exists() or not path.is_file():
            blocking.append(f"evidence file missing for {field}: {path}")
            continue
        resolved_paths[field] = path

    if blocking:
        return 1, {
            "ok": False,
            "run_id": run_id,
            "call_id": selected_call_id,
            "blocking_reasons": blocking,
            "checked_paths": checked_paths,
        }

    provider_request_payload = _load_json(resolved_paths["provider_request_path"], "provider-request", blocking)
    visible_tools_payload = _load_json(resolved_paths["visible_tools_path"], "visible-tools", blocking)
    tool_calls_payload = _load_json(resolved_paths["tool_calls_path"], "tool-calls", blocking)
    receipt_payload = _load_json(resolved_paths["openviking_receipt_path"], "openviking-write-receipt", blocking)
    if any(item is None for item in (provider_request_payload, visible_tools_payload, tool_calls_payload, receipt_payload)):
        return 1, {
            "ok": False,
            "run_id": run_id,
            "call_id": selected_call_id,
            "blocking_reasons": blocking,
            "checked_paths": checked_paths,
        }

    assert provider_request_payload is not None
    assert visible_tools_payload is not None
    assert tool_calls_payload is not None
    assert receipt_payload is not None

    final_prompt_text = _extract_final_prompt_text(provider_request_payload)
    if not final_prompt_text:
        blocking.append("final prompt text is empty in provider-request payload")

    visible_tool_names = _extract_visible_tool_names(visible_tools_payload)
    expected_set = set(EXPECTED_VISIBLE_TOOLS)
    actual_set = set(visible_tool_names)
    if actual_set != expected_set or len(visible_tool_names) != len(EXPECTED_VISIBLE_TOOLS):
        blocking.append(f"visible tools mismatch: actual={visible_tool_names} expected={list(EXPECTED_VISIBLE_TOOLS)}")

    tool_calls = tool_calls_payload.get("calls")
    if not isinstance(tool_calls, list):
        blocking.append("tool-calls payload missing calls array")
        tool_calls = []
    social_call = None
    write_call = None
    for item in tool_calls:
        if not isinstance(item, dict):
            continue
        if item.get("tool_name") == "social_social_sentiment_pack":
            social_call = item
        if item.get("tool_name") == "openviking_write_material":
            write_call = item
    if social_call is None:
        blocking.append("tool-calls missing social_social_sentiment_pack call")
    if write_call is None:
        blocking.append("tool-calls missing openviking_write_material call")

    if receipt_payload.get("source") != "openviking_adapter_verified_receipt":
        blocking.append("openviking receipt source is not adapter_verified")
    verification = receipt_payload.get("verification")
    if not isinstance(verification, dict) or verification.get("verified") is not True:
        blocking.append("openviking receipt verification.verified is not true")
    report_uri = receipt_payload.get("uri")
    if not isinstance(report_uri, str) or not report_uri.strip():
        blocking.append("openviking receipt uri missing")
        report_uri = ""

    material_target = call_payload.get("material_target")
    l2_prefix = material_target.get("l2_prefix") if isinstance(material_target, dict) else None
    if not isinstance(l2_prefix, str) or not l2_prefix.strip():
        blocking.append("call.material_target.l2_prefix missing")
        l2_prefix = ""
    l2_index_uri = f"{l2_prefix}index.json" if l2_prefix else ""

    report_text = ""
    tool_result_payload: dict[str, Any] | None = None
    l2_index_payload: dict[str, Any] | None = None
    l2_entry_uris: list[str] = []
    saved_provider_evidence_files: list[str] = []

    if report_uri:
        report_bytes = backend.fetch_content_by_uri(report_uri)
        report_text = report_bytes.decode("utf-8")
        (acceptance_dir / "report.md").write_text(report_text, encoding="utf-8")

    if l2_index_uri:
        l2_index_bytes = backend.fetch_content_by_uri(l2_index_uri)
        try:
            parsed = json.loads(l2_index_bytes.decode("utf-8"))
        except Exception:
            parsed = {}
        if isinstance(parsed, dict):
            l2_index_payload = parsed
            (acceptance_dir / "provider_evidence_index.json").write_text(
                json.dumps(parsed, ensure_ascii=False, indent=2) + "\n",
                encoding="utf-8",
            )
            entries = parsed.get("entries")
            if isinstance(entries, list):
                for entry in entries:
                    if isinstance(entry, dict) and isinstance(entry.get("uri"), str):
                        l2_entry_uris.append(entry["uri"])

    tool_result_uri = ""
    for uri in l2_entry_uris:
        if uri.endswith("/pack/social_sentiment_pack.json"):
            tool_result_uri = uri
            break
    if not tool_result_uri:
        for uri in l2_entry_uris:
            if "/pack/" in uri and uri.endswith("social_sentiment_pack.json"):
                tool_result_uri = uri
                break
    if not tool_result_uri:
        blocking.append("cannot find social_sentiment_pack tool result uri in provider evidence index")
    else:
        tool_result_bytes = backend.fetch_content_by_uri(tool_result_uri)
        tool_result_payload_raw = json.loads(tool_result_bytes.decode("utf-8"))
        if not isinstance(tool_result_payload_raw, dict):
            blocking.append("tool result payload is not a JSON object")
        else:
            tool_result_payload = tool_result_payload_raw
            (acceptance_dir / "tool_result_pack.json").write_text(
                json.dumps(tool_result_payload_raw, ensure_ascii=False, indent=2) + "\n",
                encoding="utf-8",
            )

    provider_evidence_dir = acceptance_dir / "provider_evidence_entries"
    provider_evidence_dir.mkdir(parents=True, exist_ok=True)
    for uri in l2_entry_uris:
        if "/provider_raw/" not in uri and "/pack/" not in uri:
            continue
        try:
            payload_bytes = backend.fetch_content_by_uri(uri)
        except Exception as exc:
            blocking.append(f"failed to fetch provider evidence uri: {uri} error={exc}")
            continue
        out_name = _safe_evidence_filename(uri)
        out_path = provider_evidence_dir / out_name
        out_path.write_bytes(payload_bytes)
        saved_provider_evidence_files.append(str(out_path))

    if tool_result_payload is None:
        pack_guard_dict = {"ok": False, "reason_codes": ["SOCIAL_PACK_SCHEMA_INVALID"], "error": "tool result missing"}
        report_guard_dict = {"ok": False, "reason_codes": ["SOCIAL_PACK_SCHEMA_INVALID"], "error": "tool result missing"}
        synthetic_report_guard_dict = {
            "ok": False,
            "reason_codes": ["SOCIAL_REPORT_UNSUPPORTED_SOURCE_CLAIM"],
            "checked": False,
        }
    else:
        pack_guard = guard_module.validate_social_pack_schema(tool_result_payload)
        report_guard = guard_module.validate_social_report_against_pack(report_text, tool_result_payload)
        synthetic_report_text = (
            report_text
            + "\n\n补充声明：我们已读取平台原帖正文、KOL观点与散户机构分层样本。"
        )
        synthetic_guard = guard_module.validate_social_report_against_pack(synthetic_report_text, tool_result_payload)
        pack_guard_dict = {"ok": bool(pack_guard.ok), "reason_codes": list(pack_guard.reason_codes)}
        report_guard_dict = {"ok": bool(report_guard.ok), "reason_codes": list(report_guard.reason_codes)}
        synthetic_report_guard_dict = {"ok": bool(synthetic_guard.ok), "reason_codes": list(synthetic_guard.reason_codes)}
        required_code = getattr(guard_module, "SOCIAL_REPORT_UNSUPPORTED_SOURCE_CLAIM")
        if synthetic_guard.ok or required_code not in synthetic_guard.reason_codes:
            blocking.append("synthetic unsupported-source claim did not trigger report guard failure")

    manifest_has_call = _manifest_has_call(run_dir, run_id, selected_call_id)
    if (not pack_guard_dict["ok"] or not report_guard_dict["ok"]) and manifest_has_call:
        blocking.append("guard failed but call is already present in approved manifest")

    # Persist core acceptance bundle.
    _copy_json(openclaw_result_path, acceptance_dir / "openclaw_result_raw.json")
    _copy_json(resolved_paths["provider_request_path"], acceptance_dir / "final_prompt_raw.json")
    _copy_json(resolved_paths["visible_tools_path"], acceptance_dir / "visible_tools_raw.json")
    _copy_json(resolved_paths["tool_calls_path"], acceptance_dir / "tool_calls_raw.json")
    _copy_json(resolved_paths["openviking_receipt_path"], acceptance_dir / "openviking_receipt_raw.json")
    (acceptance_dir / "final_prompt.md").write_text(final_prompt_text + "\n", encoding="utf-8")

    summary = {
        "ok": len(blocking) == 0,
        "run_id": run_id,
        "call_id": selected_call_id,
        "worker_id": EXPECTED_WORKER,
        "stage": EXPECTED_STAGE,
        "acceptance_dir": str(acceptance_dir),
        "evidence_saved": {
            "final_prompt_raw": str(acceptance_dir / "final_prompt_raw.json"),
            "final_prompt_text": str(acceptance_dir / "final_prompt.md"),
            "visible_tools_raw": str(acceptance_dir / "visible_tools_raw.json"),
            "tool_calls_raw": str(acceptance_dir / "tool_calls_raw.json"),
            "tool_result_pack": str(acceptance_dir / "tool_result_pack.json") if tool_result_payload is not None else None,
            "report": str(acceptance_dir / "report.md") if report_text else None,
            "openviking_receipt_raw": str(acceptance_dir / "openviking_receipt_raw.json"),
            "provider_evidence_index": str(acceptance_dir / "provider_evidence_index.json") if l2_index_payload is not None else None,
            "provider_evidence_entries": saved_provider_evidence_files,
        },
        "checks": {
            "final_prompt_present": bool(final_prompt_text),
            "visible_tools_exact_match": actual_set == expected_set and len(visible_tool_names) == len(EXPECTED_VISIBLE_TOOLS),
            "tool_calls_present": isinstance(tool_calls, list) and bool(tool_calls),
            "social_tool_call_present": social_call is not None,
            "write_tool_call_present": write_call is not None,
            "receipt_verified": isinstance(verification, dict) and verification.get("verified") is True,
            "pack_schema_guard": pack_guard_dict,
            "report_guard": report_guard_dict,
            "synthetic_unsupported_source_guard": synthetic_report_guard_dict,
            "manifest_contains_call": manifest_has_call,
            "manifest_rule_ok": not ((not pack_guard_dict["ok"] or not report_guard_dict["ok"]) and manifest_has_call),
        },
        "checked_paths": checked_paths,
        "blocking_reasons": blocking,
    }
    (acceptance_dir / "acceptance_report.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return (0 if summary["ok"] else 1), summary


def main(argv: list[str] | None = None) -> int:
    _ensure_import_paths()
    parser = _build_parser()
    args = parser.parse_args(argv)

    if args.command == "run-fresh":
        run_result = _run_fresh(args)
        if not run_result.ok or run_result.run_id is None:
            payload = {
                "ok": False,
                "mode": "run-fresh",
                "fresh_run": asdict(run_result),
                "reason": "fresh run failed or run_id missing",
            }
            print(json.dumps(payload, ensure_ascii=False, indent=2))
            return 1
        exit_code, report = _validate_existing(args, run_id=run_result.run_id, call_id=None)
        report["mode"] = "run-fresh"
        report["fresh_run"] = asdict(run_result)
        print(json.dumps(report, ensure_ascii=False, indent=2))
        return exit_code

    if args.command == "validate-existing":
        exit_code, report = _validate_existing(args, run_id=args.run_id, call_id=args.call_id)
        report["mode"] = "validate-existing"
        print(json.dumps(report, ensure_ascii=False, indent=2))
        return exit_code

    parser.error(f"unsupported command: {args.command}")
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
