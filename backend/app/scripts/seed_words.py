"""词库导入脚本 —— 把 ECDICT 的 5,040 个雅思词导入数据库并补齐音标与音频。

═══════════════════════════════════════════════════════════════════════
用法
═══════════════════════════════════════════════════════════════════════

    # 先起数据库
    pnpm db:up

    # 小样本试跑（推荐第一次这么做）
    backend/.venv/bin/python -m app.scripts.seed_words --limit 20

    # 全量（阶段 2 约 75 分钟）
    backend/.venv/bin/python -m app.scripts.seed_words

    # 只跑某个阶段
    backend/.venv/bin/python -m app.scripts.seed_words --stage 1
    backend/.venv/bin/python -m app.scripts.seed_words --stage 2 --limit 100

    # 看进度，不做任何修改
    backend/.venv/bin/python -m app.scripts.seed_words --status

═══════════════════════════════════════════════════════════════════════
三个阶段
═══════════════════════════════════════════════════════════════════════

阶段 1  解析 ECDICT csv → 插入基础数据                      几秒
        词形 / 完整释义 / 首义 / 词性 / 考试标签 / 词频 / 难度
        音标先填清洗版 ECDICT 作为兜底

阶段 2  逐词调 dictionaryapi.dev → 真 IPA + 下载真人音频     ~75 分钟
        实测覆盖：IPA 96%、音频 76%

阶段 3  剩下没音频的用 edge-tts 合成                        几分钟
        音频补至 100%

═══════════════════════════════════════════════════════════════════════
断点续跑（关键设计）
═══════════════════════════════════════════════════════════════════════

不用单独的状态文件 —— 拿 words.audio_source 当状态机，**重跑即续跑**：

    NULL          还没调过 API（或上次调用失败）  → 阶段 2 处理
    pending-tts   调过了，但该词 API 没有音频      → 阶段 3 处理
    dictapi       已有真人发音 ✓                  完成
    edge-tts      已有 TTS 音频 ✓                 完成

阶段 1 靠 (word_list_id, word) 唯一约束幂等，重复跑不会插重复行。

所以 Ctrl-C 打断、断网、关机都不怕：直接重新跑同一条命令即可。
"""

from __future__ import annotations

import argparse
import asyncio
import csv
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import quote

import httpx
from sqlalchemy import func, select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.database import AsyncSessionLocal, engine
from app.models import Word, WordList
from app.scripts.ecdict import (
    audio_filename,
    build_meanings,
    clean_ecdict_phonetic,
    derive_difficulty,
    has_ielts_tag,
    is_redundant_inflection,
    normalize_api_phonetic,
    parse_int,
    parse_part_of_speech,
)

# ─────────────────────────────────────────────────────────────
# 配置
# ─────────────────────────────────────────────────────────────

BACKEND_DIR = Path(__file__).resolve().parents[2]

#: 提交进仓库的雅思子集（1.5MB，5,040 行）。**首选数据源** ——
#: fresh clone 离线就能跑，且等于锁定了数据版本，文档里的实测数字永远对得上。
#: 见 docs/08-decisions.md ADR-011
SUBSET_PATH = BACKEND_DIR / "data" / "ecdict-ielts.csv"
#: 完整 ECDICT（66MB，770,611 行）。不进 git，只是构建缓存。
FULL_PATH = BACKEND_DIR / "data" / "ecdict.csv"
#: 兜底下载源。锁 commit SHA 而非 master —— 上游更新不会静默改变我们的数据。
ECDICT_COMMIT = "bc015ed2e24a7abef49fc6dbbb7fe32c1dadaf8b"
ECDICT_URL = (
    f"https://raw.githubusercontent.com/skywind3000/ECDICT/{ECDICT_COMMIT}/ecdict.csv"
)

WORD_LIST_NAME = "雅思核心词汇（ECDICT）"
WORD_LIST_DESC = (
    "来自 ECDICT（MIT 许可）tag 含 ielts 的全部词条。"
    "音标优先取 dictionaryapi.dev 的标准 IPA，音频为真人发音 + edge-tts 补齐。"
)

DICT_API = "https://api.dictionaryapi.dev/api/v2/entries/en/{word}"

