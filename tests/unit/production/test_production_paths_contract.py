from __future__ import annotations

import json
import os
import re
import subprocess
import tarfile
from pathlib import Path

from claw_trade.production import paths


ROOT = Path(__file__).resolve().parents[3]


def _read(relative: str) -> str:
    return (ROOT / relative).read_text(encoding="utf-8")


def test_production_path_constants_pin_single_install_layout() -> None:
    assert paths.INSTALL_ROOT == Path("/opt/claw-trade")
    assert paths.RELEASES_DIR == Path("/opt/claw-trade/releases")
    assert paths.CURRENT_LINK == Path("/opt/claw-trade/current")
    assert paths.SHARED_DIR == Path("/opt/claw-trade/shared")
    assert paths.AUTO_UPDATE_SERVICE_NAME == "claw-trade-auto-update.service"
    assert paths.AUTO_UPDATE_TIMER_NAME == "claw-trade-auto-update.timer"
    assert paths.required_shared_paths() == tuple(paths.SHARED_DIR / item for item in paths.REQUIRED_SHARED_DIRS)


def test_formal_package_and_release_name_contract() -> None:
    release_name = "claw-trade-production-0.1.0-20260626T120000Z"
    archive_name = f"{release_name}.tar.gz"

    assert paths.FORMAL_PACKAGE_PREFIX == "claw-trade-production"
    assert paths.FORMAL_PACKAGE_ARCHIVE_GLOB == "claw-trade-production-*.tar.gz"
    assert paths.RELEASE_DIR_NAME_RULE == "archive top-level directory must equal archive basename without .tar.gz"
    assert re.fullmatch(paths.RELEASE_DIR_NAME_REGEX, release_name)
    assert re.fullmatch(paths.FORMAL_PACKAGE_ARCHIVE_REGEX, archive_name)
    assert not re.fullmatch(paths.FORMAL_PACKAGE_ARCHIVE_REGEX, "claw-trade-factory-test-0.1.0-20260626T120000Z.tar.gz")

    build_script = _read("scripts/production/build_production_package.sh")
    install_script = _read("scripts/production/install_production_package.sh")
    factory_script = _read("scripts/production/install_factory_test_ubuntu.sh")
    assert 'package_name="claw-trade-production-${version}-${stamp}"' in build_script
    assert "<claw-trade-production-*.tar.gz>" in install_script
    assert "/tmp/claw-trade-production-*.tar.gz" in factory_script
    assert "validate_production_archive.py" in install_script
    assert "validate_production_archive.py" in factory_script


def test_update_public_key_owner_and_mode_contract() -> None:
    assert paths.UPDATE_PUBLIC_KEY_PATH == Path("/etc/claw-trade/update-signing-public.pem")
    assert paths.PRODUCTION_USER == "clawtrade"
    assert paths.PRODUCTION_GROUP == "clawtrade"
    assert paths.RELEASE_OWNER == "root"
    assert paths.RELEASE_GROUP == "root"
    assert paths.SHARED_OWNER == paths.PRODUCTION_USER
    assert paths.SHARED_GROUP == paths.PRODUCTION_GROUP
    assert paths.RELEASE_DIR_MODE == "0755"
    assert paths.SHARED_DIR_MODE == "0750"
    assert paths.UPDATE_PUBLIC_KEY_MODE == "0644"


def test_required_shared_dirs_cover_update_and_rescue_state() -> None:
    assert set(paths.REQUIRED_SHARED_DIRS) == {
        "cache",
        "config",
        "data",
        "license",
        "logs",
        "logs/diagnostics",
        "logs/factory-reset",
        "openclaw",
        "queues",
        "reports",
        "runs",
        "sessions",
        "tmp",
        "updates",
        "updates/downloads",
        "updates/logs",
    }


def test_install_scripts_create_the_same_shared_dirs_under_default_root() -> None:
    expected_top_level = "{cache,config,data,license,logs,openclaw,queues,reports,runs,sessions,tmp,updates}"
    expected_shared_root = '"${install_root}/shared"'
    expected_logs = '"${install_root}/shared/logs"/{diagnostics,factory-reset}'
    expected_updates = '"${install_root}/shared/updates"/{downloads,logs}'

    for script in (
        "scripts/production/install_production_package.sh",
        "scripts/production/install_factory_test_ubuntu.sh",
    ):
        text = _read(script)
        assert 'install_root="${CLAW_TRADE_INSTALL_ROOT:-/opt/claw-trade}"' in text
        assert 'install -d -m 0755 "${release_root}"' in text or 'sudo install -d -m 0755 "${install_root}/releases"' in text
        assert "install -d -m 0750" in text
        assert expected_shared_root in text
        assert f'"${{install_root}}/shared"/{expected_top_level}' in text
        assert expected_logs in text
        assert expected_updates in text


