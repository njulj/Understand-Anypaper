# 项目进度

更新日期：2026-07-02

## 总体状态

- [x] 项目基础目录和 client/server 架构已建立
- [x] FastAPI 后端可启动
- [x] Vite + React 前端可启动
- [x] Docker Compose 已包含 db/server/web
- [x] 主链路（PDF 解析 → 图谱构建 → 持久化 → 图谱视图）已打通
- [x] LLM 链路已接入（配置 `OPENAI_API_KEY` 即启用，无 key 时回退规则版）

## 前端

- [x] 三栏工作区界面已实现
- [x] 文件上传入口已实现
- [x] 上传后调用 `POST /api/papers` 已实现
- [x] 真实图谱视图已实现（SVG 力导向布局、边 + 箭头、缩放、平移、节点拖拽）
- [x] 节点点击和 Inspector 展示已实现
- [x] 节点搜索/过滤已实现（匹配高亮、非匹配淡出）
- [x] Source 栏展示解析出的内容块（含页码/章节/语义角色）
- [x] 点击 evidence 跳转/高亮原文块已实现
- [x] 人工修正 UI 已实现（编辑标题/摘要、标记 verified、删除节点，走 patch API）
- [x] 刷新后自动从后端恢复已有论文；多论文切换下拉
- [ ] 真实 PDF 阅读器（渲染 PDF 页面 + bbox 高亮）未实现，当前是文本块视图
- [ ] 手动添加节点/边的 UI 未实现（API 已支持）

## 后端 API

- [x] `POST /api/papers` 已实现（解析 + 建图 + 持久化）
- [x] `GET /api/papers` 已实现（列出已入库论文）
- [x] `GET /api/papers/{paper_id}/graph` 已实现
- [x] `GET /api/papers/{paper_id}/graph/subgraph` 已实现
- [x] `GET /api/papers/{paper_id}/blocks` 已实现（前端原文视图数据源）
- [x] `GET /api/papers/{paper_id}/references` 已实现
- [x] `GET /api/nodes/{node_id}/evidence` 已实现（返回 evidence 块全文/页码/bbox）
- [x] `GET /api/content/{content_id}/assignments` 已实现
- [x] `POST /api/graph/search` 已实现（lexical + 可选 pgvector hybrid）
- [x] `GET /api/papers/{paper_id}/completeness` 已实现
- [x] `POST /api/papers/{paper_id}/graph/patch` 已实现（增删改节点/边，记录到 graph_patches）
- [x] `POST /api/references/{reference_id}/resolve` 已实现（Crossref 元数据补全，失败回退本地解析结果）
- [x] `POST /api/references/{reference_id}/analyze` 已实现（返回 mention + 意图统计 + 递归边界判断）
- [ ] 引用论文自动递归抓取/分析未实现（analyze 只做当前论文内分析）

## PDF / Parser

- [x] `.txt` / `.md` 解析已实现（markdown 标题作为章节）
- [x] 真实 PDF 文本解析已实现（PyMuPDF）
- [x] 页码 + bbox 原文定位已实现
- [x] 标题/摘要/章节标题检测已实现（字号/加粗/编号启发式）
- [x] 公式块、图/表 caption 检测已实现（启发式）
- [x] 参考文献列表抽取已实现（`[n]` 编号切分 + 年份/DOI/arXiv/标题/作者解析）
- [x] citation mention 抽取已实现（`[n]`/`[n,m]`/`[n-m]` → 所在句子 + 意图分类）
- [ ] 版面双栏重排、跨页段落合并未实现
- [ ] 图片/表格内容本身未抽取（只有 caption）

## Graph

- [x] PAG Pydantic schema 已实现
- [x] 规则版 graph builder 已实现
- [x] contribution 节点生成已实现（无显式贡献时从摘要推断兜底）
- [x] 邻近 evidence 节点挂载已实现（按语义角色映射边类型：MOTIVATES/IMPLEMENTED_BY/VALIDATES/FORMALIZES/…）
- [x] Reference 节点 + CITES 边 + mention 意图边（BUILDS_ON/EXTENDS/CONTRASTS_WITH/…）已实现
- [x] completeness validator 已实现基础版本
- [x] LLM 版语义角色分类已实现（`analyzers/llm_analyzer.py`，无 key 回退规则）
- [x] LLM 版贡献抽取已实现（同上，evidence 落到 content_id）
- [x] 图修正 patch 机制已接入 API 和 UI
- [ ] LLM 版 evidence linking / 关系判断未细化（当前 LLM 只出角色和贡献）

## 存储

- [x] PostgreSQL + pgvector schema 已写好
- [x] 后端 API 读写数据库已实现（papers/nodes/edges/content_blocks/paper_references/citation_mentions/graph_patches）
- [x] 服务重启后数据保留（已验证）
- [x] graph store 已实现（`storage/graph_store.py`：Postgres 版 + 内存版，DB 不可达自动回退内存）
- [x] 节点 embedding 写入 pgvector（配置 API key 后自动生成）

## 检索

- [x] 基础 lexical search 已实现
- [x] embedding 生成已实现（`retrieval/embeddings.py`，需 API key）
- [x] pgvector 相似度查询已实现（cosine）
- [x] hybrid search 已实现（lexical + vector 分数合并）
- [ ] graph retrieval（沿边扩展的检索）未实现

## 引用与递归

- [x] 引用意图规则分类器已实现基础版本
- [x] traversal policy 已实现基础版本
- [x] 引用列表抽取已实现
- [x] citation mention 抽取已实现
- [x] 引用元数据解析已实现（本地规则 + Crossref resolve）
- [ ] 引用论文递归分析未实现（需要拿到被引论文 PDF）
- [ ] 递归分析缓存未实现

## LLM / 外部依赖

- [x] OpenAI 兼容 API 已接入（chat + embeddings，`OPENAI_API_KEY` / `OPENAI_BASE_URL` 配置）
- [x] embedding 模型配置已定义（`PAG_EMBEDDING_MODEL` / `PAG_EMBEDDING_DIMENSIONS`）
- [x] PDF 解析依赖 `pymupdf` 已接入
- [x] Crossref 已接入（reference resolve）
- [ ] Codex CLI 未接入实际调用
- [ ] Semantic Scholar / arXiv 服务未接入

## 测试

- [x] graph builder 基础测试已存在
- [x] parser 测试已实现（markdown + 生成 PDF，含引用/mention/唯一 ID）
- [x] API 集成测试已实现（上传、graph、blocks、evidence、search、completeness、analyze、patch 往返）
- [x] 前端 `npm run build`（tsc + vite）已通过
- [x] PostgreSQL 持久化冒烟验证已通过（含容器重启后数据保留）
- [ ] 前端交互测试未实现
- [ ] Postgres store 的自动化测试未实现（当前测试用内存 store）

## 建议下一步

- [ ] 前端接入真实 PDF 渲染（pdf.js）并用 bbox 做原文高亮
- [ ] LLM evidence linking / 边关系判断，替换邻近窗口启发式
- [ ] 引用论文递归分析（arXiv/Semantic Scholar 拉取 PDF → 复用同一管线）
- [ ] 手动添加节点/边的 UI
