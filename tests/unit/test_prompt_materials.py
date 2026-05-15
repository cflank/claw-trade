from __future__ import annotations

from hashlib import sha256
from pathlib import Path

from claw_trade.artifacts.manifest import ApprovedManifest, ManifestStore
from claw_trade.artifacts.openviking_client import OpenVikingReadResult
from claw_trade.artifacts.refs import ApprovedMaterial, L2Index, make_material_target
from claw_trade.workflow.models import ReadPolicy, Stage, WorkerCall
from claw_trade.workflow.runner import ControlRunner
from claw_trade.workflow.store import WorkflowStore

_MODEL_VISIBLE_PROTOCOL_TOKENS = (
    "artifact",
    "material_id",
    "capability",
    "receipt",
    "hash",
    "ApprovedMaterials",
    "RuntimeTarget",
    "ReportSubmission",
    "viking://",
)


class _OpenClaw:
    def probe(self) -> object:
        return object()

    def run_worker(self, command: object) -> object:
        raise AssertionError("本测试不应真实运行 OpenClaw")


class _OpenViking:
    def __init__(self, texts: dict[str, bytes]) -> None:
        self.texts = texts

    def probe_read_stat_receipt(self) -> object:
        return object()

    def probe_namespace_stat(self) -> object:
        return object()

    def ensure_namespace(self, namespace: str) -> None:
        _ = namespace

    def read_approved_l1(self, material: ApprovedMaterial) -> OpenVikingReadResult:
        content = self.texts[material.material_id]
        return OpenVikingReadResult(
            uri=material.l1_uri,
            ok=True,
            content=content,
            sha256=sha256(content).hexdigest(),
            size_bytes=len(content),
            error_category=None,
            error_message=None,
        )


def test_cn_a_bear_prompt_materials_inline_full_l1_reports(tmp_path: Path) -> None:
    run_id = "run-prompt-materials"
    source_texts = {
        ("market_analyst", Stage.FRONTLINE): "# 市场分析\n完整市场报告正文",
        ("fundamental_analyst", Stage.FRONTLINE): "# 基本面分析\n完整基本面报告正文",
        ("news_analyst", Stage.FRONTLINE): "# 新闻分析\n完整新闻报告正文",
        ("social_analyst", Stage.FRONTLINE): "# 社交舆情\n完整舆情报告正文",
        ("bull_researcher", Stage.INVESTMENT_DEBATE): "# 多方观点\n完整多方报告正文",
    }
    materials = [
        _material(tmp_path, run_id, worker_id, stage, text)
        for (worker_id, stage), text in source_texts.items()
    ]
    manifest = ApprovedManifest.empty()
    for material in materials:
        manifest.add(material)

    openviking = _OpenViking(
        {material.material_id: source_texts[(material.worker_id, material.stage)].encode("utf-8") for material in materials}
    )
    runner = ControlRunner(
        store=WorkflowStore(tmp_path / "runs"),
        manifest_store=ManifestStore(tmp_path / "runs"),
        openclaw=_OpenClaw(),  # type: ignore[arg-type]
        openviking=openviking,
    )
    call = _worker_call(
        tmp_path=tmp_path,
        run_id=run_id,
        worker_id="bear_researcher",
        upstream_materials=manifest.for_worker_call(
            stage=Stage.INVESTMENT_DEBATE,
            worker_id="bear_researcher",
            run_id=run_id,
            turn_index=1,
        ),
        openviking_read_capabilities=manifest.capabilities_for_worker_call(
            stage=Stage.INVESTMENT_DEBATE,
            worker_id="bear_researcher",
            run_id=run_id,
            turn_index=1,
        ),
        turn_index=1,
    )

    result = runner.attach_prompt_materials(call=call, manifest=manifest)

    assert result.ok is True
    assert result.call is not None
    prompt_vars = result.call.prompt_runtime_vars
    assert prompt_vars["market_research_report"] == "# 市场分析\n完整市场报告正文"
    assert prompt_vars["fundamentals_report"] == "# 基本面分析\n完整基本面报告正文"
    assert prompt_vars["news_report"] == "# 新闻分析\n完整新闻报告正文"
    assert prompt_vars["sentiment_report"] == "# 社交舆情\n完整舆情报告正文"
    assert prompt_vars["current_response"] == "Bull Analyst: # 多方观点\n完整多方报告正文"
    assert prompt_vars["history"] == "\nBull Analyst: # 多方观点\n完整多方报告正文"
    _assert_no_model_visible_protocol(prompt_vars)


