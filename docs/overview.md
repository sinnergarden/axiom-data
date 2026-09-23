# Axiom Data 怎么工作

Axiom Data 把供应商数据整理成可以重复读取、追溯来源的数据版本，供研究和其他应用使用。
它提供数据，不计算研究 Feature、训练模型或执行交易。

## 从原始数据到读取

```text
供应商响应 → RawBatch → DomainCommit → Snapshot → Reader / Derived / View
              原样保存     按数据类别整理    固定一组版本      查询、计算或导出
```

| 名称 | 作用 | 例子 |
| --- | --- | --- |
| RawBatch | 保存一次请求、原始响应和实际抓取时间 | 某证券的财报响应 |
| DomainCommit | 某一类数据的版本，包含格式、单位和来源校验 | 日行情版本、财报版本 |
| Snapshot | 固定各类数据的版本组合 | 一次研究使用的完整数据版本 |
| Derived / View | 绑定 Snapshot 的计算结果或读取格式 | 复权价格、FactView、Qlib 导出 |

已发布版本不原地修改。供应商修订数据时，发布新版本，旧 Snapshot 仍指向旧数据。
例如财报收入从 100 修订到 110，新 Snapshot 可以包含 110；旧 Snapshot 不会跟着改变。

## 对外有哪些数据

| 类别 | 内容 |
| --- | --- |
| 基础资料 | 证券信息、所属交易所、上市退市边界、交易日历 |
| 市场 | 日行情、成交量额、涨跌停、停牌状态、估值、基准行情 |
| 复权 | 复权因子、公司行动，以及由它们生成的复权价格 |
| 成分与行业 | 股票池成员及变更、SW2021 行业分类 |
| 财务 | 财报及修订、单季度值、最近四季度合计（TTM） |
| 股东 | 股东人数、前十大股东及报告完整性信息 |
| 其他 | 融资融券、资金流、业绩预告 |

数据可以通过 `SnapshotReader` 直接读取，也可以生成以下格式：

- **FactView**：按证券和日期组织事实值，并保留缺失原因、时间规则和来源信息。
- **QlibView**：供 Qlib 使用的导出格式；它来自 Snapshot，不是另一套数据源。
- **MarketReplayView**：面向市场回放的数据视图，不负责回测撮合。

“支持某类数据”不表示任何证券、日期都有值。缺失、来源限制和质量状态需要一同读取。

## 历史查询：当时能知道什么

一条财报有报告期、公告时间，也有系统实际观察到它的时间。这些时间不能混用。
例如 2026 年才抓到的 2014 年财报，不能据此证明系统在 2014 年已经见过它。

历史查询同时指定 cutoff（知道信息的截止时刻）和 PIT policy（时间选择规则）：

- `operational_pit_v1`：考虑系统实际观察时间。
- `best_effort_vendor_v1`：按供应商可用时间作尽力还原，保留证据限制。

这些查询不把证据不足的数据升级为 verified（历史可用性已验证）。

后续才观察到的修订不会进入较早 operational cutoff 的结果。
明确的空成员集合也不同于数据没抓到；行业缺失不自动用上一天或另一行业体系补齐。

## 读取示例

在安装好 `axiom_data` 的环境中运行。`snapshot_id` 必须来自已发布的具体 Snapshot；
下面函数的参数不是 `current` 或 `latest`，所选版本也必须包含例子中的证券和数据范围。

```python
from axiom_data import SnapshotReader

def read_example(data_root: str, snapshot_id: str):
    reader = SnapshotReader(data_root, snapshot_id)

    prices = reader.market_daily(
        symbols=["000001.SZ"],
        start_session="2025-01-02",
        end_session="2025-01-10",
    )
    financial = reader.as_of(
        "financial_events",
        symbols=["000001.SZ"],
        knowledge_cutoff="2025-06-30T15:00:00+08:00",
        pit_policy="best_effort_vendor_v1",
    )
    return prices, financial
```

`market_daily` 读取该 Snapshot 的日行情；`as_of` 另外按 cutoff 和 policy 选择历史可见版本。
返回值是由行字典组成的 tuple。Reader 会验证 Snapshot 及其依赖，损坏或缺少必要数据会报错。

## 构建和增量

公共流程是：计划请求 → 采集并保存 Raw → 校验并构建各类数据 → 生成 candidate Snapshot →
生成所需 Views → 验收。candidate 只是待验收版本，不等于可以直接作为默认数据使用。

`bootstrap` 构建初始版本，`daily` 在明确的父 Snapshot 上处理增量，`repair` 用固定 Raw 修复数据。
失败后通过原 run 和 checkpoint 继续，已通过验证的 Raw 不必重新抓取。
新版本复用未变化的数据分区；没有新事实或覆盖变化时，不应制造新 Snapshot。

## 在哪里找实现

| 位置 | 职责 |
| --- | --- |
| `src/axiom_data/*source*.py`、`tushare.py` | 供应商请求、字段转换及来源规则 |
| `contracts/`、`domains/`（均在包内） | 数据格式、单位和类别校验 |
| `artifacts.py`、`publication.py` | 不可变文件、版本 ID 和发布 |
| `operations.py`、`cli.py` | bootstrap、daily、repair 等公共入口 |
| `consumption.py`、`pit.py`、`*views*.py` | 查询、历史版本选择、计算与导出 |

本机正式数据根为 `/var/lib/axiom-data`，代码和数据分别存放。`catalog.sqlite` 是可重建索引，
恢复仍需要原始文件、版本说明及依赖；只有代码和一个空目录，无法恢复原来的观察历史。

需要执行命令时，再看 [bootstrap](operations/bootstrap.md)、[daily](operations/daily.md)、
[Views](operations/materialize-views.md)、[repair](operations/repair.md)、[离线恢复](operations/recovery.md)。
设计依据见 workspace 的 `design/01_axiom_overview.md` 和 `design/02_axiom_data.md`。


## 代码从哪里读起

| 职责 | 模块 |
| --- | --- |
| 供应商采集与转换 | `tushare`、`reference_source`、`fundamentals_source`、`event_source` |
| Canonical 行合同 | `domains/market`、`domains/reference`、`domains/fundamentals`、`domains/events` |
| 时间选择、修订与财务计算 | `pit` |
| Snapshot 查询与 Qlib 读取 | `consumption` |
| 查询范围与财务完整性 | `scope_coverage`、`financial_coverage` |
| View 生成与读取 | `views`、`financial_views`、`event_views` |
| 执行计划与恢复 | `operations`、`bootstrap_sources`、`view_operation` |
| 发布与存储 | `artifacts`、`publication`、`partitions` |
| 来源对账与消费准入 | `reference_reconciliation`、`financial_reconciliation`、`event_reconciliation`、`consumer_admission` |

例如股东事实查询由 `consumption` 读取 Canonical，通过 `pit.select_event_revisions`
选出可见修订；`event_views` 使用同一查询规则生成 View。Reader 不通过供应商采集代码选择修订。
财务字段定义在 `domains/fundamentals`，范围校验与 View 共用它。

旧 `pr6_*`、`pr7_*`、`dm*` 模块只保留导入兼容入口；`*_v1` 保留已发布版本的读取规则。
已保存的 schema、source profile、View kind 和 ID 前缀沿用原协议名称。
代码整理不会改写或要求重建已有数据。新执行如涉及实现摘要，使用实际新代码的摘要；
已冻结旧实现的 operation 仍遵守原有输入匹配检查。
