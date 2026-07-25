"""LLM 批量给单词打雅思话题标签。

═══════════════════════════════════════════════════════════════════════
为什么需要
═══════════════════════════════════════════════════════════════════════

阅读模式要从**同话题**内抽 3 个干扰项。全库随机的话，`apple` 会配上
"量子力学"这种选项，4 选 1 毫无区分度。

ECDICT 没有话题字段，调研确认**任何现成数据源都没有**（GitHub 搜
"ielts vocabulary topic" → 0 结果），只能用 LLM 打标。

═══════════════════════════════════════════════════════════════════════
用法
═══════════════════════════════════════════════════════════════════════

    # 先把密钥写进 backend/.env 的 LLM_API_KEY
    # 试跑（小批量，看看标得对不对再全量）
    backend/.venv/bin/python -m app.scripts.tag_topics --limit 60

    # 全量
    backend/.venv/bin/python -m app.scripts.tag_topics

    # 看进度与分布
    backend/.venv/bin/python -m app.scripts.tag_topics --status

    # 抽样人工校验（打完必做 —— 这是路线图上唯一未验证的风险项）
    backend/.venv/bin/python -m app.scripts.tag_topics --review 40

    # 重打某个话题（怀疑某类标得不好时）
    backend/.venv/bin/python -m app.scripts.tag_topics --retag 通用/抽象

═══════════════════════════════════════════════════════════════════════
断点续跑
═══════════════════════════════════════════════════════════════════════

沿用 seed_words.py 的思路 —— **拿数据本身当进度记录**，不用状态文件：

    topic IS NULL  →  待打标
    topic 有值     →  已完成

重跑即续跑。LLM 返回了白名单外的标签时**不写入**（保持 NULL），
下次自动重试 —— 与 seed 脚本对失败的处理一致（见 BUG 日志与 learning-docs/06）。
"""

from __future__ import annotations

import argparse
import asyncio
import json
import random
import sys
import time
from collections import Counter
from dataclasses import dataclass, field

import httpx
from sqlalchemy import func, select, update

from app.core.config import settings
from app.core.database import AsyncSessionLocal, engine
from app.models import Word
from app.scripts.topics import (
    TOPIC_GENERAL,
    TOPICS,
    normalize_topic,
    topic_list_for_prompt,
)

# ─────────────────────────────────────────────────────────────
# 配置
# ─────────────────────────────────────────────────────────────

#: 每次请求塞多少个词。
#: 太大 LLM 容易漏词（用 stats.missing 计数监控），太小请求数多、成本高。
#: 30 是折中：输出稳定，4,768 词 → 仅 159 次请求。
DEFAULT_BATCH = 30

#: 并发请求数。
#:
#: DeepSeek 官方并发限制（账号粒度，与 API Key 无关）：
#:     deepseek-v4-pro    500
#:     deepseek-v4-flash  2500
#: 超限返回 429。本任务用 v4-flash 足够，2500 的额度远超我们需要的量。
#:
#: 全量只有 159 个请求，所以 128 并发 ≈ **2 轮跑完**（约 1 分钟），
#: 且距 2500 的上限还有大量余量。
#:
#: 为什么不干脆设 159 一次全发：没有实质收益（只省一轮），
#: 但万一 prompt 有问题会在察觉前把全部 token 烧掉。
#: 真正的防呆手段是先 `--limit 60` 试跑，不是压低并发。
#:
#: 对比 seed_words.py 的并发 4 —— 那是**免费社区服务**（dictionaryapi.dev），
#: 我们是客人所以克制；DeepSeek 是付费 API，用自己买的配额没问题。
DEFAULT_CONCURRENCY = 128
#: 单请求超时（批量任务输出较长，给宽松些）
TIMEOUT = 120.0
MAX_RETRIES = 3

