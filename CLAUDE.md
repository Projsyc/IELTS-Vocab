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
├── docker-compose.yml  # PostgreSQL 17 容器
├── scripts/init-db.sql #   容器首次初始化（建 pgcrypto 扩展）
├── apps/frontend/      # React 前端（@ielts/frontend）
├── apps/mobile/        # Android（v3，空）
├── backend/            # FastAPI（Python，不在 pnpm workspace）
│   ├── .venv/          #   用 /opt/homebrew/bin/python3.13 建的
│   ├── .env            #   本地配置（不进 git，从 .env.example 复制）
│   ├── alembic/        #   数据库迁移
│   ├── data/           #   ⭐ 词库原始数据（见该目录 README）
│   │   ├── ecdict-ielts.csv  #     1.5MB 雅思子集，**进 git**，5,040 行
│   │   └── ecdict.csv        #     66MB 完整版，不进 git（构建缓存）
│   ├── app/
│   │   ├── core/       #     config.py（配置）/ database.py（连接与 Session）
│   │   ├── models/     #     SQLAlchemy ORM（5 张表）
│   │   ├── schemas/    #     Pydantic 请求/响应模型（待写）
│   │   ├── routers/    #     API 路由（待写）
│   │   ├── services/   #     业务逻辑（待写）
│   │   └── scripts/    #     ecdict.py（纯函数）/ seed_words.py / export_ielts_subset.py
│   ├── static/audio/   #   词库音频（seed 生成，~100MB，不进 git）
│   └── tests/          #   test_schema.py + test_ecdict.py（91 个测试）
├── packages/shared/    # 前后端共享 TS 类型（@ielts/shared）
├── docs/               # 项目文档（9 篇）
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

## 从零重建（fresh clone）

```bash
pnpm install
cp backend/.env.example backend/.env
backend/.venv/bin/pip install -r backend/requirements.txt   # 若 .venv 不存在见下
pnpm db:up          # 起 PostgreSQL 容器
pnpm db:migrate     # 建表
pnpm seed           # 导入词库（读仓库内的 1.5MB 子集，离线可跑）
```

venv 不存在时：`/opt/homebrew/bin/python3.13 -m venv backend/.venv`

**数据库和音频都是产物，不是真相来源** —— 源数据在 `backend/data/ecdict-ielts.csv`（进 git），
其余都能由脚本重建。详见 [`backend/data/README.md`](./backend/data/README.md)。

## 常用命令

```bash
pnpm install                # 前端依赖
pnpm dev:frontend           # 前端 :5173
pnpm dev:backend            # 后端 :8000

# —— 数据库（Docker）——
pnpm db:up                  # 起 PostgreSQL 容器
pnpm db:down                # 停容器（保留数据）
pnpm db:reset               # ⚠️ 删数据卷重建
pnpm db:migrate             # alembic upgrade head
pnpm db:shell               # 进 psql

# —— 词库 ——
pnpm seed                   # 导入词库（可断点续跑，中断了重跑即可）
pnpm seed:status            # 只看进度，不改数据
pnpm tag                    # LLM 打话题标签（需 .env 里的 LLM_API_KEY）
pnpm tag:status             # 看话题分布
pnpm tag:review             # 抽样人工校验标签质量

# —— 账号（邀请制，无注册接口）——
pnpm user:create alice      # 建账号（密码交互式输入）
pnpm user:list              # 列出账号
# 改密码：cd backend && .venv/bin/python -m app.scripts.manage_users passwd alice

pnpm test:backend           # 后端测试（需数据库在跑）
pnpm build / pnpm lint      # 构建 / lint 全部

backend/.venv/bin/pip install -r backend/requirements.txt   # 后端依赖
```

⚠️ **端口 5432 冲突**：装过 Postgres.app 的话它会抢 5432，导致连错库报
`role "ielts" does not exist`。停它：

```bash
/Applications/Postgres.app/Contents/Versions/17/bin/pg_ctl \
  -D "$HOME/Library/Application Support/Postgres/var-17" stop
```

## 数据库开发要点

- 改模型后生成迁移：`cd backend && .venv/bin/alembic revision --autogenerate -m "说明"`
- **autogenerate 的产物是草稿不是成品** —— 提交前必须读一遍
- ⚠️ **每个迁移都要测往返**：`upgrade → downgrade → upgrade`。只测 upgrade
  会漏掉 ENUM 残留这类问题（已踩，见 BUG-004）
- 新模型必须在 `app/models/__init__.py` 里 import，否则 autogenerate 会生成"删表"迁移
- 结构性不变式（三元组主键 / CHECK 约束 / 索引列序）已被 `backend/tests/test_schema.py` 锁住

