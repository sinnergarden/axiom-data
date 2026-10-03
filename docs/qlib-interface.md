# Reader 与 Qlib：同一事实，两条消费路径

Data的常规跨仓接口是`Snapshot + QuerySpec → DataBatch`。Research/Runtime将其映射为Core输入，UI使用JSON事实图层。Qlib接入属于P04：需要这个后端时，显式导出固定数字日频查询，Research的薄adapter读取它。Qlib不是Raw或Canonical的替代品，也不是日更必须构建的View。

## 调用与实际输出

```python
from axiom_data import Data, QuerySpec, verify_qlib_export
from axiom_research.qlib_adapter import QlibView

data = Data(DATA_ROOT)
snapshot = data.resolve('current')
# queries是完整开放日历（含lookback）、稳定证券与cutoff对齐的QuerySpecs。
manifest = data.export_qlib(
    snapshot=snapshot, queries=queries, destination=VIEW_DIR,
    # 可选：universe_query=membership_query, universe_name='csi1800'
)
verify_qlib_export(VIEW_DIR, data=data)
view = QlibView(VIEW_DIR).activate()
frame = view.read(fields=('close', 'factor'))
reference = view.reference  # 保存P04引用，与研究结果一起留存。
```

Research adapter实际调用`qlib.init`和`D.features`，返回Qlib的`instrument/datetime`索引与`$field`列。`reference`保留Snapshot、完整查询、映射、单位、价格口径、Reader/exporter版本、float32合同和view ID。它不伪造逐键PIT元数据；需要逐键来源时，重读所引用Snapshot的相同Data查询。

安装Data本体可导出和核对格式，不需要Qlib。消费或运行完整教程时安装`axiom-data[qlib]`（当前0.3.0）；本次实际使用的环境记录在[requirements-qlib.lock](../requirements-qlib.lock)。Research adapter位于Research仓库；直接使用Qlib的研究者也可以用其原生API加载同一个目录。

## 格式、时间与单位

```text
view/
  axiom-qlib.json                 固定查询、单位、映射、版本、文件摘要和容差
  calendars/day.txt              含lookback的完整开放日历
  instruments/all.txt            请求的候选范围
  instruments/csi1800.txt        可选：PIT选择后的已知成员区间
  features/<instrument>/
    close.day.bin
    factor.day.bin
    ...
```

bin采用Qlib的小端float32格式：首个值是日历起始下标，后续值对应开放session。命名成员区间包含首尾交易日，退出/再入保留为不同区间；未知成员无法用False代替。默认instrument为稳定身份的uppercase，避免六位代码复用时合并身份；可提供显式唯一映射。文件布局依据[Qlib官方Data文档](https://github.com/microsoft/qlib/blob/main/docs/component/data.rst)及[官方文件读取实现](https://github.com/microsoft/qlib/blob/main/qlib/data/storage/file_storage.py)。

导出调用原Reader，先筛PIT可见revision再转换数值。每份View只代表导出时的cutoffs；Qlib不会在读取时重新判断PIT或恢复未保存的历史公开版本。新Snapshot、字段或cutoff需要新目录；旧查询重复导出只验证并复用已有文件。日历缺交易日会改变lag含义，因此不能只导出调仓日或静默忽略缺日。

导出保留原生未复权价格、成交量和factor：股票价格CNY/share、成交量share；ETF价格CNY/fund unit、成交量fund unit。默认不把volume重命名成另一种归一化口径，不填停牌价格、不产生动量/排名/信号。这与Qlib官方示例训练数据常用的调整价格/成交量约定不同，调用训练handler或processor前由Research明确配方。若需要别名，用`field_aliases={'market_daily.volume_shares': 'volume'}`，原单位仍写入manifest。

float32转换默认`rtol=1e-6, atol=1e-8`，记录各字段最大绝对舍入误差，NaN位置不变；更严格的精度可由调用者声明，超出容差则不发布。财务与分红是原生事件，通过`Data.events`消费；不会自动前填为日频bin。需要日频财务Feature时，其投影、cutoff和训练语义由Research显式定义。

## 批量、缓存与搬移

导出按月批量读取，字段临时矩阵使用分页文件，最终每个instrument/field的bin只写一次，临时矩阵在发布前删除。只对明确需要Qlib的范围物化，不为每次Reader查询生成文件。Qlib规定的按证券/字段文件只在这条路径产生；它们不重复成为Data事实版本。

Qlib provider是进程级状态。adapter显式activate后可重复读取；另一个任务切换provider时，旧view拒绝继续读取。跨任务可用进程隔离；不在每次查询偷偷初始化后端。磁盘表达式/数据缓存关闭，内存缓存由Qlib管理，导出目录保持只读。

整个View目录可经移动硬盘或云端复制，读取不依赖原绝对路径。它是固定格式输出和来源引用，不包含完整Raw；需要原事实离线重建时同时携带Data的portable bundle。bundle捕获当前Data源码与可选Qlib环境lock，普通Data读取仍不强制安装Qlib。

## 自助命令与验收

```sh
axiom-data --data-root /absolute/source-root qlib-export \
  --snapshot current --spec examples/qlib_stock_debug.spec.json \
  --destination /absolute/new-qlib-view
axiom-data --data-root /absolute/source-root qlib-verify \
  --view /absolute/new-qlib-view --against-reader
```

两份[实际spec](../examples/qlib_stock_debug.spec.json)、[ETF spec](../examples/qlib_etf_debug.spec.json)对应现有原型。扩展十二年时按实际prepare后的身份、完整日历、所需字段及cutoff构造QuerySpecs，代码入口相同，不重拉供应商数据，也不要求agent提供补救脚本。

真实验收（本地记录：`delivery/qlib_actual_20261003.acceptance.json`）使用Qlib0.9.7：股票2证券×65交易日、ETF7证券×114交易日，9个字段合计8352个数值单元，与Reader的键和NaN位置一致，数值满足声明容差；股票成员区间一致。导出分别约0.17秒、35KiB与74KiB，不外推为十二年速度。独立反例另验证cutoff不可见、缺价、退出/再入、未知成员、文件损坏、精度失败、provider切换和搬移；训练、模型、策略回测与任意processor等价性属于消费者验收。
