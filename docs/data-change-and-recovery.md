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

标准 CLI 收集指定 Snapshot 中所选域引用的 Raw，重放该版本的输入闭包：

```sh
axiom-data --data-root /absolute/data rebuild --snapshot SNAPSHOT_ID \
  --domain market_daily --operation-id market-mapping-fix --no-promote
```

以 `axiom-data rebuild --help` 的实际参数为准。**重建替换整个所选域**，不是把一个窗口的 Raw 自动 patch 到原全域。高级 `--request` 必须给足这个域需保留的 Raw 集合；未选域沿用原 manifest，旧 Snapshot 不动。改变解释代码/配置后产生新候选，验证后才能明确发布；不要重跑相同错误逻辑便称修复成功。

历史字段扩展是另一种明确选择：后来 fetch 的已声明字段可能没有变化，因此没有新 Snapshot，但其 Raw 已含新增字段。用只读 `data.select_raw(domains=('market_daily',), receipt_cutoff='2026-10-03T12:00:00Z')` 预览成功/空响应的 ID、receipt 和摘要，保存预览，再把完整 ID 集合交给 `data.rebuild(..., domain_overrides=...)` 或高级 `--request`。cutoff 是实际接收时刻的包含上界；后续 append 不改变已保存的 ID。没有收到字段的响应仍是 null。

默认 portable bundle 是 Snapshot 及祖先的事实依赖闭包，不是完整 Raw 备份。需要保存未被 Snapshot 引用的重复观察和新增字段时，显式使用 `axiom-data export ... --raw-backup-cutoff 2026-10-03T12:00:00Z`；它额外保存此 receipt 前全部状态的 Raw 日志与原字节，Snapshot 依赖始终保留。作业计划、checkpoints 和未完成日志尾片仍另行备份。重复 fetch 不强制产生 Snapshot。

## 数据引用与完整实验版本

Data 查询重放至少保存具体 Snapshot、完整 QuerySpec/EventQuery、PIT policy、每 session cutoff，以及 Reader/稳定派生版本。完整实验再保存实际研究代码 artifact 或 commit、参数、环境锁、种子（若使用）和结果。dirty checkout 只有 HEAD 不足以冻结实际源码。bundle 可以携带代码；若以后重建需原归一化行为，要带当时的代码，而非拿最新包冒充原构建器。

0.3.2 的实际构建来源在 `build_context.builder`，由[实际构建器](../src/axiom_data/builder.py)记录：干净源码 commit 与仓库来源，或完整安装 wheel 的 origin/SHA256/RECORD；同时保存依赖锁摘要和实际 Python/包版本。wheel 必须留存，Git 历史必须可取回。operation checkpoint 固定 builder 和实际配置，未完成作业更换 builder 会拒绝继续；用原 builder 续跑，或为修正版建立明确的新操作。调用者旧 `code_ref` 只是附带说明，不能代替实际来源。未改域保持原 context；重建的成员/生命周期域更新 builder，同时保留冻结供应商输入。dirty 源码拒绝构建，不创建通用源码打包平台。bundle 导出时携带的源码不是自动推定的历史 builder。新增[离线反例](../tests/test_observation_recovery.py)覆盖后续 Raw 字段、完整 Raw 备份、成员首次合建/同日修正/A→B→A、事件空日期、Qlib闭市修订与持久复用。

可复现回答“同一输入和代码能否再次产生同一结果”，正确性回答“输入和计算是否适合研究问题”。两者分别验证。发现错误时记录 issue、受影响域/字段/时间/版本、原因、替代版本与复核状态，标记关联实验待重算；旧结果保留作对照，未受影响研究继续使用。当前不会自动分析所有实验依赖或判定整个研究库失效，不需要先建设一个全局 registry。

当前接口依据：[Raw 存储](../src/axiom_data/storage.py)、[显式重建](../src/axiom_data/updates.py)、[Reader](../src/axiom_data/reader.py)、[便携闭包](../src/axiom_data/portable.py)、[CLI](cli.md)。真实验收按测试、有限原型、同机搬移和十二年采集分别报告。
## 三个增量维度与重做范围

时间扩展使用新的显式窗口，含必要预热和修订回看，合并触及的月份并复用其他分区。标的扩展允许追加稳定身份、用新标的计划并入同一根；既有代码不可改绑。已有字段的新 Feature 公式在 Research 处理；源字段已在 Raw 时先离线 rebuild 所选域，没收到字段才补采实际接口/窗口。旧 Snapshot 与分区保留，旧回测固定原输入和代码，不被 current 更新覆盖。

现有限制：rebuild 替换整个所选域，输入必须覆盖该域；它不是自动分区修补。原全市场 Raw 的 `request.canonical_symbols` 仍冻结旧选择。仅扩展 override 的 identity_map 并 rebuild，不会自动纳入原选择外的新证券；这条离线扩标捷径尚未实现，不能冒称已可用。最小修复方向是显式传入扩标选择，保留原请求/receipt，固定新的选择身份，并验证只新增证券、旧 Snapshot 不变及未选域复用；当前不扩大实现。新增字段可能要重读整个受影响域，但不重采其他接口或覆盖旧版本。一天内完成大规模新增是性能目标，需要按实际范围、供应商限制与测量判断，不承诺任意规模硬上限。

文档、注释或 docstring 的非语义改动只检查受影响内容，不触发全量测试、重导出或采集；commit 记录来源，不充当自动全量重跑开关。未完成作业继续使用其原冻结安装包和同一计划；若换 builder 被拒绝续跑，保留原包完成该作业，无需引入语义 hash 框架。数据/配方/时间语义有变化时，才重算实际受影响产物。