## 当前状态

**✅ M1 完成** —— 词库数据全部就位

| 内容 | 状态 |
|------|------|
| 词库 | **4,768 词**（ECDICT ielts 子集，剔除 272 个冗余屈折形式） |
| 音频 | **100%**（75.6% 真人 + 24.4% edge-tts，84.7MB，全本地） |
| 音标 | **99.2%**（dictionaryapi.dev 真 IPA + 清洗版 ECDICT 兜底） |
| 词性 | **99.6%**（从释义前缀解析） |
| 话题 | **100%**（LLM 打标，21 类，已人工验收） |
| 数据库 | 5 表 + 7 索引，迁移往返已验证 |
| 算法 | Leitner + 回放 + 听写判定 + 干扰项生成（全纯函数） |
| 接口 | 认证 / 词库 / 练习，共 8 个 |
| 测试 | **409 个**（含 doctest） |

### 词库方案（已定案）

| 内容 | 来源 |
|------|------|
| 词表 + 释义 | [ECDICT](https://github.com/skywind3000/ECDICT)（**MIT**）`tag` 含 `ielts` |
| 音标 | dictionaryapi.dev 真 IPA，缺的用清洗版 ECDICT |
| 音频 | dictionaryapi.dev 真人 + edge-tts 补齐，全部下载本地 |
| 话题 | DeepSeek `deepseek-v4-flash` 打标，20 话题 + `通用/抽象` 兜底 |

⚠️ **三个已踩的坑，改相关代码前必读**：

1. **ECDICT 音标不是标准 IPA**（简化记音 + 混入西里尔 `ә`），规则转换不可行 —— 已实测 0/8 一致。所以音标优先从 API 取。
2. **`.` 在两个数据源里含义相反** —— ECDICT 里是**次重音**（→ `ˌ`），API 的 IPA 里是**音节分隔**（→ 删掉）。别把 `clean_ecdict_phonetic` 和 `normalize_api_phonetic` "统一"了，已有测试钉住。
3. **干扰项必须同时按 `topic` + `part_of_speech` 过滤** —— 释义自带词性前缀（`n.` / `vt.`），只按话题过滤会让用户靠数前缀排除答案。见 [docs/03 §5](./docs/03-data-model.md)。

## 下一步：M2 后端核心

**已完成**：

| 模块 | 内容 | 测试 |
|------|------|------|
| `services/leitner.py` | Leitner 状态机，全纯函数 | 45 |
| `services/replay.py` | 事件回放（乱序一致、增量==全量） | 23 |
| `services/dictation.py` | 听写判定 + Levenshtein 错误高亮 | 50 |
| `services/distractor.py` | 干扰项生成（降级链 + 防前缀泄露） | 36 |
| `services/practice.py` | 挑词、出题、答题落库 | — |
| `core/security.py` | bcrypt 哈希 + JWT | 30 |
| `routers/auth.py` | login / me | 23 |
| `routers/words.py` + `practice.py` | 词库、每日任务、自由练习、答题 | 29 |
| `scripts/manage_users.py` | 邀请制手动开号 | — |

**M2 只剩**：进度接口（summary / wrong-words / rebuild）

## ⭐ 实现中修正的两处设计

1. **阅读模式回传选中文本，不是选项 index** —— 题目是无状态生成的，
   服务端不保存"第几个对"，回传 index 无从验证。文本方案完全无状态且不泄露。
2. **干扰项一律剥掉词性前缀** —— 释义自带 `n.` / `vt.` 前缀，
   降级到混词性时用户数前缀就能排除答案。

## ⚠️ async engine 与事件循环（这个坑踩了三次）

**async engine 的连接池绑定在创建它的事件循环上。** 三条规则：

1. 测试里的 engine 必须是 fixture，每个测试新建并 `dispose`
2. `dispose` 必须 `await` 在建立连接的那个循环里 —— 别用第二个 `asyncio.run()` 清理第一个的资源
3. **HTTP 测试必须覆盖 `get_db` 依赖**，否则 app 会用模块级 engine

生产代码不受影响（一个进程一个循环）。详见 [BUG-005/006/008](./docs/07-bug-log.md)。

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
| ~~版权：《刘洪波雅思词汇真经》~~ | ✅ **已解除** —— 改用 ECDICT（MIT）自带 `ielts` 标签 |
| LLM 话题标签质量 | 打标后抽样人工校验 |
| 音频许可（dictionaryapi.dev / edge-tts） | 本地自用可接受；**v2 部署前必须复核**，退路是浏览器 TTS |
| seed 75 分钟流程中断 | 脚本必须断点续跑 + 失败重试 + 限速 |
