# UAP CLI 与 Electron 一体化设计文档

## 1. 文档目的

本文档用于重新定义 `uap` CLI 与打包版 Electron App 的产品定位、服务生命周期、数据模型和交互边界。

本设计只面向一类用户：

- 希望“安装完即可使用”的终端用户

这类用户：

- 不关心 `devbox`
- 不希望安装 Python、Postgres、Node 等额外依赖
- 期望 `uap` 和 Electron App 都可以直接工作
- 期望 CLI 和 Electron App 使用同一份本机数据

因此，本文档明确取消 `uap dev *` 这一类开发栈命令，不再把 CLI 设计成开发运维入口。

---

## 2. 设计目标

### 2.1 用户目标

用户需要两种使用入口：

1. 使用 CLI：
   安装 `uap` 后，直接运行 `uap paper upload xxx.pdf`、`uap graph show ...` 等命令即可使用。
2. 使用 Electron App：
   打开 App 后即可使用；如果本地后端未启动，App 可以自动启动。

### 2.2 系统目标

系统需要满足以下要求：

1. CLI 与 Electron App 共享同一个本机后端服务。
2. CLI 与 Electron App 共享同一个本机数据源。
3. 后端服务可以显式启动和停止。
4. 如果用户直接执行内容命令而服务未启动，系统可以自动拉起服务。
5. 终端用户不需要感知 Python、`uv`、`devbox`、Postgres。
6. 打包产物内部可以继续使用 PyInstaller 后端和 Go launcher，但这些实现细节不暴露给用户。
7. 用户不需要一上来手动挑选数据库或目录，但也应保留显式初始化与自定义 workspace 的能力。

---

## 3. 非目标

以下内容不属于本阶段目标：

1. 面向开发者的 `devbox/server/web/db/phoenix` 生命周期管理
2. 本地多用户协作
3. 分布式部署
4. 用桌面模式替代项目开发模式
5. 复杂服务编排器

如果未来还需要开发者模式，应单独设计，不混入终端用户 CLI。

---

## 4. 核心定位

重新定位后，`uap` 的本质是：

> 一个面向终端用户的“本地论文分析客户端入口”，它既可以作为命令行工具使用，也可以作为 Electron App 的本地服务控制层使用。

其中：

- CLI 是用户入口之一
- Electron App 是用户入口之二
- 本机后端服务是共享运行时
- 本机数据存储是共享状态源

也就是说：

- Electron 不应该再拥有一套独立的“桌面后端生命周期”
- CLI 也不应该再分裂出“开发服务栈生命周期”
- 两者都应该围绕同一个本地服务模型工作

---

## 5. 总体架构

建议的总体架构如下：

```text
┌──────────────────────┐
│      CLI: uap        │
│ paper / graph / ...  │
└──────────┬───────────┘
           │
           │ HTTP
           │
┌──────────▼───────────┐
│  Local Backend API   │  127.0.0.1:<fixed-port>
│ FastAPI + GraphStore │
└──────────┬───────────┘
           │
           │ shared persistent local store
           │
┌──────────▼───────────┐
│   Local Data Store   │
│ graph/meta/docs      │
└──────────────────────┘

┌──────────────────────┐
│  Electron App UI     │
│ tray + window        │
└──────────┬───────────┘
           │
           └──── uses the same local backend
```

实现层面可以继续保持：

- Go launcher：负责“启动/检查/停止本地后端”
- Python/PyInstaller backend：负责实际 API、解析与图谱逻辑
- Electron：负责桌面交互、托盘、服务控制 UI

但对用户暴露出来的产品模型必须是“一套本地服务”。

---

## 6. Workspace 与初始化设计

## 6.1 为什么需要 workspace

如果采用 SQLite + 本地文件目录的方式，就不能只把“数据库文件路径”当成一个孤立配置项。

更合适的抽象是：

> workspace = 一个完整的本地工作空间，里面包含数据库、文档、缓存和本地设置。

这样做有几个好处：

