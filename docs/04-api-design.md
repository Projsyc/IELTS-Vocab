# 04 API 设计

> 版本：v0.1 · 最后更新：2026-07-25
> 状态：**设计稿**，尚未实现。实现后 FastAPI 会在 `/docs` 自动生成交互式文档

---

## 1. 通用约定

### 1.1 基础路径

```
http://localhost:8000/api
```

### 1.2 认证

除登录接口外，所有接口需带 JWT：

```http
Authorization: Bearer <access_token>
```

### 1.3 响应格式

**成功**：直接返回数据对象（FastAPI 惯例，不套 `{code, data}` 壳子）

```json
{ "id": "uuid", "word": "accommodate", ... }
```

**失败**：FastAPI 标准错误格式

```json
{ "detail": "Word not found" }
```

| HTTP 状态码 | 含义 |
|------------|------|
| 200 | 成功 |
| 201 | 创建成功 |
| 400 | 请求参数错误 |
| 401 | 未登录 / token 失效 |
| 403 | 无权限 |
| 404 | 资源不存在 |
| 422 | 参数校验失败（FastAPI 自动） |

---

## 2. 认证 `/api/auth`

### `POST /api/auth/login`

```jsonc
// 请求
{ "username": "alice", "password": "····" }

// 响应 200
{
  "access_token": "eyJhbGci...",
  "token_type": "bearer",
  "user": { "id": "uuid", "username": "alice", "nickname": "Alice" }
}
```

### `GET /api/auth/me`

返回当前登录用户信息。

```jsonc
// 响应 200
{
  "id": "uuid",
  "username": "alice",
  "nickname": "Alice",
  "daily_new_limit": 20,
  "daily_review_limit": 100
}
```

> **v3 预留**：`POST /api/auth/wx-login` 微信小程序 code 换 token

---

## 3. 词库 `/api/word-lists`

### `GET /api/word-lists`

列出可用词库。

```jsonc
// 响应 200
[
  {
    "id": "uuid",
    "name": "雅思核心词汇 4000",
    "description": "参考刘洪波词表范围，释义来自 ECDICT",
    "word_count": 4000,
    "is_public": true
  }
]
```

### `GET /api/word-lists/{id}/stats`

当前用户在该词库的掌握情况。

```jsonc
// 响应 200
{
  "word_list_id": "uuid",
  "total": 4000,
  "dictation":   { "new": 3800, "learning": 150, "mastered": 50 },
  "recognition": { "new": 3600, "learning": 300, "mastered": 100 }
}
```

> `new` = 无 progress 记录；`learning` = box 1–4；`mastered` = box 5

---

## 4. 练习 `/api/practice`

### `GET /api/practice/daily`

**每日任务** —— 系统自动挑词。

```
Query 参数：
  mode       required  dictation | recognition
  list_id    optional  不传则用默认词库
```

```jsonc
// 响应 200
{
  "mode": "dictation",
  "total": 35,
  "review_count": 15,        // 其中到期复习词
  "new_count": 20,           // 其中新词
  "items": [
    {
      "word_id": "uuid",
      "word": "accommodate",           // ⚠️ 听写模式前端不应展示，仅用于提交后比对
      "phonetic": "/əˈkɒmədeɪt/",
      "meaning": "容纳；提供住宿",
      "audio_url": "/static/audio/accommodate.mp3",   // null 则前端降级 TTS
      "box": 2,                        // 当前盒子号，null 表示新词
      "options": null                  // 听写模式无选项
    }
  ]
}
```

**阅读模式**的 `items` 多了 `options`：

```jsonc
{
  "word_id": "uuid",
  "word": "accommodate",
  "phonetic": "/əˈkɒmədeɪt/",
  "audio_url": "/static/audio/accommodate.mp3",
  "box": 1,
  "options": [                          // 已打乱顺序
    { "index": 1, "text": "加速；促进" },
    { "index": 2, "text": "容纳；提供住宿" },
    { "index": 3, "text": "积累；累积" },
    { "index": 4, "text": "陪同；伴随" }
  ]
  // 注意：正确答案 index 不下发，由后端判定
}
```

