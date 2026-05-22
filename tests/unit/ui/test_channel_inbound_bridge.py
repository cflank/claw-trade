from __future__ import annotations

from claw_trade.ui_backend.channel_inbound_bridge import ChannelInboundBridge, OpenClawChannelInboundEvent


def test_inbound_bridge_processes_forwarded_request_full_report() -> None:
    called: list[tuple[str, str]] = []
    bridge = ChannelInboundBridge(
        request_full_report_file=lambda report_id, request_id: called.append((report_id, request_id)) or {"sent": True},
        build_summary_text=lambda report_id: f"{report_id} summary",
    )
    result = bridge.handle_openclaw_channel_inbound_event(
        OpenClawChannelInboundEvent(
            event_id="e1",
            occurred_at="2026-05-19T00:00:00+00:00",
            channel_kind="wechat_clawbot",
            kind="request_full_report",
            report_id="r-1",
        )
    )
    assert result["accepted"] is True
    assert result["action"] == "request_full_report"
    assert called == [("r-1", "channel_inbound:e1")]


def test_inbound_bridge_dedupes_and_rejects_untrusted_event() -> None:
    bridge = ChannelInboundBridge(
        request_full_report_file=lambda report_id, request_id: {"sent": True},
        build_summary_text=lambda report_id: "summary",
    )
    event = OpenClawChannelInboundEvent(
        event_id="e2",
        occurred_at="2026-05-19T00:00:00+00:00",
        channel_kind="wechat_clawbot",
        kind="request_full_report",
        report_id="r-2",
    )
    first = bridge.handle_openclaw_channel_inbound_event(event)
    second = bridge.handle_openclaw_channel_inbound_event(event)
    assert first["accepted"] is True
    assert second["action"] == "deduped"

    untrusted = bridge.handle_openclaw_channel_inbound_event(
        OpenClawChannelInboundEvent(
            event_id="e3",
            occurred_at="2026-05-19T00:00:00+00:00",
            channel_kind="wechat_clawbot",
            kind="request_full_report",
            report_id="r-3",
            forwarded_by_openclaw=False,
        )
    )
    assert untrusted["accepted"] is False
