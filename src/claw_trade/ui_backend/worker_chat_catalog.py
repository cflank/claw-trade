from __future__ import annotations

from dataclasses import dataclass

from claw_trade.ui_backend.report_repository import UiProductError


@dataclass(frozen=True)
class WorkerChatCatalogEntry:
    worker_id: str
    display_name: str
    aliases: tuple[str, ...]
    default: bool = False


ALLOWED_WORKER_CHAT_CATALOG: tuple[WorkerChatCatalogEntry, ...] = (
    WorkerChatCatalogEntry("portfolio_manager", "组合经理", ("组合经理", "PM"), default=True),
    WorkerChatCatalogEntry("research_manager", "研究经理", ("研究经理",)),
    WorkerChatCatalogEntry("market_analyst", "市场分析师", ("市场分析师", "市场")),
    WorkerChatCatalogEntry("fundamental_analyst", "基本面分析师", ("基本面分析师", "基本面")),
    WorkerChatCatalogEntry("news_analyst", "新闻分析师", ("新闻分析师", "新闻")),
    WorkerChatCatalogEntry("social_analyst", "情绪分析师", ("情绪分析师", "情绪")),
    WorkerChatCatalogEntry("risk_moderator", "风险经理", ("风险经理", "风险")),
)

_ITEM_BY_ID = {item.worker_id: item for item in ALLOWED_WORKER_CHAT_CATALOG}


def list_worker_chat_menu() -> tuple[dict[str, object], ...]:
    return tuple(
        {
            "workerId": item.worker_id,
            "displayName": item.display_name,
            "default": item.default,
            "aliases": item.aliases,
        }
        for item in ALLOWED_WORKER_CHAT_CATALOG
    )


def default_worker_id() -> str:
    for item in ALLOWED_WORKER_CHAT_CATALOG:
        if item.default:
            return item.worker_id
    raise RuntimeError("worker chat catalog has no default worker")


def require_allowed_worker(worker_id: str) -> WorkerChatCatalogEntry:
    item = _ITEM_BY_ID.get(worker_id)
    if item is None:
        raise UiProductError("WORKER_CHAT_WORKER_UNAVAILABLE", "这个角色暂不可用。")
    return item
