# PR8 全历史行业源资格评估

状态：完整目标集合的源对比已完成；首选 SW2021，正式 promotion 尚未放行。
等待处理的是 SW 两个接口对“特钢Ⅲ”的代码矛盾，不是先前已获准保留的少量 source coverage gap。
V1 bootstrap、baseline、56 项 full admission、daily/recovery 和完整 Notebook 尚未完成。

## 范围与证据

显式运行：`industry-qualification-20260909-r1`，数据根目录
`/home/liuming/workspace/axiom/data`。PR4 historical union 3,601 个证券，
2014-01-01 至冻结的可用日期 2026-09-08。21 个证券在这一期间已不属于上市生命周期，
eligible 为 3,580；所有 3,601 个证券均参与身份/历史存在性检查。
日历分别使用 SSE/SZSE，交易所缺失或 symbol/exchange 不一致即失败。

本轮冻结 1,821 个 RawBatch：SW 948 请求、24,528 响应行（含重复和 taxonomy）；
CITIC 873 请求、20,413 响应行。原始载荷共 10,823,256 字节。
首末响应时间跨度约 402 秒，包含不同检查阶段之间的本地分析时间，不能当成纯 API 吞吐率。
所有明确 Raw ID、manifest digest、请求和观察时间见 `raw_refs.json`。
完整逐证券分数保存在 `qualification.json` 指明的路径，绑定 SHA256。

每个候选的全量 Y/N 分页重复两次，显式 limit=1000、连续 offset、完整终止页，
跨页无重复，两次完整行集合相同。随后以 L3 分类分别请求 Y/N：
SW 347 个类别（346 个分类表类别加一个成员响应额外代码），CITIC 278 个类别，
共 1,250 个请求，与分页结果逐行、保留 multiplicity 比较全部相同。
再对全部 SW 缺口证券及 CITIC 对照/缺历史/冲突证券执行 508 个单证券 Y/N 请求，全部相同。
三个重点证券 default=Y；N 返回退出历史而不是补齐生命周期的保证。
没有股票×每日采集。

## 相同规则的比较

分母统一为 9,162,373 个交易证券 session，退市边界采用逐证券交易所官方记录。
下表的“有区间覆盖”先评价 supplier membership interval；taxonomy 代码资格独立门禁。
绝不能把这个比率冒充已通过 canonical admission 的比率。

| 指标 | SW2021 | CITIC（member API 观察体系） |
|---|---:|---:|
| 全市场单轮 Y+N 成员行 | 7,908 | 6,740 |
| 全市场 Y / N | 5,902 / 2,006 | 5,504 / 1,236 |
| 全量单轮分页请求 | 9 | 8 |
| 有区间覆盖 session | 9,035,545 | 7,270,382 |
| 区间覆盖率 | 98.615773% | 79.350426% |
| source gap session | 125,648 | 1,891,253 |
| gap securities | 110 | 2,601 |
| 完全没有成员历史的目标证券 | 0 | 31 |
| 最长连续 gap（交易日） | 2,330 | 2,576 |
| 生命周期起点一侧 gap session | 104,718 | 1,805,586 |
| 内部 gap session | 20,930 | 77,407 |
| 生命周期终点一侧 gap session | 0 | 21,168 |
| out_date ambiguity session | 1,180 | 738 |
| 内部 overlap / conflicting sessions | 0 / 0 | 42 / 42 |
| 完全重复 interval row / invalid boundaries | 0 / 0 | 0 / 0 |
| 当前 L 证券无 Y / 多个 Y | 0 / 0 | 0 / 0 |
| 成员历史观察到的 L1/L2/L3 | 31 / 131 / 338 | 30 / 108 / 278 |
| 同 code 多 name / 多 parent | 0 / 0 | 0 / 0 |

