# Bulk 作业与验收范围

统一入口为 prepare → plan → run → verify → audit。生产数据只用 Tushare；完整股票计划包含市场、财务与七个事件端点，market-only 必须显式选择。批量、日更、Raw续跑及原子current规则见 [作业合同](bulk-jobs.md) 与 [CLI](cli.md)。

先以相同生产入口完成小范围全域原型，再扩大日期与证券范围。十二年未实际下载；交付要求是配置、采集、后处理、恢复和报告全部由现存代码独立执行，不需要 agent 生成补救脚本，也不调用模型 API。正常作业没有模型 token 消耗。

成员采用最近供应商快照延续，历史候选取全部 dated 快照并集及起点前预热，不截成今天1800。不要求官网公告、外部退市表或历史调样闭合。外部差异仅 warning；本地请求失败、已知截断或转换错误仍须修复。

一年固定1800样本验证吞吐；两股三个月完整来源验证实际映射和恢复；它们都不代表十二年已完成。证据入口为 一年实测（本地记录：`docs/delivery-validation.json`）、[设计对照](design-conformance.md) 与 [最终清单](completion-checklist.md)。新增 [7ETF日线轮动](etf-rotation-data.md) 采用独立基金源配置，复用固定格式和统一操作。

旧 publication/View/admission 源码、串行runner和已撤销官网接源策略归档在仓库外 `../historical/axiom-data-precleanup-20260928/`，不进当前wheel/默认测试。旧数据根保持原样，不自动迁移；需要时用其保存的源码包恢复。
