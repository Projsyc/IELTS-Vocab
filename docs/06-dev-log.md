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

## 2026-07-25 (8) 词库与练习接口 —— 核心玩法已能跑通

**做了什么**

| 模块 | 内容 | 测试 |
|------|------|------|
| `services/distractor.py` | 干扰项生成（降级链 + 防前缀泄露） | 36 |
| `services/practice.py` | 挑词、出题、答题落库 | — |
| `routers/words.py` | 词库 list / stats | — |
| `routers/practice.py` | daily / session / answer | 29 |

测试 342 → **409**。8 个接口全部可用。

**干扰项：真实数据上 99% 走最优路径**

在 4,768 词上抽样 800 次：

```
topic_and_pos（同话题+同词性）  792  99.0%
topic_only（同话题混词性）        8   1.0%
凑不够 4 个选项的                 0
```

出题样本：

```
【通用/抽象 / vt.】
    1. 使惊骇, 使大惊
  ✓ 2. 使浓缩, 使压缩, 缩短
    3. 解开, 取消, 破坏, 毁灭, 扰乱
    4. 给...权利, 取名为, 给予名称
```

对比全库随机会出现的 `projector vs 量子力学`，这一步的价值很明确。

**实现中修正的两处设计**

1. **阅读模式回传选中文本，不是选项 index**

   写路由时才意识到：题目是**无状态生成**的（每次请求现算），
   服务端不保存"第几个是对的"。客户端回传 index 的话服务端**无从验证** ——
   除非把题目存进 session 或用种子重新生成，两者都更复杂更脆弱。

   改成回传文本：完全无状态，客户端有全部选项文本但不知道哪个对，照样不能作弊。
   额外好处是事件日志里存文本比存 index 更有分析价值。

   已更新 `docs/04-api-design.md`。

2. **干扰项一律剥掉词性前缀**

   上一轮功能测试发现的问题，这轮落实。原方案是"降级到混词性时才剥"，
   实现时改成**一直剥**：既然总要处理，统一规则少一类 bug；
   而且选项更短好读，词性在单词旁边显示一次就够，重复四遍是噪音。

**离线补传的处理**

`submit_answer` 里判断新事件是否早于 `last_answered_at`：

```
不早于 → 增量更新（快）
更早   → 全量回放，并在响应里置 wasReplayed=true
```

写了 HTTP 测试验证：先提交一个"晚"的答错（Box 1），再补传一个更早的答对，
响应 `wasReplayed=true`，最终按真实时间顺序算出 Box 1、对 1 错 1。

**两个测试写错了（不是代码问题）**

- `client.get(path, json=None)` —— httpx 的 GET 不接受 `json` 参数
- `_first_word` 取任意一个词，但 daily 只挑词频前 5 —— 测试假设错了，
  改成从 daily 实际返回的题目里取

**端到端验证**

起真实 uvicorn 跑完整流程：登录 → 词库（4,768 词）→ 阅读模式取题
（选项无 `correctIndex` 泄露）→ 听写故意拼错（拿到高亮 diff，Box 1）→
同词答对（Box 2，对 1 错 1）→ 掌握情况从"新 4768"变成"新 4767 学习中 1"。

**下次从哪继续**

→ **M2 最后一组：进度接口**

1. `GET /api/progress/summary` —— 连续天数、今日答题量、盒子分布、今日到期数
2. `GET /api/progress/wrong-words` —— 错题本，从 `answer_events` 聚合
   （这是事件溯源"免费送"的能力，见 learning-docs/05）
3. `POST /api/progress/rebuild` —— 全量重算进度
   （`services/practice.py` 里的 `rebuild_progress` 已实现单个词的重算，
   接口层需要扩展成"整个用户"并做好耗时提示）

之后 M2 就完了，可以开始 M3 前端。

---

## 2026-07-25 (7) 听写判定 + 认证接口 —— 已能真实登录

**做了什么**