def test_us_bear_prompt_materials_inline_full_l1_reports(tmp_path: Path) -> None:
    run_id = "run-us-prompt-materials"
    source_texts = {
        ("market_analyst", Stage.FRONTLINE): "# Market Analysis\nFull market report body",
        ("fundamental_analyst", Stage.FRONTLINE): "# Fundamentals\nFull fundamentals report body",
        ("news_analyst", Stage.FRONTLINE): "# News\nFull news report body",
        ("social_analyst", Stage.FRONTLINE): "# Sentiment\nFull sentiment report body",
        ("bull_researcher", Stage.INVESTMENT_DEBATE): "# Bull Case\nFull bull argument body",
    }
    materials = [
        _material(tmp_path, run_id, worker_id, stage, text)
        for (worker_id, stage), text in source_texts.items()
    ]
    manifest = ApprovedManifest.empty()
    for material in materials:
        manifest.add(material)

    openviking = _OpenViking(
        {material.material_id: source_texts[(material.worker_id, material.stage)].encode("utf-8") for material in materials}
    )
    runner = ControlRunner(
        store=WorkflowStore(tmp_path / "runs"),
        manifest_store=ManifestStore(tmp_path / "runs"),
        openclaw=_OpenClaw(),  # type: ignore[arg-type]
        openviking=openviking,
    )
    call = _worker_call(
        tmp_path=tmp_path,
        run_id=run_id,
        worker_id="bear_researcher",
        profile="US",
        upstream_materials=manifest.for_worker_call(
            stage=Stage.INVESTMENT_DEBATE,
            worker_id="bear_researcher",
            run_id=run_id,
            turn_index=1,
        ),
        openviking_read_capabilities=manifest.capabilities_for_worker_call(
            stage=Stage.INVESTMENT_DEBATE,
            worker_id="bear_researcher",
            run_id=run_id,
            turn_index=1,
        ),
        turn_index=1,
    )

    result = runner.attach_prompt_materials(call=call, manifest=manifest)

    assert result.ok is True
    assert result.call is not None
    prompt_vars = result.call.prompt_runtime_vars
    assert prompt_vars["market_research_report"] == "# Market Analysis\nFull market report body"
    assert prompt_vars["fundamentals_report"] == "# Fundamentals\nFull fundamentals report body"
    assert prompt_vars["news_report"] == "# News\nFull news report body"
    assert prompt_vars["sentiment_report"] == "# Sentiment\nFull sentiment report body"
    assert prompt_vars["current_response"] == "Bull Analyst: # Bull Case\nFull bull argument body"
    assert prompt_vars["history"] == "\nBull Analyst: # Bull Case\nFull bull argument body"
    _assert_no_model_visible_protocol(prompt_vars)


