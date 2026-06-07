from __future__ import annotations

import re
import subprocess
from pathlib import Path

import pytest
from claw_trade.config.stage_policy import load_stage_policy
from claw_trade.config.workspace import validate_worker_workspace_for_control
from claw_trade.workflow.models import Stage
from claw_trade.workflow.workers import worker_by_id

NEW_CN_A_WORKERS: tuple[str, ...] = (
    "policy_analyst",
    "hot_money_tracker",
    "lockup_watcher",
)

CN_A_CANONICAL_TOOLS = {
    "policy_analyst": "claw_get_policy_pack",
    "hot_money_tracker": "claw_get_hot_money_pack",
    "lockup_watcher": "claw_get_lockup_pack",
}

CN_A_ONLY_US_ALIGNMENT = "not_applicable_cn_a_only_worker"

BASELINE_FILES = {
    "policy_analyst": Path("../TradingAgents-astock/tradingagents/agents/analysts/policy_analyst.py"),
    "hot_money_tracker": Path("../TradingAgents-astock/tradingagents/agents/analysts/hot_money_tracker.py"),
    "lockup_watcher": Path("../TradingAgents-astock/tradingagents/agents/analysts/lockup_watcher.py"),
}

BASELINE_SNIPPETS = {
    "policy_analyst": (
        "A 股是全球最典型的「政策市」",
        "政策 → 行业影响 → 公司业务映射 → 财务影响估算",
        "政策面对该公司的总体评级（重大利好/利好/中性/利空/重大利空）",
    ),
    "hot_money_tracker": (
        "游资与资金流向追踪分析师",
        "量价异动识别",
        "龙虎榜信号",
        "北向资金",
        "资金面总体判断",
    ),
    "lockup_watcher": (
        "解禁与减持监控分析师",
        "解禁规模评估",
        "减持预披露",
        "减持压力总体评级(重大压力/中等压力/轻微压力/无明显压力)",
    ),
}

FORBIDDEN_PROMPT_PROTOCOL_TOKENS = (
    "RuntimeTarget",
    "ReportSubmission",
    "[ApprovedMaterials]",
    "material_id",
    "claim block",
    "openviking_write_material",
    "viking://",
    "URI/hash/L1/L2",
)

TRUTHFULNESS_REDLINE_SNIPPETS = {
    "policy_analyst": (
        "当资料包状态为 blocked/partial/empty",
        "只能写“无法确认”或“未取得可核验证据”",
        "不得把缺口改写成“政策不存在”",
    ),
    "hot_money_tracker": (
        "当关键覆盖组状态为 blocked/partial/empty",
        "只能写“无法确认”或“未取得可核验证据”",
        "不得把缺口外推成“未发生异动”",
        "金额/比例/日期必须逐字沿用资料包原始单位与数值",
        "不得把 CNY 改写为亿元、万元或百分比",
    ),
    "lockup_watcher": (
        "当资料包状态为 blocked/partial/empty",
        "当前无可核验解禁/筹码数据",
        "不得把缺口外推成“无任何解禁记录”",
        "“减持概率极低”",
        "“预计某月分红除息”",
        "禁止输出预测性数值、预计日期、派息金额区间",
        "只能如实陈述“未取得可核验证据，无法确认”",
    ),
}

UNAPPROVED_PROFILES: tuple[str, ...] = ("US", "HK", "CRYPTO")


@pytest.mark.parametrize("worker_id", NEW_CN_A_WORKERS)
def test_cn_a_new_worker_workspace_files_exist_and_validate(worker_id: str) -> None:
    worker_dir = Path("agents") / worker_id
    required = (
        "AGENTS.md",
        "IDENTITY.md",
        "USER.md",
        "SKILLS.md",
        "TOOLS.md",
        "STAGES.yaml",
        "prompt-review.yaml",
        "prompts/CN_A.md",
        "prompts/US.md",
        "prompts/HK.md",
        "prompts/CRYPTO.md",
        "skills/manifest.yaml",
        "skills/claw-trade-stage/SKILL.md",
    )
    for relative in required:
        assert (worker_dir / relative).is_file(), f"{worker_id} 缺少 {relative}"

    workspace_result = validate_worker_workspace_for_control(Path("agents"), worker_id)
    assert workspace_result.ok is True, workspace_result.reason


@pytest.mark.parametrize("worker_id", NEW_CN_A_WORKERS)
def test_cn_a_new_workers_are_frontline_and_cn_a_only_approved(worker_id: str) -> None:
    spec = worker_by_id(worker_id)
    assert spec.stage == Stage.FRONTLINE

    cn_a_policy = load_stage_policy(Path("agents"), worker_id, "CN_A")
    assert cn_a_policy.ok is True and cn_a_policy.policy is not None
    assert cn_a_policy.policy.stage == Stage.FRONTLINE
    assert cn_a_policy.policy.openviking_access == "none"
    assert cn_a_policy.policy.tool_intents == (CN_A_CANONICAL_TOOLS[worker_id],)

    for profile in UNAPPROVED_PROFILES:
        result = load_stage_policy(Path("agents"), worker_id, profile)
        assert result.ok is False
        assert "profile is not approved" in (result.reason or "")


