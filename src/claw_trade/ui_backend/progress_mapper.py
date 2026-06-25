from __future__ import annotations

from typing import Any

from claw_trade.workflow.workers import all_worker_ids

_STAGE_MAP: dict[str, tuple[str, int]] = {
    "frontline_running": ("前线信息采集中", 18),
    "investment_debate_running": ("投资辩论中", 38),
    "investment_decision_running": ("投资决策中", 56),
    "trade_decision_running": ("交易决策中", 72),
    "risk_debate_running": ("风险辩论中", 86),
    "portfolio_decision_running": ("风控裁决中", 94),
    "final_report_running": ("报告生成中", 97),
    "report_exporting": ("报告保存中", 99),
    "completed": ("已完成", 100),
}

_ROLE_LABELS = {
    "market_analyst": "市场分析师",
    "fundamental_analyst": "基本面分析师",
    "news_analyst": "新闻分析师",
    "social_analyst": "社交分析师",
    "policy_analyst": "政策分析师",
    "hot_money_tracker": "游资资金跟踪员",
    "lockup_watcher": "限售筹码观察员",
    "bull_researcher": "多头研究员",
    "bear_researcher": "空头研究员",
    "research_manager": "研究经理",
    "trader": "交易员",
    "risk_challenger": "风险挑战者",
    "risk_guardian": "风险守护者",
    "risk_moderator": "风险主持人",
    "portfolio_manager": "投资组合经理",
    "report_polisher": "报告整理员",
}

_PRIMARY_WORKER_IDS = tuple(
    worker_id for worker_id in all_worker_ids() if worker_id in _ROLE_LABELS and worker_id != "report_polisher"
)

_WORKER_STATUS_LABELS = {
    "pending": "等待启动",
    "running": "执行中",
    "succeeded": "已完成",
    "completed": "已完成",
    "failed": "失败",
    "blocked": "阻塞",
}


def map_workflow_progress_to_ui_state(
    workflow_state: dict[str, Any] | Any,
    worker_results: dict[str, Any] | None = None,
) -> dict[str, Any]:
    status = _read_status(workflow_state)
    stage_label, percent = _STAGE_MAP.get(status, ("处理中", 12))
    active_worker = None
    active_workers: list[str] = []
    completed_worker_ids: list[str] = []
    worker_statuses: dict[str, str] = {}
    visible_worker_ids = list(_PRIMARY_WORKER_IDS)
    if worker_results:
        active_worker = _read_value(worker_results, "activeWorkerId", "active_worker_id")
        active_workers = list(_read_value(worker_results, "activeWorkerIds", "active_worker_ids", default=[]))
        completed_worker_ids = list(_read_value(worker_results, "completedWorkers", "completed_workers", default=[]))
        raw_statuses = _read_value(worker_results, "workerStatuses", "worker_statuses", default={})
        if isinstance(raw_statuses, dict):
            worker_statuses = {str(key): str(value) for key, value in raw_statuses.items()}
        visible_worker_ids = list(_read_value(worker_results, "visibleWorkerIds", "visible_worker_ids", default=visible_worker_ids))
    if not active_worker and active_workers:
        active_worker = active_workers[0]
    role_label = _ROLE_LABELS.get(str(active_worker), "相关角色") if active_worker else None
    completed_set = set(completed_worker_ids)
    for worker_id, worker_status in worker_statuses.items():
        if worker_status in {"succeeded", "completed"}:
            completed_set.add(worker_id)
    visible_known_workers = [worker_id for worker_id in visible_worker_ids if worker_id in _ROLE_LABELS]
    completed_roles = [_ROLE_LABELS[item] for item in visible_known_workers if item in completed_set]
    waiting_roles = [
        _ROLE_LABELS[item]
        for item in visible_known_workers
        if item not in completed_set and worker_statuses.get(item, "pending") == "pending"
    ]
    worker_status_labels = [
        f"{_ROLE_LABELS[item]}：{_WORKER_STATUS_LABELS.get(worker_statuses.get(item, 'pending'), '处理中')}"
        for item in visible_known_workers
        if item in worker_statuses
    ]
    return {
        "percent": max(6, min(percent, 100)),
        "stageLabel": stage_label,
        "roleLabel": role_label,
        "currentAction": _build_current_action(stage_label=stage_label, role_label=role_label),
        "completedRoleLabels": completed_roles,
        "waitingRoleLabels": waiting_roles,
        "workerStatusLabels": worker_status_labels,
    }


def _read_status(workflow_state: dict[str, Any] | Any) -> str:
    value = _read_value(workflow_state, "status", default="unknown")
    if hasattr(value, "value"):
        value = value.value
    return str(value).strip().lower()


def _build_current_action(*, stage_label: str, role_label: str | None) -> str:
    if role_label:
        return f"{role_label}：{stage_label}"
    return stage_label


def _read_value(container: dict[str, Any] | Any, *keys: str, default: Any = None) -> Any:
    if isinstance(container, dict):
        for key in keys:
            if key in container:
                return container[key]
        return default
    for key in keys:
        if hasattr(container, key):
            return getattr(container, key)
    return default
