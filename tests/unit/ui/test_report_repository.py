from __future__ import annotations

import pytest
from claw_trade.ui_backend.report_repository import ReportRepository, UiProductError


def test_save_succeeded_report_only_then_visible_in_history() -> None:
    repo = ReportRepository()
    repo.save_succeeded_report(
        report_id="r-1",
        instrument_code="BTC",
        instrument_name="Bitcoin",
        market="CRYPTO",
        title="BTC 报告",
        markdown="# 标题\n结论段落",
    )
    items = repo.list_saved_reports()
    assert len(items) == 1
    assert items[0]["id"] == "r-1"
    assert items[0]["canForwardToChannel"] is False


def test_saved_report_exposes_channel_forward_availability_from_origin_context() -> None:
    repo = ReportRepository()
    repo.save_succeeded_report(
        report_id="r-wechat",
        instrument_code="TSLA",
        instrument_name="Tesla",
        market="US",
        title="TSLA 报告",
        markdown="# 标题\n结论段落",
        origin_context_id="wechat_clawbot:account-1:sender-1",
    )

    items = repo.list_saved_reports()

    assert items[0]["canForwardToChannel"] is True


def test_failed_or_running_report_cannot_enter_history() -> None:
    repo = ReportRepository()
    with pytest.raises(UiProductError, match="仅成功报告可以写入历史"):
        repo.save_succeeded_report(
            report_id="r-2",
            instrument_code="AAPL",
            market="US",
            title="AAPL 报告",
            markdown="正文",
            source_status="failed",
        )
    assert repo.list_saved_reports() == []


def test_delete_saved_report_hides_report_and_persists_tombstone(tmp_path) -> None:  # type: ignore[no-untyped-def]
    deletion_index = tmp_path / ".ui-deleted-reports.json"
    repo = ReportRepository(deletion_index_path=deletion_index)
    repo.save_succeeded_report(
        report_id="r-delete",
        instrument_code="BTC",
        instrument_name="Bitcoin",
        market="CRYPTO",
        title="BTC 报告",
        markdown="# 标题\n正文",
    )

    assert repo.delete_saved_report("r-delete") is True
    assert repo.list_saved_reports() == []
    assert repo.get_report("r-delete") is None

    reloaded = ReportRepository(deletion_index_path=deletion_index)
    assert reloaded.is_deleted_report("r-delete") is True


def test_remove_report_state_does_not_add_tombstone(tmp_path) -> None:  # type: ignore[no-untyped-def]
    deletion_index = tmp_path / ".ui-deleted-reports.json"
    repo = ReportRepository(deletion_index_path=deletion_index)
    repo.save_succeeded_report(
        report_id="r-hard-delete",
        instrument_code="BTC",
        instrument_name="Bitcoin",
        market="CRYPTO",
        title="BTC 报告",
        markdown="# 标题\n正文",
    )

    assert repo.remove_report_state("r-hard-delete") is True

    reloaded = ReportRepository(deletion_index_path=deletion_index)
    assert reloaded.is_deleted_report("r-hard-delete") is False


def test_discard_deleted_report_id_persists_tombstone_removal(tmp_path) -> None:  # type: ignore[no-untyped-def]
    deletion_index = tmp_path / ".ui-deleted-reports.json"
    repo = ReportRepository(deletion_index_path=deletion_index)
    repo.save_succeeded_report(
        report_id="r-discard",
        instrument_code="BTC",
        instrument_name="Bitcoin",
        market="CRYPTO",
        title="BTC 报告",
        markdown="# 标题\n正文",
    )
    repo.delete_saved_report("r-discard")

    assert repo.discard_deleted_report_id("r-discard") is True

    reloaded = ReportRepository(deletion_index_path=deletion_index)
    assert reloaded.is_deleted_report("r-discard") is False


def test_report_detail_rewrites_local_image_assets_and_resolves_files(tmp_path) -> None:  # type: ignore[no-untyped-def]
    asset_dir = tmp_path / "reports" / "assets"
    asset_dir.mkdir(parents=True)
    chart_path = asset_dir / "chart.png"
    chart_path.write_bytes(b"image")
    repo = ReportRepository()
    repo.save_succeeded_report(
        report_id="r-assets",
        instrument_code="BTC",
        instrument_name="Bitcoin",
        market="CRYPTO",
        title="BTC 报告",
        markdown="# 报告\n![趋势图](assets/chart.png)\n![远程图](https://example.com/chart.png)",
        asset_dir=asset_dir,
    )

    detail = repo.get_report_detail("r-assets")

    assert "/api/ui/get-report-asset?reportId=r-assets&assetPath=assets%2Fchart.png" in detail["markdown"]
    assert "https://example.com/chart.png" in detail["markdown"]
    assert repo.resolve_report_asset("r-assets", "assets/chart.png") == chart_path.resolve()
    assert repo.resolve_report_asset("r-assets", "../state.json") is None
    assert repo.list_markdown_image_assets("r-assets")[0]["status"] == "ready"
