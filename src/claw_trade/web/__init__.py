from __future__ import annotations


def build_research_ui_app(*args, **kwargs):  # type: ignore[no-untyped-def]
    from claw_trade.web.app import build_research_ui_app as _impl

    return _impl(*args, **kwargs)


__all__ = ["build_research_ui_app"]
