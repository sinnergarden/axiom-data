# 真实教程入口

唯一编辑源是 [Researcher notebook](researcher_tutorial.ipynb) 和 [Developer notebook](developer_tutorial.ipynb)。生成的 [Researcher HTML](researcher_tutorial.html) 与 [Developer HTML](developer_tutorial.html) 可直接阅读真实表格。HTML 不独立修改，原工作区 `design/notebooks` 仅保留导航。

Researcher 从事实链路、固定版本和第一次读取开始，解释时间、缺失、复权、财务与成员，再讲查询重放和完整实验。Developer 从 Raw、字段映射和 manifest 开始，解释批量作业、daily/refresh、恢复、迁移与部署。跨仓接口和较长验收放在附录；Qlib 是显式消费格式。

## 离线重新运行

基础 Data 运行不需要教学工具或 Qlib。复跑完整两教程需要本地真实样本和已保存的验收报告，并安装教学及 Qlib 消费依赖。另提供同级 Engine、Research、UI 的薄 adapter 源码；Data 包本身不依赖它们。已测锁定环境为 Python 3.12/macOS，其他平台需解析适用环境。

```sh
python -m pip install -r requirements-local.lock -e .
python -m pip install -r requirements-notebooks.txt -r requirements-qlib.lock
python -m ipykernel install --user --name axiom-data --display-name 'Axiom Data'
python examples/real_tutorials.py
```

本命令执行已有 notebook、更新输出并生成 HTML，不生成另一份正文。只读正式样本，写入演示使用临时数据根，不加载 token 或联网采集。`--no-execute` 只渲染已有输出，不记作新执行。运行报告在本地 `docs/real-tutorial-validation.json`，未推送生产数据或运行日志。

初始化路径和变量集中在 [tutorial_support.py](../examples/tutorial_support.py)，可用 `AXIOM_WORKSPACE`、`AXIOM_TUTORIAL_DATA_ROOT`、`AXIOM_UNIFIED_DATA_ROOT`、`AXIOM_UNIFIED_SNAPSHOT` 等环境变量适配搬移后的实际样本。静态 HTML 不要求安装环境，也无需先拉十二年数据。

## 教学与验收范围

表格来自一年固定1800行情样本、两证券完整来源样本和七ETF日线样本，三者各有固定版本。两证券原教学样本含留存原供应商响应的离线回放，receipt 属于回放时间；新鲜生产入口验证另见 [bulk preflight](../docs/bulk-preflight.md)。合成测试只证明相应语义边界，不当作供应商数据。

[权威设计](../docs/design/README.md)规定目标，[当前交付](../DELIVERY.md)记录已测范围。研究策略、模型、账户回测和 UI 产品仍由其 owner 实现和验收。
