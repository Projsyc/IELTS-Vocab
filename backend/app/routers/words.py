"""词库路由 —— 对应 docs/04-api-design.md §3。"""

from __future__ import annotations

import uuid

from fastapi import APIRouter, HTTPException, status
from sqlalchemy import case, func, select

from app.core.deps import CurrentUser, DbSession
from app.models import PracticeMode, UserProgress, Word, WordList
from app.models.enums import MAX_BOX
from app.schemas.practice import MasteryBreakdown, WordListOut, WordListStats

router = APIRouter(prefix="/api/word-lists", tags=["词库"])


@router.get("", response_model=list[WordListOut], summary="列出可用词库")
async def list_word_lists(current_user: CurrentUser, db: DbSession) -> list[WordListOut]:
    """公开词库 + 自己的私有词库（私有词库是 v2 功能，当前恒为空）。"""
    rows = (
        await db.execute(
            select(WordList, func.count(Word.id))
            .outerjoin(Word, Word.word_list_id == WordList.id)
            .where((WordList.is_public.is_(True)) | (WordList.owner_id == current_user.id))
            .group_by(WordList.id)
            .order_by(WordList.created_at)
        )
    ).all()

    return [
        WordListOut(
            id=wl.id,
            name=wl.name,
            description=wl.description,
            word_count=count,
            is_public=wl.is_public,
        )
        for wl, count in rows
    ]


async def _breakdown(
    db: DbSession,
    user_id: uuid.UUID,
    word_list_id: uuid.UUID,
    mode: PracticeMode,
    total: int,
) -> MasteryBreakdown:
    """按盒子号分档统计。

    一条 SQL 拿到 learning / mastered，new 用总数减出来 ——
    比"查所有词再逐个判断"少一次全表扫描。
    """
    learning, mastered = (
        await db.execute(
            select(
                func.count(case((UserProgress.box < MAX_BOX, 1))),
                func.count(case((UserProgress.box >= MAX_BOX, 1))),
            )
            .select_from(UserProgress)
            .join(Word, Word.id == UserProgress.word_id)
            .where(
                UserProgress.user_id == user_id,
                UserProgress.mode == mode,
                Word.word_list_id == word_list_id,
            )
        )
    ).one()

    return MasteryBreakdown(
        new=total - learning - mastered,
        learning=learning,
        mastered=mastered,
    )


@router.get(
    "/{list_id}/stats",
    response_model=WordListStats,
    summary="当前用户在该词库的掌握情况",
)
async def word_list_stats(
    list_id: uuid.UUID, current_user: CurrentUser, db: DbSession
) -> WordListStats:
    """两种模式**分别统计** —— 听写和阅读进度独立（ADR-003）。"""
    exists = (
        await db.execute(select(WordList.id).where(WordList.id == list_id))
    ).scalar_one_or_none()
    if exists is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="词库不存在")

    total = (
        await db.execute(
            select(func.count()).select_from(Word).where(Word.word_list_id == list_id)
        )
    ).scalar_one()

    return WordListStats(
        word_list_id=list_id,
        total=total,
        dictation=await _breakdown(db, current_user.id, list_id, PracticeMode.DICTATION, total),
        recognition=await _breakdown(
            db, current_user.id, list_id, PracticeMode.RECOGNITION, total
        ),
    )
