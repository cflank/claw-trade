from __future__ import annotations

import argparse
from pathlib import Path

from claw_trade.production.paths import ProductionPaths
from claw_trade.production.preflight import run_preflight


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", default="/opt/claw-trade")
    args = parser.parse_args(argv)
    result = run_preflight(ProductionPaths.from_root(Path(args.root)))
    for failure in result.failures:
        print(f"{failure.code}: {failure.message}")
    return 0 if result.ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
