from __future__ import annotations

import subprocess
import sys
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[2]
SHARED_PYTHON_ROOT = REPO_ROOT / "openclaw_plugins" / "claw-trade-frontline-tools" / "python"

SCRIPT_ENTRYPOINTS: tuple[Path, ...] = (
    REPO_ROOT / "agents/market_analyst/skills/cn-a-market-data/scripts/market_data_pack.py",
    REPO_ROOT / "agents/fundamental_analyst/skills/cn-a-fundamental-data/scripts/fundamental_data_pack.py",
    REPO_ROOT / "agents/news_analyst/skills/cn-a-news-data/scripts/news_data_pack.py",
    REPO_ROOT / "agents/social_analyst/skills/cn-a-social-data/scripts/social_data_pack.py",
)

SHARED_MODULES: tuple[str, ...] = (
    "frontline_data_pack",
    "frontline_data_pack.runtime_context",
    "frontline_data_pack.pack_envelope",
    "frontline_data_pack.provider",
    "frontline_data_pack.mongodb",
    "frontline_data_pack.openviking",
    "frontline_data_pack.brief",
    "frontline_data_pack.us_data_pack",
)


def test_frontline_shared_python_modules_are_importable() -> None:
    code = """
import importlib
import sys
from pathlib import Path

shared_root = Path(sys.argv[1])
sys.path.insert(0, str(shared_root))
for module_name in sys.argv[2:]:
    importlib.import_module(module_name)
"""
    result = subprocess.run(
        [sys.executable, "-c", code, str(SHARED_PYTHON_ROOT), *SHARED_MODULES],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, f"shared module import failed: {result.stderr}"


def test_frontline_script_entrypoints_are_importable() -> None:
    code = """
import importlib.util
import sys
from pathlib import Path

module_path = Path(sys.argv[1])
module_name = sys.argv[2]
scripts_root = module_path.parent
sys.path.insert(0, str(scripts_root))
spec = importlib.util.spec_from_file_location(module_name, module_path)
if spec is None or spec.loader is None:
    raise RuntimeError(f"unable to load {module_path}")
module = importlib.util.module_from_spec(spec)
sys.modules[module_name] = module
spec.loader.exec_module(module)
"""
    for index, module_path in enumerate(SCRIPT_ENTRYPOINTS):
        result = subprocess.run(
            [sys.executable, "-c", code, str(module_path), f"frontline_import_{index}"],
            cwd=REPO_ROOT,
            capture_output=True,
            text=True,
            check=False,
        )
        assert result.returncode == 0, f"entrypoint import failed for {module_path}: {result.stderr}"
