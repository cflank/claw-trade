from __future__ import annotations

import pytest
from claw_trade.ui_backend.report_context import ReportContextRetriever
from claw_trade.ui_backend.report_qa import ReportQaContextPolicy, ReportQuestionService
from claw_trade.ui_backend.report_repository import ReportRepository, UiProductError


class _FakeGateway:
    def __init__(self) -> None:
        self.session_create_calls = 0
        self.chat_calls = 0
        self.last_prompt = ""

    def sessions_create(self, *, metadata: dict[str, object]) -> str:
        self.session_create_calls += 1
        assert metadata["scope"] == "report_qa"
        assert metadata["sessionKey"] == f"ui:report_qa:{metadata['reportId']}"
        assert metadata["label"] == f"ui:report_qa:{metadata['reportId']}"
        return "sess-1"

    def chat_send(self, *, request_id: str, context_id: str, session_id: str, text: str) -> dict[str, object]:
        self.chat_calls += 1
        self.last_prompt = text
        assert request_id
        assert context_id == "ctx-1"
        assert session_id == "sess-1"
        return {"text": "基于报告的回答"}


def _repo_with_report() -> ReportRepository:
    repo = ReportRepository()
    repo.save_succeeded_report(
        report_id="r-qa",
        instrument_code="BTC",
        market="CRYPTO",
        title="BTC 报告",
        markdown="# 报告\n- 要点A\n- 要点B",
    )
    return repo


def test_ask_report_question_uses_openclaw_chat_session_and_report_context() -> None:
    repo = _repo_with_report()
    gateway = _FakeGateway()
    service = ReportQuestionService(
        repo,
        gateway,
        context_policy=ReportQaContextPolicy(max_total_chars=10_000),
    )
    reply = service.ask_report_question(
        report_id="r-qa",
        text="请解释要点A",
        request_id="req-1",
        context_id="ctx-1",
    )
    assert reply["text"] == "基于报告的回答"
    assert "要点A" in gateway.last_prompt
    assert "用户追问：" in gateway.last_prompt
    assert gateway.session_create_calls == 1
    assert gateway.chat_calls == 1


def test_ask_report_question_rejects_overlong_context_without_summarizing() -> None:
    repo = _repo_with_report()
    gateway = _FakeGateway()
    service = ReportQuestionService(
        repo,
        gateway,
        context_policy=ReportQaContextPolicy(max_total_chars=5),
    )
    with pytest.raises(UiProductError) as exc:
        service.ask_report_question(
            report_id="r-qa",
            text="这个问题会超长",
            request_id="req-2",
            context_id="ctx-1",
        )
    assert exc.value.code == "REPORT_CONTEXT_TOO_LONG"
    assert gateway.chat_calls == 0


def test_ask_report_question_uses_worker_l1_context_instead_of_full_report(tmp_path) -> None:
    run_id = "run-qa"
    run_dir = tmp_path / run_id
    (run_dir / "openviking").mkdir(parents=True)
    (run_dir / "reports" / "worker-appendix").mkdir(parents=True)
    manifest = {
        "materials": [
            {
                "worker_id": "fundamental_analyst",
                "stage": "frontline",
                "call_id": "call-fund",
                "l1_uri": f"viking://resources/workflow/{run_id}/frontline/fundamental_analyst/call-fund/report.md",
            }
        ]
    }
    (run_dir / "openviking" / "approved-manifest.json").write_text(
        __import__("json").dumps(manifest),
        encoding="utf-8",
    )
    (run_dir / "reports" / "worker-appendix" / "02-fundamental_analyst.md").write_text(
        "基本面估值：AHR999 需要结合链上信号。\n\n无关长段落：" + ("X" * 5000),
        encoding="utf-8",
    )
    repo = ReportRepository()
    repo.save_succeeded_report(
        report_id=run_id,
        instrument_code="BTC",
        market="CRYPTO",
        title="BTC 报告",
        markdown="# 完整报告\n" + ("整篇正文不应该全部进入 prompt。" * 1000),
    )
    gateway = _FakeGateway()
    service = ReportQuestionService(
        repo,
        gateway,
        context_policy=ReportQaContextPolicy(max_total_chars=100_000),
        context_retriever=ReportContextRetriever(
            run_root=tmp_path,
            openviking_backend_factory=None,
            mongo_uri="",
            max_context_chars=2_000,
        ),
    )

    service.ask_report_question(
        report_id=run_id,
        text="基本面估值为什么缺 AHR999？",
        request_id="req-l1",
        context_id="ctx-1",
    )

    assert "fundamental_analyst L1" in gateway.last_prompt
    assert "AHR999" in gateway.last_prompt
    assert "viking://" not in gateway.last_prompt
    assert "整篇正文不应该全部进入 prompt" not in gateway.last_prompt