生命周期起点/终点两侧统计允许同一个全生命周期缺口同时命中两侧，不能再次求和；
唯一 gap 总数按 session 集合计算。SW 62 个证券累计 gap 超过 1,000 session，
不能用平均覆盖率声称每个证券历史都完整。全部 gap 已被独立的单证券请求复核，
它们是供应商可重现的数据缺口，不是明确“无行业”的经济事实。
最早 membership 起点保留原值，绝不截成 2014-01-01 或向前填充。

CITIC 的 42 个内部冲突 session 来自 000615.SZ、000990.SZ、002254.SZ，
均为 2022-12-13 至 2022-12-30 的 14 个交易日。没有通过选择其一掩盖冲突。

## 三个重点证券

| 证券 | 上市日期 | SW 首次 membership | SW gap | CITIC 首次 membership | CITIC gap |
|---|---|---|---:|---|---:|
| 000506.SZ | 1993-03-12 | 2009-01-05 | 1,000 | 2019-12-02 | 1,442 |
| 000975.SZ | 2000-06-08 | 2009-06-01 | 119 | 2005-01-04 | 1,442 |
| 001289.SZ | 2022-01-24 | 2022-02-18 | 14 | 2022-03-01 | 21 |

000975 的 CITIC 虽有 2005 年记录，但 2014 年初至 2019-11-29 仍缺整段可用分类，
不能凭最早记录日期判断覆盖。SW 在 2014-01-02 至 2014-06-30 缺 119 个 session。
000506 的 SW 内部缺口为 2018-06-19 至 2022-07-28。
001289 的 bak_basic 对照在上市日 industry=null，次日及 2022-02-17 为“新型电力”；
这些是另一套字段证据，不能替换 SW“风力发电”或 CITIC“其他发电”。
原 bak_basic Raw ID 和对照行仍见此前 `sw2021_qualification.json`，未重新抓取。

## out_date 与 observation

针对目标证券相邻历史 interval，使用各自交易所日历：

| 关系 | SW | CITIC |
|---|---:|---:|
| new.in_date == old.out_date | 0 | 584 |
| next trading session | 1,166 | 0 |
| 日历范围内 gap | 23 | 126 |
| 实际日期 overlap | 0 | 3 |
| 超出本轮日历范围，未判断交易日关系 | 331 | 134 |

这些分布支持两套 source 的边界惯例不同，不能互相替换。
本轮没有以统计多数强行决定 out_date 包含关系。建议新 contract 使用用户已批准的
`boundary_session_ambiguous`：在 out_date 对应交易日标记 ambiguity；
后续明确 membership 恢复 classified。无 out_date 的历史 Y 也不能证明证券当前仍上市。
历史 knowledge 一律 best_effort，实际采集后的 observation 才提供 observed knowledge；
in/out 日期不会升级为 strict PIT 证据。

## Taxonomy 身份矛盾与裁决点

SW `index_classify(src=SW2021, level=L3)` 两次都返回：
`index_code=850401.SI, industry_code=230501, industry_name=特钢Ⅲ, parent_code=230500`。
对应 L2 为 `801045.SI / 230500 / 特钢Ⅱ`。

但 `index_member_all` 两轮完整分页及类别请求都使用
`l3_code=850412.SI, l3_name=特钢Ⅲ, l2_code=801045.SI`。
请求 850401.SI 的 Y/N 都为空；850412.SI 有 25 条全市场记录。
其中目标集合有 20 个证券/20 条记录，实际影响 32,458 个 session；部分历史记录早于 2014。
两个代码在 source 中不相等，未创建 alias、未按名称改写代码。
申万官方下载文件因服务器证书链无法验证而未被纳入权威证据。

已向用户提出具体处置：允许这部分成为
`classification_unavailable/taxonomy_code_unresolved`，保留 raw codes 并继续；
或要求取得权威 mapping 后继续。该决定未到达前，不把这些行当合法分类 promotion。
剔除这部分后，已知 taxonomy 且有确定区间覆盖的 session 为 9,003,087。

