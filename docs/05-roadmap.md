# 05 开发路线图

> 最后更新：2026-07-25

---

## 版本规划

```
v0.1  项目初始化        ████████████ 已完成
v1.0  MVP（本地可跑）    ░░░░░░░░░░░░ 进行中
v2.0  上线 + 自定义词库   ░░░░░░░░░░░░ 未开始
v3.0  多端（小程序/安卓）  ░░░░░░░░░░░░ 未开始
```

---

## v0.1 — 项目初始化 ✅

- [x] pnpm workspace 骨架
- [x] 前端脚手架（React 19 + Vite 8 + TS），构建通过
- [x] 后端脚手架（FastAPI + Python 3.13），启动通过
- [x] `packages/shared` 共享类型
- [x] 需求访谈（grilling session）
- [x] 项目文档
- [ ] Tailwind + shadcn/ui 接入
- [ ] PostgreSQL 本地环境 + Alembic 初始化

---

## v1.0 — MVP

**目标**：本地跑通完整链路 —— 登录 → 两种模式练习 → Leitner 排期 → 进度持久化。
**验收标准**：见 [01-product-spec.md §8](./01-product-spec.md)

### M1 数据地基

- [x] **Docker PostgreSQL 环境** —— `docker-compose.yml`，PostgreSQL 17-alpine
- [x] **Alembic 初始化 + 建表**（5 张表 + 7 个索引）
  - [x] 后端分层结构 `app/{core,models,schemas,routers,services,scripts}`
  - [x] SQLAlchemy 模型（含三元组主键、CHECK 约束、ENUM）
  - [x] 首个迁移 `5cbb15f13ff0`，**往返测试通过**（upgrade → downgrade → upgrade）
  - [x] `tests/test_schema.py` 锁住结构性不变式（7 个测试）
- [x] **词库数据源调研** → 见 [09-wordlist-research](./09-wordlist-research.md)
  - [x] 确认 ECDICT（MIT）`tag` 含 `ielts` 的 5,040 词可用
  - [x] 实测字段完整度、音频/音标覆盖率、许可状况
  - [x] 5 项数据决策定案（[ADR-007~009](./08-decisions.md)）
- [x] 词库导入脚本 `scripts/seed_words.py` —— **已跑完全量：4,768 词**
  - [x] 两趟扫描筛 ielts + **剔除 272 个冗余屈折形式**（[ADR-010](./08-decisions.md)）
  - [x] 解析词性 + 拆分完整释义/首义
  - [x] 逐词调 dictionaryapi.dev 取 IPA + 下载音频
  - [x] **断点续跑**（`audio_source` 状态机）—— 首轮 154 个失败，补跑全部成功
  - [x] 限速 + 指数退避重试 + 分批落库 + 高频词优先
  - [x] 无音频的用 edge-tts 补齐 → **音频 100% 覆盖**
  - [x] `--status` / `--limit` / `--stage` / `--concurrency` / `--delay` 参数
  - [x] 91 个测试（含在真实词表上验证剔除逻辑与文件名无碰撞）
- [x] **源数据提交进仓库**（[ADR-011](./08-decisions.md)）—— 1.5MB 雅思子集，
      fresh clone 离线可复现，附 `backend/data/README.md` 说明
- [x] LLM 批量打话题标签脚本 `scripts/tag_topics.py`
  - [x] 定义话题体系：20 个雅思话题 + `通用/抽象` 兜底（[ADR-012](./08-decisions.md)）
  - [x] DeepSeek `deepseek-v4-flash`，OpenAI 兼容接口，配置全在 `.env`
  - [x] 白名单严格校验，认不出就留空（可被重跑捡起）
  - [x] 断点续跑（`topic IS NULL` 即待处理集）
  - [x] 全量打标 **4,768/4,768（100%）**，拒绝 0 / 漏词 0 / 失败 0，0.7 分钟
  - [x] **抽样人工校验通过** —— 明确错误率 2–5%，反查 14/14 全对

