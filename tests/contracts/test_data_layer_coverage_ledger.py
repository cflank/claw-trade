from __future__ import annotations

import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
TASK_LIST = REPO_ROOT / "docs" / "数据层实施任务清单.md"
DETAIL_DESIGN = REPO_ROOT / "docs" / "数据层详细设计.md"
OVERALL_DESIGN = REPO_ROOT / "docs" / "数据层总体设计.md"

ALLOWED_LEDGER_STATUSES = {"覆盖", "BLOCKED"}
EXPECTED_TASK_IDS = tuple(f"T{index}" for index in range(15)) + ("T9A", "T9B")
EXPECTED_COLLECTIONS = {
    "openbb_provider_manifests",
    "openbb_provider_validation_receipts",
    "openbb_run_provider_plans",
    "openbb_provider_attempts",
    "openbb_provider_http_evidence",
    "openbb_raw_payloads",
    "openbb_normalized",
    "openbb_cache_entries",
    "openbb_rate_limits",
    "openbb_single_flight_calls",
    "crypto_lens_analysis_evidence",
}


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def _section(text: str, heading: str) -> str:
    start = text.index(heading)
    next_heading = re.search(r"\n##\s+", text[start + len(heading) :])
    if next_heading is None:
        return text[start:]
    end = start + len(heading) + next_heading.start()
    return text[start:end]


def _table_rows(section_text: str) -> list[dict[str, str]]:
    lines = [line.strip() for line in section_text.splitlines() if line.strip().startswith("|")]
    header: list[str] | None = None
    rows: list[dict[str, str]] = []
    for line in lines:
        cells = [cell.strip() for cell in line.strip("|").split("|")]
        if not cells:
            continue
        if set(cells[0]) <= {"-", ":"}:
            continue
        if header is None:
            header = cells
            continue
        if len(cells) != len(header):
            continue
        rows.append(dict(zip(header, cells, strict=True)))
    return rows


def _coverage_rows() -> list[dict[str, str]]:
    return _table_rows(_section(_read(TASK_LIST), "## 3. Coverage Matrix"))


def _coverage_by_design_item() -> dict[str, dict[str, str]]:
    return {row["设计项"]: row for row in _coverage_rows()}


def _task_ids(cell: str) -> set[str]:
    return set(re.findall(r"\bT(?:\d+|9A|9B)(?:-T\d+)?\b", cell))


def test_data_layer_task_list_contains_all_planned_task_ids() -> None:
    text = _read(TASK_LIST)

    for task_id in EXPECTED_TASK_IDS:
        assert f"Task ID：{task_id}" in text
        assert re.search(rf"^### {re.escape(task_id)}[：:`]", text, flags=re.MULTILINE)


def test_all_dg_requirements_are_mapped_in_task_coverage_matrix() -> None:
    detail_text = _read(DETAIL_DESIGN)
    matrix = _coverage_by_design_item()
    expected_dg_ids = set(re.findall(r"\bDG-[A-Z]+-\d{3}\b", detail_text))

    missing = sorted(dg_id for dg_id in expected_dg_ids if dg_id not in matrix)
    assert not missing
    for dg_id in expected_dg_ids:
        row = matrix[dg_id]
        assert _task_ids(row["覆盖任务 ID"]), f"{dg_id} has no task mapping"


def test_design_sections_are_mapped_in_task_coverage_matrix() -> None:
    matrix = _coverage_by_design_item()

    for index in range(1, 21):
        design_item = f"§5.{index}"
        matches = [row for key, row in matrix.items() if key.startswith(design_item)]
        assert matches, f"{design_item} is not mapped"
        for row in matches:
            assert _task_ids(row["覆盖任务 ID"]), f"{row['设计项']} has no task mapping"

    for section_index in range(6, 20):
        design_item = f"§{section_index}"
        matches = [row for key, row in matrix.items() if key.startswith(design_item)]
        assert matches, f"{design_item} is not mapped"
        for row in matches:
            assert _task_ids(row["覆盖任务 ID"]), f"{row['设计项']} has no task mapping"

    deviation_rows = [row for key, row in matrix.items() if key.startswith("§22")]
    assert deviation_rows
    for row in deviation_rows:
        assert _task_ids(row["覆盖任务 ID"])


def test_coverage_matrix_statuses_are_limited_to_covered_or_blocked() -> None:
    for row in _coverage_rows():
        assert row["覆盖状态"] in ALLOWED_LEDGER_STATUSES