| 模块 | 内容 | 测试 |
|------|------|------|
| `services/dictation.py` | 听写判定 + Levenshtein 错误位置高亮 | 50 |
| `core/security.py` | bcrypt 密码哈希 + JWT | 30 |
| `core/deps.py` | `get_current_user` 依赖注入 | — |
| `schemas/auth.py` | Pydantic 模型（camelCase 输出） | — |
| `routers/auth.py` | login / me | 23 |
| `scripts/manage_users.py` | 邀请制手动开号 | — |

测试总数 237 → **342**。

**听写 diff 为什么要用编辑距离**

逐位比较在漏字母时会全线崩掉：

```
用户   a c c o m o d a t e      （少一个 m）
正确   a c c o m m o d a t e

逐位比较：位置 5 之后全部错位 → 报 6 个错，用户看不出问题在哪
对齐后：  只报"位置 5 少了个 m" → 一眼看懂
```

用 Levenshtein DP 求最小编辑脚本再回溯出对齐。单词只有十几字符，性能不是问题。

实测效果（真实雅思拼写错误）：

```
accomodate  → 1 处：missing 'm'
acommodate  → 1 处：missing 'c'
accommadate → 1 处：wrong a→o
recieve     → 2 处：wrong（ie/ei 颠倒的最小编辑就是 2 次替换）
enviroment  → 1 处：missing 'n'
```

还写了**双向可还原性测试** —— 从 diff 既能还原出正确答案，也能还原出用户输入，
证明对齐过程没丢字符。

**踩坑：passlib 不能用（BUG-007）**

按原定的 `passlib[bcrypt]` 写完密码哈希，一跑就炸：

```
AttributeError: module 'bcrypt' has no attribute '__about__'
```

passlib 1.7.4 靠读 `bcrypt.__about__.__version__` 探测版本，但 bcrypt 4.1+ 已移除该属性。
**passlib 最后一次发版是 2020 年**，实际停止维护，不会有适配。

改为直接用 `bcrypt` 库（API 只有两个函数），并用 **SHA-256 预哈希**绕开 bcrypt 的
72 字节上限与 NUL 截断问题。写了测试守护：超长密码可用、72 字节之后的差异能被区分。

> 教训：选依赖先看最后发版时间。停更 6 年本该是个信号。
> 而且装完该立刻写个最小验证脚本，别等写完整个模块才发现底层不可用。

**同一个坑踩了第三次（BUG-008）**

HTTP 测试打 `main.app` 时又报 "Event loop is closed" —— 19 个过、4 个挂，看着像随机失败。

根因还是那个：**async engine 的连接池绑定在创建它的事件循环上**。
这次是 app 里的路由通过 `get_db` 用了模块级 engine。

三次现场：
| # | 场景 |
|---|------|
| BUG-005 | 单测里直接用模块级 engine |
| BUG-006 | 脚本用第二个 `asyncio.run()` 去 dispose |
| BUG-008 | HTTP 测试打 app，app 内部用模块级 engine |

已整理成三条规则写进 `tests/conftest.py` 文件头和 CLAUDE.md，不再一次次现修。
第三条的解法是覆盖 `get_db` 依赖，顺带的好处是 app 与测试共用 session，
测试数据对被测接口立即可见。

**安全上刻意做的几件事**

- **用户名不存在与密码错误返回完全相同的 401** —— 否则能靠错误信息枚举有效用户名。
  而且即便用户不存在也走一次密码校验，让两条路径耗时接近，减少时序侧信道。
- 响应绝不含 `password_hash` 与微信字段，**写了测试守护**（直接断言哈希串不在响应体里）
- JWT 测试覆盖 `alg=none` 攻击、伪造密钥签名、篡改签名、过期
- 建账号脚本的密码走 `getpass` 交互输入，**不接受命令行参数** —— 参数会留在 shell 历史和进程列表里

**端到端验证**

起真实 uvicorn，走完整链路：

