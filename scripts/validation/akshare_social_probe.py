from __future__ import annotations

import argparse
import json
import signal
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Mapping

CODE = "600519"
SYMBOLS = ("SH600519", "100.600519")
TIMEOUT_SECONDS = 15


class TimeoutGuard:
    def __init__(self, seconds: int) -> None:
        self._seconds = seconds
        self._previous: Any = None

    def __enter__(self) -> None:
        self._previous = signal.signal(signal.SIGALRM, self._raise)
        signal.alarm(self._seconds)

    def __exit__(self, *args: object) -> None:
        signal.alarm(0)
        signal.signal(signal.SIGALRM, self._previous)

    @staticmethod
    def _raise(signum: int, frame: object) -> None:
        del signum, frame
        raise TimeoutError("timeout")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", required=True)
    args = parser.parse_args()

    import akshare as ak

    calls: list[tuple[str, dict[str, Any]]] = []
    for name in ("stock_hot_rank_latest_em", "stock_hot_keyword_em", "stock_hot_rank_relate_em"):
        for symbol in SYMBOLS:
            calls.append((name, {"symbol": symbol}))
    calls.extend(
        [
            ("stock_hot_rank_em", {}),
            ("stock_hot_up_em", {}),
            ("stock_news_em", {"symbol": CODE}),
        ]
    )

    results = [_run_call(ak, name, kwargs) for name, kwargs in calls]
    payload = {
        "schema_version": "akshare-social-probe-v1",
        "generated_at": datetime.now(tz=UTC).isoformat(),
        "target_code": CODE,
        "tested_symbol_forms": SYMBOLS,
        "results": results,
        "summary": _summary(results),
    }
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")
    print(json.dumps(payload["summary"], ensure_ascii=False, sort_keys=True))
    return 0


def _run_call(ak: Any, name: str, kwargs: Mapping[str, Any]) -> dict[str, Any]:
    started = time.monotonic()
    if not hasattr(ak, name):
        return {
            "function": name,
            "kwargs": dict(kwargs),
            "status": "missing_function",
            "elapsed_ms": int((time.monotonic() - started) * 1000),
        }
    try:
        with TimeoutGuard(TIMEOUT_SECONDS):
            frame = getattr(ak, name)(**kwargs)
        columns = [str(column) for column in getattr(frame, "columns", [])]
        rows = _frame_rows(frame)
        return {
            "function": name,
            "kwargs": dict(kwargs),
            "status": "ok" if rows else "empty",
            "row_count": len(rows),
            "columns": columns,
            "sample": rows[:3],
            "social_usable_fields": _social_usable_fields(columns, rows),
            "elapsed_ms": int((time.monotonic() - started) * 1000),
        }
    except Exception as exc:
        return {
            "function": name,
            "kwargs": dict(kwargs),
            "status": "error",
            "error": f"{type(exc).__name__}: {str(exc)[:300]}",
            "elapsed_ms": int((time.monotonic() - started) * 1000),
        }


def _frame_rows(frame: Any) -> list[dict[str, Any]]:
    if frame is None or not hasattr(frame, "to_dict"):
        return []
    try:
        records = frame.to_dict(orient="records")
    except TypeError:
        return []
    if not isinstance(records, list):
        return []
    return [_jsonable(row) for row in records if isinstance(row, Mapping)]


def _jsonable(row: Mapping[str, Any]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in row.items():
        if hasattr(value, "isoformat"):
            result[str(key)] = value.isoformat()
        elif value is None or isinstance(value, (str, int, float, bool)):
            result[str(key)] = value
        else:
            result[str(key)] = str(value)
    return result


def _social_usable_fields(columns: list[str], rows: list[dict[str, Any]]) -> tuple[str, ...]:
    text = " ".join(columns).lower()
    fields: set[str] = set()
    if any(key in text for key in ("排名", "rank")):
        fields.add("rank")
    if any(key in text for key in ("热度", "关注", "新晋粉丝", "粉丝", "heat", "popularity")):
        fields.add("score")
    if any(key in text for key in ("关键词", "keyword", "词")):
        fields.add("keyword")
    if any(key in text for key in ("时间", "日期", "date", "time")):
        fields.add("timestamp")
    if any(key in text for key in ("来源", "source")):
        fields.add("source")
    if rows:
        fields.add("source")
    return tuple(sorted(fields))


def _summary(results: list[Mapping[str, Any]]) -> dict[str, Any]:
    ok = [item for item in results if item.get("status") == "ok"]
    return {
        "tested_count": len(results),
        "ok_count": len(ok),
        "empty_count": sum(1 for item in results if item.get("status") == "empty"),
        "error_count": sum(1 for item in results if item.get("status") == "error"),
        "missing_function_count": sum(1 for item in results if item.get("status") == "missing_function"),
        "ok_functions": [f"{item.get('function')} {item.get('kwargs')}" for item in ok],
        "covered_fields": sorted({field for item in ok for field in item.get("social_usable_fields", ())}),
    }


if __name__ == "__main__":
    raise SystemExit(main())