SYSTEM_PROMPT = """你是雅思词汇教学专家。你的任务是给英语单词标注它最可能出现的雅思话题类别。

规则：
1. 只能从给定的话题清单里选，**必须原样输出话题名**，不要改写、不要翻译、不要加后缀
2. 每个单词只选**一个**最贴切的话题
3. 判断依据是"这个词在雅思听力/阅读/写作里最常出现在哪类语境"，而不是字面联想
4. 话题中立的通用词汇（抽象概念、程度副词、常见动词、逻辑连接词等）一律归入「通用/抽象」
   —— 强行塞进具体话题会降低标注质量，不要勉强
5. 多义词按**最常用**的义项判断

输出格式：严格的 JSON 对象，key 是单词原文，value 是话题名。不要输出任何其他内容。"""


def build_user_prompt(items: list[tuple[str, str]]) -> str:
    """items: [(word, meaning_primary), ...]

    带上中文释义能显著提高准确率 —— 光看词形 LLM 容易在多义词上判错，
    比如 `bank` 到底是银行还是河岸。
    """
    lines = [f"- {w}: {m}" for w, m in items]
    return f"""话题清单（只能从这些里选，原样输出）：
{topic_list_for_prompt()}

请给下面 {len(items)} 个单词各标一个话题：
{chr(10).join(lines)}

输出 JSON，形如 {{"word1": "教育", "word2": "通用/抽象"}}"""


# ─────────────────────────────────────────────────────────────
# 工具
# ─────────────────────────────────────────────────────────────


def log(msg: str) -> None:
    print(msg, flush=True)


@dataclass
class Stats:
    tagged: int = 0
    rejected: int = 0       # LLM 给了白名单外的标签
    missing: int = 0        # LLM 漏了这个词
    api_failed: int = 0
    batches: int = 0
    prompt_tokens: int = 0
    completion_tokens: int = 0
    bad_labels: Counter = field(default_factory=Counter)
    failures: list[str] = field(default_factory=list)


def require_api_key() -> None:
    if not settings.LLM_API_KEY:
        log("❌ 没有配置 LLM_API_KEY。")
        log("")
        log("   把密钥写进 backend/.env：")
        log("       LLM_API_KEY=sk-xxxxxxxx")
        log("")
        log(f"   当前配置：{settings.LLM_BASE_URL}  model={settings.LLM_MODEL}")
        sys.exit(1)


# ─────────────────────────────────────────────────────────────
# LLM 调用
# ─────────────────────────────────────────────────────────────


def parse_llm_json(content: str) -> dict[str, str]:
    """解析 LLM 返回的 JSON，容忍 markdown 代码块包裹。"""
    text = content.strip()

    # 去掉 ```json ... ``` 包裹
    if text.startswith("```"):
        lines = text.split("\n")
        lines = lines[1:]                       # 去掉 ```json
        if lines and lines[-1].strip() == "```":
            lines = lines[:-1]
        text = "\n".join(lines).strip()

    data = json.loads(text)
    if not isinstance(data, dict):
        raise ValueError(f"期望 JSON 对象，得到 {type(data).__name__}")
    return {str(k): str(v) for k, v in data.items()}


