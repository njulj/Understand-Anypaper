# 项目进度

更新日期：2026-07-02

## 总体状态

- [x] 项目基础目录和 client/server 架构已建立
- [x] FastAPI 后端可启动
- [x] Vite + React 前端可启动
- [x] Docker Compose 已包含 db/server/web
- [ ] 还不是完整产品，目前是可运行 MVP 骨架
- [ ] 尚未接入 LLM

## 前端

- [x] 三栏工作区界面已实现
- [x] 文件上传入口已实现
- [x] 上传后调用 `POST /api/papers` 已实现
- [x] 图节点基础展示已实现
- [x] 节点点击和 Inspector 展示已实现
- [x] 节点搜索/过滤已实现
- [ ] 真实 PDF 阅读器未实现
- [ ] 点击 evidence 跳转/高亮原文未实现
- [ ] 真实图布局、边展示、缩放拖拽未实现
- [ ] 人工修正 UI 未实现

## 后端 API

- [x] `POST /api/papers` 已实现
- [x] `GET /api/papers/{paper_id}/graph` 已实现
- [x] `GET /api/papers/{paper_id}/graph/subgraph` 已实现
- [x] `GET /api/nodes/{node_id}/evidence` 已实现
- [x] `GET /api/content/{content_id}/assignments` 已实现
- [x] `POST /api/graph/search` 已实现基础版本
- [x] `GET /api/papers/{paper_id}/completeness` 已实现
- [ ] 引用 resolve API 只是占位
- [ ] 引用递归 analyze API 只是占位

## PDF / Parser

- [x] `.txt` / `.md` 按段落解析已实现
- [x] 内容块 `ContentBlock` 模型已实现
- [ ] 真实 PDF 文本解析未实现
- [ ] 页码、版面、公式、图表、引用抽取未实现
- [ ] bbox / 原文定位未实现

## Graph

- [x] PAG Pydantic schema 已实现
- [x] 规则版 graph builder 已实现
- [x] contribution 节点生成已实现基础版本
- [x] 邻近 evidence 节点挂载已实现基础版本
- [x] completeness validator 已实现基础版本
- [ ] LLM 版贡献抽取未实现
- [ ] LLM 版语义角色分类未实现
- [ ] LLM 版 evidence linking 未实现
- [ ] LLM 版关系判断未实现
- [ ] 图修正 patch 机制未接入 API/UI

## 存储

- [x] PostgreSQL + pgvector schema 已写好
- [x] Docker Compose 已包含 pgvector 数据库
- [ ] 后端 API 尚未读写数据库
- [ ] 当前 graph 只存放在进程内 `_PAPERS`
- [ ] 服务重启后数据不会保留
- [ ] graph store / repository 未实现

## 检索

- [x] 基础 lexical search 已实现
- [ ] embedding 生成未实现
- [ ] pgvector 查询未实现
- [ ] hybrid search 未实现
- [ ] graph retrieval 未实现

## 引用与递归

- [x] 引用意图规则分类器已实现基础版本
- [x] traversal policy 已实现基础版本
- [ ] 引用列表抽取未实现
- [ ] citation mention 抽取未实现
- [ ] 引用元数据解析未实现
- [ ] 引用论文递归分析未实现
- [ ] 递归分析缓存未实现

## LLM / 外部依赖

- [ ] OpenAI API 未接入
- [ ] Codex CLI 未接入实际调用
- [ ] `OPENAI_API_KEY` 等模型配置未定义
- [ ] embedding 模型配置未定义
- [ ] Crossref / Semantic Scholar / arXiv 等引用服务未接入
- [ ] PDF 解析依赖如 `pymupdf` / `pdfplumber` / GROBID 未接入

## 测试

- [x] graph builder 基础测试已存在
- [x] 前端 `npm run build` 已通过
- [ ] API 集成测试不足
- [ ] parser 测试不足
- [ ] 前端交互测试未实现

## 建议下一步

- [ ] 优先实现真实 PDF 文本解析
- [ ] 然后接入第一个 LLM 分析链路
- [ ] 再把 graph 持久化到 PostgreSQL
- [ ] 之后升级前端为真实图谱视图
