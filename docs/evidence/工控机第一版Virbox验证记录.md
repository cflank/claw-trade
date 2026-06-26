# 工控机第一版 Virbox 验证记录

## 结论

- 结论：未通过，等待 Linux x86_64 SDK/runtime 和测试授权码
- 验证日期：2026-06-26
- Ubuntu 版本：待在目标机执行 `lsb_release -a` 后填写
- Virbox SDK 版本：未取得
- 探针脚本：`techlab/virbox_probe/virbox_probe.py`

## 状态矩阵

| 场景 | normalized_status | report | data_refresh | 退出码 | 证据 |
| --- | --- | --- | --- | --- | --- |
| 未激活 | 待验证 | block | block | 待验证 | 待补齐脱敏输出 |
| 已激活 | 待验证 | allow | allow | 待验证 | 待补齐脱敏输出 |
| 过期 | 待验证 | block | block | 待验证 | 待补齐脱敏输出 |
| 宽限期内 | 待验证 | allow | allow | 待验证 | 待补齐脱敏输出 |
| 超过宽限 | 待验证 | block | block | 待验证 | 待补齐脱敏输出 |
| 吊销 | 待验证 | block | block | 待验证 | 待补齐脱敏输出 |
| runtime 不可用 | runtime_unavailable | block | block | 非 0 | 当前本机未发现 SDK/runtime 安全目录 |
| 网络不可用 | 待验证 | block 或宽限策略 | block 或宽限策略 | 待验证 | 待补齐脱敏输出 |

## 停止条件

- 如果无法稳定读取状态，停止后续实施。
- 如果不能识别吊销或过期，停止后续实施。
- 如果 Python/FastAPI 服务进程不能调用，停止后续实施。
- 当前未取得 SDK/runtime，不能进入授权状态模型和后端阻断接入。