#: audio_source 的状态值。前两个是终态，pending-tts 是中间态。
SRC_DICTAPI = "dictapi"
SRC_EDGE_TTS = "edge-tts"
SRC_PENDING_TTS = "pending-tts"

#: 并发数。dictionaryapi.dev 是免费社区服务，别开太大 —— 我们是客人。
DEFAULT_CONCURRENCY = 4
#: 每个请求后的额外等待（秒），进一步降低压力
DEFAULT_DELAY = 0.1
#: 单请求超时
TIMEOUT = 10.0
#: 失败重试次数（网络抖动很常见）
MAX_RETRIES = 3

#: edge-tts 音色。en-GB 是雅思考试的主要口音。
TTS_VOICE = "en-GB-SoniaNeural"


# ─────────────────────────────────────────────────────────────
# 工具
# ─────────────────────────────────────────────────────────────


def log(msg: str) -> None:
    print(msg, flush=True)


@dataclass
class Stats:
    """跑完打印的统计。"""

    inserted: int = 0
    skipped: int = 0
    api_ok: int = 0
    api_no_audio: int = 0
    api_not_found: int = 0
    api_failed: int = 0
    ipa_updated: int = 0
    audio_downloaded: int = 0
    tts_generated: int = 0
    tts_failed: int = 0
    failures: list[str] = field(default_factory=list)


def audio_dir() -> Path:
    d = BACKEND_DIR / settings.AUDIO_DIR
    d.mkdir(parents=True, exist_ok=True)
    return d


# ─────────────────────────────────────────────────────────────
# 阶段 0：准备 ECDICT csv
# ─────────────────────────────────────────────────────────────


def ensure_ecdict() -> Path:
    """定位词库源数据，按优先级三选一。

    1. data/ecdict-ielts.csv  —— 提交进仓库的子集，1.5MB。**fresh clone 直接命中这个**
    2. data/ecdict.csv        —— 完整版（66MB），若之前下载过
    3. 下载完整版             —— 都没有才走网络，且锁定 commit SHA

    这个顺序保证了：clone 下来不联网也能跑，同时保留"想用完整版"的余地。
    """
    if SUBSET_PATH.exists() and SUBSET_PATH.stat().st_size > 100_000:
        log(f"   数据源：{SUBSET_PATH.name}（仓库内雅思子集）")
        return SUBSET_PATH

    if FULL_PATH.exists() and FULL_PATH.stat().st_size > 1_000_000:
        log(f"   数据源：{FULL_PATH.name}（完整 ECDICT）")
        return FULL_PATH

    FULL_PATH.parent.mkdir(parents=True, exist_ok=True)
    log(f"⬇  未找到本地数据，下载完整 ECDICT（66MB）→ {FULL_PATH}")
    log(f"   源：ECDICT @ {ECDICT_COMMIT[:12]}")
    tmp = FULL_PATH.with_suffix(".csv.part")
    with httpx.stream("GET", ECDICT_URL, timeout=180.0, follow_redirects=True) as r:
        r.raise_for_status()
        with tmp.open("wb") as f:
            for chunk in r.iter_bytes(chunk_size=1 << 20):
                f.write(chunk)
    tmp.rename(FULL_PATH)   # 原子替换，避免半个文件被误认为下载成功
    log(f"✓  下载完成（{FULL_PATH.stat().st_size / 1e6:.0f}MB）")
    return FULL_PATH


