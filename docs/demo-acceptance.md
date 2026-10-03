# D01–D18 验收索引

编号来自 [Data 设计](design/02_axiom_data.md)。独立合成反例验证难以自然遇到的语义；真实来源窗口验证映射、实际接口和操作路径。两者共同验收代码，不把合成测试冒称供应商历史证明。

| ID | 当前测试入口 | 真实证据与范围 |
|---|---|---|
| D01 旧 Snapshot 不变 | test_local_api、test_completion_events | 双教程固定旧 Snapshot；新候选读取不改旧文件 |
| D02 同 Raw 重建 | test_completion_replay、test_bulk_event_processing | 一年市场全域字节相同；完整来源教程离线重建 |
| D03 重复观察与改值 | test_local_updates、test_completion_temporal、test_bulk_event_processing | 不变刷新复用 Canonical/Snapshot；A→B→A 由独立反例保证 |
| D04 单位/证券/日期 | test_provider_contracts、test_local_sources、test_bulk_event_processing、test_etf_sources | 实际市场与全部七个股票事件端点独立Raw核对；CNY单位抽样；ETF基金份额/千元映射与专用身份 |
| D05 合同/映射修复 | test_local_updates | 显式 domain_overrides 从旧 Raw 重建新候选，不覆写旧版 |
| D06 PIT、晚到与开盘边界 | test_local_reader、test_public_evidence、test_completion_temporal、test_dividend_availability | 实际历史strict查询不可见；精确证据绑定与未认证修订反例；股票/ETF实施公告较晚时不提前可见 |
| D07 财务、多期、撤销、股东 | test_completion_events、test_full_sources、test_bulk_event_processing | 实际四类财务、分红、股东、涨跌停；单季/TTM 与完整报告组教程 |
| D08 入池/退出/再入/池外持仓 | test_completion_states、test_local_reader、test_vendor_membership、test_reference_ready | dated供应商快照延续、不倒填、空响应、退出再入、strict receipt与原始快照日期 |
| D09 单域修复/补证据 | test_local_updates、test_public_evidence | 原域 provenance 与不相关文件复用；补证据不重写 Raw receipt |
| D10 失败不切 current | test_batch_jobs_v2、test_full_sources、test_local_updates、test_etf_jobs | 完整窗口复用成功Raw续跑；股票及ETF必需阶段成功才发布；完成后重跑不查询供应商 |
| D11 换目录离线恢复 | test_completion_portable、test_local_api、test_etf_jobs | 最终安装包独立运行；搬移后股票14域与ETF8域Raw重建字节相同；随包源码读取复验 |
| D12 损坏 | test_completion_portable、test_local_review | 指定依赖损坏失败；不重算摘要洗白，不牵连无关域 |
| D13 覆盖不足 | test_completion_replay、test_delivery_processing | 一年 known suspension 单列；完整两股窗口未解释缺价 0 |
| D14 Qlib | test_qlib_export；实际Qlib消费与双教程 | 股票/ETF共8352个数字单元按固定Query等价；NaN、源单位、成员区间、重复导出及独立安装后搬移读取通过 |
| D15 交易状态 | test_completion_states、test_vendor_listing、test_vendor_listing_audit | 供应商list/delist日期的半开边界、实际receipt与日期/身份映射审计；外部核对只warning |
| D16 UI 旧查询 | test_completion_replay、test_completion_consumers | 实际 ViewRef/P12 图层复读；完整 BacktestRun/UI 产品属消费者 |
| D17 真实消费者 | test_completion_consumers 与两套教程 | 实际 Data → Research adapter → Core、Runtime gate、UI P12，保留单位、键、null、cutoff 与 refs |
| D18 批次固定 | test_completion_replay | session/batch 固定 Snapshot，current 后续变化不改已开始批次 |

运行当前测试：

```sh
PYTHONPATH=src:tests:../axiom-engine/src:../axiom-research/src:../axiom-ui/src \
  .venv/bin/python -m unittest discover -s tests -v
```

真实来源与教程报告：完整窗口（本地记录：`delivery/full_scope_debug_202606_202608.live.acceptance.json`）、一年市场（本地记录：`docs/delivery-validation.json`）、[供应商边界](reference-readiness.md)、Qlib（本地记录：`delivery/qlib_actual_20261003.acceptance.json`）、Notebook执行（本地记录：`docs/real-tutorial-validation.json`）。最终清单见[completion-checklist.md](completion-checklist.md)，此前167项测试及安装/搬移汇总见最终代码验收（本地记录：`delivery/final-code-acceptance-20261003.json`）。不引用旧实现的失败数，也不把此前市场子集交付算作全部完成。


新增0.3.1完整来源采集、恢复、包安装与教学重写的最新证据见[当前preflight](bulk-preflight.md)，旧汇总保持原日期与包版本。设计仅在[唯一入口](design/README.md)修改。