```
健康检查            200
未登录访问 /me      401
错密码登录          401
正确登录            拿到 188 字符 token
带 token 访问 /me   返回用户信息，camelCase，无敏感字段
```

**下次从哪继续**

→ M2 剩余的三组接口：

1. **词库接口**（list / stats）—— 相对简单，先热身
2. **练习接口**（daily / session / answer）—— 重点是干扰项生成：
   - ⚠️ 必须同时按 `topic` + `part_of_speech` 过滤，否则释义前缀泄露答案
   - 降级链：同话题+同词性 → 同话题 → 同词性 → 全库随机
   - 答题落库：追加 `answer_events` + 增量更新 `user_progress`
3. **进度接口**（summary / wrong-words / rebuild）

建议顺序：先把干扰项生成写成**纯函数 + 单测**（给定候选词列表返回选项），
再接数据库查询 —— 和 Leitner/dictation 一样的路子，好测。

---

## 2026-07-25 (6) M2 起步：Leitner 算法 + 事件回放（含 68 个测试）

**做了什么**

- **`app/services/leitner.py`** —— Leitner 状态机，全纯函数
  - `apply_answer(box, is_correct, answered_at) -> LeitnerState`
  - `LeitnerState` 是 frozen dataclass，转移返回新实例而非改旧的
  - 拒绝 naive datetime（数据库列是 TIMESTAMPTZ，naive 会被静默按本地时区解释）
- **`app/services/replay.py`** —— 事件回放
  - `AnswerRecord` 刻意**不依赖 ORM**，所以单测不需要数据库
  - `replay()` 全量回放（真相）、`replay_incremental()` 增量（性能）
- **测试**：`test_leitner.py` 45 个 + `test_replay.py` 23 个 → 总计 **237 个**
- **doctest 纳入常规测试**（`addopts = --doctest-modules`）—— 文档里的示例写错会被抓到

**写之前先想清的一个坑：时间戳相同怎么排序**

这不是理论问题 —— 顺序不同结果就不同：

```
先对后错 → Box 1        先错后对 → Box 2
```

没有确定的次级排序键，回放就**不可复现**，整个事件溯源的前提就破了。
用事件的自增 `id` 做次级键（数据库里唯一且稳定），并写了 4 个测试固化这条。

**三条重点性质，都写了强测试**

| 性质 | 怎么验证的 |
|------|-----------|
| ⭐ 乱序回放 == 顺序回放 | 固定事件集打乱 200 次 + 60 组随机序列各打乱 8 次 |
| ⭐ 时间戳相同时结果确定 | 正反两个方向的 id 顺序 + 全同时间戳打乱 100 次 |
| ⭐ 增量 == 全量 | 逐步追加对比 + 40 组随机序列；**并固化了增量在乱序时的不等价** |

最后那条值得说明：`replay_incremental` 只在"新事件确实最新"时等价于全量。
我专门写了个测试**断言它在乱序时与全量不同** —— 这样如果有人误以为增量总是安全的、
把冲突修复也改成走增量，测试会提醒他。

**还写了个按 answered_at 排序的针对性测试**：

```python
phone_correct = AnswerRecord(102, True,  10:00)   # 离线，晚上传，id 更大
laptop_wrong  = AnswerRecord(101, False, 14:00)   # 当场入库，id 更小

按 id 排  → Box 2  ❌
按时间排  → Box 1  ✅
assert snap.box != 2, "看起来是按 event_id 排序了"
```

**端到端验证**（不只是单测）

造 6 条事件**乱序写进真实数据库** → 读出来回放 → 结果正确（Box 1、对 4 错 2）→ 落
`user_progress` 成功 → 清理测试数据。

**顺带修的小问题**

`pytest.ini` 里 `--ignore-glob` 放错位置（它是命令行选项不是 ini 选项），
导致一条 `PytestConfigWarning`。实测不需要那个 ignore，直接删掉。

**下次从哪继续**