def read_ielts_rows(path: Path, limit: int | None = None) -> tuple[list[dict], int]:
    """读出 tag 含 ielts 的行，转成待插入的字典。

    返回 (待插入行, 被剔除的冗余屈折形式数量)。

    ⚠️ 必须**两趟扫描**：要判断某个屈折形式的原型是否也在词表里，
       得先知道词表全集。一趟扫描做不到。
    """
    csv.field_size_limit(10**9)   # ECDICT 有超长字段

    # ── 第一趟：收集所有 ielts 词形 ──
    all_words: set[str] = set()
    with path.open(newline="", encoding="utf-8") as f:
        for raw in csv.DictReader(f):
            if has_ielts_tag(raw.get("tag")):
                all_words.add(raw["word"].strip())

    # ── 第二趟：转换并剔除冗余屈折形式 ──
    rows: list[dict] = []
    dropped = 0

    with path.open(newline="", encoding="utf-8") as f:
        for raw in csv.DictReader(f):
            if not has_ielts_tag(raw.get("tag")):
                continue

            word = raw["word"].strip()

            # 剔除原型也在词表里的屈折形式（如 accommodations，因为
            # accommodation 已在表中）。原型不在表里的保留，否则纯丢词。
            if is_redundant_inflection(word, raw.get("exchange"), all_words):
                dropped += 1
                continue

            translation = raw.get("translation") or ""
            meaning, meaning_primary = build_meanings(translation)
            if not meaning:
                continue   # 没释义的词没法出题

            rows.append({
                "word": word,
                "meaning": meaning,
                "meaning_primary": meaning_primary,
                # 先用清洗版 ECDICT 兜底，阶段 2 会用真 IPA 覆盖
                "phonetic": clean_ecdict_phonetic(raw.get("phonetic")),
                "part_of_speech": parse_part_of_speech(translation),
                "exam_tags": (raw.get("tag") or "").strip() or None,
                "bnc": parse_int(raw.get("bnc")),
                "frq": parse_int(raw.get("frq")),
                "difficulty": derive_difficulty(raw.get("tag")),
            })

            if limit and len(rows) >= limit:
                break

    return rows, dropped


# ─────────────────────────────────────────────────────────────
# 阶段 1：导入基础数据
# ─────────────────────────────────────────────────────────────


async def get_or_create_word_list(session: AsyncSession) -> WordList:
    wl = (await session.execute(
        select(WordList).where(WordList.name == WORD_LIST_NAME)
    )).scalar_one_or_none()

    if wl is None:
        wl = WordList(name=WORD_LIST_NAME, description=WORD_LIST_DESC, is_public=True)
        session.add(wl)
        await session.commit()
        log(f"✓  创建词库「{WORD_LIST_NAME}」")
    return wl


async def stage1_import(limit: int | None, stats: Stats) -> None:
    """解析 csv → 批量插入。靠唯一约束幂等，可重复跑。"""
    log("\n═══ 阶段 1：导入基础数据 ═══")
    path = ensure_ecdict()
    rows, dropped = read_ielts_rows(path, limit)
    log(f"   从 ECDICT 筛出 {len(rows):,} 个 ielts 词条"
        f"（已剔除 {dropped:,} 个冗余屈折形式，如 accommodations←accommodation）")

    async with AsyncSessionLocal() as session:
        wl = await get_or_create_word_list(session)

        before = (await session.execute(
            select(func.count()).select_from(Word).where(Word.word_list_id == wl.id)
        )).scalar_one()

        # 分批插入，ON CONFLICT DO NOTHING 让重复跑变成无害操作
        BATCH = 500
        for i in range(0, len(rows), BATCH):
            batch = [{**r, "word_list_id": wl.id} for r in rows[i:i + BATCH]]
            await session.execute(
                pg_insert(Word)
                .values(batch)
                .on_conflict_do_nothing(constraint="uq_words_list_word")
            )
            await session.commit()
            log(f"   {min(i + BATCH, len(rows)):>5,}/{len(rows):,}")

        after = (await session.execute(
            select(func.count()).select_from(Word).where(Word.word_list_id == wl.id)
        )).scalar_one()

    stats.inserted = after - before
    stats.skipped = len(rows) - stats.inserted
    log(f"✓  新增 {stats.inserted:,} 条，跳过已存在 {stats.skipped:,} 条")


# ─────────────────────────────────────────────────────────────
# 阶段 2：dictionaryapi.dev 取 IPA + 下载音频
# ─────────────────────────────────────────────────────────────


