# 工控机第一版 Virbox 保护包状态记录

- 记录日期：2026-06-27
- 当前结论：未产出 Virbox 保护包

## 已确认

- 当前已构建的 `.runtime/production-build/claw-trade-test-task10.tar` 是普通生产包，不是 Virbox Protector 加壳/保护后的交付包。
- 本机未发现 `virbox`、`virboxprotector`、`virbox-protector`、`dsprotector` 等可执行命令。
- 目标 Ubuntu `192.168.1.21` 也未发现 Virbox Protector/SDK/runtime 安装路径或可执行命令。

## 正确原子流程

1. 下载并记录 Virbox Protector Linux 版、Virbox Linux runtime、Virbox SDK/示例程序。
2. 根据 Virbox 官方文档确认 Python 保护输入格式。
3. 先生成可直接运行的“待保护程序”。
4. 验证待保护程序未加壳前可以启动。
5. 使用 Virbox Protector 加壳，输出独立保护后文件。
6. 验证保护后文件无授权被拦截、有授权可启动。
7. 用保护后文件重新打生产交付包。
8. 审计交付包不含源码、`.git`、测试、文档、未保护原始程序、账号/token/私钥。
9. 在 Ubuntu 工控机上验收授权阻断和真实报告路径。

## 禁止项

- 不得把普通 tar 包称为 Virbox 保护包。
- 不得在没有真实 Protector 的情况下写假加密命令。
- 不得用固定 JSON 或 mock 授权状态冒充 Virbox runtime。
