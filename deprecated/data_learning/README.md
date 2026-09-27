# 本地历史教学引用

课程 Git 版本只含源码和本配置示例，不携带固定环境路径、原件身份清单或执行输出。完整执行版与 HTML 是本地交付附件。

1. 复制 `fixture.example.json` 为同目录的 `fixture.json`，填写已授权、只读、不可变样本的真实 root、Snapshot / View 身份及各域原件行引用；不要选择 current/latest。
2. 在同目录保存与该环境匹配的 `version_inventory.json` 库存证据。源码不重新采集或扫描全库；盘点不是闭包验收。
3. 从仓库根或 notebooks 目录运行课程，使用能导入 pandas、pyarrow 和 axiom_data 的 Python 内核；运行仅读取数据和清单。保存执行副本供阅读，不把数据输出提交 Git。

`fixture.json` 与 `version_inventory.json` 被明确 gitignore，仅保留本地。`examples` 每域的行索引绑定原件和规范记录，业务标签能追到真实输入；未改历史对象、摘要、来源时间或合同。必要历史路径/标识在这个 deprecated 目录内集中保存，不拼接编码隐藏。

未配置时，执行会明确报 FIXTURE UNAVAILABLE；源码与静态解释仍可阅读。本 workspace 的现成配置和对应执行证据仅在本地保留。该配置是教学引用，不是新业务合同、动态选择器或写入入口。