def extract_from_api(payload: object) -> tuple[str | None, str | None]:
    """从 API 响应里挑出 (IPA, 音频 URL)。

    响应结构是 list[entry]，每个 entry 有 phonetic 和 phonetics[]。
    不同词的字段填充位置不一致，所以要逐层找。
    """
    if not isinstance(payload, list):
        return None, None

    ipa: str | None = None
    audio: str | None = None

    for entry in payload:
        if not isinstance(entry, dict):
            continue

        if ipa is None and entry.get("phonetic"):
            ipa = entry["phonetic"]

        for ph in entry.get("phonetics") or []:
            if not isinstance(ph, dict):
                continue
            if ipa is None and ph.get("text"):
                ipa = ph["text"]
            if audio is None and ph.get("audio"):
                audio = ph["audio"]

        if ipa and audio:
            break

    return ipa, audio


async def fetch_one(
    client: httpx.AsyncClient,
    word: str,
) -> tuple[str, str | None, bytes | None, str]:
    """查一个词。返回 (状态, IPA, 音频字节, 备注)。

    状态：ok / no-audio / not-found / failed
    """
    url = DICT_API.format(word=quote(word))

    for attempt in range(MAX_RETRIES):
        try:
            r = await client.get(url)

            if r.status_code == 404:
                return "not-found", None, None, ""
            if r.status_code == 429:
                # 被限流了，退避后重试
                await asyncio.sleep(2 ** attempt * 2)
                continue
            r.raise_for_status()

            ipa, audio_url = extract_from_api(r.json())
            ipa = normalize_api_phonetic(ipa)

            if not audio_url:
                return "no-audio", ipa, None, ""

            if audio_url.startswith("//"):
                audio_url = "https:" + audio_url

            ar = await client.get(audio_url)
            ar.raise_for_status()
            if not ar.content:
                return "no-audio", ipa, None, "音频响应为空"

            return "ok", ipa, ar.content, ""

        except Exception as exc:  # noqa: BLE001 —— 网络异常种类多，统一重试
            if attempt == MAX_RETRIES - 1:
                return "failed", None, None, f"{type(exc).__name__}: {exc}"
            await asyncio.sleep(2 ** attempt)

    return "failed", None, None, "重试耗尽"


async def stage2_enrich(
    limit: int | None,
    concurrency: int,
    delay: float,
    stats: Stats,
) -> None:
    """给还没处理过的词补 IPA 和音频。

    只挑 audio_source IS NULL 的 —— 这就是断点续跑的全部机制。
    """
    log("\n═══ 阶段 2：dictionaryapi.dev 取 IPA + 音频 ═══")

    async with AsyncSessionLocal() as session:
        stmt = (
            select(Word.id, Word.word)
            .where(Word.audio_source.is_(None))
            .order_by(Word.frq.nulls_last(), Word.word)   # 高频词优先，中断了也先有常用词
        )
        if limit:
            stmt = stmt.limit(limit)
        todo = list((await session.execute(stmt)).all())

    if not todo:
        log("   没有待处理的词（都处理过了）")
        return

    est = len(todo) * (delay + 0.9) / concurrency
    log(f"   待处理 {len(todo):,} 个词，并发 {concurrency}，预计 {est / 60:.0f} 分钟")
    log("   （随时可 Ctrl-C 中断，重跑会从断点继续）")

    adir = audio_dir()
    sem = asyncio.Semaphore(concurrency)
    done = 0
    started = time.monotonic()
    lock = asyncio.Lock()

    async def handle(client: httpx.AsyncClient, word_id, word: str) -> dict | None:
        nonlocal done
        async with sem:
            status, ipa, audio_bytes, note = await fetch_one(client, word)
            await asyncio.sleep(delay)

        values: dict = {"id": word_id}

        if status == "ok" and audio_bytes:
            fname = audio_filename(word)
            (adir / fname).write_bytes(audio_bytes)
            values["audio_url"] = f"/static/audio/{fname}"
            values["audio_source"] = SRC_DICTAPI
            stats.api_ok += 1
            stats.audio_downloaded += 1
        elif status in ("no-audio", "not-found"):
            # 标记成待 TTS，阶段 3 会捡起来。这样重跑不会再查一遍。
            values["audio_source"] = SRC_PENDING_TTS
            if status == "no-audio":
                stats.api_no_audio += 1
            else:
                stats.api_not_found += 1
        else:
            # 失败就**不写 audio_source**，保持 NULL，下次重跑会重试
            stats.api_failed += 1
            stats.failures.append(f"{word}: {note}")
            return None

        if ipa:
            values["phonetic"] = ipa
            stats.ipa_updated += 1

        async with lock:
            done += 1
            if done % 25 == 0 or done == len(todo):
                elapsed = time.monotonic() - started
                rate = done / elapsed if elapsed else 0
                eta = (len(todo) - done) / rate / 60 if rate else 0
                log(f"   {done:>5,}/{len(todo):,}  "
                    f"音频 {stats.audio_downloaded:,}  待TTS {stats.api_no_audio + stats.api_not_found:,}  "
                    f"失败 {stats.api_failed:,}  剩余 ~{eta:.0f}min")

        return values

    headers = {"User-Agent": "IELTS-Vocab-seed/0.1 (personal study project)"}
    async with httpx.AsyncClient(timeout=TIMEOUT, headers=headers, follow_redirects=True) as client:
        # 分批处理并及时落库 —— 不要攒到最后，否则中断就白跑
        BATCH = 100
        for i in range(0, len(todo), BATCH):
            chunk = todo[i:i + BATCH]
            results = await asyncio.gather(
                *(handle(client, wid, w) for wid, w in chunk)
            )
            updates = [r for r in results if r]
            if updates:
                async with AsyncSessionLocal() as session:
                    await session.execute(update(Word), updates)
                    await session.commit()

    log(f"✓  真人音频 {stats.audio_downloaded:,} · 待 TTS "
        f"{stats.api_no_audio + stats.api_not_found:,} · 失败 {stats.api_failed:,} "
        f"· IPA 更新 {stats.ipa_updated:,}")