def test_blocked_items_cannot_be_rewritten_as_covered_completion() -> None:
    text = _read(TASK_LIST)
    matrix = _coverage_by_design_item()

    assert re.search(
        r"### T9B：.*?\n\n- Task ID：T9B\n- 状态：`BLOCKED`",
        text,
        flags=re.DOTALL,
    )
    expected_blocked_rows = {
        "DG-GEN-002",
        "DG-MONGO-003",
        "DG-CRYPTO-004",
        "DG-CRYPTO-005",
        "DG-SELECT-001",
        "DG-SELECT-003",
        "DG-EVIDENCE-002",
        "§5.1 DataRequirement",
        "§5.18 ReportDataPlan",
        "§5.19 SelectDataPlan",
        "§11.2 build_report_data_plan",
        "§11.3 collect_data_requirements",
        "§11.4 merge_duplicate_requirements",
        "§12 select plan/startup/boundary",
        "§14 Crypto 设计",
    }
    for design_item in expected_blocked_rows:
        assert matrix[design_item]["覆盖状态"] == "BLOCKED"


def test_deviation_audit_records_cutover_risks_as_fail_closed() -> None:
    audit_rows = _table_rows(_section(_read(TASK_LIST), "## 4. Deviation Audit"))
    audit_by_item = {row["审计项"]: row for row in audit_rows}

    assert audit_by_item["是否做新旧兜底？"]["结果"] == "否"
    assert "warehouse marker" in audit_by_item["是否做新旧兜底？"]["任务机制"]
    assert audit_by_item["是否新增 fallback success？"]["结果"] == "否"
    assert "fail closed" in audit_by_item["是否新增 fallback success？"]["任务机制"]


def test_stop_conditions_preserve_data_layer_red_lines() -> None:
    stop_section = _section(_read(TASK_LIST), "## 5. Stop Conditions")
    required_patterns = (
        r"新增同义平行 Mongo 主表",
        r"OpenViking 保存 provider cache",
        r"Python 生成、补写、改写或替代 worker/PM 投资结论",
        r"fallback success 掩盖真实失败",
        r"cache hit、shared result、rate limited、cached empty、cooldown skipped、SDK unknown、evidence write failed 写成远端成功",
        r"搜索源、probe、预留配置项或 UI key 填写状态当正式事实源或主链路证据",
        r"放松 truthfulness hard gate",
        r"MongoRateLimitStore",
        r"RateLimitPlanMetadata",
        r"OpenClaw/OpenViking/runtime 行为变更",
    )

    for pattern in required_patterns:
        assert re.search(pattern, stop_section), f"missing stop condition: {pattern}"


def test_existing_collection_names_are_explicit_in_task_list() -> None:
    text = _read(TASK_LIST)

    missing = sorted(collection for collection in EXPECTED_COLLECTIONS if collection not in text)
    assert not missing


def test_forbidden_parallel_store_names_only_appear_as_negative_examples() -> None:
    text = _read(TASK_LIST)
    negative_context_patterns = {
        r"market_bars": r"不新增 `market_bars`",
        r"select_data_plans": r"no `select_data_plans` Mongo collection",
        r"provider cache": r"OpenViking 保存 provider cache",
        r"data_gaps": r"不新增 `market_bars`、`mongo\.normalized_records`、`mongo\.data_gaps`",
    }

    for token_pattern, negative_pattern in negative_context_patterns.items():
        assert re.search(token_pattern, text)
        assert re.search(negative_pattern, text)


def test_anti_fake_keywords_are_locked_to_prohibition_or_negative_scan_context() -> None:
    text = _read(TASK_LIST)
    required_prohibitions = (
        "无 mock / stub / fake / skeleton / placeholder success",
        "测试替身只能用于单元隔离，不能作为产品成功证据",
        "禁止用 mock/stub/fake/skeleton/placeholder success 作为设计项完成证据",
        "禁止把 Crypto history policy、单币 `/report` 成功、UI probe、旧 raw cache、hard-coded symbols、mock/stub/fake/skeleton/placeholder success 当作历史包完成",
        "fallback success 掩盖真实失败",
    )
    forbidden_allow_phrases = (
        "fallback success 可成功",
        "允许 fallback success",
        "fallback success allowed",
        "mock/stub/fake/skeleton/placeholder success 作为产品成功证据",
        "fake success allowed",
    )

    for phrase in required_prohibitions:
        assert phrase in text
    for phrase in forbidden_allow_phrases:
        assert phrase not in text


def test_design_sources_required_by_t0_are_present() -> None:
    task_text = _read(TASK_LIST)
    detail_text = _read(DETAIL_DESIGN)
    overall_text = _read(OVERALL_DESIGN)

    assert "Coverage Ledger 机制" in task_text
    assert "Coverage Matrix" in task_text
    assert "DataRequirement" in detail_text
    assert "ProviderDisplayDecision" in detail_text
    assert "把 cache hit、shared result、rate limited 写成真实远端成功" in overall_text
