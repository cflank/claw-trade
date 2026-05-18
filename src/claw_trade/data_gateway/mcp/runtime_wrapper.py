from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Mapping, Protocol, runtime_checkable

from claw_trade.data_gateway.models import (
    DomainPackResult,
    FreshnessPolicy,
    GatewaySettings,
    Market,
    PackDomain,
    PackRequest,
    RunProviderPlan,
)
from claw_trade.data_gateway.providers.base import ProviderAdapter
from claw_trade.data_gateway.errors import DataGatewayError, DataGatewayErrorCode
from claw_trade.data_gateway.providers.run_plan import RunProviderPlanStoreLike, load_plan_for_pack_runtime
from claw_trade.data_gateway.settings import validate_gateway_settings

if TYPE_CHECKING:
    from fastapi import FastAPI
    from fastmcp import FastMCP
    from openbb_mcp_server.models.settings import MCPSettings

PACK_ENDPOINTS: dict[PackDomain, str] = {
    PackDomain.MARKET: "/api/v1/claw/get_market_pack",
    PackDomain.FUNDAMENTAL: "/api/v1/claw/get_fundamental_pack",
    PackDomain.NEWS: "/api/v1/claw/get_news_pack",
    PackDomain.SOCIAL: "/api/v1/claw/get_social_pack",
}

PACK_TOOL_NAMES: dict[PackDomain, str] = {
    PackDomain.MARKET: "claw_get_market_pack",
    PackDomain.FUNDAMENTAL: "claw_get_fundamental_pack",
    PackDomain.NEWS: "claw_get_news_pack",
    PackDomain.SOCIAL: "claw_get_social_pack",
}


class PackServiceUnavailableError(RuntimeError):
    pass


@dataclass(frozen=True)
class PackToolInput:
    ticker: str
    market: Market
    profile: str
    company_name: str
    start_date: str
    end_date: str
    current_date: str
    currency: str
    run_id: str
    call_id: str
    worker_id: str
    freshness_max_age_seconds: int = 300

    def __post_init__(self) -> None:
        required = {
            "ticker": self.ticker,
            "profile": self.profile,
            "start_date": self.start_date,
            "end_date": self.end_date,
            "current_date": self.current_date,
            "currency": self.currency,
            "run_id": self.run_id,
            "call_id": self.call_id,
            "worker_id": self.worker_id,
        }
        missing = [name for name, value in required.items() if not value.strip()]
        if missing:
            raise ValueError(f"missing required fields: {', '.join(missing)}")
        if self.freshness_max_age_seconds <= 0:
            raise ValueError("freshness_max_age_seconds must be > 0")

    @classmethod
    def from_payload(cls, payload: Mapping[str, Any]) -> PackToolInput:
        market_raw = str(payload.get("market", "")).strip().upper()
        if not market_raw:
            raise ValueError("market is required")
        return cls(
            ticker=str(payload.get("ticker", "")).strip(),
            market=Market(market_raw),
            profile=str(payload.get("profile", "")).strip(),
            company_name=str(payload.get("company_name", "")).strip(),
            start_date=str(payload.get("start_date", "")).strip(),
            end_date=str(payload.get("end_date", "")).strip(),
            current_date=str(payload.get("current_date", "")).strip(),
            currency=str(payload.get("currency", "")).strip(),
            run_id=str(payload.get("run_id", "")).strip(),
            call_id=str(payload.get("call_id", "")).strip(),
            worker_id=str(payload.get("worker_id", "")).strip(),
            freshness_max_age_seconds=int(payload.get("freshness_max_age_seconds", 300)),
        )

    def to_pack_request(self, domain: PackDomain) -> PackRequest:
        return PackRequest(
            run_id=self.run_id,
            call_id=self.call_id,
            worker_id=self.worker_id,
            market=self.market,
            domain=domain,
            ticker=self.ticker,
            company_name=self.company_name,
            start_date=self.start_date,
            end_date=self.end_date,
            current_date=self.current_date,
            currency=self.currency,
            profile=self.profile,
            freshness_policy=FreshnessPolicy(max_age_seconds=self.freshness_max_age_seconds),
        )


@runtime_checkable
class OpenBBPackService(Protocol):
    settings: GatewaySettings
    adapters: tuple[ProviderAdapter, ...]

    def get_pack(self, request: PackRequest, run_plan: RunProviderPlan) -> DomainPackResult:
        """Return one domain pack result."""