def test_cn_a_research_manager_prompt_materials_inline_frontline_and_debate(tmp_path: Path) -> None:
    run_id = "run-research-manager-materials"
    manifest, openviking = _manifest_with_texts(
        tmp_path,
        run_id,
        {
            ("market_analyst", Stage.FRONTLINE): "# 市场分析\n完整市场报告正文",
            ("fundamental_analyst", Stage.FRONTLINE): "# 基本面分析\n完整基本面报告正文",
            ("news_analyst", Stage.FRONTLINE): "# 新闻分析\n完整新闻报告正文",
            ("social_analyst", Stage.FRONTLINE): "# 社交舆情\n完整舆情报告正文",
            ("bull_researcher", Stage.INVESTMENT_DEBATE): "# 多方观点\n完整多方报告正文",
            ("bear_researcher", Stage.INVESTMENT_DEBATE): "# 空方观点\n完整空方报告正文",
        },
    )
    runner = ControlRunner(
        store=WorkflowStore(tmp_path / "runs"),
        manifest_store=ManifestStore(tmp_path / "runs"),
        openclaw=_OpenClaw(),  # type: ignore[arg-type]
        openviking=openviking,
    )
    call = _worker_call(
        tmp_path=tmp_path,
        run_id=run_id,
        worker_id="research_manager",
        stage=Stage.INVESTMENT_DECISION,
        upstream_materials=manifest.for_worker_call(
            stage=Stage.INVESTMENT_DECISION,
            worker_id="research_manager",
            run_id=run_id,
        ),
        openviking_read_capabilities=manifest.capabilities_for_worker_call(
            stage=Stage.INVESTMENT_DECISION,
            worker_id="research_manager",
            run_id=run_id,
        ),
    )

    result = runner.attach_prompt_materials(call=call, manifest=manifest)

    assert result.ok is True
    assert result.call is not None
    prompt_vars = result.call.prompt_runtime_vars
    assert prompt_vars["market_research_report"] == "# 市场分析\n完整市场报告正文"
    assert prompt_vars["fundamentals_report"] == "# 基本面分析\n完整基本面报告正文"
    assert prompt_vars["news_report"] == "# 新闻分析\n完整新闻报告正文"
    assert prompt_vars["sentiment_report"] == "# 社交舆情\n完整舆情报告正文"
    assert prompt_vars["history"] == (
        "\nBull Analyst: # 多方观点\n完整多方报告正文"
        "\nBear Analyst: # 空方观点\n完整空方报告正文"
    )
    assert prompt_vars["past_memory_str"] == ""
    _assert_no_model_visible_protocol(prompt_vars)


def test_us_research_manager_prompt_materials_use_debate_only(tmp_path: Path) -> None:
    run_id = "run-us-research-manager-materials"
    manifest, openviking = _manifest_with_texts(
        tmp_path,
        run_id,
        {
            ("market_analyst", Stage.FRONTLINE): "# Market Analysis\nFull market report body",
            ("fundamental_analyst", Stage.FRONTLINE): "# Fundamentals\nFull fundamentals report body",
            ("news_analyst", Stage.FRONTLINE): "# News\nFull news report body",
            ("social_analyst", Stage.FRONTLINE): "# Sentiment\nFull sentiment report body",
            ("bull_researcher", Stage.INVESTMENT_DEBATE): "# Bull Case\nFull bull argument body",
            ("bear_researcher", Stage.INVESTMENT_DEBATE): "# Bear Case\nFull bear argument body",
        },
    )
    runner = ControlRunner(
        store=WorkflowStore(tmp_path / "runs"),
        manifest_store=ManifestStore(tmp_path / "runs"),
        openclaw=_OpenClaw(),  # type: ignore[arg-type]
        openviking=openviking,
    )
    call = _worker_call(
        tmp_path=tmp_path,
        run_id=run_id,
        worker_id="research_manager",
        stage=Stage.INVESTMENT_DECISION,
        profile="US",
        upstream_materials=manifest.for_worker_call(
            stage=Stage.INVESTMENT_DECISION,
            worker_id="research_manager",
            run_id=run_id,
        ),
        openviking_read_capabilities=manifest.capabilities_for_worker_call(
            stage=Stage.INVESTMENT_DECISION,
            worker_id="research_manager",
            run_id=run_id,
        ),
    )

    result = runner.attach_prompt_materials(call=call, manifest=manifest)

    assert result.ok is True
    assert result.call is not None
    prompt_vars = result.call.prompt_runtime_vars
    assert set(prompt_vars) == {"history", "past_memory_str"}
    assert prompt_vars["history"] == (
        "\nBull Analyst: # Bull Case\nFull bull argument body"
        "\nBear Analyst: # Bear Case\nFull bear argument body"
    )
    assert prompt_vars["past_memory_str"] == ""
    _assert_no_model_visible_protocol(prompt_vars)


