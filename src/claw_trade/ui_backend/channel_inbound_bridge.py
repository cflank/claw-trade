from __future__ import annotations

from dataclasses import dataclass
from typing import Callable


@dataclass(frozen=True)
class OpenClawChannelInboundEvent:
    event_id: str
    occurred_at: str
    channel_kind: str
    kind: str
    report_id: str
    openclaw_session_id: str | None = None
    openclaw_message_id: str | None = None
    forwarded_by_openclaw: bool = True


class ChannelInboundBridge:
    def __init__(
        self,
        *,
        request_full_report_file: Callable[[str, str], dict[str, object]],
        build_summary_text: Callable[[str], str],
        send_text: Callable[[str, str, str], dict[str, object]] | None = None,
    ) -> None:
        self._request_full_report_file = request_full_report_file
        self._build_summary_text = build_summary_text
        self._send_text = send_text
        self._seen_event_ids: set[str] = set()

    def handle_openclaw_channel_inbound_event(self, event: OpenClawChannelInboundEvent) -> dict[str, object]:
        if not event.forwarded_by_openclaw:
            return {"accepted": False, "action": "ignored_untrusted"}
        if event.event_id in self._seen_event_ids:
            return {"accepted": True, "action": "deduped"}
        self._seen_event_ids.add(event.event_id)

        if event.kind == "request_full_report":
            result = self._request_full_report_file(event.report_id, f"channel_inbound:{event.event_id}")
            return {"accepted": True, "action": "request_full_report", "result": result}
        if event.kind == "open_report_summary":
            text = self._build_summary_text(event.report_id)
            if self._send_text:
                self._send_text(event.channel_kind, text, f"summary:{event.event_id}")
            return {"accepted": True, "action": "open_report_summary"}
        return {"accepted": False, "action": "ignored"}