@dataclass
class OpenBBRuntimeWrapper:
    settings: GatewaySettings
    adapters: tuple[ProviderAdapter, ...] = ()
    pack_service: OpenBBPackService | None = None
    run_provider_plan_store: RunProviderPlanStoreLike | None = None

    def __post_init__(self) -> None:
        validate_gateway_settings(self.settings)
        for adapter in self.adapters:
            if not isinstance(adapter, ProviderAdapter):
                raise TypeError("all adapters must implement ProviderAdapter")

    def get_pack(self, domain: PackDomain, tool_input: PackToolInput) -> DomainPackResult:
        if self.pack_service is None:
            raise PackServiceUnavailableError("pack service is not configured")
        if self.run_provider_plan_store is None:
            raise DataGatewayError(
                DataGatewayErrorCode.RUN_PLAN_MISSING,
                "run provider plan store is not configured",
            )
        request = tool_input.to_pack_request(domain)
        run_plan = load_plan_for_pack_runtime(
            run_id=request.run_id,
            provider_config_version=self.settings.provider_config_version,
            store=self.run_provider_plan_store,
        )
        return self.pack_service.get_pack(request, run_plan)

    def create_pack_fastapi_app(self) -> FastAPI:
        from fastapi import Body, FastAPI, HTTPException

        app = FastAPI(title="claw-trade OpenBB pack gateway")

        def _invoke(payload: Mapping[str, Any], domain: PackDomain) -> Mapping[str, Any]:
            try:
                tool_input = PackToolInput.from_payload(payload)
                result = self.get_pack(domain, tool_input)
            except PackServiceUnavailableError as exc:
                raise HTTPException(
                    status_code=503,
                    detail={"error": "pack_service_unavailable", "message": str(exc)},
                ) from exc
            except DataGatewayError as exc:
                status_code = 409 if exc.code == DataGatewayErrorCode.CONFIG_VERSION_MISMATCH else 424
                raise HTTPException(
                    status_code=status_code,
                    detail={"error": exc.code.value, "message": exc.root_cause},
                ) from exc
            except ValueError as exc:
                raise HTTPException(status_code=422, detail={"error": "invalid_input", "message": str(exc)}) from exc

            return {
                "reader_brief_md": result.reader_brief_md,
                "status": result.readiness.status.value,
            }

        @app.post(PACK_ENDPOINTS[PackDomain.MARKET], tags=["claw"])
        def get_market_pack(payload: dict[str, Any] = Body(default_factory=dict)) -> Mapping[str, Any]:
            return _invoke(payload, PackDomain.MARKET)

        @app.post(PACK_ENDPOINTS[PackDomain.FUNDAMENTAL], tags=["claw"])
        def get_fundamental_pack(payload: dict[str, Any] = Body(default_factory=dict)) -> Mapping[str, Any]:
            return _invoke(payload, PackDomain.FUNDAMENTAL)

        @app.post(PACK_ENDPOINTS[PackDomain.NEWS], tags=["claw"])
        def get_news_pack(payload: dict[str, Any] = Body(default_factory=dict)) -> Mapping[str, Any]:
            return _invoke(payload, PackDomain.NEWS)

        @app.post(PACK_ENDPOINTS[PackDomain.SOCIAL], tags=["claw"])
        def get_social_pack(payload: dict[str, Any] = Body(default_factory=dict)) -> Mapping[str, Any]:
            return _invoke(payload, PackDomain.SOCIAL)

        return app

    def create_pack_mcp_server(self, mcp_settings: MCPSettings) -> FastMCP:
        if mcp_settings.enable_tool_discovery:
            raise ValueError("enable_tool_discovery must be false for pack-only server")

        from openbb_mcp_server.app.app import create_mcp_server

        app = self.create_pack_fastapi_app()
        mcp = create_mcp_server(mcp_settings, app)
        _prune_non_pack_tools(mcp)
        return mcp


def list_pack_routes(app: FastAPI) -> tuple[str, ...]:
    return tuple(
        sorted(
            route.path
            for route in app.routes
            if getattr(route, "path", "") in set(PACK_ENDPOINTS.values())
        )
    )


def list_mcp_tool_names(server: FastMCP) -> tuple[str, ...]:
    return tuple(sorted(_run_async(server.get_tools()).keys()))


def _prune_non_pack_tools(server: FastMCP) -> None:
    allowed = set(PACK_TOOL_NAMES.values())
    for name in tuple(_run_async(server.get_tools()).keys()):
        if name not in allowed:
            server.remove_tool(name)


def _run_async(awaitable: Any) -> Any:
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(awaitable)
    raise RuntimeError("cannot run async OpenBB MCP inspection inside running event loop")