→ M2 剩余部分：

1. `core/security.py` —— 密码哈希（passlib/bcrypt）+ JWT 签发校验
2. `schemas/` —— Pydantic 请求/响应模型（对照 `docs/04-api-design.md`）
3. 认证接口 `routers/auth.py`（login / me）
4. 练习接口 —— 其中**干扰项生成要按 `topic` + `part_of_speech` 双重过滤**，
   降级链见 [03-data-model §5](./03-data-model.md)
5. 听写判定 + diff 生成（错误位置高亮）—— 也应该是纯函数，好测

---

## 2026-07-25 (5) 话题打标完成 —— 🎉 M1 全部完成

**做了什么**

- **`app/scripts/topics.py`** —— 话题体系：20 个雅思话题 + `通用/抽象` 兜底
  - 每类附**边界说明**（`TOPIC_HINTS`），实测能显著降低多义词误标
  - `normalize_topic()` 保守清理 LLM 输出，认不出返回 None（不做模糊猜测）
- **`app/scripts/tag_topics.py`** —— LLM 批量打标
  - DeepSeek `deepseek-v4-flash`，OpenAI 兼容，配置全在 `.env`
  - 断点续跑（`topic IS NULL` 即待处理集），沿用 seed 脚本的思路
  - `--status` / `--limit` / `--review N` / `--retag <话题>` / `--batch` / `--concurrency`
- **`tests/test_topics.py`** —— 62 个测试（总计 **153 个**）

**打标结果**

```
覆盖率     4,768 / 4,768  (100%)
拒绝标签   0     ← prompt 有效，LLM 全部照白名单输出
漏词       0
批失败     0
耗时       0.7 分钟（157 请求，并发 128）
token      prompt 183,760 + completion 237,590
```

分布：兜底 `通用/抽象` 39.3%（1,872），其余 20 类从 5.9%（健康与医疗 281）到 0.6%（全球化 27）。
每类都 ≥ 27 词，远超干扰项需要的 4 个。

**人工验收 —— 路线图上最后一个未验证风险，通过**

| 检查项 | 结果 |
|--------|------|
| 随机 45 条 | ~38 明确正确、5 边界模糊、**1 错**（`panel→教育`） |
| 反查 14 个明确该有话题的词 | **14/14 全对** |
| 兜底类 30 条抽查 | 确实都话题中立，2–3 个边界 |
| 干扰项功能测试 | ✅ `intersection` → 自行车/运输/飞行 |

明确错误率 **2–5%**，低于预设 10% 阈值。

已知局限：LLM 偶尔按**非主要义项**判断 —— `panel` 主义项是"嵌板"，
却因第三义项"专题讨论小组"被标进「教育」。影响可控（误标的词仍是个合理干扰项）。

**⚠️ 功能测试发现一个下游问题（M2 必须处理）**

```
quest (n. 探索, 寻求)
  1. a. 学院的, 学术的     ← 词性不同
  2. vt. 学习；认识到       ← 词性不同
  3. vt. 减去, 扣掉         ← 词性不同
  4. n. 探索, 寻求          ← 唯一的 n.，不看意思就能选对
```

释义自带词性前缀，**干扰项必须同时按 `part_of_speech` 过滤**，否则前缀泄露答案。
已把降级链写进 [03-data-model §5](./03-data-model.md)：
同话题+同词性 → 同话题 → 同词性 → 全库随机。

**两处配置疏漏（用户指出）**

1. **只改了 `.env.example` 没改 `.env`** —— 用户的 `.env` 是早先从旧模板复制的，
   缺 `LLM_*` 三项。已补齐。
   > 顺带：想 `cat .env` 查看时被权限分类器拦下，理由是会把密钥打进对话 ——
   > 正是用户要求避免的。改用 `grep -q` 只验证键存在、不打印值。拦得对。