def test_factory_install_supports_license_key_file_activation() -> None:
    factory_script = _read("scripts/production/install_factory_test_ubuntu.sh")

    assert "--license-key-file" in factory_script
    assert "license_key_file" in factory_script
    assert "tr -d '[:space:]'" in factory_script
    assert "/v1/license/bindLicenseKey?licenseKey=" in factory_script
    assert "license key file not found" in factory_script
    assert "Virbox local service not reachable" in factory_script
    assert "cat \"${body_file}\"" not in factory_script


def test_formal_install_assigns_release_and_shared_dirs_to_service_user() -> None:
    install_script = _read("scripts/production/install_production_package.sh")
    factory_script = _read("scripts/production/install_factory_test_ubuntu.sh")
    readme = _read("packaging/production/README_FACTORY_TEST.md")

    assert 'runtime_owner="${CLAW_TRADE_RUNTIME_OWNER:-clawtrade}"' in install_script
    assert 'runtime_group="${CLAW_TRADE_RUNTIME_GROUP:-clawtrade}"' in install_script
    assert 'kiosk_owner="${CLAW_TRADE_KIOSK_OWNER:-clawkiosk}"' in install_script
    assert 'kiosk_group="${CLAW_TRADE_KIOSK_GROUP:-clawkiosk}"' in install_script
    assert 'kiosk_browser="${CLAW_TRADE_KIOSK_BROWSER_BIN:-/usr/bin/chromium-browser}"' in install_script
    assert 'groupadd --system "${group}"' in install_script
    assert 'useradd --system --no-create-home --gid "${group}" --shell /usr/sbin/nologin "${user}"' in install_script
    assert 'ensure_system_identity "${runtime_owner}" "${runtime_group}"' in install_script
    assert 'ensure_system_identity "${kiosk_owner}" "${kiosk_group}"' in install_script
    assert '[[ -x "${kiosk_browser}" ]] || fail "missing kiosk browser: ${kiosk_browser}"' in install_script
    assert 'chmod 0755 "${release_root}/${top_dir}"' in install_script
    assert 'chown root:root "${release_root}"' in install_script
    assert 'chown -R root:root "${release_root}/${top_dir}"' in install_script
    assert 'chown -R "${runtime_owner}:${runtime_group}" "${install_root}/shared"' in install_script
    assert '"${release_root}/${top_dir}/bin/claw-trade-preflight"' in install_script
    assert 'tmp_current="${install_root}/.current.${top_dir}.$$"' in install_script
    assert 'mv -Tf "${tmp_current}" "${install_root}/current"' in install_script
    assert 'chown -h root:root "${install_root}/current"' in install_script
    assert 'mv -Tf "${tmp_rescue_current}" "${install_root}/rescue-current"' in install_script

    assert 'chown -h root:root "${install_root}/rescue-current"' in install_script
    assert 'install -m 0755 "${release_root}/${top_dir}/root-helper/claw-trade-apply-update" /usr/local/lib/claw-trade/claw-trade-apply-update' in install_script
    assert 'install -m 0644 "${release_root}/${top_dir}/systemd/"*.service /etc/systemd/system/' in install_script
    assert 'install -m 0644 "${release_root}/${top_dir}/systemd/"*.timer /etc/systemd/system/' in install_script
    assert f"systemctl enable --now {paths.AUTO_UPDATE_TIMER_NAME}" in install_script
    assert 'install -m 0440 "${release_root}/${top_dir}/sudoers/claw-trade-update" /etc/sudoers.d/claw-trade-update' in install_script
    assert "visudo -cf /etc/sudoers.d/claw-trade-update" in install_script
    assert install_script.index('"${release_root}/${top_dir}/bin/claw-trade-preflight"') < install_script.index(
        'mv -Tf "${tmp_current}" "${install_root}/current"'
    )
    assert install_script.index("/usr/local/lib/claw-trade/claw-trade-apply-update") < install_script.index(
        'mv -Tf "${tmp_current}" "${install_root}/current"'
    )

    assert 'runtime_owner="${CLAW_TRADE_RUNTIME_OWNER:-clawtrade}"' in factory_script
    assert 'runtime_group="${CLAW_TRADE_RUNTIME_GROUP:-clawtrade}"' in factory_script
    assert 'sudo -u "${runtime_owner}" -g "${runtime_group}"' in factory_script
    assert 'patch_runtime_control "${install_root}/releases/${top_dir}"' in factory_script
    assert '"${install_root}/releases/${top_dir}/bin/claw-trade-preflight"' in factory_script
    assert 'sudo mv -Tf "${tmp_current}" "${install_root}/current"' in factory_script
    assert 'sudo mv -Tf "${tmp_rescue_current}" "${install_root}/rescue-current"' in factory_script
    assert 'sudo chown root:root "${install_root}/releases"' in factory_script
    assert 'sudo chown -R root:root "${install_root}/releases/${top_dir}"' in factory_script
    assert 'sudo chown -R "${runtime_owner}:${runtime_group}" "${install_root}/shared"' in factory_script
    assert 'sudo chown -h root:root "${install_root}/current"' in factory_script
    assert 'sudo chown -h root:root "${install_root}/rescue-current"' in factory_script
    assert factory_script.index('"${install_root}/releases/${top_dir}/bin/claw-trade-preflight"') < factory_script.index(
        'sudo mv -Tf "${tmp_current}" "${install_root}/current"'
    )
    assert "ufw allow 5175/tcp" not in factory_script
    assert "hostname -I" not in factory_script

    assert "sudo scripts/production/install_production_package.sh /path/to/claw-trade-production-0.1.0-20260626T120000Z.tar.gz" in readme
    assert "校验 archive basename 和包内唯一顶层目录一致" in readme
    assert "不要手动 `tar -xzf` 后再用通配符 `ln -sfn` 切换 `current`" in readme
    assert "最小 rescue 服务" in readme
    assert "systemd 失败后 rescue takeover 触发器" in readme
    assert "恢复出厂后端、维护锁、设置页入口、signed manifest 检查、signed archive 手动安装" in readme
    assert "服务重启健康检查和失败回滚基线" in readme
    assert "systemd updater timer" in readme
    assert "服务重启健康检查" in readme
    assert "sudo ln -sfn /opt/claw-trade/releases/claw-trade-production-*" not in readme
    assert (
        "sudo systemctl enable --now claw-trade-control.service claw-trade-ui.service "
        "claw-trade-kiosk.service claw-trade-auto-update.timer"
    ) in readme
    assert "sudo /opt/claw-trade/current/bin/claw-trade-control" not in readme


