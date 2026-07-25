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

## 2026-07-25 (2) M1 词库数据源调研完成 —— 最大风险解除

**做了什么**

- 完成 [09 词库数据源调研](./09-wordlist-research.md)，所有结论均为**实测**而非查文档
- **关键发现**：[ECDICT](https://github.com/skywind3000/ECDICT)（MIT，77 万词条）的 `tag` 字段自带考试大纲标注，`ielts` 标签覆盖 **5,040 词**
  → 直接替代刘洪波词表，**版权风险归零**
- 实测 ielts 子集字段完整度：释义 100%、音标 95.1%、词频 100%、词性字段全空但 99.5% 可从释义前缀解析、音频/话题字段不存在
- 实测 dictionaryapi.dev：词条 96%、音频 70–76%、IPA 96%、中位延迟 **760ms**
- 实测 edge-tts 可用（英音/美音合成正常）
- 通过 `grilling` skill 逐个确认 5 项决策，写成 [ADR-007~009](./08-decisions.md)

**遇到的问题**

- ⚠️ **原定"把 ECDICT 音标规范化成标准 IPA"经实测不可行** —— 规则转换后 0/8 与标准 IPA 一致。
  ECDICT 用简化记音体系（`i` vs `ɪ`、`ei` vs `eɪ`、`әu` vs `əʊ`），单个 `i` 无法从字符判断该转成什么。
  已向用户报告并改路线：音标改从 dictionaryapi.dev 取（96% 覆盖，真 IPA），零额外成本。
- 发现 ECDICT 音标混入西里尔字母 `ә`(U+04D9) 3,201 次、`є`(U+0454) 57 次，影响 49.4% 词条
- 破译了 ECDICT 记音里 `.` 的含义 —— 对照 `abbreviation` 的标准 IPA 确认是**次重音标记** ˌ

**关键决策**（详见 [08-decisions.md](./08-decisions.md)）

| # | 决策 | 结论 |
|---|------|------|
| 1 | 刘洪波词表 | 先不用，ECDICT 跑通再说 |
| 2 | 选词策略 | 5,040 词**全部导入** + 存 `exam_tags`，筛选交给应用层 |
| 3 | 一词多义（75.1%） | `meaning` 存完整 + `meaning_primary` 存首义 |
| 4 | 音频 | dictionaryapi.dev 76% + edge-tts 24%，**全部下载本地** |
| 5 | 音标 | 改从 dictionaryapi.dev 取 IPA 96% + 清洗版 ECDICT 兜底 4% |

**数据模型变更**（`words` 表新增 5 列）

```
+ meaning_primary  TEXT     首义，阅读模式选项用
+ audio_source     VARCHAR  'dictapi' | 'edge-tts'
+ exam_tags        VARCHAR  "cet6 toefl ielts gre"
+ bnc, frq         INT      词频
```

**下次从哪继续**

→ **M1 剩余部分**（调研已完成，剩下是工程实现）：

1. PostgreSQL 本地环境 + Alembic 初始化 + 建表
2. `scripts/seed_words.py` —— **必须支持断点续跑**（第 4 步约 75 分钟，中断了不能从头来）
3. `scripts/tag_topics.py` —— LLM 打话题标签 + 抽样人工校验

⚠️ 音频约 100MB，记得加 `.gitignore`。

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
