创建一个帮助学习/阅读任意论文的MVP产品，大体上有点像 [Egonex-AI/Understand-Anything](https://github.com/Egonex-AI/Understand-Anything).

一些决定：

- 使用client/server架构，生成graph的流程放服务端
- 使用codex+skill的方式，为了简单可以考虑cli调用给prompt的方式。
- 使用postgres sql+pgvector

MVP PRD:

你这个 MVP 的核心不应该是“把论文拆成章节图”，而应该是构建一个可追溯的 **Paper Argument Graph，论文论证图谱**：

> 以 Contribution 为核心节点，把动机、问题、方法、公式、图表、实验结果、结论和参考文献组织成一组相互关联、可递归展开的证据子图。

这样用户看到的不只是“论文有什么内容”，而是：

> 作者为什么提出这个贡献、贡献具体是什么、如何实现、哪些公式支撑、哪些实验验证、又建立在哪些前人工作之上。

---

# 一、MVP 的核心产品形态

用户上传论文后，系统生成三层图谱。

```text
Paper
├── Contribution 1
│   ├── Motivation
│   ├── Research Gap
│   ├── Method Component
│   ├── Equation
│   ├── Figure
│   ├── Experiment
│   ├── Evidence
│   └── Related References
│
├── Contribution 2
│   ├── Motivation
│   ├── Method Component
│   ├── Equation
│   ├── Table
│   └── Related References
│
└── Contribution 3
    ├── Motivation
    ├── Method Component
    ├── Experiment
    └── Evidence
```

图谱不是简单树结构，而是允许共享节点。

例如某个 Figure 2 同时解释 Contribution 1 和 Contribution 2：

```text
Contribution 1 ──SUPPORTED_BY──> Figure 2
Contribution 2 ──ILLUSTRATED_BY──> Figure 2
```

某个消融实验也可能同时验证两个模块：

```text
Experiment 4.3
├── VALIDATES ──> Module A
└── VALIDATES ──> Module B
```

因此底层必须是 **有向属性图**，而不是普通目录树。

---

# 二、MVP 最核心的四种图

## 1. 论文全局论证图

默认首页显示论文的整体逻辑。

```text
                         ┌─────────────────┐
                         │      Paper      │
                         └────────┬────────┘
                                  │
             ┌────────────────────┼────────────────────┐
             │                    │                    │
    ┌────────▼────────┐  ┌────────▼────────┐  ┌────────▼────────┐
    │ Contribution 1  │  │ Contribution 2  │  │ Contribution 3  │
    └────────┬────────┘  └────────┬────────┘  └────────┬────────┘
             │                    │                    │
       Motivation            Method Module         Experiment
       Research Gap          Equation              Evidence
       Method                Figure                Conclusion
       Evidence              Reference             Reference
```

每个 Contribution 节点应显示：

- 贡献标题
- 一句话概括
- 作者是否明确声明
- 系统提取置信度
- 关联动机数量
- 关联方法模块数量
- 关联公式、图、表数量
- 关联实验和证据数量
- 关联参考文献数量

用户可以快速判断每个贡献是否形成完整的论证闭环。

---

## 2. Contribution 证据图

点击一个 Contribution 后，展开其局部子图。

```text
                         Contribution 1
                               │
             ┌─────────────────┼──────────────────┐
             │                 │                  │
         WHY / 为什么       HOW / 怎么做       PROOF / 如何证明
             │                 │                  │
       Motivation 1        Module A           Experiment 1
       Research Gap        Module B           Table 2
       Limitation          Equation 3         Figure 5
       Prior Work          Algorithm 1        Conclusion
```

建议产品上把节点分成三个区域：

### WHY：为什么需要这个贡献

- 背景
- 动机
- 现有方法局限
- 研究缺口
- 相关工作
- 问题定义

### HOW：贡献如何实现

- 方法思想
- 模块
- 公式
- 算法
- 架构图
- 训练过程
- 推理过程

### PROOF：贡献如何被验证

- 实验设置
- 数据集
- 指标
- 主结果
- 消融实验
- 可视化结果
- 复杂度分析
- 作者结论

这种三分结构比按 Introduction、Method、Experiment 分类更适合“理解论文”。

---

## 3. 段落—公式—图表引用图

用户点击任意段落、公式、图或表，进入内容级关系图。

例如点击公式 5：

```text
Previous Paragraph
        │
        │ DEFINES
        ▼
   Variable h_i
        │
        ▼
    Equation 5
      ├── DERIVED_FROM ──> Equation 3
      ├── USES ──────────> Module A
      ├── IMPLEMENTS ────> Contribution 2
      ├── CITED_BY ──────> Paragraph 18
      ├── VISUALIZED_IN ─> Figure 4
      └── EVALUATED_BY ──> Ablation Table 3
```

点击 Figure 4：

```text
Figure 4
├── caption
├── textual explanations
├── method modules shown in figure
├── equations associated with modules
├── experimental evidence referred to
├── contribution supported
└── references mentioned in surrounding paragraphs
```

点击一个段落：

```text
Paragraph 27
├── semantic role: method explanation
├── belongs to: Contribution 2
├── mentions: Module A, Equation 5
├── cites: [12], [18]
├── supported by: Figure 4
└── followed by: Paragraph 28
```

---

## 4. 参考文献递归图

用户点击某个引用 `[18]` 后，不只展示参考文献标题，而是显示它为什么在这里被引用。

```text
Current Paragraph
       │
       │ CITES_FOR: method foundation
       ▼
Reference [18]
       │
       ├── metadata
       ├── cited claim
       ├── citation intent
       ├── referenced method
       ├── original abstract
       └── recursive analysis
```

点击“递归解析”后：

```text
Current Paper
     │
     └── EXTENDS ──> Reference [18]
                          │
                          ├── Contribution A
                          ├── Method M
                          ├── Equation 3
                          ├── Figure 2
                          └── References
                                  │
                                  └── Reference [7]
```

为了避免无限展开，需要加入：

- 最大递归深度
- 最大解析论文数
- 循环引用检测
- 已解析论文缓存
- 用户手动继续展开
- 同一论文去重

MVP 建议默认递归深度为 1，最多自动解析 5 篇直接相关论文。

---

# 三、图谱 Schema 设计

## 1. 核心节点类型

MVP 不要定义过多节点，建议控制在 15 类以内。

### 高层语义节点

```text
Paper
Contribution
Motivation
ResearchGap
Claim
Conclusion
```

### 方法节点

```text
Method
Module
Equation
Algorithm
```

### 证据节点

```text
Experiment
Result
Figure
Table
```

### 原始内容节点

```text
TextBlock
Reference
```

其中 `TextBlock` 可以进一步用属性区分：

```json
{
  "block_type": "paragraph | sentence | caption | list_item",
  "semantic_role": "motivation | method | experiment | conclusion"
}
```

不要在 MVP 中为每一种段落角色都建立独立节点类型，否则 Schema 会快速膨胀。

---

## 2. 核心边类型

### 归属关系

```text
Paper HAS_CONTRIBUTION Contribution
Contribution HAS_MOTIVATION Motivation
Contribution ADDRESSES ResearchGap
Contribution IMPLEMENTED_BY Method
Method HAS_MODULE Module
Contribution SUPPORTED_BY Result
```

### 内容依附关系

```text
TextBlock DESCRIBES Contribution
TextBlock EXPLAINS Module
TextBlock DEFINES Equation
Figure ILLUSTRATES Method
Table REPORTS Result
Experiment PRODUCES Result
Equation FORMALIZES Module
```

### 论证关系

```text
Motivation JUSTIFIES Contribution
ResearchGap MOTIVATES Contribution
Claim SUPPORTED_BY Result
Result VALIDATES Contribution
Result VALIDATES Module
Conclusion SUMMARIZES Contribution
```

### 引用关系

```text
TextBlock CITES Reference
Equation BUILDS_ON Reference
Method EXTENDS Reference
Contribution CONTRASTS_WITH Reference
Reference CITES Reference
```

### 文档结构关系

```text
TextBlock NEXT TextBlock
TextBlock PREVIOUS TextBlock
Section CONTAINS TextBlock
Figure REFERENCED_BY TextBlock
Equation REFERENCED_BY TextBlock
```

---

# 四、每个节点必须保存原始证据

系统生成的任何高层节点都不能只有一段 LLM 总结。

例如一个 Contribution 节点：

```json
{
  "id": "contribution-1",
  "type": "Contribution",
  "title": "Hierarchical cross-modal fusion",
  "summary": "The paper proposes a hierarchical fusion mechanism...",
  "source_type": "explicit",
  "confidence": 0.94,
  "evidence_ids": [
    "text-page1-block12",
    "text-page3-block41",
    "figure-2",
    "table-3"
  ],
  "page_ranges": [
    [1, 1],
    [3, 4],
    [7, 7]
  ],
  "created_by": "contribution-agent",
  "verified": false
}
```

每一条边也要保存证据。

```json
{
  "source": "table-3",
  "target": "contribution-1",
  "type": "SUPPORTED_BY",
  "confidence": 0.88,
  "evidence": {
    "page": 7,
    "block_id": "text-page7-block18",
    "text": "The proposed fusion module improves..."
  }
}
```

这是防止“看起来图谱很完整，但实际是模型脑补”的关键设计。

---

# 五、Contribution 识别与内容归类流程

这是 MVP 中最重要的算法链路。

## 第一步：提取候选 Contribution

优先从以下位置寻找：

- Abstract
- Introduction 最后一部分
- 明确的 `Our contributions are...`
- Conclusion
- Method 总结
- 作者列出的 numbered list

生成候选贡献：

```json
[
  {
    "title": "...",
    "explicit_evidence": [],
    "candidate_scope": []
  }
]
```

需要区分：

- `explicit`：作者明确列出
- `implicit`：系统根据论文归纳
- `merged`：多个相似表述合并
- `split`：一个过于宽泛的贡献拆成多个子贡献

MVP 中建议只自动生成作者明确声明的 Contribution；隐式贡献标记为“系统归纳”。

---

## 第二步：建立内容单元

先把论文拆成可定位的原子内容：

- Paragraph
- Sentence
- Figure
- Figure caption
- Table
- Table caption
- Equation
- Algorithm
- Reference mention

每个内容单元具有：

```json
{
  "content_id": "paragraph-37",
  "page": 4,
  "section": "3.2",
  "bbox": [80, 120, 520, 280],
  "text": "...",
  "embedding": "...",
  "entities": [],
  "citations": [],
  "neighbor_ids": []
}
```

---

## 第三步：进行 Contribution 归属分类

对每个内容单元执行多标签分类：

```text
Input:
- contribution definitions
- current content
- section title
- previous and next paragraph
- mentioned entities
- local figures/equations/citations

Output:
- related contributions
- relationship type
- confidence
- explanation
```

输出示例：

```json
{
  "content_id": "paragraph-37",
  "assignments": [
    {
      "contribution_id": "contribution-1",
      "relation": "EXPLAINS",
      "confidence": 0.93
    },
    {
      "contribution_id": "contribution-2",
      "relation": "CONTEXT_FOR",
      "confidence": 0.61
    }
  ]
}
```

内容单元必须支持多归属，不能强制只属于一个 Contribution。

---

## 第四步：图表和公式关联传播

图、表和公式通常不会直接写明属于哪个贡献，需要通过周围文本建立关系。

例如：

```text
Paragraph 21 → mentions Figure 3
Paragraph 21 → belongs to Contribution 2
```

系统可推导：

```text
Figure 3 → likely illustrates Contribution 2
```

但是这条边应标记为：

```json
{
  "inference_type": "relation_propagation",
  "confidence": 0.82
}
```

关联来源包括：

1. 正文显式引用：`As shown in Figure 3`
2. Caption 中出现模块名称
3. 图中 OCR 或视觉识别出的模块名称
4. 图前后的上下文
5. 方法节点和公式节点的实体重合
6. 实验小节标题

---

## 第五步：构建 Contribution 论证闭环

系统检查每个 Contribution 是否包含：

```text
Motivation / Gap
        ↓
Method / Mechanism
        ↓
Evidence / Result
        ↓
Conclusion
```

形成完整性评分：

```json
{
  "contribution_id": "contribution-1",
  "completeness": {
    "motivation": 1.0,
    "method": 1.0,
    "equations": 0.8,
    "experimental_evidence": 1.0,
    "references": 0.6,
    "overall": 0.88
  }
}
```

前端可以提示：

- Contribution 1：证据完整
- Contribution 2：缺少直接消融验证
- Contribution 3：主要是工程贡献，没有独立公式

这会形成很强的产品辨识度。

---

# 六、参考文献检索与递归解析

## 1. 引用不能只挂在论文级别

传统 citation graph 通常只有：

```text
Paper A CITES Paper B
```

你的系统需要做到内容级：

```text
Paragraph 12
    └── CITES Reference 8
            ├── citation intent: limitation
            ├── cited object: attention mechanism
            └── supports: Research Gap 1
```

引用节点应保存：

```json
{
  "reference_id": "ref-8",
  "raw_text": "...",
  "title": "...",
  "authors": [],
  "year": 2023,
  "doi": "...",
  "arxiv_id": "...",
  "citation_mentions": [
    {
      "page": 2,
      "paragraph_id": "paragraph-12",
      "sentence": "...",
      "intent": "method_comparison"
    }
  ]
}
```

---

## 2. Citation Intent 分类

MVP 至少支持以下类型：

```text
BACKGROUND
USES_METHOD
EXTENDS
COMPARES_WITH
IDENTIFIES_LIMITATION
USES_DATASET
USES_METRIC
SUPPORTS_CLAIM
CONTRADICTS
```

例如：

> Previous methods [12, 18] fail to preserve fine-grained details.

图谱可以生成：

```text
Research Gap 1
├── IDENTIFIES_LIMITATION_OF ──> Reference 12
└── IDENTIFIES_LIMITATION_OF ──> Reference 18
```

这样用户可以直接从研究缺口追溯到被批评的方法。

---

## 3. 递归解析流程

用户点击一个 Reference 后：

```text
1. 解析 DOI、arXiv ID 或标题
2. 检查本地缓存
3. 获取摘要和元数据
4. 展示引用上下文
5. 用户选择：
   - 只看元数据
   - 解析摘要
   - 解析全文
6. 全文可用时构建子 Paper Graph
7. 将子图挂载到当前引用节点
```

递归解析不是简单打开另一篇论文，而是围绕“当前引用关系”聚焦。

例如用户从 Contribution 2 中点击 Reference 18，系统首先显示：

```text
当前论文引用 Reference 18 的原因：
“作为基础特征聚合模块。”

Reference 18 中相关内容：
- Contribution 1
- Section 3.1
- Figure 2
- Equation 4
```

而不是默认展示 Reference 18 的全部内容。

---

# 七、前端交互设计

## 页面布局

建议采用三栏结构：

```text
┌─────────────────────────────────────────────────────────────┐
│ Toolbar: Paper / Contribution / Reference / Search / Filter │
├─────────────────┬────────────────────────┬──────────────────┤
│ PDF Reader      │ Graph Canvas           │ Node Inspector   │
│                 │                        │                  │
│ 原始论文页面     │ 论文论证图谱            │ 节点详情          │
│ 高亮对应区域     │ 可展开、收缩、过滤       │ 摘要、证据、引用   │
└─────────────────┴────────────────────────┴──────────────────┘
```

## 关键交互

### 点击图谱节点

- PDF 自动跳到对应页
- 高亮原文区域
- 右侧显示节点解释
- 显示相关内容列表
- 显示来源和置信度

### 点击 PDF 段落或公式

- 图谱定位到对应节点
- 显示所属 Contribution
- 显示上下游关系
- 显示引用文献

### 双击节点

递归展开一层关系：

```text
Contribution → Method → Module → Equation
```

### 右键节点

提供：

- 展开直接关系
- 展开全部证据
- 查看原文
- 查看引用
- 递归解析引用
- 隐藏其他节点
- 添加人工关系
- 标记提取错误

### 图谱过滤器

用户可以按节点类型过滤：

```text
☑ Contribution
☑ Motivation
☑ Method
☑ Equation
☑ Figure
☑ Table
☑ Experiment
☑ Reference
```

也可以按视角过滤：

```text
Overview
Method
Evidence
Citation
Reproduction
```

---

# 八、图谱布局策略

不要使用一个布局展示所有内容，否则节点很快变成“毛线团”。

建议支持四种布局。

## 1. Contribution Tree

以论文和 Contribution 为中心的分层布局。

适合快速理解论文结构。

## 2. Argument Flow

从动机到证据的左到右布局：

```text
Motivation → Gap → Contribution → Method → Experiment → Result
```

适合理解作者论证链。

## 3. Citation Network

当前内容在中心，引用文献向外展开。

```text
Current Equation
├── Reference 3
├── Reference 7
└── Reference 12
```

## 4. Evidence Map

Contribution 在中心，公式、图、表和实验环绕。

适合检查贡献是否被充分支撑。

---

# 九、MVP 服务端模块

```text
understand-anypaper/
├── parser/
│   ├── pdf_parser.py
│   ├── layout_parser.py
│   ├── equation_parser.py
│   ├── figure_parser.py
│   └── reference_parser.py
│
├── graph/
│   ├── schema.py
│   ├── graph_builder.py
│   ├── graph_store.py
│   ├── graph_query.py
│   └── graph_validator.py
│
├── analyzers/
│   ├── contribution_extractor.py
│   ├── semantic_role_classifier.py
│   ├── content_assigner.py
│   ├── evidence_linker.py
│   ├── citation_intent_classifier.py
│   └── relation_reviewer.py
│
├── retrieval/
│   ├── lexical.py
│   ├── vector.py
│   ├── graph_retrieval.py
│   └── citation_resolver.py
│
├── recursive/
│   ├── paper_resolver.py
│   ├── recursive_analyzer.py
│   ├── traversal_policy.py
│   └── cache.py
│
├── api/
│   ├── papers.py
│   ├── graphs.py
│   ├── nodes.py
│   ├── references.py
│   └── search.py
│
└── web/
```

---

# 十、建议的数据存储方式

MVP 不建议一开始引入 Neo4j，部署会变复杂。

可以使用：

- SQLite：论文、节点和边的持久化
- JSON：图谱导出和版本管理
- LanceDB 或 Qdrant：语义检索
- 本地文件夹：图、表、PDF 和缩略图
- NetworkX：服务端图算法

SQLite 表结构：

```sql
papers
nodes
edges
content_blocks
references
citation_mentions
analysis_tasks
paper_assets
```

`nodes` 表：

```text
id
paper_id
node_type
title
summary
properties_json
confidence
source_type
created_at
```

`edges` 表：

```text
id
paper_id
source_node_id
target_node_id
edge_type
evidence_json
confidence
inference_type
```

当论文集合规模扩大后，再提供 Neo4j Adapter。

---

# 十一、MVP API 设计

## 上传论文

```http
POST /api/papers
```

## 查看论文图谱

```http
GET /api/papers/{paper_id}/graph
```

## 查看某个 Contribution 子图

```http
GET /api/papers/{paper_id}/graph/subgraph
    ?node_id=contribution-1
    &depth=2
```

## 查找节点证据

```http
GET /api/nodes/{node_id}/evidence
```

## 查找内容所属 Contribution

```http
GET /api/content/{content_id}/assignments
```

## 展开引用

```http
POST /api/references/{reference_id}/resolve
```

## 递归解析引用论文

```http
POST /api/references/{reference_id}/analyze
```

请求：

```json
{
  "depth": 1,
  "focus": "current_citation_context"
}
```

## 图谱检索

```http
POST /api/graph/search
```

例如：

```json
{
  "query": "哪些实验验证了 Contribution 2？",
  "paper_id": "...",
  "node_types": ["Experiment", "Table", "Result"]
}
```

---

# 十二、MVP 处理流水线

```text
上传 PDF
   ↓
版面解析
   ↓
内容原子化
   ├── 段落
   ├── 公式
   ├── 图
   ├── 表
   └── 引用
   ↓
Contribution 提取
   ↓
语义角色识别
   ↓
内容到 Contribution 的多标签归属
   ↓
方法、证据、引用关系抽取
   ↓
图谱一致性检查
   ↓
生成 Paper Argument Graph
   ↓
用户交互式展开
   ↓
按需递归解析参考文献
```

---

# 十三、自动分析与人工校正

论文图谱不可能完全自动正确，因此 MVP 必须支持轻量人工编辑。

用户应当能够：

- 修改 Contribution 标题
- 合并两个 Contribution
- 拆分 Contribution
- 拖动内容到另一个 Contribution
- 修改关系类型
- 删除错误关系
- 添加遗漏节点
- 标记“作者明确声明”或“系统推断”

编辑后保存为 patch：

```json
{
  "operations": [
    {
      "op": "move_edge",
      "edge_id": "edge-104",
      "new_target": "contribution-2"
    }
  ]
}
```

不要直接覆盖原始自动提取结果。这样未来重新解析论文时，可以重新应用用户修改。

---

# 十四、MVP 应当明确限制的范围

第一版建议只支持：

- 英文数字版 PDF
- 结构完整的计算机科学论文
- 1–30 页论文
- 单篇论文自动分析
- 引用论文按需递归分析
- 深度最多 1–2 层
- 公式提取和解释
- 图表定位和归属
- Contribution 级结构图
- 内容级引用关系图
- 人工校正

第一版暂时不做：

- 扫描 PDF
- 复杂双栏阅读顺序纠错的全部情况
- 公式完整数学推导
- 图像内部每一个像素元素的解析
- 数百篇论文的全局知识图谱
- 自动下载全部引用论文
- 自动生成完整综述
- 自动判断论文是否正确

---

# 十五、一个具体示例

假设论文明确给出三个贡献。

```text
Paper: ExampleNet

Contribution 1:
提出新的多尺度特征提取模块

Contribution 2:
提出跨层特征融合机制

Contribution 3:
构建新的训练损失函数
```

系统图谱应组织为：

```text
ExampleNet
│
├── C1 Multi-scale Feature Extraction
│   ├── Motivation
│   │   └── Paragraph 3: existing methods lose small structures
│   ├── Research Gap
│   │   └── Paragraph 7
│   ├── Method
│   │   ├── Module: Multi-scale Encoder
│   │   ├── Figure 2
│   │   └── Equation 3
│   ├── Evidence
│   │   ├── Ablation Table 4, row 2
│   │   └── Figure 6 qualitative result
│   └── References
│       ├── Ref 8: prior multi-scale design
│       └── Ref 12: identified limitation
│
├── C2 Cross-layer Fusion
│   ├── Paragraph 18
│   ├── Module: Fusion Block
│   ├── Figure 2
│   ├── Equation 5
│   ├── Table 4, row 3
│   └── Ref 16
│
└── C3 Adaptive Loss
    ├── Paragraph 22
    ├── Equation 7
    ├── Equation 8
    ├── Training Algorithm 1
    ├── Table 5
    └── Ref 21
```

点击 Equation 5 后：

```text
Equation 5
├── belongs to Contribution 2
├── formalizes Fusion Block
├── variables defined in Paragraph 17
├── derived from Equation 4
├── uses attention concept from Ref 16
├── illustrated by Figure 2
└── validated by Table 4
```

这就是 MVP 最关键的用户体验。

---

# 十六、最合适的 MVP 功能列表

## 必须实现

1. PDF 结构解析
2. Contribution 自动提取
3. 段落、公式、图表、引用原子化
4. 内容到 Contribution 的多标签归属
5. Contribution 论证子图
6. 点击节点定位 PDF 原文
7. 节点证据与置信度展示
8. 内容级 citation intent
9. 按需递归解析引用论文
10. 图谱人工修改和持久化

## 可延后

1. 开放式聊天
2. 论文评分
3. 自动审稿
4. Zotero 插件
5. Obsidian 导出
6. 多论文大规模对比
7. 综述自动生成

开放式问答甚至不必是 MVP 的中心功能。图谱浏览和证据追踪应当优先。

---

# 十七、建议的 MVP 名称和核心概念

项目名称可以继续使用：

**Understand Anypaper**

核心图谱名称建议统一为：

- **Paper Argument Graph，论文论证图谱**
- 简称 **PAG**

核心产品文案：

> Understand Anypaper turns a research paper into an interactive argument graph, connecting every contribution with its motivation, method, equations, figures, experiments, evidence, and references.

中文：

> Understand Anypaper 将论文转换为交互式论证图谱，把每项贡献与其动机、方法、公式、图表、实验、证据和参考文献连接起来。

这个定位会比“AI 论文阅读器”更明确，也更有开源项目辨识度。