async def tag_batch(
    client: httpx.AsyncClient,
    items: list[tuple[str, str]],
    stats: Stats,
) -> dict[str, str]:
    """给一批词打标。返回 {word: 已校验的话题}，失败返回空字典。"""
    payload = {
        "model": settings.LLM_MODEL,
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": build_user_prompt(items)},
        ],
        "temperature": 0,          # 打标要可复现，不要创造性
        "response_format": {"type": "json_object"},
    }

    for attempt in range(MAX_RETRIES):
        try:
            r = await client.post("/chat/completions", json=payload)

            if r.status_code == 429:
                # DeepSeek 的 429 表示**瞬时并发超限**（账号粒度），不是配额耗尽 ——
                # 稍等即可，不需要长退避。
                await asyncio.sleep(1.5 * (attempt + 1))
                continue
            if r.status_code == 401:
                log("\n❌ 401 未授权 —— LLM_API_KEY 无效或已过期")
                sys.exit(1)
            r.raise_for_status()

            body = r.json()
            usage = body.get("usage") or {}
            stats.prompt_tokens += usage.get("prompt_tokens", 0)
            stats.completion_tokens += usage.get("completion_tokens", 0)

            content = body["choices"][0]["message"]["content"]
            raw = parse_llm_json(content)

            # ── 校验：只接受白名单内的话题 ──
            result: dict[str, str] = {}
            wanted = {w for w, _ in items}

            for word, label in raw.items():
                if word not in wanted:
                    continue                        # LLM 凭空造的词，忽略
                topic = normalize_topic(label)
                if topic:
                    result[word] = topic
                else:
                    stats.rejected += 1
                    stats.bad_labels[label] += 1

            missing = wanted - set(raw)
            stats.missing += len(missing)

            return result

        except Exception as exc:  # noqa: BLE001 —— 网络/解析异常种类多，统一重试
            if attempt == MAX_RETRIES - 1:
                stats.api_failed += 1
                sample = ", ".join(w for w, _ in items[:3])
                stats.failures.append(f"[{sample}…] {type(exc).__name__}: {exc}")
                return {}
            await asyncio.sleep(2 ** attempt)

    return {}


# ─────────────────────────────────────────────────────────────
# 主流程
# ─────────────────────────────────────────────────────────────


async def run_tagging(
    limit: int | None,
    batch_size: int,
    concurrency: int,
    stats: Stats,
) -> None:
    async with AsyncSessionLocal() as session:
        stmt = (
            select(Word.id, Word.word, Word.meaning_primary)
            .where(Word.topic.is_(None))
            .order_by(Word.frq.nulls_last(), Word.word)   # 高频优先
        )
        if limit:
            stmt = stmt.limit(limit)
        todo = list((await session.execute(stmt)).all())

    if not todo:
        log("   没有待打标的词（都打过了）")
        return

    batches = [todo[i:i + batch_size] for i in range(0, len(todo), batch_size)]
    log(f"   待打标 {len(todo):,} 词 → {len(batches)} 批（每批 {batch_size}），并发 {concurrency}")
    log(f"   模型 {settings.LLM_MODEL} @ {settings.LLM_BASE_URL}")
    log("   （随时可 Ctrl-C 中断，重跑会从断点继续）")

    sem = asyncio.Semaphore(concurrency)
    started = time.monotonic()
    done_batches = 0
    lock = asyncio.Lock()

    async def handle(chunk: list) -> list[dict]:
        nonlocal done_batches
        items = [(w, m) for _, w, m in chunk]
        by_word = {w: wid for wid, w, _ in chunk}

        async with sem:
            tagged = await tag_batch(client, items, stats)

        updates = [
            {"id": by_word[word], "topic": topic}
            for word, topic in tagged.items()
            if word in by_word
        ]

        async with lock:
            done_batches += 1
            stats.batches += 1
            stats.tagged += len(updates)
            if done_batches % 5 == 0 or done_batches == len(batches):
                elapsed = time.monotonic() - started
                rate = done_batches / elapsed if elapsed else 0
                eta = (len(batches) - done_batches) / rate / 60 if rate else 0
                log(f"   批 {done_batches:>3}/{len(batches)}  已标 {stats.tagged:,}  "
                    f"拒绝 {stats.rejected}  漏词 {stats.missing}  "
                    f"失败 {stats.api_failed}  剩余 ~{eta:.0f}min")

        return updates

    headers = {
        "Authorization": f"Bearer {settings.LLM_API_KEY}",
        "Content-Type": "application/json",
    }
    base = settings.LLM_BASE_URL.rstrip("/")
    if not base.endswith("/v1"):
        base += "/v1"

    # httpx 默认最多 100 个连接，并发高于此会在连接池排队 —— 要跟着放开
    limits = httpx.Limits(
        max_connections=max(concurrency + 10, 100),
        max_keepalive_connections=max(concurrency, 20),
    )

    async with httpx.AsyncClient(
        base_url=base, headers=headers, timeout=TIMEOUT, limits=limits
    ) as client:
        # 分组落库，中断不丢已完成的。
        #
        # ⚠️ GROUP 必须 ≥ concurrency，否则它会成为真正的并发上限，
        #    让 --concurrency 形同虚设（曾经写死 GROUP=10，把 128 并发卡到 10）。
        #    真正的限流器是上面那个 Semaphore。
        group_size = max(concurrency * 2, 20)
        for i in range(0, len(batches), group_size):
            group = batches[i:i + group_size]
            results = await asyncio.gather(*(handle(c) for c in group))
            flat = [u for r in results for u in r]
            if flat:
                async with AsyncSessionLocal() as session:
                    await session.execute(update(Word), flat)
                    await session.commit()

    log(f"✓  打标 {stats.tagged:,} · 拒绝 {stats.rejected} · 漏词 {stats.missing} "
        f"· 批失败 {stats.api_failed}")

    if stats.bad_labels:
        log(f"\n   被拒绝的标签（LLM 没照白名单输出）：")
        for label, cnt in stats.bad_labels.most_common(8):
            log(f"     {label!r} × {cnt}")