2. **并发默认值太低，且我的理由是错的** —— 我说"瓶颈是 TPM，全发必然 429"，
   但 DeepSeek 的文档限制是**并发数**（v4-pro 500 / v4-flash 2500），不是 TPM。
   已从 16 调到 128（157 请求 → 2 轮）。

**顺带抓到一个真 bug**

`run_tagging` 里写死 `GROUP = 10`（分组落库用），会把**实际并发卡在 10**，
让 `--concurrency 128` 形同虚设。已改为 `max(concurrency * 2, 20)`，
并放开 httpx 连接池上限（默认 100，低于 128 会排队）。

---

## 🎉 M1 完成

| 内容 | 数据 |
|------|------|
| 词库 | **4,768 词** |
| 音频 | **100%**（75.6% 真人 + 24.4% TTS，84.7MB） |
| 音标 | **99.2%** |
| 词性 | **99.6%** |
| 话题 | **100%**（21 类，已验收） |
| 数据库 | 5 表 + 7 索引，迁移往返已验证 |
| 测试 | 153 个 |

路线图上原有的三个风险全部解除：版权（ADR-007）、数据源（ADR-007）、标签质量（ADR-012）。

**下次从哪继续 —— M2 后端核心**

先写这两个**纯函数 + 单测**，它们是整个算法的核心，也是事件回放能工作的前提：

1. `services/leitner.py` —— `apply_answer(box, is_correct, at) -> (new_box, next_review)`
   - 测试覆盖：答对升箱、答错回 Box 1、Box 5 封顶
2. `services/replay.py` —— 按 `answered_at` 排序回放重建进度
   - 测试覆盖：**乱序事件回放结果必须与顺序回放一致**

然后才是 `core/security.py`（JWT）和 API 路由。

---

## 2026-07-25 (4) seed 脚本完成，词库导入跑通

**做了什么**

- **`app/scripts/ecdict.py`** —— 纯函数模块，11 个函数全部可单测
  - 词性解析、释义拆分、音标清洗、难度推导、屈折形式识别、文件名生成
- **`app/scripts/seed_words.py`** —— 三阶段导入脚本
  - 阶段 1：两趟扫描 csv → 插入基础数据（几秒）
  - 阶段 2：逐词调 dictionaryapi.dev 取 IPA + 下载音频（~25 分钟）
  - 阶段 3：edge-tts 补齐剩余音频（几分钟）
  - `--status` / `--limit` / `--stage` / `--concurrency` / `--delay` 参数
- **`tests/test_ecdict.py`** —— 83 个测试，含在真实 5,040 词上的验证

**断点续跑设计**（关键约束，已实测验证）

不用状态文件，拿 `words.audio_source` 当状态机：

| 值 | 含义 | 谁处理 |
|---|---|---|
| `NULL` | 没调过 API **或上次失败** | 阶段 2（重试） |
| `pending-tts` | 调过了但该词没音频 | 阶段 3 |
| `dictapi` / `edge-tts` | 完成 ✓ | — |

**实测验证过程**（不是"应该能行"，是真跑过）：

```
1. 清库 → 跑 15 词        → 15 条，音频 15
2. 扩到 40 词 → 阶段 1    → 新增 25、跳过 15   ✅ 幂等
3.           → 阶段 2    → 只处理 25 个        ✅ 跳过已完成
4. 重复跑阶段 2           → "没有待处理的词"    ✅
5. 某次 abrasion 失败     → 状态显示"待处理 1"  ✅ 不静默跳过
6. 重跑                   → 精确只处理那 1 个   ✅ 失败重试
```

**发现并修的两个数据质量问题**

1. **API 的 IPA 混了音节分隔点** —— `/əˈ.bɪl.ɪ.ti/` `/ə.ˈkaʊnt/`。45% 的词带、55% 不带，格式不统一。
   ⚠️ 关键认识：**`.` 在两个数据源里含义完全不同** ——
   ECDICT 里是次重音（→`ˌ`），API 里是音节分隔（→删掉）。
   差点用同一套规则处理，那会把音节点误当次重音。已写测试钉住这条差异。

