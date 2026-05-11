from __future__ import annotations

from pathlib import Path


DOCS_ROOT = Path("docs")
LEGACY_ROOT = DOCS_ROOT / "reference" / "legacy"
CANONICAL_DOCS = {
    "需求设计.md",
    "架构设计.md",
    "详细设计.md",
    "任务清单.md",
}
OLD_TOP_LEVEL_DOCS = {
    "CN_A_frontline_provider矩阵资料层开发任务清单.md",
    "CN_A_frontline_provider矩阵资料层落地方案.md",
    "CN_A_frontline_provider矩阵资料层详细设计.md",
    "CN_A_fundamental数据服务层开发任务清单.md",
    "CN_A_fundamental数据服务层总体设计.md",
    "CN_A_fundamental数据服务层详细设计.md",
    "CN_A_news数据服务层开发任务清单.md",
    "CN_A_news数据服务层设计方案.md",
    "CN_A_news数据服务层详细设计.md",
    "CN_A_social数据服务层实施覆盖矩阵.md",
    "CN_A_social数据服务层开发任务清单.md",
    "CN_A_social数据服务层设计方案.md",
    "CN_A_social数据服务层详细设计.md",
    "CN_A_social数据服务层部署配置与容量评审.md",
    "claw对齐CN改造参考.md",
    "control详细设计实施任务清单.md",
    "control详细设计方案.md",
    "control迁移方案.md",
    "current_workflow_inventory.md",
    "dev_mongodb_tushare_setup.md",
    "frontline工具资料包改造说明.md",
    "stock_news_agent_design.md",
    "原版prompt.md",
}


def test_docs_are_consolidated_into_four_canonical_entries() -> None:
    actual_top_level_docs = {path.name for path in DOCS_ROOT.glob("*.md")}
    assert CANONICAL_DOCS <= actual_top_level_docs
    assert not (OLD_TOP_LEVEL_DOCS & actual_top_level_docs)
    assert (LEGACY_ROOT / "README.md").exists()
    for filename in OLD_TOP_LEVEL_DOCS:
        assert (LEGACY_ROOT / filename).exists(), f"legacy source document missing: {filename}"


def test_canonical_docs_disclose_legacy_source_boundary() -> None:
    for filename in CANONICAL_DOCS:
        text = (DOCS_ROOT / filename).read_text(encoding="utf-8")
        assert "不是旧文档全文替代" in text
        assert "reference/legacy/README.md" in text

    legacy_index = (LEGACY_ROOT / "README.md").read_text(encoding="utf-8")
    assert "已恢复非 evidence 旧文档：23 份" in legacy_index
    assert "25,585 行" in legacy_index
    assert "不自动恢复旧决策" in legacy_index


def test_canonical_docs_preserve_social_design_and_task_coverage() -> None:
    requirements = (DOCS_ROOT / "需求设计.md").read_text(encoding="utf-8")
    architecture = (DOCS_ROOT / "架构设计.md").read_text(encoding="utf-8")
    details = (DOCS_ROOT / "详细设计.md").read_text(encoding="utf-8")
    tasks = (DOCS_ROOT / "任务清单.md").read_text(encoding="utf-8")

    assert "social_analyst" in requirements
    assert "只有关键词热度" in requirements
    assert "不写情绪指数" in requirements

    assert "MongoDB 不负责" in architecture
    assert "OpenViking" in architecture
    assert "worker 间主链" in architecture

    assert "## 12. Social 详细设计" in details
    assert "雪球" in details
    assert "股吧" in details

    assert "### P4：Social 文本源" in tasks
    assert "T-TST-002" in tasks
    assert "T-TST-003" in tasks


def test_canonical_docs_keep_evidence_directory_out_of_merge() -> None:
    assert (DOCS_ROOT / "evidence").is_dir()
    baseline_files = list((DOCS_ROOT / "evidence").glob("**/tradingagents_cn_*"))
    assert baseline_files, "TradingAgents-CN baseline evidence must remain available"