> ## ✅ M1 完成
>
> 词库数据全部就位：
> **4,768 词** · 音频 **100%**（84.7MB）· 音标 **99.2%** · 词性 **99.6%** · 话题 **100%**
>
> 路线图上原有的三个 🔴/🟡 风险全部解除（版权、数据源、标签质量）。

---

## v1.0 M2 后端核心

**下一步从这里开始。**

- [x] `core/security.py` —— bcrypt 密码哈希 + JWT 签发/校验，30 个测试
  - [x] ⚠️ **改用 bcrypt 直接调用，移除 passlib**（passlib 停更，读不了 bcrypt 5.0 版本号，[BUG-007](./07-bug-log.md)）
  - [x] SHA-256 预哈希绕开 bcrypt 的 72 字节上限与 NUL 截断
  - [x] 安全场景测试：alg=none 攻击、伪造密钥、过期、篡改签名、损坏哈希
- [x] **Leitner 纯函数 + 单元测试** —— `services/leitner.py`，45 个测试
  - [x] `apply_answer(box, is_correct, at) -> LeitnerState`
  - [x] 答对升箱 / 答错回 Box 1 / Box 5 封顶
  - [x] 拒绝 naive datetime（数据库列是 TIMESTAMPTZ）
  - [x] 纯函数性质、不可变性、入参不被修改
- [x] **事件回放函数 + 单元测试** —— `services/replay.py`，23 个测试
  - [x] 按 `(answered_at, event_id)` 排序回放
  - [x] ⭐ **乱序回放 == 顺序回放**（200 次随机打乱 + 60 组随机序列验证）
  - [x] ⭐ **时间戳相同时结果确定**（用自增 id 做次级键）
  - [x] ⭐ **增量更新 == 全量回放**（并固化了增量在乱序时的已知局限）
  - [x] 端到端验证：乱序写入真实数据库 → 回放 → 落 `user_progress`
- [x] **听写判定 + diff 生成** —— `services/dictation.py`，50 个测试
  - [x] 严格匹配（忽略首尾空格与大小写），差一字母即判错
  - [x] Levenshtein 对齐生成错误位置高亮（漏字母只报 1 处错，不是全线错位）
  - [x] 覆盖真实雅思拼写错误：漏双写字母、ie/ei 颠倒、多字母、替换
  - [x] 双向可还原性测试（从 diff 能还原出正确答案，也能还原出用户输入）
- [x] 认证接口（login / me）—— `routers/auth.py`，23 个 HTTP 测试
  - [x] ⭐ 用户名不存在与密码错误返回**完全相同**的 401（防用户名枚举）
  - [x] 响应绝不含 `password_hash` / 微信字段（已写测试守护）
  - [x] camelCase 输出，与 `packages/shared` 的 TS 类型一致
  - [x] `scripts/manage_users.py` —— 邀请制手动开号（密码走 getpass，不进 shell 历史）
- [x] 词库接口（list / stats）—— `routers/words.py`
  - [x] 两种模式分别统计（听写/阅读进度独立）
- [x] 练习接口（daily / session / answer）—— `routers/practice.py`，29 个 HTTP 测试
  - [x] 听写判定 + diff 生成（错误位置高亮）
  - [x] **阅读干扰项生成** —— `services/distractor.py`，36 个测试
    - [x] 降级链：同话题+同词性 → 同话题 → 同词性 → 全库随机
    - [x] ⭐ **一律剥掉词性前缀**，即便降级到混词性也不泄露答案
    - [x] 释义文本去重（避免出现两个相同选项）
    - [x] 真实 4,768 词实测：**99% 走最优路径**，0 次凑不够选项
  - [x] 答题落库：追加 `answer_events` + 更新 `user_progress`
    - [x] ⭐ **离线补传的更早事件自动触发全量回放**（增量在乱序时会算错）
  - [x] ⚠️ 阅读模式回传**选中文本**而非 index（题目无状态生成，index 无从验证）