# ─────────────────────────────────────────────────────────────
# 状态与校验
# ─────────────────────────────────────────────────────────────


async def show_status() -> None:
    async with AsyncSessionLocal() as session:
        total = (await session.execute(select(func.count()).select_from(Word))).scalar_one()
        if not total:
            log("数据库里还没有单词。先跑 pnpm seed。")
            return

        tagged = (await session.execute(
            select(func.count()).select_from(Word).where(Word.topic.isnot(None))
        )).scalar_one()

        rows = (await session.execute(
            select(Word.topic, func.count())
            .where(Word.topic.isnot(None))
            .group_by(Word.topic)
            .order_by(func.count().desc())
        )).all()

    log(f"\n话题覆盖: {tagged:,}/{total:,} ({tagged / total * 100:.1f}%)")
    if tagged < total:
        log(f"待打标:   {total - tagged:,}")

    if not rows:
        return

    log(f"\n分布（{len(rows)} 个话题）:")
    for topic, cnt in rows:
        bar = "█" * max(1, round(cnt / max(c for _, c in rows) * 28))
        flag = "  ← 兜底" if topic == TOPIC_GENERAL else ""
        log(f"  {topic:12s} {cnt:>5,}  {cnt / tagged * 100:5.1f}%  {bar}{flag}")

    # 干扰项需要同话题内至少 4 个词（1 正确 + 3 干扰）
    thin = [(t, c) for t, c in rows if c < 4]
    if thin:
        log(f"\n⚠️  词数不足 4 的话题（干扰项会退化到兜底逻辑）:")
        for t, c in thin:
            log(f"     {t}: {c}")

    unknown = [t for t, _ in rows if t not in TOPICS]
    if unknown:
        log(f"\n⚠️  白名单外的话题（不该出现）: {unknown}")


