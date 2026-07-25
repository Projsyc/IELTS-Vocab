"""进度路由 —— 对应 docs/04-api-design.md §5。"""

from __future__ import annotations

import time
from datetime import UTC, datetime

from fastapi import APIRouter, Query

from app.core.deps import CurrentUser, DbSession
from app.models import PracticeMode
from app.schemas.progress import (
    DailyStatsOut,
    ProgressSummaryOut,
    RebuildRequest,
    RebuildResponse,
    WrongWordOut,
    WrongWordsPage,
)
from app.services import progress as progress_service

router = APIRouter(prefix="/api/progress", tags=["进度"])

#: 时区偏移的合法范围（分钟）。UTC-12 ~ UTC+14 覆盖全部现行时区。
_TZ_MIN, _TZ_MAX = -720, 840


@router.get("/summary", response_model=ProgressSummaryOut, summary="学习总览")
async def summary(
    current_user: CurrentUser,
    db: DbSession,
    tz_offset_minutes: int = Query(
        default=0,
        alias="tzOffsetMinutes",   # 与 body 的 camelCase 保持一致
        ge=_TZ_MIN,
        le=_TZ_MAX,
        description="客户端时区偏移（分钟，东为正）。JS: -new Date().getTimezoneOffset()",
    ),
) -> ProgressSummaryOut:
    """连续天数、今日答题量、盒子分布、到期待复习数。

    ⚠️ "今日"和"连续天数"按**客户端本地日期**切分。不传 `tzOffsetMinutes`
       就按 UTC 算 —— 东八区用户会把早上 8 点前的学习记到前一天。
    """
    result = await progress_service.build_summary(
        db, current_user.id, datetime.now(UTC), tz_offset_minutes
    )

    return ProgressSummaryOut(
        streak_days=result.streak_days,
        today=DailyStatsOut(
            answered=result.today.answered,
            correct=result.today.correct,
            accuracy=result.today.accuracy,
        ),
        total_answered=result.total_answered,
        boxes={
            mode.value: {str(box): count for box, count in dist.items()}
            for mode, dist in result.boxes.items()
        },
        due_now={mode.value: count for mode, count in result.due_now.items()},
    )


@router.get("/wrong-words", response_model=WrongWordsPage, summary="错题本")
async def wrong_words(
    current_user: CurrentUser,
    db: DbSession,
    mode: PracticeMode | None = Query(default=None, description="不传则两种模式都返回"),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
) -> WrongWordsPage:
    """错题本 —— 从 `answer_events` 聚合，按错误次数降序。

    `recentInputs` 是最近几次答错时的输入，能看出总是怎么拼错的。
    这是事件溯源"免费送"的能力：只存最终进度的话做不到。
    """
    entries, total = await progress_service.list_wrong_words(
        db, current_user.id, mode, limit, offset
    )

    return WrongWordsPage(
        total=total,
        limit=limit,
        offset=offset,
        items=[
            WrongWordOut(
                word_id=e.word.id,
                word=e.word.word,
                meaning_primary=e.word.meaning_primary,
                phonetic=e.word.phonetic,
                mode=e.mode,
                wrong_count=e.wrong_count,
                last_wrong_at=e.last_wrong_at,
                recent_inputs=list(e.recent_inputs),
            )
            for e in entries
        ],
    )


@router.post("/rebuild", response_model=RebuildResponse, summary="从事件重算进度")
async def rebuild(
    payload: RebuildRequest, current_user: CurrentUser, db: DbSession
) -> RebuildResponse:
    """⭐ 把 `user_progress` 整个从 `answer_events` 重算出来。

    这个接口能存在，本身就是混合式事件溯源（ADR-002）的价值体现 ——
    进度表是**缓存**，删了能重建。

    用途：多端冲突后修复、改了 Leitner 参数需按新规则重建、怀疑数据脏了。

    顺带清理孤儿进度行（有进度但无对应事件）。
    """
    started = time.monotonic()
    result = await progress_service.rebuild_all_progress(
        db, current_user.id, payload.word_id
    )
    return RebuildResponse(
        rebuilt=result.rebuilt,
        removed=result.removed,
        events_read=result.events_read,
        duration_ms=round((time.monotonic() - started) * 1000),
    )
