# 数据变化、局部修复与实验复核

先界定修改涉及的域、字段、证券、经济时间和知识时间，再选择补拉、重建或重读。目标是修正真实问题，保留原始观察和旧版本；无需因为一个字段错误重新下载所有十二年数据。

## 新字段与新接口

研究计算只使用已有 close、volume 时，Research 新增配方和参数即可，Data 的事实不变。供应商已有 Raw 中包含尚未规范化的字段时，给该域的新合同声明 nullable 类型、单位与映射，用原 Raw 离线重建；原响应没收到字段的记录保留 null。原请求未取字段、或者新 endpoint 尚未采集时，才补对应字段/接口和窗口，再归一化。

已有 `domain_overrides` 支持显式替换合同和来源映射；新 endpoint 的实际请求、经济键、修订和时间解释仍需新增小 adapter 及针对验证。当前没有自动接源插件平台。测试 `test_source_evolution.py` 验证同一 Raw 中已有字段可恢复、未采字段保持空、其他域与旧版本不变。

## Raw 缺失、请求错误和供应商修订

已保存对象损坏或丢失时，优先从已验证的 bundle/副本恢复原字节，核对原摘要。不要改摘要让错误文件“通过”。没有原副本时补原请求，只能得到一次新的供应商观察；已丢失的旧 vintage 无法制造。请求窗口漏拉、字段漏取或明确截断时，只补受影响选择器并保存新 receipt。

供应商修订也是新观察，追加 Raw 后产生新候选 Snapshot；原响应和首次接收时间保留。日更默认回看 31 个原公告日，较旧公告日期或报告期修订需显式历史 refresh。信任 Tushare 的内容，不通过外部官网/PDF 构建发布门槛；请求、类型、键、单位和文件错误仍由 Axiom 修正。

Raw 日志末尾未带换行的片段属于未完成 append。只读操作忽略该片段，继续读取完整前缀；恢复写入在已有 writer lock 下先将片段保存至 `raw/recovery/`，再截至最后完整换行。正常路径只检查末字节，不重复扫描全库。已完成 receipt 不变；完整行损坏仍报错。六项专项测试覆盖 Reader、prepare 重用、bundle 导出、checkpoint 与 bulk 恢复，另有真实进程中断续跑验证。

## 归一化、PIT、复权和 schema 修正

- 字段映射/单位/键/Canonical 错误：修正代码或合同，从原 Raw 重建所选域。
- Reader 的可见性/修订选择错误：同一 Snapshot 用修正版 Reader 重读，重算相关研究输入；无需重新联网。原错误行为需原 Reader 版本才能重现。
- 稳定派生或 Research 配方错误：保留固定输入，修正函数/配方，重算受影响输出。
- 不兼容磁盘 schema：明确迁移到新根，保留旧格式的代码与 bundle。当前 `local_data_v1` 并没有承诺未来格式都能由最新包透明读取。

标准 CLI 可收集所选域的全部 Raw 并创建候选：

```sh
axiom-data --data-root /absolute/data rebuild --snapshot SNAPSHOT_ID \
  --domain market_daily --operation-id market-mapping-fix --no-promote
```

以 `axiom-data rebuild --help` 的实际参数为准。**重建替换整个所选域**，不是把一个窗口的 Raw 自动 patch 到原全域。高级 `--request` 必须给足这个域需保留的 Raw 集合；未选域沿用原 manifest，旧 Snapshot 不动。改变解释代码/配置后产生新候选，验证后才能明确发布；不要重跑相同错误逻辑便称修复成功。

## 数据引用与完整实验版本

Data 查询重放至少保存具体 Snapshot、完整 QuerySpec/EventQuery、PIT policy、每 session cutoff，以及 Reader/稳定派生版本。完整实验再保存实际研究代码 artifact 或 commit、参数、环境锁、种子（若使用）和结果。dirty checkout 只有 HEAD 不足以冻结实际源码。bundle 可以携带代码；若以后重建需原归一化行为，要带当时的代码，而非拿最新包冒充原构建器。

可复现回答“同一输入和代码能否再次产生同一结果”，正确性回答“输入和计算是否适合研究问题”。两者分别验证。发现错误时记录 issue、受影响域/字段/时间/版本、原因、替代版本与复核状态，标记关联实验待重算；旧结果保留作对照，未受影响研究继续使用。当前不会自动分析所有实验依赖或判定整个研究库失效，不需要先建设一个全局 registry。

当前接口依据：[Raw 存储](../src/axiom_data/storage.py)、[显式重建](../src/axiom_data/updates.py)、[Reader](../src/axiom_data/reader.py)、[便携闭包](../src/axiom_data/portable.py)、[CLI](cli.md)。真实验收按测试、有限原型、同机搬移和十二年采集分别报告。
