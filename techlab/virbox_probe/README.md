# Virbox Linux x86 验证记录

本目录只放验证脚本和脱敏记录，不提交 Virbox 私有 SDK、runtime、账号、完整授权码或授权缓存。

## 当前状态

- SDK 来源：Windows Virbox SDK 工具盒，`C:\Program Files (x86)\senseshield\sdk\API`
- SDK 版本：`linux-2.4.0.54102-0300000000000009-sdk`
- Ubuntu 版本：待目标机验证
- CPU 架构：x86_64 目标
- Python 版本：Python 3.12 目标
- 官方样例路径：`sdk/API/linux/C/samples`
- 本机 runtime 安装路径：待填写
- 官方状态命令：`virbox_status_sdk`，由 `build_virbox_status_sdk.sh` 从官方 Linux C SDK 编译

## 使用方式

`virbox_status_sdk` 负责调用官方 Virbox Control API 读取本机许可状态，并输出 claw-trade 许可 JSON。
它不绑定授权码，不内置授权成功结果，不保存账号、密码、完整授权码或 API 密码。

先编译：

```bash
techlab/virbox_probe/build_virbox_status_sdk.sh \
  "/mnt/c/Program Files (x86)/senseshield/sdk/API"
```

复制到目标机后运行：

```bash
VIRBOX_LICENSE_ID=16427 ./virbox_status_sdk
```

输出示例：

```json
{
  "raw_status": "0",
  "normalized_status": "activated",
  "features": ["report", "data_refresh"],
  "expires_at": "2026-07-27T00:00:00Z",
  "grace_until": null,
  "device_id_hash": null,
  "license_suffix": "6ZSN",
  "error_code": null,
  "error_message": null
}
```

`virbox_probe.py` 只负责包装外部状态命令，并校验输出字段。生产环境可以直接把
`CLAW_TRADE_VIRBOX_STATUS_COMMAND` 指向 `virbox_status_sdk`；需要统一包装时再用
`virbox_probe.py`。

官方状态命令必须输出 JSON，至少包含：

```json
{
  "raw_status": "activated",
  "normalized_status": "activated",
  "features": ["report", "data_refresh"]
}
```

运行：

```bash
VIRBOX_STATUS_COMMAND="/path/to/official/status-command" \
python3 techlab/virbox_probe/virbox_probe.py
```

可选超时：

```bash
VIRBOX_STATUS_TIMEOUT_SECONDS=10 \
VIRBOX_STATUS_COMMAND="/path/to/official/status-command" \
python3 techlab/virbox_probe/virbox_probe.py
```

如果 Virbox 官方样例输出格式不同，先用官方 SDK 文档补充适配逻辑，再跑状态矩阵。不得用固定 JSON 冒充官方状态。
