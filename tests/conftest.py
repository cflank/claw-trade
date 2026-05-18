import importlib.util
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

CN_A_FUNDAMENTAL_SCRIPTS = ROOT / "agents/fundamental_analyst/skills/cn-a-fundamental-data/scripts"
CN_A_SOCIAL_SCRIPTS = ROOT / "agents/social_analyst/skills/cn-a-social-data/scripts"
CN_A_SCRIPT_MODULE_NAMES = frozenset(
    {
        "boundary",
        "policy",
    }
)
_ORIGINAL_SPEC_FROM_FILE_LOCATION = importlib.util.spec_from_file_location


class _CnAScriptLoader:
    def __init__(self, loader, script_root: Path) -> None:
        self._loader = loader
        self._script_root = script_root

    def create_module(self, spec):  # noqa: ANN001
        create_module = getattr(self._loader, "create_module", None)
        if create_module is None:
            return None
        return create_module(spec)

    def exec_module(self, module) -> None:  # noqa: ANN001
        _reset_cn_a_script_import_state(preferred_root=self._script_root)
        self._loader.exec_module(module)


def _spec_from_file_location(name, location, *args, **kwargs):  # noqa: ANN001
    spec = _ORIGINAL_SPEC_FROM_FILE_LOCATION(name, location, *args, **kwargs)
    if spec is None or spec.loader is None:
        return spec
    script_root = _cn_a_script_root_for(location)
    if script_root is not None:
        spec.loader = _CnAScriptLoader(spec.loader, script_root)
    return spec


importlib.util.spec_from_file_location = _spec_from_file_location


def pytest_collect_file(file_path: Path, parent):  # noqa: ANN001
    _ = parent
    path_text = Path(str(file_path)).as_posix()
    if "test_cn_a_fundamental" in path_text or "test_cn_a_social" in path_text:
        _reset_cn_a_script_import_state()


def _cn_a_script_root_for(location) -> Path | None:  # noqa: ANN001
    try:
        path = Path(str(location)).resolve()
    except OSError:
        return None
    for root in (CN_A_FUNDAMENTAL_SCRIPTS, CN_A_SOCIAL_SCRIPTS):
        if path.is_relative_to(root):
            return root
    return None


def _reset_cn_a_script_import_state(preferred_root: Path | None = None) -> None:
    for name in CN_A_SCRIPT_MODULE_NAMES:
        sys.modules.pop(name, None)
    for path in (CN_A_FUNDAMENTAL_SCRIPTS, CN_A_SOCIAL_SCRIPTS):
        path_text = str(path)
        while path_text in sys.path:
            sys.path.remove(path_text)
    if preferred_root is not None:
        sys.path.insert(0, str(preferred_root))
