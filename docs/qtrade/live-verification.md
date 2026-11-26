# QMT 与 AI 执行验证记录

日期：2026-11-26。代码契约测试、浏览器联调和券商环境验收分开记录。

| 检查 | 实际结果 |
| --- | --- |
| 新增执行器、风控、存储与官方 SDK 适配契约 | 57 通过；SDK 为测试替身，没有真实资金账户 |
| Qtrade、执行扩展、原扩展加载、公开 OpenAPI 回归 | 125 通过 |
| 扩大到模拟账户、策略注册、回测正确性与矩阵策略 | 263 通过、2 个既有 Windows 用例失败，见下文 |
| 前端全量 | 130 文件、983 测试通过；限定最多 4 workers |
| TypeScript + Vite 生产构建 | 通过；原 ECharts 大块警告保留 |
| 新增 Python 代码 Ruff | 通过 |
| Edge 真实后端联调 | 1440×1000、390×844；无 pageerror；移动端页面与滚动宽度均 390 |
| 页面状态与防误操作 | 缺少 SDK/本机配置有明确提示；保存、启动监控、紧急停止正常；无账户时自动执行按钮禁用 |
| 配置同步 | 高级 JSON 和服务端刷新同步白名单显示；组件测试校验显示值与提交值一致 |

核心故障覆盖：提交前持久化、重复事件、进程重启、提交响应丢失、未知状态禁止重发、SDK 明确拒绝、部分成交、撤单待确认、成交回报冲突、未来数据库版本、账号及工作区排他、并发扫描、暂停与配置变更、AI 无效输出及每日预算、独立于 AI 的保护性退出、本机同源控制面。

浏览器使用隔离工作目录和示例额度，AI 关闭，没有调用收费模型，没有向券商提交订单。示例额度不代表用户的实盘资金配置。

界面证据：原[研究工作台](screenshots/workbench.png)；新增执行页[桌面深色](screenshots/live-desktop.png)、[桌面浅色](screenshots/live-light.png)、[窄屏](screenshots/live-mobile.png)。

复现检查（后端命令在 `backend/` 执行，设置 `PYTHONUTF8=1`）：

```text
uv run pytest tests/test_qtrade_live.py tests/test_qtrade_live_contracts.py -q
uv run pytest tests/test_qtrade.py tests/test_qtrade_live.py tests/test_qtrade_live_contracts.py tests/test_paper_execution_extension.py tests/test_extensions.py tests/test_openapi_contract.py -q
uv run ruff check app/custom/qtrade/live tests/test_qtrade_live.py tests/test_qtrade_live_contracts.py
```

前端在 `frontend/` 执行 `pnpm exec vitest run --maxWorkers=4 --minWorkers=1` 与 `pnpm build`；本机为绕过全局 pnpm 版本托管和安装自检，额外使用了 `--config.managePackageManagerVersions=false --config.verifyDepsBeforeRun=false`。CI 使用锁文件指定的 pnpm 9。

GitHub Windows 作业已经实际验证新增 57 项执行契约以及原 180 项桌面/锁回归（3 项跳过）；最终提交的完整结果以 PR 检查为准。

## Windows 全量与扩大回归的限制

扩大回归中的 `test_extension_packages.py` 有两个旧用例失败：Windows ZIP 路径规范化使 `a\\evil.py` 的拒绝断言不成立；当前系统缺少创建符号链接的权限（WinError 1314）。相关实现和测试相对 main 没有改动。

还尝试运行全部 5132 个后端用例。出现旧回测证据测试的符号链接权限失败、并发 manifest 写入失败等，并在旧引擎集成测试的包树摘要扫描中持续耗时，之后终止了该次运行；不能报告本机全量通过。上述回测证据失败另行单独复现，相关文件没有修改。PR 的完整 Linux 后端及多系统独立引擎结果以 GitHub Actions 为准。

## 未完成的外部验收

用户尚未开通量化券商通道，本机没有可验收的 MiniQMT/XtQuant 账户。真实 SDK 兼容性、交易权限、行情源、空查询语义、备注透传、报撤单与成交回报、长时间运行、断网及券商仿真验收均不能由测试替身证明。

当前默认影子模式、零额度、无授权。只有部署者按[操作手册](live-operations.md)配置官方环境、完成券商仿真验收并启用开关后，才可限时授权自动报单。本次没有训练机构 Alpha，没有收益或零故障承诺。
