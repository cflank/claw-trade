from __future__ import annotations

import argparse
import json
import sys
from typing import Any, Protocol

from claw_trade.ui_backend.openclaw_cron_adapter import OpenClawCronAdapter
from claw_trade.ui_backend.system_cron_provisioner import SystemCronJobRef, SystemCronProvisioner
from claw_trade.web.openclaw_gateway import OpenClawGatewayRpcClient


class SystemCronProvisionerLike(Protocol):
    def ensure_selection_data_refresh(self) -> SystemCronJobRef: ...

    def ensure_data_maintenance(
        self,
        *,
        market: str,
        job_kind: str,
        schedule: dict[str, Any],
    ) -> SystemCronJobRef: ...


def main(argv: list[str] | None = None, *, provisioner: SystemCronProvisionerLike | None = None) -> int:
    parser = _build_parser()
    namespace = parser.parse_args(sys.argv[1:] if argv is None else argv)
    selected = _selected_jobs(namespace)
    if not selected and not namespace.selection_data_refresh:
        print("No system cron job selected.", file=sys.stderr)
        return 2
    resolved_provisioner = provisioner or _build_provisioner(namespace)
    results: list[SystemCronJobRef] = []
    if namespace.selection_data_refresh:
        results.append(resolved_provisioner.ensure_selection_data_refresh())
    for market, job_kind, expr in selected:
        results.append(
            resolved_provisioner.ensure_data_maintenance(
                market=market,
                job_kind=job_kind,
                schedule={"kind": "cron", "expr": expr, "tz": "UTC", "staggerMs": 0},
            )
        )
    print(
        json.dumps(
            {
                "results": [
                    {"key": result.key, "openclawCronJobId": result.openclaw_cron_job_id}
                    for result in results
                ]
            },
            ensure_ascii=False,
            separators=(",", ":"),
        )
    )
    return 0


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Provision claw-trade system cron jobs.")
    parser.add_argument("--selection-data-refresh", action="store_true")
    parser.add_argument("--cn-a-eod-cron-expr")
    parser.add_argument("--hk-eod-cron-expr")
    parser.add_argument("--us-eod-cron-expr")
    parser.add_argument("--crypto-kline-refresh-cron-expr")
    parser.add_argument("--gateway-call-bin", default="openclaw")
    parser.add_argument("--gateway-ws-url", default="ws://127.0.0.1:18789")
    parser.add_argument("--gateway-timeout-ms", type=int, default=10_000)
    parser.add_argument("--gateway-token")
    parser.add_argument("--gateway-password")
    return parser


def _selected_jobs(namespace: argparse.Namespace) -> tuple[tuple[str, str, str], ...]:
    jobs: list[tuple[str, str, str]] = []
    for attr, market, job_kind in (
        ("cn_a_eod_cron_expr", "CN_A", "eod"),
        ("hk_eod_cron_expr", "HK", "eod"),
        ("us_eod_cron_expr", "US", "eod"),
        ("crypto_kline_refresh_cron_expr", "CRYPTO", "kline-refresh"),
    ):
        expr = str(getattr(namespace, attr) or "").strip()
        if expr:
            jobs.append((market, job_kind, expr))
    return tuple(jobs)


def _build_provisioner(namespace: argparse.Namespace) -> SystemCronProvisioner:
    gateway = OpenClawGatewayRpcClient(
        gateway_call_bin=namespace.gateway_call_bin,
        gateway_ws_url=namespace.gateway_ws_url,
        timeout_ms=namespace.gateway_timeout_ms,
        token=namespace.gateway_token,
        password=namespace.gateway_password,
    )
    return SystemCronProvisioner(OpenClawCronAdapter(gateway))


if __name__ == "__main__":
    raise SystemExit(main())
