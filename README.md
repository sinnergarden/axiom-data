# axiom-data

Axiom Data 将供应商响应整理成不可变的数据版本，提供行情、成分与行业、财务、股东、
融资融券、资金流和业绩预告，以及绑定数据版本的查询和导出。

第一次了解这个仓库，先看 **[从一条行情看懂 Data](notebooks/data_acceptance.ipynb)**。
它用中芯国际四天的历史样本，按“原始响应 → 整理单位 → 固定版本 → 当时可知 → 用途视图”
讲解，保存了小样本运行输出，可以先读而不运行。演示通过不等于全量生产数据通过验收。

需要 Python 查询示例或查找实现时，再看 [架构与公共接口](docs/overview.md)。

## 只记住这条流程

供应商响应原样保存，按各类数据的规则整理，再固定成一个 Snapshot。
研究与回测通过 Reader 或已生成的 View 读取这个版本。后来的修订生成新版本，旧研究的输入保留。
Data 提供事实、来源、时间和缺失解释；它不训练模型、不决定买卖。

## 文档入口

- [首次构建](docs/operations/bootstrap.md) / [每日增量](docs/operations/daily.md)
- [生成 Views](docs/operations/materialize-views.md) / [数据修复](docs/operations/repair.md)
- [离线恢复](docs/operations/recovery.md) / [独立验收](docs/operations/v1-independent-review.md)
- [完整范围准入与终端证据](docs/operations/full-admission.md)
- [目录布局](docs/operations/physical-layout.md)
- [股东人数缺少报告期时的处理](docs/operations/holder-source-admission.md)
- [脚本用途](scripts/README.md) / [历史决策与运行证据](docs/history/README.md)

日常先用 notebook 和操作文档；`reports/`、历史脚本无需按目录顺序阅读，也不要当作生产入口。
其中一些记录仍是回归测试和恢复的依据，清理时应保留引用关系。

代码位于 `src/axiom_data/`，测试位于 `tests/`。本机正式数据根为 `/var/lib/axiom-data`；
旧 workspace `data/` 仅作历史证据留存。仓库里的 `reports/` 是各次验证记录，不是完整生产数据。
供应商凭据通过运行环境或既有安全配置读取，不写入仓库。

公共 View 计划使用 `financial_fact`、`event_fact` 等职责名称；读取财务
FactView 可使用 `financial_fact_view_id` 与 `read("financial")`。历史
artifact 仍保留原有版本标识，调用方不需要把开发阶段编号当作当前业务概念。

## 运行测试

在仓库根目录执行：

```sh
PYTHONPATH=src python3 -m unittest discover -s tests -v
```

支持的代码能力与某个数据版本是否通过验收是两件事。使用数据时指定具体 Snapshot ID，
并检查该版本的范围、质量和验收结果。

Historical adjusted-price plans use `plan_historical_views(..., data_root=...,
 snapshot_id=...)` with the fixed Snapshot's security and calendar rows. The v2
plan retains the original target and eligible interval and records requested and
actual anchors plus factor provenance under `price_anchor_resolution`. Save the
complete returned plan with the operation evidence; pass its concrete `views` to
`materialize_views`. Geometry-only calls report `price_anchor_validation=NOT_READY`.
The Gate A v4 policy selects the last observed valid factor within the applicable
interval for nonstrict history. For example, the synthetic regression uses the
old fixed Snapshot input for 600069.SH: identity end August 28 and no August 27
factor. August 26 can be the conversion anchor; August 27
remains in scope and its source gap still needs admission. Strict requests retain
explicit anchors. Materialization preflights all price anchors before publishing
any View; other families and source gaps require their own admission.

Fixed adjusted FactView reads reject requested symbols, dates or fields outside
the materialized scope. Default and subset reads keep the existing values, missing
states and PIT semantics. Scope validation alone does not prove source coverage.
Published View schemas and old Gate A policy files remain unchanged; old readiness
reports must be recomputed when implementation/policy changes, as before.

The 600069 example does not settle the conflict between the historical SSE list
date and the contemporaneous SSE delisting announcement. That source conflict
continues to block real-data admission until separately resolved.
