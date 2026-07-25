"""数据库表结构冒烟测试。

不测业务逻辑，只锁住几条**容易被无意破坏**的结构性约束：
  - user_progress 主键必须是 (user_id, word_id, mode) 三元组（ADR-003）
  - Leitner box 的 CHECK 约束生效
  - words 的 meaning / meaning_primary 双字段都 NOT NULL（ADR-008）
  - 回放索引的列顺序正确（决定查询能否走索引）

运行：
    docker compose up -d                          # 先起数据库
    backend/.venv/bin/python -m pytest tests/ -v
"""

import uuid
from datetime import UTC, datetime, timedelta

from sqlalchemy import select, text

from app.models import AnswerEvent, PracticeMode, User, UserProgress, Word, WordList


async def test_progress_primary_key_is_triple(db_conn):
    """user_progress 主键必须是三元组 —— 听写和阅读进度独立（ADR-003）。

    如果有人把 mode 从主键里去掉，这个测试会挂。
    """
    pk = (await db_conn.execute(text("""
        select string_agg(a.attname, ',' order by array_position(c.conkey, a.attnum))
        from pg_constraint c
        join pg_attribute a on a.attrelid = c.conrelid and a.attnum = any(c.conkey)
        where c.conrelid = 'user_progress'::regclass and c.contype = 'p'
    """))).scalar()
    assert pk == "user_id,word_id,mode", f"主键应为三元组，实际: {pk}"


async def test_box_check_constraint(db_conn):
    """Leitner box 必须被约束在 1–5。"""
    definition = (await db_conn.execute(text("""
        select pg_get_constraintdef(oid) from pg_constraint
        where conname = 'ck_progress_box_range'
    """))).scalar()
    assert definition is not None, "缺少 ck_progress_box_range 约束"
    assert "box >= 1" in definition and "box <= 5" in definition


async def test_practice_mode_enum_values(db_conn):
    """数据库 ENUM 值必须与 Python 的 PracticeMode 一致。"""
    labels = (await db_conn.execute(text("""
        select string_agg(e.enumlabel, ',' order by e.enumsortorder)
        from pg_type t join pg_enum e on e.enumtypid = t.oid
        where t.typname = 'practice_mode'
    """))).scalar()
    assert labels == "dictation,recognition"
    assert {m.value for m in PracticeMode} == {"dictation", "recognition"}


async def test_meaning_fields_not_null(db_conn):
    """meaning 和 meaning_primary 都必须 NOT NULL（ADR-008 双字段设计）。"""
    result = (await db_conn.execute(text("""
        select string_agg(column_name || '=' || is_nullable, ',' order by column_name)
        from information_schema.columns
        where table_name = 'words' and column_name in ('meaning', 'meaning_primary')
    """))).scalar()
    assert result == "meaning=NO,meaning_primary=NO", result


async def test_replay_index_column_order(db_conn):
    """回放索引的列顺序决定查询能否走索引。

    (user_id, word_id, mode, answered_at) —— 前三个等值过滤，最后一个排序键。
    """
    definition = (await db_conn.execute(text("""
        select indexdef from pg_indexes where indexname = 'idx_events_replay'
    """))).scalar()
    assert definition is not None, "缺少 idx_events_replay 索引"
    positions = [definition.index(c) for c in ("user_id", "word_id", "mode", "answered_at")]
    assert positions == sorted(positions), f"索引列顺序错误: {definition}"


async def test_answer_events_uses_bigint_pk(db_conn):
    """answer_events 主键用 BIGINT 自增 —— 事件量大，比 UUID 索引更紧凑。"""
    dtype = (await db_conn.execute(text("""
        select data_type from information_schema.columns
        where table_name = 'answer_events' and column_name = 'id'
    """))).scalar()
    assert dtype == "bigint", dtype


async def test_crud_round_trip(db_session):
    """插入完整数据链，验证外键、默认值、以及三元组主键的实际行为。

    重点验证：同一个 (user, word) 能存两条不同 mode 的进度，且盒子号互相独立。
    """
    suffix = uuid.uuid4().hex[:8]
    user = User(username=f"_test_{suffix}", password_hash="x", nickname="测试用户")
    wl = WordList(name=f"_test_list_{suffix}")
    db_session.add_all([user, wl])
    await db_session.flush()

    word = Word(
        word=f"accommodate_{suffix}",
        meaning="vt. 容纳 / n. 住处",
        meaning_primary="vt. 容纳",
        phonetic="/əˈkɒmədeɪt/",
        part_of_speech="vt.",
        topic="住宿",
        exam_tags="cet6 toefl ielts",
        bnc=6548,
        frq=8241,
        word_list_id=wl.id,
    )
    db_session.add(word)
    await db_session.flush()

    now = datetime.now(UTC)
    db_session.add_all([
        AnswerEvent(
            user_id=user.id, word_id=word.id, mode=PracticeMode.DICTATION,
            is_correct=False, user_input="accomodate", answered_at=now, device_id="test",
        ),
        # 同词、同用户，两种模式各一条 —— 三元组主键必须允许
        UserProgress(
            user_id=user.id, word_id=word.id, mode=PracticeMode.DICTATION,
            box=1, next_review_at=now + timedelta(days=1), wrong_count=1,
        ),
        UserProgress(
            user_id=user.id, word_id=word.id, mode=PracticeMode.RECOGNITION,
            box=3, next_review_at=now + timedelta(days=4), correct_count=2,
        ),
    ])
    await db_session.commit()

    rows = (await db_session.execute(
        select(UserProgress).where(
            UserProgress.user_id == user.id, UserProgress.word_id == word.id
        )
    )).scalars().all()
    assert len(rows) == 2, "三元组主键应允许同词不同模式各一条进度"
    assert {r.mode for r in rows} == {PracticeMode.DICTATION, PracticeMode.RECOGNITION}
    assert {r.box for r in rows} == {1, 3}, "两种模式的盒子号应互相独立"

    # 服务端默认值生效
    assert word.difficulty == 2
    assert word.created_at is not None

    # 清理（级联删除带走 word / event / progress）
    await db_session.delete(user)
    await db_session.delete(wl)
    await db_session.commit()
