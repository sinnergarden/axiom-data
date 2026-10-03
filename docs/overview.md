# 阅读路线

先读 [README](../README.md) 理解仓库边界，再按角色阅读 [Researcher 教程](../notebooks/researcher_tutorial.html) 或 [Developer 教程](../notebooks/developer_tutorial.html)。[Data 设计](design/02_axiom_data.md) 定义目标合同；教程与实现应服从它。

执行入口见 [CLI](cli.md)，存储和操作合同见 [实现说明](local-implementation-contract.md)，真实验收与反例对应见 [设计对照](design-conformance.md)。当前完整交付范围见 [工作清单](completion-checklist.md)。

## 统一事实数据字典

用 `Data.dictionary` 先选字段，再查值。类型、Raw→Canonical 映射、来源/规范单位、转换、累计或存量口径、时间规则和查询方法来自实际 adapter 合同与 source profile；中文含义是展示注释，不构成另一份 schema。

```python
from axiom_data import Data
data = Data('/absolute/data-root')
supported = data.dictionary()  # 代码支持；不要求数据根存在
snapshot = data.resolve('current')
dictionary = data.dictionary(snapshot=snapshot,
    domains=('market_daily', 'financial_events'))
# dictionary['fields'] 是可直接转成 DataFrame 的字段行。
```

| 字典状态 | 含义 | 下一步 |
|---|---|---|
| 代码支持 | 不指定 Snapshot，查看已实现 adapter 的合同/映射 | 选择具体 Snapshot 与需求范围 |
| Snapshot已声明 | 该固定版本包含这个合同/字段，成员 is_member 是 Reader 投影 | 查询实际值与逐键缺失/PIT 原因；声明不保证非空或覆盖完整 |
| 支持但该Snapshot未接入 | 当前代码能处理，该版本没有相应来源/合同 | 使用含该域的新 Snapshot，或按需要准备该源 |
| 仅Raw，未接入Canonical | 显式查看的一份 Raw 中存在该列，但没有规范映射 | 声明键/类型/单位/时间后从已存 Raw 重建所选域 |

默认只读元数据，不读事实分区、不扫描 Raw 历史。需要核对未接入字段时显式加 `raw_batch_id=一个已保存ID`，仅查看那份响应；没有完整供应商字段目录的声明。股东组持股数、停复牌事件类别等输入已参与 normalizer 归纳，但不是独立标量 Reader 字段。

日频值通过 `Data.read + QuerySpec`，成员通过 `Data.members(fields=('is_member',), universe_id=...)`，财务/分红通过 `Data.events + EventQuery`。身份/日历等依赖由 `Data.states`、`Data.plan_scope` 使用；底层合同字段不都能作为日频值查询。时间查询需要显式 policy 和 cutoff，见 [Researcher 教程](../notebooks/researcher_tutorial.html)。

Data 管事实字典；Research 管公式、窗口、缺失处理和 Feature 配方。已发布 Research R0 只有身份/合同，本地未提交 adapter 不等于已发布的财务联合 FeatureBuild。固定 Qlib 导出可以持久复用，Research 的联合对齐与 FeatureBuild 保存仍待实现。
