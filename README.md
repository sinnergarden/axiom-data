# axiom-data

Axiom Data 为量化研究保存可复现的数据输入：原始响应进入 Raw，按明确合同整理为 typed Parquet，一份不可变 Snapshot 固定各域版本。查询按证券、时间、字段和 PIT 政策返回 DataBatch，无需预先生成磁盘 View。

**本轮约定的完整代码范围已通过验收。** 财务、事件、PIT、供应商成员与生命周期、7ETF日线、Reader/Qlib消费者接口、教程及搬移均有执行证据，见 [当前 preflight](docs/bulk-preflight.md)、[交付说明](DELIVERY.md) 与 [完整交付验收](docs/completion-checklist.md)。十二年全量网络采集尚未运行；真实原型与完整历史数据的覆盖范围分别记录。

## 从使用场景开始

[Data 设计](docs/design/02_axiom_data.md) 是权威合同。两份教程分别讲研究用途与实现细节，使用保留的真实输入运行：

- [Quant Researcher](notebooks/researcher_tutorial.html)：数据组织、查询、PIT、缺失、派生与消费者使用。
- [Quant Developer](notebooks/developer_tutorial.html)：存储格式、来源解释、批量采集、日更、恢复、搬移及协议。

教程说明实现，不另立一套合同。复跑方法见 [Notebook 说明](notebooks/README.md)；边界反例留在测试中，不伪装成真实源验证。

## 安装与查询

Python 3.11+，在隔离环境中安装：

```sh
python -m pip install -r requirements-local.lock -e .
axiom-data --help
```

```python
from axiom_data import Data, QuerySpec

data = Data('/path/to/data')
snapshot = data.resolve('current')  # 一次实验只解析一次
query = QuerySpec(
    domain='market_daily', fields=('close', 'volume_shares'),
    symbols=('your-stable-security-id',), sessions=('2020-01-02',),
    pit_policy='best_effort_vendor_v1',
    cutoff_by_session={'2020-01-02': '2020-01-02T20:00:00+08:00'},
)
batch = data.read(snapshot=snapshot, query=query)
frame = batch.frame
payload = batch.to_json()  # records + field_meta + context
```

读取不会联网、发布数据或解析变化中的 current。`Data.members` 读取生效区间，`Data.events` 返回财务和其他事件的原生键，`Data.states` 解释交易日、上市/摘牌和停牌状态。`single_quarter`、`ttm`、`adjust_prices` 是固定输入之上的纯函数。策略 Feature、模型与账户属于消费者。

## 采集与更新

命令顺序为 `prepare → plan → run → verify → audit`；读取、检查、重建、导出和导入也使用同一个 `axiom-data` 入口。`plan` 默认包括财务与事件，只有显式 `--market-only` 才限制为行情。参阅 [CLI](docs/cli.md)、[批量作业](docs/bulk-jobs.md)、[性能与存储](docs/performance-and-storage.md) 和 examples 中的配置。

Raw 保存请求、原响应及实际接收时间。归一化失败后从相同计划恢复；全部必需阶段成功后才推进 current。新增证券只能追加稳定绑定；更改单位或来源解释时显式重建目标域，旧 Snapshot 不变。供应商凭据位于数据根和代码之外。

显式、人工审阅的官方 ETF 拆分补充使用独立事实域和同一 update/events 管线，见 [份额折算输入](docs/fund-share-conversions.md)。它保留公告原件与真实 receipt，不改原分红、因子或状态域。

## 时间与来源

- `operational_pit_v1` 使用实际首次观察时间。
- `market_pit_safe_v1` 使用精确 revision 的公开证据，缺少证据时回退到首次观察。
- `best_effort_vendor_v1` 使用明确的供应商日期假设，适合终态历史探索，不声称恢复了当年的信息集。
- `bootstrap_hybrid_v1` 明确按 session 指定上述政策。

来源字段的单位、修订和缺失语义见 [事件接源](docs/local-event-sources.md)、[公开证据](docs/public-evidence.md) 和 [供应商生命周期与成员](docs/reference-readiness.md)。供应商没有返回一行，不能随意解释为零、停牌或非成员。来源本身的错误与 Axiom 映射错误分别记录。

## 跨仓与搬移

Research 将 DataBatch 转成 Core 输入；Runtime 固定一次决策所用的 Snapshot 和查询，并维护回放时钟；UI 组合各 owner 的只读投影。Data 不依赖这些仓库，也不承担模型、撮合或完整前端。

需要Qlib的任务显式导出固定Snapshot的数字日频查询；Research的`QlibView`通过实际Qlib读取。导出保留原始单位、NaN、PIT cutoffs和成员区间，同查询可以复用；普通Reader和日更不自动物化。详见[Qlib接口与真实验证](docs/qlib-interface.md)，两套教程均有实际运行章节。实际消费者环境按`requirements-qlib.lock`安装；基础Data导出不导入Qlib。

[Portable bundle](docs/local-portable.md) 保存固定 Snapshot 的 Raw/Parquet 闭包以及可恢复源码和环境信息。移动硬盘或云端仅负责搬运相同字节；新机器安装环境后可以离线读和重建。旧格式数据使用其随附代码，不在当前包里增加兼容发布系统。

## 维护

新增字段、数据修正与实验复核按 [变更与恢复](docs/data-change-and-recovery.md) 和 [接源演进](docs/source-evolution.md) 增加必要合同与转换即可，无需插件注册服务。仓库只保留当前实现；旧源码备份在工作区仓库之外，已有数据根未自动迁移。

```sh
PYTHONPATH=src:tests:../axiom-engine/src:../axiom-research/src:../axiom-ui/src python -m unittest discover -s tests -v
```

[设计对照](docs/design-conformance.md) 与 [验收索引](docs/demo-acceptance.md) 区分真实来源核对、语义反例测试与按消费者启用的条件项。

Public design/tutorial editing now lives in [axiom-docs](https://github.com/sinnergarden/axiom-docs). This repository retains code, configuration and local measured reports.
