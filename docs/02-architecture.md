# 02 技术架构

> 版本：v0.1 · 最后更新：2026-07-25

---

## 1. 技术栈总览

| 层次 | 选型 | 版本 | 备注 |
|------|------|------|------|
| **Monorepo** | pnpm workspace | pnpm 11.x | 零配置，后续可升 Turborepo |
| **前端框架** | React + Vite | React 19 / Vite 8 | TypeScript |
| **前端样式** | Tailwind CSS + shadcn/ui | — | 组件可复制可改，不是黑盒依赖 |
| **后端框架** | FastAPI | 0.140+ | Python **3.13**（3.14 beta 不兼容，见 BUG-001） |
| **ORM** | SQLAlchemy 2.x | 2.0+ | async 模式 |
| **数据库迁移** | Alembic | 1.18+ | |
| **数据库** | PostgreSQL | 15+ | |
| **认证** | JWT（python-jose） | — | 密码哈希用 passlib/bcrypt |
| **音频合成** | Edge-TTS（预生成） | — | 用户上传词降级浏览器 TTS |

---

## 2. 目录结构

```
IELTS-Vocab/
├── package.json              # 根配置：统一命令入口
├── pnpm-workspace.yaml       # 声明 workspace 范围
├── CLAUDE.md                 # AI 助手项目速览
│
├── apps/
│   ├── frontend/             # ✅ Web 前端（React + Vite）
│   │   ├── src/
│   │   │   ├── components/   #   UI 组件
│   │   │   ├── pages/        #   页面
│   │   │   ├── hooks/        #   自定义 hooks
│   │   │   ├── api/          #   后端接口封装
│   │   │   └── lib/          #   工具函数
│   │   └── package.json      #   name: @ielts/frontend
│   │
│   ├── mobile/               # 🔜 Android（v3）
│   └── miniapp/              # 🔜 微信小程序（v3）
│
├── backend/                  # ✅ FastAPI 后端（不在 pnpm workspace 内）
│   ├── main.py               #   应用入口
│   ├── .venv/                #   Python 虚拟环境
│   ├── .python-version       #   固定 3.13
│   ├── requirements.txt
│   ├── app/                  #   🔜 待建
│   │   ├── models/           #     SQLAlchemy 数据模型
│   │   ├── schemas/          #     Pydantic 请求/响应模型
│   │   ├── routers/          #     API 路由
│   │   ├── services/         #     业务逻辑（Leitner 算法等）
│   │   ├── core/             #     配置、安全、数据库连接
│   │   └── scripts/          #     词库导入、LLM 打标、TTS 生成
│   └── alembic/              #   🔜 数据库迁移脚本
│
├── packages/
│   └── shared/               # ✅ 前后端共享 TS 类型
│       └── src/index.ts
│
├── docs/                     # ✅ 项目文档（你在这）
└── learning-docs/            # ✅ 学习笔记
```

> **注意**：`backend/` 是 Python 项目，**不受 pnpm workspace 管理**。
> pnpm 只管 `apps/*` 和 `packages/*`（见 `pnpm-workspace.yaml`）。
> 但它们在同一个 git 仓库，仍享受 Monorepo 的好处（原子提交、共享文档）。

---

## 3. 分层架构

### 3.1 后端分层

```
HTTP 请求
   ↓
[routers/]      路由层 —— 只负责收参数、调 service、返响应
   ↓
[services/]     业务层 —— Leitner 算法、干扰项生成、事件回放
   ↓
[models/]       数据层 —— SQLAlchemy ORM
   ↓
PostgreSQL
```

**规矩**：
- `routers/` 里不写业务逻辑，只做参数校验和调用
- `services/` 里不碰 HTTP 概念（不 import FastAPI 的东西）
- 算法逻辑（Leitner 升降箱）必须是**纯函数**，方便单测

### 3.2 前端分层

```
[pages/]        页面级组件，负责组装和路由
   ↓
[components/]   可复用 UI 组件（无业务逻辑）
   ↓
[hooks/]        状态与副作用（useDictation、useProgress…）
   ↓
[api/]          后端接口封装（统一错误处理、鉴权头）
```

---

## 4. 关键架构决策

### 4.1 混合式事件溯源

这是本项目最重要的架构选择，影响所有和"进度"相关的代码。

```
┌───────────────────────────────────────────────────────┐
│  写入路径（用户答题）                                    │
│                                                       │
│  POST /api/answer                                     │
│      ↓                                                │
│  1. 追加一条 answer_events（不可变，永不删改）           │
│      ↓                                                │
│  2. 增量更新 progress 缓存（算新盒子号和下次复习日）      │
└───────────────────────────────────────────────────────┘

┌───────────────────────────────────────────────────────┐
│  读取路径（查今日任务）                                  │
│                                                       │
│  GET /api/session/daily                               │
│      ↓                                                │
│  直接查 progress 表（快，一次索引查询）                  │
└───────────────────────────────────────────────────────┘

┌───────────────────────────────────────────────────────┐
│  修复路径（多端冲突 / 算法改了 / 数据疑似出错）           │
│                                                       │
│  按 user_id 拉出全部 answer_events，按时间戳排序         │
│      ↓                                                │
│  纯函数重放 Leitner 状态机                              │
│      ↓                                                │
│  覆盖写回 progress 表                                  │
└───────────────────────────────────────────────────────┘
```

**核心不变式**：`progress` 表的任何一行，都必须能由 `answer_events` 完整重算出来。
一旦某个功能往 `progress` 里写了事件之外的信息，这个设计就破了。

详见 [08-decisions.md#adr-002](./08-decisions.md)。

### 4.2 共享类型

`packages/shared/src/index.ts` 定义前后端共用的 TypeScript 类型。

**局限**：后端是 Python，**吃不到这些 TS 类型**。两边需要人工保持一致。

**后续可选方案**（v2 考虑）：
- FastAPI 自动生成 OpenAPI schema → 用 `openapi-typescript` 生成前端类型
- 这样类型就是从后端单向流出，不会不一致

---

## 5. 环境与命令

### 5.1 环境要求

| 工具 | 版本 | 检查命令 |
|------|------|----------|
| Node.js | ≥ 18 | `node -v` |
| pnpm | ≥ 8 | `pnpm -v` |
| Python | **3.13**（不能用 3.14 beta） | `backend/.venv/bin/python -V` |
| PostgreSQL | ≥ 15 | `psql --version` |

### 5.2 常用命令

```bash
# —— 安装 ——
pnpm install                              # 前端依赖
backend/.venv/bin/pip install -r backend/requirements.txt   # 后端依赖

# —— 开发 ——
pnpm dev:frontend                         # 前端 dev server（:5173）
pnpm dev:backend                          # 后端 dev server（:8000）
pnpm dev                                  # 同时启动（并行）

# —— 构建与检查 ——
pnpm build                                # 构建所有 workspace
pnpm lint                                 # lint 所有 workspace

# —— 数据库（待建） ——
# alembic upgrade head                    # 应用迁移
# python -m app.scripts.seed_words        # 导入词库
```

---

## 6. 端口约定

| 服务 | 端口 |
|------|------|
| 前端 dev server | 5173 |
| 后端 API | 8000 |
| PostgreSQL | 5432 |

后端 CORS 已放行 `http://localhost:5173`（见 `backend/main.py`）。

---

**相关文档**：[03 数据模型](./03-data-model.md) · [04 API 设计](./04-api-design.md) · [08 架构决策](./08-decisions.md)
