# Virbox Linux x86 验证记录

本目录只放验证脚本和脱敏记录，不提交 Virbox 私有 SDK、runtime、账号、完整授权码或授权缓存。

## 当前状态

- SDK 来源：未取得
- SDK 版本：未取得
- Ubuntu 版本：待目标机验证
- CPU 架构：x86_64 目标
- Python 版本：Python 3.12 目标
- 官方样例路径：待填写，必须在 `/home/frank/secure-vendor/virbox/linux-x86_64` 或目标机安全目录下
- 本机 runtime 安装路径：待填写
- 官方状态命令：待填写

## 使用方式

`virbox_probe.py` 只负责调用外部官方状态命令，并校验输出字段。它不内置授权成功结果。

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