def test_production_package_bundles_weixin_plugin_and_uses_production_runtime() -> None:
    build_script = _read("scripts/production/build_production_package.sh")

    assert 'OPENCLAW_WEIXIN_PLUGIN_SPEC="${OPENCLAW_WEIXIN_PLUGIN_SPEC:-@tencent-weixin/openclaw-weixin@2.4.4}"' in build_script
    assert "prepare_openclaw_plugin_assets" in build_script
    assert 'npm install \\' in build_script
    assert 'cp -a packaging/production/runtime/. "${package_root}/runtime/"' in build_script
    assert 'cp -a scripts/start-control-runtime.sh "${package_root}/runtime/claw-trade-control-runtime"' not in build_script
    assert "tar --exclude='agents/*/prompt-review.yaml'" in build_script
    assert 'tar -C "${package_root}" -cf "${package_root}/runtime/assets/openclaw_plugins.tar" openclaw_plugins' in build_script


def test_production_archive_validator_requires_basename_matched_single_top_dir(tmp_path: Path) -> None:
    validator = ROOT / "scripts/production/validate_production_archive.py"
    archive = tmp_path / "claw-trade-production-0.1.0-20260626T120000Z.tar.gz"
    release_name = archive.name[: -len(".tar.gz")]

    with tarfile.open(archive, "w:gz") as tar:
        info = tarfile.TarInfo(f"{release_name}/bin/claw-trade-ui")
        info.size = 0
        tar.addfile(info)

    completed = subprocess.run(["python3", str(validator), str(archive)], check=True, capture_output=True, text=True)
    assert completed.stdout.strip() == release_name

    bad_archive = tmp_path / "claw-trade-production-0.1.0-20260626T120001Z.tar.gz"
    with tarfile.open(bad_archive, "w:gz") as tar:
        info = tarfile.TarInfo("wrong-release/bin/claw-trade-ui")
        info.size = 0
        tar.addfile(info)

    failed = subprocess.run(["python3", str(validator), str(bad_archive)], check=False, capture_output=True, text=True)
    assert failed.returncode != 0
    assert "does not match archive basename" in failed.stderr

    traversal_archive = tmp_path / "claw-trade-production-0.1.0-20260626T120002Z.tar.gz"
    with tarfile.open(traversal_archive, "w:gz") as tar:
        info = tarfile.TarInfo("claw-trade-production-0.1.0-20260626T120002Z/../escape")
        info.size = 0
        tar.addfile(info)

    failed = subprocess.run(
        ["python3", str(validator), str(traversal_archive)],
        check=False,
        capture_output=True,
        text=True,
    )
    assert failed.returncode != 0
    assert "invalid archive member path" in failed.stderr

    symlink_archive = tmp_path / "claw-trade-production-0.1.0-20260626T120003Z.tar.gz"
    with tarfile.open(symlink_archive, "w:gz") as tar:
        info = tarfile.TarInfo("claw-trade-production-0.1.0-20260626T120003Z/link")
        info.type = tarfile.SYMTYPE
        info.linkname = "/tmp/escape"
        tar.addfile(info)

    failed = subprocess.run(["python3", str(validator), str(symlink_archive)], check=False, capture_output=True, text=True)
    assert failed.returncode != 0
    assert "unsupported archive member type" in failed.stderr

    setuid_archive = tmp_path / "claw-trade-production-0.1.0-20260626T120004Z.tar.gz"
    with tarfile.open(setuid_archive, "w:gz") as tar:
        info = tarfile.TarInfo("claw-trade-production-0.1.0-20260626T120004Z/bin/tool")
        info.mode = 0o4755
        info.size = 0
        tar.addfile(info)

    failed = subprocess.run(["python3", str(validator), str(setuid_archive)], check=False, capture_output=True, text=True)
    assert failed.returncode != 0
    assert "setuid/setgid" in failed.stderr


