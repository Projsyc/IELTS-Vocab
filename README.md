# IELTS Vocabulary App 🎯

> 雅思单词记忆应用 · 听写 + 认词双模式 · 间隔重复排期

参考[同桌英语背单词板块](https://ielts.itongzhuo.com/business/ielts/student/jumpAnswerWordDictation.do?sSubjects=0&leftType=1)，用 Monorepo 组织，Web 优先，后续扩展 Android 与微信小程序。

---

## 核心玩法

**听写模式** —— 播放发音，键盘拼写，严格判定并高亮错误位置

```
🔊 [播放]  →  请拼写：[ accomodate ]  →  ✗  a c c o m _ o d a t e
                                              ↑ 少了一个 m
```

**阅读模式** —— 展示单词，从同话题的 4 个中文释义里选

```
accommodate  /əˈkɒmədeɪt/
  1. 加速；促进      2. 容纳；提供住宿
  3. 积累；累积      4. 陪同；伴随
  [1] [2] [3] [4]        [空格 = 不知道]
```

错词按 **Leitner 5 盒子**（1/2/4/7/15 天）自动排期复习。两种模式进度独立计算。

---

## 技术栈

| | |
|---|---|
| **前端** | React 19 · Vite 8 · TypeScript · Tailwind + shadcn/ui |
| **后端** | FastAPI · Python 3.13 · SQLAlchemy 2.x · Alembic |
| **数据库** | PostgreSQL 15+ |
| **Monorepo** | pnpm workspace |

---

## 项目结构

```
IELTS-Vocab/
├── apps/frontend/      # Web 前端
├── apps/mobile/        # Android（规划中）
├── backend/            # FastAPI 后端
├── packages/shared/    # 前后端共享类型
├── docs/               # 项目文档
└── learning-docs/      # 学习笔记
```

---

## 快速开始

**环境要求**

- Node.js ≥ 18、pnpm ≥ 8
- Python **3.13**（⚠️ 3.14 beta 不兼容 pydantic）
- PostgreSQL ≥ 15

**安装**

```bash
# 前端
pnpm install

# 后端（注意用 3.13）
/opt/homebrew/bin/python3.13 -m venv backend/.venv
backend/.venv/bin/pip install -r backend/requirements.txt
```

**运行**

```bash
pnpm dev:frontend    # → http://localhost:5173
pnpm dev:backend     # → http://localhost:8000  （API 文档 /docs）
pnpm dev             # 两个一起
```

---

## 文档

| 入口 | 内容 |
|------|------|
| [`docs/`](./docs/) | 需求 · 架构 · 数据模型 · API · 路线图 · 开发日志 · BUG 日志 · 架构决策 |
| [`learning-docs/`](./learning-docs/) | 概念扫盲笔记（pnpm / Monorepo / Leitner / 事件溯源） |
| [`CLAUDE.md`](./CLAUDE.md) | 项目速览（给 AI 助手看） |

**新接触本项目**，建议按这个顺序读：

1. [产品需求](./docs/01-product-spec.md) —— 这东西是干嘛的
2. [技术架构](./docs/02-architecture.md) —— 代码怎么组织
3. [事件溯源笔记](./learning-docs/05-event-sourcing.md) ⭐ —— 动进度相关代码前必读
4. [数据模型](./docs/03-data-model.md) —— 表结构

---

## 当前进度

```
v0.1  项目初始化        ████████████ ✅
v1.0  MVP（本地可跑）    ░░░░░░░░░░░░ 进行中
v2.0  部署 + 自定义词库   ░░░░░░░░░░░░
v3.0  小程序 + Android   ░░░░░░░░░░░░
```

下一步见 [路线图](./docs/05-roadmap.md)。

---

## 说明

个人学习项目，非商业用途。词库选词参考公开词表，释义采用开源词典数据（如 ECDICT）。