2. **词表混入屈折形式** —— 试跑时发现 4 个词没音标，追查发现是 `accidents`
   `accommodations` `accountants` `account for` 这类复数和短语。
   统计后：272 个"真冗余"（原型也在表里）+ 156 个原型不在表里 + 66 个专有名词 + 2 个短语。
   经确认只剔那 272 个 → **词库 5,040 → 4,768 词**（[ADR-010](./08-decisions.md)）

**踩的坑**

- 🐛 **BUG-006**：脚本收尾用 `asyncio.run(engine.dispose())` 报 "Event loop is closed"。
  和 BUG-005 同一类错误（async engine 绑定事件循环），**刚踩过又踩** ——
  已在 bug 日志里写成通用规则。
- ⚠️ 差点把 63MB 的 `ecdict.csv` 提交进 git，提交前检查时发现，已加 `.gitignore`

**新增命令**

```bash
pnpm seed              # 导入词库（断点续跑）
pnpm seed:status       # 只看进度
```

**全量导入实测结果**

| 指标 | 调研预测 | 实际 |
|------|---------|------|
| 词库规模 | 4,768 | **4,768** ✅ |
| 真人音频 | 76% | **75.6%**（3,606） |
| TTS 补齐 | 24% | **24.4%**（1,162） |
| 音频覆盖 | 100% | **100%**（4,768 文件，84.7 MB） |
| 音标覆盖 | ~96% | **99.2%** |
| 耗时 | 20–26 min | **38.8 min**（含失败重试） |

完整性校验全过：音频文件缺失 0、空释义 0、西里尔字母残留 0。
剩余缺口：36 个无音标（0.8%，屈折形式和短语）、19 个无词性（0.4%，释义以 `[经]` 开头）。

**首轮 154 个失败，全是高频词 —— 断点续跑救了这一把**

首轮跑完有 154 个失败（3.2%），清一色是 `might` `American` `director` `record` 这类**最常用的词**。

原因：seed 按 `ORDER BY frq` 排序（高频优先），所以它们在**开跑最初几秒**被处理，
那时 4 个并发同时冲上去、毫无预热。

补跑一轮 `--concurrency 2 --delay 0.4` → **154 个全部成功、0 失败**。

如果当初把失败标记成 `failed` 状态，这 154 个高频词会**永久缺音频且无任何提示**。
保持 NULL 让它们自动回到待处理队列 —— 这个设计选择的价值在真实数据上得到了验证。

**用户质疑推动的一次修正（[ADR-011](./08-decisions.md)）**

原方案把源数据全部 gitignore，运行时从 GitHub `master` 下载。用户质疑"别人能从头构建吗、数据库能持久化吗"。

复查后确认：自动下载确实能让 fresh clone 跑通，但**从 `master` 下载等于没锁版本** ——
上游一更新，文档里的实测数字（5,040 → 剔 272 → 4,768）就静默失效。

关键观察：完整版 66MB 里 **99.3% 是我们永不使用的非雅思词**。
只导出 ielts 子集 → **1.5MB**，可以直接提交。

改动：
- 提交 `backend/data/ecdict-ielts.csv`（1.4MB，5,040 行，保留全部原始列）
- 新增 `export_ielts_subset.py`（含 `--verify` 校验一致性）
- 下载兜底源锁 commit SHA 而非 `master`
- 新增 `backend/data/README.md` 记录溯源、列说明、数据陷阱、重新导出流程
- **顺带修了个测试漏洞**：测试原本查 `data/ecdict.csv`，fresh clone 上不存在会**静默 skip**，
  那两个重要验证（文件名碰撞、屈折剔除）就不跑了。改读子集后 fresh clone 也会真正执行

验证方式：真的把 66MB 移走，只留 1.4MB 子集 → 读到 4,768 词、剔 272 个、91 测试全通。

