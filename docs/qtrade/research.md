# REA 实测与量化开源选型

调查日期：2026-11-25。以下区分实测、源码确认和后续候选，不以 Star 数量证明质量。

## REA 已实际运行

工具：[morluto/rea](https://github.com/morluto/rea)，版本 6.10.1，源码提交 `ff2f1c2f72a5cbe696086d64a28d450dcd7dc4b5`。先阅读其运行时检查与 Python 打包应用、JavaScript 应用分析文档；当前系统 Node 22.18 被工具明确拒绝，随后通过 npm 的临时 Node 24 运行，未修改系统 Node。

对用户指明的本机 TSP 安装程序做只读静态库存检查，未启动或注入原程序。SHA-256：`eee05387d31085b8287d669c86751c7616f6c00e856384903d223feb5d9d6461`。REA 确认为 PyInstaller CArchive 2.1，Python 3.12，13 个外层记录，无加密标记；整个归档图 3,670 节点、3,677 occurrences、3,669 verified member hashes，无 integrity contradiction。安装目录的公开升级清单标示版本 0.3.26。库存完整不等于 Python 字节码已经反编译，也不能证明运行时行为。

随后对安装版的静态前端目录实际运行 `analyze-javascript-application --artifact-format directory`。分析 197 个相关文件，读取 4,223,895 文本字节，解析 196 个 JavaScript 文件、1,016,053 AST 节点，产生 11,415 findings，无 parse failure。Evidence ID 为 `ev_272d8e0f864415a200710ee2dcd8e1b7b75f364a5ceffcb9ef0be4fcf9458e59`。识别到 `/backtest`、`/screener`、`/regime` 等路由候选，说明本地版是浏览器前端资源加 Python 宿主，不能按 Electron ASAR 假设处理。

重要限制：主 bundle 的 member-mutation 分析触及 524,288 alias-state 预算；alias selection 达到 512 预算；HTML 的两个内联脚本未纳入分析。工具明确报告 graph coverage incomplete；动态路径和混淆关系仍不确定。无 parse failure 不意味着语义恢复完整，也不能由静态路由候选证明接口可调用。

原始证据仅保存在本地工作目录；大体积图、安装包、用户账户、数据源 Key、行情库、日志均未发布。仓库提供 [复现脚本](../../scripts/inspect-tsp-rea.ps1)，传入你自己的程序或静态目录；输出目录不要指向源码目录或用户数据目录。

## 源码调用链核对

下载上游 0.4.1 后，逐项核对源码与测试：扩展加载器、前端路由注册、标准策略上下文、唯一策略源码保存入口、矩阵策略种子、模拟账户 API、模拟撮合和 paper_execution 快照/生命周期。由此选择现有扩展契约新增 Qtrade 页面，而不是复制旧安装版的压缩代码。

模拟盘自动执行所需的关键机制已在上游实现：真实数据日期确认、策略版本指纹、账户级暂停、订单幂等、T+1 及费用。Qtrade 复用这些能力，不宣称本次重新实现了交易引擎。实盘接口和券商凭据机制尚未验证，不以 REA 绕过券商授权。

## 可用的量化开源方向

| 项目与一手来源 | 可为 Qtrade 提供什么 | 当前状态与接入条件 |
| --- | --- | --- |
| [TSP](https://github.com/shy3130/tick-stock-panel) / 自带 Tivelo | 已有行情抽象、策略、因子、回测、模拟盘与桌面包装 | 已作为完整底座；保留 MIT 和各包许可 |
| [AKShare](https://github.com/akfamily/akshare) | Python 财经数据接口，适合作为新增数据 Provider 的候选 | 本次只调研，未安装；先核对字段单位、可用性、来源授权与限流，不能把网页接口当作稳定 SLA |
| [Microsoft Qlib](https://github.com/microsoft/qlib) | 因子/模型研究与实验工作流 | 本次只调研，未集成；后续独立研究 worker 导出信号，需处理时点、样本外、幸存者偏差和版本 |
| [VeighNa / vn.py](https://github.com/vnpy/vnpy) | 事件驱动交易与券商 gateway 设计，可进一步调查 XTP 等接入 | 本次只调研，未集成；券商 SDK、权限和订单回报必须单独验收 |
| [ta](https://github.com/bukosabino/ta) | 技术指标实现和数值对照候选 | 未安装；底座已含指标，不重复引入第二套运行管道，可用于固定样本交叉验证 |
| [XtQuant 官方交易文档](https://dict.thinktrader.net/nativeApi/xttrader.html) | QMT 连接、订阅账户、报撤单与订单回报契约 | 后续通道候选；本次未安装 SDK、未连接账号、未报单 |

这轮没有把上述候选全部塞入依赖。每个新库必须解决实际缺口，并先核对对应版本的许可证与数据条款；本次不宣称它们已经成为平台能力。