def test_cn_a_risk_moderator_prompt_materials_inline_trader_and_prior_risk(tmp_path: Path) -> None:
    run_id = "run-risk-moderator-materials"
    manifest, openviking = _manifest_with_texts(
        tmp_path,
        run_id,
        {
            ("market_analyst", Stage.FRONTLINE): "# 市场分析\n完整市场报告正文",
            ("fundamental_analyst", Stage.FRONTLINE): "# 基本面分析\n完整基本面报告正文",
            ("news_analyst", Stage.FRONTLINE): "# 新闻分析\n完整新闻报告正文",
            ("social_analyst", Stage.FRONTLINE): "# 社交舆情\n完整舆情报告正文",
            ("trader", Stage.TRADE_DECISION): "# 交易决策\n完整交易员报告正文",
            ("risk_challenger", Stage.RISK_DEBATE): "# 激进风险\n完整激进报告正文",
            ("risk_guardian", Stage.RISK_DEBATE): "# 保守风险\n完整保守报告正文",
        },
    )
    runner = ControlRunner(
        store=WorkflowStore(tmp_path / "runs"),
        manifest_store=ManifestStore(tmp_path / "runs"),
        openclaw=_OpenClaw(),  # type: ignore[arg-type]
        openviking=openviking,
    )
    call = _worker_call(
        tmp_path=tmp_path,
        run_id=run_id,
        worker_id="risk_moderator",
        stage=Stage.RISK_DEBATE,
        upstream_materials=manifest.for_worker_call(
            stage=Stage.RISK_DEBATE,
            worker_id="risk_moderator",
            run_id=run_id,
            turn_index=2,
        ),
        openviking_read_capabilities=manifest.capabilities_for_worker_call(
            stage=Stage.RISK_DEBATE,
            worker_id="risk_moderator",
            run_id=run_id,
            turn_index=2,
        ),
        turn_index=2,
    )

    result = runner.attach_prompt_materials(call=call, manifest=manifest)

    assert result.ok is True
    assert result.call is not None
    prompt_vars = result.call.prompt_runtime_vars
    assert prompt_vars["trader_decision"] == "# 交易决策\n完整交易员报告正文"
    assert prompt_vars["current_risky_response"] == "Risky Analyst: # 激进风险\n完整激进报告正文"
    assert prompt_vars["current_safe_response"] == "Safe Analyst: # 保守风险\n完整保守报告正文"
    assert prompt_vars["history"] == (
        "\nRisky Analyst: # 激进风险\n完整激进报告正文"
        "\nSafe Analyst: # 保守风险\n完整保守报告正文"
    )
    _assert_no_model_visible_protocol(prompt_vars)


