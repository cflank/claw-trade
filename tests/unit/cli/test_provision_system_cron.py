from __future__ import annotations

import json
from typing import Any

from claw_trade.cli.provision_system_cron import main
from claw_trade.ui_backend.system_cron_provisioner import SystemCronJobRef


class FakeProvisioner:
    def __init__(self) -> None:
        self.selection_calls = 0
        self.maintenance_calls: list[tuple[str, str, dict[str, Any]]] = []

    def ensure_selection_data_refresh(self) -> SystemCronJobRef:
        self.selection_calls += 1
        return SystemCronJobRef(
            key="selection-data-refresh:CN_A:daily",
            openclaw_cron_job_id="cron-selection",
        )

    def ensure_data_maintenance(self, *, market: str, job_kind: str, schedule: dict[str, Any]) -> SystemCronJobRef:
        self.maintenance_calls.append((market, job_kind, schedule))
        return SystemCronJobRef(
            key=f"data-maintenance:{market}:{job_kind}",
            openclaw_cron_job_id=f"cron-{market}-{job_kind}",
        )


def test_selection_data_refresh_flag_provisions_one_job(capsys: object) -> None:
    provisioner = FakeProvisioner()

    code = main(["--selection-data-refresh"], provisioner=provisioner)

    captured = capsys.readouterr()
    assert code == 0
    assert provisioner.selection_calls == 1
    assert provisioner.maintenance_calls == []
    assert json.loads(captured.out) == {
        "results": [{"key": "selection-data-refresh:CN_A:daily", "openclawCronJobId": "cron-selection"}]
    }


def test_cron_expr_flags_provision_expected_data_maintenance_jobs(capsys: object) -> None:
    provisioner = FakeProvisioner()

    code = main(
        [
            "--cn-a-eod-cron-expr",
            "0 1 * * *",
            "--hk-eod-cron-expr",
            "0 2 * * *",
            "--us-eod-cron-expr",
            "0 3 * * *",
            "--crypto-kline-refresh-cron-expr",
            "0 4 * * *",
        ],
        provisioner=provisioner,
    )

    captured = capsys.readouterr()
    assert code == 0
    assert provisioner.maintenance_calls == [
        ("CN_A", "eod", {"kind": "cron", "expr": "0 1 * * *", "tz": "UTC", "staggerMs": 0}),
        ("HK", "eod", {"kind": "cron", "expr": "0 2 * * *", "tz": "UTC", "staggerMs": 0}),
        ("US", "eod", {"kind": "cron", "expr": "0 3 * * *", "tz": "UTC", "staggerMs": 0}),
        ("CRYPTO", "kline-refresh", {"kind": "cron", "expr": "0 4 * * *", "tz": "UTC", "staggerMs": 0}),
    ]
    assert [item["key"] for item in json.loads(captured.out)["results"]] == [
        "data-maintenance:CN_A:eod",
        "data-maintenance:HK:eod",
        "data-maintenance:US:eod",
        "data-maintenance:CRYPTO:kline-refresh",
    ]


def test_no_flags_exits_with_no_job_selected(capsys: object) -> None:
    code = main([], provisioner=FakeProvisioner())

    captured = capsys.readouterr()
    assert code == 2
    assert "No system cron job selected." in captured.err
    assert captured.out == ""
