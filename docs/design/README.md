# Axiom 设计入口

这一目录是整体设计的唯一权威正文。文档存放在 Data repo，Core、Trade、Research、UI 的逻辑职责仍按总纲划分。修改在此提交，其他仓引用明确版本。

1. [整体架构与仓库边界](01_axiom_overview.md)
2. [Data 设计](02_axiom_data.md)
3. [Core](03_axiom_core.md)、[Trade](04_axiom_trade.md)、[Research](05_axiom_research.md)、[UI](06_axiom_ui.md)
4. [补丁 A](07_重要补丁_A.md)与[补丁 B](07_重要补丁_B.md)

[Researcher 教程](../../notebooks/researcher_tutorial.ipynb)和[Developer 教程](../../notebooks/developer_tutorial.ipynb)用真实小样本解释实现；HTML 由 notebook 生成。设计规定目标，教程展示当前真实行为，验收范围见 [当前交付](../../DELIVERY.md)。

2026-10-03 从工作区 `design/` 迁入，保留了八份正文及 A/B 补丁。原文件未在 Data 的旧 Git 历史内，不虚构迁移前提交历史；首次纳入版本控制后，以本仓提交记录后续变化。原路径仅保留导航，可恢复的迁移前副本保存在仓库外历史归档。