# ─────────────────────────────────────────────────────────────
# 阶段 3：edge-tts 兜底
# ─────────────────────────────────────────────────────────────


async def stage3_tts(limit: int | None, concurrency: int, stats: Stats) -> None:
    """给 API 没音频的词用 edge-tts 合成。"""
    log("\n═══ 阶段 3：edge-tts 补齐音频 ═══")

    import edge_tts   # 延迟导入：只跑阶段 1/2 时不必装

    async with AsyncSessionLocal() as session:
        stmt = select(Word.id, Word.word).where(Word.audio_source == SRC_PENDING_TTS)
        if limit:
            stmt = stmt.limit(limit)
        todo = list((await session.execute(stmt)).all())

    if not todo:
        log("   没有待合成的词")
        return

    log(f"   待合成 {len(todo):,} 个词，音色 {TTS_VOICE}")

    adir = audio_dir()
    sem = asyncio.Semaphore(concurrency)

    async def synth(word_id, word: str) -> dict | None:
        async with sem:
            fname = audio_filename(word)
            path = adir / fname
            for attempt in range(MAX_RETRIES):
                try:
                    await edge_tts.Communicate(word, TTS_VOICE).save(str(path))
                    if path.exists() and path.stat().st_size > 0:
                        stats.tts_generated += 1
                        return {
                            "id": word_id,
                            "audio_url": f"/static/audio/{fname}",
                            "audio_source": SRC_EDGE_TTS,
                        }
                    raise RuntimeError("生成的文件为空")
                except Exception as exc:  # noqa: BLE001
                    if attempt == MAX_RETRIES - 1:
                        stats.tts_failed += 1
                        stats.failures.append(f"{word} (tts): {type(exc).__name__}: {exc}")
                        return None
                    await asyncio.sleep(2 ** attempt)
        return None

    BATCH = 50
    for i in range(0, len(todo), BATCH):
        chunk = todo[i:i + BATCH]
        results = await asyncio.gather(*(synth(wid, w) for wid, w in chunk))
        updates = [r for r in results if r]
        if updates:
            async with AsyncSessionLocal() as session:
                await session.execute(update(Word), updates)
                await session.commit()
        log(f"   {min(i + BATCH, len(todo)):>5,}/{len(todo):,}  "
            f"成功 {stats.tts_generated:,}  失败 {stats.tts_failed:,}")

    log(f"✓  TTS 合成 {stats.tts_generated:,} · 失败 {stats.tts_failed:,}")


# ─────────────────────────────────────────────────────────────
# 状态报告
# ─────────────────────────────────────────────────────────────


