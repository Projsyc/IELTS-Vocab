# 🔄 断点续跑与幂等性

> 记录时间：2026-07-25
> 起因：seed 脚本要跑 25 分钟，中断了不能从头来

---

## 问题：跑到一半断了怎么办

`seed_words.py` 要给 4,768 个单词逐个调 API 下载音频，约 25 分钟。

这 25 分钟里可能发生：

- 你 Ctrl-C 了
- 网断了
- 电脑睡眠了
- 某个词让脚本崩了

**如果没有断点续跑机制**，每次都得从第 1 个词重新开始 —— 不仅浪费 25 分钟，还要把 4,768 个请求再打一遍到别人的免费服务器上。

---

## 两个关键概念

### 幂等（Idempotent）

**同一个操作执行一次和执行多次，结果一样。**

```
❌ 非幂等：INSERT INTO words (word) VALUES ('apple')
          跑两次 → 数据库里有两个 apple

✅ 幂等：  INSERT ... ON CONFLICT DO NOTHING
          跑两次 → 还是一个 apple
```

日常例子：
- 电梯按钮是幂等的 —— 按 5 次和按 1 次，电梯来一次
- 往购物车"添加商品"不是幂等的 —— 点 5 次买 5 件

### 断点续跑（Resumable）

**中断后重新运行，能自动从上次停下的地方继续，而不是从头开始。**

关键要求：程序必须能回答"**哪些已经做完了？**"

---

## 怎么记录进度？两种思路

### 思路 A：单独的状态文件

```
progress.json
{
  "done": ["apple", "banana", "cherry", ...],
  "failed": ["xylophone"]
}
```

**问题**：
- 状态文件和数据库可能**不一致** —— 写完数据库、还没写状态文件时崩了，重跑会重做一遍
- 多一个文件要管：放哪？怎么清理？换机器怎么办？
- 状态文件损坏了就全乱了

### 思路 B：让数据本身携带状态 ✅（本项目采用）

**不额外记状态，直接看数据库里的数据处于什么状态。**

本项目用 `words.audio_source` 这一个字段当**状态机**：

```
┌─────────────┐   阶段2调API    ┌──────────────┐
│    NULL     │ ─────成功────>  │   dictapi    │ ✓ 完成
│ 还没处理    │                 └──────────────┘
│ 或上次失败  │
└─────────────┘   阶段2调API     ┌──────────────┐  阶段3合成  ┌──────────┐
       │        ──该词没音频──>  │ pending-tts  │ ────────>  │ edge-tts │ ✓ 完成
       │                        └──────────────┘             └──────────┘
       │
       └── 调用失败 → **不写任何值**，保持 NULL → 下次重跑自动重试
```

于是"哪些还没做"变成一条 SQL：

```sql
SELECT id, word FROM words WHERE audio_source IS NULL;
```

**重跑就是续跑**，不需要任何额外机制。

---

## 一个容易忽略的细节：失败要留 NULL

这是整个设计里最关键的一行：

```python
if status == "ok":
    values["audio_source"] = "dictapi"        # 标记完成
elif status in ("no-audio", "not-found"):
    values["audio_source"] = "pending-tts"    # 标记"该走 TTS"
else:
    return None            # ← 失败！不写 audio_source，保持 NULL
```

**为什么失败时什么都不写？**

如果失败时写个 `"failed"`，那这个词就永远是 `failed` 状态，重跑不会再碰它 —— 一次网络抖动导致的失败会变成永久性的数据缺失，而且**你不会收到任何提示**。

保持 NULL 意味着它自动回到"待处理"队列。实测中确实发生过：

```
第一次跑 40 词 → abrasion 失败 → 状态报告显示"待阶段 2 处理：1"
重跑          → 精确地只处理了 abrasion 一个 → 补齐
```

**教训**：失败状态和"未开始"状态如果不需要区分，就别区分 —— 少一个状态少一类 bug。

---

## 另外三个配套设计

### 1. 分批落库，别攒到最后

```python
# ❌ 危险：4728 个词全处理完才提交，中途断了全白跑
results = await gather(*(handle(w) for w in all_words))
await session.execute(update(Word), results)

# ✅ 每 100 个提交一次，最多损失 100 个词的进度
for i in range(0, len(todo), 100):
    results = await gather(*(handle(w) for w in todo[i:i+100]))
    await session.execute(update(Word), results)
    await session.commit()
```

### 2. 高频词优先

```sql
ORDER BY frq NULLS LAST
```

万一只跑了一半，先拿到的是最常用的词 —— 部分结果也有价值。

### 3. 限速，做个有礼貌的客人

dictionaryapi.dev 是免费社区服务，不是你买的 API：

```python
DEFAULT_CONCURRENCY = 4      # 并发别开太大
DEFAULT_DELAY = 0.1          # 每个请求后等一下
headers = {"User-Agent": "IELTS-Vocab-seed/0.1 (personal study project)"}
                             # ↑ 表明身份和用途，方便对方联系你
```

而且**把结果下载到本地**（而不是每次播放都去请求）本身就是对人家更友好的做法。

---

## 检验清单

写一个长时间运行的数据处理脚本时，问自己：

- [ ] 中途 Ctrl-C，重跑能继续吗？
- [ ] 重跑两次，数据会重复吗？（幂等）
- [ ] 失败的条目会被自动重试，还是永久跳过了？
- [ ] 有办法查看当前进度吗？（本项目：`--status`）
- [ ] 能先小样本试跑吗？（本项目：`--limit 20`）
- [ ] 崩溃时最多损失多少工作量？（本项目：100 个词）

---

**相关文档**：[docs/09 词库调研 §6.3](../docs/09-wordlist-research.md) · `backend/app/scripts/seed_words.py`
