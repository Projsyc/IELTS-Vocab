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
                │       │ 回放      │ 归属
                │       ↓           ↓
                │    ┌──────────────────┐  ┌────────────────┐
                └───>│  user_progress   │  │ test_sessions  │
                │    │  （缓存，可重算）  │  │ （一次测试）    │
                │    └──────────────────┘  └────────────────┘
                │
                │    ┌────────────────┐
                └───>│  word_stars    │  用户书签，**不是**事件的推论
                     └────────────────┘
┌──────────────┐
│  word_lists  │
└──────────────┘
        │ 1:N
        ↓
┌──────────────────────────────────┐
│           words                  │
│  （词形/释义/音标/音频/话题）       │
└──────────────────────────────────┘
```

> ⚠️ `word_stars` 单独一张表而非塞进 `user_progress` ——
> 星标是用户主动打的书签，**不可能从事件重算**，
> 塞进去会被 `POST /progress/rebuild` 抹掉（见 [ADR-013](./08-decisions.md)）。

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

> **已实现**：`backend/app/services/leitner.py`（纯函数）+ `replay.py`（回放）
> 测试：`tests/test_leitner.py`（45 个）+ `tests/test_replay.py`（23 个）

复习间隔（可配置，默认值，定义在 `app/models/enums.py`）：

| box | 间隔 |
|-----|------|
| 1 | 1 天 |
| 2 | 2 天 |
| 3 | 4 天 |
| 4 | 7 天 |
| 5 | 15 天 |

**转移规则**（纯函数，已单测）：

```python
def apply_answer(box: int, is_correct: bool, answered_at: datetime) -> LeitnerState:
    """给定当前盒子号和一次答题结果，返回新状态。

    纯函数：同样的输入永远得到同样的输出。这是事件回放能工作的前提。
    """
    if is_correct:
        new_box = min(box + 1, MAX_BOX)
    else:
        new_box = MIN_BOX              # 答错直接回 Box 1，不是降一箱

    return LeitnerState(
        box=new_box,
        next_review_at=answered_at + timedelta(days=BOX_INTERVALS[new_box]),
    )
```

**为什么必须是纯函数**：一旦引入 `datetime.now()`、随机数或数据库查询，
重放同一批事件就可能得到不同结果，`user_progress` 的"可重算"性质就没了。

### 3.1 回放算法

```python
def replay(events) -> ProgressSnapshot | None:
    # 第一趟：收集被更正的事件 id
    corrected = {e.corrects_event_id for e in events if e.corrects_event_id}

    # 第二趟：排除更正事件本身与测试事件
    scoring = [e for e in events if not e.is_correction and not e.is_test]

    for e in sorted(scoring, key=lambda e: (e.answered_at, e.event_id)):
        #                                    ↑ 主键          ↑ 次级键
        verdict = True if e.event_id in corrected else e.is_correct
        box = apply_answer(box, verdict, e.answered_at).box
```

**两类特殊事件**（[ADR-013](./08-decisions.md)）：

| 类型 | 判别 | 回放时 |
|------|------|--------|
| 测试事件 | `is_test = true` | **直接跳过** —— 测试"错了就是错了"，不进 Leitner 循环 |
| 更正事件 | `corrects_event_id` 非空 | **本身不参与转移**，而是把被指向的事件视为答对 |

> ⚠️ **所有从数据库构造 `AnswerRecord` 的地方都必须查出这两个字段。**
> 它们有默认值，漏了不报错但会静默算错（已踩，见 BUG-010）。
> 检查方法：`grep -rn "AnswerRecord(" app/`

### 3.2 ⚠️ 排序的两个坑（都已写测试固化）

**坑一：必须按 `answered_at` 排，不能按 `created_at` 或自增 `id`。**

```
手机 10:00 答对（离线） → 15:05 才上传，拿到 id=102
电脑 14:00 答错         → 当场入库，id=101

按 id 排:          错(14:00) → 对(10:00)  →  Box 2   ❌ 顺序颠倒
按 answered_at 排: 对(10:00) → 错(14:00)  →  Box 1   ✅
```

**坑二：时间戳相同时必须有确定的次级排序键。**

顺序不同结果就不同 —— 先对后错进 Box 1，先错后对进 Box 2。
没有确定的次级键，回放**不可复现**，整个事件溯源的前提就破了。
用事件的自增 `id`：数据库里唯一且稳定。

### 3.3 增量更新与全量回放

答题接口走**增量**（`replay_incremental`，快），冲突修复走**全量**（`replay`，准）。

⚠️ **增量只在"新事件确实是最新的"时候等价于全量。** 离线补传的事件可能更早，
那时必须走全量。调用方负责判断 `new_event.answered_at >= current.last_answered_at`。

两者的等价性已有测试在随机事件序列上验证；增量在乱序时的**不等价**也写了测试固化，
防止有人误以为增量总是安全的。

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
-- 从同话题内抽 3 个其他词的首义
-- ⚠️ 必须同时按 part_of_speech 过滤 —— 见下方"词性泄露"
SELECT meaning_primary FROM words
WHERE topic = :topic
  AND part_of_speech = :pos          -- ⭐ 不能省
  AND id <> :current_word_id
  AND word_list_id = :list_id
ORDER BY random()
LIMIT 3;
```

### ⚠️ 词性泄露（M2 实现时必须处理）

释义文本自带词性前缀（`n. 交集, 十字路口` / `vt. 减去, 扣掉`）。
如果干扰项不按词性过滤，会出现这种题：

```
quest  (正确答案 n. 探索, 寻求)
  1. a. 学院的, 学术的      ← 词性不同
  2. vt. 学习；认识到        ← 词性不同
  3. vt. 减去, 扣掉          ← 词性不同
  4. n. 探索, 寻求           ← 唯一的 n.，不用看意思就能选对
```

用户靠数前缀就能排除，题目失效。**这是 2026-07-25 话题打标后的功能测试中实测发现的。**

**降级链**（同话题 + 同词性可能凑不够 3 个）：

```
同话题 + 同词性        ← 首选
  ↓ 不足 3 个
同话题（放宽词性）      ← 但要把前缀剥掉再展示，避免泄露
  ↓ 不足 3 个
同词性（放宽话题）
  ↓ 不足 3 个
全库随机（剥掉前缀）
```

> 4,768 词 ÷ 21 话题 ÷ 约 10 种词性 → 部分组合会偏薄，降级链必然会用到。
> 实测话题最少 27 词（`全球化`），但其中 `n.` 可能只有十几个。

### 兜底：`topic IS NULL` 或话题词数不足

理论上不会发生（打标已 100% 覆盖），但用户上传词库（v2）会有未打标的词。

---

## 6. 待确认 / 未决

- [x] ~~`words.meaning` 是否需要支持一词多义？~~ → 已解决：`meaning` + `meaning_primary` 双字段（[ADR-008](./08-decisions.md)）
- [ ] 音频文件存哪：本地文件系统 / 对象存储？MVP 先本地（`backend/static/audio/`，不进 git）
- [ ] `answer_events` 长期增长的归档策略（一个用户一年约几万条，暂不是问题）
- [ ] `difficulty` 从 `exam_tags` 推导的具体规则待定（初步：含 zk/gk/cet4 → 1；含 cet6/ky/toefl → 2；仅 ielts/gre → 3）

---

**相关文档**：[02 技术架构](./02-architecture.md) · [04 API 设计](./04-api-design.md) · [learning-docs/05 事件溯源](../learning-docs/05-event-sourcing.md)