def test_production_preflight_requires_systemd_sudoers_and_root_helper_inputs() -> None:
    preflight = _read("packaging/production/bin/claw-trade-preflight")

    assert "test -x /usr/bin/openssl" in preflight
    for unit in (
        "claw-trade-control.service",
        "claw-trade-ui.service",
        "claw-trade-kiosk.service",
        "claw-trade-rescue.service",
        "claw-trade-rescue-trigger.service",
        "claw-trade-apply-update.service",
        "claw-trade-auto-update.service",
    ):
        assert f'test -f "${{ROOT_DIR}}/systemd/{unit}"' in preflight
    assert f'test -f "${{ROOT_DIR}}/systemd/{paths.AUTO_UPDATE_TIMER_NAME}"' in preflight
    assert 'test -x "${ROOT_DIR}/bin/claw-trade-auto-update"' in preflight
    assert 'test -x "${ROOT_DIR}/root-helper/claw-trade-apply-update"' in preflight
    assert 'test -f "${ROOT_DIR}/sudoers/claw-trade-update"' in preflight


def test_production_packaging_does_not_define_parallel_opt_roots() -> None:
    production_files = [
        path
        for base in (ROOT / "scripts/production", ROOT / "packaging/production")
        for path in base.rglob("*")
        if path.is_file() and "__pycache__" not in path.parts
    ]
    opt_paths: set[str] = set()
    for path in production_files:
        opt_paths.update(re.findall(r"/opt/[A-Za-z0-9._*/-]*", path.read_text(encoding="utf-8")))

    assert opt_paths
    for opt_path in opt_paths:
        assert opt_path in {"/opt", "/opt/"} or opt_path == str(paths.INSTALL_ROOT) or opt_path.startswith(
            f"{paths.INSTALL_ROOT}/"
        )