async def show_status() -> None:
    """打印当前进度，不做任何修改。"""
    async with AsyncSessionLocal() as session:
        total = (await session.execute(select(func.count()).select_from(Word))).scalar_one()
        if not total:
            log("数据库里还没有单词。先跑：python -m app.scripts.seed_words")
            return

        rows = (await session.execute(
            select(Word.audio_source, func.count())
            .group_by(Word.audio_source)
            .order_by(func.count().desc())
        )).all()

        with_ipa = (await session.execute(
            select(func.count()).select_from(Word).where(Word.phonetic.isnot(None))
        )).scalar_one()
        with_topic = (await session.execute(
            select(func.count()).select_from(Word).where(Word.topic.isnot(None))
        )).scalar_one()

    log(f"\n单词总数: {total:,}\n")
    log("音频状态:")
    labels = {
        SRC_DICTAPI: "✓ 真人发音 (dictapi)",
        SRC_EDGE_TTS: "✓ TTS 合成 (edge-tts)",
        SRC_PENDING_TTS: "→ 待阶段 3 合成",
        None: "→ 待阶段 2 处理",
    }
    for src, cnt in rows:
        log(f"  {labels.get(src, src or '?'):28s} {cnt:>6,}  {cnt / total * 100:5.1f}%")

    log(f"\n音标覆盖: {with_ipa:,}/{total:,} ({with_ipa / total * 100:.1f}%)")
    log(f"话题覆盖: {with_topic:,}/{total:,} ({with_topic / total * 100:.1f}%)"
        f"{'   ← 需跑 tag_topics.py' if with_topic < total else ''}")

    audio_files = list(audio_dir().glob("*.mp3"))
    size_mb = sum(p.stat().st_size for p in audio_files) / 1e6
    log(f"\n音频文件: {len(audio_files):,} 个，{size_mb:.1f} MB")


# ─────────────────────────────────────────────────────────────
# 入口
# ─────────────────────────────────────────────────────────────


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="seed_words",
        description="导入 ECDICT 雅思词库并补齐音标与音频（可断点续跑）",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    p.add_argument("--stage", type=int, choices=[1, 2, 3],
                   help="只跑指定阶段，默认全跑")
    p.add_argument("--limit", type=int,
                   help="最多处理多少词（试跑用）")
    p.add_argument("--concurrency", type=int, default=DEFAULT_CONCURRENCY,
                   help=f"并发请求数，默认 {DEFAULT_CONCURRENCY}（别调太高，人家是免费服务）")
    p.add_argument("--delay", type=float, default=DEFAULT_DELAY,
                   help=f"每请求后额外等待秒数，默认 {DEFAULT_DELAY}")
    p.add_argument("--status", action="store_true",
                   help="只看进度，不做修改")
    return p


async def main_async(args: argparse.Namespace) -> int:
    # ⚠️ engine 必须在**本循环内**销毁。
    # 曾经写成 finally: asyncio.run(engine.dispose())，那会新建第二个事件循环去关
    # 属于第一个循环的连接 → "Event loop is closed"（同 BUG-005）。
    try:
        if args.status:
            await show_status()
            return 0

        stats = Stats()
        started = time.monotonic()

        try:
            if args.stage in (None, 1):
                await stage1_import(args.limit, stats)
            if args.stage in (None, 2):
                await stage2_enrich(args.limit, args.concurrency, args.delay, stats)
            if args.stage in (None, 3):
                await stage3_tts(args.limit, args.concurrency, stats)
        except KeyboardInterrupt:
            log("\n⚠  已中断。进度已保存，重跑同一命令即可从断点继续。")
            return 130

        log(f"\n═══ 完成（{(time.monotonic() - started) / 60:.1f} 分钟）═══")
        await show_status()

        if stats.failures:
            log(f"\n⚠  {len(stats.failures)} 个失败（重跑会自动重试），前 10 个：")
            for f in stats.failures[:10]:
                log(f"     {f}")

        return 0
    finally:
        await engine.dispose()


def main() -> int:
    args = build_parser().parse_args()
    return asyncio.run(main_async(args))


if __name__ == "__main__":
    sys.exit(main())
