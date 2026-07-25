# IELTS Vocabulary App — CLAUDE.md

> 给 AI 助手的项目速览。详细文档见 [`docs/`](./docs/)

## 项目概述

仿[同桌英语背单词板块](https://ielts.itongzhuo.com/business/ielts/student/jumpAnswerWordDictation.do?sSubjects=0&leftType=1)的雅思单词记忆应用。Monorepo 架构，Web 优先，后续扩展 Android 和微信小程序。

**用户规模**：作者本人 + 小范围朋友（5–30 人）。不是商业产品。

## 核心玩法（两种模式）

| 模式 | 流程 | 判定 |
|------|------|------|
| **听写** `dictation` | 播音频 → 拼写 → 判对错 | 严格匹配（忽略大小写/首尾空格），错误位置高亮 |
| **阅读** `recognition` | 展示单词 → 4 选 1 中文释义 → 键盘 1/2/3/4 或空格="不知道" | 干扰项从**同雅思话题**内抽 3 个 |

错词通过 **Leitner 5 盒**（1/2/4/7/15 天）安排复习。答对升一箱，答错**直接回 Box 1**。

## 技术栈

| 层 | 选型 | 注意 |
|----|------|------|
| Monorepo | pnpm workspace | 只管 `apps/*` + `packages/*`，backend 不在内 |
| 前端 | React 19 + Vite 8 + TS | 样式用 Tailwind + shadcn/ui（待接入） |
| 后端 | FastAPI + **Python 3.13** | ⚠️ **不能用 3.14 beta**，pydantic 会崩（BUG-001） |
| 数据库 | PostgreSQL 15+ | SQLAlchemy 2.x async + Alembic |
| 认证 | JWT | 用户名密码，邀请制；预留微信 openid 字段 |

## 目录结构

```
IELTS-Vocab/
├── apps/frontend/      # React 前端（@ielts/frontend）
├── apps/mobile/        # Android（v3，空）
├── backend/            # FastAPI（Python，不在 pnpm workspace）
│   └── .venv/          #   用 /opt/homebrew/bin/python3.13 建的
├── packages/shared/    # 前后端共享 TS 类型（@ielts/shared）
├── docs/               # 项目文档（8 篇）
└── learning-docs/      # 概念扫盲笔记（写给初学者）
```

## ⭐ 最重要的架构约束

**混合式事件溯源**（详见 [docs/08-decisions.md ADR-002](./docs/08-decisions.md)）：

```
answer_events（只追加，事实来源）  ──回放──>  user_progress（缓存）
```

**铁律**：
1. `answer_events` **只 INSERT**，永不 UPDATE / DELETE
2. `user_progress` 的每一行必须能从事件**完整重算**出来 —— 不要往里写事件之外的信息
3. 回放按 `answered_at`（客户端时间）排序，**不是** `created_at` 或自增 ID
4. Leitner 转移必须是**纯函数**，才能重放

**进度主键是三元组 `(user_id, word_id, mode)`** —— 听写和阅读进度独立，因为"认得出"和"会拼写"是两种能力。

## 常用命令

```bash
pnpm install                # 前端依赖
pnpm dev:frontend           # 前端 :5173
pnpm dev:backend            # 后端 :8000
pnpm build                  # 构建全部
pnpm lint                   # lint 全部

backend/.venv/bin/pip install -r backend/requirements.txt   # 后端依赖
backend/.venv/bin/python -c "import main"                   # 验证后端能加载
```

## 当前状态

**v0.1 已完成**：骨架搭建 + 需求确定 + 文档
**下一步（M1）**：词库数据源调研 → 4000 词导入 → LLM 打话题标签 → TTS 生成音频

⚠️ **M1 的第一件事必须是词库数据源调研** —— 这是最大的未验证风险，卡住要尽早暴露。

## 开发约定

- 后端分层：`routers/`（只收参数）→ `services/`（业务逻辑）→ `models/`（ORM）
  - `services/` 里不 import FastAPI 的东西
  - 算法逻辑必须是纯函数，方便单测
- 改数据库表 → 同步改 `docs/03-data-model.md` + 写 Alembic 迁移 + 在 ADR 记原因
- 踩坑 → 记 `docs/07-bug-log.md`
- 开发告一段落 → 追加 `docs/06-dev-log.md`

## ⭐ Git 与文档工作流（每次都要遵守）

### 分支策略

```
main                    ← 只接受 merge，不直接开发
 ├── feat/xxx           ← 新功能
 ├── fix/xxx            ← 修 BUG
 └── docs/xxx           ← 纯文档改动
```

**特性功能一律开新分支开发，完成后 merge 回 main。**

```bash
git switch -c feat/leitner-algorithm     # 开分支
# ... 开发 ...
git switch main && git merge feat/leitner-algorithm
git push origin main
git branch -d feat/leitner-algorithm     # 删掉已合并分支
```

### 关键节点必做（缺一不可）

到达关键节点（功能完成 / 阶段结束 / 重要决策）时，**先更新文档再提交**：

| 更新对象 | 什么时候 |
|---------|---------|
| `CLAUDE.md` | 技术栈变化、新增架构约束、目录结构调整 |
| `docs/06-dev-log.md` | **每次**都追加一条（做了什么 + 下次从哪继续） |
| `docs/07-bug-log.md` | 踩到坑就记 |
| `docs/08-decisions.md` | 做了架构选择就记 ADR（含代价） |
| `docs/03-data-model.md` | 改了表结构 |
| `docs/05-roadmap.md` | 勾掉完成项、调整计划 |
| `learning-docs/` | 用到了新概念就写扫盲笔记 |

然后提交并推送**本地 + 远程**：

```bash
git add -A
git commit -m "feat: xxx"
git push origin main
```

### 提交信息规范（Conventional Commits）

```
feat:     新功能
fix:      修 BUG
docs:     文档
refactor: 重构
test:     测试
chore:    杂活（依赖、配置）
```

### 遇到问题或需要规划时

**主动调用 `grilling` skill 追问用户**，不要自己猜需求。

- `grilling` — ✅ AI 可自动调用。方法论：**一次只问一个问题**，等回答后再问下一个；
  能从环境查到的*事实*自己查，只把*决策*交给用户；未达成共识前不要动手实现
- `grill-me` — 用户手动触发器（`/grill-me`），AI 无法调用，内部就是启动 grilling

> 来源：[mattpocock/skills](https://github.com/mattpocock/skills)，已装 productivity + engineering 共 22 个到 `~/.claude/skills/`

### 远程仓库

```
https://github.com/Projsyc/IELTS-Vocab
```


## 已知风险

| 风险 | 应对 |
|------|------|
| 版权：《刘洪波雅思词汇真经》 | **只用选词范围**，释义走开源词典（ECDICT）。不复制书中释义/例句/编排 |
| LLM 话题标签质量 | M1 抽样人工校验 |
| 词库数据源找不到 | M1 优先验证 |
