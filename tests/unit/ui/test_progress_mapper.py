from __future__ import annotations

from claw_trade.ui_backend.progress_mapper import map_workflow_progress_to_ui_state
from claw_trade.workflow.models import RunStatus


def test_known_stage_maps_to_chinese_labels() -> None:
    mapped = map_workflow_progress_to_ui_state(
        {"status": "investment_debate_running"},
        {"activeWorkerId": "bull_researcher", "completedWorkers": ["market_analyst"]},
    )
    assert mapped["stageLabel"] == "投资辩论中"
    assert mapped["roleLabel"] == "多头研究员"
    assert mapped["percent"] == 38
    assert "market_analyst" not in str(mapped)


def test_unknown_status_falls_back_to_processing() -> None:
    mapped = map_workflow_progress_to_ui_state({"status": "mystery_status"})
    assert mapped["stageLabel"] == "处理中"
    assert mapped["percent"] == 12


def test_run_status_enum_maps_to_real_stage() -> None:
    mapped = map_workflow_progress_to_ui_state({"status": RunStatus.FRONTLINE_RUNNING})
    assert mapped["stageLabel"] == "前线信息采集中"
    assert mapped["percent"] == 18


def test_worker_status_labels_are_reader_facing() -> None:
    mapped = map_workflow_progress_to_ui_state(
        {"status": "final_report_running"},
        {
            "activeWorkerIds": ["report_polisher"],
            "completedWorkers": ["market_analyst", "portfolio_manager"],
            "workerStatuses": {
                "market_analyst": "succeeded",
                "portfolio_manager": "succeeded",
                "report_polisher": "running",
            },
            "visibleWorkerIds": ["market_analyst", "portfolio_manager", "report_polisher"],
        },
    )
    assert mapped["roleLabel"] == "报告整理员"
    assert mapped["currentAction"] == "报告整理员：报告生成中"
    assert mapped["waitingRoleLabels"] == []
    assert "市场分析师：已完成" in mapped["workerStatusLabels"]
    assert "报告整理员：执行中" in mapped["workerStatusLabels"]
    assert "market_analyst" not in str(mapped)