CITIC metadata 只由冻结 member API 的 L1/L2/L3 code/name/parent 建立。
没有在所检查的官方文档中识别到独立 taxonomy endpoint；没有声称不存在其他接口。
未编造 CITIC 2019/2020 等版号。若它被采用，须将 API observation profile digest
作为明确来源体系版本，而不能把供应商 edition 当作已知事实。

## 选源与 missing 决策

按用户给定顺序，首选 SW2021：覆盖显著更好，内部冲突为零，有明确 SW2021 分类表，
重复及类别复核稳定；每天全量 Y/N 仅比 CITIC 多一页。
拒绝 CITIC 作为 V1 authority：约 20.64% session 无分类、31 个目标证券无历史，
并存在已复现的内部冲突。拒绝原因与积分和市场惯例无关。

推荐 SW 每日抓取完整 Y/N 加 L1/L2/L3 taxonomy，当前规模约 12 个请求；
遇到满页继续至终止页，并比较经济 interval 和 code metadata，记录新 observation。
CITIC 同等 member-only 扫描约 8 页。两者上限随供应商行数变化，不是固定预算保证。
当前 qualification 数据重复收集是验收成本，不应当作每天必要成本。

最终已获准的 missing 语义：
- classified：合法 taxonomy、明确 active interval，且在证券生命周期内。
- classification_unavailable/source_coverage_gap：完整请求已证明供应商未提供分类。
- no_observation/collection_gap：请求未完整，不能混为 supplier gap。
- outside_security_lifetime：上市前/退市生效日起。
- boundary_session_ambiguous：out_date session 归属不确定。

`taxonomy_code_unresolved` 的新增具体处置仍待上述决定。
未创建新的 canonical industry contract/lineage；不声称已经发布 final version。
旧 industry contract、SW pilot、2,596 份 bak_basic Raw 保持原样，不成为新 lineage parent。
没有跨 taxonomy fallback、sentinel industry ID 或 forward-fill。

## 228 个退市证券

从交易所自身站点冻结 SSE 终止上市 JSON（159 行，总数及分页一致）和
SZSE 终止上市 Excel（208 行）。所有 228 个目标 D 证券均找到对应记录：
211 个 supplier date 一致，17 个不一致。原始内容及 SHA256 见 `primary_evidence.json`。
已保存的 000005 公告证明其 2024-04-26 为摘牌日，并与 SZSE 表相符。

因此不对 Tushare 字段作全局“摘牌日”或“最后交易日”的改名推断。
确定的 source precedence 是：保留 stock_basic 原值，逐证券使用交易所官方 termination
日期作为 delist_effective_session，生命周期保持 `[list_session, delist_effective_session)`。
不把 supplier 字段自动搬到 last_trade_session；例如 600687 的不同来源给出
2021-03-03 / 2021-03-04，不能仅凭差一天推断最后交易日。

所有 17 个差异见 `qualification.json`。以官方边界替换后本轮期望 session 增加 16，
另一差异证券 600786 已在 2014 之前退市，对本轮分母无影响。
边界 evidence 的完整目标对账已完成；正式 security-master 新 profile/adapter 和
canonical bootstrap 尚未发布，因此没有把“对账完成”写成 V1 blocker 实现已关闭。

## 交付状态

完成：全 union 双源比较、重复/分页/类别/缺口复核、官方退市全表对账、
真实反例 fixture 和公共只读 qualification helpers。
未完成：taxonomy 决策后的新 contract、正式 builder 接入、full bootstrap、
56/56 full admission、baseline、daily/T+1/revision/no-change、offline recovery、完整 Notebook。
没有 merge/push，没有进入 Research/Core/Trade/UI 或扩大 Data domain scope。

最终检查点验证：164/164 PASS，零跳过，113.487 秒；独立重算全部逐证券分数，
真实 fixture 与 Raw 行一致，1,821 个 Raw 引用及一手文件 digest/只读权限通过。
详见 `validation.json` 和 `full_tests.log`。