def test_systemd_units_use_current_release_and_shared_config_only() -> None:
    for service in ("claw-trade-control.service", "claw-trade-ui.service"):
        text = _read(f"packaging/production/systemd/{service}")
        assert f"User={paths.PRODUCTION_USER}" in text
        assert f"Group={paths.PRODUCTION_GROUP}" in text
        assert f"WorkingDirectory={paths.SHARED_DIR}" in text
        assert f"EnvironmentFile=-{paths.SHARED_DIR}/config/claw-trade.env" in text
        assert re.search(rf"^ExecStart={paths.CURRENT_LINK}/bin/claw-trade-(control|ui)$", text, re.MULTILINE)
    assert f"EnvironmentFile=-{paths.SHARED_DIR}/tmp/dev-services/runtime.env" in _read(
        "packaging/production/systemd/claw-trade-ui.service"
    )

    rescue = _read(f"packaging/production/systemd/{paths.RESCUE_SERVICE_NAME}")
    assert f"User={paths.PRODUCTION_USER}" in rescue
    assert f"Group={paths.PRODUCTION_GROUP}" in rescue
    assert f"WorkingDirectory={paths.SHARED_DIR}" in rescue
    assert f"EnvironmentFile=-{paths.SHARED_DIR}/config/claw-trade.env" in rescue
    assert f"ExecStart={paths.RESCUE_CURRENT_LINK}/bin/{paths.RESCUE_BIN_NAME}" in rescue

    apply_update = _read(f"packaging/production/systemd/{paths.UPDATE_APPLY_SERVICE_NAME}")
    assert "Type=oneshot" in apply_update
    assert "WorkingDirectory=" not in apply_update
    assert "EnvironmentFile=" not in apply_update
    assert "ExecStart=/usr/local/lib/claw-trade/claw-trade-apply-update" in apply_update

    auto_update = _read(f"packaging/production/systemd/{paths.AUTO_UPDATE_SERVICE_NAME}")
    assert "Type=oneshot" in auto_update
    assert f"User={paths.PRODUCTION_USER}" in auto_update
    assert f"Group={paths.PRODUCTION_GROUP}" in auto_update
    assert f"WorkingDirectory={paths.SHARED_DIR}" in auto_update
    assert f"EnvironmentFile=-{paths.SHARED_DIR}/config/claw-trade.env" in auto_update
    assert f"ExecStart={paths.CURRENT_LINK}/bin/claw-trade-auto-update" in auto_update

    auto_update_timer = _read(f"packaging/production/systemd/{paths.AUTO_UPDATE_TIMER_NAME}")
    assert "OnBootSec=10min" in auto_update_timer
    assert "OnUnitActiveSec=6h" in auto_update_timer
    assert "RandomizedDelaySec=30min" in auto_update_timer
    assert "Persistent=true" in auto_update_timer
    assert f"Unit={paths.AUTO_UPDATE_SERVICE_NAME}" in auto_update_timer

    for service in ("claw-trade-control.service", "claw-trade-ui.service"):
        text = _read(f"packaging/production/systemd/{service}")
        assert f"OnFailure={paths.RESCUE_TRIGGER_SERVICE_NAME}" in text
        assert "StartLimitIntervalSec=60" in text
        assert "StartLimitBurst=3" in text
    assert "SuccessExitStatus=143" in _read("packaging/production/systemd/claw-trade-control.service")

    trigger = _read(f"packaging/production/systemd/{paths.RESCUE_TRIGGER_SERVICE_NAME}")
    assert f"ExecStart=-/bin/systemctl stop {paths.MAIN_UI_SERVICE_NAME}" in trigger
    assert f"ExecStart=/bin/systemctl start {paths.RESCUE_SERVICE_NAME}" in trigger

    sudoers = _read("packaging/production/sudoers/claw-trade-update")
    assert f"{paths.PRODUCTION_USER} ALL=(root) NOPASSWD: /bin/systemctl start --no-block {paths.UPDATE_APPLY_SERVICE_NAME}" in sudoers


