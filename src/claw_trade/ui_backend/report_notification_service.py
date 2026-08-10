from __future__ import annotations

import re
import secrets
from collections.abc import Mapping
from contextlib import AbstractContextManager
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Callable, Protocol

from claw_trade.ui_backend.pdf_export_service import PdfExportService, pdf_export_user_message
from claw_trade.ui_backend.report_cleanup import ReportFileSendTracker
from claw_trade.ui_backend.report_repository import ReportRepository, UiProductError
from claw_trade.ui_backend.summary_builder import (
    CompletionSummaryBuilder,
    render_completion_summary_text,
)
from claw_trade.ui_backend.wechat_delivery_store import WechatDeliveryStore

_MAX_AUTOMATIC_SEND_ATTEMPTS = 2


class ChannelUserBridge(Protocol):
    def get_channel_status(self, *, probe: bool = False) -> dict[str, object]: ...

    def send_text(
        self,
        *,
        channel_kind: str,
        text: str,
        dedupe_key: str,
        target: str | None = None,
        account_id: str | None = None,
    ) -> dict[str, object]: ...

    def send_report_file_via_channel(
        self,
        *,
        request_id: str,
        report_id: str,
        channel_kind: str,
        file_name: str,
        payload: bytes | None = None,
        file_path: Path | None = None,
        target: str | None = None,
        account_id: str | None = None,
    ) -> dict[str, object]: ...

    def resolve_default_report_file_target(self, *, channel_kind: str) -> tuple[str, str | None] | None: ...

    def wechat_delivery_account(self) -> AbstractContextManager[str | None]: ...


