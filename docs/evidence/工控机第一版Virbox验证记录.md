# 工控机第一版 Virbox 验证记录

## 结论

- 结论：部分通过；Linux x86_64 SDK/API 已取得，SDK 状态命令已可编译并已在目标 Ubuntu 真机验证“已激活”状态。过期、吊销、未激活、宽限等状态矩阵尚未验证。
- 验证日期：2026-06-27
- Ubuntu 版本：待在目标机执行 `lsb_release -a` 后填写
- Virbox SDK 版本：`linux-2.4.0.54102-0300000000000009-sdk`
- 探针脚本：`techlab/virbox_probe/virbox_probe.py`
- SDK 状态命令源码：`techlab/virbox_probe/virbox_status_sdk.c`
- SDK 状态命令构建脚本：`techlab/virbox_probe/build_virbox_status_sdk.sh`

## 2026-06-27 SDK/API 验证进展

- SDK 来源：Windows `C:\Program Files (x86)\senseshield\sdk\API`。
- 已确认 SDK 内含 Linux C 头文件、`lib64/libslm_control.so`、`lib64/libslm_runtime.so` 和官方 sample。
- 已用官方 `sdk/API/linux/C/samples/sample_06.c` 在本机编译通过，证明 Linux x86_64 SDK 文件可用。
- 已新增 `virbox_status_sdk`：调用官方 Virbox Control API，读取本机许可列表，按许可 ID 过滤，并输出 claw-trade 许可 JSON。
- 已生成本机探针包：
  - 路径：`.runtime/virbox-status-sdk/virbox-status-sdk-linux-x86_64.tgz`
  - SHA256：`8b59188fdd9083970ac4c3c2b560b4362c57da3e125c657650529755dd3f8984`
- 本机无 Virbox LCC 服务时，`virbox_status_sdk` 返回 `runtime_unavailable`，没有假成功。
- 用户已提供测试授权码；完整授权码不得写入仓库、证据文件、README、日志或交付包。
- 在本机构建侧尝试绑定该授权码失败，Virbox 返回“软锁指纹生成失败”。该环境是仓库 `.runtime` 下的临时 Linux runtime，不等于正式 `/opt/senseshield` 安装，也不等于目标 Ubuntu 工控机。

目标机已激活状态验证：

- `http://192.168.1.21:5175/` 返回 `200`，说明 claw-trade UI 仍在线。
- 目标机 Virbox 官方 `ssclt -l all` 能看到软锁许可：许可 ID `16427`，授权码后缀 `6ZSN`，状态 `Normal`。
- `VIRBOX_LICENSE_ID=16427 /opt/claw-trade/shared/license/virbox-status-sdk/virbox_status_sdk` 返回 `normalized_status=activated`、`features=["report","data_refresh"]`、`license_suffix=6ZSN`。
- 已把 `virbox_status_sdk` 安装到 `/opt/claw-trade/shared/license/virbox-status-sdk/`。
- 已更新目标机 `/opt/claw-trade/shared/config/runtime.env`：
  - `CLAW_TRADE_LICENSE_REQUIRED=1`
  - `CLAW_TRADE_VIRBOX_STATUS_COMMAND=/opt/claw-trade/shared/license/virbox-status-sdk/virbox_status_sdk`
  - `VIRBOX_LICENSE_ID=16427`
- 已修并覆盖目标机 `claw-trade-ui` 启动脚本，使其读取 `shared/config/runtime.env`。
- 重启 UI 后，`GET http://127.0.0.1:5175/api/ui/get-license-status` 返回：
  - `status=activated`
  - `allowsReportGeneration=true`
  - `allowsDataRefresh=true`
  - `expiresAt=2026-07-25T23:49:21Z`
  - `licenseSuffix=6ZSN`
- `http://192.168.1.21:5175/` 局域网访问返回 `200`。

## 状态矩阵

| 场景 | normalized_status | report | data_refresh | 退出码 | 证据 |
| --- | --- | --- | --- | --- | --- |
| 未激活 | 待验证 | block | block | 待验证 | 待补齐脱敏输出 |
| 已激活 | activated | allow | allow | 0 | 目标机 `192.168.1.21` 已验证，许可 ID `16427`，后缀 `6ZSN` |
| 过期 | 待验证 | block | block | 待验证 | 待补齐脱敏输出 |
| 宽限期内 | 待验证 | allow | allow | 待验证 | 待补齐脱敏输出 |
| 超过宽限 | 待验证 | block | block | 待验证 | 待补齐脱敏输出 |
| 吊销 | 待验证 | block | block | 待验证 | 待补齐脱敏输出 |
| runtime 不可用 | runtime_unavailable | block | block | 非 0 | 本机未启动 Virbox LCC 时，SDK 状态命令返回 `slm_ctrl_client_open failed: 0x02000003` |
| 网络不可用 | 待验证 | block 或宽限策略 | block 或宽限策略 | 待验证 | 待补齐脱敏输出 |

## 停止条件

- 如果无法稳定读取状态，停止后续实施。
- 如果不能识别吊销或过期，停止后续实施。
- 如果 Python/FastAPI 服务进程不能调用，停止后续实施。
- 未验证过期、吊销、未激活、宽限等状态前，不能把完整状态矩阵标记为通过。
