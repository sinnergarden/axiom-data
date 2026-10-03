# Data 设计与实现的关系

[Data 设计](design/02_axiom_data.md) 定义目标合同；两份已执行教程解释使用与实现；本文件记录可核对的实现证据。教程不是另一套 schema，来源相对核对也不等于供应商历史完全正确。

## 当前证据

| 设计要求 | 实现和真实验证 | 证据边界 |
|---|---|---|
| Raw → Canonical → Snapshot | 原字节对象按内容复用；fetch 日志保留每次观察；typed ZSTD Parquet；不可变分区与内嵌域清单 | 一年 CSI1800 行情、两股完整来源真实窗口；未实际运行十二年全域 |
| 统一批量入口 | prepare → plan → run → verify → audit；完整计划包含市场、四类财务、分红、股东、涨跌停及Tushare stock_basic上市/退市字段 | 不把显式 market-only 子计划当完整 Data |
| 增量与恢复 | 日更公告窗口有界；原 Raw 续跑；cap 细分；必需阶段成功才推进 current | 较旧公告修订需要显式历史 refresh，短窗口不是发现全部历史修订的保证 |
| 财务原生键与单位 | 证券、endpoint、报表类型、报告期、版本；收入/资产负债/现金流金额 CNY，指标百分数；单季、TTM 纯变换 | 真实 Tushare 四类财务映射已核对；以 Tushare 原始字段和明确源单位验收，外部对照仅作可选抽样 |
| 分红、股东、规则 | 实施状态、原生事件键、整组股东版本、来源完整性声明、每日价格限制 | 空响应不等于零；供应商报告组完整不等于外部认证 |
| PIT 与修订 | operational 实际 receipt；market-safe 精确 revision 证据，否则回退；best-effort 明示假设；显式 hybrid；A→B→A 保留 | 终态历史不能恢复未保存的公开 vintage；真实历史严格查询不可见是正确结果 |
| 生命周期 | calendar与身份、S/R日内事件、供应商list/delist事件；闭市/停牌/未上市/退市/缺数分别解释 | 接受供应商日期解释；strict使用receipt；外部差异只warning，不要求逐份公告认证 |
| 历史供应商成员 | 所有dated index_weight快照候选并集；最近可见快照延续，保留source_snapshot_date | 不月底倒填、不称精确官方daily；月内短暂成员遗漏是来源限制，不gate初始化 |
| 按需 Reader / ViewRef | 固定 Snapshot 与 QuerySpec；按月/列读取，事件保留原生键；可保存逻辑查询，不强制磁盘 View | 完整 provenance 有成本；大结果超过缓存预算时不强留缓存；研究任务可自行复用批量结果 |
| 离线重建与 portable | 同 Raw 重建；选定 Snapshot 的事实依赖闭包、可恢复源码与环境记录；搬移后离线读取 | 同 OS 换目录已验证；跨 OS 尚未实际运行；无变化观察和作业恢复检查点不自动属于 Snapshot 导出闭包 |
| Research / Core / Runtime / UI | 实际 DataBatch → Research adapter → Core；Runtime 决策/回放时钟与固定引用；UI P12 事实图层 | 是 Data 消费薄接口；完整模型、账户撮合和浏览器产品归各自 owner |
| Qlib | 不可变P04目录、实际Research QlibView读取、原始单位/NaN/PIT cutoff与成员区间，股票/ETF共8352个数字单元等价，搬移读取一致 | 显式数字日频字段导出；事件保持原生接口；训练归一化、feature/model归Research；不自动成为日更前置 |

两股完整来源窗口的固定 Snapshot 为 `s_52fd63f4d0b07a5d2658ee589402ec7c27062f944cb07fa1974df2e2a7f5dde6`。它包含 2026-06—08 月行情、五季度财务 warmup 与全部七个事件端点；核对状态 passed，未解释缺价 0。此前窗口保留作来源映射证据。当前统一原型位于 `../data/csi1800_tushare_only_v4_202606_202608`，固定 Snapshot `s_a0db6028118997d93b9f70dff852b701b030b152a30b2e8d325c08ad64931049`：14 域均完成 verify/audit、重复运行和离线重建。7ETF日线原型另有8域、7×114行情/因子/限价观察，按相同存储及Reader接口验收，不包含策略计算。

## 证据入口

- 当前统一原型核对（本地记录：`delivery/csi1800_tushare_only_v4_202606_202608.audit.json`）与恢复验证（本地记录：`delivery/csi1800_tushare_only_v4_202606_202608.portable.json`）：生产口径只含Tushare。
- 7ETF日线实测（本地记录：`delivery/etf_rotation_daily_202606_202608.report.json`）：身份、日线、因子、分红、价格限制、预热和基准。
- 一年市场实测（本地记录：`docs/delivery-validation.json`）：固定 1800 工程样本，不是历史当日池。
- [供应商成员与生命周期](reference-readiness.md)：实际快照频率、延续政策与supplier日期边界。
- 真实教程执行（本地记录：`docs/real-tutorial-validation.json`）：Researcher 与 Developer 实际运行记录。
- Qlib实际消费（本地记录：`delivery/qlib_actual_20261003.acceptance.json`）与独立安装搬移（本地记录：`delivery/qlib_installed_20261003.acceptance.json`）：PyQlib0.9.7直接读取固定导出。
- [D01–D18 语义索引](demo-acceptance.md)：晚到、失败、损坏等独立反例。
- [完整交付清单](completion-checklist.md)：未完成项不计为交付。

约定的完整代码范围已验收：此前167项测试和54个教学单元完成；当前174项测试、94个改写教学单元执行无错误；0.3.0 wheel在独立环境安装后验证两类原型与实际Qlib，搬移后14个股票域与8个ETF域均从Raw重建为相同Parquet字节。汇总见最终代码验收（本地记录：`delivery/final-code-acceptance-20261003.json`）。这些证据不声称已拉取十二年或已证明供应商全部历史能力。


新增0.3.1完整来源采集、恢复、包安装与教学重写的最新证据见[当前preflight](bulk-preflight.md)，旧汇总保持原日期与包版本。设计仅在[唯一入口](design/README.md)修改。

0.3.2独立复核修复此前自然样本未覆盖的观察边界：实际builder绑定、后续Raw字段选择/备份、分红过滤空经济日期、成员首次合建receipt链及同日修正、Qlib日历PIT修订。留存wheel安装后183项离线测试通过、无跳过，其中新增9项观察/恢复测试，实际Qlib消费仍通过。两教程本次只同步说明并从ipynb渲染，原代码与已执行输出保留。没有新供应商调用，没有十二年全量或首次全量Qlib性能结论。
