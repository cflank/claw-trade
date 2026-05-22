"""CLI module package."""

from claw_trade.cli.run_control import main, parse_args


def serve_research_ui_main(argv: list[str] | None = None) -> int:
    from claw_trade.web.app import main as web_main

    return web_main(argv)

__all__ = ["main", "parse_args", "serve_research_ui_main"]
