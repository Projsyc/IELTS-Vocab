"""测试模式与标记的路由 —— 对应 ADR-013。"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

from fastapi import APIRouter, HTTPException, Query, status
from sqlalchemy import select

from app.core.deps import CurrentUser, DbSession
from app.models import PracticeMode, Word
from app.routers.practice import _resolve_word_list, _to_out
from app.schemas.practice import (
    CorrectRequest,
    CorrectResponse,
    ProgressOut,
    StarredPage,
    StarredWordOut,
    StarRequest,
    StartTestRequest,
    StartTestResponse,
    TestAnswerOut,
    TestDetailOut,
    TestSessionsPage,
    TestSummaryOut,
)
from app.services import testing as testing_service
from app.services.practice import rebuild_progress
from app.services.testing import TestSummary

router = APIRouter(prefix="/api", tags=["测试与标记"])


def _summary_out(s: TestSummary) -> TestSummaryOut:
    return TestSummaryOut(
        test_session_id=s.session.id,
        mode=s.session.mode,
        scope=s.session.scope,
        scope_value=s.session.scope_value,
        total=s.session.total,
        answered=s.answered,
        correct=s.correct,
        score=s.score,
        is_complete=s.is_complete,
        created_at=s.session.created_at,
    )


# ─────────────────────────────────────────────────────────────
# 测试模式
# ─────────────────────────────────────────────────────────────


@router.post("/practice/test", response_model=StartTestResponse, summary="开始一次测试")
async def start_test(
    payload: StartTestRequest, current_user: CurrentUser, db: DbSession
) -> StartTestResponse:
    """开始测试并出题。

    答题时要把返回的 `testSessionId` 带上 —— 服务端据此把这批答题
    标记为测试（不影响 Leitner 进度）并归到同一次考试。
    """
    word_list_id = await _resolve_word_list(db, payload.list_id)
    try:
        session, practice_set = await testing_service.start_test(
            db,
            current_user,
            payload.mode,
            word_list_id,
            payload.scope,
            payload.scope_value,
            payload.count,
        )
    except ValueError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc

    return StartTestResponse(
        test_session_id=session.id,
        mode=session.mode,
        scope=session.scope,
        scope_value=session.scope_value,
        total=session.total,
        items=_to_out(practice_set).items,
    )


@router.get("/progress/tests", response_model=TestSessionsPage, summary="测试历史")
async def list_tests(
    current_user: CurrentUser,
    db: DbSession,
    mode: PracticeMode | None = Query(default=None),
    limit: int = Query(default=20, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
) -> TestSessionsPage:
    summaries, total = await testing_service.list_test_sessions(
        db, current_user.id, mode, limit, offset
    )
    return TestSessionsPage(
        total=total,
        limit=limit,
        offset=offset,
        items=[_summary_out(s) for s in summaries],
    )


@router.get(
    "/progress/tests/{session_id}",
    response_model=TestDetailOut,
    summary="单次测试详情",
)
async def test_detail(
    session_id: uuid.UUID, current_user: CurrentUser, db: DbSession
) -> TestDetailOut:
    result = await testing_service.test_detail(db, current_user.id, session_id)
    if result is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="找不到这次测试")

    summary, answers = result
    return TestDetailOut(
        summary=_summary_out(summary),
        answers=[
            TestAnswerOut(
                word_id=w.id,
                word=w.word,
                meaning_primary=w.meaning_primary,
                user_input=e.user_input,
                is_correct=e.is_correct,
                answered_at=e.answered_at,
            )
            for w, e in answers
        ],
    )


# ─────────────────────────────────────────────────────────────
# 判我对
# ─────────────────────────────────────────────────────────────


@router.post("/practice/correct", response_model=CorrectResponse, summary="判我对")
async def correct(
    payload: CorrectRequest, current_user: CurrentUser, db: DbSession
) -> CorrectResponse:
    """把某次答错改判为对 —— 追加一条更正事件，不修改历史。

    限制（见 services/testing.py）：
      · 只能改自己的记录
      · 原本必须是答错
      · **测试模式不能用** —— 测试就是要如实反映水平
      · 只有听写模式能用，且编辑距离 ≤ 2（防止刷进度）
    """
    try:
        correction = await testing_service.correct_answer_event(
            db, current_user, payload.event_id, payload.answered_at
        )
    except ValueError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc

    # 更正改变的是过去某条事件的判定，增量表达不了 —— 必须全量重算
    snapshot = await rebuild_progress(
        db, current_user.id, correction.word_id, correction.mode
    )
    if snapshot is None:
        raise HTTPException(status.HTTP_500_INTERNAL_SERVER_ERROR, detail="重算进度失败")

    # 把重算结果写回缓存
    from app.models import UserProgress

    progress = (
        await db.execute(
            select(UserProgress).where(
                UserProgress.user_id == current_user.id,
                UserProgress.word_id == correction.word_id,
                UserProgress.mode == correction.mode,
            )
        )
    ).scalar_one_or_none()
    if progress is None:
        progress = UserProgress(
            user_id=current_user.id,
            word_id=correction.word_id,
            mode=correction.mode,
        )
        db.add(progress)

    progress.box = snapshot.box
    progress.next_review_at = snapshot.next_review_at
    progress.correct_count = snapshot.correct_count
    progress.wrong_count = snapshot.wrong_count
    progress.last_answered_at = snapshot.last_answered_at
    await db.commit()

    return CorrectResponse(
        correction_event_id=correction.id,
        progress=ProgressOut(
            box=snapshot.box,
            next_review_at=snapshot.next_review_at,
            correct_count=snapshot.correct_count,
            wrong_count=snapshot.wrong_count,
        ),
    )


# ─────────────────────────────────────────────────────────────
# 星标
# ─────────────────────────────────────────────────────────────


@router.put("/words/{word_id}/star", status_code=status.HTTP_204_NO_CONTENT,
            summary="加星标（重点关注）")
async def star(
    word_id: uuid.UUID, payload: StarRequest, current_user: CurrentUser, db: DbSession
) -> None:
    exists = (await db.execute(select(Word.id).where(Word.id == word_id))).scalar_one_or_none()
    if exists is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="单词不存在")
    await testing_service.star_word(db, current_user.id, word_id, payload.note)


@router.delete("/words/{word_id}/star", status_code=status.HTTP_204_NO_CONTENT,
               summary="取消星标")
async def unstar(word_id: uuid.UUID, current_user: CurrentUser, db: DbSession) -> None:
    """幂等 —— 没标过也返回 204，前端不必先查再删。"""
    await testing_service.unstar_word(db, current_user.id, word_id)


@router.get("/words/starred", response_model=StarredPage, summary="星标列表")
async def starred(
    current_user: CurrentUser,
    db: DbSession,
    limit: int = Query(default=100, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
) -> StarredPage:
    rows, total = await testing_service.list_starred(db, current_user.id, limit, offset)
    return StarredPage(
        total=total,
        limit=limit,
        offset=offset,
        items=[
            StarredWordOut(
                word_id=w.id,
                word=w.word,
                meaning_primary=w.meaning_primary,
                phonetic=w.phonetic,
                topic=w.topic,
                note=s.note,
                created_at=s.created_at,
            )
            for w, s in rows
        ],
    )
