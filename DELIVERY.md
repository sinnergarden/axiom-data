# Data 交付与运行说明

本轮约定的完整代码范围已验收，包括财务/事件、PIT、供应商生命周期与成员、7ETF日线、Reader与Qlib消费者接口、真实教程、独立安装和数据搬移。验收对象是当前代码及真实原型，十二年全量网络采集尚未运行。见最终验收记录（本地记录：`delivery/final-code-acceptance-20261003.json`）与[完整清单](docs/completion-checklist.md)。

## 安装与教程

当前修订为 **axiom-data 0.3.2**，运行环境Python3.11+。此前0.3.1独立安装和真实preflight证据保留原版本；本次修订针对builder来源、历史Raw选择/备份、事件过滤、成员receipt链和Qlib日历修订。普通采集、Reader查询和Qlib格式导出仅依赖pandas、pyarrow及其运行依赖。实际Qlib读取另安装可选消费者环境，Notebook另安装教学环境。

```sh
python -m venv .venv
. .venv/bin/activate
python -m pip install -r requirements-local.lock .
axiom-data --help
```

- [Researcher教程](notebooks/researcher_tutorial.html)：研究输入、原生事件、PIT、交易状态、Reader与实际Qlib读取。
- [Developer教程](notebooks/developer_tutorial.html)：实际Raw/Parquet/Snapshot/Qlib格式、统一作业、恢复、搬移及接口。
- [权威设计](docs/design/02_axiom_data.md)、[设计对照](docs/design-conformance.md)、[接口合同](docs/local-implementation-contract.md)。

两份Notebook此前执行94个代码单元、0错误，HTML由唯一ipynb编辑源生成。本次0.3.2仅同步说明文字，保留原代码与输出后重新渲染，没有冒称重新执行真实教程。设计正文统一在docs/design，旧路径只保留导航。此前0.3.1的174项测试及真实preflight保留原版本；0.3.2留存wheel独立安装后183项离线测试全部通过、无跳过，含9项新增观察/恢复反例和实际Qlib消费。Data包不通过editable安装或仓库路径导入；运行依赖复用本机锁定环境，Research/Engine/UI消费者源码另行提供，尚未实测另一操作系统。

## Reader与Qlib消费

普通Research/Core/Runtime/UI继续使用固定Snapshot的Reader/DataBatch接口。需要Qlib的任务显式调用`export_qlib`或`qlib-export`，生成不可变P04目录；Research的`QlibView`实际调用Qlib读取，并保留原Snapshot、QuerySpec、单位、PIT cutoff、映射和容差。同查询复用已有导出，普通查询和日更不自动生成Qlib文件。

股票与7ETF真实原型共8352个数字单元通过实际Qlib/Reader等价核对，NaN、单位及股票成员区间一致；导出分别约0.17秒，35/74KiB。价格未复权，财务/分红仍由事件接口读取，不自动前填为日频，也不计算策略feature。见[接口与使用方法](docs/qlib-interface.md)、真实Qlib验收（本地记录：`delivery/qlib_actual_20261003.acceptance.json`）和独立安装/搬移验收（本地记录：`delivery/qlib_installed_20261003.acceptance.json`）。

可选环境使用`python -m pip install -r requirements-qlib.lock`；锁记录本轮Python3.12/macOS实测环境。需要重新解析其他平台环境时可安装`axiom-data[qlib]`，不声称当前锁跨平台通用。

## 真实证据与范围

| 样本 | 数据根 | 结果与范围 |
|---|---|---|
| 固定1800×一年行情 | ../data/csi1800_one_year_202509_202608 | 242交易日；735市场调用；active193.56秒、wall282.62秒；约258MiB；工程集合，不冒称历史当日成员 |
| 双证券完整来源与供应商参考域 | ../data/csi1800_tushare_only_v4_202606_202608 | 14域；2026-06—08月双股行情/事件、五季度财务预热；供应商list/delist及16组dated成员快照；verify/audit通过、重复运行零请求 |
| 7ETF日线 | ../data/etf_rotation_daily_202606_202608 | 含预热114交易日；行情/因子/限价各798行，基准114行，分红44条；8域；采集及处理18.27秒，根约0.80MiB |

股票统一原型重放此前真实Tushare返回，不是本轮重新联网取得全部数据；其新Raw接收时间记录重放时间，日历响应从原全年返回裁到计划范围。这一点不冒充当年或原采集时点的观察证据。早期真实采集保留作来源映射核对；ETF原型为本轮实际联网采集。来源相对审计验证本地请求、映射、单位和格式，不为供应商全部历史正确性背书。

分红的实施公告晚于原公告时，best-effort整行按较晚日期计算可见时间，避免提前取得实施细节；股票下一交易日09:30、ETF当日20:00。strict仍使用实际receipt或精确版本证据。真实股票边界、独立反例及教程已验证。