1. CLI 与 Electron 更容易共享同一份状态
2. 用户可以整体备份、迁移、同步 workspace
3. 我们可以围绕 workspace 设计初始化、切换和恢复逻辑
4. SQLite、documents、cache、settings 都有统一归属

## 6.2 workspace 目录结构

推荐目录结构：

```text
<workspace-root>/
├── uap.sqlite
├── documents/
├── cache/
├── logs/
└── settings.json
```

其中：

- `uap.sqlite`：主数据库
- `documents/`：原始 PDF、导出资源、渲染缓存等
- `cache/`：可再生缓存
- `logs/`：本地诊断日志
- `settings.json`：workspace 级别设置

此外，还需要一个全局用户配置文件，用于记录“默认 workspace”：

```text
<user-config-dir>/
└── uap/
    └── config.json
```

这个全局配置至少包含：

- 默认 workspace 路径
- 最近打开的 workspace 列表
- 初始化状态

## 6.3 默认策略：自动初始化

对于普通用户，不应强制他们先理解 workspace。

因此推荐默认策略为：

1. 首次打开 Electron 时，如果尚未初始化，则自动创建默认 workspace
2. 首次执行 `uap paper *` / `uap graph *` 时，如果尚未初始化，则自动创建默认 workspace
3. 默认 workspace 放在系统 app data 目录下，而不是当前 shell 工作目录

例如：

- macOS：`~/Library/Application Support/Understand Anypaper/workspaces/default`
- Windows：`%AppData%/Understand Anypaper/workspaces/default`

这样普通用户可以零配置上手。

## 6.4 显式初始化：`uap init`

除了自动初始化，还应提供显式初始化命令：

```bash
uap init
uap init --path /path/to/workspace
```

`uap init` 的职责：

1. 创建 workspace 目录结构
2. 初始化 SQLite 数据库
3. 写入默认 `settings.json`
4. 更新全局配置中的默认 workspace
5. 支持“连接已有 workspace”而不是重复创建

建议交互模型：

- 无参数执行：初始化默认 workspace
- `--path`：在指定路径创建或接管 workspace
- 如果指定路径已存在并且看起来像有效 workspace，则提示“使用已有 workspace”

## 6.5 Electron 图形化初始化

Electron 需要提供与 `uap init` 等价的图形化入口。

建议有两种触发方式：

1. 首次启动向导
2. 设置页中的“初始化 / 切换 workspace”

图形化初始化应支持：

1. 使用默认 workspace
2. 选择自定义目录
3. 打开已有 workspace
4. 将当前 workspace 设为默认 workspace

原则上：

- Electron UI 只是初始化入口的图形化表达
- 底层逻辑应与 CLI 共用

也就是说，Electron 不应自己再维护一套独立的 workspace 初始化逻辑。

## 6.6 初始化与后端启动的关系

后端服务启动前必须知道自己使用哪个 workspace。

因此推荐顺序是：

1. 解析当前默认 workspace
2. 若不存在，则自动初始化默认 workspace
3. 再启动本地后端服务
4. 后端拿到 workspace 路径后，使用其中的 SQLite 和 documents 目录

换句话说：

- `ensure backend running`
- 隐含依赖于
- `ensure workspace initialized`

---

## 7. 生命周期设计

## 7.1 本地后端服务生命周期

本地后端服务是整个系统的核心共享资源。

它的生命周期规则如下：

### 启动条件

本地后端服务可以通过以下方式启动：

1. 用户执行 `uap service start`
2. 用户打开 Electron App 并点击“启动服务”
3. Electron App 发现用户进入需要后端的页面且服务未启动，自动启动
4. 用户执行 `uap paper *` / `uap graph *` / `uap node *` 等内容命令，CLI 检测到服务未启动后自动启动

### 运行状态

服务启动后：

1. 固定监听本机地址，例如 `127.0.0.1:8765`
2. 使用固定本地数据目录
3. CLI 与 Electron 都连接到这个地址
4. 重复启动请求不会再拉起第二个服务实例

### 停止条件

本地后端服务可以通过以下方式停止：

1. 用户执行 `uap service stop`
2. 用户在 Electron UI 中点击“停止服务”
3. 用户在 tray/menu bar 菜单中点击“停止服务”

