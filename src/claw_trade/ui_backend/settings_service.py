from __future__ import annotations

import shutil
import tempfile
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Callable, Mapping

from claw_trade.config.report_workflow_settings import (
    ReportWorkflowSettings,
    load_report_workflow_settings,
)
from claw_trade.ui_contracts.constants import PRODUCT_ERROR_CODES

_WORKFLOW_ENV_ALLOWLIST = (
    "CLAW_TRADE_REPORT_MAX_DEBATE_ROUNDS",
    "CLAW_TRADE_REPORT_MAX_RISK_DISCUSS_ROUNDS",
    "CLAW_TRADE_REPORT_MAX_ROUNDS_HARD_LIMIT",
    "CLAW_TRADE_REPORT_FRONTLINE_EXECUTION_MODE",
    "CLAW_TRADE_REPORT_DEFAULT_PROFILE",
    "CLAW_TRADE_REPORT_DEFAULT_MARKET",
    "CLAW_TRADE_REPORT_DEFAULT_CURRENCY",
    "CLAW_TRADE_REPORT_DEFAULT_CURRENCY_SYMBOL",
)


class UiBoundaryError(ValueError):
    def __init__(self, code: str, user_message: str) -> None:
        if code not in PRODUCT_ERROR_CODES:
            raise ValueError(f"未知产品错误码: {code}")
        super().__init__(user_message)
        self.code = code
        self.user_message = user_message


@dataclass(frozen=True)
class ReportWorkflowSettingsSnapshot:
    max_debate_rounds: int
    max_risk_discuss_rounds: int
    frontline_execution_mode: str
    default_profile: str
    default_market: str
    default_currency: str
    default_currency_symbol: str

    def to_user_dict(self) -> dict[str, Any]:
        return {
            "maxDebateRounds": self.max_debate_rounds,
            "maxRiskDiscussRounds": self.max_risk_discuss_rounds,
            "frontlineExecutionMode": self.frontline_execution_mode,
            "defaultProfile": self.default_profile,
            "defaultMarket": self.default_market,
            "defaultCurrency": self.default_currency,
            "defaultCurrencySymbol": self.default_currency_symbol,
        }


class EnvLocalAllowlistWriter:
    def __init__(self, env_path: Path, *, allowed_keys: tuple[str, ...]) -> None:
        self._env_path = env_path
        self._allowed_keys = frozenset(allowed_keys)

    def write_allowed_env_keys(self, updates: Mapping[str, str]) -> None:
        safe_updates: dict[str, str] = {}
        for key, value in updates.items():
            if key not in self._allowed_keys:
                raise UiBoundaryError("INVALID_INPUT", f"不允许写入配置项: {key}")
            safe_updates[key] = str(value).strip()
        if not safe_updates:
            return
        existing_lines = self._read_lines()
        merged_lines = self._merge_lines(existing_lines, safe_updates)
        self._atomic_write(merged_lines)

    def clear_allowed_env_keys(self, keys: tuple[str, ...]) -> None:
        safe_keys: set[str] = set()
        for key in keys:
            if key not in self._allowed_keys:
                raise UiBoundaryError("INVALID_INPUT", f"不允许清除配置项: {key}")
            safe_keys.add(key)
        if not safe_keys:
            return
        existing_lines = self._read_lines()
        filtered_lines = self._filter_cleared_lines(existing_lines, safe_keys)
        if filtered_lines != existing_lines:
            self._atomic_write(filtered_lines)

    def _read_lines(self) -> list[str]:
        if not self._env_path.exists():
            return []
        return self._env_path.read_text(encoding="utf-8").splitlines()

    def _merge_lines(self, lines: list[str], updates: Mapping[str, str]) -> list[str]:
        output = list(lines)
        replaced = {key: False for key in updates}
        for idx, raw in enumerate(output):
            stripped = raw.strip()
            if not stripped or stripped.startswith("#") or "=" not in stripped:
                continue
            key, _, _ = stripped.partition("=")
            key = key.strip()
            if key in updates:
                output[idx] = f"{key}={updates[key]}"
                replaced[key] = True
        for key, done in replaced.items():
            if not done:
                output.append(f"{key}={updates[key]}")
        return output

    def _filter_cleared_lines(self, lines: list[str], keys: set[str]) -> list[str]:
        output: list[str] = []
        for raw in lines:
            stripped = raw.strip()
            if not stripped or stripped.startswith("#") or "=" not in stripped:
                output.append(raw)
                continue
            key, _, _ = stripped.partition("=")
            if key.strip() in keys:
                continue
            output.append(raw)
        return output

    def _atomic_write(self, lines: list[str]) -> None:
        self._env_path.parent.mkdir(parents=True, exist_ok=True)
        backup_path = self._env_path.with_suffix(f"{self._env_path.suffix}.bak")
        if self._env_path.exists():
            shutil.copy2(self._env_path, backup_path)
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            delete=False,
            dir=str(self._env_path.parent),
        ) as handle:
            handle.write("\n".join(lines).rstrip() + "\n")
            temp_name = handle.name
        Path(temp_name).replace(self._env_path)