def test_cn_a_portfolio_manager_prompt_materials_use_research_plan_and_risk_history(tmp_path: Path) -> None:
    run_id = "run-portfolio-materials"
    manifest, openviking = _manifest_with_texts(
        tmp_path,
        run_id,
        {
            ("research_manager", Stage.INVESTMENT_DECISION): "# 投资计划\n完整研究经理报告正文",
            ("trader", Stage.TRADE_DECISION): "# 交易决策\n完整交易员报告正文",
            ("risk_challenger", Stage.RISK_DEBATE): "# 激进风险\n完整激进报告正文",
            ("risk_guardian", Stage.RISK_DEBATE): "# 保守风险\n完整保守报告正文",
            ("risk_moderator", Stage.RISK_DEBATE): "# 中性风险\n完整中性报告正文",
        },
    )
    runner = ControlRunner(
        store=WorkflowStore(tmp_path / "runs"),
        manifest_store=ManifestStore(tmp_path / "runs"),
        openclaw=_OpenClaw(),  # type: ignore[arg-type]
        openviking=openviking,
    )
    call = _worker_call(
        tmp_path=tmp_path,
        run_id=run_id,
        worker_id="portfolio_manager",
        stage=Stage.PORTFOLIO_DECISION,
        upstream_materials=manifest.for_worker_call(
            stage=Stage.PORTFOLIO_DECISION,
            worker_id="portfolio_manager",
            run_id=run_id,
        ),
        openviking_read_capabilities=manifest.capabilities_for_worker_call(
            stage=Stage.PORTFOLIO_DECISION,
            worker_id="portfolio_manager",
            run_id=run_id,
        ),
    )

    result = runner.attach_prompt_materials(call=call, manifest=manifest)

    assert result.ok is True
    assert result.call is not None
    prompt_vars = result.call.prompt_runtime_vars
    assert prompt_vars["currency"] == "CNY"
    assert prompt_vars["currency_symbol"] == "¥"
    assert prompt_vars["trader_plan"] == "# 投资计划\n完整研究经理报告正文"
    assert prompt_vars["research_plan"] == "# 投资计划\n完整研究经理报告正文"
    assert prompt_vars["trader_decision"] == "# 交易决策\n完整交易员报告正文"
    assert prompt_vars["history"] == (
        "\nRisky Analyst: # 激进风险\n完整激进报告正文"
        "\nSafe Analyst: # 保守风险\n完整保守报告正文"
        "\nNeutral Analyst: # 中性风险\n完整中性报告正文"
    )
    assert prompt_vars["past_memory_str"] == ""
    _assert_no_model_visible_protocol(prompt_vars)


def test_cn_a_report_polisher_prompt_materials_inline_full_final_report_inputs(tmp_path: Path) -> None:
    run_id = "run-report-polisher-materials"
    manifest, openviking = _manifest_with_texts(
        tmp_path,
        run_id,
        {
            ("market_analyst", Stage.FRONTLINE): "# 市场分析\n完整市场报告正文",
            ("fundamental_analyst", Stage.FRONTLINE): "# 基本面分析\n完整基本面报告正文",
            ("news_analyst", Stage.FRONTLINE): "# 新闻分析\n完整新闻报告正文",
            ("social_analyst", Stage.FRONTLINE): "# 社交舆情\n完整舆情报告正文",
            ("bull_researcher", Stage.INVESTMENT_DEBATE): "# 多方观点\n完整多方报告正文",
            ("bear_researcher", Stage.INVESTMENT_DEBATE): "# 空方观点\n完整空方报告正文",
            ("research_manager", Stage.INVESTMENT_DECISION): "# 投资计划\n完整研究经理报告正文",
            ("trader", Stage.TRADE_DECISION): "# 交易决策\n完整交易员报告正文",
            ("risk_challenger", Stage.RISK_DEBATE): "# 激进风险\n完整激进报告正文",
            ("risk_guardian", Stage.RISK_DEBATE): "# 保守风险\n完整保守报告正文",
            ("risk_moderator", Stage.RISK_DEBATE): "# 中性风险\n完整中性报告正文",
            ("portfolio_manager", Stage.PORTFOLIO_DECISION): "# 组合经理\n完整组合经理最终裁决正文",
        },
    )
    runner = ControlRunner(
        store=WorkflowStore(tmp_path / "runs"),
        manifest_store=ManifestStore(tmp_path / "runs"),
        openclaw=_OpenClaw(),  # type: ignore[arg-type]
        openviking=openviking,
    )
    call = _worker_call(
        tmp_path=tmp_path,
        run_id=run_id,
        worker_id="report_polisher",
        stage=Stage.FINAL_REPORT,
        upstream_materials=manifest.for_worker_call(
            stage=Stage.FINAL_REPORT,
            worker_id="report_polisher",
            run_id=run_id,
        ),
        openviking_read_capabilities=manifest.capabilities_for_worker_call(
            stage=Stage.FINAL_REPORT,
            worker_id="report_polisher",
            run_id=run_id,
        ),
    )

    result = runner.attach_prompt_materials(call=call, manifest=manifest)

    assert result.ok is True
    assert result.call is not None
    prompt_vars = result.call.prompt_runtime_vars
    assert prompt_vars["portfolio_manager_report"] == "# 组合经理\n完整组合经理最终裁决正文"
    assert prompt_vars["market_analyst_report"] == "# 市场分析\n完整市场报告正文"
    assert prompt_vars["fundamental_analyst_report"] == "# 基本面分析\n完整基本面报告正文"
    assert prompt_vars["news_analyst_report"] == "# 新闻分析\n完整新闻报告正文"
    assert prompt_vars["social_analyst_report"] == "# 社交舆情\n完整舆情报告正文"
    assert prompt_vars["trader_report"] == "# 交易决策\n完整交易员报告正文"
    assert "### 多头研究员\n# 多方观点\n完整多方报告正文" in prompt_vars["supporting_worker_reports"]
    assert "### 风险整合方\n# 中性风险\n完整中性报告正文" in prompt_vars["supporting_worker_reports"]
    _assert_no_model_visible_protocol(prompt_vars)


