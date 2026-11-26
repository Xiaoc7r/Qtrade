# Qtrade

<!-- impeccable:product-schema 1 -->

## Platform
web

## Users
个人 A 股量化研究者。用户已确认沿用 TSP，并进一步要求可自由配置的 AI 自动监控与风控后自动交易；当前尚未开通券商量化接口。

## Product Purpose
以 TSP 为底座，将市场环境、多维筛选、策略研究、回测与模拟执行串成易用的个人工作流。

## Capabilities and Constraints
保留 React / FastAPI / Tivelo 和桌面包装。所有账户、行情与配置使用独立工作区，不自动迁移桌面 TSP 私有数据。QMT 官方适配与自动执行框架已实现，但券商权限和 SDK 环境尚未提供，实际券商验收未完成。默认影子模式；真实报单必须满足部署与风控条件。数据缺失必须显示，不伪造实时市场或收益。

## Brand Commitments
名称 Qtrade；新增工作台继承 TSP 已有主题、组件和导航。

## Product Principles
数据可追溯；分析和成交分离；复用单一账本；先回测再模拟；操作必须可见、可停、可核对。