class SettingsService:
    def __init__(
        self,
        *,
        settings_loader: Callable[[], ReportWorkflowSettings] = load_report_workflow_settings,
        env_writer: EnvLocalAllowlistWriter | None = None,
    ) -> None:
        self._settings_loader = settings_loader
        self._env_writer = env_writer or EnvLocalAllowlistWriter(
            Path(".env.local"),
            allowed_keys=_WORKFLOW_ENV_ALLOWLIST,
        )

    def current_report_workflow_settings(self) -> ReportWorkflowSettingsSnapshot:
        settings = self._settings_loader()
        return self._snapshot_from_settings(settings)

    def save_report_workflow_settings(self, patch: Mapping[str, Any]) -> dict[str, Any]:
        current = self._settings_loader()
        max_debate_rounds = int(patch.get("maxDebateRounds", current.max_debate_rounds))
        max_risk_discuss_rounds = int(patch.get("maxRiskDiscussRounds", current.max_risk_discuss_rounds))
        self._assert_round_limit(
            max_debate_rounds=max_debate_rounds,
            max_risk_discuss_rounds=max_risk_discuss_rounds,
            hard_limit=current.max_rounds_hard_limit,
        )
        frontline_execution_mode = str(
            patch.get("frontlineExecutionMode", current.frontline_execution_mode)
        ).strip() or "serial"
        if frontline_execution_mode not in {"serial", "parallel"}:
            raise UiBoundaryError("INVALID_INPUT", "frontlineExecutionMode 只支持 serial 或 parallel。")
        snapshot = ReportWorkflowSettingsSnapshot(
            max_debate_rounds=max_debate_rounds,
            max_risk_discuss_rounds=max_risk_discuss_rounds,
            frontline_execution_mode=frontline_execution_mode,
            default_profile=str(patch.get("defaultProfile", current.default_profile)).strip() or current.default_profile,
            default_market=str(patch.get("defaultMarket", current.default_market)).strip() or current.default_market,
            default_currency=str(patch.get("defaultCurrency", current.default_currency)).strip() or current.default_currency,
            default_currency_symbol=str(
                patch.get("defaultCurrencySymbol", current.default_currency_symbol)
            ).strip()
            or current.default_currency_symbol,
        )
        self._env_writer.write_allowed_env_keys(
            {
                "CLAW_TRADE_REPORT_MAX_DEBATE_ROUNDS": str(snapshot.max_debate_rounds),
                "CLAW_TRADE_REPORT_MAX_RISK_DISCUSS_ROUNDS": str(snapshot.max_risk_discuss_rounds),
                "CLAW_TRADE_REPORT_FRONTLINE_EXECUTION_MODE": snapshot.frontline_execution_mode,
                "CLAW_TRADE_REPORT_DEFAULT_PROFILE": snapshot.default_profile,
                "CLAW_TRADE_REPORT_DEFAULT_MARKET": snapshot.default_market,
                "CLAW_TRADE_REPORT_DEFAULT_CURRENCY": snapshot.default_currency,
                "CLAW_TRADE_REPORT_DEFAULT_CURRENCY_SYMBOL": snapshot.default_currency_symbol,
            }
        )
        return {
            "workflowSettings": snapshot.to_user_dict(),
            "updatedAt": _now_iso(),
        }

    def freeze_report_workflow_settings_snapshot(
        self,
        overrides: Mapping[str, Any] | None = None,
    ) -> ReportWorkflowSettingsSnapshot:
        current = self._settings_loader()
        patch = dict(overrides or {})
        max_debate_rounds = int(patch.get("maxDebateRounds", current.max_debate_rounds))
        max_risk_discuss_rounds = int(patch.get("maxRiskDiscussRounds", current.max_risk_discuss_rounds))
        self._assert_round_limit(
            max_debate_rounds=max_debate_rounds,
            max_risk_discuss_rounds=max_risk_discuss_rounds,
            hard_limit=current.max_rounds_hard_limit,
        )
        return ReportWorkflowSettingsSnapshot(
            max_debate_rounds=max_debate_rounds,
            max_risk_discuss_rounds=max_risk_discuss_rounds,
            frontline_execution_mode=str(
                patch.get("frontlineExecutionMode", current.frontline_execution_mode)
            ).strip()
            or current.frontline_execution_mode,
            default_profile=str(patch.get("defaultProfile", current.default_profile)).strip() or current.default_profile,
            default_market=str(patch.get("defaultMarket", current.default_market)).strip() or current.default_market,
            default_currency=str(patch.get("defaultCurrency", current.default_currency)).strip() or current.default_currency,
            default_currency_symbol=str(
                patch.get("defaultCurrencySymbol", current.default_currency_symbol)
            ).strip()
            or current.default_currency_symbol,
        )

    def confirm_report_settings_and_create(
        self,
        overrides: Mapping[str, Any] | None,
        create_run_request: Callable[[ReportWorkflowSettingsSnapshot], Any],
    ) -> Any:
        snapshot = self.freeze_report_workflow_settings_snapshot(overrides)
        return create_run_request(snapshot)

    def _snapshot_from_settings(self, settings: ReportWorkflowSettings) -> ReportWorkflowSettingsSnapshot:
        return ReportWorkflowSettingsSnapshot(
            max_debate_rounds=settings.max_debate_rounds,
            max_risk_discuss_rounds=settings.max_risk_discuss_rounds,
            frontline_execution_mode=settings.frontline_execution_mode,
            default_profile=settings.default_profile,
            default_market=settings.default_market,
            default_currency=settings.default_currency,
            default_currency_symbol=settings.default_currency_symbol,
        )

    @staticmethod
    def _assert_round_limit(*, max_debate_rounds: int, max_risk_discuss_rounds: int, hard_limit: int) -> None:
        if max_debate_rounds < 1 or max_risk_discuss_rounds < 1:
            raise UiBoundaryError("INVALID_INPUT", "rounds 必须 >= 1。")
        if max_debate_rounds > hard_limit or max_risk_discuss_rounds > hard_limit:
            raise UiBoundaryError(
                "INVALID_INPUT",
                f"rounds 超过硬上限 {hard_limit}，请先调整设置。",
            )


def _now_iso() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")
