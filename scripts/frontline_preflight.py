#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys


REPO_ROOT = Path(__file__).resolve().parents[1]
SHARED_PYTHON_ROOT = REPO_ROOT / "openclaw_plugins" / "claw-trade-frontline-tools" / "python"
if str(SHARED_PYTHON_ROOT) not in sys.path:
    sys.path.insert(0, str(SHARED_PYTHON_ROOT))

from frontline_data_pack.health import run_frontline_preflight  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description="Run CN_A frontline preflight checks.")
    parser.add_argument("--profile", default="CN_A", help="Stage policy profile, default CN_A")
    parser.add_argument("--run-id", default="preflight-probe", help="Run id used by evidence dir health check")
    parser.add_argument("--evidence-root", default=None, help="Optional explicit evidence root path")
    args = parser.parse_args()

    result = run_frontline_preflight(
        profile=args.profile,
        run_id=args.run_id,
        evidence_root=args.evidence_root,
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())

