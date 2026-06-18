from __future__ import annotations

from pathlib import Path

import pytest

WORKER_STAGE = {
    "selection_strategist": "selection_review",
    "selection_skeptic": "selection_review",
    "selection_manager": "selection_decision",
    "selection_portfolio_manager": "selection_portfolio_decision",
}

FORBIDDEN_PROMPT_TOKENS = (
    "RuntimeTarget",
    "ReportSubmission",
    "material_id",
    "manifest",
    "lineage",
    "receipt",
    "openviking",
    "openclaw",
    "mongo",
    "provider envelope",
    "raw/debug",
    "uri",
    "sha256",
)


@pytest.mark.parametrize("worker_id", tuple(WORKER_STAGE))
def test_selection_workers_have_required_prompt_files(worker_id: str) -> None:
    worker_dir = Path("agents") / worker_id
    for relative in ("IDENTITY.md", "SKILLS.md", "STAGES.yaml", "prompts/CN_A.md", "prompts/CRYPTO.md"):
        assert (worker_dir / relative).is_file(), f"{worker_id} 缺少 {relative}"


@pytest.mark.parametrize("profile", ("CN_A", "CRYPTO"))
@pytest.mark.parametrize("worker_id", tuple(WORKER_STAGE))
def test_selection_prompt_front_matter_and_boundary(worker_id: str, profile: str) -> None:
    prompt_path = Path("agents") / worker_id / "prompts" / f"{profile}.md"
    text = prompt_path.read_text(encoding="utf-8")
    front_matter = _front_matter(prompt_path)

    assert front_matter["profile"] == profile
    assert front_matter["profile_status"] == "approved"
    assert front_matter["worker_id"] == worker_id
    assert front_matter["stage"] == WORKER_STAGE[worker_id]

    lowered = text.lower()
    for token in FORBIDDEN_PROMPT_TOKENS:
        assert token.lower() not in lowered, f"{worker_id} prompt 出现禁用协议字段: {token}"


def test_strategist_and_skeptic_prompt_use_tool_read_boundary_only() -> None:
    strategist = Path("agents/selection_strategist/prompts/CN_A.md").read_text(encoding="utf-8")
    skeptic = Path("agents/selection_skeptic/prompts/CN_A.md").read_text(encoding="utf-8")

    for text in (strategist, skeptic):
        assert "候选池工具" in text
        assert (
            "首轮提示不预置完整候选池正文" in text
            or "候选池正文不在首轮提示中预置" in text
        )
        assert "工具调用不填写业务参数" in text


def test_manager_and_pm_prompt_forbid_tools_and_requery() -> None:
    manager = Path("agents/selection_manager/prompts/CN_A.md").read_text(encoding="utf-8")
    pm = Path("agents/selection_portfolio_manager/prompts/CN_A.md").read_text(encoding="utf-8")

    for text in (manager, pm):
        assert "当前阶段不使用任何工具" in text
        assert "不重新查数" in text


def test_pm_prompt_keeps_select_three_bucket_semantics_only() -> None:
    pm = Path("agents/selection_portfolio_manager/prompts/CN_A.md").read_text(encoding="utf-8")
    assert "进入 `/report`、观察、放弃" in pm
    assert "不因词面或模板触发 `/select` runtime 失败" in pm
    assert "不自动触发 `/report`" in pm
    assert "不向 `/report` 注入 `/select` PM 结论" in pm
    assert "进入 /report:" in pm
    assert "观察:" in pm
    assert "放弃:" in pm
    assert "不允许追加三分类之外的新标题" in pm


def _front_matter(path: Path) -> dict[str, str]:
    text = path.read_text(encoding="utf-8")
    lines = text.splitlines()
    assert lines and lines[0] == "---", f"{path} 缺少 front matter"
    end = lines.index("---", 1)

    result: dict[str, str] = {}
    for line in lines[1:end]:
        if not line.strip():
            continue
        key, value = line.split(":", 1)
        result[key.strip()] = value.strip()
    return result