class ReportNotificationService:
    def __init__(
        self,
        repository: ReportRepository,
        summary_builder: CompletionSummaryBuilder,
        pdf_export_service: PdfExportService,
        channel_bridge: ChannelUserBridge,
        *,
        in_app_notifier: Callable[[str, str], None] | None = None,
        file_send_tracker: ReportFileSendTracker | None = None,
        delivery_store: WechatDeliveryStore | None = None,
        now_provider: Callable[[], datetime] | None = None,
    ) -> None:
        self._repository = repository
        self._summary_builder = summary_builder
        self._pdf_export_service = pdf_export_service
        self._channel_bridge = channel_bridge
        self._in_app_notifier = in_app_notifier or (lambda _report_id, _text: None)
        self._file_send_tracker = file_send_tracker or ReportFileSendTracker()
        self._delivery_store = delivery_store
        self._now_provider = now_provider or (lambda: datetime.now(UTC))

    def create_report_delivery_intent(self, intent_id: str) -> None:
        if self._delivery_store is not None:
            self._delivery_store.create_waiting_report(intent_id)

    def link_report_delivery_intent(self, intent_id: str, report_id: str) -> None:
        if self._delivery_store is not None:
            self._delivery_store.link_report(intent_id, report_id=report_id)

    def fail_report_delivery_intent(self, intent_id: str, error: str) -> None:
        if self._delivery_store is not None:
            self._delivery_store.mark_report_failed(intent_id, error=error)

    def recover_completed_deliveries(self) -> int:
        if self._delivery_store is None:
            return 0
        recovered = 0
        for record in self._delivery_store.list_delivery_records(states={"waiting_report"}):
            report_id = str(record.get("report_id") or "").strip()
            if not report_id or self._repository.get_report(report_id) is None:
                continue
            self.notify_report_completion(
                report_id,
                delivery_intent_id=str(record["intent_id"]),
            )
            recovered += 1
        return recovered

    def create_binding_code(self, *, request_id: str, ttl_seconds: int = 300) -> dict[str, object]:
        if self._delivery_store is None:
            raise RuntimeError("wechat_delivery_store_unavailable")
        if ttl_seconds < 1 or ttl_seconds > 900:
            raise ValueError("binding_code_ttl_out_of_range")
        code = secrets.token_hex(4).upper()
        expires_at = _to_iso_z(self._now_provider() + timedelta(seconds=ttl_seconds))
        challenge = self._delivery_store.get_or_create_binding_challenge(
            request_id,
            code=code,
            expires_at=expires_at,
        )
        code = challenge["code"]
        expires_at = challenge["expires_at"]
        return {
            "code": code,
            "expiresAt": expires_at,
            "message": f"请在要接收报告的新微信中发送：绑定通知 {code}",
        }

    def bind_notification_recipient(
        self,
        text: str,
        *,
        account_id: str | None,
        sender_id: str,
    ) -> dict[str, object]:
        if self._delivery_store is None:
            return {"bound": False, "message": "微信通知存储不可用，未绑定。"}
        with self._channel_bridge.wechat_delivery_account() as current_account_id:
            incoming_account_id = str(account_id or "").strip()
            sender_id = sender_id.strip()
            if not incoming_account_id or not sender_id or current_account_id != incoming_account_id:
                return {"bound": False, "message": "当前微信账号不唯一或与入站账号不一致，未绑定。"}
            match = re.fullmatch(r"\s*绑定通知\s+([0-9A-Fa-f]{8})\s*", text)
            if match is None or not self._delivery_store.consume_binding_challenge(
                code=match.group(1),
                account_id=incoming_account_id,
                sender_id=sender_id,
                now=_to_iso_z(self._now_provider()),
            ):
                return {"bound": False, "message": "绑定码错误、已过期或已使用。"}
        pending_count = len(self._delivery_store.list_delivery_records(states={"pending"}))
        self.retry_pending()
        return {
            "bound": True,
            "pendingCount": pending_count,
            "message": f"绑定成功，已将 {pending_count} 条待发送报告转移到此微信并开始发送。",
        }

    def remember_current_notification_recipient(self, *, account_id: str | None, sender_id: str) -> bool:
        if self._delivery_store is None:
            return False
        incoming_account_id = str(account_id or "").strip()
        sender_id = sender_id.strip()
        if not incoming_account_id or not sender_id:
            return False
        with self._channel_bridge.wechat_delivery_account() as current_account_id:
            if current_account_id != incoming_account_id:
                return False
            self._delivery_store.set_binding(account_id=incoming_account_id, sender_id=sender_id)
        self.retry_pending()
        return True

    def retry_pending(
        self,
        *,
        manual: bool = False,
        request_id: str | None = None,
    ) -> dict[str, object]:
        if self._delivery_store is None:
            return {"attempted": 0, "sent": 0}
        if manual and request_id:
            reservation = self._delivery_store.begin_manual_retry(request_id)
            if reservation["started"] is not True:
                return {
                    "attempted": int(reservation["attempted"]),
                    "sent": int(reservation["sent"]),
                    "inProgress": bool(reservation["in_progress"]),
                }
        states = {"pending", "unknown"} if manual else {"pending"}
        records = self._delivery_store.list_delivery_records(states=states)
        attempted = 0
        sent = 0
        binding = self._delivery_store.get_binding()
        if binding is not None:
            with self._channel_bridge.wechat_delivery_account() as current_account_id:
                status: Mapping[str, object] = {}
                if current_account_id and binding.get("account_id") == current_account_id:
                    try:
                        status = self._channel_bridge.get_channel_status(probe=False)
                    except Exception:
                        status = {}
                if str(status.get("state")) == "connected" and bool(status.get("canSendText")):
                    for record in records:
                        if not manual and int(record.get("attempt_count", 0)) >= _MAX_AUTOMATIC_SEND_ATTEMPTS:
                            continue
                        delivery = self._attempt_ready_delivery(
                            str(record["intent_id"]),
                            binding=binding,
                            current_account_id=current_account_id,
                            can_send_file=bool(status.get("canSendFile")),
                            allow_unknown=manual,
                        )
                        if delivery is None:
                            continue
                        attempted += 1
                        sent += int(delivery == "sent")
        result: dict[str, object] = {"attempted": attempted, "sent": sent}
        if manual and request_id:
            self._delivery_store.complete_manual_retry(
                request_id,
                attempted=attempted,
                sent=sent,
            )
            result["inProgress"] = False
        return result

    def retry_delivery(self, intent_id: str) -> dict[str, object]:
        state = self._attempt_delivery(intent_id, allow_unknown=True)
        status = self.get_delivery_status(intent_id)
        return {"attempted": state is not None, "delivery": state, "status": status}

    def get_delivery_status(self, intent_id: str) -> dict[str, object] | None:
        if self._delivery_store is None:
            return None
        return self._delivery_store.get_delivery_status(intent_id)

    def latest_delivered_report_for_recipient(self, *, account_id: str | None, sender_id: str) -> str | None:
        store = self._delivery_store
        account_id = str(account_id or "").strip()
        sender_id = sender_id.strip()
        if store is None or not account_id or not sender_id:
            return None
        binding = store.get_binding()
        if binding is None or binding.get("account_id") != account_id or binding.get("sender_id") != sender_id:
            return None
        bound_at = str(binding.get("bound_at") or "")
        deliveries = []
        for item in store.list_delivery_records(states={"sent"}):
            report_id = str(item.get("report_id") or "").strip()
            delivered_account_id = str(item.get("delivered_account_id") or "").strip()
            delivered_sender_id = str(item.get("delivered_sender_id") or "").strip()
            if delivered_account_id or delivered_sender_id:
                delivered_to_binding = delivered_account_id == account_id and delivered_sender_id == sender_id
            else:
                delivered_to_binding = str(item.get("updated_at") or "") > bound_at
            if delivered_to_binding and report_id and self._repository.get_report(report_id) is not None:
                deliveries.append(item)
        if not deliveries:
            return None
        latest = max(deliveries, key=lambda item: str(item.get("updated_at") or ""))
        return str(latest["report_id"])

    def notify_report_completion(
        self,
        report_id: str,
        *,
        channel_kind: str = "wechat_clawbot",
        target: str | None = None,
        account_id: str | None = None,
        text_footer: str | None = None,
        delivery_intent_id: str | None = None,
    ) -> dict[str, object]:
        if self._delivery_store is not None and delivery_intent_id:
            self._delivery_store.mark_report_pending(
                delivery_intent_id,
                report_id=report_id,
                text_footer=text_footer,
            )
            text = self._completion_text(report_id, text_footer=text_footer)
            self._in_app_notifier(report_id, text)
            state = self._attempt_delivery(delivery_intent_id)
            if state is None:
                state = str(self._delivery_store.get_delivery_status(delivery_intent_id)["state"])
            return {
                "sent": state == "sent",
                "delivery": state,
                "text": text,
            }
        summary = self._summary_builder.get_cached(report_id)
        if summary is None:
            summary = self._summary_builder.build_completion_summary_from_saved_report(
                report_id,
                pdf_available=False,
            )
        if not target:
            try:
                default_target = self._channel_bridge.resolve_default_report_file_target(channel_kind=channel_kind)
            except Exception:
                default_target = None
            if default_target is not None:
                target, default_account_id = default_target
                account_id = account_id or default_account_id
        status: dict[str, object] | None = None
        can_send_file: bool | None = None
        if target:
            try:
                status = self._channel_bridge.get_channel_status(probe=False)
            except Exception:
                status = {"state": "unknown", "canSendText": False, "canSendFile": False}
            if str(status.get("state")) == "connected" and bool(status.get("canSendText")):
                can_send_file = bool(status.get("canSendFile"))

        text = _append_text_footer(render_completion_summary_text(summary, can_send_file=can_send_file), text_footer)
        self._in_app_notifier(report_id, text)

        if not target:
            self._in_app_notifier(report_id, "微信通知暂不可用，已在设备界面显示完成摘要。")
            return {"sent": False, "delivery": "in_app_only", "text": text}

        status = status or {"state": "unknown", "canSendText": False, "canSendFile": False}
        if str(status.get("state")) != "connected" or not bool(status.get("canSendText")):
            self._in_app_notifier(report_id, "微信通知暂不可用，已在设备界面显示完成摘要。")
            return {"sent": False, "delivery": "in_app_only", "text": text}
        try:
            sent = self._channel_bridge.send_text(
                channel_kind=channel_kind,
                text=text,
                dedupe_key=f"completion:{report_id}",
                target=target,
                account_id=account_id,
            )
            return {"sent": sent.get("sent") is True, "delivery": "channel", "text": text}
        except Exception:
            self._in_app_notifier(report_id, "微信通知发送失败，已在设备界面显示完成摘要。")
            return {"sent": False, "delivery": "in_app_only", "text": text}

    def _completion_text(
        self,
        report_id: str,
        *,
        text_footer: str | None,
        can_send_file: bool | None = None,
    ) -> str:
        summary = self._summary_builder.get_cached(report_id)
        if summary is None:
            summary = self._summary_builder.build_completion_summary_from_saved_report(
                report_id,
                pdf_available=False,
            )
        return _append_text_footer(
            render_completion_summary_text(summary, can_send_file=can_send_file),
            text_footer,
        )

    def _attempt_delivery(self, intent_id: str, *, allow_unknown: bool = False) -> str | None:
        store = self._delivery_store
        if store is None:
            return None
        delivery_status = store.get_delivery_status(intent_id)
        if (
            not allow_unknown
            and delivery_status is not None
            and int(delivery_status.get("attemptCount", 0)) >= _MAX_AUTOMATIC_SEND_ATTEMPTS
        ):
            return None
        binding = store.get_binding()
        if binding is None:
            return None
        with self._channel_bridge.wechat_delivery_account() as current_account_id:
            if not current_account_id or binding.get("account_id") != current_account_id:
                return None
            try:
                status = self._channel_bridge.get_channel_status(probe=False)
            except Exception:
                return None
            if str(status.get("state")) != "connected" or not bool(status.get("canSendText")):
                return None
            return self._attempt_ready_delivery(
                intent_id,
                binding=binding,
                current_account_id=current_account_id,
                can_send_file=bool(status.get("canSendFile")),
                allow_unknown=allow_unknown,
            )

    def _attempt_ready_delivery(
        self,
        intent_id: str,
        *,
        binding: Mapping[str, str],
        current_account_id: str,
        can_send_file: bool,
        allow_unknown: bool,
    ) -> str | None:
        store = self._delivery_store
        if store is None:
            return None
        record = store.begin_attempt(intent_id, allow_unknown=allow_unknown)
        if record is None:
            return None
        report_id = str(record.get("report_id") or "").strip()
        if not report_id:
            store.mark_unknown(intent_id, error="missing_report_id")
            return "unknown"
        text = self._completion_text(
            report_id,
            text_footer=str(record.get("text_footer") or "") or None,
            can_send_file=can_send_file,
        )
        try:
            result = self._channel_bridge.send_text(
                channel_kind="wechat_clawbot",
                text=text,
                dedupe_key=intent_id,
                target=binding["sender_id"],
                account_id=current_account_id,
            )
        except Exception:
            try:
                store.mark_unknown(intent_id, error="send_result_unknown")
            except Exception:
                pass
            return "unknown"
        if result.get("sent") is True:
            message_id = result.get("messageId") or result.get("message_id")
            try:
                store.mark_sent(
                    intent_id,
                    message_id=str(message_id or "") or None,
                    account_id=current_account_id,
                    sender_id=binding["sender_id"],
                )
            except Exception:
                return "unknown"
            return "sent"
        if result.get("sent") is False and result.get("resultKnown") is True:
            try:
                store.mark_known_failure(intent_id, error="send_rejected")
            except Exception:
                return "unknown"
            return "pending"
        try:
            store.mark_unknown(intent_id, error="send_result_unknown")
        except Exception:
            pass
        return "unknown"

    def request_full_report_file(
        self,
        report_id: str,
        request_id: str,
        *,
        channel_kind: str = "wechat_clawbot",
        target: str | None = None,
        account_id: str | None = None,
    ) -> dict[str, object]:
        with self._file_send_tracker.track(report_id):
            return self._request_full_report_file(
                report_id,
                request_id,
                channel_kind=channel_kind,
                target=target,
                account_id=account_id,
            )

    def _request_full_report_file(
        self,
        report_id: str,
        request_id: str,
        *,
        channel_kind: str,
        target: str | None,
        account_id: str | None,
    ) -> dict[str, object]:
        report = self._repository.get_report(report_id)
        if report is None:
            raise UiProductError("REPORT_NOT_FOUND", "没有找到这份报告。")

        current_default_target = self._channel_bridge.resolve_default_report_file_target(channel_kind=channel_kind)
        resolved_target = str(target or "").strip()
        resolved_account_id = str(account_id or "").strip() or None
        if not resolved_target:
            if current_default_target is not None:
                resolved_target, resolved_account_id = current_default_target
        if not resolved_target:
            return {
                "sent": False,
                "code": "NOTIFICATION_UNAVAILABLE",
                "userMessage": "微信已连接，但没有可投递的微信聊天。请先在要接收报告的聊天里给 ClawBot 发一条消息，再点转发。",
            }

        try:
            payload = self._pdf_export_service.render_saved_markdown_to_pdf_bytes(report_id)
        except Exception as exc:
            return {
                "sent": False,
                "code": "PDF_EXPORT_FAILED",
                "userMessage": pdf_export_user_message(exc),
            }

        try:
            result = self._channel_bridge.send_report_file_via_channel(
                request_id=request_id,
                report_id=report_id,
                channel_kind=channel_kind,
                file_name=_report_pdf_filename(report.instrument_code, report.market, report.generated_at),
                payload=payload,
                file_path=None,
                target=resolved_target,
                account_id=resolved_account_id,
            )
        except Exception as exc:
            code = str(getattr(exc, "code", "FILE_SEND_UNSUPPORTED"))
            if code not in {"NOTIFICATION_UNAVAILABLE", "FILE_SEND_UNSUPPORTED"}:
                code = "FILE_SEND_UNSUPPORTED"
            user_message = str(getattr(exc, "user_message", "") or "").strip()
            return {
                "sent": False,
                "code": code,
                "userMessage": user_message
                or (
                    "完整报告文件暂不可发送，请在设备界面查看。"
                    if code == "FILE_SEND_UNSUPPORTED"
                    else "微信文件发送失败，报告没有发出。请稍后重试。"
                ),
            }
        if not isinstance(result, Mapping):
            return {
                "sent": False,
                "code": "FILE_SEND_UNSUPPORTED",
                "userMessage": "完整报告文件暂不可发送，请在设备界面查看。",
            }
        message_id = result.get("messageId") or result.get("message_id")
        if isinstance(message_id, str):
            message_id = message_id.strip() or None
        else:
            message_id = None
        if result.get("ok") is False:
            sent = False
        elif "sent" in result:
            sent = bool(result.get("sent"))
        else:
            sent = bool(message_id)
        if not sent:
            return {
                "sent": False,
                "code": "FILE_SEND_UNSUPPORTED",
                "userMessage": "完整报告文件暂不可发送，请在设备界面查看。",
            }
        return {
            "sent": True,
            "messageId": message_id,
            "userMessage": "完整报告已发送。",
        }


def _report_pdf_filename(instrument_code: str, market: str, generated_at: str) -> str:
    parts = [
        _filename_token(instrument_code) or "report",
        _filename_token(market),
        _filename_token(generated_at[:10]),
        "report",
    ]
    return "-".join(part for part in parts if part) + ".pdf"


def _append_text_footer(text: str, footer: str | None) -> str:
    clean_footer = (footer or "").strip()
    if not clean_footer:
        return text
    return f"{text.rstrip()}\n{clean_footer}"


def _filename_token(value: str) -> str:
    return re.sub(r"[^\w.-]+", "_", str(value or "")).strip("._-")


def _to_iso_z(value: datetime) -> str:
    return value.astimezone(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")
