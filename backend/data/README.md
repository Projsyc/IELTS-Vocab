# `backend/data/` —— 词库原始数据

> 这个目录放**未经处理的源数据**。经过处理的结果在 PostgreSQL 里，不在这。

---

## 文件说明

| 文件 | 大小 | 进 git？ | 说明 |
|------|------|---------|------|
| `ecdict-ielts.csv` | 1.5 MB | ✅ **提交** | ECDICT 中 `tag` 含 `ielts` 的 5,040 行，保留全部原始列 |
| `ecdict.csv` | 66 MB | ❌ 忽略 | 完整 ECDICT（770,611 行），只是构建缓存 |

### 为什么只提交子集

完整版 66MB 里 **99.3% 是我们永远不会碰的非雅思词**。只导出 5,040 行的雅思子集就压到 1.5MB，同时拿到全部好处：

1. **fresh clone 离线可复现** —— 不依赖 `raw.githubusercontent.com` 还活着
2. **等于锁定了数据版本** —— 文档里那些实测数字（5,040 → 剔 272 → 4,768）永远对得上。
   如果改成运行时从 `master` 下载，上游一更新这些数字就静默失效了
3. **许可允许** —— ECDICT 是 MIT，可自由再分发

决策过程见 [ADR-011](../../docs/08-decisions.md)。

---

## 溯源

| | |
|---|---|
| **来源** | [skywind3000/ECDICT](https://github.com/skywind3000/ECDICT) |
| **许可** | MIT |
| **上游 commit** | [`bc015ed2e24a7abef49fc6dbbb7fe32c1dadaf8b`](https://github.com/skywind3000/ECDICT/commit/bc015ed2e24a7abef49fc6dbbb7fe32c1dadaf8b)（2025-03-28） |
| **导出日期** | 2026-07-25 |
| **子集行数** | 5,040 |
| **子集 sha256** | `6b44d6f681fafac94f460331942353c0771be575d85793c000ab50ce0ba17282` |

---

## 列说明

保留了 ECDICT 的全部 13 列（多出的 0.8MB 无所谓，以后想用别的字段不必重新导出）。
本项目实际用到的：

| 列 | 用途 | 覆盖率 |
|----|------|--------|
| `word` | 词形 | 100% |
| `translation` | 中文释义，多义项用**字面 `\n`** 分隔 → 拆成 `meaning` + `meaning_primary` | 100% |
| `tag` | 考试标签（空格分隔）→ 筛 ielts、存 `exam_tags`、推导 `difficulty` | 100% |
| `phonetic` | 音标兜底（⚠️ **不是标准 IPA**，见下） | 95.1% |
| `exchange` | 词形变化，`0:xxx` 表示本词是 xxx 的变形 → 用于剔除冗余屈折形式 | 84.5% |
| `bnc` / `frq` | 词频排名（`0` 表示无排名 → 存 NULL） | 100% |

未使用但保留的：`definition`（英文释义）、`collins`（星级）、`oxford`、`pos`（对 ielts 子集**全为空**）、`detail`、`audio`（后两者全为空）。

---

## ⚠️ 两个已知的数据陷阱

### 1. `phonetic` 不是标准 IPA

ECDICT 用**简化记音体系**，且混入了西里尔字母：

| 字符 | Unicode | 出现次数 | 问题 |
|------|---------|---------|------|
| `ә` | U+04D9 | 3,201 | CYRILLIC SCHWA，应为 IPA `ə` (U+0259) |
| `є` | U+0454 | 57 | CYRILLIC UKRAINIAN IE，应为 IPA `ɛ` (U+025B) |

而且元音记法与现代 IPA 系统性不同（`i` vs `ɪ`、`ei` vs `eɪ`、`әu` vs `əʊ`）。
**规则转换成标准 IPA 已实测不可行（0/8 一致）** —— 单个 `i` 无法从字符判断该转成什么。

所以音标优先从 dictionaryapi.dev 取（96% 覆盖真 IPA），这里的只作兜底。

### 2. `.` 在不同数据源里含义相反

| 来源 | `.` 的含义 | 处理函数 |
|------|-----------|---------|
| **ECDICT 记音**（本目录） | **次重音** | `clean_ecdict_phonetic()` → 转 `ˌ` |
| dictionaryapi.dev 的 IPA | **音节分隔** | `normalize_api_phonetic()` → 删掉 |

别把这两个函数"统一"了。已有测试
`tests/test_ecdict.py::test_dot_means_different_things_in_each_source` 钉住这条差异。

---

## 重新导出子集

上游 ECDICT 更新后想同步：

```bash
# 1. 删掉旧的完整版，让脚本重新下载（或手动改 seed_words.py 里的 ECDICT_COMMIT）
rm backend/data/ecdict.csv

# 2. 触发下载
backend/.venv/bin/python -m app.scripts.seed_words --stage 1 --limit 1

# 3. 重新导出子集
backend/.venv/bin/python -m app.scripts.export_ielts_subset

# 4. 校验（也可单独用来检查仓库内子集是否与上游一致）
backend/.venv/bin/python -m app.scripts.export_ielts_subset --verify
```

⚠️ **换数据后必须复核**：`docs/09-wordlist-research.md` 和 `docs/08-decisions.md`
里的实测数字（词条数、剔除数、覆盖率）可能变化，
`tests/test_ecdict.py::test_inflection_filter_on_real_wordlist` 会因断言不符而失败 —— 这是**刻意**的保护。

---

## 数据库能持久化吗？

能，但**不该依赖它**。

- Docker 数据卷 `ielts_postgres_data` 会持久化，`docker compose down` 不删数据
- 但 `pnpm db:reset`（带 `-v`）会删掉，换机器也不会跟着走
- **所以数据库始终是"可从源数据 + 脚本重建的产物"**，不是真相来源

完整重建路径（fresh clone → 可用数据库）：

```bash
pnpm install
cp backend/.env.example backend/.env
pnpm db:up          # 起 PostgreSQL
pnpm db:migrate     # 建表
pnpm seed           # 导入词库 ← 读的就是本目录的 ecdict-ielts.csv
```

音频（~100MB）也是产物，由 seed 阶段 2/3 生成，同样不进 git。
