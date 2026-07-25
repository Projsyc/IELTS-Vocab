# 03 数据模型设计

> 版本：v0.1 · 最后更新：2026-07-25
> ⚠️ 改动本文档 = 改数据库表结构，必须同步写 Alembic 迁移

---

## 1. 实体关系总览

```
┌──────────┐         ┌──────────────────┐
│  users   │────┬───>│  answer_events   │  只追加，永不改
└──────────┘    │    │  （事实来源）      │
                │    └──────────────────┘
                │              │ 回放
                │              ↓
                │    ┌──────────────────┐
                └───>│  user_progress   │  缓存，可从事件重算
                     │  （查询用）        │
                     └──────────────────┘
                              │
┌──────────────┐              │
│  word_lists  │              │
└──────────────┘              │
        │ 1:N                 │
        ↓                     ↓
┌──────────────────────────────────┐
│           words                  │
│  （词形/释义/音标/音频/话题）       │
└──────────────────────────────────┘
```

---

## 2. 表结构

### 2.1 `users` — 用户

```sql
CREATE TABLE users (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    username        VARCHAR(50)  NOT NULL UNIQUE,
    password_hash   VARCHAR(255) NOT NULL,       -- bcrypt
    nickname        VARCHAR(50)  NOT NULL,

    -- 预留：微信小程序一键登录（v3）
    wx_openid       VARCHAR(64)  UNIQUE,
    wx_unionid      VARCHAR(64)  UNIQUE,

    -- 每日任务配置
    daily_new_limit     INT NOT NULL DEFAULT 20,   -- 每天最多学几个新词
    daily_review_limit  INT NOT NULL DEFAULT 100,  -- 每天最多复习几个

    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);
```

**说明**：
- MVP 邀请制，无邮箱字段、无找回密码流程
- `wx_openid` / `wx_unionid` 现在为空，v3 做小程序时启用
- 每日限额存用户级别，允许不同用户不同强度

---

### 2.2 `words` — 单词

```sql
CREATE TABLE words (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    word            VARCHAR(100) NOT NULL,          -- 英文词形

    -- 释义：双字段（一词多义占 75.1%，见 ADR-008）
    meaning         TEXT         NOT NULL,          -- 完整释义，多义项用 " / " 分隔
    meaning_primary TEXT         NOT NULL,          -- 第一义项，阅读模式选项用

    phonetic        VARCHAR(100),                   -- IPA 音标 /əˈkɒmədeɪt/
    part_of_speech  VARCHAR(20),                    -- n. / v. / adj. ...（从释义前缀解析）

    -- 阅读模式干扰项的关键字段
    topic           VARCHAR(50),                    -- 雅思话题：教育/环境/科技...

    -- 音频（seed 阶段全部本地化，100% 有值）
    audio_url       VARCHAR(500),                   -- 本地音频路径
    audio_source    VARCHAR(20),                    -- 'dictapi' | 'edge-tts'，便于排查

    -- 来自 ECDICT 的元数据
    exam_tags       VARCHAR(100),                   -- "cet6 toefl ielts gre"，空格分隔
    bnc             INT,                            -- BNC 语料词频排名
    frq             INT,                            -- 当代语料词频排名

    difficulty      SMALLINT NOT NULL DEFAULT 2,    -- 1易 2中 3难（从 exam_tags 推导）
    word_list_id    UUID REFERENCES word_lists(id) ON DELETE CASCADE,

    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),

    UNIQUE (word_list_id, word)
);

CREATE INDEX idx_words_topic   ON words(topic);          -- 干扰项按话题抽
CREATE INDEX idx_words_list    ON words(word_list_id);
CREATE INDEX idx_words_frq     ON words(frq);            -- 按词频选词/分级
```

**关键字段说明**：

