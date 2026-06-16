from __future__ import annotations

import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
DETAIL_DESIGN = REPO_ROOT / "docs" / "数据层详细设计.md"
TASK_LIST = REPO_ROOT / "docs" / "数据层实施任务清单.md"
DATA_GATEWAY_ROOT = REPO_ROOT / "src" / "claw_trade" / "data_gateway"


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def _section(text: str, heading: str) -> str:
    start = text.index(heading)
    next_heading = re.search(r"\n##\s+", text[start + len(heading) :])
    if next_heading is None:
        return text[start:]
    end = start + len(heading) + next_heading.start()
    return text[start:end]


def test_dlt14_design_lists_minimum_closure_order() -> None:
    section = _section(_read(DETAIL_DESIGN), "## 16. 编码落地建议")

    required_order_snippets = (
        "1. 模型合同闭环",
        "2. 仓库命中闭环",
        "3. 非远端状态闭环",
        "4. 单 provider fetch 闭环",
        "5. 批量闭环",
        "6. 后台维护闭环",
        "7. 四市场合同闭环",
    )
    for snippet in required_order_snippets:
        assert snippet in section, f"DLT-14 design source missing required order item: {snippet}"


def test_dlt14_task_boundary_rejects_out_of_scope_completion_evidence() -> None:
    text = _read(TASK_LIST)
    dlt14_row_pattern = re.compile(
        r"DLT-14[：:]\s*最小(?:实现)?闭环顺序.*?不得先实现 report 数据工具、select 控制面、UI display、OpenViking writer 或 truthfulness guard",
        re.DOTALL,
    )
    assert dlt14_row_pattern.search(text), "DLT-14 boundary line missing from task list"


def test_dlt14_requires_data_api_and_model_entry_files_before_completion_claim() -> None:
    required_entry_files = (
        DATA_GATEWAY_ROOT / "models.py",
        DATA_GATEWAY_ROOT / "api.py",
    )
    missing = [str(path.relative_to(REPO_ROOT)) for path in required_entry_files if not path.exists()]
    assert not missing, (
        "DLT-14 blocked: minimum closure order cannot be claimed before models/api exist; "
        f"missing={missing}"
    )


def test_dlt14_forbids_report_select_ui_openviking_guard_modules_as_data_layer_core() -> None:
    if not DATA_GATEWAY_ROOT.exists():
        raise AssertionError("DLT-14 blocked: missing src/claw_trade/data_gateway")

    forbidden_path_parts = {
        "guard",
        "guards",
        "openviking",
        "report",
        "reports",
        "select",
        "selection",
        "truthfulness",
        "ui",
    }
    hits: list[str] = []
    for path in DATA_GATEWAY_ROOT.rglob("*.py"):
        rel = str(path.relative_to(DATA_GATEWAY_ROOT)).lower()
        parts = set(Path(rel).with_suffix("").parts)
        if parts & forbidden_path_parts:
            hits.append(rel)
    assert not hits, (
        "DLT-14 violation: out-of-scope modules found in new data layer core path "
        "(report/select/UI/OpenViking/truthfulness-guard should not be completion evidence): "
        f"{hits}"
    )
