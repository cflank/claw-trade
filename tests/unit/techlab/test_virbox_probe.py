from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path


def _load_probe_module():
    script_path = Path(__file__).parents[3] / "techlab" / "virbox_probe" / "virbox_probe.py"
    spec = importlib.util.spec_from_file_location("virbox_probe", script_path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def test_probe_fails_without_official_status_command(monkeypatch, capsys) -> None:
    probe = _load_probe_module()
    monkeypatch.delenv("VIRBOX_STATUS_COMMAND", raising=False)

    assert probe.main() == 2

    payload = json.loads(capsys.readouterr().out)
    assert payload["normalized_status"] == "runtime_unavailable"
    assert payload["features"] == []
    assert payload["error_code"] == "virbox_probe_error"


def test_probe_wraps_official_status_command_json(tmp_path: Path, monkeypatch, capsys) -> None:
    probe = _load_probe_module()
    command = tmp_path / "official-status"
    command.write_text(
        "#!/usr/bin/env python3\n"
        "import json\n"
        "print(json.dumps({\n"
        "    'raw_status': 'activated',\n"
        "    'normalized_status': 'activated',\n"
        "    'features': ['report', 'data_refresh'],\n"
        "    'license_suffix': '1234',\n"
        "}))\n",
        encoding="utf-8",
    )
    command.chmod(0o755)
    monkeypatch.setenv("VIRBOX_STATUS_COMMAND", str(command))

    assert probe.main() == 0

    payload = json.loads(capsys.readouterr().out)
    assert payload["raw_status"] == "activated"
    assert payload["normalized_status"] == "activated"
    assert payload["features"] == ["report", "data_refresh"]
    assert payload["license_suffix"] == "1234"
    assert payload["error_code"] is None


def test_probe_rejects_json_without_normalized_status(tmp_path: Path, monkeypatch, capsys) -> None:
    probe = _load_probe_module()
    command = tmp_path / "official-status"
    command.write_text("#!/usr/bin/env python3\nprint('{}')\n", encoding="utf-8")
    command.chmod(0o755)
    monkeypatch.setenv("VIRBOX_STATUS_COMMAND", str(command))

    assert probe.main() == 2

    payload = json.loads(capsys.readouterr().out)
    assert payload["normalized_status"] == "runtime_unavailable"
    assert "normalized_status" in payload["error_message"]