@pytest.mark.parametrize("worker_id", NEW_CN_A_WORKERS)
def test_cn_a_prompt_front_matter_and_tool_boundary(worker_id: str) -> None:
    prompt_path = Path("agents") / worker_id / "prompts" / "CN_A.md"
    text = prompt_path.read_text(encoding="utf-8")
    front_matter = _front_matter(prompt_path)

    assert front_matter["profile"] == "CN_A"
    assert front_matter["profile_status"] == "approved"
    assert front_matter["worker_id"] == worker_id
    assert front_matter["stage"] == "frontline"

    assert CN_A_CANONICAL_TOOLS[worker_id] in text
    assert "可用工具" in text
    assert "最终报告正文必须直接从报告标题或正文第一句开始" in text
    assert "数据限制与风险提示" in text
    assert "{company_name}" in text
    assert "{ticker}" in text
    assert "{current_date}" in text
    assert "Markdown 表格" in text
    for snippet in TRUTHFULNESS_REDLINE_SNIPPETS[worker_id]:
        assert snippet in text

    for token in FORBIDDEN_PROMPT_PROTOCOL_TOKENS:
        assert token not in text, f"{worker_id} prompt 出现协议文本 {token!r}"


@pytest.mark.parametrize("worker_id", NEW_CN_A_WORKERS)
def test_unapproved_profile_prompts_fail_closed_without_fallback(worker_id: str) -> None:
    for profile in UNAPPROVED_PROFILES:
        prompt_path = Path("agents") / worker_id / "prompts" / f"{profile}.md"
        text = prompt_path.read_text(encoding="utf-8")
        front_matter = _front_matter(prompt_path)

        assert front_matter["profile"] == profile
        assert front_matter["profile_status"] == "unapproved"
        assert "not been approved" in text
        assert "Fail explicitly" in text
        assert "Do not fallback" in text


@pytest.mark.parametrize("worker_id", NEW_CN_A_WORKERS)
def test_baseline_files_are_readable_and_prompt_keeps_role_snippets(worker_id: str) -> None:
    baseline_path = BASELINE_FILES[worker_id]
    assert baseline_path.is_file(), f"missing baseline file: {baseline_path}"
    baseline_text = baseline_path.read_text(encoding="utf-8")
    for snippet in BASELINE_SNIPPETS[worker_id]:
        assert snippet in baseline_text, f"baseline missing snippet: {snippet!r}"

    prompt_text = (Path("agents") / worker_id / "prompts" / "CN_A.md").read_text(encoding="utf-8")
    if worker_id == "policy_analyst":
        assert "重大利好 / 利好 / 中性 / 利空 / 重大利空" in prompt_text
    if worker_id == "hot_money_tracker":
        assert "主力流入 / 主力流出 / 资金博弈 / 无明显信号" in prompt_text
    if worker_id == "lockup_watcher":
        assert "重大压力 / 中等压力 / 轻微压力 / 无明显压力" in prompt_text


@pytest.mark.parametrize("worker_id", NEW_CN_A_WORKERS)
def test_skill_manifest_exports_only_cn_a_canonical_tool(worker_id: str) -> None:
    manifest = (Path("agents") / worker_id / "skills" / "manifest.yaml").read_text(encoding="utf-8")
    exported = re.findall(r"tool_exports:\n\s*-\s*([A-Za-z0-9_]+)", manifest)
    assert exported == [CN_A_CANONICAL_TOOLS[worker_id]]
    assert "claw-trade-stage/SKILL.md" in manifest


@pytest.mark.parametrize("worker_id", NEW_CN_A_WORKERS)
def test_prompt_review_marks_new_workers_as_cn_a_only(worker_id: str) -> None:
    review = _simple_yaml(Path("agents") / worker_id / "prompt-review.yaml")
    assert review["us_alignment"] == CN_A_ONLY_US_ALIGNMENT
    assert review["cn_a_alignment"] == "TradingAgents-CN"


def test_tradingagents_astock_commit_is_fixed_for_cn_a_worker_baseline() -> None:
    result = subprocess.run(
        ["git", "-C", "../TradingAgents-astock", "rev-parse", "HEAD"],
        check=True,
        capture_output=True,
        text=True,
    )
    assert result.stdout.strip() == "661ccffa812f5182079f604838d4eea3b4abc7ea"


def _front_matter(path: Path) -> dict[str, str]:
    text = path.read_text(encoding="utf-8")
    lines = text.splitlines()
    assert lines and lines[0] == "---", f"{path} missing front matter"
    end = lines.index("---", 1)
    return _parse_key_value_lines(lines[1:end])


def _simple_yaml(path: Path) -> dict[str, str]:
    return _parse_key_value_lines(path.read_text(encoding="utf-8").splitlines())


def _parse_key_value_lines(lines: list[str]) -> dict[str, str]:
    result: dict[str, str] = {}
    for line in lines:
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        key, value = line.split(":", 1)
        result[key.strip()] = value.strip()
    return result
