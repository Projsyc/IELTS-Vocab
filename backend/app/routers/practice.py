"""练习路由 —— 对应 docs/04-api-design.md §4。

按项目约定，本层只收参数、调 service、组装响应，不写业务逻辑。
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

from fastapi import APIRouter, HTTPException, Query, status
from sqlalchemy import select

from app.core.deps import CurrentUser, DbSession
from app.models import PracticeMode, Word, WordList
from app.schemas.practice import (
    AnswerRequest,
    AnswerResponse,
    ChoiceOption,
    CreateSessionRequest,
    DiffCharOut,
    PracticeItemOut,
    PracticeSetOut,
    ProgressOut,
)
from app.services import practice as practice_service
from app.services import testing as testing_service
from app.services.practice import PracticeSet

router = APIRouter(prefix="/api/practice", tags=["练习"])


async def _resolve_word_list(db: DbSession, list_id: uuid.UUID | None) -> uuid.UUID:
    """定位词库。不传就用第一个公开词库（MVP 只有一个）。"""
    if list_id is not None:
        found = (
            await db.execute(select(WordList.id).where(WordList.id == list_id))
        ).scalar_one_or_none()
        if found is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, detail="词库不存在")
        return found

    default = (
        await db.execute(
            select(WordList.id)
            .where(WordList.is_public.is_(True))
            .order_by(WordList.created_at)
            .limit(1)
        )
    ).scalar_one_or_none()
    if default is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="还没有任何词库，请先跑 pnpm seed")
    return default


def _to_out(practice_set: PracticeSet) -> PracticeSetOut:
    """把 service 层结果转成响应模型。

    ⚠️ 刻意**不下发** `question.correct_index` —— 否则看 network 面板就能作弊。
    """
    items = []
    for item in practice_set.items:
        options = None
        if item.question is not None:
            options = [
                ChoiceOption(index=o.index, text=o.text) for o in item.question.options
            ]
        items.append(
            PracticeItemOut(
                word_id=item.word.id,
                word=item.word.word,
                phonetic=item.word.phonetic,
                meaning=item.word.meaning,
                audio_url=item.word.audio_url,
                part_of_speech=item.word.part_of_speech,
                box=item.box,
                options=options,
            )
        )

    return PracticeSetOut(
        mode=practice_set.mode,
        total=practice_set.total,
        review_count=practice_set.review_count,
        new_count=practice_set.new_count,
        items=items,
    )


@router.get("/daily", response_model=PracticeSetOut, summary="每日任务")
async def daily(
    current_user: CurrentUser,
    db: DbSession,
    mode: PracticeMode = Query(description="dictation | recognition"),
    list_id: uuid.UUID | None = Query(
        default=None, alias="listId", description="不传则用默认词库"
    ),
) -> PracticeSetOut:
    """系统自动挑词：到期复习词优先，再用新词补足到每日配额。"""
    word_list_id = await _resolve_word_list(db, list_id)
    result = await practice_service.build_daily_set(
        db, current_user, mode, word_list_id, datetime.now(UTC)
    )
    return _to_out(result)


@router.post("/session", response_model=PracticeSetOut, summary="自由练习")
async def create_session(
    payload: CreateSessionRequest, current_user: CurrentUser, db: DbSession
) -> PracticeSetOut:
    """用户自选词库、模式、数量、范围。"""
    word_list_id = await _resolve_word_list(db, payload.list_id)
    try:
        result = await practice_service.build_free_set(
            db,
            current_user,
            payload.mode,
            word_list_id,
            payload.count,
            payload.scope,
            payload.topic,
            datetime.now(UTC),
        )
    except ValueError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    return _to_out(result)


@router.post("/answer", response_model=AnswerResponse, summary="提交一次答题")
async def submit(
    payload: AnswerRequest, current_user: CurrentUser, db: DbSession
) -> AnswerResponse:
    """提交答题 —— 追加事件 + 更新进度缓存（ADR-002）。"""
    word = (
        await db.execute(select(Word).where(Word.id == payload.word_id))
    ).scalar_one_or_none()
    if word is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="单词不存在")

    if payload.answered_at.tzinfo is None:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            detail="answered_at 必须带时区信息",
        )

    # 测试模式：校验会话属于当前用户，否则客户端能编个 id
    # 把真实练习伪装成"不计入进度"的测试
    if payload.test_session_id is not None:
        session = await testing_service.get_test_session(
            db, current_user.id, payload.test_session_id
        )
        if session is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, detail="找不到这次测试")
        if session.mode is not payload.mode:
            raise HTTPException(
                status.HTTP_400_BAD_REQUEST,
                detail=f"这次测试是{session.mode.value}模式，不能提交{payload.mode.value}的答案",
            )

    outcome = await practice_service.submit_answer(
        db,
        current_user,
        word,
        payload.mode,
        payload.user_input,
        payload.answered_at,
        payload.device_id,
        payload.test_session_id,
    )

    diff = None
    if outcome.dictation is not None:
        diff = [
            DiffCharOut(
                pos=d.pos, char=d.char, status=d.status.value, expected=d.expected
            )
            for d in outcome.dictation.diff
        ]

    return AnswerResponse(
        is_correct=outcome.is_correct,
        correct_answer=outcome.correct_answer,
        diff=diff,
        progress=(
            None
            if outcome.progress is None
            else ProgressOut(
                box=outcome.progress.box,
                next_review_at=outcome.progress.next_review_at,
                correct_count=outcome.progress.correct_count,
                wrong_count=outcome.progress.wrong_count,
            )
        ),
        was_replayed=outcome.was_replayed,
        event_id=outcome.event_id,
    )
