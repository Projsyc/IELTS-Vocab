# 06 开发日志

> 倒序排列，最新的在最上面。
> **记录约定**：每次开发告一段落追加一条。写清楚"做了什么"和"下次从哪继续"。

---

## 模板

```markdown
## YYYY-MM-DD 一句话标题

**做了什么**
- 

**遇到的问题**
- （详细的坑记到 07-bug-log.md，这里只留一句索引）

**下次从哪继续**
- 
```

---

## 2026-07-25 项目初始化 + 需求确定

**做了什么**

- 搭建 Monorepo 骨架
  - `pnpm-workspace.yaml` 声明 `apps/*` + `packages/*`
  - 根 `package.json` 提供统一命令（`dev` / `build` / `lint`）
- 前端脚手架：React 19 + Vite 8 + TypeScript，包名 `@ielts/frontend`
  - ✅ `pnpm -F @ielts/frontend build` 通过
- 后端脚手架：FastAPI + Python 3.13
  - `main.py` 含 CORS 配置和 `/api/health`
  - 依赖：SQLAlchemy 2.x / Alembic / asyncpg / python-jose / passlib
  - ✅ 应用能正常加载
- `packages/shared` 共享 TypeScript 类型（User / Word / Dictation / API 响应）
- 完成 4 轮 grilling session，确定全部核心需求
- 撰写文档：`docs/` 8 篇 + `learning-docs/` 若干

**遇到的问题**

- 🐛 **BUG-001**：系统默认 Python 是 `3.14.0b3` beta 版，pydantic 直接崩。改用 Homebrew Python 3.13，并写 `.python-version` 固定。详见 [07-bug-log.md](./07-bug-log.md)
- ⚠️ `grill-me` skill 指向的 `/grilling` 子命令在配置中不存在，按 skill 描述手动执行了访谈

**关键决策**（详见 [08-decisions.md](./08-decisions.md)）

| 决策 | 结论 |
|------|------|
| Monorepo 工具 | pnpm workspace（不上 Turborepo） |
| 进度同步架构 | **混合式事件溯源** —— `answer_events` 追加 + `progress` 缓存 |
| 进度粒度 | `(user, word, mode)` 三元组，两种模式进度独立 |
| 间隔重复算法 | Leitner 5 盒，1/2/4/7/15 天 |
| 听写判定 | 严格匹配 + 错误位置高亮 |
| 阅读干扰项 | 同雅思话题内随机抽 3 个 |
| UI 方案 | Tailwind + shadcn/ui |
| 登录 | 用户名密码（邀请制），预留微信 openid 字段 |
| 词库 | 4000 词，刘洪波词表选词范围 + 开源词典释义 |

**下次从哪继续**

→ **M1 数据地基**，第一件事是**词库数据源调研**（这是最大的未验证风险）：

1. 找 ECDICT 或其他开源词典，确认字段齐不齐（词形/释义/音标/词性）
2. 拿到刘洪波词表的选词范围（只要词形列表）
3. 两者取交集，验证覆盖率够不够 4000 词
4. 如果数据源不行，整个 M1 要重新规划 —— **所以先做这个**

然后：Tailwind + shadcn/ui 接入、PostgreSQL 本地环境、Alembic 初始化。

---