def test_us_report_polisher_prompt_materials_inline_full_final_report_inputs(tmp_path: Path) -> None:
    run_id = "run-us-report-polisher-materials"
    manifest, openviking = _manifest_with_texts(
        tmp_path,
        run_id,
        {
            ("market_analyst", Stage.FRONTLINE): "# Market\nFull market report body",
            ("fundamental_analyst", Stage.FRONTLINE): "# Fundamentals\nFull fundamentals report body",
            ("news_analyst", Stage.FRONTLINE): "# News\nFull news report body",
            ("social_analyst", Stage.FRONTLINE): "# Sentiment\nFull sentiment report body",
            ("bull_researcher", Stage.INVESTMENT_DEBATE): "# Bull\nFull bull report body",
            ("bear_researcher", Stage.INVESTMENT_DEBATE): "# Bear\nFull bear report body",
            ("research_manager", Stage.INVESTMENT_DECISION): "# Investment Plan\nFull research manager body",
            ("trader", Stage.TRADE_DECISION): "# Trading Plan\nFull trader body",
            ("risk_challenger", Stage.RISK_DEBATE): "# Aggressive Risk\nFull aggressive risk body",
            ("risk_guardian", Stage.RISK_DEBATE): "# Conservative Risk\nFull conservative risk body",
            ("risk_moderator", Stage.RISK_DEBATE): "# Neutral Risk\nFull neutral risk body",
            ("portfolio_manager", Stage.PORTFOLIO_DECISION): "# Portfolio Manager\nFull PM final decision body",
        },
    )
    runner = ControlRunner(
        store=WorkflowStore(tmp_path / "runs"),
        manifest_store=ManifestStore(tmp_path / "runs"),
        openclaw=_OpenClaw(),  # type: ignore[arg-type]
        openviking=openviking,
    )
    call = _worker_call(
        tmp_path=tmp_path,
        run_id=run_id,
        worker_id="report_polisher",
        profile="US",
        stage=Stage.FINAL_REPORT,
        upstream_materials=manifest.for_worker_call(
            stage=Stage.FINAL_REPORT,
            worker_id="report_polisher",
            run_id=run_id,
        ),
        openviking_read_capabilities=manifest.capabilities_for_worker_call(
            stage=Stage.FINAL_REPORT,
            worker_id="report_polisher",
            run_id=run_id,
        ),
    )

    result = runner.attach_prompt_materials(call=call, manifest=manifest)

    assert result.ok is True
    assert result.call is not None
    prompt_vars = result.call.prompt_runtime_vars
    assert prompt_vars["portfolio_manager_report"] == "# Portfolio Manager\nFull PM final decision body"
    assert prompt_vars["market_analyst_report"] == "# Market\nFull market report body"
    assert prompt_vars["fundamental_analyst_report"] == "# Fundamentals\nFull fundamentals report body"
    assert prompt_vars["news_analyst_report"] == "# News\nFull news report body"
    assert prompt_vars["social_analyst_report"] == "# Sentiment\nFull sentiment report body"
    assert prompt_vars["trader_report"] == "# Trading Plan\nFull trader body"
    assert "### Bull Researcher\n# Bull\nFull bull report body" in prompt_vars["supporting_worker_reports"]
    assert "### Neutral Risk Analyst\n# Neutral Risk\nFull neutral risk body" in prompt_vars["supporting_worker_reports"]
    assert "technical market analysis section" in prompt_vars["chart_assets_note"]
    _assert_no_model_visible_protocol(prompt_vars)