### 非停止条件

以下事件不应导致服务停止：

1. Electron 主窗口关闭
2. Electron 主窗口隐藏到 tray
3. CLI 命令执行结束

也就是说，CLI 命令不拥有服务进程的生命周期；它们只消费该服务。

---

## 7.2 Electron 生命周期

Electron 的职责应该从“持有后端进程”改为“服务控制器 + 可视化客户端”。

### Electron 启动时

Electron 启动后做以下事情：

1. 创建主窗口
2. 创建 tray/menu bar 图标
3. 检查本地后端服务是否已运行
4. 如果已运行，直接连接
5. 如果未运行，根据产品策略：
   - 自动启动，或
   - 展示“启动服务”按钮，让用户手动启动

建议：

- 打开 App 时自动检查服务
- 若服务不存在，则自动启动
- 若启动失败，则给出明确错误提示和重试入口

### Electron 关闭窗口时

关闭主窗口时：

- 只隐藏窗口
- 不退出 Electron 常驻进程
- 不停止本地后端服务

### Electron 真正退出时

用户从 tray 菜单点击“退出应用”时：

- 可以只退出 UI 壳层
- 不必强制停止后端服务

是否“退出应用时顺带停止服务”，建议作为可配置策略，而不是默认行为。

推荐默认行为：

- 退出 Electron 不自动停服务

原因：

- CLI 可能还在使用服务
- 用户可能只是关闭 UI，但仍希望后端保持热启动状态

---

## 7.3 CLI 生命周期

CLI 应被设计成“无状态客户端 + 服务控制入口”。

CLI 命令分为两类：

1. 服务控制命令
2. 内容操作命令

### 服务控制命令

- `uap service start`
- `uap service stop`
- `uap service status`

这些命令直接控制或查询本地后端服务生命周期。

### 内容操作命令

- `uap paper upload`
- `uap paper list`
- `uap paper show`
- `uap graph show`
- `uap graph search`
- `uap node evidence`

这些命令的行为应该是：

1. 先检测本地服务是否在线
2. 如果在线，则直接请求
3. 如果不在线，则自动执行与 `uap service start` 等价的启动流程
4. 等待健康检查通过后继续执行命令

因此，终端用户最常见的使用方式可以是：

```bash
uap paper upload ./paper.pdf
```

而不是：

```bash
uap service start
uap paper upload ./paper.pdf
```

后者仍然可用，但不是用户必须理解的前置步骤。

---

## 8. 命令设计

## 8.1 对外命令面

建议保留并聚焦以下命令：

### 服务命令

```bash
uap init
uap service start
uap service stop
uap service status
```

### 论文命令

```bash
uap paper upload <file>
uap paper list
uap paper show <paper-id>
uap paper delete <paper-id>
```

### 图谱命令

```bash
uap graph show <paper-id>
uap graph search <paper-id> <query>
```

### 节点命令

```bash
uap node evidence <paper-id> <node-id>
```

### 内部桌面命令

```bash
uap desktop run-backend
```

该命令保留为内部或高级命令，不作为普通用户主路径宣传。

---

## 8.2 命令语义约束

### `uap init`

语义：

- 初始化或接管一个本地 workspace

要求：

1. 默认创建或确认默认 workspace
2. 支持用户指定路径
3. 支持复用已有 workspace
4. 执行完成后更新全局默认 workspace 配置
5. 不要求用户手动理解 SQLite 文件名和目录细节

### `uap service start`

语义：

- 启动“唯一的本机共享后端服务”

要求：

1. 如果服务已运行，不重复启动
2. 如果服务未运行，拉起它
3. 返回明确状态：
   - 已启动
   - 已经在运行
   - 启动失败

### `uap service stop`

语义：

- 停止“唯一的本机共享后端服务”

要求：

1. 如果服务不存在，返回“未运行”
2. 如果服务存在，正常终止
3. 不删除用户数据

### `uap service status`

语义：

- 查询本机共享后端服务状态

要求：

至少返回：

