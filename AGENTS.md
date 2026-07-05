# Understand AnyPaper

生成一个交互式图，帮助用户学习一篇论文。

> 构建一个可追溯的 **Paper Argument Graph，论文论证图谱**：
>
> 以 Contribution 为核心节点，把动机、问题、方法、公式、图表、实验结果、结论和参考文献组织成一组相互关联、可递归展开的证据子图。
>
> 这样用户看到的不只是“论文有什么内容”，而是：
>
> 作者为什么提出这个贡献、贡献具体是什么、如何实现、哪些公式支撑、哪些实验验证、又建立在哪些前人工作之上。

[详细功能文档](./PRD.md)

## 项目架构

这是一个 MVP 形态的 client/server 项目：

```text
docker-compose.yml
├── db      PostgreSQL 16 + pgvector，加载 apps/server/sql/schema.sql
├── server  FastAPI 后端，负责 PDF 解析、PAG 构建和 API
└── web     Vite + React 前端壳，展示 PDF / 图谱 / Inspector 三栏工作区
```

当前主流程是：

1. 前端或调用方上传论文到 `POST /api/papers`。
2. `apps/server/understand_anypaper/api/routes.py` 把上传文件写到临时文件。
3. `PdfParser` 产出 `ParsedPaper` 和一组只负责原文定位的 `SourceBlock`。
4. `LLMAnalyzer` 基于 `SourceBlock` 切分一组 `SemanticUnit`，每个 unit 只有一个 semantic role，并记录自己来自哪些 source ranges。
5. `PaperArgumentGraphBuilder` 从 contribution `SemanticUnit` 出发构建 `PaperArgumentGraph`，节点和边统一引用 `semantic_unit_ids` 作为证据。
6. 图通过 `GraphStore` 保存；数据库可用时写入 PostgreSQL，否则回退到进程内内存 store。

## 目录和文件职责

### 根目录

- `README.md`：面向开发者的项目简介、MVP 范围、快速启动、API 列表和设计原则。
- `PRD.md`：产品需求文档，描述 Paper Argument Graph 的目标体验和功能设计。
- `AGENTS.md`：给维护者和 coding agent 看的项目说明，也记录架构约定。
- `.env.example` / `.env`：本地环境变量示例和实际配置。后端默认读取 `PAG_` 前缀变量，但 `DATABASE_URL` 也在 compose/server 环境中使用。
- `docker-compose.yml`：本地一键启动 db、server、web。db 会把 `apps/server/sql/schema.sql` 挂到 PostgreSQL 初始化目录。

### 后端：`apps/server`

- `Dockerfile`：构建 FastAPI 服务镜像，安装 Python 包并启动 uvicorn。
- `pyproject.toml`：Python 项目元数据、依赖、dev 依赖和 ruff 配置。
- `sql/schema.sql`：PostgreSQL/pgvector schema。核心表包括 `papers`、`nodes`、`edges`、`source_blocks`、`semantic_units`、`paper_references`、`citation_mentions`、`analysis_tasks`、`graph_patches`。
- `tests/test_graph_builder.py`：覆盖当前 PAG builder 的核心行为：能创建 contribution 节点，并保证节点/边带 evidence。

### 后端包：`apps/server/understand_anypaper`

- `main.py`：FastAPI 应用入口，配置 CORS、挂载 API router，并提供 `/health`。
- `config.py`：`pydantic-settings` 配置入口。默认值包括数据库 URL、递归深度/数量限制和 `codex_cli`。
- `api/routes.py`：当前所有 REST API。注意图数据暂存在模块级 `_PAPERS` 内存字典中，重启会丢失。
- `parser/models.py`：解析和语义切分数据模型。`SourceBlock` 表示 PDF/text parser 的原文定位块，`SemanticUnit` 表示 LLM 切出的论证语义单元，`ParsedPaper` 表示解析后的论文。
- `parser/pdf_parser.py`：parser facade。对 `.txt`/`.md` 读文本并按段落生成 `SourceBlock`；对 PDF 用 PyMuPDF 提取页面、bbox、段落/公式/图表 caption 和引用。它不再分配 semantic role。
- `graph/schema.py`：Paper Argument Graph 的 Pydantic 模型和枚举，包括 `NodeType`、`EdgeType`、`GraphNode`、`GraphEdge`、`PaperArgumentGraph`。节点和边通过 `semantic_unit_ids` 溯源。
- `graph/graph_builder.py`：从 `ParsedPaper.semantic_units` 构建 PAG。当前策略是找 `role == "contribution"` 的 semantic units 生成 contribution 节点，再把其他 semantic units 按 role 连接到最近或显式指定的 contribution。
- `graph/graph_validator.py`：贡献完整度评分，检查每个 contribution 是否有 motivation/gap、method/module、equation、experiment/result、reference 类型邻居。
- `analyzers/citation_intent_classifier.py`：规则版引用意图分类器，输出 BACKGROUND、USES_METHOD、EXTENDS、COMPARES_WITH 等枚举。
- `recursive/traversal_policy.py`：参考文献递归分析的边界策略，限制最大深度、最大论文数和重复访问。
- `retrieval/`：向量检索和图检索的预留包，目前只有包入口。

### 前端：`apps/web`

- `Dockerfile`：构建 Vite dev server 镜像。
- `package.json` / `package-lock.json`：前端依赖和脚本。`npm run dev` 使用 `vite --host 0.0.0.0`，方便容器端口映射到宿主机。
- `index.html`：Vite HTML 入口，挂载 `src/main.tsx`。
- `tsconfig.json`：TypeScript 配置。
- `src/main.tsx`：当前 React 单页壳。展示顶部工具栏、PDF Reader、Graph Pane、Node Inspector 三个主要区域。
- `src/styles.css`：前端页面样式。
- `src/vite-env.d.ts`：Vite 类型声明。

---

# 开发提示

- 我们仍处于早期阶段，如果要修改数据库格式，直接把旧的库删了就行，不用考虑migration
-