- [ ] 进度接口（summary / wrong-words / rebuild）← **M2 只剩这个**

### M3 前端

- [ ] Tailwind + shadcn/ui 接入
- [ ] 路由 + 登录页
- [ ] API 层封装（统一鉴权头、错误处理）
- [ ] 首页 / 今日任务概览
- [ ] **听写练习页**
  - [ ] 音频播放（含重播）+ `audio_url` 为空时降级浏览器 TTS
  - [ ] 输入框 + Enter 提交
  - [ ] 错误位置高亮组件
- [ ] **阅读练习页**
  - [ ] 4 选 1 + 键盘 1/2/3/4 + 空格="不知道"
- [ ] 练习结果页（本轮正确率、盒子变化）
- [ ] 进度总览页（盒子分布、连续天数）
- [ ] 错题本页

### M4 收尾

- [ ] 端到端手动走查（对照验收标准）
- [ ] 补 README 快速开始
- [ ] 整理开发日志

---

## v2.0 — 上线 + 自定义词库

- [ ] **部署**
  - [ ] 部署方案选型（国内云 vs Vercel+Railway）
  - [ ] Docker 化
  - [ ] 环境变量与配置分离
  - [ ] HTTPS
- [ ] **用户自定义词库**
  - [ ] CSV 上传 + 解析 + 格式校验 + 友好错误提示
  - [ ] 词库权限（私有 / 共享）
  - [ ] 上传词无音频 → 浏览器 TTS 降级
- [ ] **离线支持**
  - [ ] 前端本地缓存题目
  - [ ] 离线答题暂存 → 联网批量补传
  - [ ] 冲突时触发事件回放
- [ ] 客户端时钟异常检测（`answered_at` 偏离过大时告警）
- [ ] 邮箱注册 / 找回密码（如果用户量增长）

---

## v3.0 — 多端

- [ ] **微信小程序**
  - [ ] 微信一键登录（启用 `wx_openid`）
  - [ ] 已有账号绑定微信
  - [ ] 小程序端音频播放适配
- [ ] **Android**
  - [ ] 技术选型（React Native / Flutter / 原生）
  - [ ] 离线优先架构

---

## 需求池（想到但还没排期）

| 想法 | 价值 | 成本 | 备注 |
|------|------|------|------|
| 一词多义支持 | 中 | 中 | 当前 `meaning` 是单条 TEXT，改起来要动表 |
| 例句展示 | 中 | 低 | ECDICT 可能自带 |
| 词根词缀提示 | 中 | 高 | 需要额外数据源 |
| 学习曲线图表 | 低 | 低 | 事件表已有数据，纯前端活 |
| 语音输入（说单词） | 低 | 高 | 有意思但偏题 |
| 从 OpenAPI 自动生成前端类型 | 中 | 低 | 解决 Python/TS 类型不同步问题 |
| Turborepo 升级 | 低 | 低 | 构建慢了再说 |

---

## 风险追踪

| 风险 | 状态 | 应对 |
|------|------|------|
| ~~版权（刘洪波词汇真经）~~ | ✅ **已解除** | 改用 ECDICT（MIT）自带的 `ielts` 标签，不再需要该书词表。见 [ADR-007](./08-decisions.md) |
| ~~词库数据源找不到合适的~~ | ✅ **已解除** | ECDICT 提供 5,040 雅思词，字段完整度已实测。见 [09](./09-wordlist-research.md) |
| LLM 话题标签质量 | 🟡 待验证 | 打标后抽样人工校验。无任何现成数据源提供此字段 |
| 音频许可（edge-tts / dictionaryapi.dev） | 🟡 已识别 | 本地自用风险低；**v2 部署前必须复核**，退路是浏览器 TTS |
| seed 脚本 75 分钟流程中断 | 🟡 已识别 | 脚本必须支持断点续跑 + 失败重试 + 限速 |

---

**相关文档**：[01 产品需求](./01-product-spec.md) · [06 开发日志](./06-dev-log.md)