def test_main_and_rescue_share_the_fixed_local_ui_port_contract() -> None:
    assert paths.MAIN_UI_PORT == 5175
    assert paths.RESCUE_UI_PORT == paths.MAIN_UI_PORT
    assert paths.RESCUE_BIND_HOST == "127.0.0.1"
    assert paths.LOCAL_UI_URL == "http://127.0.0.1:5175/"
    assert paths.RESCUE_TAKEOVER_CONTRACT == (
        "claw-trade-rescue.service conflicts with claw-trade-ui.service; "
        "only one service may bind 127.0.0.1:5175"
    )

    ui_bin = _read("packaging/production/bin/claw-trade-ui")
    kiosk_service = _read("packaging/production/systemd/claw-trade-kiosk.service")
    rescue_service = _read("packaging/production/systemd/claw-trade-rescue.service")
    trigger_service = _read("packaging/production/systemd/claw-trade-rescue-trigger.service")
    policy = json.loads(_read("packaging/production/kiosk/chromium-policy.json"))

    assert f'--host "${{CLAW_TRADE_UI_HOST:-{paths.RESCUE_BIND_HOST}}}"' in ui_bin
    assert f'--port "${{CLAW_TRADE_UI_PORT:-{paths.MAIN_UI_PORT}}}"' in ui_bin
    assert 'PYTHON_BIN="${PYTHON_BIN:-${ROOT_DIR}/runtime/python/bin/python}"' in ui_bin
    assert f"User={paths.KIOSK_USER}" in kiosk_service
    assert f"Group={paths.KIOSK_GROUP}" in kiosk_service
    assert "ExecStart=/opt/claw-trade/current/bin/claw-trade-kiosk" in kiosk_service
    assert f'url="${{CLAW_TRADE_KIOSK_URL:-{paths.LOCAL_UI_URL}}}"' in _read(
        "packaging/production/bin/claw-trade-kiosk"
    )
    assert policy["URLAllowlist"] == [f"http://127.0.0.1:{paths.MAIN_UI_PORT}/*"]
    assert f"Conflicts={paths.MAIN_UI_SERVICE_NAME}" in rescue_service
    assert f"Environment=CLAW_TRADE_RESCUE_HOST={paths.RESCUE_BIND_HOST}" in rescue_service
    assert f"Environment=CLAW_TRADE_RESCUE_PORT={paths.RESCUE_UI_PORT}" in rescue_service
    assert "[Install]" not in rescue_service
    assert "WantedBy=" not in rescue_service
    assert f"ExecStart=/bin/systemctl start {paths.RESCUE_SERVICE_NAME}" in trigger_service


def test_rescue_entrypoint_runs_minimal_rescue_service() -> None:
    rescue_bin = ROOT / "packaging/production/bin/claw-trade-rescue"
    text = rescue_bin.read_text(encoding="utf-8")

    assert os.access(rescue_bin, os.X_OK)
    assert "claw_trade.production.rescue_app" in text
    assert "CLAW_TRADE_RESCUE_HOST:-127.0.0.1" in text
    assert "CLAW_TRADE_RESCUE_PORT:-5175" in text
    assert 'CLAW_TRADE_INSTALL_ROOT' not in text
    assert '--install-root "/opt/claw-trade"' in text


def test_production_control_runtime_writes_runtime_state_under_shared() -> None:
    control_bin = _read("packaging/production/bin/claw-trade-control")
    ui_bin = _read("packaging/production/bin/claw-trade-ui")
    runtime_script = _read("packaging/production/runtime/claw-trade-control-runtime")
    mongodb_script = _read("scripts/start-local-mongodb.sh")

    assert "/opt/claw-trade/shared/cache/factory-seeds/current-seed/normalized" in control_bin
    assert 'CLAW_TRADE_REPORT_RUN_DIR="${CLAW_TRADE_REPORT_RUN_DIR:-/opt/claw-trade/shared/runs}"' in control_bin
    assert 'CLAW_TRADE_REPORT_RUN_DIR="${CLAW_TRADE_REPORT_RUN_DIR:-/opt/claw-trade/shared/runs}"' in ui_bin
    assert (
        'CLAW_TRADE_LOCAL_MONGODB_RUNTIME_DIR="${CLAW_TRADE_LOCAL_MONGODB_RUNTIME_DIR:-/opt/claw-trade/shared/tmp/mongodb}"'
        in control_bin
    )
    assert 'CLAW_TRADE_LOCAL_MONGODB_CURRENT_DIR="${ROOT_DIR}/.runtime/mongodb/current"' in control_bin
    assert 'SHARED="${ROOT}/shared"' in runtime_script
    assert 'OPENCLAW_STATE_DIR="${OPENCLAW_STATE_DIR:-${SHARED}/data/openclaw-state}"' in runtime_script
    assert 'CLAW_TRADE_RUNTIME_ASSETS_ROOT="${CLAW_TRADE_RUNTIME_ASSETS_ROOT:-${SHARED}/runtime-assets}"' in runtime_script
    assert 'LOG_DIR="${SHARED}/logs"' in runtime_script
    assert 'RUNTIME_DIR="${CLAW_TRADE_LOCAL_MONGODB_RUNTIME_DIR:-${ROOT_DIR}/.runtime/mongodb}"' in mongodb_script
    assert 'MONGO_CURRENT_DIR="${CLAW_TRADE_LOCAL_MONGODB_CURRENT_DIR:-${RUNTIME_DIR}/current}"' in mongodb_script