def test_cn_a_research_manager_prompt_materials_preserve_multiple_debate_rounds(tmp_path: Path) -> None:
    run_id = "run-research-manager-multi-round"
    source_items = (
        ("market_analyst", Stage.FRONTLINE, "# 市场分析\n完整市场报告正文", 0, 1, 1),
        ("fundamental_analyst", Stage.FRONTLINE, "# 基本面分析\n完整基本面报告正文", 0, 1, 1),
        ("news_analyst", Stage.FRONTLINE, "# 新闻分析\n完整新闻报告正文", 0, 1, 1),
        ("social_analyst", Stage.FRONTLINE, "# 社交舆情\n完整舆情报告正文", 0, 1, 1),
        ("bull_researcher", Stage.INVESTMENT_DEBATE, "# 第一轮多方\n多方第一轮正文", 0, 1, 1),
        ("bear_researcher", Stage.INVESTMENT_DEBATE, "# 第一轮空方\n空方第一轮正文", 1, 1, 1),
        ("bull_researcher", Stage.INVESTMENT_DEBATE, "# 第二轮多方\n多方第二轮正文", 2, 2, 2),
        ("bear_researcher", Stage.INVESTMENT_DEBATE, "# 第二轮空方\n空方第二轮正文", 3, 2, 2),
    )
    materials = [
        _material(
            tmp_path,
            run_id,
            worker_id,
            stage,
            text,
            turn_index=turn_index,
            round_index=round_index,
            role_turn_index=role_turn_index,
        )
        for worker_id, stage, text, turn_index, round_index, role_turn_index in source_items
    ]
    manifest = ApprovedManifest.empty()
    for material in materials:
        manifest.add(material)
    openviking = _OpenViking({material.material_id: source_items[index][2].encode("utf-8") for index, material in enumerate(materials)})
    runner = ControlRunner(
        store=WorkflowStore(tmp_path / "runs"),
        manifest_store=ManifestStore(tmp_path / "runs"),
        openclaw=_OpenClaw(),  # type: ignore[arg-type]
        openviking=openviking,
    )
    call = _worker_call(
        tmp_path=tmp_path,
        run_id=run_id,
        worker_id="research_manager",
        stage=Stage.INVESTMENT_DECISION,
        upstream_materials=manifest.for_worker_call(
            stage=Stage.INVESTMENT_DECISION,
            worker_id="research_manager",
            run_id=run_id,
        ),
        openviking_read_capabilities=manifest.capabilities_for_worker_call(
            stage=Stage.INVESTMENT_DECISION,
            worker_id="research_manager",
            run_id=run_id,
        ),
    )

    result = runner.attach_prompt_materials(call=call, manifest=manifest)

    assert result.ok is True
    assert result.call is not None
    history = result.call.prompt_runtime_vars["history"]
    assert "Bull Analyst: # 第一轮多方\n多方第一轮正文" in history
    assert "Bear Analyst: # 第一轮空方\n空方第一轮正文" in history
    assert "Bull Analyst: # 第二轮多方\n多方第二轮正文" in history
    assert "Bear Analyst: # 第二轮空方\n空方第二轮正文" in history
    assert history.index("第一轮多方") < history.index("第一轮空方")
    assert history.index("第一轮空方") < history.index("第二轮多方")
    assert history.index("第二轮多方") < history.index("第二轮空方")
    _assert_no_model_visible_protocol(result.call.prompt_runtime_vars)