- 是否运行
- 服务地址
- 健康检查结果
- 使用的数据目录
- 使用的存储模式

### `uap paper *` / `graph *` / `node *`

语义：

- 所有内容命令都依赖本地后端

要求：

1. 自动检查服务
2. 服务不存在时自动拉起
3. 启动成功后继续执行
4. 启动失败时输出可操作错误

---

## 9. 数据共享设计

## 9.1 共享原则

CLI 与 Electron App 必须共享：

1. 论文列表
2. 已构建图谱
3. semantic units
4. source document
5. 手工 patch
6. 相关本地设置

换句话说：

- CLI 上传一篇论文后，Electron 里应立刻可见
- Electron 中分析一篇论文后，CLI `uap paper list` 应立刻可见

---

## 9.2 当前实现的关键问题

当前桌面后端默认：

```text
DATABASE_URL=memory
```

这会让 `create_graph_store()` 返回 `InMemoryGraphStore`。

这意味着：

1. 数据仅存在当前进程内存中
2. 服务停止后数据丢失
3. CLI 和 Electron 只有在“连接同一个仍然存活的进程”时才能共享数据
4. 任何重启都破坏“直接使用用户”的预期

因此，当前 `memory` 模式不能作为最终设计。

---

## 9.3 目标数据模型

面向直接使用用户，建议采用：

> 单机持久化、本地零依赖的嵌入式存储

可选方案：

1. SQLite
2. DuckDB
3. 本地文件 + JSON/二进制索引

推荐优先级：

1. SQLite 作为主存储
2. 文档二进制（PDF、页面缓存等）存文件系统
3. 如果向量检索仍需要特殊支持，可在 SQLite 之外增加本地索引文件

要求：

1. CLI 与 Electron 都指向同一个 workspace
2. 打包后端默认使用默认 workspace
3. 服务重启后数据仍存在
4. workspace 可被显式初始化、迁移和重连

---

## 10. 服务发现与自动拉起

## 10.1 固定端点

为简化设计，建议本地服务使用固定端点：

```text
127.0.0.1:8765
```

好处：

1. CLI 无需复杂服务发现
2. Electron 无需复杂 IPC 协调
3. 状态判断直接做健康检查即可

---

## 10.2 自动拉起流程

当内容命令执行时：

1. 先请求 `GET /health`
2. 如果成功，继续执行原命令
3. 如果失败，进入自动拉起流程
4. 自动拉起流程调用共享启动入口
5. 等待健康检查通过
6. 重试原命令

伪流程：

```text
uap paper upload x.pdf
  -> check backend health
  -> not running
  -> start backend
  -> wait healthy
  -> upload paper
```

Electron 也应走同一逻辑，而不是维护另一套“只在 Electron 里存在”的启动实现。

在正式流程上，这里还要加一层：

```text
uap paper upload x.pdf
  -> ensure workspace initialized
  -> check backend health
  -> not running
  -> start backend
  -> wait healthy
  -> upload paper
```

---

## 10.3 单实例约束

同一台机器上，直接使用模式默认只允许一套本地后端服务实例。

要求：

1. 如果服务已在运行，新的 `service start` 不重复启动
2. 如果 Electron 启动时服务已存在，直接复用
3. 如果 CLI 内容命令发现服务已存在，直接复用

可选实现：

1. 固定端口健康检查
2. PID 文件
3. 本地锁文件

推荐：

- 健康检查 + PID 文件组合

---

## 11. Electron 与 CLI 的关系

## 11.1 一致性原则

Electron 与 CLI 不应是两个产品。

它们是：

- 同一个本地系统的两个入口

因此必须满足：

1. 命令语义一致
2. 服务生命周期一致
3. 数据一致
4. 错误模型一致

---

## 11.2 Electron 应如何复用 CLI 逻辑

推荐方式：

- Electron 不直接实现“服务管理业务逻辑”
- Electron 通过调用与 CLI 相同的 Go launcher / service manager 完成启动、停止、状态检查

具体来说：

1. Electron 点击“启动服务”
   - 等价于 `uap service start`