**下次从哪继续**

→ `app/scripts/tag_topics.py`，**M1 最后一项**：

1. 用 LLM 给 4,768 词打雅思话题标签（教育/环境/科技/健康/文化/商业…）
2. 话题体系要先定（用几个类别？沿用雅思写作常见话题分类？）
3. 打完必须**抽样人工校验** —— 这是路线图上唯一还没验证的风险项
4. 同样需要断点续跑（`topic IS NULL` 就是待处理集，机制已有先例）

---

## 2026-07-25 (3) 数据库建表完成，迁移往返已验证

**做了什么**

- **Docker PostgreSQL 环境**：`docker-compose.yml`（PostgreSQL 17-alpine，含健康检查 + 数据卷）
  - `scripts/init-db.sql` 首次初始化建 pgcrypto 扩展
- **后端分层结构**：`app/{core,models,schemas,routers,services,scripts}` + `tests/`
  - `core/config.py` —— pydantic-settings 读 `.env`，配置集中不散落
  - `core/database.py` —— async engine + sessionmaker + `get_db()` 依赖
- **SQLAlchemy 模型**（5 张表，15 列的 `words` 是最大的一张）
  - `user_progress` 主键是三元组 `(user_id, word_id, mode)`
  - `answer_events` 主键用 BIGINT 自增（事件量大，比 UUID 索引紧凑）
  - 文件头写死了 ADR-002 的三条铁律，防止后人无意破坏
- **Alembic**：async 模板 + 从 `app.core.config` 读连接串（密码不进 git）
- **首个迁移** `5cbb15f13ff0` —— 5 表 + 7 索引 + 1 ENUM + 1 CHECK
- **`tests/test_schema.py`** —— 7 个测试锁住结构性不变式，全部通过

**踩了 4 个坑**（详见 [07-bug-log.md](./07-bug-log.md)）

| # | 问题 | 严重度 |
|---|------|--------|
| BUG-002 | Docker Desktop 起不来：`Docker.raw` 属主是 root | 🔴 |
| BUG-003 | SQLAlchemy async 缺 `greenlet`（不是硬依赖） | 🔴 |
| BUG-004 | **Alembic downgrade 后 ENUM 残留，导致无法重新 upgrade** | 🔴 |
| BUG-005 | pytest 里模块级 async engine 跨事件循环报 "Event loop is closed" | 🟡 |

**其中 BUG-004 最值得记住** —— 如果我只测了 `upgrade` 就交差，这个"迁移不可逆"的问题会
一直潜伏到某天需要回滚时才爆。**教训：每个迁移都要测 `upgrade → downgrade → upgrade` 往返。**

**一个环境上的意外**

开发中途 5432 端口被 **Postgres.app** 占了（用户修 Docker 时装的备用方案），导致
`localhost:5432` 连到它而不是容器，报 `role "ielts" does not exist`。
`pg_isready` 健康检查不做认证所以没拦住。已停掉 Postgres.app，并在 CLAUDE.md 记了排查方法。

**验证结果**

```console
$ alembic upgrade head && alembic downgrade base && alembic upgrade head
✅ 三轮全部成功，残留 ENUM: 0

$ pytest tests/ -v
✅ 7 passed in 0.28s
```

**新增命令**

```bash
pnpm db:up / db:down / db:reset / db:migrate / db:shell
pnpm test:backend
```

**下次从哪继续**

→ `backend/app/scripts/seed_words.py`，M1 剩下的两个脚本之一。

关键约束（来自 [09 调研](./09-wordlist-research.md)）：
1. **必须可断点续跑** —— 逐词调 dictionaryapi.dev 约 75 分钟，中断了不能从头来
2. 限速 + 失败重试，别骚扰免费服务
3. 音频存 `backend/static/audio/`，已在 `.gitignore`
4. 无音频的 24% 用 edge-tts 补（需 `pip install edge-tts`，还没加进 requirements）

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