def _assert_no_model_visible_protocol(prompt_vars: dict[str, str]) -> None:
    for value in prompt_vars.values():
        for token in _MODEL_VISIBLE_PROTOCOL_TOKENS:
            assert token not in value


def _manifest_with_texts(
    tmp_path: Path,
    run_id: str,
    source_texts: dict[tuple[str, Stage], str],
) -> tuple[ApprovedManifest, _OpenViking]:
    materials = [
        _material(tmp_path, run_id, worker_id, stage, text)
        for (worker_id, stage), text in source_texts.items()
    ]
    manifest = ApprovedManifest.empty()
    for material in materials:
        manifest.add(material)
    openviking = _OpenViking(
        {material.material_id: source_texts[(material.worker_id, material.stage)].encode("utf-8") for material in materials}
    )
    return manifest, openviking


def _material(
    tmp_path: Path,
    run_id: str,
    worker_id: str,
    stage: Stage,
    text: str,
    turn_index: int = 0,
    round_index: int = 1,
    role_turn_index: int = 1,
) -> ApprovedMaterial:
    call_id = f"call-{stage.value}-{worker_id}-t{turn_index}"
    target = make_material_target(
        run_id=run_id,
        stage=stage,
        worker_id=worker_id,
        call_id=call_id,
        turn_index=turn_index,
        round_index=round_index,
        role_turn_index=role_turn_index,
    )
    content = text.encode("utf-8")
    hard_gate_path = tmp_path / "hard-gates" / f"{stage.value}-{worker_id}-t{turn_index}.json"
    hard_gate_path.parent.mkdir(parents=True, exist_ok=True)
    hard_gate_path.write_text('{"category":"hard_gate","ok":true}', encoding="utf-8")
    return ApprovedMaterial(
        material_id=f"mat-{stage.value}-{worker_id}-t{turn_index}",
        run_id=run_id,
        call_id=call_id,
        worker_id=worker_id,
        stage=stage,
        target_name=target.target_name,
        l1_uri=target.l1_uri,
        l1_sha256=sha256(content).hexdigest(),
        l1_size_bytes=len(content),
        l2_index_uri=None,
        l2_index=L2Index(
            entries=(),
            empty_reason="无 L2",
            index_uri=None,
            index_sha256=None,
            index_size_bytes=None,
        ),
        l1_claims=(),
        approved_at="2026-05-11T12:00:00Z",
        hard_gate_result_path=hard_gate_path,
        turn_index=turn_index,
        round_index=round_index,
        role_turn_index=role_turn_index,
    )


def _worker_call(
    *,
    tmp_path: Path,
    run_id: str,
    worker_id: str,
    upstream_materials,
    openviking_read_capabilities,
    stage: Stage = Stage.INVESTMENT_DEBATE,
    profile: str = "CN_A",
    turn_index: int = 0,
    round_index: int = 1,
    role_turn_index: int = 1,
) -> WorkerCall:
    call_id = f"call-{worker_id}-t{turn_index}"
    target = make_material_target(
        run_id=run_id,
        stage=stage,
        worker_id=worker_id,
        call_id=call_id,
        turn_index=turn_index,
        round_index=round_index,
        role_turn_index=role_turn_index,
    )
    return WorkerCall(
        call_id=call_id,
        run_id=run_id,
        worker_id=worker_id,
        stage=stage,
        profile=profile,
        ticker="600519",
        company_name="贵州茅台",
        market="CN_A",
        currency="CNY",
        currency_symbol="¥",
        current_date="2026-05-11",
        start_date="2026-04-11",
        end_date="2026-05-11",
        allowed_tools=(),
        upstream_materials=upstream_materials,
        openviking_read_capabilities=openviking_read_capabilities,
        material_target=target,
        read_policy=ReadPolicy(),
        evidence_dir=tmp_path / "runs" / run_id / "calls" / call_id,
        stop_after_first_response=False,
        turn_index=turn_index,
        round_index=round_index,
        role_turn_index=role_turn_index,
    )
