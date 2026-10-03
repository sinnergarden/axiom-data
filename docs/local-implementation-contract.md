# 本地实现合同

权威设计为 [AX-DATA](design/02_axiom_data.md)。本页描述当前公共接口和格式；真实验证范围见 [设计对照](design-conformance.md)。实现不要求服务、注册中心、Admission 或磁盘 View。

## 固定输入与返回

`Data(root)` 是公共入口。`resolve('current')` 只在实验或决策批次开始时调用一次；后续方法传具体 Snapshot ID。只读方法不联网、不写磁盘、不自动修复。

- `read(snapshot=, query=QuerySpec)`：日频事实，隐含键为 security_id/session。
- `members(snapshot=, query=QuerySpec)`：显式 universe_id、sessions、cutoff，输出 nullable is_member；供应商快照延续的半开区间 `[from,to)`，有可见完整组才能证明非成员。
- `events(snapshot=, query=EventQuery)`：保留报告/经济事件原生键；先选可见 revision，再过滤经济日期，区间 start/end 含两端。
- `states(snapshot=, query=QuerySpec)`：解释闭市、生命周期、整日/日内停牌和缺数；不制造闭市 Canonical 行，不从缺价推断停牌。
- `inspect(snapshot=, required_scope=QuerySpec)`：检查实际用途范围，freshness 与覆盖分开。
- `update` / `rebuild`：显式写操作，保留 Raw、旧 Snapshot 和第一次实际观察；rebuild 不请求来源。
- `export` / `import`：固定闭包的搬移；目标目录须尚不存在，先验证字节再接受。

`QuerySpec` 固定 domain、fields、symbols、sessions、pit_policy、cutoff_by_session；可选 purpose、price_basis、adjustment_anchor、universe_id、policy_by_session。所有 cutoff 带时区。`bootstrap_hybrid_v1` 必须逐 session 声明具体 policy。Reader 只直接读取未复权市场事实，复权使用固定输入纯函数。

`EventQuery` 固定 domain、fields、symbols、start、end、cutoff、pit_policy、time_field、filters 与 purpose。日报与事件是不同表形状，事件不默认向每日前填。

`DataBatch` 包含 pandas frame、field_meta、context。整数和布尔保留 nullable dtype；JSON 为 null，不是 NaN。field_meta 描述单位、实际 Raw/revision、可用依据与缺失原因。context 保存具体 Snapshot、合同、来源、Reader 版本、查询和限制。`to_json()` 返回稳定语义内容；`to_response()` 增加本次 generated_at，该字段不是 source freshness，不进入缓存身份。

## 持久格式

```text
raw/objects/<sha256>/payload.bin
raw/fetches.jsonl
canonical/<domain>/<partition>/<content-id>.parquet
snapshots/<snapshot-id>.json
operations/<operation-id>/...
current.json
```

构造 LocalStore 或只读查询不创建目录。Raw 原字节内容寻址，日志一行一次请求；相同 payload 复用对象。Parquet 按声明字段写类型与 ZSTD；日频按月，财报按报告年，成员按变更区间，完整组压缩保存，读取元信息保留原source_snapshot_date。Snapshot schema 为 local_data_v1，内嵌各域 contract、source_profile、partitions、raw_batch_ids、coverage 与实际 build_context。所有持久对象引用相对数据根，旧对象不覆写。

合同声明经济 logical_key、字段 dtype/unit/nullability；来源配置声明参数、转换、身份、空值、修订顺序和 availability。Raw 时间来自实际 receipt，不用公告日替代。内容相同的再次观察不复制 Canonical、不改变 first_observed；事实或可用性合同改变才新建 Snapshot。A→B→A 是三个实际终态发生，不把最后一次 A 合并回首次 A。

完整源流程在一个冻结计划中分阶段建立候选，最终必需范围成功才一次推进 current。失败保留 Raw 与检查点；恢复同一个 plan/operation，不篡改计划。新增证券绑定只能单调追加。未知单位、代码和截断响应明确失败；预期缺失返回理由，不能填零。

## 时间与读取成本

operational 使用实际第一次观察；market-safe 使用精确 revision 公开证据，否则回退观察；best-effort 使用明确来源假设，不提供完整历史 vintage；hybrid 不被描述为全部严格 PIT。原文证据只是选择性补充，不是每份财报的强制 PDF 工作流。

Reader 按月/字段裁剪，事件在逻辑键包含 report_period 时可按报告年裁剪。可变公告日期的查询须先处理全部相关 revision，不能提前过滤旧版本而复活它。成员完整组一次为整批证券解析；默认缓存 64 MiB，可显式增加或关闭。调用方修改结果不会污染缓存。批量预读结果可由研究任务持有复用，回测不必逐 bar 重读全历史。

## 消费者边界

Data 不 import Engine、Research 或 UI。Research/Runtime adapter 属于消费者，按键把 DataBatch 映射到 Core FactBatch ABI，保留单位、cutoff、空值与来源；不重做 PIT。Runtime 分别限制决策 Reader 与执行回放时钟，固定 session/batch 的真实 Snapshot/QuerySpec。UI BFF 组合各 owner 的只读投影；Data JSON 只是 P12 的事实输入。

Feature、Label、模型、委托、成交和账户不写入Data Snapshot。完整回测或UI产品不因薄接口测试通过就被宣称完成。Qlib P04已实现为显式、不可变的原生数字日频导出；Research拥有实际Qlib消费adapter。保留固定Snapshot/Query、单位、PIT cutoffs、NaN、映射与容差，不自动复权、训练归一化或将财务事件前填。实际Reader等价、成员区间与搬移读取已验证，见[Qlib合同](qlib-interface.md)。

接口参数、时间、副作用与失败语义以源码 docstring 为准；执行入口见 [CLI](cli.md)。当前仓库只保留这套实现，旧源码在工作区仓库外的 `../historical/axiom-data-precleanup-20260928/`，旧数据没有自动迁移。