2. Electron 点击“停止服务”
   - 等价于 `uap service stop`
3. Electron 显示服务状态
   - 等价于 `uap service status`

这可以避免：

- Electron 一套逻辑
- CLI 一套逻辑
- 两者逐渐漂移

---

## 12. 建议的实现分层

建议分成四层。

在此基础上增加一个前置层：

### 第 0 层：workspace 管理层

负责：

- workspace 初始化
- 默认 workspace 解析
- workspace 元数据读写
- 全局配置读写
- workspace 目录校验

这层是所有“服务可启动”前的前置依赖。

### 第 1 层：服务控制层

负责：

- start
- stop
- status
- ensure running
- PID / lock / health check

这是 CLI 和 Electron 共用的核心层。

### 第 2 层：后端启动层

负责：

- 解析当前是否有 packaged backend
- 选择后端可执行文件
- 拉起 FastAPI backend

这层可以继续由 Go launcher 完成。

### 第 3 层：内容命令层

负责：

- paper/graph/node 等 API 调用
- 自动确保服务已启动

### 第 4 层：UI 层

负责：

- Electron 窗口
- 托盘
- 服务启动/停止按钮
- 状态反馈

---

## 13. 风险与取舍

## 12.1 当前最大风险

最大风险不是“谁来拉起服务”，而是“共享数据是否真的持久化”。

如果还使用 `memory`：

- 生命周期设计再漂亮也不成立

因此，本地持久化 store 是第一优先级。

## 12.2 常驻服务的资源占用

本地后端常驻会带来：

- 内存占用
- 端口占用
- 后台进程感知

这是合理取舍，因为它换来：

- 更快的二次打开速度
- CLI 和 Electron 的统一状态

可以后续增加：

- 空闲自动休眠
- 自动停止配置

但不建议第一版就做复杂。

## 12.3 “退出 Electron 是否停止服务”

这是一个典型策略问题。

本设计建议：

- 默认不停止

理由：

1. CLI 可能正在使用
2. 用户可能只是关 UI
3. “共享本地服务”语义更自然

如果用户强烈希望退出时停服务，可以后续加配置项。

---

## 14. 迁移建议

建议按以下顺序迁移。

### 阶段 1：workspace 模型落地

目标：

- 确立默认 workspace 与全局配置
- 设计 `uap init`
- 补齐 Electron 图形化初始化入口

### 阶段 2：命令语义重构

目标：

- 把 `uap service *` 重新定义为本地共享服务生命周期

### 阶段 3：本地持久化存储

目标：

- 用零依赖本地持久化 store 替换 `memory`
- 确保 CLI 与 Electron 可跨重启共享数据

### 阶段 4：自动拉起

目标：

- 为所有内容命令增加 `ensure running`
- Electron 首次进入时自动连接或自动启动

### 阶段 5：Electron 服务控制 UI

目标：

- 加启动服务 / 停止服务 / 服务状态
- 托盘菜单直接接服务控制

### 阶段 6：打包收口

目标：

- 打包后只暴露“可直接使用”的行为
- 不要求用户理解 Python、uv、devbox、Postgres

---

## 15. 最终建议

最终建议可以概括成一句话：

> `uap` 和打包版 Electron App 都应被视为“同一个本地论文分析系统”的两个入口，它们共享同一个本地后端服务与同一个本地持久化数据源；`uap service *` 定义这套本地服务的生命周期，而所有内容命令和 Electron UI 都应基于自动确保服务可用这一前提来工作。

---

## 16. 审核问题

为了便于审核，建议重点确认以下问题：

1. `uap service *` 是否彻底转为“终端用户本地服务生命周期”？
2. 是否彻底删除 `uap dev *` / 开发栈相关语义？
3. Electron 是否只作为共享服务的客户端与控制器？
4. “退出 Electron 不自动停服务”是否符合预期？
5. 本地持久化 store 是否接受以 SQLite 为优先方案？
6. workspace 是否以“自动初始化 + `uap init` / 图形化初始化”组合模式推进？
7. CLI 内容命令自动拉起服务是否作为默认行为？
