# axiom-data

Axiom Data 将供应商响应整理成不可变的数据版本，提供行情、成分与行业、财务、股东、
融资融券、资金流和业绩预告，以及绑定数据版本的查询和导出。

先读 **[架构、数据与使用示例](docs/overview.md)**：一页了解数据从哪里来、怎样形成版本、
历史查询如何选择数据，以及如何用 Python 读取。

## 文档入口

- [首次构建](docs/operations/bootstrap.md) / [每日增量](docs/operations/daily.md)
- [生成 Views](docs/operations/materialize-views.md) / [数据修复](docs/operations/repair.md)
- [离线恢复](docs/operations/recovery.md) / [独立验收](docs/operations/v1-independent-review.md)
- [目录布局](docs/operations/physical-layout.md)
- [股东人数缺少报告期时的处理](docs/operations/holder-source-admission.md)
- [脚本用途](scripts/README.md) / [历史决策与运行证据](docs/history/README.md)

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
