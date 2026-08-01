# 项目进度

更新日期：2026-07-09

## 总体状态

- [x] 项目基础目录和 client/server 架构已建立
- [x] FastAPI 后端可启动
- [x] Vite + React 前端可启动
- [x] Docker Compose 已包含 db/server/web
- [x] 主链路（PDF 解析 → 图谱构建 → 持久化 → 图谱视图）已打通
- [x] 单 Agent 构图链路已接入（配置 `OPENAI_API_KEY` 后生成完整 graph；无 key 时明确失败）

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
- [x] 真实 PDF 阅读器已实现（后端 PyMuPDF 渲染页面 PNG + 前端 bbox 高亮；文本块视图保留为 fallback）
- [x] 手动添加节点/边的 UI 已实现（走 patch API，并标记 human-added / verified）

## 后端 API

- [x] `POST /api/papers` 已实现（解析 + 建图 + 持久化）
- [x] `GET /api/papers` 已实现（列出已入库论文）
- [x] `GET /api/papers/{paper_id}/graph` 已实现
- [x] `GET /api/papers/{paper_id}/graph/subgraph` 已实现
- [x] `GET /api/papers/{paper_id}/blocks` 已实现（前端原文视图数据源）
- [x] `GET /api/papers/{paper_id}/references` 已实现
- [x] `GET /api/nodes/{node_id}/evidence` 已实现（返回 evidence 块全文/页码/bbox）
- [x] `POST /api/graph/search` 已实现（lexical + graph expansion）
- [x] `GET /api/papers/{paper_id}/completeness` 已实现
- [x] `POST /api/papers/{paper_id}/graph/patch` 已实现（增删改节点/边，记录到 graph_patches）
- [x] `POST /api/references/{reference_id}/resolve` 已实现（Crossref 元数据补全，失败回退本地解析结果）
- [x] `POST /api/references/{reference_id}/analyze` 已实现（返回 mention + 意图统计 + 递归边界判断）
- [x] 引用论文递归抓取/分析基础版已实现（`analyze` 传 `expand: true` 时优先缓存命中，再尝试 arXiv PDF 下载并复用解析建图管线）

## PDF / Parser

- [x] `.txt` / `.md` 解析已实现（markdown 标题作为章节）
- [x] 真实 PDF 文本解析已实现（PyMuPDF）
- [x] 页码 + bbox 原文定位已实现
- [x] 标题/摘要/章节标题检测已实现（字号/加粗/编号启发式）
- [x] 公式块、图/表 caption 检测已实现（启发式）
- [x] 参考文献列表抽取已实现（`[n]` 编号切分 + 年份/DOI/arXiv/标题/作者解析）
- [x] citation mention 抽取已实现（`[n]`/`[n,m]`/`[n-m]` → 所在句子 + 意图分类）
- [x] 版面双栏阅读顺序重排已实现基础启发式
- [x] Agent 唯一使用 `block_id + start_offset + end_offset` 定位原文；服务端从精确跨度派生 page-local bbox
- [x] 跨页段落合并已实现（PDF body blocks 会按续写启发式合并，并写入 `metadata.plain_text` / `metadata.page_texts`）



## Graph

- [x] PAG Pydantic schema 已实现
- [x] `PaperGraphAgent` 一次负责完整 PAG 的创建、检查和修复，无后续 builder/linker 步骤
- [x] Agent 工作区提供 PDF、页面图片、稳定文本块索引和 graph 编辑工具
- [x] 每次 graph 编辑后返回结构/locator/孤立 evidence 等检查结果；早期编辑支持 `disable_checks`
- [x] GPT 模型使用 Responses API custom `apply_patch`；其他模型使用 Chat Completions `search_replace`
- [x] contribution、facet、evidence、引用节点和关系均由同一个 Agent 创建
- [x] completeness validator 已实现基础版本
- [x] 图修正 patch 机制已接入 API 和 UI

## 存储

- [x] PostgreSQL schema 已写好
- [x] 后端 API 读写数据库已实现（papers/nodes/edges/semantic_units/paper_references/graph_patches）
- [x] 服务重启后数据保留（已验证）
- [x] graph store 已实现（`storage/graph_store.py`：PostgreSQL + SQLite + 内存版，DB 不可达自动回退内存）

## 检索

- [x] 基础 lexical search 已实现
- [x] graph retrieval（沿边扩展的检索）已实现（`POST /api/graph/search` 支持 `expand_depth` 并返回 `expanded_subgraph`）

## 引用与递归

- [x] 引用意图规则分类器已实现基础版本
- [x] traversal policy 已实现基础版本
- [x] 引用列表抽取已实现
- [x] citation mention 抽取已实现
- [x] 引用元数据解析已实现（本地规则 + Crossref resolve）
- [x] 引用论文递归分析基础版已实现（arXiv PDF 可自动下载；无可下载 PDF 时仍提示上传）
- [x] 递归分析缓存基础版已实现（按 source reference、arXiv ID、标题匹配已解析论文）

## LLM / 外部依赖

- [x] OpenAI Responses / Chat Completions API 已接入（`OPENAI_API_KEY` / `OPENAI_BASE_URL` 配置）
- [x] PDF 解析依赖 `pymupdf` 已接入
- [x] Crossref 已接入（reference resolve）
- [x] Semantic Scholar / arXiv 服务已接入（Semantic Scholar 元数据补全；arXiv PDF 递归扩展）

## 分析架构规划

- [x] 前置 PDF 解析固定化；整张图由 Agent 在受控工作区内迭代生成并通过确定性校验
- [x] Graph 是 Agent 的最终产物，不再经过语义切分、归属分配或规则 builder 后处理

## 测试

- [x] Agent graph workspace 的校验与物化测试已存在
- [x] parser 测试已实现（markdown + 生成 PDF，含引用/mention/唯一 ID）
- [x] API 集成测试已实现（上传、graph、blocks、evidence、search、completeness、analyze、patch 往返）
- [x] 前端 `npm run build`（tsc + vite）已通过
- [x] PostgreSQL 持久化冒烟验证已通过（含容器重启后数据保留）
- [ ] 前端交互测试未实现
- [ ] Postgres store 的自动化测试未实现（当前测试用内存 store）

## 建议下一步

- [x] 前端接入真实 PDF 页面渲染并用 bbox 做原文高亮（当前采用后端渲染 PNG，不依赖 pdf.js）
- [x] Agent 直接完成 evidence linking / 边关系判断
- [x] 引用论文递归分析（arXiv 拉取 PDF → 复用同一管线；Semantic Scholar 做元数据补全）
- [x] 手动添加节点/边的 UI

## 仍然存在的bug

### 图表定位

模型会输出论文中图表或者表格的坐标，但现在看来，网页上渲染的框框有些漂移，没有覆盖到原图表，可能是解析/渲染出现了错误，需要修复一下。
