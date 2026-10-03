# 身份、历史成员与采集范围

prepare 冻结采集用的稳定证券并集和日历；Data.members 回答指定 universe/session/cutoff 下的成员状态。生产来源只用 Tushare，外部 cross-check 是可选 warning，不改写供应商事实，不阻断 prepare/run/current。

## 稳定身份与生命周期

股票 stock_basic 按 SSE/SZSE × L/D/P 六片批量读取。代码绑定 `cnstock.<source-code>.<listing-date-YYYYMMDD>`，既有绑定不重分配。历史 T 前缀别名保留，不剥掉 T 后与普通代码合并。响应达到已知 cap 时拆请求，不能把本地截断当完整响应。

list_date 与 delist_date 接受供应商原字段。listing_events 表达 listing/delisting 两类经济事件，上市日期包含、退市日期排除：`[listing_date, delisting_date)`。这是明确的供应商日期解释，不称独立法律认证；末次有行情日不代替 delist_date。严格 PIT 使用真实 receipt，历史 best-effort 才采用声明的经济日期假设。外部日期分歧只报 warning。

## 历史成员：最近供应商快照延续

用户已确认日频研究口径：选择截至 session 最近一份可见且 trade_date 不晚于该日的 Tushare index_weight 完整快照，延续到下一份。没有此前快照时为 unknown。月末新名单不回填月初，成功空响应不清空已有名单。供应商组数差异报告 warning，不触发官网补证门槛。

三个指数按月查询，保留实际所有 trade_date；CSI1800 是三组名单的并集。源快照日期、研究 session、first_observed_at 分开。`is_member.by_key.source_snapshot_date` 给出这次投影选择的原始快照日期，即便某成员连续入池、正区间始点更早也不会误标。存储采用变更区间与压缩完整组；同值刷新不复制事实，不逐日保存1800行名单。

operational 按实际 receipt 可见；market-safe 未有精确版本公开证据时同样回退 receipt；best-effort 使用声明的供应商日期假设。PIT 不恢复缺失的旧 vintage，也不保证收益偏差只向下。

[接口文档](https://tushare.pro/document/2?doc_id=96) 描述月度成分和权重。真实频率探测（本地记录：`docs/tushare-membership-cadence.json`） 的三个指数在 2026-06-12、15 单日查询均为空，整月和 6/30 查询均只返回 6/30 的300/500/1000行。每日多请求不会产生供应商未提供的日名单。idx_anns 公告目录不是当前成员管线的必要输入，不解析官网附件。

## 候选并集与来源限制

historical_union 收集范围内所有供应商快照中的证券，另含起点前快照、lookback 与池外持仓/挂单。不能截成今天1800。anchor_members 是固定锚点工程样本，不能称完整历史池。

月度快照可能遗漏月内短暂成员；来源没有返回成立前名单时保持未知，不制造指数历史。这些以 limitations 报告，按已接受来源继续初始化，不转为官方调整链 gate。

## 状态、财务与ETF

先使用交易日历与供应商生命周期，再看 suspend_d 的 S/R 及日内时段。闭市不生成交易 session；缺价不是停牌，空响应不是正常交易许可。停牌估值前填是 Runtime 明确政策，不写回 Canonical 价格。

四类财务金额合同为 CNY、比率为百分数；原报告键/报告期/公告日/修订和单季/TTM 保留。常规财务不下载 PDF。ETF 按独立 fund 接口采集，不用股票 daily/adj_factor 冒充，见 [ETF轮动的数据合同](etf-rotation-data.md)。实际验收范围见 [设计对照](design-conformance.md)。