async def review_sample(n: int) -> None:
    """随机抽样，供人工核对标签质量。

    这是路线图上**唯一未验证的风险项**，打完标必须走一遍。
    """
    async with AsyncSessionLocal() as session:
        rows = (await session.execute(
            select(Word.word, Word.meaning_primary, Word.topic, Word.part_of_speech)
            .where(Word.topic.isnot(None))
            .order_by(func.random())
            .limit(n)
        )).all()

    if not rows:
        log("还没有已打标的词。")
        return

    log(f"\n═══ 随机抽样 {len(rows)} 条，请人工核对 ═══\n")
    log(f"{'单词':<20} {'词性':<6} {'话题':<12} 释义")
    log("─" * 78)
    for word, meaning, topic, pos in rows:
        log(f"{word:<20} {pos or '-':<6} {topic:<12} {(meaning or '')[:28]}")

    log(f"""
─── 怎么判断 ───
问自己：这个词在雅思听力/阅读/写作里，最常出现在这个话题的语境里吗？

  ✅ 合理：environment→环境、tuition→教育、surgery→健康与医疗
  ✅ 合理：abundant→通用/抽象（话题中立的词就该进兜底）
  ❌ 可疑：具体领域词进了「通用/抽象」，或明显串了话题

若错误率超过约 10%，考虑：
  1. 调整 app/scripts/topics.py 里的 TOPIC_HINTS（边界说明）
  2. 改 SYSTEM_PROMPT
  3. 用 --retag <话题> 重打某一类
改完记得把校验结果记进 docs/06-dev-log.md。""")


async def retag_topic(topic: str) -> int:
    """把某个话题下的词清空 topic，以便重打。"""
    async with AsyncSessionLocal() as session:
        result = await session.execute(
            update(Word).where(Word.topic == topic).values(topic=None)
        )
        await session.commit()
        return result.rowcount or 0


# ─────────────────────────────────────────────────────────────
# 入口
# ─────────────────────────────────────────────────────────────


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="tag_topics",
        description="用 LLM 给单词打雅思话题标签（可断点续跑）",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    p.add_argument("--limit", type=int, help="最多处理多少词（试跑用）")
    p.add_argument("--batch", type=int, default=DEFAULT_BATCH,
                   help=f"每次请求塞多少词，默认 {DEFAULT_BATCH}")
    p.add_argument("--concurrency", type=int, default=DEFAULT_CONCURRENCY,
                   help=f"并发请求数，默认 {DEFAULT_CONCURRENCY}"
                        f"（DeepSeek v4-flash 上限 2500，可放心调高）")
    p.add_argument("--status", action="store_true", help="只看进度与分布")
    p.add_argument("--review", type=int, metavar="N",
                   help="随机抽 N 条供人工核对")
    p.add_argument("--retag", metavar="话题",
                   help="清空某话题下的标签以便重打")
    return p


async def main_async(args: argparse.Namespace) -> int:
    try:
        if args.status:
            await show_status()
            return 0

        if args.review:
            await review_sample(args.review)
            return 0

        if args.retag:
            if args.retag not in TOPICS:
                log(f"❌ 「{args.retag}」不在话题白名单里。可用话题：")
                for t in TOPICS:
                    log(f"     {t}")
                return 1
            n = await retag_topic(args.retag)
            log(f"✓  已清空「{args.retag}」下 {n:,} 个词的标签，重跑本脚本即可重打。")
            return 0

        require_api_key()

        stats = Stats()
        started = time.monotonic()
        log("\n═══ LLM 话题打标 ═══")

        try:
            await run_tagging(args.limit, args.batch, args.concurrency, stats)
        except KeyboardInterrupt:
            log("\n⚠  已中断。进度已保存，重跑同一命令即可从断点继续。")
            return 130

        log(f"\n═══ 完成（{(time.monotonic() - started) / 60:.1f} 分钟）═══")
        log(f"   token 用量：prompt {stats.prompt_tokens:,} + "
            f"completion {stats.completion_tokens:,}")

        await show_status()

        if stats.failures:
            log(f"\n⚠  {len(stats.failures)} 批失败（重跑会自动重试），前 5 个：")
            for f in stats.failures[:5]:
                log(f"     {f}")

        log("\n👉 下一步：抽样人工校验")
        log("   backend/.venv/bin/python -m app.scripts.tag_topics --review 40")

        return 0
    finally:
        # engine 必须在本循环内销毁（见 BUG-006）
        await engine.dispose()


def main() -> int:
    args = build_parser().parse_args()
    return asyncio.run(main_async(args))


if __name__ == "__main__":
    sys.exit(main())