| 字段 | 为什么存在 |
|------|-----------|
| `meaning` / `meaning_primary` | 75.1% 是多义词。完整释义中位数 27 字、最长 169 字，塞进 4 选 1 太挤且**长度差异会泄露答案**；首义中位数仅 14 字。选项用 `meaning_primary`，答题后展示 `meaning`。见 [ADR-008](./08-decisions.md) |
| `topic` | **阅读模式的命根子**。干扰项从同 topic 内抽，否则 4 选 1 毫无难度。无现成数据源，由 LLM 批量打标 |
| `audio_url` | seed 阶段全部下载到本地，播放零延迟。热链实测中位延迟 760ms，不可接受 |
| `audio_source` | 标记音频来自真人发音还是 TTS，便于日后替换或排查 |
| `exam_tags` | 允许应用层筛选（如"跳过四级词"），且是 `difficulty` 的推导依据 |
| `bnc` / `frq` | 词频，用于难度分级和"先学高频词"的排序 |
| `part_of_speech` | 备选的干扰项分组维度（topic 缺失时的兜底） |

> **数据来源与实测覆盖率见 [09 词库数据源调研](./09-wordlist-research.md)。**
> 词表来自 [ECDICT](https://github.com/skywind3000/ECDICT)（MIT）`tag` 含 `ielts` 的 5,040 词。


---

### 2.3 `word_lists` — 词库

```sql
CREATE TABLE word_lists (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    name            VARCHAR(100) NOT NULL,
    description     TEXT,

    -- v2：用户自定义词库
    owner_id        UUID REFERENCES users(id) ON DELETE CASCADE,  -- NULL = 系统内置
    is_public       BOOLEAN NOT NULL DEFAULT true,

    created_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);
```

**说明**：`owner_id IS NULL` 表示系统内置词库（MVP 只有这种）。
v2 做用户上传时，`owner_id` 指向上传者，`is_public` 控制是否共享。

---

### 2.4 `answer_events` — 答题事件（事实来源）⭐

```sql
CREATE TYPE practice_mode AS ENUM ('dictation', 'recognition');

CREATE TABLE answer_events (
    id              BIGSERIAL PRIMARY KEY,
    user_id         UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    word_id         UUID NOT NULL REFERENCES words(id) ON DELETE CASCADE,
    mode            practice_mode NOT NULL,

    is_correct      BOOLEAN NOT NULL,
    user_input      TEXT,                    -- 听写：用户拼的；阅读：选了第几项 / "unknown"

    -- 多端同步的关键
    answered_at     TIMESTAMPTZ NOT NULL,    -- 客户端答题时刻（不是入库时刻！）
    device_id       VARCHAR(64),             -- 哪个设备答的

    created_at      TIMESTAMPTZ NOT NULL DEFAULT now()   -- 入库时刻
);

CREATE INDEX idx_events_replay ON answer_events(user_id, word_id, mode, answered_at);
CREATE INDEX idx_events_user_time ON answer_events(user_id, answered_at DESC);
```

**这张表是整个系统的地基。铁律：**

1. **只追加（append-only）** —— 永不 `UPDATE`，永不 `DELETE`
2. **`answered_at` 用客户端时间**，不是服务器入库时间。离线答的题晚几小时上传，回放时必须按真实答题顺序排
3. 任何时候都能从这张表**完整重算**出 `user_progress`

> `answered_at` 用客户端时间意味着**客户端时钟不准会污染顺序**。
> 缓解：入库时若 `answered_at` 超出 `created_at ± 24h`，记录告警（v2 处理）。

---

### 2.5 `user_progress` — 学习进度（缓存）

```sql
CREATE TABLE user_progress (
    user_id         UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    word_id         UUID NOT NULL REFERENCES words(id) ON DELETE CASCADE,
    mode            practice_mode NOT NULL,

    -- Leitner 状态
    box             SMALLINT NOT NULL DEFAULT 1 CHECK (box BETWEEN 1 AND 5),
    next_review_at  TIMESTAMPTZ NOT NULL,     -- 下次该复习的时刻

    -- 统计（也可从事件算，冗余存一份方便展示）
    correct_count   INT NOT NULL DEFAULT 0,
    wrong_count     INT NOT NULL DEFAULT 0,
    last_answered_at TIMESTAMPTZ,

    updated_at      TIMESTAMPTZ NOT NULL DEFAULT now(),

    PRIMARY KEY (user_id, word_id, mode)     -- ⭐ 三元组主键
);

CREATE INDEX idx_progress_due ON user_progress(user_id, mode, next_review_at);
```

**⭐ 主键是三元组 `(user_id, word_id, mode)`**

这是刻意的设计：同一个词在听写模式和阅读模式下**各有一套独立进度**。

> **为什么**："认得出"（recognition）和"会拼写"（dictation）是两种能力。
> 你能一眼认出 `accommodate` 是"容纳"，但拼的时候可能少个 `m`。
> 合并成一个进度会互相污染，导致该练的拼写被误判为已掌握。

**这张表是缓存。** 删掉整张表，从 `answer_events` 回放能完整重建。
如果哪天有个功能想往这里写"事件里没有的信息"，说明设计破了，停下来重新想。

---

## 3. Leitner 状态机

复习间隔（可配置，默认值）：

| box | 间隔 |
|-----|------|
| 1 | 1 天 |
| 2 | 2 天 |
| 3 | 4 天 |
| 4 | 7 天 |
| 5 | 15 天 |

**转移规则**（纯函数，必须可单测）：

```python
BOX_INTERVALS = {1: 1, 2: 2, 3: 4, 4: 7, 5: 15}  # days

def apply_answer(box: int, is_correct: bool, answered_at: datetime) -> tuple[int, datetime]:
    """给定当前盒子号和一次答题结果，返回 (新盒子号, 下次复习时刻)。

    纯函数：同样的输入永远得到同样的输出。这是事件回放能工作的前提。
    """
    if is_correct:
        new_box = min(box + 1, 5)
    else:
        new_box = 1                    # 答错直接掉回 Box 1，不是降一箱

    next_review = answered_at + timedelta(days=BOX_INTERVALS[new_box])
    return new_box, next_review
```

**回放算法**：

```python
def replay(events: list[AnswerEvent]) -> ProgressState:
    """从零开始回放某个 (user, word, mode) 的全部事件。"""
    box = 1
    next_review = None
    for e in sorted(events, key=lambda e: e.answered_at):   # ⭐ 按客户端时间排序
        box, next_review = apply_answer(box, e.is_correct, e.answered_at)
    return ProgressState(box=box, next_review_at=next_review)
```

---

## 4. 今日任务查询

```sql
-- 1. 到期复习词（优先级最高）
SELECT w.* FROM user_progress p
JOIN words w ON w.id = p.word_id
WHERE p.user_id = :uid
  AND p.mode = :mode
  AND p.next_review_at <= now()
ORDER BY p.next_review_at
LIMIT :review_limit;

-- 2. 新词（补足数量）—— 没有 progress 记录的就是新词
SELECT w.* FROM words w
LEFT JOIN user_progress p
       ON p.word_id = w.id AND p.user_id = :uid AND p.mode = :mode
WHERE w.word_list_id = :list_id
  AND p.word_id IS NULL
LIMIT :new_limit;
```

---

## 5. 干扰项查询（阅读模式）

```sql
-- 从同话题内抽 3 个其他词的首义（不是完整释义 —— 长度要均匀，否则泄露答案）
SELECT meaning_primary FROM words
WHERE topic = :topic
  AND id <> :current_word_id
  AND word_list_id = :list_id
ORDER BY random()
LIMIT 3;
```

**兜底**：若 `topic` 为 `NULL` 或同话题词不足 3 个 → 退化为按 `part_of_speech` 抽 → 再不够则全库随机。

---

## 6. 待确认 / 未决

- [x] ~~`words.meaning` 是否需要支持一词多义？~~ → 已解决：`meaning` + `meaning_primary` 双字段（[ADR-008](./08-decisions.md)）
- [ ] 音频文件存哪：本地文件系统 / 对象存储？MVP 先本地（`backend/static/audio/`，不进 git）
- [ ] `answer_events` 长期增长的归档策略（一个用户一年约几万条，暂不是问题）
- [ ] `difficulty` 从 `exam_tags` 推导的具体规则待定（初步：含 zk/gk/cet4 → 1；含 cet6/ky/toefl → 2；仅 ielts/gre → 3）

---

**相关文档**：[02 技术架构](./02-architecture.md) · [04 API 设计](./04-api-design.md) · [learning-docs/05 事件溯源](../learning-docs/05-event-sourcing.md)
