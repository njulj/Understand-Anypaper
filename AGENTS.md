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
3. `PdfParser` 产出 `ParsedPaper`，对 PDF 渲染页面图片，并保留原始 PDF bytes 供后续 bbox 文本提取。
4. `SemanticUnitSlicer` 把页面图片发给多模态 LLM，切分一组 `SemanticUnit`，每个 unit 只有一个 semantic role，并记录 `page + bbox` 形式的 evidence。bbox 采用归一化 `[ymin, xmin, ymax, xmax]`。
5. `ContributionEvidenceAssigner` 再调一次 LLM，把每个 evidence unit 分配给它支撑的 contribution（写入 `properties.contribution_unit_ids`）。
6. `PaperArgumentGraphBuilder` 构建 `PaperArgumentGraph`：paper 节点 → contribution 节点 → why/how/proof facet 节点 → evidence 节点，节点和边统一引用 `semantic_unit_ids` 作为证据。
7. 图通过 `GraphStore` 保存；数据库可用时写入 PostgreSQL，否则回退到进程内内存 store。

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
- `sql/schema.sql`：PostgreSQL/pgvector schema。核心表包括 `papers`、`nodes`、`edges`、`semantic_units`、`paper_references`、`graph_patches`。
- `tests/test_graph_builder.py`：覆盖当前 PAG builder 的核心行为：能创建 contribution 节点，并保证节点/边带 evidence。

### 后端包：`apps/server/understand_anypaper`

- `main.py`：FastAPI 应用入口，配置 CORS、挂载 API router，并提供 `/health`。
- `config.py`：`pydantic-settings` 配置入口。默认值包括数据库 URL、递归深度/数量限制和 OpenAI/embedding 配置。
- `observability.py`：LLM 可观测性入口。当环境里配置了 `OTEL_EXPORTER_OTLP_*` endpoint 时，安装 OTel providers 把 agent-framework 自带的 LLM span 导出到 Arize Phoenix（或任意 OTLP 后端）；未配置时完全不生效。
- `api/routes.py`：当前所有 REST API。上传接口以 NDJSON 流返回处理进度，图数据通过 `GraphStore` 持久化。
- `parser/models.py`：解析和语义切分数据模型。`DocumentPage` 表示渲染后的页面图片元数据，`PageSourceLocation` 表示 semantic unit 的 `page + bbox + extracted_text`，`SemanticUnit` 表示 LLM 切出的论证语义单元。
- `parser/pdf_parser.py`：parser facade。对 PDF 用 PyMuPDF 渲染页面图片，并提取 title/abstract/reference 元数据；semantic role 和 evidence bbox 由多模态 LLM 负责。`.txt`/`.md` 仍作为文本 fallback。
- `graph/schema.py`：Paper Argument Graph 的 Pydantic 模型和枚举，包括 `NodeType`、`EdgeType`、`GraphNode`、`GraphEdge`、`PaperArgumentGraph`。节点和边通过 `semantic_unit_ids` 溯源。
- `graph/graph_builder.py`：从 `ParsedPaper.semantic_units` 构建 PAG。为每个 contribution 生成节点和 why/how/proof facet 节点，再按 `properties.contribution_unit_ids` 把 evidence 节点挂到对应 facet 下。
- `graph/graph_validator.py`：贡献完整度评分，检查每个 contribution 是否有 motivation/gap、method/module、equation、experiment/result、reference 类型邻居。
- `analyzers/structured_agent.py`：LLM 结构化输出的薄封装，返回类型化的 Pydantic 模型。
- `analyzers/semantic_unit_slicer.py`：多模态语义切分器，把页面图片发给 LLM 并把返回的 text/bbox locator 解析成 `PageSourceLocation`。
- `analyzers/contribution_evidence_assigner.py`：LLM evidence→contribution 分配器，按 contribution 并行调用。
- `storage/graph_store.py`：`GraphStore` 抽象及 PostgreSQL/内存两个实现，负责论文、图、semantic units、references 和源 PDF 的持久化。
- `recursive/traversal_policy.py`：参考文献递归分析的边界策略，限制最大深度、最大论文数和重复访问。
- `retrieval/embeddings.py`：OpenAI 兼容 embedding 客户端，供 Postgres store 的向量检索使用。

### 前端：`apps/web`

- `Dockerfile`：构建 Vite dev server 镜像。
- `package.json` / `package-lock.json`：前端依赖和脚本。`npm run dev` 使用 `vite --host 0.0.0.0`，方便容器端口映射到宿主机。
- `index.html`：Vite HTML 入口，挂载 `src/main.tsx`。
- `tsconfig.json`：TypeScript 配置。
- `src/main.tsx`：当前 React 单页壳。展示顶部工具栏、PDF Reader、Graph Pane、Node Inspector 三个主要区域。
- `src/api.ts`：后端 REST API 的类型定义和 fetch 封装，含带进度流的上传实现。
- `src/GraphView.tsx`：基于 jsMind 的思维导图式图谱渲染组件。
- `src/styles.css`：前端页面样式。
- `src/vite-env.d.ts`：Vite 类型声明。

---

# 开发提示

- 我们仍处于早期阶段，如果要修改数据库格式，直接把旧的库删了就行，不用考虑migration
- 开发环境和开发服务器使用devbox管理,比如`devbox services restart`
- `devbox services up` 会顺带启动 Arize Phoenix（http://localhost:6006），server 的每次 LLM 调用都会以 trace 形式出现在里面（含 prompt/response，`ENABLE_SENSITIVE_DATA=true`），调试 prompt 时先看它
