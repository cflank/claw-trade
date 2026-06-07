from __future__ import annotations

import re
from pathlib import Path

from tests.settings.s16_test_helpers import (
    PROJECT_ROOT,
    SETTINGS_S16_ARTIFACT_DIR,
    ensure_s16_dirs,
    read_text,
    write_json,
)

_TEXT_SUFFIX_ALLOWLIST = {
    ".py",
    ".ts",
    ".tsx",
    ".js",
    ".jsx",
    ".json",
    ".md",
    ".txt",
    ".log",
    ".html",
    ".yml",
    ".yaml",
}
_BINARY_SUFFIX_BLOCKLIST = {
    ".png",
    ".jpg",
    ".jpeg",
    ".gif",
    ".webp",
    ".bmp",
    ".ico",
    ".pdf",
    ".zip",
    ".gz",
    ".tar",
    ".tgz",
    ".woff",
    ".woff2",
}
_EXCLUDE_DIR_NAMES = {"__pycache__", ".git", "node_modules", "dist", "build", ".next"}
_MAX_SCAN_FILE_BYTES = 1_500_000


def _iter_scan_files(scan_roots: list[Path]) -> tuple[list[Path], dict[str, int]]:
    files: list[Path] = []
    skipped = {"non_text_suffix": 0, "oversized": 0}
    visited: set[Path] = set()
    for root in scan_roots:
        if not root.exists():
            continue
        for candidate in sorted(root.rglob("*")):
            if candidate in visited:
                continue
            if candidate.is_dir():
                continue
            if any(part in _EXCLUDE_DIR_NAMES for part in candidate.parts):
                continue
            suffix = candidate.suffix.lower()
            if suffix in _BINARY_SUFFIX_BLOCKLIST or suffix not in _TEXT_SUFFIX_ALLOWLIST:
                skipped["non_text_suffix"] += 1
                continue
            if candidate.stat().st_size > _MAX_SCAN_FILE_BYTES:
                skipped["oversized"] += 1
                continue
            files.append(candidate)
            visited.add(candidate)
    return files, skipped


def _classify_hit(*, path: Path, line_text: str) -> str:
    path_str = str(path).replace("\\", "/")
    if "/tests/" in path_str:
        return "test_assertion_or_fixture"
    if "/.runtime/test-artifacts/" in path_str:
        return "artifact_evidence_trace"
    if "/src/" in path_str or "/web/research-ui/src/" in path_str:
        return "implementation_risk"
    return "other_context"


def test_settings_live_no_fake_success_paths_in_impl_tests_and_evidence() -> None:
    ensure_s16_dirs()

    scan_roots = [
        PROJECT_ROOT / "src" / "claw_trade" / "ui_backend",
        PROJECT_ROOT / "src" / "claw_trade" / "web",
        PROJECT_ROOT / "web" / "research-ui" / "src",
        PROJECT_ROOT / "tests" / "settings",
        PROJECT_ROOT / ".runtime" / "test-artifacts" / "settings-ui-text",
    ]
    settings_artifacts_root = PROJECT_ROOT / ".runtime" / "test-artifacts"
    scan_roots.extend(sorted(settings_artifacts_root.glob("settings-s*")))

    files_to_scan, skipped = _iter_scan_files(scan_roots)
    assert files_to_scan, "no files scanned for no-fake-success coverage"

    banned_patterns = [
        r"route\.fulfill\(",
        r"_temporary_report_model_ready",
        r"fake[_\s-]*(ready|success|online|login)",
        r"mock[_\s-]*(ready|success|online|login)",
        r"stub[_\s-]*(ready|success|online|login)",
        r"capture-only success",
        r"保存配置即通过",
        r"静默切换默认模型",
        r"silent fallback",
        r"伪造登录成功",
    ]

    hits: list[dict[str, object]] = []
    for target in files_to_scan:
        content = read_text(target)
        for line_number, raw_line in enumerate(content.splitlines(), start=1):
            for pattern in banned_patterns:
                if not re.search(pattern, raw_line, flags=re.IGNORECASE):
                    continue
                hits.append(
                    {
                        "path": str(target),
                        "line": line_number,
                        "pattern": pattern,
                        "classification": _classify_hit(path=target, line_text=raw_line),
                        "excerpt": raw_line.strip()[:220],
                    }
                )

    blocking_hits = [item for item in hits if item["classification"] == "implementation_risk"]
    assert not blocking_hits, f"implementation fake-success risk found: {blocking_hits}"

    report_gate_text = read_text(PROJECT_ROOT / "tests" / "settings" / "test_report_model_gate.py")
    assert "saved_unverified" in report_gate_text
    assert "blocked is True" in report_gate_text
    assert "default model" not in report_gate_text.lower()

    enhanced_live_text = read_text(PROJECT_ROOT / "tests" / "settings" / "live" / "test_enhanced_source_attempts_live.py")
    assert "expected default-source success attempts" in enhanced_live_text
    assert "assert all(item.provider != \"tushare_kline_fallback\" for item in success_attempts)" in enhanced_live_text

    sections_live_log = read_text(PROJECT_ROOT / ".runtime" / "test-artifacts" / "settings-s14" / "03-settings-sections-visual.log")
    assert "1 passed" in sections_live_log

    no_masquerade_doc = read_text(
        PROJECT_ROOT / ".runtime" / "test-artifacts" / "settings-s05" / "06-default-source-cover-no-masquerade.md"
    )
    assert "no masquerade" in no_masquerade_doc.lower()
    assert "default source covers and flow continues" in no_masquerade_doc.lower()

    write_json(
        SETTINGS_S16_ARTIFACT_DIR / "no-fake-success-scan.json",
        {
            "scanRoots": [str(path) for path in scan_roots],
            "excludeRules": {
                "excludeDirNames": sorted(_EXCLUDE_DIR_NAMES),
                "textSuffixAllowlist": sorted(_TEXT_SUFFIX_ALLOWLIST),
                "binarySuffixBlocklist": sorted(_BINARY_SUFFIX_BLOCKLIST),
                "maxScanFileBytes": _MAX_SCAN_FILE_BYTES,
            },
            "scanSummary": {
                "scannedFileCount": len(files_to_scan),
                "skipped": skipped,
            },
            "bannedPatterns": banned_patterns,
            "hits": hits,
            "hitCountsByClassification": {
                "test_assertion_or_fixture": len([item for item in hits if item["classification"] == "test_assertion_or_fixture"]),
                "artifact_evidence_trace": len([item for item in hits if item["classification"] == "artifact_evidence_trace"]),
                "implementation_risk": len(blocking_hits),
                "other_context": len([item for item in hits if item["classification"] == "other_context"]),
            },
            "blockingHits": blocking_hits,
            "status": "pass" if not blocking_hits else "fail",
        },
    )
