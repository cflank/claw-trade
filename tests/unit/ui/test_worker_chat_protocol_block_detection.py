from __future__ import annotations

from claw_trade.ui_backend.report_repository import ReportRepository
from claw_trade.ui_backend.report_worker_chat_context import (
    FORBIDDEN_REPORT_WORKER_PROTOCOL_MARKERS,
    sanitize_report_worker_chat_visible_text,
)
from claw_trade.ui_backend.worker_chat import WorkerChatController
from claw_trade.ui_backend.worker_chat_openclaw import OpenClawWorkerChatClient


def test_report_worker_chat_filters_protocol_markers_before_openclaw(tmp_path) -> None:  # type: ignore[no-untyped-def]
    reports_dir = tmp_path / "run-1" / "reports"
    asset_dir = reports_dir / "assets"
    asset_dir.mkdir(parents=True)
    appendix_dir = reports_dir / "worker-appendix"
    appendix_dir.mkdir()
    appendix_dir.joinpath("07-risk_moderator.md").write_text(
        "风险经理附录：业务风险保留。 raw-output.md provider_request receipt sha256 hash manifest viking:// "
        "RuntimeTarget [ApprovedMaterials] single_worker_minimal toolPolicy promptProfile",
        encoding="utf-8",
    )
    repo = ReportRepository()
    repo.save_succeeded_report(
        report_id="report-1",
        instrument_code="BTC",
        market="CRYPTO",
        title="BTC 报告",
        markdown="# BTC 报告\n\n正式报告风险：保留正文。 RuntimeTarget viking://secret/path mongo://secret/source OpenViking Mongo sha256 manifest",
        asset_dir=asset_dir,
    )
    gateway = _RecordingWorkerChatGateway()
    controller = WorkerChatController(repo, OpenClawWorkerChatClient(gateway))

    controller.send_worker_chat(
        request_id="req-protocol",
        mode="report_worker_chat",
        worker_id="risk_moderator",
        text="请解释 raw-output.md 和 promptProfile 里的风险？",
        conversation_id="reader",
        report_id="report-1",
    )

    message = str(gateway.chat_calls[0]["message"])
    assert "业务风险保留" in message
    assert "正式报告风险：保留正文" in message
    for marker in FORBIDDEN_REPORT_WORKER_PROTOCOL_MARKERS:
        assert marker not in message
    assert "secret/path" not in message
    assert "secret/source" not in message
    assert "OpenViking" not in message
    assert "Mongo" not in message


def test_report_worker_chat_filters_protocol_markers_from_appendix_report_and_question(tmp_path) -> None:  # type: ignore[no-untyped-def]
    reports_dir = tmp_path / "run-1" / "reports"
    asset_dir = reports_dir / "assets"
    asset_dir.mkdir(parents=True)
    appendix_dir = reports_dir / "worker-appendix"
    appendix_dir.mkdir()
    appendix_dir.joinpath("03-market_analyst.md").write_text(
        "市场附录正文保留。 raw-output raw-output.json raw-output.txt viking://appendix/path mongo://appendix/source OpenViking Mongo",
        encoding="utf-8",
    )
    repo = ReportRepository()
    repo.save_succeeded_report(
        report_id="report-1",
        instrument_code="BTC",
        market="CRYPTO",
        title="BTC 报告",
        markdown="# BTC 报告\n\n正式报告正文保留。 raw-output viking://report/path mongo://report/source OpenViking Mongo",
        asset_dir=asset_dir,
    )
    gateway = _RecordingWorkerChatGateway()
    controller = WorkerChatController(repo, OpenClawWorkerChatClient(gateway))

    controller.send_worker_chat(
        request_id="req-protocol-wide",
        mode="report_worker_chat",
        worker_id="market_analyst",
        text="请解释 viking://question/path mongo://question/source OpenViking Mongo raw-output.json 的含义",
        conversation_id="reader",
        report_id="report-1",
    )

    message = str(gateway.chat_calls[0]["message"])
    assert "市场附录正文保留" in message
    assert "正式报告正文保留" in message
    for forbidden in (
        "raw-output",
        "raw-output.json",
        "raw-output.txt",
        "viking://",
        "mongo://",
        "appendix/path",
        "appendix/source",
        "report/path",
        "report/source",
        "question/path",
        "question/source",
        "OpenViking",
        "Mongo",
    ):
        assert forbidden not in message


def test_protocol_marker_filter_does_not_rewrite_normal_words() -> None:
    text = "BTC hashrate 回升，普通问题要求解释矿工算力，不包含内部协议。"

    sanitized = sanitize_report_worker_chat_visible_text(text)

    assert sanitized == text
    assert "hashrate" in sanitized


def test_protocol_marker_filter_removes_standalone_protocol_tokens() -> None:
    sanitized = sanitize_report_worker_chat_visible_text(
        "hash receipt sha256 manifest raw-output raw-output.md raw-output.json raw-output.txt provider_request"
    )

    assert "hash" not in sanitized
    assert "receipt" not in sanitized
    assert "sha256" not in sanitized
    assert "manifest" not in sanitized
    assert "raw-output" not in sanitized
    assert "raw-output.md" not in sanitized
    assert "raw-output.json" not in sanitized
    assert "raw-output.txt" not in sanitized
    assert "provider_request" not in sanitized


def test_protocol_marker_filter_removes_complete_internal_uris_and_product_names() -> None:
    sanitized = sanitize_report_worker_chat_visible_text(
        "保留正文 viking://resources/workflow/run/frontline/report.md mongo://db/collection/id OpenViking Mongo"
    )

    assert "保留正文" in sanitized
    assert "viking://" not in sanitized
    assert "mongo://" not in sanitized
    assert "resources/workflow" not in sanitized
    assert "db/collection" not in sanitized
    assert "OpenViking" not in sanitized
    assert "Mongo" not in sanitized


def test_protocol_marker_filter_removes_internal_uris_without_leading_space() -> None:
    sanitized = sanitize_report_worker_chat_visible_text(
        "正文viking://resources/workflow/run/frontline/report.md 证据mongo://db/collection/id"
    )

    assert "正文" in sanitized
    assert "证据" in sanitized
    assert "viking://" not in sanitized
    assert "mongo://" not in sanitized
    assert "resources/workflow" not in sanitized
    assert "db/collection" not in sanitized


class _RecordingWorkerChatGateway:
    def __init__(self) -> None:
        self.session_metadata: list[dict[str, object]] = []
        self.chat_calls: list[dict[str, object]] = []

    def sessions_create(self, *, metadata: dict[str, object]) -> str:
        self.session_metadata.append(metadata)
        return str(metadata["sessionKey"])

    def worker_chat_send(
        self,
        *,
        session_key: str,
        message: str,
        idempotency_key: str,
    ) -> dict[str, str]:
        self.chat_calls.append(
            {
                "session_key": session_key,
                "message": message,
                "idempotency_key": idempotency_key,
            }
        )
        return {"text": "worker answer"}
