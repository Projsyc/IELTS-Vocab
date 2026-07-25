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
  "word_id": "uuid",
  "mode": "dictation",
  "user_input": "accomodate",
  "answered_at": "2026-07-25T10:30:00+08:00",   // ⭐ 客户端时间
  "device_id": "web-chrome-a1b2c3"
}

// 请求（阅读模式）
{
  "word_id": "uuid",
  "mode": "recognition",
  "user_input": "3",              // 选了第几项；"unknown" 表示点了"不知道"
  "answered_at": "2026-07-25T10:30:05+08:00",
  "device_id": "web-chrome-a1b2c3"
}
```

```jsonc
// 响应 200（听写模式，答错）
{
  "is_correct": false,
  "correct_answer": "accommodate",
  "diff": [                        // 错误位置高亮用
    { "pos": 0, "char": "a", "status": "ok" },
    { "pos": 1, "char": "c", "status": "ok" },
    { "pos": 2, "char": "c", "status": "ok" },
    { "pos": 3, "char": "o", "status": "ok" },
    { "pos": 4, "char": "m", "status": "ok" },
    { "pos": 5, "char": "",  "status": "missing", "expected": "m" },
    { "pos": 6, "char": "o", "status": "ok" }
    // ...
  ],
  "progress": {                    // 更新后的 Leitner 状态
    "box": 1,                      // 答错，掉回 Box 1
    "next_review_at": "2026-07-26T10:30:00+08:00"
  }
}
```

**服务端处理流程**：

```
1. 判定对错
   - 听写：strip() + lower() 后严格比对
   - 阅读：比对选项 index（"unknown" 恒判错）
2. 追加一条 answer_events  ← 不可变
3. 增量更新 user_progress  ← 缓存
4. 返回结果 + diff + 新进度
```

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

| 方法 | 路径 | 说明 | MVP |
|------|------|------|-----|
| POST | `/api/auth/login` | 登录 | ✅ |
| GET | `/api/auth/me` | 当前用户 | ✅ |
| GET | `/api/word-lists` | 词库列表 | ✅ |
| GET | `/api/word-lists/{id}/stats` | 词库掌握情况 | ✅ |
| GET | `/api/practice/daily` | 每日任务 | ✅ |
| POST | `/api/practice/session` | 自由练习 | ✅ |
| POST | `/api/practice/answer` | 提交答题 | ✅ |
| POST | `/api/practice/answers/batch` | 批量提交（离线） | v2 |
| GET | `/api/progress/summary` | 学习总览 | ✅ |
| GET | `/api/progress/wrong-words` | 错题本 | ✅ |
| POST | `/api/progress/rebuild` | 重算进度 | ✅ |
| GET | `/api/health` | 健康检查 | ✅ 已实现 |

---

**相关文档**：[03 数据模型](./03-data-model.md) · [01 产品需求](./01-product-spec.md)