机器记录：一年实测（本地记录：`docs/delivery-validation.json`）、统一原型（本地记录：`delivery/csi1800_tushare_only_v4_202606_202608.report.json`）、ETF实测（本地记录：`delivery/etf_rotation_daily_202606_202608.report.json`）、教程执行（本地记录：`docs/real-tutorial-validation.json`）。

## 十二年初始化与日更

使用同一个入口：prepare → plan → run → status → verify → audit。完整计划默认含财务与事件；恢复重用相同计划和operation ID；离线重建、导出和导入同样使用已有代码。正常采集/日更不调用模型API，无模型token消耗。

生产配置：[CSI1800历史并集](examples/delivery_csi1800_twelve_year.json)、[固定1800工程队列](examples/delivery_csi1800_twelve_year_fixed1800.json)、[7ETF日线](examples/etf_rotation_daily_twelve_year.json)。完整命令、日更与恢复见[CLI](docs/cli.md)。股票默认历史并集保留所有Tushare dated快照候选、起点前快照、lookback和池外持仓；截至session最近可见的供应商快照延续，不月底倒填、不称精确官方每日成员。外部核对只作warning，不阻断发布。

现行容量模型（本地记录：`delivery/csi1800_twelve_year_tushare_v4.capacity.json`）以2014-11-01至2026-09-28、三项股票基准估算：固定1800工程口径准备及完整域初始选择器上界约2.50万。如果实际请求数达到这一上界，300次/分钟对应约83分钟的限速时间；这是请求模型，不是实际工期保证。含后处理排期2–6小时，单根预留5–9GiB，根＋搬移包＋恢复副本预留15–27GiB。实际交易日数、历史候选并集、接口权限、限额拆分、重试会改变结果；尚未冻结依赖真实prepare结果的十二年计划或完成全量测速。

日更按有界公告窗口处理，默认回看31天。旧公告日期不变的历史修订需显式历史refresh，不能靠短窗口保证发现。7ETF只提供身份、原始日线、因子、分红、限价、日历和基准；动量/排名/信号归Research，开盘成交、佣金和账户归Engine/Runtime，见[ETF合同](docs/etf-rotation-data.md)。

## 搬移与仓库维护

此前用0.3.0安装包导出和导入两类原型：股票14域、ETF8域从Raw离线重建，合同及全部Parquet字节相同，current不变；随包源码与Qlib可选环境锁已核对。见股票搬移（本地记录：`delivery/csi1800_qlib_code_20261003.portable.json`）、ETF搬移（本地记录：`delivery/etf_rotation_qlib_code_20261003.portable.json`）。这是同一macOS主机换目录与独立环境的实测；跨OS未实际运行，当前写入器使用POSIX锁，Windows写入不在本轮范围。

Snapshot bundle保存固定版本读取/重建的Raw、Parquet、祖先、源码和环境闭包。完整作业恢复还需工作根中的preparation、计划与checkpoints；无变化的额外Raw观察不强制加入每份Snapshot。运行sealed源码使用python -B，供应商凭据放在代码和数据根之外。

Qlib目录自身可闭包搬移并直接读取；需要重建它时另带原Snapshot bundle。已有导出是某一固定cutoff的结果，新信息进入后按新查询导出新目录。Qlib运行时的全局provider由Research显式激活，跨任务切换不能悄悄读错数据。

旧实现、重复入口、串行runner及过时报告移至工作区../historical/axiom-data-precleanup-20260928/，不进入当前wheel或默认测试；已有数据根未自动迁移。Data不依赖Research/Engine/UI；Reader和Qlib薄adapter与协议已验收，完整回测账户、模型训练和前端归各自owner。


当前0.3.1另完成四组新鲜完整来源采集、真实中断续跑及独立安装搬移；旧Raw半写恢复与四类变更边界已验证/写明，见[当前preflight](docs/bulk-preflight.md)。此前与当前的原型、包版本和验证范围分别保留。

Public design/tutorial editing now lives in [axiom-docs](https://github.com/sinnergarden/axiom-docs). This repository retains code, configuration and local measured reports.

## 0.3.4 bounded follow-up

Offline `Data.rebuild` now accepts explicit per-domain `canonical_symbols` over
saved whole-market Raw, with append-only stable mappings. It records the derived
selection without changing original requests, receipts or old Snapshots. Supply
the full selected-domain Raw closure; selected domains are rebuilt and unrelated
domains are reused. Missing Raw rows remain missing. Sixteen relevant Raw/rebuild,
field-evolution, resume, identity and update tests passed against the retained wheel;
this is not a rerun of the full historical suite.

Public editable design/tutorial sources have moved to axiom-docs; old entries
are links. The twelve-year run remains paused on the original 0.3.3 wheel/plan,
with saved Raw/checkpoints unchanged. 0.3.4 does not authorize or resume collection.