> **安全考量**：阅读模式不下发正确答案，防止用户看 network 面板作弊。
> 听写模式的 `word` 字段必须下发（前端要做错误高亮），前端自觉不显示。

---

### `POST /api/practice/session`

**自由练习** —— 用户自选参数生成一组题。

```jsonc
// 请求
{
  "list_id": "uuid",
  "mode": "recognition",
  "count": 20,
  "scope": "all"          // all | review_only | new_only | topic
  "topic": "环境"          // scope=topic 时必填
}

// 响应 200：格式同 /api/practice/daily
```

---

### `POST /api/practice/answer` ⭐

**提交一次答题** —— 核心接口。

```jsonc
// 请求（听写模式）
{
  "wordId": "uuid",
  "mode": "dictation",
  "userInput": "accomodate",
  "answeredAt": "2026-07-25T10:30:00+08:00",   // ⭐ 客户端时间，必须带时区
  "deviceId": "web-chrome-a1b2c3"
}

// 请求（阅读模式）
{
  "wordId": "uuid",
  "mode": "recognition",
  "userInput": "容纳; 提供住宿",        // ⭐ 选中选项的**文本**，不是 index
  "answeredAt": "2026-07-25T10:30:05+08:00",
  "deviceId": "web-chrome-a1b2c3"
}
```

> ⚠️ **阅读模式回传文本而非选项 index**（实现时修正了初稿设计）
>
> 题目是**无状态生成**的 —— 每次请求现算，服务端不保存"第几个是对的"。
> 如果客户端回传 index，服务端无从验证；除非把题目存进 session 或用种子
> 重新生成，两者都更复杂更脆弱。
>
> 回传文本则完全无状态：客户端本来就有全部 4 个选项文本，
> 但**不知道哪个对**，照样不能作弊。
>
> 额外好处：事件日志里存文本比存 index 更有分析价值 ——
> index 脱离当次题目毫无意义，文本能直接看出用户选了什么。

```jsonc
// 响应 200（听写模式，答错）
{
  "isCorrect": false,
  "correctAnswer": "accommodate",
  "diff": [                        // 错误位置高亮用，Levenshtein 对齐
    { "pos": 0, "char": "a", "status": "ok",      "expected": null },
    { "pos": 4, "char": "m", "status": "ok",      "expected": null },
    { "pos": 5, "char": "",  "status": "missing", "expected": "m" },
    { "pos": 6, "char": "o", "status": "ok",      "expected": null }
    // ...
  ],
  "progress": {
    "box": 1,                      // 答错，掉回 Box 1
    "nextReviewAt": "2026-07-26T10:30:00+08:00",
    "correctCount": 0,
    "wrongCount": 1
  },
  "wasReplayed": false             // 是否触发了全量回放（见下）
}
```

`status` 取值：`ok` / `wrong`（拼错） / `missing`（漏字母） / `extra`（多字母）。

**服务端处理流程**：

```
1. 判定对错
   听写：strip + 忽略大小写后严格比对
   阅读：比对选中文本与正确释义；"unknown" 恒判错
2. 追加一条 answer_events           ← 不可变，事实来源
3. 更新 user_progress               ← 缓存
   ├─ 新事件不早于已记录的 last_answered_at → 增量更新（快）
   └─ 新事件更早（离线补传）           → **全量回放**，并置 wasReplayed=true
```

> ⭐ 第 3 步的分支是正确性关键。增量更新只在"新事件确实最新"时等价于全量，
> 离线补传的更早事件必须走全量回放 —— 已有单测固化这个局限。

---

### `POST /api/practice/answers/batch`

**批量提交**（离线补传用，v2）。

```jsonc
// 请求
{ "answers": [ /* 多条 answer 对象 */ ] }

// 响应 200
{
  "accepted": 20,
  "conflicts_resolved": 3,      // 触发了事件回放的词数
  "progress_updates": [ /* 受影响的 progress */ ]
}
```