def test_ask_report_question_does_not_surface_raw_or_openviking_protocol_context(tmp_path) -> None:
    run_id = "run-qa-protocol"
    run_dir = tmp_path / run_id
    (run_dir / "openviking").mkdir(parents=True)
    (run_dir / "calls" / "call-risk").mkdir(parents=True)
    (run_dir / "openviking" / "approved-manifest.json").write_text(
        __import__("json").dumps(
            {
                "materials": [
                    {
                        "worker_id": "risk_moderator",
                        "stage": "risk_debate",
                        "call_id": "call-risk",
                        "l1_uri": f"viking://resources/workflow/{run_id}/risk_debate/risk_moderator/call-risk/report.md",
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    (run_dir / "openviking" / "lineage-relations.json").write_text(
        __import__("json").dumps(
            {
                "relations": [
                    {
                        "worker_id": "risk_moderator",
                        "from_uri": "mongo://secret/source",
                        "to_uri": "viking://secret/l1",
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    (run_dir / "calls" / "call-risk" / "raw-output.md").write_text(
        "RAW OUTPUT SHOULD NOT BECOME REPORT QA CONTEXT",
        encoding="utf-8",
    )
    repo = ReportRepository()
    repo.save_succeeded_report(
        report_id=run_id,
        instrument_code="BTC",
        market="CRYPTO",
        title="BTC 报告",
        markdown="# 正式报告\n正式报告风险片段：只允许这段进入 prompt。",
    )
    gateway = _FakeGateway()
    service = ReportQuestionService(
        repo,
        gateway,
        context_policy=ReportQaContextPolicy(max_total_chars=100_000),
        context_retriever=ReportContextRetriever(
            run_root=tmp_path,
            openviking_backend_factory=_explode_if_openviking_is_used,
            mongo_uri="mongodb://should-not-be-used",
            mongo_database="should-not-be-used",
            max_context_chars=2_000,
        ),
    )

    service.ask_report_question(
        report_id=run_id,
        text="风险是什么？",
        request_id="req-no-protocol",
        context_id="ctx-1",
    )

    assert "正式报告风险片段" in gateway.last_prompt
    assert "RAW OUTPUT SHOULD NOT BECOME REPORT QA CONTEXT" not in gateway.last_prompt
    assert "OpenViking" not in gateway.last_prompt
    assert "Mongo" not in gateway.last_prompt
    assert "mongo://" not in gateway.last_prompt
    assert "mongo://secret" not in gateway.last_prompt
    assert "viking://" not in gateway.last_prompt
    assert "viking://secret" not in gateway.last_prompt


def test_ask_report_question_sanitizes_saved_report_protocol_text() -> None:
    repo = ReportRepository()
    repo.save_succeeded_report(
        report_id="legacy-protocol",
        instrument_code="BTC",
        market="CRYPTO",
        title="BTC 报告",
        markdown=(
            "# 正式报告\n"
            "正式报告正文保留。 raw-output raw-output.json viking://report/path "
            "mongo://report/source OpenViking Mongo"
        ),
    )
    gateway = _FakeGateway()
    service = ReportQuestionService(
        repo,
        gateway,
        context_policy=ReportQaContextPolicy(max_total_chars=10_000),
    )

    service.ask_report_question(
        report_id="legacy-protocol",
        text="解释正式报告 viking://question/path mongo://question/source OpenViking Mongo raw-output.txt",
        request_id="req-legacy-protocol",
        context_id="ctx-1",
    )

    assert "正式报告正文保留" in gateway.last_prompt
    for forbidden in (
        "raw-output",
        "raw-output.json",
        "viking://",
        "mongo://",
        "report/path",
        "report/source",
        "question/path",
        "question/source",
        "OpenViking",
        "Mongo",
    ):
        assert forbidden not in gateway.last_prompt


def test_ask_report_question_sanitizes_worker_appendix_protocol_text(tmp_path) -> None:
    run_id = "legacy-appendix-protocol"
    run_dir = tmp_path / run_id
    (run_dir / "openviking").mkdir(parents=True)
    (run_dir / "reports" / "worker-appendix").mkdir(parents=True)
    (run_dir / "openviking" / "approved-manifest.json").write_text(
        __import__("json").dumps(
            {
                "materials": [
                    {
                        "worker_id": "market_analyst",
                        "stage": "frontline",
                        "call_id": "call-market",
                        "l1_uri": f"viking://resources/workflow/{run_id}/frontline/market_analyst/call-market/report.md",
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    (run_dir / "reports" / "worker-appendix" / "03-market_analyst.md").write_text(
        "市场附录正文保留。 raw-output.txt viking://appendix/path mongo://appendix/source OpenViking Mongo",
        encoding="utf-8",
    )
    repo = ReportRepository()
    repo.save_succeeded_report(
        report_id=run_id,
        instrument_code="BTC",
        market="CRYPTO",
        title="BTC 报告",
        markdown="# 正式报告\n正式报告正文保留。",
    )
    gateway = _FakeGateway()
    service = ReportQuestionService(
        repo,
        gateway,
        context_policy=ReportQaContextPolicy(max_total_chars=100_000),
        context_retriever=ReportContextRetriever(
            run_root=tmp_path,
            max_context_chars=2_000,
        ),
    )

    service.ask_report_question(
        report_id=run_id,
        text="市场怎么看？",
        request_id="req-legacy-appendix-protocol",
        context_id="ctx-1",
    )

    assert "市场附录正文保留" in gateway.last_prompt
    for forbidden in (
        "raw-output",
        "raw-output.txt",
        "viking://",
        "mongo://",
        "appendix/path",
        "appendix/source",
        "OpenViking",
        "Mongo",
    ):
        assert forbidden not in gateway.last_prompt


def _explode_if_openviking_is_used() -> object:
    raise AssertionError("report QA context must not call OpenViking for model-visible context")
