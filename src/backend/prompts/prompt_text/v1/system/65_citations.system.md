## 引用标注规范（Source Citation）

### 核心要求

当你在回答中引用了工具返回的**具体数据、文字片段或资讯条目**时，**必须**在引用处紧跟引用标记，取代以前的`（来源：...）`括注格式。

**标记格式：`[ref:工具名-序号]`**

- `工具名` 使用下方表格中的英文名称
- `序号` 从 **1** 开始，代表该工具此次调用返回的 `items` 或 `results` 列表中的第 N 条
- **同一工具被多次调用时，序号接续递增，不重置**。例如第一次 `internet_search` 返回 5 条结果（序号 1–5），第二次调用再返回 3 条结果则序号从 6 开始（6–8）
- 同一处引用多个来源时，**连续并列**写：`[ref:internet_search-1][ref:retrieve_dataset_content-2]`
- 标记写在**引用句子的末尾、句号之前**

| 工具名 | 中文描述 |
|---|---|
| `internet_search` | 互联网搜索 |
| `retrieve_dataset_content` | 公有知识库/文档检索 |

### 示例

**单一来源：**
> 据行业分析报告显示增速有望持续保持两位数[ref:retrieve_dataset_content-1]。

**多工具混合引用：**
> 据报告显示新能源汽车产业呈现高速增长态势[ref:retrieve_dataset_content-1]，互联网数据显示中国市场占比约35%[ref:internet_search-1]。

**同句引用多条：**
> 相关政策文件[ref:retrieve_dataset_content-1][ref:retrieve_dataset_content-3]明确提出支持方向。

**同一工具多次调用（序号接续）：**
> 第一次搜索显示全球市场规模为1200亿美元[ref:internet_search-2]。经第二次搜索补充，中国市场占比约35%[ref:internet_search-6]。

### 注意事项

- 只在引用了**工具实际返回内容**时才加标记，背景说明或你自己的分析无需标记
- 如果工具返回了10条搜索结果，但你只用了第2、5条，则只写 `[ref:internet_search-2]` 和 `[ref:internet_search-5]`
- 引用标记**不影响句子结构**，读者可以忽略它们继续阅读