服务端逻辑：批量入库事件 → 对涉及的 `(user, word, mode)` **触发回放重算**。

---

## 5. 进度 `/api/progress`

### `GET /api/progress/summary`

学习总览。

```jsonc
// 响应 200
{
  "streak_days": 7,                  // 连续学习天数
  "today": { "answered": 35, "correct": 28, "accuracy": 0.80 },
  "boxes": {
    "dictation":   { "1": 50, "2": 30, "3": 20, "4": 10, "5": 5 },
    "recognition": { "1": 40, "2": 35, "3": 25, "4": 15, "5": 10 }
  },
  "due_today": { "dictation": 15, "recognition": 22 }
}
```

### `GET /api/progress/wrong-words`

错题本 —— 从 `answer_events` 聚合。

```
Query：mode, limit, offset
```

```jsonc
// 响应 200
{
  "total": 42,
  "items": [
    {
      "word": { "id": "uuid", "word": "accommodate", "meaning": "容纳" },
      "wrong_count": 5,
      "last_wrong_at": "2026-07-25T10:30:00+08:00",
      "recent_inputs": ["accomodate", "acommodate"]   // 看看老是怎么拼错的
    }
  ]
}
```

### `POST /api/progress/rebuild`

**从事件重算进度**（运维/调试接口）。

```jsonc
// 请求
{ "word_id": "uuid" }    // 不传则重算当前用户全部

// 响应 200
{ "rebuilt_count": 1234, "duration_ms": 890 }
```

> 用途：算法参数改了、怀疑 progress 表脏了、多端冲突后手动修复。
> 这个接口能存在，本身就是混合式事件溯源的价值体现。

---

## 6. 接口清单速查

| 方法 | 路径 | 说明 | 状态 |
|------|------|------|-----|
| POST | `/api/auth/login` | 登录 | ✅ 已实现 |
| GET | `/api/auth/me` | 当前用户 | ✅ 已实现 |
| GET | `/api/word-lists` | 词库列表 | ✅ 已实现 |
| GET | `/api/word-lists/{id}/stats` | 词库掌握情况 | ✅ 已实现 |
| GET | `/api/practice/daily` | 每日任务 | ✅ 已实现 |
| POST | `/api/practice/session` | 自由练习 | ✅ 已实现 |
| POST | `/api/practice/answer` | 提交答题 | ✅ 已实现 |
| POST | `/api/practice/answers/batch` | 批量提交（离线） | v2 |
| GET | `/api/progress/summary` | 学习总览 | ✅ 已实现 |
| GET | `/api/progress/wrong-words` | 错题本 | ✅ 已实现 |
| POST | `/api/progress/rebuild` | 重算进度 | ✅ 已实现 |
| GET | `/api/health` | 健康检查 | ✅ 已实现 |

> 字段命名：请求体、响应、**查询参数**统一用 camelCase
> （与 `packages/shared` 的 TS 类型一致）。后端内部用 snake_case，
> 由 Pydantic 的 `alias_generator` 与 `Query(alias=...)` 转换。

---

## 7. 时区处理 ⚠️

`GET /api/progress/summary` 的 **"今日"和"连续天数"依赖客户端时区**。

服务器跑在 UTC，用户在东八区：

```
北京时间 2026-07-25 07:00  =  UTC 2026-07-24 23:00
→ 按 UTC 算会把今天的学习记成昨天，连续天数也会断错
```

所以前端**必须**带上 `tzOffsetMinutes`：

```js
const offset = -new Date().getTimezoneOffset();   // 北京 → 480
fetch(`/api/progress/summary?tzOffsetMinutes=${offset}`)
```

不传则按 UTC 算（合法值范围 −720 ~ 840，覆盖 UTC−12 ~ UTC+14）。

比在 `users` 表存时区更好：用户出差换时区时自动跟随，不用改设置。

---

**相关文档**：[03 数据模型](./03-data-model.md) · [01 产品需求](./01-product-spec.md)
