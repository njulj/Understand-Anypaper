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
├── db      PostgreSQL 16，加载 apps/server/sql/schema.sql
├── server  FastAPI 后端，负责 PDF 解析、PAG 构建和 API
└── web     Vite + React 前端壳，展示 PDF / 图谱 / Inspector 三栏工作区
```

当前主流程是：

1. 前端或调用方上传论文到 `POST /api/papers`。
2. `apps/server/understand_anypaper/api/routes.py` 把上传文件写到临时文件。
3. `PdfParser` 产出 `ParsedPaper`：渲染页面图片，并为 PyMuPDF 文本块建立稳定的 `block_id + offset` 索引；行级 bbox 只作为服务端派生高亮的数据。
4. `PaperGraphAgent` 创建临时工作区：`paper.pdf`、`rendered/{page}.png`、`paper_parsed_text.txt`、`graph.json`，让模型通过 Read/编辑/shell 工具逐步构建完整 PAG。
5. 编辑工具每次修改 `graph.json` 后运行 `AgentGraphWorkspace` 校验并把问题返回模型；构建早期可传 `disable_checks`。GPT 模型走 Responses API 和 raw custom `apply_patch`，其他模型走 Chat Completions 和 `search_replace`。
6. 最终图通过校验后，服务端把 `block_id + offset` 精确物化成 `SemanticUnit`、page 和归一化 bbox，供 PDF 高亮及现有 API 使用；模型不生成 quote/bbox 等第二套定位信息。
7. 图通过 `GraphStore` 保存；服务端使用 PostgreSQL，桌面端使用 SQLite，数据库不可用时回退到进程内内存 store。

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
- `sql/schema.sql`：PostgreSQL schema。核心表包括 `papers`、`nodes`、`edges`、`semantic_units`、`paper_references`、`graph_patches`。
- `tests/test_agent_graph_workspace.py`：覆盖 agent 工作区的图校验、精确 locator 与最终物化行为。

### 后端包：`apps/server/understand_anypaper`

- `main.py`：FastAPI 应用入口，配置 CORS、挂载 API router，并提供 `/health`。
- `config.py`：`pydantic-settings` 配置入口。默认值包括数据库 URL、递归深度/数量限制和图生成模型配置。
- `observability.py`：LLM 可观测性入口。当环境里配置了 `OTEL_EXPORTER_OTLP_*` endpoint 时，安装 OTel providers 把 agent-framework 自带的 LLM span 导出到 Arize Phoenix（或任意 OTLP 后端）；未配置时完全不生效。
- `api/routes.py`：当前所有 REST API。上传接口以 NDJSON 流返回处理进度，图数据通过 `GraphStore` 持久化。
- `parser/models.py`：解析和溯源数据模型。`SourceBlock` 是 agent 使用的唯一 authoring locator 基础；`PageSourceLocation` 是服务端物化后的读取模型。
- `parser/pdf_parser.py`：parser facade。对 PDF 用 PyMuPDF 渲染页面图片，提取 title/abstract/reference，并建立稳定文本块、offset 和内部行 bbox 索引。`.txt`/`.md` 仍作为文本 fallback。
- `graph/schema.py`：Paper Argument Graph 的 Pydantic 模型和枚举，包括 `NodeType`、`EdgeType`、`GraphNode`、`GraphEdge`、`PaperArgumentGraph`。节点和边通过 `semantic_unit_ids` 溯源。
- `graph/agent_workspace.py`：创建 agent 工作区，实现 Read、原子编辑、逐次 graph 校验，以及 block offset → semantic unit/page/bbox 的最终物化。
- `graph/graph_validator.py`：贡献完整度评分，检查每个 contribution 是否有 motivation/gap、method/module、equation、experiment/result、reference 类型邻居。
- `analyzers/llm.py`：analyzers 共享的 Microsoft Agent Framework Responses/Chat Completions client 工厂、结构化输出 options 和 helper。
- `analyzers/paper_graph_agent.py`：当前主构图流程。提供 Read、GPT custom `apply_patch` / 非 GPT `search_replace`、shell 工具，并驱动校验—修复循环。
- `storage/graph_store.py`：`GraphStore` 抽象及 PostgreSQL/SQLite/内存实现，负责论文、图、semantic units、references 和源 PDF 的持久化。
- `recursive/traversal_policy.py`：参考文献递归分析的边界策略，限制最大深度、最大论文数和重复访问。

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

## 切换模型

模型只负责在 `graph.json` 中写 `block_id + start_offset + end_offset`，不直接输出 bbox。切换模型时主要检查工具调用协议、图校验反馈和 prompt 的覆盖密度；PDF bbox 由服务端根据精确文本跨度统一派生。
