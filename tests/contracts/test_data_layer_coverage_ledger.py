from __future__ import annotations

import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
TASK_LIST = REPO_ROOT / "docs" / "数据层实施任务清单.md"
DETAIL_DESIGN = REPO_ROOT / "docs" / "数据层详细设计.md"
OVERALL_DESIGN = REPO_ROOT / "docs" / "数据层总体设计.md"

DLT_IDS = tuple(f"DLT-{index:02d}" for index in range(1, 15))
AUTHORIZED_COLLECTIONS = (
    "normalized_datasets",
    "raw_payloads",
    "provider_attempts",
    "provider_rate_limits",
    "single_flight_calls",
    "provider_result_cache",
    "dataset_manifests",
    "maintenance_jobs",
)


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def _table_rows(section_text: str) -> list[dict[str, str]]:
    lines = [line.strip() for line in section_text.splitlines() if line.strip().startswith("|")]
    header: list[str] | None = None
    rows: list[dict[str, str]] = []
    for line in lines:
        cells = [cell.strip() for cell in line.strip("|").split("|")]
        if not cells or set(cells[0]) <= {"-", ":"}:
            continue
        if header is None:
            header = cells
            continue
        if len(cells) == len(header):
            rows.append(dict(zip(header, cells, strict=True)))
    return rows


def _section(text: str, heading: str) -> str:
    start = text.index(heading)
    next_heading = re.search(r"\n##\s+", text[start + len(heading) :])
    if next_heading is None:
        return text[start:]
    end = start + len(heading) + next_heading.start()
    return text[start:end]


def test_task_list_uses_dlt_coverage_ids_without_old_task_ids() -> None:
    text = _read(TASK_LIST)

    for task_id in DLT_IDS:
        assert f"### {task_id}：" in text
    assert "Task ID：T9B" not in text
    assert "DG-GEN-" not in text


def test_coverage_matrix_maps_every_dlt_to_design_source_and_tests() -> None:
    rows = _table_rows(_section(_read(TASK_LIST), "## 4. Coverage Matrix"))
    by_task = {row["任务"].split()[0]: row for row in rows}

    assert set(DLT_IDS) <= set(by_task)
    for task_id in DLT_IDS:
        row = by_task[task_id]
        assert "docs/数据层详细设计.md" in row["设计来源"] or "§" in row["设计来源"]
        assert row["目标实现文件/目录"]
        assert "tests/" in row["目标测试文件"]
        assert row["状态"] in {"VERIFIED", "PARTIAL", "BLOCKED"}


def test_authorized_collection_names_match_detailed_design_boundary() -> None:
    task_text = _read(TASK_LIST)
    detail_text = _read(DETAIL_DESIGN)

    for name in AUTHORIZED_COLLECTIONS:
        assert name in task_text
        assert name in detail_text

    boundary_section = _section(task_text, "## 1. 设计来源与硬边界")
    assert "8 个名称" in boundary_section
    assert "OpenViking" in boundary_section
    assert "report 是否继续" in boundary_section
    assert "select 是否成功" in boundary_section


def test_crypto_source_note_keeps_binance_kline_and_coingecko_roles_separate() -> None:
    task_text = _read(TASK_LIST)

    assert "Binance Public Data / Binance REST Kline" in task_text
    assert "CoinGecko Pro 是设置页中的增强数据源" in task_text
    assert "不替代 Binance 原始交易所 K 线作为主行情源" in task_text


def test_stop_conditions_preserve_data_layer_red_lines() -> None:
    stop_section = _section(_read(TASK_LIST), "## 7. Stop Conditions")
    required_patterns = (
        r"新增不在详细设计.*Mongo collection",
        r"OpenViking material",
        r"report/select",
        r"cache_hit.*shared_result.*rate_limited.*cached_empty.*cooldown_skipped",
        r"sdk_http_unknown",
        r"evidence_write_failed",
    )

    for pattern in required_patterns:
        assert re.search(pattern, stop_section, flags=re.DOTALL), f"missing stop condition: {pattern}"


def test_design_sources_keep_goal_and_current_implementation_separate() -> None:
    detail_text = _read(DETAIL_DESIGN)
    overall_text = _read(OVERALL_DESIGN)

    assert "目标详细设计" in detail_text
    assert "当前代码不代表已经全部完成" in overall_text
