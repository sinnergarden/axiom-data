# 双证券三个月完整源调试

此 scope 仅固定平安银行 `000001.SZ`、浦发银行 `600000.SH` 的 2026-06-01 至 2026-08-31 市场窗口，用于验证 `plan → run → verify → audit → rebuild` 通道。默认 `plan` 还包括收入、资产负债、现金流、财务指标、分红、前十股东报告、涨跌停价及Tushare stock_basic生命周期字段。财务请求有五个季度的前置期；稳定标识中的上市日期与来源日期解释均使用显式供应商绑定。这里的手工双证券scope不包含历史成员准备，不能当成CSI1800历史股票池。包含成员准备的现行生产入口见[CLI](../docs/cli.md)；外部官方表不参与默认采集或发布。

```sh
cd /Users/liuming/Documents/axiom/axiom-data
. .venv/bin/activate
mkdir -p delivery
axiom-data --data-root /Users/liuming/Documents/axiom/data/full_scope_pipeline_debug plan \
  --scope examples/full_scope_debug_202606_202608.scope.json \
  --output delivery/full_scope_debug_202606_202608.plan.json \
  --operation-id full-scope-debug-202606-202608 \
  --calendar-end 2026-10-31
axiom-data --data-root /Users/liuming/Documents/axiom/data/full_scope_pipeline_debug run \
  --plan delivery/full_scope_debug_202606_202608.plan.json \
  --token-file /Users/liuming/Documents/axiom/tushare_token.txt
axiom-data --data-root /Users/liuming/Documents/axiom/data/full_scope_pipeline_debug status \
  --plan delivery/full_scope_debug_202606_202608.plan.json
axiom-data --data-root /Users/liuming/Documents/axiom/data/full_scope_pipeline_debug verify \
  --plan delivery/full_scope_debug_202606_202608.plan.json
axiom-data --data-root /Users/liuming/Documents/axiom/data/full_scope_pipeline_debug audit \
  --plan delivery/full_scope_debug_202606_202608.plan.json \
  --output delivery/full_scope_debug_202606_202608.audit.json
axiom-data --data-root /Users/liuming/Documents/axiom/data/full_scope_pipeline_debug rebuild \
  --snapshot current --operation-id full-scope-debug-offline-rebuild --no-promote
```

中断后重用同一计划和 `run` 命令恢复；不要重新生成计划或改 operation ID。审计状态只是来源相对的本地验证，不是供应商每证券每报告的独立完整性证明。凭据只在外置文件中；请勿将 token 写入 scope、plan 或日志。
